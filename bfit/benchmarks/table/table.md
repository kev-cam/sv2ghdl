# bfit table mode — table/PWL models from the start (SIMPLIS-style)

`bfit table` is an **independent mode**: replace every device with a table /
piecewise-linear model FROM THE START and never attempt the accurate
analytical/JIT solve. It trades accuracy for speed and rock-solid convergence —
every nonlinearity becomes a bounded, smooth, interpolated lookup, so Newton never
sees a runaway exp and the matrix stays well-scaled. This is the SIMPLIS bet (a
deliberately approximate engine that always converges), not SPICE's "exact device,
hope it converges". It is orthogonal to `--merge` (a lossless structural rewrite).

    bfit table bridge.cir --device-va vadiode.va -o bridge_table.cir
    # -> one table .so per distinct diode .model; D instances -> Y<model> table instances

Table models are PyMS/Xyce VAE-ABI `.so` (`xyce/utils/PyMS/vae/table_model.py`).
Coverage today: diodes (1-D I(Vd)). MOS/BJT pass through untouched and are reported
until their 2-D table emitters are ported into PyMS table_model (devchar has the
characterization). Nothing is silently left in accurate mode.

## Result — discrete 4-diode bridge run as a real MNA circuit (5 Vpk, Rl = 1k)

`bench_discrete.py` solves the table-ized bridge (4 separate 2-node diode models,
no merge) as a 4-unknown Newton circuit over one input cycle; `diode_exp.cpp` is the
accurate exp baseline (`-DCLAMP` ≈ limexp limiting).

| model            | Newton iters | time(s) | Vout_pk | fails |
|------------------|-------------:|--------:|--------:|------:|
| table mode       |     **881**  | **0.016** | 3.36  |   0   |
| exp (limexp)     |      1390    |  0.026  |  3.62   |   0   |
| exp (raw)        |      1390    |  0.025  |  3.62   |   0*  |

- **~37% fewer Newton iterations, ~1.6× faster.** The savings are *bigger* at the
  circuit level than per-device (11% for one component, below) because the exp's
  steepness compounds across the coupled solve while the table's bounded slopes keep
  Newton tame.
- **Accuracy: ~7%** (Vout 3.36 vs 3.62 V) — 256-pt linear interp across the knee;
  sample density / higher-order interp is the knob.
- *raw exp converged here only because of the 0.5 V Newton step clamp; without
  damping it overflows — exactly the runaway the table sidesteps with no limiting hack.

## Variant — `bfit merge --table` (the merged structure as one table)

`--merge --table` renders the *merged* bridge as ONE 4-terminal table `.so`
(`emit_bridge_table_so`, 4 diodes sharing one lookup) — the table fallback for a
structure that won't converge in PyMS. Per-eval (bench_eval.cpp, exp in-range):

| model                | ns/eval | Newton iters | converges |
|----------------------|--------:|-------------:|-----------|
| table (O(1) uniform) |  **12.6** |   **6021** | yes       |
| exp (clamped)        |   23.8  |     6771     | yes       |

**Lesson: a table is only a speedup with the right lookup.** The first cut linear-
scanned breakpoints = 239 ns/eval (10× SLOWER); the grid is uniform, so `interp1d`
now indexes O(1) as `(x-x0)/dx`.
