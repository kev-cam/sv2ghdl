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

| Design | style | size | cycles | agree | our-nvc | our-nvc ∥ | our-l3d | our-nvc --accel | stock-nvc | ghdl | gpu-farm |
| :-- | :-- | --: | --: | :--: | --: | --: | --: | --: | --: | --: | --: |
| bench_seq | seq: LFSR + register chain | 45/5 | 1000000 | ✓ | 0.469 ×22.6 | — | — | — | 🟢 0.424 ×25.0 | 10.619 ×1.0 | — |
| bench_comb | comb: 32-bit mul/add datapath | 60/4 | 2000000 | ✓ | 1.952 ×1.0 | — | — | — | 🟢 1.913 ×1.0 | brk | — |
| b01 | FSM: serial flow comparator | 96/2 | 3000000 | ✓ | 1.984 ×5.6 | — | 2.002 ×5.6 = | 2.860 ×3.9 | 🟢 1.790 ×6.2 | 11.128 ×1.0 | 8.9e9 ×5282 |
| b06 | FSM: interrupt handler | 112/2 | 2000000 | ✓ | 1.838 ×4.3 | — | 1.820 ×4.4 = | 2.570 ×3.1 | 🟢 1.658 ×4.8 | 7.969 ×1.0 | 7.7e9 ×6419 |
| b12 | ctrl+datapath: 1-player game | 442/8 | 3000000 | ✓ | 3.001 ×4.3 | 🟢 2.905 ×4.5 2t 1.9×cpu | 3.171 ×4.1 = | — | 3.280 ×4.0 | 13.009 ×1.0 | 5.3e8 ×516 |
| b14 | CPU: Viper processor subset | 490/2 | 1000000 | ✓ | 🟢 0.774 ×10.9 | — | 0.789 ×10.7 = | — | 0.813 ×10.4 | 8.428 ×1.0 | 1.2e9 ×958 |
| b17 | 3x CPU: three b14-class cores | 758/18 | 1000000 | ✓ | 1.912 ×6.4 | — | 🟢 1.882 ×6.5 = | — | 2.404 ×5.1 | 12.316 ×1.0 | 1.8e7 ×34 |
| b22 | 3x CPU: b14-class pipeline copy | 1539/8 | 1000000 | ✓ | 1.532 ×4.9 | — | 🟢 1.451 ×5.1 = | — | 1.509 ×4.9 | 7.445 ×1.0 | 2.1e8 ×304 |

### Reading these numbers

**our-nvc is a 1.18.0-based fork; stock-nvc here is 1.22.0 — four releases
newer.** The gap has been closed by profiling, one discrete cause at a time,
and the fork now trades blows with a release four versions newer: it
leads or ties half the rows (2026-08-29 regenerate: b14 and b17
outright, b12 via the parallel scheduler's first qualifying cell, b22
via l3d — the 4-state engine winning rows outright while carrying MORE
per-wire semantics), trails b01/b06 ~10% and the tiny bench_seq.
Which side of parity a given row lands on moves a few percent run to
run; the month-scale trend is what the mechanism list below records.  Fused dispatch is
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

### The gpu-farm column

`bench_gpu.sh` compiles the same DUT into a gsm 2-phase C model (via the
fork's whole-scope Verilog dump), certifies it against the VHDL engines'
checksum — same LFSR stimulus, same fold, bit-exact CHK match required —
then runs 4,096 stimulus-decorrelated instances as one CUDA farm.  Cells
are aggregate instance-cycles/s on a **T1000 laptop-class GPU** and the
multiple over the row's fastest single engine.  Instance 0 replays the
canonical stimulus, so its CHK must (and does) equal the VHDL engines'.
**All six ITC designs are now covered** — the last four were unlocked by
the integer-array-memory / boolean-literal / division translator work
(nvc dcd34b090), each certified at full cycle count.

| design | agg inst-cyc/s | per-instance | vs fastest single |
| :-- | --: | --: | --: |
| b01 | 8.85e9 | 2.16M cyc/s | ×5282 |
| b06 | 7.74e9 | 1.89M cyc/s | ×6419 |
| b12 | 5.33e8 | 130k cyc/s | ×516 |
| b14 | 1.24e9 | 302k cyc/s | ×958 |
| b17 | 1.81e7 | 4.4k cyc/s | ×34 |
| b22 | 2.10e8 | 51k cyc/s | ×304 |

The spread tracks design size: the small FSMs run each instance FASTER
than any CPU engine runs one copy, while b17 (three CPU cores per
instance) pays the width-for-latency trade and still delivers 34× the
best single engine in aggregate.  The syn benches have no separable DUT
scope and stay uncovered by construction.

The b06 --accel checksum divergence an earlier run of this table
caught (async-reset drive path, four stacked defects) is fixed in nvc
45059fe51 and guarded by the `arst` accelbench shape — this table's
cross-engine gate keeps working in both directions.

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
| 8 | 0.116s | 0.138s | 0.120s | 1.15x |
| 32 | 0.118s | 0.172s | 0.124s | 1.39x |
| 128 | 0.123s | 0.312s | 0.132s | 2.36x |
| 1024 | 0.184s | 1.557s | 0.251s | 6.20x |

### Demand-driven (pull) vs forward (push) evaluation

The Verilator-beating lever (`bfit/prototypes/demand_eval.c`): compute a
signal only when observed, recursing *backward* through its cone, vs a
forward model that evaluates the whole design every cycle. Same netlist,
pull result verified bit-identical to push. 8329-node design, 5000 cycles:

| evaluator | observation | vs forward push |
| :-- | :-- | --: |
| compiled pull cone | every cycle | **27.06x FASTER** |
| pull (interpreted) | every cycle | 10.65x |
| pull (interpreted) | every 100th cycle | 108.11x |
| pull (interpreted) | final only | 167.57x |

Compiled cones skip dead **logic** at push's per-eval speed (no interp
overhead); memoisation/multicycle-collapse additionally skip unobserved
**time**. Honest crossover: on a fully-live design densely observed pull
*loses* ~2.5× to overhead — it wins as dead/unobserved work rises (~70%
break-even), the regime real designs under a test actually sit in.
