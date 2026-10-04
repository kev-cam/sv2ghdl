#!/usr/bin/env python3
"""stdcell2bfit.py -- generate a bfit behavioral macromodel from a static-CMOS
standard-cell transistor netlist.

A static CMOS cell's logic *is* its transistor network: each FET is a
gate-programmed conductance between its drain and source. We emit ONE such
conductance per transistor and let the solver compute every node voltage -- so
series stacks (AND of gate conditions), parallel legs (OR), AND internal nodes
of multi-stage compound cells (AND = NAND+inv, XOR, muxes, C-elements with
keepers...) all resolve from topology alone. Each driven node gets a weak
rfloat/cint pair (DC anchor + transient stability). The inverter is the
1-transistor-per-rail case.

MODEL FORM v2 (threshold square-law -- the hysteretic fix). The old form was a
LINEAR gate-programmed conductance (NMOS ~ V(g)/vsup): every FET half-conducts
at mid-rail, so both networks fight everywhere -- measured on the th22
C-element it overcounted energy 3-10x (crowbar that physically does not
exist), ran ~10x too fast, and its conductance-divider transfer has trip-point
gain <1, the exact defect that made library/cmos_inv v1 collapse chains to
mid-rail. v2 conduction is THRESHOLDED, LINEAR in overdrive (--alpha 1,
the default -- short-channel FETs are velocity-saturated, I ~ overdrive^~1;
--alpha 2 emits the long-channel square-law):

    NMOS:  g = IF( V(g) > vthr,        s * ((V(g)-vthr)/(vsup-vthr))^a      / ron, 0) + gm0
    PMOS:  g = IF( vsup-V(g) > vthr, s*kp * ((vsup-V(g)-vthr)/(vsup-vthr))^a / ron, 0) + gm0

(Xyce-safe: IF() not max/pow -- ^a is emitted as repeated factors; `vthr`/
`gm0` because `vt`/`gmin` are RESERVED Xyce param names.) A FET is OFF below
threshold, so crowbar flows only while an input actually traverses the trip
region -- physical short-circuit energy -- and the transfer regenerates rails
(gain > 1 through the threshold), which also makes KEEPER cells hold state
instead of leaking to a divider level.  Measured on th22/SG13G2 (130 nm):
alpha=1 fits fast-edge AND slow-edge (3-stage chain) delay to ~2% at once;
alpha=2 cannot do better than -17%/+7% -- too slew-sensitive.

STRENGTH from geometry: each FET's conductance is scaled by s = (W/L) /
(W/L)_max of its own polarity, so `ron` means "ON resistance of the
strongest device of that polarity at full overdrive". `kp` trims the
P-network vs N-network drive (the cell's own 2:1 sizing usually makes kp~1).
FEEDBACK/KEEPER devices -- gate tied to a port the cell itself drives (the
Sutherland C-element keeper: gate on Y, channel back onto X) -- get an extra
multiplier `kfb`, the tunable knob for keeper fight energy (55% of th22's
44.6 fJ/op is the keeper; a model without this knob is the liberty bug again).

Run ONCE per cell (pre-simulation) to build the bfit model library; tune
vthr/ron/kp/kfb/cin/cint against the transistor reference with `bfit tune`
(fit.json energy + delay features).

Usage: stdcell2bfit.py cell.cir [subckt] [--alpha {1,2}]  -> emits the .subckt
"""
import sys, re

_SI = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3,
       "k": 1e3, "meg": 1e6, "g": 1e9, "t": 1e12}
def spice_num(s):
    m = re.match(r"^([+-]?[\d.]+(?:e[+-]?\d+)?)(meg|[fpnumkgt])?$", s.strip().lower())
    if not m: return None
    return float(m.group(1)) * (_SI[m.group(2)] if m.group(2) else 1.0)

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
            if len(t) >= 6:
                w = l = None
                for x in t[6:]:
                    xl = x.lower()
                    if   xl.startswith("w="): w = spice_num(xl[2:])
                    elif xl.startswith("l="): l = spice_num(xl[2:])
                wl = (w / l) if (w and l) else 1.0
                blocks[cur]["dev"].append((t[0], t[1], t[2], t[3], t[5], wl))
    if want and want in blocks: return want, blocks[want]
    return (next(iter(blocks.items())) if blocks else (None, None))

def sanit(n):
    return re.sub(r"[^A-Za-z0-9_]", "_", n)

def main():
    argv = list(sys.argv[1:]); alpha = 1
    if "--alpha" in argv:
        i = argv.index("--alpha"); alpha = int(argv[i+1]); del argv[i:i+2]
    f = argv[0]; want = argv[1] if len(argv) > 1 else None
    text = open(f).read(); pol = pol_map(text)
    name, blk = parse_subckt(text, want)
    if not blk: sys.exit("no .subckt found in %s" % f)
    ports, dev = blk["ports"], blk["dev"]
    gates = {d[2] for d in dev}
    hi = next((p for p in ports if re.match(r"(?i)^(v?dd|vcc|vpwr|vp)$", p)), ports[-2])
    lo = next((p for p in ports if re.match(r"(?i)^(v?ss|gnd|vgnd|vp?n|0)$", p)), ports[-1])
    def classify(model):                        # .model wins; else guess from the name
        m = model.lower()
        if m in pol: return pol[m]
        if any(k in m for k in ("pmos", "pfet", "pch")) or m.startswith("p"): return "p"
        if any(k in m for k in ("nmos", "nfet", "nch")) or m.startswith("n"): return "n"
        return None
    # name, drain, source, gate, type, W/L
    fets = [(d[0], d[1], d[3], d[2], classify(d[4]), d[5]) for d in dev]
    fets = [x for x in fets if x[4] in ("n", "p")]
    nn = sum(1 for x in fets if x[4] == "n"); npc = sum(1 for x in fets if x[4] == "p")
    driven = sorted({n for _, d, s, _, _, _ in fets for n in (d, s) if n not in (hi, lo)})
    outs = [p for p in ports if p in driven]
    ins = [p for p in ports if p in gates and p not in driven]
    # strength: normalize W/L within each polarity (ron = strongest device ON)
    wlmax = {t: max([x[5] for x in fets if x[4] == t] or [1.0]) for t in ("n", "p")}
    # keeper/feedback devices: gate tied to a PORT this cell itself drives (its
    # output fed back) -- NOT any internal driven node, or every second stage
    # of a compound cell would be misclassified as a keeper.
    fb = {nm for nm, _, _, ga, _, _ in fets if ga in driven and ga in ports}
    def gexpr(gate, typ, s, isfb):              # thresholded square-law channel conductance
        s_lit = "%.6g%s%s" % (s, "*kp" if typ == "p" else "", "*kfb" if isfb else "")
        if typ == "n":
            od, on = "V(%s)-vthr" % gate, "V(%s)>vthr" % gate
        else:
            od, on = "vsup-V(%s)-vthr" % gate, "(vsup-V(%s))>vthr" % gate
        q = "*".join(["((%s)/(vsup-vthr))" % od] * alpha)
        return "IF(%s,%s*%s/ron,0)+gm0" % (on, s_lit, q)
    print("* bfit macromodel for cell '%s' (stdcell2bfit v2: thresholded square-law" % name)
    print("* gate-programmed conductances, W/L-scaled, keeper-aware)")
    print("* inputs=%s  driven-ports=%s  rails=%s/%s  (%d NMOS, %d PMOS%s)"
          % (",".join(ins), ",".join(outs) or "?", hi, lo, nn, npc,
             ", keeper: " + ",".join(sorted(fb)) if fb else ""))
    print(".subckt %s %s PARAMS: vsup=3.3 vthr=0.6 ron=1000 kp=1 kfb=1 gm0=1e-9"
          " rfloat=1e9 cin=2f cint=0.5f" % (name, " ".join(ports)))
    for p in ports:                                     # R-C load on every gate-driven port
        if p in gates:
            print("Cin_%s %s %s {cin}" % (sanit(p), p, lo))
    for nm, d, s, ga, ty, wl in fets:                   # each FET = programmed conductance d<->s
        print("B%s %s %s I={ (%s)*(V(%s)-V(%s)) }"
              % (sanit(nm), d, s, gexpr(ga, ty, wl / wlmax[ty], nm in fb), d, s))
    for n in driven:                                    # real-R DC backbone (convergence) + transient cap
        print("Rf_%s %s %s {rfloat}" % (sanit(n), n, lo))
        print("Cn_%s %s %s {cint}" % (sanit(n), n, lo))
    print(".ends")

if __name__ == "__main__":
    main()
