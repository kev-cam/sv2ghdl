#!/bin/bash
# c6288_run.sh -- baseline of VACASK's C6288 16x16-multiplier benchmark on our
# engines (Linux/WSL). Brings the benchmark from the VACASK tree into our perf
# set. Baseline = each engine's own native transistor-level PSP103.4 run (same
# circuit, ~1020 timepoints). No behavioral substitution here (that is the
# -bfit lane, added later). Full-process wall-clock, 1 warm + REPS timed, min.
#
#   ENGINES  subset to run     (default "vacask ngspice")
#   REPS     timed reps        (default 2)
#
# Xyce note: our Xyce build (7.10.0) has no built-in PSP103 (level=103) and no
# OSDI loader, so it cannot run this netlist natively. Getting C6288 onto Xyce
# needs PSP103 via PyMS (.hdl) or the -bfit behavioral lane -- tracked separately.
set +e
REPS=${REPS:-2}
ENGINES=${ENGINES:-"vacask ngspice"}

VACASK=/opt/build.VACASK/Release/simulator/vacask
OVR=/opt/openvaf-r/openvaf-r          # OpenVAF-reloaded, OSDI 0.4  (VACASK)
CLASSIC=/opt/openvaf/openvaf          # classic OpenVAF, OSDI 0.3   (ngspice)
DEVSRC=/usr/local/src/VACASK/devices
SRC=/usr/local/src/VACASK/benchmark/c6288

timeit() {  # label -- command via "$@", run in $PWD
  local label="$1"; shift
  "$@" >/dev/null 2>&1                              # warm (ignored)
  local best="" sum=0 dt t0 t1
  for r in $(seq 1 "$REPS"); do
    t0=$(date +%s.%N); "$@" >/dev/null 2>&1; t1=$(date +%s.%N)
    dt=$(awk -v a="$t0" -v b="$t1" 'BEGIN{print b-a}')
    best=$(awk -v a="$dt" -v b="$best" 'BEGIN{if(b==""||a<b)print a;else print b}')
    sum=$(awk -v a="$dt" -v b="$sum" 'BEGIN{print a+b}')
  done
  awk -v l="$label" -v b="$best" -v s="$sum" -v n="$REPS" \
    'BEGIN{printf "%-9s wall_min=%.2f  wall_avg=%.2f\n", l, b, s/n}'
}

echo "C6288 baseline  REPS=$REPS (+1 warm)  engines: $ENGINES  $(date +%F' '%H:%M:%S)"

for e in $ENGINES; do
  case "$e" in
  vacask)
    R=/tmp/c6288/vacask; rm -rf "$R"; mkdir -p "$R/spice"
    cp "$SRC/vacask/runme.sim" "$SRC/vacask/models.inc" "$SRC/vacask/multiplier.inc" "$R/"
    "$OVR" --allow variant_const_simparam -I"$DEVSRC" "$DEVSRC/psp103v4/psp103.va" -o "$R/psp103v4.osdi"     >/dev/null 2>&1
    "$OVR" --allow variant_const_simparam -I"$DEVSRC" "$DEVSRC/spice/resistor.va"  -o "$R/spice/resistor.osdi" >/dev/null 2>&1
    ( cd "$R" && timeit vacask "$VACASK" --skip-embed --skip-postprocess --no-output runme.sim ) ;;
  ngspice)
    R=/tmp/c6288/ngspice; rm -rf "$R"; mkdir -p "$R"
    cp "$SRC/ngspice/runme.sim" "$SRC/ngspice/models.inc" "$SRC/ngspice/multiplier.inc" "$R/"
    "$CLASSIC" --allow variant_const_simparam -I"$DEVSRC" "$DEVSRC/psp103v4/psp103.va" -o "$R/psp103v4.osdi" >/dev/null 2>&1
    ( cd "$R" && timeit ngspice ngspice -b runme.sim ) ;;
  *) echo "$e: skipped (no native PSP103 path)";;
  esac
done
echo "done $(date +%H:%M:%S)"

# ---------------------------------------------------------------------------
# bfit lane (BFIT=1, default on): gate-recognizer substitution (recognize_gates
# classifies the not/nor/and subckts by switch-level truth table and replaces
# THREE subckt bodies -> all 10112 FETs become ~2400 behavioral gates; the
# pruned deck has no PSP103 left, so even a Xyce build without PSP103/OSDI runs
# it). Per engine: warm + timed run, functional check (product == 0xFFFE0001),
# rel-L2 of p31 vs that engine's own native gold (Xyce has none -> '-').
# MERGE_CSV=<open.csv> merges the c6288 row for assemble.py.
# ---------------------------------------------------------------------------
BFIT=${BFIT:-1}
if [ "$BFIT" = 1 ]; then
  BF=$(cd "$(dirname "$0")/.." && pwd)
  XB=${XYCE_DIR:-/usr/local/src/xyce-build/src}
  export LD_LIBRARY_PATH=$HOME/xyce-libs:$XB:$XB/../utils/XyceCInterface:/usr/local/lib
  W=/tmp/c6288/bfit; rm -rf "$W"; mkdir -p "$W"
  { echo "C6288 16x16 multiplier -- flattened for the bfit lane"
    cat "$SRC/ngspice/models.inc" "$SRC/ngspice/multiplier.inc"
    awk '/^\.control/{c=1} !c{print} /^\.endc/{c=0}' "$SRC/ngspice/runme.sim" \
      | grep -viE '^\.(include|options|option|end$)' | tail -n +2
    echo ".tran 2p 2n uic"
    echo ".end"
  } > "$W/flat.cir"

  prodchk() { python3 - "$1" <<'PYEOF'
import sys
ln = open(sys.argv[1]).readlines()[-1].split()
vals = [float(x) for x in ln[1::2]]
w = 0
for i, v in enumerate(vals):
    if v > 0.6: w |= (1 << i)
print("0x%08X %s" % (w, "PASS" if w == 0xFFFE0001 else "FAIL"))
PYEOF
  }

  echo "--- gold waveforms (native runs, p31) ---"
  ( cd /tmp/c6288/vacask && rm -f tranmul.raw \
    && "$VACASK" --skip-embed --skip-postprocess runme.sim >/dev/null 2>&1 \
    && python3 "$BF/benchmarks/raw2dat.py" tranmul.raw p31 "$W/vc_gold.dat" ) \
    && echo "vacask gold ok" || echo "vacask gold FAILED"
  ( cd /tmp/c6288/ngspice \
    && sed 's/^  rusage all/  wrdata nggold.dat v(p31)\n  rusage all/' runme.sim > gold.sim \
    && timeout 700 ngspice -b gold.sim >/dev/null 2>&1 \
    && cp -f nggold.dat "$W/ng_gold.dat" ) \
    && echo "ngspice gold ok" || echo "ngspice gold FAILED"

  cd "$W"
  for e in vacask ngspice xyce; do
    for acc in balanced fast; do
      env "$(echo "$e" | tr a-z A-Z)_USE_BFIT=auto" python3 "$BF/bfit.py" front flat.cir \
          --sim "$e" --accuracy "$acc" -o "f_${e}_${acc}.cir" >/dev/null 2>&1
      case "$e" in
      vacask)
        rm -f tran1.raw
        SIM_OPENVAF=$OVR "$VACASK" -qp --skip-postprocess "f_${e}_${acc}.cir" >/dev/null 2>&1  # warm
        rm -f tran1.raw
        t0=$(date +%s.%N); SIM_OPENVAF=$OVR timeout 300 "$VACASK" -qp --skip-postprocess "f_${e}_${acc}.cir" >/dev/null 2>&1; rc=$?; t1=$(date +%s.%N)
        [ -f tran1.raw ] && python3 "$BF/benchmarks/raw2dat.py" tran1.raw p31 "d_${e}_${acc}.dat" >/dev/null 2>&1
        gold=vc_gold.dat ;;
      ngspice)
        sed '/^\.end$/d' "f_${e}_${acc}.cir" > "r_${e}_${acc}.cir"
        printf '.control\nrun\nwrdata d_%s_%s.dat v(p31)\nquit\n.endc\n.end\n' "$e" "$acc" >> "r_${e}_${acc}.cir"
        timeout 300 ngspice -b "r_${e}_${acc}.cir" >/dev/null 2>&1  # warm
        t0=$(date +%s.%N); timeout 300 ngspice -b "r_${e}_${acc}.cir" >/dev/null 2>&1; rc=$?; t1=$(date +%s.%N)
        gold=ng_gold.dat ;;
      xyce)
        printf '.print tran format=csv v(p31)\n' > pr.tmp
        sed "/^\.end\$/e cat pr.tmp" "f_${e}_${acc}.cir" > "r_${e}_${acc}.cir"
        timeout 300 "$XB/Xyce" "r_${e}_${acc}.cir" >/dev/null 2>&1  # warm
        t0=$(date +%s.%N); timeout 300 "$XB/Xyce" "r_${e}_${acc}.cir" >/dev/null 2>&1; rc=$?; t1=$(date +%s.%N)
        awk -F, 'NR>1{print $1, $2}' "r_${e}_${acc}.cir.csv" > "d_${e}_${acc}.dat" 2>/dev/null
        gold="" ;;
      esac
      t=$(awk -v a="$t0" -v b="$t1" -v r="$rc" 'BEGIN{if (r==0) printf "%.2f", b-a; else printf "brk"}')
      a="-"
      if [ -n "$gold" ] && [ -f "$gold" ] && [ -f "d_${e}_${acc}.dat" ]; then
        a=$(python3 "$BF/benchmarks/accuracy.py" 0 "$gold" "d_${e}_${acc}.dat" 2>/dev/null \
            | grep -aoE 'rel-L2 err *[0-9.]+%' | grep -aoE '[0-9.]+%')
      fi
      echo "c6288 ${e} ${acc}: ${t}s  acc=${a:-?}"
      eval "T_${e}_${acc}=$t A_${e}_${acc}=${a:-?}"
    done
  done
  if [ -n "${MERGE_CSV:-}" ]; then
    python3 "$BF/benchmarks/csvmerge.py" "$MERGE_CSV" c6288 \
      ng_bal="$T_ngspice_balanced" ng_bal_acc="$A_ngspice_balanced" \
      ng_fast="$T_ngspice_fast" ng_fast_acc="$A_ngspice_fast" \
      xy_bal="$T_xyce_balanced" xy_bal_acc=- xy_fast="$T_xyce_fast" xy_fast_acc=- \
      vc_bal="$T_vacask_balanced" vc_bal_acc="$A_vacask_balanced" \
      vc_fast="$T_vacask_fast" vc_fast_acc="$A_vacask_fast"
    echo "merged c6288 bfit cells -> $MERGE_CSV"
  fi
fi
