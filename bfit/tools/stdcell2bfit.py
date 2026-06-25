#!/usr/bin/env python3
"""stdcell2bfit.py -- generate a bfit behavioral macromodel from a static-CMOS
standard-cell transistor netlist.

A static CMOS cell's logic *is* its transistor network: each FET is a
gate-programmed conductance between its drain and source (NMOS ~ V(gate); PMOS ~
Vhi-V(gate)). We emit ONE such conductance per transistor and let the solver
compute every node voltage -- so series stacks (AND of gate conditions), parallel
legs (OR), AND internal nodes of multi-stage compound cells (AND = NAND+inv, XOR,
muxes...) all resolve from topology alone. Each driven node gets a weak hi/lo
leakage pair (DC anchor + static-power floor) and a small cap (transient
stability). No tanh, linear-algebraic, cheap per step, linear between input
changes -> big adaptive steps (same family as library/cmos_inv). The inverter is
the 1-transistor-per-rail case.

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
        elif cur and s and s[0].upper() in "MX":
            t = s.split()           # M/X name drain gate source bulk model (X = PDK FET subckt)
            if len(t) >= 6: blocks[cur]["dev"].append((t[0], t[1], t[2], t[3], t[5]))
    if want and want in blocks: return want, blocks[want]
    return (next(iter(blocks.items())) if blocks else (None, None))

def sanit(n):
    return re.sub(r"[^A-Za-z0-9_]", "_", n)

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
    def gexpr(gate, typ):                           # gate-programmed channel conductance d<->s
        return ("(V(%s)/vsup)/ron+gmin" % gate) if typ == "n" \
               else "((vsup-V(%s))/vsup)/ron+gmin" % gate
    def classify(model):                        # .model wins; else guess from the name
        m = model.lower()
        if m in pol: return pol[m]
        if any(k in m for k in ("pmos", "pfet", "pch")) or m.startswith("p"): return "p"
        if any(k in m for k in ("nmos", "nfet", "nch")) or m.startswith("n"): return "n"
        return None
    fets = [(d[0], d[1], d[3], d[2], classify(d[4])) for d in dev]  # name,drain,source,gate,typ
    fets = [x for x in fets if x[4] in ("n", "p")]
    nn = sum(1 for x in fets if x[4] == "n"); npc = sum(1 for x in fets if x[4] == "p")
    driven = sorted({n for _, d, s, _, _ in fets for n in (d, s) if n not in (hi, lo)})
    print("* bfit macromodel for cell '%s' (stdcell2bfit: per-FET gate-programmed conductances)" % name)
    print("* inputs=%s  output=%s  rails=%s/%s  (%d NMOS, %d PMOS)"
          % (",".join(ins), out, hi, lo, nn, npc))
    print(".subckt %s %s PARAMS: vsup=3.3 ron=1000 gmin=1e-9 rfloat=1e9 cin=2f cint=0.5f"
          % (name, " ".join(ports)))
    for p in ins:                                       # decouple each input with an R-C load
        print("Cin_%s %s %s {cin}" % (sanit(p), p, lo))
    for nm, d, s, ga, ty in fets:                       # each FET = gate-programmed conductance d<->s
        print("B%s %s %s I={ (%s)*(V(%s)-V(%s)) }" % (sanit(nm), d, s, gexpr(ga, ty), d, s))
    for n in driven:                                    # real-R DC backbone (convergence) + transient cap
        print("Rf_%s %s %s {rfloat}" % (sanit(n), n, lo))
        print("Cn_%s %s %s {cint}" % (sanit(n), n, lo))
    print(".ends")

if __name__ == "__main__":
    main()
