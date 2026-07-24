# Cross-simulator VHDL performance

Single-thread RTL simulation, **same source + same LFSR stimulus on every
engine**; a 64-bit checksum printed by each run is compared across engines — a
row's **agree** is ✓ only if every *running* engine matches. Each cell is
`seconds ×speedup` (base `×` vs the **slowest running engine** in the row);
🟢 = fastest engine in the row. `brk` = exceeded the 150s wall cap;
`—` = `--accel` declined (design too small / no synthesizable hierarchy —
revisit at VeeR scale). Run-phase wall-clock, best of 2. DUTs are plain
`bit`/`std_logic` (no 3D-logic).

Engines: **our-nvc** 1.19-devel (kev-cam fork, `--std=2040`) · **our-nvc --accel**
(yosys front-end) · **stock-nvc** 1.22.0 (Nick's release .deb) · **ghdl** 5.0.1 (mcode).

| Design | cycles | agree | our-nvc | our-nvc --accel | stock-nvc | ghdl |
| :-- | --: | :--: | --: | --: | --: | --: |
| bench_seq | 1000000 | ✓ | 0.598 ×16.8 | — | 🟢 0.404 ×24.9 | 10.062 ×1.0 |
| bench_comb | 2000000 | ✓ | 2.318 ×1.0 | — | 🟢 1.819 ×1.3 | brk |
| b01 | 3000000 | ✓ | 2.286 ×4.6 | — | 🟢 1.679 ×6.3 | 10.620 ×1.0 |
| b06 | 2000000 | ✓ | 2.042 ×3.7 | — | 🟢 1.494 ×5.1 | 7.586 ×1.0 |
| b12 | 3000000 | ✓ | 4.036 ×3.1 | — | 🟢 3.035 ×4.1 | 12.435 ×1.0 |
| b14 | 1000000 | ✓ | 1.065 ×7.5 | — | 🟢 0.777 ×10.3 | 8.010 ×1.0 |
| b17 | 1000000 | ✓ | 3.056 ×3.8 | — | 🟢 2.262 ×5.2 | 11.759 ×1.0 |
| b22 | 1000000 | ✓ | 1.996 ×3.5 | — | 🟢 1.504 ×4.7 | 7.079 ×1.0 |

### Reading these numbers

**our-nvc is a 1.18.0-based fork; stock-nvc here is 1.22.0 — four releases
newer.** The consistent ~1.3-1.5x is therefore mostly upstream work we have
not merged, not fork regressions. That was measured, not assumed: `bench_comb`
was 4.1x off (7.42s) until the numeric_std multiply spent 63.8% of its runtime
in a shift-and-add loop that upstream 1.22 had replaced with a single native
64-bit multiply; porting that one fast path took it to 2.32s and closed the
row to the same ~1.3x as everything else. Expect the rest of the gap to have
the same character — discrete upstream optimisations, findable by profile.

The ITC'99 cores are controllers that reach a halt state and then stop
toggling, at which point a run measures clock-toggle overhead rather than RTL
activity (b17 gave the *same* checksum at 10k and 20k cycles). The generated
testbenches re-pulse reset every 512 cycles so the DUT keeps executing for the
whole run. b20 is excluded: its two b14 cores form a closed loop whose
top-level outputs never leave 0, so its checksum cannot detect divergence.

Design sizes (lines of VHDL): bench_seq/bench_comb are synthetic micro-
benchmarks; b01 60, b06 128, b12 573, **b14 509, b17 810 (2x b15), b22 1613
(3x b14-family)** — the b14/b17/b22 rows are the "bigger design" datapoints.

### 3D-Logic

**Packed word (l3dw), the intended representation, is now built** —
`run_l3dw_perf.sh` (our-nvc `--std=2040`, same op sequence at matched wire
counts). One 32-bit word carries three byte-planes (value / driven /
uncertain), so an element is a *group of 8 wires* and a bus op is a byte-
parallel formula computing 8 wires per byte-op:

| wires | std_logic | logic3d | l3dw word | l3dw vs logic3d |
| --: | --: | --: | --: | --: |
| 8 | 0.144s | 0.167s | 0.149s | 1.12x |
| 32 | 0.146s | 0.206s | 0.160s | 1.29x |
| 128 | 0.154s | 0.338s | 0.179s | 1.89x |
| 1024 | 0.214s | 1.556s | 0.381s | 4.08x |

Near-parity single-bit (single-bit is a wash by design) and a growing win on
vectors — **up to 4.1x over the current logic3d**, lifting 3D-logic from 0.19x
std_logic to 0.57x at 1024 wires. Correctness is gated by `test/regress/
logic3dw1` in the nvc tree (intrinsic == VHDL body; 2-state == std_logic;
X-propagation == the logic3d LUT). To *beat* std_logic on wide vectors the
value bytes must be contiguous (a C prototype of the pure value plane hit 9.4x
that way); the packed word strides them by 4, so SSE gets 4 words/op — the word
is the storage/semantic unit (narrow bus + mixed-signal), a wide path can
gather. Next: `iverilog/tgt-vhdl` (`support.cc:72`) should emit `l3dw_vector`
for a Verilog bus so real designs use it.

The rest of this section is the original analysis of *why* the current
`logic3d` is slow, which motivated the packed word:

`logic3d` is declared `subtype logic3d is natural range 0 to 7`,
and nvc sizes a subtype from its *base* type (`type_bit_width`/`lower_type`
both chase `T_SUBTYPE` to the base), so a logic3d element is **4 bytes against
std_logic's 1** — visible in the elaborated vcode, where a logic3d port lowers
as `$<-2^31..2^31-1>`. A width-isolation benchmark (identical shift/compare
logic, only the element type differs) prices it: `natural range 0 to 7` 1.429s
vs a distinct `type ... is range 0 to 7` (1 byte) 1.181s, with std_logic at
1.215s — **~17% purely from element width**, before counting the 4x SIMD lanes
the byte-per-element form would give the l3d intrinsics, which today process
`int32_t` lanes.

So "3D-logic devolves to 2-state once reset clears the Xs, therefore it should
be faster" is sound in the value domain but cannot show up while every signal
carries a 4x width tax. Narrowing is the prerequisite. The catch, confirmed
experimentally: a distinct integer type constrains *intermediate* results
(`a + b + c + d` on a 0..7 type overflows and is rejected at analysis, where a
subtype's intermediates carry INTEGER's range), so narrowing requires explicit
widening at every arithmetic site in `logic3d_types_pkg` plus retyping the
intrinsic lanes.

Mixing a promoted logic3d DUT with the original std_logic/bit testbench — the
preferable route, since it keeps one reference stimulus — currently aborts in
elaboration: `--std=2040` (STD_MX) only *warns* on a port type mismatch
(`sem.c:4816`) and never inserts a conversion, so `lower_ports` (`lower.c:11984`)
reaches a direct signal-alias store with mismatched types and hits
`(init): variable and stored value do not have same type`. Real mixed-type
ports need implicit conversion signals building in elaboration.

_Generated by `bfit/benchmarks/vhdl/run_vhdl_perf.sh`. Base nvc/ghdl RTL
simulation is single-threaded (nvc JIT is a codegen mode, not runtime
parallelism; ghdl is mcode). The fork's parallel/accelerated path is
`--accel` (yosys front-end); it declines designs with no synthesizable
hierarchy large enough to be worth a chunk, so the small circuits here read
`—` — revisit at VeeR scale. `bench_comb` uses only 32-bit arithmetic yet
still `brk`s ghdl-mcode, a useful datapoint on its own._
