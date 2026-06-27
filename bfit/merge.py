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
                            model=t[5].lower(), wl=_wl(t[6:]),
                            params=[x for x in t[6:] if "=" in x], line=ln))
    return mos

def _ports_and_rails(netlist):
    """Nodes that must stay: subckt ports, ground, and rails (any node driven by a
    voltage source -- so a supply isn't mistaken for a diff-pair tail or cascode seam)."""
    keep = {"0", "gnd"}
    for ln in netlist.splitlines():
        s = ln.strip(); low = s.lower()
        if low.startswith(".subckt"):
            keep.update(low.split()[2:])
        elif low.startswith(".global"):
            keep.update(low.split()[1:])
        elif s and s[0].upper() == "V":                 # voltage source -> its nodes are rails
            t = s.split()
            if len(t) >= 3: keep.update(t[1:3])
    return keep

def recognize_cascode(netlist, device_va=None, claimed=None):
    """Find diode-connected MOS cascodes and merge them. Without `device_va`: the
    square-law closed form -> a 2-terminal beta_eff element, internal node ELIMINATED.
    With `device_va` (the real device .va): inline the real device body twice into one
    component, internal node kept as `v1` (exact/coupled; no closed-form elimination)."""
    models = parse_models(netlist)
    mos = parse_mos(netlist)
    keep = _ports_and_rails(netlist)
    term = {}                                   # node -> [(mos, 'd'|'s'), ...]
    for m in mos:
        for role in ("d", "s"):
            term.setdefault(m[role], []).append((m, role))
    matches, claimed, tagn = [], set(claimed or []), 0
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
        tagn += 1
        if device_va:                            # GENERAL: inline the real device VA (node kept)
            mod = "casc_%s_%d" % (re.sub(r"\W", "", G), tagn)
            va = general_merge_cascode_va(device_va, mod, other, diode, X, top, G)
            inst = "Nc%d %s %s %s %smod" % (tagn, top, G, other["b"], mod)
            ins = "\n".join([
                "* --- bfit --merge: cascode %s+%s -> 1 component (general inline of real device VA;"
                " internal node '%s' kept as v1) -- compile %s.va (openvaf), pre_osdi %s.osdi ---"
                % (other["name"], diode["name"], X, mod, mod),
                inst, ".model %smod %s()" % (mod, mod)])
            claimed.add(A["name"]); claimed.add(B["name"])
            matches.append(dict(kind="cascode", drop=[other["line"], diode["line"]], insert=ins,
                                elim=None, va=va, vamodule=mod, top=top, gate=G,
                                devices=[other["name"], diode["name"]]))
            continue
        typ, kp, vth = models.get(diode["model"], ("p", 2e-5, 0.5))
        kt = kp * other["wl"]; kb = kp * diode["wl"]
        beff = kt * kb / (kt + kb)
        tag = "%s_%d" % (re.sub(r"\W", "", G), tagn)
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
         '    (* type="instance" *) parameter real kp     = %.6g from (0:inf);' % k,
         '    (* type="instance" *) parameter real vth    = %.6g;' % vth,
         '    (* type="instance" *) parameter real lambda = 0.05 from [0:inf);',
         "    real vov1, vds1, id1, vov2, vds2, id2;",
         "    analog begin"]
    L += _sl_body(1, "d1", "g1", "s", typ)
    L += _sl_body(2, "d2", "g2", "s", typ)
    L += ["    end", "endmodule", ""]
    return "\n".join(L)

# ---------------------------------------------------------------------------
# General inlined merge -- inline the REAL device Verilog-A (any model), not
# square-law. The merge is a pure structural rewrite: parse the device module,
# and for each instance emit its analog body with (a) port refs remapped to the
# merged terminals (shared node -> the tail), (b) params/locals namespaced per
# instance. Works for any device .va (EKV, BSIM-CMG, ...) with its charge model.
# ---------------------------------------------------------------------------
def parse_va_module(text):
    """Parse a single-module device .va: name, ordered ports, params, locals, body."""
    m = re.search(r"\bmodule\s+(\w+)\s*\(([^)]*)\)", text)
    name = m.group(1)
    ports = [p.strip() for p in m.group(2).split(",") if p.strip()]
    params = {}
    for pm in re.finditer(r"\bparameter\s+\w+\s+(\w+)\s*=\s*([^;,]+)", text):
        params[pm.group(1)] = pm.group(2).split("from")[0].strip()
    locals_ = set()
    for lm in re.finditer(r"(?m)^\s*(real|integer)\s+([^;]+);", text):
        if "parameter" in lm.group(0):
            continue
        for v in lm.group(2).split(","):
            locals_.add(v.strip().split("=")[0].strip())
    am = re.search(r"\banalog\s+begin\b(.*)\bend\b\s*endmodule", text, re.S)
    body = am.group(1) if am else ""
    return dict(name=name, ports=ports, params=params, locals=locals_, body=body)

def _inline_body(mod, node_map, sfx):
    """One instance's body: namespace params/locals (longest-first), then remap
    port refs inside V()/I() access functions to the merged terminals."""
    body = mod["body"]
    for nm in sorted(mod["params"].keys() | mod["locals"], key=len, reverse=True):
        body = re.sub(r"\b%s\b" % re.escape(nm), "%s__%s" % (nm, sfx), body)
    def repl(m):
        fn = m.group(1); args = [a.strip() for a in m.group(2).split(",")]
        return "%s(%s)" % (fn, ", ".join(node_map.get(a, a) for a in args))
    return re.sub(r"\b([VI])\(\s*([\w\s,]*?)\s*\)", repl, body)

def _inst_params(va_params, inst_param_tokens):
    """Map a SPICE instance's k=v tokens onto the device VA's param names (case-insensitive)."""
    iv = {}
    for tok in inst_param_tokens or []:
        k, _, v = tok.partition("="); iv[k.strip().lower()] = v.strip()
    out = {}
    for pn, default in va_params.items():
        out[pn] = iv.get(pn.lower(), default)
    return out

def _build_general(device_va, modname, insts, ports, internals, note,
                   extra_params=None, extra_body=None):
    """Assemble a merged Verilog-A module by inlining the device body once per
    instance. insts = [(node_map {va_port->merged_name}, suffix, param_tokens)];
    ports / internals = merged names that are terminals / internal electrical nodes.
    extra_params/extra_body inject merged-in linear elements (e.g. a bleed resistor)."""
    mod = parse_va_module(device_va)
    L = ['`include "disciplines.vams"', "", "// " + note,
         "module %s(%s);" % (modname, ", ".join(ports)),
         "    inout %s;" % ", ".join(ports),
         "    electrical %s;" % ", ".join(ports)]
    if internals:
        L.append("    electrical %s;  // internal node(s) kept -- a general device has no "
                 "closed-form node elimination" % ", ".join(internals))
    bodies = []
    for node_map, sfx, ptoks in insts:
        pv = _inst_params(mod["params"], ptoks)
        for pn in mod["params"]:
            L.append('    (* type="instance" *) parameter real %s__%s = %s;' % (pn, sfx, pv[pn]))
        if mod["locals"]:
            L.append("    real " + ", ".join("%s__%s" % (ln, sfx) for ln in sorted(mod["locals"])) + ";")
        bodies.append(_inline_body(mod, node_map, sfx))
    for pn, pv in (extra_params or {}).items():
        L.append('    (* type="instance" *) parameter real %s = %s;' % (pn, pv))
    L.append("    analog begin")
    for bd in bodies:
        L.append(bd.rstrip("\n"))
    for ln in (extra_body or []):
        L.append(ln)
    L += ["    end", "endmodule", ""]
    return "\n".join(L)

def _mos_ports(device_va):
    return (parse_va_module(device_va)["ports"] + ["d", "g", "s", "b"])[:4]

def general_merge_diffpair_va(device_va, modname, A_tokens, B_tokens):
    """Matched pair -> one component, shared tail `s` (5 terminals, no node removed)."""
    d, g, s, b = _mos_ports(device_va)
    insts = [({d: "d1", g: "g1", s: "s", b: "b"}, "1", A_tokens),
             ({d: "d2", g: "g2", s: "s", b: "b"}, "2", B_tokens)]
    note = ("bfit --merge GENERAL inline of '%s': matched pair -> one component, shared tail s"
            % parse_va_module(device_va)["name"])
    return _build_general(device_va, modname, insts, ["d1", "g1", "d2", "g2", "s", "b"], [], note)

def general_merge_cascode_va(device_va, modname, other, diode, X, top, G):
    """Series cascode -> one component; internal node X becomes the VA internal node
    `v1` (kept). The single coupled component (stability); unlike the square-law path
    it does NOT remove the node -- a general device has no closed-form elimination."""
    d, g, s, b = _mos_ports(device_va)
    mname = lambda nd: "v1" if nd == X else ("top" if nd == top else ("gout" if nd == G else nd))
    nmA = {d: mname(other["d"]), g: mname(other["g"]), s: mname(other["s"]), b: "b"}
    nmB = {d: mname(diode["d"]), g: mname(diode["g"]), s: mname(diode["s"]), b: "b"}
    insts = [(nmA, "1", other.get("params")), (nmB, "2", diode.get("params"))]
    note = ("bfit --merge GENERAL inline of '%s': cascode -> one component, internal node v1 (=%s)"
            % (parse_va_module(device_va)["name"], X))
    return _build_general(device_va, modname, insts, ["top", "gout", "b"], ["v1"], note)

def recognize_diff_pair(netlist, claimed=None, device_va=None):
    """Matched MOS pair sharing a SOURCE (tail) node, same model, distinct gates and
    drains (differential). Merged to ONE Verilog-A component sharing the tail -- the
    accuracy/stability case (coupled Jacobian, internal feedback in one device); the
    tail node is NOT eliminated (it's where the mirror injects the constant current).
    If `device_va` (the real device .va) is given, the merged module INLINES that real
    device body twice (any model, with its charge); otherwise a square-law body."""
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
        if s_node in keep or len(grp) < 2:            # tail is an internal node, not a rail/port
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
                if device_va:                          # GENERAL: inline the real device .va
                    va = general_merge_diffpair_va(device_va, mod, A["params"], B["params"])
                    how = "general inline of '%s'" % parse_va_module(device_va)["name"]
                else:                                  # built-in square-law body
                    va = _merged_dp_va(mod, typ, k, vth); how = "square-law"
                inst = "Ndp%d %s %s %s %s %s %s %smod" % (
                    tag, A["d"], A["g"], B["d"], B["g"], s_node, A["b"], mod)
                ins = "\n".join([
                    "* --- bfit --merge: diff_pair %s+%s -> 1 component (%s, coupled Jacobian,"
                    " tail '%s') -- compile %s.va (openvaf), then pre_osdi %s.osdi ---"
                    % (A["name"], B["name"], how, s_node, mod, mod),
                    inst, ".model %smod %s()" % (mod, mod)])
                claimed.add(A["name"]); claimed.add(B["name"])
                matches.append(dict(kind="diff_pair", drop=[A["line"], B["line"]], insert=ins,
                                    elim=None, va=va, vamodule=mod, tail=s_node,
                                    devices=[A["name"], B["name"]]))
    return matches

def _find_inverters(netlist):
    """Complementary CMOS inverters: a PMOS + NMOS sharing gate and drain, PMOS to a
    high rail, NMOS to a low rail. -> [{g,out,P,N,vdd,vss}]."""
    models = parse_models(netlist); mos = parse_mos(netlist)
    pol = {m["name"]: models.get(m["model"], (None,))[0] for m in mos}
    invs = []
    for n in mos:
        if pol[n["name"]] != "n": continue
        for p in mos:
            if pol[p["name"]] != "p": continue
            if p["g"] == n["g"] and p["d"] == n["d"]:
                invs.append(dict(g=n["g"], out=n["d"], P=p, N=n, vdd=p["s"], vss=n["s"]))
    return invs

def _xc_body(idx, d, g, s, typ):
    """One square-law transistor body for the cross-coupled component (per-device params)."""
    if typ == "p":
        vov = "V(%s,%s)-vth_%s" % (s, g, idx); vds = "V(%s,%s)" % (s, d); con = "I(%s,%s) <+ id_%s;" % (s, d, idx)
    else:
        vov = "V(%s,%s)-vth_%s" % (g, s, idx); vds = "V(%s,%s)" % (d, s); con = "I(%s,%s) <+ id_%s;" % (d, s, idx)
    # triode + saturation: current rolls off to 0 at the rail (vds->0), so the latch
    # nodes settle instead of running away (sat-only would keep sourcing at the rail).
    return ["        vov_%s = %s;" % (idx, vov),
            "        vds_%s = %s;" % (idx, vds),
            "        id_%s = (vov_%s<=0.0) ? 0.0 : ((vds_%s>=vov_%s)"
            " ? 0.5*kp_%s*vov_%s*vov_%s*(1.0+lam_%s*vds_%s)"
            " : kp_%s*(vov_%s*vds_%s-0.5*vds_%s*vds_%s));"
            % (idx, idx, idx, idx, idx, idx, idx, idx, idx, idx, idx, idx, idx, idx),
            "        " + con]

def _merged_xc_va(modname, devs):
    """Cross-coupled (regenerative) pair as ONE component: 4 transistor bodies inlined,
    the qa<->qb positive feedback resolved inside one coupled Jacobian (stable solve)."""
    L = ['`include "disciplines.vams"', "",
         "// bfit --merge: cross-coupled / regenerative pair -> ONE component.",
         "// The qa<->qb positive feedback lives in one self-consistent Jacobian -> the",
         "// solver no longer fights the loop across separate devices (better convergence).",
         "module %s(qa, qb, vdd, vss);" % modname,
         "    inout qa, qb, vdd, vss;",
         "    electrical qa, qb, vdd, vss;"]
    for idx, d, g, s, typ, kp, vth in devs:
        L.append('    (* type="instance" *) parameter real kp_%s = %.6g, vth_%s = %.6g, lam_%s = 0.05;'
                 % (idx, kp, idx, vth, idx))
        L.append("    real vov_%s, vds_%s, id_%s;" % (idx, idx, idx))
    L.append("    analog begin")
    for idx, d, g, s, typ, kp, vth in devs:
        L += _xc_body(idx, d, g, s, typ)
    L += ["    end", "endmodule", ""]
    return "\n".join(L)

def recognize_xcoupled(netlist, claimed=None):
    """Cross-coupled / regenerative pair: two inverters where each one's gate is the
    OTHER's output (qa<->qb positive feedback) -- SRAM latch, sense amp, comparator
    core. Merged to ONE component so the regenerative loop is solved in a single
    coupled Jacobian (the stability win; nodes qa/qb stay -- they fan out)."""
    invs = _find_inverters(netlist)
    models = parse_models(netlist)
    claimed = set(claimed or [])
    matches, tag = [], 0
    for i in range(len(invs)):
        for j in range(i + 1, len(invs)):
            A, B = invs[i], invs[j]
            names = [A["P"]["name"], A["N"]["name"], B["P"]["name"], B["N"]["name"]]
            if set(names) & claimed or len(set(names)) != 4: continue
            if not (A["g"] == B["out"] and B["g"] == A["out"]): continue   # cross-coupled
            qa, qb, vdd, vss = A["out"], B["out"], A["vdd"], A["vss"]
            pP = models.get(A["P"]["model"], ("p", 2e-5, 0.5)); pN = models.get(A["N"]["model"], ("n", 2e-5, 0.5))
            kpp = pP[1] * A["P"]["wl"]; kpn = pN[1] * A["N"]["wl"]
            devs = [("pa", "qa", "qb", "vdd", "p", kpp, pP[2]), ("na", "qa", "qb", "vss", "n", kpn, pN[2]),
                    ("pb", "qb", "qa", "vdd", "p", kpp, pP[2]), ("nb", "qb", "qa", "vss", "n", kpn, pN[2])]
            tag += 1
            mod = "xc_%s_%s_%d" % (re.sub(r"\W", "", qa), re.sub(r"\W", "", qb), tag)
            va = _merged_xc_va(mod, devs)
            inst = "Nxc%d %s %s %s %s %smod" % (tag, qa, qb, vdd, vss, mod)
            ins = "\n".join([
                "* --- bfit --merge: cross-coupled %s -> 1 component (regenerative qa<->qb feedback"
                " in one coupled Jacobian) -- compile %s.va (openvaf), pre_osdi %s.osdi ---"
                % ("+".join(names), mod, mod),
                inst, ".model %smod %s()" % (mod, mod)])
            for nm in names: claimed.add(nm)
            matches.append(dict(kind="xcoupled",
                                drop=[A["P"]["line"], A["N"]["line"], B["P"]["line"], B["N"]["line"]],
                                insert=ins, elim=None, va=va, vamodule=mod, devices=names))
    return matches

def parse_diodes(netlist):
    """SPICE diodes: D<name> <anode> <cathode> <model> [params]."""
    ds = []
    for ln in netlist.splitlines():
        s = ln.strip()
        if not s or s[0] in "*.;": continue
        t = s.split()
        if t[0][0].upper() == "D" and len(t) >= 4:
            ds.append(dict(name=t[0], a=t[1], c=t[2], model=t[3].lower(),
                           params=[x for x in t[4:] if "=" in x], line=ln))
    return ds

def parse_resistors(netlist):
    """Linear resistors: R<name> <n1> <n2> <value>."""
    rs = []
    for ln in netlist.splitlines():
        s = ln.strip()
        if not s or s[0] in "*.;": continue
        t = s.split()
        if t[0][0].upper() == "R" and len(t) >= 4:
            v = _num(t[3])
            if v: rs.append(dict(name=t[0], n1=t[1], n2=t[2], val=v, line=ln))
    return rs

def _bleed_resistors(netlist, term_map, claimed):
    """Resistors whose BOTH ends are bridge terminals -> merge them into the
    component (a bleed/snubber across the AC or DC nodes). Returns
    (extra_params, extra_body, dropped_lines, resistor_names)."""
    ep, eb, drop, names = {}, [], [], []
    for r in parse_resistors(netlist):
        if r["name"] in claimed: continue
        if r["n1"] in term_map and r["n2"] in term_map and r["n1"] != r["n2"]:
            m1, m2 = term_map[r["n1"]], term_map[r["n2"]]
            pn = "r_" + re.sub(r"\W", "", r["name"])
            ep[pn] = "%.6g" % r["val"]
            eb.append("        I(%s, %s) <+ V(%s, %s)/%s;  // merged-in %s" % (m1, m2, m1, m2, pn, r["name"]))
            drop.append(r["line"]); names.append(r["name"])
    return ep, eb, drop, names

def _sample_diode(device_va=None, vmin=-5.0, vmax=1.0, npts=256):
    """Sample a diode I(Vd) characteristic for the table fallback. Reads isat/n/vt
    from the device .va (or the built-in exp defaults) and evaluates the junction
    over [vmin,vmax]. Returns (vd_list, id_list, cjo). The PyMS limexp->exp issue
    is moot here: we sample the bounded characteristic into a table."""
    import math
    isat, nn, vt, cjo = 1e-14, 1.0, 0.02585, 0.0
    if device_va:
        for k, tgt in (("isat", "isat"), ("is", "isat"), ("n", "n"),
                       ("vt", "vt"), ("cjo", "cjo"), ("cj0", "cjo")):
            m = re.search(r"parameter\s+real\s+%s\s*=\s*([-\w.+eE]+)" % k, device_va)
            if m:
                v = _num(m.group(1))
                if v is not None:
                    if tgt == "isat": isat = v
                    elif tgt == "n": nn = v
                    elif tgt == "vt": vt = v
                    elif tgt == "cjo": cjo = v
    vd, idv = [], []
    for i in range(npts):
        v = vmin + (vmax - vmin) * i / (npts - 1)
        idv.append(isat * (math.exp(min(v / (nn * vt), 40.0)) - 1.0))
        vd.append(v)
    return vd, idv, cjo

def _diode_body(idx, a, c):
    """Built-in exponential diode body (fallback when no real device .va)."""
    return ["        vd_%s = V(%s, %s);" % (idx, a, c),
            "        id_%s = isat*(limexp(vd_%s/vt) - 1.0);" % (idx, idx),
            "        I(%s, %s) <+ id_%s;" % (a, c, idx)]

def _merged_bridge_va(modname, legs, extra_params=None, extra_body=None):
    """Full-bridge as ONE component (built-in exponential diodes), plus any
    merged-in linear elements (e.g. a bleed resistor) via extra_params/extra_body."""
    L = ['`include "disciplines.vams"', "",
         "// bfit --merge: full-bridge rectifier (4 diodes) -> ONE component.",
         "// The which-diode-conducts switching is resolved in one coupled Jacobian.",
         "module %s(a, b, p, n);" % modname,
         "    inout a, b, p, n;",
         "    electrical a, b, p, n;",
         '    (* type="instance" *) parameter real isat = 1e-14 from (0:inf);',
         '    (* type="instance" *) parameter real vt   = 0.02585;']
    for pn, pv in (extra_params or {}).items():
        L.append('    (* type="instance" *) parameter real %s = %s;' % (pn, pv))
    L.append("    real " + ", ".join("vd_%s, id_%s" % (i, i) for i, _, _ in legs) + ";")
    L.append("    analog begin")
    for idx, an, cat in legs:
        L += _diode_body(idx, an, cat)
    for ln in (extra_body or []):
        L.append(ln)
    L += ["    end", "endmodule", ""]
    return "\n".join(L)

def _bridge_table_so(mod, device_va, rbleed):
    """Emit the merged bridge as a table-driven VAE-ABI .so (PyMS table fallback)."""
    import os
    pyms = os.environ.get("PYMS_DIR", "/usr/local/src/xyce/utils/PyMS")
    if pyms not in sys.path: sys.path.insert(0, os.path.join(pyms, "vae"))
    from table_model import emit_bridge_table_so
    vd, idv, cjo = _sample_diode(device_va)
    return emit_bridge_table_so(mod, vd, idv, rbleed=rbleed, cjo=cjo)

def recognize_bridge(netlist, claimed=None, device_va=None, table=False):
    """Full-bridge rectifier: 4 diodes -- two with a common cathode (the + output,
    anodes = the two AC nodes a,b) and two with a common anode (the - output,
    cathodes = a,b).  Merged to ONE 4-terminal component (a,b,p,n); the diode
    switching lives in one coupled Jacobian.  With `device_va` (a 2-port diode .va)
    the real diode body is inlined; else a built-in exponential diode."""
    ds = parse_diodes(netlist)
    claimed = set(claimed or [])
    avail = [d for d in ds if d["name"] not in claimed]
    bycat, byan = {}, {}
    for d in avail:
        bycat.setdefault(d["c"], []).append(d)
        byan.setdefault(d["a"], []).append(d)
    legmap = [("1", "a", "p"), ("2", "b", "p"), ("3", "n", "a"), ("4", "n", "b")]
    matches, tag, used = [], 0, set()
    for p, plist in bycat.items():                      # p = + output (common cathode)
        if len(plist) < 2: continue
        for n, nlist in byan.items():                   # n = - output (common anode)
            if n == p or len(nlist) < 2: continue
            for i in range(len(plist)):
                for j in range(i + 1, len(plist)):
                    D1, D2 = plist[i], plist[j]
                    a, b = D1["a"], D2["a"]
                    if a == b or p in (a, b) or n in (a, b): continue
                    D3 = next((d for d in nlist if d["c"] == a and d["name"] not in used), None)
                    D4 = next((d for d in nlist if d["c"] == b and d["name"] not in used), None)
                    if not D3 or not D4: continue
                    grp = [D1, D2, D3, D4]; names = [d["name"] for d in grp]
                    if len(set(names)) != 4 or set(names) & (claimed | used): continue
                    tag += 1
                    mod = "brg_%s_%d" % (re.sub(r"\W", "", p), tag)
                    # absorb a bleed/snubber resistor across the AC INPUT nodes (a-b); the
                    # output (p-n) resistor is the load and is left external.
                    ep, eb, rdrop, rnames = _bleed_resistors(netlist, {a: "a", b: "b"}, claimed | used)
                    dva = parse_va_module(device_va) if device_va else None
                    note = "bfit --merge bridge: 4 diodes" + (" + %d merged-in resistor(s)" % len(rnames) if rnames else "")
                    ext = ".va"
                    if table:                           # table-driven .so (PyMS fallback)
                        rbleed = next((_num(v) for v in ep.values()), None)
                        va = _bridge_table_so(mod, device_va, rbleed)
                        ext = ".cpp"
                    elif dva and len(dva["ports"]) == 2:  # general: inline real diode .va
                        da, dc = dva["ports"][:2]
                        insts = [({da: m1, dc: m2}, idx, grp[k]["params"])
                                 for k, (idx, m1, m2) in enumerate(legmap)]
                        va = _build_general(device_va, mod, insts, ["a", "b", "p", "n"], [],
                                            note + " (real diode '%s')" % dva["name"], ep, eb)
                    else:                               # built-in exponential diodes
                        va = _merged_bridge_va(mod, legmap, ep, eb)
                    inst = "Nbr%d %s %s %s %s %smod" % (tag, a, b, p, n, mod)
                    build = ("compile %s.cpp (g++ -shared -fPIC), load as table .so"
                             if table else "compile %s.va (openvaf), pre_osdi %s.osdi") % (
                             (mod,) if table else (mod, mod))
                    ins = "\n".join([
                        "* --- bfit --merge%s: %s -> 1 component (switching in one coupled Jacobian)"
                        " -- %s ---" % (" --table" if table else "", "+".join(names + rnames), build),
                        inst, ".model %smod %s()" % (mod, mod)])
                    for nm in names: used.add(nm)
                    matches.append(dict(kind="bridge", drop=[d["line"] for d in grp] + rdrop, insert=ins,
                                        elim=None, va=va, vamodule=mod, ext=ext, devices=names + rnames))
    return matches

def merge_front(netlist, device_va=None, table=False):
    """Apply all --merge recognizers. Returns (text, matches, eliminated_nodes, vafiles).
    `device_va` (real device .va text) -> diff-pair merge inlines it (general); else square-law.
    `table=True` -> emit the merged bridge as a table-driven .so (PyMS fallback) instead of VA.
    vafiles keys carry their extension (.va or .cpp)."""
    xcoupled = recognize_xcoupled(netlist)
    claimed = set(d for m in xcoupled for d in m["devices"])
    bridge = recognize_bridge(netlist, claimed, device_va, table)
    claimed |= set(d for m in bridge for d in m["devices"])
    cascode = recognize_cascode(netlist, device_va, claimed)
    claimed |= set(d for m in cascode for d in m["devices"])
    dpair = recognize_diff_pair(netlist, claimed, device_va)
    matches = xcoupled + bridge + cascode + dpair
    if not matches:
        return netlist, [], [], {}
    drop, insert, vafiles = set(), {}, {}
    for m in matches:
        insert[m["drop"][0]] = m["insert"]
        for d in m["drop"]: drop.add(d)
        if m.get("va"): vafiles[m["vamodule"] + m.get("ext", ".va")] = m["va"]
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
    ap.add_argument("--device-va", help="real device Verilog-A to INLINE (general merge) "
                                        "instead of the built-in square-law body")
    ap.add_argument("--table", action="store_true",
                    help="emit the merged bridge as a table-driven .so (PyMS table fallback) "
                         "-- bounded interpolation that converges where exp does not")
    a = ap.parse_args(argv)
    dev = open(a.device_va).read() if a.device_va else None
    text, matches, elim, vafiles = merge_front(open(a.netlist).read(), dev, a.table)
    (open(a.out, "w").write(text) if a.out else sys.stdout.write(text))
    for fname, src in vafiles.items():
        d = os.path.dirname(a.out) if a.out else "."
        open(os.path.join(d, fname), "w").write(src)
        sys.stderr.write("[bfit --merge] wrote %s\n" % os.path.join(d, fname))
    sys.stderr.write("[bfit --merge] merged %d structure(s), eliminated %d node(s): %s\n" % (
        len(matches), len(elim),
        ", ".join("%s{%s}%s" % (m["kind"], "+".join(m["devices"]),
                                "->drop %s" % m["elim"] if m.get("elim") else "->%s.va" % m["vamodule"])
                  for m in matches) or "(none)"))
    return 0

if __name__ == "__main__":
    sys.exit(main())
