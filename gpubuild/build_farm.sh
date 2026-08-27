#!/bin/bash
# Flow of record: generated gsm model .c  ->  GPU farm fat binary, built in
# a podman CUDA container (no local CUDA install, no GPU needed to build).
#
#   build_farm.sh model.c harness.cu out_binary
#
# Steps:
#   1. make_device_model.sh — qualify the generated model fns as __device__
#      (skipped when <model>_dev.c is already fresh)
#   2. nvcc fat binary: sm_75 + sm_86 SASS, + compute_86 PTX so newer parts
#      (sm_89, sm_90+) JIT at load.  cudart links static, so the binary
#      needs only libcuda.so.1 (the driver) on the run host.
#      Override targets with GENC="-gencode arch=...,code=..." if your
#      hardware differs.  PTX note: PTX is an IR and is easier to reverse
#      than SASS — for IP-sensitive binaries shipped to third-party hosts,
#      set GENC to SASS-only.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
MODEL=${1:?model .c}; HARNESS=${2:?harness .cu}; OUT=${3:?output binary}
DEV=${MODEL%.c}_dev.c
if [ ! -s "$DEV" ] || [ "$MODEL" -nt "$DEV" ]; then
    [ -s "$MODEL" ] || { echo "no $MODEL and no fresh $DEV" >&2; exit 1; }
    "$HERE/make_device_model.sh" "$MODEL"   # writes $DEV alongside
fi
GENC=${GENC:-"-gencode arch=compute_75,code=sm_75 \
              -gencode arch=compute_86,code=sm_86 \
              -gencode arch=compute_86,code=compute_86"}
# --threads: parallel per-gencode compilation (big single-file models)
"$HERE/gpu-cc.sh" -O2 --threads 4 $GENC -DMODEL_C="\"$DEV\"" \
    -o "$OUT" "$HARNESS"
echo "BUILT $OUT ($(du -h "$OUT" | cut -f1)) — needs only libcuda.so.1 to run"
