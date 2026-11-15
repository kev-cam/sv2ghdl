// Verilator test bench for the N x N weight-stationary systolic array (sa.sv).
// Loads W, streams M skewed activation rows, de-skews the bottom outputs, checks C = A*W, times the stream.
// Usage: Vsa [M rows] [seed]      (N is a compile-time parameter: -DSA_N=...)
#include "Vsa.h"
#include "verilated.h"
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <vector>
#include <chrono>

#ifndef SA_N
#define SA_N 128
#endif
static const int N = SA_N;

// Pack/unpack helpers for the wide ports (Verilator exposes >64-bit ports as uint32 words).
template <typename P> static void set_bytes(P &port, const int8_t *v, int n) {
    uint32_t *w = reinterpret_cast<uint32_t *>(&port);
    for (int i = 0; i < n; i++) {
        int word = i / 4, sh = (i % 4) * 8;
        w[word] = (w[word] & ~(0xFFu << sh)) | ((uint32_t)(uint8_t)v[i] << sh);
    }
}
template <typename P> static int32_t get_word(const P &port, int i) {
    const uint32_t *w = reinterpret_cast<const uint32_t *>(&port);
    return (int32_t)w[i];
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    long M = argc > 1 ? atol(argv[1]) : 4096;
    unsigned seed = argc > 2 ? (unsigned)atol(argv[2]) : 1;
    srand(seed);

    std::vector<int8_t> A((size_t)M * N), W((size_t)N * N);
    for (auto &x : A) x = (int8_t)(rand() & 0xFF);
    for (auto &x : W) x = (int8_t)(rand() & 0xFF);
    // reference C = A * W   (M x N) = (M x N) * (N x N)
    std::vector<int32_t> C((size_t)M * N, 0);
    for (long m = 0; m < M; m++)
        for (int k = 0; k < N; k++) {
            int32_t a = A[m * N + k];
            if (!a) continue;
            const int8_t *wrow = &W[(size_t)k * N];
            int32_t *crow = &C[(size_t)m * N];
            for (int c = 0; c < N; c++) crow[c] += a * wrow[c];
        }

    Vsa *top = new Vsa;
    std::vector<int8_t> abuf(N), wbuf(N);
    auto tick = [&]() { top->clk = 0; top->eval(); top->clk = 1; top->eval(); };

    // reset
    top->rst = 1; top->load_w = 0; memset(&top->a_in, 0, sizeof(top->a_in)); memset(&top->w_in, 0, sizeof(top->w_in));
    tick(); tick();
    top->rst = 0;

    // weight load: at cycle t inject W[N-1-t][c] into column c
    top->load_w = 1;
    for (int t = 0; t < N; t++) {
        for (int c = 0; c < N; c++) wbuf[c] = W[(size_t)(N - 1 - t) * N + c];
        set_bytes(top->w_in, wbuf.data(), N);
        tick();
    }
    top->load_w = 0;
    memset(&top->w_in, 0, sizeof(top->w_in));

    // stream: cycle s (0 .. M+2N-1): row k gets A[s-k][k] if 0 <= s-k < M; column c emits C[s-N-c+1][c] (registered: read after tick)
    long total = M + 2 * N;
    long errors = 0, checked = 0;
    auto t0 = std::chrono::steady_clock::now();
    for (long s = 0; s < total; s++) {
        for (int k = 0; k < N; k++) {
            long m = s - k;
            abuf[k] = (m >= 0 && m < M) ? A[(size_t)m * N + k] : 0;
        }
        set_bytes(top->a_in, abuf.data(), N);
        tick();
        // after this edge the bottom register of column c holds the value for row m = s - N - c + 1
        for (int c = 0; c < N; c++) {
            long m = s - N - c + 1;
            if (m >= 0 && m < M) {
                int32_t got = get_word(top->p_out, c);
                if (got != C[(size_t)m * N + c]) { if (errors < 5) printf("MISMATCH m=%ld c=%d got=%d exp=%d\n", m, c, got, C[(size_t)m * N + c]); errors++; }
                checked++;
            }
        }
    }
    auto t1 = std::chrono::steady_clock::now();
    double secs = std::chrono::duration<double>(t1 - t0).count();
    printf("N=%d M=%ld cycles=%ld checked=%ld errors=%ld  time=%.3f s  rate=%.1f cycles/s  (%.2f us/cycle, %.3g PE-cycles/s)\n",
           N, M, total, checked, errors, secs, total / secs, 1e6 * secs / total, (double)N * N * total / secs);
    top->final();
    delete top;
    return errors ? 1 : 0;
}
