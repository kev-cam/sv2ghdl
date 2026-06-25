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
| Bridge rectifier (RC load) | 0 | 0.03 ×18.7 | N/A | 0.04 ×14.0 | 0.25 ×2.2 | 0.25 ×2.2 | 0.56 ×1.0 | 0.56 ×1.0 | N/A |
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
  reference fans out to many outputs as in op-amp mirror banks. Next: diff pair.
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
