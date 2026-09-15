// Yuri Panchul's a_plus_b_using_wrapped_fifos benchmark (basics-graphics-music
// labs/4_microarchitecture/4_2_fifo/4_2_9_..._benchmark/tb.sv), ported cycle-
// accurately onto the gen_statemachine 2-phase model: same phases (reset 3+3,
// back-to-back 20, only-a, only-b, backpressure 20, random until 10M sum
// transfers, drain depth*2+3), same driver update rules read pre-edge, same
// scoreboard (a/b queues, expected = a+b, mismatch/unexpected/leftover =
// errors), same transfer counters.  $urandom -> xorshift32 per instance.
// Every instance self-checks; nothing is displayed that wasn't computed.
//   yuri <N> [max_transfers=10000000] [seed=1]
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#ifdef __CUDACC__
#define SM_DEVICE __device__
#else
#define SM_DEVICE
#endif
#define SM_NO_MAIN 1
#include MODEL_C
#define WIDTH 4
#define DEPTH 4
#define QN 64
struct result { uint64_t cycles; uint32_t sum_count, a_count, b_count, errors; };
SM_DEVICE static inline uint32_t urnd(uint32_t *x) { uint32_t v = *x; v ^= v << 13; v ^= v >> 17; v ^= v << 5; *x = v; return v; }
SM_DEVICE static inline struct result run_yuri(uint32_t seed, uint32_t max_transfers) {
    state_t s; inputs_t in; outputs_t o; memset(&in, 0, sizeof in); memset(&o, 0, sizeof o);
    sm_reset(&s);
    uint32_t rng = seed ? seed : 0x9E3779B9u;
    uint8_t aq[QN], bq[QN]; int ah = 0, at = 0, bh = 0, bt = 0;      // scoreboard queues (head/tail)
    uint32_t a_count = 0, b_count = 0, sum_count = 0, errors = 0; uint64_t cycles = 0;
    int rst = 0, was_reset = 0;
    enum { P_INIT, P_RST, P_B2B, P_ONLYA_WAIT, P_ONLYA, P_ONLYB_WAIT, P_ONLYB, P_BP, P_RANDOM, P_DRAIN, P_DONE } ph = P_INIT; int cnt = 0;
    for (;;) {                                                       // one iteration = one posedge
        cycles++;
        sm_comb(&s, &in, &o);
        int a_fire = (int)(in._a_valid & o._a_ready), b_fire = (int)(in._b_valid & o._b_ready), sum_fire = (int)(o._sum_valid & in._sum_ready);
        // ---- checker (always @posedge, pre-edge values) ----
        if (rst) { ah = at = bh = bt = 0; was_reset = 1; }
        else if (was_reset) {
            if (a_fire) { aq[at % QN] = (uint8_t)in._a_data; at++; }
            if (b_fire) { bq[bt % QN] = (uint8_t)in._b_data; bt++; }
            if (sum_fire) {
                if (at == ah || bt == bh) errors++;                                       // unexpected sum
                else { uint8_t e = (uint8_t)((aq[ah % QN] + bq[bh % QN]) & ((1u << WIDTH) - 1)); ah++; bh++;
                       if (e != (uint8_t)o._sum_data) errors++; }                        // data mismatch
            }
            if (at - ah > QN || bt - bh > QN) errors++;                                   // (never: bounded by the FIFOs)
        }
        // ---- DUT edge ----
        if (rst) sm_reset(&s); else sm_clock(&s, &in);
        // ---- data driver (always @posedge) ----
        if (rst) { in._a_data = 0; in._b_data = 0; }
        else { if (a_fire) in._a_data = urnd(&rng) & ((1u << WIDTH) - 1); if (b_fire) in._b_data = urnd(&rng) & ((1u << WIDTH) - 1); }
        // ---- control driver (the initial block), reads PRE-edge counts ----
        uint32_t a_old = a_count, b_old = b_count, sum_old = sum_count;
        if (!rst) { a_count += a_fire; b_count += b_fire; sum_count += sum_fire; }       // NBA counters
        switch (ph) {
        case P_INIT:   if (++cnt == 3) { rst = 1; ph = P_RST; cnt = 0; } break;
        case P_RST:    if (++cnt == 3) { rst = 0; in._a_valid = 1; in._b_valid = 1; in._sum_ready = 1; ph = P_B2B; cnt = 0; } break;
        case P_B2B:    if (++cnt == 20) { ph = P_ONLYA_WAIT; }   /* falls into the while(~b_ready) check at this same edge */
                       if (ph != P_ONLYA_WAIT) break;
                       /* fallthrough */
        case P_ONLYA_WAIT: if (o._b_ready) { in._b_valid = 0; ph = P_ONLYA; cnt = 0; } break;
        case P_ONLYA:  if (++cnt == 20) { in._b_valid = 1; ph = P_ONLYB_WAIT; if (o._a_ready) { in._a_valid = 0; ph = P_ONLYB; cnt = 0; } } break;
        case P_ONLYB_WAIT: if (o._a_ready) { in._a_valid = 0; ph = P_ONLYB; cnt = 0; } break;
        case P_ONLYB:  if (++cnt == 20) { in._a_valid = 1; in._b_valid = 1; in._sum_ready = 0; ph = P_BP; cnt = 0; } break;
        case P_BP:     if (++cnt == 20) { ph = P_RANDOM; goto random_body; } break;
        case P_RANDOM:
            if (sum_old == max_transfers) { in._sum_ready = 1; ph = P_DRAIN; cnt = 0; break; }
        random_body:
            if (a_old == max_transfers || (a_old == max_transfers - 1 && in._a_valid && o._a_ready)) in._a_valid = 0;
            else if (!in._a_valid || o._a_ready) in._a_valid = urnd(&rng) & 1u;
            if (b_old == max_transfers || (b_old == max_transfers - 1 && in._b_valid && o._b_ready)) in._b_valid = 0;
            else if (!in._b_valid || o._b_ready) in._b_valid = urnd(&rng) & 1u;
            in._sum_ready = urnd(&rng) & 1u;
            break;
        case P_DRAIN:  if (++cnt == DEPTH * 2 + 3) ph = P_DONE; break;
        default: break;
        }
        if (ph == P_DONE) break;
    }
    if (at != ah || bt != bh) errors++;                                                    // data left in the model queues
    if (a_count != b_count || a_count != sum_count) errors++;                              // transfer counts disagree
    struct result r = { cycles, sum_count, a_count, b_count, errors }; return r;
}
#ifdef __CUDACC__
__global__ void yuri_kernel(struct result *out, int n, uint32_t max_transfers, uint32_t seed) {
    int gid = blockIdx.x * blockDim.x + threadIdx.x;
    if (gid < n) out[gid] = run_yuri(seed + 0x9E3779B9u * (uint32_t)gid, max_transfers);
}
#define CK(x) do { cudaError_t e_ = (x); if (e_ != cudaSuccess) { printf("CUDA-ERROR %s @%d\n", cudaGetErrorString(e_), __LINE__); exit(3); } } while (0)
#endif
static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + 1e-9 * t.tv_nsec; }
int main(int argc, char **argv) {
    int n = argc > 1 ? atoi(argv[1]) : 1; uint32_t maxt = argc > 2 ? (uint32_t)atol(argv[2]) : 10000000u; uint32_t seed = argc > 3 ? (uint32_t)atol(argv[3]) : 1u;
    struct result *h = (struct result *)calloc(n, sizeof *h);
    double t0, t1; const char *dev;
#ifdef __CUDACC__
    struct result *d; CK(cudaMalloc(&d, n * sizeof *d));
    yuri_kernel<<<(n + 127) / 128, 128>>>(d, n, 100u, seed); CK(cudaGetLastError()); CK(cudaDeviceSynchronize());   // warm-up (100 transfers)
    t0 = now(); yuri_kernel<<<(n + 127) / 128, 128>>>(d, n, maxt, seed); CK(cudaGetLastError()); CK(cudaDeviceSynchronize()); t1 = now();
    CK(cudaMemcpy(h, d, n * sizeof *h, cudaMemcpyDeviceToHost)); cudaDeviceProp p; CK(cudaGetDeviceProperties(&p, 0)); dev = p.name;
#else
    dev = "cpu"; t0 = now();
    #pragma omp parallel for schedule(dynamic, 1)
    for (int g = 0; g < n; g++) h[g] = run_yuri(seed + 0x9E3779B9u * (uint32_t)g, maxt);
    t1 = now();
#endif
    uint64_t cyc = 0, err = 0, sums = 0; uint64_t cmin = ~0ull, cmax = 0;
    for (int g = 0; g < n; g++) { cyc += h[g].cycles; err += h[g].errors; sums += h[g].sum_count; if (h[g].cycles < cmin) cmin = h[g].cycles; if (h[g].cycles > cmax) cmax = h[g].cycles; }
    printf("YURI dev=\"%s\" N=%d max_transfers=%u secs=%.4f inst0: cycles=%llu sum=%u a=%u b=%u errors=%u | all: cycles=%llu (min %llu max %llu) sums=%llu errors=%llu | inst-cyc/s=%.4e transfers/s=%.4e %s\n",
           dev, n, maxt, t1 - t0, (unsigned long long)h[0].cycles, h[0].sum_count, h[0].a_count, h[0].b_count, h[0].errors,
           (unsigned long long)cyc, (unsigned long long)cmin, (unsigned long long)cmax, (unsigned long long)sums, (unsigned long long)err,
           cyc / (t1 - t0), sums / (t1 - t0), err ? "*** ERRORS ***" : "PASS");
    return err ? 1 : 0;
}
