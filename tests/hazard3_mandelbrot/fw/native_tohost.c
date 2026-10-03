// SPDX-License-Identifier: Apache-2.0
//
// Native (host) runtime that produces the golden TOHOST streams.
//
// Derived from sw/native.c of verilator-hazard3-mandelbrot-testbench,
//   https://github.com/verijit/verilator-hazard3-mandelbrot-testbench
//   commit 9e768306a3cb03b9894aee609545fe3ed829d6be, file sw/native.c
// which is licensed under the Apache License, Version 2.0 (the upstream file
// carries no copyright line of its own). The licence text is
// LICENSES/Apache-2.0.txt at the root of this repository.
//
// CHANGED for the sv2ghdl hazard3 suite (tests/hazard3_mandelbrot/README.md):
// used_image() prints the image as the TOHOST stream that fw/rv32_tohost.c
// sends from the simulated CPU, one "TOHOST %08x" line per word, which is
// exactly what tb/tb_hazard3.v prints for each captured word (Verilog %h of a
// 32-bit value: eight lower-case hex digits). Upstream's version printed a
// P3 PPM. used_i32() and used_str() are as upstream.
//
// Build: build_firmware.py compiles the UNCHANGED upstream sw/mandelbrot.c
// with gcc and this file force-included (-include), with the same
// -DSIZE_LOG2/-DMAX_ITERS as the RISC-V build, and stores the program's
// output as the variant's golden.tohost.

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#ifndef MAX_ITERS
#error "build with -DSIZE_LOG2=<n> -DMAX_ITERS=<n> (see build_firmware.py): the TOHOST header reports MAX_ITERS"
#endif

void used_i32(int32_t* ptr, size_t size) {
  printf("%p:\n", (void*) ptr);
  for (size_t it = 0; it < size; it++) {
    if (it != 0) {
      printf(", ");
      if (it % 32 == 0) {
        printf("\n");
      }
    }
    printf("%d", ptr[it]);
  }
  printf("\n");
}

void used_str(const char* str) {
  printf("%s\n", str);
}

static void tohost(uint32_t v) {
  printf("TOHOST %08x\n", (unsigned) v);
}

void used_image(uint32_t* image, size_t height, size_t width) {
  uint32_t sum = 2166136261u;            // FNV-1a offset basis
  size_t n = height * width;
  tohost((uint32_t) width);              // SIZE (the image is SIZE x SIZE)
  tohost(MAX_ITERS);
  for (size_t it = 0; it < n; it++) {
    uint32_t pixel = image[it];
    tohost(pixel);
    sum = (sum ^ pixel) * 16777619u;     // FNV-1a prime
  }
  tohost(sum & 0x7fffffffu);
  tohost(0xffffffffu);                   // DONE
}
