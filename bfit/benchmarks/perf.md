# Cross-engine performance

Simulation time in **seconds** for a spread of real circuit styles, run on every
engine on this box. Each cell is `time ×speedup`, where speedup = (serial Xyce
time) / (engine time) — so **×>1 is faster than our Xyce**, and Xyce is ×1.0 by
definition. The **+bfit** columns swap in a portable `ce_stage` Verilog-AMS
macromodel where bfit recognizes the pattern (today: the BJT CE stage). 🟢 marks
the model whose fastest engine is open-source (ngspice or Xyce). `N/A` = the
engine can't run that model (see notes). All same netlist, no per-engine edits.

## Model suite

| Model | # Tx | QSPICE | SIMetrix | LTspice | ngspice | ngspice+bfit | Xyce | Xyce+bfit | Xyce-MPI |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Passive RLC band-pass | 0 | 0.02 ×27.5 | N/A | 0.03 ×18.3 | 1.45 ×0.4 | N/A | 0.55 ×1.0 | N/A | N/A |
| Bridge rectifier (RC load) | 0 | 0.03 ×18.7 | N/A | 0.04 ×14.0 | 0.25 ×2.2 | N/A | 0.56 ×1.0 | N/A | N/A |
| CMOS inverter chain ×100 | 200 | 2.07 ×1.9 | N/A | 2.47 ×1.6 | 🟢 2.05 ×1.9 | N/A | 3.86 ×1.0 | N/A | N/A |
| CMOS ring oscillator ×51 | 102 | N/A | N/A | 5.63 ×3.5 | 🟢 4.86 ×4.1 | N/A | 19.78 ×1.0 | N/A | N/A |
| 5T OTA (diff pair + mirror) | 5 | 0.03 ×15.3 | N/A | 0.05 ×9.2 | 0.25 ×1.8 | N/A | 0.46 ×1.0 | N/A | N/A |
| BJT 3-stage amp | 3 | 0.46 ×7.3 | N/A | 0.53 ×6.3 | 1.56 ×2.2 | 🟢 0.25 ×13.4 | 3.36 ×1.0 | 0.45 ×7.5 | N/A |
| SIMetrix mixed-signal A↔D cosim | digital | N/A | N/A | N/A | N/A | N/A | 🟢 0.87 ×1.0 | N/A | N/A |

ngspice wins the two digital/oscillator circuits outright; the commercial engines
win the tiny analog ones (where sub-0.1 s times are dominated by process startup,
not solve); **bfit makes the BJT amp the fastest cell in its row on either open
engine**; and the **SIMetrix mixed-signal model runs only in the Xyce+nvc stack**
— no other engine here does the analog↔digital cosim.

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
- **bfit scales further than one row shows.** On a deeper CE cascade the +bfit
  lead grows to ~21× on ngspice and ~7× on Xyce at 30 stages, then erodes as the
  cascade stiffens (~2× by 300 stages). bfit recognizes only the CE stage today;
  next patterns: current mirror, differential pair.
- **Methodology.** Each cell is engine simulation time, cross-environment launch
  excluded — Windows engines (QSPICE, LTspice) self-report "Total elapsed time";
  Linux engines (ngspice, Xyce) are inner wall-clock inside WSL, min of runs.
  Threadripper PRO 5955WX (16C/32T); Xyce serial `-O3`. Generators: `gen_models.py`
  (suite), `gen_amp.py` (cascade). Sub-0.1 s cells are startup-dominated.
- **SIMetrix = N/A.** The installed edition is the free **SIMPLIS/Elements**,
  which has no headless simulator. The mixed-signal row, however, *is* a SIMetrix
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
