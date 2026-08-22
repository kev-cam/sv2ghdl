#!/bin/bash
###############################################################################
# accel-gate.sh — correctness gate for the nvc `--accel` (yosys) path.
#
# WHY THIS EXISTS
#   coord-gate.sh gates on ivtest/iverilog-nvc, which does not exercise --accel
#   AT ALL, and nvc's own test/accel does not cover synchronous reset. On
#   2026-07-29 that blind spot let commit 8c8e1c1ba land on origin/master while
#   it silently produced WRONG VALUES on 16 of the 19 designs here: it removed
#   nvc's half of the two-sided `rst` protocol (gen_statemachine keeps `_rst`
#   out of inputs_t and expects the bridge to drive reset out of band via
#   AJB[5]), which made reset unreachable. Both existing gates stayed green.
#
#   So: any change to the bridge or the code generator must be shown to keep
#   interp == accel == perinst == Verilator on every design in accel/run.sh.
#
# WHAT IT CHECKS, IN ORDER
#   1. FRESHNESS. Refuses to run against a binary older than its source. This
#      is not paranoia: a stale `gen_statemachine` already produced one bogus
#      published measurement (the withdrawn "963x"), because the installed
#      binary predated a codegen fix that was in the .cpp. A gate that tests a
#      stale binary is worse than no gate, so the gate BUILDS rather than
#      trusting timestamps (a rebase rewrites mtimes without changing content).
#   2. THE SUITE. accel/run.sh, 19 designs x 4 engines, checksum-compared.
#
# CLASSIFY ON INSTALL EVIDENCE, NEVER ON THE CHECKSUM ALONE. A design that
# declines to accelerate falls back to the interpreter and therefore produces
# an IDENTICAL checksum — measured false-positive rate 43/43 when classifying
# on checksums. run.sh already counts `accel installed` notes and marks a case
# NO-INSTALL when there are none; this script additionally refuses a run in
# which NOTHING installed anywhere, which is what a broken translator or a
# missing gen_statemachine looks like.
#
# USAGE
#   ./accel-gate.sh [--no-build] [--quick] [case ...]
#     --no-build  skip the build step (assume nvc + gen_statemachine are current)
#     --quick     one design per shape (wide/fsm/deep/regf/many/act) instead of 19
#     case ...    explicit case names, passed through to run.sh
#
# EXIT: 0 = clean. 1 = a design mismatched or nothing installed. 2 = setup error
#       (stale build, missing tool) — deliberately distinct from a real failure.
###############################################################################
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SUITE="$HERE/accel"
SRC=/usr/local/src
NVC="${NVC:-$SRC/nvc-build/bin/nvc}"
GSM="${GEN_STATEMACHINE:-$SRC/sv2ghdl/yosys/gen_statemachine}"
GSM_SRC="$SRC/sv2ghdl/yosys/gen_statemachine.cpp"
YOSYS_BUILD="${YOSYS_BUILD:-$SRC/yosys-build}"

NOBUILD=0
QUICK=0
CASES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --no-build) NOBUILD=1; shift;;
    --quick)    QUICK=1;   shift;;
    *)          CASES+=("$1"); shift;;
  esac
done
[ "$QUICK" = 1 ] && [ ${#CASES[@]} -eq 0 ] && \
  CASES=(wide_n8w1024 fsm_n32 deep_d32 regf_n8d16 many_k12 act_lo_n8w256)

echo "=== accel-gate: $(date -u) ==="

# ---- 1. freshness -----------------------------------------------------------
[ -x "$NVC" ] || { echo "  !! no nvc binary at $NVC"; exit 2; }
[ -d "$SUITE" ] || { echo "  !! no suite at $SUITE"; exit 2; }

# ENSURE the binaries match the source rather than merely complaining about
# timestamps. Comparing mtimes alone gives false positives -- a git checkout or
# rebase rewrites a source file without changing its content, which is exactly
# what coord-gate does right before calling this. Building is authoritative and
# is a no-op when everything is already current.
if [ "$NOBUILD" = 0 ]; then
  if [ -d "$SRC/nvc-build" ]; then
    echo "  building nvc (no-op if current)"
    if ! make -C "$SRC/nvc-build" -j"$(nproc)" >"${TMPDIR:-/tmp}/accel-gate-nvc-build.log" 2>&1; then
      echo "  !! nvc build FAILED — see ${TMPDIR:-/tmp}/accel-gate-nvc-build.log"
      tail -15 "${TMPDIR:-/tmp}/accel-gate-nvc-build.log"
      exit 2
    fi
  fi
  # gen_statemachine has no Makefile: it is built by hand, so it goes stale
  # silently. That has already cost one bogus published measurement (the
  # withdrawn "963x" was measured against a binary predating a codegen fix
  # that was in the .cpp). Rebuild whenever the source is newer.
  if [ -f "$GSM_SRC" ] && { [ ! -x "$GSM" ] || [ "$GSM_SRC" -nt "$GSM" ]; }; then
    echo "  rebuilding gen_statemachine (source is newer)"
    if ! g++ $("$YOSYS_BUILD/yosys-config" --cxxflags) "$GSM_SRC" -o "$GSM" \
         -L"$YOSYS_BUILD" -lyosys -Wl,-rpath,"$YOSYS_BUILD" 2>&1 | tail -5; then
      echo "  !! gen_statemachine rebuild FAILED"; exit 2
    fi
  fi
fi
[ -x "$GSM" ] || { echo "  !! no gen_statemachine at $GSM"; exit 2; }
echo "  nvc:              $NVC ($($NVC --version 2>&1 | head -1))"
echo "  gen_statemachine: $GSM"

# ---- 2. the suite -----------------------------------------------------------
# Private WORKROOT so a gate run never shares the developer's accel cache: a
# warm cache would hide a codegen change behind a cached .so.
WORK="${ACCELBENCH_WORK:-${TMPDIR:-/tmp}/accel-gate-$$}"
rm -rf "$WORK"
export ACCELBENCH_WORK="$WORK"
export GEN_STATEMACHINE="$GSM"
export NVC

LOG="$WORK.log"
echo "  suite:            $SUITE/run.sh ${CASES[*]:-(all 19)}"
"$SUITE/run.sh" "${CASES[@]}" 2>&1 | tee "$LOG"
rc=${PIPESTATUS[0]}

# Nothing installed anywhere == the accel path never ran; the checksums would
# all match because every case fell back to the interpreter.
installed=$(awk 'NR>2 && $2 ~ /^[0-9]+$/ {s+=$2} END{print s+0}' "$LOG")
if [ "$installed" -eq 0 ]; then
  echo "VERDICT: HELD — 0 chunks installed across the whole suite; the accel"
  echo "         path did not run, so the matching checksums prove nothing."
  rm -rf "$WORK"; exit 1
fi

rm -rf "$WORK"
if [ "$rc" -ne 0 ]; then
  echo "VERDICT: HELD — $rc design(s) mismatched (see EXCLUDED above)"
  exit 1
fi

# ---- 3. optimization assertions --------------------------------------------
# Correctness above; EXISTENCE here.  opt_asserts.sh proves each landed
# optimization actually FIRES (worbits peephole shape, comb-only decline,
# two-tier cache key, synth timeout, instruction budget).  Reverting one keeps
# every checksum green -- only these assertions notice.  Validated against the
# pre-peephole Jul-27 binary: it fails exactly the three checks it should.
echo "  running opt_asserts.sh"
if ! "$HERE/accel/opt_asserts.sh" | sed 's/^/    /'; then
  echo "VERDICT: HELD — an optimization stopped firing (see FAIL lines above)"
  exit 1
fi

# ---- 4. translated-design coverage ------------------------------------------
# The accelbench designs are generated NATIVE VHDL (static sensitivity
# lists); defects specific to iverilog-TRANSLATED processes
# (wait-at-bottom dynamic sensitivity) are invisible to them — the
# single-chunk wake hole (chunk evaluated twice at t=0 then never
# again; nvc d2f50aa83) lived exactly there.  translated.sh runs one
# translated fixture through interp and accel (single + merged) and
# byte-compares.
echo "  running translated.sh"
TR_NVC="${NVC:-/usr/local/src/nvc-build/bin/nvc}"
TR_LIB="${NVC_LIBDIR:-${TR_NVC%/bin/nvc}/lib}"
if ! "$HERE/accel/translated.sh" "${TMPDIR:-/tmp}/accel-translated-$$" \
     "$TR_NVC" "$TR_LIB" | sed 's/^/    /'; then
  echo "VERDICT: HELD — translated-design accel check failed"
  exit 1
fi
echo "VERDICT: CLEAN — all designs interp == accel == perinst == Verilator"
echo "         ($installed chunks installed)"
exit 0
