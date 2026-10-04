/* tile_geom.h - split-render tile geometry, shared by the RV32 firmware
 * (fw/tile_rt.h) and the host golden (fw/tile_native.h).  bfit GPU-farm kit
 * (PolyForm Noncommercial 1.0.0).
 *
 * The 32-bit tile word (the SoC's tile_id input, read at TILE_ID 0x1004) is
 *     tile = idx | (m << 24)
 * with 2^m pixels per tile and NT = 2^(2*SIZE_LOG2 - m) tiles.  Pixel k of
 * tile idx is the row-major pixel  p = idx + k * NT  (k = 0 .. 2^m - 1), so a
 * tile is a column of pixels spaced NT/SIZE rows apart (m = 0: one pixel,
 * p = idx).  Every pixel belongs to exactly one tile; neighbouring tiles (one
 * GPU warp) render neighbouring pixels, so their run lengths are similar.
 * One firmware image therefore serves every tile count N = NT = 2^(20-m)
 * at SIZE_LOG2 = 10; the harness picks m (h3farm H3_TILE_LOG2).
 * Used inside mandelbrot_tile() only, after mandelbrot.c has defined SIZE.
 */
#define TILE_M(t)        ((t) >> 24)
#define TILE_IDX(t)      ((t) & 0xffffffu)
#define TILE_NT_LOG2(t)  (2 * SIZE_LOG2 - TILE_M(t))
#define TILE_K(t)        ((size_t) 1 << TILE_M(t))
#define TILE_P(t, k)     ((size_t) TILE_IDX(t) + ((size_t) (k) << TILE_NT_LOG2(t)))
#define TILE_X(t, k)     (TILE_P(t, k) & (SIZE - 1))
#define TILE_Y(t, k)     (TILE_P(t, k) >> SIZE_LOG2)
