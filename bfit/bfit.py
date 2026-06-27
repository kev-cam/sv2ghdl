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

def recognize_mirror(netlist):
    """MOSFET current-mirror groups: a diode-connected reference (drain==gate)
    and EVERY output FET sharing its gate node, source rail and model -- one
    reference fans out to many outputs (op-amp mirror banks). Emits a two-part
    signal-flow model:
      I->V at the reference -- a `vt` source off the rail feeds a sense resistor,
        so the reference current develops an overdrive VOLTAGE on the gate node;
        the resistor is 1 ohm when the reference is the smallest device (sizes
        are normalized on the smallest in the group).
      V->I at each output -- current = (size ratio) x overdrive, GOING RESISTIVE
        near the rail (tanh) so the output can never run past the supply.
    Handles both polarities: PMOS (rail above the gate, sources current) and
    NMOS (rail below the gate, e.g. gnd, sinks current) -- the overdrive sign and
    the resistive-rail term flip accordingly. Params vt, vsat are tuned."""
    pol = {}                                          # MOS model -> 'n'/'p' from .model
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
            M.append((t[0], t[1], t[2], t[3], t[5], _wl(t[6:]), ln))  # name d g s model wl line
    matches, claimed = [], set()
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
        tag = re.sub(r"\W", "", gr); R = "%.4g" % (smin / wlr)   # = 1 when ref is smallest
        L = ["* --- bfit: current_mirror (%s, ref %s, %d output%s) ---"
             % ("NMOS" if p == "n" else "PMOS", gr, len(outs), "s" if len(outs) > 1 else "")]
        if p == "p":          # rail above gate: vt drops below rail; outputs SOURCE rail->out
            L += ["Vt_%s %s cmv_%s __vt__" % (nr, sr, tag),
                  "R1_%s cmv_%s %s %s" % (nr, tag, gr, R)]
            ov = "(V(%s)-V(%s)-__vt__)" % (sr, gr)
            mk = lambda no, do, g: "Bcm_%s %s %s I={ %.4g*%s*tanh((V(%s)-V(%s))/__vsat__) }" \
                 % (no, sr, do, g, ov, sr, do)
        else:                 # rail below gate (gnd): vt rises above rail; outputs SINK out->rail
            L += ["Vt_%s cmv_%s %s __vt__" % (nr, tag, sr),
                  "R1_%s %s cmv_%s %s" % (nr, gr, tag, R)]
            ov = "(V(%s)-V(%s)-__vt__)" % (gr, sr)
            mk = lambda no, do, g: "Bcm_%s %s %s I={ %.4g*%s*tanh((V(%s)-V(%s))/__vsat__) }" \
                 % (no, do, sr, g, ov, do, sr)
        for no, do, wlo, lo in outs:                  # V->I: one source per mirror output
            claimed.add(no)
            L.append(mk(no, do, wlo / smin))
        drop = [lr] + [lo for _, _, _, lo in outs]
        matches.append(dict(model="current_mirror", inline=True,
                            insert="\n".join(L), drop=drop))  # drop[0]=ref line (anchor)
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
                # Decoupled logic gate, no tanh: the (hysteretic) input programs the
                # pull-up / pull-down conductances. Each has a leakage floor 1/rleak that
                # stays on, so the off-path carries the static current (power match). The
                # output is the resulting divider into the load C -- linear-algebraic,
                # cheap per step, and linear between input changes so the solver strides.
                # No kink/clamp: the leakage floor 1/rleak is sized (>= 0.5*h/ron) to keep
                # both conductances >= 0 through the hysteresis excursion, so the network
                # stays passive and smooth -> stable AND large adaptive steps.
                L = ["* --- bfit: cmos_inv (in %s, out %s, vhi %s) ---" % (gn, dn, sp),
                     "Cin_%s %s 0 __cin__" % (nn, gn),                       # decouple input (R-C load)
                     "Bo_%s 0 %s I={ ((V(%s)-V(%s)+__h__*(V(%s)-0.5*V(%s)))/(V(%s)*__ron__)+1/__rleak__)"
                     "*(V(%s)-V(%s)) - ((V(%s)-__h__*(V(%s)-0.5*V(%s)))/(V(%s)*__ron__)+1/__rleak__)*V(%s) }"
                     % (nn, dn, sp, gn, dn, sp, sp, sp, dn, gn, dn, sp, sp, dn)]
                matches.append(dict(model="cmos_inv", inline=True,
                                    insert="\n".join(L), drop=[ln_, lp]))  # drop[0]=NMOS (anchor)
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
                     "-V(%s))/__rs__) - V(%s)/__rleak__ }" % (tag, out, a, b, out, out)]
                matches.append(dict(model="bridge_rect", inline=True,
                                    insert="\n".join(L), drop=[la, lb, ga, gb]))  # drop[0]=anchor
                break
    return matches

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

def _relax_tran(mt):
    """`.tran tstep tstop [tstart [tmax]]` -> `.tran tstop/1000 tstop`: drop the
    forced max step and coarsen output to ~1000 points (smooth macromodels)."""
    p = mt.group(0).split()
    if len(p) < 3: return mt.group(0)
    tstop = _spice_num(p[2])
    if not tstop: return ".tran %s %s" % (p[1], p[2])
    return ".tran %.4g %.4g" % (tstop / 1000.0, tstop)

def front(netlist, sim, libroot, cache):
    """Transform a netlist for `sim`, honoring <SIM>_USE_BFIT. Returns
    (transformed_netlist, substituted_count, to_tune_list)."""
    mode, models = env_mode(sim)
    if mode == "off":
        return netlist, 0, []
    matches = (recognize_ce(netlist) + recognize_mirror(netlist)
               + recognize_inverter(netlist) + recognize_bridge(netlist))
    drop, insert, to_tune, used, hit = set(), {}, [], {}, False
    for m in matches:
        if mode == "auto" or m["model"] in models:
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
    text = "\n".join(res) + "\n"
    # The substituted macromodels are smooth signal-flow models: they have no
    # device-level fast transients, so we drop any forced max timestep and
    # coarsen the output cadence to ~1000 points. The solver then strides
    # adaptively and writes far fewer points -- where bfit's speedup comes from.
    if n:
        text = re.sub(r'(?im)^\.tran\s+.*$', _relax_tran, text)
    return text, n, to_tune

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
        net, n, totune = front(open(a.netlist).read(), a.sim, a.libroot, cache)
        sys.stderr.write("[bfit] %s_USE_BFIT=%s%s -> substituted %d block(s)%s\n" % (
            a.sim.upper(), mode, (":" + ",".join(models)) if models else "", n,
            ("; queued for tuning: " + ",".join(totune)) if totune else ""))
        (open(a.out, "w") if a.out else sys.stdout).write(net)
        return
    if a.cmd == "tune":
        spec = json.load(open(os.path.join(a.lib, "fit.json")))
        template = open(os.path.join(a.lib, "template.cir")).read()
        ref = open(a.ref).read()
        driver = DRIVERS[a.sim]()
        params, res = tune(ref, template, spec, driver)
        print("\nFITTED PARAMETERS:", {k: round(v,4) for k,v in params.items()})
        if a.cache:
            db = json.load(open(a.cache)) if os.path.exists(a.cache) else {}
            db[spec["module"]] = {"params": params, "residual": res, "sim": a.sim}
            json.dump(db, open(a.cache, "w"), indent=2)
            print("cached ->", a.cache)

if __name__ == "__main__":
    main()
