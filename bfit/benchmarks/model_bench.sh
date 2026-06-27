#!/bin/bash
# Linux side of the perf league table: per model, base ngspice/Xyce times +
# bfit acceleration (time -> speedup, and rel-L2 accuracy vs the ngspice base).
# Handles the cmout VA toolchain (Xyce: .hdl/PyMS; ngspice: OpenVAF .osdi).
export PATH=/usr/bin:/bin:/usr/local/bin
export LD_LIBRARY_PATH=/usr/local/src/xyce-build/src
export PYMS_DIR=/usr/local/src/xyce/utils/PyMS XYCE_SRC=/usr/local/src/xyce/src XYCE_BUILD=/usr/local/src/xyce-build
XYCE=/usr/local/src/xyce-build/src/Xyce
B=/usr/local/src/sv2ghdl/bfit/bfit.py
OV=/opt/openvaf/openvaf
ACC=/usr/local/src/sv2ghdl/bfit/benchmarks/accuracy.py
M=/mnt/c/cygwin64/tmp/perfbench/models
W=/tmp/mbench; rm -rf "$W"; mkdir -p "$W"; cd "$W" || exit 1
python3 /usr/local/src/sv2ghdl/bfit/benchmarks/gen_models.py "$M" >/dev/null 2>&1

declare -A OUT=( [rectifier]=out [inv_chain]=out [ring_osc]=n0 [ota_5t]=out [bjt_amp]=out [opamp]=no )

acc() { python3 "$ACC" 1e6 "$1" "$2" 2>/dev/null | grep -aoE '[0-9.]+%' | head -1; }
wall() { awk -v a="$1" -v b="$2" 'BEGIN{printf "%.2f",b-a}'; }

ng_run() {  # deck node outfile -> echoes secs|brk ; writes outfile (time value)
  sed '/^\.end$/d' "$1" > _d.cir
  { echo '.control'; for o in *.osdi; do [ -e "$o" ] && echo "pre_osdi $W/$o"; done
    echo run; echo "wrdata $3 v($2)"; echo quit; echo .endc; echo .end; } >> _d.cir
  local t0=$(date +%s.%N); timeout 600 ngspice -b _d.cir >/tmp/ng.log 2>&1; local rc=$?; local t1=$(date +%s.%N)
  { [ $rc -ne 0 ] || grep -qaiE 'fatal|aborted|too small'  /tmp/ng.log; } && { echo brk; return; }
  wall $t0 $t1
}
xy_run() {  # deck node outfile -> echoes secs|brk ; writes outfile (time value)
  sed "s/^\.end/.print tran format=csv V($2)\n.end/" "$1" > _x.cir
  rm -rf /tmp/mb_cache; export PYMS_CACHE=/tmp/mb_cache XYCE_VA_PATH="$W"
  local t0=$(date +%s.%N); timeout 600 "$XYCE" _x.cir >/tmp/xy.log 2>&1; local rc=$?; local t1=$(date +%s.%N)
  [ $rc -ne 0 ] && { echo brk; return; }
  awk -F, 'NR>1{print $1,$NF}' _x.cir.csv > "$3" 2>/dev/null
  wall $t0 $t1
}

echo "model | ngspice xyce | ng+bfit(s,acc) xy+bfit(s,acc)"
for m in rectifier inv_chain ring_osc ota_5t bjt_amp opamp; do
  node=${OUT[$m]}; cp "$M/$m.cir" .
  rm -f *.osdi
  ngt=$(ng_run $m.cir $node gold.dat)
  xyt=$(xy_run $m.cir $node xy.dat)
  # build bfit decks (front: mirrors->cmout, inverter/bridge/ce as before)
  rm -f *.va *.osdi
  XYCE_USE_BFIT=auto   python3 "$B" front $m.cir --sim xyce    -o ${m}_bx.cir >/dev/null 2>&1
  NGSPICE_USE_BFIT=auto python3 "$B" front $m.cir --sim ngspice -o ${m}_bn.cir >/dev/null 2>&1
  for va in *.va; do [ -e "$va" ] && $OV "$va" >/dev/null 2>&1; done
  ngbt=$(ng_run ${m}_bn.cir $node ngb.dat); nacc="-"; [ "$ngbt" != brk ] && nacc=$(acc gold.dat ngb.dat)
  xybt=$(xy_run ${m}_bx.cir $node xyb.dat); xacc="-"; [ "$xybt" != brk ] && xacc=$(acc gold.dat xyb.dat)
  printf 'RESULT %-10s ng=%-6s xy=%-6s ngb=%-6s/%-7s xyb=%-6s/%-7s\n' "$m" "$ngt" "$xyt" "$ngbt" "$nacc" "$xybt" "$xacc"
done
echo "=== DONE model_bench ==="
