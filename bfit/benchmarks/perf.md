# Cross-engine performance

Every cell is **each engine's best effort** — its own adaptive stepping with a
*sensible* output step, NOT an artificially fine `tstep` (which only handicaps
ngspice, the one engine that honours it as a step ceiling; QSPICE and Xyce ignore
it). Times in **seconds** (min of repeated runs, cross-environment launch excluded).
Speedup is **per-engine**: `native → bfit` on the *same* engine. The **accuracy loss**
column is the bfit macromodel's error vs the golden (native, fine-step) result. The
Xyce column uses the best of **bfit / merge / table-driven** (here bfit is the
operative tool; merge/table target structures — cascode stacks, exp-junction
convergence — not on the critical path of this suite).

## Model suite (best effort, 2026-06-26)

QSPICE / ngspice-native / LTspice are the **baselines to beat**; bfit is *our*
preprocessor, so it's applied to the open engines (ngspice, Xyce). It is portable
(same macromodel netlist runs anywhere), but a QSPICE user wouldn't reach for it, so
QSPICE is shown native only.

| Model *(bfit pattern)* | QSPICE | ngspice n→bfit | Xyce n→best | bfit accuracy loss |
| :--- | ---: | ---: | ---: | ---: |
| RLC band-pass *(none)* | 0.02 | 0.13 | 0.33 | — |
| Op-amp follower *(current_mirror)* | 0.02 | 0.12→0.12 **×1** | 0.43→0.23 ×1.9 | 0.01 % (ΔTHD 0.001 pt) |
| 5T OTA *(current_mirror)* | 0.03 | 0.12→0.13 **×1** | 0.23→0.23 **×1** | 1.8 % (ΔTHD 0.43 pt) |
| Bridge rectifier *(bridge_rect)* | 0.03 | 0.12→0.12 **×1** | 0.33→0.33 **×1** | 5.9 % V_DC |
| BJT 3-stage amp *(ce_stage)* | 0.04 | 0.12 | 0.33 | ⚠️ **96 % — model broken** |
| CMOS inverter ×100 *(cmos_inv)* | 3.11 | 1.83→0.73 ×2.5 | 6.73→0.83 **×8.1** | digital timing ‡ |
| CMOS ring osc ×51 *(cmos_inv)* | **brk** ‖ | 3.43→0.32 ×11 | 20.6→0.33 **×62** | ⚠️ **freq −48 %** ‡ |

‖ QSPICE *aborts* the device-level ring oscillator (timestep collapse). (Aside: the
portable bfit netlist does run on QSPICE — 0.09 s — so bfit can rescue circuits even a
commercial engine can't take, but that's a portability note, not a QSPICE result.)
‡ the `cmos_inv` delay is uncalibrated, so the bfit ring oscillates at ~half the golden
frequency (1.13 GHz → 0.59 GHz) — fast but wrong-timing until tuned; the inverter chain
inherits the same delay error.

**The honest read.** bfit's speedup is real **only where fine timesteps are physically
required** — the digital circuits, whose switching forces fine steps no engine can skip.
There the smooth gate wins **2.5–62×** and even rescues the ring oscillator on QSPICE.
On the **smooth analog** circuits best-effort native is already startup-bound (<0.5 s) —
there is no solver time to remove — so bfit is **~1×** and only *costs* accuracy
(0.01–5.9 %). Earlier large analog "wins" (op-amp ≈10×, the rectifier flip) were an
artifact of an over-fine `tstep` that throttled ngspice; with best-effort stepping they
vanish — the coarse-step native is bit-identical to the fine-step golden, so the fine
step bought nothing but slowdown. Two open issues: the **ce_stage / BJT-amp model is
broken** (~0.1× gain, 96 % error — held), and the **digital wins carry a timing cost**
(ring-osc frequency −48 %) pending `cmos_inv` delay calibration.

*(LTspice/SIMetrix/MPI columns dropped from this best-effort pass: LTspice not re-run;
SIMetrix GUI-bound; MPI is scale-out, characterized separately below.)*

## Scaling wall — who survives?

Past a few hundred series high-gain stages a BJT cascade turns numerically stiff.
Short-transient capacity probe; wall seconds if it completed, `brk` if it aborted
(timestep → ~1e-19):

| Stages | QSPICE | LTspice | ngspice | Xyce | Xyce+bfit |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 300 | 8 s | 5 s | 3 s | 4 s | 3 s |
| 1000 | brk | 53 s | brk | 18 s | 17 s |
| 3000 | brk | brk | brk | 235 s | 236 s |

By 1000 stages QSPICE and ngspice abort; LTspice dies by 3000; **only Xyce
reaches 3000** — the robustness its framework cost buys.

## Small→large crossover vs QSPICE (CMOS inverter chain, KLU)

QSPICE owns the small end; the crossover is **~N = 2000**, after which Xyce+KLU pulls
away — near-linear (~N^1.1) vs QSPICE's superlinear (~N^1.4). On *benign* chains QSPICE
does not abort, it just scales badly (the "breaks at N~1000" story is for *stiff*
cascades, above). Wall seconds, `.tran 0.1n 5n`:

| N inverters | devices | QSPICE | Xyce+KLU | winner |
| ---: | ---: | ---: | ---: | :--- |
| 2,000   | 4 k   | 3.0 | 2.0 | ~even |
| 10,000  | 20 k  | 28  | **7.8** | Xyce 3.6× |
| 50,000  | 100 k | 188 | **49**  | Xyce 3.8× |
| 200,000 | 400 k | 370 | (setup-bound) | — |

## Accuracy — does the macromodel match the golden (full-device) result?

A speedup is worthless if the answer is wrong, so every `+bfit` cell carries an
accuracy cost. `benchmarks/accuracy.py` compares the bfit run to the native-device
**golden** on the same engine and reports two numbers: **rel-L2 err%**
(‖bfit−golden‖/‖golden‖ over the steady-state window — universal, AC or DC), and
**THD%** (golden vs bfit) where the output is tone-driven — the analog signal-path
spec. Measured (ngspice golden = full device model):

| Model | bfit pattern | THD golden→bfit | rel-L2 / level err |
| :--- | :--- | ---: | ---: |
| 2-stage Miller op-amp (follower) | current_mirror | 0.008% → 0.009% (ΔTHD **0.001 pt**) | **0.01%** |
| 5T OTA (large-signal) | current_mirror | 31.46% → 31.16% (ΔTHD **0.30 pt**) | 1.8% |
| Bridge rectifier (RC load) | bridge_rect | N/A (DC+ripple, not a tone) | **5.8%** V_DC |

The analytic mirror models are essentially exact (op-amp tracks to 0.01% and
reproduces distortion to a thousandth of a percent; the OTA holds 0.3 pt even driven
to 31% THD). The rectifier's 5.8% is the fixed 1.2 V bridge-drop approximation —
tunable via `__vdrop__`. **THD% is the right league-table accuracy column for the
signal-path rows**; rectifier/PSU rows use DC-level error (THD undefined on a
non-tone output), and digital rows (inverter/ring) use propagation-delay /
oscillation-frequency error. NB `ce_stage` accuracy is **tuning-dependent**: untuned
(empty cache) the BJT amp comes out at ~0.1× gain — report ce_stage rows only with
their tuned cache.

---

## Notes

- **Speedup baseline.** `×N.M` = (serial Xyce time) / (this engine's time);
  Xyce = ×1.0. The +bfit columns use the same baseline, so they read directly
  against every other engine.
- **How bfit accelerates.** Where it recognizes a pattern it substitutes a
  portable Verilog-AMS macromodel and — because those models are smooth — drops
  the forced max timestep and coarsens output, letting the solver stride. On a
  deeper CE cascade the +bfit lead grows to ~21× on ngspice and ~7× on Xyce at
  30 stages, then erodes as the cascade stiffens. Patterns today: CE stage and
  the MOSFET current mirror — a two-part I→V (reference current → overdrive
  voltage across a sense resistor, normalized on the smallest device) / V→I
  (each output = size·overdrive, going resistive near the rail) model, so one
  reference fans out to many outputs as in op-amp mirror banks. Patterns today:
  `ce_stage`, `current_mirror`, `cmos_inv` (logic gate), `bridge_rect`. Next: diff pair.
- **Table mode (`bfit table`).** A separate, SIMPLIS-style path (orthogonal to the
  pattern substitution above and to `--merge`): replace *every* device with a
  table/PWL model from the start and never attempt the accurate analytical/JIT
  solve — trades accuracy for speed and rock-solid convergence. Diodes today;
  MOS/BJT pending their 2-D table emitters. Bench + numbers in `benchmarks/table/`.
- **Methodology.** Each cell is engine simulation time, cross-environment launch
  excluded — Windows engines (QSPICE, LTspice) self-report "Total elapsed time";
  Linux engines (ngspice, Xyce) are inner wall-clock inside WSL, min of runs.
  Threadripper PRO 5955WX (16C/32T); Xyce serial `-O3`. Generators: `gen_models.py`
  (suite), `gen_amp.py` (cascade).
- **Best-effort stepping (no cheating).** Each `.tran` uses a sensible output step
  (~period/50), not an artificially fine `tstep`. A fine `tstep` only throttles ngspice
  (it honours it as a step ceiling; QSPICE/Xyce ignore it) — that artifact produced the
  old analog "speedups", now removed. Fine steps are kept only where physics forces them
  (the digital decks). Smooth analog circuits are intrinsically startup-bound on
  QSPICE/Xyce and stay that way — we do **not** size-scale them to manufacture solver
  work. The honest consequence: bfit's real speedup shows only on the physics-stiff /
  digital circuits, with its accuracy cost reported alongside.
- **SIMetrix = N/A.** SIMetrix *does* have a non-GUI mode, but it runs through a
  standalone `SIM` console binary and requires **network** licensing. The free
  **SIMPLIS/Elements 9.2** install here ships only the GUI `SIMetrix.exe` (engine
  in `SIMCore.dll`, no `SIM.exe`) under a node-locked Intro license, so headless
  runs are doubly unavailable — it would take a licensed SIMetrix Pro with
  network licensing. The mixed-signal row, however, *is* a SIMetrix
  design: `simetrix_cosim.pl` translates a SIMetrix `.net` (digital A-devices →
  VHDL, analog → Xyce) and runs it on the Xyce+nvc runtime. The real
  **SIMetrix flyback (`fly.net`, UC3844, 18 digital A-devices)** binds 7/7
  analog↔digital boundaries and converges in this stack — the timed row uses the
  lighter `a2d` round-trip; nothing else here runs either.
- **Xyce-MPI = N/A.** A fixed ~15 s Trilinos/MPI solver-init plus inter-rank
  communication exceeds these models' entire serial runtime. Across an np=2..16
  sweep the optimum rank count grows with size (np2→4→6) but never beats serial
  here (best ~3× slower); MPI earns its keep on large meshed designs and cloud
  scale-out. See `mpisweep.sh`.
- **N/A** also covers engines that rejected a netlist unmodified (QSPICE on the
  ring oscillator) — the same portable deck ran everywhere else.

_Generated with the `benchmarks/` harness (`gen_models.py` + suite runners,
`gen_amp.py` + `run_bench.sh`, `mpisweep.sh`); see `README.md`._
