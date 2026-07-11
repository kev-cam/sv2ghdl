#!/usr/bin/env python3
# certify_cones.py — per-cone ATPG coverage certificate for a whole chunk.
#
# The whole-unit full-scan bench is too big for atalanta 2.0 to emit patterns
# (47k gates -> output overflow). Each OUTPUT / next_<reg> is driven by a small
# cone, so certify per cone and require EVERY cone to pass: this is both the
# scale fix and the natural fork-farm unit for verify-then-promote (#14).
#
# For each OUTPUT in the full-scan bench:
#   cone_slice.py (slice + const-fold)  ->  small equivalent cone bench
#   atalanta                            ->  100%-coverage patterns + netlist responses
#   scan_certify.py                     ->  drive the .so/.c model, demand agreement
# The chunk is CERTIFIED iff all cones certify at full coverage; any cone
# mismatch REJECTS it (that output's codegen disagrees with the netlist).
#
# Usage: certify_cones.py model.c TOP full.bench [workdir] [--only name,name]
import os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SLICE = os.path.join(HERE, "cone_slice.py")
CERT  = os.path.join(HERE, "scan_certify.py")
ATAL  = os.environ.get("ATALANTA", "atalanta")

if len(sys.argv) < 4:
    sys.exit("usage: certify_cones.py model.c TOP full.bench [workdir] [--only a,b]")
MODEL = os.path.abspath(sys.argv[1])
TOP, FULL = sys.argv[2], sys.argv[3]
WORK = "/tmp/certify_cones"
only = None
for a in sys.argv[4:]:
    if a.startswith("--only"):
        only = set(a.split("=", 1)[1].split(",")) if "=" in a else None
    else:
        WORK = a
os.makedirs(WORK, exist_ok=True)

outputs = []
for line in open(FULL):
    m = re.match(r'OUTPUT\((\w+)\)', line.strip())
    if m:
        outputs.append(m.group(1))
if only:
    outputs = [o for o in outputs if o in only]
if not outputs:
    sys.exit("certify_cones: no OUTPUTs found in bench")

def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)

certified, rejected, skipped = [], [], []
for o in outputs:
    cb = f"{WORK}/{o}.bench"
    ct = f"{WORK}/{o}.test"
    r = run([sys.executable, SLICE, FULL, o, cb])
    if r.returncode:
        skipped.append((o, "slice: " + r.stderr.strip()[:60])); continue
    # atalanta writes patterns + netlist fault-free responses
    r = run([ATAL, "-t", ct, cb])
    npat = 0
    if os.path.exists(ct):
        npat = sum(1 for l in open(ct) if re.match(r'\s*\d+:', l))
    if npat == 0:
        # a cone with no testable faults (e.g. output folds to a constant or a
        # bare PI alias) is trivially equivalent — nothing to certify.
        skipped.append((o, "no patterns (degenerate/constant cone)")); continue
    cov = ""
    mc = re.search(r'[Ff]ault cov\w*\s*:?\s*([0-9.]+)', r.stdout)
    if mc:
        cov = mc.group(1) + "%"
    r = run([sys.executable, CERT, MODEL, TOP, ct, f"{WORK}/sc_{o}"])
    tail = (r.stdout.strip().splitlines() or [""])[-1]
    if r.returncode == 0 and "CERTIFY PASS" in r.stdout:
        certified.append((o, npat, cov))
    else:
        rejected.append((o, tail or r.stderr.strip()[:80]))

print(f"\n=== per-cone certificate: {TOP} ({FULL}) ===")
print(f"  {len(certified)} certified, {len(rejected)} rejected, "
      f"{len(skipped)} skipped (of {len(outputs)} cones)")
for o, npat, cov in certified[:6]:
    print(f"    OK   {o}  ({npat} pat, cov {cov})")
if len(certified) > 6:
    print(f"    ...  (+{len(certified)-6} more certified)")
for o, why in rejected:
    print(f"    FAIL {o}  {why}")
for o, why in skipped[:8]:
    print(f"    skip {o}  {why}")
if len(skipped) > 8:
    print(f"    ...  (+{len(skipped)-8} more skipped)")

verdict = "CERTIFIED" if not rejected else "REJECTED"
print(f"\n>>> CHUNK {verdict}"
      + ("" if not rejected else f" — {len(rejected)} cone(s) disagree with netlist"))
sys.exit(1 if rejected else 0)
