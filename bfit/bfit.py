#!/usr/bin/env python3
"""
bfit -- behavioral-fit: auto-tune a portable Verilog-AMS macromodel to match a
device-level reference, through ANY simulator (engine-neutral, via a pluggable
sim-driver). Standalone front-end accelerator: recognize circuit patterns,
substitute auto-tuned signal-flow macromodels, cache the parameters.

This module is the ENGINE: SimDriver interface, the optimizer, and the tuner.
Sim-specific code lives only in the drivers (xyce, ngspice/OpenVAF, ...).
"""
import os, sys, json, tempfile, subprocess, argparse, math, re

# ----------------------------------------------------------------------------
# Sim-driver interface -- the ONLY engine-specific surface. A driver runs a
# netlist (standard SPICE; the macromodel is portable Verilog-AMS or its subckt
# realization) and returns {signal_name: [values], "time": [...]}.
# ----------------------------------------------------------------------------
class SimDriver:
    name = "base"
    def run(self, netlist_text, signals=None):   # -> dict[str, list[float]]
        raise NotImplementedError

class XyceDriver(SimDriver):
    name = "xyce"
    def __init__(self, binary=None, env=None):
        self.binary = binary or os.environ.get("BFIT_XYCE",
            "/usr/local/src/xyce-build/src/Xyce")
        self.env = dict(os.environ)
        if env: self.env.update(env)
        b = "/usr/local/src/xyce-build/src"
        self.env.setdefault("LD_LIBRARY_PATH",
            f"{os.path.expanduser('~')}/xyce-libs:{b}:/usr/local/lib:{b}/../utils/XyceCInterface")
    def run(self, netlist_text, signals=None):
        deck = strip_output(netlist_text)
        if signals:
            deck += ".print tran format=gnuplot " + " ".join("v(%s)" % s for s in signals) + "\n"
        deck += ".end\n"
        d = tempfile.mkdtemp(prefix="bfit_")
        cir = os.path.join(d, "c.cir")
        open(cir, "w").write(deck)
        r = subprocess.run([self.binary, cir], cwd=d, env=self.env,
                           capture_output=True, text=True, timeout=300)
        prn = cir + ".prn"
        if not os.path.exists(prn):
            raise RuntimeError(f"xyce: no .prn ({r.stdout[-300:]})")
        return _parse_prn(prn)

def _parse_prn(p):
    lines = open(p).read().splitlines()
    hdr = lines[0].split()
    keys = [h.lower() for h in hdr]
    cols = {k: [] for k in keys}
    for ln in lines[1:]:
        s = ln.split()
        if not s: continue
        try: float(s[0])
        except ValueError: continue
        for i, k in enumerate(keys):
            if i < len(s):
                try: cols[k].append(float(s[i]))
                except ValueError: pass
    out = {}
    for k, v in cols.items():
        out[k] = v
        if k.startswith("v(") and k.endswith(")"): out[k[2:-1]] = v   # v(c1)->c1
    return out

def strip_output(netlist):
    """Remove engine-specific output directives so each driver injects its own.
    Lets one portable netlist (devices + .tran + .model + .subckt) run on any
    engine -- the driver owns the I/O dialect (.print vs .control/write)."""
    out, in_ctrl = [], False
    for ln in netlist.splitlines():
        s = ln.strip().lower()
        if s.startswith(".control"): in_ctrl = True; continue
        if in_ctrl:
            if s.startswith(".endc"): in_ctrl = False
            continue
        if s.startswith((".print", ".plot", ".probe", ".save")): continue
        if s == ".end": continue
        out.append(ln)
    return "\n".join(out) + "\n"

DRIVERS = {"xyce": XyceDriver}
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from drivers_ngspice import NgspiceDriver
    DRIVERS["ngspice"] = NgspiceDriver
except Exception:
    pass
try:
    from drivers_vacask import VacaskDriver
    DRIVERS["vacask"] = VacaskDriver          # AGPL OpenVAF-native, replaces ngspice
except Exception:
    pass

# ----------------------------------------------------------------------------
# Feature extraction -- signal-flow metrics (steady state). What the fit matches.
# ----------------------------------------------------------------------------
def _steady(sig, t, t0):
    return [s for s, tt in zip(sig, t) if tt >= t0]
def feature(data, node, kind, t0):
    if node not in data: raise KeyError(f"signal {node} not in output {list(data)[:8]}")
    seg = _steady(data[node], data["time"], t0)
    if kind == "amp": return (max(seg) - min(seg)) / 2.0
    if kind == "min": return min(seg)
    if kind == "max": return max(seg)
    if kind == "pp":  return max(seg) - min(seg)
    if kind == "rms": return math.sqrt(sum(x*x for x in seg)/len(seg))
    raise ValueError(kind)
def features(data, spec, t0):
    return {f"{n}.{k}": feature(data, n, k, t0) for n, k in spec}

# ----------------------------------------------------------------------------
# Optimizer -- self-contained Nelder-Mead (no scipy dependency).
# ----------------------------------------------------------------------------
def nelder_mead(f, x0, step, bounds, tol=1e-3, maxit=120):
    n = len(x0)
    def clip(x): return [min(max(v, b[0]), b[1]) for v, b in zip(x, bounds)]
    simplex = [clip(list(x0))]
    for i in range(n):
        y = list(x0); y[i] += step[i]; simplex.append(clip(y))
    fv = [f(x) for x in simplex]
    for _ in range(maxit):
        order = sorted(range(n+1), key=lambda i: fv[i])
        simplex = [simplex[i] for i in order]; fv = [fv[i] for i in order]
        if abs(fv[-1]-fv[0]) <= tol*(abs(fv[0])+1e-12): break
        cen = [sum(simplex[i][j] for i in range(n))/n for j in range(n)]
        ref = clip([cen[j] + 1.0*(cen[j]-simplex[-1][j]) for j in range(n)])
        fr = f(ref)
        if fr < fv[0]:
            exp = clip([cen[j] + 2.0*(cen[j]-simplex[-1][j]) for j in range(n)])
            fe = f(exp)
            simplex[-1], fv[-1] = (exp, fe) if fe < fr else (ref, fr)
        elif fr < fv[-2]:
            simplex[-1], fv[-1] = ref, fr
        else:
            con = clip([cen[j] + 0.5*(simplex[-1][j]-cen[j]) for j in range(n)])
            fc = f(con)
            if fc < fv[-1]:
                simplex[-1], fv[-1] = con, fc
            else:
                for i in range(1, n+1):
                    simplex[i] = clip([simplex[0][j] + 0.5*(simplex[i][j]-simplex[0][j]) for j in range(n)])
                    fv[i] = f(simplex[i])
    i = min(range(n+1), key=lambda i: fv[i])
    return simplex[i], fv[i]

# ----------------------------------------------------------------------------
# The tuner: run reference (target features), then optimize the candidate's
# params so its features match -- both through the SAME driver.
# ----------------------------------------------------------------------------
def tune(reference_netlist, template_text, spec, driver, t0=1.5e-3, verbose=True):
    names = list(spec["params"].keys())
    bounds = [tuple(spec["params"][k]) for k in names]
    x0 = [spec["x0"][k] for k in names]
    step = [max(abs(v)*0.4, (b[1]-b[0])*0.1) for v, b in zip(x0, bounds)]
    fit = spec["features"]
    sigs = sorted({n for n, _ in fit})
    tgt = features(driver.run(reference_netlist, sigs), fit, t0)
    if verbose: print(f"[{driver.name}] target features (device-level reference):",
                      {k: round(v,4) for k,v in tgt.items()})
    evals = [0]
    def obj(x):
        evals[0] += 1
        net = template_text
        for k, v in zip(names, x): net = net.replace("__%s__" % k, repr(v))
        try: fv = features(driver.run(net, sigs), fit, t0)
        except Exception:
            return 1e6
        return sum(((fv[k]-tgt[k])/(abs(tgt[k])+1e-9))**2 for k in tgt)
    xbest, fbest = nelder_mead(obj, x0, step, bounds)
    params = dict(zip(names, xbest))
    if verbose:
        print(f"converged: residual={fbest:.4e} in {evals[0]} sim evals")
        net = template_text
        for k, v in params.items(): net = net.replace("__%s__" % k, repr(v))
        fv = features(driver.run(net, sigs), fit, t0)
        for k in tgt:
            print(f"  {k:10s} target {tgt[k]:+11.4g}  fitted {fv[k]:+11.4g}  "
                  f"({100*(fv[k]-tgt[k])/(abs(tgt[k])+1e-9):+.1f}%)")
    return params, fbest

# ----------------------------------------------------------------------------
# Front end: recognize circuit patterns, substitute auto-tuned macromodels.
# Gated per-engine by <SIM>_USE_BFIT  (e.g. XYCE_USE_BFIT):
#   off            -> passthrough (no substitution)            [default]
#   auto           -> substitute every recognized pattern
#   models:a,b,c   -> substitute only the listed macromodels
# Params come from the cache (hit) or the library best-guess (miss -> the block
# is substituted AND flagged so the background tuner refines it next).
# ----------------------------------------------------------------------------
def env_mode(sim):
    v = os.environ.get("%s_USE_BFIT" % sim.upper(), "off").strip().lower()
    if v in ("", "off"):        return ("off", [])
    if v == "auto":             return ("auto", [])
    if v.startswith("models:"): return ("models", [m.strip() for m in v[7:].split(",") if m.strip()])
    return ("off", [])          # unknown value -> safe default

def recognize_ce(netlist):
    """Rule-based recognizer for a common-emitter gain stage:
       BJT Q(c,b,e) with Rc[c<->vcc] + Rb[b<->vcc] (sharing the supply node)
       + Re[e<->gnd] (+ optional Ce bypass). in=base, out=collector."""
    R, C, Q = [], [], []
    for ln in netlist.splitlines():
        s = ln.strip()
        if not s or s[0] in "*.;": continue
        t = s.split(); u = t[0][0].upper()
        if   u == "R" and len(t) >= 4: R.append((t[0], t[1], t[2], ln))
        elif u == "C" and len(t) >= 4: C.append((t[0], t[1], t[2], ln))
        elif u == "Q" and len(t) >= 5: Q.append((t[0], t[1], t[2], t[3], ln))  # c b e
    other = lambda r, n: r[2] if r[1] == n else r[1]
    matches = []
    for (qn, c, b, e, qraw) in Q:
        rc = [r for r in R if c in (r[1], r[2])]
        rb = [r for r in R if b in (r[1], r[2])]
        re = [r for r in R if e in (r[1], r[2]) and "0" in (r[1], r[2])]
        vcc = rcsel = rbsel = None
        for r1 in rc:
            for r2 in rb:
                if other(r1, c) == other(r2, b) and other(r1, c) != "0":
                    vcc, rcsel, rbsel = other(r1, c), r1, r2; break
            if vcc: break
        if not vcc: continue
        ce = [x for x in C if e in (x[1], x[2]) and "0" in (x[1], x[2])]
        matches.append(dict(model="ce_stage",
                            insert="X%s %s %s ce_stage" % (qn, b, c),
                            vc="x%s (%s %s) cestage" % (qn, b, c),   # VACASK instance
                            drop=[qraw, rcsel[3], rbsel[3]] +
                                 ([re[0][3]] if re else []) + ([ce[0][3]] if ce else [])))
    return matches

def _wl(tokens):
    w = l = None
    for t in tokens:
        tl = t.lower()
        if   tl.startswith("w="): w = _spice_num(tl[2:])
        elif tl.startswith("l="): l = _spice_num(tl[2:])
    return (w if w else 1.0) / (l if l else 1.0)

# Verilog-A current-mirror OUTPUT leg.  Three regimes via if/else (NO tanh, NO
# max -- cheap): cutoff (off), saturation (current source gain*ov with finite
# output conductance gain*lam -- a HIGH resistance off a far rail), triode
# (resistive gain*Vds -- a LOW resistance pulling the drain to its source rail).
# Continuous at the boundaries, so no event is needed for correctness.  A
# @(cross()) at each boundary would pin the acceptance timestep there, but this
# OpenVAF build rejects cross() (only initial_step/final_step), and PyMS parses+
# ignores it -- so it is omitted; re-add once PyMS honors cross() (it FD's the
# Jacobian to track the live regime regardless).  gain/vth/lam/pol are baked per
# ratio so no model-card override is needed (the PyMS math .so does not yet
# thread .model params).  pol=+1 NMOS, -1 PMOS.
CMOUT_VA = '''`include "disciplines.vams"

module {mod}(g, d, s);
    inout g, d, s;
    electrical g, d, s;
    (* type="instance" *) parameter real gain = {gain:.7g};
    (* type="instance" *) parameter real vth  = {vth:.7g};
    (* type="instance" *) parameter real lam  = {lam:.7g};
    (* type="instance" *) parameter real pol  = {pol:.7g};
    real ov, vds, iout;
    analog begin
        ov  = pol*V(g,s) - vth;
        vds = pol*V(d,s);
        if (ov <= 0.0)
            iout = 0.0;                       // cutoff
        else if (vds >= ov)
            iout = gain*ov*(1.0 + lam*vds);   // saturation: current source, high Rout
        else
            iout = gain*vds;                  // triode: resistive pull to source rail
        I(d,s) <+ pol*iout;
    end
endmodule
'''

def recognize_mirror(netlist, sim="xyce"):
    """MOSFET current-mirror groups: a diode-connected reference (drain==gate)
    and EVERY output FET sharing its gate node, source rail and model -- one
    reference fans out to many outputs (op-amp mirror banks). Emits:
      I->V at the reference -- a `vt` source off the rail feeds a sense resistor,
        so the reference current develops an overdrive VOLTAGE on the gate node;
        the resistor is 1 ohm when the reference is the smallest device (sizes
        are normalized on the smallest in the group).
      V->I at each output -- a `cmout` Verilog-A leg (see CMOUT_VA): saturation
        current = (size ratio) x overdrive with a finite output conductance, and
        a triode regime that pulls the drain to its source rail once it droops
        below saturation, so the node is never a pure current source (which has
        a vanishing output conductance -> singular matrix in Xyce).  vth/lambda
        come from the device .model; gain = size ratio; one VA module per
        distinct (gain, pol, vth, lam), carried in match['va'] for front() to
        write and wire up (.hdl + .model)."""
    pol, vto, lam = {}, {}, {}                         # MOS model -> pol / VTO / LAMBDA
    for ln in netlist.splitlines():
        s = ln.strip().lower()
        if s.startswith(".model") and len(s.split()) >= 3:
            nm = s.split()[1]
            if   "pmos" in s: pol[nm] = "p"
            elif "nmos" in s: pol[nm] = "n"
            mv = re.search(r"\bvto?\s*=\s*([-+\d.eE]+)", s)
            ml = re.search(r"\blambda\s*=\s*([-+\d.eE]+)", s)
            vto[nm] = abs(float(mv.group(1))) if mv else 0.6
            lam[nm] = float(ml.group(1)) if ml else 0.02
    M = []
    for ln in netlist.splitlines():
        s = ln.strip()
        if not s or s[0] in "*.;": continue
        t = s.split()
        if t[0][0].upper() == "M" and len(t) >= 6:
            M.append((t[0], t[1], t[2], t[3], t[5], _wl(t[6:]), ln))  # name d g s model wl line
    matches, claimed, modcache = [], set(), {}
    for nr, dr, gr, sr, mr, wlr, lr in M:
        if dr != gr or nr in claimed:                 # reference must be diode-connected
            continue
        outs = [(no, do, wlo, lo) for (no, do, go, so, mo, wlo, lo) in M
                if go == gr and so == sr and mo == mr and do != go and no not in claimed]
        if not outs:
            continue
        smin = min([wlr] + [w for _, _, w, _ in outs])   # normalize on the smallest device
        claimed.add(nr)
        p = pol.get(mr.lower(), "n" if sr == "0" else "p")   # .model, else rail-is-gnd heuristic
        vth = vto.get(mr.lower(), 0.6); lm = lam.get(mr.lower(), 0.02)
        polv = 1.0 if p == "n" else -1.0
        tag = re.sub(r"\W", "", gr); R = "%.4g" % (smin / wlr)   # = 1 when ref is smallest
        L = ["* --- bfit: current_mirror (VA cmout legs, %s, ref %s, %d output%s) ---"
             % ("NMOS" if p == "n" else "PMOS", gr, len(outs), "s" if len(outs) > 1 else "")]
        vcL = ["// --- bfit: current_mirror (VA cmout legs, ref %s, %d outputs) ---" % (gr, len(outs))]
        if p == "p":          # rail above gate: vt drops below rail; outputs SOURCE rail->out
            L += ["Vt_%s %s cmv_%s %.7g" % (nr, sr, tag, vth),
                  "R1_%s cmv_%s %s %s" % (nr, tag, gr, R)]
            vcL += ["vt_%s (%s cmv_%s) v dc=%.7g" % (nr, sr, tag, vth),
                    "r1_%s (cmv_%s %s) rcm r=%s" % (nr, tag, gr, R)]
        else:                 # rail below gate (gnd): vt rises above rail; outputs SINK out->rail
            L += ["Vt_%s cmv_%s %s %.7g" % (nr, tag, sr, vth),
                  "R1_%s %s cmv_%s %s" % (nr, gr, tag, R)]
            vcL += ["vt_%s (cmv_%s %s) v dc=%.7g" % (nr, tag, sr, vth),
                    "r1_%s (%s cmv_%s) rcm r=%s" % (nr, gr, tag, R)]
        va = []
        for no, do, wlo, lo in outs:                  # V->I: one cmout VA leg per output
            claimed.add(no)
            gain = wlo / smin
            key = (round(gain, 6), polv, round(vth, 6), round(lm, 6))
            if key not in modcache:
                mod = "cmout_%d" % (len(modcache) + 1)
                modcache[key] = mod
                va.append((mod, CMOUT_VA.format(mod=mod, gain=gain, vth=vth, lam=lm, pol=polv)))
            mod = modcache[key]
            # Verilog-A leg: ports (g, d, s) = (gate/ref, drain/out, source/rail)
            inst = ("Y%s %s" % (mod, no)) if sim == "xyce" else ("N%s" % no)
            L.append("%s %s %s %s %smod" % (inst, gr, do, sr, mod))
            # small grounded cap on the output: gives the otherwise-instantaneous
            # mirror leg a pole (frequency response) and stabilises the high-Z
            # output node for the solver.
            L.append("Ccm_%s %s 0 1f" % (no, do))
            vcL += ["x_%s (%s %s %s) %scard" % (no, gr, do, sr, mod),
                    "ccm_%s (%s 0) ccm c=1f" % (no, do)]
        drop = [lr] + [lo for _, _, _, lo in outs]
        matches.append(dict(model="current_mirror", inline=True,
                            insert="\n".join(L), vc="\n".join(vcL),
                            drop=drop, va=va))  # drop[0]=ref line (anchor)
    return matches

def recognize_inverter(netlist):
    """CMOS logic inverter: a complementary NMOS+PMOS pair sharing gate (in) and
    drain (out), PMOS to a high rail, NMOS to gnd. Replaced by a smooth
    rail-to-rail inverting transfer through Rout, so the kept load cap sets the
    propagation delay -- a behavioral logic gate the solver can stride across.
    (NAND/NOR generalize this with series/parallel pull networks.)"""
    pol = {}
    for ln in netlist.splitlines():
        s = ln.strip().lower()
        if s.startswith(".model") and len(s.split()) >= 3:
            nm = s.split()[1]
            if   "pmos" in s: pol[nm] = "p"
            elif "nmos" in s: pol[nm] = "n"
    M = []
    for ln in netlist.splitlines():
        s = ln.strip()
        if not s or s[0] in "*.;": continue
        t = s.split()
        if t[0][0].upper() == "M" and len(t) >= 6:
            M.append((t[0], t[1], t[2], t[3], t[5], ln))   # name d g s model line
    matches, claimed = [], set()
    for nn, dn, gn, sn, mn, ln_ in M:                       # candidate pull-down (NMOS to gnd)
        if pol.get(mn.lower()) != "n" or sn != "0" or nn in claimed: continue
        for npc, dp, gp, sp, mp, lp in M:                   # matching pull-up (PMOS to a rail)
            if pol.get(mp.lower()) != "p" or npc in claimed: continue
            if gp == gn and dp == dn and sp != "0":         # shared gate+drain, PMOS to high rail
                claimed.add(nn); claimed.add(npc)
                # Regenerative clamped-linear gate (v2), no tanh: a linear inverting
                # transfer with gain __g__ about the vhi/2 trip point, hard-clamped to
                # [0, vhi], driving the kept load C through __rout__ (delay = rout*C).
                # g>1 regenerates rail-to-rail levels through arbitrary chain depth --
                # the old conductance-divider form had trip-point gain <1 and collapsed
                # a chain to mid-rail. Piecewise-linear, so the solver strides between
                # input edges. Identical transfer to the Verilog-A cmos_inv.va module.
                L = ["* --- bfit: cmos_inv (in %s, out %s, vhi %s) ---" % (gn, dn, sp),
                     "Cin_%s %s 0 __cin__" % (nn, gn),                       # decouple input (R-C load)
                     "Bo_%s 0 %s I={ (max(0, min(V(%s), 0.5*V(%s) - __g__*(V(%s)-0.5*V(%s)))) - V(%s))/__rout__ }"
                     % (nn, dn, sp, sp, gn, sp, dn)]
                matches.append(dict(model="cmos_inv", inline=True,
                                    insert="\n".join(L),
                                    vc="x%s (%s %s %s) cmosinv" % (nn, gn, dn, sp),  # in out vhi
                                    drop=[ln_, lp]))  # drop[0]=NMOS (anchor)
                break
    return matches

def recognize_bridge(netlist):
    """Full-bridge rectifier: four diodes with the AC across two nodes (a,b), two
    diodes conducting into the shared DC output, two returning to gnd. Replaced by
    a behavioral full-wave rectifier (no tanh): Vrect = max(0,|V(a)-V(b)|-2*vdrop)
    charges the output one way through rs (diode conduction), with a reverse-leakage
    path; the kept load R+C set the ripple. One B-source for four exp() diodes."""
    D = []
    for ln in netlist.splitlines():
        s = ln.strip()
        if not s or s[0] in "*.;": continue
        t = s.split()
        if t[0][0].upper() == "D" and len(t) >= 4:
            D.append((t[0], t[1], t[2], ln))      # name anode cathode line
    into = {}                                     # cathode -> [(anode, line)]
    for nm, an, ca, ln in D:
        into.setdefault(ca, []).append((an, ln))
    matches, claimed = [], set()
    for out, tops in into.items():
        if len(tops) < 2 or out == "0": continue
        for i in range(len(tops)):
            for j in range(i + 1, len(tops)):
                a, la = tops[i]; b, lb = tops[j]
                if a == b or la in claimed or lb in claimed: continue
                # bottom: a diode 0->a and a diode 0->b (anode gnd, cathode the AC nodes)
                ga = next((l for an, l in into.get(a, []) if an == "0" and l not in claimed), None)
                gb = next((l for an, l in into.get(b, []) if an == "0" and l not in claimed), None)
                if not ga or not gb: continue
                for l in (la, lb, ga, gb): claimed.add(l)
                tag = re.sub(r"\W", "", out)
                L = ["* --- bfit: bridge_rect (ac %s,%s -> %s) ---" % (a, b, out),
                     "Brect_%s 0 %s I={ max(0, (max(0, abs(V(%s)-V(%s)) - 2*__vdrop__)"
                     "-V(%s))/__rs__) - V(%s)/__rleak__ }" % (tag, out, a, b, out, out),
                     # the dropped diodes were the AC nodes' only DC path to ground;
                     # restore it with their reverse-leakage resistance (Xyce flags the
                     # orphan as a singular matrix; ngspice only hid it under gmin)
                     "Rleak_%s_a %s 0 __rleak__" % (tag, a),
                     "Rleak_%s_b %s 0 __rleak__" % (tag, b)]
                matches.append(dict(model="bridge_rect", inline=True,
                                    insert="\n".join(L),
                                    vc="x%s (%s %s %s) brm" % (tag, a, b, out),  # a b out
                                    drop=[la, lb, ga, gb]))  # drop[0]=anchor
                break
    return matches

def _logical_lines(netlist):
    """Join SPICE '+' continuation lines (analysis only -- never for anchors)."""
    out = []
    for ln in netlist.splitlines():
        s = ln.strip()
        if s.startswith("+") and out:
            out[-1] += " " + s[1:]
        else:
            out.append(s)
    return out

def _mos_pol(netlist):
    """.model name -> 'n'/'p', from SPICE nmos/pmos types OR OSDI model cards
    carrying type=1/type=-1 (e.g. PSP103: .model psp103n psp103va type=1)."""
    pol = {}
    for s in _logical_lines(netlist):
        sl = s.lower()
        if not sl.startswith(".model") or len(sl.split()) < 3:
            continue
        nm = sl.split()[1]
        if re.search(r"\bpmos\b", sl) or re.search(r"type\s*=\s*-1", sl):
            pol[nm] = "p"
        elif re.search(r"\bnmos\b", sl) or re.search(r"type\s*=\s*\+?1", sl):
            pol[nm] = "n"
    return pol

def _subckts(netlist):
    """name -> ports + ORIGINAL body lines (the drop anchors) for each
    top-level .subckt definition."""
    subs, stack = {}, []
    for ln in netlist.splitlines():
        s = ln.strip()
        sl = s.lower()
        if sl.startswith(".subckt") and len(s.split()) >= 2:
            t = s.split()
            ports = [x for x in t[2:] if "=" not in x and not x.lower().startswith("params:")]
            stack.append((t[1].lower(), ports, []))
        elif sl.startswith(".ends"):
            if stack:
                nm, ports, body = stack.pop()
                if not stack:
                    subs[nm] = dict(ports=ports, body=body)
        elif stack and s and s[0] not in "*;+":
            stack[-1][2].append(ln)
    return subs

def _fet_wrappers(subs, pol):
    """Subckts wrapping exactly one FET (raw M or an OSDI instance, e.g.
    C6288's nmos/pmos around PSP103) -> name: (pol, port positions of d,g,s,b)."""
    wraps = {}
    for nm, sc in subs.items():
        if len(sc["body"]) != 1:
            continue
        t = sc["body"][0].split()
        if t[0][0].lower() not in "mn" or len(t) < 6:
            continue
        model = next((x for x in t[5:] if "=" not in x), None)
        p = pol.get((model or "").lower())
        if not p:
            continue
        try:
            perm = [sc["ports"].index(x) for x in t[1:5]]   # d g s b positions
        except ValueError:
            continue
        wraps[nm] = (p, perm)
    return wraps

def _switch_out(fets, hi, lo, fixed, out):
    """Tiny switch-level solve: logic values propagate from the rails + inputs
    through ON channels (union-find per sweep, iterated for staged gates like
    AND = NAND+INV). Returns out's value, or None (floating / rail short)."""
    val = dict(fixed)
    val[hi], val[lo] = 1, 0
    for _ in range(8):
        parent = {}
        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for d, g, s, p in fets:
            gv = val.get(g)
            if gv is None:
                continue
            if (p == "n" and gv == 1) or (p == "p" and gv == 0):
                parent[find(d)] = find(s)
        rh, rl = find(hi), find(lo)
        if rh == rl:
            return None                       # pull-up/pull-down contention
        changed = False
        for n in list(parent):
            v = 1 if find(n) == rh else (0 if find(n) == rl else None)
            if v is not None and val.get(n) is None:
                val[n] = v
                changed = True
        if not changed:
            break
    return val.get(out)

# truth table -> (name, inverting, use-min input combiner)
GATEFN = {(1, 0): ("inv", 1, 0), (0, 1): ("buf", 0, 0),
          (1, 0, 0, 0): ("nor2", 1, 0), (1, 1, 1, 0): ("nand2", 1, 1),
          (0, 0, 0, 1): ("and2", 0, 1), (0, 1, 1, 1): ("or2", 0, 1)}

def recognize_gates(netlist):
    """Static CMOS gate SUBCKTS (INV/BUF/NOR2/NAND2/AND2/OR2): a subckt whose
    body is only FETs (raw M lines, or x-instances of single-FET wrapper
    subckts -- C6288's nmos/pmos around PSP103) is classified by a switch-level
    truth table and its body replaced by ONE behavioral gate (SPICE B-source /
    cmos_gate VA module). Replacing the definition accelerates every instance:
    C6288's 10112 FETs are three subckt bodies. Load caps in the body are kept
    (they set the delay). Params ride the tuned cmos_inv family."""
    pol = _mos_pol(netlist)
    subs = _subckts(netlist)
    wraps = _fet_wrappers(subs, pol)
    matches = []
    for nm, sc in subs.items():
        if nm in wraps:
            continue
        fets, drop, ok = [], [], True
        for ln in sc["body"]:
            t = ln.split()
            u = t[0][0].lower()
            if u == "c":
                continue                       # keep load caps
            if u == "m" and len(t) >= 6:
                model = next((x for x in t[5:] if "=" not in x), t[5])
                p = pol.get(model.lower())
                if not p: ok = False; break
                fets.append((t[1], t[2], t[3], t[4], p)); drop.append(ln)
            elif u == "x" and len(t) >= 3:
                toks = [x for x in t[1:] if "=" not in x and not x.lower().startswith("params:")]
                ref = toks[-1].lower() if toks else ""
                if ref not in wraps: ok = False; break
                p, perm = wraps[ref]
                nodes = toks[:-1]
                if len(nodes) <= max(perm): ok = False; break
                fets.append((nodes[perm[0]], nodes[perm[1]], nodes[perm[2]],
                             nodes[perm[3]], p)); drop.append(ln)
            else:
                ok = False; break
        if not ok or len(fets) < 2:
            continue
        from collections import Counter
        pb = Counter(b for _, _, _, b, p in fets if p == "p")
        nb = Counter(b for _, _, _, b, p in fets if p == "n")
        if not pb or not nb:
            continue
        hi, lo = pb.most_common(1)[0][0], nb.most_common(1)[0][0]
        if hi == lo:
            continue
        gset = {g for _, g, _, _, _ in fets}
        cset = {x for d, _, s, _, _ in fets for x in (d, s)}
        ins = [p for p in sc["ports"] if p in gset and p not in cset]
        outs = [p for p in sc["ports"] if p in cset]
        if len(outs) != 1 or not 1 <= len(ins) <= 2:
            continue
        if any(p not in (hi, lo) for p in sc["ports"] if p not in ins and p not in outs):
            continue                           # stray port that is not a rail
        out = outs[0]
        fl = [(d, g, s, p) for d, g, s, _, p in fets]
        combos = [(a,) for a in (0, 1)] if len(ins) == 1 else \
                 [(a, b) for a in (0, 1) for b in (0, 1)]
        table = []
        for cb in combos:
            v = _switch_out(fl, hi, lo, dict(zip(ins, cb)), out)
            if v is None:
                table = None; break
            table.append(v)
        fn = GATEFN.get(tuple(table)) if table else None
        if not fn:
            continue
        name, invf, usemin = fn
        tag = re.sub(r"\W", "_", nm)
        F = ("V(%s)" % ins[0]) if len(ins) == 1 else \
            ("%s(V(%s),V(%s))" % ("min" if usemin else "max", ins[0], ins[1]))
        sgn = "-" if invf else "+"
        L = ["* --- bfit: cmos gate '%s' (subckt %s, %d FETs) ---" % (name, nm, len(fets))]
        for k, i_ in enumerate(ins):
            L.append("Cin_g%d %s 0 __cin__" % (k + 1, i_))
        L.append("Bo_gate 0 %s I={ (max(0, min(V(%s), 0.5*V(%s) %s __g__*(%s - 0.5*V(%s)))) - V(%s))/__rout__ }"
                 % (out, hi, hi, sgn, F, hi, out))
        mod = "cmos_gate%d" % len(ins)
        extra = " inv=%d" % invf + (" usemin=%d" % usemin if len(ins) == 2 else "")
        vc = "\n".join(["// bfit gate %s (subckt %s)" % (name, nm),
                        "model g_%s %s ( cin=__cin__ g=__g__ rout=__rout__%s )" % (tag, mod, extra),
                        "xg_%s (%s %s %s) g_%s" % (tag, " ".join(ins), out, hi, tag)])
        matches.append(dict(model="cmos_inv", gate=name, inline=True,
                            insert="\n".join(L), vc=vc,
                            va_lib=["@LIB@/cmos_gates/%s.va" % mod],
                            drop=drop))
    return matches

def prune_unused(netlist):
    """Iteratively drop .subckt definitions never instantiated and .model cards
    (with '+' continuations) never referenced. Gate substitution orphans the
    FET wrapper subckts; the next pass takes their device model cards too --
    the substituted deck then needs no device models at all (an engine without
    PSP103 can run it)."""
    lines = netlist.splitlines()
    while True:
        subs, stack = {}, []
        for i, ln in enumerate(lines):
            sl = ln.strip().lower()
            if sl.startswith(".subckt") and len(sl.split()) >= 2:
                stack.append((sl.split()[1], i))
            elif sl.startswith(".ends") and stack:
                nm, a = stack.pop()
                if not stack:
                    subs.setdefault(nm, []).append((a, i))
        models = {}
        for i, ln in enumerate(lines):
            s = ln.strip()
            if s.lower().startswith(".model") and len(s.split()) >= 2:
                j = i + 1
                while j < len(lines) and lines[j].strip().startswith("+"):
                    j += 1
                models.setdefault(s.split()[1].lower(), []).append((i, j - 1))
        subrefs, modrefs = set(), set()
        for s in _logical_lines("\n".join(lines)):   # join '+' continuations --
            if not s or s[0] in "*;.":               # x1 <64 ports...> c6288 spans lines
                continue
            t = s.split()
            toks = [x for x in t[1:] if "=" not in x and not x.lower().startswith("params:")]
            if not toks:
                continue
            u = t[0][0].lower()
            if u == "x":
                subrefs.add(toks[-1].lower())
            elif u in "mnqdj":
                modrefs.add(toks[-1].lower())
        kill = set()
        for nm, spans in subs.items():
            if nm not in subrefs:
                for a, b in spans:
                    kill.update(range(a, b + 1))
        for nm, spans in models.items():
            if nm not in modrefs:
                for a, b in spans:
                    kill.update(range(a, b + 1))
        if not kill:
            return "\n".join(lines) + ("\n" if netlist.endswith("\n") else "")
        lines = [ln for i, ln in enumerate(lines) if i not in kill]

def _subckt_block(template, params):
    L = template.splitlines()
    a = next(i for i, l in enumerate(L) if l.strip().lower().startswith(".subckt"))
    z = next(i for i, l in enumerate(L) if l.strip().lower().startswith(".ends"))
    blk = "\n".join(L[a:z+1])
    for k, v in params.items(): blk = blk.replace("__%s__" % k, repr(v))
    return blk

_SI_SUF = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3,
           "k": 1e3, "meg": 1e6, "g": 1e9, "t": 1e12}
def _spice_num(s):
    m = re.match(r"^([+-]?[\d.]+(?:e[+-]?\d+)?)(meg|[fpnumkgt])?$", s.strip().lower())
    if not m: return None
    return float(m.group(1)) * (_SI_SUF[m.group(2)] if m.group(2) else 1.0)

def _relax_tran(mt, points=1000):
    """`.tran tstep tstop [...]` -> `.tran tstop/points tstop`: coarsen to ~`points`
    samples so the solver strides over the smooth macromodels (fewer points =
    faster + coarser; the `front --accuracy` knob sets `points`).

    ngspice honours the first arg as a step CEILING and strides at it. Xyce's
    adaptive integrator only treats it as a suggested/print step and its
    local-truncation-error (LTE) control still refines to resolve the input --
    so capping the max step (DTMAX / delmax) is NOT enough; it never strides.
    front() additionally loosens Xyce's LTE for sim=xyce so it ACCEPTS the
    coarse steps and undersamples like ngspice (same accuracy/speed trade)."""
    p = mt.group(0).split()
    if len(p) < 3: return mt.group(0)
    uic = " uic" if any(x.lower() == "uic" for x in p[3:]) else ""
    tstop = _spice_num(p[2])
    if not tstop: return ".tran %s %s%s" % (p[1], p[2], uic)
    return ".tran %.4g %.4g%s" % (tstop / float(points), tstop, uic)

def front(netlist, sim, libroot, cache, points=1000, reltol=0.1, abstol=0.01):
    """Transform a netlist for `sim`, honoring <SIM>_USE_BFIT. Returns
    (transformed_netlist, substituted_count, to_tune_list).

    Speed/accuracy knob: `points` is the transient coarsening target (None =>
    'exact', no coarsening). `reltol`/`abstol` loosen Xyce's LTE so it accepts
    the coarse step (None => leave Xyce's tolerances untouched)."""
    mode, models = env_mode(sim)
    if mode == "off":
        return netlist, 0, []
    matches = (recognize_ce(netlist) + recognize_mirror(netlist, sim)
               + recognize_inverter(netlist) + recognize_bridge(netlist)
               + recognize_gates(netlist))
    drop, insert, to_tune, used, va_files, hit = set(), {}, [], {}, {}, False
    gate_hit = False
    for m in matches:
        if mode == "auto" or m["model"] in models:
            if any(d in drop for d in m["drop"]):
                continue                 # lines already claimed by an earlier match
            if m.get("gate"): gate_hit = True
            if m.get("va"):              # VA legs bake all params -- nothing to fit/tune
                p = {}
                for modname, content in m["va"]: va_files[modname] = content
            else:
                p = (cache.get(m["model"]) or {}).get("params")
                if not p:
                    p = json.load(open(os.path.join(libroot, m["model"], "fit.json")))["x0"]
                    if m["model"] not in to_tune: to_tune.append(m["model"])
            hit = True
            blk = m["insert"]
            if m.get("inline"):          # macromodel emitted as raw elements (e.g. mirror groups)
                for k, v in p.items(): blk = blk.replace("__%s__" % k, repr(v))
            else:                        # macromodel realized as an appended .subckt (e.g. ce_stage)
                used[m["model"]] = p
            insert[m["drop"][0]] = blk
            for d in m["drop"]: drop.add(d)
    if not hit:
        return netlist, 0, []
    out, n = [], 0
    for ln in netlist.splitlines():
        if ln in insert: out.append(insert[ln]); n += 1; continue
        if ln in drop: continue
        out.append(ln)
    # append each used macromodel realisation (subckt) just before .end
    blocks = []
    for mdl, params in used.items():
        tmpl = open(os.path.join(libroot, mdl, "template.cir")).read()
        blocks.append("* --- bfit: %s (params %s) ---" %
                      (mdl, "cached" if cache.get(mdl) else "best-guess (queued for tuning)"))
        blocks.append(_subckt_block(tmpl, params))
    res, placed = [], False
    for ln in out:
        if ln.strip().lower() == ".end" and not placed:
            res.extend(blocks); placed = True
        res.append(ln)
    if not placed: res.extend(blocks)
    # Verilog-A mirror legs: write each module's .va beside the deck and wire it
    # in right after the title line (Xyce wants .hdl early). For ngspice the
    # OpenVAF-built .osdi is loaded by the run driver via pre_osdi, so emit only
    # the .model card there.
    if va_files:
        for modname, content in va_files.items():
            with open(modname + ".va", "w") as f:
                f.write(content)
        wire = []
        for modname in va_files:
            if sim == "xyce":
                wire.append('.hdl "%s.va"' % modname)
            wire.append(".model %smod %s()" % (modname, modname))
        res = ([res[0]] + wire + res[1:]) if res else wire
    text = "\n".join(res) + "\n"
    if gate_hit:      # gate substitution orphans wrapper subckts + device models
        text = prune_unused(text)
    if sim == "xyce":
        # our Xyce build SILENTLY DROPS subckt instance lines with leading
        # whitespace (fork parser quirk; upstream accepts them) -- de-indent.
        # '+' continuations keep their first-column marker.
        text = "\n".join(l.lstrip() for l in text.splitlines()) + "\n"
    # The substituted macromodels are smooth signal-flow models: they have no
    # device-level fast transients, so we drop any forced max timestep and
    # coarsen the output cadence to ~1000 points. The solver then strides
    # adaptively and writes far fewer points -- where bfit's speedup comes from.
    if n and points:                 # points falsy => 'exact': keep the fine .tran
        text = re.sub(r'(?im)^\.tran\s+.*$', lambda mt: _relax_tran(mt, points), text)
        if sim == "xyce" and reltol:
            # Xyce won't stride over the coarsened .tran on its own: its LTE
            # control refines to resolve the input. Loosen LTE (so it ACCEPTS
            # the coarse step) and cap the max step at the coarsened cadence C,
            # so it undersamples like ngspice. (delmax alone or DTMAX alone does
            # NOT stride -- the loosened reltol/abstol is the operative knob.)
            m = re.search(r'(?im)^\.tran\s+(\S+)\s', text)
            C = _spice_num(m.group(1)) if m else None
            if C and C > 0:
                opt = ".options timeint reltol=%g abstol=%g delmax=%.4g" % (reltol, abstol, C)
                text = re.sub(r'(?im)^(\.end\b)', opt + "\n\\1", text, count=1)
    return text, n, to_tune

# Speed/accuracy presets for `front`: (coarsen-to-points, xyce reltol, xyce abstol).
# 'exact' => no coarsening (full reference accuracy, macromodel-only speed); raw
# --points/--reltol/--abstol override any individual value so the forms compose.
ACC_PRESETS = {"exact": (None, None, None),
               "balanced": (1000, 0.1, 0.01),
               "fast": (300, 0.5, 0.1)}

def main():
    ap = argparse.ArgumentParser(prog="bfit")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tune", help="fit a macromodel template to a reference")
    t.add_argument("--ref", required=True, help="device-level reference netlist")
    t.add_argument("--lib", required=True, help="library entry dir (template.cir + fit.json)")
    t.add_argument("--sim", default="xyce", choices=list(DRIVERS))
    t.add_argument("--cache", help="param cache json (read/write)")
    f = sub.add_parser("front", help="recognize patterns + substitute macromodels "
                                     "(gated by <SIM>_USE_BFIT)")
    f.add_argument("netlist")
    f.add_argument("--libroot",
                   default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "library"))
    f.add_argument("--sim", default="xyce")
    f.add_argument("--cache", default="cache.json")
    f.add_argument("-o", "--out")
    f.add_argument("--accuracy", default="balanced", choices=list(ACC_PRESETS),
                   help="speed/accuracy preset: exact (no coarsening), balanced "
                        "(~1000 pts, reltol 0.1), fast (~300 pts, reltol 0.5). Default balanced.")
    f.add_argument("--points", type=int, help="override coarsening target sample count "
                                              "(0/omit with --accuracy exact = no coarsening)")
    f.add_argument("--reltol", type=float, help="override Xyce LTE reltol")
    f.add_argument("--abstol", type=float, help="override Xyce LTE abstol")
    mg = sub.add_parser("merge", help="ANALYTICAL lossless merge of directly-coupled "
                        "transistor structures -- eliminate internal nodes (exact, NOT "
                        "reduced-order like `front`)")
    mg.add_argument("netlist")
    mg.add_argument("-o", "--out")
    mg.add_argument("--device-va", help="real device Verilog-A to INLINE for the merged "
                                        "component (general merge) instead of square-law")
    mg.add_argument("--table", action="store_true",
                    help="emit the merged bridge as a table-driven .so (PyMS table fallback) "
                         "-- bounded interpolation that converges where exp does not")
    tb = sub.add_parser("table", help="SIMPLIS-style TABLE MODE: every device -> table model "
                        "from the start (fast + always converges, accuracy traded). "
                        "INDEPENDENT of merge.")
    tb.add_argument("netlist")
    tb.add_argument("-o", "--out")
    tb.add_argument("--device-va", help="diode Verilog-A to read default isat/n/vt/cjo from")
    a = ap.parse_args()
    if a.cmd == "table":
        from tablemode import table_front
        dev = open(a.device_va).read() if a.device_va else None
        text, sofiles, rep = table_front(open(a.netlist).read(), dev)
        (open(a.out, "w") if a.out else sys.stdout).write(text)
        for fname, src in sofiles.items():
            d = os.path.dirname(a.out) if a.out else "."
            open(os.path.join(d, fname), "w").write(src)
            sys.stderr.write("[bfit table] wrote %s (g++ -shared -fPIC)\n" % os.path.join(d, fname))
        sys.stderr.write("[bfit table] table-ized %d diode(s) over %d model(s)%s\n" % (
            rep["diode_insts"], rep["diode_models"],
            ("; passthrough (need 2-D table): " + ", ".join(rep["skipped"])) if rep["skipped"] else ""))
        return
    if a.cmd == "merge":
        from merge import merge_front
        dev = open(a.device_va).read() if a.device_va else None
        text, matches, elim, vafiles = merge_front(open(a.netlist).read(), dev, a.table)
        (open(a.out, "w") if a.out else sys.stdout).write(text)
        for fname, src in vafiles.items():
            d = os.path.dirname(a.out) if a.out else "."
            open(os.path.join(d, fname), "w").write(src)
            how = "compile: g++ -shared -fPIC" if fname.endswith(".cpp") else "compile with openvaf"
            sys.stderr.write("[bfit merge] wrote %s (%s)\n" % (os.path.join(d, fname), how))
        sys.stderr.write("[bfit merge] merged %d structure(s), eliminated %d node(s): %s\n" % (
            len(matches), len(elim),
            ", ".join("%s{%s}%s" % (m["kind"], "+".join(m["devices"]),
                      "->drop %s" % m["elim"] if m.get("elim") else "->%s.va" % m["vamodule"])
                      for m in matches) or "(none)"))
        return
    if a.cmd == "front":
        cache = json.load(open(a.cache)) if os.path.exists(a.cache) else {}
        mode, models = env_mode(a.sim)
        pts, rt, at = ACC_PRESETS[a.accuracy]
        if a.points is not None: pts = a.points or None      # --points 0 => exact
        if a.reltol is not None: rt = a.reltol
        if a.abstol is not None: at = a.abstol
        if pts and rt is None: rt, at = 0.1, 0.01            # coarsening needs an LTE for xyce
        if a.sim == "vacask":
            from drivers_vacask import front_vacask
            net, n = front_vacask(open(a.netlist).read(), cache, a.libroot,
                                  points=pts, reltol=rt or 0.1)
            sys.stderr.write("[bfit] VACASK_USE_BFIT=%s -> substituted %d block(s); accuracy=%s (points=%s)\n"
                             % (mode, n, a.accuracy, pts if pts else "exact"))
            (open(a.out, "w") if a.out else sys.stdout).write(net)
            return
        net, n, totune = front(open(a.netlist).read(), a.sim, a.libroot, cache,
                               points=pts, reltol=rt, abstol=at)
        sys.stderr.write("[bfit] %s_USE_BFIT=%s%s -> substituted %d block(s); accuracy=%s (points=%s)%s\n" % (
            a.sim.upper(), mode, (":" + ",".join(models)) if models else "", n,
            a.accuracy, pts if pts else "exact",
            ("; queued for tuning: " + ",".join(totune)) if totune else ""))
        (open(a.out, "w") if a.out else sys.stdout).write(net)
        return
    if a.cmd == "tune":
        spec = json.load(open(os.path.join(a.lib, "fit.json")))
        tmpl = "template.vc" if a.sim == "vacask" else "template.cir"
        template = open(os.path.join(a.lib, tmpl)).read()
        ref = open(a.ref).read()
        driver = DRIVERS[a.sim]()
        params, res = tune(ref, template, spec, driver, t0=spec.get("t0", 1.5e-3))
        print("\nFITTED PARAMETERS:", {k: round(v,4) for k,v in params.items()})
        if a.cache:
            db = json.load(open(a.cache)) if os.path.exists(a.cache) else {}
            db[spec["module"]] = {"params": params, "residual": res, "sim": a.sim}
            json.dump(db, open(a.cache, "w"), indent=2)
            print("cached ->", a.cache)

if __name__ == "__main__":
    main()
