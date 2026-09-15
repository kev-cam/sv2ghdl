#!/bin/bash
# On-instance sweep for RTLMeter-core farms: expected checksums come from
# expect.txt ("<name> <cycles> <CHK>" per line, produced by the CPU build).
# Cert at N=4096 (or CERT_N), then adaptive N sweep; block 128 only.
cd "$(dirname "$0")"
echo "SWEEP-START $(date -u +%FT%TZ) host=$(hostname)"
nvidia-smi --query-gpu=name,driver_version,clocks.max.sm,memory.total,power.limit --format=csv
TARGET=${TARGET:-6}; CERT_N=${CERT_N:-4096}; NLIST=${NLIST:-"1024 4096 16384 65536 262144 1048576"}
while read -r d cyc exp nl smem; do   # optional: per-design N list (comma-separated) and FARM_SMEM throttle bytes
  NL=${nl:+$(echo $nl | tr , " ")}; NL=${NL:-$NLIST}; export FARM_SMEM=${smem:-0}
  [ -n "$d" ] || continue; B=./farm_${d}_gpu; chmod +x $B 2>/dev/null; [ -x $B ] || { echo "$d: no binary"; continue; }
  echo "== $d"
  out=$(timeout 1800 $B $cyc $CERT_N 128 1); echo "$out"
  chk=$(echo "$out" | grep -oE 'CHK0=[0-9A-F]+' | cut -d= -f2)
  [ "$chk" = "$exp" ] && echo "CERT $d MATCH $chk" || echo "CERT $d MISMATCH got=$chk expected=$exp"
  rate=$(echo "$out" | grep -oE 'agg_inst_cyc_per_s=[0-9.e+]+' | cut -d= -f2); [ -n "$rate" ] || continue
  for N in $NL; do
    c=$(python3 -c "r=float('$rate'); c=int(r*$TARGET/$N); print(max(200, min(3000000, c)))")
    out=$(timeout 900 $B $c $N 128 2); echo "$out"
    r2=$(echo "$out" | grep -oE 'agg_inst_cyc_per_s=[0-9.e+]+' | cut -d= -f2); [ -n "$r2" ] && rate=$r2
  done
done < expect.txt
echo "SWEEP-END $(date -u +%FT%TZ)"
