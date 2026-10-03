"""End-to-end vcs-ams tests: Verilog-A, shared parents, through-nets, vector bits
and reserved port names (docs/VAMOS_AMS_DESIGN.md §9, e2e items 9, 10, 11, 13, 14).

Every class compiles its design once with `vcs-ams --vamos-analog=<engine>` and
runs `./simv` once (setUpClass); its tests only read the results: the exit codes,
the compile and run output ($display lines, end-of-run line, warnings), the IE
report, the boundary file, ams/ams.json (the plan), the emitted deck and the
published rawfile.  Each item has one class per engine (VACASK and Xyce), except
item 9, which runs on VACASK (Xyce refuses a parameterised .hdl, which is checked
too).

Timing notes, used by the tolerances below:
  - the gated D2A has a 500.7 ohm series resistance, which adds to the RC of a
    cell whose input is resistive;
  - an A2D event is raised at the first accepted analog step past the threshold,
    and the translated `$time` truncates to the time unit (vvp rounds), so a
    displayed time can be up to ~1 ns early or late against the analytic crossing.

Run (WSL):  python3 -m unittest -v test_ams_e2e_hier   (from tests/vamos)
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

from ams_e2e_lib import SHIMS, TIMEOUT, AmsCase, engines_available, needs_ams

TOL_NS = 1.5          # displayed digital event time vs the analytic crossing
TOL_RAW_NS = 0.4      # interpolated rawfile crossing vs the analytic crossing
D2A_R = 500.7         # series resistance of the gated D2A (rmap strength 6)


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


def rc_cross(t_edge_ns: float, tau_ns: float, v_final: float, thr: float, rising: bool) -> float:
    """Analytic threshold crossing of a first-order step response (ns)."""
    if rising:
        return t_edge_ns + tau_ns * math.log(v_final / (v_final - thr))
    return t_edge_ns + tau_ns * math.log(v_final / thr)


def ie_entries(report: str) -> List[Tuple[str, str, Dict[str, str]]]:
    """(kind, node, {key: value}) for every d2a/a2d line of an IE report."""
    out = []
    for ln in report.splitlines():
        m = re.match(r"^(d2a|a2d)\s+(.*?)\s*\bnode=(\S+?)\s*;\s*$", ln)
        if m:
            keys = {}
            for kv in m.group(2).split():
                k, _, v = kv.partition("=")
                keys[k] = v
            out.append((m.group(1), m.group(3), keys))
    return out


def report_block(report: str, node: str) -> List[str]:
    """The lines of a node's IE-report entry (entry lines + comments, up to a blank line)."""
    lines = report.splitlines()
    for i, ln in enumerate(lines):
        if ln.rstrip().endswith("node=%s;" % node) or ln.startswith("// node=%s:" % node):
            j = i
            while j > 0 and lines[j - 1].strip() and not lines[j - 1].startswith("//"):
                j -= 1                                   # a BIDIR entry has two IE lines
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


def events(out: str, tag: str) -> List[Tuple[int, str]]:
    """(time in ps, value) from `$display("%0t <tag>=%b", $time, x)` lines."""
    ev = []
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].startswith(tag + "="):
            ev.append((int(parts[0]), parts[1][len(tag) + 1:]))
    return ev


def settle(ev: List[Tuple[int, str]]) -> Tuple[Optional[str], List[Tuple[int, str]]]:
    """(value settled at t=0, later changes with repeats dropped)."""
    init = None
    later: List[Tuple[int, str]] = []
    for t, v in ev:
        if t == 0:
            init = v
            continue
        prev = later[-1][1] if later else init
        if v != prev:
            later.append((t, v))
    return init, later


def bit_edges(ev: List[Tuple[int, str]], bit: int) -> Tuple[Optional[str], List[Tuple[int, str]]]:
    """(bit value settled at t=0, [(t ps, new bit value)]) for bit `bit` (0 = LSB)."""
    init, later = settle(ev)
    first = init[-1 - bit] if init else None
    out = []
    prev = first
    for t, v in later:
        b = v[-1 - bit]
        if b != prev:
            out.append((t, b))
            prev = b
    return first, out


# =============================================================================
# shared compile + run
# =============================================================================

class SharedRun(AmsCase):
    """One `vcs-ams` compile and one `./simv` run per class, read by every test.

    Subclasses set ENGINE, FILES ({relative path: text}), COMPILE_ARGS and RUN.
    """
    ENGINE = ""
    FILES: Dict[str, str] = {}
    COMPILE_ARGS: Tuple[str, ...] = ("-sverilog", "tb.sv")
    RUN = True

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if cls.ENGINE not in engines_available():
            raise unittest.SkipTest("analog engine %s is not available" % cls.ENGINE)
        cls.wd = tempfile.mkdtemp(prefix="vamos-e2e-hier-")
        for rel, text in cls.FILES.items():
            p = os.path.join(cls.wd, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write(text)
        cls.comp = _run(["vcs-ams"] + list(cls.COMPILE_ARGS) + ["--vamos-analog=" + cls.ENGINE],
                        cls.wd)
        cls.sim = None
        if cls.RUN and cls.comp.returncode == 0:
            cls.sim = _run(["./simv"], cls.wd)

    @classmethod
    def tearDownClass(cls) -> None:
        d = getattr(cls, "wd", None)
        if d:
            if os.environ.get("VAMOS_TEST_KEEP"):
                sys.stderr.write("kept %s\n" % d)
            else:
                shutil.rmtree(d, ignore_errors=True)
        super().tearDownClass()

    # -- preconditions -----------------------------------------------------------------

    def need_compile(self) -> None:
        self.assertEqual(self.comp.returncode, 0, "vcs-ams failed (%s):\n%s"
                         % (self.ENGINE, self.comp.stdout[-6000:]))

    def need_run(self) -> str:
        self.need_compile()
        self.assertIsNotNone(self.sim)
        self.assertEqual(self.sim.returncode, 0, "./simv failed (%s):\n%s"
                         % (self.ENGINE, self.sim.stdout[-6000:]))
        return self.sim.stdout

    # -- artefacts ---------------------------------------------------------------------

    def text(self, rel: str) -> str:
        with open(os.path.join(self.wd, rel), encoding="utf-8", errors="replace") as fh:
            return fh.read()

    def init_line(self, needle: str) -> int:
        """1-based line of the control file holding `needle`."""
        for k, ln in enumerate(self.FILES["vcsAD.init"].splitlines(), 1):
            if needle in ln:
                return k
        raise AssertionError("%r is not in the control file" % needle)

    def ie_report(self) -> str:
        self.need_compile()
        return self.text(os.path.join("simv.msv", "interface_element.rpt"))

    def boundary(self) -> List[List[str]]:
        self.need_compile()
        return [ln.split() for ln in self.text("simv.daidir/ams/vamos.boundary").splitlines()
                if ln.strip()]

    def plan(self) -> dict:
        self.need_compile()
        return json.loads(self.text("simv.daidir/ams/ams.json"))

    def deck(self) -> str:
        self.need_compile()
        name = "vamos.sim" if self.ENGINE == "vacask" else "vamos.cir"
        return self.text(os.path.join("simv.daidir", "ams", "deck", name))

    def record(self) -> dict:
        self.need_compile()
        return json.loads(self.text("simv.daidir/vamos.job.json"))["ams"]

    def rawfile(self):
        self.need_run()
        return self.raw(self.wd)

    def node(self, alias: str) -> dict:
        """The plan node whose canonical name or aliases include `alias`."""
        for n in self.plan()["nodes"]:
            if n["canonical"] == alias or alias in n["aliases"]:
                return n
        self.fail("no analog node named %s in ams.json" % alias)

    def roles(self) -> Dict[str, str]:
        return {n["canonical"]: n["role"] for n in self.plan()["nodes"]}

    # -- assertions --------------------------------------------------------------------

    def assert_edges(self, got: List[Tuple[int, str]], want: List[Tuple[float, str]],
                     what: str, tol_ns: float = TOL_NS) -> None:
        """got: [(t ps, value)] from the display; want: [(t ns, value)]."""
        self.assertEqual([v for _, v in got], [v for _, v in want],
                         "%s (%s): values %s, want %s" % (what, self.ENGINE, got, want))
        for (tg, _), (tw, _) in zip(got, want):
            self.assertAlmostEqual(tg / 1000.0, tw, delta=tol_ns,
                                   msg="%s (%s): edge at %g ns, want %.2f ns; all %s"
                                   % (what, self.ENGINE, tg / 1000.0, tw, got))

    def assert_crossings(self, raw, node: str, thr: float, want_ns: List[float], what: str,
                         tol_ns: float = TOL_RAW_NS) -> None:
        got = [t * 1e9 for t in raw.crossings(node, thr)]
        self.assertEqual(len(got), len(want_ns), "%s (%s): crossings of %s at %g V: %s, want %s"
                         % (what, self.ENGINE, node, thr, got, want_ns))
        for g, w in zip(got, want_ns):
            self.assertAlmostEqual(g, w, delta=tol_ns, msg="%s (%s): %s crosses %g V at %g ns, "
                                   "want %.2f ns" % (what, self.ENGINE, node, thr, g, w))

    def assert_never_unknown(self, out: str, tags: Sequence[str]) -> None:
        for tag in tags:
            bad = [(t, v) for t, v in events(out, tag) if t > 0 and re.search(r"[xXzZ]", v)]
            self.assertEqual(bad, [], "%s is X/Z after t=0 (%s)" % (tag, self.ENGINE))

    def assert_digital_stop(self, out: str, t_s: str) -> None:
        self.assertIn("co-simulation finished: digital stop at %s s" % t_s, out)
        self.assertTrue(os.path.isfile(os.path.join(self.wd, "vamos_ams.raw")),
                        "no vamos_ams.raw published")

    def assert_no_engine_chatter(self, out: str) -> None:
        """§6: engine chatter about vamos's own bridge nodes never reaches the user."""
        bad = [ln for ln in out.splitlines()
               if ln.startswith("Netlist warning") or ln.strip() == "Terminal"]
        self.assertEqual(bad, [], "engine chatter in the ./simv output (%s)" % self.ENGINE)


# =============================================================================
# e2e 9: a .hdl Verilog-A resistor (VACASK)
# =============================================================================

VRES_VA = """\
// A Verilog-A resistor (vcs-ams e2e 9).
`include "disciplines.vams"

module vres(a, b);
    inout a, b;
    electrical a, b;
    parameter real r = 2000.0 from (0:inf);
    analog I(a, b) <+ V(a, b) / r;
endmodule
"""

# The netlist and the .va live in analog/: `.hdl "vres.va"` resolves against the
# including file's directory (§4.3.1), not the vcs cwd.
CELL9_SP = """\
* Verilog-A resistors in a SPICE cell
.hdl "vres.va"
.model rmod vres r=3k
.subckt vadiv in out out2
e1 buf 0 in 0 1
* r=1k on the instance line: out = 3k/(1k+3k) = 0.75 in (the 2k default: 0.6 in)
xr1 buf out vres r=1k
r2 out 0 3k
c2 out 0 2p
* r=3k from the model card: out2 = 1k/(3k+1k) = 0.25 in (the 2k default: 0.333 in)
xr2 buf out2 rmod
r3 out2 0 1k
.ends
.tran 1n 400n
"""

TB9 = """\
`timescale 1ns/1ps
module tb;
  reg clk = 0;
  wire o, o2;
  always #50 clk = ~clk;
  vadiv u1 (.in(clk), .out(o), .out2(o2));
  always @(o) $display("%0t o=%b", $time, o);
  always @(o2) $display("%0t o2=%b", $time, o2);
  initial #400 $finish;
endmodule
"""

INIT9 = """\
choose xa analog/cell.sp;
d2a hiv=1.8 lov=0 node=tb.u1.in;
a2d loth=0.9 hith=0.9 node=tb.u1.out;
a2d loth=0.3 hith=0.3 node=tb.u1.out2;
"""

FILES9 = {"tb.sv": TB9, "vcsAD.init": INIT9, "analog/cell.sp": CELL9_SP, "analog/vres.va": VRES_VA}


@needs_ams
class TestE2E09HdlVacask(SharedRun):
    """e2e 9: a .hdl Verilog-A resistor inside a SPICE cell, on VACASK."""
    ENGINE = "vacask"
    FILES = FILES9

    TAU = 750.0 * 2e-12 * 1e9          # (1k || 3k) * 2p, in ns

    def test_va_compiled_and_loaded(self):
        rec = self.record()
        self.assertEqual(rec["engine"], "vacask")
        self.assertIn("ams/va/1_vres.osdi", rec["osdi"])
        osdi = os.path.join(self.wd, "simv.daidir", "ams", "va", "1_vres.osdi")
        self.assertTrue(os.path.isfile(osdi), "the .hdl file was not compiled at compile time")
        deck = self.deck()
        loads = re.findall(r'(?m)^load\s+"([^"]+)"', deck)
        # the daidir's own .osdi files load relative to the deck (a copied daidir loads its own)
        deck_dir = os.path.join(self.wd, "simv.daidir", "ams", "deck")
        self.assertTrue(any(os.path.realpath(os.path.join(deck_dir, p)) == os.path.realpath(osdi)
                            for p in loads), "the deck does not load %s: %s" % (osdi, loads))
        self.assertFalse([p for p in loads if os.path.isabs(p) and "/ams/va/" in p], loads)
        # one card per VA instance: the instance-line value and the .model card value
        self.assertRegex(deck, r"\bvres\s+r=1000(\.0*)?\b")
        self.assertRegex(deck, r"\bvres\s+r=3000(\.0*)?\b")

    def test_divider_levels(self):
        raw = self.rawfile()
        self.assertAlmostEqual(raw.at("n_u1_in", 45e-9), 0.0, delta=0.01)
        self.assertAlmostEqual(raw.at("n_u1_in", 95e-9), 1.8, delta=0.01)
        # parameters honoured: 0.75 * 1.8 and 0.25 * 1.8 (defaults would give 1.08 / 0.6)
        self.assertAlmostEqual(raw.at("n_u1_out", 95e-9), 1.35, delta=0.01)
        self.assertAlmostEqual(raw.at("n_u1_out2", 95e-9), 0.45, delta=0.005)
        self.assertAlmostEqual(raw.at("n_u1_out", 145e-9), 0.0, delta=0.01)
        self.assertAlmostEqual(raw.at("n_u1_out2", 145e-9), 0.0, delta=0.005)

    def test_rc_crossings(self):
        raw = self.rawfile()
        want = []
        for k in range(4):
            want.append(rc_cross(50 + 100 * k, self.TAU, 1.35, 0.9, True))
            if k < 3:
                want.append(rc_cross(100 + 100 * k, self.TAU, 1.35, 0.9, False))
        self.assert_crossings(raw, "n_u1_out", 0.9, want, "VA divider RC")

    def test_digital(self):
        out = self.need_run()
        init, got = settle(events(out, "o"))
        self.assertEqual(init, "0")
        want = []
        for k in range(4):
            want.append((rc_cross(50 + 100 * k, self.TAU, 1.35, 0.9, True), "1"))
            if k < 3:
                want.append((rc_cross(100 + 100 * k, self.TAU, 1.35, 0.9, False), "0"))
        self.assert_edges(got, want, "o")
        init, got = settle(events(out, "o2"))
        self.assertEqual(init, "0")
        want2 = []
        for k in range(4):
            want2.append((50.0 + 100 * k, "1"))
            if k < 3:
                want2.append((100.0 + 100 * k, "0"))
        self.assert_edges(got, want2, "o2")
        self.assert_never_unknown(out, ["o", "o2"])

    def test_ie_report(self):
        ents = ie_entries(self.ie_report())
        self.assertEqual(sorted((k, n) for k, n, _ in ents),
                         [("a2d", "tb.u1.out"), ("a2d", "tb.u1.out2"), ("d2a", "tb.u1.in")])
        d = {(k, n): v for k, n, v in ents}
        self.assertEqual(float(d[("d2a", "tb.u1.in")]["hiv"]), 1.8)
        self.assertEqual(float(d[("d2a", "tb.u1.in")]["lov"]), 0.0)
        self.assertEqual(float(d[("a2d", "tb.u1.out")]["hith"]), 0.9)
        self.assertEqual(float(d[("a2d", "tb.u1.out2")]["loth"]), 0.3)

    def test_end_of_run(self):
        out = self.need_run()
        self.assert_digital_stop(out, "4e-07")


@needs_ams
class TestE2E09HdlXyceRefuses(SharedRun):
    """e2e 9 on Xyce: PyMS ignores Verilog-A parameter overrides, so the compile refuses
    the design with the instance and its origin (§4.5) instead of simulating 2k resistors."""
    ENGINE = "xyce"
    FILES = FILES9
    RUN = False

    def test_refused_with_origin(self):
        out = self.comp.stdout
        self.assertNotEqual(self.comp.returncode, 0, out)
        self.assertRegex(out, r"analog/cell\.sp:7: xr1: Verilog-A parameters \(r\) on Xyce")
        self.assertRegex(out, r"analog/cell\.sp:11: xr2: Verilog-A parameters \(r\) on Xyce")
        self.assertFalse(os.path.exists(os.path.join(self.wd, "simv.msv", "interface_element.rpt")))


# =============================================================================
# e2e 10: a wrapper holding a SPICE cell, instantiated twice (shared parent)
# =============================================================================

# w1.u3 is driven by clk2 and hosts its own D2A and A2D.  w2.u3 shares both of its
# nets with u1 (clk in, yb out): yb is a digitally read net on which another cut
# instance's output (u1.y) hosts the A2D, so on the w2 path both bits are passive.
# No port_dir: the direction probe makes `a` an input (u1's actual is a reg).
TB10 = """\
`timescale 1ns/1ps
module tb;
  reg clk = 1'b0;
  reg clk2 = 1'b0;
  wire yb, y3;
  always #50 clk = ~clk;
  always #75 clk2 = ~clk2;
  rc_sp u1 (.a(clk), .y(yb));
  wrap w1 (.a(clk2), .y(y3));
  wrap w2 (.a(clk), .y(yb));
  always @(yb) $display("%0t yb=%b", $time, yb);
  always @(y3) $display("%0t y3=%b", $time, y3);
  initial #600 $finish;
endmodule

module wrap (input a, output y);
  rc_sp u3 (.a(a), .y(y));
endmodule
"""

RC10_SP = """\
* RC low-pass: 10k into 1p
.subckt rc_sp a y
r1 a y 10k
c1 y 0 1p
.ends
.tran 1n 600n
"""

# Per-path levels on the shared wrapper.  The w2 rules name w2.u3's (passive) ports;
# they reach the nodes u1 hosts, because a cell/inst selector matches any cut port
# on the node (§3.2).
INIT10 = """\
choose xa rc.sp;
d2a inst=tb.w1.u3 port=a hiv=3.0 lov=0;
d2a inst=tb.w2.u3 port=a hiv=1.8 lov=0;
a2d inst=tb.w1.u3 port=y loth=1.5 hith=1.5;
a2d inst=tb.w2.u3 port=y loth=0.9 hith=0.9;
"""

FILES10 = {"tb.sv": TB10, "rc.sp": RC10_SP, "vcsAD.init": INIT10}


class _Item10:
    """e2e 10 assertions (mixed into one class per engine)."""
    FILES = FILES10
    # yb: two 10k in parallel from the clk node (driven through the D2A's 500.7 ohm)
    # into 2 x 1p; y3: 10k + 500.7 into 1p
    TAU_YB = (5000.0 + D2A_R) * 2e-12 * 1e9
    TAU_Y3 = (10000.0 + D2A_R) * 1e-12 * 1e9

    def test_ie_report_per_path(self):
        rpt = self.ie_report()
        ents = {(k, n): v for k, n, v in ie_entries(rpt)}
        self.assertEqual(sorted(ents), [("a2d", "tb.u1.y"), ("a2d", "tb.w1.u3.y"),
                                        ("d2a", "tb.u1.a"), ("d2a", "tb.w1.u3.a")])
        self.assertEqual(float(ents[("d2a", "tb.u1.a")]["hiv"]), 1.8)        # the w2 rule
        self.assertEqual(float(ents[("d2a", "tb.w1.u3.a")]["hiv"]), 3.0)     # the w1 rule
        self.assertEqual(float(ents[("a2d", "tb.u1.y")]["hith"]), 0.9)
        self.assertEqual(float(ents[("a2d", "tb.w1.u3.y")]["hith"]), 1.5)
        self.assertIn("// All Boundary Nets tb.u1.a tb.w2.u3.a", report_block(rpt, "tb.u1.a"))
        self.assertIn("// All Boundary Nets tb.u1.y tb.w2.u3.y", report_block(rpt, "tb.u1.y"))

    def test_roles_per_path(self):
        p = self.plan()
        insts = p["instances"]
        hosts = sorted(insts[n["host"]["inst"]]["vpath"] for n in p["nodes"] if n["host"])
        self.assertEqual(hosts, ["tb.u1", "tb.u1", "tb.w1.u3", "tb.w1.u3"])
        # w2.u3 hosts nothing: none of its bridge signals is in the boundary file
        paths = [b[1] for b in self.boundary()]
        self.assertEqual(len(paths), 6, paths)
        self.assertFalse([x for x in paths if x.startswith(".w2.")], paths)
        self.assertEqual(sum(1 for b in self.boundary() if b[0] == "A2D"), 2)

    def test_one_clone_for_three_paths(self):
        cut = self.text("simv.daidir/ams/cut.vhd")
        clones = re.findall(r"(?im)^entity\s+(\S+__vams)\s+is", cut)
        self.assertEqual(len(clones), 1, clones)
        tbl = re.search(r"VAMS_PATHS_\d+\s*:\s*\S+\s*:=\s*\(([^;]*)\)", cut)
        self.assertIsNotNone(tbl)
        paths = sorted(s.strip() for s in re.findall(r'"([^"]*)"', tbl.group(1)))
        self.assertEqual(paths, [":tb:u1:", ":tb:w1:u3:", ":tb:w2:u3:"])

    def test_deck_shares_nodes(self):
        x = deck_x_nodes(self.deck())
        self.assertEqual(x["xv_w2_u3"], x["xv_u1"])
        self.assertNotEqual(x["xv_w1_u3"], x["xv_u1"])

    def test_analog_levels_per_path(self):
        raw = self.rawfile()
        self.assertAlmostEqual(raw.at("n_u1_a", 95e-9), 1.8, delta=0.01)
        self.assertAlmostEqual(raw.at("n_u1_a", 45e-9), 0.0, delta=0.01)
        self.assertAlmostEqual(raw.at("n_w1_u3_a", 145e-9), 3.0, delta=0.01)
        self.assertAlmostEqual(raw.at("n_u1_y", 99e-9), 1.8, delta=0.05)
        self.assertAlmostEqual(raw.at("n_w1_u3_y", 149e-9), 3.0, delta=0.05)
        want_yb = []
        for k in range(6):
            want_yb.append(rc_cross(50 + 100 * k, self.TAU_YB, 1.8, 0.9, True))
            if k < 5:
                want_yb.append(rc_cross(100 + 100 * k, self.TAU_YB, 1.8, 0.9, False))
        self.assert_crossings(raw, "n_u1_y", 0.9, want_yb, "yb")
        want_y3 = []
        for k in range(4):
            want_y3.append(rc_cross(75 + 150 * k, self.TAU_Y3, 3.0, 1.5, True))
            if k < 3:
                want_y3.append(rc_cross(150 + 150 * k, self.TAU_Y3, 3.0, 1.5, False))
        self.assert_crossings(raw, "n_w1_u3_y", 1.5, want_y3, "y3")

    def test_both_nets_toggle_never_x(self):
        out = self.need_run()
        init, got = settle(events(out, "yb"))
        self.assertEqual(init, "0")
        want = []
        for k in range(6):
            want.append((rc_cross(50 + 100 * k, self.TAU_YB, 1.8, 0.9, True), "1"))
            if k < 5:
                want.append((rc_cross(100 + 100 * k, self.TAU_YB, 1.8, 0.9, False), "0"))
        self.assert_edges(got, want, "yb")
        init, got = settle(events(out, "y3"))
        self.assertEqual(init, "0")
        want = []
        for k in range(4):
            want.append((rc_cross(75 + 150 * k, self.TAU_Y3, 3.0, 1.5, True), "1"))
            if k < 3:
                want.append((rc_cross(150 + 150 * k, self.TAU_Y3, 3.0, 1.5, False), "0"))
        self.assert_edges(got, want, "y3")
        self.assert_never_unknown(out, ["yb", "y3"])
        self.assert_digital_stop(out, "6e-07")

    def test_variable_warning(self):
        # a reg joining two SPICE ports: VCS digitises each connection (§5.4 warning)
        self.need_compile()
        self.assertIn("variable tb.clk joins SPICE ports in analog", self.comp.stdout)

    def test_no_engine_chatter(self):
        # Xyce wraps its "Netlist warning: Voltage Node (N_W1_U3_A_E) connected to only
        # 1 device" message onto a second line (" Terminal") for longer node names:
        # neither line may reach the user.
        self.assert_no_engine_chatter(self.need_run())


@needs_ams
class TestE2E10SharedParentVacask(_Item10, SharedRun):
    ENGINE = "vacask"


@needs_ams
class TestE2E10SharedParentXyce(_Item10, SharedRun):
    ENGINE = "xyce"


# =============================================================================
# e2e 11: through-nets (_Readable shadows, comb_fused copies, port temporaries)
# =============================================================================

TB11 = """\
`timescale 1ns/1ps
// (a) cell -> wrapper output -> second cell (tgt-vhdl _Readable shadow)
// (b) bandgap vref -> ADC inside one module; vref is also that module's output
// (c) cell output -> v[0] -> second cell; a cell output on w[5:4] read by a cell
//     on w[4] (tgt-vhdl port temporaries)
// +define+READERS: ve and v are also read digitally.
module tb;
  reg clk = 1'b0;
  always #50 clk = ~clk;
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
  always @(qe) $display("%0t qe=%b", $time, qe);
  always @(q) $display("%0t q=%b", $time, q);
`ifdef READERS
  always @(ve) $display("%0t ve=%b", $time, ve);
  always @(v) $display("%0t v=%b", $time, v);
`endif
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

CELLS11_SP = """\
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

PORT_DIRS11 = """\
port_dir -cell src (input a; output vo);
port_dir -cell sink (input a; output q);
port_dir -cell bg (output vref);
port_dir -cell adc (input vin, clk; output q);
port_dir -cell cout (input a; output y);
port_dir -cell cout2 (input a; output y);
port_dir -cell cin (input a);
"""

LEVELS11 = """\
d2a hiv=1.8 lov=0 node=tb.clk;
a2d loth=0.6 hith=0.6 node=*;
"""

FILES11 = {"tb.sv": TB11, "cells.sp": CELLS11_SP,
           "vcsAD.init": "choose xa cells.sp;\n" + PORT_DIRS11 + LEVELS11}
FILES11_AUTO = {"tb.sv": TB11, "cells.sp": CELLS11_SP,
                "vcsAD.init": "choose xa cells.sp;\n" + LEVELS11}

# The through-nets: (alias, canonical, (xv instance, its port index), (xv, index))
THROUGH11 = [
    ("tb.ve", "tb.ue.a", ("xv_we_u", 1), ("xv_ue", 0)),              # (a) src.vo / sink.a
    ("tb.vref", "tb.bg.u1.vref", ("xv_bg_u1", 0), ("xv_bg_u2", 0)),  # (b) bg.vref / adc.vin
    ("tb.v[0]", "tb.c3.y", ("xv_c3", 1), ("xv_c4", 0)),              # (c) cout.y / cin.a
    ("tb.w[4]", "tb.c5.y[0]", ("xv_c5", 2), ("xv_c6", 0)),           # (c) cout2.y[0] / cin.a
]


class _Item11Common:
    """Assertions shared by every e2e 11 variant."""
    TAU_Q = 10.0                       # sink: 10k * 1p behind an ideal buffer

    def test_one_deck_node_per_through_net(self):
        x = deck_x_nodes(self.deck())
        for alias, _, (xa, ia), (xb, ib) in THROUGH11:
            self.assertIn(xa, x, sorted(x))
            self.assertIn(xb, x, sorted(x))
            self.assertEqual(x[xa][ia], x[xb][ib], "%s: %s and %s are on different deck nodes"
                             % (alias, xa, xb))
            self.assertEqual(x[xa][ia], self.node(alias)["name"])

    def test_analog_through_nets(self):
        raw = self.rawfile()
        self.assertAlmostEqual(raw.at("n_ue_a", 95e-9), 1.8, delta=0.01)      # ve follows clk
        self.assertAlmostEqual(raw.at("n_ue_a", 45e-9), 0.0, delta=0.01)
        # vref preserved: 1.2 V for the whole run
        vref = raw.column("n_bg_u1_vref")
        self.assertAlmostEqual(min(vref), 1.2, delta=0.002)
        self.assertAlmostEqual(max(vref), 1.2, delta=0.002)
        self.assertAlmostEqual(raw.at("n_bg_u2_q", 45e-9), 1.2, delta=0.01)
        self.assertAlmostEqual(raw.at("n_bg_u2_q", 95e-9), -0.6, delta=0.01)
        # one node per through-net: the second cell's 10k load halves the first cell's
        # 10k output (two separate nodes would leave 1.8 V and 0 V)
        self.assertAlmostEqual(raw.at(self.node("tb.v[0]")["name"], 95e-9), 0.9, delta=0.01)
        self.assertAlmostEqual(raw.at(self.node("tb.w[4]")["name"], 95e-9), 0.9, delta=0.01)
        self.assertAlmostEqual(raw.at(self.node("tb.w[5]")["name"], 95e-9), 1.8, delta=0.01)
        self.assertAlmostEqual(raw.at(self.node("tb.v[0]")["name"], 45e-9), 0.0, delta=0.01)

    def test_readers_toggle(self):
        out = self.need_run()
        init, got = settle(events(out, "qe"))
        self.assertEqual(init, "0")
        want = []
        for k in range(4):
            want.append((rc_cross(50 + 100 * k, self.TAU_Q, 1.8, 0.6, True), "1"))
            if k < 3:
                want.append((rc_cross(100 + 100 * k, self.TAU_Q, 1.8, 0.6, False), "0"))
        self.assert_edges(got, want, "qe")
        init, got = settle(events(out, "q"))
        self.assertEqual(init, "1")
        want = []
        for k in range(4):
            want.append((50.0 + 100 * k, "0"))
            if k < 3:
                want.append((100.0 + 100 * k, "1"))
        self.assert_edges(got, want, "q")
        self.assert_never_unknown(out, ["qe", "q"])
        self.assert_digital_stop(out, "4e-07")


class _Item11NoReaders(_Item11Common):
    def test_through_nets_have_no_ie(self):
        roles = self.roles()
        rpt = self.ie_report()
        ents = ie_entries(rpt)
        for alias, canon, _, _ in THROUGH11:
            n = self.node(alias)
            self.assertEqual(n["canonical"], canon)
            self.assertEqual(n["role"], "THROUGH", "%s: %s" % (alias, roles))
            self.assertFalse([e for e in ents if e[1] == canon], "%s has an IE: %s" % (canon, ents))
            self.assertIn("// node=%s: through-net (analog only, no interface element)" % canon,
                          rpt.splitlines())
        # the whole design: one D2A (clk) and the two A2Ds of the digital readers
        clk = self.node("tb.clk")
        self.assertEqual(sorted((k, n) for k, n, _ in ents),
                         sorted([("d2a", clk["canonical"]), ("a2d", "tb.bg.u2.q"),
                                 ("a2d", "tb.ue.q")]))
        self.assertEqual(len(self.boundary()), 4)

    def test_no_undriven_warning(self):
        self.need_compile()
        self.assertNotIn("undriven-net constant", self.comp.stdout)


@needs_ams
class TestE2E11ThroughVacask(_Item11NoReaders, SharedRun):
    ENGINE = "vacask"
    FILES = FILES11


@needs_ams
class TestE2E11ThroughXyce(_Item11NoReaders, SharedRun):
    ENGINE = "xyce"
    FILES = FILES11


class _Item11Readers(_Item11Common):
    COMPILE_ARGS = ("-sverilog", "tb.sv", "+define+READERS")

    def test_exactly_one_a2d_each(self):
        ents = ie_entries(self.ie_report())
        for alias, canon in (("tb.ve", "tb.ue.a"), ("tb.v[0]", "tb.c3.y")):
            self.assertEqual(self.node(alias)["role"], "A2D")
            self.assertEqual([(k, n) for k, n, _ in ents if n == canon], [("a2d", canon)])
            a2d = [b for b in self.boundary() if b[0] == "A2D" and b[2] == canon + "__a"]
            self.assertEqual(len(a2d), 1, self.boundary())
        # the other through-nets stay analog only
        for alias in ("tb.vref", "tb.w[4]"):
            self.assertEqual(self.node(alias)["role"], "THROUGH")
        self.assertEqual(len(ents), 5, ents)

    def test_new_readers_toggle(self):
        out = self.need_run()
        init, got = settle(events(out, "ve"))
        self.assertEqual(init, "0")
        want = []
        for k in range(4):
            want.append((50.0 + 100 * k, "1"))
            if k < 3:
                want.append((100.0 + 100 * k, "0"))
        self.assert_edges(got, want, "ve")
        # v[1] has no driver at all; only v[0] (the through-net, now an A2D) is checked
        first, got = bit_edges(events(out, "v"), 0)
        self.assertEqual(first, "0")
        self.assert_edges(got, want, "v[0]")
        bad = [(t, v) for t, v in events(out, "v") if t > 0 and v[-1] not in "01"]
        self.assertEqual(bad, [], "v[0] unknown after t=0")


@needs_ams
class TestE2E11ReadersVacask(_Item11Readers, SharedRun):
    ENGINE = "vacask"
    FILES = FILES11


@needs_ams
class TestE2E11ReadersXyce(_Item11Readers, SharedRun):
    ENGINE = "xyce"
    FILES = FILES11


# The same design with every SPICE port left auto (no port_dir, the VCS default).
# Two auto ports sit on an `input` port of the enclosing module (src.a <- wrap_e.a,
# adc.clk <- bgadc.clk), which tb feeds from the reg clk: the direction probe makes
# them inputs, and clk stays one analog node with one D2A.
class _Item11Auto(_Item11NoReaders):
    def test_auto_directions(self):
        # every port is auto: the report says which direction the probe chose
        blk = report_block(self.ie_report(), "tb.ue.a")
        self.assertTrue([ln for ln in blk if ln.startswith("// direction: auto")
                         and ln.endswith(" tb.we.u.vo")], blk)


@needs_ams
class TestE2E11AutoPortsVacask(_Item11Auto, SharedRun):
    ENGINE = "vacask"
    FILES = FILES11_AUTO


@needs_ams
class TestE2E11AutoPortsXyce(_Item11Auto, SharedRun):
    ENGINE = "xyce"
    FILES = FILES11_AUTO


# =============================================================================
# e2e 13: declared scalar cut outputs into vector bits
# =============================================================================

TB13 = """\
`timescale 1ns/1ps
// through a bit-select, a generate loop, an instance array and two plain
// instances on two[0]/two[1]; bsel's other bits are driven digitally
module tb;
  reg clk = 1'b0;
  always #50 clk = ~clk;
  wire [3:0] bsel;
  wire [1:0] gy, arr, two;
  assign bsel[3] = 1'b1;
  assign bsel[1:0] = 2'b01;
  cout ub (.a(clk), .y(bsel[2]));
  genvar i;
  generate for (i = 0; i < 2; i = i + 1) begin : g
    cout u (.a(clk), .y(gy[i]));
  end endgenerate
  cout ca [1:0] (.a(clk), .y(arr));
  cout t0 (.a(clk), .y(two[0]));
  cout t1 (.a(clk), .y(two[1]));
  always @(bsel) $display("%0t bsel=%b", $time, bsel);
  always @(gy) $display("%0t gy=%b", $time, gy);
  always @(arr) $display("%0t arr=%b", $time, arr);
  always @(two) $display("%0t two=%b", $time, two);
  initial #300 $finish;
endmodule
"""

COUT13_SP = """\
* buffered RC: y follows a with tau = 10 ns
.subckt cout a y
e1 x 0 a 0 1
r1 x y 10k
c1 y 0 1p
.ends
.tran 1n 300n
"""

# Per-bit thresholds written with Verilog names: a generate scope, an instance-array
# element, and two parent-vector bits.
RULES13 = """\
d2a hiv=1.8 lov=0 node=tb.clk;
a2d loth=0.9 hith=0.9 node=*;
a2d loth=1.5 hith=1.5 node=tb.g[0].u.y;
a2d loth=1.2 hith=1.2 node=tb.ca[1].y;
a2d loth=0.3 hith=0.3 node=tb.two[1];
a2d loth=1.5 hith=1.5 node=tb.bsel[2];
"""

FILES13 = {"tb.sv": TB13, "cout.sp": COUT13_SP,
           "vcsAD.init": "choose xa cout.sp;\nport_dir -cell cout (input a; output y);\n" + RULES13}
FILES13_AUTO = {"tb.sv": TB13, "cout.sp": COUT13_SP, "vcsAD.init": "choose xa cout.sp;\n" + RULES13}

# canonical name -> (A2D threshold, parent vector, bit, parent alias)
BITS13 = {
    "tb.ub.y": (1.5, "bsel", 2, "tb.bsel[2]"),
    "tb.g[0].u.y": (1.5, "gy", 0, "tb.gy[0]"),
    "tb.g[1].u.y": (0.9, "gy", 1, "tb.gy[1]"),
    "tb.ca[0].y": (0.9, "arr", 0, "tb.arr[0]"),
    "tb.ca[1].y": (1.2, "arr", 1, "tb.arr[1]"),
    "tb.t0.y": (0.9, "two", 0, "tb.two[0]"),
    "tb.t1.y": (0.3, "two", 1, "tb.two[1]"),
}


class _Item13:
    TAU = 10.0

    def want_edges(self, thr: float) -> List[Tuple[float, str]]:
        out = []
        for k in range(3):
            out.append((rc_cross(50 + 100 * k, self.TAU, 1.8, thr, True), "1"))
            if k < 2:
                out.append((rc_cross(100 + 100 * k, self.TAU, 1.8, thr, False), "0"))
        return out

    def test_one_a2d_per_bit(self):
        ents = ie_entries(self.ie_report())
        a2d = {n: v for k, n, v in ents if k == "a2d"}
        self.assertEqual(sorted(a2d), sorted(BITS13))
        d2a = [n for k, n, _ in ents if k == "d2a"]
        self.assertEqual(d2a, [self.node("tb.clk")["canonical"]])
        for canon, (thr, _, _, alias) in BITS13.items():
            self.assertEqual(self.node(alias)["canonical"], canon)
            self.assertEqual(self.node(alias)["role"], "A2D")
            self.assertEqual(float(a2d[canon]["loth"]), thr, canon)
            self.assertEqual(float(a2d[canon]["hith"]), thr, canon)
        self.assertEqual(sorted(b[2] for b in self.boundary() if b[0] == "A2D"),
                         sorted(c + "__a" for c in BITS13))

    def test_verilog_name_rules_matched(self):
        rpt = self.ie_report()
        self.assertIn("// User Specified Aliases tb.two[1]", report_block(rpt, "tb.t1.y"))
        self.assertIn("// User Specified Aliases tb.bsel[2]", report_block(rpt, "tb.ub.y"))
        for canon, needle in (("tb.g[0].u.y", "node=tb.g[0].u.y"), ("tb.ca[1].y", "node=tb.ca[1].y"),
                              ("tb.t1.y", "node=tb.two[1]"), ("tb.ub.y", "node=tb.bsel[2]")):
            k = self.init_line(needle)
            self.assertIn("// levels: rule a2d.hith vcsAD.init:%d, a2d.loth vcsAD.init:%d" % (k, k),
                          report_block(rpt, canon), canon)
        self.need_compile()
        self.assertNotIn("MSV-IE-OPT-TNF", self.comp.stdout)

    def test_parent_bits_follow_their_thresholds(self):
        out = self.need_run()
        for canon, (thr, vec, bit, _) in sorted(BITS13.items()):
            first, got = bit_edges(events(out, vec), bit)
            self.assertEqual(first, "0", "%s[%d] at t=0" % (vec, bit))
            self.assert_edges(got, self.want_edges(thr), "%s[%d] (%s)" % (vec, bit, canon))
        # the digitally driven bits of bsel never move
        for t, v in events(out, "bsel"):
            if t > 0:
                self.assertEqual((v[0], v[2], v[3]), ("1", "0", "1"), "bsel=%s at %d" % (v, t))

    def test_parent_not_tied_to_z(self):
        out = self.need_run()
        self.assert_never_unknown(out, ["bsel", "gy", "arr", "two"])
        self.assertNotIn("undriven-net constant", self.comp.stdout)
        self.assert_digital_stop(out, "3e-07")

    def test_analog_bits(self):
        raw = self.rawfile()
        for canon in BITS13:
            n = self.node(canon)["name"]
            self.assertAlmostEqual(raw.at(n, 99e-9), 1.8, delta=0.02, msg=canon)
            self.assertAlmostEqual(raw.at(n, 45e-9), 0.0, delta=0.02, msg=canon)

    def test_top_net_names_the_bit(self):
        # a node on one bit of a vector: the Top-Net is the bit (tb.arr[0]), not the
        # whole bus, as VCS prints it ("// Top-Net top.s[0]", PAMS p269-270)
        rpt = self.ie_report()
        for canon, (_, _, _, alias) in BITS13.items():
            self.assertIn("// Top-Net %s" % alias, report_block(rpt, canon), canon)


@needs_ams
class TestE2E13VectorBitsVacask(_Item13, SharedRun):
    ENGINE = "vacask"
    FILES = FILES13


@needs_ams
class TestE2E13VectorBitsXyce(_Item13, SharedRun):
    ENGINE = "xyce"
    FILES = FILES13


# The same design with auto ports (no port_dir).  With an inout shell port on bsel[2],
# tgt-vhdl merges `assign bsel[3] = 1'b1; assign bsel[1:0] = 2'b01;` into
# `bsel <= logic3d_vector'(L3D_1, L3D_Z, L3D_0, L3D_1);`.  The cut reads it bit by
# bit (§5.4: a driver that can only produce Z is not a driver), so the L3D_Z element
# does not drive bsel(2): tb.ub.y stays an A2D-only node, with no D2A and no
# 3.3 V fallback warning.
class _Item13Auto(_Item13):
    def test_no_spurious_d2a(self):
        self.need_compile()
        self.assertEqual(self.node("tb.bsel[2]")["role"], "A2D")
        self.assertNotIn("no supply reached from tb.ub.y", self.comp.stdout)
        self.assertEqual(sum(1 for b in self.boundary() if b[0] == "D2A"), 2)


@needs_ams
class TestE2E13VectorBitsAutoVacask(_Item13Auto, SharedRun):
    ENGINE = "vacask"
    FILES = FILES13_AUTO


@needs_ams
class TestE2E13VectorBitsAutoXyce(_Item13Auto, SharedRun):
    ENGINE = "xyce"
    FILES = FILES13_AUTO


# =============================================================================
# e2e 14: ports named in / out / OUT
# =============================================================================

# in, out (VHDL reserved words) on one SPICE-only cell; In / OUT spelled by the
# instance on a second cell whose subckt says IN OUT.
TB14 = """\
`timescale 1ns/1ps
module tb;
  reg clk = 1'b0;
  always #50 clk = ~clk;
  wire y1, y2;
  inv_io u1 (.in(clk), .out(y1));
  buf_up u2 (.In(clk), .OUT(y2));
  always @(y1) $display("%0t y1=%b", $time, y1);
  always @(y2) $display("%0t y2=%b", $time, y2);
  initial #300 $finish;
endmodule
"""

IO14_SP = """\
* inv_io: analog inverter, out = 1.8 - in, behind 10k into 1p
.subckt inv_io in out
vdd1 vd 0 1.8
e1 x vd in 0 -1
r1 x out 10k
c1 out 0 1p
.ends
* buf_up: OUT = 0.75 * IN
.subckt buf_up IN OUT
e1 OUT 0 IN 0 0.75
.ends
.tran 1n 300n
"""

INIT14 = """\
choose xa io.sp;
port_dir -cell inv_io (input in; output out);
port_dir -cell buf_up (input IN; output OUT);
a2d loth=0.6 hith=0.6 node=tb.u2.OUT;
"""

FILES14 = {"tb.sv": TB14, "io.sp": IO14_SP, "vcsAD.init": INIT14}


class _Item14:
    TAU = 10.0

    def test_ie_report(self):
        rpt = self.ie_report()
        ents = {(k, n.lower()): v for k, n, v in ie_entries(rpt)}
        self.assertEqual(sorted(ents), [("a2d", "tb.u1.out"), ("a2d", "tb.u2.out"),
                                        ("d2a", "tb.u1.in")])
        # levels from the deck's only supply (inv_io's vdd1, 1.8 V): no 3.3 V fallback
        self.assertEqual(float(ents[("d2a", "tb.u1.in")]["hiv"]), 1.8)
        self.assertEqual(float(ents[("a2d", "tb.u1.out")]["hith"]), 0.9)
        # the rule written node=tb.u2.OUT matched (names are case-insensitive)
        self.assertEqual(float(ents[("a2d", "tb.u2.out")]["hith"]), 0.6)
        self.need_compile()
        self.assertNotIn("3.3 V fallback", self.comp.stdout)
        blk = [ln.lower() for ln in report_block(rpt, "tb.u1.in")]
        self.assertIn("// all boundary nets tb.u1.in tb.u2.in", blk)

    def test_port_binding_by_value(self):
        raw = self.rawfile()
        # in -> out inverts; IN -> OUT scales by 0.75: swapped ports would show neither
        self.assertAlmostEqual(raw.at("n_u1_out", 45e-9), 1.8, delta=0.01)
        self.assertAlmostEqual(raw.at("n_u1_out", 99e-9), 0.0, delta=0.02)
        self.assertAlmostEqual(raw.at("n_u2_out", 95e-9), 1.35, delta=0.01)
        self.assertAlmostEqual(raw.at("n_u2_out", 45e-9), 0.0, delta=0.01)
        # the inverter output swings 1.8 V <-> 0 through 10k into 1p: 0.9 V after tau*ln 2
        want = []
        for k in range(3):
            want.append(50 + 100 * k + self.TAU * math.log(2))
            if k < 2:
                want.append(100 + 100 * k + self.TAU * math.log(2))
        self.assert_crossings(raw, "n_u1_out", 0.9, want, "inverter")

    def test_digital(self):
        out = self.need_run()
        init, got = settle(events(out, "y1"))
        self.assertEqual(init, "1")
        want = []
        for k in range(3):
            want.append((50 + 100 * k + self.TAU * math.log(2), "0"))
            if k < 2:
                want.append((100 + 100 * k + self.TAU * math.log(2), "1"))
        self.assert_edges(got, want, "y1")
        init, got = settle(events(out, "y2"))
        self.assertEqual(init, "0")
        want = []
        for k in range(3):
            want.append((50.0 + 100 * k, "1"))
            if k < 2:
                want.append((100.0 + 100 * k, "0"))
        self.assert_edges(got, want, "y2")
        self.assert_never_unknown(out, ["y1", "y2"])
        self.assert_digital_stop(out, "3e-07")


@needs_ams
class TestE2E14PortNamesVacask(_Item14, SharedRun):
    ENGINE = "vacask"
    FILES = FILES14


@needs_ams
class TestE2E14PortNamesXyce(_Item14, SharedRun):
    ENGINE = "xyce"
    FILES = FILES14


# in, out and OUT on ONE cell: a multi-view cell (Verilog is case-sensitive, so out
# and OUT are two ports) overridden by use_spice and bound by position (the control
# file is case-insensitive, §2.1, so a by-name port_map cannot tell out from OUT).
# The translated entity declares `out_sig` and `OUT_sig_1` (the case-collision
# suffix), and the instance's port map must name those same formals.
TB14B = """\
`timescale 1ns/1ps
module tb;
  reg clk = 1'b0;
  always #50 clk = ~clk;
  wire o1, o2;
  dual u3 (.in(clk), .out(o1), .OUT(o2));
  always @(o1) $display("%0t o1=%b", $time, o1);
  always @(o2) $display("%0t o2=%b", $time, o2);
  initial #300 $finish;
endmodule

module dual (input in, output out, output OUT);
  assign out = in;
  assign OUT = ~in;
endmodule
"""

DUAL14_SP = """\
* dual: o1 = in (buffer), o2 = 1.8 - in (inverter)
.subckt dual in o1 o2
vdd1 vd 0 1.8
e1 o1 0 in 0 1
e2 o2 vd in 0 -1
.ends
.tran 1n 300n
"""

INIT14B = """\
choose xa dual.sp;
use_spice -cell dual port_map (* => snps_by_position);
"""

FILES14B = {"tb.sv": TB14B, "dual.sp": DUAL14_SP, "vcsAD.init": INIT14B}


class _Item14OutOUT:
    def test_out_and_OUT_on_one_cell(self):
        out = self.need_run()
        ents = sorted((k, n.lower()) for k, n, _ in ie_entries(self.ie_report()))
        self.assertEqual(ents, [("a2d", "tb.u3.o1"), ("a2d", "tb.u3.o2"), ("d2a", "tb.u3.in")])
        raw = self.rawfile()
        self.assertAlmostEqual(raw.at("n_u3_o1", 95e-9), 1.8, delta=0.01)
        self.assertAlmostEqual(raw.at("n_u3_o2", 95e-9), 0.0, delta=0.01)
        self.assertAlmostEqual(raw.at("n_u3_o2", 45e-9), 1.8, delta=0.01)
        want1, want2 = [], []
        for k in range(3):
            want1.append((50.0 + 100 * k, "1"))
            want2.append((50.0 + 100 * k, "0"))
            if k < 2:
                want1.append((100.0 + 100 * k, "0"))
                want2.append((100.0 + 100 * k, "1"))
        init, got = settle(events(out, "o1"))
        self.assertEqual(init, "0")
        self.assert_edges(got, want1, "o1 (port out)")
        init, got = settle(events(out, "o2"))
        self.assertEqual(init, "1")
        self.assert_edges(got, want2, "o2 (port OUT)")


@needs_ams
class TestE2E14OutAndOUTVacask(_Item14OutOUT, SharedRun):
    ENGINE = "vacask"
    FILES = FILES14B


@needs_ams
class TestE2E14OutAndOUTXyce(_Item14OutOUT, SharedRun):
    ENGINE = "xyce"
    FILES = FILES14B


if __name__ == "__main__":
    unittest.main()
