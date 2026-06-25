#!/bin/bash
# Find the optimum MPI rank count for Xyce on each circuit size.
# Sweeps np=2..16, measures serial Xyce for the speedup baseline, prunes once
# we are clearly past the optimum (3 consecutive worse/failed runs).
export PATH=/usr/bin:/bin:/usr/local/bin
W=/mnt/c/cygwin64/tmp/perfbench
GEN=/usr/local/src/sv2ghdl/bfit/benchmarks/gen_amp.py
XB=/usr/local/src/xyce-build/src
XBMPI=$HOME/xyce-build-mpi/src
SER_LD=$HOME/xyce-libs:$XB:$XB/../utils/XyceCInterface:/usr/local/lib
MPI_LD=$HOME/trilinos-mpi/lib:$XBMPI
SIZES="${SIZES:-3 30 100 300}"
TMO=${TMO:-200}

time_run() { local ld="$1"; shift; local t0 t1 rc
  t0=$(date +%s.%N)
  LD_LIBRARY_PATH="$ld" timeout "$TMO" "$@" >/tmp/m_$$.log 2>&1; rc=$?
  t1=$(date +%s.%N)
  if [ $rc -eq 124 ]; then echo "t/o"; return; fi
  if [ $rc -ne 0 ]; then echo "brk"; return; fi
  awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.2f", b-a}'; }

for n in $SIZES; do
  python3 "$GEN" "$n" > "$W/amp$n.cir"
  echo "=== N=$n ($n transistors) ==="
  ser=$(time_run "$SER_LD" "$XB/Xyce" -r "$W/amp$n.s.raw" "$W/amp$n.cir")
  echo "  serial Xyce: ${ser}s"
  best=""; bestnp=""; worse=0
  for np in $(seq 2 16); do
    t=$(time_run "$MPI_LD" mpiexec --bind-to none -np "$np" "$XBMPI/Xyce" -r "$W/amp$n.m.raw" "$W/amp$n.cir")
    printf "  np=%-2d %ss\n" "$np" "$t"
    case "$t" in
      t/o|brk) worse=$((worse+1));;
      *) if [ -z "$best" ] || awk -v a="$t" -v b="$best" 'BEGIN{exit !(a<b)}'; then
           best="$t"; bestnp="$np"; worse=0
         else worse=$((worse+1)); fi;;
    esac
    [ "$worse" -ge 3 ] && { echo "  (past optimum -> stop)"; break; }
  done
  if [ -n "$best" ]; then
    sp=$(awk -v s="$ser" -v b="$best" 'BEGIN{printf "%.1f", s/b}')
    echo "  >>> BEST ${best}s at np=$bestnp  (x${sp} vs serial ${ser}s)"
  else
    echo "  >>> MPI never completed under ${TMO}s (serial ${ser}s)"
  fi
done
rm -f /tmp/m_$$.log
