#!/bin/bash
# Packed 3D-logic word (l3dw) vs the current logic3d (int32/wire) and std_logic,
# same logical op sequence at matched wire counts (WIRES = NWORDS*8), on our-nvc
# --std=2040. Shows the packed word's speedup over the current 3D-logic rep.
# Correctness of l3dw is gated by test/regress/logic3dw1 in the nvc tree
# (intrinsic == VHDL body; 2-state == std_logic; X == logic3d LUT).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
N=${NVC:-/usr/local/src/nvc-build/bin/nvc}; L=/usr/local/src/nvc-build/lib
W=/home/claude/vhdl_bench/l3dwrun; rm -rf "$W"; mkdir -p "$W"; cd "$W"
run(){ local t0 t1; t0=$(date +%s%N); "$@" >/dev/null 2>&1; t1=$(date +%s%N)
       awk -v x=$((t1-t0)) 'BEGIN{printf "%.3f", x/1e9}'; }
best(){ local b=99 r t; for r in 1 2 3; do t=$(run "$@")
        awk -v t=$t -v b=$b 'BEGIN{exit !(t<b)}' && b=$t; done; echo "$b"; }
$N -L $L --std=2040 --work=w3 -a "$HERE/bench_l3d.vhd"  >/dev/null 2>&1
$N -L $L --std=2040 --work=wb -a "$HERE/bench_l3dw.vhd" >/dev/null 2>&1
$N -L $L --std=2008 --work=ws -a "$HERE/bench_slvm.vhd" >/dev/null 2>&1
printf "| wires | std_logic | logic3d | l3dw word | l3dw vs logic3d |\n"
printf "| --: | --: | --: | --: | --: |\n"
for nw in 1 4 16 128; do
  wires=$((nw*8))
  for p in "w3 2040 bench_l3d" "wb 2040 bench_l3dw" "ws 2008 bench_slvm"; do
    set -- $p; $N -L $L --std=$2 --work=$1 -e -gCYCLES=200000 -gNWORDS=$nw $3 >/dev/null 2>&1
    $N -L $L --std=$2 --work=$1 -r $3 >/dev/null 2>&1   # warm
  done
  t3=$(best $N -L $L --std=2040 --work=w3 -r bench_l3d)
  tb=$(best $N -L $L --std=2040 --work=wb -r bench_l3dw)
  ts=$(best $N -L $L --std=2008 --work=ws -r bench_slvm)
  printf "| %d | %ss | %ss | %ss | %sx |\n" "$wires" "$ts" "$t3" "$tb" \
     "$(awk -v a=$t3 -v b=$tb 'BEGIN{printf "%.2f", a/b}')"
done
