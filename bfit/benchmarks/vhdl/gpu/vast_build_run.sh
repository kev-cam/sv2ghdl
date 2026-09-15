#!/bin/bash
# Remote build+run for models too big to compile here: rent a GPU host with a
# CUDA *devel* image, ship model_s_dev.c + stim.h + farm.cu (+ sweep, expect),
# build on the host (nvcc -Xcicc -O1 -Xptxas -O1, one SASS target), run the
# sweep, log, destroy.  Source leaves this machine: use only for public RTL.
#   VAST_API_KEY=... ./vast_build_run.sh <GPU> <arch e.g. sm_89> <name> <dir-with-model_s_dev.c+stim.h> [more names:dirs...]
set -u; : "${VAST_API_KEY:?}"; export VAST_API_KEY
GPU=$1; ARCH=$2; shift 2
K=/usr/local/src/sv2ghdl/bfit/benchmarks/vhdl/gpu; V=~/.local/bin/vastai; cd $K
NGPUS=${NGPUS:-1}; TAG=${GPU}x${NGPUS}-build
OFFER=$($V search offers "gpu_name=$GPU num_gpus=$NGPUS rentable=true ${VERIFIED:-verified=true} cuda_vers>=12.4 reliability>0.95 inet_down>100 disk_space>=32 cpu_cores_effective>=${MINCORES:-6} cpu_ram>=${MINRAM:-16}" -o 'dph' --raw | \
  python3 -c "import json,sys; o=json.load(sys.stdin); o=[x for x in o if x.get('dph_total',9)<${MAXDPH:-3.0} and str(x.get('machine_id')) not in '${EXCLUDE:-}'.split(',') and str(x.get('id')) not in '${EXCLUDE_OFFERS:-}'.split(',')]; print(o[0]['id'], o[0]['dph_total'], str(o[0].get('gpu_name')).replace(' ','_'), o[0].get('cpu_cores_effective'), o[0].get('cpu_ram')) if o else print('NONE')")
echo "$(date +%T) offer: $OFFER" | tee -a vast_bench.log
read -r OID DPH GN CORES RAM <<< "$OFFER"; [ "$OID" != NONE ] || exit 2
IID=$($V create instance $OID --image nvidia/cuda:12.4.1-runtime-ubuntu22.04 --ssh --direct --disk 32 --raw | python3 -c "import json,sys; print(json.load(sys.stdin)['new_contract'])")
echo "$(date +%T) instance $IID on offer $OID ($GN \$${DPH}/h ${CORES} cores ${RAM} MB)" | tee -a vast_bench.log
trap 'echo "$(date +%T) destroying $IID"; $V destroy instance $IID -y; echo "$(date +%T) destroyed $IID" | tee -a vast_bench.log' EXIT
# stage: one tarball with everything
ST=$(mktemp -d); mkdir -p $ST/src; cp farm.cu sweep_rtlm.sh $ST/src/; cp rtlm/expect.txt $ST/src/ 2>/dev/null
# toolchain: NVIDIA redist nvcc 12.4 fetched ON the instance (runtime image comes up reliably; devel did not)
B=https://developer.download.nvidia.com/compute/cuda/redist
TOOL="mkdir -p /root/ct && cd /root/ct && (apt-get update -qq >/dev/null && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq xz-utils curl g++ >/dev/null 2>&1; g++ --version | head -1) && for a in cuda_nvcc/linux-x86_64/cuda_nvcc-linux-x86_64-12.4.131-archive cuda_cudart/linux-x86_64/cuda_cudart-linux-x86_64-12.4.127-archive cuda_cccl/linux-x86_64/cuda_cccl-linux-x86_64-12.4.127-archive; do curl -sSL $B/\$a.tar.xz | tar xJ; done && for d in cuda_*-archive; do cp -rn \$d/* . ; done && mkdir -p targets/x86_64-linux && ln -sfn ../../lib targets/x86_64-linux/lib && ln -sfn ../../include targets/x86_64-linux/include && export PATH=/root/ct/bin:\$PATH && nvcc --version | tail -1"
BUILD="$TOOL && export PATH=/root/ct/bin:\$PATH && cd /root/src"
for spec in "$@"; do n=${spec%%:*}; rest=${spec#*:}; d=${rest%%:*}; mode=${rest#*:}; [ "$mode" = "$rest" ] && mode=aos; mkdir -p $ST/src/$n
  if [ "$mode" = soa ]; then cp $d/model_s_soa_dev.c $ST/src/$n/model_dev.c; cp $d/stim_soa.h $ST/src/$n/stim.h; cp $d/soa_alloc.h $ST/src/$n/; DEF="-DFARM_SOA -DSOA_ALLOC_H='\"soa_alloc.h\"'"
  else cp $d/model_s_dev.c $ST/src/$n/model_dev.c 2>/dev/null || cp $d/model_dev.c $ST/src/$n/model_dev.c; cp $d/stim.h $ST/src/$n/; DEF=""; fi
  BUILD="$BUILD && (cd $n && time nvcc -I. -O2 -Xcicc -O1 -Xptxas -O1,-v --threads 4 -diag-suppress 177,550 -gencode arch=compute_${ARCH#sm_},code=$ARCH -cudart static $DEF -DMODEL_C='\"model_dev.c\"' -DSTIM_H='\"stim.h\"' -o ../farm_${n}_gpu ../farm.cu 2>&1 | grep -v warning | grep -E "ptxas info|real|error" | tail -4; ls -la ../farm_${n}_gpu)"
done
[ -n "${PREBUILT:-}" ] && for b in $PREBUILT; do cp $b $ST/src/; done
(cd $ST && tar czf src.tgz src) && ls -la $ST/src.tgz
export RUN_TIMEOUT=${RUN_TIMEOUT:-14000}
/usr/local/src/sv2ghdl/gpubuild/vast_run.sh $IID "cd /root && tar xzf src.tgz && echo DPH=$DPH GPU=$TAG && $BUILD && cd /root/src && bash ./sweep_rtlm.sh" $ST/src.tgz
HP=$(grep -o "status=running [^ ]*" vast_${IID}.log | head -1 | cut -d' ' -f2); mkdir -p rtlm
[ -n "$HP" ] && scp -o ConnectTimeout=15 -P ${HP#*:} "root@${HP%:*}:/root/src/farm_*_gpu" rtlm/ 2>>vast_${IID}.log && ls -la rtlm/farm_*_gpu | awk '{print "fetched", $5, $9}'
mv -f vast_${IID}.log results/vast_${TAG}_${IID}.log 2>/dev/null; rm -rf $ST
