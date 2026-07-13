#!/usr/bin/env python3
"""sp2vc.py -- minimal SPICE -> VACASK netlist translator for the bfit VACASK
driver. Handles the device-level subset the perf-league / tuning decks use:
V (dc/SIN/PULSE), I, R, C, MOSFET LEVEL=1, NPN BJT, diode, .model, .tran, .ic,
.print (-> save), and multitone B-source inputs (V={A*(sin+sin+..)} -> series
sine vsources). Nonlinear B-source *macromodels* are NOT handled here -- the
VACASK driver instantiates those from their Verilog-A (.vams) form instead.

VACASK device models (compiled to OSDI, path via env SP2VC_DEV, default the
build tree) provide sp_mos1 / sp_bjt / sp_diode / resistor / capacitor.
"""
import os, re

DEV = os.environ.get("SP2VC_DEV", "/opt/build.VACASK/Release/devices")

def num(tok):
    """SPICE number token -> VACASK-safe token. Only 'meg' differs (VACASK has
    no meg; k/u/n/p/f/m/g match). Leaves expressions/names untouched."""
    m = re.match(r"^([+-]?[\d.]+(?:e[+-]?\d+)?)(meg)$", tok, re.I)
    if m:
        return "%se6" % m.group(1)
    return tok

def _sines(node_p, node_n, expr):
    """A multitone B-source V={amp*(sin(2pi f1 t)+sin(2pi f2 t)+...)} (+dc) ->
    a series string of ideal sine vsources between node_p and node_n. Returns
    (lines, models_needed). Falls back to () if not a recognized multitone."""
    expr = expr.strip().lstrip("{").rstrip("}").strip()   # V={...} braces would mask the dc term
    amp = re.search(r"([-\d.eE]+)\s*\*\s*\(", expr)
    dc  = re.match(r"\s*([-\d.eE]+)\s*\+", expr)
    freqs = re.findall(r"sin\s*\(\s*6\.?2\d*\s*\*\s*([-\d.eE]+)", expr)
    if not amp or not freqs:
        return None
    a = amp.group(1)
    chain, prev, k = [], node_p, 0
    if dc:
        mid = "%s_dc" % node_p
        chain.append('v%s_dc (%s %s) v dc=%s' % (node_p, prev, mid, dc.group(1)))
        prev = mid
    for i, f in enumerate(freqs):
        nxt = node_n if i == len(freqs)-1 else "%s_s%d" % (node_p, i)
        chain.append('v%s_s%d (%s %s) v type="sine" ampl=%s freq=%s'
                     % (node_p, i, prev, nxt, a, f))
        prev = nxt
    return chain

def _src(name, np_, nn, rest):
    """V/I source SPICE tail -> VACASK. rest is the token list after the nodes."""
    kind = "v" if name[0] in "vV" else "i"
    if not rest:
        return '%s (%s %s) %s dc=0' % (name, np_, nn, kind)
    t0 = rest[0]
    up = " ".join(rest).upper()
    _NUM = r"[-+]?[\d.]+(?:[eE][-+]?\d+)?\w*"
    if up.startswith("SIN"):
        inner = re.sub(r"(?i)^\s*sin\s*\(?", "", " ".join(rest)).rstrip(") ")
        p = re.findall(_NUM, inner)
        off, amp, freq = (p+["0","0","0"])[:3]
        return '%s (%s %s) %s dc=%s type="sine" ampl=%s freq=%s' % (name, np_, nn, kind, num(off), num(amp), num(freq))
    if up.startswith("PULSE"):
        inner = re.sub(r"(?i)^\s*pulse\s*\(?", "", " ".join(rest)).rstrip(") ")
        p = re.findall(_NUM, inner)
        v0, v1, td, tr, tf, pw, per = (p+["0"]*7)[:7]
        return ('%s (%s %s) %s type="pulse" val0=%s val1=%s delay=%s rise=%s fall=%s width=%s period=%s'
                % (name, np_, nn, kind, num(v0), num(v1), num(td), num(tr), num(tf), num(pw), num(per)))
    return '%s (%s %s) %s dc=%s' % (name, np_, nn, kind, num(t0))

# SPICE .model -> (vacask module, param filter). sp_ models take SPICE param names.
def _model(line):
    m = re.match(r"\.model\s+(\S+)\s+(\w+)\s*\(?(.*)\)?", line, re.I)
    if not m: return None, None
    name, typ, body = m.group(1), m.group(2).lower(), m.group(3)
    body = body.rstrip(") ")
    params = dict(re.findall(r"(\w+)\s*=\s*([-\d.eE]+\w*)", body))
    def emit(mod, extra=""):
        ps = " ".join("%s=%s" % (k.lower(), num(v)) for k, v in params.items() if k.lower() != "level")
        return 'model %s %s ( %s %s )' % (name, mod, extra, ps)
    if typ == "npn":    return name, emit("sp_bjt", "type=1 subs=1")
    if typ == "pnp":    return name, emit("sp_bjt", "type=-1 subs=1")
    if typ == "nmos":   return name, emit("sp_mos1", "type=1")
    if typ == "pmos":   return name, emit("sp_mos1", "type=-1")
    if typ == "d":      return name, emit("sp_diode")
    return name, None

def translate(spice, extra_loads=None, extra_head=None, extra_body=None):
    lines = spice.splitlines()
    title = lines[0].strip() if lines and not lines[0].strip().startswith((".", "*")) else "translated circuit"
    body, models, need, tran, ics, saves = [], [], set(), None, [], []
    subckt = False
    for raw in lines[1:]:
        s = raw.strip()
        if not s or s[0] in "*;": continue
        if s.startswith("@@VC "):          # pre-formed VACASK line (macromodel insert), verbatim
            body.append(s[5:]); continue
        low = s.lower(); t = s.split(); u = t[0][0].lower()
        if low.startswith(".model"):
            nm, out = _model(s)
            if out:
                models.append(out)
                mt = re.match(r"\.model\s+\S+\s+(\w+)", s, re.I)      # load module even if all
                mt = mt.group(1).lower() if mt else ""               # instances were substituted
                need.add({"npn": "q", "pnp": "q", "nmos": "m", "pmos": "m", "d": "d"}.get(mt, ""))
            continue
        if low.startswith(".tran"):
            p = t[1:]
            tstep, tstop = (p+["0","0"])[:2]
            mx = (" maxstep=%s" % num(p[3])) if len(p) >= 4 and p[3].lower() != "uic" else ""
            uic = ' icmode="uic"' if "uic" in low else ""
            tran = 'analysis tran1 tran step=%s stop=%s%s%s' % (num(tstep), num(tstop), mx, uic)
            continue
        if low.startswith(".ic"):
            for nn, vv in re.findall(r"v\((\w+)\)\s*=\s*([-\d.eE]+\w*)", s, re.I):
                ics.append('"%s"; %s' % (nn, num(vv)))
            continue
        if low.startswith(".print"):
            for nn in re.findall(r"v\((\w+)\)", s, re.I): saves.append("v(%s)" % nn)
            continue
        if low == ".end" or low.startswith((".control", ".endc", ".options", ".option")): continue
        if low.startswith(".subckt"):
            body.append("subckt %s (%s)" % (t[1], " ".join(t[2:]))); subckt = True; continue
        if low.startswith(".ends"):
            body.append("ends"); subckt = False; continue
        # instances
        if u == "r": body.append("%s (%s %s) r r=%s" % (t[0], t[1], t[2], num(t[3]))); need.add("r"); continue
        if u == "c": body.append("%s (%s %s) c c=%s" % (t[0], t[1], t[2], num(t[3]))); need.add("c"); continue
        if u in "vi":
            if len(t) >= 4 and ("{" in s or "sin(" in low):     # behavioral multitone input
                ch = _sines(t[1], t[2], s.split("=",1)[-1] if "=" in s else " ".join(t[3:]))
                if ch: body += ch; need.add("v"); continue
            body.append(_src(t[0], t[1], t[2], t[3:])); need.add(u); continue
        if u == "b":   # behavioral source: only multitone V={A*(sin+sin+..)} inputs -> series sines
            ch = _sines(t[1], t[2], s.split("=", 1)[-1] if "=" in s else "")
            if ch: body += ch; need.add("v")
            continue
        if u == "q": body.append("%s (%s %s %s) %s" % (t[0], t[1], t[2], t[3], t[4])); need.add("q"); continue
        if u == "d": body.append("%s (%s %s) %s" % (t[0], t[1], t[2], t[3])); need.add("d"); continue
        if u == "m":
            extra = " ".join(x.split("=", 1)[0].lower() + "=" + num(x.split("=", 1)[1])
                             for x in t[6:] if "=" in x)   # sp_mos1 params are lowercase (w l ad ..)
            body.append("%s (%s %s %s %s) %s %s" % (t[0], t[1], t[2], t[3], t[4], t[5], extra)); need.add("m"); continue
        if u == "x":   # subckt instance: X<name> nodes... subckt
            body.append("%s (%s) %s" % (t[0], " ".join(t[1:-1]), t[-1])); continue
    # assemble
    load = {"r": '"%s/resistor.osdi"' % DEV, "c": '"%s/capacitor.osdi"' % DEV,
            "q": '"%s/spice/bjt.osdi"' % DEV, "d": '"%s/spice/diode.osdi"' % DEV,
            "m": '"%s/spice/mos1.osdi"' % DEV}
    out, _seen = [title], set()
    def _ld(line):
        if line not in _seen:
            _seen.add(line); out.append(line)
    for k in ("r", "c", "q", "d", "m"):
        if k in need: _ld("load %s" % load[k])
    for l in (extra_loads or []): _ld('load "%s"' % l)
    out += ["model v vsource", "model i isource"]
    if "r" in need: out.append("model r resistor")
    if "c" in need: out.append("model c capacitor")
    out += (extra_head or [])
    out += models + [""] + body + (extra_body or []) + ["", "control"]
    if ics: tran = (tran or "analysis tran1 tran") + ' ic=[%s]' % "; ".join(ics)
    out.append("  " + (tran or "analysis tran1 tran"))
    if saves: out.insert(out.index("control"), "  ")   # placeholder (default saves = all)
    out.append("endc")
    return "\n".join(out) + "\n"

if __name__ == "__main__":
    import sys
    print(translate(open(sys.argv[1]).read()))
