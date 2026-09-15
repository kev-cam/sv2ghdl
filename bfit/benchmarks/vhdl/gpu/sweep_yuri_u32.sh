#!/bin/bash
# 32-bit-carrier build of Yuri's benchmark: the bet run x3, then breadth points
cd "$(dirname "$0")"; chmod +x yuri_gpu_u32
echo "SWEEP-START $(date -u +%FT%TZ) host=$(hostname)"
nvidia-smi --query-gpu=name,driver_version,clocks.max.sm --format=csv
for r in 1 2 3; do ./yuri_gpu_u32 1 10000000 $r; done
for N in 4096 65536 262144; do ./yuri_gpu_u32 $N 1000000 1; done
echo "SWEEP-END $(date -u +%FT%TZ)"
