# Cross-engine performance

Simulation time in **seconds** for a spread of real circuit styles, run on every
engine on this box. Each cell is `time ×speedup`, where speedup = (serial Xyce
time) / (engine time) — so **×>1 is faster than our Xyce**, and Xyce is ×1.0 by
definition. 🟢 marks the model whose fastest engine is open-source (ngspice or
Xyce). `N/A` = the engine can't run that model (see notes). All same netlist, no
per-engine edits.

## Model suite

| Model | # Tx | QSPICE | SIMetrix | LTspice | ngspice | Xyce | Xyce-MPI |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Passive RLC band-pass | 0 | 0.02 ×27.5 | N/A | 0.03 ×18.3 | 1.45 ×0.4 | 0.55 ×1.0 | N/A |
| Bridge rectifier (RC load) | 0 | 0.03 ×18.7 | N/A | 0.04 ×14.0 | 0.25 ×2.2 | 0.56 ×1.0 | N/A |
| CMOS inverter chain ×100 | 200 | 2.07 ×1.9 | N/A | 2.47 ×1.6 | 🟢 2.05 ×1.9 | 3.86 ×1.0 | N/A |
| CMOS ring oscillator ×51 | 102 | N/A | N/A | 5.63 ×3.5 | 🟢 4.86 ×4.1 | 19.78 ×1.0 | N/A |
| 5T OTA (diff pair + mirror) | 5 | 0.03 ×15.3 | N/A | 0.05 ×9.2 | 0.25 ×1.8 | 0.46 ×1.0 | N/A |
| BJT 3-stage amp | 3 | 0.46 ×7.3 | N/A | 0.53 ×6.3 | 1.56 ×2.2 | 3.36 ×1.0 | N/A |
| SIMetrix mixed-signal A↔D cosim | digital | N/A | N/A | N/A | N/A | 🟢 0.87 ×1.0 | N/A |

ngspice wins the two digital/oscillator circuits outright; the commercial engines
win the tiny analog ones (where sub-0.1 s times are dominated by process startup,
not solve); and the **SIMetrix mixed-signal model runs only in the Xyce+nvc
stack** — no other engine here does the analog↔digital cosim.

## bfit acceleration (recognized patterns)

Where bfit recognizes a pattern it swaps in a portable `ce_stage` Verilog-AMS
macromodel and takes adaptive timesteps — the same substitution accelerates
*any* engine. On an N-stage BJT CE amplifier cascade (`.tran 20n 2m`):

| Stages | # Tx | ngspice | ngspice+bfit | Xyce | Xyce+bfit | Xyce-MPI (best np) |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 3 | 3 | 1.55 ×1.0 | 0.25 ×6.2 | 3.26 ×1.0 | 0.45 ×7.2 | 19.33 ×0.2 (np2) |
| 30 | 30 | 9.36 ×1.0 | 0.45 ×20.8 | 13.26 ×1.0 | 2.15 ×6.2 | 51.65 ×0.3 (np4) |
| 100 | 100 | 36.28 ×1.0 | 8.46 ×4.3 | 45.69 ×1.0 | 12.66 ×3.6 | 150.24 ×0.3 (np6) |
| 300 | 300 | 158.79 ×1.0 | 82.83 ×1.9 | 185.31 ×1.0 | 87.43 ×2.1 | N/A |

Here the +bfit speedup is relative to the *same engine* plain. bfit's lead is
largest at small/mid scale (~21× on ngspice at 30 stages) and erodes as the
cascade deepens.

## Scaling wall — who survives?

Past a few hundred series high-gain stages the cascade turns numerically stiff.
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

- **Speedup baseline.** `×N.M` = (serial Xyce time) / (this engine's time) unless
  stated otherwise (the bfit table uses same-engine-plain). Xyce = ×1.0.
- **Methodology.** Each cell is engine simulation time, cross-environment launch
  excluded — Windows engines (QSPICE, LTspice) self-report "Total elapsed time";
  Linux engines (ngspice, Xyce) are inner wall-clock inside WSL, min of runs.
  Measured on a Threadripper PRO 5955WX (16C/32T); Xyce serial `-O3`. Generators:
  `gen_models.py` (suite) and `gen_amp.py` (cascade); `mpisweep.sh` for the MPI
  column. Sub-0.1 s cells are startup-dominated, not solve-dominated.
- **SIMetrix = N/A.** The installed edition is the free **SIMPLIS/Elements**,
  which has no headless simulator. The mixed-signal row, however, *is* a SIMetrix
  design: `simetrix_cosim.pl` translates a SIMetrix `.net` (digital A-devices →
  VHDL, analog → Xyce) and runs it on the Xyce+nvc runtime. The real
  **SIMetrix flyback (`fly.net`, UC3844 controller, 18 digital A-devices)** binds
  7/7 analog↔digital boundaries and converges in this stack — the timed row uses
  the lighter `a2d` round-trip; nothing else here runs either.
- **Xyce-MPI = N/A on the suite.** A fixed ~15 s Trilinos/MPI solver-init plus
  inter-rank communication exceeds these models' entire serial runtime, so MPI
  never wins (best case ~3× slower); see the bfit table for the full np=2..16
  sweep. MPI earns its keep on large meshed designs and cloud scale-out.
- **N/A cells** also cover engines that rejected a netlist unmodified (QSPICE on
  the ring oscillator) — the same portable deck ran everywhere else.

_Generated with `benchmarks/run_bench.sh`, `mpisweep.sh`, and the model suite
runner; see `README.md`._
