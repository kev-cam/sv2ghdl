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
| `gen_amp.py`    | generates an N-stage common-emitter BJT amplifier cascade (linear device-count scaling; every stage is the exact pattern bfit's recognizer substitutes) |
| `run_bench.sh`  | **the orchestrator** — run from Cygwin; drives Windows-native engines + (via `wsl.exe`) the Linux engines, assembles `perf.md`/`perf.csv` |
| `xrun.sh`       | WSL-side helper — runs one Linux engine on one circuit, prints min inner wall-clock |
| `mpisweep.sh`   | finds the optimum Xyce-MPI rank count (np=2..16 sweep) per size and the speedup vs serial — source of the `Xyce-MPI` column |
| `perf.md` / `perf.csv` | the generated table |

## Running it

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
- **MPI is for scale-out, not these sizes** — sweeping np=2..16, the optimum rank
  count grows with size (np2→4→6) but is still ×0.2–0.3 (3–5× *slower*) than
  serial, and times out by N=300. See `mpisweep.sh`.

## Roadmap — pattern library

Each new pattern the recognizer learns widens what bfit can accelerate. Next up:

- **current mirror** — a very common bias/load pattern; substitute with a
  programmable-gain controlled-source macromodel (signal-flow, not I–V physics).
- differential pair (`diff_pair`) — partially stubbed in the cache schema.
- parameter cache read in the production flow (skip re-tuning known stages).
- real `.vams` → OSDI path via an OpenVAF binary, replacing the B-source
  template stand-in.
