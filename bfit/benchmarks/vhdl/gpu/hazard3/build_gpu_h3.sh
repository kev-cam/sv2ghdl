#!/bin/bash
# build_gpu_h3.sh - the GPU binaries, built HERE with gpubuild/gpu-cc.sh:
# nvcc 12.4 inside the digest-pinned nvidia/cuda:12.4.1-devel-ubuntu22.04
# image under podman with --network=none (no GPU, no driver, no CUDA install on
# the build host; only the compiled binary ever ships).  bfit GPU-farm kit
# (PolyForm Noncommercial 1.0.0).  Needs build_h3.sh first.
#   build_gpu_h3.sh [thr] [split]          env NVCC_THREADS (default 1: ptxas -v
#                                          lines of parallel arch jobs would interleave)
# Fat binary: SASS sm_80 (A100) sm_86 (RTX 3090/A40/A6000) sm_89 (RTX 4090/
# L40S/L4) sm_90 (H100/H200) + compute_90 PTX (JIT on newer parts); cudart
# static, so the run host needs only libcuda.so.1.  Each binary holds three
# kernel variants (h3farm.cu: __launch_bounds__(128, MINB), MINB 1/3/4).
# Writes bin/h3<wl>_gpu, bin/SHA256SUMS, results/ptxas_<wl>.txt.
set -u
K=$(cd "$(dirname "$0")" && pwd)
W=${H3_WORK:-$HOME/gf_hz3/work}
CC=${GPU_CC:-/usr/local/src/sv2ghdl/gpubuild/gpu-cc.sh}
GENC="-gencode arch=compute_80,code=sm_80 -gencode arch=compute_86,code=sm_86 -gencode arch=compute_89,code=sm_89 -gencode arch=compute_90,code=sm_90 -gencode arch=compute_90,code=compute_90"
mkdir -p $K/bin $K/results
for wl in ${@:-thr split}; do
    B=$W/gpu_$wl; rm -rf $B; mkdir -p $B
    [ -s $W/$wl/model_m_dev.c ] || { echo "$wl: no model (run build_h3.sh)"; exit 1; }
    cp $K/h3farm.cu $W/$wl/model_m_dev.c $B/          # gpu-cc.sh mounts the cwd only
    cd $B
    t0=$(date +%s)
    $CC -O2 --threads ${NVCC_THREADS:-1} -Xptxas -v -diag-suppress 177,550 $GENC -cudart static \
        -DH3_NAME="\"h3$wl\"" -DMODEL_C="\"model_m_dev.c\"" -o h3${wl}_gpu h3farm.cu > nvcc.log 2>&1
    rc=$?
    secs=$(( $(date +%s)-t0 ))
    [ $rc = 0 ] && [ -s h3${wl}_gpu ] || { echo "$wl: nvcc FAILED (exit $rc, $B/nvcc.log)"; grep -m5 error nvcc.log; exit 1; }
    cp h3${wl}_gpu $K/bin/
    # ptxas -v: one block per (kernel, arch): entry name, stack frame / spill, registers
    awk -v wl=$wl -v secs=$secs -v size=$(stat -c %s h3${wl}_gpu) '
        /Compiling entry function/ { match($0, /h3_kernelILi[0-9]+E/); k = substr($0, RSTART + 12, RLENGTH - 13);
                                     match($0, /for .sm_[0-9]+/); a = substr($0, RSTART + 5, RLENGTH - 5) }
        /bytes stack frame/        { st = $1; ss = $5; sl = $9 }
        /Used [0-9]+ registers/    { if (k != "") { match($0, /Used [0-9]+ registers/); r = substr($0, RSTART + 5, RLENGTH - 15);
                                     printf "%s %s MINB=%s registers=%s stack_frame=%s spill_stores=%s spill_loads=%s\n", wl, a, k, r, st, ss, sl; k = "" } }
        END { printf "%s binary=%d bytes nvcc=%ds (podman gpu-cc.sh, %s)\n", wl, size, secs, "nvcc 12.4.131" }' nvcc.log \
        | sort -u > $K/results/ptxas_$wl.txt
    cat $K/results/ptxas_$wl.txt
done
(cd $K/bin && sha256sum h3*_gpu > SHA256SUMS && cat SHA256SUMS)
echo BUILD-GPU-H3-DONE
