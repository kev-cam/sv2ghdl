# Cross-engine performance

One run per circuit, **same netlist on every engine**. Base columns are
simulation **seconds** (lower is better); **+bfit** columns are
`×speedup (rel-L2 error vs that engine's base waveform)`. Transients are sized
so QSPICE solves for **≥3 s** (not startup) and driven with **multi-tone**
inputs so the adaptive engines can't stride through steady state. 🟢 = fastest
open engine/mode in the row; 🔵 = open engine beating both commercial. `brk` =
aborted (timestep collapse).

| Model | # Tx | QSPICE | LTspice | ngspice | Xyce | ngspice+bfit | Xyce+bfit |
| :-- | --: | --: | --: | --: | --: | --: | --: |
| Bridge rectifier (4 diodes) | 0 | 3.7 | 3.3 | 8.3 | 115 | 🟢 ×69 (31%) | ×1.7 (5%) |
| CMOS inverter chain ×100 | 200 | 3.1 | 3.1 | 🔵 1.9 | 6.7 | 🟢 ×2.7 (26%†) | 🔵 ×8.1 (26%†) |
| CMOS ring oscillator ×51 | 102 | brk | 5.5 | 🔵 3.4 | 21 | 🟢 ×11 (70%†) | 🔵 ×63 (70%†) |
| 5T OTA (diff pair + mirror) | 5 | 3.9 | 4.5 | 6.5 | 104 | 🟢 ×50 (64%) | ×0.8 (16%) |
| BJT 3-stage CE amp | 3 | 3.9 | 7.7 | 5.4 | 207 | 🟢 ×45 (100%‡) | ×8.3 (89%‡) |
| 2-stage Miller op-amp | 8 | 3.5 | 4.6 | 38 | 81 | 🟢 ×318 (6%) | ×0.8 (0.16%) |
| BJT cascade ×3000 (breaker) | 3000 | brk | brk | brk | 🟢 96 | — | — |

**Accuracy (rel-L2)** is the time-domain error of the accelerated waveform vs
the same engine's base run. It is amplitude error on the analog rows but
**timing/phase** on the digital ones (†: inverter edge delay, the free-running
oscillator's frequency), so 26–70% there overstates the *functional* error.
‡ the BJT `ce_stage` macromodel is currently broken — the speedup is real but
the waveform is wrong; shown for honesty.

**Reading it.** bfit trades accuracy for speed (SIMPLIS-style). On **ngspice**
the smooth macromodels let the solver drop the forced fine step → large ×; on
**Xyce**, which already strides adaptively, the win shows only on the digital
decks (ring osc ×63) and it can even be slower on small analog. The clean win
is the **op-amp** — bfit's current-mirror legs + the merged diff-pair give
**×318 on ngspice at 6%**, and **0.16%** on Xyce (exact, though no speed win on
a circuit this small). The **breaker** is the other half of the story: at 3000
stiff cascade stages QSPICE, LTspice and ngspice all abort — only Xyce solves
it.

_Models: `gen_models.py`. Open engines (base + bfit + accuracy):
`model_bench.sh`. Commercial: `win_models.sh`. Accuracy: `accuracy.py`._
