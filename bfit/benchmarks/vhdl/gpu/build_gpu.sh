#!/bin/bash
# Fat binaries: SASS for sm_80 (A100) sm_86 (3090/A4000..) sm_89 (4090/L40S) sm_90 (H100) + compute_90 PTX (JIT on newer parts)
S=/usr/local/src/sv2ghdl/bfit/benchmarks/vhdl/gpu
NVCC=/home/claude/tools/cuda-redist/bin/nvcc
declare -A MODEL=( [b01]=farm/model_b01.c [b06]=farm/model_b06.c [b12]=farm/model_b12.c [b14]=farm/model_b14.c [b17]=farm/model_b17.c [b22]=farm/model_b22.c )
GENC="-gencode arch=compute_80,code=sm_80 -gencode arch=compute_86,code=sm_86 -gencode arch=compute_89,code=sm_89 -gencode arch=compute_90,code=sm_90 -gencode arch=compute_90,code=compute_90"
cd $S
for n in ${@:-b01 b06 b14 b12 b22 b17}; do
  t0=$(date +%s)
  $NVCC -O2 --threads 5 -diag-suppress 177,550 $GENC -cudart static -DMODEL_C="\"$S/farm/model_${n}_dev.c\"" -DSTIM_H="\"$S/farm/stim_$n.h\"" -o farm/farm_${n}_gpu farm.cu > farm/nvcc_$n.log 2>&1
  echo "$n: exit=$? $(( $(date +%s)-t0 ))s $(ls -la farm/farm_${n}_gpu 2>/dev/null | awk '{print $5}') bytes"
done
echo BUILD-ALL-DONE
