"""Phase 2 (V): the VACASK runner, vamos/spectre/run_vacask.py (docs/VAMOS_SPECTRE_DESIGN.md §7.1,
§1 steps 10-11, §2.5, §2.7; the stop rules vamos/spectre/stop.py).

    cd tests/vamos && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_spectre_run_vacask

Both legs, no engine:
  TestCounts        vacask_count against the grids VACASK runs (measured on the 2026-10 build)
  TestCheckStep     check_step on synthetic VACASK rawfiles: the four rules of §7.1, the kept units
  TestAhdlCache     §2.5's Verilog-A directory, CDS_VLOGA_INCLUDE, the include list, the .osdi key
  TestFakeVacask    run() end to end with scripted stand-ins for VACASK and openvaf-r (VAMOS_VACASK,
                    VAMOS_OPENVAF): the statuses, files, failed_points and rc of §7.1 and stop.py, the
                    messages, the pseudo-terminal and the pipe, the Verilog-A cache, and the signals
                    of §2.7 (forwarded, a second one -> stop.Stop, the grace-period kill, kills from
                    outside, SIGUSR1, SIGUSR2), each sent by the stand-in at a chosen row
WSL (needs_vacask):
  TestVacaskE2E     e2e 1-10 through parse -> build -> resolve -> run(), against the vacask parts of
                    engine_expect.json (test_spectre_chain's checks)
  TestVacaskFailures   a failing op among good ones, a failing sweep point (inside a sweep block and in
                    a dc sweep), a tran aborting mid-run (E66), a bind failure (E28), a parse error and
                    a failing alter in the deck (rc 2), the Verilog-A cache with openvaf-r
  TestVacaskSignals    SIGTERM, SIGINT and a kill from outside during a long tran (rc 3, the partial
                    rows readable), a second signal (stop.Stop), SIGUSR1 (a status line, the run goes
                    on), a crash (rc 2)
"""

import json
import os
import re
import signal
import struct
import subprocess
import sys
import threading
import time
import unittest
from datetime import datetime
from unittest import mock

from vamos_testlib import LINUX, TempDir, procs_under  # noqa: F401
from spectre_testlib import SPECTRE_FIXTURES, needs_vacask  # noqa: E402

import test_spectre_chain as chainlib  # noqa: E402  (its helpers; its test classes stay in its module)

from vamos import banner  # noqa: E402
from vamos.ams import engines  # noqa: E402
from vamos.netlist import plan as PL, rawfile, signals as SG, spectre, vacask  # noqa: E402
from vamos.netlist.expr_ast import Num  # noqa: E402
from vamos.netlist.ir import SweepSpec  # noqa: E402
from vamos.notes import NoteError, error, warning  # noqa: E402
from vamos.spectre import results, run_vacask as RV, stop  # noqa: E402
from vamos.spectre.job import Settings, SpectreJob  # noqa: E402
from vamos.spectre.log import SpectreLog  # noqa: E402

E2E = os.path.join(SPECTRE_FIXTURES, "e2e")
POSIX_SIGNALS = all(hasattr(signal, n) for n in ("SIGUSR1", "SIGUSR2", "SIGHUP", "SIGQUIT", "SIGKILL"))
needs_signals = unittest.skipUnless(POSIX_SIGNALS and hasattr(os, "setpgid"),
                                    "needs POSIX signals and process groups")


# -- shared helpers ------------------------------------------------------------------------------------

def prepare(deck: str):
    """parse -> build -> resolve for VACASK, as test_spectre_chain.chain does, without rendering:
    (the parsed IR, the plan, the render copy, the SignalMap)."""
    deck = os.path.abspath(deck)
    nl = spectre.parse(deck, os.path.dirname(deck), spectre.SpectreParseOpts())
    plan = PL.build(nl, "vacask", Settings())
    nl2, sigmap, _notes = SG.resolve(nl, plan)
    return nl, plan, nl2, sigmap


def steps_of(plan):
    return [a.step for a in plan.actions if a.op == "analysis" and a.step is not None]


def write_raw(path: str, names, rows, complete: bool = True, cplx: bool = False, declared=None) -> None:
    """A VACASK binary rawfile (lib/outrawfile.cpp): `No. Points:` reserves 16 blanks and is filled
    when the analysis completes.  declared: a count written whatever the rows (a wrong field)."""
    with open(path, "wb") as fh:
        fh.write(("Title: t\nDate: d\nPlotname: p\nFlags: %s unpadded\nNo. Variables: %d\nNo. Points: "
                  % ("complex" if cplx else "real", len(names))).encode())
        pos = fh.tell()
        fh.write(b" " * 16 + b"\nVariables:\n")
        for i, n in enumerate(names):
            fh.write(("\t%d\t%s\tnotype\n" % (i, n)).encode())
        fh.write(b"Binary:\n")
        for row in rows:
            for v in row:
                if cplx:
                    c = complex(v)
                    fh.write(struct.pack("<dd", c.real, c.imag))
                else:
                    fh.write(struct.pack("<d", float(v)))
        if complete or declared is not None:
            fh.seek(pos)
            fh.write(str(len(rows) if declared is None else declared).encode())


def level(lid: str, mode: str, name: str = "", target=("variable", "p", ""), **kw):
    spec = SweepSpec(target="param", mode=mode, **{k: (Num(v) if k in ("start", "stop", "step") else
                                                       ([Num(x) for x in v] if k == "values" else v))
                                                   for k, v in kw.items()})
    return PL.SweepLevel(name=name, id=lid, target=target, spec=spec)


def step(kind: str, context=(), own=None, **args):
    return PL.AnalysisStep(id="vamos_a1", name="an", kind=kind, context=list(context), own=own, args=dict(args))


def freq(lid: str = "vamos_w9", **kw):
    lv = level(lid, kw.pop("mode", "dec"), target=("freq", "", ""), **kw)
    return lv


class QuietSignals:
    """No-op handlers for the signals run() handles while a test sends them: one that arrives after
    run() has returned must not end the test process.  run() saves and restores them."""

    def __enter__(self):
        self.saved = {}
        for s in stop.HANDLED + stop.FLAGS:
            self.saved[s] = signal.signal(s, lambda *a: None)
        return self

    def __exit__(self, *exc):
        for s, h in self.saved.items():
            signal.signal(s, h)


class RunnerCase(TempDir):
    """A job, a file-only SpectreLog and run() in the test's scratch directory."""

    def setUp(self):
        super().setUp()
        self.grace = RV.INTERRUPT_GRACE
        self.logs = []

    def tearDown(self):
        RV.INTERRUPT_GRACE = self.grace
        for lg in self.logs:
            lg.close()
        super().tearDown()

    def job(self, deck: str, **kw) -> SpectreJob:
        fields = dict(netlist=deck, log_mode="file", log_path=os.path.join(self.tmp, "spectre.out"),
                      ahdllibdir=os.path.join(self.tmp, "ahdl"))
        fields.update(kw)
        return SpectreJob("spectre", [deck], self.tmp, **fields)

    def log_for(self, job: SpectreJob) -> SpectreLog:
        lg = SpectreLog(job, banner.Banner(banner.load_profile("spectre"), "spectre"), datetime.now())
        self.logs.append(lg)
        return lg

    def log_text(self) -> str:
        for lg in self.logs:
            if lg.file:
                lg.file.flush()
        p = os.path.join(self.tmp, "spectre.out")
        if not os.path.isfile(p):
            return ""
        with open(p, encoding="utf-8") as fh:
            return fh.read()

    def deck(self, name: str, text: str, extra=None) -> str:
        p = self.write(name, text)
        for fn, body in (extra or {}).items():
            self.write(fn, body)
        return p

    def run_deck(self, deck: str, run_dir: str = None, strict: bool = False, job=None):
        """parse -> build -> resolve -> run_vacask.run; (EngineResult, plan, sigmap, render copy, run_dir)."""
        _nl, plan, nl2, sigmap = prepare(deck)
        run_dir = run_dir or os.path.join(self.tmp, "run")
        os.makedirs(run_dir, exist_ok=True)
        job = job or self.job(deck)
        er = RV.run(nl2, plan, sigmap, run_dir, job, self.log_for(job), strict=strict)
        return er, plan, sigmap, nl2, run_dir


# -- vacask_count ---------------------------------------------------------------------------------------

class TestCounts(unittest.TestCase):
    """The points VACASK runs (lib/answeep.cpp; measured 2026-10-16 on VACASK 0.3.4-91: 0..0.3 step 0.1
    runs 3 points, where start + k*step with a tolerance would give 4).  Phase 2's integration: a step
    grid whose VACASK count differs from the plan's is printed as values=[...] with the plan's points
    (vacask.step_values), so vacask_count, the points of the deck as rendered, equals the plan's grid."""

    def count(self, mode, **kw):
        return RV.vacask_count(level("w", mode, **kw).spec)

    def test_stepped_sweeps_follow_vacask(self):
        # VACASK's own rule on from/to/step
        self.assertEqual(vacask.vacask_step_count(0.0, 2.0, 0.1), 21)
        self.assertEqual(vacask.vacask_step_count(0.0, 0.3, 0.1), 3)
        self.assertEqual(vacask.vacask_step_count(0.0, 0.7, 0.1), 7)
        self.assertEqual(vacask.vacask_step_count(2.0, 0.0, -0.1), 21)
        self.assertIsNone(vacask.vacask_step_count(0.0, 1.0, -1.0))
        # the deck as rendered: step= where VACASK's count is the plan's, else the plan's values
        self.assertEqual(self.count("step", start=0, stop=2, step=0.1), 21)
        self.assertEqual(self.count("step", start=0, stop=0.3, step=0.1), 4)
        self.assertEqual(self.count("step", start=0, stop=0.7, step=0.1), 8)
        self.assertEqual(self.count("step", start=2, stop=0, step=-0.1), 21)
        self.assertEqual(self.count("step", start=0, stop=2, step=0.5), 5)
        self.assertEqual(self.count("step", start=0, stop=100, step=50), 3)
        self.assertIsNone(self.count("step", start=0, stop=1, step=-1))
        for start, stop_, st in ((0, 2, 0.1), (0, 0.3, 0.1), (0, 0.7, 0.1), (2, 0, -0.1), (0, 100, 50), (1e-3, 2e-3, 1e-4)):
            spec = level("w", "step", start=start, stop=stop_, step=st).spec
            self.assertEqual(self.count("step", start=start, stop=stop_, step=st), len(results.grid_values(spec)),
                             (start, stop_, st))
        self.assertIsNone(vacask.step_values(level("w", "step", start=0, stop=2, step=0.1).spec))
        self.assertEqual(vacask.step_values(level("w", "step", start=0, stop=0.3, step=0.1).spec),
                         results.grid_values(level("w", "step", start=0, stop=0.3, step=0.1).spec))

    def test_lin_dec_and_values(self):
        self.assertEqual(self.count("lin", start=0, stop=1, count=7), 8)
        self.assertEqual(self.count("dec", start=1, stop=1000, count=3), 10)
        self.assertEqual(self.count("dec", start=1, stop=1e6, count=7), 43)
        self.assertEqual(self.count("dec", start=1, stop=1e6, count=10), 61)      # e2e 1: 61 AC points
        self.assertEqual(self.count("dec", start=10, stop=100, count=1), 2)
        self.assertEqual(self.count("values", values=[1e3, 1e3, 2e3]), 3)
        self.assertEqual(self.count("log", values=[1.0, 3.0, 10.0]), 3)
        self.assertIsNone(self.count("dec", start=0, stop=10, count=3))


# -- check_step ------------------------------------------------------------------------------------------

class TestCheckStep(TempDir):
    """§7.1: the rawfile exists; No. Points is filled and equals the rows; the planned groups; a tran's
    groups end at stop.  `done`: the complete leading units of a failed swept step."""

    def raw(self, *a, **kw) -> str:
        p = os.path.join(self.tmp, "vamos_a1.raw")
        write_raw(p, *a, **kw)
        return p

    def test_missing_file(self):
        c = RV.check_step(step("op"), os.path.join(self.tmp, "none.raw"))
        self.assertFalse(c.exists)
        self.assertFalse(c.complete)
        self.assertIn("no rawfile", c.reason)

    def test_op_complete_header_only_and_wrong_count(self):
        c = RV.check_step(step("op"), self.raw(["a"], [[1.0]]))
        self.assertTrue(c.complete, c.reason)
        c = RV.check_step(step("op"), self.raw(["a"], [], complete=False))       # E66: a failed op
        self.assertFalse(c.complete)
        self.assertIsNone(c.declared)
        self.assertIn("blank", c.reason)
        c = RV.check_step(step("op"), self.raw(["a"], [[1.0]] * 3, declared=5))
        self.assertFalse(c.complete)
        self.assertIn("declares 5 points and holds 3", c.reason)
        c = RV.check_step(step("op"), self.raw(["a"], [[1.0], [2.0]]))           # an op is one point
        self.assertFalse(c.complete)
        self.assertIn("1 planned", c.reason)

    def test_plain_tran_rules(self):
        st = step("tran", stop=1e-3)
        rows = [[0.0, 0.0], [0.5e-3, 1.0], [1e-3, 2.0]]
        self.assertTrue(RV.check_step(st, self.raw(["time", "out"], rows)).complete)
        c = RV.check_step(st, self.raw(["time", "out"], rows[:2], complete=False))   # E66: aborted, blank count
        self.assertFalse(c.complete)
        c = RV.check_step(st, self.raw(["time", "out"], rows[:2]))                  # filled, but short of stop
        self.assertFalse(c.complete)
        self.assertIn("before stop", c.reason)
        st2 = step("tran", stop=1e-3 * (1 + 5e-10))                                 # within 1e-9
        self.assertTrue(RV.check_step(st2, self.raw(["time", "out"], rows)).complete)

    def test_swept_tran_groups(self):
        ctx = [level("vamos_w1", "values", name="swp", values=[1, 2, 3])]
        st = step("tran", context=ctx, stop=1e-3)
        group = lambda v: [[v, 0.0, 0.0], [v, 0.5e-3, 1.0], [v, 1e-3, 2.0]]  # noqa: E731
        full = group(1) + group(2) + group(3)
        names = ["vamos_w1", "time", "out"]
        self.assertTrue(RV.check_step(st, self.raw(names, full)).complete)
        c = RV.check_step(st, self.raw(names, group(1) + group(2)[:2], complete=False))
        self.assertFalse(c.complete)
        self.assertEqual((c.groups, c.done), (2, 1))
        c = RV.check_step(st, self.raw(names, group(1) + group(2)))                  # filled: 2 of 3 groups
        self.assertFalse(c.complete)
        self.assertEqual((c.groups, c.planned, c.done), (2, 3, 2))
        self.assertIn("2 of the 3 planned sweep groups", c.reason)
        # equal consecutive sweep values split by the restart of the time axis, never by value (E5)
        same = [level("vamos_w1", "values", name="swp", values=[1, 1])]
        self.assertTrue(RV.check_step(step("tran", context=same, stop=1e-3), self.raw(names, group(1) * 2)).complete)

    def test_op_rows_under_sweeps(self):
        ctx = [level("vamos_w1", "values", values=[1, 2]), level("vamos_w2", "values", values=[1, 2, 3])]
        names = ["vamos_w1", "vamos_w2", "out"]
        self.assertTrue(RV.check_step(step("op", context=ctx), self.raw(names, [[0, 0, 0]] * 6)).complete)
        c = RV.check_step(step("op", context=ctx), self.raw(names, [[0, 0, 0]] * 4, complete=False))
        self.assertEqual((c.complete, c.done), (False, 4))

    def test_dc_sweep_points(self):
        # 0..0.2 step 0.1: 3 points on VACASK and in the plan (0..0.3 would be printed as values, TestCounts)
        own = level("vamos_w1", "step", target=("instance", "v1", "dc"), start=0, stop=0.2, step=0.1)
        names = ["vamos_w1", "out"]
        self.assertTrue(RV.check_step(step("dc", own=own), self.raw(names, [[0, 0]] * 3)).complete)
        c = RV.check_step(step("dc", own=own), self.raw(names, [[0, 0]] * 2, complete=False))
        self.assertEqual((c.complete, c.done), (False, 2))
        # a dc sweep inside a sweep block: the kept units are the block's points
        ctx = [level("vamos_w2", "values", values=[1, 2])]
        st = step("dc", context=ctx, own=own)
        self.assertTrue(RV.check_step(st, self.raw(["vamos_w2"] + names, [[0, 0, 0]] * 6)).complete)
        c = RV.check_step(st, self.raw(["vamos_w2"] + names, [[0, 0, 0]] * 5, complete=False))
        self.assertEqual((c.complete, c.done), (False, 1))

    def test_ac_frequency_groups(self):
        ctx = [level("vamos_w1", "values", values=[1, 2])]
        st = step("ac", context=ctx, own=freq(start=10, stop=100, count=1))
        names = ["vamos_w1", "frequency", "out"]
        rows = [[1, 10, 1], [1, 100, 1], [2, 10, 1], [2, 100, 1]]
        self.assertTrue(RV.check_step(st, self.raw(names, rows, cplx=True)).complete)
        c = RV.check_step(st, self.raw(names, rows[:3], cplx=True, complete=False))
        self.assertEqual((c.complete, c.groups, c.done), (False, 2, 1))
        # one frequency per sweep point: every row is a group
        st1 = step("ac", context=ctx, own=freq(mode="values", values=[1e3]))
        self.assertTrue(RV.check_step(st1, self.raw(names, [[1, 1e3, 1], [2, 1e3, 1]], cplx=True)).complete)
        # a plain ac is one group whatever its grid
        self.assertTrue(RV.check_step(step("ac", own=freq(start=1, stop=10, count=1)),
                                      self.raw(["frequency", "out"], [[1, 1], [10, 1]], cplx=True)).complete)


# -- the Verilog-A directory and cache key ---------------------------------------------------------------

class TestAhdlCache(TempDir):
    """§2.5's order: -ahdllibdir, CDS_AHDLCMI_SIMDB_DIR (missing or not writable: an error),
    %C:r:t.ahdlSimDB under -outdir, then in the cwd; §2.8's include list; the .osdi key."""

    def job(self, **kw):
        return SpectreJob("spectre", [], self.tmp, **kw)

    def test_order(self):
        env = {}
        self.assertEqual(RV.ahdl_dir(self.job(netlist="sub/amp.scs", ahdllibdir="lib"), env),
                         os.path.join(self.tmp, "lib"))
        self.assertEqual(RV.ahdl_dir(self.job(netlist="sub/amp.scs", outdir="o"), env),
                         os.path.join(self.tmp, "o", "amp.ahdlSimDB"))
        self.assertEqual(RV.ahdl_dir(self.job(netlist="sub/amp.scs"), env), os.path.join(self.tmp, "amp.ahdlSimDB"))
        self.assertEqual(RV.ahdl_dir(self.job(), env), os.path.join(self.tmp, "stdin.ahdlSimDB"))
        simdb = os.path.join(self.tmp, "simdb")
        os.makedirs(simdb)
        env = {"CDS_AHDLCMI_SIMDB_DIR": simdb}
        self.assertEqual(RV.ahdl_dir(self.job(netlist="amp.scs", outdir="o"), env), simdb)
        self.assertEqual(RV.ahdl_dir(self.job(netlist="amp.scs", ahdllibdir="/abs/l"), env), "/abs/l")
        # the defaults layer's -ahdllibdir counts too (args.effective)
        self.assertEqual(RV.ahdl_dir(self.job(netlist="amp.scs", defaults={"ahdllibdir": "dl"}), {}),
                         os.path.join(self.tmp, "dl"))

    def test_simdb_dir_must_exist_and_be_writable(self):
        with self.assertRaises(RV._Fail) as cm:
            RV.ahdl_dir(self.job(netlist="amp.scs"), {"CDS_AHDLCMI_SIMDB_DIR": os.path.join(self.tmp, "nope")})
        self.assertIn("does not exist", cm.exception.notes[0].message)
        if LINUX and hasattr(os, "geteuid") and os.geteuid() != 0:
            ro = os.path.join(self.tmp, "ro")
            os.makedirs(ro)
            os.chmod(ro, 0o500)
            try:
                with self.assertRaises(RV._Fail) as cm:
                    RV.ahdl_dir(self.job(netlist="amp.scs"), {"CDS_AHDLCMI_SIMDB_DIR": ro})
                self.assertIn("not writable", cm.exception.notes[0].message)
            finally:
                os.chmod(ro, 0o700)

    def test_include_list(self):
        self.assertEqual(RV.vloga_include({"CDS_VLOGA_INCLUDE": "a:b;c, d"}), ["a", "b", "c", "d"])
        self.assertEqual(RV.vloga_include({}), [])
        job = self.job(incdirs=["inc", "/abs/inc", "inc"])
        self.assertEqual(RV.va_search(job, {"CDS_VLOGA_INCLUDE": "va;/abs/va"}),
                         [os.path.join(self.tmp, "va"), "/abs/va", os.path.join(self.tmp, "inc"), "/abs/inc"])

    def test_key_follows_every_input(self):
        src = self.write("m.va", '`include "h.vams"\nmodule m(a); inout a; electrical a; analog V(a) <+ `K; endmodule\n')
        inc = self.write("inc/h.vams", "`define K 1.0\n")
        ov = self.write("bin/openvaf-r", "#!/bin/sh\n")
        lib = os.path.join(self.tmp, "lib")
        self.assertEqual(RV.va_files(src, [os.path.dirname(inc)]), [src, inc])   # a user's .vams header too
        a = RV.osdi_path(src, lib, ov, [os.path.dirname(inc)], [])
        self.assertTrue(re.match(re.escape(os.path.join(lib, "m_")) + r"[0-9a-f]{40}\.osdi$", a), a)
        self.assertEqual(RV.osdi_path(src, lib, ov, [os.path.dirname(inc)], []), a)
        self.assertNotEqual(RV.osdi_path(src, lib, ov, [os.path.dirname(inc)], ["X=1"]), a)
        self.assertNotEqual(RV.osdi_path(src, lib, ov, [], []), a)
        with open(inc, "w") as fh:                       # an `include`d file changes the key
            fh.write("`define K 2.0\n")
        b = RV.osdi_path(src, lib, ov, [os.path.dirname(inc)], [])
        self.assertNotEqual(b, a)
        with open(src, "a") as fh:
            fh.write("// edited\n")
        self.assertNotEqual(RV.osdi_path(src, lib, ov, [os.path.dirname(inc)], []), b)


# -- a scripted stand-in for VACASK and openvaf-r ---------------------------------------------------------

FAKE_VACASK = r'''
import json, os, signal, struct, sys, time

spec_path = os.environ["VAMOS_FAKE_VACASK"]
with open(spec_path) as fh:
    spec = json.load(fh)
fd0 = "/proc/self/fd/0"
seen = {"argv": sys.argv[1:], "cwd": os.getcwd(), "pid": os.getpid(),
        "pgid": os.getpgid(0) if hasattr(os, "getpgid") else None, "stdout_tty": os.isatty(1),
        "stdin": os.readlink(fd0) if os.path.islink(fd0) else None,
        "env": {k: os.environ.get(k) for k in ("SIM_MODULE_PATH", "SIM_OPENVAF", "LD_LIBRARY_PATH")}}
with open(spec_path + ".seen", "w") as fh:
    json.dump(seen, fh)
if spec.get("ignore_term"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)


def out(line):
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def nap(seconds):
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(0.02)


def event(ev):
    nap(ev.get("before", 0))
    if "sig" in ev:
        os.kill(os.getppid(), getattr(signal, ev["sig"]))
    if "self" in ev:
        os.kill(os.getpid(), getattr(signal, ev["self"]))
    nap(ev.get("sleep", 0))


for line in ["This is vacask 0.3.4-fake.", "(c)2023-2026 EDA Lab FE Uni-Lj, Arpad Buermen",
             "https://codeberg.org/arpadbuermen/VACASK", "  OpenMP parallel processing", "",
             "Available CPUs:     1", "CPUs used:          1", "OpenBLAS CPUs:      1", ""]:
    out(line)
for line in spec.get("pre", []):
    out(line)
if spec.get("parse_error"):
    sys.exit(spec.get("rc", 1))
out("Simulating: " + spec.get("title", "// fake"))
for line in spec.get("setup", []):
    out(line)
if spec.get("setup_error"):
    sys.exit(spec.get("rc", 1))
for st in spec.get("steps", []):
    out("Running analysis '%s'." % st["id"])
    raw = st.get("raw")
    if raw is not None:
        names, cplx = raw["vars"], raw.get("complex", False)
        with open(st["id"] + ".raw", "wb") as fh:
            fh.write(("Title: fake\nDate: now\nPlotname: %s\nFlags: %s unpadded\nNo. Variables: %d\nNo. Points: "
                      % (st["id"], "complex" if cplx else "real", len(names))).encode())
            pos = fh.tell()
            fh.write(b" " * 16 + b"\nVariables:\n")
            for i, n in enumerate(names):
                fh.write(("\t%d\t%s\tnotype\n" % (i, n)).encode())
            fh.write(b"Binary:\n")
            fh.flush()
            for i, row in enumerate(raw["rows"]):
                for v in row:
                    fh.write(struct.pack("<dd", v, 0.0) if cplx else struct.pack("<d", v))
                fh.flush()
                for ev in raw.get("events", []):
                    if ev["at"] == i:
                        event(ev)
            if raw.get("complete", True):
                fh.seek(pos)
                fh.write(str(len(raw["rows"])).encode())
    out("  Elapsed time: %s" % st.get("elapsed", "0.00123"))
    for line in st.get("lines", []):
        out(line)
for line in spec.get("tail", []):
    out(line)
if spec.get("self"):
    os.kill(os.getpid(), getattr(signal, spec["self"]))
    nap(5)
sys.exit(spec.get("rc", 0))
'''

FAKE_OPENVAF = r'''
import json, os, signal, sys, time
args = sys.argv[1:]
src, out = args[0], args[args.index("-o") + 1]
log = os.environ.get("VAMOS_FAKE_OPENVAF_LOG")
if log:
    with open(log, "a") as fh:
        fh.write(json.dumps({"argv": args, "cwd": os.getcwd()}) + "\n")
text = open(src).read()
if "fake_signal_parent" in text:
    os.kill(os.getppid(), signal.SIGTERM)
    end = time.time() + 30
    while time.time() < end:
        time.sleep(0.02)
if "fake_syntax_error" in text:
    sys.stdout.write("\x1b[0m\x1b[1m\x1b[31merror\x1b[0m\x1b[1m: unexpected token in %s\x1b[0m\n" % src)
    sys.exit(65)
with open(out, "w") as fh:
    fh.write("fake osdi of %s\n" % src)
    if "fake_slow" in text:                      # a compile two runs overlap on
        fh.flush()
        time.sleep(1.5)
    fh.write("complete\n")
sys.stdout.write("\x1b[0m\x1b[1m\x1b[32mFinished\x1b[0m building %s\n" % os.path.basename(src))
'''

DRIVER = r'''
import os, sys
sys.path[:0] = [%r]
from datetime import datetime
import vamos_testlib  # noqa: F401  (puts the repository on sys.path)
from vamos import banner
from vamos.spectre import run_vacask as RV
from vamos.spectre.job import SpectreJob
from vamos.spectre.log import SpectreLog
from test_spectre_run_vacask import prepare
deck, run_dir, lib = sys.argv[1:4]
_nl, plan, nl2, sigmap = prepare(deck)
job = SpectreJob("spectre", [deck], os.path.dirname(deck), netlist=deck, log_mode="file",
                 log_path=run_dir + ".log", ahdllibdir=lib)
log = SpectreLog(job, banner.Banner(banner.load_profile("spectre"), "spectre"), datetime.now())
er = RV.run(nl2, plan, sigmap, run_dir, job, log)
log.close()
print(er.rc, er.status)
sys.exit(er.rc)
''' % os.path.dirname(os.path.abspath(__file__))

FAKE_DECK = """// fake: an rc circuit with every kind of step
simulator lang=spectre
parameters rval=1k
V1 (in 0) vsource dc=1 type=pulse val0=0 val1=1 rise=1u fall=1u width=1m
R1 (in out) resistor r=rval
C1 (out 0) capacitor c=1u
op1 dc
tr1 tran stop=1m
swp sweep param=rval values=[1k 2k 3k] {
  tr2 tran stop=1m
}
dcs dc param=rval values=[1k 2k 3k 4k]
ac1 ac start=1 stop=1k dec=1
"""

VA_DECK = """// fake: a Verilog-A resistor
simulator lang=spectre
ahdl_include "vr.va"
V1 (a 0) vsource dc=1
N1 (a 0) vr r=2k
op1 dc
"""

VR_VA = """`include "disciplines.vams"
module vr(p, n);
  inout p, n; electrical p, n;
  parameter real r = 1k;
  analog I(p, n) <+ V(p, n) / r;
endmodule
"""


class FakeCase(RunnerCase):
    """run() with VAMOS_VACASK (and VAMOS_OPENVAF) naming the scripted stand-ins above."""

    def setUp(self):
        super().setUp()
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        self.vacask = self.script("vacask", FAKE_VACASK)
        self.openvaf = self.script("openvaf-r", FAKE_OPENVAF)
        self.spec_path = os.path.join(self.tmp, "fake.json")
        os.environ["VAMOS_VACASK"] = self.vacask
        os.environ["VAMOS_OPENVAF"] = self.openvaf
        os.environ["VAMOS_FAKE_VACASK"] = self.spec_path
        os.environ["VAMOS_FAKE_OPENVAF_LOG"] = os.path.join(self.tmp, "openvaf.log")
        os.environ.pop("VAMOS_VERBOSE", None)
        os.environ.pop("CDS_AHDLCMI_SIMDB_DIR", None)
        os.environ.pop("CDS_VLOGA_INCLUDE", None)

    def script(self, name: str, body: str) -> str:
        p = os.path.join(self.bin, name)
        with open(p, "w") as fh:
            fh.write("#!%s\n%s" % (sys.executable, body))
        os.chmod(p, 0o755)
        return p

    def fake(self, spec: dict) -> None:
        with open(self.spec_path, "w") as fh:
            json.dump(spec, fh)

    def seen(self) -> dict:
        p = self.spec_path + ".seen"
        if not os.path.isfile(p):
            return {}
        with open(p) as fh:
            return json.load(fh)

    def openvaf_calls(self):
        p = os.environ["VAMOS_FAKE_OPENVAF_LOG"]
        if not os.path.isfile(p):
            return []
        with open(p) as fh:
            return [json.loads(x) for x in fh if x.strip()]

    def setup_deck(self, text: str = FAKE_DECK, extra=None):
        deck = self.deck("deck.scs", text, extra)
        _nl, plan, _nl2, _sm = prepare(deck)
        return deck, steps_of(plan)


def op_raw(rows=None, complete=True):
    return {"vars": ["in", "out", "V1:flow(br)"], "rows": rows if rows is not None else [[1.0, 1.0, -1e-3]],
            "complete": complete}


def tran_raw(n=11, stop_=1e-3, upto=None, complete=True, events=(), sweep=None):
    rows = []
    for g in (sweep or [None]):
        for k in range(n):
            t = stop_ * k / (n - 1)
            if upto is not None and t > upto and g == (sweep or [None])[-1]:
                break
            rows.append(([g] if g is not None else []) + [t, 1.0, t])
    return {"vars": (["SW"] if sweep else []) + ["time", "in", "out"], "rows": rows, "complete": complete,
            "events": list(events)}


class TestFakeVacask(FakeCase):
    """§7.1's statuses and stop.py's rc, the messages and §2.7's signals, with scripted engines."""

    def all_ok(self, steps):
        a1, a2, a3, a4, a5 = steps
        sw3 = a3.context[0].id
        sw4 = a4.own.id
        return {"steps": [
            {"id": a1.id, "raw": op_raw()},
            {"id": a2.id, "raw": tran_raw()},
            {"id": a3.id, "raw": dict(tran_raw(sweep=[1e3, 2e3, 3e3]), vars=[sw3, "time", "in", "out"])},
            {"id": a4.id, "raw": {"vars": [sw4, "in", "out"], "rows": [[r, 1.0, 0.5] for r in (1e3, 2e3, 3e3, 4e3)]}},
            {"id": a5.id, "raw": {"vars": ["frequency", "in", "out"], "complex": True,
                                  "rows": [[f, 1.0, 0.9] for f in (1.0, 10.0, 100.0, 1000.0)]}}]}

    def test_all_ok(self):
        deck, steps = self.setup_deck()
        self.assertEqual([s.kind for s in steps], ["dc", "tran", "tran", "dc", "ac"])
        self.assertEqual([s.own is None for s in steps], [True, True, True, False, False])
        self.fake(self.all_ok(steps))
        er, plan, sigmap, nl2, run_dir = self.run_deck(deck)
        self.assertEqual(er.rc, 0, self.log_text())
        self.assertEqual(er.status, {s.id: "ok" for s in steps})
        self.assertEqual(er.files, {s.id: os.path.join(run_dir, s.id + ".raw") for s in steps})
        self.assertEqual(er.failed_points, {})
        self.assertEqual(er.names, vacask.names_for(nl2, plan, sigmap))
        self.assertTrue(er.names)
        seen = self.seen()
        self.assertEqual(seen["argv"], [RV.DECK])
        self.assertEqual(os.path.realpath(seen["cwd"]), os.path.realpath(run_dir))
        self.assertTrue(seen["stdout_tty"], "VACASK's output goes through a pseudo-terminal")
        if LINUX:
            self.assertEqual(seen["pgid"], seen["pid"], "VACASK runs in a process group of its own")
            self.assertEqual(seen["stdin"], "/dev/null")
        with open(os.path.join(run_dir, RV.DECK), encoding="utf-8") as fh:
            self.assertIn("analysis %s op" % steps[0].id, fh.read())
        with open(os.path.join(run_dir, RV.ENGINE_LOG)) as fh:
            self.assertIn("This is vacask", fh.read())                 # the whole output is kept
        text = self.log_text()
        self.assertNotIn("This is vacask", text)                       # VACASK's banner is not printed (§8.7)
        self.assertNotIn("Simulating:", text)
        self.assertNotIn("Running analysis", text)
        for s in steps:
            self.assertIn("`%s'" % s.name, text)
        self.assertIn("DC Analysis `op1'", text)
        self.assertIn("Transient Analysis `tr1': time = (0 s -> 1 ms)", text)
        self.assertIn("Total time required for dc analysis `op1' was 0.00 s.", text)
        self.assertIn("Total time required for tran analysis `tr2' was", text)
        self.assertEqual(text.count("Total time required"), len(steps))
        self.assertEqual(er.log, [])

    def test_failed_steps_and_points(self):
        deck, steps = self.setup_deck()
        a1, a2, a3, a4, a5 = steps
        spec = self.all_ok(steps)
        st = {s["id"]: s for s in spec["steps"]}
        # a failing op (E66: a header-only rawfile, no count)
        st[a1.id]["raw"] = op_raw(rows=[], complete=False)
        st[a1.id]["lines"] = ["Factorization failed, size=3, rank=2, zero pivot @ node 'v3:flow(br)'.",
                              "Homotopy failed, 12 step(s) tried.", "Analysis '%s' aborted." % a1.id]
        # a tran aborting mid-run: its rows up to the abort, a blank count, VACASK exit 0 (E66)
        st[a2.id]["raw"] = tran_raw(upto=0.4e-3, complete=False)
        st[a2.id]["lines"] = ["Timestep too small. Transient analysis aborted.", "Analysis '%s' aborted." % a2.id]
        # a failing sweep point inside a sweep block: point 1 complete, point 2 failed (E27)
        raw3 = dict(tran_raw(sweep=[1e3]), vars=st[a3.id]["raw"]["vars"], complete=False)
        st[a3.id]["raw"] = raw3
        st[a3.id]["lines"] = ["Operating point analysis failed.", "Analysis '%s' aborted." % a3.id,
                              "Sweep aborted @ 2/3 (2000)."]
        # a failing point of a dc sweep: points 1-2 written
        raw4 = st[a4.id]["raw"]
        raw4["rows"], raw4["complete"] = raw4["rows"][:2], False
        st[a4.id]["lines"] = ["Analysis '%s' aborted." % a4.id, "Sweep aborted @ 3/4 (3000)."]
        self.fake(spec)
        er, *_ = self.run_deck(deck)
        self.assertEqual(er.rc, 1, self.log_text())
        self.assertEqual(er.status, {a1.id: "failed", a2.id: "failed", a3.id: "failed", a4.id: "failed",
                                     a5.id: "ok"})
        self.assertEqual(er.failed_points, {a3.id: 2, a4.id: 3})
        self.assertEqual(sorted(er.files), sorted(s.id for s in steps))
        text = self.log_text()
        self.assertIn("Error from spectre during DC analysis `op1'.", text)
        self.assertIn("Analysis `op1' aborted.", text)                 # the engine's message, Spectre's name
        self.assertNotIn("'%s'" % a1.id, text)
        self.assertIn("Error from spectre during transient analysis `tr1'.", text)
        self.assertIn("vamos: analysis `tr1' failed: its rawfile's No. Points: is blank (5 rows written)", text)
        self.assertIn("sweep `swp' of analysis `tr2' failed at point 2 of 3: the results of point 1 are written", text)
        self.assertIn("sweep `dcs' of analysis `dcs' failed at point 3 of 4: the results of points 1-2 are written",
                      text)
        self.assertIn("Analysis '%s' aborted." % a1.id, er.log)          # EngineResult.log: the engine's lines

    def test_the_point_vacask_names_bounds_the_kept_groups(self):
        deck, steps = self.setup_deck()
        a3 = steps[2]
        spec = {"steps": [{"id": a3.id, "raw": dict(tran_raw(sweep=[1e3, 2e3]), vars=[a3.context[0].id, "time",
                                                                                      "in", "out"], complete=False),
                           "lines": ["Sweep aborted @ 2/3 (2000)."]}]}
        self.fake(spec)
        er, *_ = self.run_deck(deck)
        self.assertEqual(er.failed_points[a3.id], 2)                    # two complete groups, VACASK says point 2

    def test_a_count_short_of_stop_fails(self):
        deck, steps = self.setup_deck()
        spec = self.all_ok(steps)
        spec["steps"][1]["raw"] = tran_raw(upto=0.5e-3, complete=True)
        self.fake(spec)
        er, *_ = self.run_deck(deck)
        self.assertEqual((er.rc, er.status[steps[1].id]), (1, "failed"))
        self.assertIn("before stop = 1 ms", self.log_text())

    def test_bind_failure(self):
        deck, steps = self.setup_deck()
        spec = self.all_ok(steps)
        spec["steps"][0] = {"id": steps[0].id, "lines": ["Node 'nosuch' not found.", "Failed to bind analysis outputs."]}
        self.fake(spec)
        er, *_ = self.run_deck(deck)
        self.assertEqual(er.rc, 1)
        self.assertEqual(er.status[steps[0].id], "failed")
        self.assertNotIn(steps[0].id, er.files)
        self.assertNotIn(steps[0].id, er.failed_points)
        text = self.log_text()
        self.assertIn("Failed to bind analysis outputs.", text)
        self.assertIn("VACASK wrote no rawfile for it", text)

    def test_stale_rawfiles_are_removed(self):
        deck, steps = self.setup_deck()
        run_dir = os.path.join(self.tmp, "run")
        os.makedirs(run_dir)
        write_raw(os.path.join(run_dir, steps[0].id + ".raw"), ["in", "out", "V1:flow(br)"], [[1, 1, 0]])
        spec = self.all_ok(steps)
        spec["steps"][0] = {"id": steps[0].id, "lines": ["Failed to bind analysis outputs."]}
        self.fake(spec)
        er, *_ = self.run_deck(deck, run_dir=run_dir)
        self.assertEqual(er.status[steps[0].id], "failed")

    def test_a_step_vacask_never_names_fails_on_exit_0(self):
        deck, steps = self.setup_deck()
        spec = self.all_ok(steps)
        del spec["steps"][4]
        self.fake(spec)
        er, *_ = self.run_deck(deck)
        self.assertEqual((er.rc, er.status[steps[4].id]), (1, "failed"))

    def test_parse_error(self):
        deck, steps = self.setup_deck()
        self.fake({"parse_error": True, "rc": 1,
                   "pre": ["Parser syntax error, unexpected (, expecting newline", "  r1 (a 0) r r=1 (",
                           "                ^", "8:34 (0xc0) in vamos.sim"]})
        er, *_ = self.run_deck(deck)
        self.assertEqual(er.rc, 2)
        self.assertEqual((er.status, er.files, er.failed_points), ({}, {}, {}))
        text = self.log_text()
        self.assertIn("Error from spectre during circuit read-in.", text)
        self.assertIn("Parser syntax error", text)
        self.assertIn("exit status 1 before any analysis ran", text)

    def test_elaboration_error(self):
        deck, steps = self.setup_deck()
        self.fake({"setup_error": True, "rc": 1, "setup": ["Parameter 'bogus' not found."]})
        er, *_ = self.run_deck(deck)
        self.assertEqual((er.rc, er.status), (2, {}))
        self.assertIn("Error from spectre during initial setup.", self.log_text())

    def test_failing_control_statement_after_an_analysis(self):
        deck, steps = self.setup_deck()
        spec = {"rc": 1, "steps": [{"id": steps[0].id, "raw": op_raw()}],
                "tail": ['    alter instance("R1") bogus=3', "    ^", "Parameter 'bogus' not found."]}
        self.fake(spec)
        er, *_ = self.run_deck(deck)
        self.assertEqual(er.rc, 2)
        self.assertEqual(er.status, {steps[0].id: "ok"})              # the analysis that finished is kept
        text = self.log_text()
        self.assertIn("Error from spectre during control statements after analysis `op1'.", text)
        self.assertIn("Parameter 'bogus' not found.", text)
        self.assertIn("exit status 1", text)

    def test_a_crash_is_status_2(self):
        deck, steps = self.setup_deck()
        spec = self.all_ok(steps)
        spec["steps"][1]["raw"] = tran_raw(complete=False, events=[{"at": 4, "self": "SIGABRT"}])
        self.fake(spec)
        er, *_ = self.run_deck(deck)
        self.assertEqual(er.rc, 2, self.log_text())
        self.assertEqual(er.status, {steps[0].id: "ok", steps[1].id: "failed"})
        self.assertIn("VACASK was ended by SIGABRT during analysis `tr1'", self.log_text())

    def test_cannot_start(self):
        deck, steps = self.setup_deck()
        os.environ["VAMOS_VACASK"] = os.path.join(self.tmp, "no", "vacask")
        er, *_ = self.run_deck(deck)
        self.assertEqual((er.rc, er.status), (2, {}))
        self.assertIn("cannot run VACASK", self.log_text())

    def test_floods_are_capped(self):
        deck, steps = self.setup_deck()
        spec = self.all_ok(steps)
        spec["steps"][0]["lines"] = ["strobe %d" % i for i in range(3000)]
        self.fake(spec)
        er, *_ = self.run_deck(deck)
        self.assertEqual(er.rc, 0)
        text = self.log_text()
        self.assertIn("strobe %d" % (RV.ENGINE_LINES - 1), text)
        self.assertNotIn("strobe %d\n" % RV.ENGINE_LINES, text)
        self.assertIn("more engine lines in %s" % os.path.join(self.tmp, "run", RV.ENGINE_LOG), text)
        self.assertLessEqual(len(er.log), RV.LOG_LINES + 1)

    def test_engine_warnings_are_counted(self):
        deck, steps = self.setup_deck()
        spec = self.all_ok(steps)
        self.fake(spec)
        _er, *_ = self.run_deck(deck)
        base = self.logs[-1].counts["warnings"]
        spec["steps"][1]["lines"] = ["Warning, solution 'x' not found. No initial conditions applied."]
        self.fake(spec)
        er, *_ = self.run_deck(deck)
        self.assertEqual(er.rc, 0)
        self.assertEqual(self.logs[-1].counts["warnings"], base + 1)
        self.assertIn("Warning from spectre during transient analysis `tr1'.\n    Warning, solution 'x' not found.",
                      self.log_text())
        # an OSDI device's messages (lib/osdicallback.cpp) are engine warnings too, as on Xyce (phase 2);
        # OSDI(info) stays a plain line
        spec["steps"][1]["lines"] = ["OSDI(warn) M8: Source conductance reset to 1.0e3 mho.", "OSDI(info) M8: hello"]
        self.fake(spec)
        er, *_ = self.run_deck(deck)
        self.assertEqual((er.rc, self.logs[-1].counts["warnings"]), (0, base + 1))
        self.assertIn("Warning from spectre during transient analysis `tr1'.\n    OSDI(warn) M8: Source conductance "
                      "reset to 1.0e3 mho.", self.log_text())
        self.assertIn("\nOSDI(info) M8: hello\n", self.log_text())

    def test_pipe_without_a_pty(self):
        deck, steps = self.setup_deck()
        self.fake(self.all_ok(steps))
        with mock.patch.object(os, "openpty", side_effect=OSError("no pty")):
            er, *_ = self.run_deck(deck)
        self.assertEqual(er.rc, 0, self.log_text())
        self.assertEqual(set(er.status.values()), {"ok"})
        self.assertFalse(self.seen()["stdout_tty"])

    def test_verbose_echo(self):
        deck, steps = self.setup_deck()
        self.fake(self.all_ok(steps))
        os.environ["VAMOS_VERBOSE"] = "1"
        _er, *_ = self.run_deck(deck)
        self.assertIn("vamos: + cd %s && %s vamos.sim" % (os.path.join(self.tmp, "run"), self.vacask), self.log_text())

    def test_render_errors_and_warnings(self):
        deck, steps = self.setup_deck()
        self.fake(self.all_ok(steps))
        real = vacask.render

        def failing(*a, **kw):
            raise NoteError([error("deck.scs:3", "cannot print this")])
        with mock.patch.object(vacask, "render", failing):
            er, *_ = self.run_deck(deck)
        self.assertEqual((er.rc, er.status), (2, {}))
        self.assertEqual(self.seen(), {})                                # VACASK did not run
        self.assertIn("cannot print this", self.log_text())

        def warns(*a, **kw):
            text = real(*a, **kw)
            kw["notes"].append(warning("deck.scs:5", "an approximation"))
            return text
        with mock.patch.object(vacask, "render", warns):
            er, *_ = self.run_deck(deck)
        self.assertEqual(er.rc, 0)
        self.assertIn("Warning from spectre during circuit read-in.", self.log_text())
        os.remove(self.spec_path + ".seen")
        with mock.patch.object(vacask, "render", warns):
            er, *_ = self.run_deck(deck, strict=True)                    # --vamos-strict: an error, no run
        self.assertEqual((er.rc, er.status, self.seen()), (2, {}, {}))
        self.assertIn("Error from spectre during circuit read-in.", self.log_text())

    def test_plan_for_another_engine(self):
        deck, _steps = self.setup_deck()
        _nl, plan, nl2, sigmap = prepare(deck)
        plan.engine = "xyce"
        job = self.job(deck)
        with self.assertRaises(ValueError):
            RV.run(nl2, plan, sigmap, self.tmp, job, self.log_for(job))

    # -- the Verilog-A cache (§1 step 10, §2.5)
    def test_verilog_a_cache(self):
        deck, _steps = self.setup_deck(VA_DECK, {"vr.va": VR_VA})
        self.fake({"steps": [{"id": "vamos_a1", "raw": op_raw()}]})
        os.environ["CDS_VLOGA_INCLUDE"] = "vinc"
        job = self.job(deck, incdirs=["inc"], va_defines=["FAST=1"])
        er, *_ = self.run_deck(deck, job=job)
        self.assertEqual(er.rc, 0, self.log_text())
        lib = os.path.join(self.tmp, "ahdl")
        osdi = [f for f in os.listdir(lib)]
        self.assertEqual(len(osdi), 1, osdi)                              # no temporary file left behind
        self.assertRegex(osdi[0], r"^vr_[0-9a-f]{40}\.osdi$")
        calls = self.openvaf_calls()
        self.assertEqual(len(calls), 1)
        argv = calls[0]["argv"]
        self.assertEqual(argv[0], os.path.join(self.tmp, "vr.va"))
        self.assertEqual(argv[argv.index("-o") + 1].endswith(".part"), True)   # written beside, then moved
        self.assertEqual([argv[i + 1] for i, a in enumerate(argv) if a == "-I"],
                         [os.path.join(self.tmp, "vinc"), os.path.join(self.tmp, "inc")])
        self.assertEqual([argv[i + 1] for i, a in enumerate(argv) if a == "-D"], ["FAST=1"])
        self.assertEqual(os.path.realpath(calls[0]["cwd"]), os.path.realpath(self.tmp))
        with open(os.path.join(self.tmp, "run", RV.DECK)) as fh:
            self.assertIn('load "%s"' % os.path.join(lib, osdi[0]), fh.read())
        self.assertEqual(self.seen()["env"]["SIM_OPENVAF"], self.openvaf)        # engines.env_for (§7.1)
        # a second run reuses the compiled file; an edited source compiles anew
        _er, *_ = self.run_deck(deck, run_dir=os.path.join(self.tmp, "run2"), job=job)
        self.assertEqual(len(self.openvaf_calls()), 1)
        with open(os.path.join(self.tmp, "vr.va"), "a") as fh:
            fh.write("// edited\n")
        _er, *_ = self.run_deck(deck, run_dir=os.path.join(self.tmp, "run3"), job=job)
        self.assertEqual(len(self.openvaf_calls()), 2)
        self.assertEqual(len(os.listdir(lib)), 2)

    def test_two_runs_share_the_cache(self):
        """§2.5: concurrent runs share the directory; each .osdi is written beside it and moved, so
        both runs load a complete file and no temporary one is left."""
        deck, _steps = self.setup_deck(VA_DECK, {"vr.va": VR_VA.replace("endmodule", "// fake_slow\nendmodule")})
        self.fake({"steps": [{"id": "vamos_a1", "raw": op_raw()}]})
        driver = self.write("driver.py", DRIVER)
        lib = os.path.join(self.tmp, "shared")
        procs = [subprocess.Popen([sys.executable, driver, deck, os.path.join(self.tmp, "run%d" % k), lib],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT) for k in (1, 2)]
        outs = [p.communicate(timeout=120)[0].decode("utf-8", "replace") for p in procs]
        self.assertEqual([p.returncode for p in procs], [0, 0], outs)
        self.assertEqual(len(self.openvaf_calls()), 2, "the two compiles overlapped")
        files = os.listdir(lib)
        self.assertEqual(len(files), 1, files)
        with open(os.path.join(lib, files[0])) as fh:
            self.assertTrue(fh.read().endswith("complete\n"))
        for k in (1, 2):
            with open(os.path.join(self.tmp, "run%d" % k, RV.DECK)) as fh:
                self.assertIn('load "%s"' % os.path.join(lib, files[0]), fh.read())

    def test_verilog_a_compile_failure(self):
        deck, _steps = self.setup_deck(VA_DECK, {"vr.va": VR_VA.replace("endmodule", "// fake_syntax_error\nendmodule")})
        self.fake({"steps": [{"id": "vamos_a1", "raw": op_raw()}]})
        er, *_ = self.run_deck(deck)
        self.assertEqual((er.rc, er.status, self.seen()), (2, {}, {}))
        text = self.log_text()
        self.assertIn("Error from spectre during AHDL read-in.", text)
        self.assertIn("openvaf-r failed (exit status 65)", text)
        self.assertIn("unexpected token", text)
        self.assertNotIn("\x1b[", text)                                   # its colours are stripped
        self.assertEqual(os.listdir(os.path.join(self.tmp, "ahdl")), [])

    def test_verilog_a_without_openvaf_or_its_directory(self):
        deck, _steps = self.setup_deck(VA_DECK, {"vr.va": VR_VA})
        self.fake({"steps": [{"id": "vamos_a1", "raw": op_raw()}]})
        with mock.patch.object(engines, "openvaf", lambda: None):
            er, *_ = self.run_deck(deck)
        self.assertEqual(er.rc, 2)
        self.assertIn("cannot find openvaf-r", self.log_text())
        os.environ["CDS_AHDLCMI_SIMDB_DIR"] = os.path.join(self.tmp, "missing")
        er, *_ = self.run_deck(deck, job=self.job(deck, ahdllibdir=None))
        self.assertEqual(er.rc, 2)
        self.assertIn("CDS_AHDLCMI_SIMDB_DIR=", self.log_text())
        # a run without Verilog-A never needs the directory
        deck2, steps = self.setup_deck()
        self.fake(self.all_ok(steps))
        er, *_ = self.run_deck(deck2, run_dir=os.path.join(self.tmp, "run2"), job=self.job(deck2, ahdllibdir=None))
        self.assertEqual(er.rc, 0, self.log_text())

    # -- signals (§2.7, stop.py)
    @needs_signals
    def test_forwarded_signals_stop_with_status_3(self):
        for sig in ("SIGTERM", "SIGINT", "SIGHUP", "SIGQUIT"):
            with self.subTest(sig=sig):
                deck, steps = self.setup_deck()
                spec = self.all_ok(steps)
                spec["steps"][1]["raw"] = tran_raw(complete=False, events=[{"at": 5, "sig": sig, "sleep": 20}])
                self.fake(spec)
                run_dir = os.path.join(self.tmp, "run_" + sig)
                t0 = time.time()
                with QuietSignals():
                    er, *_ = self.run_deck(deck, run_dir=run_dir)
                self.assertLess(time.time() - t0, 15, "VACASK got SIGTERM and stopped at once")
                self.assertEqual(er.rc, 3, self.log_text())
                self.assertEqual(er.status, {steps[0].id: "ok", steps[1].id: "interrupted"})
                path = er.files[steps[1].id]
                plot = rawfile.read(path)
                self.assertEqual(plot.declared_points, len(plot.points))     # fix_points repaired the count
                self.assertEqual(len(plot.points), 6)
                self.assertIn("analysis `tr1' was interrupted at time = 500 us (vamos got %s and stopped VACASK)"
                              % sig, self.log_text())

    @needs_signals
    def test_a_second_signal_raises_stop(self):
        deck, steps = self.setup_deck()
        spec = self.all_ok(steps)
        spec["ignore_term"] = True
        spec["steps"][1]["raw"] = tran_raw(complete=False, events=[{"at": 3, "sig": "SIGINT", "sleep": 0.3},
                                                                   {"at": 3, "sig": "SIGINT", "sleep": 20}])
        self.fake(spec)
        t0 = time.time()
        with QuietSignals():
            with self.assertRaises(stop.Stop) as cm:
                self.run_deck(deck)
        self.assertLess(time.time() - t0, 15)
        self.assertEqual(cm.exception.signum, signal.SIGINT)
        pid = self.seen()["pid"]
        self.assertFalse(_alive(pid), "the engine was killed")

    @needs_signals
    def test_grace_period_kill_converts_with_status_3(self):
        RV.INTERRUPT_GRACE = 0.5
        deck, steps = self.setup_deck()
        spec = self.all_ok(steps)
        spec["ignore_term"] = True
        spec["steps"][1]["raw"] = tran_raw(complete=False, events=[{"at": 4, "sig": "SIGTERM", "sleep": 30}])
        self.fake(spec)
        t0 = time.time()
        with QuietSignals():
            er, *_ = self.run_deck(deck)
        self.assertLess(time.time() - t0, 15)
        self.assertEqual(er.rc, 3)
        self.assertEqual(er.status[steps[1].id], "interrupted")
        self.assertIn("VACASK did not stop within 0.5 s and was killed", self.log_text())

    @needs_signals
    def test_kills_from_outside(self):
        for sig, rc in (("SIGKILL", 3), ("SIGTERM", 3), ("SIGINT", 3), ("SIGHUP", 3), ("SIGQUIT", 2), ("SIGSEGV", 2)):
            with self.subTest(sig=sig):
                deck, steps = self.setup_deck()
                spec = self.all_ok(steps)
                spec["steps"][1]["raw"] = tran_raw(complete=False, events=[{"at": 5, "self": sig, "sleep": 5}])
                self.fake(spec)
                er, *_ = self.run_deck(deck, run_dir=os.path.join(self.tmp, "run_" + sig))
                self.assertEqual(er.rc, rc, self.log_text())
                self.assertEqual(er.status[steps[1].id], "interrupted" if rc == 3 else "failed")
                self.assertNotIn(steps[2].id, er.status)

    @needs_signals
    def test_signal_after_the_last_row(self):
        deck, steps = self.setup_deck()
        spec = self.all_ok(steps)
        spec["steps"][1]["raw"]["events"] = [{"at": 10, "sig": "SIGTERM", "sleep": 20}]   # after its last row
        spec["steps"][1]["raw"]["complete"] = False
        self.fake(spec)
        with QuietSignals():
            er, *_ = self.run_deck(deck)
        self.assertEqual(er.rc, 3)
        self.assertEqual(er.status[steps[1].id], "interrupted")
        self.assertEqual(len(rawfile.read(er.files[steps[1].id]).points), 11)

    @needs_signals
    def test_usr1_status_line_and_usr2_note(self):
        deck, steps = self.setup_deck()
        spec = self.all_ok(steps)
        spec["steps"][1]["raw"] = tran_raw(events=[{"at": 5, "before": 0.3, "sig": "SIGUSR1", "sleep": 0.6},
                                                   {"at": 6, "sig": "SIGUSR2", "sleep": 0.6}])
        self.fake(spec)
        with QuietSignals():
            er, *_ = self.run_deck(deck)
        self.assertEqual(er.rc, 0, self.log_text())                      # never forwarded: the run goes on
        self.assertEqual(set(er.status.values()), {"ok"})
        text = self.log_text()
        self.assertRegex(text, r"spectre status: analysis `tr1' running, time = 500 us of 1 ms \(50 %\), "
                               r"6 points written, elapsed = \d+ s\.")
        self.assertIn("Notice from spectre during transient analysis `tr1'.\n    SIGUSR2: checkpoint not supported", text)

    @needs_signals
    def test_signal_during_the_verilog_a_compile(self):
        deck, _steps = self.setup_deck(VA_DECK, {"vr.va": VR_VA.replace("endmodule", "// fake_signal_parent\nendmodule")})
        self.fake({"steps": [{"id": "vamos_a1", "raw": op_raw()}]})
        t0 = time.time()
        with QuietSignals():
            er, *_ = self.run_deck(deck)
        self.assertLess(time.time() - t0, 15)
        self.assertEqual((er.rc, er.status), (3, {}))
        self.assertEqual(self.seen(), {})                                 # VACASK never ran
        self.assertEqual(os.listdir(os.path.join(self.tmp, "ahdl")), [])
        self.assertIn("the run was stopped before VACASK ran (vamos got SIGTERM and stopped openvaf-r)", self.log_text())


def _state(pid: int) -> str:
    """A process's state letter from /proc ("" when it is gone or there is no /proc)."""
    try:
        with open("/proc/%d/stat" % pid) as fh:
            return fh.read().rsplit(")", 1)[-1].split()[0]
    except (OSError, IndexError):
        return ""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:                                   # a zombie is not running
        with open("/proc/%d/stat" % pid) as fh:
            return fh.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


# -- the real VACASK (WSL) ---------------------------------------------------------------------------------

@needs_vacask
class TestVacaskE2E(RunnerCase):
    """e2e 1-10 through run(): the statuses, the files, and engine_expect.json's vacask checks on the
    rawfiles run() leaves (test_spectre_chain.check_run)."""

    def check_case(self, case: str) -> None:
        ran = 0
        for deck, engine, exp in chainlib.engine_cases(case):
            if engine != "vacask" or (exp.get("status") == 2 and "refused" in exp):
                continue                                   # refused before any engine run (test_spectre_chain)
            with self.subTest(deck=deck):
                path = os.path.join(chainlib.case_dir(case), deck)
                run_dir = os.path.join(self.tmp, "%s_%s" % (case, os.path.splitext(deck)[0]))
                er, plan, sigmap, nl2, run_dir = self.run_deck(path, run_dir=run_dir)
                steps = steps_of(plan)
                self.assertEqual(er.rc, exp["status"], self.log_text()[-3000:])
                self.assertEqual(er.status, {s.id: "ok" for s in steps})
                self.assertEqual(er.files, {s.id: os.path.join(run_dir, s.id + ".raw") for s in steps})
                self.assertEqual(er.failed_points, {})
                self.assertEqual(er.names, vacask.names_for(nl2, plan, sigmap))
                r = chainlib.ChainResult()
                r.nl, r.plan, r.nl2, r.sigmap, r.names, r.steps = nl2, plan, nl2, sigmap, er.names, steps
                res = chainlib.EngineRun()
                res.status, res.stdout = er.rc, self.log_text()
                res.plots = {sid: rawfile.read_all(p) for sid, p in er.files.items()}
                chainlib.check_run(self, r, res, exp)
                ran += 1
        self.assertTrue(ran)


def _e2e_test(case):
    def test(self):
        self.check_case(case)
    test.__name__ = "test_" + case
    test.__doc__ = "e2e %s through run_vacask.run (§11 T2 %d)" % (case, int(case[:2]))
    return test


for _case in chainlib.CASES:
    if int(_case[:2]) <= 10:                           # the phase-1b decks with an engine_expect.json
        setattr(TestVacaskE2E, "test_" + _case, _e2e_test(_case))


BADSRC_VA = """`include "disciplines.vams"
// a 1 V source that goes NaN when bad > 0.5, or after t = tbad when tbad > 0 (the E66 pattern)
module badsrc(a);
  inout a; electrical a;
  parameter real bad = 0;
  parameter real tbad = 0;
  analog begin
    if (bad > 0.5 || (tbad > 0 && $abstime > tbad))
      V(a) <+ sqrt(-1.0 - V(a)*0);
    else
      V(a) <+ 1.0;
  end
endmodule
"""

FAIL_DECK = """// run_vacask: a failing op among good ones, failing sweep points, a tran aborting mid-run
simulator lang=spectre
ahdl_include "badsrc.va"
parameters pb=0 tb=0
N1 (a) badsrc bad=pb tbad=tb
R1 (a b) resistor r=1k
C1 (b 0) capacitor c=1n
op1 dc
alt1 alter param=pb value=1
op2 dc
alt2 alter param=pb value=0
op3 dc
swp sweep param=pb values=[0 1 0] {
  tr1 tran stop=1u
}
dcs dc param=pb values=[0 0 1 0]
alt3 alter param=tb value=1u
tr2 tran stop=3u
alt4 alter param=tb value=0
op4 dc
"""

RC_DECK = """// run_vacask: an RC filter, two ops around a tran
simulator lang=spectre
V1 (in 0) vsource type=sine sinedc=0 ampl=1 freq=1k
R1 (in out) resistor r=1k
C1 (out 0) capacitor c=1u
op1 dc
tr tran stop=%s maxstep=1e-7
op2 dc
"""


@needs_vacask
class TestVacaskFailures(RunnerCase):
    """§7.1's failure rules on the real engine (E6, E27, E28, E66)."""

    def test_failing_analyses_and_points(self):
        deck = self.deck("fail.scs", FAIL_DECK, {"badsrc.va": BADSRC_VA})
        er, plan, _sm, _nl2, run_dir = self.run_deck(deck)
        names = {s.id: s.name for s in steps_of(plan)}
        by_name = {names[k]: v for k, v in er.status.items()}
        self.assertEqual(by_name, {"op1": "ok", "op2": "failed", "op3": "ok", "tr1": "failed", "dcs": "failed",
                                   "tr2": "failed", "op4": "ok"}, self.log_text())
        self.assertEqual(er.rc, 1)                                        # VACASK itself exits 0 (E6, E66)
        fp = {names[k]: v for k, v in er.failed_points.items()}
        self.assertEqual(fp, {"tr1": 2, "dcs": 3})
        tr2 = [k for k, v in names.items() if v == "tr2"][0]
        self.assertLess(rawfile.read(er.files[tr2]).last_time(), 1.1e-6)  # the rows up to the abort (E66)
        text = self.log_text()
        self.assertIn("Analysis `op2' aborted.", text)
        self.assertIn("Sweep aborted @ 2/3", text)
        self.assertIn("Timestep too small", text)
        self.assertEqual(len([f for f in os.listdir(os.path.join(self.tmp, "ahdl")) if f.endswith(".osdi")]), 1)

    def test_bind_failure(self):
        deck = self.deck("rc.scs", RC_DECK % "1m" + "save out\n")       # explicit saves, not save default
        _nl, plan, nl2, sigmap = prepare(deck)
        first = steps_of(plan)[0].id
        sigmap.per_step[first].append((("v", "nosuch"), "nosuch", "V"))   # a save VACASK cannot bind (E28)
        job = self.job(deck)
        er = RV.run(nl2, plan, sigmap, os.path.join(self.tmp, "run"), job, self.log_for(job))
        self.assertEqual(er.rc, 1, self.log_text())
        self.assertEqual(er.status[first], "failed")
        self.assertNotIn(first, er.files)
        self.assertEqual(set(v for k, v in er.status.items() if k != first), {"ok"})
        self.assertIn("Failed to bind analysis outputs.", self.log_text())

    def patched(self, edit):
        real = vacask.render

        def render(*a, **kw):
            return edit(real(*a, **kw))
        return mock.patch.object(vacask, "render", render)

    def test_parse_error_in_the_deck(self):
        deck = self.deck("rc.scs", RC_DECK % "1m")
        with self.patched(lambda t: t.replace("control\n", "R9 (in 0) (\ncontrol\n", 1)):
            er, *_ = self.run_deck(deck)
        self.assertEqual((er.rc, er.status), (2, {}), self.log_text())
        self.assertIn("Error from spectre during circuit read-in.", self.log_text())
        self.assertIn("Parser syntax error", self.log_text())

    def test_failing_alter_after_an_analysis(self):
        deck = self.deck("rc.scs", RC_DECK % "1m")
        _nl, plan, _nl2, _sm = prepare(deck)
        second = steps_of(plan)[1].id
        with self.patched(lambda t: t.replace("  analysis %s " % second,
                                              '  alter instance("R1") bogus=3\n  analysis %s ' % second, 1)):
            er, *_ = self.run_deck(deck)
        self.assertEqual(er.rc, 2, self.log_text())
        self.assertEqual(er.status, {steps_of(plan)[0].id: "ok"})
        self.assertIn("Parameter 'bogus' not found.", self.log_text())

    def test_verilog_a_cache_with_openvaf(self):
        case = os.path.join(E2E, "08_include_ahdl", "deck.scs")
        er, *_ = self.run_deck(case)
        self.assertEqual(er.rc, 0, self.log_text())
        lib = os.path.join(self.tmp, "ahdl")
        files = sorted(os.listdir(lib))
        self.assertEqual([re.sub(r"_[0-9a-f]{40}\.osdi$", "", f) for f in files], ["vares", "vres"])
        mtimes = [os.path.getmtime(os.path.join(lib, f)) for f in files]
        er2, *_ = self.run_deck(case, run_dir=os.path.join(self.tmp, "run2"))
        self.assertEqual(er2.rc, 0)
        self.assertEqual(sorted(os.listdir(lib)), files)
        self.assertEqual([os.path.getmtime(os.path.join(lib, f)) for f in files], mtimes)


@needs_vacask
class TestVacaskSignals(RunnerCase):
    """§2.7 on the real engine: VACASK stops at once on the forwarded SIGTERM and leaves a rawfile
    that fix_points repairs (E40, E81)."""

    def signal_when_running(self, run_dir: str, step_id: str, actions, min_bytes: int = 200000):
        """Once the step's rawfile holds min_bytes: each action in turn, a signal number (sent to this
        process, where run() handles it), a callable, or a float (a pause in seconds).  Two signals
        sent back to back can reach Python's handler once (a pending signal is one flag), so a second
        one follows a pause."""
        path = os.path.join(run_dir, step_id + ".raw")

        def wait_and_act():
            end = time.time() + 60
            while time.time() < end:
                try:
                    if os.path.getsize(path) >= min_bytes:
                        break
                except OSError:
                    pass
                time.sleep(0.05)
            for a in actions:
                if callable(a):
                    a()
                elif isinstance(a, float):
                    time.sleep(a)
                else:
                    os.kill(os.getpid(), a)
        th = threading.Thread(target=wait_and_act, daemon=True)
        th.start()
        return th

    @staticmethod
    def to_engine(run_dir: str, sig: int):
        """An action: send sig to the VACASK running in run_dir (vamos_testlib.procs_under); for
        SIGSTOP, wait until it has stopped (a SIGTERM pending beside SIGSTOP is taken first)."""
        def act():
            pids = procs_under(run_dir)
            for pid in pids:
                os.kill(pid, sig)
            if sig == signal.SIGSTOP:
                end = time.time() + 10
                while time.time() < end and not all(_state(pid) in ("T", "t", "") for pid in pids):
                    time.sleep(0.01)
        return act

    def run_signalled(self, run_dir: str, step_id: str, actions, job, plan, nl2, sigmap, min_bytes: int = 200000):
        """run() with the actions of signal_when_running, the helper thread joined before the quiet
        handlers go (a late signal never reaches the next test); stop.Stop is returned, not raised."""
        th = self.signal_when_running(run_dir, step_id, actions, min_bytes)
        with QuietSignals():
            try:
                return RV.run(nl2, plan, sigmap, run_dir, job, self.log_for(job))
            except stop.Stop as e:
                return e
            finally:
                th.join(70)

    def long_tran(self, stop_: str = "3"):
        deck = self.deck("rc.scs", RC_DECK % stop_)
        _nl, plan, nl2, sigmap = prepare(deck)
        return deck, plan, nl2, sigmap, steps_of(plan)

    def test_sigterm_and_sigint(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(sig=sig):
                deck, plan, nl2, sigmap, (op1, tr, op2) = self.long_tran()
                run_dir = os.path.join(self.tmp, "run_%d" % sig)
                t0 = time.time()
                er = self.run_signalled(run_dir, tr.id, [sig], self.job(deck), plan, nl2, sigmap)
                self.assertLess(time.time() - t0, 50, "the run stopped well before the tran's end")
                self.assertEqual(er.rc, 3, self.log_text())
                self.assertEqual(er.status, {op1.id: "ok", tr.id: "interrupted"})
                plot = rawfile.read(er.files[tr.id])
                self.assertEqual(plot.declared_points, len(plot.points))     # fix_points (E40)
                self.assertGreater(len(plot.points), 1000)
                self.assertLess(plot.last_time(), 3.0)
                self.assertEqual(procs_under(run_dir), [])
                self.assertIn("analysis `tr' was interrupted at time = ", self.log_text())

    def test_kill_from_outside(self):
        for sig, rc in ((signal.SIGKILL, 3), (signal.SIGSEGV, 2)):
            with self.subTest(sig=sig):
                deck, plan, nl2, sigmap, (op1, tr, op2) = self.long_tran()
                run_dir = os.path.join(self.tmp, "run_%d" % sig)
                er = self.run_signalled(run_dir, tr.id, [self.to_engine(run_dir, sig)], self.job(deck), plan, nl2,
                                        sigmap)
                self.assertEqual(er.rc, rc, self.log_text())
                self.assertEqual(er.status[tr.id], "interrupted" if rc == 3 else "failed")
                self.assertNotIn(op2.id, er.status)

    def test_second_signal_raises_stop(self):
        """VACASK ends within milliseconds of the forwarded SIGTERM [E81]; stopped (SIGSTOP) first, it
        keeps that SIGTERM pending, so the second SIGINT comes while it still runs."""
        deck, plan, nl2, sigmap, (op1, tr, op2) = self.long_tran()
        run_dir = os.path.join(self.tmp, "run")
        got = self.run_signalled(run_dir, tr.id, [self.to_engine(run_dir, signal.SIGSTOP), signal.SIGINT, 0.5,
                                                 signal.SIGINT], self.job(deck), plan, nl2, sigmap)
        self.assertIsInstance(got, stop.Stop, self.log_text())
        self.assertEqual(got.signum, signal.SIGINT)
        self.assertEqual(procs_under(run_dir), [])

    def test_usr1_prints_a_status_line_and_the_run_goes_on(self):
        deck, plan, nl2, sigmap, (op1, tr, op2) = self.long_tran("0.05")
        run_dir = os.path.join(self.tmp, "run")
        er = self.run_signalled(run_dir, tr.id, [signal.SIGUSR1], self.job(deck), plan, nl2, sigmap,
                                min_bytes=100000)
        self.assertEqual(er.rc, 0, self.log_text())
        self.assertEqual(set(er.status.values()), {"ok"})
        self.assertRegex(self.log_text(), r"spectre status: analysis `tr' running, time = [0-9.]+ [mu]?s of 50 ms "
                                          r"\(\d+ %\), \d+ points written, elapsed = \d+ s\.")


if __name__ == "__main__":
    unittest.main()
