# Cross-engine performance

Simulation time in **seconds** for a spread of real circuit styles, run on every
engine on this box. Each cell is `time ×speedup`, where speedup = (serial Xyce
time) / (engine time) — so **×>1 is faster than our Xyce**, and Xyce is ×1.0 by
definition. The **+bfit** columns swap in portable Verilog-AMS macromodels where
bfit recognizes a pattern (today: the BJT CE stage, the MOSFET current mirror,
the CMOS logic inverter, and the full-bridge rectifier), and pass the netlist
through untouched otherwise. 🟢 marks an open
engine (ngspice or Xyce, including +bfit) that is the **fastest** in its row;
🔵 marks one that **beats both commercial engines** (QSPICE/LTspice) but isn't
the outright fastest. `N/A` = the engine can't run that model (see notes). All
same netlist, no per-engine edits.

## Model suite

| Model | # Tx | QSPICE | SIMetrix | LTspice | ngspice | ngspice+bfit | Xyce | Xyce+bfit | Xyce-MPI |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Passive RLC band-pass | 0 | 0.02 ×27.5 | N/A | 0.03 ×18.3 | 1.45 ×0.4 | 1.45 ×0.4 | 0.55 ×1.0 | 0.55 ×1.0 | N/A |
| Bridge rectifier (RC load, short) | 0 | 0.03 ×18.7 | N/A | 0.04 ×14.0 | 0.25 ×2.2 | 0.25 ×2.2 | 0.56 ×1.0 | 0.56 ×1.0 † | N/A |
| CMOS inverter chain ×100 | 200 | 2.07 ×1.9 | N/A | 2.47 ×1.6 | 2.05 ×1.9 | 🟢 0.75 ×5.1 | 3.86 ×1.0 | 🔵 0.85 ×4.5 | N/A |
| CMOS ring oscillator ×51 | 102 | N/A | N/A | 5.63 ×3.5 | 4.86 ×4.1 | 🟢 0.45 ×44 | 19.78 ×1.0 | 🟢 0.45 ×44 | N/A |
| 5T OTA (diff pair + mirror) | 5 | 0.03 ×15.3 | N/A | 0.05 ×9.2 | 0.25 ×1.8 | 0.15 ×3.1 | 0.46 ×1.0 | 0.45 ×1.0 | N/A |
| 2-stage Miller op-amp (CMOS) | 7 | 0.02 ×17.0 | N/A | 0.06 ×5.7 | 1.09 ×0.3 | 0.11 ×3.1 | 0.34 ×1.0 | 0.31 ×1.1 | N/A |
| BJT 3-stage amp | 3 | 0.46 ×7.3 | N/A | 0.53 ×6.3 | 1.56 ×2.2 | 🟢 0.25 ×13.4 | 3.36 ×1.0 | 🔵 0.45 ×7.5 | N/A |
| SIMetrix mixed-signal A↔D cosim | digital | N/A | N/A | N/A | N/A | N/A | 🟢 0.87 ×1.0 | N/A | N/A |

bfit substitutes the **CE stages** in the BJT amp (tuned macromodel: ngspice
1.56→0.25, Xyce 3.36→0.45 — the fastest cell in its row) and the **current
mirrors** in the OTA and the op-amp. The mirror model is two-part (I→V / V→I)
with rail compliance and handles both polarities and fan-out: in the **2-stage
op-amp** it replaces an NMOS bias bank (one reference feeding the tail + the
2nd-stage sink) *and* a PMOS load mirror, cutting **ngspice 1.09→0.11 s (≈10×)**
while tracking the output to ~0.01% — the behavioral mirrors have no internal
pole, so the solver drops the forced fine timestep and strides. (Xyce barely
moves: it was already taking adaptive steps.) On the **digital** circuits bfit
substitutes the **CMOS inverter** with a programmed-conductance logic gate (no
`tanh`): the inverter chain drops 2.7× and the **ring oscillator 44× on Xyce**
(19.78→0.45 s, still oscillating) — here *both* engines win, because the forced
fine step the digital decks demand is exactly what the smooth gate removes. On
the models with no pattern yet, bfit passes the netlist through unchanged, so it
never costs anything. The commercial engines win the tiny analog circuits
(sub-0.1 s = process startup, not solve), and the **SIMetrix mixed-signal model
runs only in the Xyce+nvc stack**.

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

## † Rectifier flips on a realistic transient (2026-06-26)

The suite rectifier above is a short startup-bound deck, so bfit shows nothing and
QSPICE's near-zero startup wins. Two fixes change the picture: (1) the `bridge_rect`
recognizer was **orphaning the AC input nodes** (no DC path to ground) — ngspice hid
it under gmin but Xyce rejected it as singular, so the old `Xyce+bfit ×1.0` was a
*silent crash*, not a no-op. Fixed (`120243b`: restore the diodes' reverse-leak path).
(2) On a realistic long transient (`rect_big.cir`, 12 V/60 Hz, 600 ms) the diodes'
brief conduction windows force fine steps that the smooth B-source removes:

| Bridge rectifier, 600 ms | QSPICE | ngspice | ngspice+bfit | Xyce | Xyce+bfit |
| :--- | ---: | ---: | ---: | ---: | ---: |
| time (s) | 0.25 | 0.38 | 🟢 **0.05** | 4.09 | 🔵 **0.23** |
| ×vs Xyce | ×16 | ×11 | **×82** | ×1.0 | ×18 |

**ngspice+bfit (0.05 s) now beats QSPICE (0.25 s) 5×** and Xyce+bfit ties it — the
rectifier flips from a QSPICE win to ours once the transient is long enough to matter.
Accuracy: V_DC +5.8 % (the fixed 1.2 V bridge drop, tunable via `__vdrop__`).

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
  (suite), `gen_amp.py` (cascade). Sub-0.1 s cells are startup-dominated.
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
