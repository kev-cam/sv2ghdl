// Hazard3 Mandelbrot instance farm: farm.cu's harness (same CLI, same FARM
// line, same contiguous multi-GPU split) specialised for a CPU SoC that talks
// through TOHOST instead of an LFSR testbench.  Part of the bfit GPU-farm kit
// (PolyForm Noncommercial 1.0.0, bfit/LICENSE).
//
// Per instance gid: sm_reset; the model's tile_id INPUT = gid | m << 24 (the
// firmware reads it at TILE_ID 0x1004); then per cycle c = 1..cycles: if the SoC's
// tohost_valid register is set, fold tohost_data into the instance checksum
// (FNV-1a-64 over 32-bit words), and stop at the DONE word 0xffffffff
// (done = c: the same count tests/hazard3_mandelbrot/tb/tb_hazard3.v prints);
// then one clock edge.  tohost_valid/tohost_data are plain registers of the
// SoC, so they are read from the state instead of running the output cone
// (sm_comb) every cycle; -DH3_USE_COMB reads them from sm_comb's outputs
// instead (cross-check: same CHK/DONE).
//   CHK  = FNV-1a-64 over the instance's TOHOST words (host recomputes it from
//          a golden TOHOST stream: certification needs no GPU)
//   AGG  = FNV-1a-64 over the CHK vector bytes in gid order (as farm.cu)
//   CAGG = FNV-1a-64 over the done-cycle vector (cycle-exact population hash)
// Same source builds for CPU (g++ -x c++ -DFARM_CPU [-fopenmp]) and GPU (nvcc).
//   h3farm <cycles> <N> [block] [reps] [ngpu]
//   env H3_TILE_LOG2=<m> split render: the tile word is gid | m << 24 (2^m
//                        pixels per tile, fw/tile_geom.h); default 0.  The
//                        throughput firmware ignores tile_id.
//   env H3_GID0=<g>      first instance id (default 0): run tiles g..g+N-1
//   env H3_DUMP=<file>   write every instance's TOHOST words (H3_WMAX per
//                        instance, uint32 LE, gid order) for image assembly
//   env H3_WMAX=<w>      words kept per instance for H3_DUMP (default 64)
//   env H3_PPM=<file>    split render: check every instance's stream is
//                        (tile word, 2^m pixels, DONE), place the pixels by
//                        fw/tile_geom.h, require every pixel exactly once,
//                        and write the image as upstream's output.ppm (P6);
//                        md5sum of it must be 693d2391e979a114a82af00b3e64e54c
//                        when N = NT (all tiles).  H3_SIZE_LOG2 default 10.
//   env FARM_SMEM=<b>    dynamic shared memory per block (residency throttle)
//   env H3_MINB=<1|3|4>  GPU kernel variant (__launch_bounds__(128, MINB), below)
//   env H3_GPU_INTERLEAVE=1  multi-GPU: device g runs gids g, g+ngpu, ... instead
//                        of a contiguous slice (balances the split render)
// CPU-only diagnostic build -DH3_PCTRACE: also runs sm_comb and reports the
// first cycle at which the fetch address (keep_alive = i_haddr) equals each
// address in env H3_PCS (comma-separated hex), e.g. memset entry/return.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#ifdef __CUDACC__
#define SM_DEVICE __device__
#define SM_ROM_ATTR __device__
#define SM_ROM_LD(a, i) __ldg(&(a)[i])
#define H3_TRAP() __trap()
#else
#define SM_DEVICE
#define SM_ROM_ATTR
#define SM_ROM_LD(a, i) ((a)[i])
#define H3_TRAP() abort()
#endif
#define SM_NO_MAIN 1
#include MODEL_C
#ifndef H3_NAME
#define H3_NAME "hazard3"
#endif
#define H3_DONE_WORD 0xffffffffu
#define FNV64_OFF 0xcbf29ce484222325ull
#define FNV64_PRIME 0x100000001b3ull

struct h3_res { uint64_t chk; uint32_t done, cyc, nw, pad; };
#ifdef H3_PCTRACE
#ifdef __CUDACC__
#error "H3_PCTRACE is a CPU diagnostic"
#endif
#define H3_USE_COMB 1
static uint64_t pc_addr[16]; static long pc_first[16]; static int pc_n;
#endif

SM_DEVICE static inline void run_instance(uint32_t gid, int cycles, h3_res *r, uint32_t *words, int wmax, uint32_t tilehi) {
    state_t s; inputs_t in;
    memset(&in, 0, sizeof in);
    sm_reset(&s);
    in._tile_id = gid | tilehi;
#ifdef H3_USE_COMB
    outputs_t o; memset(&o, 0, sizeof o);
#endif
    uint64_t chk = FNV64_OFF; uint32_t nw = 0, done = 0; int c;
    for (c = 1; c <= cycles; c++) {
#ifdef H3_USE_COMB
        sm_comb(&s, &in, &o);
        const uint64_t v = o._tohost_valid, d = o._tohost_data;
#ifdef H3_PCTRACE
        for (int i = 0; i < pc_n; i++) if (pc_first[i] < 0 && o._keep_alive == pc_addr[i]) pc_first[i] = c;
#endif
#else
        const uint64_t v = s._tohost_valid, d = s._tohost_data;
#endif
        if (v) {
            const uint32_t w = (uint32_t)d;
            chk = (chk ^ w) * FNV64_PRIME;
            if (words && (int)nw < wmax) words[nw] = w;
            nw++;
            if (w == H3_DONE_WORD) { done = (uint32_t)c; break; }
        }
        sm_clock(&s, &in);
    }
    r->chk = chk; r->done = done; r->cyc = done ? done : (uint32_t)cycles; r->nw = nw; r->pad = 0;
}

#ifdef __CUDACC__
// Register/occupancy variants in ONE binary (env H3_MINB, default 1): the
// kernel is instantiated with __launch_bounds__(128, MINB), i.e. at least MINB
// blocks of 128 threads resident per SM, which caps registers at 64K/(128*MINB)
// (1: 255 regs = 8 warps/SM; 3: 168 = 12 warps; 4: 128 = 16 warps).  Rule 2 of
// gpu_farm.md says spills hurt; the rented sweep measures which way it goes.
// Blocks are therefore at most 128 threads.
template <int MINB> __global__ void __launch_bounds__(128, MINB)
h3_kernel(h3_res *out, uint32_t *words, int wmax, int cycles, int n, uint32_t base, uint32_t stride, uint32_t tilehi) {
    int lid = blockIdx.x * blockDim.x + threadIdx.x;
    if (lid < n) run_instance(base + (uint32_t)lid * stride, cycles, &out[lid], words ? words + (size_t)lid * wmax : 0, wmax, tilehi);
}
static void h3_launch(int minb, int grid, int block, size_t smem, h3_res *out, uint32_t *words, int wmax, int cycles, int n, uint32_t base, uint32_t stride, uint32_t tilehi) {
    switch (minb) {
    case 3:  h3_kernel<3><<<grid, block, smem>>>(out, words, wmax, cycles, n, base, stride, tilehi); break;
    case 4:  h3_kernel<4><<<grid, block, smem>>>(out, words, wmax, cycles, n, base, stride, tilehi); break;
    default: h3_kernel<1><<<grid, block, smem>>>(out, words, wmax, cycles, n, base, stride, tilehi); break;
    }
}
static void h3_smem_attr(size_t smem) {
    if (!smem) return;
    cudaFuncSetAttribute(h3_kernel<1>, cudaFuncAttributeMaxDynamicSharedMemorySize, (int)smem);
    cudaFuncSetAttribute(h3_kernel<3>, cudaFuncAttributeMaxDynamicSharedMemorySize, (int)smem);
    cudaFuncSetAttribute(h3_kernel<4>, cudaFuncAttributeMaxDynamicSharedMemorySize, (int)smem);
}
#define CK(x) do { cudaError_t e_ = (x); if (e_ != cudaSuccess) { fprintf(stderr, "CUDA: %s @%d\n", cudaGetErrorString(e_), __LINE__); printf("FARM-ERROR design=%s CUDA=\"%s\" state_t=%zu inputs_t=%zu\n", H3_NAME, cudaGetErrorString(e_), sizeof(state_t), sizeof(inputs_t)); exit(3); } } while (0)
#endif
static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + 1e-9 * t.tv_nsec; }
static uint64_t fnv_bytes(uint64_t h, uint64_t v) { for (int b = 0; b < 8; b++) { h ^= (v >> (8 * b)) & 0xff; h *= FNV64_PRIME; } return h; }

int main(int argc, char **argv) {
    int cycles = argc > 1 ? atoi(argv[1]) : 100000;
    int n      = argc > 2 ? atoi(argv[2]) : 4096;
    int block  = argc > 3 ? atoi(argv[3]) : 128;
    int reps   = argc > 4 ? atoi(argv[4]) : 1;
    int ngpu   = argc > 5 ? atoi(argv[5]) : 1;
    uint32_t gid0 = getenv("H3_GID0") ? (uint32_t)strtoul(getenv("H3_GID0"), 0, 0) : 0;
    uint32_t tm = getenv("H3_TILE_LOG2") ? (uint32_t)atoi(getenv("H3_TILE_LOG2")) : 0, tilehi = tm << 24;
    const char *dump = getenv("H3_DUMP"), *ppm = getenv("H3_PPM");
#ifdef H3_PCTRACE
    if (getenv("H3_PCS")) { char *e, *q = getenv("H3_PCS"); while (*q && pc_n < 16) { pc_addr[pc_n] = strtoull(q, &e, 16); pc_first[pc_n++] = -1; q = *e ? e + 1 : e; } }
#endif
    int wmax = getenv("H3_WMAX") ? atoi(getenv("H3_WMAX")) : ppm ? (1 << tm) + 2 : 64;
    const int slog2 = getenv("H3_SIZE_LOG2") ? atoi(getenv("H3_SIZE_LOG2")) : 10;
    h3_res *h = (h3_res *)calloc(n, sizeof *h);
    uint32_t *hw = (dump || ppm) ? (uint32_t *)calloc((size_t)n * wmax, sizeof *hw) : 0;
    double best = 1e30;
#ifdef __CUDACC__
    size_t smem = getenv("FARM_SMEM") ? (size_t)atol(getenv("FARM_SMEM")) : 0;
    const int minb = getenv("H3_MINB") ? atoi(getenv("H3_MINB")) : 1;
    if (block > 128) { fprintf(stderr, "h3farm: block <= 128 (launch bounds)\n"); exit(3); }
    int ndev = 0; CK(cudaGetDeviceCount(&ndev));
    if (ngpu < 1) ngpu = 1;
    if (ngpu > ndev) { fprintf(stderr, "only %d devices\n", ndev); exit(3); }
    // instance -> device: contiguous global-id slices (farm.cu's split, default) or, with
    // H3_GPU_INTERLEAVE=1, gid = g + k*ngpu on device g (every card sees every part of the
    // image: balanced split render).  Results are gathered back into gid order either way,
    // so CHK0/AGG/CAGG/the image do not depend on the layout.
    const int ilv = ngpu > 1 && getenv("H3_GPU_INTERLEAVE") && atoi(getenv("H3_GPU_INTERLEAVE"));
    h3_res *d[64]; uint32_t *dw[64]; int cnt[64], base[64]; const uint32_t stride = ilv ? (uint32_t)ngpu : 1u;
    for (int g = 0; g < ngpu; g++) {
        if (ilv) { base[g] = g; cnt[g] = (n - g + ngpu - 1) / ngpu; }
        else { base[g] = (int)((long long)n * g / ngpu); cnt[g] = (int)((long long)n * (g + 1) / ngpu) - base[g]; }
        CK(cudaSetDevice(g)); CK(cudaMalloc(&d[g], (cnt[g] ? cnt[g] : 1) * sizeof *d[g]));
        h3_smem_attr(smem);
        dw[g] = 0;
        if (hw) CK(cudaMalloc(&dw[g], (size_t)(cnt[g] ? cnt[g] : 1) * wmax * sizeof(uint32_t)));
        int grid = (cnt[g] + block - 1) / block;
        if (cnt[g]) h3_launch(minb, grid, block, smem, d[g], dw[g], wmax, 16, cnt[g], gid0 + base[g], stride, tilehi);   // warm-up
        CK(cudaGetLastError());
    }
    for (int g = 0; g < ngpu; g++) { CK(cudaSetDevice(g)); CK(cudaDeviceSynchronize()); }
    for (int r = 0; r < reps; r++) {
        double t0 = now();
        for (int g = 0; g < ngpu; g++) {             // launches are async: all cards run concurrently
            CK(cudaSetDevice(g)); int grid = (cnt[g] + block - 1) / block;
            if (cnt[g]) h3_launch(minb, grid, block, smem, d[g], dw[g], wmax, cycles, cnt[g], gid0 + base[g], stride, tilehi);
            CK(cudaGetLastError());
        }
        for (int g = 0; g < ngpu; g++) { CK(cudaSetDevice(g)); CK(cudaDeviceSynchronize()); }
        double t = now() - t0; if (t < best) best = t;
    }
    for (int g = 0; g < ngpu; g++) {
        CK(cudaSetDevice(g));
        if (!cnt[g]) continue;
        if (!ilv) {
            CK(cudaMemcpy(h + base[g], d[g], cnt[g] * sizeof *h, cudaMemcpyDeviceToHost));
            if (hw) CK(cudaMemcpy(hw + (size_t)base[g] * wmax, dw[g], (size_t)cnt[g] * wmax * sizeof *hw, cudaMemcpyDeviceToHost));
        } else {                                      // gather device g's lids back to gids g + k*ngpu
            h3_res *t = (h3_res *)malloc(cnt[g] * sizeof *t);
            CK(cudaMemcpy(t, d[g], cnt[g] * sizeof *t, cudaMemcpyDeviceToHost));
            for (int k = 0; k < cnt[g]; k++) h[(size_t)k * ngpu + g] = t[k];
            free(t);
            if (hw) {
                uint32_t *tw = (uint32_t *)malloc((size_t)cnt[g] * wmax * sizeof *tw);
                CK(cudaMemcpy(tw, dw[g], (size_t)cnt[g] * wmax * sizeof *tw, cudaMemcpyDeviceToHost));
                for (int k = 0; k < cnt[g]; k++) memcpy(hw + ((size_t)k * ngpu + g) * wmax, tw + (size_t)k * wmax, wmax * sizeof *tw);
                free(tw);
            }
        }
    }
    cudaDeviceProp p; CK(cudaGetDeviceProperties(&p, 0));
    static char devname[256]; snprintf(devname, sizeof devname, "%dx %s", ngpu, p.name);
    const char *dev = devname;
#else
    const char *dev = "cpu"; const int minb = 0, ilv = 0;
    for (int r = 0; r < reps; r++) {
        double t0 = now();
        #pragma omp parallel for schedule(dynamic, 16)
        for (int g = 0; g < n; g++)
            run_instance(gid0 + (uint32_t)g, cycles, &h[g], hw ? hw + (size_t)g * wmax : 0, wmax, tilehi);
        double t = now() - t0; if (t < best) best = t;
    }
#ifdef H3_PCTRACE
    for (int i = 0; i < pc_n; i++) printf("PCHIT addr=0x%llx first_cycle=%ld\n", (unsigned long long)pc_addr[i], pc_first[i]);
#endif
#endif
    uint64_t agg = FNV64_OFF, cagg = FNV64_OFF; double ic = 0; uint32_t dmin = 0xffffffffu, dmax = 0; int ndone = 0, imax = 0;
    for (int g = 0; g < n; g++) {
        agg = fnv_bytes(agg, h[g].chk); cagg = fnv_bytes(cagg, h[g].done);
        ic += h[g].cyc;
        if (h[g].done) { ndone++; if (h[g].done < dmin) dmin = h[g].done; if (h[g].done > dmax) { dmax = h[g].done; imax = g; } }
    }
    if (!ndone) dmin = 0;
    printf("FARM design=%s dev=\"%s\" N=%d cycles=%d block=%d secs=%.4f agg_inst_cyc_per_s=%.4e per_inst_cyc_per_s=%.4e CHK0=%016llX AGG=%016llX"
           " DONE0=%u CAGG=%016llX ndone=%d done_min=%u done_max=%u argmax=%u inst_cyc=%.0f gid0=%u m=%u minb=%d ilv=%d\n",
           H3_NAME, dev, n, cycles, block, best, ic / best, ic / n / best,
           (unsigned long long)h[0].chk, (unsigned long long)agg,
           h[0].done, (unsigned long long)cagg, ndone, dmin, dmax, gid0 + (uint32_t)imax, ic, gid0, tm, minb, ilv);
    if (getenv("H3_PERINST")) {                       // per-instance table: gid chk done words
        FILE *f = fopen(getenv("H3_PERINST"), "w");
        if (f) { for (int g = 0; g < n; g++) fprintf(f, "%u %016llX %u %u\n", gid0 + (uint32_t)g, (unsigned long long)h[g].chk, h[g].done, h[g].nw); fclose(f); }
    }
    if (dump) {
        FILE *f = fopen(dump, "wb");
        if (!f || fwrite(hw, sizeof *hw, (size_t)n * wmax, f) != (size_t)n * wmax) { fprintf(stderr, "H3_DUMP write failed\n"); exit(4); }
        fclose(f);
    }
    if (ppm) {                                        // split render: assemble the union of the tiles
        const size_t S = (size_t)1 << slog2, k = (size_t)1 << tm, ntl = (size_t)2 * slog2 - tm;
        uint32_t *img = (uint32_t *)calloc(S * S, sizeof *img); unsigned char *seen = (unsigned char *)calloc(S * S, 1);
        long bad = 0, multi = 0, covered = 0;
        for (int g = 0; g < n; g++) {
            const uint32_t *w = hw + (size_t)g * wmax, tw = (gid0 + (uint32_t)g) | tilehi;
            if (h[g].nw != k + 2 || w[0] != tw || w[k + 1] != H3_DONE_WORD) { bad++; continue; }
            for (size_t j = 0; j < k; j++) {
                const size_t p = (size_t)(tw & 0xffffffu) + (j << ntl);
                if (p >= S * S || w[1 + j] >> 24) { bad++; break; }
                img[p] = w[1 + j]; if (seen[p]++) multi++; else covered++;
            }
        }
        FILE *f = fopen(ppm, "wb");
        if (!f) { fprintf(stderr, "H3_PPM open failed\n"); exit(4); }
        fprintf(f, "P6\n%zu %zu\n255\n", S, S);
        for (size_t p = 0; p < S * S; p++) { fputc((img[p] >> 16) & 0xff, f); fputc((img[p] >> 8) & 0xff, f); fputc(img[p] & 0xff, f); }
        fclose(f);
        printf("IMAGE file=%s size=%zu covered=%ld of %zu multi=%ld bad_instances=%ld complete=%d\n",
               ppm, S, covered, S * S, multi, bad, (int)(covered == (long)(S * S) && !multi && !bad));
    }
    return 0;
}
