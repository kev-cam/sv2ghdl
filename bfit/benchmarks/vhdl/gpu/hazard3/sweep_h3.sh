#!/bin/bash
# sweep_h3.sh - ON THE RENTED HOST: certify and measure both Hazard3 workloads.
# bfit GPU-farm kit (PolyForm Noncommercial 1.0.0).  Shipped next to
# h3thr_gpu, h3split_gpu and h3expect.txt by vast_h3.sh (binary-only: no
# source, no toolchain on the host).  Every number it prints is re-checked on
# the build host by check_h3.py from the log; the CERT lines here are a
# convenience.  Uses only bash, awk, md5sum, nvidia-smi (also sampled for CLOCKS lines).
#   sweep_h3.sh            env: MINBS ("1 3 4") NLIST (thr N list, 1 .. 1M) MLIST ("0 2 4")
#                               BLKS_SPLIT ("32 128") SPLIT_EXTRA_MINB (0: split uses the best thr variant)
#                               VARIANT_NS ("65536 262144": N at which the kernel variant is chosen)
#                               TCYC (100000 >= the thr image's 95038 cycles: every
#                               thr point is a complete, certified render)
#                               BIN_THR/BIN_SPLIT (default ./h3thr_gpu ./h3split_gpu)
cd "$(dirname "$0")"
echo "SWEEP-START $(date -u +%FT%TZ) host=$(hostname)"
nvidia-smi --query-gpu=index,name,driver_version,clocks.max.sm,memory.total,power.limit --format=csv 2>/dev/null || echo "no nvidia-smi"
NG=$(nvidia-smi -L 2>/dev/null | wc -l); [ "$NG" -ge 1 ] || NG=1; echo "NGPU=$NG"
# actual SM clock / power under load (CLOCKS lines at the end): the projection scales per SM x GHz,
# and rented cards are often power- or clock-capped below clocks.max.sm
nvidia-smi --query-gpu=index,clocks.sm,power.draw,utilization.gpu --format=csv,noheader,nounits -lms 1000 > smi_clocks.csv 2>/dev/null & SMI=$!
BT=${BIN_THR:-./h3thr_gpu}; BS=${BIN_SPLIT:-./h3split_gpu}; chmod +x $BT $BS 2>/dev/null
TCYC=${TCYC:-100000}; MINBS=${MINBS:-"1 3 4"}; MLIST=${MLIST:-"0 2 4"}
NLIST=${NLIST:-"1 32 4096 16384 65536 262144 1048576"}     # N=1: one GPU thread (rule 4's per-instance rate)
read -r _ T_CHK T_DONE <<< "$(grep '^thr ' h3expect.txt)"
f() { echo "$1" | grep -oE "$2=[0-9A-Fa-f.e+-]+" | head -1 | cut -d= -f2; }
run() { local t0 t1 o; t0=$(date +%s.%N); o=$("$@" 2>&1); t1=$(date +%s.%N); echo "$o"; echo "WALL cmd_secs=$(awk "BEGIN{printf \"%.3f\", $t1-$t0}")"; }

echo "== thr cert (N=4096, 1 GPU, each kernel variant)"
best_mb=1; best=0
for mb in $MINBS; do
    o=$(H3_MINB=$mb run timeout 900 $BT $TCYC 4096 128 1 1); echo "$o"
    [ "$(f "$o" CHK0)" = "$T_CHK" ] && [ "$(f "$o" DONE0)" = "$T_DONE" ] && [ "$(f "$o" ndone)" = 4096 ] \
        && echo "CERT thr minb=$mb MATCH CHK0=$T_CHK DONE0=$T_DONE" || echo "CERT thr minb=$mb MISMATCH got CHK0=$(f "$o" CHK0) DONE0=$(f "$o" DONE0) ndone=$(f "$o" ndone)"
done
echo "== thr: kernel variant choice at N=${VARIANT_NS:-65536 262144} (1 GPU)"
for mb in $MINBS; do
    for N in ${VARIANT_NS:-65536 262144}; do
        o=$(H3_MINB=$mb timeout 900 $BT $TCYC $N 128 1 1); echo "$o"
        r=$(f "$o" agg_inst_cyc_per_s); [ -n "$r" ] && awk -v r=$r -v b=$best 'BEGIN{exit !(r>b)}' && { best=$r; best_mb=$mb; }
    done
done
echo "BEST thr minb=$best_mb agg=$best"
echo "== thr: N sweep, best variant, 1 GPU"
for N in $NLIST; do H3_MINB=$best_mb timeout 1800 $BT $TCYC $N 128 1 1; done
if [ $NG -gt 1 ]; then
    echo "== thr: all $NG GPUs (contiguous gid slices, one wall clock)"
    for N in 1048576 $((NG * 262144)) $((NG * 1048576)); do H3_MINB=$best_mb timeout 1800 $BT $TCYC $N 128 1 $NG; done
fi

echo "== split render: every tile of upstream's 1024x1024 image, N = NT tiles, all $NG GPU(s)"
# block 32: a block keeps its SM slot until its slowest tile ends, so small blocks waste less (project_h3.py)
for m in $MLIST; do
    read -r _ _ NT S_AGG S_CAGG S_MD5 <<< "$(grep "^split $m " h3expect.txt)"
    for mb in $best_mb $(echo $MINBS | tr ' ' '\n' | grep -vx $best_mb | head -${SPLIT_EXTRA_MINB:-0}); do
     for ilv in $([ $NG -gt 1 ] && echo "1 0" || echo 0); do     # multi-GPU: interleaved gids (balanced) and contiguous slices
      for blk in ${BLKS_SPLIT:-32 128}; do
        rm -f img.ppm
        o=$(H3_GPU_INTERLEAVE=$ilv H3_MINB=$mb H3_TILE_LOG2=$m H3_PPM=img.ppm run timeout 1800 $BS 2000000000 $NT $blk 1 $NG); echo "$o"
        md5=$(md5sum img.ppm 2>/dev/null | cut -d' ' -f1); echo "IMAGE-MD5 m=$m $md5"
        [ "$(f "$o" AGG)" = "$S_AGG" ] && [ "$(f "$o" CAGG)" = "$S_CAGG" ] && [ "$(f "$o" ndone)" = "$NT" ] && [ "$md5" = "$S_MD5" ] \
            && echo "CERT split m=$m minb=$mb block=$blk ilv=$ilv MATCH AGG=$S_AGG CAGG=$S_CAGG md5=$md5 secs=$(f "$o" secs)" \
            || echo "CERT split m=$m minb=$mb block=$blk ilv=$ilv MISMATCH AGG=$(f "$o" AGG) CAGG=$(f "$o" CAGG) ndone=$(f "$o" ndone) md5=$md5"
      done
     done
    done
done
kill $SMI 2>/dev/null
awk -F', *' '$4 + 0 > 50 {n[$1]++; s[$1] += $2; p[$1] += $3; if (!($1 in mn) || $2 < mn[$1]) mn[$1] = $2; if ($2 > mx[$1]) mx[$1] = $2}
    END {for (g in n) printf "CLOCKS gpu=%s busy_samples=%d sm_mhz_mean=%.0f min=%d max=%d power_w_mean=%.0f\n", g, n[g], s[g] / n[g], mn[g], mx[g], p[g] / n[g]}' smi_clocks.csv 2>/dev/null
echo "SWEEP-END $(date -u +%FT%TZ)"
