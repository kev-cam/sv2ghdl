#!/usr/bin/env python3
"""Summarize a verilator_ref.py report: highlight validated 3D-logic divergences
(VL_CONFIRMS_SHIM), candidate real bugs (VL_CONFIRMS_IVL), and 4-state-only
tests (VL_DEGENERATE, split by whether shim values still match iverilog)."""
import json, re, sys
from collections import Counter

rep = json.load(open(sys.argv[1] if len(sys.argv)>1 else "/tmp/verilator_ref_full.json"))
R = rep["results"]

# nvc runtime-failure markers that leak into shim "output" (delta-loop limits,
# unhandled assertion/severity, elaboration diagnostics) -- these are shim
# RUNTIME failures, not value divergences.
_NVC_FAIL = re.compile(r'(--stop-delta|is active|process :|\bseverity (failure|error)|'
                       r'\.vhd:\d+|\bNote: you can increase|^\s*[|=>]\s|unhandled|'
                       r'\bFatal\b|\bBounds check|\bIndex \d+ outside)', re.I)
def shim_is_nvc_fail(r):
    return any(_NVC_FAIL.search(l) for l in r.get("shim", []))

# reclassify VL_CONFIRMS_IVL into runtime-fail vs genuine divergence
for r in R:
    if r["class"] == "VL_CONFIRMS_IVL" and shim_is_nvc_fail(r):
        r["class"] = "SHIM_RUNTIME_FAIL"
c = Counter(r["class"] for r in R)
tot = len(R)
print(f"TOTAL {tot} tests\n")
order = ["AGREE","VL_CONFIRMS_SHIM","VL_DEGENERATE","VL_CONFIRMS_IVL","VL_NOCOMPILE",
         "SHIM_RUNTIME_FAIL","SHIM_NO_OUTPUT","SHIM_ERROR","IVL_ERROR","MISSING_SRC"]
for k in order + [x for x in c if x not in order]:
    if k in c: print(f"  {k:18s} {c[k]:5d}  {100*c[k]/tot:4.1f}%")

def show(cls, limit=None, diffs=0):
    xs = [r for r in R if r["class"]==cls]
    print(f"\n===== {cls} ({len(xs)}) =====")
    for r in (xs if limit is None else xs[:limit]):
        vm = r.get("value_matches_ivl")
        print(f"  {r['name']:30s} vmatch_ivl={vm} {r.get('detail','')[:40]}")
    return xs

# VL_DEGENERATE: how many still match iverilog on value (only self-check/x-display differs)
deg = [r for r in R if r["class"]=="VL_DEGENERATE"]
degvm = sum(1 for r in deg if r.get("value_matches_ivl"))
print(f"\nVL_DEGENERATE: {degvm}/{len(deg)} have shim VALUE == iverilog (only verdict/x-display differs)")

# The two classes that need human eyes:
cs = show("VL_CONFIRMS_SHIM")     # 3D-logic validated as cleaner 2-state
ci = show("VL_CONFIRMS_IVL")      # shim differs from BOTH -> candidate real bugs

def dump_diff(r):
    print(f"\n--- {r['name']} ({r['class']}) ---")
    for tag in ("ivl","shim","vl"):
        if tag in r:
            print(f"  [{tag}]");
            for l in r[tag][:8]: print(f"      {l}")

print("\n########## VL_CONFIRMS_SHIM diffs (3D-logic == Verilator, cleaner than iverilog) ##########")
for r in cs[:12]: dump_diff(r)
print("\n########## VL_CONFIRMS_IVL diffs (shim differs from BOTH -> scrutinize) ##########")
for r in ci[:15]: dump_diff(r)
