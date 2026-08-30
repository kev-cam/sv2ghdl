#!/bin/bash
# accelbench runner: build + verify every design in every mode, report a table.
#
#   MODES
#     interp    nvc, no accel                      (the gold reference)
#     accel     nvc --accel, DEFAULT admission gate (no MIN_MODULES knob)
#     perinst   nvc --accel, NVC_ACCEL_PER_INSTANCE=1 (+MIN_MODULES=1)
#     vltr      Verilator --binary on the matched SystemVerilog
#
# All four must report the same Y= checksum.  Timing is `perf stat -e
# instructions` (wall clock is not trustworthy on this box).
#
# usage: ./run.sh [-t] [case ...]      -t = also collect instruction counts
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
# Build trees and the PRIVATE accel cache (~/.cache/nvc/accel) go under WORKROOT.
# Default keeps them OUT of the nvc source tree; override with ACCELBENCH_WORK.
WORKROOT="${ACCELBENCH_WORK:-$HOME/accelbench-work}"
mkdir -p "$WORKROOT"
export HOME="$WORKROOT"                   # PRIVATE accel cache, not the shared one
NVC="${NVC:-/usr/local/src/nvc-build/bin/nvc}"
VLIB="${NVC_LIBDIR:-/usr/local/src/nvc-build/lib}"
# NOTE: /usr/local/src/verilator-build/verilator sets VERILATOR_ROOT=/usr/local/src/verilator
# but that tree has no include/verilated.mk, so --binary cannot link.  The working
# install prefix is verilator-build/dest (same build, 5.051 devel v5.050-60-g4262aea87).
VLROOT="${VLROOT:-/usr/local/src/verilator-build/dest/usr/local/share/verilator}"
VERILATOR="${VERILATOR:-/usr/local/src/verilator-build/dest/usr/local/bin/verilator}"
export VERILATOR_ROOT="$VLROOT"
export OBJCACHE=
BUILD="$WORKROOT/build"
TIME=0
[ "${1:-}" = "-t" ] && { TIME=1; shift; }

# ---- case list:  name  shape  "params"  "extra-generics"
CASES=(
  "wide_n8w256    wide  N=8 W=256   CYC=20000  |"
  "wide_n8w1024   wide  N=8 W=1024  CYC=20000  |"
  "wide_n16w1024  wide  N=16 W=1024 CYC=20000  |"
  "wide_n8w2048   wide  N=8 W=2048  CYC=20000  |"
  "fsm_n8         fsm   N=8         CYC=20000 |"
  "fsm_n32        fsm   N=32        CYC=20000 |"
  "fsm_n128       fsm   N=128       CYC=20000 |"
  "deep_d8        deep  D=8 W=32    CYC=20000 |"
  "deep_d32       deep  D=32 W=32   CYC=20000 |"
  "deep_d128      deep  D=128 W=32  CYC=20000 |"
  "regf_n8d16     regf  N=8 DEPTH=16 W=32 CYC=20000 |"
  "regf_n8d32     regf  N=8 DEPTH=32 W=32 CYC=20000 |"
  "regf_n16d32    regf  N=16 DEPTH=32 W=32 CYC=20000 |"
  "many_k12       many  K=12 M=8    CYC=20000 |"
  "many_k24       many  K=24 M=8    CYC=20000 |"
  "act_lo_n8w256  act   N=8 W=256   CYC=20000 | -gALLON=0 -GALLON=0"
  "act_hi_n8w256  act   N=8 W=256   CYC=20000 | -gALLON=1 -GALLON=1"
  "act_lo_n16w512 act   N=16 W=512  CYC=20000 | -gALLON=0 -GALLON=0"
  "act_hi_n16w512 act   N=16 W=512  CYC=20000 | -gALLON=1 -GALLON=1"
  "arst_n8        arst  N=8         CYC=20000 |"
  "arst_n32       arst  N=32        CYC=20000 |"
)

sel=("$@")
want() { [ ${#sel[@]} -eq 0 ] && return 0; for s in "${sel[@]}"; do [ "$s" = "$1" ] && return 0; done; return 1; }

getY() { grep -oE 'Y=[0-9]+' | tail -1 | cut -d= -f2; }
insn() { grep -oE '^[ ]*[0-9,]+[ ]+instructions' | head -1 | tr -d ' ,' | sed 's/instructions//'; }

printf "%-15s %-9s %-7s %-6s %-11s %-11s %-11s %-11s %s\n" \
  CASE CHUNKS CELLS REGS INTERP ACCEL PERINST VERILATOR VERDICT
printf '%.0s-' {1..104}; echo

# Cache persists by default so re-runs cost no synth.  Staleness is handled by
# the key, not by deletion: model.c hashes a cache-version byte, gen_statemachine's
# mtime, the top module name and the emitted Verilog into the .so name.  Set
# FRESHCACHE=1 to force a cold build (KEEPCACHE=1 is accepted for compatibility).
[ "${FRESHCACHE:-0}" = "1" ] && rm -rf "$WORKROOT/.cache/nvc/accel"

fails=0; suite_ok=(); suite_bad=()
for row in "${CASES[@]}"; do
  name=$(echo "$row" | awk '{print $1}')
  want "$name" || continue
  shape=$(echo "$row" | awk '{print $2}')
  params=$(echo "$row" | sed 's/|.*//' | awk '{$1="";$2="";print}')
  extra=$(echo "$row" | sed 's/.*|//')
  vgen=$(echo "$extra" | tr ' ' '\n' | grep '^-g' | tr '\n' ' ')
  sgen=$(echo "$extra" | tr ' ' '\n' | grep '^-G' | tr '\n' ' ')

  D="$BUILD/$name"
  rm -rf "$D"; mkdir -p "$D"
  python3 "$HERE/gen.py" $shape "$D" $params >/dev/null || { echo "$name GEN FAIL"; continue; }
  eval "$(sed 's/=/="/; s/$/"/' "$D/INFO")"

  W="$D/work"
  A=(-M 512m -H 512m --std=2008 --work="$W" -L "$VLIB")
  anafail=0
  for f in $order; do
    $NVC "${A[@]}" -a "$D/$f" >"$D/ana.log" 2>&1 || \
      { echo "$name ANALYSE FAIL $f"; tail -8 "$D/ana.log"; anafail=1; break; }
  done
  [ $anafail -eq 1 ] && continue
  $NVC "${A[@]}" -e $vgen "$tb" >"$D/elab.log" 2>&1 || { echo "$name ELAB FAIL"; tail -5 "$D/elab.log"; continue; }

  # The generated bridge is compiled with nvc's OWN default (`gcc -g -O3`,
  # src/rt/model.c) unless overridden.  This suite used to hardcode
  # `NVC_ACCEL_CC=cc`, i.e. plain cc with no -O flag at all -- which is not
  # what ships, and which distorted the first published table (see README:
  # the "8 of 19 rows lose to the interpreter" result was an -O0 artifact).
  # As a CORRECTNESS gate this matters too: -O0 and -O3 expose different
  # latent undefined behaviour in generated code.  Force a level with e.g.
  # ACCEL_CC='cc' (-O0) or ACCEL_CC='gcc -O2'.
  AE=(NVC_ACCEL=1 NVC_ACCEL_JIT=1 NVC_ACCEL_FROM_VHDL=1)
  [ -n "${ACCEL_CC:-}" ] && AE+=("NVC_ACCEL_CC=$ACCEL_CC")

  yi=$($NVC "${A[@]}" -r "$tb" 2>&1 | tee "$D/interp.log" | getY)
  env "${AE[@]}" $NVC "${A[@]}" -r "$tb" >"$D/accel.log" 2>&1
  ya=$(getY < "$D/accel.log")
  env "${AE[@]}" NVC_ACCEL_PER_INSTANCE=1 NVC_ACCEL_MIN_MODULES=1 \
      $NVC "${A[@]}" -r "$tb" >"$D/perinst.log" 2>&1
  yp=$(getY < "$D/perinst.log")

  chunks=$(grep -c 'accel installed' "$D/accel.log")
  # NOTE: gen_statemachine only prints cell/reg counts on a COLD synth; a case
  # whose netlist is already in the cache (e.g. act_hi after act_lo -- which is
  # the proof that they share one netlist) shows "cached".
  cells=$(grep -oE '[0-9]+ comb cells' "$D/accel.log" | grep -oE '[0-9]+' | paste -sd+ | bc 2>/dev/null)
  regs=$(grep -oE '[0-9]+ registers' "$D/accel.log" | grep -oE '[0-9]+' | paste -sd+ | bc 2>/dev/null)

  # ---- verilator
  VD="$D/vltr"; mkdir -p "$VD"
  yv=""
  if $VERILATOR --binary -j 4 -Wno-fatal -O3 -CFLAGS -O2 \
        --Mdir "$VD" -o vsim --top-module "$tb" $sgen "$D/$sv" >"$D/vltr.log" 2>&1; then
     yv=$("$VD/vsim" 2>&1 | tee "$D/vltr.run.log" | getY)
  fi

  v="OK"
  [ -z "$yi" ] && v="NO-GOLD"
  [ "$ya" != "$yi" ] && v="ACCEL-MISMATCH"
  [ "$yp" != "$yi" ] && v="${v/OK/}PERINST-MISMATCH"
  [ "$yv" != "$yi" ] && v="${v/OK/}VLTR-MISMATCH"
  [ "$chunks" = "0" ] && v="$v/NO-INSTALL"
  [ "$v" = "OK" ] && suite_ok+=("$name") || { suite_bad+=("$name:$v"); fails=$((fails+1)); }

  printf "%-15s %-9s %-7s %-6s %-11s %-11s %-11s %-11s %s\n" \
     "$name" "${chunks:-0}" "${cells:-cached}" "${regs:-cached}" "${yi:-?}" "${ya:-?}" "${yp:-?}" "${yv:-?}" "$v"

  if [ $TIME -eq 1 ]; then
    ti=$(perf stat -e instructions $NVC "${A[@]}" -r "$tb" 2>&1 | insn)
    ta=$(env "${AE[@]}" perf stat -e instructions $NVC "${A[@]}" -r "$tb" 2>&1 | insn)
    tv=$(perf stat -e instructions "$VD/vsim" 2>&1 | insn)
    printf "%-15s   insn: interp=%-14s accel=%-14s verilator=%-14s  accel_speedup=%s  vs_vltr=%s\n" \
      "" "${ti:-?}" "${ta:-?}" "${tv:-?}" \
      "$(python3 -c "print('%.2fx'%($ti/$ta))" 2>/dev/null)" \
      "$(python3 -c "print('%.1fx'%($ta/$tv))" 2>/dev/null)"
  fi
done

echo
echo "IN SUITE  (${#suite_ok[@]}): ${suite_ok[*]:-none}"
echo "EXCLUDED  (${#suite_bad[@]}): ${suite_bad[*]:-none}"
exit $fails
