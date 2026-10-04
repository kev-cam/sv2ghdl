#!/bin/bash
# local_gpu_h3.sh - the kit's own h3farm.cu + models on a LOCAL GPU (no rental), kept inside a
# display watchdog (WDDM/WSL2: kernelExecTimeoutEnabled=1, ~2 s per kernel).  bfit GPU-farm kit
# (PolyForm Noncommercial 1.0.0).  Needs build_h3.sh and cert_h3.sh (the CPU per-tile table).
#   local_gpu_h3.sh [sm_75]          -> results/local_gpu_<arch>.log (the anchor of project_h3.py)
#   LOCAL_SWEEP=1 local_gpu_h3.sh    also runs sweep_h3.sh + check_h3.py end to end with
#                                    local-sized N (whole-image kernels: ~1 min each on a T1000;
#                                    only on a GPU that tolerates that) -> results/local_sweep_<arch>.log
# 1. builds h3thr/h3split for <arch> with gpubuild/gpu-cc.sh (podman, --network=none); prints
#    the device (SMs, clock, watchdog) and samples the SM clock under load
# 2. thr: complete 95,038-cycle renders, N=32, every kernel variant: CHK0/DONE0/AGG/CAGG
#    (skipped when one kernel would exceed 1.5 s under a watchdog)
# 3. split m=0: EVERY tile of the image in one-wave slices of SMs x 256 tiles: the per-tile
#    table must equal the CPU gsm table; AGG/CAGG; image md5 from the dumped words
# 4. rates (measured first, after a warm-up): thr plateau (5,000 cycles, 1/2/4 waves), lone
#    warp, one wave of in-set split tiles per kernel variant
# Runs only the local binaries it builds; ships nothing anywhere.
set -u
export PYTHONDONTWRITEBYTECODE=1                 # check_h3 is imported: no __pycache__ in the kit
K=$(cd "$(dirname "$0")" && pwd)
W=${H3_WORK:-$HOME/gf_hz3/work}; ARCH=${1:-sm_75}; B=$W/local_$ARCH
CC=${GPU_CC:-/usr/local/src/sv2ghdl/gpubuild/gpu-cc.sh}
LOG=$K/results/local_gpu_$ARCH.log; mkdir -p $K/results $B; : > $LOG
say() { echo "$*" | tee -a $LOG; }
f() { echo "$1" | grep -oE "$2=[0-9A-Fa-f.e+-]+" | head -1 | cut -d= -f2; }
[ -s $W/split/gsm_m0.txt ] || { echo "no CPU per-tile table $W/split/gsm_m0.txt (run cert_h3.sh)"; exit 1; }
cd $B
cp $K/h3farm.cu .; cp $W/thr/model_m_dev.c thr_dev.c; cp $W/split/model_m_dev.c split_dev.c
cat > devq.cu <<'EOF'
#include <cstdio>
#include <cuda_runtime.h>
int main() { cudaDeviceProp p; if (cudaGetDeviceProperties(&p, 0) != cudaSuccess) return 1;
  int clk = 0; cudaDeviceGetAttribute(&clk, cudaDevAttrClockRate, 0);
  printf("DEV name=\"%s\" sm=%d.%d SMS=%d clock_mhz=%d watchdog=%d\n", p.name, p.major, p.minor, p.multiProcessorCount, clk / 1000, p.kernelExecTimeoutEnabled); }
EOF
G="-gencode arch=compute_${ARCH#sm_},code=$ARCH"
$CC -O2 $G -cudart static -o devq devq.cu > cc_devq.log 2>&1 || { cat cc_devq.log; exit 1; }
for wl in thr split; do
    $CC -O2 $G -cudart static -diag-suppress 177,550 -DH3_NAME="\"h3$wl\"" -DMODEL_C="\"${wl}_dev.c\"" -o h3$wl h3farm.cu > cc_$wl.log 2>&1 || { tail cc_$wl.log; exit 1; }
done
D=$(./devq) || { echo "no usable CUDA device"; exit 1; }
say "$D"; SMS=$(f "$D" SMS); WD=$(f "$D" watchdog); S=$((SMS * 256))
nvidia-smi --query-gpu=clocks.sm,utilization.gpu --format=csv,noheader,nounits -lms 100 > smi.csv 2>/dev/null & SMI=$!
for w in 1 2; do H3_MINB=1 timeout 60 ./h3thr 5000 $S 128 1 1 > /dev/null; done      # warm-up: leave the idle clocks
say "== rates (thr plateau at 1/2/4 waves of $S, 5,000 cycles; lone warp; one wave of in-set split tiles)"
for n in $S $((2 * S)) $((4 * S)); do o=$(H3_MINB=1 timeout 60 ./h3thr 5000 $n 128 1 1); say "$o"; done
o=$(H3_MINB=1 timeout 60 ./h3thr 20000 32 128 1 1); say "$o"; LONE=$(f "$o" per_inst_cyc_per_s)
for mb in 1 3 4; do o=$(H3_MINB=$mb H3_TILE_LOG2=0 H3_GID0=$((512 * 1024 + 256)) timeout 60 ./h3split 2000000000 $S 32 1 1); say "minb=$mb $o"; done
kill $SMI 2>/dev/null
say "CLOCK $(awk -F', ' '$2 > 50 {n++; s += $1} END {if (n) printf "sm_mhz_busy_mean=%.0f samples=%d", s / n, n; else print "sm_mhz_busy_mean=?"}' smi.csv)"
say "== thr certification: complete renders, N=32"
read -r TCHK TDONE <<< "$(grep '^workload=thr' $K/expect/thr.txt | sed -E 's/.*CHK0=([0-9A-F]+) DONE0=([0-9]+).*/\1 \2/')"
EST=$(awk -v r=$LONE 'BEGIN{printf "%.2f", 95038 / r}')
if [ "$WD" = 1 ] && awk -v e=$EST 'BEGIN{exit !(e > 1.5)}'; then
    say "SKIP thr certification: one kernel would take ~${EST}s under a display watchdog"
else
    for mb in 1 3 4; do
        o=$(H3_MINB=$mb timeout 60 ./h3thr 100000 32 128 1 1); say "$o"
        ok=$(python3 -c "import sys; sys.path.insert(0, '$K'); import check_h3 as c
print('MATCH' if '$(f "$o" CHK0)' == '$TCHK' and '$(f "$o" DONE0)' == '$TDONE' and '$(f "$o" ndone)' == '32'
      and '$(f "$o" AGG)' == '%016X' % c.fnv_rep(int('$TCHK', 16), 32) and '$(f "$o" CAGG)' == '%016X' % c.fnv_rep($TDONE, 32) else 'MISMATCH')")
        say "CERT-LOCAL thr minb=$mb N=32 $ok"
    done
fi
say "== split m=0: every tile, one-wave slices of $S"
rm -rf slices; mkdir slices; i=0; t0=$(date +%s)
for ((g = 0; g < 1048576; g += S)); do
    n=$(( 1048576 - g < S ? 1048576 - g : S ))
    H3_MINB=1 H3_TILE_LOG2=0 H3_GID0=$g H3_PERINST=slices/p_$i.txt H3_DUMP=slices/d_$i.bin H3_WMAX=3 \
        timeout 60 ./h3split 2000000000 $n 32 1 1 >> slices/farm.log 2>&1 || { say "slice $i FAILED"; break; }
    i=$((i + 1))
done
say "slices=$i wall=$(( $(date +%s) - t0 ))s kernel_secs_sum=$(grep -oE 'secs=[0-9.]+' slices/farm.log | cut -d= -f2 | awk '{s += $1} END {printf "%.2f", s}') errors=$(grep -c 'FARM-ERROR\|CUDA' slices/farm.log)"
for ((j = 0; j < i; j++)); do cat slices/p_$j.txt; done > gpu_m0.txt
for ((j = 0; j < i; j++)); do cat slices/d_$j.bin; done > gpu_m0_words.bin
say "$(python3 - $W/split/gsm_m0.txt $K <<'EOF'
import array, hashlib, sys
sys.path.insert(0, sys.argv[2]); import check_h3 as c
gpu = [l.split() for l in open('gpu_m0.txt')]; cpu = [l.split() for l in open(sys.argv[1])]
e = c.expectations()['split'][0]
agg = '%016X' % c.fnv_vec(int(r[1], 16) for r in gpu); cagg = '%016X' % c.fnv_vec(int(r[2]) for r in gpu)
w = array.array('I'); w.frombytes(open('gpu_m0_words.bin', 'rb').read())
img = bytearray(); bad = 0
for p in range(len(w) // 3):
    t, px, d = w[3 * p:3 * p + 3]
    bad += t != p or d != 0xffffffff or px >> 24
    img += bytes(((px >> 16) & 255, (px >> 8) & 255, px & 255))
md5 = hashlib.md5(b'P6\n1024 1024\n255\n' + bytes(img)).hexdigest()
ok = len(gpu) == 1 << 20 and gpu == cpu and agg == e['AGG'] and cagg == e.get('CAGG') and md5 == e['md5'] and not bad
print('CERT-LOCAL split m=0 %s tiles=%d table_rows_differing_from_cpu=%d AGG=%s CAGG=%s image_md5=%s bad_streams=%d'
      % ('MATCH' if ok else 'MISMATCH', len(gpu), sum(1 for a, b in zip(gpu, cpu) if a != b) + abs(len(gpu) - len(cpu)), agg, cagg, md5, bad))
EOF
)"
if [ -n "${LOCAL_SWEEP:-}" ]; then   # the rented-host pipeline itself: sweep_h3.sh + check_h3.py
    say "== sweep_h3.sh end to end (complete renders; whole-image launches take ~1 min on a T1000)"
    rm -rf sweep; mkdir sweep; cp $K/sweep_h3.sh sweep/; cp h3thr sweep/h3thr_gpu; cp h3split sweep/h3split_gpu
    python3 $K/check_h3.py --write-expect sweep/h3expect.txt > /dev/null || exit 1
    GN=$(echo "$D" | sed -E 's/.*name="(NVIDIA )?([^"]*)".*/\2/' | tr ' ' '_')
    { echo "DPH=0 GPU=${GN}x1-local"
      MINBS="1 3 4" VARIANT_NS=$((2 * S)) NLIST="1 32 $S $((2 * S))" MLIST="0 2 4" BLKS_SPLIT=32 bash sweep/sweep_h3.sh
    } > $K/results/local_sweep_$ARCH.log 2>&1
    python3 $K/check_h3.py $K/results/local_sweep_$ARCH.log | tail -2 | tee -a $LOG
fi
say "LOCAL-GPU-DONE"
