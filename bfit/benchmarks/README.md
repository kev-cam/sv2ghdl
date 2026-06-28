# bfit benchmarks

A reusable, cross-engine performance harness for bfit. It sweeps a synthetic
circuit across several sizes and every SPICE engine installed on this box, then
emits a Markdown + CSV performance table.

## Why it exists

The point bfit makes: with a portable Verilog-AMS macromodel library, the user
is never trapped by one engine's overhead. Xyce is built for parallel
scale-out, so it carries framework cost that makes small circuits slower than
QSPICE/LTspice/ngspice — but the *same* bfit macromodel accelerates **any**
engine, and a user who just wants a small circuit fast can run it on ngspice
with no model changes. This harness measures all of that side by side.

## Files

| file            | what it does |
|-----------------|--------------|
| `gen_models.py` | generates the **model suite** — a varied, portable set (bridge rectifier, CMOS inverter chain ×100, CMOS ring oscillator ×51, 5T OTA, BJT amp, 2-stage Miller op-amp) driven with **multi-tone** inputs, runs unmodified on every engine |
| **`model_bench.sh`** | **league-table runner (Linux side)**: per model, native ngspice/Xyce + `bfit --accuracy {balanced,fast}` + Xyce-MPI np-sweep + the 3000-stage breaker → `open.csv` |
| **`win_models.sh`** | league-table runner (Windows side, from Cygwin): QSPICE + LTspice per model → `commercial.csv` |
| **`assemble.py`** | turns `open.csv` + `commercial.csv` into **`perf.md`** — computes every ×multiplier and 🟢/🔵 dot deterministically (no hand arithmetic) |
| `open.csv` / `commercial.csv` | the measured snapshots `assemble.py` reads (committed, so `perf.md` regenerates without re-measuring) |
| `accuracy.py`   | rel-L2 / THD of a bfit run vs that engine's own native golden |
| `gen_amp.py`    | generates an N-stage CE BJT cascade (the breaker / scaling study) |
| `run_bench.sh` / `xrun.sh` / `mpisweep.sh` | the older N=3..3000 cascade-scaling study (separate from the league table) |
| `perf.md` / `perf.csv` | the tables |

The **mixed-signal cosim** row in `perf.md` comes from
`xyce/utils/test_simetrix_cosim` — a SIMetrix `.net` translated by
`simetrix_cosim.pl` (digital A-devices → VHDL, analog → Xyce) and run on the
Xyce+nvc runtime; no other engine here can simulate it.

## Reproducing the league table (`perf.md`)

Three steps; the two measurement steps run in their own environment, then the
assembler stitches the CSVs into the page:

```sh
# 1. Linux engines (from WSL): native + bfit bal/fast + Xyce-MPI sweep + breaker
#    -> open.csv   (sequential, ~1 h; the breaker MPI sweep dominates)
bash model_bench.sh                       # ROWS="inv_chain" DO_BREAKER=0  for a quick smoke

# 2. Commercial engines (from Cygwin): QSPICE + LTspice -> commercial.csv
bash win_models.sh

# 3. assemble -> perf.md  (instant; pure arithmetic on the two CSVs)
python3 assemble.py open.csv commercial.csv > perf.md
```

`open.csv` / `commercial.csv` are committed, so step 3 alone regenerates
`perf.md` (re-run 1–2 only to re-measure). `model_bench.sh` path knobs are env
vars with sensible defaults: `XYCE` / `XYCE_MPI` (+ their `*_LD` library paths),
`MPIRUN`, `BFIT_NGSPICE`, `OPENVAF`, `MODELS`, `ROWS`, `DO_BREAKER`. If no MPI
Xyce is present the MPI column is simply `—`.

**The MPI rule:** a run is killed once it passes the serial wall-clock — a
slower-than-serial MPI run has already lost — so the column shows the fastest np
that *beats* serial, else `—`. Small circuits never beat serial (decomposition
overhead); only the 3000-stage breaker does, at a *middle* rank count.

## Running the cascade-scaling study

```sh
# from a Cygwin shell:
cd /cygdrive/c/cygwin64/tmp/perfbench   # any Windows-visible scratch dir
SIZES="3 30 100" REPS=1 MPINP=2 bash run_bench.sh
```

Knobs (environment variables):

- `SIZES`   — stage counts to sweep (default `3 30 300`)
- `REPS`    — timed reps for the Linux engines, min wins (default `2`)
- `MPINP`   — MPI ranks for the Xyce-parallel cell (default `4`)
- `ENGINES` — restrict the engine list, e.g. `ENGINES="qspice ngspice xyce"`

## Engines

`qspice` `ltspice` `simetrix` `ngspice` `ngspice_bfit` `xyce` `xyce_bfit`
`xyce_mpi`. Auto-detected; missing ones are skipped. `simetrix` is GUI-bound
here (no headless netlist entry point) and reports `na`.

The `_bfit` columns run the netlist through `bfit front`, which recognizes the
CE stages, swaps in the portable `ce_stage` Verilog-AMS macromodel (tuned
params from the cache), and relaxes the forced timestep — signal-flow
macromodels are smooth, so the engine takes far larger adaptive steps.

## Methodology

Each cell is the time the engine spent **simulating**, with cross-environment
process launch excluded so the numbers are comparable:

- **Windows engines** (QSPICE, LTspice) run natively and self-report their own
  "Total elapsed time" — immune to the ~40 s WSL-interop launch stall.
- **Linux engines** (ngspice, Xyce, Xyce-MPI) are timed by inner wall-clock
  *inside* WSL (min of `REPS`), which excludes the `wsl.exe` hop.

Adding an engine = add a `run_<name>` function (Windows) or a case in
`xrun.sh` (Linux) and list it in `order`/`detect`.

## Results

Full numbers and discussion are in **[`perf.md`](perf.md)** (raw data in
`perf.csv`). Headlines from a 3 / 30 / 100 / 300-stage sweep plus a capacity
probe out to 3000 stages:

- **bfit accelerates every engine** — the same portable `ce_stage` macromodel
  cuts ngspice up to ~21× and Xyce up to ~7×, no per-engine work. Largest at
  small/mid scale; ~2× by N=300 as the deep cascade stiffens.
- **No engine traps the user.** Plain Xyce carries framework cost (built for
  parallel scale-out): ~2× ngspice, ~7× QSPICE on small circuits. The same
  Verilog-AMS netlist just runs on ngspice — portability is the escape hatch.
- **At scale, robustness flips the ranking.** By N=1000 QSPICE and ngspice abort
  (timestep → ~1e-19); LTspice dies by N=3000; **only Xyce reaches 3000 stages.**
- **MPI is for scale-out, and the crossover is real.** It *loses* on every small
  circuit (decomposition overhead ≫ work) — slower than serial, so the league
  table shows `—`. But on the 3000-stage breaker it finally **wins, ×1.7 at
  np=4** (266 s vs 464 s serial); np=2/8/16 lose, so the optimum is a *middle*
  rank count, not max. MPI is the large-circuit / cloud lever, not a
  small-circuit one.

## Roadmap — pattern library

Each new pattern the recognizer learns widens what bfit can accelerate.

- **CE stage** — done (`library/ce_stage`, tuned).
- **current mirror** — done (`library/current_mirror`): a two-part model — I→V
  at the reference (a `vt` source off the rail + a 1 Ω-normalized sense resistor
  turning the reference current into an overdrive voltage) and V→I at each output
  (`size·overdrive`, going resistive near the rail). Both polarities (NMOS/PMOS);
  one reference fans out to many outputs (op-amp mirror banks). On a 2-stage
  Miller op-amp it cuts ngspice 1.09→0.11 s (~10×) accurate to ~0.01%; OTA ~1%.
- **CMOS logic gate (inverter)** — done (`library/cmos_inv`): no `tanh`. The input
  presents an R-C load and simply *programs* the pull-up/pull-down conductances
  (each with a leakage floor for the static-power match); the output is the
  resulting divider into the load C — linear-algebraic, cheap per step, linear
  between input changes. Cuts ngspice on the inverter chain 2.2× and the ring
  oscillator **17×** (it still oscillates). NAND/NOR = series/parallel pull
  networks; light hysteresis is a tunable `h` (default 0 — positive feedback
  destabilises the smooth form unless leakage is large, which collapses swing).
- **full-bridge rectifier** — done (`library/bridge_rect`, power electronics): no
  `tanh`; the four diodes collapse to one B-source — `Vrect = max(0, |V(a)-V(b)|
  - 2·vdrop)` charges the output one way through `rs` (diode conduction) with a
  reverse-leakage path, the kept load R+C set the ripple. Output within ~5% on
  the suite rectifier (startup-bound there; the win shows in a larger SMPS).
- differential pair — next; partially stubbed in the cache schema.
- parameter cache read in the production flow (skip re-tuning known stages).
- real `.vams` → OSDI path via an OpenVAF binary, replacing the B-source
  template stand-in.
