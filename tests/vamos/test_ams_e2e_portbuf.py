"""End-to-end vcs-ams tests: cut ports behind a buffered input port (tgt-vhdl's port
buffers; docs/VAMOS_AMS_DESIGN.md §5.4, vamos/ams/cut.py "port buffers").

The design is e2e 11 (test_ams_e2e_hier) plus a second wrap_e, w2, on a variable of its
own (clk2, with a d2a rule of its own), whose D2A the cell behind the buffer hosts.  With
src.a declared inout, wrap_e's input port a, which tb drives from a variable, is also driven
inside (the shell's marker), so the iverilog core buffers it and tgt-vhdl draws the buffer
in tb as `PB_we_a <= clk`.  In Verilog the buffer is one-way: the cell can drive only
wrap_e's side of the port, which nothing reads, so declaring src.a inout must give exactly
what input gives: one analog node with tb.clk's other SPICE inputs, tb.clk's rule on it,
the same interface elements, deck and run.  Each engine compiles and runs both
declarations and compares them.  (Before the fix the cut split tb.we.u.a off silently, with
its own D2A at the 1.2 V default, so qe rose at 57 ns instead of 54; here tb.clk2's rule
would match no node, an [MSV-IE-OPT-TNF] error.)

Where what the cell drives could be seen (wrap_e reads its port) or would fight the
buffer (src.a declared output), the compile stops at the cut with an error naming
port_dir input.

The IE report's step-2b wording (the direction probe's reason for an auto port on a
wrapper input port, shells.ShellResult.directions) comes from flow.py passing
sh.directions to the cut, which TestE2EProbeReason* check.

The file also holds the cut's other end-to-end regressions (vamos/ams/cut.py):
  * TestE2EParamOverride*: a Verilog parameter override on a multi-view cell's instance is
    an error whatever the parameter's type (§0, §4.7): real, string, untyped-real, by #(),
    by position, by defparam and hierarchical defparam, with param_pass enable, and where
    the translator's variant merge left another run's "--   P = v" lines in design.vhd (a
    cell named after a VHDL reserved word; a wrapper module sorting before the top).  An
    override equal to the default, values computed from overridden range parameters
    (localparam real LSB = VREF / (1 << N)) and a negative integer default compile and run.
  * TestE2EX2v*: the D2A's X rules (§3.4, PAMS p206) on both engines: x2v=4 drives hiv
    after a 0, lov after a 1, and otherwise (after Z or X, and at start-up) holds the
    previous voltage; x2v=3 always holds it; a cell whose port is named v drives its node.

Run (WSL):  python3 -m unittest -v test_ams_e2e_portbuf   (from tests/vamos)
Keep the build directories with VAMOS_TEST_KEEP=1.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import Dict, List, Optional, Sequence, Tuple

from ams_e2e_lib import SHIMS, TIMEOUT, engines_available, needs_ams
from vamos_testlib import ROOT

TOL_NS = 1.5          # displayed digital event time vs the analytic crossing (see test_ams_e2e_hier)
TAU = 10.0            # sink: 10k into 1p behind the unity buffer src, ns


# =============================================================================
# the design
# =============================================================================

TB = """\
`timescale 1ns/1ps
// e2e 11 with a second wrapper on a variable of its own (clk2)
module tb;
  reg clk = 1'b0;
  always #50 clk = ~clk;
  reg clk2 = 1'b0;
  always #40 clk2 = ~clk2;
  wire ve, qe;
  wrap_e we (.a(clk), .y(ve));
  sink ue (.a(ve), .q(qe));
  wire vref, q;
  bgadc bg (.clk(clk), .vref(vref), .q(q));
  wire [1:0] v;
  wire [7:0] w;
  cout c3 (.a(clk), .y(v[0]));
  cin c4 (.a(v[0]));
  cout2 c5 (.a(clk), .y(w[5:4]));
  cin c6 (.a(w[4]));
  wire v2, q2;
  wrap_e w2 (.a(clk2), .y(v2));
  sink u2 (.a(v2), .q(q2));
  always @(qe) $display("%0t qe=%b", $time, qe);
  always @(q) $display("%0t q=%b", $time, q);
  always @(q2) $display("%0t q2=%b", $time, q2);
  initial #400 $finish;
endmodule

module wrap_e (input a, output y);
  src u (.a(a), .vo(y));
endmodule

module bgadc (input clk, output vref, output q);
  bg u1 (.vref(vref));
  adc u2 (.vin(vref), .clk(clk), .q(q));
endmodule
"""

CELLS_SP = """\
* src: unity buffer; sink: RC low-pass (10k into 1p)
.subckt src a vo
e1 vo 0 a 0 1
.ends
.subckt sink a q
r1 a q 10k
c1 q 0 1p
.ends
* bandgap: 1.2 V behind 1k; adc: q = vin - clk (high only while clk is low)
.subckt bg vref
vbg x 0 1.2
rbg x vref 1k
.ends
.subckt adc vin clk q
e1 q 0 vin clk 1
.ends
* cout: buffer behind 10k; cout2: the same on two bus bits; cin: a 10k load
.subckt cout a y
e1 x 0 a 0 1
r1 x y 10k
.ends
.subckt cout2 a y[1] y[0]
e1 x 0 a 0 1
r1 x y[1] 10k
r0 x y[0] 10k
.ends
.subckt cin a
r1 a 0 10k
.ends
.tran 1n 400n
"""

PORT_DIRS = """\
port_dir -cell src (%s a; output vo);
port_dir -cell sink (input a; output q);
port_dir -cell bg (output vref);
port_dir -cell adc (input vin, clk; output q);
port_dir -cell cout (input a; output y);
port_dir -cell cout2 (input a; output y);
port_dir -cell cin (input a);
"""

LEVELS = """\
d2a hiv=1.8 lov=0 node=tb.clk;
d2a hiv=1.5 lov=0 node=tb.clk2;
a2d loth=0.6 hith=0.6 node=*;
"""


def files(src_a: str, tb: str = TB, port_dirs: bool = True) -> Dict[str, str]:
    """The design with src.a declared `src_a` (no port_dir at all when port_dirs is False)."""
    init = "choose xa cells.sp;\n" + (PORT_DIRS % src_a if port_dirs else "") + LEVELS
    return {"tb.sv": tb, "cells.sp": CELLS_SP, "vcsAD.init": init}


def line_of(text: str, needle: str) -> int:
    for k, ln in enumerate(text.splitlines(), 1):
        if needle in ln:
            return k
    raise AssertionError("%r is not in the text" % needle)


# =============================================================================
# helpers
# =============================================================================

def _env() -> Dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = SHIMS + os.pathsep + env.get("PATH", "")
    env.pop("VAMOS_ANALOG", None)
    return env


def _run(cmd: Sequence[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), cwd=cwd, env=_env(), stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, universal_newlines=True, errors="replace",
                          timeout=TIMEOUT)


class Build:
    """One `vcs-ams` compile (and one ./simv run when it compiled) in a scratch directory."""

    def __init__(self, engine: str, tag: str, fs: Dict[str, str], run: bool = True):
        self.engine = engine
        self.wd = tempfile.mkdtemp(prefix="vamos-e2e-portbuf-%s-" % tag)
        for rel, text in fs.items():
            p = os.path.join(self.wd, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write(text)
        self.comp = _run(["vcs-ams", "-sverilog", "tb.sv", "--vamos-analog=" + engine], self.wd)
        self.sim = _run(["./simv"], self.wd) if run and self.comp.returncode == 0 else None

    def remove(self) -> None:
        if os.environ.get("VAMOS_TEST_KEEP"):
            sys.stderr.write("kept %s\n" % self.wd)
        else:
            shutil.rmtree(self.wd, ignore_errors=True)

    def text(self, rel: str) -> str:
        with open(os.path.join(self.wd, rel), encoding="utf-8", errors="replace") as fh:
            return fh.read()

    def report(self) -> str:
        return self.text(os.path.join("simv.msv", "interface_element.rpt"))

    def plan(self) -> dict:
        return json.loads(self.text(os.path.join("simv.daidir", "ams", "ams.json")))

    def deck(self) -> str:
        return self.text(os.path.join("simv.daidir", "ams", "deck",
                                      "vamos.sim" if self.engine == "vacask" else "vamos.cir"))


def ie_entries(report: str) -> List[Tuple[str, str, Dict[str, str]]]:
    """(kind, node, {key: value}) for every d2a/a2d line of an IE report."""
    out = []
    for ln in report.splitlines():
        m = re.match(r"^(d2a|a2d)\s+(.*?)\s*\bnode=(\S+?)\s*;\s*$", ln)
        if m:
            out.append((m.group(1), m.group(3), dict(kv.partition("=")[::2] for kv in m.group(2).split())))
    return out


def report_block(report: str, node: str) -> List[str]:
    """The lines of a node's IE-report entry (entry lines + comments, up to a blank line)."""
    lines = report.splitlines()
    for i, ln in enumerate(lines):
        if ln.rstrip().endswith("node=%s;" % node):
            j = i
            while j > 0 and lines[j - 1].strip() and not lines[j - 1].startswith("//"):
                j -= 1
            k = i
            while k < len(lines) and lines[k].strip():
                k += 1
            return lines[j:k]
    return []


def deck_x_nodes(deck: str) -> Dict[str, List[str]]:
    """xv_ instance -> its nodes, from a VACASK (`x (a b) m`) or Xyce (`x a b m`) deck."""
    out: Dict[str, List[str]] = {}
    for ln in deck.splitlines():
        s = ln.strip()
        if not s.lower().startswith("xv_"):
            continue
        m = re.match(r"^(\S+)\s*\(([^)]*)\)\s*\S+", s)
        if m:
            out[m.group(1).lower()] = [n.lower() for n in m.group(2).split()]
        else:
            toks = s.split()
            out[toks[0].lower()] = [t.lower() for t in toks[1:-1]]
    return out


def events(out: str) -> List[Tuple[int, str]]:
    """(time in ps, "tag=value") of every `$display("%0t <tag>=%b", ...)` line."""
    ev = []
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) == 2 and parts[0].isdigit() and "=" in parts[1]:
            ev.append((int(parts[0]), parts[1]))
    return ev


def edges(ev: List[Tuple[int, str]], tag: str) -> Tuple[Optional[str], List[Tuple[int, str]]]:
    """(value settled at t=0, later changes) of one tag."""
    init = None
    later: List[Tuple[int, str]] = []
    for t, tv in ev:
        name, _, v = tv.partition("=")
        if name != tag:
            continue
        if t == 0:
            init = v
            continue
        if v != (later[-1][1] if later else init):
            later.append((t, v))
    return init, later


def rc_cross(t_edge_ns: float, v_final: float, thr: float, rising: bool) -> float:
    """Threshold crossing of the sink's first-order step response (ns)."""
    if rising:
        return t_edge_ns + TAU * math.log(v_final / (v_final - thr))
    return t_edge_ns + TAU * math.log(v_final / thr)


# =============================================================================
# inout behind the buffered port == input
# =============================================================================

DIRECTION_WE = ("// direction: inout→input (one-way port buffer: input port tb.we.a fed from "
                "variable tb.clk) tb.we.u.a")
DIRECTION_W2 = ("// direction: inout→input (one-way port buffer: input port tb.w2.a fed from "
                "variable tb.clk2) tb.w2.u.a")


class _SameAsInput:
    """src.a declared input and declared inout: the same nodes, IEs, deck and run."""
    ENGINE = ""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if cls.ENGINE not in engines_available():
            raise unittest.SkipTest("analog engine %s is not available" % cls.ENGINE)
        cls.builds = {m: Build(cls.ENGINE, m, files(m)) for m in ("input", "inout")}

    @classmethod
    def tearDownClass(cls) -> None:
        for b in getattr(cls, "builds", {}).values():
            b.remove()
        super().tearDownClass()

    def need(self, mode: str, run: bool = True) -> Build:
        b = self.builds[mode]
        self.assertEqual(b.comp.returncode, 0, "vcs-ams failed (%s, src.a %s):\n%s"
                         % (self.ENGINE, mode, b.comp.stdout[-6000:]))
        if run:
            self.assertIsNotNone(b.sim)
            self.assertEqual(b.sim.returncode, 0, "./simv failed (%s, src.a %s):\n%s"
                             % (self.ENGINE, mode, b.sim.stdout[-6000:]))
        return b

    def test_port_buffers_drawn(self):
        # the inout build really goes through tgt-vhdl's port buffers; the input build has none
        vhd = self.need("inout", run=False).text("simv.daidir/nvc/design.vhd")
        for inst in ("we", "w2"):
            self.assertRegex(vhd, r"signal PB_%s_a : resolved_logic3d := L3D_X;  -- Port buffer of "
                                  r"input a of instance %s " % (inst, inst))
        self.assertIn("PB_we_a <= clk;", vhd)
        self.assertIn("PB_w2_a <= clk2;", vhd)
        self.assertNotIn("Port buffer", self.need("input", run=False).text("simv.daidir/nvc/design.vhd"))

    def test_same_interface_elements(self):
        ents = ie_entries(self.need("inout", run=False).report())
        self.assertEqual(ents, ie_entries(self.need("input", run=False).report()))
        got = {(k, n): kv for k, n, kv in ents}
        self.assertEqual(sorted(got), [("a2d", "tb.bg.u2.q"), ("a2d", "tb.u2.q"), ("a2d", "tb.ue.q"),
                                       ("d2a", "tb.c3.a"), ("d2a", "tb.w2.u.a")])
        self.assertEqual(float(got[("d2a", "tb.c3.a")]["hiv"]), 1.8)       # tb.clk's rule
        self.assertEqual(float(got[("d2a", "tb.w2.u.a")]["hiv"]), 1.5)     # tb.clk2's rule

    def test_report_differs_by_the_direction_lines_only(self):
        rin = self.need("input", run=False).report()
        rio = self.need("inout", run=False).report()
        extra = [ln for ln in rio.splitlines() if ln.startswith("// direction:")]
        self.assertEqual(extra, [DIRECTION_WE, DIRECTION_W2])
        self.assertEqual([ln for ln in rio.splitlines() if ln not in extra], rin.splitlines())
        blk = report_block(rio, "tb.c3.a")
        self.assertIn("// All Boundary Nets tb.bg.u2.clk tb.c3.a tb.c5.a tb.we.u.a", blk)
        self.assertIn("// Top-Net tb.clk", blk)
        self.assertIn(DIRECTION_WE, blk)
        blk = report_block(rio, "tb.w2.u.a")
        self.assertIn("// host tb.w2.u.a", blk)                 # the cell behind the buffer hosts it
        self.assertIn("// Top-Net tb.clk2", blk)
        self.assertIn(DIRECTION_W2, blk)

    def test_same_boundary_and_deck(self):
        bi, bo = self.need("input", run=False), self.need("inout", run=False)
        self.assertEqual(bo.text("simv.daidir/ams/vamos.boundary"),
                         bi.text("simv.daidir/ams/vamos.boundary"))
        self.assertEqual(len(bo.text("simv.daidir/ams/vamos.boundary").splitlines()), 7)
        x = deck_x_nodes(bo.deck())
        self.assertEqual(x, deck_x_nodes(bi.deck()))
        self.assertEqual(x["xv_we_u"][0], x["xv_c3"][0])        # one node with tb.clk's inputs
        self.assertEqual(x["xv_we_u"][0], x["xv_bg_u2"][1])
        self.assertNotEqual(x["xv_w2_u"][0], x["xv_we_u"][0])
        roles = {n["canonical"]: (n["role"], sorted(n["aliases"])) for n in bo.plan()["nodes"]}
        self.assertEqual(roles, {n["canonical"]: (n["role"], sorted(n["aliases"]))
                                 for n in bi.plan()["nodes"]})
        self.assertEqual(roles["tb.w2.u.a"], ("D2A", ["tb.clk2", "tb.w2.a"]))

    def test_same_run(self):
        out = self.need("inout").sim.stdout
        ev = events(out)
        ev_in = events(self.need("input").sim.stdout)
        for tag in ("qe", "q", "q2"):
            self.assertEqual(edges(ev, tag), edges(ev_in, tag), tag)
        init, got = edges(ev, "qe")
        self.assertEqual(init, "0")
        want = []
        for k in range(4):                                       # 1.8 V (tb.clk's rule)
            want.append((rc_cross(50 + 100 * k, 1.8, 0.6, True), "1"))
            if k < 3:
                want.append((rc_cross(100 + 100 * k, 1.8, 0.6, False), "0"))
        self.assert_edges(got, want, "qe")
        init, got = edges(ev, "q2")
        self.assertEqual(init, "0")
        want = []
        for k in range(5):                                       # 1.5 V (tb.clk2's rule)
            want.append((rc_cross(40 + 80 * k, 1.5, 0.6, True), "1"))
            if k < 4:
                want.append((rc_cross(80 + 80 * k, 1.5, 0.6, False), "0"))
        self.assert_edges(got, want, "q2")
        init, got = edges(ev, "q")
        self.assertEqual(init, "1")
        self.assertEqual([v for _, v in got], ["0", "1"] * 3 + ["0"])
        self.assertIn("co-simulation finished: digital stop at 4e-07 s", out)

    def test_analog_levels(self):
        b = self.need("inout")
        raw = self.raw_of(b)
        self.assertAlmostEqual(raw.at("n_ue_a", 95e-9), 1.8, delta=0.01)       # src buffers clk
        self.assertAlmostEqual(raw.at("n_ue_a", 45e-9), 0.0, delta=0.01)
        self.assertAlmostEqual(raw.at("n_w2_u_a", 70e-9), 1.5, delta=0.01)     # clk2 high, 1.5 V
        self.assertAlmostEqual(raw.at("n_w2_u_a", 100e-9), 0.0, delta=0.01)
        self.assertAlmostEqual(raw.at("n_u2_a", 70e-9), 1.5, delta=0.01)

    # -- helpers -------------------------------------------------------------------------

    def raw_of(self, b: Build):
        from vamos.netlist import rawfile
        return rawfile.read(os.path.join(b.wd, "vamos_ams.raw"))

    def assert_edges(self, got: List[Tuple[int, str]], want: List[Tuple[float, str]], what: str):
        self.assertEqual([v for _, v in got], [v for _, v in want],
                         "%s (%s): values %s, want %s" % (what, self.ENGINE, got, want))
        for (tg, _), (tw, _) in zip(got, want):
            self.assertAlmostEqual(tg / 1000.0, tw, delta=TOL_NS,
                                   msg="%s (%s): edge at %g ns, want %.2f ns; all %s"
                                   % (what, self.ENGINE, tg / 1000.0, tw, got))


@needs_ams
class TestE2EPortBufSameVacask(_SameAsInput, unittest.TestCase):
    ENGINE = "vacask"


@needs_ams
class TestE2EPortBufSameXyce(_SameAsInput, unittest.TestCase):
    ENGINE = "xyce"


# =============================================================================
# what a one-way buffer cannot carry: errors naming port_dir input
# =============================================================================

TB_READ = TB.replace("module wrap_e (input a, output y);\n  src u (.a(a), .vo(y));\n",
                     "module wrap_e (input a, output y, output z);\n  src u (.a(a), .vo(y));\n"
                     "  assign z = ~a;\n")


class _Errors:
    """The compile stops at the cut, before anything runs, with one error per cell port."""
    ENGINE = ""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if cls.ENGINE not in engines_available():
            raise unittest.SkipTest("analog engine %s is not available" % cls.ENGINE)
        assert TB_READ != TB
        cls.builds = {"output": Build(cls.ENGINE, "output", files("output"), run=False),
                      "read": Build(cls.ENGINE, "read", files("inout", tb=TB_READ), run=False)}

    @classmethod
    def tearDownClass(cls) -> None:
        for b in getattr(cls, "builds", {}).values():
            b.remove()
        super().tearDownClass()

    def errors(self, case: str) -> List[str]:
        b = self.builds[case]
        self.assertNotEqual(b.comp.returncode, 0, b.comp.stdout[-4000:])
        self.assertIn("vamos: error: AMS compile failed at the digital cut", b.comp.stdout)
        self.assertFalse(os.path.exists(os.path.join(b.wd, "simv.msv", "interface_element.rpt")))
        return [ln for ln in b.comp.stdout.splitlines() if ln.startswith("vamos: error: tb.")]

    def test_output_behind_the_buffer(self):
        msg = ("port a of %s is an output behind input port %s, fed one way from variable %s (a "
               "port buffer): the cell could drive only the wrapper's side of that port, against the "
               "buffer; declare it input (port_dir -cell src (input a;)), or connect a net to %s")
        self.assertEqual(sorted(self.errors("output")), [
            "vamos: error: tb.w2.u: " + msg % ("tb.w2.u", "tb.w2.a", "tb.clk2", "tb.w2.a"),
            "vamos: error: tb.we.u: " + msg % ("tb.we.u", "tb.we.a", "tb.clk", "tb.we.a")])

    def test_inout_read_inside(self):
        msg = ("port a of %s is inout behind input port %s, fed one way from variable %s (a port "
               "buffer), and %s reads that port: what the cell drives there would reach only the "
               "wrapper's side, which vamos does not model; declare it input (port_dir -cell src "
               "(input a;)), or connect a net to %s")
        self.assertEqual(sorted(self.errors("read")), [
            "vamos: error: tb.w2.u: " + msg % ("tb.w2.u", "tb.w2.a", "tb.clk2", "tb.w2", "tb.w2.a"),
            "vamos: error: tb.we.u: " + msg % ("tb.we.u", "tb.we.a", "tb.clk", "tb.we", "tb.we.a")])


@needs_ams
class TestE2EPortBufErrorsVacask(_Errors, unittest.TestCase):
    ENGINE = "vacask"


@needs_ams
class TestE2EPortBufErrorsXyce(_Errors, unittest.TestCase):
    ENGINE = "xyce"


# =============================================================================
# the probe's step-2b reason in the IE report (every port auto)
# =============================================================================

def flow_passes_directions() -> bool:
    """Whether vamos/ams/flow.py hands shells.ShellResult.directions to the cut."""
    with open(os.path.join(ROOT, "vamos", "ams", "flow.py"), errors="replace") as fh:
        return re.search(r"\bdirections\s*=", fh.read()) is not None


class _ProbeReason:
    """With every SPICE port auto, src.a and adc.clk become inputs by the probe's step 2b; the
    clk node's direction lines give that reason, not '(variable actual)'."""
    ENGINE = ""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if cls.ENGINE not in engines_available():
            raise unittest.SkipTest("analog engine %s is not available" % cls.ENGINE)
        if not flow_passes_directions():
            raise unittest.SkipTest("flow.py does not pass shells.ShellResult.directions to "
                                    "cut.analyse yet (directions=sh.directions)")
        cls.build = Build(cls.ENGINE, "auto", files("", port_dirs=False), run=False)

    @classmethod
    def tearDownClass(cls) -> None:
        b = getattr(cls, "build", None)
        if b is not None:
            b.remove()
        super().tearDownClass()

    def test_step_2b_reason(self):
        b = self.build
        self.assertEqual(b.comp.returncode, 0, b.comp.stdout[-6000:])
        blk = report_block(b.report(), "tb.c3.a")
        src_u = line_of(TB, "src u (")
        why_we = ("wrap_e.u (tb.sv:%d), connected to input port wrap_e.a, which tb.we (tb.sv:%d) "
                  "connects to variable clk" % (src_u, line_of(TB, "wrap_e we (")))
        why_bg = ("bgadc.u2 (tb.sv:%d), connected to input port bgadc.clk, which tb.bg (tb.sv:%d) "
                  "connects to variable clk" % (line_of(TB, "adc u2 ("), line_of(TB, "bgadc bg (")))
        self.assertIn("// direction: auto→input (%s) tb.we.u.a" % why_we, blk)
        self.assertIn("// direction: auto→input (%s) tb.bg.u2.clk" % why_bg, blk)
        # the other auto inputs on the node keep the probe's variable-actual wording
        self.assertIn("// direction: auto→input (variable actual) tb.c3.a", blk)


@needs_ams
class TestE2EProbeReasonVacask(_ProbeReason, unittest.TestCase):
    ENGINE = "vacask"


@needs_ams
class TestE2EProbeReasonXyce(_ProbeReason, unittest.TestCase):
    ENGINE = "xyce"


# =============================================================================
# parameter overrides on multi-view cells (§0, §4.7; vamos/ams/cut.py param_overrides)
# =============================================================================

def _cells_sp(names: Sequence[str], params: Optional[Dict[str, str]] = None) -> str:
    """A 0.25x buffer subckt (with a 1 MOhm input load) per cell; params: subckt -> 'p=v'."""
    out = ["* multi-view cells: e1 y = 0.25 a"]
    for n in names:
        p = (params or {}).get(n)
        out += [".subckt %s a y%s" % (n, " " + p if p else ""),
                "e1 y 0 a 0 %s" % ("'%s'" % p.split("=")[0] if p else "0.25"),
                "rl a 0 1meg", ".ends"]
    return "\n".join(out + [".tran 0.1n 30n", ""])


def _tb(cells: str, insts: Sequence[str], extra: str = "") -> str:
    ys = ", ".join("y%d" % k for k in range(1, len(insts) + 1))
    return ("`timescale 1ns/1ps\n%s"
            "module tb;\n  reg a = 0;\n  wire %s;\n%s%s"
            "  initial begin\n    #10 a = 1;\n    #10 $display(\"%%0t done\", $time);\n"
            "    $finish;\n  end\nendmodule\n"
            % (cells, ys, "".join("  %s\n" % x for x in insts), extra))


def _vcell(name: str, header: str, body: str = "", ports: str = "(input a, output y)",
           assign: str = "a") -> str:
    return "module %s %s %s;\n%s  assign y = %s;\nendmodule\n" % (name, header, ports, body, assign)


OVR_CELLS = ["c_real", "c_str", "c_defp", "c_untyped", "c_pos", "c_body", "c_hier", "c_pp",
             "buffer", "mvcell"]
OVR_TB = _tb(
    _vcell("c_real", "#(parameter real GAIN = 0.25)") +
    _vcell("c_str", '#(parameter MODE = "fast")') +
    _vcell("c_defp", "#(parameter real GAIN = 0.25)") +
    _vcell("c_untyped", "#(parameter GAIN = 0.25)") +
    _vcell("c_pos", "#(parameter real GAIN = 0.25)") +
    _vcell("c_body", "", body="  parameter real GAIN = 0.25;\n") +
    _vcell("c_hier", "#(parameter real GAIN = 0.25)") +
    _vcell("c_pp", "#(parameter real gain = 0.25)") +
    # a VHDL reserved word: its own run's variant (G = 0.25) is the one design.vhd keeps
    _vcell("buffer", "#(parameter real G = 0.25)") +
    _vcell("mvcell", "#(parameter real GAIN = 0.25)") +
    "module wrap (input a, output y);\n  c_hier u (.a(a), .y(y));\nendmodule\n"
    # sorts before tb: design.vhd keeps a_wrap's run's mvcell variant (GAIN = 0.25)
    "module a_wrap #(parameter real G = 0.25) (input a, output y);\n"
    "  mvcell #(.GAIN(G)) u (.a(a), .y(y));\nendmodule\n",
    ["c_real #(.GAIN(0.9)) u1 (.a(a), .y(y1));",
     'c_str #(.MODE("slow")) u2 (.a(a), .y(y2));',
     "c_defp u3 (.a(a), .y(y3));",
     "c_untyped #(.GAIN(0.9)) u4 (.a(a), .y(y4));",
     "c_pos #(0.9) u5 (.a(a), .y(y5));",
     "c_body u6 (.a(a), .y(y6));",
     "wrap w7 (.a(a), .y(y7));",
     "c_pp #(.gain(0.9)) u8 (.a(a), .y(y8));",
     "buffer #(.G(0.9)) u9 (.a(a), .y(y9));",
     "a_wrap #(.G(0.9)) w10 (.a(a), .y(y10));"],
    "  defparam u3.GAIN = 0.9;\n  defparam u6.GAIN = 0.9;\n  defparam tb.w7.u.GAIN = 0.9;\n")
OVR_ERRORS = [("tb.u1", "GAIN=0.9", "c_real"), ("tb.u2", 'MODE="slow"', "c_str"),
              ("tb.u3", "GAIN=0.9", "c_defp"), ("tb.u4", "GAIN=0.9", "c_untyped"),
              ("tb.u5", "GAIN=0.9", "c_pos"), ("tb.u6", "GAIN=0.9", "c_body"),
              ("tb.w7.u", "GAIN=0.9", "c_hier"), ("tb.u8", "gain=0.9", "c_pp"),
              ("tb.u9", "G=0.9", "buffer"), ("tb.w10.u", "GAIN=0.9", "mvcell")]

SAME_CELLS = ["k_same", "k_noovr", "k_str", "k_expr", "k_neg", "k_lsb", "k_step", "k_func",
              "block", "mvcell"]
SAME_TB = _tb(
    _vcell("k_same", "#(parameter real GAIN = 0.25)") +
    _vcell("k_noovr", "#(parameter real GAIN = 0.25)") +
    _vcell("k_str", '#(parameter MODE = "fast")') +
    _vcell("k_expr", "#(parameter real GAIN = 1.0/4)") +
    _vcell("k_neg", "#(parameter OFF = -1)") +
    # N only shapes the input bus (an override that is allowed); LSB/STEP follow it
    _vcell("k_lsb", "#(parameter N = 2, parameter real VREF = 1.0, "
           "localparam real LSB = VREF / (1 << N))", ports="(input [N-1:0] a, output y)",
           assign="a[0]") +
    _vcell("k_step", "#(parameter N = 2, parameter real STEP = 1.0 / N)",
           ports="(input [N-1:0] a, output y)", assign="a[0]") +
    # a localparam vamos cannot evaluate ($signed): no override can reach it
    _vcell("k_func", "#(parameter N = 2, parameter real VREF = 1.0, "
           "localparam real LSB = VREF / $signed(1 << N))",
           ports="(input [N-1:0] a, output y)", assign="a[0]") +
    _vcell("block", "#(parameter real G = 0.25)") +
    _vcell("mvcell", "#(parameter real GAIN = 0.25)") +
    "module a_wrap #(parameter real G = 0.25) (input a, output y);\n"
    "  mvcell #(.GAIN(G)) u (.a(a), .y(y));\nendmodule\n",
    ["k_same #(.GAIN(0.25)) u1 (.a(a), .y(y1));",
     "k_noovr u2 (.a(a), .y(y2));",
     'k_str #(.MODE("fast")) u3 (.a(a), .y(y3));',
     "k_expr #(.GAIN(0.25)) u4 (.a(a), .y(y4));",
     "k_neg u5 (.a(a), .y(y5));",
     "k_lsb #(.N(4)) u6 (.a({3'b000, a}), .y(y6));",
     "k_step #(.N(4)) u7 (.a({3'b000, a}), .y(y7));",
     "k_func #(.N(4)) u8 (.a({3'b000, a}), .y(y8));",
     "block u9 (.a(a), .y(y9));",
     "a_wrap w10 (.a(a), .y(y10));"])


def _range_cells_sp(names: Sequence[str]) -> str:
    return "\n".join(["* 4-bit input cells: y = 0.25 a[0]"] + [
        ".subckt %s a[3] a[2] a[1] a[0] y\ne1 y 0 a[0] 0 0.25\nrl a[0] 0 1meg\n.ends" % n
        for n in names] + [""])


def _override_files(tb: str, cells: Sequence[str], sp: str, extra_init: str = "") -> Dict[str, str]:
    init = "choose xa cells.sp;\n" + extra_init + "".join("use_spice -cell %s;\n" % c for c in cells)
    return {"tb.sv": tb, "cells.sp": sp, "vcsAD.init": init}


class _ParamOverride:
    ENGINE = ""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if cls.ENGINE not in engines_available():
            raise unittest.SkipTest("analog engine %s is not available" % cls.ENGINE)
        ovr_sp = _cells_sp(OVR_CELLS, {"c_pp": "gain=0.25"})
        same_sp = (_cells_sp([c for c in SAME_CELLS if c not in ("k_lsb", "k_step", "k_func")]) +
                   _range_cells_sp(["k_lsb", "k_step", "k_func"]))
        cls.builds = {
            "ovr": Build(cls.ENGINE, "ovr", _override_files(OVR_TB, OVR_CELLS, ovr_sp,
                                                             "param_pass enable;\n"), run=False),
            "same": Build(cls.ENGINE, "same", _override_files(SAME_TB, SAME_CELLS, same_sp))}

    @classmethod
    def tearDownClass(cls) -> None:
        for b in getattr(cls, "builds", {}).values():
            b.remove()
        super().tearDownClass()

    def test_every_type_is_refused(self):
        b = self.builds["ovr"]
        out = b.comp.stdout
        self.assertNotEqual(b.comp.returncode, 0, out[-4000:])
        self.assertIn("vamos: error: AMS compile failed at the analog deck", out)
        self.assertIn("param_pass has no effect", out)
        got = sorted(ln for ln in out.splitlines() if "parameter override" in ln)
        want = sorted("vamos: error: %s: parameter override %s on SPICE instance %s is not passed "
                      "to subckt %s" % (p, pv, p, s) for p, pv, s in OVR_ERRORS)
        self.assertEqual(got, want, out[-6000:])

    def test_unchanged_and_computed_values_pass(self):
        b = self.builds["same"]
        self.assertEqual(b.comp.returncode, 0, b.comp.stdout[-6000:])
        self.assertNotIn("parameter override", b.comp.stdout)
        self.assertIsNotNone(b.sim)
        self.assertEqual(b.sim.returncode, 0, b.sim.stdout[-4000:])
        self.assertIn("20000 done", b.sim.stdout)


@needs_ams
class TestE2EParamOverrideVacask(_ParamOverride, unittest.TestCase):
    ENGINE = "vacask"


@needs_ams
class TestE2EParamOverrideXyce(_ParamOverride, unittest.TestCase):
    ENGINE = "xyce"


# =============================================================================
# the D2A's X rules: x2v=4 and x2v=3 (§3.4, PAMS p206; vamos/ams/cut.py _logic_slot)
# =============================================================================

X2V_TB = """\
`timescale 1ns/1ps
module tb;
  reg d1;                // x2v=4: X at start-up, then 0, X, 1, X
  reg d2 = 1'b1;         // x2v=4 through a net: 1, Z, X, 0, Z, X
  reg d3;                // x2v=3, on a port named v: X at start-up, then 1, X, 0, X
  wire n2;
  wire y1, y2, y3;
  assign n2 = d2;
  buf1 u1 (.a(d1), .y(y1));
  buf1 u2 (.a(n2), .y(y2));
  vbuf u3 (.v(d3), .y(y3));
  initial begin
    #20 d1 = 1'b0;
    #10 d1 = 1'bx;
    #10 d1 = 1'b1;
    #10 d1 = 1'bx;
  end
  initial begin
    #10 d2 = 1'bz;
    #10 d2 = 1'bx;
    #10 d2 = 1'b0;
    #10 d2 = 1'bz;
    #10 d2 = 1'bx;
  end
  initial begin
    #15 d3 = 1'b1;
    #10 d3 = 1'bx;
    #10 d3 = 1'b0;
    #10 d3 = 1'bx;
  end
  initial #60 $finish;
endmodule
"""

X2V_SP = """\
* unity buffers with a 1 MOhm input load; vbuf's input port is named v
.subckt buf1 a y
e1 y 0 a 0 1
rl a 0 1meg
.ends
.subckt vbuf v y
e1 y 0 v 0 1
rl v 0 1meg
.ends
.tran 0.1n 60n
"""

X2V_INIT = """\
choose xa cells.sp;
d2a hiv=1.8 lov=0 x2v=4 node=tb.u1.a;
d2a hiv=1.8 lov=0 x2v=4 node=tb.u2.a;
d2a hiv=1.8 lov=0 x2v=3 node=tb.u3.v;
"""

# (node, time ns, D2A level, enable): PAMS x2v=4 is hiv after a 0, lov after a 1, the
# previous voltage otherwise (after Z or X, and at start-up: lov); x2v=3 always holds
X2V_WANT = [
    ("n_u1_a", 5, 0.0, 1.0), ("n_u1_a", 24, 0.0, 1.0), ("n_u1_a", 34, 1.8, 1.0),
    ("n_u1_a", 44, 1.8, 1.0), ("n_u1_a", 54, 0.0, 1.0),
    ("n_u2_a", 5, 1.8, 1.0), ("n_u2_a", 14, 1.8, 0.0), ("n_u2_a", 24, 1.8, 1.0),
    ("n_u2_a", 34, 0.0, 1.0), ("n_u2_a", 44, 0.0, 0.0), ("n_u2_a", 54, 0.0, 1.0),
    ("n_u3_v", 5, 0.0, 1.0), ("n_u3_v", 19, 1.8, 1.0), ("n_u3_v", 29, 1.8, 1.0),
    ("n_u3_v", 39, 0.0, 1.0), ("n_u3_v", 49, 0.0, 1.0),
]


class _X2v:
    ENGINE = ""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if cls.ENGINE not in engines_available():
            raise unittest.SkipTest("analog engine %s is not available" % cls.ENGINE)
        cls.build = Build(cls.ENGINE, "x2v", {"tb.sv": X2V_TB, "cells.sp": X2V_SP,
                                              "vcsAD.init": X2V_INIT})

    @classmethod
    def tearDownClass(cls) -> None:
        b = getattr(cls, "build", None)
        if b is not None:
            b.remove()
        super().tearDownClass()

    def test_d2a_x_rules(self):
        b = self.build
        self.assertEqual(b.comp.returncode, 0, b.comp.stdout[-6000:])
        self.assertIsNotNone(b.sim)
        self.assertEqual(b.sim.returncode, 0, b.sim.stdout[-4000:])
        from vamos.netlist import rawfile
        raw = rawfile.read(os.path.join(b.wd, "vamos_ams.raw"))
        for node, t, level, enable in X2V_WANT:
            self.assertAlmostEqual(raw.at(node + "_d", t * 1e-9), level, delta=1e-3,
                                   msg="%s D2A level at %d ns" % (node, t))
            self.assertAlmostEqual(raw.at(node + "_e", t * 1e-9), enable, delta=1e-3,
                                   msg="%s D2A enable at %d ns" % (node, t))
            if enable == 1.0:          # driven: the node follows (500.7 Ohm into 1 MOhm)
                self.assertAlmostEqual(raw.at(node, t * 1e-9), level * 1e6 / (1e6 + 500.7),
                                       delta=5e-3, msg="%s at %d ns" % (node, t))


@needs_ams
class TestE2EX2vVacask(_X2v, unittest.TestCase):
    ENGINE = "vacask"


@needs_ams
class TestE2EX2vXyce(_X2v, unittest.TestCase):
    ENGINE = "xyce"


if __name__ == "__main__":
    unittest.main()
