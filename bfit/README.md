# bfit — behavioral-fit

A standalone, **simulator-agnostic** accelerator for analog/mixed-signal
simulation. `bfit` recognizes standard circuit patterns, substitutes **portable
Verilog-A signal-flow macromodels**, and **auto-tunes** their parameters to
match the device-level reference — through *whatever* simulator you already use.

The payoff: a recognized block (e.g. a gain stage) becomes a smooth behavioral
model with the *voltage transfer* right but **no device physics and no per-node
current/power balance** to grind to 1e-6. That lets the integrator take large
adaptive timesteps. On the bundled common-emitter example the macromodel is
~7× faster than the transistor-level deck for identical output; across the
benchmark suite (`benchmarks/perf.md`) every accelerated row beats both
commercial engines measured there.

## Three modes

| command | what it does | exactness |
|---|---|---|
| `bfit front` | recognize patterns, substitute tuned reduced-order macromodels, coarsen the transient (`--accuracy exact\|balanced\|fast`) | approximate, tuned to features |
| `bfit merge` | **analytical, lossless** merge of directly-coupled transistor structures into one component, eliminating internal nodes (diode-connected cascode, matched diff pair, cross-coupled pair, full-bridge rectifier) | exact where the devices are square-law or Verilog-A described |
| `bfit table` | SIMPLIS-style **table mode**: every device becomes a bounded, interpolated lookup from the start, so Newton never sees a runaway exponential | approximate, always converges (diodes today; MOSFET/BJT pass through) |

`bfit tune` is the fitter behind `front`: it runs the reference once to get target **features**
(per-stage amplitudes, clip levels, delays), then optimises the macromodel parameters
(Nelder-Mead) so the candidate's features match, and writes them to the parameter cache.

## Why standalone / portable

- **Macromodels are Verilog-A** (`library/*/*.va`, compiled to OSDI with OpenVAF for ngspice
  and with openvaf-r for VACASK; `.vams` sources where the model was first written in
  Verilog-AMS). Run on ngspice, VACASK, Xyce/PyMS, Spectre, AMS-Designer. No engine lock-in.
- **The only engine-specific code is a thin sim-driver** (write netlist → run → parse raw).
  Everything else — recognizer, tuner, cache — is engine-neutral.
- Pure **netlist-in / netlist-out** plus the library and a parameter cache: drop into any
  existing flow.

## Architecture

```
 netlist ─► parser ─► graph ─► recognizer ─► matched patterns
                                                 │
            cache hit ─► params           substituter ─► transformed netlist + .va/.osdi
                │ miss: best-guess + mark      │
                ▼                              ▼
        background TUNER ◄──── sim-driver (xyce │ ngspice+OpenVAF │ vacask+openvaf-r) ──► run ref vs macro
                │  Nelder-Mead fits params to minimise signal-flow feature error
                ▼
         parameter cache  (pattern + quantised values + corner → params)
```

Drivers: `xyce` (always), `ngspice` (when `drivers_ngspice.py` imports), `vacask`
(`drivers_vacask.py` + `sp2vc.py`, which translates the SPICE reference into VACASK syntax;
VACASK is the licence-clean AGPL, OpenVAF-native fast path and the default analog engine of
vamos). `front` is gated by `<SIM>_USE_BFIT` in the environment, so a flow can carry the hook
without engaging it.

## Status (2026-11)

Working: `front` with five recognizers (`recognize_ce`, `recognize_mirror`, `recognize_inverter`,
`recognize_bridge`, `recognize_gates`), the three accuracy presets, cache read and write, and the
VACASK path (`front --sim vacask`, which also sets the VACASK striding knobs); `merge` with its
four recognizers and the `--table` fallback for the merged bridge; `table` for diodes; `tune`
through Xyce and VACASK. The June 2026 note that the recognizer, cache reuse and the ngspice
driver were "next steps" is history.

Partial: the ngspice driver runs decks for `front` but its `.measure`-based tuner features are
`NotImplementedError`, so tune through Xyce or VACASK; `table` needs the 2-D MOSFET/BJT table
emitters ported into PyMS's `table_model` before those devices stop passing through (they are
reported, never silently left in accurate mode); `cmos_gates` models are parameter-free (no
`fit.json`); `stdcell2bfit.py` output is not yet consumed by `front` (`tools/README.md`).

## Use

```sh
# accelerate a deck: recognize + substitute + coarsen, via Xyce (default), ngspice or VACASK
XYCE_USE_BFIT=1 python3 bfit.py front deck.cir --sim xyce --accuracy balanced -o deck_bfit.cir
python3 bfit.py front deck.cir --sim vacask --cache cache.json -o deck_bfit.sim

# auto-tune a library entry to a device-level reference
python3 bfit.py tune --ref examples/bjt_ref.cir --lib library/ce_stage --sim xyce --cache cache.json
python3 bfit.py tune --ref examples/inv_ref.cir --lib library/cmos_inv  --sim vacask

# analytical merge (writes the merged component's Verilog-A beside the output; compile with openvaf)
python3 bfit.py merge deck.cir -o deck_merged.cir [--device-va real_mos.va] [--table]

# SIMPLIS-style table mode
python3 bfit.py table deck.cir -o deck_table.cir [--device-va diode.va]
```

## Library

| entry | model | files |
|---|---|---|
| `ce_stage` | common-emitter gain stage with clipping, the first and most-tuned entry | `ce_stage.va` + `.osdi`, `ce_stage.vams`, `template.cir` (Xyce), `template.vc` (VACASK), `fit.json` |
| `current_mirror` | reference I→V plus per-output V→I legs, both polarities, one reference fanning out to many outputs | `template.cir`, `fit.json`; the Verilog-A legs are written beside the deck at substitution time |
| `cmos_inv` | regenerative clamped-linear inverter transfer (v2: gain above 1 at the trip point, so chains and ring oscillators regenerate) | `cmos_inv.va` + `.osdi`, `template.cir`, `template.vc`, `fit.json` |
| `cmos_gates` | 1- and 2-input gate transfers for `recognize_gates` | `cmos_gate1.va`, `cmos_gate2.va` + `.osdi` |
| `bridge_rect` | full-bridge rectifier as one four-terminal component | `bridge.va` + `.osdi`, `template.cir`, `fit.json` |
| `th22` | 2-input threshold gate (Muller C-element) with its weak-feedback keeper, hysteretic: the first state-holding cell | `th22.vams`, `template.cir`, `fit.json`, `ref_th22_3f.cir`, `residuals.json` |

## Layout

```
bfit.py                 engine: SimDriver interface, drivers table, Nelder-Mead, tuner,
                        front (recognizers + substitution + coarsening), CLI
drivers_ngspice.py      ngspice + OpenVAF driver (OSDI path)
drivers_vacask.py       VACASK driver (openvaf-r OSDI); tune and front through VACASK
sp2vc.py                SPICE -> VACASK netlist translator for the VACASK driver
merge.py                bfit merge: the analytical recognizers and emitters
tablemode.py            bfit table: table-mode front end (PyMS/Xyce VAE-ABI .so emitters)
library/<pattern>/      see the table above
examples/               device-level reference decks (bjt_ref.cir, inv_ref.cir)
benchmarks/             cross-engine perf league (perf.md), cascade study, merge and table
                        benchmarks, VHDL perf, the GPU instance farm (benchmarks/README.md)
tools/                  stdcell2bfit.py, standard-cell -> macromodel generator (tools/README.md)
prototypes/             demand-driven / lazy evaluation experiments
LICENSE                 PolyForm Noncommercial 1.0.0
```

## Adding a pattern

Drop a `library/<name>/` with the Verilog-A model (`<name>.va`; compile it with `openvaf` for
ngspice and `openvaf-r` for VACASK so the `.osdi` sits beside it), a `template.cir` that
instantiates it for the Xyce tuner (a SPICE realisation of the same transfer is fine) and, for
VACASK, a `template.vc`, and a `fit.json` declaring the tunable parameters, their bounds and seeds,
and the features the tuner must match. Then add a `recognize_<name>()` in `bfit.py` and list it
in `front()`'s match set; the recognizer returns the matched instances and the substitution.
Analytical merges go in `merge.py` instead.

## Licence

`bfit/` is licensed under the PolyForm Noncommercial License 1.0.0 (`LICENSE` here), separately
from the rest of the repository (GPL-3.0-or-later). GPL code in the repository runs bfit as a
separate program and never imports it.
