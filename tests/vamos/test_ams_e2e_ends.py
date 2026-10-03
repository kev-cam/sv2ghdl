"""vcs-ams end-to-end tests: how a co-simulation ends, the top, concurrent runs, stop times.

docs/VAMOS_AMS_DESIGN.md §9, end-to-end items
   4  $finish before the .tran stop: a fast end with rc 0; $stop; $fatal (rc != 0);
      `initial $finish` at t=0
  15  a use_spice cell that is never instantiated, no -top: the top stays tb (a
      SPICE-only cell, and a multi-view cell whose Verilog view is in the sources)
  16  two concurrent ./simv runs of one build, from two directories, with different
      +plusargs: both rc 0, each <prefix>.raw equal to its solo run
  19  a mid-transient analog failure in HSPICE syntax inside the test-1 cell: the
      compile succeeds, ./simv fails, nothing is published (the deck is first shown
      to fail standalone on the engine)
  30  decks with `.tran 1n 3.3u` and `.tran 1n '1u/3'` end with "analog end" and a
      filled rawfile header; +vcs+finish+N before the deck stop publishes a rawfile
      whose No. Points: matches its data

Beyond the letter of §9: item 4 also ends at an odd time (612.345 ns), at a $finish
triggered by an A2D change and at a $fatal during the t=0 settle; item 16 uses an output
prefix with a directory (choose -o) and a behavioral source (VACASK compiles it in the
run directory); test_item30_stop_beyond_32bit_fs holds the §6 stop-time contract for a
deck stop and a +vcs+finish+N above 2^32 fs (4.294967295 us).

Every test runs on each available engine (VACASK and Xyce).  They need the whole
stack (nvc, iverilog, VACASK and/or Xyce): Linux/WSL.

    cd /usr/local/src/sv2ghdl
    python3 -m unittest discover -s tests/vamos -p 'test_ams_e2e_ends.py' -v

The circuit is the test-1 cell: a 10 kOhm / 1 pF low-pass behind the D2A (500.7 Ohm
series resistance) with a unity buffer to the A2D.  The deck's only supply is a
1.8 V source, so every IE gets hiv 1.8 V and a 0.9 V threshold (§3.3 step 4).  The
buffered output crosses 0.9 V ln(2)*tau = 7.28 ns after each clock edge.
"""

from __future__ import annotations

import glob
import json
import math
import os
import re
import shutil
import subprocess
import time
import unittest
from typing import List, Optional, Tuple

from ams_e2e_lib import AmsCase, needs_ams

# -- fixtures ---------------------------------------------------------------------------------

TITLE = "* vcs-ams e2e (test-1 cell): RC low-pass with a buffer, 1.8 V supply\n"
SUPPLY = "vsup vdd 0 1.8\nrload vdd 0 1meg\n"
RC_CELL = """\
.subckt rc_cell in out
r1 in mid 10k
c1 mid 0 1p
e1 out 0 mid 0 1
{EXTRA}.ends
"""
INIT = "choose xa rc.sp;\n"

# §9 item 19: the comparator loop of the C-side Xyce test, written in HSPICE syntax and
# placed inside the test-1 cell (instance and node names changed so that they do not
# collide with the cell's own r1/c1/out).
FAIL_LINES = """\
ecmp cmp 0 vol='if(time < 150n, 0, if(v(cap) > 0.5, 0, 1))'
rcmp cmp cap 1k
ccmp cap 0 1p
"""


def netlist(tran: str, cell_extra: str = "", extra: str = "") -> str:
    return TITLE + SUPPLY + RC_CELL.replace("{EXTRA}", cell_extra) + extra + ".tran %s\n" % tran


TB = """\
`timescale 1ns/1ps
module tb;
  reg clk = 0;
  wire out;
  logic seen;
  always #50 clk = ~clk;
  rc_cell u1 (.in(clk), .out(out));
  always @(out) begin seen = out; $display("%0t out=%b", $time, out); end
{BODY}
endmodule
"""


def testbench(body: str) -> str:
    return TB.replace("{BODY}", body)


# Item 4: one build; a plusarg picks how the digital side ends the run.
TB_ENDS = testbench("""\
  initial begin
    if ($test$plusargs("t0")) $finish;
    else if ($test$plusargs("fatal0")) $fatal(1, "e2e4 fatal at t=0");
    else if ($test$plusargs("odd")) #612.345 $finish;
    else if (!$test$plusargs("a2d")) begin
      #600;
      if ($test$plusargs("stop")) $stop;
      else if ($test$plusargs("fatal")) $fatal(1, "e2e4 fatal at %0t", $time);
      else $finish;
    end
  end
  // a $finish triggered by an A2D sample: the first rising crossing after 300 ns (357.3 ns)
  always @(posedge out) if ($test$plusargs("a2d") && $time > 300) $finish;""")

# Item 16: the clock half period and the end time come from plusargs.
TB_PLUSARGS = """\
`timescale 1ns/1ps
module tb;
  reg clk = 0;
  wire out;
  logic seen;
  integer half;
  rc_cell u1 (.in(clk), .out(out));
  initial begin
    if (!$value$plusargs("half=%d", half)) half = 50;
    $display("half=%0d", half);
    forever #(half) clk = ~clk;
  end
  initial begin
    if ($test$plusargs("long")) #3600 $finish;
    else #3000 $finish;
  end
  always @(out) begin seen = out; $display("%0t out=%b", $time, out); end
endmodule
"""

# Item 15: a cell nobody instantiates, named by use_spice.
GHOST_SUBCKT = """\
.subckt ghost a y
rg a y 1k
cg y 0 1p
.ends
"""
GHOST_V = """\
`timescale 1ns/1ps
// The Verilog view of ghost: never instantiated.  use_spice makes it a cut cell, and a
// cut cell is never the top (§1.4), although it is a structural root like tb.
module ghost (input a, output y);
  assign #1 y = a;
endmodule
"""
INIT_GHOST = INIT + "use_spice -cell ghost;\n"

# -- expected values ------------------------------------------------------------------------

VDD = 1.8
TAU = (10e3 + 500.7) * 1e-12                 # R1 + the D2A series resistance, times C1
DELAY = math.log(2.0) * TAU                  # edge -> 0.9 V crossing of the buffered output


def charged(dt: float) -> float:
    return VDD * (1.0 - math.exp(-dt / TAU))


def discharged(dt: float) -> float:
    return VDD * math.exp(-dt / TAU)


def expected_events(half_ns: float, end_ns: float) -> List[Tuple[float, str]]:
    """(time in ps, value) of every A2D change after t=0 for a clock of half period half_ns."""
    out = []
    m = 1
    while m * half_ns + DELAY * 1e9 < end_ns:
        out.append(((m * half_ns + DELAY * 1e9) * 1e3, "1" if m % 2 else "0"))
        m += 1
    return out


# -- output helpers ------------------------------------------------------------------------

# nvc's own end lines ("** Note: ..." / "** Error: ..."); a vamos error that repeats a
# failure line ("vamos: error: co-simulation failed: ** Error: ...") is not one
_END_LINE = re.compile(r"^\*\* (?:Note|Error): .*(?:co-simulation finished:|transient failed at|"
                       r"co-simulation stalled at|co-simulation interrupted at)")
_ANALOG_END = re.compile(r"^\*\* Note: co-simulation finished: analog end at ([-+0-9.eE]+) s$")
_DIGITAL_STOP = re.compile(r"^\*\* Note: co-simulation finished: digital stop at ([-+0-9.eE]+) s$")
DIGITAL_STOP0 = ("** Note: co-simulation finished: digital stop at 0 s "
                 "(before the first analog step)")
NO_ANALOG = "vamos: note: no analog output: the digital stopped at t=0"
REWRITTEN = "rawfile header point count rewritten"
RAW = "vamos_ams.raw"


def end_lines(out: str) -> List[str]:
    return [ln.strip() for ln in out.splitlines() if _END_LINE.search(ln)]


def header_points(path: str) -> Tuple[Optional[int], int]:
    """(the first plot's No. Points: as written, the points its data holds), read with no help
    from vamos.netlist.rawfile (whose reader counts from the data whatever the header says)."""
    with open(path, "rb") as fh:
        data = fh.read()
    m = re.search(rb"No\. Points:[ \t]*([^\r\n]*)", data)
    field = m.group(1).strip() if m else b""
    declared = int(field) if field.isdigit() else None
    nvars = int(re.search(rb"No\. Variables:[ \t]*(\d+)", data).group(1))
    flags = re.search(rb"Flags:[ \t]*([^\r\n]*)", data)
    width = 2 if flags and b"complex" in flags.group(1).lower() else 1
    mb = re.search(rb"\nBinary:[ \t]*\r?\n", data)
    if mb:
        nbytes = len(data) - mb.end()
        row = nvars * width * 8
        if nbytes % row:
            raise AssertionError("%s: %d data bytes are not a whole number of %d-byte points"
                                 % (path, nbytes, row))
        return declared, nbytes // row
    mv = re.search(rb"\nValues:[ \t]*\r?\n", data)
    if not mv:
        raise AssertionError("%s: no Binary: or Values: section" % path)
    return declared, len(data[mv.end():].split()) // (nvars + 1)


def run_dirs(d: str) -> List[str]:
    return sorted(p for p in glob.glob(os.path.join(d, "vamos_ams.run.*")) if os.path.isdir(p))


class EndsCase(AmsCase):
    """Shared assertions on runs."""

    def one_end_line(self, out: str) -> str:
        lines = end_lines(out)
        self.assertEqual(len(lines), 1, "expected exactly one end-of-run line:\n%s" % out)
        return lines[0]

    def assert_digital_stop(self, out: str, t: float) -> None:
        line = self.one_end_line(out)
        m = _DIGITAL_STOP.match(line)
        self.assertIsNotNone(m, "not a digital-stop end line: %r\n%s" % (line, out))
        self.assertAlmostEqual(float(m.group(1)), t, delta=t * 1e-9 + 1e-21, msg=line)

    def assert_analog_end(self, out: str, t: float) -> None:
        line = self.one_end_line(out)
        m = _ANALOG_END.match(line)
        self.assertIsNotNone(m, "not an analog-end end line: %r\n%s" % (line, out))
        # the line prints the engine's time rounded to the digital's femtosecond clock,
        # exactly (nvc src/cosim.c time_text); 1e-8 relative is a generous bound
        self.assertAlmostEqual(float(m.group(1)), t, delta=t * 1e-8, msg=line)

    def assert_events(self, out: str, want: List[Tuple[float, str]], tag: str = "out") -> None:
        got = [(t, v) for t, v in self.display_events(out, tag) if t > 0]
        self.assertEqual([v for _, v in got], [v for _, v in want],
                         "A2D values after t=0: got %s, want %s" % (got, want))
        for (tg, _), (tw, _) in zip(got, want):
            self.assertAlmostEqual(tg, tw, delta=1000.0, msg="A2D change at %s ps, want ~%.0f ps"
                                   % (tg, tw))

    def assert_header_matches(self, path: str) -> int:
        declared, counted = header_points(path)
        self.assertIsNotNone(declared, "%s: blank No. Points: field" % path)
        self.assertEqual(declared, counted, "%s: No. Points: %s but the data holds %d points"
                         % (path, declared, counted))
        from vamos.netlist import rawfile
        self.assertEqual(len(rawfile.read(path).points), counted)
        return counted


# -- §9 item 4 ------------------------------------------------------------------------------------

@needs_ams
class TestE2E04RunEnds(EndsCase):
    """$finish / $stop / $fatal at 600 ns with the deck stop at 4 us, and a stop at t=0.

    Beyond the §9 list, the same build also ends at an odd time (612.345 ns, between analog
    steps) and at a $finish triggered by an A2D change (the time comes from the analog side)."""

    def test_item4_run_ends(self):
        for engine in self.engines():
            with self.subTest(engine=engine):
                d = self.case("ends_" + engine, {"tb.sv": TB_ENDS, "rc.sp": netlist("1n 4u"),
                                                 "vcsAD.init": INIT})
                self.compile(d, "-sverilog", "tb.sv", engine=engine)
                with self.subTest(end="$finish"):
                    self._finish(d)
                with self.subTest(end="$stop"):
                    self._stop(d)
                with self.subTest(end="$fatal"):
                    self._fatal(d)
                with self.subTest(end="$finish at 612.345 ns"):
                    self._odd(d)
                with self.subTest(end="$finish on an A2D change"):
                    self._a2d(d)
                with self.subTest(end="initial $finish (t=0)"):
                    self._t0(d)
                with self.subTest(end="$fatal at t=0"):
                    self._fatal0(d)

    def _clean(self, d: str) -> None:
        for p in [os.path.join(d, RAW)] + run_dirs(d):
            if os.path.isdir(p):
                shutil.rmtree(p)
            elif os.path.exists(p):
                os.remove(p)

    def _analog_until_600ns(self, d: str) -> None:
        raw = self.raw(d)
        last = raw.last_time()
        # a fast end: the analog stops with the digital at 600 ns, not at the 4 us deck stop
        self.assertGreaterEqual(last, 600e-9 * (1 - 1e-9))
        self.assertLessEqual(last, 602e-9, "the analog ran on after the digital stop")
        # clock low 500-550 ns, high 550-600 ns
        self.assertAlmostEqual(raw.at("n_u1_out", 549e-9), discharged(49e-9 - 10e-12), delta=0.02)
        self.assertAlmostEqual(raw.at("n_u1_out", 599e-9), charged(49e-9 - 10e-12), delta=0.02)
        self.assertAlmostEqual(raw.at("n_u1_in", 599e-9), VDD, delta=0.02)
        self.assertAlmostEqual(raw.at("vdd", 300e-9), VDD, delta=1e-9)

    def _finish(self, d: str) -> None:
        r = self.simv(d)
        self.assert_digital_stop(r.stdout, 600e-9)
        self.assertNotIn("vamos: error", r.stdout)
        self.assert_events(r.stdout, expected_events(50, 600))
        self._analog_until_600ns(d)
        self.assertEqual(run_dirs(d), [], "the run directory of a clean run is removed")

    def _stop(self, d: str) -> None:
        self._clean(d)
        r = self.simv(d, "+stop")
        self.assertIn("STOP called", r.stdout)          # T1: $stop -> std.env.stop
        self.assertNotIn("FINISH called", r.stdout)
        self.assert_digital_stop(r.stdout, 600e-9)
        self.assert_events(r.stdout, expected_events(50, 600))
        self._analog_until_600ns(d)

    def _fatal(self, d: str) -> None:
        self._clean(d)
        r = self.simv(d, "+fatal", expect_rc=None)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertRegex(r.stdout, r"FATAL: .*e2e4 fatal at\s+600000")   # T1 prints the message
        self.assert_digital_stop(r.stdout, 600e-9)
        self.assert_events(r.stdout, expected_events(50, 600))
        m = re.search(r"vamos: note: run directory kept: (\S+)", r.stdout)
        self.assertIsNotNone(m, "a failed run keeps its run directory:\n" + r.stdout)
        self.assertTrue(os.path.isdir(m.group(1)), m.group(1))

    def _odd(self, d: str) -> None:
        """A stop between analog steps (612.345 ns) is landed on, not rounded to a step."""
        self._clean(d)
        r = self.simv(d, "+odd")
        self.assert_digital_stop(r.stdout, 612.345e-9)
        self.assert_events(r.stdout, expected_events(50, 612.345))
        raw = self.raw(d)
        self.assertGreaterEqual(raw.last_time(), 612.345e-9 * (1 - 1e-9))
        self.assertLessEqual(raw.last_time(), 614.345e-9, "the analog ran on after the stop")
        # clock low from 600 ns: 12.3 ns of discharge from ~1.785 V
        self.assertAlmostEqual(raw.at("n_u1_out", 612.345e-9),
                               charged(50e-9) * math.exp(-(12.345e-9 - 5e-12) / TAU), delta=0.02)

    def _a2d(self, d: str) -> None:
        """A $finish caused by an A2D change ends the run at that analog time point."""
        self._clean(d)
        r = self.simv(d, "+a2d")
        line = self.one_end_line(r.stdout)
        m = _DIGITAL_STOP.match(line)
        self.assertIsNotNone(m, "not a digital-stop end line: %r\n%s" % (line, r.stdout))
        stop = float(m.group(1))
        raw = self.raw(d)
        rise = [t for t in raw.crossings("n_u1_out", 0.9, +1) if t > 300e-9]
        self.assertEqual(len(rise), 1, "the analog ran past the A2D-triggered stop: %s" % rise)
        self.assertAlmostEqual(rise[0], 350e-9 + DELAY, delta=0.3e-9)
        # the A2D sees the crossing at the end of the analog step that crossed
        self.assertGreaterEqual(stop, rise[0])
        self.assertLess(stop, rise[0] + 3e-9)
        self.assertGreaterEqual(raw.last_time(), stop * (1 - 1e-9))
        self.assertLessEqual(raw.last_time(), stop + 2e-9)
        self.assert_events(r.stdout, expected_events(50, 358))

    def _t0(self, d: str) -> None:
        self._clean(d)
        r = self.simv(d, "+t0")
        self.assertEqual(self.one_end_line(r.stdout), DIGITAL_STOP0, r.stdout)
        self.assertEqual([e for e in self.display_events(r.stdout, "out") if e[0] > 0], [])
        published = os.path.isfile(os.path.join(d, RAW))
        # §6: "no rawfile is required (one that exists is published; a missing one gets the
        # note ...)".  VACASK offers t=0 after its operating point and writes that one point
        # (E1 deviation 1), Xyce runs no transient at all (E1 open issue 4).  §9 item 4 asks
        # for the note on both engines, which contradicts §6 for VACASK (doc finding): this
        # test follows §6 and checks whichever outcome the engine produced.
        if published:
            self.assertNotIn(NO_ANALOG, r.stdout)
            raw = self.raw(d)
            self.assertEqual(raw.last_time(), 0.0, "a stop at t=0 must not simulate past t=0")
            self.assertEqual(len(raw.points), 1)
            self.assertAlmostEqual(raw.at("vdd", 0.0), VDD, delta=1e-9)
            self.assertAlmostEqual(raw.at("n_u1_in", 0.0), 0.0, delta=1e-6)
            self.assertAlmostEqual(raw.at("n_u1_out", 0.0), 0.0, delta=1e-6)
        else:
            self.assertIn(NO_ANALOG, r.stdout)

    def _fatal0(self, d: str) -> None:
        """$fatal during the t=0 settle: the t=0 end line, and still a failure."""
        self._clean(d)
        r = self.simv(d, "+fatal0", expect_rc=None)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertRegex(r.stdout, r"FATAL: .*e2e4 fatal at t=0")
        self.assertEqual(self.one_end_line(r.stdout), DIGITAL_STOP0, r.stdout)
        self.assertEqual([e for e in self.display_events(r.stdout, "out") if e[0] > 0], [])
        self.assertRegex(r.stdout, r"vamos: note: run directory kept: ")


# -- §9 item 15 -----------------------------------------------------------------------------------

@needs_ams
class TestE2E15UnusedUseSpice(EndsCase):
    """use_spice -cell ghost, never instantiated, and no -top: the top stays tb."""

    def test_item15_spice_only_cell(self):
        self._check("spice", {"tb.sv": testbench("  initial #300 $finish;"),
                              "rc.sp": netlist("1n 1u", extra=GHOST_SUBCKT),
                              "vcsAD.init": INIT_GHOST}, ["tb.sv"])

    def test_item15_multi_view_cell(self):
        # ghost.v comes first on the command line: a least-referenced-module guess would see
        # two roots (ghost, tb) and could take either.
        self._check("multi", {"ghost.v": GHOST_V, "tb.sv": testbench("  initial #300 $finish;"),
                              "rc.sp": netlist("1n 1u", extra=GHOST_SUBCKT),
                              "vcsAD.init": INIT_GHOST}, ["ghost.v", "tb.sv"])

    def _check(self, name: str, files: dict, sources: List[str]) -> None:
        for engine in self.engines():
            with self.subTest(engine=engine):
                d = self.case("%s_%s" % (name, engine), files)
                c = self.compile(d, "-sverilog", *sources, engine=engine)
                self.assertRegex(c.stdout, r"vamos: note: .*cell ghost not instantiated")
                self.assertEqual(self._top_level_modules(c.stdout), ["tb"], c.stdout)
                self.assertIn("vamos: AMS: 1 SPICE instance(s), 2 analog node(s), 3 bridge(s)",
                              c.stdout)
                with open(os.path.join(d, "simv.daidir", "vamos.job.json")) as fh:
                    self.assertEqual(json.load(fh)["tops"], ["tb"])
                with open(os.path.join(d, "simv.daidir", "ams", "ams.json")) as fh:
                    plan = json.load(fh)
                self.assertEqual(plan["top"], "tb")
                self.assertEqual([i["vpath"] for i in plan["instances"]], ["tb.u1"])
                rpt = self.report(d)
                self.assertRegex(rpt, r"(?m)^d2a hiv=1\.8 lov=0(\.0)? .*node=tb\.u1\.in;$")
                self.assertRegex(rpt, r"(?m)^a2d loth=0\.9 hith=0\.9 node=tb\.u1\.out;$")
                self.assertNotIn("ghost", rpt)

                r = self.simv(d)
                self.assert_digital_stop(r.stdout, 300e-9)
                self.assert_events(r.stdout, expected_events(50, 300))
                raw = self.raw(d)
                self.assertAlmostEqual(raw.at("n_u1_out", 299e-9), charged(49e-9 - 10e-12),
                                       delta=0.02)
                self.assertAlmostEqual(raw.at("n_u1_out", 249e-9), discharged(49e-9 - 10e-12),
                                       delta=0.02)

    @staticmethod
    def _top_level_modules(out: str) -> List[str]:
        lines = out.splitlines()
        for i, ln in enumerate(lines):
            if ln.strip() == "Top Level Modules:":
                tops = []
                for nxt in lines[i + 1:]:
                    if not nxt.startswith(" ") or not nxt.strip():
                        break
                    tops.append(nxt.strip())
                return tops
        return []


# -- §9 item 16 -----------------------------------------------------------------------------------

@needs_ams
class TestE2E16ConcurrentRuns(EndsCase):
    """One build, two run directories, different +plusargs: solo, then both at once.

    The control file gives an output prefix with a directory (choose -o waves/e2e16): each
    run publishes <its cwd>/waves/e2e16.raw and keeps its per-run directory beside it.  The
    cell has a behavioral source, so VACASK writes and compiles <deck>__behavioral.va in its
    cwd on every run: the reason runs need their own directories (§6)."""

    RUNS = (("ra", ["+half=50"], 50, 3000), ("rb", ["+half=80", "+long"], 80, 3600))
    PREFIX = os.path.join("waves", "e2e16")
    BEHAVIORAL = "ehalf hv 0 vol='0.5*v(mid)'\n"

    def test_item16_concurrent_runs(self):
        from vamos.netlist import rawfile
        for engine in self.engines():
            with self.subTest(engine=engine):
                d = self.case("conc_" + engine, {
                    "build/tb.sv": TB_PLUSARGS,
                    "build/rc.sp": netlist("1n 4u", cell_extra=self.BEHAVIORAL),
                    "build/vcsAD.init": "choose xa rc.sp -o %s;\n" % self.PREFIX})
                build = os.path.join(d, "build")
                self.compile(build, "-sverilog", "tb.sv", engine=engine)
                exe = os.path.join(build, "simv")

                solo = {}
                for sub, args, half, end in self.RUNS:
                    rd = os.path.join(d, sub)
                    os.makedirs(rd)
                    r = self.simv(rd, *args, exe=exe)
                    self.assertIn("half=%d" % half, r.stdout)
                    self.assert_digital_stop(r.stdout, end * 1e-9)
                    self.assert_events(r.stdout, expected_events(half, end))
                    published = os.path.join(rd, self.PREFIX + ".raw")
                    self.assertFalse(os.path.exists(os.path.join(rd, RAW)))
                    raw = rawfile.read(published)
                    self.assertAlmostEqual(raw.last_time(), end * 1e-9, delta=2e-9)
                    rise = raw.crossings("n_u1_out", 0.9, +1)
                    self.assertAlmostEqual(rise[0], half * 1e-9 + DELAY, delta=0.3e-9)
                    t = half * 1e-9 + 20e-9
                    self.assertAlmostEqual(raw.at("xv_u1.hv", t), 0.5 * raw.at("n_u1_out", t),
                                           delta=1e-6)
                    os.replace(published, os.path.join(rd, "solo.raw"))
                    solo[sub] = (raw, self.display_events(r.stdout, "out"))
                self.assertNotEqual(solo["ra"][0].points, solo["rb"][0].points)

                procs, logs, started, ended = {}, {}, {}, {}
                env = self.child_env()
                try:
                    for sub, args, _, _ in self.RUNS:
                        rd = os.path.join(d, sub)
                        logs[sub] = open(os.path.join(rd, "conc.log"), "w")
                        started[sub] = time.time()
                        procs[sub] = subprocess.Popen([exe] + args, cwd=rd, env=env,
                                                      stdout=logs[sub], stderr=subprocess.STDOUT)
                    deadline = time.time() + 1200
                    while len(ended) < len(procs) and time.time() < deadline:
                        for sub, p in procs.items():
                            if sub not in ended and p.poll() is not None:
                                ended[sub] = time.time()
                        time.sleep(0.02)
                finally:
                    for sub, p in procs.items():
                        if p.poll() is None:
                            p.kill()
                        p.wait()
                        logs[sub].close()
                self.assertEqual(sorted(ended), ["ra", "rb"], "a concurrent run hung")
                self.assertLess(max(started.values()), min(ended.values()),
                                "the two runs did not overlap")

                for sub, args, half, end in self.RUNS:
                    rd = os.path.join(d, sub)
                    with open(os.path.join(rd, "conc.log"), errors="replace") as fh:
                        out = fh.read()
                    self.assertEqual(procs[sub].returncode, 0, "%s %s:\n%s" % (sub, args, out))
                    self.assert_digital_stop(out, end * 1e-9)
                    self.assertEqual(self.display_events(out, "out"), solo[sub][1],
                                     "%s: the digital output differs from the solo run" % sub)
                    got = rawfile.read(os.path.join(rd, self.PREFIX + ".raw"))
                    want = solo[sub][0]
                    self.assertEqual(got.plotname, want.plotname)
                    self.assertEqual(got.variables, want.variables)
                    self.assertEqual(len(got.points), len(want.points), sub)
                    self.assertTrue(got.points == want.points,
                                    "%s: the concurrent rawfile differs from the solo run" % sub)
                    left = [p for p in os.listdir(os.path.join(rd, "waves"))
                            if p.startswith("e2e16.run.")]
                    self.assertEqual(left, [], "%s: run directory left behind" % sub)


# -- §9 item 19 -----------------------------------------------------------------------------------

STANDALONE_19 = TITLE + SUPPLY + "vin in 0 0\nx1 in out rc_cell\n" + \
    RC_CELL.replace("{EXTRA}", FAIL_LINES) + ".tran 1n 1u\n"


@needs_ams
class TestE2E19AnalogFailure(EndsCase):
    """A mid-transient analog failure: rc != 0, the engine's failure line, nothing published."""

    def test_item19_analog_failure(self):
        for engine in self.engines():
            with self.subTest(engine=engine):
                d = self.case("fail_" + engine, {
                    "tb.sv": testbench("  initial #600 $finish;"),
                    "rc.sp": netlist("1n 1u", cell_extra=FAIL_LINES),
                    "vcsAD.init": INIT,
                    "standalone/fail.sp": STANDALONE_19})
                self._standalone_fails(engine, os.path.join(d, "standalone"))

                self.compile(d, "-sverilog", "tb.sv", engine=engine)  # the smoke check passes
                r = self.simv(d, expect_rc=None)
                out = r.stdout
                self.assertNotEqual(r.returncode, 0, out)
                name = {"vacask": "VACASK", "xyce": "Xyce"}[engine]
                m = re.search(r"\*\* Error: (\w+) transient failed at ([-+0-9.eE]+) s", out)
                early = re.search(r"analog output ends early at ([-+0-9.eE]+) s", out)
                self.assertTrue(m or early, "no analog failure line:\n" + out)
                if m:
                    self.assertEqual(self.one_end_line(out), m.group(0), out)
                    self.assertEqual(m.group(1), name)
                    self.assertGreaterEqual(float(m.group(2)), 149e-9)
                    self.assertLess(float(m.group(2)), 160e-9)
                self.assertEqual([ln for ln in end_lines(out) if "co-simulation finished" in ln],
                                 [], "a failed run claims a clean end:\n" + out)
                self.assertFalse(os.path.exists(os.path.join(d, RAW)),
                                 "a failed run published %s" % RAW)
                self.assertRegex(out, r"vamos: note: run directory kept: ")
                # the co-simulation ran until the comparator fired at 150 ns
                self.assert_events(out, expected_events(50, 150))

                # the same build runs cleanly when it stops before the trigger
                for p in run_dirs(d):
                    shutil.rmtree(p)
                r = self.simv(d, "+vcs+finish+120000")
                self.assert_analog_end(r.stdout, 120e-9)
                raw = self.raw(d)
                self.assertAlmostEqual(raw.last_time(), 120e-9, delta=120e-9 * 1e-9)
                self.assertAlmostEqual(raw.at("n_u1_out", 99e-9), charged(49e-9 - 10e-12),
                                       delta=0.02)

    def _standalone_fails(self, engine: str, d: str) -> None:
        """§9 item 19: first confirm on the engine alone that the deck fails."""
        from vamos.ams import engines
        from vamos.netlist import ir, spice, tables, vacask, xyce
        nl = spice.parse([os.path.join(d, "fail.sp")], [], d, ir.ParseOpts())
        if engine == "vacask":
            vacask.emit(nl, os.path.join(d, "fail.sim"), "tran1")
            cmd = [engines.vacask_bin(), "fail.sim"]
        else:
            xyce.emit(nl, os.path.join(d, "fail.cir"))
            cmd = [engines.xyce_bin(), "fail.cir"]
        env = dict(os.environ)
        env.update(engines.env_for(engine, tables.nvc_libdir(), dict(os.environ)))
        r = subprocess.run(cmd, cwd=d, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, errors="replace", timeout=600)
        self.assertNotEqual(r.returncode, 0, "the deck converges standalone on %s:\n%s"
                            % (engine, r.stdout[-3000:]))
        self.assertRegex(r.stdout, r"(?i)time ?step too small", r.stdout[-3000:])


# -- §9 item 30 -----------------------------------------------------------------------------------

TB_FREE = testbench("  // no $finish: the analog side ends the run")


@needs_ams
class TestE2E30StopTimes(EndsCase):
    """The deck stop is not a whole number of fs: the run still ends at the analog end.

    simv passes nvc --stop-time=ceil(stop*1e15)+1 fs (§6), strictly after the deck stop, so
    the engine reaches its own stop, finishes normally and fills No. Points: itself; with
    +vcs+finish+N the engine ends paused (Xyce leaves the field blank) and vamos rewrites
    the field from the data before publishing."""

    def test_item30_tran_3p3u(self):
        for engine in self.engines():
            with self.subTest(engine=engine):
                d = self.case("t33_" + engine, {"tb.sv": TB_FREE, "rc.sp": netlist("1n 3.3u"),
                                                "vcsAD.init": INIT})
                self.compile(d, "-sverilog", "tb.sv", engine=engine)
                self._deck_end(d, 3.3e-6)
                # clock high 3250-3300 ns
                self.assertAlmostEqual(self.raw(d).at("n_u1_out", 3299e-9),
                                       charged(49e-9 - 10e-12), delta=0.02)

                # +vcs+finish+N before the deck stop (N in units of the 1 ps precision)
                os.remove(os.path.join(d, RAW))
                r = self.simv(d, "+vcs+finish+1500000")
                self.assert_analog_end(r.stdout, 1.5e-6)
                path = os.path.join(d, RAW)
                n = self.assert_header_matches(path)
                raw = self.raw(d)
                self.assertEqual(len(raw.points), n)
                self.assertAlmostEqual(raw.last_time(), 1.5e-6, delta=1.5e-6 * 1e-9)
                self.assertAlmostEqual(raw.at("n_u1_out", 1499e-9), charged(49e-9 - 10e-12),
                                       delta=0.02)
                self.assert_events(r.stdout, expected_events(50, 1500))

    def test_item30_tran_1u_over_3(self):
        for engine in self.engines():
            with self.subTest(engine=engine):
                d = self.case("t13_" + engine, {"tb.sv": TB_FREE, "rc.sp": netlist("1n '1u/3'"),
                                                "vcsAD.init": INIT})
                self.compile(d, "-sverilog", "tb.sv", engine=engine)
                with open(os.path.join(d, "simv.daidir", "vamos.job.json")) as fh:
                    self.assertAlmostEqual(json.load(fh)["ams"]["stop"], 1e-6 / 3, delta=1e-21)
                self._deck_end(d, 1e-6 / 3)
                # clock low 300-350 ns
                self.assertAlmostEqual(self.raw(d).at("n_u1_out", 1e-6 / 3),
                                       discharged(1e-6 / 3 - 300e-9 - 10e-12), delta=0.02)

    def test_item30_stop_beyond_32bit_fs(self):
        # Not one of the two §9 decks: the same contract (§6: --stop-time is ceil(stop*1e15)+1
        # fs, and the run ends with "analog end" at the deck stop) for a stop whose femtosecond
        # count does not fit in 32 bits (anything above 4.294967295 us).  A count that wrapped
        # to 32 bits would end the run early: 6000000001fs at 1.7050327e-06 s and
        # +vcs+finish+4500000 (4.5e9 fs) at 2.05032704e-07 s ("analog output ends early").
        for engine in self.engines():
            with self.subTest(engine=engine):
                d = self.case("t6_" + engine, {"tb.sv": TB_FREE, "rc.sp": netlist("1n 6u"),
                                               "vcsAD.init": INIT})
                self.compile(d, "-sverilog", "tb.sv", engine=engine)
                with self.subTest(run="deck stop 6 us"):
                    r = self.simv(d, expect_rc=None)
                    self.assertEqual(r.returncode, 0, r.stdout)
                    self.assert_analog_end(r.stdout, 6e-6)
                    self.assertAlmostEqual(self.raw(d).last_time(), 6e-6, delta=6e-15)
                with self.subTest(run="+vcs+finish+4500000 (4.5 us)"):
                    if os.path.exists(os.path.join(d, RAW)):
                        os.remove(os.path.join(d, RAW))
                    r = self.simv(d, "+vcs+finish+4500000", expect_rc=None)
                    self.assertEqual(r.returncode, 0, r.stdout)
                    self.assert_analog_end(r.stdout, 4.5e-6)
                    self.assert_header_matches(os.path.join(d, RAW))
                    self.assertAlmostEqual(self.raw(d).last_time(), 4.5e-6, delta=4.5e-15)

    def _deck_end(self, d: str, stop: float) -> None:
        r = self.simv(d)
        self.assert_analog_end(r.stdout, stop)
        self.assertNotIn("vamos: error", r.stdout)
        # the engine finished normally and filled the header itself (a run that ends paused,
        # e.g. at a --stop-time before the deck stop, leaves Xyce's field blank)
        self.assertNotIn(REWRITTEN, r.stdout)
        path = os.path.join(d, RAW)
        self.assert_header_matches(path)
        self.assertAlmostEqual(self.raw(d).last_time(), stop, delta=stop * 1e-9)
        self.assert_events(r.stdout, expected_events(50, stop * 1e9))
        self.assertEqual(run_dirs(d), [])


if __name__ == "__main__":
    unittest.main()
