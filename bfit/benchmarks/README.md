# bfit benchmarks

A reusable, cross-engine performance harness for bfit. It runs a suite of
synthetic circuits on every SPICE engine installed on this box, with and
without bfit, and emits a Markdown + CSV performance table.

## Why it exists

The point bfit makes: with a portable Verilog-A macromodel library, the user
is never trapped by one engine's overhead. Xyce is built for parallel
scale-out, so it carries framework cost that makes small circuits slower than
QSPICE/LTspice/ngspice — but the *same* bfit macromodel accelerates **any**
engine, and a user who just wants a small circuit fast can run it on ngspice
or VACASK with no model changes. This harness measures all of that side by side.

## Files

The league table (`perf.md`):

| file | what it does |
|---|---|
| `gen_models.py` | generates the **model suite** — bridge rectifier, CMOS inverter chain ×100, CMOS ring oscillator ×51, 5T OTA, BJT 3-stage CE amp, 2-stage Miller op-amp — driven with **multi-tone** inputs, runs unmodified on every SPICE engine |
| `gen_models_vacask.py` | ports the suite to VACASK's Spectre-style syntax (MOSFET LEVEL=1 → `sp_mos1`, diode → `sp_diode`, NPN → `sp_bjt`, multitone B-sources → series sines) |
| `gen_amp.py` | generates an N-stage CE BJT cascade: the 3000-stage **breaker** row, and the cascade-scaling study |
| `c6288_run.sh` | the C6288 16×16 multiplier (PSP103, 10,112 transistors) from the VACASK tree, native on each engine that has the model |
| **`model_bench.sh`** | **league-table runner (Linux side)**: per model, native ngspice/Xyce/VACASK + `bfit front --accuracy {balanced,fast}` on each + Xyce-MPI np-sweep + the breaker → `open.csv`. `DO_VC`/`DO_NGXY` gate the VACASK and ngspice+Xyce lanes |
| `xrun_model.sh`, `run_lin_models.sh` | one Linux engine on one model (used by `model_bench.sh`); a loop over the suite |
| `csvmerge.py` | merges one lane's fields into `open.csv` keyed by model, so the ng/xy and vc lanes can run independently |
| `raw2dat.py` | dumps one node of a rawfile as `time value` lines, so the VACASK lane reuses the accuracy pipeline |
| **`win_models.sh`** | league-table runner (Windows side, from Cygwin): QSPICE + LTspice per model → `commercial.csv` |
| **`assemble.py`** | turns `open.csv` + `commercial.csv` into **`perf.md`** — computes every ×multiplier and 🟢/🔵 dot deterministically (no hand arithmetic) |
| `open.csv` / `commercial.csv` | the measured snapshots `assemble.py` reads (committed, so `perf.md` regenerates without re-measuring) |
| `accuracy.py` | signal-to-error ratio (rel-L2) / THD of a bfit run vs that engine's own native golden |
| `perf.md` / `perf.csv` | the table and its CSV |

Other studies in this directory:

| file | what it is |
|---|---|
| `run_bench.sh` / `xrun.sh` / `mpisweep.sh` | the cascade-scaling study (N = 3 … 3000 CE stages, every engine, Xyce-MPI rank sweep) |
| `cascade-2026-07-06.md`, `.csv` | that study's cascade-depth stress run |
| `c6288-2026-07-12.md` | the C6288 baseline write-up |
| `merge_bench.sh`, `merge.md` | `bfit merge` benchmark: node count, timesteps and wall time, transistor vs merged, per pattern |
| `table/`, `table/table.md` | `bfit table` (table-mode) evaluation and transient benchmarks |
| `vhdl/`, `vhdl_perf.md`, `sv_perf.md` | cross-simulator VHDL performance (nvc, ghdl, logic3d packings) and nvc vs Verilator on SystemVerilog |
| `vhdl/gpu/`, `gpu_farm.md` | the GPU instance farm and its scaling rules; `vhdl/gpu/hazard3/` is the Hazard3 Mandelbrot case on it |

## Reproducing the league table (`perf.md`)

Three steps; the two measurement steps run in their own environment, then the
assembler stitches the CSVs into the page:

```sh
# 1. Linux engines (from WSL): native + bfit bal/fast on ngspice, Xyce and VACASK,
#    Xyce-MPI sweep, breaker -> open.csv   (sequential, ~1 h; the breaker MPI sweep dominates)
bash model_bench.sh                       # ROWS="inv_chain" DO_BREAKER=0  for a quick smoke
DO_NGXY=0 bash model_bench.sh             # refresh only the VACASK lane (csvmerge keeps the rest)

# 2. Commercial engines (from Cygwin): QSPICE + LTspice -> commercial.csv
bash win_models.sh

# 3. assemble -> perf.md  (instant; pure arithmetic on the two CSVs)
python3 assemble.py open.csv commercial.csv > perf.md
```

`open.csv` / `commercial.csv` are committed, so step 3 alone regenerates
`perf.md` (re-run 1–2 only to re-measure). `model_bench.sh` path knobs are env
vars with sensible defaults: `XYCE` / `XYCE_MPI` (+ their `*_LD` library paths),
`MPIRUN`, `BFIT_NGSPICE`, `OPENVAF` (OSDI 0.3, ngspice), `OPENVAF_R` (OSDI 0.4,
VACASK), `VACASK`, `MODELS`, `ROWS`, `DO_BREAKER`, `DO_VC`, `DO_NGXY`. If no MPI
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

League table columns: QSPICE, LTspice, ngspice, Xyce, VACASK, Xyce-MPI, and the
`+bfit` lanes `ng`, `xy`, `vc` at `bal` and `fast`. The cascade study's engine
list (`run_bench.sh`) is `qspice` `ltspice` `simetrix` `ngspice` `ngspice_bfit`
`xyce` `xyce_bfit` `xyce_mpi`, auto-detected, missing ones skipped; `simetrix` is
GUI-bound here (no headless netlist entry point) and reports `na`.

The `+bfit` columns run the netlist through `bfit front`, which recognizes the
stages it has models for (CE stages, current mirrors, inverters and gates, the
bridge), swaps in the portable Verilog-A macromodels (tuned params from the
cache), and relaxes the forced timestep — signal-flow macromodels are smooth, so
the engine takes far larger adaptive steps. On VACASK `front --sim vacask` also
sets the striding knobs the engine needs (`perf.md` lists them).

## Methodology

Each cell is the time the engine spent **simulating**, with cross-environment
process launch excluded so the numbers are comparable:

- **Windows engines** (QSPICE, LTspice) run natively and self-report their own
  "Total elapsed time" — immune to the ~40 s WSL-interop launch stall.
- **Linux engines** (ngspice, Xyce, VACASK, Xyce-MPI) are timed by inner
  wall-clock *inside* WSL (min of `REPS`), which excludes the `wsl.exe` hop.

Adding an engine to the league table = a `run_<name>` function (Windows) or a
lane in `xrun_model.sh` + `model_bench.sh` (Linux) and a column in `assemble.py`.

## Results

Full numbers and discussion are in **[`perf.md`](perf.md)** (raw data in
`perf.csv`). Headlines:

- **bfit accelerates every engine.** Every `+bfit` row beats both commercial
  tools; the cleanest win is the op-amp (merged diff pair + current-mirror legs).
  The `--accuracy` knob trades speed for fidelity, and the accuracy column shows
  where the fast preset undersamples the multi-tone amps.
- **No engine traps the user.** Plain Xyce carries framework cost (built for
  parallel scale-out) and is the ×1.0 reference in most rows; the same netlist
  runs on ngspice or VACASK. VACASK, a fully adaptive solver, lands in the
  ngspice/Xyce range natively and is the fastest accelerated lane on most rows.
- **At scale, robustness flips the ranking.** On the 3000-stage breaker QSPICE,
  LTspice and ngspice abort (timestep collapse) and VACASK times out; **only Xyce
  solves it**, and Xyce-MPI then nearly halves that (×1.9 at np = 4; np = 2/8/16
  lose, so the optimum is a *middle* rank count). MPI is the large-circuit /
  cloud lever, not a small-circuit one.
- **C6288 (PSP103)** runs only where the model exists, ngspice and VACASK; bfit
  cuts it from 46 s and 70 s to under a second on VACASK.

The earlier cascade-sweep headlines (ngspice up to ~21× and Xyce up to ~7× from
the `ce_stage` model alone, MPI ×1.7 at np = 4 on the breaker) came from the June
2026 cascade study (`run_bench.sh`); the league table supersedes them.

## Pattern library and what is next

Each new pattern the recognizer learns widens what bfit can accelerate.

- **CE stage** — done (`library/ce_stage`, tuned).
- **current mirror** — done (`library/current_mirror`): I→V at the reference,
  V→I at each output, both polarities, one reference fanning out to many outputs
  (op-amp mirror banks).
- **CMOS inverter and gates** — done (`library/cmos_inv` v2, a regenerative
  clamped-linear transfer that keeps chains and ring oscillators regenerating;
  `library/cmos_gates` for `recognize_gates`).
- **full-bridge rectifier** — done (`library/bridge_rect`), and as an exact
  four-terminal component in `bfit merge`.
- **differential pair** — done analytically in `bfit merge` (`recognize_diff_pair`,
  one coupled Jacobian, tail kept); the OTA and op-amp rows run it.
- **parameter cache** — read in the production `front` path.
- **Verilog-A → OSDI** — done: the library ships `.va` + `.osdi`; VACASK and
  ngspice load them, Xyce goes through PyMS.
- Next: a delay-aligned accuracy measure for the amplifier rows (`perf.md`);
  2-D table emitters for MOSFET/BJT in `bfit table`; `stdcell2bfit.py` models
  wired into `front` (`../tools/README.md`); per-cell `ron/gmin/cin` fits from
  the ATPG patterns.
