#!/bin/bash
# Translated-design accel check: the accelbench designs are generated
# NATIVE VHDL, whose processes carry static sensitivity lists — they can
# never catch defects specific to iverilog-TRANSLATED processes
# (wait-at-bottom dynamic sensitivity).  The single-chunk wake hole
# (chunk evaluated twice at t=0 then never again; nvc d2f50aa83) lived
# in exactly that blind spot.  This check runs one translated fixture
# (glue2b: interp-side gated producer -> accel-admitted registered
# consumer) through interp and accel and byte-compares, merged and
# unmerged.
#
# Usage: translated.sh <workdir> <nvc-binary> <nvc-libdir> [ok-fn] [bad-fn]
set -u
W=$1; NVC=$2; NVCLIB=$3
SRC="$(dirname "$0")/translated_glue2b.v"
IV=${IVERILOG:-/usr/local/src/iverilog/_install/bin/iverilog}
IVL=${IVL_BUILD_LIB:-/usr/local/src/iverilog/_install/lib/ivl}
DEDUP=${SV_DEDUP:-/usr/local/src/sv2ghdl/bin/sv-dedup-vhdl}

ok()  { echo "      PASS  $1  ${2:-}"; }
bad() { echo "      FAIL  $1  ${2:-}"; FAILED=1; }
FAILED=0

D="$W/translated"; rm -rf "$D"; mkdir -p "$D"; cd "$D" || exit 2
"$IV" -B"$IVL" -tvhdl -psv2vhdl=1 -g2012 -o d.vhd "$SRC" 2>/dev/null
"$DEDUP" d.vhd > d2.vhd 2>/dev/null
"$NVC" --std=2040 -L "$NVCLIB" -a d2.vhd -e top >/dev/null 2>&1 \
  || { bad "translated fixture elaborates"; exit 1; }

"$NVC" --std=2040 -L "$NVCLIB" -r top 2>&1 \
  | grep -aE "Note.*t=" | sed -E 's/.*Note: [0-9a-z+]+: //' > gold.txt
[ -s gold.txt ] || { bad "translated interp golden" "(no samples)"; exit 1; }

for mode in "" "NVC_ACCEL_MERGE=1"; do
  label=$([ -n "$mode" ] && echo merged || echo single)
  rm -rf "$D/.cache" "$HOME/.cache/nvc/accel"
  env NVC_ACCEL=1 NVC_ACCEL_JIT=1 NVC_ACCEL_FROM_VHDL=1 \
      NVC_ACCEL_MIN_MODULES=1 $mode \
      "$NVC" --std=2040 -L "$NVCLIB" -r top 2>&1 | tee raw_$label.txt \
    | grep -aE "Note.*t=" | sed -E 's/.*Note: [0-9a-z+]+: //' > accel_$label.txt
  inst=$(grep -acE "accel installed" raw_$label.txt)
  if [ "$inst" -lt 1 ]; then
    bad "translated accel ($label) installs" "(installs=$inst)"
  elif diff -q gold.txt accel_$label.txt >/dev/null; then
    ok "translated accel ($label) matches interp" "($(wc -l < gold.txt) samples)"
  else
    bad "translated accel ($label) matches interp" \
        "(first: $(diff gold.txt accel_$label.txt | sed -n '2p' | head -c 60))"
  fi
done
exit $FAILED
