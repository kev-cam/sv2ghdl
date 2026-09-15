#!/bin/bash
# CPU certification: g++ build of farm.cu per design, canonical cycles, N=1 -> CHK0 vs nvc CHK.
S=/usr/local/src/sv2ghdl/bfit/benchmarks/vhdl/gpu
cd $S; mkdir -p farm
declare -A MODEL=( [b01]=farm/model_b01.c [b06]=farm/model_b06.c [b12]=farm/model_b12.c [b14]=farm/model_b14.c [b17]=farm/model_b17.c [b22]=farm/model_b22.c )
declare -A CYC=( [b01]=3000000 [b06]=2000000 [b12]=3000000 [b14]=1000000 [b17]=1000000 [b22]=1000000 )
for n in b01 b06 b12 b14 b17 b22; do
  python3 gen_stim.py I99T/i99t/$n/$n.vhd $n > farm/stim_$n.h || { echo "$n: gen_stim FAILED"; continue; }
  t0=$(date +%s.%N)
  g++ -O2 -fopenmp -x c++ -DFARM_CPU -DMODEL_C="\"$S/${MODEL[$n]}\"" -DSTIM_H="\"$S/farm/stim_$n.h\"" -o farm/farm_${n}_cpu farm.cu > farm/cc_$n.log 2>&1 || { echo "$n: g++ FAILED"; grep -m5 error farm/cc_$n.log; continue; }
  t1=$(date +%s.%N)
  nvcchk=$(grep -oE 'CHK=[0-9A-F]+' nvc-chk/$n.r.log | head -1 | cut -d= -f2)
  out=$(OMP_NUM_THREADS=1 farm/farm_${n}_cpu ${CYC[$n]} 1)
  chk0=$(echo "$out" | grep -oE 'CHK0=[0-9A-F]+' | cut -d= -f2)
  secs=$(echo "$out" | grep -oE 'secs=[0-9.]+' | cut -d= -f2)
  [ "$chk0" = "$nvcchk" ] && v=MATCH || v=MISMATCH
  echo "$n: $v cpu-model CHK0=$chk0 nvc=$nvcchk  1-inst ${CYC[$n]} cyc in ${secs}s (g++ $(awk "BEGIN{printf \"%.0f\", $t1-$t0}")s)"
done
