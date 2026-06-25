# bfit tools

## `stdcell2bfit.py` — standard-cell → bfit macromodel generator

Generates a bfit behavioral macromodel from a static-CMOS standard-cell
transistor netlist, **run once per cell before simulation** to build the model
library.

A static CMOS cell's logic *is* its pull-up (PMOS) and pull-down (NMOS)
networks. The tool extracts both and series-parallel-reduces them to conductance
expressions:

- transistors **in series** → resistances add → **AND** of their gate conditions
- transistors **in parallel** → conductances add → **OR**

Each transistor's on-conductance is programmed by its gate (NMOS ∝ V(gate),
PMOS ∝ Vhi−V(gate)); the output is the resulting pull-up/pull-down divider into
the load C, with a leakage floor for the static-power match. No `tanh`, linear-
algebraic, cheap per step — the same family as `library/cmos_inv` (which is the
one-transistor case). Validated: NAND2 → 1110, NOR2 → 1000.

```
stdcell2bfit.py cell.cir [subckt]   > cell_bfit.cir
```

### ATPG characterization (the "run once" step)

The cell's **logic** comes from topology; its **parameters** (`ron`, `rleak`,
`cin`) and timing are fit by characterizing the real cell. The existing ATPG
flow supplies the stimulus:

```
Atalanta  ──.tst──▶  tests-atpg/test2spice.pl  ──▶  SPICE patterns (all input
                                                     transitions, incl. Monte Carlo)
```

Simulate the transistor cell on those patterns, then fit the macromodel params so
its levels/delays match. Done once per cell → a portable bfit cell-model library.

### Next

- Point it at the real library (`IHP-Open-PDK/.../sg13g2_stdcell/spice`) to emit
  the whole cell set.
- Wire the generated models into `front()` so bfit substitutes recognized cell
  instances automatically (today `recognize_inverter` handles the inverter case).
- Fit `ron/rleak/cin` per cell from the ATPG-pattern simulation.
