#!/bin/bash
# Open-engine side of the perf league table (-> benchmarks/perf.md, via assemble.py).
# Per model, sequentially (valid timing, no resource contention):
#   * ngspice & Xyce NATIVE base
#   * ng+bfit & xy+bfit at  --accuracy balanced  AND  fast
#     (rel-L2 accuracy vs that engine's OWN native base waveform)
#   * Xyce-MPI np 2/4/8/16 sweep: best np that BEATS serial, killed once a run
#     passes the serial wall-clock (a slower-than-serial run has already lost).
#   * the 3000-stage "breaker" cascade (capacity row; native engines only).
# Writes $MODELS/open.csv for assemble.py. Paths are env-overridable.
#
#   bash model_bench.sh                  # full suite (~1 h; the breaker MPI dominates)
#   ROWS="inv_chain" DO_BREAKER=0 bash model_bench.sh    # quick smoke of one row
export PATH=/usr/bin:/bin:/usr/local/bin
HERE=$(cd "$(dirname "$0")" && pwd)
BFIT=${BFIT:-$HERE/../bfit.py}; GENM=$HERE/gen_models.py; GENA=$HERE/gen_amp.py; ACC=$HERE/accuracy.py
XYCE=${XYCE:-/usr/local/src/xyce-build/src/Xyce}
XYCE_LD=${XYCE_LD:-/usr/local/src/xyce-build/src}
XYCE_MPI=${XYCE_MPI:-$HOME/xyce-build-mpi/src/Xyce}
XYCE_MPI_LD=${XYCE_MPI_LD:-$HOME/xyce-build-mpi/src:$HOME/trilinos-mpi/lib:$HOME/trilinos-mpi/lib64}
MPIRUN=${MPIRUN:-mpirun --allow-run-as-root --oversubscribe}
OV=${OPENVAF:-/opt/openvaf/openvaf}
VACASK=${VACASK:-/opt/build.VACASK/Release/simulator/vacask}
OVR=${OPENVAF_R:-/opt/openvaf-r/openvaf-r}      # OSDI 0.4 (VACASK); $OV is 0.3 (ngspice)
DO_VC=${DO_VC:-1}                               # VACASK lane (vc_* columns)
DO_NGXY=${DO_NGXY:-1}                           # ngspice+Xyce lanes; 0 = VACASK-only refresh
export BFIT_NGSPICE=${BFIT_NGSPICE:-$(command -v ngspice)}
export PYMS_DIR=${PYMS_DIR:-/usr/local/src/xyce/utils/PyMS}
export XYCE_SRC=${XYCE_SRC:-/usr/local/src/xyce/src} XYCE_BUILD=${XYCE_BUILD:-/usr/local/src/xyce-build}
M=${MODELS:-/mnt/c/cygwin64/tmp/perfbench/models}
W=${WORK:-/tmp/mbench}; rm -rf "$W"; mkdir -p "$W"; cd "$W" || exit 1
OUTCSV=${OUTCSV:-$M/open.csv}
ROWS=${ROWS:-"rectifier inv_chain ring_osc ota_5t bjt_amp opamp"}
DO_BREAKER=${DO_BREAKER:-1}

python3 "$GENM" "$M" >/dev/null 2>&1
python3 "$GENA" 3000 | sed 's/^\.tran .*/.tran 20n 200u 0 20n/' > "$M/breaker.cir"
if [ "$DO_VC" = 1 ]; then   # macromodel OSDI for the VACASK lane -- compile time
  for _lv in ce_stage/ce_stage cmos_inv/cmos_inv bridge_rect/bridge; do   # stays OUTSIDE the timed runs
    "$OVR" "$HERE/../library/${_lv}.va" -o "$HERE/../library/${_lv}.osdi" >/dev/null 2>&1
  done
fi
declare -A OUT=( [rectifier]=out [inv_chain]=out [ring_osc]=n0 [ota_5t]=out [bjt_amp]=out [opamp]=no [breaker]=out )
declare -A FRQ=( [rectifier]=60 [inv_chain]=1e7 [ring_osc]=1e8 [ota_5t]=1e6 [bjt_amp]=1e4 [opamp]=3e5 [breaker]=1e4 )

wall(){ awk -v a=$1 -v b=$2 'BEGIN{printf "%.2f",b-a}'; }
lt(){ awk -v x=$1 -v y=$2 'BEGIN{exit !(x+0<y+0)}'; }
rl2(){ python3 "$ACC" "$1" "$2" "$3" 2>/dev/null | grep -aoE 'rel-L2 err *[0-9.]+%' | grep -aoE '[0-9.]+%'; }

ng_run(){ # deck node outfile -> secs|brk ; writes outfile (time value)
  sed '/^\.end$/d' "$1" > _d.cir
  { echo '.control'; for o in *.osdi; do [ -e "$o" ] && echo "pre_osdi $W/$o"; done
    echo run; echo "wrdata $3 v($2)"; echo quit; echo .endc; echo .end; } >> _d.cir
  local t0=$(date +%s.%N); LD_LIBRARY_PATH=$XYCE_LD timeout ${TMO:-700} ngspice -b _d.cir >/tmp/ng.log 2>&1; local rc=$?; local t1=$(date +%s.%N)
  { [ $rc -eq 124 ] || grep -qaiE 'fatal|aborted|too small|iteration limit' /tmp/ng.log; } && { echo brk; return; }
  wall $t0 $t1; }
xy_run(){ # deck node outfile -> secs|brk ; writes outfile (time value)
  sed "s/^\.end/.print tran format=csv V($2)\n.end/" "$1" > _x.cir
  rm -rf /tmp/mb_cache; export PYMS_CACHE=/tmp/mb_cache XYCE_VA_PATH=$W
  local t0=$(date +%s.%N); LD_LIBRARY_PATH=$XYCE_LD timeout ${TMO:-700} "$XYCE" _x.cir >/tmp/xy.log 2>&1; local rc=$?; local t1=$(date +%s.%N)
  [ $rc -ne 0 ] && { echo brk; return; }
  awk -F, 'NR>1{print $1,$NF}' _x.cir.csv > "$3" 2>/dev/null
  wall $t0 $t1; }
xy_mpi(){ # deck node np cap -> secs|brk
  [ -x "$XYCE_MPI" ] || { echo brk; return; }
  sed "s/^\.end/.print tran format=csv V($2)\n.end/" "$1" > _m$3.cir
  local t0=$(date +%s.%N)
  LD_LIBRARY_PATH=$XYCE_MPI_LD timeout $4 $MPIRUN -x LD_LIBRARY_PATH -np $3 "$XYCE_MPI" _m$3.cir >/tmp/mpi.log 2>&1
  local rc=$?; local t1=$(date +%s.%N); rm -f _m$3.cir.csv
  [ $rc -ne 0 ] && { echo brk; return; }
  wall $t0 $t1; }
vc_run(){ # deck.sim node outfile -> secs|brk|t/o ; deck is already VACASK syntax.
  # SIM_OPENVAF lets VACASK self-compile any .va the deck loads; the lane
  # pre-compiles with $OVR so the timed run only loads cached .osdi.
  local t0=$(date +%s.%N); SIM_OPENVAF=$OVR timeout ${TMO:-700} "$VACASK" -qp --skip-postprocess "$1" >/tmp/vc.log 2>&1; local rc=$?; local t1=$(date +%s.%N)
  [ $rc -eq 124 ] && { echo "t/o"; return; }
  { [ $rc -ne 0 ] || grep -qaiE 'error|singular|too small' /tmp/vc.log; } && { echo brk; return; }
  python3 "$HERE/raw2dat.py" tran1.raw "$2" "$3" >/dev/null 2>&1 || { echo brk; return; }
  wall $t0 $t1; }
# best np that beats serial (cap = serial wall-clock + 2s grace). 'mode=small'
# early-exits when np2 & np4 both lose (more ranks are strictly worse on a tiny
# circuit); 'mode=full' (breaker) runs all np -- a 1-D cascade can win at high np.
mpi_sweep(){ local deck=$1 node=$2 base=$3 mode=$4; local best=999999 bnp=0
  [ "$base" = brk ] && { echo "brk 0"; return; }
  local cap=$(awk -v b=$base 'BEGIN{c=b+2; if(c<10)c=10; if(c>900)c=900; printf "%d",c}')
  for np in 2 4 8 16; do
    local t=$(xy_mpi "$deck" $node $np $cap)
    [ "$t" != brk ] && lt "$t" "$best" && { best=$t; bnp=$np; }
    [ "$mode" = small ] && [ "$np" = 4 ] && [ "$bnp" = 0 ] && break
  done
  [ "$bnp" = 0 ] && best=brk
  echo "$best $bnp"; }

# Rows are MERGED into $OUTCSV per model (csvmerge.py), so the ng/xy and vc
# lanes can be refreshed independently (DO_NGXY=0 / DO_VC=0) without
# clobbering the other lane's columns.
run_row(){ local m=$1 mode=$2 node=${OUT[$m]} F=${FRQ[$m]}
  cp "$M/$m.cir" .; rm -f *.va *.osdi
  local args=()
  if [ "$DO_NGXY" = 1 ]; then
  local ngb=$(ng_run $m.cir $node gold.dat)
  local xyb=$(xy_run $m.cir $node xygold.dat)
  local ngbal=- ngbalA=- ngfast=- ngfastA=- xybal=- xybalA=- xyfast=- xyfastA=-
  for acc in balanced fast; do
    rm -f *.va *.osdi
    NGSPICE_USE_BFIT=auto python3 "$BFIT" front $m.cir --sim ngspice --accuracy $acc -o n_$acc.cir >/dev/null 2>&1
    for va in *.va; do [ -e "$va" ] && $OV "$va" >/dev/null 2>&1; done
    local nt=$(ng_run n_$acc.cir $node nd.dat); local na=-; { [ "$nt" != brk ] && [ "$ngb" != brk ]; } && na=$(rl2 $F gold.dat nd.dat)
    rm -f *.va *.osdi
    XYCE_USE_BFIT=auto python3 "$BFIT" front $m.cir --sim xyce --accuracy $acc -o x_$acc.cir >/dev/null 2>&1
    local xt=$(xy_run x_$acc.cir $node xd.dat); local xa=-; { [ "$xt" != brk ] && [ "$xyb" != brk ]; } && xa=$(rl2 $F xygold.dat xd.dat)
    if [ "$acc" = balanced ]; then ngbal=$nt; ngbalA=$na; xybal=$xt; xybalA=$xa
    else ngfast=$nt; ngfastA=$na; xyfast=$xt; xyfastA=$xa; fi
  done
  local mb mnp; read mb mnp < <(mpi_sweep "$M/$m.cir" $node "$xyb" "$mode")
  args+=(ng_base="$ngb" xy_base="$xyb" \
         ng_bal="$ngbal" ng_bal_acc="${ngbalA:-?}" ng_fast="$ngfast" ng_fast_acc="${ngfastA:-?}" \
         xy_bal="$xybal" xy_bal_acc="${xybalA:-?}" xy_fast="$xyfast" xy_fast_acc="${xyfastA:-?}" \
         mpi_best="$mb" mpi_np="$mnp")
  fi
  if [ "$DO_VC" = 1 ]; then
  rm -f *.va *.osdi tran1.raw
  python3 "$HERE/../sp2vc.py" $m.cir > vb.sim
  local vcb=$(vc_run vb.sim $node vcgold.dat)
  local vcbal=- vcbalA=- vcfast=- vcfastA=-
  for acc in balanced fast; do
    rm -f *.va
    VACASK_USE_BFIT=auto python3 "$BFIT" front $m.cir --sim vacask --accuracy $acc -o v_$acc.sim >/dev/null 2>&1
    for va in *.va; do [ -e "$va" ] && "$OVR" "$va" -o "${va%.va}.osdi" >/dev/null 2>&1; done
    rm -f tran1.raw
    local vt=$(vc_run v_$acc.sim $node vd.dat); local vA=-
    { [ "$vt" != brk ] && [ "$vt" != "t/o" ] && [ "$vcb" != brk ] && [ "$vcb" != "t/o" ]; } && vA=$(rl2 $F vcgold.dat vd.dat)
    if [ "$acc" = balanced ]; then vcbal=$vt; vcbalA=$vA; else vcfast=$vt; vcfastA=$vA; fi
  done
  args+=(vc_base="$vcb" vc_bal="$vcbal" vc_bal_acc="${vcbalA:-?}" vc_fast="$vcfast" vc_fast_acc="${vcfastA:-?}")
  fi
  python3 "$HERE/csvmerge.py" "$OUTCSV" "$m" "${args[@]}"
  echo "$m: ${args[*]}"
}
for m in $ROWS; do run_row $m small; done
if [ "$DO_BREAKER" = 1 ]; then       # capacity row: native engines only, no bfit
  node=${OUT[breaker]}
  args=()
  if [ "$DO_NGXY" = 1 ]; then
    ngb=$(ng_run "$M/breaker.cir" $node bg.dat)
    xyb=$(xy_run "$M/breaker.cir" $node bxg.dat)
    read mb mnp < <(mpi_sweep "$M/breaker.cir" $node "$xyb" full)
    args+=(ng_base="$ngb" xy_base="$xyb" mpi_best="$mb" mpi_np="$mnp" \
           ng_bal=- ng_bal_acc=- ng_fast=- ng_fast_acc=- xy_bal=- xy_bal_acc=- xy_fast=- xy_fast_acc=-)
  fi
  if [ "$DO_VC" = 1 ]; then
    python3 "$HERE/../sp2vc.py" "$M/breaker.cir" > vbrk.sim
    vcb=$(vc_run vbrk.sim $node vbg.dat)
    args+=(vc_base="$vcb" vc_bal=- vc_bal_acc=- vc_fast=- vc_fast_acc=-)
  fi
  python3 "$HERE/csvmerge.py" "$OUTCSV" breaker "${args[@]}"
  echo "breaker: ${args[*]}"
fi
echo "=== DONE -> $OUTCSV ==="
