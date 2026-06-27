#!/bin/bash
# xrun_model.sh <mode> <model.cir> [tmo]
# Runs ONE Linux engine on ONE model netlist; prints min-ish inner wall-clock
# seconds, or "brk"/"t/o"/"na". mode: ngspice | ngspice_bfit | xyce | xyce_bfit | xyce_mpiN
export PATH=/usr/bin:/bin:/usr/local/bin
M=/mnt/c/cygwin64/tmp/perfbench/models
mode="$1"; cir="$2"; tmo="${3:-180}"; base="${cir%.cir}"
XB=/usr/local/src/xyce-build/src
XBMPI=$HOME/xyce-build-mpi/src
export LD_LIBRARY_PATH=$HOME/xyce-libs:$XB:$XB/../utils/XyceCInterface:/usr/local/lib
BFIT=/usr/local/src/sv2ghdl/bfit/bfit.py
CACHE=/tmp/bfit_cache.json

ng_wrap() { sed '/^\.end$/d' "$1" > "$2"
  printf '.control\nset filetype=binary\nrun\nwrite %s.raw\nquit\n.endc\n.end\n' "${2%.cir}" >> "$2"; }

case "$mode" in
  ngspice)      ng_wrap "$M/$cir" "$M/${base}_ng.cir"; CMD="ngspice -b $M/${base}_ng.cir";;
  ngspice_bfit) env NGSPICE_USE_BFIT=auto python3 "$BFIT" front "$M/$cir" --sim ngspice --cache "$CACHE" -o "$M/${base}_bf.cir" >/dev/null 2>&1 || cp "$M/$cir" "$M/${base}_bf.cir"
                ng_wrap "$M/${base}_bf.cir" "$M/${base}_bfng.cir"; CMD="ngspice -b $M/${base}_bfng.cir";;
  xyce)         CMD="$XB/Xyce -r $M/${base}.raw $M/$cir";;
  xyce_bfit)    env XYCE_USE_BFIT=auto python3 "$BFIT" front "$M/$cir" --sim xyce --cache "$CACHE" -o "$M/${base}_bx.cir" >/dev/null 2>&1 || cp "$M/$cir" "$M/${base}_bx.cir"
                CMD="$XB/Xyce -r $M/${base}_bx.raw $M/${base}_bx.cir";;
  xyce_mpi*)    np="${mode#xyce_mpi}"; [ -z "$np" ] && np=4
                [ -x "$XBMPI/Xyce" ] || { echo na; exit 0; }
                export LD_LIBRARY_PATH=$HOME/trilinos-mpi/lib:$XBMPI:$LD_LIBRARY_PATH
                CMD="mpiexec --bind-to none -np $np $XBMPI/Xyce -r $M/${base}_m.raw $M/$cir";;
  *) echo na; exit 0;;
esac

timeout "$tmo" $CMD >/tmp/xrm_$$.log 2>&1; rc=$?
if [ $rc -eq 124 ]; then echo "t/o"; rm -f /tmp/xrm_$$.log; exit 0; fi
if [ $rc -ne 0 ]; then echo "brk"; rm -f /tmp/xrm_$$.log; exit 0; fi
# only a HARD abort counts as brk -- a bare "timestep too small" can be a benign
# warning the engine recovers from (it false-flagged ring_osc+bfit, which completes
# and oscillates). rc!=0 above already catches real aborts; match only fatal phrasing.
grep -qaiE 'fatal error|simulation.{0,4}aborted|run aborted|doAnalyses: *TRAN: *Timestep too small' /tmp/xrm_$$.log && { echo brk; rm -f /tmp/xrm_$$.log; exit 0; }
rm -f /tmp/xrm_$$.log
best=""
for _ in 1 2; do
  t0=$(date +%s.%N); timeout "$tmo" $CMD >/dev/null 2>&1; t1=$(date +%s.%N)
  dt=$(awk -v a="$t0" -v b="$t1" 'BEGIN{print b-a}')
  best=$(awk -v a="$dt" -v b="$best" 'BEGIN{if(b==""||a<b)print a;else print b}')
done
awk -v b="$best" 'BEGIN{printf "%.2f", b}'
