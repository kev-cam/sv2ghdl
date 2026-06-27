#!/usr/bin/env python3
"""bfit table : SIMPLIS-style TABLE MODE.

Replace every device with a table / piecewise-linear model FROM THE START and
never attempt the accurate analytical/JIT mode. This is an INDEPENDENT mode --
not a --merge fallback. It trades accuracy for speed and rock-solid convergence:
every nonlinearity becomes a bounded, smooth, interpolated lookup, so Newton
never sees a runaway exp and the matrix entries stay well-scaled. This is the
SIMPLIS bet -- a deliberately approximate engine that always converges -- rather
than SPICE's "exact device, hope it converges".

Table models are emitted as PyMS/Xyce VAE-ABI .so (vae/table_model.py); the same
lookups also serve OpenVAF/ngspice once compiled. Coverage today: diodes (1-D
I(Vd)). MOSFET/BJT need their 2-D table emitters ported into PyMS table_model
(devchar has the characterization); until then they pass through untouched and
are reported, so nothing is silently left in accurate mode.
"""
import os, re, sys
from merge import parse_diodes, parse_diode_models, _sample_diode, _num

def _pyms_table():
    pyms = os.environ.get("PYMS_DIR", "/usr/local/src/xyce/utils/PyMS")
    vae = os.path.join(pyms, "vae")
    if vae not in sys.path: sys.path.insert(0, vae)
    import table_model
    return table_model

def table_front(netlist, device_va=None):
    """Table-ize every supported device. Returns (text, sofiles, report) where
    sofiles maps '<name>.cpp' -> C++ source and report lists what was/ wasn't done."""
    tm = _pyms_table()
    dmodels = parse_diode_models(netlist)
    diodes = parse_diodes(netlist)
    sofiles, tabled, skipped = {}, [], []

    # one table .so per distinct diode .model (instances share it)
    used_models = sorted({d["model"] for d in diodes})
    for mname in used_models:
        mp = dmodels.get(mname, {})
        vd, idv, cjo = _sample_diode(device_va,
                                     isat=mp.get("is"), n=mp.get("n"), cjo=mp.get("cjo", mp.get("cj0")))
        mod = "tbl_" + re.sub(r"\W", "", mname)
        sofiles[mod + ".cpp"] = tm.emit_diode_table_so(mod, vd, idv, cjo=cjo)
        tabled.append((mname, mod))

    # rewrite each diode instance to point at its table model; drop the original
    # diode .model card and emit each table .model exactly once
    modmap = {mname: mod for mname, mod in tabled}
    out, emitted = [], set()
    for ln in netlist.splitlines():
        s = ln.strip()
        t = s.split()
        if (len(t) >= 3 and t[0].lower() == ".model" and t[1].lower() in modmap):
            continue                                    # drop original diode .model
        if t and t[0][0].upper() == "D" and len(t) >= 4 and t[3].lower() in modmap:
            mod = modmap[t[3].lower()]
            if mod not in emitted:
                out.append(".model %smod %s()" % (mod, mod)); emitted.add(mod)
            out.append("Y%s %s %s %s %smod  ; bfit table: %s -> table model %s"
                       % (mod, t[0], t[1], t[2], mod, t[0], mod))
        else:
            out.append(ln)

    # report devices we can't table-ize yet (MOS/BJT)
    for ln in netlist.splitlines():
        c = ln.strip()[:1].upper()
        if c in ("M", "Q", "J", "Z"):
            skipped.append(ln.strip().split()[0])

    report = dict(diode_models=len(used_models), diode_insts=len(diodes),
                  tabled=tabled, skipped=skipped)
    return "\n".join(out) + "\n", sofiles, report

def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="bfit table",
        description="SIMPLIS-style table mode: every device -> table model from the start "
                    "(fast + always converges, accuracy traded). Independent of --merge.")
    ap.add_argument("netlist")
    ap.add_argument("-o", "--out")
    ap.add_argument("--device-va", help="diode Verilog-A to read default isat/n/vt/cjo from")
    a = ap.parse_args(argv)
    dev = open(a.device_va).read() if a.device_va else None
    text, sofiles, rep = table_front(open(a.netlist).read(), dev)
    (open(a.out, "w").write(text) if a.out else sys.stdout.write(text))
    d = os.path.dirname(a.out) if a.out else "."
    for fname, src in sofiles.items():
        open(os.path.join(d, fname), "w").write(src)
        sys.stderr.write("[bfit table] wrote %s (g++ -shared -fPIC)\n" % os.path.join(d, fname))
    sys.stderr.write("[bfit table] table-ized %d diode instance(s) over %d model(s): %s\n" % (
        rep["diode_insts"], rep["diode_models"],
        ", ".join("%s->%s" % (m, mod) for m, mod in rep["tabled"]) or "(none)"))
    if rep["skipped"]:
        sys.stderr.write("[bfit table] NOT table-ized (need 2-D PyMS table emitter): %s\n"
                         % ", ".join(rep["skipped"]))
    return 0

if __name__ == "__main__":
    sys.exit(main())
