"""vcs-ams end-to-end tests: bidirectional pins, pulls, Z and weak drivers, open-drain pads and
pad rings -- docs/VAMOS_AMS_DESIGN.md §9 e2e items 8 (with 8b a/b/c), 12, 26, 28 and 29.

Every item compiles one small Verilog+SPICE design with vcs-ams and runs ./simv on each
available analog engine (VACASK and Xyce; one test method per engine), then asserts real
values: rawfile node voltages (closed-form divider values of the D2A's 500.7 ohm series
resistance, the 3500.2 ohm pull/weak strength and the SPICE resistors / level-1 MOS), the
gated-D2A enable nodes, $display'd digital values sampled mid-way between stimulus changes,
the IE report (roles, levels, "moved into the analog deck"), the boundary file, the deck and
the end-of-run line.  Where the doc asks for it (item 12; also used for 8 and 28) the sampled
digital values are compared with what vvp gives for the same testbench with Verilog models
of the SPICE cells.

Needs the whole stack (nvc, iverilog, VACASK and/or Xyce): WSL/Linux.

    cd tests/vamos && python3 -m unittest test_ams_e2e_bidir -v
    VAMOS_TEST_KEEP=1 keeps the case directories (printed at the end).

Each (design, engine) is compiled and run once per class; the test methods assert on the
cached results.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import Dict, List, Optional, Tuple

from ams_e2e_lib import AmsCase, engines_available, needs_ams

ENGINES = ("vacask", "xyce")
DECK = {"vacask": "vamos.sim", "xyce": "vamos.cir"}
NS = 1e-9
IVERILOG = os.environ.get("VAMOS_TEST_IVERILOG", "/usr/local/src/iverilog/_install/bin/iverilog")
VVP = os.environ.get("VAMOS_TEST_VVP", "/usr/local/src/iverilog/_install/bin/vvp")

# ---------------------------------------------------------------------------------------
# electrical constants of the interface elements (§3.4) and the expected divider values
# ---------------------------------------------------------------------------------------

VDD = 1.8                      # the decks' only supply, so every IE's default hiv (§3.3)
RSER = 500.7                   # D2A series resistance (rmap strength 6)
RWEAK = 3500.2                 # weak / pull strength: enable = WF = RSER / RWEAK
WF = RSER / RWEAK              # 0.14305
TOL = 2e-3                     # volts


def divider(v: float, r_drive: float, r_load: float, v_load: float = 0.0) -> float:
    """A source v behind r_drive into r_load to v_load."""
    return (v / r_drive + v_load / r_load) / (1 / r_drive + 1 / r_load)


def mos_on(vdd: float = VDD, r: float = RWEAK, beta: float = 200e-6 * 10, vov: float = 1.3) -> float:
    """Drain voltage of the level-1 open-drain NMOS (kp=200u, W/L=10, Vgs-Vt=1.3) in triode
    against a pull of r to vdd: beta*(vov*v - v^2/2) = (vdd - v)/r."""
    b = beta * vov + 1.0 / r
    return (b - (b * b - 2.0 * beta * vdd / r) ** 0.5) / beta


LOWDRV = divider(0.0, RSER, 10e3, VDD)            # strong 0 against a 10k pull-up: 0.0858 V
HIDRV10K = divider(VDD, RSER, 10e3)               # strong 1 into a 10k load: 1.7142 V
WEAK10K = divider(VDD, RWEAK, 10e3)               # weak/pull 1 into a 10k load: 1.3333 V
VON = mos_on()                                    # open drain on against the moved pull: 0.1908 V

# ---------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------

_END = re.compile(r"co-simulation finished: (digital stop at ([-+0-9.eE]+) s|analog end at "
                  r"([-+0-9.eE]+) s)")


def samples(out: str) -> List[Tuple[int, Dict[str, str]]]:
    """`S <t> a=v b=v ...` lines (nvc pads %0t; vvp does not) -> [(t, {a: v, ...})]."""
    res = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "S" and parts[1].isdigit():
            res.append((int(parts[1]), dict(p.split("=", 1) for p in parts[2:] if "=" in p)))
    return res


def events(out: str, tag: str) -> List[Tuple[int, str]]:
    """(time in ps, value) of every `%0t <tag>=%b` line, in order."""
    ev = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].startswith(tag + "="):
            ev.append((int(parts[0]), parts[1][len(tag) + 1:]))
    return ev


def ie_entries(text: str) -> Dict[str, dict]:
    """interface_element.rpt -> {node: {'d2a': {k: v}, 'a2d': {k: v}, 'comments': [...]}}."""
    entries: Dict[str, dict] = {}
    cur: Optional[dict] = None
    for line in text.splitlines():
        s = line.strip()
        if not s:
            cur = None
            continue
        m = re.match(r"^(d2a|a2d)\s+(.*);$", s)
        if m:
            keys: Dict[str, str] = {}
            for tok in m.group(2).split():
                k, eq, v = tok.partition("=")
                keys[k] = v if eq else ""
            node = keys.pop("node")
            cur = entries.setdefault(node, {"comments": []})
            if m.group(1) in cur:
                cur.setdefault("duplicates", []).append(m.group(1))
            cur[m.group(1)] = keys
            continue
        m = re.match(r"^// node=(\S+): (.*)$", s)
        if m:
            cur = entries.setdefault(m.group(1), {"comments": []})
            cur["comments"].append(m.group(2))
            continue
        if s.startswith("//") and cur is not None:
            cur["comments"].append(s[2:].strip())
    return entries


def role(ents: Dict[str, dict], node: str) -> str:
    """D2A / A2D / BIDIR from the report's IE lines, or the comment of a node without one."""
    e = ents.get(node)
    if e is None:
        return "MISSING"
    if "d2a" in e and "a2d" in e:
        return "BIDIR"
    if "d2a" in e:
        return "D2A"
    if "a2d" in e:
        return "A2D"
    return "NONE: " + "; ".join(e["comments"][:1])


def window(raw, node: str, t0: float, t1: float) -> List[float]:
    return [v for t, v in zip(raw.time(), raw.column(node)) if t0 <= t <= t1]


def read_text(*parts: str) -> str:
    with open(os.path.join(*parts), errors="replace") as fh:
        return fh.read()


class Result:
    """One compile (and, when it succeeded, one ./simv run) of a case directory."""

    def __init__(self, d: str, engine: str, comp, sim):
        self.d, self.engine, self.comp, self.sim = d, engine, comp, sim

    @property
    def cout(self) -> str:
        return self.comp.stdout

    @property
    def sout(self) -> str:
        return self.sim.stdout if self.sim is not None else ""

    def boundary(self) -> List[List[str]]:
        return [ln.split() for ln in read_text(self.d, "simv.daidir", "ams",
                                               "vamos.boundary").splitlines() if ln.strip()]

    def deck(self) -> str:
        return read_text(self.d, "simv.daidir", "ams", "deck", DECK[self.engine])

    def cut_vhd(self) -> str:
        return read_text(self.d, "simv.daidir", "ams", "cut.vhd")

    def xv_nodes(self, xname: str) -> List[str]:
        """The node list of the deck's X instance xname (both deck syntaxes)."""
        for line in self.deck().splitlines():
            s = line.strip()
            if s.lower().startswith(xname.lower() + " "):
                if "(" in s:                                  # VACASK: x (a b) subckt
                    return s[s.index("(") + 1:s.index(")")].split()
                return s.split()[1:-1]                        # Xyce: x a b subckt
        raise AssertionError("no %s in the deck:\n%s" % (xname, self.deck()))


def _per_engine(cls):
    """Every `check_<name>(self, engine)` becomes test_<name>_vacask and test_<name>_xyce."""
    for name in sorted(vars(cls)):
        if not name.startswith("check_"):
            continue
        fn = getattr(cls, name)
        for eng in ENGINES:
            def test(self, fn=fn, eng=eng):
                self.need(eng)
                fn(self, eng)
            test.__name__ = "test_%s_%s" % (name[len("check_"):], eng)
            test.__doc__ = "%s [%s]" % ((fn.__doc__ or name).strip().splitlines()[0], eng)
            setattr(cls, test.__name__, test)
    return cls


class SharedCase(AmsCase):
    """Compiles and runs each (design, engine) once per class; tests assert on the results."""

    _root: Optional[str] = None
    _cache: Optional[Dict[Tuple[str, str], object]] = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._root = tempfile.mkdtemp(prefix="vamos-e2e-bidir-%s-" % cls.__name__)
        cls._cache = {}

    @classmethod
    def tearDownClass(cls):
        if cls._root:
            if os.environ.get("VAMOS_TEST_KEEP"):
                sys.stderr.write("kept %s\n" % cls._root)
            else:
                shutil.rmtree(cls._root, ignore_errors=True)
        super().tearDownClass()

    def need(self, engine: str) -> None:
        if engine not in engines_available():
            self.skipTest("%s is not installed" % engine)

    def _write(self, d: str, files: Dict[str, str]) -> None:
        for rel, text in files.items():
            p = os.path.join(d, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write(text)

    def run_case(self, key: str, files: Dict[str, str], engine: str) -> Result:
        ck = (key, engine)
        if ck not in self._cache:
            d = os.path.join(self._root, "%s_%s" % (key, engine))
            self._write(d, files)
            comp = self.compile(d, "-sverilog", "tb.sv", engine=engine, expect_rc=None)
            sim = None
            if comp.returncode == 0 and os.path.isfile(os.path.join(d, "simv")):
                sim = self.simv(d, expect_rc=None)
            self._cache[ck] = Result(d, engine, comp, sim)
        return self._cache[ck]

    def vvp(self, key: str, files: Dict[str, str]) -> str:
        """stdout of iverilog -g2012 tb.sv models.v | vvp (the digital reference)."""
        ck = ("vvp:" + key, "")
        if ck not in self._cache:
            if not (os.access(IVERILOG, os.X_OK) and os.access(VVP, os.X_OK)):
                self.skipTest("needs iverilog/vvp at %s" % IVERILOG)
            d = os.path.join(self._root, key + "_vvp")
            self._write(d, files)
            r = subprocess.run([IVERILOG, "-g2012", "-o", "tb.vvp", "tb.sv", "models.v"], cwd=d,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               universal_newlines=True, errors="replace", timeout=600)
            self.assertEqual(r.returncode, 0, r.stdout)
            r = subprocess.run([VVP, "-n", "tb.vvp"], cwd=d, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, universal_newlines=True,
                               errors="replace", timeout=600)
            self.assertEqual(r.returncode, 0, r.stdout)
            self._cache[ck] = r.stdout
        return self._cache[ck]

    # -- assertions shared by the items ---------------------------------------------------

    def assertRan(self, r: Result, t_stop: float):
        """Compile and simv exit 0, exactly one end line (a digital stop at t_stop), no error
        lines; the published rawfile reaches t_stop with a matching point count."""
        self.assertEqual(r.comp.returncode, 0, "vcs-ams failed:\n" + r.cout)
        self.assertIsNotNone(r.sim, "no ./simv run:\n" + r.cout)
        self.assertEqual(r.sim.returncode, 0, r.sout)
        ends = _END.findall(r.sout)
        self.assertEqual(len(ends), 1, "want exactly one end-of-run line:\n" + r.sout)
        self.assertTrue(ends[0][0].startswith("digital stop at"), r.sout)
        self.assertAlmostEqual(float(ends[0][1]), t_stop, delta=t_stop * 1e-9)
        self.assertNotIn("** Error", r.sout)
        self.assertNotIn("vamos: error", r.sout)
        raw = self.raw(r.d)
        self.assertAlmostEqual(raw.last_time(), t_stop, delta=t_stop * 1e-9)
        self.assertEqual(raw.declared_points, len(raw.points), "No. Points: of the rawfile")
        return raw

    def assertVolts(self, raw, node: str, want: Dict[float, float], tol: float = TOL) -> None:
        """node voltage at each time (s) of want."""
        got = {t: raw.at(node, t) for t in want}
        bad = {t: (round(got[t], 5), round(v, 5)) for t, v in want.items()
               if abs(got[t] - v) > tol}
        self.assertFalse(bad, "%s (t: (got, want)): %s" % (node, bad))

    def assertRole(self, ents: Dict[str, dict], node: str, want: str) -> None:
        self.assertEqual(role(ents, node), want, "%s: %s" % (node, ents.get(node)))
        self.assertNotIn("duplicates", ents[node], "%s has repeated IE lines" % node)

    def assertBridges(self, r: Result, canonical: str, want: List[str]) -> None:
        """The boundary lines of one analog node: sorted (direction, suffix) pairs."""
        got = sorted((b[0], b[2][len(canonical):]) for b in r.boundary()
                     if b[2].startswith(canonical + "__"))
        self.assertEqual(got, sorted(want), "boundary lines of %s" % canonical)

    def assertSamples(self, out: str, want: List[Tuple[int, Dict[str, str]]]) -> None:
        got = samples(out)
        self.assertEqual([t for t, _ in got], [t for t, _ in want], "sample times:\n" + out)
        for (t, g), (_, w) in zip(got, want):
            sub = {k: g.get(k) for k in w}
            self.assertEqual(sub, w, "sampled values at %d ps" % t)


# =======================================================================================
# item 8: a bidirectional inout pin driven by the digital side and then released;
#         tri-state drivers against a 10 kOhm pull-up
# =======================================================================================

TB08 = """\
`timescale 1ns/1ps
// a tri-state driver one level down, reaching the pin through its inout port
module iodrv (inout p, input en, input d);
  assign p = en ? d : 1'bz;
endmodule

// a pad wrapper with an inout port: one replica's parent drives it, the other's only reads it
module padw (inout io);
  pu_pad u (.pad(io));
endmodule

module tb;
  // (1) declared-inout pin io: driven by the digital side, then released; the SPICE side
  //     drives it back through 10k from a buffer of dat
  reg dat = 1'b1, drv = 1'b0, en = 1'b1;
  wire io;
  assign io = en ? drv : 1'bz;
  bidi u_bidi (.io(io), .dat(dat));
  // (2) tri-state drivers against a 10k pull-up inside the SPICE pad: a continuous assign,
  //     a bufif1 and a driver inside a submodule
  reg d2 = 1'b0, en2 = 1'b1;
  wire pad;
  assign pad = en2 ? d2 : 1'bz;
  pu_pad u_pu (.pad(pad));
  reg x1 = 1'b0, e1 = 1'b1;
  wire q1;
  bufif1 g_q1 (q1, x1, e1);
  pu_pad u_q1 (.pad(q1));
  reg x2 = 1'b0, e2 = 1'b1;
  wire q2;
  iodrv drv2 (.p(q2), .en(e2), .d(x2));
  pu_pad u_q2 (.pad(q2));
  // (3) per-path roles in one shared wrapper: w1's net is driven and read (BIDIR), w2's is
  //     only read (A2D)
  reg en3 = 1'b1, d3 = 1'b0;
  wire n1, n2;
  assign n1 = en3 ? d3 : 1'bz;
  padw w1 (.io(n1));
  padw w2 (.io(n2));

  always @(io) $display("%0t io=%b", $time, io);
  always @(pad) $display("%0t pad=%b", $time, pad);
  task sample;
    $display("S %0t io=%b pad=%b q1=%b q2=%b n1=%b n2=%b", $time, io, pad, q1, q2, n1, n2);
  endtask
  initial begin
    #50 sample;
    #50 drv = 1'b1; d2 = 1'b1; x1 = 1'b1; x2 = 1'b1; d3 = 1'b1;
    #50 sample;
    #50 en = 1'b0; en2 = 1'b0; e1 = 1'b0; e2 = 1'b0; en3 = 1'b0;   // release every pin
    #50 sample;
    #50 dat = 1'b0; en2 = 1'b1; d2 = 1'b0; e1 = 1'b1; x1 = 1'b0; en3 = 1'b1; d3 = 1'b0;
    #50 sample;
    #50 dat = 1'b1; en2 = 1'b0; e1 = 1'b0; e2 = 1'b1; x2 = 1'b0; en3 = 1'b0;
    #50 sample;
    #50 $finish;
  end
endmodule
"""
SP08 = """\
* e2e 8: bidirectional pins
.global vdd
vdd vdd 0 1.8
* the SPICE side drives io through 10k from a buffer of dat
.subckt bidi io dat
e1 int 0 dat 0 1
r1 int io 10k
c1 io 0 10f
.ends
* a pad with a 10k pull-up to vdd
.subckt pu_pad pad
rpu pad vdd 10k
c1 pad 0 10f
.ends
.tran 1n 600n
.end
"""
INIT08 = """\
choose xa pads.sp;
port_dir -cell bidi (inout io; input dat);
port_dir -cell pu_pad (inout pad);
"""
MODELS08 = """\
// Verilog models of the SPICE cells, for the vvp reference only
module bidi (inout io, input dat);
  assign (weak1, weak0) io = dat;
endmodule
module pu_pad (inout pad);
  assign (weak1, weak0) pad = 1'b1;
endmodule
"""
SAMPLES08 = [
    (50000, {"io": "0", "pad": "0", "q1": "0", "q2": "0", "n1": "0", "n2": "1"}),
    (150000, {"io": "1", "pad": "1", "q1": "1", "q2": "1", "n1": "1", "n2": "1"}),
    (250000, {"io": "1", "pad": "1", "q1": "1", "q2": "1", "n1": "1", "n2": "1"}),
    (350000, {"io": "0", "pad": "0", "q1": "0", "q2": "1", "n1": "0", "n2": "1"}),
    (450000, {"io": "1", "pad": "1", "q1": "1", "q2": "0", "n1": "1", "n2": "1"}),
]
T08 = [50 * NS, 150 * NS, 250 * NS, 350 * NS, 450 * NS]

# A weak driver (not a static pull) on a bidirectional net is not in v1 (§0, §5.4 rule 5).
TB08W = """\
`timescale 1ns/1ps
module tb;
  reg c = 1'b1;
  wire pad;
  assign (weak1, weak0) pad = c;
  pu_pad u (.pad(pad));
  always @(pad) $display("%0t pad=%b", $time, pad);
  initial #50 $finish;
endmodule
"""


@needs_ams
@_per_engine
class TestE2E08Bidir(SharedCase):
    """§9 e2e 8: a declared-inout pin driven by the digital side and then released (the SPICE
    side then drives it back); tri-state drivers (assign, bufif1, one level down) against a
    10 kOhm pull-up inside the SPICE pad."""

    FILES = {"tb.sv": TB08, "pads.sp": SP08, "vcsAD.init": INIT08}

    def r(self, engine):
        return self.run_case("e8", self.FILES, engine)

    def check_bidir_pin_driven_then_released(self, engine):
        """io: strong drive wins over the SPICE side's 10k; after the release the SPICE side
        drives the pin and the digital reads it; the enable is 1 while driven, 0 after."""
        r = self.r(engine)
        raw = self.assertRan(r, 500 * NS)
        self.assertVolts(raw, "n_u_bidi_io", dict(zip(T08, [LOWDRV, VDD, VDD, 0.0, VDD])))
        self.assertVolts(raw, "n_u_bidi_io_e", dict(zip(T08, [1.0, 1.0, 0.0, 0.0, 0.0])), 1e-6)
        # the released pin follows dat (SPICE side) and the digital side reads it back
        ev = [e for e in events(r.sout, "io") if e[0] > 200000]
        self.assertEqual([v for _, v in ev], ["0", "1"], events(r.sout, "io"))
        self.assertTrue(300000 <= ev[0][0] <= 301000 and 400000 <= ev[1][0] <= 401000, ev)
        ents = ie_entries(self.report(r.d))
        self.assertRole(ents, "tb.u_bidi.io", "BIDIR")
        self.assertRole(ents, "tb.u_bidi.dat", "D2A")
        e = ents["tb.u_bidi.io"]
        self.assertEqual((float(e["d2a"]["hiv"]), float(e["d2a"]["lov"])), (VDD, 0.0))
        self.assertEqual((float(e["a2d"]["loth"]), float(e["a2d"]["hith"])), (0.9, 0.9))
        self.assertIn("Top-Net tb.io", e["comments"])
        self.assertIn("All Boundary Nets tb.u_bidi.io", e["comments"])
        self.assertBridges(r, "tb.u_bidi.io", [("A2D", "__a"), ("D2A", "__d"), ("D2A", "__e")])

    def check_tristate_against_10k_pullup(self, engine):
        """assign / bufif1 / submodule tri-state drivers against the pad's 10k pull-up:
        0 -> 0.0858 V, 1 -> vdd, released -> vdd (pull-up) with the enable at 0."""
        r = self.r(engine)
        raw = self.assertRan(r, 500 * NS)
        for node, volts, en in (
                ("n_u_pu_pad", [LOWDRV, VDD, VDD, LOWDRV, VDD], [1, 1, 0, 1, 0]),
                ("n_u_q1_pad", [LOWDRV, VDD, VDD, LOWDRV, VDD], [1, 1, 0, 1, 0]),
                ("n_u_q2_pad", [LOWDRV, VDD, VDD, VDD, LOWDRV], [1, 1, 0, 0, 1])):
            self.assertVolts(raw, node, dict(zip(T08, volts)))
            self.assertVolts(raw, node + "_e", dict(zip(T08, [float(x) for x in en])), 1e-6)
        self.assertSamples(r.sout, SAMPLES08)
        # after each release the reader sees the pull-up's 1, never x or z
        ev = events(r.sout, "pad")
        self.assertTrue(all(v in ("0", "1") for t, v in ev if t > 0), ev)
        self.assertEqual(ev[-1], (400000, "1"), ev)
        ents = ie_entries(self.report(r.d))
        for node in ("tb.u_pu.pad", "tb.u_q1.pad", "tb.u_q2.pad"):
            self.assertRole(ents, node, "BIDIR")
            self.assertBridges(r, node, [("A2D", "__a"), ("D2A", "__d"), ("D2A", "__e")])
        self.assertIn("Top-Net tb.q2", ents["tb.u_q2.pad"]["comments"])

    def check_per_path_roles_in_a_shared_wrapper(self, engine):
        """One wrapper module, two instances: the driven-and-read path is BIDIR (0.0858 V /
        vdd / released to vdd with the enable at 0), the read-only path is A2D only (vdd)."""
        r = self.r(engine)
        raw = self.assertRan(r, 500 * NS)
        ents = ie_entries(self.report(r.d))
        self.assertRole(ents, "tb.w1.u.pad", "BIDIR")
        self.assertRole(ents, "tb.w2.u.pad", "A2D")
        self.assertBridges(r, "tb.w1.u.pad", [("A2D", "__a"), ("D2A", "__d"), ("D2A", "__e")])
        self.assertBridges(r, "tb.w2.u.pad", [("A2D", "__a")])
        self.assertVolts(raw, "n_w1_u_pad", dict(zip(T08, [LOWDRV, VDD, VDD, LOWDRV, VDD])))
        self.assertVolts(raw, "n_w1_u_pad_e", dict(zip(T08, [1.0, 1.0, 0.0, 1.0, 0.0])), 1e-6)
        self.assertVolts(raw, "n_w2_u_pad", {t: VDD for t in T08})
        self.assertEqual([s[1]["n1"] + s[1]["n2"] for s in samples(r.sout)],
                         ["01", "11", "11", "01", "11"])

    def check_digital_matches_vvp(self, engine):
        """The sampled digital values equal vvp's for the same testbench (weak Verilog models
        of the SPICE drive and pull-up)."""
        r = self.r(engine)
        self.assertRan(r, 500 * NS)
        ref = samples(self.vvp("e8", {"tb.sv": TB08, "models.v": MODELS08}))
        self.assertEqual(ref, SAMPLES08)
        self.assertEqual(samples(r.sout), ref)

    def check_weak_driver_on_bidir_net_is_refused(self, engine):
        """A weak driver other than a static pull on a BIDIR net is a v1 error naming it."""
        r = self.run_case("e8w", {"tb.sv": TB08W, "pads.sp": SP08, "vcsAD.init": INIT08}, engine)
        self.assertNotEqual(r.comp.returncode, 0, r.cout)
        self.assertRegex(r.cout, r"vamos: error: tb\.u\.pad: weak driver\(s\) tb:sv_strength_buf\S* "
                                 r"\(tb\) on bidirectional net tb\.u\.pad: only static pulls are "
                                 r"supported on a BIDIR net in v1")
        self.assertIsNone(r.sim)


# =======================================================================================
# item 8b: start-up of bidirectional pins
# =======================================================================================

TB08B = """\
`timescale 1ns/1ps
module tb;
  // (a) no digital driver at t=0, pull-up inside the cell
  reg ena = 1'b0, da = 1'b0;
  wire pa;
  assign pa = ena ? da : 1'bz;
  pu_cell u_a (.pad(pa));
  // (b) a strong 1 from t=0 while the cell pulls low through 5k
  reg enb = 1'b1;
  wire pb;
  assign pb = enb ? 1'b1 : 1'bz;
  pd_cell u_b (.pad(pb));
  // (c) the cell holds the pin mid-band (0.9 V); the digital driver is never enabled
  reg enc = 1'b0, dc = 1'b0;
  wire pc;
  assign pc = enc ? dc : 1'bz;
  mid_cell u_c (.pad(pc));
  // (c') the same cell, driven low and released into the mid-band at 100 ns
  reg ence = 1'b1;
  wire pe;
  assign pe = ence ? 1'b0 : 1'bz;
  mid_cell u_e (.pad(pe));

  always @(pa) $display("%0t pa=%b", $time, pa);
  always @(pb) $display("%0t pb=%b", $time, pb);
  always @(pc) $display("%0t pc=%b", $time, pc);
  always @(pe) $display("%0t pe=%b", $time, pe);
  initial begin
    #1   $display("S %0t pa=%b pb=%b pc=%b pe=%b", $time, pa, pb, pc, pe);
    #99  ena = 1'b1; enb = 1'b0; ence = 1'b0;
    #50  $display("S %0t pa=%b pb=%b pc=%b pe=%b", $time, pa, pb, pc, pe);
    #50  $finish;
  end
endmodule
"""
SP08B = """\
* e2e 8b: start-up of bidirectional pins
.global vdd
vdd vdd 0 1.8
* (a) a pull-up inside the cell
.subckt pu_cell pad
rpu pad vdd 10k
.ends
* (b) the cell pulls low through 5k
.subckt pd_cell pad
rpd pad 0 5k
.ends
* (c) the cell holds the pin at 0.9 V
.subckt mid_cell pad
r1 pad vdd 10k
r2 pad 0 10k
.ends
.tran 1n 200n
.end
"""
INIT08B = """\
choose xa cells.sp;
port_dir -cell pu_cell (inout pad);
port_dir -cell pd_cell (inout pad);
port_dir -cell mid_cell (inout pad);
a2d loth=0.6 hith=1.2 midv_time=5n node=tb.u_c.pad;
a2d loth=0.6 hith=1.2 midv_time=5n node=tb.u_e.pad;
"""


@needs_ams
@_per_engine
class TestE2E08bStartup(SharedCase):
    """§9 e2e 8b*: (a) an inout pin with no digital driver at t=0 and a pull-up in the cell;
    (b) a strong 1 from t=0 against a 5 kOhm pull-down; (c) a pin held mid-band (HITH 1.2 V,
    LOTH 0.6 V, node 0.9 V, midv_time 5 ns) with no external driver; plus (c') the same pin
    released into the mid-band after being driven."""

    FILES = {"tb.sv": TB08B, "cells.sp": SP08B, "vcsAD.init": INIT08B}

    def r(self, engine):
        return self.run_case("e8b", self.FILES, engine)

    def check_a_no_driver_at_t0(self, engine):
        """(a) OP at the pull-up voltage with the enable at 0; the reader settles to 1 at t=0
        and sees no transition until the master drives at 100 ns."""
        r = self.r(engine)
        raw = self.assertRan(r, 200 * NS)
        self.assertAlmostEqual(raw.at("n_u_a_pad", 0.0), VDD, delta=1e-3)
        self.assertLess(max(window(raw, "n_u_a_pad_e", 0.0, 99 * NS)), 1e-9)
        self.assertVolts(raw, "n_u_a_pad", {50 * NS: VDD, 150 * NS: LOWDRV})
        ev = events(r.sout, "pa")
        at0 = [v for t, v in ev if t == 0]
        self.assertTrue(at0 and at0[-1] == "1", ev)
        self.assertNotIn("0", at0, ev)                       # no start-up glitch to 0
        self.assertEqual([e for e in ev if 0 < e[0] < 100000], [], ev)
        self.assertEqual(samples(r.sout)[0], (1000, {"pa": "1", "pb": "1", "pc": "x", "pe": "0"}))

    def check_b_strong_one_against_5k_pulldown(self, engine):
        """(b) OP at the hiv/5 kOhm divider value; readers see 1 at t=0; released -> 0 V."""
        r = self.r(engine)
        raw = self.assertRan(r, 200 * NS)
        self.assertAlmostEqual(raw.at("n_u_b_pad", 0.0), divider(VDD, RSER, 5e3), delta=1e-3)
        self.assertGreater(min(window(raw, "n_u_b_pad_e", 0.0, 99 * NS)), 1.0 - 1e-9)
        self.assertVolts(raw, "n_u_b_pad", {50 * NS: divider(VDD, RSER, 5e3), 150 * NS: 0.0})
        ev = events(r.sout, "pb")
        at0 = [v for t, v in ev if t == 0]
        self.assertTrue(at0 and at0[-1] == "1", ev)
        self.assertNotIn("0", [v for t, v in ev if t < 100000], ev)
        self.assertEqual(samples(r.sout)[1][1]["pb"], "0")

    def check_c_mid_band_no_oscillation(self, engine):
        """(c) the node stays at 0.9 V, the enable never leaves 0, the reader stays x with no
        event after t=0 (no D2A/A2D loop), and the run takes a bounded number of steps."""
        r = self.r(engine)
        raw = self.assertRan(r, 200 * NS)
        v = window(raw, "n_u_c_pad", 0.0, 200 * NS)
        self.assertLess(max(abs(x - 0.9) for x in v), 1e-3)
        self.assertLess(max(window(raw, "n_u_c_pad_e", 0.0, 200 * NS)), 1e-9)
        self.assertEqual([e for e in events(r.sout, "pc") if e[0] > 0], [])
        self.assertEqual([s[1]["pc"] for s in samples(r.sout)], ["x", "x"])
        self.assertLess(len(raw.points), 2000, "too many analog steps: oscillation?")
        ents = ie_entries(self.report(r.d))
        self.assertRole(ents, "tb.u_c.pad", "BIDIR")
        a = ents["tb.u_c.pad"]["a2d"]
        self.assertEqual((float(a["loth"]), float(a["hith"]), float(a["midv_time"])),
                         (0.6, 1.2, 5e-9))

    def check_c_released_into_mid_band(self, engine):
        """(c') driven 0 (0.0819 V against the cell's 0.9 V / 5k), released at 100 ns: the
        node returns to 0.9 V, the enable drops to 0 and stays there, the A2D holds 0 and goes
        to x exactly midv_time (5 ns) later, with no further event."""
        r = self.r(engine)
        raw = self.assertRan(r, 200 * NS)
        self.assertVolts(raw, "n_u_e_pad", {50 * NS: divider(0.0, RSER, 5e3, 0.9),
                                            150 * NS: 0.9, 199 * NS: 0.9})
        self.assertLess(max(window(raw, "n_u_e_pad_e", 100.5 * NS, 200 * NS)), 1e-9)
        ev = [e for e in events(r.sout, "pe") if e[0] > 0]
        self.assertEqual([v for _, v in ev], ["0", "x"], ev)
        self.assertEqual(ev[0][0], 100000, ev)
        self.assertTrue(105000 <= ev[1][0] <= 106000, ev)            # midv_time after release


# =======================================================================================
# item 12: gates and pulls driving cut inputs; open-drain outputs and pads with Verilog pulls
# =======================================================================================

TB12 = """\
`timescale 1ns/1ps
module tb;
  // 12a: a nand gate, a pullup and a weak assign driving cut inputs (one D2A each)
  reg a = 1'b0, b = 1'b0, c = 1'b1;
  wire na, pu, wk;
  nand g_nand (na, a, b);
  sink u_nand (.in(na));
  pullup (pu);
  sink u_pull (.in(pu));
  assign (weak1, weak0) wk = c;
  sink u_wk (.in(wk));
  // 12b: an open-drain SPICE output on an auto port, a Verilog pullup, a reader
  reg g_od = 1'b0;
  wire od;
  pullup (od);
  od_out u_od (.g(g_od), .d(od));
  // 12c: SPICE open-drain pad + Verilog pullup + open-drain Verilog master + reader,
  //      with port_dir inout (od_pad_io) and as an auto port (od_pad_auto)
  reg m1 = 1'b0, s1 = 1'b0;
  wire p1;
  pullup (p1);
  assign p1 = m1 ? 1'b0 : 1'bz;
  od_pad_io u_p1 (.pad(p1), .g(s1));
  reg m2 = 1'b0, s2 = 1'b0;
  wire p2;
  pullup (p2);
  assign p2 = m2 ? 1'b0 : 1'bz;
  od_pad_auto u_p2 (.pad(p2), .g(s2));
  // 12d: pull-only, no master: the pad sits at hiv (1.8 V; 1.5 V for p3h by a d2a rule)
  reg s3 = 1'b0, s3h = 1'b0;
  wire p3, p3h;
  pullup (p3);
  pullup (p3h);
  od_pad_io u_p3 (.pad(p3), .g(s3));
  od_pad_io u_p3h (.pad(p3h), .g(s3h));
  // 12e: a tri1 net instead of wire + pullup
  reg m4 = 1'b0, s4 = 1'b0;
  tri1 p4;
  assign p4 = m4 ? 1'b0 : 1'bz;
  od_pad_auto u_p4 (.pad(p4), .g(s4));
  // the pull-down polarity: an open-source SPICE pad (PMOS to vdd, gate active low), a
  // Verilog pulldown (p5) or a tri0 net (p6), an active-high open-source Verilog master
  reg m5 = 1'b0, s5 = 1'b1, s6 = 1'b1;
  wire p5;
  pulldown (p5);
  assign p5 = m5 ? 1'b1 : 1'bz;
  os_pad u_p5 (.pad(p5), .gb(s5));
  tri0 p6;
  assign p6 = m5 ? 1'b1 : 1'bz;
  os_pad u_p6 (.pad(p6), .gb(s6));
  // two SPICE open-drain devices on one line (I2C-like) with a pullup, a master and a reader:
  // one BIDIR node hosted by the first device, the second device's port passive on it
  reg mb = 1'b0, sb1 = 1'b0, sb2 = 1'b0;
  wire sda;
  pullup (sda);
  assign sda = mb ? 1'b0 : 1'bz;
  od_pad_auto u_d1 (.pad(sda), .g(sb1));
  od_pad_auto u_d2 (.pad(sda), .g(sb2));

  always @(od) $display("%0t od=%b", $time, od);
  always @(p1) $display("%0t p1=%b", $time, p1);
  always @(p2) $display("%0t p2=%b", $time, p2);
  always @(p4) $display("%0t p4=%b", $time, p4);
  task sample;
    $display("S %0t na=%b od=%b p1=%b p2=%b p3=%b p3h=%b p4=%b p5=%b p6=%b sda=%b",
             $time, na, od, p1, p2, p3, p3h, p4, p5, p6, sda);
  endtask
  initial begin
    #50 sample;
    #50 a = 1'b1; c = 1'b0; g_od = 1'b1; m1 = 1'b1; m2 = 1'b1; m4 = 1'b1; m5 = 1'b1;
        mb = 1'b1;
    #50 sample;
    #50 b = 1'b1; g_od = 1'b0; m1 = 1'b0; m2 = 1'b0; m4 = 1'b0; m5 = 1'b0; mb = 1'b0;
    #50 sample;
    #50 a = 1'b0; s1 = 1'b1; s2 = 1'b1; s4 = 1'b1; s5 = 1'b0; s6 = 1'b0; sb1 = 1'b1;
    #50 sample;
    #50 s1 = 1'b0; s2 = 1'b0; s4 = 1'b0; s5 = 1'b1; s6 = 1'b1; sb1 = 1'b0; sb2 = 1'b1;
    #50 sample;
    #50 $finish;
  end
endmodule
"""
SP12 = """\
* e2e 12: gates, pulls and open-drain pads
.global vdd
vdd vdd 0 1.8
.model nch nmos level=1 vto=0.5 kp=200u
.model pch pmos level=1 vto=-0.5 kp=200u
* a 10k load
.subckt sink in
rl in 0 10k
.ends
* an open-drain output
.subckt od_out g d
m1 d g 0 0 nch w=10u l=1u
.ends
* open-drain pads: the same circuit, declared inout (port_dir) or auto
.subckt od_pad_io pad g
m1 pad g 0 0 nch w=10u l=1u
.ends
.subckt od_pad_auto pad g
m1 pad g 0 0 nch w=10u l=1u
.ends
* an open-source pad: PMOS to vdd, gate active low
.subckt os_pad pad gb
m1 pad gb vdd vdd pch w=10u l=1u
.ends
.tran 1n 600n
.end
"""
INIT12 = """\
choose xa cells.sp;
port_dir -cell sink (input in);
port_dir -cell od_pad_io (inout pad; input g);
d2a hiv=1.5 node=tb.u_p3h.pad;
"""
MODELS12 = """\
// Verilog models of the SPICE cells, for the vvp reference only
module sink (input in);
endmodule
module od_out (input g, inout d);
  assign d = g ? 1'b0 : 1'bz;
endmodule
module od_pad_io (inout pad, input g);
  assign pad = g ? 1'b0 : 1'bz;
endmodule
module od_pad_auto (inout pad, input g);
  assign pad = g ? 1'b0 : 1'bz;
endmodule
module os_pad (inout pad, input gb);
  assign pad = gb ? 1'bz : 1'b1;
endmodule
"""
_S12 = "na od p1 p2 p3 p3h p4 p5 p6 sda".split()
SAMPLES12 = [(t, dict(zip(_S12, v.split()))) for t, v in (
    (50000, "1 1 1 1 1 1 1 0 0 1"),
    (150000, "1 0 0 0 1 1 0 1 1 0"),
    (250000, "0 1 1 1 1 1 1 0 0 1"),
    (350000, "1 1 0 0 1 1 0 1 1 0"),
    (450000, "1 1 1 1 1 1 1 0 0 0"))]
T12 = [50 * NS, 150 * NS, 250 * NS, 350 * NS, 450 * NS]
OD_PADS = [  # (canonical, deck node, pull)
    ("tb.u_od.d", "n_u_od_d", "up"), ("tb.u_p1.pad", "n_u_p1_pad", "up"),
    ("tb.u_p2.pad", "n_u_p2_pad", "up"), ("tb.u_p3.pad", "n_u_p3_pad", "up"),
    ("tb.u_p3h.pad", "n_u_p3h_pad", "up"), ("tb.u_p4.pad", "n_u_p4_pad", "up"),
    ("tb.u_p5.pad", "n_u_p5_pad", "down"), ("tb.u_p6.pad", "n_u_p6_pad", "down"),
    ("tb.u_d1.pad", "n_u_d1_pad", "up")]


@needs_ams
@_per_engine
class TestE2E12Pulls(SharedCase):
    """§9 e2e 12: a nand gate and a pullup driving cut inputs (one D2A each; also a weak
    assign); an open-drain SPICE output with a Verilog pullup; SPICE open-drain pads with a
    Verilog pullup, an open-drain Verilog master and a reader (port_dir inout and auto) giving
    1/0/1 as vvp does; a pull-only pad at hiv; a tri1 variant; and the pull-down polarity
    (pulldown, tri0)."""

    FILES = {"tb.sv": TB12, "cells.sp": SP12, "vcsAD.init": INIT12}

    def r(self, engine):
        return self.run_case("e12", self.FILES, engine)

    def check_a_gate_pullup_weak_one_d2a_each(self, engine):
        """nand -> strong D2A (1.714 V / 0 V into 10k); pullup and weak assign -> one D2A
        each with the enable at the weak fraction (1.333 V); the pull stays digital."""
        r = self.r(engine)
        raw = self.assertRan(r, 500 * NS)
        self.assertVolts(raw, "n_u_nand_in", dict(zip(T12, [HIDRV10K, HIDRV10K, 0.0,
                                                            HIDRV10K, HIDRV10K])))
        self.assertVolts(raw, "n_u_nand_in_e", {t: 1.0 for t in T12}, 1e-6)
        self.assertVolts(raw, "n_u_pull_in", {t: WEAK10K for t in T12})
        self.assertVolts(raw, "n_u_pull_in_e", {t: WF for t in T12}, 1e-5)
        self.assertVolts(raw, "n_u_wk_in", dict(zip(T12, [WEAK10K, 0.0, 0.0, 0.0, 0.0])))
        self.assertVolts(raw, "n_u_wk_in_e", {t: WF for t in T12}, 1e-5)
        ents = ie_entries(self.report(r.d))
        for node in ("tb.u_nand.in", "tb.u_pull.in", "tb.u_wk.in"):
            self.assertRole(ents, node, "D2A")
            self.assertBridges(r, node, [("D2A", "__d"), ("D2A", "__e")])
        self.assertFalse(any("moved into the analog deck" in c
                             for c in ents["tb.u_pull.in"]["comments"]))

    def check_b_open_drain_output_reads_one_while_off(self, engine):
        """od_out (auto port) with a Verilog pullup and a reader: the pull is moved into the
        deck (BIDIR, enable at WF toward vdd): drain off -> vdd and the reader sees 1; on ->
        the level-1 NMOS against the pull (0.19 V) and the reader sees 0."""
        r = self.r(engine)
        raw = self.assertRan(r, 500 * NS)
        self.assertVolts(raw, "n_u_od_d", dict(zip(T12, [VDD, VON, VDD, VDD, VDD])))
        self.assertVolts(raw, "n_u_od_d_e", {t: WF for t in T12}, 1e-5)
        self.assertEqual([s[1]["od"] for s in samples(r.sout)], ["1", "0", "1", "1", "1"])
        ents = ie_entries(self.report(r.d))
        self.assertRole(ents, "tb.u_od.d", "BIDIR")
        self.assertTrue(any(c.startswith("pull-up moved into the analog deck")
                            for c in ents["tb.u_od.d"]["comments"]), ents["tb.u_od.d"])

    def check_c_open_drain_pads_match_vvp(self, engine):
        """p1 (port_dir inout), p2 (auto port), p4 (tri1), p5/p6 (pulldown/tri0): 1/0/1 as vvp
        gives; the pad voltages: pull -> vdd, master 0 -> 0 V (enable 1), the SPICE open drain
        -> 0.19 V (pull still on)."""
        r = self.r(engine)
        raw = self.assertRan(r, 500 * NS)
        ref = samples(self.vvp("e12", {"tb.sv": TB12, "models.v": MODELS12}))
        self.assertEqual(ref, SAMPLES12)
        self.assertEqual(samples(r.sout), ref)
        for node in ("n_u_p1_pad", "n_u_p2_pad", "n_u_p4_pad"):
            self.assertVolts(raw, node, dict(zip(T12, [VDD, 0.0, VDD, VON, VDD])))
            self.assertVolts(raw, node + "_e", dict(zip(T12, [WF, 1.0, WF, WF, WF])), 1e-5)
        for node in ("n_u_p5_pad", "n_u_p6_pad"):
            self.assertVolts(raw, node, dict(zip(T12, [0.0, VDD, 0.0, VDD - VON, 0.0])))
            self.assertVolts(raw, node + "_d", {50 * NS: 0.0, 250 * NS: 0.0, 450 * NS: 0.0}, 1e-6)

    def check_c_two_open_drain_devices_on_one_line(self, engine):
        """An I2C-like line: two SPICE open-drain devices, a pullup, a master and a reader make
        one BIDIR node hosted by the first device (the second port passive on the same deck
        node); either device pulls it to 0.19 V, the master to 0 V."""
        r = self.r(engine)
        raw = self.assertRan(r, 500 * NS)
        ents = ie_entries(self.report(r.d))
        self.assertRole(ents, "tb.u_d1.pad", "BIDIR")
        self.assertNotIn("tb.u_d2.pad", ents)
        self.assertIn("All Boundary Nets tb.u_d1.pad tb.u_d2.pad",
                      ents["tb.u_d1.pad"]["comments"])
        self.assertEqual(r.xv_nodes("xv_u_d1")[0], r.xv_nodes("xv_u_d2")[0])
        self.assertBridges(r, "tb.u_d2.pad", [])
        self.assertVolts(raw, "n_u_d1_pad", dict(zip(T12, [VDD, 0.0, VDD, VON, VON])))
        self.assertEqual([s[1]["sda"] for s in samples(r.sout)], ["1", "0", "1", "0", "0"])

    def check_d_pull_only_pad_at_hiv(self, engine):
        """A pull-only pad with no master sits at hiv: 1.8 V, and 1.5 V under `d2a hiv=1.5`
        (the moved pull drives toward the node's own hiv); both read 1."""
        r = self.r(engine)
        raw = self.assertRan(r, 500 * NS)
        self.assertVolts(raw, "n_u_p3_pad", {t: VDD for t in T12})
        self.assertVolts(raw, "n_u_p3h_pad", {t: 1.5 for t in T12})
        self.assertVolts(raw, "n_u_p3h_pad_e", {t: WF for t in T12}, 1e-5)
        self.assertEqual({s[1]["p3"] + s[1]["p3h"] for s in samples(r.sout)}, {"11"})
        ents = ie_entries(self.report(r.d))
        self.assertEqual(float(ents["tb.u_p3h.pad"]["d2a"]["hiv"]), 1.5)
        self.assertEqual(float(ents["tb.u_p3.pad"]["d2a"]["hiv"]), VDD)

    def check_e_pulls_moved_into_the_deck(self, engine):
        """Every pull on a bidirectional pad (pullup, tri1, pulldown, tri0) is one BIDIR node
        with the pull moved into the deck and commented out of the digital side; the pull on
        the D2A-only input stays digital."""
        r = self.r(engine)
        self.assertRan(r, 500 * NS)
        ents = ie_entries(self.report(r.d))
        for canonical, _, pull in OD_PADS:
            self.assertRole(ents, canonical, "BIDIR")
            self.assertTrue(any(c.startswith("pull-%s moved into the analog deck" % pull)
                                for c in ents[canonical]["comments"]),
                            "%s: %s" % (canonical, ents[canonical]["comments"]))
            self.assertBridges(r, canonical, [("A2D", "__a"), ("D2A", "__d"), ("D2A", "__e")])
        cut = r.cut_vhd()
        self.assertEqual(cut.count("-- vamos: pull moved into the analog deck (BIDIR):"),
                         len(OD_PADS), cut)
        self.assertIn("sv_tri1_p4", cut)
        self.assertIn("sv_tri0_p6", cut)


# =======================================================================================
# item 26: tri-state on an auto port; cut inputs tied to Z or undriven
# =======================================================================================

TB26 = """\
`timescale 1ns/1ps
module wrap (input x);
  pd_in u (.a(x));
endmodule

module tb;
  // 26a: a tri-state driver on an auto port of a pad with an internal pull-up, plus a reader
  reg en = 1'b1, d = 1'b0;
  wire pad;
  assign pad = en ? d : 1'bz;
  pu_pad u_pad (.pad(pad));
  always @(pad) $display("%0t pad=%b", $time, pad);
  // 26b: cut inputs tied to 1'bz or on an undriven wire, against a SPICE pull-down
  wire fl_in, fl_au;
  pd_in u_z (.a(1'bz));          // declared input, tied to Z
  pd_in u_w (.a(fl_in));         // declared input, undriven wire
  pd_au a_z (.a(1'bz));          // auto port, tied to Z
  pd_au a_w (.a(fl_au));         // auto port, undriven wire
  wrap  w_z (.x(1'bz));          // Z reaching the cut input through a wrapper port
  wrap  w_1 (.x(1'b1));          // control: a strong 1 through the same wrapper is a D2A
  // 26c: a vector constant with a Z bit: bit 0 is driven 1 (a D2A), bit 1 is Z (no IE)
  wire [1:0] wv;
  assign wv = 2'bz1;
  pd_in u_v0 (.a(wv[0]));
  pd_in u_v1 (.a(wv[1]));
  task sample;
    $display("S %0t pad=%b", $time, pad);
  endtask
  initial begin
    #50 sample;
    #50 d = 1'b1;
    #50 sample;
    #50 en = 1'b0;
    #50 sample;
    #50 $finish;
  end
endmodule
"""
SP26 = """\
* e2e 26: tri-state on an auto port; undriven cut inputs against a pull-down
.global vdd
vdd vdd 0 1.8
* a pad with an internal 10k pull-up
.subckt pu_pad pad
rpu pad vdd 10k
.ends
* inputs with a 10k pull-down; a weak 40k pull-up holds them at 0.36 V
.subckt pd_in a
rpd a 0 10k
rw a vdd 40k
.ends
.subckt pd_au a
rpd a 0 10k
rw a vdd 40k
.ends
.tran 1n 400n
.end
"""
INIT26 = """\
choose xa cells.sp;
port_dir -cell pd_in (input a);
"""
PD_LEVEL = divider(VDD, 40e3, 10e3)             # 0.36 V
UNDRIVEN26 = [("tb.u_z.a", "n_u_z_a"), ("tb.u_w.a", "n_u_w_a"), ("tb.a_z.a", "n_a_z_a"),
              ("tb.a_w.a", "n_a_w_a"), ("tb.w_z.u.a", "n_w_z_u_a")]


@needs_ams
@_per_engine
class TestE2E26TriState(SharedCase):
    """§9 e2e 26*: a tri-state driver on an auto port of a SPICE pad with an internal pull-up
    (after release pad = vdd and the reader sees 1); cut inputs tied to 1'bz or on an undriven
    wire, against a SPICE pull-down: no D2A, the node keeps its analog value."""

    FILES = {"tb.sv": TB26, "cells.sp": SP26, "vcsAD.init": INIT26}

    def r(self, engine):
        return self.run_case("e26", self.FILES, engine)

    def check_a_tristate_auto_port_released_to_vdd(self, engine):
        """0 -> 0.0858 V, 1 -> vdd, released -> vdd through the pull-up with the enable at 0;
        the reader sees 1 after the release; the auto port is inout and the node BIDIR."""
        r = self.r(engine)
        raw = self.assertRan(r, 300 * NS)
        t = [50 * NS, 150 * NS, 250 * NS]
        self.assertVolts(raw, "n_u_pad_pad", dict(zip(t, [LOWDRV, VDD, VDD])))
        self.assertVolts(raw, "n_u_pad_pad_e", dict(zip(t, [1.0, 1.0, 0.0])), 1e-6)
        self.assertLess(max(window(raw, "n_u_pad_pad_e", 200.5 * NS, 300 * NS)), 1e-9)
        self.assertEqual([s[1]["pad"] for s in samples(r.sout)], ["0", "1", "1"])
        ev = [e for e in events(r.sout, "pad") if e[0] >= 200000]
        self.assertTrue(ev and all(v == "1" for _, v in ev), events(r.sout, "pad"))
        ents = ie_entries(self.report(r.d))
        self.assertRole(ents, "tb.u_pad.pad", "BIDIR")
        self.assertTrue(any(c.startswith("direction: auto→inout")
                            for c in ents["tb.u_pad.pad"]["comments"]), ents["tb.u_pad.pad"])

    def check_b_z_tied_and_undriven_inputs_keep_analog_value(self, engine):
        """Z-tied and undriven cut inputs (declared input or auto, direct or through a wrapper
        port) get no IE: no d2a line, no boundary line, no bridge source in the deck, and the
        node keeps the SPICE divider's 0.36 V; a 1 tie through the same wrapper is a D2A."""
        r = self.r(engine)
        raw = self.assertRan(r, 300 * NS)
        ents = ie_entries(self.report(r.d))
        deck = r.deck()
        names = {b[2] for b in r.boundary()}
        for canonical, node in UNDRIVEN26:
            self.assertIn(canonical, ents, sorted(ents))
            self.assertNotIn("d2a", ents[canonical], ents[canonical])
            self.assertNotIn("a2d", ents[canonical], ents[canonical])
            self.assertFalse([n for n in names if n.startswith(canonical + "__")], names)
            self.assertNotIn(":%s__d" % canonical, deck)
            v = window(raw, node, 0.0, 300 * NS)
            self.assertLess(max(abs(x - PD_LEVEL) for x in v), 1e-4, node)
        self.assertRole(ents, "tb.w_1.u.a", "D2A")
        self.assertVolts(raw, "n_w_1_u_a", {150 * NS: divider(VDD, RSER, 8e3, PD_LEVEL)})

    def check_c_z_bit_of_a_vector_constant(self, engine):
        """`assign wv = 2'bz1`: bit 0 is a D2A (1.715 V into the cell), bit 1 drives only Z:
        no IE on it, no bridge, and its node keeps the SPICE divider's 0.36 V."""
        # tgt-vhdl writes `wv <= logic3d_vector'(L3D_Z, L3D_1)`: the cut reads it bit by bit,
        # and an element that can only produce Z drives nothing (§5.4)
        r = self.r(engine)
        raw = self.assertRan(r, 300 * NS)
        ents = ie_entries(self.report(r.d))
        self.assertRole(ents, "tb.u_v0.a", "D2A")
        self.assertVolts(raw, "n_u_v0_a", {150 * NS: divider(VDD, RSER, 8e3, PD_LEVEL)})
        v = window(raw, "n_u_v1_a", 0.0, 300 * NS)
        self.assertLess(max(abs(x - PD_LEVEL) for x in v), 1e-4)
        self.assertIn("tb.u_v1.a", ents, sorted(ents))
        self.assertNotIn("d2a", ents["tb.u_v1.a"], ents["tb.u_v1.a"])
        self.assertFalse([b for b in r.boundary() if b[2].startswith("tb.u_v1.a__")])


# =======================================================================================
# item 28: generate pad rings on bit-selects (declared inout and auto ports)
# =======================================================================================

TB28 = """\
`timescale 1ns/1ps
// a pad wrapper: a pullup and a tri-state driver around the SPICE pad
module pad_w (inout pad, input oe, input d);
  pullup (pad);
  assign pad = oe ? d : 1'bz;
  pad_sp u (.pad(pad));
endmodule

module tb;
  // ring A: auto ports on a descending bus with a non-zero LSB;
  // ring B: declared-inout ports (port_dir) on an ascending bus.
  //   bit 3 / 0: digitally driven, never read   (A: D2A only; B: BIDIR, declared inout)
  //   bit 4 / 1: never driven digitally, read   (A2D only; the SPICE side drives it)
  //   bit 5 / 2: tri-state driver plus reader   (BIDIR)
  // Each ring pad's SPICE side drives the pad through 10k from its ctl bit.
  wire [5:3] pa;
  wire [0:2] pb;
  reg  [5:3] ca = 3'b001;        // ca[3] keeps its initial 1 (a static control bit)
  reg  [0:2] cb = 3'b000;        // cb[0] keeps its initial 0
  reg d0 = 1'b0, en2 = 1'b1, d2 = 1'b0;
  assign pa[3] = d0;
  assign pa[5] = en2 ? d2 : 1'bz;
  assign pb[0] = d0;
  assign pb[2] = en2 ? d2 : 1'bz;
  // ring W: pad wrappers, the pull and the tri-state inside one shared module
  wire [2:0] pw;
  reg  [2:0] oe = 3'b000, dw = 3'b000;
  genvar g;
  generate
    for (g = 3; g <= 5; g = g + 1) begin : ra
      ring_au ua (.pad(pa[g]), .ctl(ca[g]));
    end
    for (g = 0; g <= 2; g = g + 1) begin : rb
      ring_io ub (.pad(pb[g]), .ctl(cb[g]));
    end
    for (g = 0; g < 3; g = g + 1) begin : rw
      pad_w w (.pad(pw[g]), .oe(oe[g]), .d(dw[g]));
    end
  endgenerate
  always @(pa[4]) $display("%0t a4=%b", $time, pa[4]);
  always @(pa[5]) $display("%0t a5=%b", $time, pa[5]);
  always @(pb[1]) $display("%0t b1=%b", $time, pb[1]);
  always @(pb[2]) $display("%0t b2=%b", $time, pb[2]);
  always @(pw) $display("%0t pw=%b", $time, pw);
  task sample;
    $display("S %0t a4=%b a5=%b b1=%b b2=%b pw=%b", $time, pa[4], pa[5], pb[1], pb[2], pw);
  endtask
  initial begin
    #50 sample;
    #50 d0 = 1'b1; ca[4] = 1'b1; cb[1] = 1'b1; d2 = 1'b1; oe = 3'b101; dw = 3'b001;
    #50 sample;
    #50 en2 = 1'b0; ca[5] = 1'b1; cb[2] = 1'b0; ca[4] = 1'b0; oe = 3'b010; dw = 3'b000;
    #50 sample;
    #50 ca[5] = 1'b0; cb[2] = 1'b1; cb[1] = 1'b0; d0 = 1'b0;
    #50 sample;
    #50 $finish;
  end
endmodule
"""
SP28 = """\
* e2e 28: pad rings; each ring pad's SPICE side drives it through 10k from ctl
.global vdd
vdd vdd 0 1.8
.subckt ring_io pad ctl
e1 int 0 ctl 0 1
r1 int pad 10k
.ends
.subckt ring_au pad ctl
e1 int 0 ctl 0 1
r1 int pad 10k
.ends
* the wrapped pad: a 100k load
.subckt pad_sp pad
rl pad 0 100k
.ends
.tran 1n 500n
.end
"""
INIT28 = """\
choose xa cells.sp;
port_dir -cell ring_io (inout pad; input ctl);
d2a hiv=1.2 node=tb.pa[5];
a2d loth=0.3 hith=0.6 node=tb.pb[1];
"""
MODELS28 = """\
// Verilog models of the SPICE pads (a weak drive from ctl), for the vvp reference only
module ring_io (inout pad, input ctl);
  assign (weak1, weak0) pad = ctl;
endmodule
module ring_au (inout pad, input ctl);
  assign (weak1, weak0) pad = ctl;
endmodule
module pad_sp (inout pad);
endmodule
"""
SAMPLES28 = [
    (50000, {"a4": "0", "a5": "0", "b1": "0", "b2": "0", "pw": "111"}),
    (150000, {"a4": "1", "a5": "1", "b1": "1", "b2": "1", "pw": "011"}),
    (250000, {"a4": "0", "a5": "1", "b1": "1", "b2": "0", "pw": "101"}),
    (350000, {"a4": "0", "a5": "0", "b1": "0", "b2": "1", "pw": "101"}),
]
T28 = [50 * NS, 150 * NS, 250 * NS, 350 * NS]
PW_REL = divider(VDD, RWEAK, 100e3)            # a released wrapper pad (moved pull): 1.739 V
PW_HI = divider(VDD, RSER, 100e3)              # a wrapper pad driven 1: 1.791 V
RING28 = {  # canonical -> (deck node, expected role, volts at T28)
    "tb.ra[3].ua.pad": ("n_ra_3__ua_pad", "D2A", None),       # value: see the static-bit test
    "tb.ra[4].ua.pad": ("n_ra_4__ua_pad", "A2D", [0.0, VDD, 0.0, 0.0]),
    "tb.ra[5].ua.pad": ("n_ra_5__ua_pad", "BIDIR", [0.0, divider(1.2, RSER, 10e3), VDD, 0.0]),
    "tb.rb[0].ub.pad": ("n_rb_0__ub_pad", "BIDIR", [0.0, HIDRV10K, HIDRV10K, 0.0]),
    "tb.rb[1].ub.pad": ("n_rb_1__ub_pad", "A2D", [0.0, VDD, VDD, 0.0]),
    "tb.rb[2].ub.pad": ("n_rb_2__ub_pad", "BIDIR", [0.0, HIDRV10K, 0.0, VDD]),
    "tb.rw[0].w.u.pad": ("n_rw_0__w_u_pad", "BIDIR", [PW_REL, PW_HI, PW_REL, PW_REL]),
    "tb.rw[1].w.u.pad": ("n_rw_1__w_u_pad", "BIDIR", [PW_REL, PW_REL, 0.0, 0.0]),
    "tb.rw[2].w.u.pad": ("n_rw_2__w_u_pad", "BIDIR", [PW_REL, 0.0, PW_REL, PW_REL]),
}
XV28 = {"tb.ra[%d].ua.pad" % g: "xv_ra_%d__ua" % g for g in (3, 4, 5)}
XV28.update({"tb.rb[%d].ub.pad" % g: "xv_rb_%d__ub" % g for g in (0, 1, 2)})
XV28.update({"tb.rw[%d].w.u.pad" % g: "xv_rw_%d__w_u" % g for g in (0, 1, 2)})

# The doc's literal ring, twice in one module: both generate blocks name their instance `u`.
TB28L = """\
`timescale 1ns/1ps
module tb;
  wire [1:0] pa, pb;
  reg d = 1'b0;
  assign pa[0] = d;
  assign pb[0] = ~d;
  genvar g;
  generate
    for (g = 0; g < 2; g = g + 1) begin : ga
      pad_sp u (.pad(pa[g]));
    end
    for (g = 0; g < 2; g = g + 1) begin : gb
      pad_sp u (.pad(pb[g]));
    end
  endgenerate
  initial begin
    #50 d = 1'b1;
    #50 $finish;
  end
endmodule
"""
SP28L = """\
* e2e 28: two rings of loaded pads
.global vdd
vdd vdd 0 1.8
.subckt pad_sp pad
rl pad 0 10k
.ends
.tran 1n 200n
.end
"""


@needs_ams
@_per_engine
class TestE2E28PadRing(SharedCase):
    """§9 e2e 28: generate pad rings `for (g...) cell u (.pad(bus[g]))` on bit-selects, with
    declared inout ports (ascending bus) and with auto ports (descending bus, non-zero LSB),
    covering the D2A-only, A2D-only and bidirectional roles, plus a ring of pad wrappers with
    a pull and a tri-state inside one shared module: each pad its own node."""

    FILES = {"tb.sv": TB28, "cells.sp": SP28, "vcsAD.init": INIT28}

    def r(self, engine):
        return self.run_case("e28", self.FILES, engine)

    def check_each_pad_its_own_node(self, engine):
        """Nine pads, nine distinct deck nodes (each X instance's pad node), each with its own
        voltage; the Verilog-name rules (tb.pa[5], tb.pb[1]) reach exactly their pads; the
        sampled digital values equal vvp's."""
        r = self.r(engine)
        raw = self.assertRan(r, 400 * NS)
        pads = {c: r.xv_nodes(x)[0] for c, x in XV28.items()}
        self.assertEqual(pads, {c: v[0] for c, v in RING28.items()})
        self.assertEqual(len(set(pads.values())), len(pads))
        for canonical, (node, _, volts) in sorted(RING28.items()):
            if volts is not None:
                self.assertVolts(raw, node, dict(zip(T28, volts)))
        ents = ie_entries(self.report(r.d))
        self.assertEqual(float(ents["tb.ra[5].ua.pad"]["d2a"]["hiv"]), 1.2)
        hiv = {c: float(e["d2a"]["hiv"]) for c, e in ents.items() if "d2a" in e and c != "tb.ra[5].ua.pad"}
        self.assertEqual(set(hiv.values()), {VDD}, hiv)
        a = ents["tb.rb[1].ub.pad"]["a2d"]
        self.assertEqual((float(a["loth"]), float(a["hith"])), (0.3, 0.6))
        loth = {c: float(e["a2d"]["loth"]) for c, e in ents.items()
                if "a2d" in e and c != "tb.rb[1].ub.pad"}
        self.assertEqual(set(loth.values()), {0.9}, loth)
        ref = samples(self.vvp("e28", {"tb.sv": TB28, "models.v": MODELS28}))
        self.assertEqual(ref, SAMPLES28)
        self.assertEqual(samples(r.sout), ref)

    def check_roles_d2a_a2d_bidir(self, engine):
        """Roles per §5.4: a driven, unread auto pad is D2A only; an undriven, read pad is A2D
        only (auto or declared inout); a tri-state pad with a reader, and every driven
        declared-inout pad, is BIDIR; the wrapper pads are BIDIR with the pull moved."""
        # tgt-vhdl writes the bus drivers as one concatenation `pa <= t_a & t_z & t_b` whose
        # bit-4 operand is only ever assigned L3D_Z: that bit is not driven (§5.4, per bit),
        # so tb.ra[4].ua.pad and tb.rb[1].ub.pad stay A2D only
        r = self.r(engine)
        self.assertRan(r, 400 * NS)
        ents = ie_entries(self.report(r.d))
        got = {c: role(ents, c) for c in RING28}
        self.assertEqual(got, {c: v[1] for c, v in RING28.items()})
        for canonical, (_, want, _) in RING28.items():
            sfx = {"D2A": [("D2A", "__d"), ("D2A", "__e")], "A2D": [("A2D", "__a")],
                   "BIDIR": [("A2D", "__a"), ("D2A", "__d"), ("D2A", "__e")]}[want]
            self.assertBridges(r, canonical, sfx)
        for g in range(3):
            c = "tb.rw[%d].w.u.pad" % g
            self.assertTrue(any(x.startswith("pull-up moved into the analog deck")
                                for x in ents[c]["comments"]), ents[c])

    def check_static_control_bit_from_initialiser(self, engine):
        """ca[3] is never reassigned after `reg [5:3] ca = 3'b001`: its cut input is driven 1
        (D2A at hiv), so pad pa[3]'s SPICE side pulls toward vdd through 10k and the digital
        0 on pa[3] gives 0.0858 V; cb[0] likewise holds 0 through a D2A."""
        # ca[4]/ca[5] are assigned, ca[3] never is: the initializer drives each bit that has
        # no other source with its own literal (a missing D2A would leave the SPICE input at
        # 0 V while the digital value is 1)
        r = self.r(engine)
        raw = self.assertRan(r, 400 * NS)
        ents = ie_entries(self.report(r.d))
        self.assertRole(ents, "tb.ra[3].ua.ctl", "D2A")
        self.assertRole(ents, "tb.rb[0].ub.ctl", "D2A")
        self.assertVolts(raw, "n_ra_3__ua_ctl", {t: VDD for t in T28})
        self.assertVolts(raw, "n_ra_3__ua_pad", dict(zip(T28, [LOWDRV, VDD, VDD, LOWDRV])))

    def check_two_rings_with_the_same_instance_name(self, engine):
        """The doc's ring twice in one module, both generate blocks naming the instance `u`:
        compiles, and each ring's pads are their own nodes."""
        # both loops' instances would get the VHDL labels u_g0/u_g1 (basename + genvar
        # suffix): the translated labels must stay unique within the architecture
        r = self.run_case("e28l", {"tb.sv": TB28L, "cells.sp": SP28L,
                                   "vcsAD.init": "choose xa cells.sp;\n"}, engine)
        raw = self.assertRan(r, 100 * NS)
        ents = ie_entries(self.report(r.d))
        self.assertRole(ents, "tb.ga[0].u.pad", "D2A")
        self.assertRole(ents, "tb.gb[0].u.pad", "D2A")
        self.assertVolts(raw, "n_ga_0__u_pad", {25 * NS: 0.0, 75 * NS: HIDRV10K})
        self.assertVolts(raw, "n_gb_0__u_pad", {25 * NS: HIDRV10K, 75 * NS: 0.0})


# =======================================================================================
# item 29: two cut outputs on one digitally read net
# =======================================================================================

TB29 = """\
`timescale 1ns/1ps
module tb;
  reg clk = 1'b0;
  always #25 clk = ~clk;
  wire a = clk;
  // two declared SPICE outputs (port_dir output) on one digitally read net
  wire yo;
  drv_out u1 (.a(a), .y(yo));
  drv_out u2 (.a(a), .y(yo));
  // the same with auto ports
  wire ya;
  drv_au u3 (.a(a), .y(ya));
  drv_au u4 (.a(a), .y(ya));
  always @(yo) $display("%0t yo=%b", $time, yo);
  always @(ya) $display("%0t ya=%b", $time, ya);
  initial #200 $finish;
endmodule
"""
SP29 = """\
* e2e 29: two buffers drive one node through 1k each
.global vdd
vdd vdd 0 1.8
.subckt drv_out a y
e1 int 0 a 0 1
r1 int y 1k
.ends
.subckt drv_au a y
e1 int 0 a 0 1
r1 int y 1k
.ends
.tran 1n 300n
.end
"""
INIT29 = """\
choose xa cells.sp;
port_dir -cell drv_out (input a; output y);
"""


@needs_ams
@_per_engine
class TestE2E29SharedOutput(SharedCase):
    """§9 e2e 29: two cut outputs on one digitally read net (declared outputs, and auto ports):
    exactly one A2D, and the digital value is never X after the first analog step."""

    FILES = {"tb.sv": TB29, "cells.sp": SP29, "vcsAD.init": INIT29}

    def r(self, engine):
        return self.run_case("e29", self.FILES, engine)

    def check_one_a2d_never_x(self, engine):
        """One A2D per net (hosted by the first port in walk order, the other port passive and
        on the same deck node); the net follows the clock with no x after the first value."""
        r = self.r(engine)
        raw = self.assertRan(r, 200 * NS)
        ents = ie_entries(self.report(r.d))
        a2ds = sorted(b[2] for b in r.boundary() if b[0] == "A2D")
        self.assertEqual(a2ds, ["tb.u1.y__a", "tb.u3.y__a"])
        for host, other in (("tb.u1.y", "tb.u2.y"), ("tb.u3.y", "tb.u4.y")):
            self.assertRole(ents, host, "A2D")
            self.assertNotIn(other, ents)
            self.assertIn("All Boundary Nets %s %s" % (host, other), ents[host]["comments"])
        self.assertEqual(r.xv_nodes("xv_u1")[1], r.xv_nodes("xv_u2")[1])
        self.assertEqual(r.xv_nodes("xv_u3")[1], r.xv_nodes("xv_u4")[1])
        for node in ("n_u1_y", "n_u3_y"):
            self.assertVolts(raw, node, {10 * NS: 0.0, 40 * NS: VDD, 60 * NS: 0.0, 90 * NS: VDD})
        for tag in ("yo", "ya"):
            # the t=0 delta sequence (initial value, the A2D's X before its first sample, the
            # first sample) is a race and is not asserted beyond its settled value
            ev = events(r.sout, tag)
            at0 = [v for t, v in ev if t == 0]
            self.assertTrue(at0 and at0[-1] == "0", ev)
            late = [e for e in ev if e[0] > 0]
            self.assertEqual([v for _, v in late], ["1" if k % 2 else "0" for k in range(1, 8)], ev)
            for k, (t, _) in enumerate(late, 1):                     # one event per clock edge
                self.assertTrue(25000 * k <= t <= 25000 * k + 1000, ev)


if __name__ == "__main__":
    unittest.main()
