#!/usr/bin/env python3
# scan_certify.py — full-scan ATPG coverage certificate for a gen_statemachine
# model: apply every atalanta pattern ({PI, PPI=state} -> expected {PO,
# PPO=next-state}) to the compiled C model and demand exact agreement.
#
#   json2bench.py emits the full-scan bench (DFF Q -> INPUT, D-cone ->
#   OUTPUT next_<q>); atalanta generates patterns + fault-free responses
#   computed from the NETLIST — an independent reference for the C model.
#
# The reusable pieces (parse_test, parse_field_maps, gen_body) are imported by
# certify_cones.py for the compile-once per-cone sweep.
#
# Usage: scan_certify.py model.c TOP circuit.test [workdir]
import re, subprocess, sys, os


def parse_test(path):
    """Parse an atalanta .test: returns (pis, pos, patterns[(vec,resp)])."""
    pis, pos, pats = [], [], []
    mode = None
    for line in open(path):
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
    return pis, pos, pats


def _fields_of(src, tname):
    """Fields of the `typedef struct { ... } tname;` block, by CLOSING tag so
    intervening structs (e.g. fsm_coverage_t between state_t and outputs_t)
    don't confuse a positional heuristic. Returns {field: (wide, limbs)}."""
    end = src.index('} %s;' % tname)
    start = src.rindex('typedef struct {', 0, end)
    body = src[start:end]
    f = {}
    for m in re.finditer(r'(uint64_t|uint32_t)\s+(_\w+)(\[(\d+)\])?;', body):
        _, nm, _br, limbs = m.groups()
        f[nm] = (limbs is not None, int(limbs) if limbs else 0)
    return f


def parse_field_maps(src):
    """Returns (in_f, st_f, out_f) field maps for inputs_t/state_t/outputs_t."""
    return (_fields_of(src, 'inputs_t'),
            _fields_of(src, 'state_t'),
            _fields_of(src, 'outputs_t'))


def resolve(bench_name, table):
    """bench net name -> (field, bit). Prefer exact field, else name_bit."""
    cand = '_' + bench_name
    if cand in table:
        return cand, 0
    m = re.match(r'(.*)_(\d+)$', bench_name)
    if m and ('_' + m.group(1)) in table:
        return '_' + m.group(1), int(m.group(2))
    return None, None


def _setter(kind, name, pos_in_vec, in_f, st_f, s_var='s', in_var='in',
            vec='v'):
    fld, b = resolve(name, in_f if kind == 'pi' else st_f)
    if fld is None:
        return None
    base = f"{in_var}.{fld}" if kind == 'pi' else f"{s_var}.{fld}"
    wide = (in_f if kind == 'pi' else st_f)[fld][0]
    if wide:
        return (f"    if({vec}[{pos_in_vec}]=='1') {base}[{b>>5}] |= 1u<<{b&31};")
    return (f"    if({vec}[{pos_in_vec}]=='1') {base} |= UINT64_C(1)<<{b};")


def _getter(name, pos_in_vec, st_f, out_f, s_var='s', o_var='o', exp='r',
            failvar='fail', patvar='pi_', quiet=False):
    if name.startswith('next_'):
        fld, b = resolve(name[5:], st_f); base, tab = f"{s_var}.{fld}", st_f
    else:
        fld, b = resolve(name, out_f); base, tab = f"{o_var}.{fld}", out_f
    if fld is None:
        return None
    wide = tab[fld][0]
    val = (f"(({base}[{b>>5}]>>{b&31})&1)" if wide else f"(({base}>>{b})&1)")
    if quiet:      # count-only, for the 1529-cone monolithic sweep driver
        return (f"    {{ int e=({exp}[{pos_in_vec}]=='1');"
                f" if({val}!=e && {exp}[{pos_in_vec}]!='x') {failvar}++; }}")
    return (f"    {{ int e=({exp}[{pos_in_vec}]=='1');"
            f" if({val}!=e && {exp}[{pos_in_vec}]!='x')"
            f" {{ {failvar}++; if({failvar}<=10)"
            f" printf(\"pat %d {name} got %d want %c\\n\","
            f" {patvar}, (int){val}, {exp}[{pos_in_vec}]); }} }}")


def gen_body(pis, pos, in_f, st_f, out_f, **kw):
    """Position-based setters/getters for one .test. Returns
    (setters, getters, unmapped, n_ppi). kw threads C var names for embedding
    inside per-cone functions."""
    setters, getters, unmapped, n_ppi = [], [], [], 0
    for i, p in enumerate(pis):
        kind = 'pi' if resolve(p, in_f)[0] else 'ppi'
        st = _setter('pi' if kind == 'pi' else 'ppi', p, i, in_f, st_f,
                     **{k: kw[k] for k in ('s_var', 'in_var', 'vec') if k in kw})
        if st is None:
            unmapped.append(('PI', p))
        else:
            setters.append(st)
            if kind == 'ppi':
                n_ppi += 1
    for i, p in enumerate(pos):
        g = _getter(p, i, st_f, out_f,
                    **{k: kw[k] for k in
                       ('s_var', 'o_var', 'exp', 'failvar', 'patvar', 'quiet')
                       if k in kw})
        if g is None:
            unmapped.append(('PO', p))
        else:
            getters.append(g)
    return setters, getters, unmapped, n_ppi


def main():
    if len(sys.argv) < 4:
        sys.exit("usage: scan_certify.py model.c TOP circuit.test [workdir]")
    MODEL, TOP, TEST = sys.argv[1:4]
    WORK = sys.argv[4] if len(sys.argv) > 4 else "/tmp/scan_certify"
    os.makedirs(WORK, exist_ok=True)

    pis, pos, pats = parse_test(TEST)
    print(f"{len(pis)} PIs, {len(pos)} POs, {len(pats)} patterns")
    if not pats or not pos:
        sys.exit("certify: EMPTY pattern set — a certificate cannot pass vacuously")

    in_f, st_f, out_f = parse_field_maps(open(MODEL).read())
    setters, getters, unmapped, n_ppi = gen_body(pis, pos, in_f, st_f, out_f)
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


if __name__ == '__main__':
    main()
