# Cross-engine performance

One run per circuit, **same netlist on every engine**. Base columns are
simulation **seconds** (lower is better); **+bfit** columns are
`×speedup (rel-L2 error vs that engine's base waveform)`. Transients are sized
so QSPICE solves for **≥3 s** (not startup) and driven with **multi-tone**
inputs so the adaptive engines can't stride through steady state. 🟢 = fastest
open engine/mode in the row; 🔵 = that mode also beats **both** commercial
engines. `brk` = aborted (timestep collapse).

| Model | # Tx | QSPICE | LTspice | ngspice | Xyce | ngspice+bfit | Xyce+bfit |
| :-- | --: | --: | --: | --: | --: | --: | --: |
| Bridge rectifier (4 diodes) | 0 | 3.7 | 3.3 | 8.3 | 115 | 🔵 ×119 (31%) | ×12 (2.7%) |
| CMOS inverter chain ×100 | 200 | 3.1 | 3.1 | 1.9 | 6.7 | ×3 (26%†) | 🔵 ×16 (26%†) |
| CMOS ring oscillator ×51 | 102 | brk | 5.5 | 3.4 | 21 | 🔵 ×15 (70%†) | ×64 (70%†) |
| 5T OTA (diff pair + mirror) | 5 | 3.9 | 4.5 | 6.5 | 104 | 🔵 ×93 (74%) | ×17 (74%) |
| BJT 3-stage CE amp | 3 | 3.9 | 7.7 | 5.4 | 207 | ×8 (134%‡) | 🔵 ×627 (110%‡) |
| 2-stage Miller op-amp | 8 | 3.5 | 4.6 | 38 | 81 | 🔵 ×475 (6%) | ×5 (6%) |
| BJT cascade ×3000 (breaker) | 3000 | brk | brk | brk | 🟢 96 | — | — |

**Accuracy (rel-L2)** is the time-domain error of the accelerated waveform vs
the same engine's base run. It is amplitude error on the analog rows but
**timing/phase** on the digital ones (†: inverter edge delay, the free-running
oscillator's frequency), so 26–70% there overstates the *functional* error.
‡ the BJT amp is an overdriven 3-stage **limiter** (railed output); the
`ce_stage` macromodel fits its features to <1% but rel-L2 here is
phase-dominated, so 110–134% overstates the functional error — the speedup is
real.

**Reading it.** bfit trades accuracy for speed (SIMPLIS-style): it swaps the
transistor stages for smooth macromodels and coarsens the transient to ~1000
points, so the solver strides instead of resolving every device. On **ngspice**
the coarsened step is honoured directly (op-amp **×475 at 6%**). **Xyce** needs
one extra nudge — its local-truncation-error control keeps refining past the
coarse step, so bfit loosens Xyce's LTE for accelerated decks; with that, Xyce
now strides on the **analog** rows too (5T OTA ×0.8 → **×17**, op-amp ×0.8 →
**×5**) instead of being slower than native. The price is that Xyce's accuracy
converges to ngspice's undersampled level (op-amp 0.16% → 6%); when accuracy
beats speed, the un-coarsened path keeps Xyce exact. The clean win is the
**op-amp** — bfit's current-mirror legs + the merged diff-pair give a real
speedup on both engines at ~6%. The **breaker** is the other half of the story:
at 3000 stiff cascade stages QSPICE, LTspice and ngspice all abort — only Xyce
solves it.

_Models: `gen_models.py`. Open engines (base + bfit + accuracy):
`model_bench.sh`. Commercial: `win_models.sh`. Accuracy: `accuracy.py`._
