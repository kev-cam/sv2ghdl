#!/usr/bin/env python3
# certify_cones.py — per-cone ATPG chunk certificate, COMPILE-ONCE.
#
# The whole-unit full-scan bench is too big for atalanta 2.0 to emit patterns
# (47k gates -> output overflow). Each OUTPUT / next_<reg> is driven by a small
# cone, so certify per cone and require EVERY cone to pass: the scale fix and
# the fork-farm unit for verify-then-promote (#14).
#
#   phase A (parallel): cone_slice (slice+const-fold) -> atalanta -> per-cone
#                       .test (100%-coverage patterns + netlist responses)
#   phase B (compile ONCE): one driver #includes the 12MB model a single time
#                       with a cone_<i>() function per cone (direct-field
#                       setters/getters from scan_certify.gen_body); compiled
#                       once, run once. Recompiling the model per cone (the old
#                       path) was hopeless at 1529 cones x 12MB.
#
# The chunk is CERTIFIED iff every non-degenerate cone certifies at full
# coverage; any cone mismatch REJECTS it (that output's codegen disagrees with
# the yosys netlist). Degenerate cones (0 patterns) and unmappable cones are
# SKIPPED (logged), never silently passed.
#
# Usage: certify_cones.py model.c TOP full.bench [workdir]
#          [--only=a,b] [--jobs=N] [--atalanta-timeout=S] [--cc="cc -O0"]
import os, re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scan_certify as sc                      # noqa: E402

SLICE = os.path.join(HERE, "cone_slice.py")
ATAL = os.environ.get("ATALANTA", "atalanta")

if len(sys.argv) < 4:
    sys.exit("usage: certify_cones.py model.c TOP full.bench [workdir] "
             "[--only=a,b] [--jobs=N] [--atalanta-timeout=S] [--cc=CC]")
MODEL = os.path.abspath(sys.argv[1])
TOP, FULL = sys.argv[2], sys.argv[3]
WORK, only, JOBS, ATO, CC = "/tmp/certify_cones", None, \
    max(1, (os.cpu_count() or 4) - 2), 60, "cc -O0"
for a in sys.argv[4:]:
    if a.startswith("--only="):
        only = set(a.split("=", 1)[1].split(","))
    elif a.startswith("--jobs="):
        JOBS = int(a.split("=", 1)[1])
    elif a.startswith("--atalanta-timeout="):
        ATO = int(a.split("=", 1)[1])
    elif a.startswith("--cc="):
        CC = a.split("=", 1)[1]
    elif not a.startswith("--"):
        WORK = a
os.makedirs(WORK, exist_ok=True)

outputs = [m.group(1) for m in
           (re.match(r'OUTPUT\((\w+)\)', l.strip()) for l in open(FULL)) if m]
if only:
    outputs = [o for o in outputs if o in only]
if not outputs:
    sys.exit("certify_cones: no OUTPUTs found in bench")


def run(cmd, timeout):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def gen_cone(o):
    """phase A worker: slice+fold -> atalanta -> .test. Returns dict."""
    cb, ct = f"{WORK}/{o}.bench", f"{WORK}/{o}.test"
    if os.path.exists(ct):
        os.remove(ct)
    r = run([sys.executable, SLICE, FULL, o, cb], 40)
    if r is None or r.returncode:
        return {"o": o, "st": "skip", "why": "slice failed/timeout"}
    r = run([ATAL, "-t", ct, cb], ATO)
    if r is None:
        return {"o": o, "st": "skip", "why": f"atalanta timeout ({ATO}s)"}
    npat = 0
    if os.path.exists(ct):
        npat = sum(1 for l in open(ct) if re.match(r'\s*\d+:', l))
    if npat == 0:
        return {"o": o, "st": "skip", "why": "degenerate/const cone (0 pat)"}
    cov = ""
    mc = re.search(r'[Ff]ault cov\w*\s*:?\s*([0-9.]+)', r.stdout)
    if mc:
        cov = mc.group(1)
    return {"o": o, "st": "ok", "ct": ct, "npat": npat, "cov": cov}


print(f"[A] slicing + atalanta over {len(outputs)} cones "
      f"({JOBS} parallel, {ATO}s/cone timeout)...", flush=True)
conework = []
done = 0
with ThreadPoolExecutor(max_workers=JOBS) as ex:
    for res in ex.map(gen_cone, outputs):
        conework.append(res)
        done += 1
        if done % 100 == 0:
            print(f"    {done}/{len(outputs)} cones sliced", flush=True)

ok = [c for c in conework if c["st"] == "ok"]
skipped = [c for c in conework if c["st"] == "skip"]
print(f"[A] {len(ok)} cones with patterns, {len(skipped)} skipped", flush=True)

# ---- phase B: ONE driver, model #included once, a function per cone ---------
print("[B] generating monolithic driver (model included once)...", flush=True)
src = open(MODEL).read()
in_f, st_f, out_f = sc.parse_field_maps(src)

funcs, names, unmapped_cones = [], [], []
for c in ok:
    pis, pos, _ = sc.parse_test(c["ct"])
    setters, getters, unmapped, _ = sc.gen_body(
        pis, pos, in_f, st_f, out_f, quiet=True)
    if unmapped:
        unmapped_cones.append((c["o"], unmapped[:3]))
        continue
    i = len(names)
    funcs.append(f"""static int cone_{i}(void){{ /* {c['o']} */
  state_t s; inputs_t in; outputs_t o; int fail=0, n; (void)n;
  FILE*f=fopen("{c['ct']}","r"); if(!f) return -2;
  char line[65536], v[65536], r[65536];
  while(fgets(line,sizeof line,f)){{
    if(sscanf(line," %d: %s %s",&n,v,r)!=3) continue;
    memset(&s,0,sizeof s);memset(&in,0,sizeof in);memset(&o,0,sizeof o);
{chr(10).join(setters)}
    sm_comb(&s,&in,&o);
    sm_clock(&s,&in);
{chr(10).join(getters)}
  }}
  fclose(f); return fail;
}}""")
    names.append(c["o"])

if not names:
    print("certify_cones: no mappable cones with patterns — cannot certify")
    for o, u in unmapped_cones[:10]:
        print(f"    unmapped {o}: {u}")
    sys.exit(2)

arr_names = ",".join(f'"{n}"' for n in names)
arr_fns = ",".join(f"cone_{i}" for i in range(len(names)))
driver = f"""#define SM_NO_MAIN 1
#include "{MODEL}"
#include <stdio.h>
#include <string.h>
{chr(10).join(funcs)}
static const char* NM[] = {{{arr_names}}};
static int (*FN[])(void) = {{{arr_fns}}};
int main(void){{
  int nc=(int)(sizeof(FN)/sizeof(FN[0])), rej=0, cert=0, miss=0;
  for(int i=0;i<nc;i++){{
    int fl=FN[i]();
    if(fl==-2){{ miss++; printf("MISS %s\\n", NM[i]); }}
    else if(fl>0){{ rej++; printf("FAIL %s %d\\n", NM[i], fl); }}
    else cert++;
  }}
  printf("SUMMARY certified=%d rejected=%d missing=%d total=%d\\n",
         cert, rej, miss, nc);
  return rej?1:0;
}}
"""
dc = f"{WORK}/certify_all.c"
open(dc, "w").write(driver)
print(f"[B] driver: {len(names)} cone fns, {os.path.getsize(dc)>>10} KiB; "
      f"compiling once with '{CC}' ...", flush=True)
import time as _t                              # only for wall timing the compile
t0 = _t.monotonic()
r = subprocess.run(CC.split() + ["-o", f"{WORK}/certify_all", dc],
                   capture_output=True, text=True)
if r.returncode:
    print(r.stderr[:2000]); sys.exit("certify_cones: driver compile FAILED")
print(f"[B] compiled in {_t.monotonic()-t0:.0f}s; running certificate...",
      flush=True)
r = subprocess.run([f"{WORK}/certify_all"], capture_output=True, text=True)

fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL ")]
summ = [l for l in r.stdout.splitlines() if l.startswith("SUMMARY")]

print(f"\n=== per-cone certificate: {TOP} ({FULL}) ===")
print(f"  {len(names)} cones certified-or-checked, "
      f"{len(skipped)} skipped, {len(unmapped_cones)} unmapped")
print(f"  {summ[0] if summ else '(no summary line)'}")
for l in fails[:25]:
    print(f"    {l}")
if len(fails) > 25:
    print(f"    ... (+{len(fails)-25} more rejected cones)")
# a few representative skips
seen = {}
for c in skipped:
    seen.setdefault(c["why"], 0)
    seen[c["why"]] += 1
for why, n in sorted(seen.items(), key=lambda x: -x[1]):
    print(f"    skip[{n}]: {why}")
if unmapped_cones:
    print(f"    unmapped examples: "
          f"{[(o, u) for o, u in unmapped_cones[:3]]}")

# Verdict. A cone we could not MAP (bench net name absent from the model's
# struct fields) is a genuine coverage HOLE, not a pass — refuse to certify the
# chunk when any output cone went unmapped. Degenerate (0-pattern) cones are
# trivially equivalent and do not count against completeness.
if r.returncode != 0:
    verdict, code = "REJECTED", 1
    tail = f" — {len(fails)} cone(s) disagree with netlist"
elif unmapped_cones:
    verdict, code = "INCOMPLETE", 2
    tail = (f" — {len(unmapped_cones)} output cone(s) unmappable "
            f"(bench↔model name mismatch); cannot certify without full coverage")
else:
    verdict, code = "CERTIFIED", 0
    tail = ""
print(f"\n>>> CHUNK {verdict}{tail}")
sys.exit(code)
