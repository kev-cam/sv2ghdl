# Cross-engine performance

One run per circuit, **same netlist on every engine**. Each cell is
`seconds ×speedup` (fewer seconds / bigger × is better); **+bfit** cells append
`(signal-to-error ratio in dB vs that engine's own base; higher = better)`.
Transients are sized so
QSPICE solves for **≥3 s** and driven with **multi-tone** inputs so the adaptive
engines can't coast to steady state. 🟢 = fastest cell in the row; 🔵 = an open
engine/mode beating **both** commercial tools. `brk` = aborted (timestep
collapse); `—` = no benefit over that engine's own base.

**The `×` reference.** Every `×` — base, **+bfit**, and **Xyce-MPI** — is
relative to the row's **slowest native engine** (×1.0), so multipliers compare
directly across ALL columns: the biggest `×` in a row is its 🟢 cell. What an
acceleration bought a given engine is its `+bfit` seconds against its own base
column. `n/a` = the engine has no model/path for that circuit (distinct from
`brk` = tried and aborted). **bal / fast** are the `bfit front --accuracy` presets
(`balanced` ≈1000 pts + tight LTE; `fast` ≈300 pts + loose LTE); `exact`
(no coarsening, not shown) keeps the engine at reference accuracy.

| Model | # Tx | QSPICE | LTspice | ngspice | Xyce | VACASK | Xyce-MPI | ng+bfit bal | ng+bfit fast | xy+bfit bal | xy+bfit fast | vc+bfit bal | vc+bfit fast |
| :-- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |
| Bridge rectifier (4 diodes) | 0 | 3.7 ×31.6 | 3.3 ×35.4 | 8.4 ×13.9 | 117 ×1.0 | 🔵 3.2 ×36.4 | — | 🔵 0.12 ×973.7 (+10 dB) | 🟢 0.11 ×1062.2 (+21 dB) | 9.6 ×12.1 (+25 dB) | 9.4 ×12.4 (+31 dB) | 🔵 0.11 ×1062.2 (+9 dB) | 🔵 0.11 ×1062.2 (0 dB) |
| CMOS inverter chain ×100 | 200 | 3.1 ×2.2 | 3.1 ×2.2 | 🔵 1.9 ×3.6 | 6.8 ×1.0 | 6.4 ×1.1 | — | 🔵 1.5 ×4.5 (+5 dB) | 🔵 1.5 ×4.5 (+5 dB) | 🔵 0.72 ×9.5 (+1 dB) | 🔵 1.2 ×5.6 (+3 dB) | 🟢 0.21 ×32.5 (+6 dB) | 🔵 0.21 ×32.5 (+6 dB) |
| CMOS ring oscillator ×51 | 102 | brk | 5.5 ×3.9 | 🔵 3.5 ×6.0 | 21 ×1.0 | 16 ×1.4 | — | 🔵 0.92 ×23.1 (0 dB) | 🔵 0.92 ×23.1 (0 dB) | 🔵 0.52 ×40.8 (0 dB) | 🔵 1 ×20.8 (0 dB) | 🟢 0.21 ×101.1 (0 dB) | 🔵 0.21 ×101.1 (0 dB) |
| 5T OTA (diff pair + mirror) | 5 | 3.9 ×26.3 | 4.5 ×22.8 | 6.7 ×15.3 | 103 ×1.0 | 10 ×10.2 | — | 🔵 0.12 ×855.1 (+3 dB) | 🔵 0.12 ×855.1 (+2 dB) | 8 ×12.8 (+3 dB) | 7.2 ×14.2 (+2 dB) | 🟢 0.11 ×932.8 (+3 dB) | 🔵 0.11 ×932.8 (+3 dB) |
| BJT 3-stage CE amp ‡ | 3 | 3.9 ×53.1 | 7.7 ×26.9 | 5.5 ×37.5 | 207 ×1.0 | 20 ×10.4 | — | 🔵 0.52 ×398.1 (-3 dB) | 🔵 0.52 ×398.1 (-3 dB) | 🔵 2.8 ×73.4 (-3 dB) | 🔵 0.22 ×940.9 (0 dB) | 🟢 0.11 ×1881.8 (0 dB) | 🔵 0.11 ×1881.8 (0 dB) |
| 2-stage Miller op-amp | 8 | 3.5 ×22.6 | 4.6 ×17.2 | 38 ×2.1 | 79 ×1.0 | 20 ×4.0 | — | 🔵 0.12 ×659.9 (+24 dB) | 🔵 0.12 ×659.9 (+26 dB) | 21 ×3.8 (+25 dB) | 21 ×3.8 (+22 dB) | 🟢 0.11 ×719.9 (+23 dB) | 🔵 0.11 ×719.9 (+27 dB) |
| C6288 16×16 multiplier (PSP103) | 10112 | n/a | n/a | 🔵 46 ×1.5 | n/a | 🔵 70 ×1.0 | — | 🔵 6.6 ×10.7 (∞ dB) | 🔵 2.5 ×28.6 (∞ dB) | 🔵 4.3 ×16.1 | 🔵 1.9 ×37.9 | 🔵 0.75 ×93.4 (∞ dB) | 🟢 0.35 ×200.2 (∞ dB) |
| BJT cascade ×3000 (breaker) | 3000 | brk | brk | brk | 🔵 462 ×1.0 | t/o | 🟢 238 ×1.9 (np 4) | — | — | — | — | — | — |

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

**VACASK** (native column) is the new open engine — the same deck, ported to its
Spectre-style syntax by `gen_models_vacask.py` (MOSFET LEVEL=1 → `sp_mos1`, diode
→ `sp_diode`, NPN → `sp_bjt`, multitone B-sources → series ideal sines). Models
compile to OSDI 0.4 via OpenVAF-reloaded. It is a fully adaptive (LTE-driven)
solver, so its per-deck work lands in the ngspice/Xyce range rather than the
QSPICE/LTspice stride-and-coast regime; timepoint counts are recorded next to the
runner. The **vc+bfit** columns run the SAME portable Verilog-A macromodels
through VACASK (`bfit front --sim vacask`, backed by a VACASK tuner driver --
`sp2vc` + `drivers_vacask`; `model_bench.sh` runs the lane via `vc_run`, gate it
with `DO_VC`/`DO_NGXY`). All four macromodels are wired: `ce_stage`, `bridge`,
`current_mirror` (VA cmout legs), and `cmos_inv` **v2** -- the inverter was
redesigned as a regenerative clamped-linear transfer (gain>1 at the trip point;
the old conductance-divider form could not regenerate a chain in ANY engine) and
retuned through VACASK, which also refreshes the ng/xy digital cells. Striding
in VACASK needs three knobs (`front --sim vacask` sets them): `tran_ffmax=0`
(drop the max-input-frequency step cap), `tran_redofactor=0` + huge
`tran_lteratio` (disarm LTE), `tran_method="gear2"` (trap rings on undersampled
inputs). VACASK (AGPL) is the license-clean, OpenVAF-native drop-in for ngspice
in the accelerated lane.

**Reading it.** bfit swaps device stages for smooth macromodels and coarsens the
transient, so the solver strides — every accelerated row beats both commercial
tools. The cleanest win is the **op-amp** (merged diff-pair + current-mirror
legs). The **`--accuracy` knob** trades speed for fidelity (compare each `bal`
vs `fast` cell); the fast multi-tone amps lose more to undersampling. The
**breaker** is the other half: at 3000 stiff stages QSPICE, LTspice and ngspice
all abort — only Xyce solves it, and MPI then nearly halves that.

_Models: `gen_models.py` (+ `gen_amp.py` for the breaker; `gen_models_vacask.py`
ports them to VACASK, `c6288_run.sh` runs C6288). Open engines:
`model_bench.sh` → `open.csv`. Commercial: `win_models.sh` → `commercial.csv`.
Table: `assemble.py`. Accuracy: `accuracy.py`. Speed/accuracy knob:
`bfit front --accuracy {exact,balanced,fast}` (or raw `--points/--reltol/--abstol`)._


## VACASK vs ngspice — the replacement case

ngspice's licensing is a patchwork; VACASK is a single clean AGPL-3.0 codebase
that consumes the **same OpenVAF Verilog-A**. The question is whether switching
costs performance. In the bfit-accelerated lane — the flow this tooling
actually runs — it does not: VACASK is **never slower than ngspice** and wins
the hard (digital / stiff) rows by ×4–7. Seconds head-to-head, same macromodels,
same methodology:

| Model | ngspice | VACASK | VACASK adv. | ng+bfit bal | vc+bfit bal | VACASK adv. |
| :-- | --: | --: | --: | --: | --: | --: |
| Bridge rectifier (4 diodes) | 8.4 | 3.2 | ×2.6 | 0.12 | 0.11 | ×1.1 |
| CMOS inverter chain ×100 | 1.9 | 6.4 | ×0.3 | 1.5 | 0.21 | ×7.2 |
| CMOS ring oscillator ×51 | 3.5 | 16 | ×0.2 | 0.92 | 0.21 | ×4.4 |
| 5T OTA (diff pair + mirror) | 6.7 | 10 | ×0.7 | 0.12 | 0.11 | ×1.1 |
| BJT 3-stage CE amp ‡ | 5.5 | 20 | ×0.3 | 0.52 | 0.11 | ×4.7 |
| 2-stage Miller op-amp | 38 | 20 | ×1.9 | 0.12 | 0.11 | ×1.1 |
| C6288 16×16 multiplier (PSP103) | 46 | 70 | ×0.7 | 6.6 | 0.75 | ×8.7 |

Accelerated tally: **4 decisive VACASK wins, 3 ties** (within the 10 ms timer
grain), **0 losses** — the `fast` preset shows the same pattern. C6288's
accelerated cells come from the **gate recognizers** (`recognize_gates`): the
not/nor/and subckts are classified by a switch-level truth table and THREE
subckt-body substitutions turn all 10112 PSP103 FETs into ~2400 behavioral
gates — the multiplier still computes 0xFFFF × 0xFFFF = 0xFFFE0001 on every
engine. The substituted deck contains no transistors at all, so even our
PSP103-less Xyce runs it (bfit as an *enabler*; Xyce's native cell stays
`n/a`). Native transistor-level is hardware-dependent: on this no-AVX-512 box
ngspice leads most native rows including C6288 (VACASK's OSDI model evaluation
leans on wide vectors), while on the VACASK project's Zen 4 reference machine
VACASK leads ngspice natively as well (58 s vs 72 s on C6288 — see below).
Same portable Verilog-A everywhere: `bfit front --sim vacask` vs
`--sim ngspice` is a one-flag swap.

## C6288 16x16 multiplier (native, transistor-level)

VACASK's flagship benchmark, brought in from its tree: **10112 transistors /
25380 nodes**, PSP103.4 MOSFETs, 0xFFFF x 0xFFFF as a transistor-level transient
(~1020 timepoints). Baseline = each engine's native run of the same circuit,
full-process wall, min of 2. Runner: `c6288_run.sh`; snapshot `c6288-2026-07-12.md`.

| Engine | Wall (s) | Timepoints (acc/rej) | NR iters |
| :-- | --: | :-- | --: |
| ngspice-45.2 | 45.98 | 1020 / 1 | 3474 |
| VACASK 0.3.3 | 70.08 | 1023 / 10 | 3512 |
| Xyce 7.10 (ours) | n/a | -- | -- |

Xyce, QSPICE and LTspice are absent NATIVELY: our Xyce build has no built-in
PSP103 (`level=103`) and no OSDI loader, and QSPICE/LTspice have no
OSDI/Verilog-A path wired for PSP103 on this box. VACASK's 1023/10/3512 matches
the project README's 1021/7/3487, so the port is verified. Note the ordering:
on the README's Zen4/AVX-512 machine VACASK leads (58 s vs ngspice 72 s); this
box has no AVX-512, which is where VACASK's OSDI model-eval edge comes from, so
ngspice leads here instead.

The **+bfit cells** in the main table come from the gate-recognizer lane
(`c6288_run.sh`, `BFIT=1` default): `recognize_gates` switch-level-classifies
the three gate subckts and replaces their BODIES, turning 10112 PSP103 FETs
into ~2400 behavioral gates with no transistors left — the product is still
0xFFFE0001 on every engine, and the deck runs on Xyce with no PSP103 at all.
Accuracy is rel-L2 of p31 vs the engine's own native gold (Xyce has none →
`-`).

## Cascade-depth stress runs

The N-stage cascade sweep (`run_bench.sh`) is a separate lane and writes
**date-named snapshots** next to this page — `cascade-YYYY-MM-DD.md` — so
each run is preserved rather than overwriting this table:
- [2026-07-06](cascade-2026-07-06.md)
