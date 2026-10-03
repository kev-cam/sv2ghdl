// SPDX-License-Identifier: Apache-2.0
//
// Bare-metal RV32 runtime for the hazard3 regression variants.
//
// Derived from sw/rv32.c of verilator-hazard3-mandelbrot-testbench,
//   https://github.com/verijit/verilator-hazard3-mandelbrot-testbench
//   commit 9e768306a3cb03b9894aee609545fe3ed829d6be, file sw/rv32.c
// which is licensed under the Apache License, Version 2.0 (the upstream file
// carries no copyright line of its own). The licence text is
// LICENSES/Apache-2.0.txt at the root of this repository.
//
// CHANGED for the sv2ghdl hazard3 suite (tests/hazard3_mandelbrot/README.md):
//   - used_image() no longer discards the image: it streams it to the TOHOST
//     word, which the portable SoC snoops on its data port and the testbench
//     prints. The stream is
//         SIZE, MAX_ITERS,                       (header)
//         SIZE*SIZE pixel words, row-major,      (each < 2^24)
//         checksum,                              (FNV-1a over the pixels, bit 31 cleared)
//         0xffffffff                             (DONE; the only word with bit 31 set)
//   - the TOHOST_* macros, the checksum and the MAX_ITERS check were added.
// _start (no stack pointer set up: sp is 0 out of reset and the stack wraps
// to the top of the data RAM), the type definitions, used_i32(), used_str()
// and memset() are as upstream.
//
// Build: build_firmware.py compiles the UNCHANGED upstream sw/mandelbrot.c
// with this file force-included (clang --include), as upstream's sw/Makefile
// does with rv32.c.

#ifndef MAX_ITERS
#error "build with -DSIZE_LOG2=<n> -DMAX_ITERS=<n> (see build_firmware.py): the TOHOST header reports MAX_ITERS"
#endif

int main();

#define NULL ((void*) 0)

__asm__(
  ".global _start\n"
  ".type _start, @function\n"
  "_start:\n"
  "  call main\n"
  // Protect early finish from prefetch.
  "  nop\n"
  "  nop\n"
  "  nop\n"
  ".align 2\n"
  ".option push\n"
  ".option arch, -c\n"
  "  ebreak\n"
  ".option pop\n"
  ".size _start, .-_start\n"
);

typedef _BitInt(8) int8_t;
typedef _BitInt(16) int16_t;
typedef _BitInt(32) int32_t;
typedef _BitInt(64) int64_t;

typedef unsigned _BitInt(8) uint8_t;
typedef unsigned _BitInt(16) uint16_t;
typedef unsigned _BitInt(32) uint32_t;
typedef unsigned _BitInt(64) uint64_t;

typedef unsigned _BitInt(sizeof(void*) * 8) size_t;

// TOHOST: one word on the data port. The portable SoC's snooper captures every
// store to this address (the data RAM ignores the high address bits, so the
// store also lands in a RAM word nothing else uses). Keep in step with
// TOHOST_ADDR in gen_soc.py.
#define TOHOST_ADDR 0x00001000u
#define TOHOST_DONE 0xffffffffu
#define TOHOST_WRITE(v) (*(volatile uint32_t*) TOHOST_ADDR = (uint32_t) (v))

void used_i32(int32_t* ptr, size_t size) {
  asm volatile("" : : "r"(ptr) : "memory");
}

void used_str(const char* str) {
  asm volatile("" : : "r"(str) : "memory");
}

void used_image(uint32_t* image, size_t height, size_t width) {
  uint32_t sum = 2166136261u;            // FNV-1a offset basis
  size_t n = height * width;
  TOHOST_WRITE(width);                   // SIZE (the image is SIZE x SIZE)
  TOHOST_WRITE(MAX_ITERS);
  for (size_t it = 0; it < n; it++) {
    uint32_t pixel = image[it];
    TOHOST_WRITE(pixel);
    sum = (sum ^ pixel) * 16777619u;     // FNV-1a prime
  }
  TOHOST_WRITE(sum & 0x7fffffffu);
  TOHOST_WRITE(TOHOST_DONE);
}

void memset(void* ptr, int value, size_t num) {
  uint8_t* bytes = (uint8_t*) ptr;
  for (size_t it = 0; it < num; it++) {
    bytes[it] = (uint8_t) value;
  }
}
