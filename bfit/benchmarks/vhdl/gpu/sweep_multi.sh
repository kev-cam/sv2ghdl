#!/bin/bash
# Multi-GPU scaling sweep on one node: certify (N=4096, 1 GPU), then for each GPU
# count g: fixed total N (4096 / 65536 / 1M split across g cards) and per-card
# saturating N (g x 1M).  AGG must be identical for the same N at any g.
cd "$(dirname "$0")"
echo "SWEEP-START $(date -u +%FT%TZ) host=$(hostname)"
nvidia-smi --query-gpu=index,name,driver_version,clocks.max.sm,memory.total,power.limit --format=csv
NG=$(nvidia-smi -L | wc -l); echo "NGPU=$NG"
declare -A CYC=( [b01]=3000000 [b06]=2000000 [b12]=3000000 [b14]=1000000 [b17]=1000000 [b22]=1000000 )
declare -A EXP=( [b01]=A29A0B8A83930283 [b06]=574DA57280D8C019 [b12]=C946C4B187AEB9D3 [b14]=BE00000000000383 [b17]=1CDC023B08293B0C [b22]=64A596EC9D810F52 )
TARGET=${TARGET:-5}; GLIST=${GLIST:-"1 2 4 8"}; PERGPU=${PERGPU:-1048576}
for d in ${@:-b01 b06 b14 b12 b22 b17}; do
  B=./farm_${d}_gpu; chmod +x $B; [ -x $B ] || { echo "$d: no binary"; continue; }
  echo "== $d"
  out=$(timeout 900 $B ${CYC[$d]} 4096 128 1 1); echo "$out"
  chk=$(echo "$out" | grep -oE 'CHK0=[0-9A-F]+' | cut -d= -f2)
  [ "$chk" = "${EXP[$d]}" ] && echo "CERT $d MATCH $chk" || echo "CERT $d MISMATCH got=$chk expected=${EXP[$d]}"
  rate=$(echo "$out" | grep -oE 'agg_inst_cyc_per_s=[0-9.e+]+' | cut -d= -f2)
  for g in $GLIST; do
    [ $g -le $NG ] || continue
    for N in 4096 65536 1048576 $((g*PERGPU)); do
      cyc=$(python3 -c "r=float('$rate')*$g; c=int(r*$TARGET/$N); print(max(1000, min(3000000, c)))")
      out=$(timeout 600 $B $cyc $N 128 2 $g); echo "$out"
    done
    r2=$(echo "$out" | grep -oE 'agg_inst_cyc_per_s=[0-9.e+]+' | cut -d= -f2); [ -n "$r2" ] && rate=$(python3 -c "print(float('$r2')/$g)")
  done
done
echo "SWEEP-END $(date -u +%FT%TZ)"
