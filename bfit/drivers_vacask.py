#!/usr/bin/env python3
"""drivers_vacask.py -- bfit driver for VACASK (Verilog-A/OSDI simulator).

Pipeline: a SPICE reference netlist is translated to VACASK syntax (sp2vc) and
run; a VACASK candidate template (already VACASK, .vams macromodel loaded via
OSDI) is run as-is. Both return {signal: [values], "time": [...]} so the tuner
(bfit tune --sim vacask) fits macromodel params against VACASK itself. VACASK
replaces ngspice as the license-clean (AGPL) OpenVAF-native fast path.
"""
import os, sys, subprocess, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sp2vc


def _is_spice(text):
    tl = text.lower()
    return (".tran" in tl) or ("\n.model" in tl) or tl.lstrip().startswith(".model")


def _read_raw(path, pyrf):
    if pyrf not in sys.path:
        sys.path.insert(0, pyrf)
    from rawfile import rawread
    plot = rawread(path).get()
    return {n: list(plot[n]) for n in plot.names}


class VacaskDriver:
    name = "vacask"

    def __init__(self, binary=None, openvaf=None):
        self.binary = binary or os.environ.get(
            "BFIT_VACASK", "/opt/build.VACASK/Release/simulator/vacask")
        self.openvaf = openvaf or os.environ.get(
            "BFIT_OPENVAF_R", "/opt/openvaf-r/openvaf-r")
        self.pyrf = os.environ.get("VACASK_PYTHON", "/usr/local/src/VACASK/python")

    def run(self, netlist_text, signals=None):
        deck = sp2vc.translate(netlist_text) if _is_spice(netlist_text) else netlist_text
        lib = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library")
        deck = deck.replace("@DEV@", sp2vc.DEV).replace("@LIB@", lib)
        d = tempfile.mkdtemp(prefix="bfitvc_")
        open(os.path.join(d, "c.sim"), "w").write(deck)
        env = dict(os.environ)
        env["SIM_OPENVAF"] = self.openvaf          # on-the-fly .va compile uses reloaded
        try:
            r = subprocess.run([self.binary, "-qp", "--skip-postprocess", "c.sim"],
                               cwd=d, env=env, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            raise RuntimeError("vacask: timeout")
        raw = os.path.join(d, "tran1.raw")
        if not os.path.exists(raw):
            raise RuntimeError("vacask: no tran1.raw (%s)" % ((r.stdout + r.stderr)[-400:]))
        return _read_raw(raw, self.pyrf)


# ---------------------------------------------------------------------------
# front --sim vacask : recognize patterns, drop the device stages, translate
# the kept SPICE to VACASK (sp2vc) and inject the Verilog-A macromodel
# instances (loaded via OSDI). Coarsens the transient with tran_lteratio (the
# VACASK LTE lever) so the solver strides over the smooth macromodels.
# ---------------------------------------------------------------------------
import re, json

# model -> (instance model name, VA module, osdi load, param-card formatter)
_MODCARD = {
 "ce_stage":    ("cestage", "ce_stage", "@LIB@/ce_stage/ce_stage.osdi",
                 lambda p: "gain=%(gain)g vlo=%(Vlo)g vhi=%(Vhi)g rout=%(Rout)g rin=100000 fp=%(fp)g" % p),
 "cmos_inv":    ("cmosinv", "cmos_inv", "@LIB@/cmos_inv/cmos_inv.osdi",
                 lambda p: "cin=%(cin)g ron=%(ron)g rleak=%(rleak)g h=%(h)g vsup=3.3" % p),
 "bridge_rect": ("brm", "bridge", "@LIB@/bridge_rect/bridge.osdi",
                 lambda p: "vdrop=%(vdrop)g rs=%(rs)g rleak=%(rleak)g" % p),
}

def _params(model, cache, libroot):
    c = cache.get(model)
    if c and c.get("params"):
        return c["params"]
    return json.load(open(os.path.join(libroot, model, "fit.json")))["x0"]

def _coarsen(deck, points, libroot):
    import bfit
    lteratio = 10.0 if (not points or points >= 800) else 40.0
    def repl(mo):
        line = mo.group(0)
        st = re.search(r'stop=(\S+)', line).group(1)
        sv = bfit._spice_num(st)
        step = ("%.4g" % (sv / points)) if (sv and points) else st
        icm = re.search(r'(icmode="\w+")', line); ic = re.search(r'(ic=\[[^\]]*\])', line)
        ex = ((" " + icm.group(1)) if icm else "") + ((" " + ic.group(1)) if ic else "")
        return "analysis tran1 tran step=%s stop=%s%s" % (step, st, ex)
    deck = re.sub(r'analysis \w+ tran [^\n]*', repl, deck)
    return deck.replace("  analysis tran1",
                        "  options tran_lteratio=%g\n  analysis tran1" % lteratio, 1)

def front_vacask(spice, cache, libroot, points=1000, reltol=0.1):
    """SPICE deck -> accelerated VACASK deck. Recognized stages become VA
    macromodel instances; the rest is translated device-for-device."""
    import bfit
    mode = os.environ.get("VACASK_USE_BFIT", "off").strip().lower()
    repl, used = {}, {}    # spice line -> "@@VC <insert>" (anchor) or None (drop)
    if mode not in ("", "off"):
        matches = (bfit.recognize_ce(spice) + bfit.recognize_inverter(spice)
                   + bfit.recognize_bridge(spice))   # mirror: TODO
        for m in matches:
            if "vc" not in m:
                continue
            repl[m["drop"][0]] = "@@VC " + m["vc"]          # in-place (stays inside subckt)
            for d in m["drop"][1:]:
                repl.setdefault(d, None)
            used[m["model"]] = _params(m["model"], cache, libroot)
    out = []
    for ln in spice.splitlines():
        if ln in repl:
            if repl[ln] is not None:
                out.append(repl[ln])
        else:
            out.append(ln)
    n_ins = sum(1 for v in repl.values() if v is not None)
    loads, head = [], []
    for model, p in used.items():
        iname, vamod, osdi, fmt = _MODCARD[model]
        loads.append(osdi)
        head.append("model %s %s ( %s )" % (iname, vamod, fmt(p)))
    deck = sp2vc.translate("\n".join(out), extra_loads=loads, extra_head=head)
    if used and points:
        deck = _coarsen(deck, points, libroot)
    deck = deck.replace("@LIB@", libroot).replace("@DEV@", sp2vc.DEV)
    return deck, n_ins
