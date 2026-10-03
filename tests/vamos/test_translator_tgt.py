"""Translator patches T1, T2, T3 and T5 (VAMOS_AMS_DESIGN.md §7) on the real stack,
and the tgt-vhdl fixes the AMS end-to-end tests found: %t of a real and its field
width (e2e 18), an input port driven inside its module (e2e 27 through a wrapper),
instance labels of alike-named generate instances (e2e 28) and port names that
differ only in case (e2e 14).

    python3 -m unittest discover -s tests/vamos -p 'test_translator_tgt.py' -v

Each fixture tests/vamos/fixtures/ams/xlat_*.v (or inline SOURCE) is translated once with
bin/iverilog-sv2ghdl (iverilog -tvhdl -psv2vhdl=1, the vcs personality's
translator).  The tests check the VHDL the patches emit and, for behaviour,
run the design under nvc (bin/vvp-sv2ghdl, as the ivtest suite does) and
under vvp, and compare the *settled* value of every $display per time step:
nvc runs a time step as VHDL delta cycles, so an `always @(x) $display'
may also print intermediate (and time-0 initialisation) values that vvp
never shows; the last value printed per display and time step, with
unchanged repeats dropped, must match.

Linux (WSL) only: the stack is Linux ELF.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from typing import Dict, List, Tuple

from vamos_testlib import ROOT, fixture, needs_stack

from vamos import tools  # noqa: E402

BIN = os.path.join(ROOT, "bin")


def _stack_env() -> dict:
    """The environment NvcBackend gives the translator and the run."""
    env = dict(os.environ)
    nvc = tools.find_real("nvc")
    iverilog = tools.find_real("iverilog")
    if nvc:
        env["NVC"] = nvc
        libdir = tools.nvc_libdir(nvc)
        env["NVC_LIBDIR"] = libdir
        prefix = os.path.dirname(os.path.dirname(os.path.realpath(nvc)))
        for pydir in (os.path.join(libdir, "sv2vhdl"),
                      os.path.join(os.path.dirname(prefix), "nvc", "lib", "sv2vhdl")):
            if os.path.isfile(os.path.join(pydir, "sv2vhdl_resolver.py")):
                env["PYTHONPATH"] = pydir + (os.pathsep + env["PYTHONPATH"]
                                             if env.get("PYTHONPATH") else "")
                break
    if iverilog:
        env["IVERILOG"] = iverilog
    return env


def _vvp() -> str:
    iverilog = tools.find_real("iverilog") or ""
    cand = os.path.join(os.path.dirname(os.path.realpath(iverilog)), "vvp")
    return cand if os.access(cand, os.X_OK) else (tools.find_real("vvp") or "")


def _run(cmd: List[str], cwd: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, errors="replace", timeout=600)


_DISPLAY_RE = re.compile(r"^(\d+) (\w+)=(.*)$")


def settled(text: str) -> Dict[str, List[Tuple[int, str]]]:
    """{display key: [(time, value text)]} from lines "<time> <key>=<...>".

    Per key and time step only the last line counts, and a step whose value
    equals the previous one is dropped (a delta-cycle glitch, not an event).
    Time 0 only seeds that comparison: whether an `always @(...)' sees the
    time-0 settling is a race in Verilog (vvp often does not), while a VHDL
    process with a sensitivity list always runs once at time 0.
    """
    last = {}  # type: Dict[Tuple[str, int], str]
    order = []  # type: List[Tuple[str, int]]
    for line in text.splitlines():
        m = _DISPLAY_RE.match(line.strip())
        if not m:
            continue
        k = (m.group(2), int(m.group(1)))
        if k not in last:
            order.append(k)
        last[k] = m.group(2) + "=" + m.group(3)
    out = {}  # type: Dict[str, List[Tuple[int, str]]]
    for key, t in sorted(order, key=lambda kt: (kt[0], kt[1])):
        seq = out.setdefault(key, [])
        if not seq or seq[-1][1] != last[(key, t)]:
            seq.append((t, last[(key, t)]))
    for key in list(out):
        out[key] = [e for e in out[key] if e[0] > 0]
        if not out[key]:
            del out[key]
    return out


class Translated(unittest.TestCase):
    """Translate one fixture (or the inline SOURCE) per class (setUpClass); run on demand."""

    FIXTURE = ""
    SOURCE = ""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vamos-xlat-")
        cls.env = _stack_env()
        cls.src = os.path.join(cls.tmp, (cls.FIXTURE or "src") + ".v")
        if cls.SOURCE:
            with open(cls.src, "w") as fh:
                fh.write(cls.SOURCE)
        else:
            shutil.copy(fixture("ams", cls.FIXTURE + ".v"), cls.src)
        cls.xlat = _run([os.path.join(BIN, "iverilog-sv2ghdl"), "-o", "vsim", "-g2012", cls.src],
                        cls.tmp, cls.env)
        cls.outdir = os.path.join(cls.tmp, "vsim")
        try:
            with open(os.path.join(cls.outdir, "design.vhd"), errors="replace") as fh:
                cls.vhdl = fh.read()
        except OSError:
            cls.vhdl = ""
        cls._nvc = None
        cls._vvp = None

    @classmethod
    def tearDownClass(cls):
        if os.environ.get("VAMOS_TEST_KEEP"):
            print("kept %s" % cls.tmp)
        else:
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.assertEqual(self.xlat.returncode, 0, self.xlat.stdout + self.xlat.stderr)
        self.assertIn("entity tb is", self.vhdl)
        self.assertNotIn("sv2vhdl:deferred", self.vhdl)

    def nvc(self) -> subprocess.CompletedProcess:
        if self.__class__._nvc is None:
            self.__class__._nvc = _run([os.path.join(BIN, "vvp-sv2ghdl"), "vsim"], self.tmp, self.env)
        return self.__class__._nvc

    def vvp(self) -> subprocess.CompletedProcess:
        if self.__class__._vvp is None:
            vvp = _vvp()
            self.assertTrue(vvp, "no vvp next to iverilog")
            c = _run([self.env["IVERILOG"], "-g2012", "-o", "ref.vvp", self.src], self.tmp, self.env)
            self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
            self.__class__._vvp = _run([vvp, "-n", "ref.vvp"], self.tmp, self.env)
        return self.__class__._vvp

    def assert_settled_like_vvp(self):
        n, v = self.nvc(), self.vvp()
        self.assertEqual(n.returncode, 0, n.stdout + n.stderr)
        self.assertEqual(v.returncode, 0, v.stdout + v.stderr)
        want = settled(v.stdout)
        self.assertTrue(want, v.stdout)
        self.assertEqual(settled(n.stdout), want, "\n--- nvc:\n%s\n--- vvp:\n%s" % (n.stdout, v.stdout))

    def lines(self, pattern: str) -> List[str]:
        return [ln.strip() for ln in self.vhdl.splitlines() if re.search(pattern, ln)]


# ---------------------------------------------------------------------- T1

_SEV_RE = re.compile(r"^(INFO|WARNING|ERROR|FATAL): \S+:(\d+): ")


def _severity_lines(text: str) -> List[str]:
    """Output lines with the source path of severity messages normalised
    (vvp names the file as given, the translation names _norm.sv) and the
    nvc report lines ("** ...") and bare "INFO" note dropped."""
    out = []
    for line in text.splitlines():
        if line.startswith("** ") or line == "INFO" or "$finish called" in line:
            continue
        out.append(_SEV_RE.sub(lambda m: "%s: FILE:%s: " % (m.group(1), m.group(2)), line))
    return out


@needs_stack
class TestT1Severity(Translated):
    FIXTURE = "xlat_t1_severity"

    def test_vhdl(self):
        self.assertNotIn("Unsupported system task", self.vhdl)
        for want in ('report "INFO";', 'report "WARNING" severity warning;',
                     'report "ERROR" severity error;', 'report "FATAL" severity failure;'):
            self.assertIn(want, self.vhdl)

    def test_messages_match_vvp(self):
        n, v = self.nvc(), self.vvp()
        self.assertEqual(_severity_lines(n.stdout), _severity_lines(v.stdout),
                         "\n--- nvc:\n%s\n--- vvp:\n%s" % (n.stdout, v.stdout))
        self.assertIn("still running", n.stdout)           # $error does not stop the run
        self.assertNotIn("FAILED", n.stdout + n.stderr)    # $fatal does

    def test_reports_and_exit_status(self):
        n = self.nvc()
        self.assertNotEqual(n.returncode, 0)               # $fatal: nvc exits non-zero
        self.assertNotEqual(self.vvp().returncode, 0)      # as vvp does
        self.assertRegex(n.stderr, r"\*\* Warning: \S+: WARNING")
        self.assertRegex(n.stderr, r"\*\* Error: \S+: ERROR")
        self.assertRegex(n.stdout, r"\*\* Failure: \S+: FATAL")
        self.assertIn("\nINFO\n", n.stdout)                # the note-severity report


@needs_stack
class TestT1Stop(Translated):
    FIXTURE = "xlat_t1_stop"

    def test_vhdl(self):
        self.assertIn("std.env.stop;", self.vhdl)
        self.assertNotIn("Unsupported system task", self.vhdl)

    def test_stop_ends_the_run(self):
        n = self.nvc()
        self.assertEqual(n.returncode, 0, n.stdout + n.stderr)
        self.assertTrue(n.stdout.startswith("pending "), n.stdout)
        self.assertIn("STOP called", n.stdout)
        self.assertNotIn("FAILED", n.stdout + n.stderr)


# ---------------------------------------------------------------------- T2

_COPY_RE = r"^\s*SW\w*_b\s*(<=|:=)"


@needs_stack
class TestT2Pad(Translated):
    FIXTURE = "xlat_t2_pad"

    def test_alias(self):
        aliases = self.lines(r"^\s*alias SW\w*_b is ")
        self.assertEqual(len(aliases), 1, aliases)
        self.assertRegex(aliases[0], r"^alias (SW\w*_b) is pbus\(2\);")
        name = aliases[0].split()[1]
        self.assertTrue(self.lines(r"pad => %s\b" % name))
        self.assertEqual(self.lines(_COPY_RE), [])

    def test_both_directions_like_vvp(self):
        self.assert_settled_like_vvp()


@needs_stack
class TestT2Generate(Translated):
    FIXTURE = "xlat_t2_gen"

    def arch(self, entity: str) -> str:
        m = re.search(r"^architecture from_verilog of %s is\n(.*?)^end architecture;" % entity,
                      self.vhdl, re.M | re.S)
        self.assertTrue(m, entity)
        return m.group(1)

    def test_one_alias_per_iteration(self):
        for entity, want in (("tb", ["pbus(0)", "pbus(1)", "pbus(2)", "pbus(3)"]),
                             (r"xlat_ring\w*", ["pads(0)", "pads(1)"])):
            text = self.arch(entity)
            aliases = re.findall(r"^\s*alias (SW\w*_b) is (\w+\(\d+\));", text, re.M)
            self.assertEqual(sorted(t for _, t in aliases), want, entity)
            names = [n for n, _ in aliases]
            self.assertEqual(len(set(names)), len(want), names)
            for name in names:
                self.assertEqual(len(re.findall(r"pad => %s\b" % name, text)), 1, name)
        self.assertEqual(self.lines(_COPY_RE), [])

    def test_ring_bus_port_stays_inout(self):
        # the ring's own bus port is driven through the aliases: inout, resolved
        self.assertTrue(self.lines(r"^\s*pads : inout resolved_logic3d_vector\(1 downto 0\)"),
                        self.vhdl)

    def test_pad_ring_like_vvp(self):
        self.assert_settled_like_vvp()


@needs_stack
class TestT2ThroughVcs(unittest.TestCase):
    """The vcs personality itself (pure digital, `vcs ... -R`) on the pad rings."""

    def test_pad_ring_like_vvp(self):
        tmp = tempfile.mkdtemp(prefix="vamos-xlat-vcs-")
        try:
            src = os.path.join(tmp, "xlat_t2_gen.v")
            shutil.copy(fixture("ams", "xlat_t2_gen.v"), src)
            env = _stack_env()
            env["PATH"] = os.path.join(ROOT, "shims") + os.pathsep + env.get("PATH", "")
            r = _run(["vcs", "-full64", "xlat_t2_gen.v", "-R"], tmp, env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("deferred", r.stdout + r.stderr)
            c = _run([env["IVERILOG"], "-g2012", "-o", "ref.vvp", src], tmp, env)
            self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
            v = _run([_vvp(), "-n", "ref.vvp"], tmp, env)
            want = settled(v.stdout)
            self.assertTrue(want, v.stdout)
            self.assertEqual(settled(r.stdout), want, "\n--- vcs:\n%s\n--- vvp:\n%s" % (r.stdout, v.stdout))
        finally:
            if os.environ.get("VAMOS_TEST_KEEP"):
                print("kept %s" % tmp)
            else:
                shutil.rmtree(tmp, ignore_errors=True)


@needs_stack
class TestT2PartSelect(Translated):
    FIXTURE = "xlat_t2_part"

    def test_slice_aliases(self):
        targets = sorted(a.split(" is ")[1].split(";")[0]
                         for a in self.lines(r"^\s*alias SW\w*_b is "))
        # tb's two connections, and the wrapper's (emitted in each of its variants)
        self.assertIn("pbus(3 downto 2)", targets)
        self.assertIn("pbus(7 downto 4)", targets)
        self.assertIn("w(2 downto 1)", targets)
        self.assertEqual(self.lines(_COPY_RE), [])

    def test_part_selects_like_vvp(self):
        self.assert_settled_like_vvp()


@needs_stack
class TestT2Ranges(Translated):
    FIXTURE = "xlat_t2_ranges"

    def test_offsets(self):
        targets = sorted(a.split(" is ")[1].split(";")[0]
                         for a in self.lines(r"^\s*alias SW\w*_b is "))
        self.assertEqual(targets, ["asc(3)", "hi(1)"])     # hi[5] of [7:4]; asc[0] of [0:3]

    def test_like_vvp(self):
        self.assert_settled_like_vvp()


@needs_stack
class TestT2InputPortFallback(Translated):
    FIXTURE = "xlat_t2_inport"

    def test_copy_with_warning(self):
        # no alias of an `in' port: a temporary and a one-way copy, and a warning
        self.assertEqual(self.lines(r"^\s*alias SW"), [])
        self.assertTrue(self.lines(r"^\s*SW\w*_b <= d\(1\);"), self.vhdl)
        with open(os.path.join(self.outdir, "iverilog.log")) as fh:
            self.assertIn("is connected one way only", fh.read())

    def test_input_direction_like_vvp(self):
        self.assert_settled_like_vvp()


# ---------------------------------------------------------------------- T3

@needs_stack
class TestT3InstancePaths(Translated):
    FIXTURE = "xlat_t3_paths"

    def test_paths(self):
        paths = sorted(set(m.group(1) for m in re.finditer(r"-- Verilog instance: (\S+)", self.vhdl)))
        self.assertEqual(paths, sorted(["m", "ua[0]", "ua[1]", "gi.ui", "genblk2.un",
                                        "outer[0].inner[1].deep", "g[0].xb", "g[1].xb"]))

    def test_comment_precedes_its_instance(self):
        lines = self.vhdl.splitlines()
        n = 0
        for i, line in enumerate(lines):
            if "-- Verilog instance: " in line:
                self.assertRegex(lines[i + 1], r"^\s*\w+: entity work\.\w+", line)
                self.assertIn("-- Generated from instantiation at ", lines[i - 1])
                n += 1
        self.assertGreaterEqual(n, 8)

    def test_every_user_instance_is_named(self):
        insts = [ln for ln in self.vhdl.splitlines() if re.match(r"^\s*\w+: entity work\.", ln)]
        named = self.vhdl.count("-- Verilog instance: ")
        self.assertEqual(len(insts), named)

    def test_runs(self):
        self.assert_settled_like_vvp()


# ---------------------------------------------------------------------- T5

@needs_stack
class TestT5TriNets(Translated):
    FIXTURE = "xlat_t5_tri"

    def pulls(self) -> Dict[str, str]:
        """{actual: entity} of the sv_pullup/sv_pulldown instances, checking
        each is preceded by its strength comment."""
        lines = self.vhdl.splitlines()
        out = {}
        for i, line in enumerate(lines):
            m = re.match(r"^\s*\w+: entity sv2vhdl\.(sv_pullup|sv_pulldown)\(behavioral\)", line)
            if not m:
                continue
            self.assertEqual(lines[i - 1].strip(), "-- sv_strength: pull1 pull0")
            pm = re.search(r"y => ([\w()]+)", "\n".join(lines[i:i + 4]))
            self.assertTrue(pm, line)
            out[pm.group(1)] = m.group(1)
        return out

    def test_pull_instances(self):
        p = self.pulls()
        for net in ("t1", "t1d", "t1c", "v1(0)", "v1(1)", "v1(2)", "v1(3)"):
            self.assertEqual(p.get(net), "sv_pullup", (net, p))
        for net in ("t0", "t0d"):
            self.assertEqual(p.get(net), "sv_pulldown", (net, p))

    def test_no_strong_default(self):
        self.assertEqual(self.lines(r"^\s*(t1|t0)\s*<=\s*L3D_[01];"), [])

    def test_unconnected_tri1_input(self):
        self.assertTrue(self.lines(r"a => L3D_1"), self.vhdl)

    def test_like_vvp(self):
        self.assert_settled_like_vvp()


# ------------------------------------------------- fixes found by the AMS e2e tests

def _tagged(text: str, tags: str) -> List[str]:
    """Output lines starting with one of the one-letter tags and a blank."""
    return [ln for ln in text.splitlines() if len(ln) > 1 and ln[0] in tags and ln[1] == " "]


@needs_stack
class TestPercentT(Translated):
    """%t of a real ($realtime, a real variable) keeps its fraction (5355 for 5.355 ns
    under `timescale 1ns/1ps, not 5000), and the field width follows the format:
    %0t none, %<N>t N blanks, %0<N>t N zeros, plain %t the $timeformat width (e2e 18)."""

    SOURCE = """\
`timescale 1ns/1ps
module tb;
  real r;
  time tv;
  initial begin
    #1.234;
    r = 2.5;
    tv = $time;
    $display("A %0t|", $realtime);
    $display("B %t|", $realtime);
    $display("C %0t|", $time);
    $display("D %t|", $time);
    $display("E %12t|", $realtime);
    $display("F %012t|", $realtime);
    $display("G %0t|", r);
    $display("H %0t|", 2.5);
    $display("I %0t|", tv);
    $display("J %0t|", -r);
    $strobe("K %0t|", $realtime);
    $display("W %.3t|", $realtime);
    #4.1216;
    $display("L %0t|", $realtime);
    $display("M %3t|", $realtime);
    $timeformat(-9, 3, " ns", 12);
    $display("N %t|", $realtime);
    $display("O %0t|", $realtime);
    $display("P %t|", $time);
    $display("Q %15t|", $realtime);
    $display("R %0t|", r);
    $timeformat(-15, 0, "fs", 0);
    $display("S %t|", $realtime);
    $display("T %t|", r);
    $finish;
  end
endmodule
"""
    TAGS = "ABCDEFGHIJKLMNOPQRST"

    def test_like_vvp(self):
        n, v = self.nvc(), self.vvp()
        self.assertEqual(n.returncode, 0, n.stdout + n.stderr)
        want = _tagged(v.stdout, self.TAGS)
        self.assertEqual(len(want), len(self.TAGS), v.stdout)
        self.assertEqual(_tagged(n.stdout, self.TAGS), want,
                         "\n--- nvc:\n%s\n--- vvp:\n%s" % (n.stdout, v.stdout))

    def test_values(self):
        got = dict((ln[0], ln[2:]) for ln in _tagged(self.nvc().stdout, self.TAGS))
        self.assertEqual(got["A"], "1234|")
        self.assertEqual(got["B"], "%20s|" % "1234")
        self.assertEqual(got["F"], "000000001234|")
        self.assertEqual(got["J"], "-2500|")
        self.assertEqual(got["L"], "5356|")
        self.assertEqual(got["O"], "5.356 ns|")

    def test_vhdl(self):
        # a real argument goes, in scope units, to sv_tstr's real overload: no integer()
        # cast (which stopped the run past 2^31 precision ticks; test_translator_time.py)
        self.assertNotRegex(self.vhdl, r"sv_tstr\(integer\(")
        self.assertRegex(self.vhdl, r"sv_tstr\(r, -9, -12\)")
        self.assertEqual(len(self.lines(r"^\s*function Verilog_Time_Field\(")), 1)

    def test_precision_not_translated_is_said(self):
        with open(os.path.join(self.outdir, "iverilog.log")) as fh:
            self.assertIn("the precision of %.3t is not translated", fh.read())


@needs_stack
class TestDrivenInputPort(Translated):
    """An input port whose net is also driven inside the module (through a child's inout
    port, as a vamos shell's Z marker does, or by a child's output reg) translates and
    runs like vvp, whatever its actual (e2e 27 through a wrapper):

    - a variable or an expression: the core's port buffer becomes a copy into a resolved
      PB_<label>_<port> signal of the parent, associated with the port;
    - a net: the core coerces the port to inout;
    - none (each module is an elaboration root once, in sv2vhdl-modules): the port is
      inout, so the per-module translation analyses (no whole-design fallback)."""

    SOURCE = """\
`timescale 1ns/1ps
module child(inout p, output q);
  bufif1 b(p, 1'b0, 1'b0);
  assign q = p;
endmodule

module wrap(input a, output q);
  child c(.p(a), .q(q));
endmodule

module rsrc(input clk, output reg y);
  always @(clk) y = clk;
endmodule

module wreg(input a, input clk, output q);
  rsrc s(.clk(clk), .y(a));
  assign q = a;
endmodule

module tb;
  reg r = 1'b0, c = 1'b0;
  wire n = r;
  wire q1, q2, q3, q5;
  wrap w1(.a(r), .q(q1));
  wrap w2(.a(n), .q(q2));
  wrap w3(.a(~r), .q(q3));
  wreg g1(.a(1'bz), .clk(c), .q(q5));
  always @(q1) $display("%0d q1=%b", $time, q1);
  always @(q2) $display("%0d q2=%b", $time, q2);
  always @(q3) $display("%0d q3=%b", $time, q3);
  always @(q5) $display("%0d q5=%b", $time, q5);
  initial begin
    #10 r = 1'b1; c = 1'b1;
    #10 r = 1'bz;
    #10 r = 1'b0; c = 1'b0;
    #10 $finish;
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_settled_like_vvp()

    def test_port_buffers_in_the_parent(self):
        for inst, actual in (("w1", "r"), ("w3", None), ("g1", None)):
            sig = "PB_%s_a" % inst
            self.assertTrue(self.lines(r"^\s*signal %s : resolved_logic3d\b" % sig), self.vhdl)
            copies = self.lines(r"^\s*%s <= " % sig)
            self.assertEqual(len(copies), 1, copies)
            if actual:
                self.assertEqual(copies[0], "%s <= %s;" % (sig, actual))
            self.assertEqual(len(self.lines(r"^\s*a => %s,?$" % sig)), 1, sig)
        # the net actual joins the coerced port; nothing is copied into a port from a
        # dangling temporary
        self.assertTrue(self.lines(r"^\s*a => n,?$"), self.vhdl)
        self.assertEqual(self.lines(r"^\s*a <= LO_"), [])

    def test_driven_input_is_inout(self):
        ports = re.findall(r"^entity (wrap\w*|wreg\w*) is\n\s*port \(\n\s*a : (\w+) (\w+)",
                           self.vhdl, re.M)
        self.assertTrue(ports, self.vhdl)
        for ent, mode, typ in ports:
            self.assertEqual((mode, typ), ("inout", "resolved_logic3d"), ent)

    def test_per_module_translation_analyses(self):
        with open(os.path.join(self.outdir, "iverilog.log")) as fh:
            log = fh.read()
        self.assertIn("=== sv2vhdl-modules: iverilog -tvhdl -s wrap: translated", log)
        self.assertNotIn("(whole design)", log)


@needs_stack
class TestInstanceLabels(Translated):
    """Two generate blocks that name their instances alike get distinct labels (e2e 28)."""

    SOURCE = """\
`timescale 1ns/1ps
module leaf(input a, output y);
  assign y = ~a;
endmodule

module tb;
  reg [1:0] pa = 2'b00, pb = 2'b11;
  wire [1:0] ya, yb;
  genvar g;
  for (g = 0; g < 2; g = g + 1) begin : ga
    leaf u (.a(pa[g]), .y(ya[g]));
  end
  for (g = 0; g < 2; g = g + 1) begin : gb
    leaf u (.a(pb[g]), .y(yb[g]));
  end
  always @(ya) $display("%0d ya=%b", $time, ya);
  always @(yb) $display("%0d yb=%b", $time, yb);
  initial begin
    #10 pa = 2'b01; pb = 2'b10;
    #10 $finish;
  end
endmodule
"""

    def test_labels(self):
        labels = [ln.split(":")[0] for ln in self.lines(r"^\s*\w+: entity work\.leaf")]
        self.assertEqual(len(labels), 4, labels)
        self.assertEqual(len(set(l.lower() for l in labels)), 4, labels)
        self.assertEqual(labels, ["u_g0", "u_g1", "gb_u_g0", "gb_u_g1"])
        paths = re.findall(r"-- Verilog instance: (\S+)", self.vhdl)
        self.assertEqual(paths, ["ga[0].u", "ga[1].u", "gb[0].u", "gb[1].u"])

    def test_like_vvp(self):
        self.assert_settled_like_vvp()


@needs_stack
class TestPortNamesDifferingInCase(Translated):
    """Ports that differ only in case (out and OUT; q and Q) are declared out_sig and
    OUT_sig_1, q and Q_1, and every instance names its formals that way (e2e 14)."""

    SOURCE = """\
`timescale 1ns/1ps
module dual (input in, output out, output OUT);
  assign out = in;
  assign OUT = ~in;
endmodule

module regd (input d, output reg q, output Q);
  always @(d) q = d;
  assign Q = ~d;
endmodule

module wrapr (input a, output q, output Q);
  regd r (.d(a), .q(q), .Q(Q));
endmodule

module tb;
  reg clk = 1'b0;
  wire o1, o2, o3, o4;
  dual u3 (.in(clk), .out(o1), .OUT(o2));
  wrapr w (.a(clk), .q(o3), .Q(o4));
  always @(o1) $display("%0d o1=%b", $time, o1);
  always @(o2) $display("%0d o2=%b", $time, o2);
  always @(o3) $display("%0d o3=%b", $time, o3);
  always @(o4) $display("%0d o4=%b", $time, o4);
  initial begin
    #10 clk = 1'b1;
    #10 clk = 1'b0;
    #10 $finish;
  end
endmodule
"""

    def entity_ports(self) -> Dict[str, List[str]]:
        out = {}
        for m in re.finditer(r"^entity (\w+) is\n\s*port \((.*?)\n\s*\);", self.vhdl, re.M | re.S):
            out[m.group(1).lower()] = [p.split(":")[0].strip().lower()
                                       for p in m.group(2).split(";") if ":" in p]
        return out

    def test_formals_are_declared_ports(self):
        ports = self.entity_ports()
        self.assertIn("out_sig_1", ports[[e for e in ports if e.startswith("dual")][0]])
        n = 0
        for m in re.finditer(r"^\s*(\w+): entity work\.(\w+)\n\s*port map \((.*?)\);",
                             self.vhdl, re.M | re.S):
            formals = [f.lower() for f in re.findall(r"(\w+) =>", m.group(3))]
            self.assertEqual(len(formals), len(set(formals)), m.group(0))
            self.assertTrue(set(formals) <= set(ports[m.group(2).lower()]), m.group(0))
            n += 1
        self.assertGreaterEqual(n, 3)

    def test_like_vvp(self):
        self.assert_settled_like_vvp()


class TestSettled(unittest.TestCase):
    """The comparison helper itself (no stack needed)."""

    def test_glitches_and_repeats_drop(self):
        nvc = "0 a=x\n0 a=0\n0 b=1\n10 a=1\n10 a=0\n20 a=1\n20 b=1\n30 b=0\n"
        vvp = "0 a=0\n20 a=1\n30 b=0\n"
        self.assertEqual(settled(nvc), settled(vvp))
        self.assertEqual(settled(vvp), {"a": [(20, "a=1")], "b": [(30, "b=0")]})

    def test_a_real_difference_shows(self):
        self.assertNotEqual(settled("0 a=0\n10 a=1\n"), settled("0 a=0\n10 a=z\n"))


if __name__ == "__main__":
    unittest.main()
