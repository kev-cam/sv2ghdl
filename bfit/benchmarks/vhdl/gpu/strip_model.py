#!/usr/bin/env python3
# Drop the gsm model function BODIES the farm never calls (sm_clock_out,
# sm_clock_late, sm_clock_late_out, sm_fsm_coverage_report): each replicates
# the whole netlist and multiplies compile time/memory 3-4x on core-sized
# models.  A body runs from '^void fn(' to the first column-0 '}' after it;
# it is removed only if its #if/#endif count balances.  Keeps sm_reset,
# sm_comb, sm_clock_masked, sm_clock, sm_eval.   usage: strip_model.py model.c > out.c
import re, sys
lines = open(sys.argv[1]).read().split('\n')
spans = []
for fn in ('sm_clock_out', 'sm_clock_late', 'sm_clock_late_out', 'sm_fsm_coverage_report'):
    s = next((i for i, l in enumerate(lines) if re.match(r'^(void|int) ' + re.escape(fn) + r'\(', l)), None)
    if s is None: continue
    e = next((j for j in range(s + 1, len(lines)) if lines[j] == '}'), None)
    if e is None: continue
    body = lines[s:e + 1]
    if sum(1 for l in body if re.match(r'^#if', l)) != sum(1 for l in body if re.match(r'^#endif', l)):
        sys.stderr.write('WARNING: unbalanced preprocessor in %s, keeping it\n' % fn); continue
    spans.append((s, e + 1, fn))
keep = []; last = 0
for s, e, fn in sorted(spans):
    keep.extend(lines[last:s]); keep.append('/* %s removed by strip_model.py (%d lines) */' % (fn, e - s)); last = e
keep.extend(lines[last:])
sys.stdout.write('\n'.join(keep))
sys.stderr.write('kept %d of %d lines; removed %s\n' % (len(keep), len(lines), [(fn, e - s) for s, e, fn in spans]))
