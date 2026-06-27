#!/bin/bash
M=/cygdrive/c/cygwin64/tmp/perfbench/models
Q="/cygdrive/c/Program Files/QSPICE/QSPICE64.real.exe"
LT="/cygdrive/c/Program Files/ADI/LTspice/LTspice.exe"
cd "$M" || exit 1
getelapsed() { awk 'tolower($0)~/total elapsed time/{for(i=1;i<=NF;i++)if($i+0>0){printf "%.2f",$i;exit}}' "$1" 2>/dev/null; }
for m in rectifier inv_chain ring_osc ota_5t bjt_amp opamp breaker; do
  timeout 180 "$Q" "$m.cir" -binary -r "$m.qraw" -o "$m.qout" >/dev/null 2>&1
  q=$(getelapsed "$m.qout"); [ -z "$q" ] && q="FAIL"
  cp "$m.cir" "$m.net"
  timeout 180 "$LT" -b -Run "$m.net" >/dev/null 2>&1
  l=$(getelapsed "$m.log"); [ -z "$l" ] && l="FAIL"
  printf '%-12s QSPICE=%-9s LTspice=%-9s\n' "$m" "$q" "$l"
done
