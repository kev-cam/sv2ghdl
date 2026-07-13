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

| Model | # Tx | QSPICE | LTspice | ngspice | Xyce | VACASK | Xyce-MPI | ng+bfit bal | ng+bfit fast | xy+bfit bal | xy+bfit fast | vc+bfit bal | vc+bfit fast |
| :-- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |
| Bridge rectifier (4 diodes) | 0 | 3.7 ×31.6 | 3.3 ×35.4 | 8.4 ×13.9 | 117 ×1.0 | 🔵 3.2 ×36.4 | — | 🔵 0.12 ×70.2 (+10 dB) | 🟢 0.11 ×76.6 (+21 dB) | 9.6 ×12.1 (+25 dB) | 9.4 ×12.4 (+31 dB) | 🔵 0.11 ×29.2 (+9 dB) | 🔵 0.11 ×29.2 (0 dB) |
| CMOS inverter chain ×100 | 200 | 3.1 ×2.2 | 3.1 ×2.2 | 🔵 1.9 ×3.6 | 6.8 ×1.0 | 6.4 ×1.1 | — | 🔵 1.5 ×1.3 (+5 dB) | 🔵 1.5 ×1.3 (+5 dB) | 🔵 0.72 ×9.5 (+1 dB) | 🔵 1.2 ×5.6 (+3 dB) | 🟢 0.21 ×30.5 (+6 dB) | 🔵 0.21 ×30.5 (+6 dB) |
| CMOS ring oscillator ×51 | 102 | brk | 5.5 ×3.9 | 🔵 3.5 ×6.0 | 21 ×1.0 | 16 ×1.4 | — | 🔵 0.92 ×3.8 (0 dB) | 🔵 0.92 ×3.8 (0 dB) | 🔵 0.52 ×40.8 (0 dB) | 🔵 1 ×20.8 (0 dB) | 🟢 0.21 ×74.4 (0 dB) | 🔵 0.21 ×74.4 (0 dB) |
| 5T OTA (diff pair + mirror) | 5 | 3.9 ×26.3 | 4.5 ×22.8 | 6.7 ×15.3 | 103 ×1.0 | 10 ×10.2 | — | 🔵 0.12 ×56.0 (+3 dB) | 🔵 0.12 ×56.0 (+2 dB) | 8 ×12.8 (+3 dB) | 7.2 ×14.2 (+2 dB) | 🟢 0.11 ×91.1 (+3 dB) | 🔵 0.11 ×91.1 (+3 dB) |
| BJT 3-stage CE amp ‡ | 3 | 3.9 ×53.1 | 7.7 ×26.9 | 5.5 ×37.5 | 207 ×1.0 | 20 ×10.4 | — | 🔵 0.52 ×10.6 (-3 dB) | 🔵 0.52 ×10.6 (-3 dB) | 🔵 2.8 ×73.4 (-3 dB) | 🔵 0.22 ×940.9 (0 dB) | 🟢 0.11 ×181.1 (0 dB) | 🔵 0.11 ×181.1 (0 dB) |
| 2-stage Miller op-amp | 8 | 3.5 ×22.6 | 4.6 ×17.2 | 38 ×2.1 | 79 ×1.0 | 20 ×4.0 | — | 🔵 0.12 ×320.5 (+24 dB) | 🔵 0.12 ×320.5 (+26 dB) | 21 ×3.8 (+25 dB) | 21 ×3.8 (+22 dB) | 🟢 0.11 ×180.3 (+23 dB) | 🔵 0.11 ×180.3 (+27 dB) |
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

Xyce, QSPICE and LTspice are absent here: our Xyce build has no built-in PSP103
(`level=103`) and no OSDI loader, and QSPICE/LTspice have no OSDI/Verilog-A path
wired for PSP103 on this box. Getting C6288 onto Xyce needs PSP103 via PyMS
(`.hdl`) or the `-bfit` behavioral lane. VACASK's 1023/10/3512 matches the
project README's 1021/7/3487, so the port is verified. Note the ordering:
on the README's Zen4/AVX-512 machine VACASK leads (58 s vs ngspice 72 s); this
box has no AVX-512, which is where VACASK's OSDI model-eval edge comes from, so
ngspice leads here instead.

## Cascade-depth stress runs

The N-stage cascade sweep (`run_bench.sh`) is a separate lane and writes
**date-named snapshots** next to this page — `cascade-YYYY-MM-DD.md` — so
each run is preserved rather than overwriting this table:
- [2026-07-06](cascade-2026-07-06.md)
