#!/bin/bash
# Yuri's a_plus_b benchmark on one GPU: the bet run (1 instance, 10M transfers),
# then breadth: many independent seeds of the same test at once.
cd "$(dirname "$0")"; chmod +x yuri_gpu
echo "SWEEP-START $(date -u +%FT%TZ) host=$(hostname)"
nvidia-smi --query-gpu=name,driver_version,clocks.max.sm,memory.total --format=csv
for r in 1 2 3; do ./yuri_gpu 1 10000000 $r; done            # the bet: one test, three seeds
for N in 128 1024 4096; do ./yuri_gpu $N 10000000 1; done      # full 10M-transfer test x N seeds
for N in 16384 65536 262144 1048576; do ./yuri_gpu $N 1000000 1; done   # 1M-transfer test x N seeds
echo "SWEEP-END $(date -u +%FT%TZ)"
