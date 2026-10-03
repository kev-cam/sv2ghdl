#!/bin/bash
# Regenerate the cut fixtures' design.vhd / _norm.sv from their tb.v (WSL: the
# translator stack is Linux ELF).  Translates as NvcBackend.analyse does
# (iverilog-sv2ghdl -g2012 -s tb), in /tmp/vamos_fx/<case> so the absolute
# paths in the provenance comments stay the same as in the checked-in files.
#
#   bash tests/vamos/fixtures/vhdl/cut_regen.sh [case ...]     (default: all)
#
# design_t2.vhd (cut_swvp) and design_t3.vhd (cut_vec) are hand-edited copies
# in the form translator patches T2/T3 produce; redo them by hand if
# design.vhd changes.
here=$(cd "$(dirname "$0")" && pwd)
S2G=${S2G:-/usr/local/src/sv2ghdl/bin/iverilog-sv2ghdl}
export NVC=${NVC:-/usr/local/src/nvc-build/bin/nvc}
export NVC_LIBDIR=${NVC_LIBDIR:-/usr/local/src/nvc-build/lib}
export IVERILOG=${IVERILOG:-/usr/local/src/iverilog/_install/bin/iverilog}
cases=("$@")
if [ ${#cases[@]} -eq 0 ]; then
  for d in "$here"/cut_*/; do
    [ -f "$d/tb.v" ] && cases+=("$(basename "$d" | sed 's/^cut_//')")
  done
fi
for c in "${cases[@]}"; do
  src=$here/cut_$c
  W=/tmp/vamos_fx/$c
  rm -rf "$W"; mkdir -p "$W"; cp "$src/tb.v" "$W/"
  (cd "$W" && "$S2G" -o "$W/nvc" -g2012 -s tb tb.v > s2g.log 2>&1)
  if [ -f "$W/nvc/design.vhd" ] && ! grep -q "sv2vhdl:deferred" "$W/nvc/design.vhd"; then
    cp "$W/nvc/design.vhd" "$W/nvc/_norm.sv" "$src/"
    echo "$c: ok"
  else
    echo "$c: FAILED (see $W/s2g.log)"
  fi
done
