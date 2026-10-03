"""simv runs: how vamos reads nvc's output, ends a run and checks its rawfile.

Unit tests for vamos/backends/nvc.py (OutputFilter, remap_exit, stream: raw lines,
signals, nvc's lifetime), vamos/backends/cosim.py (EndState, ChatterFilter, the ABI
pre-check, the O(1)-memory rawfile check, the .tran TSTART rules, the 2 fs floor) and
vamos/personalities/simv.py (+vcs+finish+ forms, -h, messages, the footer, ending with
the signal that interrupted the run).  The stack-free tests run anywhere (Cygwin too);
TestPlainSimv needs nvc + iverilog (Linux/WSL).  The co-simulation end-to-end cases are
in test_ams_e2e_run.py.

    cd /usr/local/src/sv2ghdl
    python3 -m unittest discover -s tests/vamos -p 'test_vamos_run.py' -v
"""

import contextlib
import io
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import time
import tracemalloc
import unittest

from vamos_testlib import LINUX, ROOT, TempDir, fixture, have_stack

from vamos.backends import cosim, nvc  # noqa: E402
from vamos.job import UNKNOWN, UNSUPPORTED, Job  # noqa: E402
from vamos.netlist import rawfile  # noqa: E402
from vamos.optable import scan  # noqa: E402
from vamos.personalities import simv  # noqa: E402

POSIX_SIGNALS = hasattr(signal, "SIGALRM") and hasattr(signal, "setitimer")


def fx(name):
    return fixture("netlist", name)


class Sink:
    """A Console stand-in: collects out/err lines."""

    def __init__(self):
        self.lines = []

    def out(self, line):
        self.lines.append(line)

    err = out

    def text(self):
        return "\n".join(self.lines)


# -- what classifies a co-simulation (cosim.EndState) ---------------------------------------

# Testbench output: nvc reports.  Each carries a phrase a tool line also uses.
USER_LINES = [
    "** Note: 100ns+0: irq handler not registered yet",
    "** Note: 100ns+0: retrying: PLL initialize failed once",
    "** Note: 100ns+0: lane 3: unknown direction bit",
    "** Note: 100ns+0: fifo registry full, dropping",
    "** Note: 100ns+0: ELF ABI version: 0",
    "** Note: 100ns+0: cosim ABI ok",
    "** Note: 100ns+0: ** Error: Xyce transient failed at 1 s",
    "** Note: 100ns+0: ** Error: co-simulation stalled at 1 s",
    "** Note: 100ns+0: ** Error: co-simulation interrupted at 1 s",
    "** Note: 100ns+0: ** Note: co-simulation finished: digital stop at 1 s",
    "** Note: 100ns+0: ** Warning:   A2D x <-> y [FAILED: signal not found]",
    "** Note: 100ns+0: [cosim_bridge] signal 'x' not registered",
    "** Note: 100ns+0: *** Caught signal 11 (SEGV_MAPERR)",
    "** Note: 100ns+0: SIMULATION FINISHED",
    "** Note: (init): malformed boundary line",
    "** Warning: 100ns+0: WARNING",
    "** Error: 100ns+0: ERROR",
    "** Failure: 300ns+0: FATAL",
    "** Fatal: 300ns+0: index 5 outside of INTEGER range 0 to 3",
]


class TestEndState(unittest.TestCase):
    def feed(self, lines):
        s = cosim.EndState()
        for ln in lines:
            s.feed(ln)
        return s

    def test_testbench_lines_never_classify(self):
        s = self.feed(USER_LINES)
        self.assertEqual(s.failures, [])
        self.assertFalse(s.ended)
        self.assertFalse(s.started)

    def test_end_lines(self):
        cases = [
            ("** Note: co-simulation finished: digital stop at 0 s (before the first analog step)",
             "stop0", "0"),
            ("** Note: co-simulation finished: digital stop at 6e-07 s", "stop", "6e-07"),
            ("** Note: co-simulation finished: analog end at 3600.000000000000001 s", "analog",
             "3600.000000000000001"),
            ("** Error: VACASK transient failed at 1.5e-07 s", "failed", "1.5e-07"),
            ("** Error: Xyce transient failed at 1.5e-07 s", "failed", "1.5e-07"),
            ("** Error: co-simulation stalled at 2e-07 s", "stalled", "2e-07"),
            ("** Error: co-simulation interrupted at 0.00347155001 s", "interrupted",
             "0.00347155001"),
        ]
        for line, kind, t in cases:
            with self.subTest(line=line):
                s = self.feed(USER_LINES + [line])
                self.assertEqual((s.kind, s.time_text), (kind, t))
                self.assertEqual(s.failures, [line] if kind in ("failed", "stalled") else [])
        s = self.feed(["** Note: co-simulation finished: analog end at 3600.000000000000001 s"])
        self.assertEqual(s.time_fs, 3600 * 10 ** 15 + 1)            # exact, not a double
        s = self.feed(["** Note: co-simulation finished: digital stop at 0.002000000006 s"])
        self.assertEqual(s.time_fs, 2000000006000)
        self.assertEqual(s.digital_stop, 0.002000000006)
        self.assertTrue(self.feed(["** Note: starting co-simulation (stop_time=1e-06 s)"]).started)

    def test_failure_lines(self):
        lines = [
            "** Warning:   A2D .u1.nosuch_path <-> tb.u1.out__a [FAILED: signal not found]",
            "\x1b[33m** Warning:\x1b[0m   D2A .u1.x <-> tb.u1.x__d [FAILED: signal not found]",
            "[cosim_bridge] signal 'tb.u1.x__d' not registered",
            "[cosim_bridge] signal 'tb.u1.x__d' not registered as A2D (the boundary file has it "
            "as D2A)",
            "[cosim_bridge] code: source 'd2a:x' is not bound to a boundary signal; failing it",
            "[cosim_bridge] VACASK external-source ABI mismatch",
            "** Error: /d/vamos.boundary:3: malformed boundary line: expected D2A|A2D <nvc path>",
            "** Error: /d/vamos.boundary:4: unknown direction 'X2D' (expected D2A or A2D)",
            "** Error:   bridge D2A: tb.x <-> 'n' FAILED (registry full: at most 8192 boundary "
            "signals)",
            "** Fatal: co-simulation ABI mismatch: /x/libcosim_bridge.so does not export "
            "cosim_bridge_abi() (it predates the co-simulation finish protocol); rebuild",
            "** Fatal: VACASK initialize failed for netlist /d/vamos.sim",
            "** Warning: cannot load Xyce: libxycecinterface.so: cannot open shared object file",
            "*** Caught signal 11 (SEGV_MAPERR) [address=0x0, ip=0x1]",
        ]
        for line in lines:
            with self.subTest(line=line):
                s = self.feed([line])
                self.assertEqual(len(s.failures), 1)
        ok = ["** Warning: some boundary signals could not be resolved",
              "[cosim_bridge] warning: D2A 'x': a 1e-15 s ramp at 1.5 s is shorter than VACASK "
              "resolves at that time; it takes 1.5e-13 s",
              "[cosim_bridge] bound D2A VACASK source to 'tb.u1.in__d'",
              "** Note: resolved 2/2 boundary signals", "Netlist warning: something"]
        self.assertEqual(self.feed(ok).failures, [])

    def test_first_end_line_wins(self):
        s = self.feed(["** Note: co-simulation finished: digital stop at 6e-07 s",
                       "** Note: co-simulation finished: analog end at 1e-06 s"])
        self.assertEqual((s.kind, s.time_text), ("stop", "6e-07"))


# -- what reaches the user (cosim.ChatterFilter) -------------------------------------------

class TestChatterFilterSources(unittest.TestCase):
    """Testbench lines (raw nvc reports) always reach the user; engine chatter does not."""

    TB = ["", "***** TEST PASSED *****", "\tcode=42", "    -----------------",
          "  Supply level 3 (nominal)", "Timing summary of 1 processor",
          "Co-simulation finish at 3e-07 s.  Exiting transient loop", "/d/vamos.cir",
          "[cosim_bridge] bound D2A DPWL to 'x'", "** Note: loaded 2 boundary mappings from x",
          "       Total Devices                                          10",
          "Xyce                                         1        1.665 (100.0%)        0.088 "
          "(100.0%)",
          "Netlist warning: Voltage Node (N_U1_IN_E) connected to only 1 device Terminal"]

    def run_filter(self, pairs, engine):
        out = []
        f = cosim.ChatterFilter(out.append, "/d/vamos.cir", quiet_nodes=["n_u1_in_e"],
                                engine=engine)
        filt = nvc.OutputFilter(f, out.append)
        for raw in pairs:
            f.see_raw(raw)
            filt.feed(raw)
        f.flush()
        return out

    def test_testbench_lines_pass_on_both_engines(self):
        raws = ["** Note: 100ns+0: " + t for t in self.TB]
        for engine in ("vacask", "xyce", None):
            with self.subTest(engine=engine):
                self.assertEqual(self.run_filter(raws, engine), self.TB)

    def test_engine_chatter(self):
        xyce = ["", "*****", "***** Welcome to the Xyce(TM) Parallel Electronic Simulator",
                "\tNumber Successful Steps Taken:\t\t243",
                "       B level 1 (Expression Based Voltage or Current Source)  1",
                "       ---------------------------------------------------------",
                "       Total Devices                                          10",
                "Co-simulation finish at 3e-07 s.  Exiting transient loop",
                "Timing summary of 1 processor",
                "                 Stats                   Count       CPU Time              Wall Time",
                "Xyce                                         1        1.665 (100.0%)        "
                "0.088 (100.0%)",
                "Netlist warning: Voltage Node (N_U1_IN_E) connected to only 1 device Terminal"]
        tool = ["** Note: initializing Xyce co-simulation", "** Note: loaded libcosim_bridge.so",
                "[cosim_bridge] bound D2A DPWL to 'tb.u1.in__e'",
                "** Note: Xyce initialized with netlist: /d/vamos.cir",
                "** Note: starting co-simulation (stop_time=1e-06 s)"]
        end = "** Note: co-simulation finished: digital stop at 3e-07 s"
        self.assertEqual(self.run_filter(tool + xyce + [end, ""], "xyce"), [end])
        # VACASK prints none of Xyce's chatter: a bare line of that shape is shown there
        self.assertEqual(self.run_filter(["\tNumber Successful Steps Taken:\t\t243", ""],
                                         "vacask"), ["\tNumber Successful Steps Taken:\t\t243"])

    def test_report_flushes_a_held_warning(self):
        head = "Netlist warning: Voltage Node (N_U1_IN_E) connected to only 1 device"
        out = self.run_filter([head, "** Note: 100ns+0: hello", " Terminal"], "xyce")
        self.assertEqual(out, [head, "hello", " Terminal"])


# -- nvc.py: the output filter and exit status --------------------------------------------

class TestOutputFilter(unittest.TestCase):
    def feed(self, lines):
        f = nvc.OutputFilter(lambda s: None, lambda s: None)
        for ln in lines:
            f.feed(ln)
        return f

    def test_simulation_finished_text_never_hides_a_fatal(self):
        f = self.feed(["** Note: 300ns+0: SIMULATION FINISHED: 3 checks failed",
                       "** Note: 300ns+0: FATAL: tb.sv:9: self-check failed",
                       "** Failure: 300ns+0: FATAL"])
        self.assertEqual(nvc.remap_exit(1, f), 1)
        # only nvc's own report of a translation made without sv2vhdl mode maps to 0
        self.assertEqual(nvc.remap_exit(1, self.feed(["** Failure: 600ns+0: SIMULATION FINISHED"])),
                         0)

    def test_error_reports_exit_0_tool_errors_do_not(self):
        self.assertEqual(nvc.remap_exit(1, self.feed(["** Error: 100ns+0: ERROR"])), 0)
        self.assertEqual(nvc.remap_exit(1, self.feed(["** Error: 100ns+0: ERROR",
                                                      "** Error: cannot open x"])), 1)
        self.assertEqual(nvc.remap_exit(1, self.feed(["** Error: 100ns+0: ERROR",
                                                      "** Failure: 200ns+0: FATAL"])), 1)
        self.assertEqual(nvc.remap_exit(-9, self.feed(["** Failure: 600ns+0: SIMULATION FINISHED"])),
                         -9)

    def test_design_end(self):
        f = self.feed(["** Note: 25ns+0: FINISH called",
                       "   Procedure FINISH [] at lib/std.08/env-body.vhd:42"])
        self.assertTrue(f.ended)
        self.assertEqual(f.last_time, "25ns")
        f = self.feed(["** Note: 25ns+1: STOP called", "   Procedure STOP [] at x"])
        self.assertTrue(f.ended)
        f = self.feed(["** Note: 25ns+0: FINISH called",          # a $display of that text
                       "   Procedure FLUSH_LINE [] at lib/sv2vhdl/sv_display_pkg.vhd:500"])
        self.assertFalse(f.ended)
        self.assertTrue(self.feed(["** Failure: 30ns+0: FATAL"]).ended)

    def test_reports(self):
        for line in USER_LINES:
            self.assertTrue(nvc.is_report(line), line)
        for line in ("** Note: co-simulation finished: digital stop at 6e-07 s",
                     "** Error: VACASK transient failed at 1e-07 s", "[cosim_bridge] bound x",
                     "** Note: loaded libcosim_bridge.so"):
            self.assertFalse(nvc.is_report(line), line)

    def test_times(self):
        self.assertEqual(nvc.fs_text(0), "0")
        self.assertEqual(nvc.fs_text(600 * 10 ** 6), "600ns")
        self.assertEqual(nvc.fs_text(2000000006000), "2000000006ps")
        self.assertEqual(nvc.fs_text(3257407025), "3257407025fs")
        self.assertEqual(nvc.fs_text(3600 * 10 ** 15), "3600000ms")
        self.assertEqual(nvc.footer_time("0ms"), "0")
        self.assertEqual(nvc.footer_time("25000ps"), "25ns")
        self.assertEqual(nvc.footer_time("0"), "0")


# -- nvc.py: running nvc (a stand-in program) ---------------------------------------------

FAKE_NVC = r'''
import os, signal, sys, time
mode = sys.argv[1]
def interrupted(signum, frame):
    print("** Error: co-simulation interrupted at 1e-07 s", flush=True)
    sys.exit(130)
signal.signal(signal.SIGINT, interrupted if mode == "graceful" else signal.SIG_IGN)
sys.stdout.write("** Note: 0ms+0: a\rb\n")
sys.stdout.flush()
if mode == "lines":
    sys.exit(0)
sig = getattr(signal, os.environ.get("FAKE_SIG", "SIGINT"))
os.kill(os.getppid(), sig)
if mode == "twice":
    time.sleep(0.5)
    os.kill(os.getppid(), sig)
if mode == "ignored":
    time.sleep(0.5)
    print("** Note: 1ns+0: still running", flush=True)
    sys.exit(0)
for _ in range(600):
    time.sleep(0.05)
print("** Note: 30ms+0: not interrupted", flush=True)
'''


def fake_backend(tmp):
    be = object.__new__(nvc.NvcBackend)
    be.job, be.emit, be.nvc = None, (lambda s: None), sys.executable
    be.libdir = be.workdir = tmp
    be.interrupted, be.killed = None, False
    return be


class _FakeNvc(TempDir):
    def stream(self, mode, sig="SIGINT"):
        be = fake_backend(self.tmp)
        env = dict(os.environ, FAKE_SIG=sig)
        out, raws = [], []
        t0 = time.time()
        rc, f = be.stream([sys.executable, "-c", FAKE_NVC, mode], env, self.tmp, out.append,
                          out.append, on_raw=raws.append)
        return be, rc, out, raws, time.time() - t0


class TestStream(_FakeNvc):
    def test_lines_split_at_newline_only(self):
        _, rc, out, raws, _ = self.stream("lines")
        self.assertEqual(rc, 0)
        self.assertEqual(raws, ["** Note: 0ms+0: a\rb"])
        self.assertEqual(out, ["a\rb"])

    def test_cannot_start(self):
        be = fake_backend(self.tmp)
        with self.assertRaises(nvc.BackendError):
            be.stream([os.path.join(self.tmp, "no-such-nvc")], dict(os.environ), self.tmp,
                      print, print)

    def test_run_command_turns_colour_off(self):
        be = fake_backend(self.tmp)
        os.environ["NVC_COLORS"] = "always"
        _, env = be.run_command("tb", [])
        self.assertEqual(env["NVC_COLORS"], "never")


# Signals interrupt a blocked pipe read (and so run vamos's handlers at once) on Linux, where
# nvc runs; Cygwin's Python delays them until the read returns.
@unittest.skipUnless(LINUX and POSIX_SIGNALS, "signals to nvc: Linux")
class TestStreamSignals(_FakeNvc):
    def test_interrupt_is_passed_to_nvc(self):
        sigs = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
        # A suite started under nohup inherits SIGHUP ignored (a background job without job
        # control, SIGINT), and vamos keeps an inherited SIG_IGN on purpose
        # (test_ignored_signal_stays_ignored): give such a signal its default for this test.
        inherited = {s: signal.getsignal(s) for s in sigs}
        for s in sigs:
            if inherited[s] == signal.SIG_IGN:
                signal.signal(s, signal.SIG_DFL)
        try:
            before = [signal.getsignal(s) for s in sigs]
            for sig in ("SIGINT", "SIGTERM", "SIGHUP"):
                with self.subTest(signal=sig):
                    be, rc, out, raws, _ = self.stream("graceful", sig)
                    self.assertEqual(be.interrupted, getattr(signal, sig))
                    self.assertFalse(be.killed)
                    self.assertEqual(rc, 130)
                    self.assertEqual(raws[-1], "** Error: co-simulation interrupted at 1e-07 s")
            # the handlers are restored afterwards
            self.assertEqual([signal.getsignal(s) for s in sigs], before)
        finally:
            for s, h in inherited.items():
                signal.signal(s, h)

    def test_second_signal_kills(self):
        be, rc, _, _, took = self.stream("twice")
        self.assertEqual((be.interrupted, be.killed), (signal.SIGINT, True))
        self.assertEqual(rc, -signal.SIGKILL)
        self.assertLess(took, nvc.INTERRUPT_GRACE)

    def test_grace_period_kills(self):
        saved = nvc.INTERRUPT_GRACE
        nvc.INTERRUPT_GRACE = 0.5
        try:
            be, rc, _, raws, took = self.stream("stubborn")
        finally:
            nvc.INTERRUPT_GRACE = saved
        self.assertEqual((be.interrupted, be.killed, rc), (signal.SIGINT, True, -signal.SIGKILL))
        self.assertLess(took, 10)
        self.assertNotIn("** Note: 30ms+0: not interrupted", raws)

    def test_ignored_signal_stays_ignored(self):
        saved = signal.signal(signal.SIGHUP, signal.SIG_IGN)       # nohup
        try:
            be, rc, out, _, _ = self.stream("ignored", "SIGHUP")
        finally:
            signal.signal(signal.SIGHUP, saved)
        self.assertIsNone(be.interrupted)
        self.assertEqual((rc, out[-1]), (0, "still running"))

    def test_own_process_group(self):
        """A terminal's Ctrl-C reaches vamos alone; nvc never reads the terminal."""
        be = fake_backend(self.tmp)
        out = []
        rc, _ = be.stream([sys.executable, "-c", "import os, sys\nprint('** Note: 0ms+0: %d %d %r' "
                           "% (os.getpgrp(), os.getpid(), sys.stdin.read()))"],
                          dict(os.environ), self.tmp, out.append, out.append)
        pgrp, pid, stdin = out[0].split(" ", 2)
        self.assertEqual((rc, pgrp, stdin), (0, pid, "''"))
        self.assertNotEqual(int(pgrp), os.getpgrp())

    def test_ctrl_z_stops_nvc_too(self):
        pidfile = os.path.join(self.tmp, "child.pid")
        child = ("import os, time\nopen(%r, 'w').write(str(os.getpid()))\n"
                 "for _ in range(600):\n    print('** Note: 1ns+0: x', flush=True)\n"
                 "    time.sleep(0.05)\n" % pidfile)
        parent = ("import os, sys\nsys.path.insert(0, %r)\nsys.path.insert(0, %r)\n"
                  "from test_vamos_run import fake_backend\n"
                  "be = fake_backend(%r)\n"
                  "be.stream([sys.executable, '-c', %r], dict(os.environ), %r, lambda s: None, "
                  "print)\n" % (ROOT, os.path.dirname(os.path.abspath(__file__)), self.tmp, child,
                                self.tmp))
        p = subprocess.Popen([sys.executable, "-c", parent])
        try:
            deadline = time.time() + 30
            while not os.path.exists(pidfile) and time.time() < deadline:
                time.sleep(0.05)
            time.sleep(0.2)
            with open(pidfile) as fh:
                pid = int(fh.read())
            os.kill(p.pid, signal.SIGTSTP)
            self.assertTrue(_wait_state([p.pid, pid], "T"), "Ctrl-Z did not stop vamos and nvc")
            os.kill(p.pid, signal.SIGCONT)
            self.assertTrue(_wait_state([p.pid, pid], "SR"), "nvc did not go on with vamos")
        finally:
            p.kill()
            p.wait()

    def test_nvc_dies_with_vamos(self):
        pidfile = os.path.join(self.tmp, "child.pid")
        child = ("import os, time\nopen(%r, 'w').write(str(os.getpid()))\ntime.sleep(60)\n"
                 % pidfile)
        parent = ("import os, sys\nsys.path.insert(0, %r)\nsys.path.insert(0, %r)\n"
                  "from test_vamos_run import fake_backend\n"
                  "be = fake_backend(%r)\n"
                  "be.stream([sys.executable, '-c', %r], dict(os.environ), %r, print, print)\n"
                  % (ROOT, os.path.dirname(os.path.abspath(__file__)), self.tmp, child, self.tmp))
        p = subprocess.Popen([sys.executable, "-c", parent])
        deadline = time.time() + 30
        while not os.path.exists(pidfile) and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(0.2)
        with open(pidfile) as fh:
            pid = int(fh.read())
        os.kill(p.pid, signal.SIGKILL)
        p.wait()
        deadline = time.time() + 10
        while time.time() < deadline and _alive(pid):
            time.sleep(0.05)
        self.assertFalse(_alive(pid), "nvc outlived a killed vamos")


def _state(pid):
    try:
        with open("/proc/%d/stat" % pid) as fh:
            return fh.read().rsplit(")", 1)[1].split()[0]
    except OSError:
        return "gone"


def _alive(pid):
    return _state(pid) not in ("Z", "gone")


def _wait_state(pids, states, timeout=10.0):
    """True once every pid is in one of states (/proc/<pid>/stat letters)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if all(_state(p) in states for p in pids):
            return True
        time.sleep(0.05)
    return False


# -- simv.py: +vcs+finish+<time> ------------------------------------------------------------

class TestFinishForms(TempDir):
    def finish(self, val, precision="1ps"):
        rt = Job(personality="simv", cwd=self.tmp)
        scan(simv.TABLE, ["+vcs+finish+" + val], rt, simv._positional, simv._unknown)
        fs = simv.finish_fs(rt, Job(personality="vcs", precision=precision), Sink())
        return fs, [(u.option, u.disposition) for u in rt.unmapped]

    def test_forms(self):
        self.assertEqual(self.finish("300000"), (300 * 10 ** 6, []))
        self.assertEqual(self.finish("300ns"), (300 * 10 ** 6, []))
        self.assertEqual(self.finish("9001us"), (9001 * 10 ** 9, []))
        self.assertEqual(self.finish("300000+0"), (300 * 10 ** 6, []))
        self.assertEqual(self.finish("1+1"), (((1 << 32) + 1) * 1000, []))
        self.assertEqual(self.finish("1410065408+2"), (10 ** 10 * 1000, []))   # the UG's example
        self.assertEqual(self.finish("0"), (0, []))                   # a stop at 0, not dropped
        self.assertEqual(self.finish("1.5ns"), (1500000, []))
        # a 1 s precision: one tick is translated as 1 ms; 6 s is 6 ticks
        self.assertEqual(self.finish("6s", "1s"), (6 * 10 ** 12, []))
        self.assertEqual(self.finish("6", "1s"), (6 * 10 ** 12, []))

    def test_bad_values_are_reported(self):
        for val in ("", "abc", "300xs", "3+", "-5"):
            with self.subTest(val=val):
                fs, notes = self.finish(val)
                self.assertIsNone(fs)
                self.assertEqual(notes, [("+vcs+finish+" + val, UNKNOWN)])
        fs, notes = self.finish("100", precision=None)
        self.assertEqual((fs, notes), (None, [("+vcs+finish+100", UNSUPPORTED)]))
        fs, notes = self.finish("9999999999+9999999")                  # beyond TIME'HIGH
        self.assertEqual((fs, notes), (None, [("+vcs+finish+9999999999+9999999", UNSUPPORTED)]))


# -- simv.py: the run (a stand-in backend) ---------------------------------------------------

class StubBackend:
    result = (0, "10ns")
    signal_after = None
    seen = None

    def __init__(self, job, emit):
        self.interrupted, self.killed = None, False

    def run_tools(self):
        return []

    def run(self, top, plusargs, out, err, stop_fs=None):
        StubBackend.seen = stop_fs
        self.interrupted = StubBackend.signal_after
        return StubBackend.result


class TestSimvRun(TempDir):
    def setUp(self):
        super().setUp()
        self.daidir = os.path.join(self.tmp, "simv.daidir")
        os.makedirs(self.daidir)
        with open(os.path.join(self.daidir, "vamos.job.json"), "w") as fh:
            fh.write(Job(personality="vcs", tops=["tb"], precision="1ps",
                         daidir=self.daidir).to_json())
        self.saved = simv.NvcBackend
        simv.NvcBackend = StubBackend
        StubBackend.result, StubBackend.signal_after, StubBackend.seen = (0, "10ns"), None, None

    def tearDown(self):
        simv.NvcBackend = self.saved
        super().tearDown()

    def run_simv(self, args, opts=None, daidir=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = simv.run_daidir(daidir or self.daidir, list(args), opts or {})
        return rc, out.getvalue(), err.getvalue()

    def test_help(self):
        rc, out, err = self.run_simv(["-h"], daidir=os.path.join(self.tmp, "nowhere"))
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("usage: ./simv", out)
        self.assertIn("+vcs+finish+<time>", out)
        self.assertIn("VAMOS_GUIDE.md", out)
        self.assertIsNone(StubBackend.seen)
        self.assertEqual(self.run_simv(["-help"])[0], 0)

    def test_strict_wording_matches_the_compile(self):
        rc, _, err = self.run_simv(["-cm", "line", "+vcs+finish+abc"], {"strict": True})
        self.assertEqual(rc, 1)
        self.assertIn("vamos: error: --vamos-strict: 2 unsupported/unknown option(s): -cm line "
                      "+vcs+finish+abc", err)

    def test_notes_say_why(self):
        rc, _, err = self.run_simv(["+notimingcheck", "-assert", "nopostproc"])
        self.assertEqual(rc, 0)
        self.assertIn("vamos: note: +notimingcheck: timing checks are not modelled, so there are "
                      "none to disable", err)
        self.assertRegex(err, r"vamos: note: -assert nopostproc: assertion run-time controls are "
                              r"not mapped yet \(vamos prints no assertion summary")

    def test_newer_job_message(self):
        with open(os.path.join(self.daidir, "vamos.job.json"), "w") as fh:
            json.dump({"personality": "vcs", "schema": 99}, fh)
        rc, _, err = self.run_simv([])
        self.assertEqual(rc, 1)
        self.assertEqual(err.count("newer vamos"), 1, err)
        self.assertIn("compiled by a newer vamos (schema 99); recompile", err)

    def test_footer(self):
        rc, out, _ = self.run_simv(["+vcs+finish+22ns"])
        self.assertEqual((rc, StubBackend.seen), (0, 22 * 10 ** 6))
        self.assertIn("Time: 10ns", out)
        StubBackend.result = (1, None)                     # nothing was simulated: no footer
        rc, out, _ = self.run_simv([])
        self.assertEqual(rc, 1)
        self.assertNotIn("S i m u l a t i o n", out)

    def test_a_signal_ends_simv_with_that_signal(self):
        script = ("import sys\nsys.path.insert(0, %r)\nsys.path.insert(0, %r)\n"
                  "import signal\nfrom vamos.personalities import simv\n"
                  "import test_vamos_run as t\n"
                  "t.StubBackend.result = (143, '7ns')\n"
                  "t.StubBackend.signal_after = signal.SIGTERM\n"
                  "simv.NvcBackend = t.StubBackend\n"
                  "sys.exit(simv.run_daidir(%r, [], {}))\n"
                  % (ROOT, os.path.dirname(os.path.abspath(__file__)), self.daidir))
        r = subprocess.run([sys.executable, "-c", script], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)
        self.assertEqual(r.returncode, -signal.SIGTERM, r.stdout)
        self.assertIn("Time: 7ns", r.stdout)            # the footer comes first
        self.assertNotIn("Traceback", r.stdout)


# -- cosim.py: the rawfile check -------------------------------------------------------------

def blank(path, value=b""):
    """Rewrite the No. Points: value field, keeping its width (Xyce's paused state)."""
    with open(path, "rb") as fh:
        data = fh.read()
    start = data.index(b"No. Points: ") + len(b"No. Points: ")
    end = data.index(b"\n", start)
    with open(path, "wb") as fh:
        fh.write(data[:start] + value.ljust(end - start) + data[end:])


class TestCheckRaw(TempDir):
    """cosim.check_raw = rawfile.fix_points + rawfile.read(...).last_time(), in O(1) memory."""

    def variants(self):
        """(name, bytes) of every fixture and the shapes test_netlist_rawfile covers."""
        out = []
        for name in ("raw_vacask_tran.raw", "raw_vacask_tran_ascii.raw", "raw_xyce_tran.raw",
                     "raw_xyce_tran_ascii.raw", "raw_vacask_ac.raw", "raw_vacask_ac_ascii.raw",
                     "raw_xyce_ac.raw"):
            with open(fx(name), "rb") as fh:
                data = fh.read()
            out.append((name, data))
            s = data.index(b"No. Points: ") + len(b"No. Points: ")
            e = data.index(b"\n", s)
            for tag, val in (("blank", b""), ("wrong99", b"99"), ("small12", b"12")):
                out.append((name + ":" + tag, data[:s] + val.ljust(e - s) + data[e:]))
            out.append((name + ":narrow", data[:s - len(b"No. Points: ")] + b"No. Points: 5" + data[e:]))
            ls = data.index(b"No. Points:")
            out.append((name + ":nocount", data[:ls] + data[data.index(b"\n", ls) + 1:]))
            out.append((name + ":two", data + data.replace(b"Transient Analysis", b"Second")))
            if b"Binary:" in data:
                out.append((name + ":truncated", data[:-12]))
            else:
                out.append((name + ":truncated", data[:data.rindex(b"\t")]))
                out.append((name + ":crlf", data.replace(b"\n", b"\r\n")))
        return out

    def test_same_as_rawfile(self):
        for name, data in self.variants():
            with self.subTest(case=name):
                a, b = os.path.join(self.tmp, "a.raw"), os.path.join(self.tmp, "b.raw")
                for p in (a, b):
                    with open(p, "wb") as fh:
                        fh.write(data)
                plots = cosim.scan_raw(a)
                want = rawfile.read_all(a)
                self.assertEqual([p.n for p in plots], [len(r.points) for r in want])
                for p, r in zip(plots, want):
                    t = [x[0].real if r.complex else x[0] for x in r.points]
                    self.assertEqual((p.first, p.last), (t[0], t[-1]) if t else (None, None))
                changed = cosim._fix_points(a, plots)
                self.assertEqual(changed, rawfile.fix_points(b))
                with open(a, "rb") as fa, open(b, "rb") as fb:
                    self.assertEqual(fa.read(), fb.read())
                self.assertFalse(os.path.exists(a + ".vamos-fix"))

    def test_check_raw(self):
        p = os.path.join(self.tmp, "x.raw")
        shutil.copy(fx("raw_xyce_tran.raw"), p)
        blank(p)
        self.assertEqual(cosim.check_raw(p), (True, 1e-9))
        self.assertEqual(cosim.check_raw(p), (False, 1e-9))
        with self.assertRaises(rawfile.RawError):                 # an operating point
            cosim.check_raw(fx("raw_vacask_op.raw"))
        for name, data in (("empty.raw", b""), ("text.raw", b"hello world\n"),
                           ("lt.raw", b"Title: x\nFlags: real forward\nNo. Variables: 1\n"
                                      b"No. Points: 0\nVariables:\n\t0\ttime\ttime\nBinary:\n"),
                           ("nodata.raw", b"Title: x\nNo. Variables: 1\nVariables:\n\t0\ttime\ttime\n"),
                           ("novars.raw", b"Title: x\nNo. Variables: 1\nBinary:\n"),
                           ("junk.raw", b"Title: x\nNo. Variables: 1\nVariables:\n\t0\ttime\ttime\n"
                                        b"Values:\n0\t1.0\nbad\n")):
            with self.subTest(case=name):
                q = os.path.join(self.tmp, name)
                with open(q, "wb") as fh:
                    fh.write(data)
                with self.assertRaises(rawfile.RawError):
                    cosim.check_raw(q)

    def test_no_points(self):
        """VACASK's file for a run that ended before the .tran start: a header, no data."""
        p = os.path.join(self.tmp, "empty_tran.raw")
        with open(p, "wb") as fh:
            fh.write(b"Title: x\nDate: y\nPlotname: Transient Analysis\nFlags: real\n"
                     b"No. Variables: 2\nNo. Points: 0             \nVariables:\n"
                     b"\t0\ttime\tnotype\n\t1\tv\tnotype\nBinary:\n")
        self.assertEqual(cosim.check_raw(p), (False, None))

    def test_memory_is_constant(self):
        """A 64 MB rawfile with a blank count: the check never holds its data."""
        for narrow in (False, True):
            with self.subTest(rewrite=narrow):
                p = os.path.join(self.tmp, "big%d.raw" % narrow)
                head = (b"Title: big\nDate: x\nPlotname: Transient Analysis\nFlags: real\n"
                        b"No. Variables: 4\nNo. Points: " + (b"7" if narrow else b" " * 18) +
                        b"\nVariables:\n\t0\ttime\ttime\n\t1\ta\tv\n\t2\tb\tv\n\t3\tc\tv\nBinary:\n")
                n = (64 << 20) // 32
                with open(p, "wb") as fh:
                    fh.write(head)
                    fh.truncate(len(head) + (n - 1) * 32)     # sparse zeros
                    fh.seek(0, 2)
                    fh.write(struct.pack("<4d", 2.5e-3, 0.0, 0.0, 0.0))
                tracemalloc.start()
                try:
                    changed, last = cosim.check_raw(p)
                    peak = tracemalloc.get_traced_memory()[1]
                finally:
                    tracemalloc.stop()
                self.assertEqual((changed, last), (True, 2.5e-3))
                self.assertLess(peak, 8 << 20, "peak %d bytes" % peak)
                with open(p, "rb") as fh:
                    self.assertIn(b"No. Points: %d" % n, fh.read(400))


# -- cosim.py: .tran TSTART, the end-of-output rules, publishing ----------------------------

class TestTranStart(TempDir):
    def test_decks(self):
        cases = [("vacask", "control\n  analysis vamos_tran tran step=1e-09 stop=1e-06 "
                            "start=5e-07 maxstep=5e-09\nendc\n", 5e-07),
                 ("vacask", "  analysis vamos_tran tran step=1e-09 stop=1e-06 maxstep=5e-09\n", 0.0),
                 ("xyce", "* t\n.tran 1e-09 1e-06 5e-07 5e-09\n.end\n", 5e-07),
                 ("xyce", ".tran 1e-09 1e-06 5e-07\n", 5e-07),
                 ("xyce", ".tran 1e-09 1e-06 0.0 5e-09 UIC\n", 0.0),
                 ("xyce", ".tran 1e-09 1e-06 UIC\n", 0.0)]
        for engine, text, want in cases:
            with self.subTest(text=text):
                self.assertEqual(cosim.tran_start(self.write("d", text), engine), want)
        self.assertEqual(cosim.tran_start(os.path.join(self.tmp, "none"), "xyce"), 0.0)


def write_raw(path, times):
    with open(path, "wb") as fh:
        fh.write(b"Title: x\nDate: y\nPlotname: Transient Analysis\nFlags: real\n"
                 b"No. Variables: 2\nNo. Points: %-16d\nVariables:\n\t0\ttime\tnotype\n"
                 b"\t1\tv\tnotype\nBinary:\n" % len(times))
        for t in times:
            fh.write(struct.pack("<2d", t, 0.0))


class TestPublish(TempDir):
    def publish(self, end_line, times=None, deck="", engine="vacask", stop=1e-6, run_fs=None):
        state = cosim.EndState()
        state.feed(end_line)
        raw = os.path.join(self.tmp, "vamos_tran.raw")
        if os.path.exists(raw):
            os.remove(raw)
        if times is not None:
            write_raw(raw, times)
        published = os.path.join(self.tmp, "vamos_ams.raw")
        if os.path.exists(published):
            os.remove(published)
        con = Sink()
        ok = cosim._publish({}, self.write("deck", deck), engine, stop,
                            run_fs if run_fs is not None else int(stop * 1e15) + 1, state, raw,
                            published, con)
        return ok, os.path.exists(published), con.text()

    def test_two_fs_floor(self):
        stop = "** Note: co-simulation finished: digital stop at 1.73760596e-07 s"
        ok, pub, msg = self.publish(stop, [0.0, 173760595.7301e-15])     # 0.27 fs short
        self.assertEqual((ok, pub, msg), (True, True, ""))
        ok, pub, msg = self.publish(stop, [0.0, 173760593e-15])          # 3 fs short
        self.assertEqual((ok, pub), (False, False))
        self.assertIn("analog output ends early at 1.73760593e-07 s (expected 1.73760596e-07 s)",
                      msg)

    def test_tstart(self):
        stop = "** Note: co-simulation finished: digital stop at 3e-07 s"
        vdeck = "  analysis vamos_tran tran step=1e-09 stop=1e-06 start=5e-07\n"
        ok, pub, msg = self.publish(stop, [], vdeck)                   # VACASK: no points
        self.assertEqual((ok, pub), (True, False))
        self.assertIn("vamos: note: no analog output: the run ended at 3e-07 s, before the "
                      "deck's output start 5e-07 s (.tran TSTART)", msg)
        ok, pub, msg = self.publish(stop, None, ".tran 1e-09 1e-06 5e-07 5e-09\n", "xyce")
        self.assertEqual((ok, pub), (True, False))                    # Xyce: no file at all
        self.assertIn("(.tran TSTART)", msg)
        analog = "** Note: co-simulation finished: analog end at 2e-07 s"
        self.assertEqual(self.publish(analog, [], vdeck, run_fs=200000000)[:2], (True, False))
        stop0 = ("** Note: co-simulation finished: digital stop at 0 s (before the first analog "
                 "step)")
        ok, pub, msg = self.publish(stop0, [], vdeck)
        self.assertEqual((ok, pub, msg),
                         (True, False, "vamos: note: no analog output: the digital stopped at t=0"))
        # a run past TSTART must have reached its end
        ok, pub, msg = self.publish("** Note: co-simulation finished: digital stop at 7e-07 s",
                                    [5e-7, 7e-7], vdeck)
        self.assertEqual((ok, pub), (True, True))

    def test_missing_output_fails(self):
        stop = "** Note: co-simulation finished: digital stop at 3e-07 s"
        ok, pub, msg = self.publish(stop, [])
        self.assertEqual((ok, pub), (False, False))
        self.assertIn("vamos: error: the analog rawfile has no points (expected output through "
                      "3e-07 s)", msg)
        ok, pub, msg = self.publish(stop, None)
        self.assertEqual((ok, msg), (False, "vamos: error: the analog engine wrote no rawfile"))
        with open(os.path.join(self.tmp, "vamos_tran.raw"), "w") as fh:
            fh.write("junk\n")
        state = cosim.EndState()
        state.feed(stop)
        con = Sink()
        self.assertFalse(cosim._publish({}, self.write("deck", ""), "vacask", 1e-6, 10 ** 9, state,
                                        os.path.join(self.tmp, "vamos_tran.raw"),
                                        os.path.join(self.tmp, "p.raw"), con))
        self.assertIn("vamos: error: cannot read the analog rawfile", con.text())

    def test_footer_time(self):
        class Filt:
            last_time = "50ns"
        raw = os.path.join(self.tmp, "part.raw")
        state = cosim.EndState()
        self.assertIsNone(cosim._footer(state, Filt(), raw))          # nothing was simulated
        state.feed("** Note: starting co-simulation (stop_time=1e-06 s)")
        self.assertEqual(cosim._footer(state, Filt(), raw), "50ns")    # nvc died: last report
        write_raw(raw, [0.0, 1e-7, 2e-7])
        self.assertEqual(cosim._footer(state, Filt(), raw), "200ns")   # ... or the analog's
        state.feed("** Note: co-simulation finished: digital stop at 0.002000000006 s")
        self.assertEqual(cosim._footer(state, Filt(), raw), "2000000006ps")

    def test_concurrent_removal_of_the_published_file(self):
        p = os.path.join(self.tmp, "vamos_ams.raw")
        cosim._remove_published(p)                          # already gone: not an error
        with open(p, "w") as fh:
            fh.write("x")
        cosim._remove_published(p)
        self.assertFalse(os.path.exists(p))


# -- cosim.py: the ABI pre-check -------------------------------------------------------------

def make_elf(path, symbols):
    """A minimal ELF64 shared object whose .dynsym holds symbols: {name: defined}."""
    strtab = b"\0"
    syms = [struct.pack("<IBBHQQ", 0, 0, 0, 0, 0, 0)]
    for name, defined in symbols.items():
        syms.append(struct.pack("<IBBHQQ", len(strtab), 0x12, 0, 7 if defined else 0, 0, 0))
        strtab += name.encode() + b"\0"
    symtab = b"".join(syms)
    sym_off = 64
    str_off = sym_off + len(symtab)
    sh_off = (str_off + len(strtab) + 7) // 8 * 8
    ehdr = (b"\x7fELF" + bytes([2, 1, 1]) + bytes(9) +
            struct.pack("<HHIQQQIHHHHHH", 3, 62, 1, 0, 0, sh_off, 0, 64, 0, 0, 64, 3, 0))
    shdrs = (struct.pack("<IIQQQQIIQQ", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0) +
             struct.pack("<IIQQQQIIQQ", 0, 11, 2, 0, sym_off, len(symtab), 2, 1, 8, 24) +
             struct.pack("<IIQQQQIIQQ", 0, 3, 2, 0, str_off, len(strtab), 0, 0, 1, 0))
    with open(path, "wb") as fh:
        fh.write(ehdr + symtab + strtab + bytes(sh_off - str_off - len(strtab)) + shdrs)


class TestAbiCheck(TempDir):
    def setUp(self):
        super().setUp()
        self.nvclib = os.path.join(self.tmp, "nvc", "lib")
        self.xyce = os.path.join(self.tmp, "xyce")
        for d in (self.nvclib, self.xyce):
            os.makedirs(d)

    def check(self, bridge=True, xyce=True):
        if bridge is not None:
            make_elf(os.path.join(self.nvclib, "libcosim_bridge.so"), {"cosim_bridge_abi": bridge})
        if xyce is not None:
            make_elf(os.path.join(self.xyce, "libxycecinterface.so"), {"xyce_cosim_abi": xyce,
                                                                       "xyce_open": True})
        return cosim.abi_check("xyce", self.nvclib, os.pathsep.join([self.nvclib, self.xyce]))

    def test_good(self):
        self.assertEqual(self.check(), ([], None))

    def test_missing_library(self):
        notes, err = self.check(xyce=None)
        self.assertRegex(err, r"^libxycecinterface\.so not found \(searched .*%s.*\): set "
                              r"VAMOS_XYCE_LIBS to the directories holding libxycecinterface\.so "
                              r"and libxyce\.so$" % re.escape(self.xyce))
        self.assertNotIn("rebuild", err)

    def test_stale_library(self):
        notes, err = self.check(xyce=False)
        self.assertEqual(err, "%s does not export xyce_cosim_abi() (it predates the vamos "
                              "co-simulation ABI): rebuild Xyce with the vamos co-simulation "
                              "patches" % os.path.join(self.xyce, "libxycecinterface.so"))
        notes, err = self.check(bridge=False)
        self.assertIn("libcosim_bridge.so does not export cosim_bridge_abi()", err)
        self.assertIn("rebuild it from nvc src/cosim_bridge.cpp", err)
        self.assertNotIn("Xyce", err)

    def test_unreadable_library_is_left_to_nvc(self):
        self.check()
        with open(os.path.join(self.xyce, "libxycecinterface.so"), "w") as fh:
            fh.write("INPUT(libother.so)\n")                        # a linker script
        notes, err = cosim.abi_check("xyce", self.nvclib, os.pathsep.join([self.nvclib, self.xyce]))
        self.assertIsNone(err)
        self.assertEqual(len(notes), 1)
        self.assertIn("nvc checks the ABI when it loads it", notes[0])

    @unittest.skipUnless(LINUX and shutil.which("nm"), "real ELF libraries and nm")
    def test_agrees_with_nm(self):
        from vamos.ams import engines
        libs = [("/usr/local/src/nvc-build/lib/libcosim_bridge.so", "cosim_bridge_abi"),
                (engines.vacask_cinterface(), "vacask_cosim_abi"),
                (engines.xyce_cinterface() or "", "xyce_cosim_abi")]
        for lib, sym in libs:
            if not os.path.isfile(lib):
                continue
            nm = subprocess.run(["nm", "-D", "--defined-only", lib], stdout=subprocess.PIPE,
                                universal_newlines=True).stdout
            for s in (sym, "no_such_symbol_xyz"):
                with self.subTest(lib=lib, sym=s):
                    self.assertEqual(cosim._elf_defines(lib, s),
                                     re.search(r"\b%s\b" % s, nm) is not None)


# -- plain simv (the digital stack) ------------------------------------------------------------

@unittest.skipUnless(have_stack(), "needs nvc + iverilog (Linux/WSL)")
class TestPlainSimv(TempDir):
    def vcs(self, src):
        self.write("tb.v", src)
        env = dict(os.environ)
        env["PATH"] = os.path.join(ROOT, "shims") + os.pathsep + env.get("PATH", "")
        self.env = env
        r = subprocess.run(["vcs", "-timescale=1ns/1ps", "tb.v"], cwd=self.tmp, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=600)
        self.assertEqual(r.returncode, 0, r.stdout)

    def simv(self, *args):
        return subprocess.run(["./simv"] + list(args), cwd=self.tmp, env=self.env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True, timeout=600)

    def test_fatal_after_simulation_finished_text(self):
        self.vcs("module tb;\n  initial begin\n    #300 $display(\"SIMULATION FINISHED: 3 checks "
                 "failed\");\n    $fatal(1, \"self-check failed\");\n  end\nendmodule\n")
        r = self.simv()
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("SIMULATION FINISHED: 3 checks failed", r.stdout)

    def test_finish_time_footer_and_forms(self):
        self.vcs("module tb;\n  reg clk = 0;\n  always #5 clk = ~clk;\n"
                 "  initial #1000 $finish;\nendmodule\n")
        for arg, want in (("+vcs+finish+22000", "Time: 22ns"), ("+vcs+finish+22ns", "Time: 22ns"),
                          ("+vcs+finish+22000+0", "Time: 22ns"), ("+vcs+finish+0", "Time: 0")):
            with self.subTest(arg=arg):
                r = self.simv(arg)
                self.assertEqual(r.returncode, 0, r.stdout)
                self.assertIn(want + "\n", r.stdout)
                self.assertNotIn("FINISH called", r.stdout)
        r = self.simv()
        self.assertIn("FINISH called", r.stdout)
        self.assertIn("Time: 1us\n", r.stdout)
        r = self.simv("+vcs+finish+22xs")
        self.assertIn("vamos: warning: unknown option +vcs+finish+22xs ignored (not a time value",
                      r.stdout)
        self.assertIn("Time: 1us\n", r.stdout)

    def test_help(self):
        self.vcs("module tb;\n  initial $display(\"ran\");\nendmodule\n")
        r = self.simv("-h")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("usage: ./simv", r.stdout)
        self.assertNotIn("ran", r.stdout.split("usage")[0])
        self.assertNotIn("\nran\n", r.stdout)


if __name__ == "__main__":
    unittest.main()
