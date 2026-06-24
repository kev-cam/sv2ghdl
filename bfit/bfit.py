#!/usr/bin/env python3
"""
bfit -- behavioral-fit: auto-tune a portable Verilog-AMS macromodel to match a
device-level reference, through ANY simulator (engine-neutral, via a pluggable
sim-driver). Standalone front-end accelerator: recognize circuit patterns,
substitute auto-tuned signal-flow macromodels, cache the parameters.

This module is the ENGINE: SimDriver interface, the optimizer, and the tuner.
Sim-specific code lives only in the drivers (xyce, ngspice/OpenVAF, ...).
"""
import os, sys, json, tempfile, subprocess, argparse, math

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

def main():
    ap = argparse.ArgumentParser(prog="bfit")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tune", help="fit a macromodel template to a reference")
    t.add_argument("--ref", required=True, help="device-level reference netlist")
    t.add_argument("--lib", required=True, help="library entry dir (template.cir + fit.json)")
    t.add_argument("--sim", default="xyce", choices=list(DRIVERS))
    t.add_argument("--cache", help="param cache json (read/write)")
    a = ap.parse_args()
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
