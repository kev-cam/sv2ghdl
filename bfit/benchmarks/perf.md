# Cross-engine performance

One run per circuit, **same netlist on every engine**. Each cell is
`seconds ×speedup` (fewer seconds / bigger × is better); **+bfit** cells append
`(signal-to-error ratio in dB vs that engine's own base; higher = better)`.
Transients are sized so
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
| Bridge rectifier (4 diodes) | 0 | 3.7 ×30.8 | 3.3 ×34.5 | 8.4 ×13.5 | 114 ×1.0 | — | 🟢 0.11 ×76.6 (+10 dB) | 🔵 0.11 ×76.6 (+21 dB) | 9.5 ×12.0 (+25 dB) | 9.4 ×12.1 (+31 dB) |
| CMOS inverter chain ×100 | 200 | 3.1 ×2.0 | 3.1 ×2.0 | 🔵 1.8 ×3.4 | 6.2 ×1.0 | — | 🔵 0.72 ×2.5 (+12 dB) | 🔵 0.52 ×3.5 (+12 dB) | 🔵 0.52 ×12.0 (+12 dB) | 🟢 0.32 ×19.5 (+12 dB) |
| CMOS ring oscillator ×51 | 102 | brk | 5.5 ×3.6 | 🔵 3.5 ×5.6 | 20 ×1.0 | — | 🔵 0.32 ×11.0 (+3 dB) | 🟢 0.11 ×32.0 (+3 dB) | 🔵 0.32 ×61.2 (+3 dB) | 🔵 0.22 ×89.0 (+3 dB) |
| 5T OTA (diff pair + mirror) | 5 | 3.9 ×26.6 | 4.5 ×23.1 | 6.6 ×15.7 | 104 ×1.0 | — | 🔵 0.12 ×55.2 (+3 dB) | 🟢 0.11 ×60.2 (+2 dB) | 7.6 ×13.6 (+3 dB) | 7.2 ×14.4 (+2 dB) |
| BJT 3-stage CE amp ‡ | 3 | 3.9 ×52.8 | 7.7 ×26.8 | 5.4 ×38.0 | 206 ×1.0 | — | 🔵 0.72 ×7.5 (-3 dB) | 🔵 0.72 ×7.5 (-3 dB) | 🔵 3.1 ×66.0 (-2 dB) | 🟢 0.22 ×936.5 (0 dB) |
| 2-stage Miller op-amp | 8 | 3.5 ×22.8 | 4.6 ×17.4 | 38 ×2.1 | 80 ×1.0 | — | 🔵 0.12 ×316.3 (+24 dB) | 🟢 0.11 ×345.0 (+26 dB) | 21 ×3.8 (+25 dB) | 21 ×3.8 (+22 dB) |
| BJT cascade ×3000 (breaker) | 3000 | brk | brk | brk | 🔵 474 ×1.0 | 🟢 245 ×1.9 (np 4) | — | — | — | — |

**Accuracy = signal-to-error ratio in dB** (`SER = −20·log₁₀(rel-L2)`); higher is
better, +25 dB ≈ 6% error, 0 dB = error equals signal. It is **phase-sensitive**,
so a macromodel that matches amplitude but lags in phase scores low: † the
digital rows (inverter, ring) are dominated by **timing** (edge delay, the
oscillator's frequency), not amplitude; ‡ the BJT amp is an overdriven
**limiter** whose macromodel matches the clipping levels to <1% but sits near
0 dB on phase alone. A delay-aligned SER (removing benign propagation delay) is
the honest fix for the amps — coming next.

**Xyce-MPI.** Domain-decomposition overhead dwarfs the work on small circuits,
so MPI is **slower than serial on every small row** (→ —, killed once it passes
the serial wall-clock). It pays off only at **scale**: the 3000-stage breaker
wins at a *middle* rank count (the cloud / large-circuit lever, not a
single-small-circuit one).

**Behavioral-assist (Xyce column).** Each Xyce cell is the faster of *plain*
Xyce and Xyce with the quiescence-bypass stack (`XYCE_BYPASS=1e-12
XYCE_FROZEN_STATE=1`), chosen per row and verified correct against the plain run.
It wins on the **digital/switching** rows — inverter chain 6.8→6.3 s (−8%), ring
oscillator 21→19 s (−8%) — where most devices sit quiescent between edges; it is
correctly rejected on the **analog** rows (op-amp/OTA: no quiescent set, and
frozen state corrupts slow analog nodes) and is N/A on the diode/BJT rows
(MOSFET1-only). `XYCE_FROZEN_JAC` is excluded — it segfaults when stacked and
adds no speed.

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

## Cascade-depth stress runs

The N-stage cascade sweep (`run_bench.sh`) is a separate lane and writes
**date-named snapshots** next to this page — `cascade-YYYY-MM-DD.md` — so
each run is preserved rather than overwriting this table:
- [2026-07-06](cascade-2026-07-06.md)
