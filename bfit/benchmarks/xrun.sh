#!/bin/bash
# xrun.sh -- WSL-side single-engine runner for the bfit perf table.
# Runs ONE Linux engine on ONE circuit and prints the min inner wall-clock
# (seconds, after one warm run) to stdout. Inner timing excludes wsl.exe
# launch, so it measures the engine, not the cross-environment hop.
#
#   xrun.sh <mode> <basename.cir> [reps] [mpinp]
#   mode: ngspice | ngspice_bfit | xyce | xyce_bfit | xyce_mpi
#
# Prints a float, or "fail" (engine returned nonzero) / "na" (unsupported).
set -u
export PATH=/usr/bin:/bin:/usr/local/bin
W=/mnt/c/cygwin64/tmp/perfbench
mode="$1"; cir="$2"; reps="${3:-2}"; mpinp="${4:-4}"; tmo="${5:-300}"
base="${cir%.cir}"
XB=/usr/local/src/xyce-build/src
XBMPI=$HOME/xyce-build-mpi/src
export LD_LIBRARY_PATH=$HOME/xyce-libs:$XB:$XB/../utils/XyceCInterface:/usr/local/lib
BFIT=/usr/local/src/sv2ghdl/bfit/bfit.py
CACHE=/tmp/bfit_cache.json
[ -f "$CACHE" ] || cat > "$CACHE" <<'EOF'
{"ce_stage": {"params": {"gain": 9.82, "Vlo": 0.45, "Vhi": 9.53, "Rout": 1321.0, "Rin": 100000.0, "fp": 6000.0}, "sim": "neutral"}}
EOF

ng_wrap() { sed '/^\.end$/d' "$1" > "$2"
  printf '.control\nset filetype=binary\nrun\nwrite %s.raw\nquit\n.endc\n.end\n' "${2%.cir}" >> "$2"; }

bfit_make() { # src sim out  -> substituted + timestep-relaxed netlist
  local var; case "$2" in xyce) var=XYCE_USE_BFIT;; ngspice) var=NGSPICE_USE_BFIT;; esac
  env "$var=auto" python3 "$BFIT" front "$1" --sim "$2" --cache "$CACHE" -o "$3.t" >/dev/null 2>&1 || cp "$1" "$3.t"
  sed -E 's/^\.tran .*/.tran 1u 2m/' "$3.t" > "$3"; rm -f "$3.t"; }

# build the command for the mode
case "$mode" in
  ngspice)      ng_wrap "$W/$cir" "$W/${base}_ng.cir"; CMD="ngspice -b $W/${base}_ng.cir";;
  ngspice_bfit) bfit_make "$W/$cir" ngspice "$W/${base}_bfng.cir"; ng_wrap "$W/${base}_bfng.cir" "$W/${base}_bfngr.cir"; CMD="ngspice -b $W/${base}_bfngr.cir";;
  xyce)         CMD="$XB/Xyce -r $W/${base}.raw $W/$cir";;
  xyce_bfit)    bfit_make "$W/$cir" xyce "$W/${base}_bf.cir"; CMD="$XB/Xyce -r $W/${base}_bf.raw $W/${base}_bf.cir";;
  xyce_mpi)     [ -x "$XBMPI/Xyce" ] || { echo na; exit 0; }
                export LD_LIBRARY_PATH=$HOME/trilinos-mpi/lib:$XBMPI:$LD_LIBRARY_PATH
                CMD="mpiexec --bind-to none -np $mpinp $XBMPI/Xyce -r $W/${base}_m.raw $W/$cir";;
  *) echo na; exit 0;;
esac

# warm + validate, with timeout; classify break vs timeout
timeout "$tmo" $CMD >/tmp/xr_$$.log 2>&1; rc=$?
if [ $rc -eq 124 ]; then echo "t/o"; rm -f /tmp/xr_$$.log; exit 0; fi
if [ $rc -ne 0 ]; then echo "brk"; rm -f /tmp/xr_$$.log; exit 0; fi
# ngspice aborts the transient on Δt→0 but still exits 0 -- catch it in the log
if grep -qaiE 'time ?step too small|step too small|no convergence|iteration limit|fatal' /tmp/xr_$$.log; then
  echo "brk"; rm -f /tmp/xr_$$.log; exit 0; fi
rm -f /tmp/xr_$$.log
best=""
for _ in $(seq 1 "$reps"); do
  t0=$(date +%s.%N); timeout "$tmo" $CMD >/dev/null 2>&1; t1=$(date +%s.%N)
  dt=$(awk -v a="$t0" -v b="$t1" 'BEGIN{print b-a}')
  best=$(awk -v a="$dt" -v b="$best" 'BEGIN{if(b==""||a<b)print a;else print b}')
done
awk -v b="$best" 'BEGIN{printf "%.2f", b}'
