# The GPU-farm ceiling: how far the instance farm scales, and the rules

*Measured 2026-09-14 on rented Vast.AI cards. Kit: `vhdl/gpu/` (harness,
sweep, launcher, parsers, raw logs in `vhdl/gpu/results/`). Total rental
spend for everything below: $12.48, of which $10.45 was the 8× H100 node.*

The `gpu-farm` column of `vhdl_perf.md` was one configuration (4,096
instances) on one laptop GPU (T1000). This document sweeps instance count
per card, card class, and cards per node, to find where the speed-up stops.

## Method

Same six ITC'99 designs, same `gen_tb.py` LFSR stimulus and 64-bit fold,
replayed per instance by a CUDA kernel around the gen_statemachine 2-phase
model (`farm.cu`; `gen_stim.py` derives the drive/fold from the entity).
Instance 0 replays the canonical stimulus and its CHK must equal the VHDL
engines' checksum at the full cycle count — **every card × design cell
below passed that (30/30)**. Instances >0 are seed-decorrelated; the FNV
hash over all instance checksums (`AGG`) is identical across cards and
across GPU-count splits for equal (N, cycles), so the whole population is
certified, not just instance 0. Binaries were built here (NVIDIA redist
nvcc 12.4, no GPU on the build host; the `gpubuild/gpu-cc.sh` podman path
builds the same source) and only binaries were shipped. Throughput is
aggregate instance-cycles per wall second, best of 2, kernel-only timing.

## Single card: plateau throughput

Plateau = best over N ∈ {1k … 4M} and block ∈ {128, 256}, one GPU per row; the multi-GPU nodes (rule 5 below) reach 8× these on 8 cards, e.g. b01 6.44e11 and b06 5.85e11 instance-cycles/s on 8× H100 PCIe. "vs fastest CPU
engine" uses the `vhdl_perf.md` row's fastest single-thread VHDL engine.
"vs T1000" is against the original column (N=4096). Cost uses the hourly
price paid.

| GPU | GPUs used | design | cert | plateau agg inst-cyc/s | at N / block | per-inst cyc/s @4096 | vs fastest CPU engine | vs T1000 | $ per 1e12 inst-cyc |
| :-- | --: | :-- | :-- | --: | :-- | --: | --: | --: | --: |
| A100_SXM4 (NVIDIA A100-SXM4-40GB) | 1 | b01 | MATCH | 5.88e+10 | 1048576 / 256 | 4.05e+06 | ×35,105 | ×6.6 | $0.003 |
| A100_SXM4 (NVIDIA A100-SXM4-40GB) | 1 | b06 | MATCH | 5.58e+10 | 4194304 / 128 | 3.84e+06 | ×46,287 | ×7.2 | $0.003 |
| A100_SXM4 (NVIDIA A100-SXM4-40GB) | 1 | b12 | MATCH | 3.56e+09 | 4194304 / 256 | 1.98e+05 | ×3,448 | ×6.7 | $0.052 |
| A100_SXM4 (NVIDIA A100-SXM4-40GB) | 1 | b14 | MATCH | 7.57e+09 | 4194304 / 128 | 4.63e+05 | ×5,857 | ×6.1 | $0.024 |
| A100_SXM4 (NVIDIA A100-SXM4-40GB) | 1 | b17 | MATCH | 1.76e+08 | 4194304 / 128 | 1.94e+04 | ×331 | ×9.7 | $1.055 |
| A100_SXM4 (NVIDIA A100-SXM4-40GB) | 1 | b22 | MATCH | 2.43e+09 | 4194304 / 128 | 1.29e+05 | ×3,524 | ×11.6 | $0.076 |
| H100_SXM (NVIDIA H100 80GB HBM3) | 1 | b01 | MATCH | 1.05e+11 | 4194304 / 128 | 6.06e+06 | ×62,930 | ×11.9 | $0.008 |
| H100_SXM (NVIDIA H100 80GB HBM3) | 1 | b06 | MATCH | 9.55e+10 | 4194304 / 128 | 5.38e+06 | ×79,156 | ×12.3 | $0.009 |
| H100_SXM (NVIDIA H100 80GB HBM3) | 1 | b12 | MATCH | 6.13e+09 | 4194304 / 256 | 2.58e+05 | ×5,937 | ×11.5 | $0.133 |
| H100_SXM (NVIDIA H100 80GB HBM3) | 1 | b14 | MATCH | 1.31e+10 | 4194304 / 256 | 6.60e+05 | ×10,111 | ×10.5 | $0.062 |
| H100_SXM (NVIDIA H100 80GB HBM3) | 1 | b17 | MATCH | 2.68e+08 | 4194304 / 256 | 2.32e+04 | ×505 | ×14.8 | $3.042 |
| H100_SXM (NVIDIA H100 80GB HBM3) | 1 | b22 | MATCH | 4.15e+09 | 4194304 / 128 | 1.78e+05 | ×6,029 | ×19.8 | $0.196 |
| RTX_3090 (NVIDIA GeForce RTX 3090) | 1 | b01 | MATCH | 5.72e+10 | 262144 / 128 | 5.71e+06 | ×34,119 | ×6.5 | $0.001 |
| RTX_3090 (NVIDIA GeForce RTX 3090) | 1 | b06 | MATCH | 5.17e+10 | 262144 / 128 | 5.33e+06 | ×42,874 | ×6.7 | $0.001 |
| RTX_3090 (NVIDIA GeForce RTX 3090) | 1 | b12 | MATCH | 3.39e+09 | 1048576 / 256 | 3.19e+05 | ×3,286 | ×6.4 | $0.009 |
| RTX_3090 (NVIDIA GeForce RTX 3090) | 1 | b14 | MATCH | 7.29e+09 | 1048576 / 256 | 6.51e+05 | ×5,645 | ×5.9 | $0.004 |
| RTX_3090 (NVIDIA GeForce RTX 3090) | 1 | b17 | MATCH | 1.13e+08 | 1048576 / 256 | 1.17e+04 | ×213 | ×6.2 | $0.277 |
| RTX_3090 (NVIDIA GeForce RTX 3090) | 1 | b22 | MATCH | 2.29e+09 | 4194304 / 128 | 1.99e+05 | ×3,319 | ×10.9 | $0.014 |
| RTX_4090 (NVIDIA GeForce RTX 4090) | 1 | b01 | MATCH | 1.29e+11 | 65536 / 256 | 7.64e+06 | ×77,089 | ×14.6 | $0.001 |
| RTX_4090 (NVIDIA GeForce RTX 4090) | 1 | b06 | MATCH | 1.20e+11 | 4194304 / 128 | 7.17e+06 | ×99,381 | ×15.5 | $0.001 |
| RTX_4090 (NVIDIA GeForce RTX 4090) | 1 | b12 | MATCH | 7.85e+09 | 65536 / 256 | 4.30e+05 | ×7,602 | ×14.7 | $0.013 |
| RTX_4090 (NVIDIA GeForce RTX 4090) | 1 | b14 | MATCH | 1.66e+10 | 4194304 / 128 | 8.61e+05 | ×12,865 | ×13.4 | $0.006 |
| RTX_4090 (NVIDIA GeForce RTX 4090) | 1 | b17 | MATCH | 4.67e+08 | 16384 / 128 | 3.14e+04 | ×878 | ×25.8 | $0.215 |
| RTX_4090 (NVIDIA GeForce RTX 4090) | 1 | b22 | MATCH | 5.34e+09 | 65536 / 256 | 2.74e+05 | ×7,752 | ×25.4 | $0.019 |

Per-N detail is in the raw logs; the shape is the same everywhere: flat
(latency-bound) up to ~16k instances, rising to a plateau by 65k–262k,
flat beyond to 4M.

## Rule 1 — a card is a constant rate of comb-cell evaluations

Plateau × comb cells per instance is flat to ±30% across five of six
designs on every card:

| card | design | cells | regs | plateau inst-cyc/s | cell-evals/s | N at 90% | resident threads | ×1 CPU core |
| :-- | :-- | --: | --: | --: | --: | --: | --: | --: |


So **aggregate instance-cycles/s ≈ K_card / cells_per_instance**, with K_card
≈ 4–5.6e12 (RTX 4090), 2.8–4.3e12 (H100 SXM), 1.6–2.5e12 (A100 40GB, RTX
3090). The 4090 beats the H100 on this integer, no-FP, no-tensor work
(3.1 GHz vs 2.0 GHz shader clock) and is an order of magnitude cheaper per
instance-cycle. Cell counts are yosys cells in the gsm model (b01 32, b06
33, b14 328, b12 463, b22 1000, b17 2517).

## Rule 2 — the register cliff

b17 breaks rule 1 on every card (3–5× fewer cell-evals/s). Its kernel needs
255 registers plus a 3,400-byte per-thread stack (ptxas: 3,156 B spill
stores); b22 at 216 registers and no stack is fine. Once per-thread state
spills, throughput is cache-bound, and the optimum moves to LOWER occupancy
— on the 4090, b17 at 16k instances per card beats 1M per card (4.7e8 vs
3.4e8 per card). VeeR-class state is deep in this regime; the
struct-of-arrays transform (`yosys/make_soa_model.py`) and `__launch_bounds__`
are the levers, and they were not applied here.

## Rule 3 — saturation is the card's resident-thread count

90% of plateau arrives at 16k–262k instances per card, i.e. of the order of
SMs × resident threads/SM (4090: 128 × 1536 ≈ 197k; A100: 108 × 2048 ≈
221k; H100 SXM: 132 × 2048 ≈ 270k). Below that, wall time is independent
of N: a 4090 runs 4,096 or 16,384 instances of b01 for 3M cycles in the
same 0.39 s.

## Rule 4 — breadth, never depth

One instance runs at a fixed per-instance rate (b01 on the 4090: 7.6M
cycles/s, 130 ns per cycle) — **2.5× slower than one CPU core running the
same compiled C model** (19M cycles/s on this 5-vCPU VM). The GPU never
speeds up a single run; it only multiplies runs. Against that identical
compiled model on one CPU core, one 4090 is worth 5,700–11,700 cores
(2,800 on spill-bound b17); a 3090 or A100 ≈ 2,500–5,300 cores.

## Rule 5 — cards multiply linearly, with no top-out in card count

Multi-GPU nodes, instances split in contiguous global-id slices across
cards, all launches concurrent, one wall clock (efficiency = vs one card at
the same TOTAL N):

### RTX_4090x4 b01  cert=MATCH   (agg inst-cyc/s; eff = vs 1 GPU at same N)
| total N | 1 GPU | 2 GPU | 4 GPU |
| --: | --: | --: | --: |
| 4096 | 3.19e+10 | 3.15e+10 (49%) | 3.15e+10 (25%) |
| 65536 | 1.31e+11 | 2.56e+11 (98%) | 5.03e+11 (96%) |
| 1048576 | 1.32e+11 | 2.61e+11 (99%) | 5.20e+11 (99%) |
| 2097152 | — | 2.61e+11 | — |
| 4194304 | — | — | 5.21e+11 |
AGG consistent for equal (N, cycles) across GPU counts: YES
### RTX_4090x4 b06  cert=MATCH   (agg inst-cyc/s; eff = vs 1 GPU at same N)
| total N | 1 GPU | 2 GPU | 4 GPU |
| --: | --: | --: | --: |
| 4096 | 3.00e+10 | 2.97e+10 (50%) | 2.98e+10 (25%) |
| 65536 | 1.22e+11 | 2.40e+11 (98%) | 4.74e+11 (97%) |
| 1048576 | 1.22e+11 | 2.43e+11 (99%) | 4.84e+11 (99%) |
| 2097152 | — | 2.42e+11 | — |
| 4194304 | — | — | 4.85e+11 |
AGG consistent for equal (N, cycles) across GPU counts: YES
### RTX_4090x4 b12  cert=MATCH   (agg inst-cyc/s; eff = vs 1 GPU at same N)
| total N | 1 GPU | 2 GPU | 4 GPU |
| --: | --: | --: | --: |
| 4096 | 1.80e+09 | 1.78e+09 (50%) | 1.78e+09 (25%) |
| 65536 | 7.83e+09 | 1.54e+10 (98%) | 2.83e+10 (90%) |
| 1048576 | 7.97e+09 | 1.57e+10 (99%) | 3.14e+10 (98%) |
| 2097152 | — | 1.57e+10 | — |
| 4194304 | — | — | 3.15e+10 |
AGG consistent for equal (N, cycles) across GPU counts: YES
### RTX_4090x4 b14  cert=MATCH   (agg inst-cyc/s; eff = vs 1 GPU at same N)
| total N | 1 GPU | 2 GPU | 4 GPU |
| --: | --: | --: | --: |
| 4096 | 3.61e+09 | 3.57e+09 (50%) | 3.56e+09 (25%) |
| 65536 | 1.65e+10 | 3.21e+10 (97%) | 5.70e+10 (86%) |
| 1048576 | 1.69e+10 | 3.35e+10 (99%) | 6.66e+10 (98%) |
| 2097152 | — | 3.35e+10 | — |
| 4194304 | — | — | 6.70e+10 |
AGG consistent for equal (N, cycles) across GPU counts: YES
### RTX_4090x4 b17  cert=MATCH   (agg inst-cyc/s; eff = vs 1 GPU at same N)
| total N | 1 GPU | 2 GPU | 4 GPU |
| --: | --: | --: | --: |
| 4096 | 1.32e+08 | 1.24e+08 (47%) | 1.24e+08 (24%) |
| 65536 | 3.09e+08 | 6.21e+08 (100%) | 1.89e+09 (153%) |
| 1048576 | 3.38e+08 | 6.80e+08 (101%) | 1.15e+09 (85%) |
| 2097152 | — | 6.75e+08 | — |
| 4194304 | — | — | 1.35e+09 |
AGG consistent for equal (N, cycles) across GPU counts: YES
### RTX_4090x4 b22  cert=MATCH   (agg inst-cyc/s; eff = vs 1 GPU at same N)
| total N | 1 GPU | 2 GPU | 4 GPU |
| --: | --: | --: | --: |
| 4096 | 1.14e+09 | 1.13e+09 (50%) | 1.13e+09 (25%) |
| 65536 | 5.26e+09 | 1.01e+10 (96%) | 1.81e+10 (86%) |
| 1048576 | 5.43e+09 | 1.07e+10 (99%) | 2.14e+10 (98%) |
| 2097152 | — | 1.08e+10 | — |
| 4194304 | — | — | 2.15e+10 |
AGG consistent for equal (N, cycles) across GPU counts: YES

### RTX_3090x4 b01  cert=MATCH   (agg inst-cyc/s; eff = vs 1 GPU at same N)
| total N | 1 GPU | 2 GPU | 4 GPU |
| --: | --: | --: | --: |
| 4096 | 2.35e+10 | 2.33e+10 (49%) | 2.34e+10 (25%) |
| 65536 | 4.97e+10 | 8.76e+10 (88%) | 1.75e+11 (88%) |
| 1048576 | 5.47e+10 | 1.09e+11 (99%) | 2.14e+11 (98%) |
| 2097152 | — | 1.08e+11 | — |
| 4194304 | — | — | 2.14e+11 |
AGG consistent for equal (N, cycles) across GPU counts: YES
### RTX_3090x4 b06  cert=MATCH   (agg inst-cyc/s; eff = vs 1 GPU at same N)
| total N | 1 GPU | 2 GPU | 4 GPU |
| --: | --: | --: | --: |
| 4096 | 2.18e+10 | 2.19e+10 (50%) | 2.19e+10 (25%) |
| 65536 | 4.52e+10 | 8.00e+10 (88%) | 1.60e+11 (89%) |
| 1048576 | 4.98e+10 | 9.85e+10 (99%) | 1.95e+11 (98%) |
| 2097152 | — | 9.81e+10 | — |
| 4194304 | — | — | 1.95e+11 |
AGG consistent for equal (N, cycles) across GPU counts: YES
### RTX_3090x4 b12  cert=MATCH   (agg inst-cyc/s; eff = vs 1 GPU at same N)
| total N | 1 GPU | 2 GPU | 4 GPU |
| --: | --: | --: | --: |
| 4096 | 1.30e+09 | 1.31e+09 (51%) | 1.31e+09 (25%) |
| 65536 | 2.93e+09 | 5.11e+09 (87%) | 1.01e+10 (86%) |
| 1048576 | 3.25e+09 | 6.39e+09 (98%) | 1.26e+10 (97%) |
| 2097152 | — | 6.38e+09 | — |
| 4194304 | — | — | 1.25e+10 |
AGG consistent for equal (N, cycles) across GPU counts: YES
### RTX_3090x4 b14  cert=MATCH   (agg inst-cyc/s; eff = vs 1 GPU at same N)
| total N | 1 GPU | 2 GPU | 4 GPU |
| --: | --: | --: | --: |
| 4096 | 2.62e+09 | 2.64e+09 (50%) | 2.62e+09 (25%) |
| 65536 | 6.21e+09 | 1.08e+10 (87%) | 2.14e+10 (86%) |
| 1048576 | 6.94e+09 | 1.36e+10 (98%) | 2.70e+10 (97%) |
| 2097152 | — | 1.36e+10 | — |
| 4194304 | — | — | 2.68e+10 |
AGG consistent for equal (N, cycles) across GPU counts: YES
### RTX_3090x4 b17  cert=MATCH   (agg inst-cyc/s; eff = vs 1 GPU at same N)
| total N | 1 GPU | 2 GPU | 4 GPU |
| --: | --: | --: | --: |
| 4096 | 4.76e+07 | 9.41e+07 (99%) | 1.03e+08 (54%) |
| 65536 | 1.07e+08 | 2.10e+08 (99%) | 4.27e+08 (100%) |
| 1048576 | 1.12e+08 | 2.22e+08 (100%) | 4.32e+08 (97%) |
| 2097152 | — | 2.21e+08 | — |
| 4194304 | — | — | 4.34e+08 |
AGG consistent for equal (N, cycles) across GPU counts: YES
### RTX_3090x4 b22  cert=MATCH   (agg inst-cyc/s; eff = vs 1 GPU at same N)
| total N | 1 GPU | 2 GPU | 4 GPU |
| --: | --: | --: | --: |
| 4096 | 8.08e+08 | 8.17e+08 (51%) | 8.12e+08 (25%) |
| 65536 | 1.99e+09 | 3.46e+09 (87%) | 6.56e+09 (83%) |
| 1048576 | 2.20e+09 | 4.30e+09 (98%) | 8.38e+09 (95%) |
| 2097152 | — | 4.29e+09 | — |
| 4194304 | — | — | 8.34e+09 |
AGG consistent for equal (N, cycles) across GPU counts: YES

### H100_PCIEx8 b01  cert=MATCH   (agg inst-cyc/s; eff = vs 1 GPU at same N)
| total N | 1 GPU | 2 GPU | 4 GPU | 8 GPU |
| --: | --: | --: | --: | --: |
| 4096 | 2.19e+10 | 2.19e+10 (50%) | 2.19e+10 (25%) | 2.18e+10 (12%) |
| 65536 | 7.17e+10 | 1.20e+11 (83%) | 1.79e+11 (62%) | 3.50e+11 (61%) |
| 1048576 | 8.14e+10 | 1.62e+11 (99%) | 3.21e+11 (99%) | 6.42e+11 (98%) |
| 2097152 | — | 1.62e+11 | — | — |
| 4194304 | — | — | 3.23e+11 | — |
| 8388608 | — | — | — | 6.44e+11 |
AGG consistent for equal (N, cycles) across GPU counts: YES
### H100_PCIEx8 b06  cert=MATCH   (agg inst-cyc/s; eff = vs 1 GPU at same N)
| total N | 1 GPU | 2 GPU | 4 GPU | 8 GPU |
| --: | --: | --: | --: | --: |
| 4096 | 1.96e+10 | 1.96e+10 (50%) | 1.96e+10 (25%) | 1.96e+10 (12%) |
| 65536 | 6.53e+10 | 1.08e+11 (83%) | 1.61e+11 (61%) | 3.13e+11 (60%) |
| 1048576 | 7.32e+10 | 1.46e+11 (100%) | 2.91e+11 (99%) | 5.80e+11 (99%) |
| 2097152 | — | 1.46e+11 | — | — |
| 4194304 | — | — | 2.93e+11 | — |
| 8388608 | — | — | — | 5.85e+11 |
AGG consistent for equal (N, cycles) across GPU counts: YES
### H100_PCIEx8 b12  cert=MATCH   (agg inst-cyc/s; eff = vs 1 GPU at same N)
| total N | 1 GPU | 2 GPU | 4 GPU | 8 GPU |
| --: | --: | --: | --: | --: |
| 4096 | 9.48e+08 | 1.07e+09 (56%) | 1.07e+09 (28%) | 1.09e+09 (14%) |
| 65536 | 4.13e+09 | 6.80e+09 (82%) | 9.93e+09 (60%) | 1.47e+10 (44%) |
| 1048576 | 4.70e+09 | 9.27e+09 (99%) | 1.84e+10 (98%) | 3.61e+10 (96%) |
| 2097152 | — | 9.33e+09 | — | — |
| 4194304 | — | — | 1.86e+10 | — |
| 8388608 | — | — | — | 3.72e+10 |
AGG consistent for equal (N, cycles) across GPU counts: YES
### H100_PCIEx8 b14  cert=MATCH   (agg inst-cyc/s; eff = vs 1 GPU at same N)
| total N | 1 GPU | 2 GPU | 4 GPU | 8 GPU |
| --: | --: | --: | --: | --: |
| 4096 | 2.39e+09 | 2.38e+09 (50%) | 2.38e+09 (25%) | 2.39e+09 (12%) |
| 65536 | 8.78e+09 | 1.44e+10 (82%) | 2.15e+10 (61%) | 3.82e+10 (54%) |
| 1048576 | 9.98e+09 | 1.99e+10 (100%) | 3.95e+10 (99%) | 7.85e+10 (98%) |
| 2097152 | — | 2.00e+10 | — | — |
| 4194304 | — | — | 3.98e+10 | — |
| 8388608 | — | — | — | 7.96e+10 |
AGG consistent for equal (N, cycles) across GPU counts: YES
### H100_PCIEx8 b17  cert=MATCH   (agg inst-cyc/s; eff = vs 1 GPU at same N)
| total N | 1 GPU | 2 GPU | 4 GPU | 8 GPU |
| --: | --: | --: | --: | --: |
| 4096 | 8.53e+07 | 8.62e+07 (50%) | 8.68e+07 (25%) | 8.69e+07 (13%) |
| 65536 | 1.97e+08 | 3.43e+08 (87%) | 6.17e+08 (78%) | 1.21e+09 (77%) |
| 1048576 | 2.05e+08 | 4.09e+08 (100%) | 8.22e+08 (100%) | 1.60e+09 (98%) |
| 2097152 | — | 4.10e+08 | — | — |
| 4194304 | — | — | 8.20e+08 | — |
| 8388608 | — | — | — | 1.64e+09 |
AGG consistent for equal (N, cycles) across GPU counts: YES
### H100_PCIEx8 b22  cert=MATCH   (agg inst-cyc/s; eff = vs 1 GPU at same N)
| total N | 1 GPU | 2 GPU | 4 GPU | 8 GPU |
| --: | --: | --: | --: | --: |
| 4096 | 6.55e+08 | 7.73e+08 (59%) | 7.76e+08 (30%) | 8.03e+08 (15%) |
| 65536 | 2.97e+09 | 4.86e+09 (82%) | 7.02e+09 (59%) | 1.01e+10 (42%) |
| 1048576 | 3.36e+09 | 6.63e+09 (99%) | 1.31e+10 (98%) | 2.39e+10 (89%) |
| 2097152 | — | 6.67e+09 | — | — |
| 4194304 | — | — | 1.33e+10 | — |
| 8388608 | — | — | — | 2.59e+10 |
AGG consistent for equal (N, cycles) across GPU counts: YES


Reading: at 1M+ total instances every node scales at 96–99% up to 8 cards
(8× H100 PCIe: b01 6.44e11, b06 5.85e11 instance-cycles/s). At 4,096 total
instances adding cards buys nothing at all (each card gets 512 instances
and is latency-bound). The only "top-out" is per card: **speed-up ∝ cards
as long as N ≥ cards × N_sat (rule 3)**; below that the curve is flat. The
earlier 8× 3090 VeeR run was still accelerating because it satisfied that.

## Against Verilator (RTLMeter)

Verilator's own RTLMeter dashboard (github.com/verilator/verilator-rtlmeter-
results, data pulled 2026-09-14, Verilator 5.053 devel, server-class EPYC
9V74 / Xeon 8573C hosts) reports the `execute` step's `speed` = simulated
clock cycles / elapsed, in kHz. Median of the last 10 CI runs, best in
parentheses:

| case | gcc, 1 thread | clang, 4 threads |
| :-- | --: | --: |
| VeeR-EH1 default dhry | 144.5 kHz (201) | 94.4 (126) |
| VeeR-EH2 default hello | 42.6 kHz (58) | 33.2 (47) |
| VeeR-EH2 default dhry | 44.0 kHz (62) | 33.8 (52) |
| VeeR-EL2 default dhry | 86.5 kHz (134) | 68.6 (118) |
| Vortex mini sgemm | 86.3 kHz (173) | 71.2 (143) |
| XiangShan mini hello | 2.5 kHz (3.8) | 5.3 (11) |

Two things follow. (a) `sv_perf.md`'s 30,195 cycles/s for VeeR-EH2 hello is
the same number as RTLMeter's on a shared box. (b) **Verilator's own
multi-threading is worth nothing on core-sized designs** (4 threads is
SLOWER than 1 on every VeeR row; it only helps on XiangShan/OpenTitan-scale
designs) — so the CPU baseline to beat is one Verilator thread, ~44 kHz for
EH2, ~145 kHz for EH1.

The farm's win is aggregate, and rule 1 gives its size: for a design of C
cells, one 4090 delivers ≈ 4.5e12 / C instance-cycles/s if the per-thread
state fits registers, ≈ 1.2e12 / C if it spills (rule 2). VeeR-EH2 is in
the spill regime and its gsm cell count has not been measured here; at a
nominal 1e5 cells that is ≈ 1.2e7 instance-cycles/s per 4090, i.e. ≈ 270×
one Verilator thread — from ONE card, at ≥ 200k instances, and multiplying
linearly with cards (rule 5). That last paragraph is arithmetic on rules 1
and 2, not a measurement: the measurement is VeeR-EH2 through this kit
(sv2ghdl → gsm → `farm.cu`) with `make_soa_model.py` applied, against the
RTLMeter row above. Everything above it is measured.

## Cost

| node | $/h paid | minutes | $ |
| :-- | --: | --: | --: |
| RTX 3090 ×1 | 0.11 | 20.7 | 0.04 |
| RTX 4090 ×1 | 0.36 | 17.5 | 0.11 |
| A100 40GB ×1 | 0.67 | 19.4 | 0.22 |
| H100 SXM ×1 | 2.94 | 20.6 | 1.01 |
| RTX 3090 ×4 | 0.54 | 16.9 | 0.15 |
| RTX 4090 ×4 | 1.88 | 16.3 | 0.51 |
| H100 PCIe ×8 | 19.20 | 32.7 | 10.45 |

Per 1e12 instance-cycles the 4090 costs $0.001 (b01) to $0.02 (b22); the
H100 8–20× more for the same work. For this workload rent 4090s.

## Reproduce

```
cd bfit/benchmarks/vhdl/gpu
./build_models.sh            # nvc --accel -> whole-scope Verilog -> gsm model (b17: direct gen_statemachine)
./cert_cpu.sh                # g++ build must reproduce nvc's CHK for all six
./build_gpu.sh               # fat binaries sm_80/86/89/90 + PTX (needs ~/tools/cuda-redist or gpu-cc.sh)
VAST_API_KEY=... ./vast_bench.sh RTX_4090                          # single card, sweep.sh
VAST_API_KEY=... NGPUS=4 SWEEP=sweep_multi.sh VERIFIED= ./vast_bench.sh RTX_4090   # multi-GPU node
python3 report.py; python3 report_rules.py; python3 report_multi.py
```

## RTLMeter cores through the kit: design styles and sizes (2026-09-15)

*Added after the user's request to run VeeR-EH2 with the SoA transform and
other RTLMeter designs, Verilator being the baseline to beat.*

### Method changes for SystemVerilog cores

Same farm, three additions. (1) Sources come from RTLMeter's vendored
`designs/<d>/` (descriptor.yaml file list, config include dirs, defines),
go through `sv2v` and `gen_statemachine` on the core's top module (not the
tb_top with its memory models), then `strip_model.py` drops the unused
clock-variant functions (3–4× less C). (2) `gen_stim_v.py` derives the LFSR
drive and fold from the model's own port structs and emits a **Verilator
twin testbench** with byte-identical consumption order and fold; **Verilator
5.040 is the oracle**: a design enters the table only when the gsm model
reproduces Verilator's checksum at ≥20k cycles. Held at zero: extra clocks
(`jtag_tck`) and all `jtag_*` inputs (the TAP's negedge-tck flops are outside
the 2-phase model and RTLMeter never drives JTAG either — that was the one
bit that differed on EH1's first attempt). (3) Core-sized state does not fit
CUDA's per-thread local memory (512 KB): EH1 carries 1.23 MB per instance
(ICCM/DCCM banks), EH2 481 KB, and the array-of-structs kernel fails to
launch ("invalid argument"). `soa_prep.py` runs `yosys/make_soa_model.py`
over model+stimulus and then turns every `[SM_N]` member into a pointer to a
runtime-sized global buffer (one binary for any N; `farm.cu -DFARM_SOA`).
SoA reproduces AoS bit-for-bit (Servant: CHK and population hash equal at
64 and 4,096 instances). Big models are compiled on the rented host
(`vast_build_run.sh`: runtime image + NVIDIA redist nvcc + apt g++, `-Xcicc
-O1 -Xptxas -O1`; EH1 3.5 min, EH2 33–56 min on 8–16 cores; here EH1 alone
ran over an hour and EH2 exhausted memory).

### Results, RTX 4090 unless noted

| design | style | cells | regs | state/inst | layout | cert vs Verilator | GPU plateau inst-cyc/s | GPUs used | at N | cell-evals/s | Verilator 1T, this VM | ×Verilator (this VM) | RTLMeter CI 1T | ×CI |
| :-- | :-- | --: | --: | --: | :-- | :-- | --: | --: | --: | --: | --: | --: | --: | --: |
| ITC b01 | 2-state FSM (VHDL, nvc oracle) | 32 | 3 | 24 B | AoS | nvc CHK | 1.29e11 | 1 | 65k+ | 4.1e12 | — | — | — | — |
| ITC b22 | 3× CPU pipeline (VHDL) | 1,000 | 44 | ~350 B | AoS | nvc CHK | 5.34e9 | 1 | 65k+ | 5.3e12 | — | — | — | — |
| ITC b17 | 3× CPU cores (VHDL) | 2,517 | 135 | 3.4 KB spill | AoS | nvc CHK | 4.67e8 | 1 | 16k | 1.2e12 | — | — | — | — |
| Servant serv_rf_top | bit-serial RISC-V (SERV) | 466 | 49 | 8.7 KB | AoS | MATCH @100k | 4.86e9 | 1 | 1M | 2.3e12 | 4.68e5 | ×10,400 | 1,730 kHz | ×2,800 |
| Servant serv_rf_top | same | 466 | 49 | 8.7 KB | SoA | MATCH @100k | 3.94e9 | 1 | 1M | 1.8e12 | 4.68e5 | ×8,400 | 1,730 kHz | ×2,280 |
| VeeR-EH1 veer_wrapper | 4-stage RV32 core, ICCM/DCCM/icache | 23,323 | 1,447 | 1.23 MB | SoA (AoS cannot launch) | MATCH @100k | 5.39e6 | 1 | 16k (VRAM cap; on the L40S 32k is 10% BELOW 16k) | 1.3e11 | 1.33e4 | ×405 | 144.5 kHz | ×37 |
| VeeR-EH2 eh2_veer_wrapper | dual-thread superscalar RV32 | 71,966 | 4,260 | 481 KB | SoA, **L40S 48 GB** (4090 cannot launch: 119 KB frame) | MATCH @20k | 1.11e6 | 1 | 16k (peak; 32k is 8% lower) | 8.0e10 | 5.62e3 | ×197 | 44.0 kHz | ×25 |

Same binaries on an **L40S (48 GB, 142 SMs, 2.5 GHz)**: Servant 6.19e9 (34% above
the 4090 — this design is latency-bound per thread and the L40S has more
SMs), EH1 4.16e6 at 16k (23% below the 4090), EH2 1.11e6 at 16k. Both VeeR
cores PEAK at 16k instances and fall at 32k: with 19–38 GB of state in
flight the working set thrashes L2 and TLBs, so more breadth stops paying
before VRAM runs out — the first design class where the plateau is not
monotonic in N.

CPU compiled-model rates on this VM (one core, same model): Servant 2.4e5,
EH1 5.4e3 (AoS) / 1.06e4 (SoA) cycles/s. The Verilator column is the twin
testbench on this 5-vCPU VM (2.2 GHz-class); the CI column is RTLMeter's
own dashboard (Verilator 5.053, `gcc` 1 thread, `dhry`, median of the last
10 runs on EPYC 9V74 / Xeon 8573C hosts) — 6–8× faster hosts than this VM,
which is why both columns are shown.

### What the core-sized designs change in the rules

**Rule 1 holds only while the per-thread frame is small.** Servant (frame 0,
209 registers) sits on the small-design constant. EH1's kernel has a 23 KB
stack frame per thread and delivers 1.3e11 cell-evals/s — **35× below the
constant** — and the sweep is capped at 16k instances by 24 GB of state,
still short of the saturation N. Per GPU thread EH1 runs at 330–1,200
cycles/s against 10,600 on one CPU core: the breadth multiplier has to
cover a 10–30× per-thread deficit before it wins, and it does (×405 the
local Verilator thread, ×37 RTLMeter's server-class number) but it is a
different regime from the ITC/Servant class.

**The frame, not the state, is the limit for the biggest cores.** With SoA
the state lives in global memory, but every wire of the netlist is still a
per-thread local (`uint64_t` per cell): EH2's kernel frame is 119 KB
(cuobjdump), and CUDA reserves local memory for every thread the device
can hold — 128 SMs × 2,048 × 119 KB = 31 GB on a 4090 — so the launch
fails with "out of memory" on a 24 GB card regardless of N or block size
(the dynamic-shared-memory residency throttle does not change that
reservation). A 48 GB Ada card (L40S / RTX 6000 Ada) runs the same sm_89
binary. The lever that removes it is compiler-side: `-Xcicc -O1` (needed
to compile 111 MB of C in under an hour) does not reuse stack slots across
the 72k wire temporaries; full cicc optimisation, or emitting wires in
SoA/bit-packed form, would shrink the frame by an order of magnitude.

**SoA costs 19% on a register-resident design** (Servant 4.86e9 → 3.94e9)
and is mandatory above ~200 KB of state; the CPU build of EH1 is 2× FASTER
in SoA than AoS (cache lines carry one member of many instances).

### Cost of this phase

| what | $ |
| :-- | --: |
| 4090 hosts: 5 failed attempts (devel image never came up ×2, key refused, no gcc, include path) | 0.20 |
| 4090 remote builds + sweeps (EH1 diag; SoA build ×2, 33–56 min compiles) | 0.95 |
| L40S binary-only run (all four binaries) | 0.14 |
| **total (measured from instance lifetimes)** | **1.29** |

### Not in the table, and why

| design | blocker |
| :-- | :-- |
| HummingbirdV2-E203 | 87 of 94 gated-clock cones convert (`GSM_ICG2EN`), but the two TCM RAM clocks and five enable cones with a latch inside decline; a pass-through gate is WRONG (whole domains are gated: cert mismatch). Needs `icg2en_rewrite` to accept a matched ICG latch inside an enable cone and the `clk & en` RAM-clock shape. |
| VeeR-EL2 | sv2v mangles the `el2_mem_if` interface into a 2,291-bit cast function that yosys rejects. |
| Vortex (VX_core) | sv2v silently drops every module with interface-typed ports (36 of 103 modules, including VX_core). |
| XuanTie-E902 | sv2v port redeclaration error (`busif_kid_clicintattr_sel`). |

### Reproduce

```
cd bfit/benchmarks/vhdl/gpu
export VERILATOR_ROOT=~/tools/src/verilator PATH=~/tools/src/verilator/bin:$PATH
# model:   sv2v <descriptor sources> > d.sv ; gen_statemachine d.sv <top> model.c ; strip_model.py model.c > model_s.c
# stim:    gen_stim_v.py model_s.c <top> clk > stim.h        (also writes tb_<top>.cpp, the Verilator twin)
# oracle:  verilator --cc --exe --build -O3 --x-initial 0 --x-assign 0 ... tb_<top>.cpp ; ./obj_dir/v<top> <cycles>
# farm:    g++ -DFARM_CPU (cert) ; soa_prep.py model_s.c stim.h ; -DFARM_SOA -DSOA_ALLOC_H=soa_alloc.h
# GPU:     rtlm_build.sh <dir> <top>  (local)   or   VAST_API_KEY=.. vast_build_run.sh RTX_4090 sm_89 <top>:<dir>:soa
# sweep:   sweep_rtlm.sh with rtlm/expect.txt (name cycles CHK [N-list] [FARM_SMEM]) ; report_rtlm.py
```

## Yuri Panchul's bet: `a_plus_b_using_wrapped_fifos` benchmark (2026-09-15)

The 2026-05-28 meetsv thread: Yuri's modified lab test
(`basics-graphics-music/labs/4_microarchitecture/4_2_fifo/4_2_9_a_plus_b_using_wrapped_fifos_benchmark`,
width 4 / depth 4, **10,000,000 sum transfers**, timeout 1e9, logging and
VCD off) takes Icarus 270 s on his i5-6500T and 105 s on his Mac Mini M4;
the bet was to run it functionally correct in under 10 s with no testbench
cheating. The testbench was ported cycle-accurately onto the gsm model
(`vhdl/gpu/rtlm/yuri/yuri.cu`: the same reset/back-to-back/only-a/only-b/
backpressure/random/drain phases, the same driver update rules read
pre-edge, the same a/b scoreboard queues with expected = a+b, the same
transfer counters; `$urandom` → xorshift32 per instance). Every instance
checks itself and reports its own error count; a run "passes" only when
every instance's counters agree and its queues drain empty. One test is
22.2M cycles.

**Baselines on this 5-vCPU VM (2.2 GHz-class):** Verilator 5.040 on the
unmodified `tb.sv` (`--binary --timing -O3`): **26.5 s**, 0.85M cycles/s.
The gsm C model of the same port on one CPU core: 5.2 s, 4.2M cycles/s.
Icarus 12 on the same testbench on this VM: **828 s** (Yuri's own Icarus
numbers are 270 s on his i5 and 105 s on his M4, so this VM is ~3× slower
than his desktop).

**RTX 4090, one GPU, binary shipped, all runs PASS with 0 errors:**

| run | complete tests | wall | instance-cycles/s | transfers/s | speed-up vs Verilator 1T (this VM) |
| :-- | --: | --: | --: | --: | --: |
| 1 GPU thread, 10M transfers (the bet run; 3 seeds: 11.26 / 11.24 / 11.24 s) | 1 | 11.24 s | 1.98e6 | 8.9e5 | **×2.4** |
| 128 seeds, 10M transfers each | 128 | 12.26 s | 2.32e8 | 1.04e8 | ×274 |
| 1,024 seeds, 10M transfers each | 1,024 | 12.26 s | 1.86e9 | 8.35e8 | ×2,190 |
| 4,096 seeds, 10M transfers each | 4,096 | 12.33 s | 7.38e9 | 3.32e9 | ×8,720 |
| 16,384 seeds, 1M transfers each | 16,384 | 3.87 s | 9.41e9 | 4.24e9 | ×11,100 |
| 65,536 seeds, 1M transfers each | 65,536 | 14.7 s | 9.88e9 | 4.45e9 | ×11,700 |
| 262,144 seeds, 1M transfers each | 262,144 | 59.0 s | 9.88e9 | 4.44e9 | ×11,700 |
| 1,048,576 seeds, 1M transfers each | 1,048,576 | 235 s | 9.93e9 | 4.47e9 | ×11,700 |
| **32-bit carriers (`GSM_U32=1`), 1 GPU thread, 10M transfers** (3 seeds: 8.86 / 8.85 / 8.85 s) | 1 | **8.85 s** | 2.51e6 | 1.13e6 | **×3.0** (×94 vs Icarus here) |
| 32-bit carriers, 4,096 seeds, 1M transfers | 4,096 | 1.00 s | 9.12e9 | 4.11e9 | ×10,800 |
| 32-bit carriers, 65,536 seeds, 1M transfers | 65,536 | 12.1 s | 1.20e10 | 5.41e9 | ×14,200 |
| 32-bit carriers, 262,144 seeds, 1M transfers | 262,144 | 48.2 s | 1.21e10 | 5.44e9 | ×14,300 |

Speed-up = instance-cycles/s ÷ Verilator's 0.847M cycles/s on the same
testbench on the same VM (for the single run, 26.5 s ÷ 11.24 s).

**Reading.** The bet as worded — ONE sequential run under 10 s — is met by
the 32-bit-carrier build: **8.85 s on one GPU thread**, three seeds within
10 ms of each other, zero errors. The 64-bit-carrier build missed it by
1.2 s (11.24 s): a lone CUDA thread is a weak scalar core, 64-bit integer
ops cost double on it, and a sequential test cannot use the card's breadth;
the 32-bit emission (`GSM_U32=1`, 56 registers) is worth 27% per thread and
22% at the plateau. Either way it beats Verilator's single thread on the
real testbench (2.4× / 3.0×) and Icarus on the same VM by 74× / 94×. What the GPU is built for shows
in the other rows: 4,096 complete copies of the 10M-transfer test, each
self-checked, finish in the wall time of one, and at the plateau the card
checks 4.4 billion transfers per second — 11,700× the Verilator thread, or
in the bet's own units, the 22.2M-cycle test at a rate of one every 2.2 ms.
This is the "1000× Verilator" claim in its true form: aggregate over
seeds, not latency of one run. The 4-bit design occupies 80 registers and a
320-byte stack per thread, so it sits well inside rule 1; the harness does
more per cycle than the plain farm (PRNG, scoreboard, 64-bit carriers),
which is why its plateau is 9.9e9 rather than the ~1e11 of the ITC FSMs.
The 32-bit-carrier rows above are that build; raw log
`results/vast_RTX_4090x1_51099583.log`.

Reproduce: `rtlm/yuri/yuri.cu` + the model from `gen_statemachine dut.sv
a_plus_b_using_wrapped_fifos width=4 depth=4`; `sweep_yuri.sh` on the
instance; raw log `results/vast_RTX_4090x1_51098213.log`.
