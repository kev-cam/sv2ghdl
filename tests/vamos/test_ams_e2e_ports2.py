"""End-to-end vcs-ams tests (both engines) for the translator's port connections
(tgt-vhdl, scope.cc; the plain-mode cases are in test_translator_ports.py):

  * TestE2EArrayPortBuf*: an instance array of wrappers on a variable, each wrapper
    holding a SPICE cell on its input (TB-01).  With src.a declared inout the
    wrapper's input is driven inside, so the iverilog core buffers it -- one buffer
    for the whole array, in element [0] -- and tgt-vhdl draws it in the parent for
    every element.  Declaring src.a inout must give exactly what input gives: the
    same interface elements and the same run (before the fix element [1]'s port was
    left open, its cell input floated on its own node, and qe(1) never rose).
  * TestE2ERealPortExpr*: real expressions on a multi-view cell's real input port
    (TB-04/TB-11): r1 + 0.2, -r1, r1 / 2.0, r1 * 2.0, a real wire assigned r1 + 0.2,
    code * 0.1 (a vector cast to real), and an undriven `wire real' (TB-10) -- the
    analog input is that value (before: 0 V, silently, for every expression; the
    undriven wire did not translate at all).
  * TestE2EConcatAuto*: a SPICE-only cell's auto (inout) bus port on a concatenation
    of wires (TB-05/TB-12): compiles and runs like the port_dir input/output
    declaration (before: nvc "cannot index non-array type LOGIC3D").

Run (WSL):  python3 -m unittest -v test_ams_e2e_ports2   (from tests/vamos)
Keep the build directories with VAMOS_TEST_KEEP=1.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import Dict, List, Sequence, Tuple

from ams_e2e_lib import SHIMS, TIMEOUT, engines_available, needs_ams


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
    """One `vcs-ams` compile and one ./simv run in a scratch directory."""

    def __init__(self, engine: str, tag: str, fs: Dict[str, str]):
        self.engine = engine
        self.wd = tempfile.mkdtemp(prefix="vamos-e2e-ports2-%s-%s-" % (tag, engine))
        for rel, text in fs.items():
            with open(os.path.join(self.wd, rel), "w") as fh:
                fh.write(text)
        self.comp = _run(["vcs-ams", "-sverilog", "tb.sv", "--vamos-analog=" + engine], self.wd)
        self.sim = _run(["./simv"], self.wd) if self.comp.returncode == 0 else None

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

    def design(self) -> str:
        return self.text(os.path.join("simv.daidir", "nvc", "design.vhd"))


def ie_entries(report: str) -> List[Tuple[str, str, str]]:
    """(kind, node, settings) of every d2a/a2d line of an IE report."""
    out = []
    for ln in report.splitlines():
        m = re.match(r"^(d2a|a2d)\s+(.*?)\s*\bnode=(\S+?)\s*;\s*$", ln)
        if m:
            out.append((m.group(1), m.group(3), " ".join(sorted(m.group(2).split()))))
    return sorted(out)


def events(out: str) -> List[Tuple[int, str]]:
    """(time, "tag=value") of every `$display("%0t <tag>=%b", ...)` line."""
    ev = []
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) == 2 and parts[0].isdigit() and "=" in parts[1]:
            ev.append((int(parts[0]), parts[1]))
    return ev


class _Builds:
    """setUpClass builds every (tag, files) of BUILDS on ENGINE; need() checks them."""
    ENGINE = ""
    BUILDS: Dict[str, Dict[str, str]] = {}

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if cls.ENGINE not in engines_available():
            raise unittest.SkipTest("analog engine %s is not available" % cls.ENGINE)
        cls.builds = {tag: Build(cls.ENGINE, tag, fs) for tag, fs in cls.BUILDS.items()}

    @classmethod
    def tearDownClass(cls) -> None:
        for b in getattr(cls, "builds", {}).values():
            b.remove()
        super().tearDownClass()

    def need(self, tag: str) -> Build:
        b = self.builds[tag]
        self.assertEqual(b.comp.returncode, 0, "vcs-ams failed (%s, %s):\n%s"
                         % (self.ENGINE, tag, b.comp.stdout[-6000:]))
        self.assertIsNotNone(b.sim)
        self.assertEqual(b.sim.returncode, 0, "./simv failed (%s, %s):\n%s"
                         % (self.ENGINE, tag, b.sim.stdout[-6000:]))
        return b


# =============================================================================
# TB-01: an instance array of wrappers behind the core's port buffer
# =============================================================================

ARRAY_TB = """\
`timescale 1ns/1ps
module tb;
  reg clk = 1'b0;
  always #50 clk = ~clk;
  wire [1:0] ve, qe;
  wrap_e we[1:0] (.a(clk), .y(ve));
  sink u0 (.a(ve[0]), .q(qe[0]));
  sink u1 (.a(ve[1]), .q(qe[1]));
  always @(qe) $display("%0t qe=%b", $time, qe);
  initial #400 $finish;
endmodule

module wrap_e (input a, output y);
  src u (.a(a), .vo(y));
endmodule
"""

ARRAY_SP = """\
* src: unity buffer; sink: RC low-pass (10k into 1p)
.subckt src a vo
e1 vo 0 a 0 1
.ends
.subckt sink a q
r1 a q 10k
c1 q 0 1p
.ends
.tran 1n 400n
"""


def array_files(src_a: str) -> Dict[str, str]:
    init = ("choose xa cells.sp;\n"
            "port_dir -cell src (%s a; output vo);\n"
            "port_dir -cell sink (input a; output q);\n"
            "d2a hiv=1.8 lov=0 node=tb.clk;\n"
            "a2d loth=0.6 hith=0.6 node=*;\n" % src_a)
    return {"tb.sv": ARRAY_TB, "cells.sp": ARRAY_SP, "vcsAD.init": init}


class _ArrayPortBuf(_Builds):
    BUILDS = {"input": array_files("input"), "inout": array_files("inout")}

    def test_buffer_feeds_every_element(self):
        vhd = self.need("inout").design()
        maps = dict(re.findall(r"^\s*(we\d): entity work\.\w+\n\s*port map \((.*?)\);",
                               vhd, re.M | re.S))
        self.assertEqual(sorted(maps), ["we0", "we1"])
        for label, assoc in maps.items():
            self.assertRegex(assoc, r"\ba => PB_we0_a\b", label)
        self.assertIn("PB_we0_a <= clk;", vhd)

    def test_same_interface_elements(self):
        ents = ie_entries(self.need("inout").report())
        self.assertEqual(ents, ie_entries(self.need("input").report()))
        self.assertTrue(any(k == "d2a" for k, _, _ in ents), ents)

    def test_same_run(self):
        ev = events(self.need("inout").sim.stdout)
        self.assertEqual([e for e in ev if e[0] > 0],
                         [e for e in events(self.need("input").sim.stdout) if e[0] > 0])
        # both elements' cells follow clk: qe rises to 11 and falls to 00
        self.assertIn("qe=11", [v for _, v in ev])
        self.assertIn("qe=00", [v for t, v in ev if t > 100000])


@needs_ams
class TestE2EArrayPortBufVacask(_ArrayPortBuf, unittest.TestCase):
    ENGINE = "vacask"


@needs_ams
class TestE2EArrayPortBufXyce(_ArrayPortBuf, unittest.TestCase):
    ENGINE = "xyce"


# =============================================================================
# TB-04 / TB-11 / TB-10: real expressions on a real port of a multi-view cell
# =============================================================================

REAL_TB = """\
`timescale 1ns/1ps
module amp(input real vin, output real vout);
  assign vout = vin * 2.0;
endmodule
module tb;
  real r1 = 0.1;
  reg [3:0] code = 4'd3;
  wire real rv, nd;
  real o1, o2, o3, o4, o5, o6, o7;
  assign rv = r1 + 0.2;
  amp u1 (.vin(r1 + 0.2), .vout(o1));
  amp u2 (.vin(-r1), .vout(o2));
  amp u3 (.vin(r1 / 2.0), .vout(o3));
  amp u4 (.vin(r1 * 2.0), .vout(o4));
  amp u5 (.vin(rv), .vout(o5));
  amp u6 (.vin(code * 0.1), .vout(o6));
  amp u7 (.vin(nd), .vout(o7));
  initial begin
    #10 r1 = 0.25; code = 4'd7;
    #10 $display("%0t o=%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f", $time, o1, o2, o3, o4, o5, o6, o7);
    $finish;
  end
endmodule
"""

REAL_SP = """\
* real-port amplifier, gain 2
.subckt amp vin vout
e1 vout 0 vin 0 2
.ends
.tran 0.1n 30n
"""

REAL_FILES = {"tb.sv": REAL_TB, "cells.sp": REAL_SP,
              "vcsAD.init": "choose xa cells.sp;\nuse_spice -cell amp;\n"}


class _RealPortExpr(_Builds):
    BUILDS = {"real": REAL_FILES}

    def test_values(self):
        out = self.need("real").sim.stdout
        m = re.search(r"^20000 o=(\S+)$", out, re.M)
        self.assertTrue(m, out)
        got = [float(v) for v in m.group(1).split(",")]
        want = [0.9, -0.5, 0.25, 1.0, 0.9, 1.4, 0.0]
        for g, w, what in zip(got, want, ("r1+0.2", "-r1", "r1/2", "r1*2", "rv", "code*0.1",
                                          "undriven")):
            self.assertAlmostEqual(g, w, delta=0.002, msg="%s: %s (%s)" % (what, got, out))

    def test_no_logic3d_real_arithmetic(self):
        vhd = self.need("real").design()
        self.assertNotRegex(vhd, r"real_to_l3d1\(\w+\) [-+*/] real_to_l3d1")
        self.assertNotIn("nd <= L3D_Z;", vhd)


@needs_ams
class TestE2ERealPortExprVacask(_RealPortExpr, unittest.TestCase):
    ENGINE = "vacask"


@needs_ams
class TestE2ERealPortExprXyce(_RealPortExpr, unittest.TestCase):
    ENGINE = "xyce"


# =============================================================================
# TB-05 / TB-12: a SPICE-only cell's auto bus port on a concatenation
# =============================================================================

CONCAT_TB = """\
`timescale 1ns/1ps
module tb;
  reg x = 0, z = 0;
  wire p, q, r, s;
  wire w3, w2, w1, w0;
  wire dout;
  assign w3 = x;
  assign w2 = 1'b0;
  assign w1 = ~x;
  assign w0 = 1'b1;
  quad u1 (.a({1'b1, x, 1'b0, z}), .y({p, q, r, s}));
  dac4 d1 (.d({w3, w2, w1, w0}), .out(dout));
  initial begin
    #10 x = 1;
    #20 $display("%0t p=%b%b%b%b", $time, p, q, r, s);
    $finish;
  end
endmodule
"""

CONCAT_SP = """\
* quad buffer with bus ports; 4-bit weighted DAC with a bus input
.subckt quad a<3> a<2> a<1> a<0> y<3> y<2> y<1> y<0>
e3 y<3> 0 a<3> 0 1
e2 y<2> 0 a<2> 0 1
e1 y<1> 0 a<1> 0 1
e0 y<0> 0 a<0> 0 1
.ends
.subckt dac4 d<3> d<2> d<1> d<0> out
e0 out 0 vol='(v(d<3>)*8 + v(d<2>)*4 + v(d<1>)*2 + v(d<0>))/15'
.ends
.tran 0.1n 40n
"""


def concat_files(port_dirs: bool) -> Dict[str, str]:
    init = "choose xa cells.sp;\nbus_format <%d>;\n"
    if port_dirs:
        init += "port_dir -cell quad (input a; output y);\nport_dir -cell dac4 (input d);\n"
    return {"tb.sv": CONCAT_TB, "cells.sp": CONCAT_SP, "vcsAD.init": init}


class _ConcatAuto(_Builds):
    BUILDS = {"auto": concat_files(False), "dirs": concat_files(True)}

    def test_runs_like_port_dir(self):
        out = self.need("auto").sim.stdout
        self.assertIn("30000 p=1100", out)
        self.assertIn("30000 p=1100", self.need("dirs").sim.stdout)

    def test_individual_association(self):
        vhd = self.need("auto").design()
        m = re.search(r"^\s*u1: entity work\.\w+\n\s*port map \((.*?)\);", vhd, re.M | re.S)
        self.assertTrue(m, vhd)
        for k, net in ((3, "p"), (2, "q"), (1, "r"), (0, "s")):
            self.assertRegex(m.group(1), r"\by\(%d\) => %s\b" % (k, net))

    def test_same_interface_elements_as_port_dir(self):
        # the auto buses on concatenations get what port_dir input/output gives: a D2A
        # per DAC and quad input bit, an A2D per quad output bit
        auto = ie_entries(self.need("auto").report())
        self.assertEqual(auto, ie_entries(self.need("dirs").report()))
        nodes = [(k, n) for k, n, _ in auto]
        for b in range(4):
            self.assertIn(("d2a", "tb.d1.d<%d>" % b), nodes)
            self.assertIn(("d2a", "tb.u1.a<%d>" % b), nodes)
            self.assertIn(("a2d", "tb.u1.y<%d>" % b), nodes)


@needs_ams
class TestE2EConcatAutoVacask(_ConcatAuto, unittest.TestCase):
    ENGINE = "vacask"


@needs_ams
class TestE2EConcatAutoXyce(_ConcatAuto, unittest.TestCase):
    ENGINE = "xyce"


if __name__ == "__main__":
    unittest.main()
