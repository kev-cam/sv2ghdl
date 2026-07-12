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
