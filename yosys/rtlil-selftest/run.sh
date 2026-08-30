#!/bin/bash
###############################################################################
# rtlil-selftest — proves the gsm_rtlil_* direct-construction facade against
# the read_verilog text path on the rtoy fixture.  Three oracles:
#
#   1. DETERMINISM, in-process: two full builder sessions in ONE process
#      (yosys's global autoidx advances between them) must emit byte-identical
#      C — this is what the builder-mode canonicalization pass guarantees and
#      the read_verilog path structurally CANNOT provide.
#   2. BEHAVIORAL EQUALITY: the builder C and the text-path C, compiled under
#      the same driving harness (reset + 64 cycles of varying d), must print
#      identical traces.
#   3. ENGAGEMENT: the builder really ran (rc=0 and its outputs exist) — a
#      silent fallback cannot fake a pass.
#
# EXIT: 0 clean, 1 a check failed, 2 setup problem.
###############################################################################
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
GSM="${GEN_STATEMACHINE:-$HERE/../gen_statemachine}"
LIB="${NVC_GSM_LIB:-$HERE/../libgsm.so}"
W="${TMPDIR:-/tmp}/rtlil-selftest-$$"
mkdir -p "$W"
trap 'rm -rf "$W"' EXIT

[ -x "$GSM" ] || { echo "  !! no gen_statemachine at $GSM"; exit 2; }
[ -f "$LIB" ] || { echo "  !! no libgsm.so at $LIB"; exit 2; }

gcc -o "$W/drv" "$HERE/gsm_rtlil_test.c" -ldl || exit 2

# builder: two sessions in one process -> out.c and out.c.2
if ! "$W/drv" "$LIB" "$W/b.c" > "$W/drv.log" 2>&1; then
  echo "  FAIL  rtlil builder synth (see $W/drv.log)"; cat "$W/drv.log"; exit 1
fi
if ! cmp -s "$W/b.c" "$W/b.c.2"; then
  echo "  FAIL  rtlil builder in-process determinism (sessions differ)"; exit 1
fi

# text path on the equivalent source
if ! "$GSM" "$HERE/rtlil_toy.v" rtoy "$W/t.c" > "$W/t.log" 2>&1; then
  echo "  FAIL  text-path synth of rtlil_toy.v"; exit 1
fi

# driven behavioral comparison
gcc -O1 -DMODEL_C="\"$W/t.c\"" -o "$W/h_t" "$HERE/rtoy_harness.c" || exit 1
gcc -O1 -DMODEL_C="\"$W/b.c\"" -o "$W/h_b" "$HERE/rtoy_harness.c" || exit 1
"$W/h_t" > "$W/h_t.txt" && "$W/h_b" > "$W/h_b.txt"
if ! diff -q "$W/h_t.txt" "$W/h_b.txt" > /dev/null; then
  echo "  FAIL  rtlil builder vs text path behavioral divergence:"
  diff "$W/h_t.txt" "$W/h_b.txt" | head -5
  exit 1
fi

echo "  PASS  rtlil builder self-test (determinism + 64-cycle equality)"
exit 0
