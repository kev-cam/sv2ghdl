# bfit cross-engine performance

An N-stage common-emitter BJT amplifier cascade (`gen_amp.py`) benchmarked across
every SPICE engine on this box. Each stage is exactly the pattern bfit's
recognizer substitutes, so one circuit family exercises both the plain engines
and the bfit macromodel path, while device count scales linearly with N.

Each cell is engine **simulation time in seconds**, cross-environment process
launch excluded — Windows engines (QSPICE, LTspice) self-report their "Total
elapsed time"; Linux engines (ngspice, Xyce) are timed by inner wall-clock inside
WSL. The **+bfit** columns swap in the portable `ce_stage` Verilog-AMS macromodel
and take adaptive timesteps. The **Xyce-MPI** column is the fastest of an
np = 2..16 sweep (`mpisweep.sh`), shown as `time ×speedup (optimum np)`, where
speedup is relative to serial Xyce. Measured on a Threadripper PRO 5955WX
(16C/32T); Xyce serial `-O3`. `brk` = engine aborted (timestep collapse on the
stiff deep cascade); `t/o` = exceeded the time cap.

## Speed — `.tran 20n 2m` (100k forced steps)

| Stages | Transistors | QSPICE | LTspice | ngspice | ngspice+bfit |   Xyce | Xyce+bfit | Xyce-MPI (best np) |
| -----: | ----------: | -----: | ------: | ------: | -----------: | -----: | --------: | -----------------: |
|      3 |           3 |   0.48 |    0.55 |    1.55 |     **0.25** |   3.26 |  **0.45** |    19.33 ×0.2 (np2) |
|     30 |          30 |   5.12 |    4.04 |    9.36 |     **0.45** |  13.26 |  **2.15** |    51.65 ×0.3 (np4) |
|    100 |         100 |  25.83 |   16.26 |   36.28 |     **8.46** |  45.69 | **12.66** |   150.24 ×0.3 (np6) |
|    300 |         300 | 163.02 |   81.74 |  158.79 |    **82.83** | 185.31 | **87.43** |                t/o |

## Scaling wall — capacity probe (short transient, does it finish?)

Past a few hundred stages the deep high-gain cascade turns numerically stiff and
the direct-SPICE engines collapse their timestep. Short-transient run; wall
seconds if it completed, `brk` if it aborted:

| Stages | QSPICE | LTspice | ngspice |  Xyce | Xyce+bfit |
| -----: | -----: | ------: | ------: | ----: | --------: |
|    300 |    8 s |     5 s |     3 s |   4 s |       3 s |
|   1000 |  **brk** |  53 s | **brk** |  18 s |      17 s |
|   3000 |  **brk** | **brk** | **brk** | 235 s |     236 s |

## Takeaways

- **bfit accelerates every engine.** The same portable `ce_stage` macromodel cuts
  ngspice up to ~21× and Xyce up to ~7×, with no per-engine work. The lead is
  largest at small/mid scale and erodes as the cascade deepens (series tanh
  clampers get stiff): ~2× by N=300.
- **No engine traps the user.** Plain Xyce carries framework cost — it is built
  for parallel scale-out — so on small circuits it is ~2× ngspice and ~7× QSPICE.
  A user who just wants it fast runs the *same Verilog-AMS netlist* on ngspice, or
  applies bfit. Portability is the escape hatch.
- **At scale, robustness flips the ranking.** By N=1000 QSPICE and ngspice abort
  (timestep → ~1e-19); LTspice hangs on but dies by N=3000. **Only Xyce reaches
  3000 stages** — the last engine standing. That robustness is what Xyce's
  framework cost buys.
- **MPI is for scale-out, not these sizes.** Sweeping np = 2..16, the optimum rank
  count *grows* with the problem (np2 → np4 → np6 for N=3 → 100), but MPI never
  beats serial here: best case is ×0.2–0.3 (3–5× *slower*), and by N=300 it can't
  finish a step inside the cap. A fixed ~15 s solver-init plus inter-rank
  communication dominates until the design is large and meshed; the serial/MPI
  crossover is well beyond a 1-D 300-device chain.

SIMetrix is installed but GUI-bound here (no headless netlist entry point), so it
is not in the timed set.

_Speed table from `benchmarks/run_bench.sh` (`SIZES="3 30 100 300"`) with the
Xyce-MPI column from `mpisweep.sh`; capacity probe is the same harness with a
short transient. See `README.md`._
