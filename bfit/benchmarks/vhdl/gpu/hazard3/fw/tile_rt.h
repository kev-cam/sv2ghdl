/* tile_rt.h - RV32 runtime of the split-render firmware.  bfit GPU-farm kit
 * (PolyForm Noncommercial 1.0.0).
 *
 * Build order (build_h3.sh): clang <upstream sw/Makefile flags>
 *     --include <checkout>/sw/rv32.c     (upstream, unchanged: _start, the
 *                                          int types, memset, used_*)
 *     --include fw/tile_rt.h             (this file)
 *     tile_mandelbrot.c                  (gen_tile_fw.py output)
 * main() reads the tile word from TILE_ID (the SoC's tile_id input), renders
 * that tile with the generated mandelbrot_tile(), and streams to TOHOST:
 *     tile word, the 2^m pixel words (each < 2^24), DONE 0xffffffff.
 * No image buffer and no memset: the stack holds two small frames.
 */
#include "tile_geom.h"
#define TOHOST_ADDR  0x00001000u
#define TILE_ID_ADDR 0x00001004u
#define TOHOST_DONE  0xffffffffu
#define TOHOST_WRITE(v) (*(volatile uint32_t *) TOHOST_ADDR = (uint32_t) (v))
#define tile_put(v) TOHOST_WRITE(v)

void mandelbrot_tile(uint32_t tile);

int main() {
  uint32_t tile = *(volatile uint32_t *) TILE_ID_ADDR;
  TOHOST_WRITE(tile);
  mandelbrot_tile(tile);
  TOHOST_WRITE(TOHOST_DONE);
  return 0;
}
