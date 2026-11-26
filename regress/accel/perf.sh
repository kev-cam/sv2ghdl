#!/bin/bash
# Per-cycle instruction cost, with fixed overhead differenced out.
#
# A single `perf stat` on an nvc run measures process start + elaboration load +
# dlopen + N cycles of simulation.  For a 2000-cycle run that fixed cost swamps
# the model, which makes the accel-vs-interp and accel-vs-Verilator ratios look
# far better or worse than they are.  So every engine is measured at TWO cycle
# counts and the model cost is the slope:
#
#     insn_per_cycle = (I_hi - I_lo) / (CYC_hi - CYC_lo)
#
# The three engines are measured back-to-back per case (interleaved arms), and
# instruction counts -- not wall clock -- are the metric, per the box's rules.
#
# usage: ./perf.sh [case ...]
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
# Build trees and the PRIVATE accel cache go under WORKROOT (never the nvc tree).
WORKROOT="${ACCELBENCH_WORK:-$HOME/accelbench-work}"
mkdir -p "$WORKROOT"
export HOME="$WORKROOT"
NVC="${NVC:-/usr/local/src/nvc-build/bin/nvc}"
VLIB="${NVC_LIBDIR:-/usr/local/src/nvc-build/lib}"
VLROOT="${VLROOT:-/usr/local/src/verilator-build/dest/usr/local/share/verilator}"
VERILATOR="${VERILATOR:-/usr/local/src/verilator-build/dest/usr/local/bin/verilator}"
export VERILATOR_ROOT="$VLROOT"; export OBJCACHE=
BUILD="$WORKROOT/pbuild"
LO=${LO:-2000}
HI=${HI:-22000}

CASES=(
  "wide_n8w256    wide  N=8 W=256   |"
  "wide_n8w1024   wide  N=8 W=1024  |"
  "wide_n16w1024  wide  N=16 W=1024 |"
  "wide_n8w2048   wide  N=8 W=2048  |"
  "fsm_n8         fsm   N=8         |"
  "fsm_n32        fsm   N=32        |"
  "fsm_n128       fsm   N=128       |"
  "deep_d8        deep  D=8 W=32    |"
  "deep_d32       deep  D=32 W=32   |"
  "deep_d128      deep  D=128 W=32  |"
  "regf_n8d16     regf  N=8 DEPTH=16 W=32 |"
  "regf_n8d32     regf  N=8 DEPTH=32 W=32 |"
  "regf_n16d32    regf  N=16 DEPTH=32 W=32 |"
  "many_k12       many  K=12 M=8    |"
  "many_k24       many  K=24 M=8    |"
  "act_lo_n8w256  act   N=8 W=256   | -gALLON=0 -GALLON=0"
  "act_hi_n8w256  act   N=8 W=256   | -gALLON=1 -GALLON=1"
  "act_lo_n16w512 act   N=16 W=512  | -gALLON=0 -GALLON=0"
  "act_hi_n16w512 act   N=16 W=512  | -gALLON=1 -GALLON=1"
)

sel=("$@")
want() { [ ${#sel[@]} -eq 0 ] && return 0; for s in "${sel[@]}"; do [ "$s" = "$1" ] && return 0; done; return 1; }
# Instructions retired by a command: `$PERF_I <command> 2>&1 | insn`.  perf prints a line for every
# PMU that counted, and a hybrid CPU (P and E cores) has two, `cpu_core/instructions/` and
# `cpu_atom/instructions/`, each with what ran on its kind of core: the total is their sum.  The
# old parse wanted one bare `instructions` line and found none there, so every count was empty.
# --no-scale, because by default perf scales each line up to an estimate of the whole run, and two
# estimates of the whole do not add up; -x, for bare numbers in a fixed shape (count, unit, event,
# time the counter ran, ...).  A `<not counted>` line (no time on that kind of core) is skipped.
# The event is spelt `instructions`, `instructions:u`, `cpu_core/instructions/`,
# `cpu_core/instructions/u` or, by perf 6.12 without the right to count the kernel,
# `cpu_core/instructions:u/`.
PERF_I="perf stat --no-scale -x, -e instructions"
insn() { awk -F, '$1 ~ /^[0-9]+$/ && $2 == "" && $3 ~ /^([a-z_0-9]+\/)?instructions(\/[a-zA-Z]*|:[a-zA-Z]+\/?)?$/ && $4 ~ /^[0-9]+$/ { s += $1; n++ } END { if (n) printf "%.0f\n", s }'; }

printf "%-15s %10s %10s %10s   %8s %8s\n" \
  CASE "interp/cyc" "accel/cyc" "vltr/cyc" "acc:int" "acc:vltr"
printf '%.0s-' {1..70}; echo

for row in "${CASES[@]}"; do
  name=$(echo "$row" | awk '{print $1}'); want "$name" || continue
  shape=$(echo "$row" | awk '{print $2}')
  params=$(echo "$row" | sed 's/|.*//' | awk '{$1="";$2="";print}')
  extra=$(echo "$row" | sed 's/.*|//')
  vgen=$(echo "$extra" | tr ' ' '\n' | grep '^-g' | tr '\n' ' ')
  sgen=$(echo "$extra" | tr ' ' '\n' | grep '^-G' | tr '\n' ' ')

  D="$BUILD/$name"; rm -rf "$D"; mkdir -p "$D"
  python3 "$HERE/gen.py" $shape "$D" $params >/dev/null || continue
  eval "$(sed 's/=/="/; s/$/"/' "$D/INFO")"
  W="$D/work"; A=(-M 512m -H 512m --std=2008 --work="$W" -L "$VLIB")
  ok=1; for f in $order; do $NVC "${A[@]}" -a "$D/$f" >/dev/null 2>&1 || ok=0; done
  [ $ok -eq 0 ] && { echo "$name ANALYSE FAIL"; continue; }
  # The bridge is compiled with nvc's OWN default (`gcc -g -O3`, model.c) unless
  # ACCEL_CC overrides.  This used to be hardcoded `NVC_ACCEL_CC=cc` -- plain cc
  # with NO -O flag -- which measured an unoptimised accel build against
  # `verilator -O3` and produced the withdrawn table in README.md.  The artifact
  # is shape-dependent (1.07x on deep, 4.53x on regf_n16d32), so it distorted the
  # comparison BETWEEN shapes, not just the overall level.  Set ACCEL_CC=cc to
  # reproduce the old numbers.
  AE=(NVC_ACCEL=1 NVC_ACCEL_JIT=1 NVC_ACCEL_FROM_VHDL=1)
  [ -n "${ACCEL_CC:-}" ] && AE+=("NVC_ACCEL_CC=$ACCEL_CC")

  declare -A I A_ V
  for C in $LO $HI; do
    $NVC "${A[@]}" -e $vgen -gCYC=$C "$tb" >/dev/null 2>&1 || { ok=0; break; }
    # warm the accel cache (compile) before timing
    env "${AE[@]}" $NVC "${A[@]}" -r "$tb" >/dev/null 2>&1
    VD="$D/v$C"
    # -CFLAGS -O3 to match what nvc gives its own bridge (`gcc -g -O3`).  At
    # -CFLAGS -O2 the comparison quietly favoured us by one optimisation level.
    $VERILATOR --binary -j 4 -Wno-fatal -O3 -CFLAGS "${VLCFLAGS:--O3}" --Mdir "$VD" -o vsim \
        --top-module "$tb" $sgen -GCYC=$C "$D/$sv" >/dev/null 2>&1 || { ok=0; break; }
    # interleaved arms, same case, back to back
    I[$C]=$($PERF_I $NVC "${A[@]}" -r "$tb" 2>&1 | insn)
    A_[$C]=$(env "${AE[@]}" $PERF_I $NVC "${A[@]}" -r "$tb" 2>&1 | insn)
    V[$C]=$($PERF_I "$VD/vsim" 2>&1 | insn)
  done
  [ $ok -eq 0 ] && { echo "$name BUILD FAIL"; continue; }

  read ipc apc vpc r1 r2 <<<"$(python3 -c "
dc=$HI-$LO
i=(${I[$HI]}-${I[$LO]})/dc; a=(${A_[$HI]}-${A_[$LO]})/dc; v=(${V[$HI]}-${V[$LO]})/dc
print('%.0f %.0f %.0f %.2f %.1f'%(i,a,v,i/a if a else 0, a/v if v else 0))")"
  printf "%-15s %10s %10s %10s   %7sx %7sx\n" "$name" "$ipc" "$apc" "$vpc" "$r1" "$r2"
done
