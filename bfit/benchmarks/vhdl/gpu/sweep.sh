#!/bin/bash
# On-instance sweep: certify each design at canonical cycles (CHK0 must equal the
# nvc checksum), then adaptive N sweep to find the throughput plateau.
# usage: sweep.sh [designs...]   (binaries farm_<d>_gpu alongside)
cd "$(dirname "$0")"
echo "SWEEP-START $(date -u +%FT%TZ) host=$(hostname)"
nvidia-smi --query-gpu=name,driver_version,clocks.max.sm,memory.total,power.limit --format=csv
declare -A CYC=( [b01]=3000000 [b06]=2000000 [b12]=3000000 [b14]=1000000 [b17]=1000000 [b22]=1000000 )
declare -A EXP=( [b01]=A29A0B8A83930283 [b06]=574DA57280D8C019 [b12]=C946C4B187AEB9D3 [b14]=BE00000000000383 [b17]=1CDC023B08293B0C [b22]=64A596EC9D810F52 )
TARGET=${TARGET:-6}     # seconds per timed point
CERT_N=${CERT_N:-4096}; NLIST=${NLIST:-"1024 4096 16384 65536 262144 1048576 4194304"}; BLKS=${BLKS:-"128 256"}
for d in ${@:-b01 b06 b14 b12 b22 b17}; do
  B=./farm_${d}_gpu; chmod +x $B; [ -x $B ] || { echo "$d: no binary"; continue; }
  echo "== $d"
  # 1. certification at the canonical cycle count, N=4096 (the T1000 column's config)
  out=$(timeout 900 $B ${CYC[$d]} $CERT_N 128 1); echo "$out"
  chk=$(echo "$out" | grep -oE 'CHK0=[0-9A-F]+' | cut -d= -f2)
  [ "$chk" = "${EXP[$d]}" ] && echo "CERT $d MATCH $chk" || echo "CERT $d MISMATCH got=$chk expected=${EXP[$d]}"
  # 2. adaptive N sweep, block 128 and 256; cycles chosen from the last rate for ~TARGET s
  rate=$(echo "$out" | grep -oE 'agg_inst_cyc_per_s=[0-9.e+]+' | cut -d= -f2)
  for N in $NLIST; do
    for blk in $BLKS; do
      cyc=$(python3 -c "import math; r=float('$rate'); c=int(r*$TARGET/$N); print(max(1000, min(3000000, c)))")
      out=$(timeout 600 $B $cyc $N $blk 2); echo "$out"
      r2=$(echo "$out" | grep -oE 'agg_inst_cyc_per_s=[0-9.e+]+' | cut -d= -f2); [ -n "$r2" ] && rate=$r2
    done
  done
done
echo "SWEEP-END $(date -u +%FT%TZ)"
