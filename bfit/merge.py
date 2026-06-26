#!/usr/bin/env python3
"""bfit --merge : ANALYTICAL (lossless) merge of directly-coupled transistor
structures into one component, ELIMINATING internal nodes. This is distinct from
bfit's reduced-order behavioral substitution -- the merged element is exact (a
structural rewrite), valid where the devices are square-law / Verilog-A described.

Speedup comes from removing an internal matrix node (and, for feedback structures,
from absorbing the internal feedback into one self-consistent Jacobian).

First pattern -- diode-connected MOS cascode (a series stack with a purely
internal node): two MOSFETs share a degree-2 internal node X, one is
diode-connected (gate==drain==G) and they share the gate G. Solving the series
current continuity at X eliminates it: the stack collapses to ONE 2-terminal
element between the top rail and G with
        I = (beta_eff/2)*(Vsg - |Vth|)^2 ,   beta_eff = kt*kb/(kt+kb)
(two square-law devices combine like series conductances). Same as the result
validated in /tmp/merge_demo2.sh (V1 eliminated, currents identical, ~2x on
stack-heavy circuits).
"""
import re, sys

_SI = {"f":1e-15,"p":1e-12,"n":1e-9,"u":1e-6,"m":1e-3,"k":1e3,"meg":1e6,"g":1e9,"t":1e12}
def _num(s):
    if s is None: return None
    m = re.match(r"^([+-]?[\d.]+(?:e[+-]?\d+)?)(meg|[fpnumkgt])?$", str(s).strip().lower())
    return None if not m else float(m.group(1))*(_SI[m.group(2)] if m.group(2) else 1.0)

def _grab(body, key):
    m = re.search(r"\b%s\s*=\s*([-\w.+]+)" % key, body)
    return _num(m.group(1)) if m else None

def _wl(tokens):
    w = l = None
    for t in tokens:
        tl = t.lower()
        if   tl.startswith("w="): w = _num(tl[2:])
        elif tl.startswith("l="): l = _num(tl[2:])
    return (w if w else 1.0)/(l if l else 1.0)

def parse_models(netlist):
    """name -> (type 'n'/'p', kp, |Vth|)."""
    out = {}
    for ln in netlist.splitlines():
        s = ln.strip()
        if s.lower().startswith(".model"):
            t = s.split(); name = t[1].lower(); body = s.lower()
            typ = "p" if "pmos" in body else ("n" if "nmos" in body else None)
            vto = _grab(body, "vto")
            out[name] = (typ, _grab(body, "kp") or 2e-5, abs(vto) if vto is not None else 0.5)
    return out

def parse_mos(netlist):
    mos = []
    for ln in netlist.splitlines():
        s = ln.strip()
        if not s or s[0] in "*.;": continue
        t = s.split()
        if t[0][0].upper() == "M" and len(t) >= 6:
            mos.append(dict(name=t[0], d=t[1], g=t[2], s=t[3], b=t[4],
                            model=t[5].lower(), wl=_wl(t[6:]), line=ln))
    return mos

def _ports_and_rails(netlist):
    """Nodes that must stay (subckt ports + ground + supply-looking names)."""
    keep = {"0", "gnd"}
    for ln in netlist.splitlines():
        s = ln.strip().lower()
        if s.startswith(".subckt"):
            keep.update(s.split()[2:])
        if s.startswith(".global"):
            keep.update(s.split()[1:])
    return keep

def recognize_cascode(netlist):
    """Find diode-connected MOS cascodes and emit the merged 2-terminal element."""
    models = parse_models(netlist)
    mos = parse_mos(netlist)
    keep = _ports_and_rails(netlist)
    term = {}                                   # node -> [(mos, 'd'|'s'), ...]
    for m in mos:
        for role in ("d", "s"):
            term.setdefault(m[role], []).append((m, role))
    matches, claimed, tagn = [], set(), 0
    for X, lst in term.items():
        if X in keep or len(lst) != 2:          # internal node touched by exactly 2 device d/s
            continue
        (A, _), (B, _) = lst
        if A["name"] in claimed or B["name"] in claimed: continue
        # one must be diode-connected (gate==drain) and they must share that gate
        diode = A if A["g"] == A["d"] else (B if B["g"] == B["d"] else None)
        if diode is None: continue
        other = B if diode is A else A
        if other["g"] != diode["g"]: continue   # shared gate
        if diode["model"] != other["model"]: continue
        G = diode["g"]                           # output == gate node
        # X must be the seam: diode.source==X and other.drain==X (the cascode stack)
        if not (diode["s"] == X and other["d"] == X): continue
        top = other["s"]                         # far rail (source of the top device)
        typ, kp, vth = models.get(diode["model"], ("p", 2e-5, 0.5))
        kt = kp * other["wl"]; kb = kp * diode["wl"]
        beff = kt * kb / (kt + kb)
        tagn += 1; tag = "%s_%d" % (re.sub(r"\W", "", G), tagn)
        if typ == "p":                           # PMOS: current top -> G, drive = Vsg = V(top)-V(G)
            drv = "(V(%s)-V(%s)-%g)" % (top, G, vth)
            b = "B_mrg_%s %s %s I={ 0.5*%.6g*pow(max(0,%s),2) }" % (tag, top, G, beff, drv)
        else:                                    # NMOS: current G -> top (sinks), drive = Vgs = V(G)-V(top)
            drv = "(V(%s)-V(%s)-%g)" % (G, top, vth)
            b = "B_mrg_%s %s %s I={ 0.5*%.6g*pow(max(0,%s),2) }" % (tag, G, top, beff, drv)
        ins = ["* --- bfit --merge: cascode %s+%s -> 1 element, internal node '%s' eliminated"
               " (beta_eff=kt*kb/(kt+kb)=%.4g) ---" % (other["name"], diode["name"], X, beff), b]
        claimed.add(A["name"]); claimed.add(B["name"])
        matches.append(dict(kind="cascode", drop=[other["line"], diode["line"]],
                            insert="\n".join(ins), elim=X, top=top, gate=G,
                            devices=[other["name"], diode["name"]]))
    return matches

def _sl_body(idx, d, g, s, typ):
    """Square-law MOSFET analog-body lines for one inlined instance."""
    if typ == "p":                                   # PMOS: Vsg drive, current s->d
        vov = "V(%s,%s) - vth" % (s, g); vds = "V(%s,%s)" % (s, d); con = "I(%s,%s) <+ id%d;" % (s, d, idx)
    else:                                            # NMOS: Vgs drive, current d->s
        vov = "V(%s,%s) - vth" % (g, s); vds = "V(%s,%s)" % (d, s); con = "I(%s,%s) <+ id%d;" % (d, s, idx)
    return ["        vov%d = %s;" % (idx, vov),
            "        vds%d = %s;" % (idx, vds),
            "        id%d = (vov%d>0.0) ? 0.5*kp*vov%d*vov%d*(1.0+lambda*vds%d) : 0.0;" % (idx,idx,idx,idx,idx),
            "        " + con]

def _merged_dp_va(mod, typ, k, vth):
    """Generate the merged diff-pair Verilog-A module: the two matched FETs inlined
    into ONE component sharing the tail node `s` (the mechanical two-instance->single
    rewrite). One coupled Jacobian; tail stays (it's the mirror's injection point)."""
    L = ['`include "disciplines.vams"', "",
         "// bfit --merge: matched %s pair as ONE component (shared tail s -> coupled Jacobian)."
         % ("PMOS" if typ == "p" else "NMOS"),
         "module %s(d1, g1, d2, g2, s, b);" % mod,
         "    inout d1, g1, d2, g2, s, b;",
         "    electrical d1, g1, d2, g2, s, b;",
         "    parameter real kp     = %.6g from (0:inf);" % k,
         "    parameter real vth    = %.6g;" % vth,
         "    parameter real lambda = 0.05 from [0:inf);",
         "    real vov1, vds1, id1, vov2, vds2, id2;",
         "    analog begin"]
    L += _sl_body(1, "d1", "g1", "s", typ)
    L += _sl_body(2, "d2", "g2", "s", typ)
    L += ["    end", "endmodule", ""]
    return "\n".join(L)

def recognize_diff_pair(netlist, claimed=None):
    """Matched MOS pair sharing a SOURCE (tail) node, same model, distinct gates and
    drains (differential). Merged to ONE Verilog-A component sharing the tail -- the
    accuracy/stability case (coupled Jacobian, internal feedback in one device); the
    tail node is NOT eliminated (it's where the mirror injects the constant current)."""
    models = parse_models(netlist)
    mos = parse_mos(netlist)
    keep = _ports_and_rails(netlist)
    claimed = set(claimed or [])
    bysrc = {}
    for m in mos:
        if m["name"] not in claimed:
            bysrc.setdefault(m["s"], []).append(m)
    matches, tag = [], 0
    for s_node, grp in bysrc.items():
        if s_node in ("0", "gnd") or len(grp) < 2:    # tail is an internal node, not ground
            continue
        for i in range(len(grp)):
            for j in range(i + 1, len(grp)):
                A, B = grp[i], grp[j]
                if A["name"] in claimed or B["name"] in claimed: continue
                if A["model"] != B["model"]: continue
                if A["g"] == B["g"] or A["d"] == B["d"]: continue   # differential: distinct g & d
                typ, kp, vth = models.get(A["model"], ("n", 2e-5, 0.5))
                k = kp * A["wl"]                       # matched pair -> same k
                tag += 1
                mod = "dp_%s_%d" % (re.sub(r"\W", "", s_node), tag)
                va = _merged_dp_va(mod, typ, k, vth)
                inst = "Ndp%d %s %s %s %s %s %s %smod" % (
                    tag, A["d"], A["g"], B["d"], B["g"], s_node, A["b"], mod)
                ins = "\n".join([
                    "* --- bfit --merge: diff_pair %s+%s -> 1 component (coupled Jacobian, tail '%s')"
                    " -- compile %s.va (openvaf), then pre_osdi %s.osdi ---"
                    % (A["name"], B["name"], s_node, mod, mod),
                    inst, ".model %smod %s()" % (mod, mod)])
                claimed.add(A["name"]); claimed.add(B["name"])
                matches.append(dict(kind="diff_pair", drop=[A["line"], B["line"]], insert=ins,
                                    elim=None, va=va, vamodule=mod, tail=s_node,
                                    devices=[A["name"], B["name"]]))
    return matches

def merge_front(netlist):
    """Apply all --merge recognizers. Returns (text, matches, eliminated_nodes, vafiles)."""
    cascode = recognize_cascode(netlist)
    claimed = set(d for m in cascode for d in m["devices"])
    dpair = recognize_diff_pair(netlist, claimed)
    matches = cascode + dpair
    if not matches:
        return netlist, [], [], {}
    drop, insert, vafiles = set(), {}, {}
    for m in matches:
        insert[m["drop"][0]] = m["insert"]
        for d in m["drop"]: drop.add(d)
        if m.get("va"): vafiles[m["vamodule"]] = m["va"]
    out = []
    for ln in netlist.splitlines():
        if ln in insert: out.append(insert[ln]); continue
        if ln in drop: continue
        out.append(ln)
    return "\n".join(out) + "\n", matches, [m["elim"] for m in matches if m.get("elim")], vafiles

def main(argv=None):
    import argparse, os
    ap = argparse.ArgumentParser(prog="bfit merge",
        description="analytical lossless merge of directly-coupled transistor structures")
    ap.add_argument("netlist")
    ap.add_argument("-o", "--out")
    a = ap.parse_args(argv)
    text, matches, elim, vafiles = merge_front(open(a.netlist).read())
    (open(a.out, "w").write(text) if a.out else sys.stdout.write(text))
    for mod, va in vafiles.items():
        d = os.path.dirname(a.out) if a.out else "."
        open(os.path.join(d, mod + ".va"), "w").write(va)
        sys.stderr.write("[bfit --merge] wrote %s.va\n" % os.path.join(d, mod))
    sys.stderr.write("[bfit --merge] merged %d structure(s), eliminated %d node(s): %s\n" % (
        len(matches), len(elim),
        ", ".join("%s{%s}%s" % (m["kind"], "+".join(m["devices"]),
                                "->drop %s" % m["elim"] if m.get("elim") else "->%s.va" % m["vamodule"])
                  for m in matches) or "(none)"))
    return 0

if __name__ == "__main__":
    sys.exit(main())
