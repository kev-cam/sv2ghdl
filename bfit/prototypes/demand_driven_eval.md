# Demand-driven (pull / backward) evaluation — the path to beating Verilator

**Thesis (user, 2026-07-24):** *"Only evaluating things when needed is the way
to beat Verilator; it only works forward, we can look backward."*

Verilator is **push**: compiled straight-line code re-evaluates the whole design
every cycle, forward in time. It is fast per node, but it has no way to *not*
evaluate something — no way to skip logic that doesn't matter this run, and no
way to skip time. A **pull** evaluator computes a signal's value only when it is
observed, by recursing **backward** through its dependency cone, memoised per
`(node, time)`. Two kinds of work then cost nothing:

1. **Dead / unobserved logic** — anything not in the backward cone of an
   observed output is never evaluated. (Verilator statically removes
   *structurally* dead code, but must still evaluate logic that is live in
   general yet never reaches an observed output *this run*.)
2. **Unobserved time** — a registered value at cycle `t` pulls its driver from
   `t-1` only as deep as an observation requires; cycles nobody looks at never
   run. With multicycle collapse (a closed pipeline is a pure function of its
   input `latency` cycles earlier) the intermediate cycles vanish entirely.

## Measured (prototypes here)

`demand_eval.c` builds a small synchronous netlist — a live pipeline that feeds
the observed output, plus a pile of free-running logic that feeds nothing — and
times **push** (forward, all nodes every cycle, O(nodes) memory, Verilator-like)
against **pull** (backward, memoised per node×time). Every pull result is
checked bit-identical to push first; speed is meaningless if it is wrong.

Wallclock, 8329-node design, 5000 cycles:

| pull observation | evals   | vs push (wallclock) |
| :--------------- | ------: | ------------------: |
| every cycle      | 1.64 M  | **8.3× faster**     |
| every 10th       | 164 k   | **50× faster**      |
| every 100th      | 16 k    | **119× faster**     |
| final only       | 329     | **169× faster**     |

(push did 20.8 M node-evals.) All **correct=YES**.

## The honest caveat — and the crossover

Fewer evals is **not** proportional speedup: a pull eval (memo probe +
recursion) is dearer than a compiled push node-eval. So pull wins wallclock only
where the eval-count reduction beats the per-eval overhead. Sweeping the
dead/unobserved fraction at **every-cycle** observation (pull's hardest case):

| % logic dead/unobserved | pull vs push |
| ----------------------: | -----------: |
| 0 %                     | 0.40× (slower) |
| 55 %                    | 0.90× (breakeven) |
| ~70 %                   | **crossover** |
| 96 %                    | 10.2× faster |
| 98 %                    | 19.8× faster |

So with a fully-live design densely observed — Verilator's home turf — pull
*loses* ~2.5× to its own overhead. Past ~70 % dead/unobserved-per-cycle, pull
wins even observing every cycle; with sparse observation it wins always.

Real designs under a specific test sit high on this axis: a CPU running a small
program exercises a fraction of its logic per cycle, most signals don't toggle
most cycles, and a self-checking testbench observes a handful of outputs. That
is exactly the regime where pull + collapse beats a forward compiled model.

## The engineering path (how this becomes a real simulator win)

The prototype **interprets** the pull (dict/array memo + recursion). To push the
crossover down — win even at lower dead fractions and dense observation —
**compile the pull cones**: generate straight-line code per observed output's
backward cone (per-eval cost approaches Verilator's), memoise at coarse
granularity, and collapse closed multicycle chains to their pure-function form.
That is the fork's existing cone-compilation machinery (`gen_statemachine` /
`--accel`) turned **backward** and made **demand-driven**, gated by symbolic /
differential verification (verify-then-promote). See
`analytical_closed_pipeline_acceleration` and `vtable_state_specialization`
(the latter already hit ~15× Verilator on small designs by specialising eval
per state) in memory.

Net: **match** Verilator with forward accel + 3D-logic fidelity; **beat** it by
not doing the work it can't avoid — evaluate only what is observed, only when,
and skip the time in between.

## Run

    cc -O2 -o demand_eval demand_eval.c && ./demand_eval    # wallclock + crossover
    python3 lazy_eval_demo.py                               # pedagogical eval-count version
