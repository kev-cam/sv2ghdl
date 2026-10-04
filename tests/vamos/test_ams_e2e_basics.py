"""vcs-ams end-to-end tests: docs/VAMOS_AMS_DESIGN.md §9 items 1, 2, 3, 5, 6, 17 and 18.

Every item compiles a small Verilog+SPICE design with vcs-ams (or vcs -ad...)
and runs ./simv on each available analog engine (VACASK and Xyce; one test
method per engine), then asserts values: rawfile node voltages and threshold
crossing times against closed-form RC / level-1 MOS results, $display'd
digital values and their times against the rawfile, exit codes, IE-report
lines, the boundary file and the end-of-run line.

Needs the whole stack (nvc, iverilog, VACASK and/or Xyce): WSL/Linux.

    cd tests/vamos && python3 -m unittest test_ams_e2e_basics -v
    VAMOS_TEST_KEEP=1 keeps the case directories (printed at the end).

Each (case, engine) is compiled and run once per class; the test methods
assert on the cached results.  Times printed by the testbenches use
`$display("T %0.4f ...", $realtime)`, which keeps the fraction of the time
unit; `%t` of $realtime is checked on its own
(TestE2E18Timescale.check_percent_t_of_realtime).
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import sys
import tempfile
import unittest
from typing import Dict, List, Optional, Tuple

from ams_e2e_lib import AmsCase, engines_available, needs_ams
from vamos_testlib import LAUNCHER

ENGINES = ("vacask", "xyce")
DECK = {"vacask": "vamos.sim", "xyce": "vamos.cir"}
NS = 1e-9
PS = 1e-12

# ---------------------------------------------------------------------------------------
# design constants shared by the expectations
# ---------------------------------------------------------------------------------------

RSER = 500.7          # D2A series resistance (VCS rmap strength 6, §3.4)
TRAMP = 1e-11         # default D2A rise/fall time
VSUP = 1.8            # the decks' only supply

# RC cell (items 1, 5, 17, 18): r1 500 ohm, c1 0.5 pF behind the D2A's 500.7 ohm
RC_R, RC_C = 500.0, 0.5e-12
RC_TAU = (RSER + RC_R) * RC_C


def rc_rise_delay(hiv: float, vth: float) -> float:
    """Edge (start of the D2A ramp) to the RC node crossing vth while charging 0 -> hiv.

    A first-order response to a linear ramp of length TRAMP lags the step
    response by TRAMP/2 once the ramp is over."""
    return TRAMP / 2 + RC_TAU * math.log(hiv / (hiv - vth))


def rc_fall_delay(hiv: float, vth: float) -> float:
    """Edge to the RC node crossing vth while discharging hiv -> 0 (settled before)."""
    return TRAMP / 2 + RC_TAU * math.log(hiv / vth)


def level1_delay(c: float, beta: float, vt: float, vdd: float, lam: float = 0.0,
                 n: int = 4000) -> float:
    """Step-input propagation delay of a level-1 (Shichman-Hodges) inverter.

    The on device (Vgs = vdd) moves the load C from vdd to vdd/2 (or 0 to
    vdd/2, symmetric for the matched devices used here):
    t = C * integral(dV / Ids(V)), saturation while V > vdd - vt, triode below."""
    vov = vdd - vt

    def ids(v: float) -> float:
        if v >= vov:
            return 0.5 * beta * vov * vov * (1 + lam * v)
        return beta * (vov * v - 0.5 * v * v) * (1 + lam * v)

    a, b = vdd / 2, vdd
    h = (b - a) / n
    s = 0.5 * (1 / ids(a) + 1 / ids(b)) + sum(1 / ids(a + i * h) for i in range(1, n))
    return c * s * h


# ---------------------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------------------

RC_SP = """\
* e2e: RC cell; the capacitor node is buffered to the output
.subckt rc_cell in out
r1 in mid 500
c1 mid 0 0.5p
e1 out 0 mid 0 1
.ends
vsup sup 0 1.8
rsup sup 0 1meg
.tran 1p 100n
.end
"""

# item 1: the clock is a reg, the A2D output port drives an SV logic; no port_dir
TB01 = """\
`timescale 1ns/1ps
module tb;
  reg clk = 0;
  logic seen;
  always #5 clk = ~clk;
  rc_cell u1 (.in(clk), .out(seen));
  always @(seen) $display("T %0.4f seen=%b", $realtime, seen);
  initial #58 $finish;
endmodule
"""

# item 2: level-1 CMOS inverter chain, a SPICE-only cell auto-bound (no use_spice)
INV_SP = """\
* e2e 2: level-1 MOS inverter chain
.model nch nmos level=1 vto=0.5 kp=120u gamma=0.4 phi=0.7 lambda=0.02
.model pch pmos level=1 vto=-0.5 kp=40u gamma=0.4 phi=0.7 lambda=0.02
.subckt inv a y
mp y a vdd vdd pch w=6u l=1u
mn y a 0 0 nch w=2u l=1u
cl y 0 100f
.ends
.subckt inv_chain a y
x1 a n1 inv
x2 n1 n2 inv
x3 n2 y inv
.ends
.global vdd
vdd vdd 0 1.8
.tran 1p 60n
.end
"""
INV_BETA, INV_VT, INV_C, INV_LAMBDA = 240e-6, 0.5, 100e-15, 0.02   # kp*W/L, both devices

TB02 = """\
`timescale 1ns/1ps
module tb;
  reg clk = 0;
  wire y;
  always #10 clk = ~clk;
  inv_chain u1 (.a(clk), .y(y));
  always @(y) $display("T %0.4f y=%b", $realtime, y);
  initial #55 $finish;
endmodule
"""

# item 2, the same netlist in upper case, as HSPICE decks often are (names are
# case-insensitive in HSPICE; the Verilog instantiation spells inv_chain/a/y)
INV_SP_UPPER = "\n".join(ln if ln.startswith("*") else ln.upper()
                         for ln in INV_SP.splitlines()) + "\n"

# item 2 variant: u2's input is tied by `wire t; assign t = 1'b1;`.  Both
# actuals are nets, so the auto port stays inout and iverilog folds the tie
# into u2's own VHDL variant: two variants of inv_chain, both must be cut.
TB02_TIED = """\
`timescale 1ns/1ps
module tb;
  reg clk = 0;
  wire a1, y1, y2;
  wire t;
  assign t = 1'b1;
  assign a1 = clk;
  always #10 clk = ~clk;
  inv_chain u1 (.a(a1), .y(y1));
  inv_chain u2 (.a(t), .y(y2));
  always @(y1) $display("T %0.4f y1=%b", $realtime, y1);
  always @(y2) $display("T %0.4f y2=%b", $realtime, y2);
  initial #55 $finish;
endmodule
"""

# item 3: 2-bit DAC, SPICE bus pins code<1> code<0>, no port_dir
DAC_SP = """\
* e2e 3: 2-bit binary-weighted resistor DAC, bus pins in <%d> format
.subckt dac2 code<1> code<0> out
r1 code<1> out 10k
r0 code<0> out 20k
.ends
.tran 1p 60n
.end
"""
DAC_INIT = """\
choose xa dac.sp;
bus_format <%d>;
d2a inst=tb.u1 port=code hiv=1.2 lov=0;
d2a inst=tb.u2 port=code hiv=2.4 lov=0;
"""
TB03 = """\
`timescale 1ns/1ps
module tb;
  reg [1:0] c1 = 2'b00, c2 = 2'b00;
  wire a1, a2;
  dac2 u1 (.code(c1), .out(a1));
  dac2 u2 (.code(c2), .out(a2));
%s  initial begin
    #10 c1 = 2'b01; c2 = 2'b01;
    #10 c1 = 2'b10; c2 = 2'b10;
    #10 c1 = 2'b11; c2 = 2'b11;
    #10 c1 = 2'b00; c2 = 2'b00;
    #10 $finish;
  end
endmodule
"""
TB03_PLAIN = TB03 % ""
TB03_DISPLAY = TB03 % '  always @(c1) $display("T %0.4f c1=%b", $realtime, c1);\n'
DAC_CODES = [(5 * NS, 0, 0), (15 * NS, 0, 1), (25 * NS, 1, 0), (35 * NS, 1, 1), (45 * NS, 0, 0)]


def dac_out(hiv: float, b1: int, b0: int) -> float:
    """The DAC output node: each bit drives through the D2A's 500.7 ohm plus its resistor."""
    g1, g0 = 1 / (10e3 + RSER), 1 / (20e3 + RSER)
    return hiv * (b1 * g1 + b0 * g0) / (g1 + g0)


# item 6: a multi-view cell; the Verilog view is a buffer, the SPICE view (use_spice) an
# inverter whose subckt lists its ports in the other order
TB06 = """\
`timescale 1ns/1ps
module tb;
  reg clk = 0;
  wire y;
  always #10 clk = ~clk;
  buf_cell u1 (.a(clk), .y(y));
  always @(y) $display("T %0.4f y=%b", $realtime, y);
  initial #55 $finish;
endmodule

// Verilog view: a plain buffer.  use_spice selects the SPICE view (an inverter).
module buf_cell (input a, output y);
  assign y = a;
endmodule
"""
CELL06_SP = """\
* e2e 6: SPICE view of buf_cell: a level-1 CMOS inverter (ports in the other order)
.model nch nmos level=1 vto=0.5 kp=120u
.model pch pmos level=1 vto=-0.5 kp=40u
.subckt buf_cell y a
mp y a vdd vdd pch w=6u l=1u
mn y a 0 0 nch w=2u l=1u
cl y 0 100f
.ends
.global vdd
vdd vdd 0 1.8
.tran 1p 60n
.end
"""
# item 6 variants: the Verilog view drives a variable output (the shell must turn
# `output reg` into a net), and use_spice binding the cell to a differently named subckt
TB06_REG = TB06.replace("module buf_cell (input a, output y);\n  assign y = a;",
                        "module buf_cell (input a, output reg y);\n  always @(a) y = a;")
CELL06_SP_RENAMED = CELL06_SP.replace(".subckt buf_cell y a", ".subckt inv_sp y a")

# item 18: no `timescale; a const time clock; -override_timescale=1ns/1ps
TB18 = """\
module tb;
  const time PH = 5ns;
  reg clk = 0;
  logic seen;
  always #PH clk = ~clk;
  rc_cell u1 (.in(clk), .out(seen));
  always @(clk) $display("T %0.4f clk=%b", $realtime, clk);
  always @(seen) $display("T %0.4f seen=%b", $realtime, seen);
  always @(seen) $display("TT %0t seen=%b", $realtime, seen);
  initial #(6*PH) $finish;
endmodule
"""
TB18_1S = """\
`timescale 1s/1s
module tb;
  reg clk = 0;
  logic seen;
  always #1 clk = ~clk;
  rc_cell u1 (.in(clk), .out(seen));
  always @(clk) $display("T %0.4f clk=%b", $realtime, clk);
  always @(seen) $display("T %0.4f seen=%b", $realtime, seen);
  initial #10 $finish;
endmodule
"""


# ---------------------------------------------------------------------------------------
# output parsing
# ---------------------------------------------------------------------------------------

_EVENT = re.compile(r"^T\s+([0-9]+\.[0-9]+)\s+(\w+)=(\S+)\s*$")
_END = re.compile(r"co-simulation finished: (digital stop at ([-+0-9.eE]+) s|analog end at "
                  r"([-+0-9.eE]+) s)")


def events(out: str, tag: str) -> List[Tuple[float, str]]:
    """(time in s, value) of every `T <ns> <tag>=<v>` line, in order."""
    ev = []
    for line in out.splitlines():
        m = _EVENT.match(line.strip())
        if m and m.group(2) == tag:
            ev.append((float(m.group(1)) * NS, m.group(3)))
    return ev


def after_zero(ev: List[Tuple[float, str]]) -> List[Tuple[float, str]]:
    """Events after t=0 (the t=0 delta sequence is a Verilog race; never asserted)."""
    return [e for e in ev if e[0] > 0.0]


def settled_at_zero(ev: List[Tuple[float, str]]) -> Optional[str]:
    zero = [v for t, v in ev if t == 0.0]
    return zero[-1] if zero else None


def ie_entries(text: str) -> Dict[str, dict]:
    """interface_element.rpt -> {node: {'d2a': {key: value}, 'a2d': {...}, 'comments': [...]}}."""
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


def boundary_lines(d: str) -> List[List[str]]:
    with open(os.path.join(d, "simv.daidir", "ams", "vamos.boundary")) as fh:
        return [ln.split() for ln in fh if ln.strip()]


def read_text(*parts: str) -> str:
    with open(os.path.join(*parts), errors="replace") as fh:
        return fh.read()


def job_record(d: str) -> dict:
    return json.loads(read_text(d, "simv.daidir", "vamos.job.json"))


class Result:
    """One compile (and, when it succeeded, one ./simv run) of a case directory."""

    def __init__(self, d: str, comp, sim):
        self.d, self.comp, self.sim = d, comp, sim

    @property
    def cout(self) -> str:
        return self.comp.stdout

    @property
    def sout(self) -> str:
        return self.sim.stdout if self.sim is not None else ""


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
    """Compiles and runs each (case, engine) once per class; tests assert on the results."""

    _root: Optional[str] = None
    _cache: Optional[Dict[Tuple[str, Optional[str]], Result]] = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._root = tempfile.mkdtemp(prefix="vamos-e2eb-%s-" % cls.__name__)
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

    def run_case(self, key: str, files: Dict[str, str], args: List[str], engine: Optional[str],
                 tool: str = "vcs-ams", run: bool = True, simv_args: Tuple[str, ...] = ()
                 ) -> Result:
        ck = (key, engine)
        if ck not in self._cache:
            d = os.path.join(self._root, "%s_%s" % (key, engine or "any"))
            for rel, text in files.items():
                p = os.path.join(d, rel)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w") as fh:
                    fh.write(text)
            comp = self.compile(d, *args, engine=engine, tool=tool, expect_rc=None)
            sim = None
            if run and comp.returncode == 0 and os.path.isfile(os.path.join(d, "simv")):
                sim = self.simv(d, *simv_args, expect_rc=None)
            self._cache[ck] = Result(d, comp, sim)
        return self._cache[ck]

    # -- assertions shared by the items ---------------------------------------------------

    def assertCompiled(self, r: Result, instances: int, nodes: int, bridges: int,
                       engine: str) -> None:
        self.assertEqual(r.comp.returncode, 0, r.cout)
        want = ("vamos: AMS: %d SPICE instance(s), %d analog node(s), %d bridge(s); %s deck "
                "ams/deck/%s" % (instances, nodes, bridges, engine, DECK[engine]))
        self.assertIn(want, r.cout)
        self.assertEqual(job_record(r.d)["ams"]["engine"], engine)

    def assertDigitalStop(self, r: Result, t_stop: float):
        """simv exit 0, exactly one end line, a digital stop at t_stop; the published rawfile
        reaches t_stop and its header point count matches its data."""
        self.assertIsNotNone(r.sim, "no ./simv run:\n" + r.cout)
        self.assertEqual(r.sim.returncode, 0, r.sout)
        ends = _END.findall(r.sout)
        self.assertEqual(len(ends), 1, "want exactly one end-of-run line:\n" + r.sout)
        self.assertTrue(ends[0][0].startswith("digital stop at"), r.sout)
        self.assertAlmostEqual(float(ends[0][1]), t_stop, delta=t_stop * 1e-9)
        self.assertNotIn("** Error", r.sout)
        self.assertNotIn("vamos: error", r.sout)
        # §6: on success the per-run directory is removed (the benches write no files)
        self.assertEqual([n for n in os.listdir(r.d) if n.startswith("vamos_ams.run.")], [])
        raw = self.raw(r.d)
        self.assertAlmostEqual(raw.last_time(), t_stop, delta=t_stop * 1e-9)
        self.assertEqual(raw.declared_points, len(raw.points), "No. Points: of the published rawfile")
        return raw

    def assertIe(self, ents: Dict[str, dict], node: str, kind: str, **want: float) -> dict:
        self.assertIn(node, ents, "no IE-report entry for %s: %s" % (node, sorted(ents)))
        got = ents[node].get(kind)
        self.assertIsNotNone(got, "%s has no %s line: %s" % (node, kind, ents[node]))
        for k, v in want.items():
            self.assertIn(k, got, "%s %s: no %s= in %s" % (kind, node, k, got))
            self.assertAlmostEqual(float(got[k]), v, delta=1e-12 + abs(v) * 1e-9,
                                   msg="%s %s %s" % (kind, node, k))
        return got

    def assertComment(self, ents: Dict[str, dict], node: str, text: str) -> None:
        self.assertTrue(any(c.startswith(text) for c in ents[node]["comments"]),
                        "%s: no comment starting %r in %s" % (node, text, ents[node]["comments"]))

    def assertCrossings(self, raw, node: str, level: float, edges: List[float],
                        delay: float, tol: float, direction: int) -> List[float]:
        """The node crosses `level` once per edge, `delay` after it (+-tol)."""
        xs = raw.crossings(node, level, direction)
        self.assertEqual(len(xs), len(edges), "%s crossings of %g V (dir %d): %s"
                         % (node, level, direction, xs))
        for e, x in zip(edges, xs):
            self.assertAlmostEqual(x - e, delay, delta=tol,
                                   msg="%s: crossing at %.6g s after the edge at %.6g s"
                                   % (node, x, e))
        return xs

    def assertEventsFollow(self, raw, node: str, level: float, ev: List[Tuple[float, str]],
                           first: str) -> None:
        """Digital A2D events (t > 0) pair one to one with the rawfile's crossings of the
        threshold, alternate starting at `first`, and each comes no earlier than its analog
        crossing and no later than the first stored analog point after it."""
        ev = after_zero(ev)
        ups, downs = raw.crossings(node, level, 1), raw.crossings(node, level, -1)
        xs = sorted([(x, "1") for x in ups] + [(x, "0") for x in downs])
        self.assertEqual([v for _, v in ev], [v for _, v in xs],
                         "digital events %s vs analog crossings %s" % (ev, xs))
        self.assertTrue(ev and ev[0][1] == first, ev)
        times = raw.time()
        for (te, _), (tx, _) in zip(ev, xs):
            nxt = min(t for t in times if t >= tx)
            self.assertGreaterEqual(te, tx - 2 * PS, "event at %.6g s before the crossing at %.6g s"
                                    % (te, tx))
            self.assertLessEqual(te, nxt + 2 * PS, "event at %.6g s after the next analog point "
                                 "%.6g s (crossing %.6g s)" % (te, nxt, tx))


# ---------------------------------------------------------------------------------------
# item 1
# ---------------------------------------------------------------------------------------

@needs_ams
@_per_engine
class TestE2E01RcClock(SharedCase):
    """§9 e2e 1: an RC driven by a Verilog `reg` clock through a D2A, its buffered capacitor
    node back to an SV `logic` through an A2D; no port_dir, no use_spice."""

    FILES = {"tb.sv": TB01, "rc.sp": RC_SP, "vcsAD.init": "choose xa rc.sp;\n"}
    EDGES_UP = [5 * NS + k * 10 * NS for k in range(6)]        # 5 15 ... 55
    EDGES_DN = [10 * NS + k * 10 * NS for k in range(5)]       # 10 20 ... 50

    def r(self, engine):
        return self.run_case("rc", self.FILES, ["-sverilog", "tb.sv"], engine)

    def check_compile_and_ie_report(self, engine):
        """compile, IE report (levels from the deck's only supply), boundary"""
        r = self.r(engine)
        self.assertCompiled(r, 1, 2, 3, engine)
        ents = ie_entries(self.report(r.d))
        self.assertEqual(sorted(ents), ["tb.u1.in", "tb.u1.out"])
        self.assertIe(ents, "tb.u1.in", "d2a", hiv=VSUP, lov=0.0, rf_time=TRAMP, x2v=0)
        self.assertNotIn("a2d", ents["tb.u1.in"])
        self.assertComment(ents, "tb.u1.in", "Top-Net tb.clk")
        self.assertComment(ents, "tb.u1.in", "direction: auto→input (variable actual)")
        self.assertComment(ents, "tb.u1.in", "levels: reference highest deck source vsup")
        self.assertIe(ents, "tb.u1.out", "a2d", loth=VSUP / 2, hith=VSUP / 2)
        self.assertNotIn("d2a", ents["tb.u1.out"])
        self.assertComment(ents, "tb.u1.out", "Top-Net tb.seen")
        self.assertComment(ents, "tb.u1.out", "direction: auto→output (variable actual)")
        for node in ("in", "out"):
            self.assertComment(ents, "tb.u1." + node, "shunt rsh_n_u1_%s 1e12 ohm to ground" % node)
        # §6 provenance: the engine and its licence, at compile and at run time
        lic = ([r"VACASK\s.*AGPL-3\.0-only", r"OpenVAF-r\s.*GPL-3\.0-only"] if engine == "vacask"
               else [r"Xyce\s.*GPL-3\.0-or-later"])
        for pat in lic:
            self.assertRegex(r.cout, pat)
            self.assertRegex(r.sout, pat)
        self.assertEqual(boundary_lines(r.d), [
            ["D2A", ".u1.vb0_0_d", "tb.u1.in__d", "rise=1e-11", "fall=1e-11"],
            ["D2A", ".u1.vb0_0_e", "tb.u1.in__e", "rise=1e-11", "fall=1e-11"],
            ["A2D", ".u1.vb1_0_a", "tb.u1.out__a"]])

    def check_rawfile_levels_and_crossings(self, engine):
        """D2A levels, RC settling and threshold crossings against the closed form"""
        raw = self.assertDigitalStop(self.r(engine), 58 * NS)
        for t, v in ((4.9 * NS, 0.0), (9.9 * NS, VSUP), (14.9 * NS, 0.0), (54.9 * NS, 0.0),
                     (57.9 * NS, VSUP)):
            self.assertAlmostEqual(raw.at("n_u1_in_d", t), v, delta=1e-6, msg="D2A source at %g" % t)
        # 4.9 ns after an edge the RC is 9.8 tau in: within 0.01 % of the level
        for t, v in ((9.9 * NS, VSUP), (14.9 * NS, 0.0), (49.9 * NS, VSUP)):
            self.assertAlmostEqual(raw.at("n_u1_in", t), v, delta=2e-3, msg="D2A node at %g" % t)
            self.assertAlmostEqual(raw.at("n_u1_out", t), v, delta=2e-3, msg="RC output at %g" % t)
        self.assertCrossings(raw, "n_u1_out", VSUP / 2, self.EDGES_UP,
                             rc_rise_delay(VSUP, VSUP / 2), 10 * PS, +1)
        self.assertCrossings(raw, "n_u1_out", VSUP / 2, self.EDGES_DN,
                             rc_fall_delay(VSUP, VSUP / 2), 10 * PS, -1)

    def check_a2d_into_logic(self, engine):
        """the SV logic follows the A2D: one event per analog crossing, in step with it"""
        r = self.r(engine)
        raw = self.assertDigitalStop(r, 58 * NS)
        ev = events(r.sout, "seen")
        self.assertEqual(settled_at_zero(ev), "0", ev)
        self.assertEqual(len(after_zero(ev)), 11, ev)
        self.assertEventsFollow(raw, "n_u1_out", VSUP / 2, ev, first="1")

    def check_report_paste_back(self, engine):
        """the report's d2a/a2d lines pasted into vcsAD.init select the same nodes (no TNF)"""
        rep = self.report(self.r(engine).d)
        lines = [ln for ln in rep.splitlines() if re.match(r"^(d2a|a2d) ", ln)]
        self.assertEqual(len(lines), 2, rep)
        files = dict(self.FILES, **{"vcsAD.init": "choose xa rc.sp;\n" + "\n".join(lines) + "\n"})
        r2 = self.run_case("rc_paste", files, ["-sverilog", "tb.sv"], engine, run=False)
        self.assertCompiled(r2, 1, 2, 3, engine)
        self.assertNotIn("MSV-IE-OPT-TNF", r2.cout)
        e1, e2 = ie_entries(rep), ie_entries(self.report(r2.d))
        for node in ("tb.u1.in", "tb.u1.out"):
            for kind in ("d2a", "a2d"):
                self.assertEqual(e1[node].get(kind), e2[node].get(kind), (node, kind))
            self.assertTrue(any(c.startswith("levels: rule") for c in e2[node]["comments"]),
                            e2[node]["comments"])


# ---------------------------------------------------------------------------------------
# item 2
# ---------------------------------------------------------------------------------------

@needs_ams
@_per_engine
class TestE2E02InverterChain(SharedCase):
    """§9 e2e 2: a level-1 MOS inverter chain, a SPICE-only cell auto-bound with no
    use_spice; cross-engine delays within ~3 %; a variant with an input tied by
    `wire t; assign t = 1'b1;` (two VHDL variants, both cut)."""

    FILES = {"tb.sv": TB02, "inv.sp": INV_SP, "vcsAD.init": "choose xa inv.sp;\n"}
    TIED = {"tb.sv": TB02_TIED, "inv.sp": INV_SP, "vcsAD.init": "choose xa inv.sp;\n"}
    EDGES = [10 * NS * k for k in range(1, 6)]                 # a toggles at 10 20 30 40 50

    def r(self, engine):
        return self.run_case("chain", self.FILES, ["-sverilog", "tb.sv"], engine)

    def rt(self, engine):
        return self.run_case("tied", self.TIED, ["-sverilog", "tb.sv"], engine)

    def chain_delays(self, raw, a: str, y: str) -> List[float]:
        xa, xy = raw.crossings(a, VSUP / 2), raw.crossings(y, VSUP / 2)
        self.assertEqual(len(xa), len(self.EDGES), xa)
        self.assertEqual(len(xy), len(self.EDGES), xy)
        return [ty - ta for ta, ty in zip(xa, xy)]

    def check_chain_compile_and_ie_report(self, engine):
        """auto-bound cell: compile, levels traced to vdd, IE report"""
        r = self.r(engine)
        self.assertCompiled(r, 1, 2, 3, engine)
        ents = ie_entries(self.report(r.d))
        self.assertEqual(sorted(ents), ["tb.u1.a", "tb.u1.y"])
        self.assertIe(ents, "tb.u1.a", "d2a", hiv=VSUP, lov=0.0)
        self.assertComment(ents, "tb.u1.a", "direction: auto→input (variable actual)")
        self.assertComment(ents, "tb.u1.a", "levels: reference trace vdd")
        self.assertIe(ents, "tb.u1.y", "a2d", loth=VSUP / 2, hith=VSUP / 2)
        self.assertComment(ents, "tb.u1.y", "direction: auto→inout")
        self.assertComment(ents, "tb.u1.y", "levels: reference trace vdd")

    def check_chain_levels_and_delays(self, engine):
        """vin = 1.8 V gives ~0 V; first-stage and chain delays"""
        raw = self.assertDigitalStop(self.r(engine), 55 * NS)
        for t in (19.9 * NS, 39.9 * NS):                      # a high
            self.assertAlmostEqual(raw.at("n_u1_a", t), VSUP, delta=1e-3)
            self.assertLess(abs(raw.at("xv_u1:n1", t)), 5e-3, "inverter at vin=1.8 V")
            self.assertAlmostEqual(raw.at("xv_u1:n2", t), VSUP, delta=5e-3)
            self.assertLess(abs(raw.at("n_u1_y", t)), 5e-3)
        for t in (9.9 * NS, 29.9 * NS):                       # a low
            self.assertLess(abs(raw.at("xv_u1:n1", t) - VSUP), 5e-3)
            self.assertAlmostEqual(raw.at("n_u1_y", t), VSUP, delta=5e-3)
        stage = level1_delay(INV_C, INV_BETA, INV_VT, VSUP, INV_LAMBDA)
        xa = raw.crossings("n_u1_a", VSUP / 2)
        xn1 = raw.crossings("xv_u1:n1", VSUP / 2)
        self.assertEqual(len(xn1), len(xa))
        for ta, tn in zip(xa, xn1):
            self.assertAlmostEqual(tn - ta, stage, delta=0.03 * stage,
                                   msg="first stage: %.4g s vs level-1 %.4g s" % (tn - ta, stage))
        for d in self.chain_delays(raw, "n_u1_a", "n_u1_y"):
            self.assertTrue(3 * stage < d < 6 * stage, "chain delay %.4g s" % d)

    def check_chain_digital(self, engine):
        """y = ~clk after the chain delay, in step with the analog crossings"""
        r = self.r(engine)
        raw = self.assertDigitalStop(r, 55 * NS)
        ev = events(r.sout, "y")
        self.assertEqual(settled_at_zero(ev), "1", ev)
        self.assertEqual([v for _, v in after_zero(ev)], ["0", "1", "0", "1", "0"])
        self.assertEventsFollow(raw, "n_u1_y", VSUP / 2, ev, first="0")

    def test_chain_cross_engine_delays(self):
        """chain delays agree between VACASK and Xyce within 3 %"""
        if len(engines_available()) < 2:
            self.skipTest("needs both VACASK and Xyce")
        delays = {}
        for e in ENGINES:
            raw = self.assertDigitalStop(self.r(e), 55 * NS)
            delays[e] = self.chain_delays(raw, "n_u1_a", "n_u1_y")
        for dv, dx in zip(delays["vacask"], delays["xyce"]):
            self.assertAlmostEqual(dv, dx, delta=0.03 * (dv + dx) / 2,
                                   msg="VACASK %.5g s vs Xyce %.5g s" % (dv, dx))

    def check_uppercase_netlist(self, engine):
        """the same chain written in upper case binds and simulates identically"""
        up = self.run_case("chain_upper", dict(self.FILES, **{"inv.sp": INV_SP_UPPER}),
                           ["-sverilog", "tb.sv"], engine)
        self.assertCompiled(up, 1, 2, 3, engine)
        ents = {k.lower(): v for k, v in ie_entries(self.report(up.d)).items()}
        self.assertEqual(sorted(ents), ["tb.u1.a", "tb.u1.y"])
        self.assertIe(ents, "tb.u1.a", "d2a", hiv=VSUP, lov=0.0)
        self.assertIe(ents, "tb.u1.y", "a2d", loth=VSUP / 2, hith=VSUP / 2)
        raw_up = self.assertDigitalStop(up, 55 * NS)
        raw_lo = self.assertDigitalStop(self.r(engine), 55 * NS)
        for d_up, d_lo in zip(self.chain_delays(raw_up, "n_u1_a", "n_u1_y"),
                              self.chain_delays(raw_lo, "n_u1_a", "n_u1_y")):
            self.assertAlmostEqual(d_up, d_lo, delta=0.1 * PS)
        self.assertEqual(events(up.sout, "y"), events(self.r(engine).sout, "y"))

    def check_tied_two_variants_both_cut(self, engine):
        """the tied input splits inv_chain into two VHDL variants; both are cut"""
        r = self.rt(engine)
        self.assertCompiled(r, 2, 4, 6, engine)
        self.assertRegex(r.cout, r"kept as a digital driver/reader")
        design = read_text(r.d, "simv.daidir", "nvc", "design.vhd")
        v1 = re.search(r"\bu1: entity work\.(\w+)", design).group(1)
        v2 = re.search(r"\bu2: entity work\.(\w+)", design).group(1)
        self.assertNotEqual(v1, v2, "u1 and u2 should bind two variants of inv_chain")
        for v in (v1, v2):
            self.assertRegex(design, r"-- Generated from Verilog module inv_chain \([^)]*\)\n"
                             r"entity %s is" % re.escape(v))
        cutv = read_text(r.d, "simv.daidir", "ams", "cut.vhd")
        for lab, v in (("u1", v1), ("u2", v2)):
            self.assertRegex(cutv, r"(?m)^entity %s__vams is" % re.escape(v))
            self.assertRegex(cutv, r"\b%s: entity work\.%s__vams\b" % (lab, re.escape(v)))
        ents = ie_entries(self.report(r.d))
        self.assertEqual(sorted(ents), ["tb.u1.a", "tb.u1.y", "tb.u2.a", "tb.u2.y"])
        self.assertIe(ents, "tb.u2.a", "d2a", hiv=VSUP, lov=0.0)
        self.assertComment(ents, "tb.u2.a", "Top-Net tb.t")
        self.assertIe(ents, "tb.u2.y", "a2d", loth=VSUP / 2, hith=VSUP / 2)

    def check_tied_values(self, engine):
        """the tied 1 drives 1.8 V; the CMOS inverter at vin = 1.8 V gives ~0 V"""
        r = self.rt(engine)
        raw = self.assertDigitalStop(r, 55 * NS)
        for t in (1 * NS, 25 * NS, 54 * NS):
            self.assertAlmostEqual(raw.at("n_u2_a", t), VSUP, delta=1e-3)
            self.assertLess(abs(raw.at("xv_u2:n1", t)), 5e-3, "inverter at vin=1.8 V")
            self.assertAlmostEqual(raw.at("xv_u2:n2", t), VSUP, delta=5e-3)
            self.assertLess(abs(raw.at("n_u2_y", t)), 5e-3)
        y2 = events(r.sout, "y2")
        self.assertEqual(settled_at_zero(y2), "0", y2)
        self.assertEqual(after_zero(y2), [])
        y1 = events(r.sout, "y1")
        self.assertEqual([v for _, v in after_zero(y1)], ["0", "1", "0", "1", "0"])
        self.assertEventsFollow(raw, "n_u1_y", VSUP / 2, y1, first="0")
        for d in self.chain_delays(raw, "n_u1_a", "n_u1_y"):
            stage = level1_delay(INV_C, INV_BETA, INV_VT, VSUP, INV_LAMBDA)
            self.assertTrue(3 * stage < d < 6 * stage, "chain delay %.4g s" % d)


# ---------------------------------------------------------------------------------------
# item 3
# ---------------------------------------------------------------------------------------

@needs_ams
@_per_engine
class TestE2E03Dac(SharedCase):
    """§9 e2e 3: a 2-bit DAC with a `bus_format <%d>` bus and no port_dir, per-instance
    `d2a inst=... hiv=` rules; a variant where the code reg is also $displayed (exactly one
    D2A per bit)."""

    def r(self, engine, display=False):
        tb = TB03_DISPLAY if display else TB03_PLAIN
        return self.run_case("dac_display" if display else "dac",
                             {"tb.sv": tb, "dac.sp": DAC_SP, "vcsAD.init": DAC_INIT},
                             ["-sverilog", "tb.sv"], engine)

    def assertOneD2aPerBit(self, r: Result, engine: str) -> None:
        self.assertCompiled(r, 2, 6, 8, engine)
        ents = ie_entries(self.report(r.d))
        d2a = sorted(n for n, e in ents.items() if "d2a" in e)
        a2d = sorted(n for n, e in ents.items() if "a2d" in e)
        self.assertEqual(d2a, ["tb.u1.code<0>", "tb.u1.code<1>", "tb.u2.code<0>", "tb.u2.code<1>"])
        self.assertEqual(a2d, [])
        for inst, hiv, line in (("u1", 1.2, 3), ("u2", 2.4, 4)):
            for b in (0, 1):
                node = "tb.%s.code<%d>" % (inst, b)
                self.assertIe(ents, node, "d2a", hiv=hiv, lov=0.0)
                self.assertComment(ents, node, "Top-Net tb.c%s" % inst[1])
                self.assertComment(ents, node, "direction: auto→input (variable actual)")
                self.assertComment(ents, node, "levels: rule d2a.hiv vcsAD.init:%d" % line)
            self.assertComment(ents, "tb.%s.out" % inst, "through-net")
        bl = boundary_lines(r.d)
        self.assertEqual(sorted(b[2] for b in bl if b[0] == "D2A"),
                         sorted("tb.%s.code<%d>__%s" % (i, b, k) for i in ("u1", "u2")
                                for b in (0, 1) for k in ("d", "e")))
        self.assertEqual([b for b in bl if b[0] != "D2A"], [])

    def assertDacLevels(self, r: Result) -> None:
        raw = self.assertDigitalStop(r, 50 * NS)
        for inst, hiv in (("u1", 1.2), ("u2", 2.4)):
            for t, b1, b0 in DAC_CODES:
                self.assertAlmostEqual(raw.at("n_%s_code_1__d" % inst, t), hiv * b1, delta=1e-6)
                self.assertAlmostEqual(raw.at("n_%s_code_0__d" % inst, t), hiv * b0, delta=1e-6)
                self.assertAlmostEqual(raw.at("n_%s_out" % inst, t), dac_out(hiv, b1, b0),
                                       delta=1e-4, msg="%s code %d%d at %g" % (inst, b1, b0, t))

    def check_rules_compile_and_ie_report(self, engine):
        """bus pins grouped by bus_format, per-instance hiv, one D2A per bit"""
        self.assertOneD2aPerBit(self.r(engine), engine)

    def check_rules_dac_levels(self, engine):
        """the DAC outputs follow the code at each instance's own hiv"""
        self.assertDacLevels(self.r(engine))

    def check_display_one_d2a_per_bit(self, engine):
        """the $displayed code reg still gives exactly one D2A per bit"""
        self.assertOneD2aPerBit(self.r(engine, display=True), engine)

    def check_display_values(self, engine):
        """$display shows the code sequence; the DAC levels are unchanged.  `reg [1:0] c1 =
        2'b00' is a variable initializer, no event under -sverilog (IEEE 1800 6.8), so the
        `always @(c1)' first prints at 10 ns, as vvp -g2012 does (the t=0 line came from the
        translated always block's old time-0 run, removed in round 6, R6T-02)"""
        r = self.r(engine, display=True)
        self.assertDacLevels(r)
        ev = events(r.sout, "c1")
        self.assertEqual([v for _, v in ev], ["01", "10", "11", "00"], ev)
        for (t, _), want in zip(ev, (10 * NS, 20 * NS, 30 * NS, 40 * NS)):
            self.assertAlmostEqual(t, want, delta=0.1 * PS)

    def check_report_paste_back(self, engine):
        """the report's per-bit d2a lines (<%d> names) pasted in place of the inst= rules
        select the same four nodes with the same levels and no TNF"""
        rep = self.report(self.r(engine).d)
        lines = [ln for ln in rep.splitlines() if re.match(r"^(d2a|a2d) ", ln)]
        self.assertEqual(len(lines), 4, rep)
        init = "choose xa dac.sp;\nbus_format <%d>;\n" + "\n".join(lines) + "\n"
        r2 = self.run_case("dac_paste", {"tb.sv": TB03_PLAIN, "dac.sp": DAC_SP, "vcsAD.init": init},
                           ["-sverilog", "tb.sv"], engine, run=False)
        self.assertCompiled(r2, 2, 6, 8, engine)
        self.assertNotIn("MSV-IE-OPT-TNF", r2.cout)
        e1, e2 = ie_entries(rep), ie_entries(self.report(r2.d))
        for node in ("tb.u1.code<0>", "tb.u1.code<1>", "tb.u2.code<0>", "tb.u2.code<1>"):
            self.assertEqual(e1[node]["d2a"], e2[node]["d2a"], node)

    def test_xyce_output_has_no_engine_chatter(self):
        """simv output carries no Xyce netlist warnings about vamos's own bridge nodes

        Xyce wraps the warning "Netlist warning: Voltage Node (N_U1_CODE_0__E) connected
        to only 1 device Terminal" onto two lines when the node name is long; neither
        half may reach the user."""
        self.need("xyce")
        r = self.r("xyce")
        leaked = [ln for ln in r.sout.splitlines()
                  if re.search(r"Voltage Node \(N_\S*_E\)|^\s*Terminal\s*$", ln)]
        self.assertEqual(leaked, [], "Xyce chatter about vamos's enable nodes reached the user")


# ---------------------------------------------------------------------------------------
# item 5
# ---------------------------------------------------------------------------------------

@needs_ams
@_per_engine
class TestE2E05NoTran(SharedCase):
    """§9 e2e 5: a netlist without .tran, ending at $finish."""

    # Besides the RC cell, an analog-only PULSE with td only: with no .tran its
    # omitted fields come from the synthesized analysis (§4.3.8): tr = tf = TSTEP =
    # 1e-11 s, pw = TSTOP = 3600 s, aperiodic.
    FILES = {"tb.sv": TB01,
             "rc.sp": RC_SP.replace(".tran 1p 100n\n", "vp p 0 pulse(0 1.8 2n)\nrp p 0 1k\n"),
             "vcsAD.init": "choose xa rc.sp;\n"}
    NOTE = "no .tran: the run ends at $finish/$stop or at 3600 s"

    def r(self, engine):
        return self.run_case("notran", self.FILES, ["-sverilog", "tb.sv"], engine)

    def check_synthesized_analysis(self, engine):
        """the compile notes the synthesized tran; the record and the deck carry it"""
        r = self.r(engine)
        self.assertCompiled(r, 1, 2, 3, engine)
        self.assertIn("vamos: note: " + self.NOTE, r.cout)
        ents = ie_entries(self.report(r.d))
        self.assertEqual(sorted(ents), ["tb.u1.in", "tb.u1.out"])
        self.assertIe(ents, "tb.u1.in", "d2a", hiv=VSUP, lov=0.0)
        self.assertIe(ents, "tb.u1.out", "a2d", loth=VSUP / 2, hith=VSUP / 2)
        rec = job_record(r.d)["ams"]
        self.assertEqual(rec["stop"], 3600.0)
        self.assertIs(rec["stop_synthesized"], True)
        deck = read_text(r.d, "simv.daidir", "ams", "deck", DECK[engine])
        if engine == "vacask":
            self.assertRegex(deck, r"(?m)^\s*analysis vamos_tran tran step=1e-11 stop=3600(\.0)? "
                             r"maxstep=1e-08\s*$")
        else:
            self.assertRegex(deck, r"(?m)^\.tran 1e-11 3600(\.0)? 0(\.0)? 1e-08\s*$")

    def check_run_ends_at_finish(self, engine):
        """simv repeats the note, ends at $finish with the RC values of item 1"""
        r = self.r(engine)
        raw = self.assertDigitalStop(r, 58 * NS)
        self.assertIn(self.NOTE, r.sout)
        self.assertAlmostEqual(raw.at("n_u1_out", 49.9 * NS), VSUP, delta=2e-3)
        self.assertAlmostEqual(raw.at("n_u1_out", 54.9 * NS), 0.0, delta=2e-3)
        self.assertCrossings(raw, "n_u1_out", VSUP / 2, TestE2E01RcClock.EDGES_UP,
                             rc_rise_delay(VSUP, VSUP / 2), 10 * PS, +1)
        ev = events(r.sout, "seen")
        self.assertEqual(len(after_zero(ev)), 11, ev)
        self.assertEventsFollow(raw, "n_u1_out", VSUP / 2, ev, first="1")

    def check_source_defaults_from_synthesized_tran(self, engine):
        """PULSE(0 1.8 2n): 10 ps edge (TSTEP), held high to the end (pw = TSTOP)"""
        raw = self.assertDigitalStop(self.r(engine), 58 * NS)
        self.assertAlmostEqual(raw.at("p", 1.9 * NS), 0.0, delta=1e-6)
        xs = raw.crossings("p", VSUP / 2)
        self.assertEqual(len(xs), 1, xs)
        self.assertAlmostEqual(xs[0], 2 * NS + TRAMP / 2, delta=1 * PS)
        for t in (2.02 * NS, 30 * NS, 57.9 * NS):
            self.assertAlmostEqual(raw.at("p", t), VSUP, delta=1e-6)


# ---------------------------------------------------------------------------------------
# item 6
# ---------------------------------------------------------------------------------------

@needs_ams
@_per_engine
class TestE2E06UseSpice(SharedCase):
    """§9 e2e 6: a multi-view cell where use_spice overrides the Verilog view."""

    FILES = {"tb.sv": TB06, "cell.sp": CELL06_SP,
             "vcsAD.init": "choose xa cell.sp;\nuse_spice -cell buf_cell;\n"}

    def r(self, engine):
        return self.run_case("usespice", self.FILES, ["-sverilog", "tb.sv"], engine)

    def check_verilog_view_replaced(self, engine):
        """the Verilog body is masked, a shell takes its place, the subckt binds by name"""
        r = self.r(engine)
        self.assertCompiled(r, 1, 2, 3, engine)
        pp = read_text(r.d, "simv.daidir", "ams", "pp.v")
        self.assertNotIn("assign y = a", pp)
        self.assertEqual(len(re.findall(r"\bmodule buf_cell\b", pp)), 1, pp)
        self.assertRegex(pp, r"module buf_cell \(a, y\);\s+input a;\s+output y;\s+"
                         r"bufif1 vamos_ams_hiz_0 \(y, 1'b0, 1'b0\);\s+endmodule")
        deck = read_text(r.d, "simv.daidir", "ams", "deck", DECK[engine])
        if engine == "vacask":
            self.assertRegex(deck, r"(?m)^xv_u1 \(n_u1_y n_u1_a\) buf_cell\s*$")
        else:
            self.assertRegex(deck, r"(?m)^xv_u1 n_u1_y n_u1_a buf_cell\s*$")
        ents = ie_entries(self.report(r.d))
        self.assertEqual(sorted(ents), ["tb.u1.a", "tb.u1.y"])
        self.assertIe(ents, "tb.u1.a", "d2a", hiv=VSUP, lov=0.0)
        self.assertIe(ents, "tb.u1.y", "a2d", loth=VSUP / 2, hith=VSUP / 2)
        for node in ents:      # declared ports: no auto-direction comment
            self.assertFalse(any(c.startswith("direction:") for c in ents[node]["comments"]))

    def check_spice_view_simulated(self, engine):
        """y is the SPICE inverter's output, not the Verilog buffer's"""
        r = self.r(engine)
        raw = self.assertDigitalStop(r, 55 * NS)
        for t in (9.9 * NS, 29.9 * NS):
            self.assertAlmostEqual(raw.at("n_u1_y", t), VSUP, delta=2e-3)
        for t in (19.9 * NS, 39.9 * NS):
            self.assertLess(abs(raw.at("n_u1_y", t)), 2e-3)
        ev = events(r.sout, "y")
        self.assertEqual(settled_at_zero(ev), "1", ev)
        self.assertEqual([v for _, v in after_zero(ev)], ["0", "1", "0", "1", "0"])
        self.assertEventsFollow(raw, "n_u1_y", VSUP / 2, ev, first="0")
        stage = level1_delay(INV_C, INV_BETA, INV_VT, VSUP)
        for t, _ in after_zero(ev):              # an analog delay, not the buffer's zero delay
            self.assertGreater(t - round(t / (10 * NS)) * 10 * NS, 0.9 * stage)

    def check_output_reg_view(self, engine):
        """a Verilog view with `output reg y`: the shell makes y a net; same SPICE result"""
        files = dict(self.FILES, **{"tb.sv": TB06_REG})
        r = self.run_case("usespice_reg", files, ["-sverilog", "tb.sv"], engine)
        self.assertCompiled(r, 1, 2, 3, engine)
        pp = read_text(r.d, "simv.daidir", "ams", "pp.v")
        self.assertNotIn("always @(a) y = a", pp)
        self.assertRegex(pp, r"module buf_cell \(a, y\);\s+input a;\s+output y;\s+bufif1 ")
        raw = self.assertDigitalStop(r, 55 * NS)
        self.assertAlmostEqual(raw.at("n_u1_y", 29.9 * NS), VSUP, delta=2e-3)
        self.assertLess(abs(raw.at("n_u1_y", 39.9 * NS)), 2e-3)
        ev = events(r.sout, "y")
        self.assertEqual([v for _, v in after_zero(ev)], ["0", "1", "0", "1", "0"])
        self.assertEventsFollow(raw, "n_u1_y", VSUP / 2, ev, first="0")

    def check_use_spice_cell_to_subckt(self, engine):
        """use_spice -cell buf_cell:inv_sp binds the Verilog cell to subckt inv_sp"""
        files = dict(self.FILES, **{"cell.sp": CELL06_SP_RENAMED, "vcsAD.init":
                                    "choose xa cell.sp;\nuse_spice -cell buf_cell:inv_sp;\n"})
        r = self.run_case("usespice_bind", files, ["-sverilog", "tb.sv"], engine)
        self.assertCompiled(r, 1, 2, 3, engine)
        deck = read_text(r.d, "simv.daidir", "ams", "deck", DECK[engine])
        if engine == "vacask":
            self.assertRegex(deck, r"(?m)^xv_u1 \(n_u1_y n_u1_a\) inv_sp\s*$")
        else:
            self.assertRegex(deck, r"(?m)^xv_u1 n_u1_y n_u1_a inv_sp\s*$")
        raw = self.assertDigitalStop(r, 55 * NS)
        self.assertLess(abs(raw.at("n_u1_y", 19.9 * NS)), 2e-3)
        ev = events(r.sout, "y")
        self.assertEqual([v for _, v in after_zero(ev)], ["0", "1", "0", "1", "0"])
        self.assertEventsFollow(raw, "n_u1_y", VSUP / 2, ev, first="0")

    def check_spice_view_delay(self, engine):
        """the inverter delay equals the level-1 closed form within 3 %

        With no .option DELMAX, HSPICE bounds the internal step by
        min(TSTOP/50, TSTEP*RMAX), RMAX=5 by default (Star-HSPICE 2001.2 manual
        11-36), and the emitted decks carry that maximum step (5 ps here).  Without
        it VACASK steps 0.34 ns straight through the 0.45 ns transition and the
        crossing lands ~20 ps late (0.471 ns against 0.450 ns)."""
        raw = self.assertDigitalStop(self.r(engine), 55 * NS)
        stage = level1_delay(INV_C, INV_BETA, INV_VT, VSUP)
        xa, xy = raw.crossings("n_u1_a", VSUP / 2), raw.crossings("n_u1_y", VSUP / 2)
        self.assertEqual(len(xa), 5, xa)
        self.assertEqual(len(xy), 5, xy)
        for ta, ty in zip(xa, xy):
            self.assertAlmostEqual(ty - ta, stage, delta=0.03 * stage,
                                   msg="inverter delay %.4g s vs level-1 %.4g s" % (ty - ta, stage))


# ---------------------------------------------------------------------------------------
# item 17
# ---------------------------------------------------------------------------------------

@needs_ams
@_per_engine
class TestE2E17AdForms(SharedCase):
    """§9 e2e 17: -ad, -ad=f, +ad, +ad=f and the vcs-ams symlink (plus vamos -vcs-ams).

    vcsAD.init and alt.init differ only in the D2A level of tb.u1.in, so the IE
    report and the rawfile show which control file the compile read."""

    FILES = {"tb.sv": TB01, "rc.sp": RC_SP,
             "vcsAD.init": "choose xa rc.sp;\nd2a node=tb.u1.in hiv=1.5;\n",
             "alt.init": "choose xa rc.sp;\nd2a node=tb.u1.in hiv=1.2;\n"}
    # form -> (tool, args, control file read, hiv, recorded personality)
    FORMS = {
        "dash_ad": ("vcs", ["-ad"], "", 1.5, "vcs"),
        "dash_ad_file": ("vcs", ["-ad=alt.init"], "alt.init", 1.2, "vcs"),
        "plus_ad": ("vcs", ["+ad"], "", 1.5, "vcs"),
        "plus_ad_file": ("vcs", ["+ad=alt.init"], "alt.init", 1.2, "vcs"),
        "vcs_ams_symlink": ("vcs-ams", [], "", 1.5, "vcs-ams"),
        "vamos_vcs_ams": (LAUNCHER, ["-vcs-ams"], "", 1.5, "vcs-ams"),
    }

    def form(self, name, engine):
        tool, args, ctl, hiv, pers = self.FORMS[name]
        r = self.run_case(name, self.FILES, args + ["-sverilog", "tb.sv"], engine, tool=tool)
        self.assertCompiled(r, 1, 2, 3, engine)
        job = job_record(r.d)
        self.assertEqual(job["ams_control"], os.path.join(r.d, ctl) if ctl else "")
        self.assertEqual(job["personality"], pers)
        self.assertTrue(os.path.isfile(os.path.join(r.d, "simv.msv", "interface_element.rpt")))
        ents = ie_entries(self.report(r.d))
        self.assertIe(ents, "tb.u1.in", "d2a", hiv=hiv, lov=0.0)
        self.assertComment(ents, "tb.u1.in", "levels: rule d2a.hiv %s:2" % (ctl or "vcsAD.init"))
        self.assertIe(ents, "tb.u1.out", "a2d", loth=VSUP / 2, hith=VSUP / 2)
        raw = self.assertDigitalStop(r, 58 * NS)
        self.assertAlmostEqual(raw.at("n_u1_in_d", 9.9 * NS), hiv, delta=1e-6)
        self.assertAlmostEqual(raw.at("n_u1_out", 9.9 * NS), hiv, delta=2e-3)
        self.assertCrossings(raw, "n_u1_out", VSUP / 2, TestE2E01RcClock.EDGES_UP,
                             rc_rise_delay(hiv, VSUP / 2), 10 * PS, +1)
        self.assertCrossings(raw, "n_u1_out", VSUP / 2, TestE2E01RcClock.EDGES_DN,
                             rc_fall_delay(hiv, VSUP / 2), 10 * PS, -1)
        ev = events(r.sout, "seen")
        self.assertEventsFollow(raw, "n_u1_out", VSUP / 2, ev, first="1")

    def check_dash_ad(self, engine):
        """vcs -ad reads ./vcsAD.init"""
        self.form("dash_ad", engine)

    def check_dash_ad_file(self, engine):
        """vcs -ad=alt.init reads alt.init (cwd-relative), not ./vcsAD.init"""
        self.form("dash_ad_file", engine)

    def check_plus_ad(self, engine):
        """vcs +ad reads ./vcsAD.init"""
        self.form("plus_ad", engine)

    def check_plus_ad_file(self, engine):
        """vcs +ad=alt.init reads alt.init"""
        self.form("plus_ad_file", engine)

    def check_vcs_ams_symlink(self, engine):
        """the vcs-ams symlink implies -ad"""
        self.form("vcs_ams_symlink", engine)

    def check_vamos_vcs_ams(self, engine):
        """vamos -vcs-ams implies -ad"""
        self.form("vamos_vcs_ams", engine)

    def check_dash_ad_file_in_subdir(self, engine):
        """vcs -ad=ctl/sub.init: its choose netlist resolves beside the control file

        §4.3.1: a relative choose netlist is tried against the cwd, then the directory
        of the file containing the reference (here ctl/sub.init)."""
        files = {"tb.sv": TB01, "ctl/sub.sp": RC_SP,
                 "ctl/sub.init": "choose xa sub.sp;\nd2a node=tb.u1.in hiv=1.0;\n"}
        r = self.run_case("dash_ad_subdir", files, ["-ad=ctl/sub.init", "-sverilog", "tb.sv"],
                          engine, tool="vcs")
        self.assertCompiled(r, 1, 2, 3, engine)
        self.assertEqual(job_record(r.d)["ams_control"], os.path.join(r.d, "ctl", "sub.init"))
        ents = ie_entries(self.report(r.d))
        self.assertIe(ents, "tb.u1.in", "d2a", hiv=1.0, lov=0.0)
        raw = self.assertDigitalStop(r, 58 * NS)
        self.assertAlmostEqual(raw.at("n_u1_in_d", 9.9 * NS), 1.0, delta=1e-6)
        self.assertCrossings(raw, "n_u1_out", VSUP / 2, TestE2E01RcClock.EDGES_UP,
                             rc_rise_delay(1.0, VSUP / 2), 10 * PS, +1)

    def check_ini_read_first(self, engine):
        """snps_vcsAD.ini (cwd) is read before the -ad file; rules merge key by key"""
        files = {"tb.sv": TB01, "rc.sp": RC_SP,
                 "snps_vcsAD.ini": "d2a node=tb.u1.in hiv=1.0 rf_time=1n;\n",
                 "vcsAD.init": "choose xa rc.sp;\nd2a node=tb.u1.in hiv=1.5;\n"}
        r = self.run_case("ini", files, ["-ad", "-sverilog", "tb.sv"], engine, tool="vcs")
        self.assertCompiled(r, 1, 2, 3, engine)
        ents = ie_entries(self.report(r.d))
        self.assertIe(ents, "tb.u1.in", "d2a", hiv=1.5, lov=0.0, rf_time=1e-9)
        self.assertIn(["D2A", ".u1.vb0_0_d", "tb.u1.in__d", "rise=1e-09", "fall=1e-09"],
                      boundary_lines(r.d))
        raw = self.assertDigitalStop(r, 58 * NS)
        self.assertAlmostEqual(raw.at("n_u1_in_d", 9.9 * NS), 1.5, delta=1e-6)
        xs = raw.crossings("n_u1_in_d", 0.75, +1)          # a 1 ns ramp: 50 % at edge + 0.5 ns
        self.assertEqual(len(xs), 6, xs)
        for x, e in zip(xs, TestE2E01RcClock.EDGES_UP):
            self.assertAlmostEqual(x - e, 0.5 * NS, delta=2 * PS)

    def test_plain_vcs_is_digital(self):
        """without -ad/+ad, vcs does not enter the AMS flow (the SPICE cell stays unknown)"""
        r = self.run_case("plain", self.FILES, ["-sverilog", "tb.sv"], None, tool="vcs", run=False)
        self.assertNotIn("vamos: AMS:", r.cout)
        self.assertIn("Unknown module type: rc_cell", r.cout)
        self.assertFalse(os.path.exists(os.path.join(r.d, "simv.msv", "interface_element.rpt")))
        if r.comp.returncode == 0:
            self.assertIsNone(job_record(r.d).get("ams_control"))
            self.assertIsNone(job_record(r.d).get("ams"))


# ---------------------------------------------------------------------------------------
# item 18
# ---------------------------------------------------------------------------------------

@needs_ams
@_per_engine
class TestE2E18Timescale(SharedCase):
    """§9 e2e 18: -override_timescale=1ns/1ps on a testbench with no `timescale and a
    `const time PH=5ns` clock driving an RC (edge times asserted); a `timescale 1s/1s design
    without the option is refused (the precision error)."""

    FILES = {"tb.sv": TB18, "rc.sp": RC_SP, "vcsAD.init": "choose xa rc.sp;\n"}
    FILES_1S = {"tb.sv": TB18_1S, "rc.sp": RC_SP, "vcsAD.init": "choose xa rc.sp;\n"}

    def r(self, engine):
        return self.run_case("override", self.FILES,
                             ["-sverilog", "tb.sv", "-override_timescale=1ns/1ps"], engine)

    def check_override_compile(self, engine):
        """the option maps: compile succeeds with a 1 ps precision"""
        r = self.r(engine)
        self.assertCompiled(r, 1, 2, 3, engine)
        job = job_record(r.d)
        self.assertEqual(job["precision"], "1ps")
        self.assertEqual(job["override_timescale"], "1ns/1ps")
        ents = ie_entries(self.report(r.d))
        self.assertIe(ents, "tb.u1.in", "d2a", hiv=VSUP, lov=0.0, rf_time=TRAMP)
        self.assertIe(ents, "tb.u1.out", "a2d", loth=VSUP / 2, hith=VSUP / 2)

    def check_override_edge_times(self, engine):
        """PH=5ns is 5 ns: digital clock edges, D2A ramps and RC crossings land on time"""
        r = self.r(engine)
        raw = self.assertDigitalStop(r, 30 * NS)
        # the clock also toggles at the $finish time (30 ns): a race, not asserted
        clk = [e for e in after_zero(events(r.sout, "clk")) if e[0] < 30 * NS - PS]
        edges = [5 * NS * k for k in range(1, 6)]
        self.assertEqual([v for _, v in clk], ["1", "0", "1", "0", "1"], clk)
        for (t, _), e in zip(clk, edges):
            self.assertAlmostEqual(t, e, delta=0.1 * PS)
        # the D2A source ramps 0 -> 1.8 V over 10 ps from each edge: 50 % at edge + 5 ps
        xs = raw.crossings("n_u1_in_d", VSUP / 2)
        self.assertEqual(len(xs), 5, xs)
        for x, e in zip(xs, edges):
            self.assertAlmostEqual(x, e + TRAMP / 2, delta=1 * PS)
        self.assertCrossings(raw, "n_u1_out", VSUP / 2, edges[0::2],
                             rc_rise_delay(VSUP, VSUP / 2), 10 * PS, +1)
        self.assertCrossings(raw, "n_u1_out", VSUP / 2, edges[1::2],
                             rc_fall_delay(VSUP, VSUP / 2), 10 * PS, -1)
        self.assertEventsFollow(raw, "n_u1_out", VSUP / 2, events(r.sout, "seen"), first="1")

    def check_percent_t_of_realtime(self, engine):
        """`$display("%0t", $realtime)` prints the A2D event times in precision units

        VCS and vvp print 5.355 ns under `timescale 1ns/1ps as "5355" (%0t: no padding):
        the fraction of the time unit is kept, and there is no padding."""
        r = self.r(engine)
        self.assertIsNotNone(r.sim, r.cout)
        exact = [t for t, _ in after_zero(events(r.sout, "seen"))]
        printed = [ln.strip("\r") for ln in r.sout.splitlines() if ln.startswith("TT ")]
        printed = [ln for ln in printed if not re.match(r"^TT\s+0 ", ln)]
        self.assertEqual(len(printed), len(exact), printed)
        for ln, t in zip(printed, exact):
            self.assertRegex(ln, r"^TT %d seen=[01]$" % int(round(t / PS)))

    def check_1s_precision_error(self, engine):
        """`timescale 1s/1s without the option: the compile stops with the precision error"""
        r = self.run_case("onesec", self.FILES_1S, ["-sverilog", "tb.sv"], engine)
        self.assertNotEqual(r.comp.returncode, 0, r.cout)
        self.assertRegex(r.cout, r"error: tb\.sv:2: module tb: digital precision 1s is coarser than "
                         r"1 ms; give -timescale or -override_timescale with a precision (<=|≤) ?1 ?ms")
        self.assertFalse(os.path.exists(os.path.join(r.d, "simv")))

    def check_no_timescale_without_option(self, engine):
        """no `timescale and no option: the compile stops naming the module"""
        r = self.run_case("nounit", self.FILES, ["-sverilog", "tb.sv"], engine)
        self.assertNotEqual(r.comp.returncode, 0, r.cout)
        self.assertRegex(r.cout, r"error: tb\.sv:1: module tb has no time unit")
        self.assertFalse(os.path.exists(os.path.join(r.d, "simv")))

    def check_timescale_prelude(self, engine):
        """-timescale=1ns/1ps (the prelude only) also gives the no-`timescale bench 1 ns"""
        r = self.run_case("prelude", self.FILES, ["-sverilog", "tb.sv", "-timescale=1ns/1ps"],
                          engine)
        self.assertCompiled(r, 1, 2, 3, engine)
        self.assertEqual(job_record(r.d)["precision"], "1ps")
        raw = self.assertDigitalStop(r, 30 * NS)
        clk = [e for e in after_zero(events(r.sout, "clk")) if e[0] < 30 * NS - PS]
        self.assertEqual([v for _, v in clk], ["1", "0", "1", "0", "1"], clk)
        for (t, _), k in zip(clk, range(1, 6)):
            self.assertAlmostEqual(t, 5 * NS * k, delta=0.1 * PS)
        self.assertEventsFollow(raw, "n_u1_out", VSUP / 2, events(r.sout, "seen"), first="1")

    def check_override_rewrites_1s(self, engine):
        """-override_timescale also rewrites an explicit `timescale 1s/1s: #1 is 1 ns"""
        r = self.run_case("onesec_override", self.FILES_1S,
                          ["-sverilog", "tb.sv", "-override_timescale=1ns/1ps"], engine)
        self.assertCompiled(r, 1, 2, 3, engine)
        raw = self.assertDigitalStop(r, 10 * NS)
        clk = [e for e in after_zero(events(r.sout, "clk")) if e[0] < 10 * NS - PS]
        self.assertEqual(len(clk), 9, clk)
        for (t, v), k in zip(clk, range(1, 10)):
            self.assertAlmostEqual(t, k * NS, delta=0.1 * PS)
            self.assertEqual(v, "1" if k % 2 else "0")
        xs = raw.crossings("n_u1_in_d", VSUP / 2)
        self.assertEqual(len(xs), 9, xs)
        for k, x in enumerate(xs, 1):
            self.assertAlmostEqual(x, k * NS + TRAMP / 2, delta=1 * PS)
        self.assertEventsFollow(raw, "n_u1_out", VSUP / 2, events(r.sout, "seen"), first="1")


if __name__ == "__main__":
    unittest.main()
