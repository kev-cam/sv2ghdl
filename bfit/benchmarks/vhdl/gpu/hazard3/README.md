# Hazard3 Mandelbrot on the GPU farm

Verijit's test case ([verilator-hazard3-mandelbrot-testbench](https://github.com/verijit/verilator-hazard3-mandelbrot-testbench),
Apache-2.0: Luke Wren's Hazard3 RV32IMAC core with two AHB-Lite SRAMs rendering a fixed-point
Mandelbrot image) through this kit: the SoC becomes a gen_statemachine model and every GPU
thread runs one complete CPU instance (`h3farm.cu`, a sibling of `../farm.cu`). Two workloads:

| workload | what every instance does | instances | cycles per instance |
| :-- | :-- | :-- | :-- |
| `thr` (throughput) | renders tests/hazard3_mandelbrot's `mandel16_i8` (16x16, MAX_ITERS 8; the unchanged `mandelbrot.c`) and streams it to TOHOST | any N (sweep 1 .. 1M) | 95,038 |
| `split` (split render) | renders ONE TILE of upstream's own workload (1024x1024, MAX_ITERS 256, same pixel formula, same colours), chosen by its instance id | N = NT = 2^(20-m) tiles of 2^m pixels: m = 0, 2, 4 gives 1,048,576 / 262,144 / 65,536 | 223 .. 11,866 (m = 0) |

**Status 2026-10-03: built and certified on CPU here, and independently rebuilt and re-certified
in review; GPU binaries built here. The same source has run on a real GPU: on this box's T1000
(an sm_75 build of the same `h3farm.cu` and models) `sweep_h3.sh` + `check_h3.py` certified
`thr` and the WHOLE image at m = 0, 2 and 4, `local_gpu_h3.sh` certified every m = 0 tile
against the CPU table, and the measured rate is the anchor of the projection. Nothing has run
on a rented card** (no rental in this workflow): every rented-card number below is a PROJECTION
and is labelled as one. `vast_h3.sh` takes the actual measurement.

## Licence boundary

Files in this directory are part of bfit (PolyForm Noncommercial 1.0.0, `bfit/LICENSE`).
Nothing of upstream or of Hazard3 is stored here: `gen_farm_soc.py` and `gen_tile_fw.py` edit
the FETCHED `soc.tmpl.v` and `sw/mandelbrot.c` at build time (every edit matched exactly once
against the pinned commits in `tests/hazard3_mandelbrot/UPSTREAM`), and their outputs, which are
Apache-2.0 derivatives, live only in the work directory. tests/hazard3_mandelbrot (Apache-2.0
`elf2hex.py`, `fw/rv32_tohost.c`, the committed `mandel16_i8` golden) is referenced by path,
never copied. sv2ghdl/vamos code (GPL-3.0-or-later) may only RUN this kit as a subprocess
(e.g. `vast_h3.sh`), never import or copy it.

## The farm SoC (`gen_farm_soc.py`)

Upstream's `soc.tmpl.v` with: a 32-bit `tile_id` INPUT that the firmware reads at TILE_ID
`0x1004` (a read in its AHB address phase makes the data phase return `tile_id` instead of the
RAM word); the TOHOST snooper of tests/hazard3_mandelbrot (store to `0x1000` -> `tohost_valid`
/ `tohost_data` one cycle after the data phase, so cycle counts compare 1:1 with its
`ref_cycles`); the hierarchical `finished` removed; RAM depths cut to what each workload needs
(instruction RAM 128 words; data RAM 512 words for `thr`, whose 16x16 image lives on the stack,
and 16 words for `split`, two 16-byte frames), the preload FOLDED modulo the depth because the
RAMs decode only the low address bits; and upstream's own `RESET_REGFILE = 0` kept (the gsm model
and the Verilator twin both start every register at 0, which upstream relies on for sp = 0;
`--regfile-reset 1` gives the 4-state-safe form: same cycles, 1,409 instead of 1,253 cells).

## Firmware (`build_h3.sh`; clang-19 / ld.lld-19, upstream `sw/Makefile` flags, linked at 0)

* `thr`: the UNCHANGED `sw/mandelbrot.c` with tests/hazard3_mandelbrot's `fw/rv32_tohost.c`,
  `-DSIZE_LOG2=4 -DMAX_ITERS=8`. build_h3.sh checks the image is byte-identical to the committed
  `mandel16_i8` variant, so that variant's golden (native gcc) and its `ref_cycles` of 95,038
  (Verilator and Icarus agree on it) apply.
* `split`: `gen_tile_fw.py` rewrites only the LOOPS of the fetched `mandelbrot.c`
  (`mandelbrot(image)` -> `mandelbrot_tile(tile)` looping over the tile's pixels; the two
  `image[y][x] = v` stores -> `tile_put(v)`; `main()` removed). The fixed-point math, the
  pixel-to-c formula, the escape loop and the colour function are untouched text (review:
  `diff` of the generated file against upstream shows only those hunks). Upstream's own
  `sw/rv32.c` (unchanged) supplies `_start`, the types and `memset`; `fw/tile_rt.h` supplies
  `main()`: read the tile word, stream `tile word, pixels, 0xffffffff` to TOHOST. Tile geometry
  (`fw/tile_geom.h`): tile word = `idx | m << 24`, pixel k of tile idx is row-major pixel
  `idx + k * NT`, so ONE firmware image serves every N; neighbouring tiles (one warp) render
  neighbouring pixels. 63 words of code, 32 bytes of stack, no data sections.
* **No whole-image memset.** Upstream's `main()` zeroes the 4 MB image byte by byte before
  rendering. `memset_cycles.sh` (upstream's committed benchmark binary on the gsm model,
  `results/memset.log`) finds mandelbrot() first fetched at cycle 29,360,156: 7.000 cycles/byte
  x 4,194,304 bytes = **29,360,128 cycles removed, 0.63% of upstream's 4,637,655,132**.
* Goldens: `fw/tile_native.h` compiles the SAME generated `mandelbrot_tile()` natively (gcc -O2)
  for every tile and prints the per-tile checksums and their population hash. The union of the
  native tiles, written as upstream's `output.ppm`, must have upstream's md5
  `693d2391e979a114a82af00b3e64e54c` (it does for m = 0, 2 and 4).
* Cost law (all 1,048,576 m = 0 instances, checked in review against an independent per-pixel
  iteration count): **done = 131 + 46 x escape iterations, +2 when iterations >= 64** (the
  green-channel clamp branch; 12,739 pixels), and 11,866 = 256 x 46 + 90 for the 398,796
  in-set pixels. The iteration total, 108,623,046, is upstream's own. The split render therefore
  simulates 5.12e9 instance-cycles against upstream's 4.64e9 single run (+0.48e9). At least
  0.39e9 of that is the loop: clang-19 issues `slli` straight after each `mulh` (a stall on
  Hazard3; same 24 instructions per iteration as upstream's binary, scheduled differently), while
  upstream's committed binary was built by **clang 20.1.2** (its `.comment`), which interleaves the
  multiplies. Its total bounds it at <= 42.42 cycles/iteration ((4,637,655,132 - 29,360,156) /
  108,623,046) against 46 here. At most 0.12e9 is per-instance boot and streaming, and the
  removed memset takes back 0.03e9. Every GPU comparison below is therefore conservative for the
  farm; a clang-20 build of the tile firmware would be >= 7.6% fewer instance-cycles (clang-20 is
  not installed here, and the task specifies clang-19).

## The gsm model

`gen_statemachine` (built from the current `sv2ghdl/yosys/gen_statemachine.cpp`, `GSM_U32=1`
32-bit carriers) -> `../strip_model.py` -> `mem_prep.py`. `$readmemh` survives the way VeeR's
ICCM/DCCM did: yosys turns the `initial` block into `$meminit` cells, gen_statemachine emits
them as `sm_reset` init values. `mem_prep.py` then makes the never-written instruction RAM (and
a yosys case-ROM) ONE shared `__ldg` table instead of per-instance state (any write to it would
trap under the RTL's tied-off write-port guard) and stores the 32-bit RAM words as `uint32_t`.
**1,253 comb cells, 112 registers; `state_t` = 2,640 bytes per instance for `thr` (2,048 of them
the data RAM) and 656 bytes for `split` (112 x 32-bit + 2 x 64-bit scalars, the 32-word register
file, the data RAM); array-of-structs.** No SoA is needed: ptxas keeps the scalars in registers,
and only the RAM arrays touch local memory. A fresh rebuild in another work directory gives a
byte-identical `gen_statemachine` and models identical up to the work-directory path that yosys
mangles into wire names (so the `model_m=` provenance hashes in `expect/` are path-dependent).

## Certification on CPU (`cert_h3.sh`, `results/cert_cpu.log`)

| check | result |
| :-- | :-- |
| `thr`, gsm CPU farm (one instance) | CHK0 `47E5C3CA649F77AD` = fold of the committed native golden; DONE0 95,038 = `ref_cycles` |
| `thr`, Verilator twin (`tb_h3twin.cpp`, same generated SoC) | the same CHK0 and DONE0 |
| `split` m = 0, EVERY tile on the gsm CPU farm | 1,048,576 / 1,048,576 done; AGG `E9759185332B77C1` = native; union image md5 = upstream's; 5,117,698,414 instance-cycles; tiles 223 .. 11,866 cycles |
| `split` m = 0, EVERY tile on the Verilator twin | per-tile (checksum, done cycle, word count) identical for all 1,048,576 |
| `split` m = 2 | 262,144 tiles: AGG `8594CF12954973DC` = native, md5 = upstream's, 5,071,298,926 instance-cycles, max 47,287; Verilator identical on every tile |
| `split` m = 4 | 65,536 tiles: AGG `741C1DC5AEFF29E6` = native, md5 = upstream's, 5,059,699,054 instance-cycles, max 180,366; Verilator identical on every tile |
| rented-host pipeline, dry run | `sweep_h3.sh` + `check_h3.py` on CPU builds of the same harness (`results/dryrun_sweep_cpu.log`): 6 / 6 FARM lines MATCH |
| review: independent golden | upstream's UNMODIFIED `mandelbrot.c` compiled natively (also by upstream's own `sw/native.c` route) renders md5 `693d2391…`; per-tile checksums recomputed from that image for every tile at m = 0, 2, 4: 0 mismatches against the gsm tables; 108,623,046 iterations |
| review: fresh rebuild | `build_h3.sh` + `cert_h3.sh` in a new work directory: every value above reproduced (same CHK0/AGG/CAGG/instance-cycles, Verilator identical on every tile) |

The GPU run is certified against these: `thr` CHK0/DONE0 per point, with AGG/CAGG recomputed on
the host for N identical instances; `split` AGG (native goldens), CAGG (the CPU run above, i.e.
cycle-exact for every tile) and the image md5 printed on the rented host.

## Real GPU: this box's T1000 (`local_gpu_h3.sh`; `results/local_gpu_sm_75.log`, `results/local_sweep_sm_75.log`)

The same `h3farm.cu` and models, built for sm_75 with `gpubuild/gpu-cc.sh` (not shipped: the
rented binaries carry sm_80..sm_90). Its SASS matches the shipped SASS in instruction count
(`thr` MINB 1: 5,056 on sm_75, sm_86 and sm_89, 5,048 sm_80, 5,072 sm_90) and mix, so the
measurement transfers per instruction. NVIDIA T1000: 14 SMs, 1.81-1.86 GHz under this load,
50 W, WSL2. CUDA reports a display watchdog (`kernelExecTimeoutEnabled=1`), so
`local_gpu_h3.sh` keeps every kernel under ~1 s; in practice single 60 s kernels ran without a
reset (compute preemption), which is what let `sweep_h3.sh` itself run here.

| measurement | result |
| :-- | :-- |
| the rented-host pipeline end to end: `sweep_h3.sh` with the sm_75 builds, complete renders, then `check_h3.py` on its log | 13 / 13 FARM lines certified: `thr` N = 4,096 for MINB 1/3/4 and N = 1 .. 7,168; the WHOLE image in one launch at m = 0 / 2 / 4 (AGG, CAGG, ndone and the on-host image md5 `693d2391…`) |
| `split` m = 0, EVERY tile (293 one-wave launches of 3,584, `local_gpu_h3.sh`) | per-tile table (CHK, done, words) identical to the CPU's for all 1,048,576; AGG `E9759185332B77C1`, CAGG `CF19776297554A6B`, image assembled from the GPU's words md5 `693d2391…` |
| `thr`, complete renders, N = 32, MINB 1 / 3 / 4 | CHK0 `47E5C3CA649F77AD`, DONE0 95,038, AGG `C21CBC70F53B8325`, CAGG `5D725C347D530A65` |
| `thr` plateau | 1.02-1.06e8 instance-cycles/s at 1.86-1.87 GHz (short runs, three sessions), 1.02e8 in the sweep at 1.81 GHz, flat from N = 3,584 (one wave) to 14,336 |
| per thread | 1.0e5 cycles/s for a warp alone; 2.9e4 at saturation |
| kernel variants | `thr` N = 7,168 (complete renders): MINB 1 1.02e8, MINB 3 8.7e7, MINB 4 6.3e7 inst-cyc/s: the spilling variants lose on Turing (MINB 3 wins only at N = 4,096, where MINB 1 needs a second, nearly empty wave) |
| whole 1024x1024 image, one launch | m = 0: 59.2 s, m = 2: 60.0 s, m = 4: 60.7 s kernel time (an earlier session: 58.7 / 59.3 / 59.9 s) = 21x Verilator's single run, on a 50 W card |
| rule 1 check | 1.04e8 x 1,253 cells = 1.3e11 cell-evals/s = 0.32-0.53 of this T1000's ITC rate (vhdl_perf.md): Hazard3 does NOT sit on gpu_farm.md's rule-1 constant |

## CPU rates (rule 4's comparison point; Threadripper PRO 5955WX)

| engine | workload | cycles/s |
| :-- | :-- | --: |
| gsm compiled model (this kit, g++ -O2), one core | `thr`, one instance, best of 3 | 2.50e6 |
| Verilator 5.032 twin (same generated SoC, -O3), one core | `thr`, one instance, best of 3 | 3.06e6 |
| gsm CPU farm, 8 OpenMP threads | `split` m = 0, every tile: 5.118e9 instance-cycles in 268 s | 1.91e7 (2.39e6 per thread) |
| Verilator twin, 8 processes | `split` m = 4, every tile: 5.060e9 in 237 s (m = 0: 966 s, mostly 1M model constructions) | 2.13e7 |
| Verilator 5.032, upstream's own benchmark (original SoC, clang++ -O3 -march=native), one core | 4,637,655,132 cycles in 1,240 .. 1,274 s | 3.6e6 .. 3.7e6 |
| Icarus / vamos (tests/hazard3_mandelbrot, portable variants) | | 4.1e3 / 2.1e3 |

## GPU binaries (`build_gpu_h3.sh`)

nvcc 12.4.131 in `docker.io/nvidia/cuda@sha256:da6791294b0b04d7e65d87b7451d6f2390b4d36225ab0701ee7dfec5769829f5`
under podman with `--network=none` (`gpubuild/gpu-cc.sh`); SASS sm_80 / sm_86 / sm_89 / sm_90 +
compute_90 PTX, cudart static (the binaries need only libc and the driver's `libcuda.so.1`). The
PTX is what lets a Blackwell card (sm_100/sm_120, e.g. RTX 5090) JIT the same binary; drop the
`compute_90,code=compute_90` gencode for SASS-only shipping (gpubuild's advice for IP-sensitive
binaries).

| binary | bytes | sha256 | nvcc wall |
| :-- | --: | :-- | --: |
| `bin/h3thr_gpu` | 2,202,392 | `0b8d26a0b4fb5852…` | 21 s |
| `bin/h3split_gpu` | 2,145,048 | `2f7c1d482125c0ee…` | 22 s |

ptxas per kernel variant (`results/ptxas_*.txt`; identical on sm_80/86/89 and on the local sm_75
build, sm_90 within a few bytes, and 254 registers for the `split` MINB=1 kernel on sm_90):

| kernel (`H3_MINB`) | registers | frame `thr` / `split` | spill stores / loads |
| :-- | --: | --: | --: |
| 1 (default) | 255 | 2,640 / 656 B | 0 / 0 |
| 3 | 168 | 2,896 / 912 B | ~390 / ~330 B |
| 4 | 128 | 3,192 / 1,208 B | ~825 / ~690 B |

At MINB=1 the frame IS `state_t` and nothing spills, at 8 warps per SM; MINB=3/4 trade spills
for 12/16 warps (slower on the T1000; the sweep measures it on each rented card).

## Projection (NOT a measurement; `project_h3.py`, `results/projection.txt`)

**Anchored on the T1000 measurement** (the second half of `projection.txt`): 4090 = T1000 x
(128 SMs x 2.75 GHz) / (14 SMs x 1.86 GHz) x 0.90 / 1.00 / 1.20 per SM per clock (Ada and Turing
issue and execute INT32 at the same width per SM; the ITC rule-1 designs give 1.00-1.15, an
upper-ish value because vhdl_perf.md's T1000 column was taken at N = 4,096); the 3090 and H100
SXM at their measured ITC plateau ratios to the 4090 (0.43-0.44, 0.78-0.81); the L40S at 1.00 /
1.11 (SM count) / 1.27 (Servant). Split render: processor sharing (every resident warp advances
at min(lone-warp rate, card rate / active warps); a block holds its slot until its slowest warp
ends), validated on the T1000's 293 measured launches (61-68 s predicted vs 64.6-64.9 s measured)
and calibrated on its certified single-launch images (x 1.064 / 1.093 / 1.109 for m = 0 / 2 / 4).

| card | `thr` plateau inst-cyc/s, low / central / high | x one Verilator thread (central) | `thr` images/s |
| :-- | --: | --: | --: |
| RTX 4090 | 1.27e9 / 1.41e9 / 1.69e9 | 380-402 | 14,800 |
| RTX 3090 | 5.4e8 / 6.1e8 / 7.4e8 | 165-175 | 6,400 |
| H100 SXM | 9.9e8 / 1.12e9 / 1.37e9 | 302-319 | 11,800 |
| L40S | 1.27e9 / 1.56e9 / 2.14e9 | 422-446 | 16,400 |

`split` kernel time for upstream's whole 1024x1024 image (m = 0, blocks of 32; 8 cards =
`H3_GPU_INTERLEAVE=1`); m = 2 is 3-7% slower, m = 4 13% slower on one card and 1.8x slower on
eight (its 180k-cycle tiles):

| card | 1 GPU central (range) | 8 GPUs central (range) |
| :-- | --: | --: |
| RTX 4090 | 4.40 s (3.67-4.89) | 0.75 s (0.63-0.84) |
| RTX 3090 | 10.1 s (8.3-11.4) | 1.75 s (1.45-1.97) |
| H100 SXM | 5.54 s (4.53-6.27) | 0.95 s (0.77-1.07) |
| L40S | 3.97 s (2.89-4.89) | 0.70 s (0.51-0.86) |

**Against Verilator and Verijit's claim (projection).** Upstream's run: Verilator 1,240-1,274 s on
one core here; Verijit's claimed 766.8 MCycles/s (upstream README: 213x Verilator's 3.6, for ONE
instance) would be 6.05 s (it cannot be run: no binary or source is published). One RTX 4090 renders the same image in a projected
~4.4 s (3.7-4.9): ~285x Verilator and ~1.4x Verijit's claim. Eight 4090s: ~0.75 s, ~1,650-1,700x
Verilator, ~8x Verijit's claim. In throughput the 4090 is a projected ~1.4e9 instance-cycles/s,
~390x one Verilator thread. Rule 4 still holds: one GPU thread runs ~1.5e5 cycles/s alone and
~4.3e4 at saturation, 17-58x SLOWER than one CPU core on the same model (2.5e6) and ~5,000x
slower than Verijit's single-instance claim. The farm wins only by breadth (one 4090 ~ 560 cores
of this model), never on a single run.

Rule 1 alone (the first half of `projection.txt`: K_card / 1,253 cells, b17 as the spill class)
gave 3.6e9 inst-cyc/s, 1.69 s and 0.33 s for the 4090: about 2.5x too optimistic for this design,
as the T1000 shows (Hazard3 reaches 0.32-0.53 of the ITC cell-eval rate); its spill-class bound
(9.4e8, 6.46 s, 1.28 s) does bracket the anchored values. Its constant-rate list schedule is 1.7x
too pessimistic for tail-heavy launches (lone warps run ~3x faster).

## Rental plan (`vast_h3.sh`, not run here; budget $20)

| # | node | `MAXDPH` cap | gpu_farm.md paid $/h | projected session (sweep + 4 min create/ship/destroy) | projected $ | worst case at the cap |
| --: | :-- | --: | --: | --: | --: | --: |
| 1 | RTX_4090 x1 | 0.60 | 0.36 | 7.7 min | 0.05 | 0.76 |
| 2 | RTX_3090 x1 | 0.30 | 0.11 | 12.5 min | 0.02 | 0.38 |
| 3 | L40S x1 | 1.20 | 0.80 | 7.6 min | 0.10 | 1.52 |
| 4 | H100_SXM x1 (optional: a data point, not a speed candidate) | 3.50 | 2.94 | 8.7 min | 0.43 | 4.43 |
| 5 | RTX_4090 x8 (maximum speed) | 5.00 | 3.76 (4x node 1.88) | 9.2 min | 0.57 | 6.33 |
| | **total** | | | | **1.17** | **13.43** |

Worst case = cap x 76 min: `vast_h3.sh` tears the session down at `MAX_SESSION` = 75 min after
create (`vast_run.sh` polls ~21-24 min for boot (80 x 15 s plus the API calls) and retries scp for
up to ~10 min, `RUN_TIMEOUT` = 40 min bounds the sweep) and then destroys the instance and VERIFIES it gone
(retrying for up to ~3 min if the API fails: $13.82 if every rental needed all of it). The
create-to-sweep overhead measured on the 2026-09 rentals was 1.2-3.8 min. Each sweep certifies
both binaries and picks MINB on the card (N = 65,536 and 262,144). It then sweeps `thr` over
N = 1 .. 1,048,576 (every point a complete, certified render) and renders the whole image at
m = 0, 2 and 4 with blocks of 32 and 128; node 5 also runs `thr` on 8 cards and the split render
with both gid layouts. Order: 1 first (cheapest check that the binaries, certification and log
parsing work on real hardware), 5 last (maximum speed); if no 8-card offer exists under the
cap, `NGPUS=4 MAXDPH=2.50` (gpu_farm.md paid 1.88).

The maximum-speed node is chosen from measurements, once: if rental 3 shows the L40S at
>= 1.15x the 4090 per card, node 5 becomes `NGPUS=8 MAXDPH=8.00 ./vast_h3.sh L40S` (worst
$10.13); an optional `MAXDPH=1.00 ./vast_h3.sh RTX_5090` probe (worst $1.27; Blackwell runs the
binaries' compute_90 PTX through the driver JIT, untested) can likewise make it
`NGPUS=8 MAXDPH=7.00 ./vast_h3.sh RTX_5090` (worst $8.87). Any one such 8-card node keeps the
whole plan's worst case under $20 ($18.49 for the largest combination); a second 8-card rental
would not, and needs the user's approval.

Who destroys the instance: `vast_h3.sh` itself, on every exit it can see (normal end, error,
Ctrl-C, TERM, HUP, the `MAX_SESSION` deadline), by id and by its unique `h3farm-*` label (so a
create reply it cannot parse still gets destroyed), before the local certification, and it exits
6 with a loud message if the API never confirms the instance gone. Nothing can trap SIGKILL, a
WSL shutdown or host sleep: run from a WSL terminal that stays open (or `setsid -f nohup ... &`
with that terminal still open), keep the PC awake, and after any abnormal end run
`./vast_h3.sh --reap` and `vastai show instances`. Loading only $20 of credit onto the vast.ai
account makes the budget a hard cap even then.

```sh
# once: the CLI in a venv (Ubuntu 26.04 refuses 'pip install --user': PEP 668); this box has ~/vastai-venv
python3 -m venv ~/vastai-venv && ~/vastai-venv/bin/pip install vastai   # vast_h3.sh finds ~/vastai-venv/bin/vastai
read -rs VAST_API_KEY && export VAST_API_KEY      # prompt: not echoed, not in shell history, never on a command line
cd /usr/local/src/sv2ghdl/bfit/benchmarks/vhdl/gpu/hazard3
MAXDPH=0.60 ./vast_h3.sh RTX_4090
MAXDPH=0.30 ./vast_h3.sh RTX_3090
MAXDPH=1.20 ./vast_h3.sh L40S
MAXDPH=3.50 ./vast_h3.sh H100_SXM                 # optional
NGPUS=8 MAXDPH=5.00 ./vast_h3.sh RTX_4090         # or the L40S / RTX_5090 node chosen above
./vast_h3.sh --reap; ~/vastai-venv/bin/vastai show instances     # nothing may be left
python3 check_h3.py                               # re-certify every results/vast_h3_*.log from the logs alone
```

Never `VAST_API_KEY=... ./vast_h3.sh` (shell history) and never wrap the key in
`wsl bash -lc "..."` (it lands in argv); `vastai set api-key KEY` also puts it on argv.

## Reproduce

```sh
cd bfit/benchmarks/vhdl/gpu/hazard3          # Linux/WSL; upstream checkout: tests/hazard3_mandelbrot/setup.sh
./build_h3.sh                                 # firmware, goldens, farm SoC, gsm model, CPU farm, Verilator twin
./cert_h3.sh                                  # CPU certification, every tile (results/cert_cpu.log, ~45 min on 8 cores)
./memset_cycles.sh                            # the memset the split render removes
./build_gpu_h3.sh                             # fat binaries in the pinned podman CUDA image
LOCAL_SWEEP=1 ./local_gpu_h3.sh sm_75         # real-GPU certification, the projection's anchor, sweep_h3.sh end to end (local card)
python3 project_h3.py --tiles $W/split/gsm_m0.txt,$W/split/gsm_m2.txt,$W/split/gsm_m4.txt   # W=~/gf_hz3/work
```

## Files

| file | what |
| :-- | :-- |
| `gen_farm_soc.py` | the farm SoC from upstream `soc.tmpl.v` (tile_id input, TOHOST, RAM depths, folded preloads) |
| `gen_tile_fw.py`, `fw/tile_geom.h`, `fw/tile_rt.h`, `fw/tile_native.h` | split-render firmware (loops of the fetched `mandelbrot.c` restructured), its runtime, its native golden |
| `build_h3.sh` | firmware, goldens, SoC, gsm model, CPU farm, Verilator twin; writes `expect/` |
| `mem_prep.py` | gsm model: shared ROM for never-written memories, 32-bit RAM words |
| `h3farm.cu` | the farm harness, CPU and GPU from one source (kernel variants `__launch_bounds__(128, 1/3/4)`, interleaved multi-GPU layout, image assembly) |
| `tb_h3twin.cpp` | Verilator twin of `h3farm.cu` on the same generated SoC |
| `h3chk.py` | the farm checksum of a golden TOHOST file |
| `cert_h3.sh`, `memset_cycles.sh` | CPU certification; memset measurement |
| `build_gpu_h3.sh` | GPU fat binaries through `gpubuild/gpu-cc.sh` |
| `local_gpu_h3.sh` | the same harness on a local GPU within a display watchdog: real-GPU certification and the projection's anchor |
| `sweep_h3.sh`, `vast_h3.sh`, `check_h3.py` | on-instance sweep (also logs the SM clock); rental driver (destroys and verifies; `--reap`); host-side certification from logs |
| `project_h3.py` | the projection above (T1000-anchored + processor sharing; rules 1-3 alone for comparison) |
| `expect/`, `results/`, `bin/` | expected values; local logs, ptxas reports, projection; binaries + SHA256SUMS (binaries are git-ignored, `results/vast_h3_*.log` are not) |
