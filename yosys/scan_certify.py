#!/usr/bin/env python3
# scan_certify.py — full-scan ATPG coverage certificate for a gen_statemachine
# model: apply every atalanta pattern ({PI, PPI=state} -> expected {PO,
# PPO=next-state}) to the compiled C model and demand exact agreement.
#
#   json2bench.py emits the full-scan bench (DFF Q -> INPUT, D-cone ->
#   OUTPUT next_<q>); atalanta generates patterns + fault-free responses
#   computed from the NETLIST — an independent reference for the C model.
#
# Usage: scan_certify.py model.c TOP circuit.test [workdir]
import re, subprocess, sys, os

if len(sys.argv) < 4:
    sys.exit("usage: scan_certify.py model.c TOP circuit.test [workdir]")
MODEL, TOP, TEST = sys.argv[1:4]
WORK = sys.argv[4] if len(sys.argv) > 4 else "/tmp/scan_certify"
os.makedirs(WORK, exist_ok=True)

# ---- parse the .test header (PI/PO order) + patterns ----------------------
pis, pos, pats = [], [], []
mode = None
for line in open(TEST):
    s = line.strip()
    if s.startswith('* Primary inputs'):
        mode = 'pi'; continue
    if s.startswith('* Primary outputs'):
        mode = 'po'; continue
    if s.startswith('* Test patterns'):
        mode = 'pat'; continue
    if s.startswith('*') or not s:
        continue
    if mode == 'pi':
        pis += s.split()
    elif mode == 'po':
        pos += s.split()
    elif mode == 'pat':
        m = re.match(r'\d+:\s+([01xX]+)\s+([01xX]+)', s)
        if m:
            pats.append((m.group(1), m.group(2)))
print(f"{len(pis)} PIs, {len(pos)} POs, {len(pats)} patterns")
if not pats or not pos:
    sys.exit("certify: EMPTY pattern set — a certificate cannot pass vacuously")

# ---- parse model struct fields (name -> width, wide?) ----------------------
src = open(MODEL).read()
def fields(struct):
    i = src.index(struct)
    j = src.index('}', i)
    f = {}
    for m in re.finditer(r'(uint64_t|uint32_t)\s+(_\w+)(\[(\d+)\])?;'
                         r'(?:\s*//\s*(\d+) bits)?', src[i:j]):
        ty, nm, _, limbs, wbits = m.groups()
        wide = limbs is not None
        f[nm] = (wide, int(limbs) if wide else 0)
    return f
in_f = fields('typedef struct {')                     # inputs_t is first
st_i = src.index('} inputs_t;')
# state struct is the typedef following inputs_t
rest = src[st_i:]
k = rest.index('typedef struct {')
st_f = {}
j = rest.index('}', k)
for m in re.finditer(r'(uint64_t|uint32_t)\s+(_\w+)(\[(\d+)\])?;', rest[k:j]):
    ty, nm, _, limbs = m.groups()
    st_f[nm] = (limbs is not None, int(limbs) if limbs else 0)

def resolve(bench_name, table):
    """bench net name -> (field, bit). Prefer exact field, else name_bit."""
    cand = '_' + bench_name
    if cand in table:
        return cand, 0
    m = re.match(r'(.*)_(\d+)$', bench_name)
    if m and ('_' + m.group(1)) in table:
        return '_' + m.group(1), int(m.group(2))
    return None, None

def setter(kind, name, bit, pos_in_vec):
    fld, b = resolve(name, in_f if kind == 'pi' else st_f)
    if fld is None:
        return None
    base = f"in.{fld}" if kind == 'pi' else f"s.{fld}"
    wide = (in_f if kind == 'pi' else st_f)[fld][0]
    if wide:
        return (f"    if(v[{pos_in_vec}]=='1') {base}[{b>>5}] |= 1u<<{b&31};")
    return (f"    if(v[{pos_in_vec}]=='1') {base} |= UINT64_C(1)<<{b};")

def getter(name, pos_in_vec):
    if name.startswith('next_'):
        fld, b = resolve(name[5:], st_f)
        base, tab = f"s.{fld}", st_f
    else:
        fld, b = resolve(name, out_f)
        base, tab = f"o.{fld}", out_f
    if fld is None:
        return None
    wide = tab[fld][0]
    val = (f"(({base}[{b>>5}]>>{b&31})&1)" if wide
           else f"(({base}>>{b})&1)")
    return (f"    {{ int e=(r[{pos_in_vec}]=='1');"
            f" if({val}!=e && r[{pos_in_vec}]!='x')"
            f" {{ fail++; if(fail<=10)"
            f" printf(\"pat %d {name} got %d want %c\\n\","
            f" pi_, (int){val}, r[{pos_in_vec}]); }} }}")

# outputs_t follows state struct
r2 = rest[k:]
k2 = r2.index('typedef struct {', 1)
j2 = r2.index('}', k2)
out_f = {}
for m in re.finditer(r'(uint64_t|uint32_t)\s+(_\w+)(\[(\d+)\])?;', r2[k2:j2]):
    ty, nm, _, limbs = m.groups()
    out_f[nm] = (limbs is not None, int(limbs) if limbs else 0)

setters, getters, unmapped = [], [], []
n_ppi = 0
for i, p in enumerate(pis):
    kind = 'pi' if resolve(p, in_f)[0] else 'ppi'
    s = setter('pi' if kind == 'pi' else 'ppi', p, 0, i)
    if s is None:
        unmapped.append(('PI', p))
    else:
        setters.append(s)
        if kind == 'ppi':
            n_ppi += 1
for i, p in enumerate(pos):
    g = getter(p, i)
    if g is None:
        unmapped.append(('PO', p))
    else:
        getters.append(g)
if unmapped:
    for k_, n_ in unmapped[:10]:
        print(f"  UNMAPPED {k_}: {n_}")
    sys.exit(f"certify: {len(unmapped)} unmapped nets — name mapping incomplete")
print(f"mapped: {len(setters)} PIs ({n_ppi} state PPIs), {len(getters)} POs")

drv = f"""#define SM_NO_MAIN 1
#include "{MODEL}"
#include <stdio.h>
#include <string.h>
int main(void) {{
  state_t s; inputs_t in; outputs_t o;
  int fail = 0, pi_ = 0;
  FILE *f = fopen("{TEST}", "r");
  char line[65536];
  while (fgets(line, sizeof line, f)) {{
    char v[65536], r[65536]; int n;
    if (sscanf(line, " %d: %s %s", &n, v, r) != 3) continue;
    pi_ = n;
    memset(&s, 0, sizeof s); memset(&in, 0, sizeof in); memset(&o, 0, sizeof o);
{chr(10).join(setters)}
    sm_comb(&s, &in, &o);
    sm_clock(&s, &in);
{chr(10).join(getters)}
  }}
  printf(fail ? "CERTIFY FAIL: %d bit mismatches\\n"
              : "CERTIFY PASS: all patterns match the netlist reference\\n",
         fail);
  return fail != 0;
}}
"""
open(f"{WORK}/certify.c", "w").write(drv)
r = subprocess.run(["cc", "-O1", "-o", f"{WORK}/certify", f"{WORK}/certify.c"],
                   capture_output=True, text=True)
if r.returncode:
    print(r.stderr[:1500]); sys.exit("certify: driver compile failed")
r = subprocess.run([f"{WORK}/certify"], capture_output=True, text=True)
print(r.stdout.strip())
sys.exit(r.returncode)
