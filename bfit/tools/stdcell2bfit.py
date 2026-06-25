#!/usr/bin/env python3
"""stdcell2bfit.py -- generate a bfit behavioral macromodel from a static-CMOS
standard-cell transistor netlist.

A static CMOS cell's logic *is* its pull-up (PMOS) and pull-down (NMOS) networks:
transistors in series -> resistances add (AND of their gate conditions); in
parallel -> conductances add (OR). Each transistor's on-conductance is programmed
by its gate (NMOS ~ V(gate); PMOS ~ Vhi-V(gate)). The output is the resulting
pull-up/pull-down divider into the load C, with a leakage floor for the static-
power match -- no tanh, linear-algebraic, cheap per step (same family as
library/cmos_inv). This recovers the inverter model as the 1-transistor case.

Run ONCE per cell (pre-simulation) to build the bfit model library; the ATPG flow
(Atalanta .tst -> test2spice.pl) supplies the patterns to characterize/validate
the cell and fit ron/rleak/cin.

Usage: stdcell2bfit.py cell.cir [subckt]   -> emits the macromodel .subckt
"""
import sys, re

def pol_map(text):
    pol = {}
    for ln in text.splitlines():
        s = ln.strip().lower()
        if s.startswith(".model") and len(s.split()) >= 3:
            nm = s.split()[1]
            if   "pmos" in s: pol[nm] = "p"
            elif "nmos" in s: pol[nm] = "n"
    return pol

def parse_subckt(text, want=None):
    cur, blocks = None, {}
    for ln in text.splitlines():
        s = ln.strip(); low = s.lower()
        if low.startswith(".subckt"):
            t = s.split(); cur = t[1]; blocks[cur] = {"ports": t[2:], "dev": []}
        elif low.startswith(".ends"):
            cur = None
        elif cur and s and s[0].upper() == "M":
            t = s.split()                      # M name drain gate source bulk model
            if len(t) >= 6: blocks[cur]["dev"].append((t[0], t[1], t[2], t[3], t[5]))
    if want and want in blocks: return want, blocks[want]
    return (next(iter(blocks.items())) if blocks else (None, None))

def sp_reduce(edges, src, dst):
    """Series-parallel reduce [(n1,n2,gexpr)] to one conductance from src to dst."""
    E = [list(e) for e in edges]
    par = lambda a, b: "(%s)+(%s)" % (a, b)
    ser = lambda a, b: "1/(1/(%s)+1/(%s))" % (a, b)
    changed = True
    while changed:
        changed = False
        seen = {}                               # parallel: same endpoints -> sum
        for i, e in enumerate(E):
            if e is None: continue
            k = frozenset((e[0], e[1]))
            if k in seen:
                E[seen[k]][2] = par(E[seen[k]][2], e[2]); E[i] = None; changed = True
            else:
                seen[k] = i
        E = [e for e in E if e]
        if changed: continue
        deg = {}                                # series: degree-2 interior node -> merge
        for a, b, g in E: deg[a] = deg.get(a, 0) + 1; deg[b] = deg.get(b, 0) + 1
        for v, d in deg.items():
            if v in (src, dst) or d != 2: continue
            inc = [i for i, e in enumerate(E) if v in (e[0], e[1])]
            if len(inc) != 2: continue
            (a1, b1, g1), (a2, b2, g2) = E[inc[0]], E[inc[1]]
            o1 = b1 if a1 == v else a1; o2 = b2 if a2 == v else a2
            E[inc[0]] = [o1, o2, ser(g1, g2)]; E[inc[1]] = None; changed = True; break
        E = [e for e in E if e]
    tot = None
    for a, b, g in E:
        if frozenset((a, b)) == frozenset((src, dst)):
            tot = g if tot is None else par(tot, g)
    return tot or "1e-15"

def main():
    f = sys.argv[1]; want = sys.argv[2] if len(sys.argv) > 2 else None
    text = open(f).read(); pol = pol_map(text)
    name, blk = parse_subckt(text, want)
    if not blk: sys.exit("no .subckt found in %s" % f)
    ports, dev = blk["ports"], blk["dev"]
    gates = {d[2] for d in dev}
    hi = next((p for p in ports if re.match(r"(?i)^(v?dd|vcc|vpwr|vp)$", p)), ports[-2])
    lo = next((p for p in ports if re.match(r"(?i)^(v?ss|gnd|vgnd|vp?n|0)$", p)), ports[-1])
    outs = [p for p in ports if p not in gates and p not in (hi, lo)]
    out = outs[0] if outs else None
    ins = [p for p in ports if p in gates]
    def g(gate, typ):
        return ("(V(%s)/V(%s))/ron" % (gate, hi)) if typ == "n" \
               else "((V(%s)-V(%s))/V(%s))/ron" % (hi, gate, hi)
    def classify(model):                        # .model wins; else guess from the name
        m = model.lower()
        if m in pol: return pol[m]
        if any(k in m for k in ("pmos", "pfet", "pch")) or m.startswith("p"): return "p"
        if any(k in m for k in ("nmos", "nfet", "nch")) or m.startswith("n"): return "n"
        return None
    n_e = [(d[1], d[3], g(d[2], "n")) for d in dev if classify(d[4]) == "n"]
    p_e = [(d[1], d[3], g(d[2], "p")) for d in dev if classify(d[4]) == "p"]
    gdn = sp_reduce(n_e, out, lo)               # pull-down network conductance
    gup = sp_reduce(p_e, out, hi)               # pull-up network conductance
    print("* bfit macromodel for cell '%s' (stdcell2bfit: pull-net -> programmed conductances)" % name)
    print("* inputs=%s  output=%s  rails=%s/%s  (%d NMOS, %d PMOS)"
          % (",".join(ins), out, hi, lo, len(n_e), len(p_e)))
    print(".subckt %s %s PARAMS: ron=1000 rleak=1e6 cin=2f" % (name, " ".join(ports)))
    for p in ins:
        print("Cin_%s %s %s {cin}" % (p, p, lo))
    print("Bo %s %s I={ ((%s)+1/rleak)*(V(%s)-V(%s)) - ((%s)+1/rleak)*(V(%s)-V(%s)) }"
          % (lo, out, gup, hi, out, gdn, out, lo))
    print(".ends")

if __name__ == "__main__":
    main()
