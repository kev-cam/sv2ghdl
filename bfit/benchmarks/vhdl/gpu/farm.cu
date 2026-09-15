// GPU/CPU instance farm for a gen_statemachine 2-phase model, replaying the
// gen_tb.py LFSR testbench per instance.  Instance 0 = canonical stimulus
// (its CHK must equal the VHDL engines'); instances >0 are decorrelated by
// seed.  Same source builds for CPU (g++ -x c++ -DFARM_CPU) and GPU (nvcc).
//   farm <cycles> <N> [block] [reps] [ngpu]   (ngpu: instances split across devices 0..ngpu-1, global ids)
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
#ifdef FARM_SOA
// Struct-of-arrays farm: every state/input/output member is a pointer to an
// [n]-strided buffer; SM_N is the runtime instance count, sm_gid the instance.
#ifdef __CUDACC__
__device__ int sm_n_rt; __device__ int sm_gid_base;
#define SM_N sm_n_rt
#define sm_gid (sm_gid_base + (int)(blockIdx.x * blockDim.x + threadIdx.x))
#else
static int sm_n_rt; static thread_local int sm_gid_cpu;
#define SM_N sm_n_rt
#define sm_gid sm_gid_cpu
#endif
#endif
#include MODEL_C
#include STIM_H
#ifdef FARM_SOA
#include SOA_ALLOC_H
#ifdef __CUDACC__
__device__ state_t g_state; __device__ inputs_t g_in; __device__ outputs_t g_out;
#else
static state_t g_state; static inputs_t g_in; static outputs_t g_out;
#endif
#endif
#ifdef STIM_CLKMASK
#define STIM_CLOCK(s, in) sm_clock_masked((s), (in), STIM_CLKMASK)   // extra clocks held: main clock bit only
#else
#define STIM_CLOCK(s, in) sm_clock((s), (in))
#endif

SM_DEVICE static inline uint32_t seed_for(uint32_t gid) {
    if (gid == 0) return 0x12345678u;              // = 305419896, gen_tb.py's seed
    uint32_t z = gid * 0x9E3779B9u; z ^= z >> 16; z *= 0x85EBCA6Bu; z ^= z >> 13; z *= 0xC2B2AE35u; z ^= z >> 16;
    return z ? z : 0xA5A5A5A5u;
}
SM_DEVICE static inline uint64_t run_instance(uint32_t gid, int cycles) {
#ifdef FARM_SOA
    state_t &s = g_state; inputs_t &in = g_in; outputs_t &o = g_out;   // buffers pre-zeroed by host
    sm_reset(&s);
#else
    state_t s; inputs_t in; outputs_t o;
    memset(&in, 0, sizeof in); memset(&o, 0, sizeof o);
    sm_reset(&s);
#endif
#if STIM_HAS_RST
    stim_reset(&in, 1);
    for (int k = 0; k < 4; k++) STIM_CLOCK(&s, &in);   // rst held for 4 posedges
    stim_reset(&in, 0);
#endif
    uint32_t lfsr = seed_for(gid); uint64_t chk = 0;
    for (int c = 1; c <= cycles; c++) {
        sm_comb(&s, &in, &o); chk = stim_fold(chk, &o);   // TB folds at posedge, delta 0
#if defined(STIM_DUMP_CYCLE) && !defined(__CUDACC__)
        if (gid == 0 && c == STIM_DUMP_CYCLE) stim_print(&o);
#endif
        sm_clock(&s, &in);                                  // DUT posedge with the old inputs
#if STIM_HAS_RST
        if ((c & 511) == 0) stim_reset(&in, 1); else if ((c & 511) == 2) stim_reset(&in, 0);  // RESTART=512
#endif
        stim_drive(&lfsr, &in);                             // new inputs land next delta
    }
    return chk;
}
#ifdef __CUDACC__
__global__ void farm_kernel(uint64_t *out, int cycles, int n, int base) {
    int lid = blockIdx.x * blockDim.x + threadIdx.x;
    if (lid < n) out[lid] = run_instance((uint32_t)(base + lid), cycles);
}
#define CK(x) do { cudaError_t e_ = (x); if (e_ != cudaSuccess) { fprintf(stderr, "CUDA: %s @%d\n", cudaGetErrorString(e_), __LINE__); printf("FARM-ERROR design=%s CUDA=\"%s\" state_t=%zu inputs_t=%zu outputs_t=%zu\n", STIM_NAME, cudaGetErrorString(e_), sizeof(state_t), sizeof(inputs_t), sizeof(outputs_t)); exit(3); } } while (0)
#endif
static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + 1e-9 * t.tv_nsec; }
int main(int argc, char **argv) {
    int cycles = argc > 1 ? atoi(argv[1]) : 100000;
    int n      = argc > 2 ? atoi(argv[2]) : 4096;
    int block  = argc > 3 ? atoi(argv[3]) : 128;
    int reps   = argc > 4 ? atoi(argv[4]) : 1;
    int ngpu   = argc > 5 ? atoi(argv[5]) : 1;
    uint64_t *h = (uint64_t *)calloc(n, sizeof *h);
    double best = 1e30;
#ifdef __CUDACC__
#ifdef FARM_SOA
    if (ngpu != 1) { fprintf(stderr, "SoA mode: 1 GPU only\n"); exit(3); }
    static void **soa_ptrs = NULL; static size_t *soa_len = NULL; static int soa_cnt = 0;
    { state_t hs; inputs_t hi; outputs_t ho; size_t tot = 0; soa_ptrs = (void**)calloc(65536, sizeof(void*)); soa_len = (size_t*)calloc(65536, sizeof(size_t));
      #define X(ST, T, NM, CNT) { size_t b = sizeof(T) * (size_t)(CNT) * (size_t)n; void *p; if (cudaMalloc(&p, b) != cudaSuccess) { printf("FARM-ERROR design=%s CUDA=\"cudaMalloc %zu MB for " #NM " failed\" N=%d bytes_per_instance=%zu\n", STIM_NAME, tot / (1u<<20), n, soa_bytes_per_instance); exit(3); } tot += b; soa_ptrs[soa_cnt] = p; soa_len[soa_cnt++] = b; SOA_SET(ST, NM, (T*)p); }
      #define SOA_SET(ST, NM, P) soa_set_##ST(NM, P)
      #define soa_set_state_t(NM, P) hs.NM = P
      #define soa_set_inputs_t(NM, P) hi.NM = P
      #define soa_set_outputs_t(NM, P) ho.NM = P
      SOA_MEMBERS(X)
      #undef X
      CK(cudaMemcpyToSymbol(g_state, &hs, sizeof hs)); CK(cudaMemcpyToSymbol(g_in, &hi, sizeof hi)); CK(cudaMemcpyToSymbol(g_out, &ho, sizeof ho));
      CK(cudaMemcpyToSymbol(sm_n_rt, &n, sizeof n)); int z = 0; CK(cudaMemcpyToSymbol(sm_gid_base, &z, sizeof z));
      fprintf(stderr, "SoA: %zu MB for %d instances (%zu bytes/instance)\n", tot >> 20, n, soa_bytes_per_instance); }
    #define SOA_ZERO() do { for (int i_ = 0; i_ < soa_cnt; i_++) CK(cudaMemset(soa_ptrs[i_], 0, soa_len[i_])); } while (0)
    // residency throttle: FARM_SMEM=<bytes> of dynamic shared memory per block caps blocks/SM, so the
    // driver's per-thread local-memory reservation (frame x resident threads) fits; 0 = off
    size_t smem = getenv("FARM_SMEM") ? (size_t)atol(getenv("FARM_SMEM")) : 0;
    if (smem) CK(cudaFuncSetAttribute(farm_kernel, cudaFuncAttributeMaxDynamicSharedMemorySize, (int)smem));
#else
    #define SOA_ZERO() do {} while (0)
    size_t smem = 0;
#endif
    int ndev = 0; CK(cudaGetDeviceCount(&ndev));
    if (ngpu < 1) ngpu = 1; if (ngpu > ndev) { fprintf(stderr, "only %d devices\n", ndev); exit(3); }
    uint64_t *d[64]; int cnt[64], base[64];
    for (int g = 0; g < ngpu; g++) {                 // contiguous global-id slices per device
        base[g] = (int)((long long)n * g / ngpu); cnt[g] = (int)((long long)n * (g + 1) / ngpu) - base[g];
        CK(cudaSetDevice(g)); CK(cudaMalloc(&d[g], (cnt[g] ? cnt[g] : 1) * sizeof *d[g]));
        int grid = (cnt[g] + block - 1) / block;
        SOA_ZERO();
        if (cnt[g]) farm_kernel<<<grid, block, smem>>>(d[g], 16, cnt[g], base[g]);   // warm-up / JIT
        CK(cudaGetLastError());                       // launch-configuration errors surface HERE, not at sync
    }
    for (int g = 0; g < ngpu; g++) { CK(cudaSetDevice(g)); CK(cudaDeviceSynchronize()); }
    for (int r = 0; r < reps; r++) {
        SOA_ZERO(); CK(cudaDeviceSynchronize());
        double t0 = now();
        for (int g = 0; g < ngpu; g++) {             // launches are async: all cards run concurrently
            CK(cudaSetDevice(g)); int grid = (cnt[g] + block - 1) / block;
            if (cnt[g]) farm_kernel<<<grid, block, smem>>>(d[g], cycles, cnt[g], base[g]);
            CK(cudaGetLastError());
        }
        for (int g = 0; g < ngpu; g++) { CK(cudaSetDevice(g)); CK(cudaDeviceSynchronize()); }
        double t = now() - t0; if (t < best) best = t;
    }
    for (int g = 0; g < ngpu; g++) { CK(cudaSetDevice(g)); if (cnt[g]) CK(cudaMemcpy(h + base[g], d[g], cnt[g] * sizeof *h, cudaMemcpyDeviceToHost)); }
    cudaDeviceProp p; CK(cudaGetDeviceProperties(&p, 0));
    static char devname[256]; snprintf(devname, sizeof devname, "%dx %s", ngpu, p.name);
    const char *dev = devname;
#else
    const char *dev = "cpu";
#ifdef FARM_SOA
    sm_n_rt = n;
    #define X(ST, T, NM, CNT) { size_t b = sizeof(T) * (size_t)(CNT) * (size_t)n; SOA_SET(ST, NM, (T*)calloc(1, b)); }
    #define SOA_SET(ST, NM, P) soa_set_##ST(NM, P)
    #define soa_set_state_t(NM, P) g_state.NM = P
    #define soa_set_inputs_t(NM, P) g_in.NM = P
    #define soa_set_outputs_t(NM, P) g_out.NM = P
    SOA_MEMBERS(X)
    #undef X
#endif
    for (int r = 0; r < reps; r++) {
#ifdef FARM_SOA
        #define X(ST, T, NM, CNT) memset(SOA_GET(ST, NM), 0, sizeof(T) * (size_t)(CNT) * (size_t)n);
        #define SOA_GET(ST, NM) soa_get_##ST(NM)
        #define soa_get_state_t(NM) g_state.NM
        #define soa_get_inputs_t(NM) g_in.NM
        #define soa_get_outputs_t(NM) g_out.NM
        SOA_MEMBERS(X)
        #undef X
#endif
        double t0 = now();
        #pragma omp parallel for schedule(dynamic, 16)
        for (int g = 0; g < n; g++) {
#ifdef FARM_SOA
            sm_gid_cpu = g;
#endif
            h[g] = run_instance((uint32_t)g, cycles); }
        double t = now() - t0; if (t < best) best = t;
    }
#endif
    uint64_t agg = 0xcbf29ce484222325ull;                    // FNV-1a over the CHK vector
    for (int g = 0; g < n; g++) for (int b = 0; b < 8; b++) { agg ^= (h[g] >> (8 * b)) & 0xff; agg *= 0x100000001b3ull; }
    double ic = (double)cycles * n;
    printf("FARM design=%s dev=\"%s\" N=%d cycles=%d block=%d secs=%.4f agg_inst_cyc_per_s=%.4e per_inst_cyc_per_s=%.4e CHK0=%016llX AGG=%016llX\n",
           STIM_NAME, dev, n, cycles, block, best, ic / best, (double)cycles / best,
           (unsigned long long)h[0], (unsigned long long)agg);
    return 0;
}
