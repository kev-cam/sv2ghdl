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
import os, shutil, tempfile, subprocess, struct

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

    def run(self, netlist_text, signals=None):
        try:
            from bfit import strip_output
            deck = strip_output(netlist_text)
        except Exception:
            deck = netlist_text.replace(".end", "")
        d = tempfile.mkdtemp(prefix="bfit_ng_")
        cir = os.path.join(d, "c.cir")
        raw = os.path.join(d, "c.raw")
        # Productized portable-model path: a `.hdl "x.vams"` in the deck is
        # OpenVAF-compiled to OSDI and loaded (pre_osdi). Behavioral realisations
        # in the deck run natively -- no OpenVAF needed.
        pre = ""
        needs_va = False
        for ln in deck.splitlines():
            s = ln.strip().lower()
            if s.startswith(".hdl") or s.startswith(".va "):
                needs_va = True
                va = ln.split()[1].strip('"')
                pre += "pre_osdi %s\n" % self.compile_va(va, d)
        ctrl = ".control\n" + pre + "set filetype=binary\nrun\nwrite c.raw\nquit\n.endc\n.end\n"
        open(cir, "w").write(deck + ctrl)
        # Resolve the binary up front so a missing/mis-set ngspice gives an
        # actionable message instead of a bare FileNotFoundError deep in the run.
        exe = self.ngspice if (os.path.sep in self.ngspice) else shutil.which(self.ngspice)
        if not exe or not os.path.exists(exe):
            raise RuntimeError(
                "ngspice not found: BFIT_NGSPICE=%r resolved to %r. Set BFIT_NGSPICE "
                "to the ngspice binary (e.g. /usr/bin/ngspice) or put it on PATH."
                % (self.ngspice, exe))
        try:
            r = subprocess.run([exe, "-b", "c.cir"], cwd=d,
                               capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            raise RuntimeError("ngspice timed out (300s) running c.cir in %s" % d)
        if not os.path.exists(raw):
            # ngspice ran but produced no rawfile -- surface WHY (its stderr/stdout
            # carry the real cause: a deck error, or the OSDI/OpenVAF path failing).
            tail = (r.stderr or r.stdout or "").strip()[-1200:]
            hint = (" For the .vams/OSDI path set BFIT_OPENVAF to a working "
                    "openvaf binary." if needs_va else "")
            raise RuntimeError(
                "ngspice produced no rawfile (exit %d).%s\n--- ngspice output (%s) ---\n%s"
                % (r.returncode, hint, cir, tail or "(no output captured)"))
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
