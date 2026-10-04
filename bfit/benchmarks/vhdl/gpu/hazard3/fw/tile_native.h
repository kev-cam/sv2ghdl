/* tile_native.h - host (native) golden of the split render.  bfit GPU-farm
 * kit (PolyForm Noncommercial 1.0.0).
 *
 *   gcc -O2 -DSIZE_LOG2=10 -DMAX_ITERS=256 -include fw/tile_native.h -o tile_native tile_mandelbrot.c
 *   tile_native <m> [image.ppm] [per-tile.txt]
 * Runs the SAME generated mandelbrot_tile() natively for every tile of
 * geometry m, builds each tile's TOHOST stream exactly as the firmware sends
 * it (tile word, pixels, DONE), and prints
 *   NATIVE m= NT= CHK0= AGG= pixels=
 * where CHK is h3farm.cu's per-instance fold (FNV-1a-64 over the words) and
 * AGG its population hash (FNV-1a-64 over the CHK vector bytes, tile order).
 * It also checks the tiles partition the image (every pixel exactly once)
 * and writes the union as the P6 image main_verilator.cpp writes (output.ppm),
 * whose md5 must be upstream's 693d2391e979a114a82af00b3e64e54c.
 */
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include "tile_geom.h"

static uint32_t *tn_buf;
static size_t tn_n;
#define tile_put(v) (tn_buf[tn_n++] = (uint32_t) (v))
void mandelbrot_tile(uint32_t tile);

static uint64_t tn_fnv_words(const uint32_t *w, size_t n) {
  uint64_t h = 0xcbf29ce484222325ull;
  for (size_t i = 0; i < n; i++) h = (h ^ w[i]) * 0x100000001b3ull;
  return h;
}
static uint64_t tn_fnv_bytes(uint64_t h, uint64_t v) {
  for (int b = 0; b < 8; b++) { h ^= (v >> (8 * b)) & 0xff; h *= 0x100000001b3ull; }
  return h;
}

int main(int argc, char **argv) {
  const uint32_t m = argc > 1 ? (uint32_t) atoi(argv[1]) : 0;
  const size_t S = (size_t) 1 << SIZE_LOG2, nt = (size_t) 1 << (2 * SIZE_LOG2 - m), k = (size_t) 1 << m;
  uint32_t *img = calloc(S * S, sizeof *img);
  unsigned char *seen = calloc(S * S, 1);
  FILE *per = argc > 3 ? fopen(argv[3], "w") : NULL;
  tn_buf = malloc((k + 2) * sizeof *tn_buf);
  if (!img || !seen || !tn_buf || m > 2 * SIZE_LOG2) { fprintf(stderr, "tile_native: bad m or out of memory\n"); return 2; }
  uint64_t agg = 0xcbf29ce484222325ull, chk0 = 0;
  for (size_t t = 0; t < nt; t++) {
    const uint32_t tile = (uint32_t) t | (m << 24);
    tn_n = 0;
    tile_put(tile);
    mandelbrot_tile(tile);
    tile_put(0xffffffffu);
    if (tn_n != k + 2) { fprintf(stderr, "tile %zu: %zu words, expected %zu\n", t, tn_n, k + 2); return 1; }
    const uint64_t chk = tn_fnv_words(tn_buf, tn_n);
    if (t == 0) chk0 = chk;
    agg = tn_fnv_bytes(agg, chk);
    for (size_t j = 0; j < k; j++) {
      const size_t p = TILE_P(tile, j);
      img[p] = tn_buf[1 + j];
      seen[p]++;
    }
    if (per) fprintf(per, "%zu %016llX\n", t, (unsigned long long) chk);
  }
  for (size_t p = 0; p < S * S; p++)
    if (seen[p] != 1) { fprintf(stderr, "pixel %zu covered %d times\n", p, seen[p]); return 1; }
  if (argc > 2) {
    FILE *f = fopen(argv[2], "wb");
    if (!f) return 1;
    fprintf(f, "P6\n%zu %zu\n255\n", S, S);
    for (size_t p = 0; p < S * S; p++) { fputc((img[p] >> 16) & 0xff, f); fputc((img[p] >> 8) & 0xff, f); fputc(img[p] & 0xff, f); }
    fclose(f);
  }
  if (per) fclose(per);
  printf("NATIVE m=%u NT=%zu CHK0=%016llX AGG=%016llX pixels=%zu\n", m, nt,
         (unsigned long long) chk0, (unsigned long long) agg, S * S);
  return 0;
}
