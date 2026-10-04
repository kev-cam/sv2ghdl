// tb_h3twin.cpp - Verilator twin of h3farm.cu: the same generated SoC
// (soc_farm.v, top 'soc') run by Verilator with h3farm's per-instance protocol,
// so the two print comparable numbers.  bfit GPU-farm kit (PolyForm NC 1.0.0).
// Per instance: a fresh model (initial blocks + $readmemh run again), tile_id =
// gid | m << 24, then per cycle c: clock low + eval (outputs = registers after
// c-1 edges), fold tohost_data when tohost_valid (FNV-1a-64 over words), stop
// at DONE 0xffffffff with done = c, else clock high + eval.  Clocking as
// upstream's main_verilator.cpp (low, eval, high, eval per cycle).
//   Vtwin <cycle-cap> <N>      env H3_GID0, H3_TILE_LOG2, H3_PERINST=<file>
#include "Vsoc.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <ctime>

static double now() { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + 1e-9 * t.tv_nsec; }
static const uint64_t OFF = 0xcbf29ce484222325ull, PR = 0x100000001b3ull;
static uint64_t fnv_bytes(uint64_t h, uint64_t v) { for (int b = 0; b < 8; b++) { h ^= (v >> (8 * b)) & 0xff; h *= PR; } return h; }

int main(int argc, char **argv) {
    long cap = argc > 1 ? atol(argv[1]) : 1000000;
    long n = argc > 2 ? atol(argv[2]) : 1;
    uint32_t gid0 = getenv("H3_GID0") ? (uint32_t)strtoul(getenv("H3_GID0"), 0, 0) : 0;
    uint32_t m = getenv("H3_TILE_LOG2") ? (uint32_t)atoi(getenv("H3_TILE_LOG2")) : 0;
    FILE *pf = getenv("H3_PERINST") ? fopen(getenv("H3_PERINST"), "w") : 0;
    uint64_t agg = OFF, cagg = OFF, chk0 = 0; uint32_t done0 = 0, dmax = 0, dmin = 0xffffffffu; double ic = 0; long ndone = 0;
    double t0 = now();
    for (long g = 0; g < n; g++) {
        VerilatedContext *ctx = new VerilatedContext;
        Vsoc *top = new Vsoc(ctx);
        top->tile_id = (gid0 + (uint32_t)g) | (m << 24);
        top->clock = 0;
        uint64_t chk = OFF; uint32_t done = 0, nw = 0; long c;
        for (c = 1; c <= cap; c++) {
            top->clock = 0; top->eval();
            if (top->tohost_valid) {
                uint32_t w = top->tohost_data; chk = (chk ^ w) * PR; nw++;
                if (w == 0xffffffffu) { done = (uint32_t)c; break; }
            }
            top->clock = 1; top->eval();
        }
        top->final(); delete top; delete ctx;
        if (g == 0) { chk0 = chk; done0 = done; }
        agg = fnv_bytes(agg, chk); cagg = fnv_bytes(cagg, done);
        ic += done ? done : cap;
        if (done) { ndone++; if (done > dmax) dmax = done; if (done < dmin) dmin = done; }
        if (pf) fprintf(pf, "%u %016llX %u %u\n", gid0 + (uint32_t)g, (unsigned long long)chk, done, nw);
    }
    double dt = now() - t0;
    if (pf) fclose(pf);
    if (!ndone) dmin = 0;
    printf("VERILATOR design=hazard3 N=%ld cap=%ld secs=%.4f cyc_per_s=%.4e CHK0=%016llX AGG=%016llX DONE0=%u CAGG=%016llX ndone=%ld done_min=%u done_max=%u inst_cyc=%.0f gid0=%u m=%u\n",
           n, cap, dt, ic / dt, (unsigned long long)chk0, (unsigned long long)agg, done0, (unsigned long long)cagg, ndone, dmin, dmax, ic, gid0, m);
    return 0;
}
