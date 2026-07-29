# Cross-simulator VHDL performance

Single-thread RTL simulation, **same source + same LFSR stimulus on every
engine**; a 64-bit checksum printed by each run is compared across engines — a
row's **agree** is ✓ only if every *running* engine matches. Each cell is
`seconds ×speedup` (base `×` vs the **slowest running engine** in the row);
🟢 = fastest engine in the row. `brk` = exceeded the 45s wall cap;
`—` = engine not applicable (`--accel` declined the design as too small;
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

| Design | style | size | cycles | agree | our-nvc | our-nvc ∥ | our-l3d | our-nvc --accel | JIT-opt Δinsn | stock-nvc | ghdl |
| :-- | :-- | --: | --: | :--: | --: | --: | --: | --: | --: | --: | --: |
| bench_seq | seq: LFSR + register chain | 45/5 | 1000000 | ✓ | 0.435 ×24.1 | — | — | — | -0.2% | 🟢 0.419 ×25.0 | 10.483 ×1.0 |
| bench_comb | comb: 32-bit mul/add datapath | 60/4 | 2000000 | ✓ | 1.959 ×1.0 | 🟢 1.875 ×1.0 2t/min4 1.9×cpu | — | — | **-4.0%** | 1.876 ×1.0 | brk |
| b01 | FSM: serial flow comparator | 96/2 | 3000000 | ✓ | 🟢 1.690 ×6.5 | — | 1.812 ×6.1 = | — | **-15.3%** | 1.755 ×6.3 | 11.065 ×1.0 |
| b06 | FSM: interrupt handler | 112/2 | 2000000 | ✓ | 🟢 1.522 ×5.2 | — | 1.607 ×4.9 = | — | **-13.2%** | 1.613 ×4.9 | 7.926 ×1.0 |
| b12 | ctrl+datapath: 1-player game | 442/8 | 3000000 | ✓ | 🟢 2.648 ×4.9 | — | 2.955 ×4.4 = | — | **-13.2%** | 3.399 ×3.8 | 13.081 ×1.0 |
| b14 | CPU: Viper processor subset | 490/2 | 1000000 | ✓ | 0.743 ×11.3 | 🟢 0.718 ×11.7 2t/min4 1.9×cpu | 0.845 ×9.9 = | — | **-12.7%** | 0.806 ×10.4 | 8.367 ×1.0 |
| b17 | 3x CPU: three b14-class cores | 758/18 | 1000000 | ✓ | 🟢 1.788 ×6.9 | — | 1.817 ×6.8 = | — | **-6.9%** | 2.354 ×5.2 | 12.265 ×1.0 |
| b22 | 3x CPU: b14-class pipeline copy | 1539/8 | 1000000 | ✓ | 🟢 1.187 ×6.2 | — | 1.201 ×6.1 = | — | **-7.7%** | 1.435 ×5.1 | 7.306 ×1.0 |

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

**JIT-opt Δinsn** is the first entry in the optimiser's technique catalogue,
measured on the path these designs actually take. `--accel` never runs on any
row here (see below), but every design goes through the JIT — so this column
reports what a JIT-path technique buys, in **instructions**, which is the
metric that survives machine and cache variation.

The technique is `NVC_JIT_ASYNC=0` — compile synchronously at the tier-up
threshold instead of continuing to interpret while a background thread
compiles. All eight checksums are identical to the async run (verified, not
assumed). It is reported as Δinstructions rather than seconds because **it
costs single-run wall-clock and wins throughput**, and only one of those is
visible in the seconds columns:

| p_tb, 16 cores | async (default) | sync |
| :-- | --: | --: |
| one sim, idle box | **0.379 s** | 0.454 s |
| one sim, all cores busy | **0.747 s** | 0.931 s |
| **16 concurrent sims** | 1.700 s | **1.076 s** |

Async spends 7–44% extra instructions to hide compile latency on a spare core,
so it wins whenever a spare core exists. Under a regression run every "spare"
core is another job, and the extra instructions come straight off aggregate
throughput — **sync is 37% faster for 16 concurrent sims**. So the applicability
condition is explicit: use it for farm and CI runs and on few-core laptops;
leave it off for a single interactive run. Note that single-run wall-clock
predicts this backwards — instructions predicted it correctly.

_Generated by `bfit/benchmarks/vhdl/run_vhdl_perf.sh`. Base nvc/ghdl RTL
simulation is single-threaded (nvc JIT is a codegen mode, not runtime
parallelism; ghdl is mcode). The fork's parallel/accelerated path is
`--accel` (yosys front-end); it declines designs with no synthesizable
hierarchy large enough to be worth a chunk, so the small circuits here read
`—`. `bench_comb` uses only 32-bit arithmetic yet
still `brk`s ghdl-mcode, a useful datapoint on its own._

### Why the `--accel` column is empty — measured at VeeR scale

This column used to say "revisit at VeeR scale". That has now been done, and the
answer is that **`--accel` currently buys nothing on VeeR-EH2: 12.58 cycles/s
against 12.02 interpreted, 0.99x.** Verilator on the same workload: 30195
cycles/s. Two measured reasons, in order.

**Coverage, not codegen.** The generated native code covered ~0.09% of VeeR sim
time because the hot blocks declined to translate — and that turned out to be a
dangling pointer rather than a missing feature. `vid()` returns one of 8
ROTATING STATIC buffers; `emit_function` borrowed that pointer across a body
emission issuing far more than 8 `vid()` calls, so the trailing `return` printed
whatever identifier last landed in the slot. **22 of 22 emitted functions had
the wrong return target.**

**Correctness, which is the real blocker.** Fixing that is a two-line change and
it does unblock synthesis: `eh2_dec` (16511 comb cells / 346 registers) and
`eh2_ifu` (30680 / 1949) now emit, synthesise, compile and install where both
previously printed `synth failed`. But the unblocked chunks are FUNCTIONALLY
WRONG. `eh2_dec` derails at cycle 77 — 125 retires under the interpreter against
42 under accel, identical for the first 16 retires, then garbage (0x2E000000)
and a repeating illegal-instruction trap. `NVC_ACCEL_VERIFY`, which drives the
chunk from the interpreter's own inputs, localises it as a COMPUTE divergence,
first report at `215ns+101 DEC_I0_BR_IMMED_D`. `eh2_ifu` installs and then
SIGSEGVs, its generated `sm_comb` stack frame being 8.33 MiB against an 8 MiB
limit.

So the earlier reading that the hot blocks were "correct but declined" was
wrong: **they were wrong and failing safe.**

**UPDATE — the correctness half is now fixed and landed (nvc `f251009c0`).** The
wrongness was a second, independent silent mistranslation: Verilog
**self-determined width**. `l3d_bit_read` returns a 1-bit scalar but was emitted
as `((a >> i) & 1'b1)`, which Verilog widths from its *left operand*. Verilog has
exactly two contexts that do not resize their parts — a concatenation element and
a replication operand — and both were fed such reads. In `eh2_dec_gpr_ctl`,
`{32 × bit_read(v_w0v,…)}` with `width(v_w0v)=31` became 992 bits whose low 32
are `0x80000001`, so **only bits 0 and 31 of every GPR write survived**. A second
instance was predicted from the base width and then measured exactly. With that
fixed, **`eh2_dec` now produces 125 RETIRE lines byte-identical to the
interpreter** under `--accel`.

**The column still stays empty, for two separate reasons.** First and unchanged:
every design in this table is far too small to be worth a chunk, so `--accel`
declines them all — that is a property of the designs, not a defect. Second, at
VeeR scale `eh2_ifu` still installs and then SIGSEGVs on an 8.33 MiB `sm_comb`
stack frame, and a separate extra-clock-group scheduling defect remains (it
collapses `eh2_dec_decode_ctl`'s 62 divergences to 2 under
`NVC_ACCEL_CK_LATE=1`). So VeeR is closer to correct but not yet a number worth
publishing. See `sv_perf.md` for where the throughput gap actually lives — it is
not representation.

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
| 8 | 0.114s | 0.135s | 0.117s | 1.15x |
| 32 | 0.116s | 0.175s | 0.122s | 1.43x |
| 128 | 0.125s | 0.313s | 0.134s | 2.34x |
| 1024 | 0.187s | 1.581s | 0.246s | 6.43x |

### Demand-driven (pull) vs forward (push) evaluation

The Verilator-beating lever (`bfit/prototypes/demand_eval.c`): compute a
signal only when observed, recursing *backward* through its cone, vs a
forward model that evaluates the whole design every cycle. Same netlist,
pull result verified bit-identical to push. 8329-node design, 5000 cycles:

| evaluator | observation | vs forward push |
| :-- | :-- | --: |
| compiled pull cone | every cycle | **26.46x FASTER** |
| pull (interpreted) | every cycle | 10.09x |
| pull (interpreted) | every 100th cycle | 116.47x |
| pull (interpreted) | final only | 167.99x |

Compiled cones skip dead **logic** at push's per-eval speed (no interp
overhead); memoisation/multicycle-collapse additionally skip unobserved
**time**. Honest crossover: on a fully-live design densely observed pull
*loses* ~2.5× to overhead — it wins as dead/unobserved work rises (~70%
break-even), the regime real designs under a test actually sit in.
