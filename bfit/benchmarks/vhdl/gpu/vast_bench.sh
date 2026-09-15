#!/bin/bash
# Rent one GPU on vast.ai, ship the farm binaries + sweep.sh, run, log, destroy.
#   VAST_API_KEY=... ./vast_bench.sh RTX_4090 [designs...]
# Offers must have a driver >= the 12.4 toolkit the binaries were built with.
set -u
: "${VAST_API_KEY:?set VAST_API_KEY}"; export VAST_API_KEY
GPU=${1:?gpu_name e.g. RTX_4090}; shift
NGPUS=${NGPUS:-1}; SWEEP=${SWEEP:-sweep.sh}; VERIFIED=${VERIFIED:-verified=true}; TAG=${GPU}x${NGPUS}
S=/usr/local/src/sv2ghdl/bfit/benchmarks/vhdl/gpu
V=~/.local/bin/vastai
cd $S
OFFER=$($V search offers "gpu_name=$GPU num_gpus=$NGPUS rentable=true $VERIFIED cuda_vers>=12.4 reliability>0.95 inet_down>20 disk_space>=16" -o 'dph' --raw | \
  python3 -c "import json,sys; o=json.load(sys.stdin); o=[x for x in o if x.get('dph_total',9)<${MAXDPH:-3.0} and str(x.get('machine_id')) not in '${EXCLUDE:-}'.split(',') and str(x.get('id')) not in '${EXCLUDE_OFFERS:-}'.split(',')]; print(o[0]['id'], o[0]['dph_total'], str(o[0].get('gpu_name')).replace(' ','_'), o[0].get('cuda_max_good'), o[0].get('geolocation','?').replace(' ','_')) if o else print('NONE')")
echo "$(date +%T) offer: $OFFER" | tee -a vast_bench.log
read -r OID DPH GN CV GEO <<< "$OFFER"; [ "$OID" != NONE ] || exit 2
IID=$($V create instance $OID --image nvidia/cuda:12.4.1-runtime-ubuntu22.04 --ssh --direct --disk 16 --raw | python3 -c "import json,sys; print(json.load(sys.stdin)['new_contract'])")
echo "$(date +%T) instance $IID on offer $OID ($GN cuda<=$CV \$${DPH}/h $GEO)" | tee -a vast_bench.log
trap 'echo "$(date +%T) destroying $IID"; $V destroy instance $IID -y; echo "$(date +%T) destroyed $IID" | tee -a vast_bench.log' EXIT
export RUN_TIMEOUT=${RUN_TIMEOUT:-5400}
BINDIR=${BINDIR:-farm}; FILES="$SWEEP ${EXTRA_FILES:-}"; for d in ${@:-b01 b06 b14 b12 b22 b17}; do FILES="$FILES $BINDIR/farm_${d}_gpu"; done
/usr/local/src/sv2ghdl/gpubuild/vast_run.sh $IID "cd /root && echo DPH=$DPH GPU=$TAG && bash ./$SWEEP $*" $FILES
mv -f vast_${IID}.log results/vast_${TAG}_${IID}.log 2>/dev/null
