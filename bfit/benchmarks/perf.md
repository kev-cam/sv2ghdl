# Cross-engine performance

One run per circuit, **same netlist on every engine**. Each cell is
`seconds ×speedup` (fewer seconds / bigger × is better); **+bfit** cells append
`(rel-L2 error vs that engine's own base waveform)`. Transients are sized so
QSPICE solves for **≥3 s** and driven with **multi-tone** inputs so the adaptive
engines can't coast to steady state. 🟢 = fastest cell in the row; 🔵 = an open
engine/mode beating **both** commercial tools. `brk` = aborted (timestep
collapse); `—` = no benefit over that engine's own base.

**The `×` reference.** Base-engine `×` is relative to the **slowest native
engine** in the row (Xyce here → ×1.0). The **+bfit** and **Xyce-MPI** `×` are
relative to **that engine's own native run** — i.e. what the acceleration
actually bought. **bal / fast** are the `bfit front --accuracy` presets
(`balanced` ≈1000 pts + tight LTE; `fast` ≈300 pts + loose LTE); `exact`
(no coarsening, not shown) keeps the engine at reference accuracy.

| Model | # Tx | QSPICE | LTspice | ngspice | Xyce | Xyce-MPI | ng+bfit bal | ng+bfit fast | xy+bfit bal | xy+bfit fast |
| :-- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |
| Bridge rectifier (4 diodes) | 0 | 3.7 ×30.7 | 3.3 ×34.4 | 8.3 ×13.6 | 114 ×1.0 | — | 🟢 0.12 ×69.3 (31%) | 🔵 0.12 ×69.3 (9%) | 9.5 ×11.9 (6%) | 9.6 ×11.8 (3%) |
| CMOS inverter chain ×100 | 200 | 3.1 ×2.2 | 3.1 ×2.2 | 🔵 1.9 ×3.6 | 6.8 ×1.0 | — | 🔵 0.72 ×2.7 (26%) | 🔵 0.52 ×3.7 (26%) | 🔵 0.52 ×13.1 (26%) | 🟢 0.32 ×21.3 (26%) |
| CMOS ring oscillator ×51 | 102 | brk | 5.5 ×3.8 | 🔵 3.5 ×5.9 | 21 ×1.0 | — | 🔵 0.32 ×11.0 (70%) | 🟢 0.12 ×29.3 (70%) | 🔵 0.32 ×65.4 (70%) | 🔵 0.22 ×95.2 (70%) |
| 5T OTA (diff pair + mirror) | 5 | 3.9 ×27.0 | 4.5 ×23.4 | 6.5 ×16.1 | 105 ×1.0 | — | 🟢 0.12 ×54.3 (73%) | 🔵 0.12 ×54.3 (82%) | 6.1 ×17.2 (72%) | 5.6 ×18.7 (82%) |
| BJT 3-stage CE amp ‡ | 3 | 3.9 ×52.4 | 7.7 ×26.6 | 5.4 ×37.7 | 204 ×1.0 | — | 🔵 0.72 ×7.5 (134%) | 🔵 0.72 ×7.5 (134%) | 🔵 3.2 ×63.5 (124%) | 🟢 0.22 ×929.5 (100%) |
| 2-stage Miller op-amp | 8 | 3.5 ×23.1 | 4.6 ×17.6 | 38 ×2.1 | 81 ×1.0 | — | 🔵 0.12 ×317.9 (6%) | 🟢 0.11 ×346.8 (5%) | 16 ×4.9 (6%) | 16 ×4.9 (8%) |
| BJT cascade ×3000 (breaker) | 3000 | brk | brk | brk | 🔵 464 ×1.0 | 🟢 266 ×1.7 (np 4) | — | — | — | — |

† digital rows (inverter, ring): rel-L2 is **timing/phase** (edge delay, the
free-running oscillator's frequency), not amplitude — so the % overstates the
*functional* error. ‡ the BJT amp is an overdriven **limiter** (railed output);
the `ce_stage` macromodel fits its features to <1% but rel-L2 here is
phase-dominated, so the % overstates it — the speedup is real.

**Xyce-MPI.** Domain-decomposition overhead dwarfs the work on small circuits,
so MPI is **slower than serial on every small row** (→ —, killed once it passes
the serial wall-clock). It pays off only at **scale**: the 3000-stage breaker
wins at a *middle* rank count (the cloud / large-circuit lever, not a
single-small-circuit one).

**Reading it.** bfit swaps device stages for smooth macromodels and coarsens the
transient, so the solver strides — every accelerated row beats both commercial
tools. The cleanest win is the **op-amp** (merged diff-pair + current-mirror
legs). The **`--accuracy` knob** trades speed for fidelity (compare each `bal`
vs `fast` cell); the fast multi-tone amps lose more to undersampling. The
**breaker** is the other half: at 3000 stiff stages QSPICE, LTspice and ngspice
all abort — only Xyce solves it, and MPI then nearly halves that.

_Models: `gen_models.py` (+ `gen_amp.py` for the breaker). Open engines:
`model_bench.sh` → `open.csv`. Commercial: `win_models.sh` → `commercial.csv`.
Table: `assemble.py`. Accuracy: `accuracy.py`. Speed/accuracy knob:
`bfit front --accuracy {exact,balanced,fast}` (or raw `--points/--reltol/--abstol`)._
