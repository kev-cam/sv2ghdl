#!/bin/bash
# Commercial side of the league table -- run from CYGWIN (Windows engines).
# Writes commercial.csv (model,qspice,ltspice) for assemble.py + prints a summary.
M=/cygdrive/c/cygwin64/tmp/perfbench/models
Q="/cygdrive/c/Program Files/QSPICE/QSPICE64.real.exe"
LT="/cygdrive/c/Program Files/ADI/LTspice/LTspice.exe"
OUT=${OUTCSV:-$(cd "$(dirname "$0")" && pwd)/commercial.csv}
cd "$M" || exit 1
getelapsed() { awk 'tolower($0)~/total elapsed time/{for(i=1;i<=NF;i++)if($i+0>0){printf "%.2f",$i;exit}}' "$1" 2>/dev/null; }
echo "model,qspice,ltspice" > "$OUT"
for m in rectifier inv_chain ring_osc ota_5t bjt_amp opamp breaker; do
  timeout 180 "$Q" "$m.cir" -binary -r "$m.qraw" -o "$m.qout" >/dev/null 2>&1
  q=$(getelapsed "$m.qout"); [ -z "$q" ] && q="brk"
  cp "$m.cir" "$m.net"
  timeout 180 "$LT" -b -Run "$m.net" >/dev/null 2>&1
  l=$(getelapsed "$m.log"); [ -z "$l" ] && l="brk"
  printf '%s,%s,%s\n' "$m" "$q" "$l" >> "$OUT"
  printf '%-12s QSPICE=%-9s LTspice=%-9s\n' "$m" "$q" "$l"
done
echo "-> $OUT"
