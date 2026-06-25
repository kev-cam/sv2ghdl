# bfit tools

## `stdcell2bfit.py` — standard-cell → bfit macromodel generator

Generates a bfit behavioral macromodel from a static-CMOS standard-cell
transistor netlist, **run once per cell before simulation** to build the model
library.

A static CMOS cell's logic *is* its transistor network. The tool emits **one
gate-programmed conductance per transistor** (a B-source `I = g·(V(d)−V(s))`)
between that FET's drain and source, and lets the solver compute every node:

- NMOS conductance `g ∝ V(gate)`, PMOS `g ∝ Vsup−V(gate)` — so transistors in
  **series** form an **AND** of gate conditions, in **parallel** an **OR**;
- every *internal* node (series-stack midpoints **and** the internal logic nodes
  of multi-stage compound cells — AND=NAND+inv, XOR, mux…) is solved directly,
  so compound and pass-gate cells come out right from topology alone;
- a small leakage floor `+gmin` on every FET keeps the matrix non-singular (and
  is the off-state static-power path); each node gets a weak `rfloat` DC anchor
  and a `cint` cap. No `tanh`, linear-algebraic, cheap per step — same family as
  `library/cmos_inv` (the 1-transistor-per-rail case).

Gate drive is normalized by a **`vsup` parameter, not `V(VDD)`** — dividing by a
solved node voltage is 0 at the first solver iterate (and `vdd` as a param name
collides case-insensitively with the `VDD` node), either of which converges to
garbage.

```
stdcell2bfit.py cell.cir [subckt]   > cell_bfit.cir
```

Validated on the IHP `sg13g2` library (84 cells → **49 combinational** models,
20 sequential flagged, 10 special, 5 empty): truth tables verified by simulation
on the generated behavioral models (no PDK transistor models needed) for
nand/nor/and/or 2–4-in, AOI/OAI (`a21oi`, `o21ai`, `a22oi`), `xor2`/`xnor2`, and
pass-gate `mux2` — all correct. **Note:** the cell's `VSS` port must be tied to
ground in the testbench (the pull-downs reference it); a floating `VSS` makes the
whole cell read stuck-high.

Sequential cells (DFF/latch/clock-gate) and tristate/special cells still emit a
pull-net model, but it does **not** capture their feedback/state or hi-Z — they
are flagged, not valid as-is.

### ATPG characterization (the "run once" step)

The cell's **logic** comes from topology; its **parameters** (`ron`, `gmin`,
`cin`) and timing are fit by characterizing the real cell. The existing ATPG
flow supplies the stimulus:

```
Atalanta  ──.tst──▶  tests-atpg/test2spice.pl  ──▶  SPICE patterns (all input
                                                     transitions, incl. Monte Carlo)
```

Simulate the transistor cell on those patterns, then fit the macromodel params so
its levels/delays match. Done once per cell → a portable bfit cell-model library.

### Next

- Wire the generated models into `front()` so bfit substitutes recognized cell
  instances automatically (today `recognize_inverter` handles the inverter case).
- Fit `ron/gmin/cin` per cell from the ATPG-pattern simulation.
- Sequential-cell handling: detect the cross-coupled feedback pair and emit a
  latch/FF behavioral primitive instead of the pull-net model.
