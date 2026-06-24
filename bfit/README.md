# bfit — behavioral-fit

A standalone, **simulator-agnostic** accelerator for analog/mixed-signal
simulation. `bfit` recognizes standard circuit patterns, substitutes **portable
Verilog-AMS signal-flow macromodels**, and **auto-tunes** their parameters to
match the device-level reference — through *whatever* simulator you already use.

The payoff: a recognized block (e.g. a gain stage) becomes a smooth behavioral
model with the *voltage transfer* right but **no device physics and no per-node
current/power balance** to grind to 1e-6. That lets the integrator take large
adaptive timesteps — on the bundled common-emitter example, the signal-flow
macromodel is **~7× faster than the transistor-level deck for identical output**
(100k forced steps → ~960 adaptive steps), and the tuned clipping waveform lines
up with the BJT cascade (linear early stages → rail-clipped output).

## Why standalone / portable

- **Macromodels are Accellera-LRM Verilog-AMS** (`*.vams`) — run on OpenVAF+ngspice,
  Spectre, AMS-Designer, Xyce/PyMS. No engine lock-in.
- **The only engine-specific code is a thin sim-driver** (write netlist → run →
  parse raw). Everything else — recognizer, tuner, cache — is engine-neutral.
- Pure **netlist-in / netlist-out** plus a `.vams` library and a parameter cache:
  drop into any existing flow.

## Architecture

```
 netlist ─► parser ─► graph ─► recognizer ─► matched patterns
                                                 │
            cache hit ─► params           substituter ─► transformed netlist + .vams
                │ miss: best-guess + mark      │
                ▼                              ▼
        background TUNER ◄──── sim-driver (xyce │ ngspice+OpenVAF │ spectre) ──► run ref vs macro
                │  Nelder-Mead fits params to minimise signal-flow feature error
                ▼
         parameter cache  (pattern + quantised values + corner → params)
```

Status: the **tuner + Xyce driver + first library entry (`ce_stage`)** are
working (this commit). Pattern recognizer, parameter cache reuse, and the
ngspice/OpenVAF driver are the next steps (the ngspice driver is stubbed in
`drivers_ngspice.py` with the OpenVAF→OSDI recipe).

## Use

```
# auto-tune the ce_stage macromodel to a device-level reference, via Xyce:
python3 bfit.py tune --ref examples/bjt_ref.cir --lib library/ce_stage --sim xyce \
                     --cache cache.json
# ... --sim ngspice  to drive ngspice+OpenVAF instead (engine-neutral)
```

The tuner runs the reference once to get target **features** (here: the per-stage
amplitudes and the output-stage clip min/max — capturing gain + clipping),
then optimises the macromodel parameters so its features match.

## Layout

```
bfit.py                       engine: SimDriver, Nelder-Mead, tuner, CLI
drivers_ngspice.py            ngspice + OpenVAF driver (OSDI path)
library/<pattern>/
    <pattern>.vams            portable Verilog-AMS macromodel (the product artifact)
    template.cir              parameterised candidate (__tokens__ filled by tuner)
    fit.json                  params/bounds/seed + fit features + recognition signature
examples/                     device-level reference decks
```

## Adding a pattern

Drop a `library/<name>/` with the three files. The `.vams` is the portable
model; `template.cir` instantiates it (for the Xyce driver, a SPICE realisation
of the same transfer is fine); `fit.json` declares the tunable params and the
features the tuner should match. A recognizer rule (signature → substitution)
hooks it into the front end.
