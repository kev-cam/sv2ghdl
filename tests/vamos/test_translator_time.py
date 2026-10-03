"""tgt-vhdl's time base, time functions and scope names, against vvp on the real stack
(VAMOS_AMS_DESIGN.md §1.2 and §7 T1/T7):

- every precision of 1 ms or finer is translated at its TRUE SI size: a 10 ps tick is
  `10 ps`, a 10 ns tick `10 ns` (they were `1 ps` / `1 ns`, so a design at 1ns/10ps ran
  10x fast in SI time: +vcs+finish+N stopped 10x late and, in AMS mode, the digital ran
  10x fast against the analog); a precision coarser than 1 ms keeps the deliberate
  compression to 1 ms per tick;
- %t of a real argument ($realtime, a real variable) goes to sv_tstr's real overload: no
  integer() cast, so no Fatal past 2^31 precision ticks (2.147 ms at 1 ps, 2.147 us at
  1 fs), and a $timeformat finer than the precision shows every digit, as vvp;
- %t's default unit is the smallest precision of the whole design, not the scope's;
- $time and $stime round to the scope's time unit (#56.93 at 1ns/1ps is 57), as vvp;
- an empty (or escaped) $timeformat suffix prints as vvp prints it (it showed `\\000`);
- %m, the "Scope:" line of $error & co. and $time inside a function, a task or a named
  block use that scope (top.chk, top.tk, top.blk), as vvp does;
- a package function called in a process before a delay translates (tgt-vhdl crashed).

    python3 -m unittest discover -s tests/vamos -p 'test_translator_time.py' -v

The translator is bin/iverilog-sv2ghdl (the vcs personality's), with the iverilog that
vamos finds: set VAMOS_IVERILOG (and IVERILOG) to test another iverilog/tgt-vhdl build.
Linux (WSL) only: the stack is Linux ELF.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from typing import Dict, List, Optional

from vamos_testlib import ROOT, needs_stack

from vamos import tools  # noqa: E402

BIN = os.path.join(ROOT, "bin")
SHIMS = os.path.join(ROOT, "shims")

_FS = {"s": 10 ** 15, "sec": 10 ** 15, "ms": 10 ** 12, "us": 10 ** 9, "ns": 10 ** 6,
       "ps": 10 ** 3, "fs": 1}


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
        env["VAMOS_IVERILOG"] = iverilog
    return env


def _vvp() -> str:
    iverilog = tools.find_real("iverilog") or ""
    for cand in (os.path.join(os.path.dirname(os.path.realpath(iverilog)), "vvp"),
                 tools.find_real("vvp") or "", "/usr/local/src/iverilog/_install/bin/vvp"):
        if cand and os.access(cand, os.X_OK):
            return cand
    return ""


def _run(cmd: List[str], cwd: str, env: dict, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, errors="replace", timeout=timeout)


def _tagged(text: str, tags: str) -> List[str]:
    """Output lines starting with one of the one-letter tags and a blank."""
    return [ln for ln in text.splitlines() if len(ln) > 1 and ln[0] in tags and ln[1] == " "]


def _time_fs(num: str, unit: str) -> int:
    return int(num) * _FS[unit]


class Design:
    """One Verilog source translated (bin/iverilog-sv2ghdl) in its own directory, run under
    nvc (bin/vvp-sv2ghdl) and under vvp on demand."""

    def __init__(self, tmp: str, name: str, source: str):
        self.dir = os.path.join(tmp, name)
        os.makedirs(self.dir)
        self.env = _stack_env()
        self.src = os.path.join(self.dir, "t.v")
        with open(self.src, "w") as fh:
            fh.write(source)
        self.xlat = _run([os.path.join(BIN, "iverilog-sv2ghdl"), "-o", "vsim", "-g2012", self.src],
                         self.dir, self.env)
        try:
            with open(os.path.join(self.dir, "vsim", "design.vhd"), errors="replace") as fh:
                self.vhdl = fh.read()
        except OSError:
            self.vhdl = ""
        try:
            with open(os.path.join(self.dir, "vsim", "iverilog.log"), errors="replace") as fh:
                self.log = fh.read()
        except OSError:
            self.log = ""
        self._nvc = None   # type: Optional[subprocess.CompletedProcess]
        self._vvp = None   # type: Optional[subprocess.CompletedProcess]

    def nvc(self) -> subprocess.CompletedProcess:
        if self._nvc is None:
            self._nvc = _run([os.path.join(BIN, "vvp-sv2ghdl"), "vsim"], self.dir, self.env)
        return self._nvc

    def vvp(self) -> subprocess.CompletedProcess:
        if self._vvp is None:
            vvp = _vvp()
            assert vvp, "no vvp next to iverilog"
            c = _run([self.env["IVERILOG"], "-g2012", "-o", "ref.vvp", self.src], self.dir, self.env)
            assert c.returncode == 0, c.stdout + c.stderr
            self._vvp = _run([vvp, "-n", "ref.vvp"], self.dir, self.env)
        return self._vvp

    def waits_fs(self) -> List[int]:
        """Every `wait for <n> <unit>;` of design.vhd, in fs."""
        return [_time_fs(n, u) for n, u in
                re.findall(r"\bwait for (\d+) (fs|ps|ns|us|ms|sec);", self.vhdl)]


class XlatCase(unittest.TestCase):
    """A scratch directory per class; SOURCE (if any) translated once in setUpClass."""

    SOURCE = ""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vamos-xtime-")
        cls.d = Design(cls.tmp, "main", cls.SOURCE) if cls.SOURCE else None

    @classmethod
    def tearDownClass(cls):
        if os.environ.get("VAMOS_TEST_KEEP"):
            print("kept %s" % cls.tmp)
        else:
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def assertTranslated(self, d: Design) -> None:
        self.assertEqual(d.xlat.returncode, 0, d.xlat.stdout + d.xlat.stderr)
        self.assertIn("entity tb is", d.vhdl, d.log)
        self.assertNotIn("sv2vhdl:deferred", d.vhdl, d.log)

    def assertLikeVvp(self, d: Design, tags: str, count: Optional[int] = None) -> List[str]:
        n, v = d.nvc(), d.vvp()
        self.assertEqual(n.returncode, 0, n.stdout + n.stderr)
        self.assertEqual(v.returncode, 0, v.stdout + v.stderr)
        want = _tagged(v.stdout, tags)
        if count is not None:
            self.assertEqual(len(want), count, v.stdout)
        self.assertEqual(_tagged(n.stdout, tags), want,
                         "\n--- nvc:\n%s%s\n--- vvp:\n%s" % (n.stdout, n.stderr, v.stdout))
        return want

    def vcs_run(self, d: Design, *simv_args: str) -> subprocess.CompletedProcess:
        """The vcs personality on the design's source (`vcs -sverilog t.v; ./simv ...`)."""
        env = dict(d.env)
        env["PATH"] = SHIMS + os.pathsep + env.get("PATH", "")
        c = _run(["vcs", "-sverilog", "t.v"], d.dir, env)
        self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
        return _run(["./simv"] + list(simv_args), d.dir, env)


# ---------------------------------------------------------------------- the time base

# A clock (#5) and two delays in the module's unit; $time, %t, $realtime, $simtime.
TICK_SRC = """\
`timescale %s
module tb;
  reg clk = 0;
  integer n = 0;
  always #5 clk = ~clk;
  always @(posedge clk) begin
    n = n + 1;
    $display("P %%0d %%0d %%0t %%0.3f %%0d", n, $time, $realtime, $realtime, $simtime);
  end
  initial begin
    #2.5 $display("Q %%0d %%0t %%0.3f %%0d", $time, $realtime, $realtime, $simtime);
    #30 $display("R %%0d %%t %%0d", $time, $realtime, $simtime);
    $finish;
  end
endmodule
"""

# (`timescale, the unit and the precision in fs)
TIMESCALES = [
    ("1ns/1ps", 10 ** 6, 10 ** 3),
    ("1ns/10ps", 10 ** 6, 10 ** 4),
    ("1ns/100ps", 10 ** 6, 10 ** 5),
    ("10ns/10ns", 10 ** 7, 10 ** 7),
    ("1us/10ns", 10 ** 9, 10 ** 7),
    ("1us/100ns", 10 ** 9, 10 ** 8),
    ("100ps/10fs", 10 ** 5, 10),
    ("1ns/100fs", 10 ** 6, 100),
    ("10us/10us", 10 ** 10, 10 ** 10),
    ("1ms/100us", 10 ** 12, 10 ** 11),
    ("1s/10ms", 10 ** 15, 10 ** 13),        # coarser than 1 ms: compressed, 1 ms a tick
]


def tick_fs(prec_fs: int) -> int:
    """The VHDL length of one tick: the precision itself, 1 ms above 1 ms (compressed)."""
    return min(prec_fs, 10 ** 12)


@needs_stack
class TestTickIsSI(XlatCase):
    """A delay of N units is emitted as N units in SI (`#5` at 1ns/10ps is `wait for 5000
    ps`, not `500 ps`) for every precision of 1 ms or finer, and the run prints what vvp
    prints ($time, %t, $realtime, $simtime)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.designs = {}   # type: Dict[str, Design]
        for ts, _, _ in TIMESCALES:
            cls.designs[ts] = Design(cls.tmp, ts.replace("/", "_"), TICK_SRC % ts)

    def test_delays_are_si(self):
        for ts, unit, prec in TIMESCALES:
            with self.subTest(timescale=ts):
                d = self.designs[ts]
                self.assertTranslated(d)
                waits = d.waits_fs()
                tick = tick_fs(prec)                # the precision, or 1 ms above 1 ms

                def units(n: int) -> int:
                    return n * unit // prec * tick
                self.assertIn(units(5), waits, (ts, d.vhdl))
                self.assertIn(units(30), waits, (ts, d.vhdl))
                for w in waits:                     # #2.5, #5, #30: nothing 10x or 100x short
                    if w == 0:                      # the clocked process's NBA hop, not a delay
                        continue
                    self.assertTrue(units(2) <= w <= units(30), (ts, w, waits))

    def test_time_functions_use_the_same_base(self):
        for ts, unit, prec in TIMESCALES:
            with self.subTest(timescale=ts):
                d = self.designs[ts]
                tick = tick_fs(prec)
                lits = [_time_fs(n, u) for n, u in
                        re.findall(r"now(?: \+ \d+ \w+\))? / \((\d+) (fs|ps|ns|us|ms|sec)\)",
                                   d.vhdl)]
                # $simtime and $realtime divide by one tick, $time by one unit (unit/prec ticks)
                self.assertIn(tick, lits, (ts, lits))
                self.assertIn(unit // prec * tick, lits, (ts, lits))

    def test_runs_like_vvp(self):
        for ts, _, _ in TIMESCALES:
            with self.subTest(timescale=ts):
                self.assertLikeVvp(self.designs[ts], "PQR", count=5)


@needs_stack
class TestFinishStopsInSI(XlatCase):
    """vcs + ./simv +vcs+finish+N stops at N precision units of SI time: 20 ns (the posedges
    at 5 and 15 ns print, the one at 25 ns does not); a 10x-compressed base ran 10x long."""

    CASES = [("1ns/10ps", "2000"), ("1ns/100ps", "200"), ("10ns/10ns", "20")]

    def test_finish(self):
        for ts, n in self.CASES:
            with self.subTest(timescale=ts):
                d = Design(self.tmp, "fin_" + ts.replace("/", "_"), TICK_SRC % ts)
                r = self.vcs_run(d, "+vcs+finish+" + n)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                ps = [ln.split()[1] for ln in _tagged(r.stdout, "P")]
                self.assertEqual(ps, ["1", "2"], r.stdout)
                self.assertEqual(_tagged(r.stdout, "R"), [], r.stdout)


# a 10ns/100ps module inside a 1ns/10ps bench: the design's tick is 10 ps
MIXED_SRC = """\
`timescale 10ns/100ps
module sub;
  initial begin
    #1.23 $display("S %0d %0t %0.4f %0d", $time, $realtime, $realtime, $simtime);
    #2 $display("U %0d %t", $time, $realtime);
  end
endmodule
`timescale 1ns/10ps
module tb;
  sub u();
  reg clk = 0;
  always #5 clk = ~clk;
  always @(posedge clk) $display("P %0d %0t %0.3f", $time, $realtime, $realtime);
  initial begin
    #33.33 $display("R %0d %0t %0.3f %0d", $time, $realtime, $realtime, $simtime);
    $finish;
  end
endmodule
"""


@needs_stack
class TestMixedTimescales(XlatCase):
    """Two timescales, one 10 ps tick: each module's delays are SI (#1.23 of 10 ns is
    12.3 ns), the run prints what vvp prints, and +vcs+finish+2000 stops at 20 ns."""

    SOURCE = MIXED_SRC

    def test_delays_are_si(self):
        self.assertTranslated(self.d)
        waits = self.d.waits_fs()
        for want in (12300 * 10 ** 3, 5000 * 10 ** 3, 33330 * 10 ** 3):   # 12.3, 5, 33.33 ns
            self.assertIn(want, waits, self.d.vhdl)

    def test_like_vvp(self):
        self.assertLikeVvp(self.d, "SUPR", count=6)

    def test_finish(self):
        r = self.vcs_run(self.d, "+vcs+finish+2000")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(_tagged(r.stdout, "S")), 1, r.stdout)      # 12.3 ns
        self.assertEqual(len(_tagged(r.stdout, "P")), 2, r.stdout)      # 5 and 15 ns
        self.assertEqual(_tagged(r.stdout, "UR"), [], r.stdout)          # 32.3 and 33.33 ns


# ---------------------------------------------------------------------- %t of a real

@needs_stack
class TestPercentTOfRealPast2e31(XlatCase):
    """%t of $realtime and of a real variable past 2^31 precision ticks prints what vvp
    prints (it stopped the run: "value 3000000000 outside of INTEGER range"), with the
    default format, field widths and $timeformat in us, ms and fs."""

    SOURCE = """\
`timescale 1ns/1ps
module tb;
  real r;
  initial begin
    r = 3.0e6;
    $display("A [%t]", r);
    #3000000.0015;
    $display("B [%t]", $realtime);
    $display("C [%0t]", $realtime);
    $display("D [%12t]", $realtime);
    $timeformat(-6, 3, " us", 20);
    $display("E [%t]", $realtime);
    $display("F [%t]", $time);
    $timeformat(-3, 6, " ms", 20);
    $display("G [%t]", $realtime);
    $timeformat(-15, 0, " fs", 24);
    $display("H [%t]", $realtime);
    $display("I [%t]", r);
    $display("J done");
  end
endmodule
"""

    def test_like_vvp(self):
        self.assertLikeVvp(self.d, "ABCDEFGHIJ", count=10)

    def test_real_overload_no_cast(self):
        self.assertNotRegex(self.d.vhdl, r"sv_tstr\(integer\(")
        self.assertRegex(self.d.vhdl, r"sv_tstr\(r, -9, -12\)")
        self.assertRegex(self.d.vhdl, r"sv_tstr\(\(real\(\(now / \(1 ps\)\)\) / 1000\.0\), -9, -12\)")


@needs_stack
class TestPercentTOfRealAtFs(XlatCase):
    """At 1 fs precision, 2^31 ticks are 2.147 us: %0t of $realtime at 3 us prints (it was
    Fatal)."""

    SOURCE = """\
`timescale 1ns/1fs
module tb;
  initial begin
    #3000;
    $display("A %0t", $time);
    $display("B %0t", $realtime);
    $display("C done");
  end
endmodule
"""

    def test_like_vvp(self):
        self.assertLikeVvp(self.d, "ABC", count=3)


@needs_stack
class TestPercentTFinerThanPrecision(XlatCase):
    """A real %t argument under a $timeformat finer than the precision keeps the digits
    vvp shows (1.2345 ns at 1ns/1ns prints 1234.500 ps, not 1000.000 ps)."""

    SOURCE = """\
`timescale 1ns/1ns
module tb;
  real r;
  initial begin
    r = 1.2345;
    $timeformat(-12, 3, " ps", 20);
    $display("A [%t]", r);
    #3;
    $display("B [%t]", $realtime);
    $timeformat(-15, 0, " fs", 20);
    $display("C [%t]", r);
    $display("D [%t]", -r);
  end
endmodule
"""

    def test_like_vvp(self):
        got = self.assertLikeVvp(self.d, "ABCD", count=4)
        self.assertEqual(got[0], "A [         1234.500 ps]")


@needs_stack
class TestPercentTRealRounding(XlatCase):
    """A real %t argument is printed as vvp's "%.0f" prints it: ties to even (2.5 -> 2),
    a negative zero, and a value past 2^31 at t=0 (the integer() cast gave 3, 0 and a
    Fatal)."""

    SOURCE = """\
`timescale 1ns/1ns
module tb;
  real r;
  initial begin
    r = 2.5;
    $display("A [%0t]", r);
    r = 3.5;
    $display("B [%0t]", r);
    r = -0.4;
    $display("C [%0t]", r);
    r = 3.0e9;
    $display("D [%0t]", r);
  end
endmodule
"""

    def test_like_vvp(self):
        got = self.assertLikeVvp(self.d, "ABCD", count=4)
        self.assertEqual(got[0], "A [2]")


# ---------------------------------------------------------------------- %t's default unit

@needs_stack
class TestDefaultTimeUnitIsTheDesigns(XlatCase):
    """Without $timeformat, %t prints in the smallest precision of the whole design (IEEE
    1364 17.3.2, vvp), also in a module with a coarser precision (a 1us/1ns module in a
    1 ps design printed 1.5 us as 1500 instead of 1500000)."""

    SOURCE = """\
`timescale 1us/1ns
module slow;
  initial begin
    #1.5;
    $display("S %t|%0t|%0t|%t", $time, $time, $realtime, $realtime);
  end
endmodule
`timescale 1ns/1ns
module leaf;
  initial #3 $display("L %0t|%0t", $time, $realtime);
endmodule
`timescale 1ns/1ns
module mid;
  leaf l();
  initial #4 $display("M %0t|%0t", $time, $realtime);
endmodule
`timescale 1ns/1ps
module tb;
  slow s();
  mid m();
  initial begin
    #1500.5;
    $display("T %t|%0t|%0t|%t", $time, $time, $realtime, $realtime);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assertLikeVvp(self.d, "SLMT", count=4)

    def test_design_precision_argument(self):
        # the architectures tb elaborates (design.vhd also holds each module translated on
        # its own, as its own root, where its precision is the design's)
        archs = {m.group(1).lower(): m.group(2) for m in
                 re.finditer(r"^architecture \w+ of (\w+) is\n(.*?)^end architecture;",
                             self.d.vhdl, re.M | re.S)}
        seen, todo = set(), ["tb"]
        while todo:
            e = todo.pop()
            if e not in seen and e in archs:
                seen.add(e)
                todo += [x.lower() for x in re.findall(r"entity work\.(\w+)", archs[e])]
        self.assertEqual(len(seen), 4, sorted(seen))
        calls = re.findall(r"sv_tstr\([^;]*?, (-?\d+), (-?\d+)\)",
                           "\n".join(archs[e] for e in seen))
        self.assertEqual(len(calls), 12, calls)
        self.assertEqual(set(p for _, p in calls), {"-12"}, calls)


# ---------------------------------------------------------------------- $time rounds

@needs_stack
class TestTimeRounds(XlatCase):
    """$time and $stime round to the unit, half up, as vvp (and IEEE 1364 17.7.1): 56.93 ns
    is 57, 57.5 ns is 58 (they truncated: 56, 57)."""

    SOURCE = """\
`timescale 1ns/1ps
module tb;
  time t;
  initial begin
    #56.93;
    $display("A %0d", $time);
    t = $time;
    $display("B %0d", t);
    $display("C %0d", $stime);
    #0.07;
    $display("D %0d", $time);
    #0.5;
    $display("E %0d %0t", $time, $time);
    #0.499;
    $display("F %0d", $time);
    #0.001;
    $display("G %0d", $time);
  end
endmodule
"""

    def test_like_vvp(self):
        got = self.assertLikeVvp(self.d, "ABCDEFG", count=7)
        self.assertEqual(got[0], "A 57")

    def test_vhdl(self):
        self.assertIn("((now + 500 ps) / (1000 ps))", self.d.vhdl)


@needs_stack
class TestRoundedTimePast2e31(XlatCase):
    """The rounded $time quotient keeps its 64 bits past 2^31 units (3e9 ns at 3 s) in %t,
    %0d, a `time` variable and arithmetic, as the truncating one did."""

    SOURCE = """\
`timescale 1ns/1ps
module tb;
  time t;
  initial begin
    #3000000000.4;
    $display("A %t", $time);
    $display("B %0d", $time);
    t = $time;
    $display("C %0d", t);
    $display("D %0t", $realtime);
    $timeformat(-6, 3, " us", 20);
    $display("E %t", $time);
    $display("F %0d", $time + 1);
    $display("G done");
  end
endmodule
"""

    def test_like_vvp(self):
        got = self.assertLikeVvp(self.d, "ABCDEFG", count=7)
        self.assertEqual(got[1], "B 3000000000")


# ---------------------------------------------------------------------- $timeformat suffix

@needs_stack
class TestTimeformatSuffix(XlatCase):
    """An empty $timeformat suffix is empty (it printed `\\000`), and an escaped character
    in a suffix is that character."""

    SOURCE = """\
`timescale 1ns/1ps
module tb;
  initial begin
    #5;
    $timeformat(-12, 0, "", 10);
    $display("A [%t]", $time);
    $timeformat(-9, 2, "", 8);
    $display("B [%t]", $realtime);
    $timeformat(-9, 1, " n\\163", 9);
    $display("C [%t]", $time);
  end
endmodule
"""

    def test_like_vvp(self):
        got = self.assertLikeVvp(self.d, "ABC", count=3)
        self.assertEqual(got[0], "A [      5000]")

    def test_vhdl(self):
        self.assertNotIn("\\000", self.d.vhdl)
        self.assertIn('sv_set_timeformat(-12, 0, "", 10)', self.d.vhdl)
        self.assertIn('sv_set_timeformat(-9, 1, " ns", 9)', self.d.vhdl)


# ---------------------------------------------------------------------- scope names

_SEV_RE = re.compile(r"^(INFO|WARNING|ERROR|FATAL): \S+:(\d+): ")
# nvc prints the subprogram of a report issued inside one ("   Function CHK [...] at
# design.vhd:34"): sv_display_line prints through report; vvp has no such line.
_TRACE_RE = re.compile(r"^\s+(Function|Procedure) \S+ \[.*\] at \S+$")


def _scope_lines(text: str) -> List[str]:
    """Output lines with the source path of severity messages normalised (vvp names the
    file as given, the translation _norm.sv), without nvc's report lines ("** ..."), the
    bare INFO note and subprogram trace lines."""
    out = []
    for line in text.splitlines():
        if line.startswith("** ") or line == "INFO" or "$finish called" in line:
            continue
        if _TRACE_RE.match(line):
            continue
        out.append(_SEV_RE.sub(lambda m: "%s: FILE:%s: " % (m.group(1), m.group(2)), line))
    return out


@needs_stack
class TestScopeOfFunctionTaskBlock(XlatCase):
    """%m and the "Scope:" line of $error/$warning/$info name the function, task or named
    block they are in (top.chk, top.tk, top.blk, top.blk.inner, top.u1.f, top.gl[0].ab), as
    vvp does (they named the module, or nothing in a function); $time in a function is in
    the module's unit (it read 0)."""

    SOURCE = """\
`timescale 1ns/1ns
module sub;
  function integer f(input integer v);
    begin
      $display("F %m %0d %0d", v, $time);
      f = v;
    end
  endfunction
  integer q;
  initial #2 q = f(4);
endmodule
module tb;
  sub u1();
  function integer chk(input integer v);
    begin
      if (v > 2) $error("too big %0d in %m", v);
      chk = v * 2;
    end
  endfunction
  task tk(input integer v);
    begin
      if (v > 2) $warning("task %0d in %m", v);
    end
  endtask
  integer r;
  initial begin : blk
    r = chk(1);
    #3 r = chk(5);
    tk(7);
    $info("in %m");
    begin : inner
      $display("B %m");
    end
    $display("r=%0d %m", r);
  end
  for (genvar g = 0; g < 1; g = g + 1) begin : gl
    initial begin : ab
      #4 $display("G %m");
    end
  end
endmodule
"""

    def test_like_vvp(self):
        n, v = self.d.nvc(), self.d.vvp()
        self.assertEqual(v.returncode, 0, v.stdout)
        want = _scope_lines(v.stdout)
        self.assertIn("       Time: 3  Scope: tb.chk", want)
        self.assertEqual(_scope_lines(n.stdout), want,
                         "\n--- nvc:\n%s\n--- vvp:\n%s" % (n.stdout, v.stdout))


@needs_stack
class TestPackageFunctionThenDelay(XlatCase):
    """A package function drawn on demand inside a process no longer leaves the process
    without its entity: the delay after the call translates (tgt-vhdl crashed, and the
    simulation did nothing)."""

    SOURCE = """\
`timescale 1ns/1ps
package pk;
  function integer pf(input integer v);
    pf = v + 1;
  endfunction
endpackage
module tb;
  integer x;
  initial begin
    #1;
    x = pk::pf(1);
    #1.5;
    $display("D %0d %0d", x, $time);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assertTranslated(self.d)
        self.assertNotIn("Segmentation fault", self.d.log)
        self.assertLikeVvp(self.d, "D", count=1)


if __name__ == "__main__":
    unittest.main()
