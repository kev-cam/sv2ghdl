#!/bin/bash
# Qualify the generated gsm model's functions as __device__ for nvcc.
# Usage: make_device_model.sh general.c   -> writes general_dev.c
in=$1; out=${1%.c}_dev.c
# - qualify every function definition line (incl. the
#   `static inline __attribute__((always_inline)) TYPE name(` helper form);
#   requires `TYPE name(` so file-scope VARIABLE decls are never touched
# - host-only emissions (FILE*/fprintf coverage reporter) stay unqualified
# - sm_live_outputs (host-tool liveness mask, default all-ones, never
#   narrowed by the farm) is constant-folded to ~0ull so device code has
#   no host-global reference; the declaration line is left intact
awk '{
  if ($0 !~ /sm_live_outputs\[[0-9]+\] *= *\{/)
    gsub(/sm_live_outputs\[[0-9]+\]/, "(~0ull)");
  if ($0 ~ /^((static|inline|__attribute__\(\(always_inline\)\)) )*(void|int|uint32_t|uint64_t) [A-Za-z_][A-Za-z0-9_]*\(/ \
      && $0 !~ /sm_fsm_coverage_report/)
    print "SM_DEVICE " $0;
  else print
}' "$in" > "$out"
echo "wrote $out (verify: every function reachable from sm_clock/sm_comb must carry SM_DEVICE)"
