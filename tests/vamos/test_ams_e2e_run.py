"""vcs-ams end-to-end: how ./simv reads a co-simulation's output and ends the run.

Regression cases for the run-time rules of docs/VAMOS_AMS_DESIGN.md §6, each on every
available engine (VACASK and Xyce):

  user output   every line a testbench prints reaches stdout and the -l log unchanged
                (banners, tab-led and dashed lines, blank lines, the phrases of nvc's and
                the bridge's own lines); the run exits 0 and publishes its rawfile; a
                $fatal fails it whatever it printed before ("SIMULATION FINISHED")
  boundary      a boundary path nvc cannot resolve fails the run and says why, also with
                NVC_COLORS=always
  TSTART        a run that ends before the deck's .tran start: rc 0, a note, nothing
                published, no traceback; runs that reach the start publish from it
  finish forms  +vcs+finish+<n><unit>, +vcs+finish+<low>+<high>, +vcs+finish+0, a bad
                value (warning; an error with --vamos-strict)
  stop digits   a $finish at 2000000006 ps: the exact stop line and footer, published
  sub-fs        a $finish at the analog's own candidate time, where the engine's last point
                sits a fraction of a femtosecond below the digital stop: published
  signals       Ctrl-C (SIGINT to the process group), SIGTERM to simv, SIGINT or SIGKILL to
                nvc, SIGKILL to simv: no traceback, the right status and message, partial
                waves kept, no nvc left running
  ABI check     no nm on PATH (runs); a library directory that does not exist (an error
                naming the directories searched and the variable to set, no footer)
  concurrency   simultaneous ./simv runs of one build in one directory all finish

They need the whole stack (nvc, iverilog, VACASK and/or Xyce): Linux/WSL.

    cd /usr/local/src/sv2ghdl
    python3 -m unittest discover -s tests/vamos -p 'test_ams_e2e_run.py' -v
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import signal
import subprocess
import time
import unittest
from typing import List, Optional

from ams_e2e_lib import AmsCase, needs_ams

TITLE = "* vcs-ams e2e (test-1 cell): RC low-pass with a buffer, 1.8 V supply\n"
SUPPLY = "vsup vdd 0 1.8\nrload vdd 0 1meg\n"
RC_CELL = """\
.subckt rc_cell in out
r1 in mid 10k
c1 mid 0 1p
e1 out 0 mid 0 1
.ends
"""
INIT = "choose xa rc.sp;\n"
RAW = "vamos_ams.raw"


def netlist(tran: str) -> str:
    return TITLE + SUPPLY + RC_CELL + ".tran %s\n" % tran


TB_HEAD = """\
`timescale 1ns/1ps
module tb;
  reg clk = 0;
  wire out;
  logic seen;
  always #50 clk = ~clk;
  rc_cell u1 (.in(clk), .out(out));
  always @(out) begin seen = out; $display("%0t out=%b", $time, out); end
"""

TB_OUTPUT = TB_HEAD + """\
  initial $display("");
  initial $display("***** HEADER at t=0 *****");
  initial begin
    #100;
    $display("line 1: plain");
    $display("***** TEST PASSED *****");
    $display("");
    $display("line 4: after a blank line");
    $display("\\tcode=%0d", 42);
    $display("    -----------------");
    $display("  Supply level 3 (nominal)");
    $display("irq handler not registered yet");
    $display("retrying: PLL initialize failed once");
    $display("ELF ABI version: 0");
    $display("** Error: Xyce transient failed at 1 s");
    $display("** Note: co-simulation finished: digital stop at 1 s");
    $display("[cosim_bridge] signal 'x' not registered");
    $display("SIMULATION FINISHED");
    $write("cfg: registry full\\n");
    $warning("PLL initialize failed");
    $info("lane 3: unknown direction bit");
    $error("PLL lock lost");
    $display("line 8: last");
    if ($test$plusargs("fatal")) $fatal(1, "self-check failed");
    #200 $finish;
  end
endmodule
"""

# The block of lines the 100 ns statement prints, in order (stdout and stderr merged)
USER_BLOCK = [
    "line 1: plain", "***** TEST PASSED *****", "", "line 4: after a blank line", "\tcode=42",
    "    -----------------", "  Supply level 3 (nominal)", "irq handler not registered yet",
    "retrying: PLL initialize failed once", "ELF ABI version: 0",
    "** Error: Xyce transient failed at 1 s",
    "** Note: co-simulation finished: digital stop at 1 s",
    "[cosim_bridge] signal 'x' not registered", "SIMULATION FINISHED", "cfg: registry full",
    re.compile(r"^WARNING: \S+:\d+: PLL initialize failed$"),
    re.compile(r"^ +Time: 100000  Scope: tb$"), "** Warning: 100ns+0: WARNING",
    re.compile(r"^INFO: \S+:\d+: lane 3: unknown direction bit$"),
    re.compile(r"^ +Time: 100000  Scope: tb$"), "INFO",
    re.compile(r"^ERROR: \S+:\d+: PLL lock lost$"),
    re.compile(r"^ +Time: 100000  Scope: tb$"), "** Error: 100ns+0: ERROR",
    "line 8: last"]
XYCE_CHATTER = ("Welcome to the Xyce", "Total Devices", "Timing summary", "Solution Summary",
                "Number Successful Steps", "Co-simulation finish at", "End of Xyce",
                "Device Count Summary")
END_RE = re.compile(r"^\*\* (?:Note|Error): .*(?:co-simulation finished:|transient failed at|"
                    r"co-simulation stalled at|co-simulation interrupted at)")

TB_TSTART = TB_HEAD + """\
  initial begin
    if ($test$plusargs("t0")) $finish;
    else if ($test$plusargs("t300")) #300 $finish;
    else if ($test$plusargs("t500")) #500 $finish;
    else if ($test$plusargs("t700")) #700 $finish;
  end
endmodule
"""

TB_FREE = TB_HEAD + "  // no $finish: the analog side or +vcs+finish+ ends the run\nendmodule\n"

TB_DIGITS = """\
`timescale 1ns/1ps
module tb;
  reg clk = 0;
  wire out;
  rc_cell u1 (.in(clk), .out(out));
  initial begin #100 clk = 1; #1999900.006 $finish; end
endmodule
"""

# A $finish on the A2D crossing: the digital stops at the analog's candidate time.  The divider
# settles just above the 0.9 V threshold, so the crossing is slow and nvc answers at the
# candidate time itself (src/cosim.c stopped_step); the engine's double then sits a fraction of
# a femtosecond below the digital's stop (the 2 fs floor of §6).  R1 per engine: one of the
# values the sweep (review_engfix/t4_subfs.sh) found short of the old 1e-9 relative rule.
TB_SUBFS = """\
`timescale 1ns/1ps
module tb;
  reg clk = 0;
  wire out;
  slow_cell u1 (.in(clk), .out(out));
  initial begin #1 clk = 1; @(posedge out) $finish; end
endmodule
"""
SUBFS_R1 = {"vacask": "96k", "xyce": "94k"}


def subfs_netlist(r1: str) -> str:
    # no supply source: the IEs get the default 3.3 V and a 1.65 V threshold, which mid
    # approaches slowly (3.3 * 103k / (R1 + 103k))
    return ("* divider RC settling just above the A2D threshold, plus a sine branch\n"
            ".subckt slow_cell in out\nr1 in mid %s\nr2 mid 0 103k\nc1 mid 0 1p\n"
            "e1 out 0 mid 0 1\nvs s 0 SIN(0 1 37.7e6)\nrs s n2 1k\ncs n2 0 1p\n.ends\n"
            ".tran 1n 3u\n" % r1)


# Busy digital work on every clock edge keeps a co-simulation running for minutes with a
# small rawfile, so a signal reaches it mid-run.
TB_BUSY = TB_HEAD.replace('$display("%0t out=%b", $time, out);', "") + """\
  integer i, acc = 0, n = 0;
  always @(posedge clk) begin
    for (i = 0; i < 200000; i = i + 1) acc = acc + i;
    n = n + 1;
    $display("tick %0d", n);
  end
endmodule
"""


def end_lines(out: str) -> List[str]:
    return [ln for ln in out.splitlines() if END_RE.search(ln)]


def run_dirs(d: str) -> List[str]:
    return sorted(p for p in glob.glob(os.path.join(d, "vamos_ams.run.*")) if os.path.isdir(p))


def procs_in(directory: str) -> List[int]:
    """Processes whose cwd is directory (nvc runs in the run directory)."""
    found = []
    for p in glob.glob("/proc/[0-9]*"):
        try:
            if os.path.realpath(os.readlink(os.path.join(p, "cwd"))) == os.path.realpath(directory):
                found.append(int(os.path.basename(p)))
        except OSError:
            pass
    return found


def children(pid: int) -> List[int]:
    out = []
    for p in glob.glob("/proc/[0-9]*/stat"):
        try:
            with open(p) as fh:
                fields = fh.read().rsplit(")", 1)[1].split()
            if int(fields[1]) == pid:
                out.append(int(p.split("/")[2]))
        except (OSError, ValueError, IndexError):
            pass
    return out


class RunCase(AmsCase):
    def build(self, name: str, engine: str, files: dict, *extra: str) -> str:
        d = self.case("%s_%s" % (name, engine), files)
        self.compile(d, "-sverilog", "tb.sv", *extra, engine=engine)
        return d

    def clean(self, d: str) -> None:
        for p in [os.path.join(d, RAW)] + run_dirs(d):
            if os.path.isdir(p):
                shutil.rmtree(p)
            elif os.path.exists(p):
                os.remove(p)

    def assert_block(self, text: str, block) -> None:
        lines = text.splitlines()
        starts = [k for k, ln in enumerate(lines) if ln == block[0]]
        self.assertEqual(len(starts), 1, "the testbench block start is not printed once:\n" + text)
        got = lines[starts[0]:starts[0] + len(block)]
        for want, line in zip(block, got):
            if isinstance(want, str):
                self.assertEqual(line, want, "testbench output changed:\n" + "\n".join(got))
            else:
                self.assertRegex(line, want, "testbench output changed:\n" + "\n".join(got))
        self.assertEqual(len(got), len(block), "testbench lines missing:\n" + "\n".join(got))


@needs_ams
class TestE2EUserOutput(RunCase):
    """Testbench output is never classified and never filtered."""

    def test_user_output(self):
        for engine in self.engines():
            with self.subTest(engine=engine):
                d = self.build("out", engine, {"tb.sv": TB_OUTPUT, "rc.sp": netlist("1n 1u"),
                                               "vcsAD.init": INIT})
                r = self.simv(d, "-l", "run.log")
                out = r.stdout
                self.assertNotIn("vamos: error", out)
                self.assert_block(out, USER_BLOCK)
                with open(os.path.join(d, "run.log")) as fh:
                    self.assert_block(fh.read(), USER_BLOCK)
                lines = out.splitlines()
                self.assertIn("", lines[:lines.index("***** HEADER at t=0 *****")])  # t=0 blank
                real_end = "** Note: co-simulation finished: digital stop at 3e-07 s"
                self.assertEqual(end_lines(out)[-1], real_end)
                after = lines[lines.index(real_end) + 1]
                self.assertNotEqual(after.strip(), "", "a stray blank line after the end line")
                for chatter in XYCE_CHATTER:
                    self.assertNotIn(chatter, out)
                self.assertTrue(os.path.isfile(os.path.join(d, RAW)), "not published:\n" + out)
                self.assertAlmostEqual(self.raw(d).last_time(), 3e-7, delta=3e-16)
                self.assertEqual(run_dirs(d), [])

                # a $fatal fails the run, whatever the testbench printed before it
                self.clean(d)
                r = self.simv(d, "+fatal", expect_rc=None)
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertIn("SIMULATION FINISHED", r.stdout)
                self.assertFalse(os.path.exists(os.path.join(d, RAW)))
                self.assertRegex(r.stdout, r"vamos: note: run directory kept: ")

                # a boundary path nvc cannot resolve: the run fails and says why
                self._unresolved_boundary(d)

    def _unresolved_boundary(self, d: str) -> None:
        path = os.path.join(d, "simv.daidir", "ams", "vamos.boundary")
        with open(path) as fh:
            text = fh.read()
        self.assertRegex(text, r"(?m)^A2D \S+ ")
        with open(path, "w") as fh:
            fh.write(re.sub(r"(?m)^A2D \S+ ", "A2D .u1.nosuch_path ", text))
        for env in (None, {"NVC_COLORS": "always"}):
            with self.subTest(boundary="unresolved", env=env):
                self.clean(d)
                r = self.run_cmd(["./simv"], d, env)
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertRegex(r.stdout, r"vamos: error: co-simulation failed: \*\* Warning: +A2D "
                                           r"\.u1\.nosuch_path <-> \S+ \[FAILED: signal not found\]")
                self.assertFalse(os.path.exists(os.path.join(d, RAW)))
                self.assertNotIn("\x1b[", r.stdout)


@needs_ams
class TestE2ETstart(RunCase):
    """.tran 1n 1u 500n: the engines write nothing before 500 ns."""

    def test_tstart(self):
        for engine in self.engines():
            with self.subTest(engine=engine):
                d = self.build("tstart", engine, {"tb.sv": TB_TSTART, "rc.sp": netlist("1n 1u 500n"),
                                                  "vcsAD.init": INIT})
                for args, t, footer in ((["+t300"], "3e-07", "300ns"),
                                        (["+vcs+finish+200000"], "2e-07", "200ns")):
                    with self.subTest(args=args):
                        self.clean(d)
                        r = self.simv(d, *args)
                        self.assertNotIn("Traceback", r.stdout)
                        self.assertIn("vamos: note: no analog output: the run ended at %s s, "
                                      "before the deck's output start 5e-07 s (.tran TSTART)" % t,
                                      r.stdout)
                        self.assertIn("Time: %s\n" % footer, r.stdout)
                        self.assertFalse(os.path.exists(os.path.join(d, RAW)))
                        self.assertEqual(run_dirs(d), [])
                with self.subTest(args="+t0"):
                    self.clean(d)
                    r = self.simv(d, "+t0")
                    self.assertIn("vamos: note: no analog output: the digital stopped at t=0",
                                  r.stdout)
                    self.assertFalse(os.path.exists(os.path.join(d, RAW)))
                for arg, first, last in (("+t500", 5e-7, 5e-7), ("+t700", 5e-7, 7e-7)):
                    with self.subTest(args=arg):
                        self.clean(d)
                        r = self.simv(d, arg)
                        self.assertNotIn("no analog output", r.stdout)
                        raw = self.raw(d)
                        self.assertAlmostEqual(raw.time()[0], first, delta=2e-15)
                        self.assertAlmostEqual(raw.last_time(), last, delta=2e-15)


@needs_ams
class TestE2EFinishAndAbi(RunCase):
    """+vcs+finish+<time> forms, the ABI pre-check and concurrent runs, on one build."""

    def test_finish_forms_abi_concurrency(self):
        for engine in self.engines():
            with self.subTest(engine=engine):
                d = self.build("free", engine, {"tb.sv": TB_FREE, "rc.sp": netlist("1n 1u"),
                                                "vcsAD.init": INIT})
                for arg in ("+vcs+finish+300ns", "+vcs+finish+300000+0"):
                    with self.subTest(arg=arg):
                        self.clean(d)
                        r = self.simv(d, arg)
                        self.assertEqual(end_lines(r.stdout),
                                         ["** Note: co-simulation finished: analog end at 3e-07 s"])
                        self.assertAlmostEqual(self.raw(d).last_time(), 3e-7, delta=3e-16)
                        self.assertIn("Time: 300ns\n", r.stdout)
                with self.subTest(arg="+vcs+finish+0"):
                    self.clean(d)
                    r = self.simv(d, "+vcs+finish+0")
                    self.assertEqual(end_lines(r.stdout),
                                     ["** Note: co-simulation finished: analog end at 0 s"])
                    self.assertIn("Time: 0\n", r.stdout)
                    if os.path.exists(os.path.join(d, RAW)):
                        self.assertEqual(self.raw(d).last_time(), 0.0)
                with self.subTest(arg="+vcs+finish+3xs"):
                    self.clean(d)
                    r = self.simv(d, "+vcs+finish+3xs")
                    self.assertIn("vamos: warning: unknown option +vcs+finish+3xs ignored (not a "
                                  "time value", r.stdout)
                    self.assertEqual(end_lines(r.stdout),
                                     ["** Note: co-simulation finished: analog end at 1e-06 s"])
                    r = self.simv(d, "--vamos-strict", "+vcs+finish+3xs", expect_rc=1)
                    self.assertIn("vamos: error: --vamos-strict: 1 unsupported/unknown option(s): "
                                  "+vcs+finish+3xs", r.stdout)
                self._abi(d, engine)
                self._concurrent(d)

    def _abi(self, d: str, engine: str) -> None:
        with self.subTest(abi="no nm on PATH"):
            bindir = os.path.join(self.tmp, "nonm_bin")
            if not os.path.isdir(bindir):
                os.makedirs(bindir)
                for src in ("/usr/bin", "/bin"):
                    for name in os.listdir(src):
                        if re.search(r"(^|-)(nm|readelf|objdump)$", name):
                            continue
                        dst = os.path.join(bindir, name)
                        if not os.path.lexists(dst):
                            os.symlink(os.path.join(src, name), dst)
            keep = [p for p in os.environ.get("PATH", "").split(os.pathsep)
                    if p and os.path.realpath(p) not in ("/usr/bin", "/bin")
                    and not os.path.exists(os.path.join(p, "nm"))]
            self.clean(d)
            r = self.run_cmd(["./simv"], d, {"PATH": os.pathsep.join(
                [os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "shims"),
                 bindir] + keep)})
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertTrue(os.path.isfile(os.path.join(d, RAW)))
        with self.subTest(abi="a library directory that does not exist"):
            var = "VAMOS_VACASK_HOME" if engine == "vacask" else "VAMOS_XYCE_LIBS"
            lib = "libvacaskcinterface.so" if engine == "vacask" else "libxycecinterface.so"
            self.clean(d)
            # (an inherited LD_LIBRARY_PATH that holds the library would rightly satisfy it)
            r = self.run_cmd(["./simv"], d, {var: "/nonexistent/vamos-test", "LD_LIBRARY_PATH": ""})
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertRegex(r.stdout, r"vamos: error: %s not found \(searched [^)]*/nonexistent/"
                                       r"vamos-test[^)]*\): set %s " % (re.escape(lib), var))
            self.assertNotIn("rebuild", r.stdout)
            self.assertNotIn("S i m u l a t i o n", r.stdout)       # nothing ran: no footer

    def _concurrent(self, d: str) -> None:
        """Runs of one build in one directory share <prefix>.raw: none of them fails."""
        self.clean(d)
        self.simv(d, "+vcs+finish+200000")
        env = self.child_env()
        for rnd in range(2):
            with self.subTest(concurrent=rnd):
                procs = [subprocess.Popen(["./simv", "+vcs+finish+200000"], cwd=d, env=env,
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          universal_newlines=True) for _ in range(8)]
                outs = [p.communicate(timeout=600)[0] for p in procs]
                for p, out in zip(procs, outs):
                    self.assertEqual(p.returncode, 0, out)
                    self.assertNotIn("Traceback", out)
                self.assertAlmostEqual(self.raw(d).last_time(), 2e-7, delta=2e-16)
                self.assertEqual(run_dirs(d), [])


@needs_ams
class TestE2EStopTimes(RunCase):
    def test_ten_digit_stop(self):
        """$finish at 2000000006 ps: %.9g would print 0.00200000001 and fail the run."""
        for engine in self.engines():
            with self.subTest(engine=engine):
                # (a 10 us maximum step: the circuit has settled long before the stop)
                d = self.build("digits", engine, {"tb.sv": TB_DIGITS, "rc.sp": netlist("1n 3m"),
                                                  "vcsAD.init": INIT},
                               "--vamos-analog-maxstep=10u")
                r = self.simv(d)
                self.assertEqual(end_lines(r.stdout), [
                    "** Note: co-simulation finished: digital stop at 0.002000000006 s"])
                self.assertIn("Time: 2000000006ps\n", r.stdout)
                self.assertAlmostEqual(self.raw(d).last_time(), 0.002000000006, delta=2e-15)

    def test_finish_at_the_candidate_time(self):
        """A2D-triggered $finish answered at the candidate time: the 2 fs floor."""
        for engine in self.engines():
            with self.subTest(engine=engine):
                d = self.build("subfs", engine, {"tb.sv": TB_SUBFS,
                                                 "rc.sp": subfs_netlist(SUBFS_R1[engine]),
                                                 "vcsAD.init": INIT})
                r = self.simv(d)
                self.assertNotIn("ends early", r.stdout)
                stop = [ln for ln in end_lines(r.stdout) if "digital stop at" in ln]
                self.assertEqual(len(stop), 1, r.stdout)
                t = float(re.search(r"digital stop at (\S+) s", stop[0]).group(1))
                last = self.raw(d).last_time()
                self.assertGreaterEqual(last, t - 2e-15)
                self.assertLessEqual(last, t + 2e-9)


@needs_ams
class TestE2ESignals(RunCase):
    """Signals during a co-simulation (busy digital work keeps it running)."""

    def start(self, d: str, new_session: bool = True) -> subprocess.Popen:
        p = subprocess.Popen(["./simv"], cwd=d, env=self.child_env(), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, universal_newlines=True,
                             start_new_session=new_session)
        seen = []
        deadline = time.time() + 300
        while time.time() < deadline:
            line = p.stdout.readline()
            if not line:
                break
            seen.append(line)
            if line.startswith("tick 2"):
                p.seen = "".join(seen)
                return p
        p.kill()
        self.fail("the co-simulation never got going:\n" + "".join(seen))

    def finish(self, p: subprocess.Popen) -> str:
        rest = p.communicate(timeout=120)[0]
        return p.seen + rest

    def nvc_of(self, p: subprocess.Popen) -> int:
        kids = children(p.pid)
        self.assertEqual(len(kids), 1, "nvc not found under simv")
        return kids[0]

    def test_signals(self):
        for engine in self.engines():
            with self.subTest(engine=engine):
                d = self.build("busy", engine, {"tb.sv": TB_BUSY, "rc.sp": netlist("1n 1m"),
                                                "vcsAD.init": INIT})
                self._ctrl_c(d)
                self._sigterm(d)
                self._nvc_killed(d)
                self._nvc_interrupted(d)
                self._simv_killed(d)

    def _interrupted(self, d: str, out: str, why: str) -> None:
        self.assertNotIn("Traceback", out)
        m = re.search(r"vamos: note: co-simulation interrupted \(%s\) at \S+ s; run directory kept "
                      r"\(partial waves\): (\S+)" % re.escape(why), out)
        self.assertIsNotNone(m, out)
        ends = end_lines(out)
        self.assertEqual(len(ends), 1, out)
        self.assertRegex(ends[0], r"^\*\* Error: co-simulation interrupted at \S+ s$")
        self.assertFalse(os.path.exists(os.path.join(d, RAW)), "an interrupted run published")
        from vamos.netlist import rawfile
        raws = glob.glob(os.path.join(m.group(1), "*.raw"))
        self.assertEqual(len(raws), 1, m.group(1))
        part = rawfile.read(raws[0])
        self.assertGreater(part.last_time(), 1e-7)
        self.assertEqual(part.declared_points, len(part.points))  # the header was made readable
        self.assertEqual(procs_in(m.group(1)), [], "nvc is still running")

    def _ctrl_c(self, d: str) -> None:
        with self.subTest(signal="Ctrl-C (SIGINT to the process group)"):
            self.clean(d)
            p = self.start(d)
            os.killpg(p.pid, signal.SIGINT)
            out = self.finish(p)
            self.assertEqual(p.returncode, -signal.SIGINT, out)
            self._interrupted(d, out, "SIGINT")

    def _sigterm(self, d: str) -> None:
        with self.subTest(signal="SIGTERM to simv"):
            self.clean(d)
            p = self.start(d)
            nvc_pid = self.nvc_of(p)
            p.send_signal(signal.SIGTERM)
            out = self.finish(p)
            self.assertEqual(p.returncode, -signal.SIGTERM, out)
            self._interrupted(d, out, "SIGTERM")
            self.assertFalse(_alive(nvc_pid), "nvc outlived simv")

    def _nvc_killed(self, d: str) -> None:
        with self.subTest(signal="SIGKILL to nvc"):
            self.clean(d)
            p = self.start(d)
            os.kill(self.nvc_of(p), signal.SIGKILL)
            out = self.finish(p)
            self.assertEqual(p.returncode, 128 + signal.SIGKILL, out)
            self.assertIn("vamos: error: nvc was killed by signal 9 (SIGKILL)", out)
            self.assertNotIn("without an end line", out)
            self.assertRegex(out, r"vamos: note: run directory kept: ")
            # the footer shows how far the run got (the partial rawfile), not "Time: 0"
            m = re.search(r"^Time: (\d+)(ms|us|ns|ps|fs)$", out, re.M)
            self.assertIsNotNone(m, out)
            fs = int(m.group(1)) * {"ms": 10 ** 12, "us": 10 ** 9, "ns": 10 ** 6, "ps": 10 ** 3,
                                    "fs": 1}[m.group(2)]
            self.assertGreaterEqual(fs, 150 * 10 ** 6, out)

    def _nvc_interrupted(self, d: str) -> None:
        with self.subTest(signal="SIGINT to nvc"):
            self.clean(d)
            p = self.start(d)
            os.kill(self.nvc_of(p), signal.SIGINT)
            out = self.finish(p)
            self.assertEqual(p.returncode, 130, out)
            self._interrupted(d, out, "nvc got SIGINT")

    def _simv_killed(self, d: str) -> None:
        with self.subTest(signal="SIGKILL to simv"):
            self.clean(d)
            p = self.start(d)
            nvc_pid = self.nvc_of(p)
            p.kill()
            p.communicate(timeout=60)
            deadline = time.time() + 15
            while time.time() < deadline and _alive(nvc_pid):
                time.sleep(0.1)
            self.assertFalse(_alive(nvc_pid), "nvc outlived a killed simv")


def _alive(pid: int) -> bool:
    try:
        with open("/proc/%d/stat" % pid) as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


if __name__ == "__main__":
    unittest.main()
