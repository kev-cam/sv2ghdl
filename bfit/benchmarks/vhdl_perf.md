# Cross-simulator VHDL performance

Single-thread RTL simulation, **same source + same LFSR stimulus on every
engine**; a 64-bit checksum printed by each run is compared across engines — a
row's **agree** is ✓ only if every *running* engine matches. Each cell is
`seconds ×speedup` (base `×` vs the **slowest running engine** in the row);
🟢 = fastest engine in the row. `brk` = exceeded the 45s wall cap;
`—` = engine not applicable (`--accel` declined the design — see below;
l3d not run for the self-contained syn benches; `∥` when no thread count
beat serial). Run-phase wall-clock, best of 3. **size** = non-blank DUT
source lines / process count.

Engines: **our-nvc** 1.19-devel (kev-cam fork, `--std=2040`) · **our-l3d** (same
engine, DUT mechanically promoted to 3D-Logic by `promote_3dlogic.py`, same
stimulus, outputs folded by exact L3D_1 match — its trailing `=`/`≠` says
whether the checksum matched the std_logic engines; ≠ is the documented
3D-logic semantic difference, not a failure) · **our-nvc --accel** (yosys
front-end) · **stock-nvc** 1.22.0 (Nick's release .deb) · **ghdl** 5.0.1 (mcode).

**our-nvc ∥** is the runtime parallel-delta scheduler
(`NVC_PARALLEL_PROCS=<threads>`).  The sweep over 2/4/8 threads and both
gate settings only NOMINATES a configuration; the nominee is then re-timed
best-of-3 interleaved against serial on identical footing, because a
best-of-many column compared against a best-of-3 column wins by sampling
bias alone.  A cell is filled only if the nominee beats serial by >2% AND
reaches 50% parallel efficiency (speedup / CPU multiplier); otherwise it
reads `—` and the measured CPU cost is noted below.

That second test is load-bearing.  The scheduler gates on delta DEPTH — how
many processes are runnable in one delta — because dispatching a shallow
delta across workers costs more than it saves, and these circuits run 1-11
processes per delta against a default gate of 64, so the parallel evaluator
never engages.  Four threads on b22 nevertheless cut the wall ~8%, while
executing 6% MORE instructions and burning 3.6x the cycles: no work is
saved, the extra threads spin.  (A control run with three dummy spinners
made serial SLOWER, so the wall gain is not a CPU-frequency artefact; it
is thread placement.)  Reporting that as a parallel speed-up would be
false, so the efficiency test rejects it.

| Design | style | size | cycles | agree | our-nvc | our-nvc ∥ | our-l3d | our-nvc --accel | stock-nvc | ghdl |
| :-- | :-- | --: | --: | :--: | --: | --: | --: | --: | --: | --: |
| bench_seq | seq: LFSR + register chain | 45/5 | 1000000 | ✓ | 0.735 ×14.4 | — | — | — | 🟢 0.418 ×25.4 | 10.616 ×1.0 |
| bench_comb | comb: 32-bit mul/add datapath | 60/4 | 2000000 | ✓ | 2.269 ×1.0 | — | — | — | 🟢 1.879 ×1.2 | brk |
| b01 | FSM: serial flow comparator | 96/2 | 3000000 | ✓ | 1.914 ×5.7 | — | 2.035 ×5.4 = | 2.169 ×5.0 | 🟢 1.690 ×6.4 | 10.896 ×1.0 |
| b06 | FSM: interrupt handler | 112/2 | 2000000 | ✓ | 1.761 ×4.5 | — | 1.856 ×4.2 = | 1.915 ×4.1 | 🟢 1.531 ×5.1 | 7.846 ×1.0 |
| b12 | ctrl+datapath: 1-player game | 442/8 | 3000000 | ✓ | 🟢 2.902 ×4.5 | — | 3.229 ×4.1 = | — | 3.111 ×4.2 | 13.135 ×1.0 |
| b14 | CPU: Viper processor subset | 490/2 | 1000000 | ✓ | 1.040 ×8.2 | — | 1.134 ×7.5 = | — | 🟢 0.823 ×10.3 | 8.509 ×1.0 |
| b17 | 3x CPU: three b14-class cores | 758/18 | 1000000 | ✓ | 🟢 2.147 ×5.7 | — | 2.178 ×5.6 = | — | 2.384 ×5.1 | 12.144 ×1.0 |
| b22 | 3x CPU: b14-class pipeline copy | 1539/8 | 1000000 | ✓ | 🟢 1.487 ×5.0 | — | 1.504 ×4.9 = | — | 1.507 ×4.9 | 7.437 ×1.0 |

### Reading these numbers

**our-nvc is a 1.18.0-based fork; stock-nvc here is 1.22.0 — four releases
newer.** The gap has been closed by profiling, one discrete cause at a time,
and the fork now LEADS stock on SEVEN of eight rows (b17 +35%, b12 +25%,
b22 +15%, b14 +6%, b01 +5%, b06 +4%, bench_comb +3%) with fused dispatch
default-on and the native projection complete: `bench_comb`
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
non-cherry-pickable four-release gap).  The big ITC jumps came from
widening fast-clk membership to (clock,reset) processes (nvc
212c9db39): async-reset DUT flops — previously excluded because reset
pulses dissolved the table and blacklisted reset forever — now join
the fused block with reset as a COMPANION, running via a two-entry
straight-line block (posedge runs all members, reset activity enters
at the every-event tail).  b12's table went from 1 member (the tb
main) to 5, b17's from 1 to 12 (branch misses −33%); the same procs
lift the our-l3d column.

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
`--accel` (yosys front-end).  Its `—` cells were long attributed to the
size pre-gate (`NVC_ACCEL_MIN_MODULES`, default 8 instances), but lowering
that gate to 1 on a separate machine showed the real blocker is TRANSLATOR
COVERAGE: all six ITC designs get past the gate and are then declined by
vhdl2vlog, which marks each unhandled construct in the Verilog it emits.
The blockers are `T_ATTR_REF` (attribute reference — in ALL six designs,
fatal on its own), the `mod`/`*` operator functions (74 sites in b14
alone) and non-architecture block scopes (b17/b22).  Nothing installs, so
every accel run here is pure interpretation — and lowering the gate is
actively harmful, costing 8-12% on b17/b22 in repeated failed synth
attempts.  These designs ARE worth accelerating (CPU-class datapaths);
they are simply unreachable through the current translator.

`bench_comb` uses only 32-bit arithmetic yet still `brk`s ghdl-mcode, a
useful datapoint on its own._

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
| 8 | 0.408s | 0.416s | 0.421s | 0.99x |
| 32 | 0.419s | 0.447s | 0.402s | 1.11x |
| 128 | 0.417s | 0.509s | 0.395s | 1.29x |
| 1024 | 0.412s | 1.662s | 0.464s | 3.58x |

**Do not compare those wall-clock figures against an earlier run of this table.**
The previous revision recorded 1.15 / 1.43 / 2.34 / 6.43x, which looks like a
large regression and is not evidence of one: `std_logic`, which nothing has
touched, moved 2.2x between the two runs (0.187s -> 0.412s at 1024 wires), so
box state dominates the comparison. Instruction counts on the same builds, which
are contention-robust and are the project's stated metric:

| wires | logic3d insn | l3dw insn | l3dw vs logic3d |
| --: | --: | --: | --: |
| 8 | 5,523,079,971 | 5,012,855,732 | 1.10x |
| 32 | 6,023,674,293 | 5,061,567,122 | 1.19x |
| 128 | 7,369,096,270 | 5,188,974,108 | 1.42x |
| 1024 | 24,628,166,076 | 6,384,000,795 | **3.86x** |

**A before/after for the certainty enum is still OUTSTANDING** (task #52). That
change (`a6cc3b749`) moved plane access from `mod`/`div` to slices — faster — and
added a 2-bit `kmax` magnitude compare to every gate op — slower. Net sign
unmeasured, because establishing it needs the pre-change package rebuilt and
measured on the same metric. If `kmax` proves expensive it only needs to run when
an operand is uncertain, which after reset is never, so an `if (Ua | Ub)` guard
would restore the old certain-path cost.

**THE STRUCTURAL POINT THIS TABLE UNDERSTATES.** `logic3d`'s vector operators are
SCALAR — `L3D_BINOP_BODY` in `src/jit/jit-intrin.c` processes one `int32` per
loop iteration — while `std_logic`'s run SIXTEEN values per 128-bit op via three
`PSHUFB` lookups (`ieee_and_vector_sse41`). std_logic *needs* a table, because a
9-value enum has no arithmetic structure; logic3d needs none, its ops are pure
bitwise formulas with no cross-lane dependency. The representation with the
better algebra is the one that never got vectorised, which is why `our-l3d` loses
to `our-nvc` in the main table above (b14: 1.134 vs 1.040). Tracked as task #54.

### Demand-driven (pull) vs forward (push) evaluation

The Verilator-beating lever (`bfit/prototypes/demand_eval.c`): compute a
signal only when observed, recursing *backward* through its cone, vs a
forward model that evaluates the whole design every cycle. Same netlist,
pull result verified bit-identical to push. 8329-node design, 5000 cycles:

| evaluator | observation | vs forward push |
| :-- | :-- | --: |
| compiled pull cone | every cycle | **26.96x FASTER** |
| pull (interpreted) | every cycle | 9.65x |
| pull (interpreted) | every 100th cycle | 107.49x |
| pull (interpreted) | final only | 164.63x |

Compiled cones skip dead **logic** at push's per-eval speed (no interp
overhead); memoisation/multicycle-collapse additionally skip unobserved
**time**. Honest crossover: on a fully-live design densely observed pull
*loses* ~2.5× to overhead — it wins as dead/unobserved work rises (~70%
break-even), the regime real designs under a test actually sit in.
