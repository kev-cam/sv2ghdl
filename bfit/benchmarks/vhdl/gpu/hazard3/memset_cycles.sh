#!/bin/bash
# memset_cycles.sh - how many cycles the split render removes by not zeroing
# the 4 MB image: upstream's COMMITTED benchmark firmware (sw/bin/mandelbrot_rv32imac,
# what the Verilator benchmark ran) on the farm SoC with a 2^21-word data RAM,
# gsm model on one CPU core with -DH3_PCTRACE: the first cycle at which the
# fetch address reaches mandelbrot()'s entry word is the whole pre-render time
# (reset, _start, main's prologue, memset of 4,194,304 bytes).  The same
# measurement on the throughput firmware (1 KB memset) gives the per-byte cost.
# bfit GPU-farm kit (PolyForm Noncommercial 1.0.0).
set -u
K=$(cd "$(dirname "$0")" && pwd); G=$(dirname "$K")
U=${HAZARD3_MANDELBROT_DIR:-$HOME/verilator-hazard3-mandelbrot-testbench}
W=${H3_WORK:-$HOME/gf_hz3/work}/memset; mkdir -p $W; cd $W
GSM=${GSM:-$HOME/gf_hz3/bin/gen_statemachine}
F=$(awk -v U=$U '/^HAZARD3_FILES :=/ {on=1; next} on && NF == 0 {exit} on {sub(/\\$/,""); if ($1!="") print U "/" $1}' $U/Makefile)
ELF=$U/sw/bin/mandelbrot_rv32imac
sym() { llvm-objdump-19 -t "$1" | awk -v s="$2" '$NF == s {print $1}'; }
MB=$(sym $ELF mandelbrot); MS=$(sym $ELF memset)
echo "upstream benchmark ELF: memset=0x$MS mandelbrot=0x$MB"
python3 $K/gen_farm_soc.py --template $U/soc.tmpl.v --elf $ELF --i-depth 128 --d-depth 2097152 -o soc_farm.v || exit 1
for f in $U/Hazard3/hdl/*.vh; do ln -sf $f .; done
GSM_U32=1 $GSM $W/soc_farm.v $F soc $W/model.c > gsm.log 2>&1 || { echo "gsm failed"; exit 1; }
python3 $G/strip_model.py model.c > model_s.c 2>/dev/null
python3 $K/mem_prep.py model_s.c > model_m.c 2>/dev/null
g++ -O2 -x c++ -DFARM_CPU -DH3_PCTRACE -DMODEL_C="\"$W/model_m.c\"" -o farm_pc $K/h3farm.cu || exit 1
# mandelbrot() entry fetch word (fetches are word-aligned)
EW=$(printf '%x' $(( 0x$MB & ~3 )))
ulimit -s unlimited
out=$(H3_PCS=$EW ./farm_pc 40000000 1); echo "$out" | grep PCHIT
PRE=$(echo "$out" | grep -oE 'first_cycle=[0-9-]+' | head -1 | cut -d= -f2)
# per-byte cost from the throughput firmware (memset of SIZE*SIZE*4 = 1024 bytes)
T=${H3_WORK:-$HOME/gf_hz3/work}/thr
g++ -O2 -x c++ -DFARM_CPU -DH3_PCTRACE -DMODEL_C="\"$T/model_m.c\"" -o farm_pc_thr $K/h3farm.cu || exit 1
TMB=$(sym $T/thr.elf mandelbrot)
TPRE=$(H3_PCS=$(printf '%x' $(( 0x$TMB & ~3 ))) ./farm_pc_thr 200000 1 | grep -oE 'first_cycle=[0-9-]+' | cut -d= -f2)
echo "MEMSET upstream_pre_render_cycles=$PRE (4194304-byte memset) thr_pre_render_cycles=$TPRE (1024-byte memset)" \
     "per_byte=$(awk "BEGIN{printf \"%.4f\", ($PRE-$TPRE)/(4194304-1024)}") of upstream's 4637655132 total = $(awk "BEGIN{printf \"%.3f%%\", 100*$PRE/4637655132}")"
