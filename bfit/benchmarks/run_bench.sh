#!/bin/bash
# run_bench.sh -- reusable cross-engine performance table for bfit.
#
# RUN FROM CYGWIN (it needs to launch both native-Windows engines and, via
# wsl.exe, the Linux engines). Sweeps an N-stage BJT amplifier cascade
# (gen_amp.py) across every engine on this box and writes perf.md + perf.csv.
#
#   SIZES   stage counts to sweep        (default "3 30 300")
#   REPS    timed reps, Linux engines    (default 2)
#   MPINP   ranks for Xyce-parallel cell (default 4)
#   ENGINES restrict engine list         (default: all detected)
#
# Methodology: each cell is the time the engine spent simulating, launch
# overhead excluded. Windows engines (QSPICE, LTspice) report their own
# "Total elapsed time"; Linux engines (ngspice, Xyce, Xyce-MPI) are timed by
# inner wall-clock inside WSL (min of REPS). Both exclude the cross-environment
# process launch, so the numbers are comparable.
set -u
WORK=/cygdrive/c/cygwin64/tmp/perfbench
mkdir -p "$WORK"
HERE=$(cd "$(dirname "$0")" && pwd)
GEN="$HERE/gen_amp.py"; [ -f "$GEN" ] || GEN=/tmp/gen_amp.py
WSL=/cygdrive/c/Windows/System32/wsl.exe
XRUN_WSL=/tmp/xrun.sh                       # deployed into WSL below

SIZES=${SIZES:-"3 30 100 300"}; REPS=${REPS:-2}; MPINP=${MPINP:-4}; TMO=${TMO:-300}

QDIR="/cygdrive/c/Program Files/QSPICE"
QSPICE="$QDIR/QSPICE64.real.exe"; [ -f "$QSPICE" ] || QSPICE="$QDIR/QSPICE64.exe"
LTSPICE="/cygdrive/c/Program Files/ADI/LTspice/LTspice.exe"
SIMETRIX="/cygdrive/c/Program Files/SIMetrix-SIMPLIS-Elements_920/bin64/SIMetrix.exe"

# deploy the WSL helper into WSL /tmp via the shared perfbench dir
cp -f "$HERE/xrun.sh" "$WORK/xrun.sh" 2>/dev/null
$WSL -- bash -lc "cp -f /mnt/c/cygwin64/tmp/perfbench/xrun.sh $XRUN_WSL; chmod +x $XRUN_WSL" 2>/dev/null

detect() {
  local e=()
  [ -f "$QSPICE" ] && e+=(qspice)
  [ -f "$SIMETRIX" ] && e+=(simetrix)
  e+=(ngspice ngspice_bfit xyce xyce_bfit xyce_mpi)
  [ -f "$LTSPICE" ] && e+=(ltspice)
  echo "${e[@]}"
}
ENGINES=${ENGINES:-"$(detect)"}

# --- Windows-native engines: run once, parse engine-reported elapsed ---
# emit the float if found; else "brk" if the log shows a convergence death; else "fail"
verdict() { local f="$1" t
  t=$(awk 'tolower($0)~/total elapsed time/{for(i=1;i<=NF;i++)if($i+0>0){printf "%.2f",$i;exit}}' "$f" 2>/dev/null)
  if [ -n "$t" ]; then echo "$t"; return; fi
  grep -qaiE 'too small|no convergence|singular|fatal|abort' "$f" 2>/dev/null && echo brk || echo fail; }
run_qspice() { local cir="$1"; ( cd "$WORK" && timeout "$TMO" "$QSPICE" "$cir" -binary -r "${cir%.cir}.qraw" -o "${cir%.cir}.qout" >/dev/null 2>&1 )
  verdict "$WORK/${cir%.cir}.qout"; }
run_ltspice() { local cir="$1"; cp "$WORK/$cir" "$WORK/${cir%.cir}.net"
  ( cd "$WORK" && timeout "$TMO" "$LTSPICE" -b -Run "${cir%.cir}.net" >/dev/null 2>&1 )
  verdict "$WORK/${cir%.cir}.log"; }
run_simetrix() { echo na; }   # GUI-bound here; no headless netlist entry point

# --- Linux engines via the WSL helper ---
run_lin() { $WSL -- bash $XRUN_WSL "$1" "$2" "$REPS" "$MPINP" "$TMO" 2>/dev/null | tr -d '\0\r' | tail -1; }
run_ngspice()      { run_lin ngspice "$1"; }
run_ngspice_bfit() { run_lin ngspice_bfit "$1"; }
run_xyce()         { run_lin xyce "$1"; }
run_xyce_bfit()    { run_lin xyce_bfit "$1"; }
run_xyce_mpi()     { run_lin xyce_mpi "$1"; }

dispatch() { case "$1" in
  qspice) run_qspice "$2";; ltspice) run_ltspice "$2";; simetrix) run_simetrix "$2";;
  *) run_$1 "$2";; esac; }

echo "engines: $ENGINES"; echo "sizes: $SIZES  reps: $REPS  mpi np: $MPINP"
declare -A T
for n in $SIZES; do
  cir="amp$n.cir"; python3 "$GEN" "$n" > "$WORK/$cir"
  echo "=== N=$n ($(grep -cE '^Q[0-9]' "$WORK/$cir") transistors) ==="
  for e in $ENGINES; do v=$(dispatch "$e" "$cir"); [ -z "$v" ] && v=fail; T["$n,$e"]=$v; echo "    $e: $v"; done
done

# --- emit table ---
order="qspice ltspice ngspice ngspice_bfit xyce xyce_bfit xyce_mpi"
hdr() { case $1 in qspice)echo QSPICE;; ltspice)echo LTspice;; simetrix)echo SIMetrix;;
  ngspice)echo ngspice;; ngspice_bfit)echo "ngspice+bfit";; xyce)echo Xyce;;
  xyce_bfit)echo "Xyce+bfit";; xyce_mpi)echo "Xyce -np$MPINP";; esac; }
cols=""; for e in $order; do case " $ENGINES " in *" $e "*) cols="$cols $e";; esac; done

CSV="$WORK/perf.csv"; MD="$WORK/perf.md"
{ printf "stages,transistors"; for e in $cols; do printf ",%s" "$(hdr $e)"; done; echo
  for n in $SIZES; do printf "%s,%s" "$n" "$n"; for e in $cols; do printf ",%s" "${T[$n,$e]:-}"; done; echo; done; } > "$CSV"
{ echo "# bfit cross-engine performance"; echo
  echo "N-stage common-emitter BJT amplifier cascade (\`gen_amp.py\`), \`.tran 20n 2m\`."
  echo "Each cell is engine **simulation time in seconds** (lower is better) — Windows"
  echo "engines self-report their \"Total elapsed time\"; Linux engines are inner"
  echo "wall-clock, min of $REPS. Cross-environment process launch is excluded."; echo
  echo "The **+bfit** columns substitute the portable \`ce_stage\` Verilog-AMS macromodel"
  echo "and take adaptive timesteps. \`brk\` = engine aborted (timestep collapse on the"
  echo "stiff deep cascade); \`t/o\` = exceeded ${TMO}s."; echo
  printf "| Stages | Transistors |"; for e in $cols; do printf " %s |" "$(hdr $e)"; done; echo
  printf '%s' "| ---: | ---: |"; for e in $cols; do printf '%s' " ---: |"; done; echo
  for n in $SIZES; do printf "| %s | %s |" "$n" "$n"; for e in $cols; do printf " %s |" "${T[$n,$e]:-—}"; done; echo; done
  echo; echo "SIMetrix is installed but GUI-bound here (no headless netlist entry"
  echo "point), so it is not in the timed set."; echo
  echo "_Generated by \`benchmarks/run_bench.sh\`._"; } > "$MD"

echo; echo "wrote $MD"; echo; cat "$MD"
# copy results into the WSL bfit/benchmarks tree
$WSL -- bash -lc "cp -f /mnt/c/cygwin64/tmp/perfbench/perf.md /mnt/c/cygwin64/tmp/perfbench/perf.csv /usr/local/src/sv2ghdl/bfit/benchmarks/ 2>/dev/null" 2>/dev/null
