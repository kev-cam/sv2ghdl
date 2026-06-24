"""
bfit sim-driver: ngspice + OpenVAF (the open Verilog-A path).

Pipeline: OpenVAF compiles the portable .vams -> an OSDI shared object; ngspice
`osdi` loads it; the macromodel is instantiated as an `N<name> ... <module>`
device. We run ngspice in batch and parse its rawfile. This keeps the WHOLE
demo open-source (no proprietary engine).

This driver returns the same {signal: [...], "time": [...]} contract as the Xyce
driver, so the tuner is engine-neutral. It is a thin adapter -- everything else
in bfit is sim-agnostic.
"""
import os, tempfile, subprocess, struct

class NgspiceDriver:
    name = "ngspice"
    def __init__(self, ngspice=None, openvaf=None):
        self.ngspice = ngspice or os.environ.get("BFIT_NGSPICE", "ngspice")
        self.openvaf = openvaf or os.environ.get("BFIT_OPENVAF", "openvaf")

    def compile_va(self, vams_path, workdir):
        """OpenVAF: .vams -> .osdi. Cached by mtime in workdir."""
        osdi = os.path.join(workdir, os.path.basename(vams_path).rsplit(".",1)[0] + ".osdi")
        subprocess.run([self.openvaf, vams_path, "-o", osdi], check=True,
                       capture_output=True, text=True)
        return osdi

    def run(self, netlist_text):
        d = tempfile.mkdtemp(prefix="bfit_ng_")
        cir = os.path.join(d, "c.cir")
        raw = os.path.join(d, "c.raw")
        # ngspice control block: batch tran already in the deck; write rawfile.
        deck = netlist_text + f"\n.control\nset filetype=binary\nrun\nwrite {raw}\nquit\n.endc\n"
        open(cir, "w").write(deck)
        subprocess.run([self.ngspice, "-b", cir], cwd=d,
                       capture_output=True, text=True, timeout=300)
        if not os.path.exists(raw):
            raise RuntimeError("ngspice: no rawfile (is ngspice+OpenVAF installed? "
                               "set BFIT_NGSPICE / BFIT_OPENVAF)")
        return _parse_ngspice_raw(raw)

def _parse_ngspice_raw(p):
    b = open(p, "rb").read()
    hdr_end = b.find(b"Binary:\n") + len(b"Binary:\n")
    head = b[:hdr_end].decode("latin1", "replace")
    nvar = int([l for l in head.splitlines() if l.startswith("No. Variables:")][0].split(":")[1])
    npt  = int([l for l in head.splitlines() if l.startswith("No. Points:")][0].split(":")[1])
    names = []
    invars = False
    for l in head.splitlines():
        if l.startswith("Variables:"): invars = True; continue
        if invars and l.strip() and not l.startswith("Binary"):
            parts = l.split()
            if len(parts) >= 2: names.append(parts[1].lower())
    vals = struct.unpack_from("<%dd" % (nvar*npt), b, hdr_end)
    out = {}
    for k in range(nvar):
        col = [vals[r*nvar + k] for r in range(npt)]
        nm = names[k] if k < len(names) else f"v{k}"
        out[nm] = col
        if nm.startswith("v(") and nm.endswith(")"): out[nm[2:-1]] = col
    if "time" not in out and names: out["time"] = out.get(names[0])
    return out
