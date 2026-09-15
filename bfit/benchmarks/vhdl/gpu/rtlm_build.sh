#!/bin/bash
# rtlm_build.sh <dir-with-model.c-and-stim.h> <name>  -> <dir>/farm_<name>_gpu (fat) + farm_<name>_cpu
set -u; d=$1; n=$2; K=/usr/local/src/sv2ghdl/bfit/benchmarks/vhdl/gpu; NVCC=/home/claude/tools/cuda-redist/bin/nvcc
cd $d; M=model.c; [ -s model_s.c ] && M=model_s.c   # stripped model (strip_model.py) if present
[ -s ${M%.c}_dev.c ] || /usr/local/src/sv2ghdl/gpubuild/make_device_model.sh $M > /dev/null; MD=${M%.c}_dev.c
GENC=${GENC:-"-gencode arch=compute_80,code=sm_80 -gencode arch=compute_86,code=sm_86 -gencode arch=compute_89,code=sm_89 -gencode arch=compute_90,code=sm_90 -gencode arch=compute_90,code=compute_90"}
g++ -O2 -fopenmp -x c++ -DFARM_CPU -DMODEL_C="\"$d/$M\"" -DSTIM_H="\"$d/stim.h\"" -o farm_${n}_cpu $K/farm.cu 2> cc_cpu.log || { echo "$n: g++ FAILED"; grep -m3 error cc_cpu.log; exit 1; }
CICC=""; [ $(stat -c %s $M) -gt 20000000 ] && CICC="-Xcicc -O1"   # gpubuild field note: huge device functions hold cicc for hours at full opt
t0=$(date +%s); $NVCC -O2 $CICC --threads 5 -diag-suppress 177,550 $GENC -cudart static -DMODEL_C="\"$d/$MD\"" -DSTIM_H="\"$d/stim.h\"" -o farm_${n}_gpu $K/farm.cu > nvcc.log 2>&1 && echo "$n: gpu built $(( $(date +%s)-t0 ))s $(stat -c %s farm_${n}_gpu) bytes" || { echo "$n: nvcc FAILED"; grep -m3 error nvcc.log; }
[ -n "${NO_PTXAS_V:-}" ] || $NVCC -O2 -diag-suppress 177,550 -arch=sm_89 -Xptxas -v -cudart static -DMODEL_C="\"$d/$MD\"" -DSTIM_H="\"$d/stim.h\"" -o /dev/null $K/farm.cu 2>&1 | grep -E "registers|spill" | tr '\n' ' '; echo
