#!/bin/bash
# cert_h3.sh - CPU certification of both Hazard3 workloads (no GPU).  bfit
# GPU-farm kit (PolyForm Noncommercial 1.0.0).  Needs build_h3.sh first.
#   thr:   gsm CPU farm, one core: CHK0/DONE0 == expect/thr.txt (golden fold,
#          ref_cycles); Verilator twin: the same CHK0/DONE0.  Rates: best of 3.
#   split: for m in $MLIST: EVERY tile on the gsm CPU farm ($THREADS OpenMP
#          threads): AGG == the native AGG, union image md5 == upstream's;
#          then EVERY tile on the Verilator twin ($THREADS processes over gid
#          ranges): the per-tile table (CHK, done cycle, words) must be
#          identical to the gsm one.  The gsm CAGG / instance-cycle sum / max
#          done cycle become the GPU run's expected values (expect/split_cpu.txt).
#   cert_h3.sh [thr] [split]      env MLIST (default "0 2 4"), THREADS (default 8), NOVL=1 skips Verilator
set -u
K=$(cd "$(dirname "$0")" && pwd)
W=${H3_WORK:-$HOME/gf_hz3/work}; THREADS=${THREADS:-8}; MLIST=${MLIST:-"0 2 4"}
UP_MD5=693d2391e979a114a82af00b3e64e54c
LOG=$K/results/cert_cpu.log; mkdir -p $K/results
say() { echo "$*" | tee -a $LOG; }
field() { echo "$1" | grep -oE "$2=[0-9A-Fa-f.e+-]+" | head -1 | cut -d= -f2; }
say "== cert_h3.sh $(date -u +%FT%TZ) host=$(hostname) cpu=$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs)"

cert_thr() {
    cd $W/thr
    local exp; exp=$(grep '^workload=thr' $K/expect/thr.txt)
    local echk=$(field "$exp" CHK0) edone=$(field "$exp" DONE0) best=0 vbest=0 o v
    for r in 1 2 3; do
        o=$(OMP_NUM_THREADS=1 ./farm_cpu 200000 1); say "$o"
        best=$(awk -v a=$best -v b=$(field "$o" agg_inst_cyc_per_s) 'BEGIN{print (b>a)?b:a}')
        v=$(./obj_twin/Vtwin 200000 1); say "$v"
        vbest=$(awk -v a=$vbest -v b=$(field "$v" cyc_per_s) 'BEGIN{print (b>a)?b:a}')
    done
    local ok=MISMATCH
    [ "$(field "$o" CHK0)" = "$echk" ] && [ "$(field "$o" DONE0)" = "$edone" ] && [ "$(field "$v" CHK0)" = "$echk" ] && [ "$(field "$v" DONE0)" = "$edone" ] && ok=MATCH
    say "CERT-CPU thr $ok gsm CHK0=$(field "$o" CHK0) DONE0=$(field "$o" DONE0) verilator CHK0=$(field "$v" CHK0) DONE0=$(field "$v" DONE0) expected CHK0=$echk DONE0=$edone"
    say "RATE thr gsm_1core_cyc_per_s=$best verilator_twin_1core_cyc_per_s=$vbest"
}

cert_split() {
    cd $W/split
    for m in $MLIST; do
        local exp; exp=$(grep "^workload=split m=$m " $K/expect/split.txt)
        local nt=$(field "$exp" NT) eagg=$(field "$exp" AGG) echk=$(field "$exp" CHK0)
        local o; o=$(H3_TILE_LOG2=$m H3_PERINST=gsm_m$m.txt H3_PPM=gsm_m$m.ppm OMP_NUM_THREADS=$THREADS ./farm_cpu 2000000000 $nt); say "$o"
        local md5; md5=$(md5sum gsm_m$m.ppm | cut -d' ' -f1)
        local ok=MISMATCH
        [ "$(field "$o" AGG)" = "$eagg" ] && [ "$(field "$o" CHK0)" = "$echk" ] && [ "$md5" = "$UP_MD5" ] && [ "$(field "$o" ndone)" = "$nt" ] && ok=MATCH
        say "CERT-CPU split m=$m $ok AGG=$(field "$o" AGG) (native $eagg) image_md5=$md5 ndone=$(field "$o" ndone)/$nt"
        say "EXPECT split m=$m NT=$nt CAGG=$(field "$o" CAGG) inst_cyc=$(field "$o" inst_cyc) done_max=$(field "$o" done_max) done_min=$(field "$o" done_min) wall_${THREADS}threads=$(field "$o" secs)"
        if [ -z "${NOVL:-}" ]; then
            local t0; t0=$(date +%s); rm -f vl_m${m}_*.txt
            for p in $(seq 0 $((THREADS - 1))); do
                local a=$(( nt * p / THREADS )) b=$(( nt * (p + 1) / THREADS ))
                H3_TILE_LOG2=$m H3_GID0=$a H3_PERINST=vl_m${m}_$p.txt ./obj_twin/Vtwin 2000000000 $((b - a)) > vl_m${m}_$p.log &
            done
            wait
            cat $(for p in $(seq 0 $((THREADS - 1))); do echo vl_m${m}_$p.txt; done) > vl_m$m.txt
            local vok=MISMATCH; cmp -s vl_m$m.txt gsm_m$m.txt && vok=MATCH
            say "CERT-VERILATOR split m=$m $vok per-tile (chk, done, words) identical for $(wc -l < vl_m$m.txt)/$nt tiles ($(( $(date +%s)-t0 ))s on $THREADS processes)"
        fi
    done
}

for wl in ${@:-thr split}; do cert_$wl; done
say "CERT-H3-DONE"
