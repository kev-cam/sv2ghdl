# `bfit merge --table` — table-driven merged models vs analytical exp

`--table` emits the `--merge`d bridge as a **table-driven `.so`** (PyMS VAE ABI)
instead of an analytical exp Verilog-A: the four diodes share one interpolated
`I(Vd)` lookup. The table comes from sampling the diode characteristic (from the
`--device-va`), so it is the **table fallback for the merged model** — the bounded,
smooth alternative that converges where PyMS's `limexp→exp` does not.

Emitter: `xyce/utils/PyMS/vae/table_model.py` (`emit_bridge_table_so`). Generate:

    bfit merge --table bridge.cir --device-va vadiode.va -o bridge_tbl.cir
    # -> brg_*.cpp ; g++ -O2 -shared -fPIC brg_*.cpp -o brg.so

## Results (full-bridge rectifier, 5 Vpk / 1 kHz, RC = 1k/10µ)

Harness in this dir: `bench_eval.cpp` (per-eval ns, dlopen + time N calls, Vd kept
in-range so exp does not overflow) and `bench_tran.py` (BE + damped Newton transient,
solves V(p), reports Newton iters + convergence). `brg_exp.cpp` is the exp baseline
(`-DCLAMP` ≈ limexp limiting).

| model                | ns/eval | Newton iters | Vout_pk | converges |
|----------------------|--------:|-------------:|--------:|-----------|
| table (O(1) uniform) |  **12.6** |   **6021** |   1.65  | yes       |
| exp (clamped/limexp) |   23.8  |     6771     |   1.79  | yes       |
| exp (raw)            |   23.2  |     6771     |   1.79  | only with Newton damping |

Findings:
- **Per-eval: table ~1.9× faster than exp** (12.6 vs 23.8 ns) — four table lookups
  beat four `exp()`. **This depends entirely on O(1) indexing.** The first cut used a
  linear breakpoint scan and measured **239 ns/eval (10× SLOWER)**; the grid is
  uniform, so `interp1d` now computes the index as `(x-x0)/dx` (no search). Lesson:
  a table is only a speedup with the right lookup.
- **Convergence: ~11% fewer Newton iterations** (6021 vs 6771) — the bounded, smooth
  table is gentler on Newton. Raw exp only diverges without damping/limiting; that
  divergence is exactly the PyMS `limexp→exp` failure the table sidesteps with no
  current-limiting hack.
- **Accuracy: Vout ~8% low** (1.65 vs 1.79 V) — 256-pt *linear* interpolation across
  the exponential knee. Denser/non-uniform sampling near Vd≈0.6–0.8 (or a higher-order
  interp) closes the gap; the table is exact at its breakpoints.

Net: the table model is worth it for a merged structure that won't otherwise converge
in PyMS (its reason for existing), and as a bonus it is faster per eval once indexed
in O(1) — at a tunable accuracy cost set by the sample density.
