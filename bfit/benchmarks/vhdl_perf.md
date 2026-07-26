# Cross-simulator VHDL performance

Single-thread RTL simulation, **same source + same LFSR stimulus on every
engine**; a 64-bit checksum printed by each run is compared across engines — a
row's **agree** is ✓ only if every *running* engine matches. Each cell is
`seconds ×speedup` (base `×` vs the **slowest running engine** in the row);
🟢 = fastest engine in the row. `brk` = exceeded the 45s wall cap;
`—` = engine not applicable (`--accel` declined the design as too small;
l3d not run for the self-contained syn benches). Run-phase wall-clock, best
of 3. **size** = non-blank DUT source lines / process count.

Engines: **our-nvc** 1.19-devel (kev-cam fork, `--std=2040`) · **our-l3d** (same
engine, DUT mechanically promoted to 3D-Logic by `promote_3dlogic.py`, same
stimulus, outputs folded by exact L3D_1 match — its trailing `=`/`≠` says
whether the checksum matched the std_logic engines; ≠ is the documented
3D-logic semantic difference, not a failure) · **our-nvc --accel** (yosys
front-end) · **stock-nvc** 1.22.0 (Nick's release .deb) · **ghdl** 5.0.1 (mcode).

| Design | style | size | cycles | agree | our-nvc | our-l3d | our-nvc --accel | stock-nvc | ghdl |
| :-- | :-- | --: | --: | :--: | --: | --: | --: | --: | --: |
| bench_seq | seq: LFSR + register chain | 45/5 | 1000000 | ✓ | 0.428 ×24.4 | — | — | 🟢 0.411 ×25.4 | 10.455 ×1.0 |
| bench_comb | comb: 32-bit mul/add datapath | 60/4 | 2000000 | ✓ | 🟢 1.842 ×1.1 | — | — | 1.987 ×1.0 | brk |
| b01 | FSM: serial flow comparator | 96/2 | 3000000 | ✓ | 🟢 1.686 ×6.5 | 1.775 ×6.2 = | — | 1.751 ×6.3 | 10.973 ×1.0 |
| b06 | FSM: interrupt handler | 112/2 | 2000000 | ✓ | 🟢 1.525 ×5.2 | 1.578 ×5.0 = | — | 1.551 ×5.1 | 7.863 ×1.0 |
| b12 | ctrl+datapath: 1-player game | 442/8 | 3000000 | ✓ | 🟢 2.719 ×4.7 | 3.073 ×4.2 = | — | 3.179 ×4.0 | 12.838 ×1.0 |
| b14 | CPU: Viper processor subset | 490/2 | 1000000 | ✓ | 🟢 0.764 ×10.9 | 0.800 ×10.4 = | — | 0.802 ×10.4 | 8.321 ×1.0 |
| b17 | 3x CPU: three b14-class cores | 758/18 | 1000000 | ✓ | 🟢 1.975 ×6.2 | 2.007 ×6.1 = | — | 2.323 ×5.2 | 12.193 ×1.0 |
| b22 | 3x CPU: b14-class pipeline copy | 1539/8 | 1000000 | ✓ | 🟢 1.323 ×5.5 | 1.326 ×5.5 = | — | 1.436 ×5.1 | 7.302 ×1.0 |

### Reading these numbers

**our-nvc is a 1.18.0-based fork; stock-nvc here is 1.22.0 — four releases
newer.** The gap has been closed by profiling, one discrete cause at a time,
and the fork now LEADS stock on SEVEN of eight rows (b17 +18%, b12 +17%,
b22 +9%, bench_comb +8%, b14 +5%, b01, b06) with fused dispatch default-on
and the native projection complete: `bench_comb`
was 4.1x off until the numeric_std shift-and-add multiply was replaced with
upstream's native 64-bit multiply; the remaining ~1.3x fell to ~1.1x when
the libnvc build switched from global-dynamic TLS to initial-exec +
-fno-plt (nvc 8a4180adb: every JIT'd-function entry had paid a
__tls_get_addr PLT call — also −4.2% wall on VeeR-EH2); and direct vtable
eval entries for static-sensitivity processes (nvc 10626274f: the
scheduler's megamorphic JIT-entry dispatch chain collapsed, b12 branch
misses −46%), the default-on fused block (59db0b647/eab523ed6), and the
native projection (eebbeae60 + 245528b74: the fast-driver waveform
schedule inlined into generated code for every 1..8-byte scalar — the
runtime spec specialized against elaborated structure; instructions −6%
to −14%) took seven rows past stock.  b14 was the projection's hardest
case: 90 assignment sites in one process function made per-site inlining
cost more in LLVM compile time than it saved (+10% wall), so the landed
form emits ONE shared body per element size with a direct call per site
— same inner code, compile cost off the critical path (nvc 245528b74).
The sole holdout: bench_seq 1.04x — 45 lines, 5 processes, the smallest
possible scheduler footprint.  Skipping the empty scheduler-phase drains
(nvc b41c7d3af: profiling found 6.0 empty-phase visits per delta, two of
them full calls into the outlined drain; an adversarial review chose
predicted-not-taken count guards over a decision-free jump route — the
win was call+ret elimination, which both forms capture) halved the gap
from 1.09x; the residue is stock's flat 1.22 scheduler/MIR core (the
non-cherry-pickable four-release gap).

The **our-l3d** column is the fork's native 4-state/mixed-signal type system
on the SAME RTL: the cost over the std_logic column is the price of carrying
value+strength+certainty per wire scalar-wise. `=` marks rows where the
3D-logic checksum matched the 2-state engines exactly — promoted ITC
controllers are reset-defined, so agreement is the expected result and a
per-design correctness sweep of the promotion path comes free with the
benchmark. The packed-word (l3dw) representation that removes most of the
scalar-carry cost is benchmarked in the companion table below.

The ITC'99 cores are controllers that reach a halt state and then stop
toggling, at which point a run measures clock-toggle overhead rather than RTL
activity (b17 gave the *same* checksum at 10k and 20k cycles). The generated
testbenches re-pulse reset every 512 cycles so the DUT keeps executing for the
whole run. b20 is excluded: its two b14 cores form a closed loop whose
top-level outputs never leave 0, so its checksum cannot detect divergence.

_Generated by `bfit/benchmarks/vhdl/run_vhdl_perf.sh`. Base nvc/ghdl RTL
simulation is single-threaded (nvc JIT is a codegen mode, not runtime
parallelism; ghdl is mcode). The fork's parallel/accelerated path is
`--accel` (yosys front-end); it declines designs with no synthesizable
hierarchy large enough to be worth a chunk, so the small circuits here read
`—` — revisit at VeeR scale. `bench_comb` uses only 32-bit arithmetic yet
still `brk`s ghdl-mcode, a useful datapoint on its own._

## Where we lead

The table above is raw single-thread `std_logic` — the one axis where a
1.18-based fork trails a 1.22 stock. The dimensions the fork is *built* for
don't show up there; these companion tables surface them.

### 3D-Logic packed word (l3dw) vs the current logic3d

our-nvc `--std=2040`, identical bitwise op sequence at matched wire counts
(WIRES = 8·NWORDS). The packed word carries 8 wires per 32-bit element, so a
bus op is byte-parallel; validated bit-for-bit by `test/regress/logic3dw1/2`
in the nvc tree. std_logic shown for reference (it isn't the 3D-logic path).

| wires | std_logic | logic3d | l3dw word | l3dw vs logic3d |
| --: | --: | --: | --: | --: |
| 8 | 0.112s | 0.136s | 0.116s | 1.17x |
| 32 | 0.115s | 0.176s | 0.121s | 1.45x |
| 128 | 0.125s | 0.314s | 0.135s | 2.33x |
| 1024 | 0.184s | 1.577s | 0.246s | 6.41x |

### Demand-driven (pull) vs forward (push) evaluation

The Verilator-beating lever (`bfit/prototypes/demand_eval.c`): compute a
signal only when observed, recursing *backward* through its cone, vs a
forward model that evaluates the whole design every cycle. Same netlist,
pull result verified bit-identical to push. 8329-node design, 5000 cycles:

| evaluator | observation | vs forward push |
| :-- | :-- | --: |
| compiled pull cone | every cycle | **26.62x FASTER** |
| pull (interpreted) | every cycle | 10.14x |
| pull (interpreted) | every 100th cycle | 119.71x |
| pull (interpreted) | final only | 169.54x |

Compiled cones skip dead **logic** at push's per-eval speed (no interp
overhead); memoisation/multicycle-collapse additionally skip unobserved
**time**. Honest crossover: on a fully-live design densely observed pull
*loses* ~2.5× to overhead — it wins as dead/unobserved work rises (~70%
break-even), the regime real designs under a test actually sit in.
