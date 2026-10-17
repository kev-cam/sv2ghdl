"""The spectre run, vamos/spectre/flow.run (docs/VAMOS_SPECTRE_DESIGN.md §1, §2.5-§2.7, §8; phase 2, F).

    cd tests/vamos && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_spectre_flow

Both legs (Cygwin Python 3.9, WSL), no engine: flow.run runs the real chain (spectre.parse, job.settings,
plan.build, signals.resolve) and the real outputs (results.collect, output/psf, nutmeg, statefile); the
engine runner is a fake installed as the module vamos.spectre.run_<engine> for the test (sys.modules), so
these tests never import the runners phase 2's V and X owners write.  The fakes write VACASK-layout
rawfiles synthesized from the plan (`synth_runner`: the sweep columns, the axis, the columns
vacask.names_for gives), or copy the real engine outputs of fixtures/spectre/psf/golden/engine/
{vacask,xyce}/ (`fixture_runner`, renaming the hand decks' columns to the plan's).  Every PSF file a run
writes is checked by psfcheck.

  TestHandOff           help, -V/-W, the scan-time stop; personalities.spectre.main hands the job over
  TestRunDirectory      where the run directory goes, when it is removed or kept (§1)
  TestResultsPath       -raw, options rawfile=, %C:r.raw, -outdir, the stale index (§1, §2.5)
  TestConversion        the results directory through psfcheck, values, noise masters, legacy names (§8)
  TestRealEngineOutput  the VACASK and Xyce fixture outputs converted through the flow
  TestOutsideCollect    save=nooutput, dc print=yes, write=/writefinal= state files, nutmeg (§8.5, §8.6)
  TestStatus            exit statuses 0-3 and the trailers (§2.7, §8.7)
  TestErrors            the exception mapping (§2.7): NoteError, ToolError, the engine choice, OSError,
                        an internal error and where its traceback goes
  TestInput             stdin, +log/=log, cpp, the duplicate option note (§14 q.54), the inventory
  TestSignals           (POSIX, a child python running flow.run) INT TERM HUP QUIT before an engine runs,
                        while a fake engine runs and during the conversion; USR1 and USR2; an ignored
                        signal stays ignored (§2.7)
  TestIntegration       (WSL, needs_vacask/needs_xyce) the real runners and engines through bin/vamos: §7.3's
                        example on both engines, SIGINT and SIGUSR1 during a long VACASK tran (released by
                        the integration stage)
"""

import contextlib
import glob
import io
import itertools
import math
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import types
import unittest
from typing import Dict, List, Optional, Sequence, Tuple

from vamos_testlib import LAUNCHER, ROOT, TempDir  # noqa: E402
from spectre_testlib import needs_cpp, needs_vacask, needs_xyce, spectre_fixture  # noqa: E402

import psfcheck  # noqa: E402
from vamos import VERSION, proc, tools  # noqa: E402
from vamos.netlist import rawfile, vacask, xyce  # noqa: E402
from vamos.output import statefile  # noqa: E402
from vamos.personalities import spectre as personality  # noqa: E402
from vamos.spectre import flow, results, stop  # noqa: E402
from vamos.spectre.results import EngineResult  # noqa: E402

TESTS = os.path.dirname(os.path.abspath(__file__))
ENGINE_DIR = spectre_fixture("psf", "golden", "engine")
TRAILER_FATAL = "spectre terminated prematurely due to fatal error.\n"
TRAILER_OK = re.compile(r"spectre completes with (\d+) errors, (\d+) warnings, and (\d+) notices\n\Z")
POSIX_SIGNALS = all(hasattr(signal, n) for n in ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT", "SIGUSR1",
                                                  "SIGUSR2", "SIGKILL"))
ENV_VARS = ("VAMOS_ANALOG", "VAMOS_BANNER", "VAMOS_VERBOSE", "VAMOS_SPECTRE_NAMES", "spectre_DEFAULTS",
            "SPECTRE_DEFAULTS", "CDS_VLOGA_INCLUDE", "VAMOS_CPP", "VAMOS_VACASK", "VAMOS_XYCE", "VAMOS_STACK",
            "VAMOS_ARGV0")

# -- decks --------------------------------------------------------------------------------------------

# §7.3's worked example (e2e 1): myop.dc, myac.ac, swp_tr.sweep, swp-00i_tr.tran.tran, logFile
RC = """// RC Low-Pass Filter
simulator lang=spectre
parameters rval=1k
V1 (in 0) vsource type=pulse val0=0 val1=1 delay=1m rise=1u fall=1u width=4m period=10m mag=1
X1 (in out) rcsec r=rval
subckt rcsec (a b)
  parameters r=1k
  R1 (a b) resistor r=r
  C1 (b 0) capacitor c=1u
ends rcsec
myop dc
myac ac start=1 stop=1M dec=10
swp sweep param=rval values=[1k 2k] {
  tr tran stop=5m
}
"""
RC_FILES = ["logFile", "myac.ac", "myop.dc", "swp-000_tr.tran.tran", "swp-001_tr.tran.tran", "swp_tr.sweep"]
RC_KEYS = ["myop-dc", "myac-ac", "swp_tr-sweep", "swp-000_tr-tran", "swp-001_tr-tran"]

# the circuit of fixtures/spectre/psf/golden/engine/vacask/nz.sim, in Spectre: a 1k/1k divider loaded by a
# 2k subckt resistor; op1.raw, tr1.raw and nz1.raw are its real VACASK outputs
DIV = """// divider noise and acxf
simulator lang=spectre
V1 (in 0) vsource dc=1 mag=1
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
X1 (out) sub
subckt sub (a)
  R3 (a 0) resistor r=2k
ends sub
save out V1:p
swp1 sweep dev=R2 param=r values=[1k 2k] {
  op1 dc
}
swp2 sweep dev=R2 param=r values=[1k 1k 2k] {
  tr1 tran stop=5m step=1m
}
nz1 (out 0) noise start=1 stop=100 dec=2 iprobe=V1
"""
# the circuit of fixtures/spectre/psf/golden/engine/xyce/{opstep,nz0}.cir: a 1k/1k divider
XY = """// divider
simulator lang=spectre
V1 (in 0) vsource dc=1 mag=1
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
save out V1:p
swp sweep dev=R2 param=r values=[1k 2k] {
  op1 dc
}
nz0 (out 0) noise start=1 stop=100 dec=2 iprobe=V1
"""
# two operating points and a tran: small and quick
TWO = """// two analyses
simulator lang=spectre
V1 (in 0) vsource dc=1
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
op1 dc
op2 dc
tr1 tran stop=1m
"""


# =====================================================================================================
# Fake engine output
# =====================================================================================================

def plan_steps(plan) -> list:
    return [a.step for a in plan.actions if a.op == "analysis" and a.step is not None]


def write_rawfile(path: str, variables: Sequence[str], rows: Sequence[Sequence[object]], cplx: bool = False,
                  plotname: str = "Analysis", declared: bool = True) -> None:
    """A VACASK-style binary rawfile: one plot, every value a double (two for a complex plot)."""
    with open(path, "wb") as fh:
        count = ("%d" % len(rows)).ljust(18) if declared else " " * 18
        fh.write(("Title: synthetic\nDate: Fri Oct 16 12:00:00 2026\nPlotname: %s\nFlags: %s\nNo. Variables: %d\n"
                  "No. Points: %s\nVariables:\n" % (plotname, "complex" if cplx else "real", len(variables),
                                                    count)).encode("latin-1"))
        for k, n in enumerate(variables):
            fh.write(("\t%d\t%s\tnotype\n" % (k, n)).encode("latin-1"))
        fh.write(b"Binary:\n")
        for row in rows:
            if cplx:
                flat: List[float] = []
                for v in row:
                    c = complex(v)
                    flat.extend((c.real, c.imag))
                fh.write(struct.pack("<%dd" % len(flat), *flat))
            else:
                fh.write(struct.pack("<%dd" % len(row), *[float(v) for v in row]))


NOISE_TOTAL = 2e-18          # each n(<inst>) column and its one contribution, V^2/Hz
ONOISE = 4e-18
POWER_GAIN = 0.25


def synth_value(kind: str, col: str, j: int, row: int) -> object:
    """The synthetic value of engine column `col` (the j-th signal column) at row `row`."""
    if kind == "noise":
        if col == "onoise":
            return ONOISE
        if col == "gain":
            return POWER_GAIN
        return NOISE_TOTAL
    v = 0.25 + 0.01 * j + 1e-4 * row
    return complex(v, -v / 10.0) if kind in ("ac", "xf") else v


def vacask_layout(st, names: Dict[tuple, str], points: int = 11) -> Tuple[List[str], List[List[object]]]:
    """§7.1's VACASK layout of one step: the context sweep columns (SweepLevel.id), a parameter sweep's
    own column, then `time` / `frequency`, then the saved columns; one plot, the points concatenated."""
    lead, grids = [], []
    for lv in st.context:
        lead.append(lv.id)
        grids.append(results.grid_values(lv.spec))
    own = st.own
    if own is not None and own.target[0] != "freq":
        lead.append(own.id)
        grids.append(results.grid_values(own.spec))
    if st.kind == "tran":
        x, xs = "time", [float(st.args["stop"]) * i / (points - 1) for i in range(points)]
    elif st.kind in ("ac", "noise", "xf"):
        x = "frequency"
        xs = (results.grid_values(own.spec) if own is not None and own.target[0] == "freq"
              else [float(st.args.get("freq", 1.0))])
    else:
        x, xs = "", [None]
    sig: List[str] = []
    for col in names.values():
        if col not in sig:
            sig.append(col)
    if st.kind == "noise":
        sig += [c[:-1] + ",thermal)" for c in list(sig) if c.startswith("n(") and c.endswith(")")]
    variables = lead + ([x] if x else []) + sig
    rows: List[List[object]] = []
    for point in (itertools.product(*grids) if grids else [()]):
        for xv in xs:
            vals = [synth_value(st.kind, c, j, len(rows)) for j, c in enumerate(sig)]
            rows.append(list(point) + ([xv] if x else []) + vals)
    return variables, rows


def synth_runner(rc: int = 0, status: Optional[Dict[str, str]] = None, failed_points: Optional[Dict[str, int]] = None,
                 missing: Sequence[str] = (), calls: Optional[list] = None, truncate: Optional[Dict[str, int]] = None,
                 hook=None, absent: Sequence[str] = ()):
    """A fake run_vacask.run: every step's VACASK-layout rawfile in the run directory, named relative
    (the flow resolves EngineResult.files against the run directory).  status/failed_points/rc as given;
    `missing` steps get no file and status failed; `absent` steps no file and no status (the engine
    never reached them, stop.py); `truncate` keeps that many rows of a step's file."""
    def run(nl, plan, sigmap, run_dir, job, log):
        if calls is not None:
            calls.append(types.SimpleNamespace(nl=nl, plan=plan, sigmap=sigmap, run_dir=run_dir, job=job, log=log,
                                               listing=sorted(os.listdir(run_dir))))
        names = vacask.names_for(nl, plan, sigmap)
        er = EngineResult(rc=rc, names=names)
        for k, st in enumerate(plan_steps(plan)):
            if st.id in absent:
                continue
            if st.id in missing:
                er.status[st.id] = "failed"
                continue
            variables, rows = vacask_layout(st, names.get(st.id, {}))
            if truncate and st.id in truncate:
                rows = rows[:truncate[st.id]]
            write_rawfile(os.path.join(run_dir, st.id + ".raw"), variables, rows, cplx=st.kind in ("ac", "xf"),
                          declared=not (truncate and st.id in truncate))
            er.files[st.id] = st.id + ".raw"
            er.status[st.id] = (status or {}).get(st.id, "ok")
        er.failed_points.update(failed_points or {})
        if hook is not None:
            hook(er, run_dir, plan)
        return er
    return run


def copy_raw(src: str, dst: str, renames: Dict[str, str]) -> None:
    """Copy a rawfile, renaming variables in its header (the binary data is untouched)."""
    with open(src, "rb") as fh:
        data = fh.read()
    out = []
    pos = 0
    pat = re.compile(rb"^(\t\d+\t)([^\t\n]+)(\t[^\n]*)$", re.M)
    for m in pat.finditer(data):
        start = data.rfind(b"Binary:\n", 0, m.start())
        if start != -1 and data.rfind(b"Variables:", 0, m.start()) < start:
            continue                                    # inside the data of an earlier plot
        name = m.group(2).decode("latin-1")
        out.append(data[pos:m.start(2)])
        out.append(renames.get(name, name).encode("latin-1"))
        pos = m.end(2)
    out.append(data[pos:])
    with open(dst, "wb") as fh:
        fh.write(b"".join(out))


def fixture_runner(files: Dict[str, Tuple[str, Dict[str, str]]], engine: str = "vacask", rc: int = 0,
                   calls: Optional[list] = None):
    """A fake runner over real engine outputs: step id -> (fixture file under psf/golden/engine/<engine>/,
    column renames); .prn tables are copied as they are."""
    def run(nl, plan, sigmap, run_dir, job, log):
        if calls is not None:
            calls.append(types.SimpleNamespace(nl=nl, plan=plan, sigmap=sigmap, run_dir=run_dir))
        mod = vacask if engine == "vacask" else xyce
        er = EngineResult(rc=rc, names=mod.names_for(nl, plan, sigmap))
        for st in plan_steps(plan):
            src, renames = files[st.id]
            path = os.path.join(ENGINE_DIR, engine, src)
            dst = os.path.join(run_dir, st.id + os.path.splitext(src)[1])
            if src.endswith(".prn"):
                shutil.copyfile(path, dst)
            else:
                copy_raw(path, dst, renames)
            er.files[st.id] = dst
            er.status[st.id] = "ok"
        return er
    return run


# =====================================================================================================
# The test base
# =====================================================================================================

@contextlib.contextmanager
def fake_runner(engine: str, fn):
    """Install fn as vamos.spectre.run_<engine>.run for the block (a stub module in sys.modules, so the
    real runner, phase 2's V or X owner's, is never imported here)."""
    name = "vamos.spectre.run_" + engine
    saved = sys.modules.get(name)
    mod = types.ModuleType(name)
    mod.run = fn                                         # type: ignore[attr-defined]
    sys.modules[name] = mod
    try:
        yield mod
    finally:
        if saved is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = saved


def never_called(*a, **k):
    raise AssertionError("the engine runner must not be called")


class FlowCase(TempDir):
    """A scratch directory that is also the cwd, a clean environment, tools.invoked = spectre."""

    def setUp(self):
        super().setUp()
        self._cwd = os.getcwd()
        os.chdir(self.tmp)
        for v in ENV_VARS:
            os.environ.pop(v, None)
        self._tools = (tools.current, tools.invoked)
        tools.invoked = "spectre"

    def tearDown(self):
        os.chdir(self._cwd)
        tools.current, tools.invoked = self._tools
        super().tearDown()

    def deck(self, text: str, name: str = "rc.scs") -> str:
        return self.write(name, text)

    def use(self, fn, engine: str = "vacask") -> None:
        cm = fake_runner(engine, fn)
        cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)

    def spectre(self, argv: Sequence[str], opts: Optional[dict] = None, stdin: Optional[str] = None
                ) -> Tuple[int, str, str]:
        """build_job + flow.run in-process -> (rc, stdout, stderr); stdin is /dev/null unless given."""
        job = personality.build_job(list(argv), os.environ, self.tmp, "spectre")
        personality.note_vamos_options(job, dict(opts or {}))
        out, err = io.StringIO(), io.StringIO()
        saved = sys.stdin
        sys.stdin = io.StringIO(stdin) if stdin is not None else open(os.devnull)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = flow.run(job, dict(opts or {}))
        finally:
            if stdin is None:
                sys.stdin.close()
            sys.stdin = saved
        return rc, out.getvalue(), err.getvalue()

    def check_results(self, d: str, escchars: bool = False) -> psfcheck.LogFile:
        """psfcheck on every data file of a results directory and on its logFile."""
        for f in sorted(os.listdir(d)):
            if f != "logFile":
                psfcheck.check(os.path.join(d, f), escchars=escchars)
        return psfcheck.check_logfile(os.path.join(d, "logFile"), escchars=escchars)

    def run_dirs(self, where: Optional[str] = None) -> List[str]:
        return sorted(glob.glob(os.path.join(where or self.tmp, "*.run.*")))

    def assertTrailerOk(self, out: str) -> Tuple[int, int, int]:
        m = TRAILER_OK.search(out)
        self.assertIsNotNone(m, out[-600:])
        return int(m.group(1)), int(m.group(2)), int(m.group(3))

    def assertFatal(self, out: str) -> None:
        self.assertTrue(out.endswith(TRAILER_FATAL), out[-600:])


# =====================================================================================================
# 1b: the hand-off, help, -V, the scan-time stop
# =====================================================================================================

class TestHandOff(FlowCase):
    def test_help_prints_and_simulates_nothing(self):
        self.use(never_called)
        rc, out, err = self.spectre(["-h"])
        self.assertEqual((rc, err), (0, ""))
        self.assertTrue(out.startswith("usage: spectre [options] [netlist]"), out[:80])
        self.assertEqual(os.listdir(self.tmp), [])
        rc, out, _ = self.spectre(["-h", "resistor"])
        self.assertEqual(rc, 0)
        self.assertIn("resistor (Spectre master", out)

    def test_help_beside_a_scan_time_error_reports_the_error(self):
        self.use(never_called)
        rc, out, err = self.spectre(["-h", "+top", "s"])
        self.assertEqual((rc, err), (2, ""))
        self.assertIn("+top s:", out)
        self.assertFatal(out)

    def test_version_alone_prints_only_its_line(self):
        self.use(never_called)
        for opt in ("-V", "-W"):
            rc, out, err = self.spectre([opt])
            self.assertEqual((rc, out, err), (0, "vamos %s (spectre personality)\n" % VERSION, ""))
        rc, out, err = self.spectre(["-V", "=log", "v.log"])
        self.assertEqual((rc, out, err), (0, "", ""))
        self.assertEqual(self.read("v.log"), "vamos %s (spectre personality)\n" % VERSION)

    def test_version_with_a_netlist_runs_on(self):
        self.deck(TWO)
        self.use(synth_runner())
        rc, out, err = self.spectre(["-V", "rc.scs"])
        self.assertEqual((rc, err), (0, ""))
        lines = out.splitlines()
        self.assertEqual(lines[0], "vamos %s (spectre personality)" % VERSION)
        self.assertEqual(lines[1], "vamos %s (spectre personality) - tools used:" % VERSION)    # the header
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "rc.raw", "logFile")))

    def test_the_provenance_header_names_the_engine_and_cpp(self):
        self.deck(TWO)
        self.use(synth_runner())
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 0, out)
        head = out.split("\n\n", 1)[0]
        self.assertIn(" - tools used:", head)
        self.assertRegex(head, r"\n  vamos +\S+")
        self.assertRegex(head, r"\n  VACASK +")
        self.assertNotRegex(head, r"\n  cpp ")
        self.use(synth_runner(), engine="xyce")
        rc, out, _ = self.spectre(["rc.scs"], {"analog": "xyce"})
        self.assertRegex(out.split("\n\n", 1)[0], r"\n  Xyce +")

    def test_scan_time_stop_is_exit_2_before_any_run_directory(self):
        self.deck(TWO)
        self.use(never_called)
        rc, out, err = self.spectre(["+top", "s", "rc.scs"])
        self.assertEqual((rc, err), (2, ""))
        self.assertIn("Error from spectre during command-line processing.\n    +top s:", out)
        self.assertFatal(out)
        self.assertEqual(os.listdir(self.tmp), ["rc.scs"])

    def test_a_run_with_unread_defaults_still_simulates_and_exits_0(self):
        """test_spectre_cli's phase-2 skip of the same name, through the flow (§2.2)."""
        self.deck(TWO)
        self.use(synth_runner())
        os.environ["SPECTRE_DEFAULTS"] = "-help +interactive -note +mpssession=x"
        rc, out, err = self.spectre(["rc.scs"])
        self.assertEqual((rc, err), (0, ""), out)
        self.assertIn("Spectre does not read -help from SPECTRE_DEFAULTS; ignored", out)
        self.assertIn("Spectre does not read +interactive from SPECTRE_DEFAULTS; ignored", out)
        self.assertTrailerOk(out)
        self.check_results(os.path.join(self.tmp, "rc.raw"))

    def test_main_hands_the_job_to_flow(self):
        self.deck(TWO)
        self.use(synth_runner())
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = personality.main(["rc.scs"], {})
        self.assertEqual((rc, err.getvalue()), (0, ""), out.getvalue())
        self.assertTrailerOk(out.getvalue())
        self.assertNotIn("not implemented yet", out.getvalue())
        self.check_results(os.path.join(self.tmp, "rc.raw"))


# =====================================================================================================
# 2: the run directory
# =====================================================================================================

class TestRunDirectory(FlowCase):
    def test_beside_the_results_and_removed_after_status_0(self):
        self.deck(TWO)
        calls: list = []
        self.use(synth_runner(calls=calls))
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 0, out)
        d = calls[0].run_dir
        self.assertEqual(os.path.dirname(d), self.tmp)
        self.assertTrue(os.path.basename(d).startswith("rc.run."), d)
        self.assertFalse(os.path.exists(d))
        self.assertNotIn("run directory kept", out)
        self.assertEqual(self.run_dirs(), [])

    def test_kept_after_status_1_2_and_3(self):
        self.deck(TWO)
        for rc_want, runner in ((1, synth_runner(rc=1, status={"vamos_a2": "failed"})),
                                (2, synth_runner(rc=2)),
                                (3, synth_runner(rc=3, status={"vamos_a3": "interrupted"}))):
            with self.subTest(status=rc_want):
                calls: list = []
                cm = fake_runner("vacask", lambda *a, _r=runner, _c=calls: (_c.append(a[3]), _r(*a))[1])
                with cm:
                    rc, out, _ = self.spectre(["rc.scs"])
                self.assertEqual(rc, rc_want, out)
                d = calls[0]
                self.assertTrue(os.path.isdir(d))
                self.assertIn("run directory kept: %s\n" % d, out)
                self.assertLess(out.index("run directory kept"), out.rindex("spectre "))     # before the trailer
                shutil.rmtree(d)

    def test_vamos_keep_keeps_it_after_status_0(self):
        self.deck(TWO)
        calls: list = []
        self.use(synth_runner(calls=calls))
        rc, out, _ = self.spectre(["rc.scs"], {"keep": True})
        self.assertEqual(rc, 0, out)
        d = calls[0].run_dir
        self.assertTrue(os.path.isfile(os.path.join(d, "vamos_a1.raw")))
        self.assertIn("run directory kept: %s" % d, out)

    def test_an_empty_run_directory_is_always_removed(self):
        self.deck(TWO)
        self.use(lambda nl, plan, sigmap, run_dir, job, log: EngineResult(rc=2))
        rc, out, _ = self.spectre(["rc.scs"], {"keep": True})
        self.assertEqual(rc, 2, out)
        self.assertEqual(self.run_dirs(), [])
        self.assertNotIn("run directory kept", out)
        rc, out, _ = self.spectre(["nosuch.scs"])                    # the input fails: nothing was written
        self.assertEqual(rc, 2, out)
        self.assertIn("nosuch.scs: netlist nosuch.scs not found", out)
        self.assertEqual(self.run_dirs(), [])

    def test_options_rawfile_moves_the_results_not_the_run_directory(self):
        os.makedirs(os.path.join(self.tmp, "sub"))
        self.deck(TWO.replace("op1 dc\n", "o1 options rawfile=\"elsewhere/r.raw\"\nop1 dc\n"))
        calls: list = []
        self.use(synth_runner(calls=calls))
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(os.path.dirname(calls[0].run_dir), self.tmp)
        self.check_results(os.path.join(self.tmp, "elsewhere", "r.raw"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "rc.raw")))
        rc, out, _ = self.spectre(["rc.scs", "-raw", "sub/out.raw"])     # -raw: the run directory follows it
        self.assertEqual(rc, 0, out)
        self.assertEqual(os.path.dirname(calls[1].run_dir), os.path.join(self.tmp, "sub"))
        self.check_results(os.path.join(self.tmp, "sub", "out.raw"))


# =====================================================================================================
# 2.5: where the results go
# =====================================================================================================

class TestResultsPath(FlowCase):
    def run_ok(self, argv, opts=None):
        rc, out, err = self.spectre(argv, opts)
        self.assertEqual((rc, err), (0, ""), out)
        return out

    def test_default_is_netlist_root_raw(self):
        self.deck(TWO)
        os.makedirs(os.path.join(self.tmp, "sub"))
        self.deck(TWO, "sub/x.scs")
        self.use(synth_runner())
        self.run_ok(["rc.scs"])
        self.check_results(os.path.join(self.tmp, "rc.raw"))
        self.run_ok(["sub/x.scs"])                                       # %C:r keeps the directory
        self.check_results(os.path.join(self.tmp, "sub", "x.raw"))

    def test_raw_and_its_percent_codes(self):
        self.deck(TWO)
        self.use(synth_runner())
        self.run_ok(["rc.scs", "-raw", "%C:r.psf"])
        self.check_results(os.path.join(self.tmp, "rc.psf"))
        self.run_ok(["rc.scs", "-r", "out"])
        self.check_results(os.path.join(self.tmp, "out"))

    def test_outdir_moves_only_default_names_without_a_slash(self):
        self.deck(TWO)
        os.makedirs(os.path.join(self.tmp, "sub"))
        self.deck(TWO, "sub/x.scs")
        self.use(synth_runner())
        self.run_ok(["-outdir", "o", "rc.scs"])
        self.check_results(os.path.join(self.tmp, "o", "rc.raw"))
        self.run_ok(["-outdir", "o", "sub/x.scs"])                      # the literal reading (§14 q.2)
        self.check_results(os.path.join(self.tmp, "sub", "x.raw"))
        self.run_ok(["-outdir", "o", "-raw", "r", "rc.scs"])            # an explicit -raw is not moved
        self.check_results(os.path.join(self.tmp, "r"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "o", "r")))

    def test_options_rawfile_loses_to_raw(self):
        self.deck(TWO.replace("op1 dc\n", "o1 options rawfile=\"opt.raw\"\nop1 dc\n"))
        self.use(synth_runner())
        self.run_ok(["rc.scs"])
        self.check_results(os.path.join(self.tmp, "opt.raw"))
        self.run_ok(["rc.scs", "-raw", "argv.raw"])
        self.check_results(os.path.join(self.tmp, "argv.raw"))

    def test_the_stale_logfile_is_removed_before_the_input_is_read(self):
        self.deck("// broken\nsimulator lang=spectre\nR1 (a 0 resistor r=1k\n")
        self.write("rc.raw/logFile", "stale")
        self.write("rc.raw/mine.txt", "kept")
        self.use(never_called)
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 2, out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "rc.raw", "logFile")))
        self.assertEqual(self.read("rc.raw/mine.txt"), "kept")           # vamos removes only its index

    def test_the_stale_nutmeg_file_is_removed(self):
        self.deck("// broken\nsimulator lang=spectre\nR1 (a 0 resistor r=1k\n")
        self.write("rc.raw", "stale")
        self.use(never_called)
        rc, out, _ = self.spectre(["rc.scs", "-format", "nutbin"])
        self.assertEqual(rc, 2, out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "rc.raw")))
        os.makedirs(os.path.join(self.tmp, "nd"))
        self.write("nd/rc.raw", "stale")                                 # a directory: <dir>/%C:r:t.raw (§8.5)
        rc, out, _ = self.spectre(["rc.scs", "-format", "nutascii", "-raw", "nd"])
        self.assertEqual(rc, 2, out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "nd", "rc.raw")))


# =====================================================================================================
# 12-13: the results directory
# =====================================================================================================

class TestConversion(FlowCase):
    def test_the_worked_example_results_directory(self):
        self.deck(RC)
        self.use(synth_runner())
        rc, out, err = self.spectre(["rc.scs"])
        self.assertEqual((rc, err), (0, ""), out)
        d = os.path.join(self.tmp, "rc.raw")
        self.assertEqual(sorted(os.listdir(d)), RC_FILES)
        log = self.check_results(d)
        self.assertEqual(log.keys(), RC_KEYS)
        op = psfcheck.check(os.path.join(d, "myop.dc"))
        self.assertEqual(op.header["design"], "// RC Low-Pass Filter")
        self.assertEqual(op.header["version"], VERSION)
        self.assertEqual(log.header["design"], "// RC Low-Pass Filter")
        self.assertIn("Simulating `rc.scs' on ", out)
        # the analysis banners and times are the runner's, printed while the engine runs (§8.7)
        self.assertNotIn("DC Analysis `myop'", out)
        self.assertNotIn("Total time required", out)

    def test_values_are_the_engine_values(self):
        self.deck(RC)
        self.use(synth_runner())
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 0, out)
        d = os.path.join(self.tmp, "rc.raw")
        op = psfcheck.check(os.path.join(d, "myop.dc"))
        for j, name in enumerate(op.names()):
            self.assertAlmostEqual(op.value(name), 0.25 + 0.01 * j, places=12)
        tr = psfcheck.check(os.path.join(d, "swp-001_tr.tran.tran"))
        self.assertEqual(len(tr.sweep_values()), 11)
        self.assertAlmostEqual(tr.sweep_values()[-1], 5e-3, places=15)
        ac = psfcheck.check(os.path.join(d, "myac.ac"))
        self.assertEqual(len(ac.sweep_values()), 61)                     # 1 Hz .. 1 MHz, dec=10 (§7.3)
        v = ac.column(ac.names()[0])[0]
        self.assertAlmostEqual(complex(v), complex(0.25, -0.025), places=12)

    def test_noise_structs_name_their_master(self):
        self.deck(DIV.replace("swp1 sweep", "model rref resistor\nR4 (out 0) rref r=1M\nswp1 sweep"))
        self.use(synth_runner())
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 0, out)
        nz = psfcheck.check(os.path.join(self.tmp, "rc.raw", "nz1.noise"))
        self.assertEqual(nz.types["resistor"].props.get("master"), "resistor")
        self.assertEqual(nz.types["rref"].props.get("master"), "resistor")      # §8.4: the model, its master
        self.assertEqual([m for m, _t, _p in nz.types["resistor"].members], ["rn", "total"])
        self.assertIn("X1.R3", nz.names())
        self.assertAlmostEqual(nz.column("out")[0], math.sqrt(ONOISE), places=20)

    def test_legacy_names(self):
        self.deck(RC)
        self.use(synth_runner())
        rc, out, _ = self.spectre(["rc.scs"], {"psf_names": "legacy"})
        self.assertEqual(rc, 0, out)
        d = os.path.join(self.tmp, "rc.raw")
        self.assertEqual(sorted(os.listdir(d)), ["logFile", "myac.ac", "myop.dc", "swp_000_tr.tran", "swp_001_tr.tran"])
        log = self.check_results(d)
        self.assertEqual(log.keys(), ["myop-dc", "myac-ac", "swp_000_tr-tran", "swp_001_tr-tran"])

    def test_escchars_escapes_the_names(self):
        self.deck(TWO.replace("R2 (out 0)", "R2 (net\\<3\\> 0)").replace("R1 (in out)", "R1 (in net\\<3\\>)"))
        self.use(synth_runner())
        rc, out, _ = self.spectre(["rc.scs", "+escchars"])
        self.assertEqual(rc, 0, out)
        d = os.path.join(self.tmp, "rc.raw")
        self.check_results(d, escchars=True)
        with open(os.path.join(d, "op1.dc"), encoding="latin-1") as fh:
            self.assertIn('"net\\<3\\>" "V"', fh.read())


# =====================================================================================================
# Real engine outputs through the flow
# =====================================================================================================

VACASK_RENAMES = {"v1:flow(br)": "V1:flow(br)", "n(r1)": "n(R1)", "n(r1,thermal)": "n(R1,thermal)",
                  "n(r2)": "n(R2)", "n(r2,thermal)": "n(R2,thermal)", "n(x1:r3)": "n(X1:R3)",
                  "n(x1:r3,thermal)": "n(X1:R3,thermal)"}


class TestRealEngineOutput(FlowCase):
    def test_vacask_outputs(self):
        self.deck(DIV)
        self.use(fixture_runner({"vamos_a1": ("op1.raw", dict(VACASK_RENAMES, w1="vamos_w1")),
                                 "vamos_a2": ("tr1.raw", dict(VACASK_RENAMES, w2="vamos_w2")),
                                 "vamos_a3": ("nz1.raw", VACASK_RENAMES)}))
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 0, out)
        d = os.path.join(self.tmp, "rc.raw")
        log = self.check_results(d)
        self.assertEqual(log.keys(), ["swp1_op1-sweep", "swp1-000_op1-dc", "swp1-001_op1-dc", "swp2_tr1-sweep",
                                      "swp2-000_tr1-tran", "swp2-001_tr1-tran", "swp2-002_tr1-tran", "nz1-noise"])
        # out = R2||R3 / (R1 + R2||R3): 0.4 V at R2 = 1k, 0.5 V at 2k; V1:p = -1 V / (R1 + R2||R3)
        for k, (vout, i1) in enumerate(((0.4, -1 / 1666.6666666666667), (0.5, -1 / 2000.0))):
            op = psfcheck.check(os.path.join(d, "swp1-%03d_op1.dc" % k))
            self.assertAlmostEqual(op.value("out"), vout, places=9)
            self.assertAlmostEqual(op.value("V1:p"), i1, places=12)
        tr = psfcheck.check(os.path.join(d, "swp2-002_tr1.tran.tran"))
        self.assertAlmostEqual(tr.sweep_values()[-1], 5e-3, places=12)
        self.assertAlmostEqual(tr.column("out")[-1], 0.5, places=9)
        nz = psfcheck.check(os.path.join(d, "nz1.noise"))
        raw = rawfile.read_all(os.path.join(ENGINE_DIR, "vacask", "nz1.raw"))[0]
        self.assertAlmostEqual(nz.column("out")[0], math.sqrt(raw.points[0][raw.exact("onoise")].real), places=20)
        self.assertEqual([m for m, _t, _p in nz.types["resistor"].members], ["rn", "total"])

    def test_xyce_outputs(self):
        self.deck(XY)
        self.use(fixture_runner({"vamos_a1": ("opstep.raw", {}), "vamos_a2": ("nz0.prn", {})}, engine="xyce"),
                 engine="xyce")
        rc, out, _ = self.spectre(["rc.scs"], {"analog": "xyce"})
        self.assertEqual(rc, 0, out)
        d = os.path.join(self.tmp, "rc.raw")
        self.check_results(d)
        for k, vout in enumerate((0.5, 2.0 / 3.0)):
            op = psfcheck.check(os.path.join(d, "swp-%03d_op1.dc" % k))
            self.assertAlmostEqual(op.value("out"), vout, places=6)
        nz = psfcheck.check(os.path.join(d, "nz0.noise"))
        self.assertAlmostEqual(nz.column("out")[0], 2.879e-9, delta=0.001e-9)       # §8.4, E8, E12
        self.assertEqual([m for m, _t, _p in nz.types["resistor"].members], ["total"])
        self.assertIn("carry only `total' (DNO)", out)

    def test_noise_gain_and_in(self):
        """§8.4: gain = sqrt(ONOISE/INOISE) = 0.5 and in = sqrt(INOISE) = out / gain on Xyce for the 1k/1k
        divider.  results.collect picks the formula by the engine (phase 2's integration): the name
        maps send ('gain',) and ('inoise',) to one column on each engine (VACASK `gain`, Xyce INOISE)."""
        self.deck(XY)
        self.use(fixture_runner({"vamos_a1": ("opstep.raw", {}), "vamos_a2": ("nz0.prn", {})}, engine="xyce"),
                 engine="xyce")
        rc, out, _ = self.spectre(["rc.scs"], {"analog": "xyce"})
        self.assertEqual(rc, 0, out)
        nz = psfcheck.check(os.path.join(self.tmp, "rc.raw", "nz0.noise"))
        self.assertAlmostEqual(nz.column("gain")[0], 0.5, places=4)
        self.assertAlmostEqual(nz.column("in")[0], nz.column("out")[0] / 0.5, delta=1e-12)


# =====================================================================================================
# What collect leaves to the flow (S4's seam): nooutput, print=yes, state files; nutmeg
# =====================================================================================================

class TestOutsideCollect(FlowCase):
    def test_save_nooutput_writes_no_data_file(self):
        self.deck(TWO.replace("op1 dc\n", "op1 dc save=nooutput\n"))
        self.use(synth_runner())
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 0, out)
        d = os.path.join(self.tmp, "rc.raw")
        self.assertEqual(sorted(os.listdir(d)), ["logFile", "op2.dc", "tr1.tran.tran"])
        self.assertEqual(self.check_results(d).keys(), ["op2-dc", "tr1-tran"])
        self.assertIn("op1: save=nooutput: no data file is written", out)
        self.deck(TWO.replace("op1 dc\n", "o1 options save=nooutput\nop1 dc\n"))
        shutil.rmtree(d)
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(sorted(os.listdir(d)), ["logFile"])
        self.assertEqual(self.check_results(d).keys(), [])

    def test_dc_print_yes_puts_the_node_voltages_in_the_log(self):
        self.deck(TWO.replace("op1 dc\n", "op1 dc print=yes\n"))
        self.use(synth_runner())
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 0, out)
        block = out.split("Node voltages of `op1' (print=yes):\n", 1)[1].split("\n")
        self.assertEqual(block[:2], ["    in = 250 mV", "    out = 260 mV"])
        self.assertEqual(out.count("Node voltages of"), 1)

    def test_write_and_writefinal_state_files(self):
        self.deck(TWO.replace("op1 dc\n", "op1 dc write=\"op1.ic\"\n")
                  .replace("tr1 tran stop=1m\n", "tr1 tran stop=1m write=\"t0.ic\" writefinal=\"%C:r.fc\"\n"))
        self.use(synth_runner())
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 0, out)
        lines = self.read("op1.ic").splitlines()
        self.assertEqual(lines[0], "# State file generated by Spectre from circuit file 'rc.scs'")
        self.assertRegex(lines[1], r"^# during 'op1' at \d{1,2}:\d\d:\d\d [AP]M, [a-z]{3} \d{1,2}, \d{4}\.$")
        self.assertEqual(lines[2:], ["in 0.25", "out 0.26", "V1:p 0.27"])             # nets, then V1:p
        first = self.read("t0.ic").splitlines()[2:]
        final = self.read("rc.fc").splitlines()[2:]
        self.assertEqual(first, ["in 0.25", "out 0.26", "V1:p 0.27"])
        self.assertEqual(final, ["in 0.251", "out 0.261", "V1:p 0.271"])           # the last of 11 points
        nodes, currents = statefile.read(os.path.join(self.tmp, "rc.fc"))
        self.assertEqual((sorted(nodes), sorted(currents)), (["in", "out"], ["V1:p"]))

    def test_state_files_follow_outdir_without_a_slash(self):
        self.deck(TWO.replace("op1 dc\n", "op1 dc write=\"op1.ic\"\n").replace("op2 dc\n", "op2 dc write=\"s/op2.ic\"\n"))
        os.makedirs(os.path.join(self.tmp, "s"))
        self.use(synth_runner())
        rc, out, _ = self.spectre(["rc.scs", "-outdir", "o"])
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "o", "op1.ic")))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "s", "op2.ic")))

    def test_nutmeg_formats(self):
        self.deck(RC)
        self.use(synth_runner())
        for fmt, binary in (("nutascii", False), ("nutbin", True)):
            with self.subTest(fmt=fmt):
                rc, out, _ = self.spectre(["rc.scs", "-format", fmt])
                self.assertEqual(rc, 0, out)
                path = os.path.join(self.tmp, "rc.raw")
                self.assertTrue(os.path.isfile(path))
                plots = rawfile.read_all(path)
                self.assertEqual([p.plotname.split("`")[1].split("'")[0] for p in plots],
                                 ["myop", "myac", "swp-000_tr", "swp-001_tr"])
                self.assertEqual(plots[0].title, "// RC Low-Pass Filter")
                self.assertEqual(len(plots[1].points), 61)
                os.remove(path)
        os.makedirs(os.path.join(self.tmp, "nd"))
        rc, out, _ = self.spectre(["rc.scs", "-format", "nutbin", "-raw", "nd"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(os.listdir(os.path.join(self.tmp, "nd")), ["rc.raw"])
        self.assertIn("is a directory: the nutbin results are written to", out)


# =====================================================================================================
# 2.7: exit statuses and trailers
# =====================================================================================================

class TestStatus(FlowCase):
    def test_status_0_completes_without_a_period(self):
        self.deck(TWO)
        self.use(synth_runner())
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 0, out)
        self.assertTrailerOk(out)
        self.assertIn("Aggregate audit (", out)

    def test_status_1_a_failed_analysis_among_good_ones(self):
        self.deck(TWO)
        self.use(synth_runner(rc=1, status={"vamos_a2": "failed"}, missing=()))
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 1, out)
        d = os.path.join(self.tmp, "rc.raw")
        self.assertEqual(self.check_results(d).keys(), ["op1-dc", "tr1-tran"])
        self.assertFalse(os.path.exists(os.path.join(d, "op2.dc")))
        errors, _warnings, _notices = self.assertTrailerOk(out)            # status 1 completes (§8.7)
        self.assertEqual(errors, 0)                  # the engine's error is the runner's message, not the flow's

    def test_status_1_from_a_missing_engine_output(self):
        self.deck(TWO)
        self.use(synth_runner(hook=lambda er, d, plan: os.remove(os.path.join(d, "vamos_a3.raw"))))
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 1, out)
        self.assertIn("the engine left no output file for analysis `tr1'", out)
        self.assertEqual(self.check_results(os.path.join(self.tmp, "rc.raw")).keys(), ["op1-dc", "op2-dc"])

    def test_status_1_a_failed_sweep_point(self):
        self.deck(RC)
        self.use(synth_runner(rc=1, status={"vamos_a3": "failed"}, failed_points={"vamos_a3": 2}))
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 1, out)
        d = os.path.join(self.tmp, "rc.raw")
        log = self.check_results(d)
        self.assertEqual(log.keys(), ["myop-dc", "myac-ac", "swp_tr-sweep", "swp-000_tr-tran"])
        self.assertFalse(os.path.exists(os.path.join(d, "swp-001_tr.tran.tran")))
        self.assertEqual(psfcheck.check(os.path.join(d, "swp_tr.sweep")).sweep_values(), [1000.0])   # the prefix

    def test_status_2_from_the_engine_converts_what_finished(self):
        self.deck(TWO)
        self.use(synth_runner(rc=2, status={"vamos_a3": "failed"}, missing=("vamos_a3",)))
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 2, out)
        self.assertFatal(out)
        self.assertEqual(self.check_results(os.path.join(self.tmp, "rc.raw")).keys(), ["op1-dc", "op2-dc"])

    def test_steps_the_engine_never_reached_are_not_warned_about(self):
        self.deck(TWO)
        self.use(synth_runner(rc=2, absent=("vamos_a2", "vamos_a3")))
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 2, out)
        self.assertNotIn("left no output file", out)                  # stop.py: absent = never reached
        self.assertEqual(self.check_results(os.path.join(self.tmp, "rc.raw")).keys(), ["op1-dc"])
        self.use(synth_runner(rc=0, absent=("vamos_a3",)))              # rc 0: the runner owes the step
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 1, out)
        self.assertIn("the engine left no output file for analysis `tr1'", out)

    def test_status_3_converts_the_interrupted_analysis(self):
        self.deck(TWO)
        self.use(synth_runner(rc=3, status={"vamos_a3": "interrupted"}, truncate={"vamos_a3": 4}))
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 3, out)
        self.assertFatal(out)
        d = os.path.join(self.tmp, "rc.raw")
        self.assertEqual(self.check_results(d).keys(), ["op1-dc", "op2-dc", "tr1-tran"])
        self.assertEqual(len(psfcheck.check(os.path.join(d, "tr1.tran.tran")).sweep_values()), 4)
        self.assertIn("run directory kept:", out)

    def test_stop_from_the_runner_is_3_without_converting(self):
        self.deck(TWO)

        def second_signal(*a):
            raise stop.Stop(signal.SIGINT)
        self.use(second_signal)
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 3, out)
        self.assertFatal(out)
        self.assertIn("stopped by a second SIGINT: the engine was killed and nothing is converted", out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "rc.raw", "logFile")))
        self.assertEqual(os.listdir(os.path.join(self.tmp, "rc.raw")), [])

    def test_a_netlist_without_analyses_runs_nothing(self):
        self.deck("// nothing\nsimulator lang=spectre\nR1 (a 0) resistor r=1k\n")
        self.use(never_called)
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 0, out)
        self.assertIn("the netlist has no analyses: nothing to run", out)
        self.assertTrailerOk(out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "rc.raw")))
        self.assertEqual(self.run_dirs(), [])


# =====================================================================================================
# 2.7: every exception is exit status 2 with the trailer
# =====================================================================================================

class TestErrors(FlowCase):
    def test_parse_error(self):
        self.deck("// broken\nsimulator lang=spectre\nR1 (a 0 resistor r=1k\n")
        self.use(never_called)
        rc, out, err = self.spectre(["rc.scs"])
        self.assertEqual((rc, err), (2, ""))
        self.assertIn("Error from spectre during circuit read-in.\n    %s:3: " % os.path.join(self.tmp, "rc.scs"), out)
        self.assertFatal(out)
        self.assertNotIn("Traceback", out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "rc.raw")))

    def test_plan_error(self):
        self.deck(TWO.replace("tr1 tran stop=1m", "tr1 tran stop=1m start=1u"))
        self.use(never_called)
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 2, out)
        self.assertIn("Error from spectre during initial setup.", out)
        self.assertIn("tran tr1: start=1e-06: only start=0 is supported", out)
        self.assertFatal(out)

    def test_engine_choice_error_before_the_run_directory(self):
        self.deck(TWO)
        self.use(never_called)
        os.environ["VAMOS_ANALOG"] = "bogus"
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 2, out)
        self.assertIn("VAMOS_ANALOG=bogus: the analog engine must be vacask or xyce", out)
        self.assertFatal(out)
        self.assertEqual(os.listdir(self.tmp), ["rc.scs"])

    def test_engine_problems(self):
        self.deck(TWO)
        self.use(never_called)
        os.environ["VAMOS_VACASK"] = os.path.join(self.tmp, "nonexistent")
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 2, out)
        self.assertIn("VAMOS_VACASK=%s is not an executable" % os.path.join(self.tmp, "nonexistent"), out)
        self.assertFatal(out)

    def test_bad_vamos_cpp_with_E_is_exit_2(self):
        self.deck(TWO)
        self.use(never_called)
        os.environ["VAMOS_CPP"] = "/nonexistent"
        rc, out, err = self.spectre(["-E", "rc.scs"])
        self.assertEqual((rc, err), (2, ""))
        self.assertIn("VAMOS_CPP=/nonexistent", out)
        self.assertFatal(out)

    def test_missing_cpp_is_exit_2(self):
        self.deck(TWO)
        self.use(never_called)
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        os.environ["PATH"] = empty
        saved = tools._real_cache.copy()
        tools._real_cache.clear()
        orig = tools._search_real
        tools._search_real = lambda name: None if name == "cpp" else orig(name)
        try:
            rc, out, _ = self.spectre(["-E", "rc.scs"])
        finally:
            tools._search_real = orig
            tools._real_cache.clear()
            tools._real_cache.update(saved)
        self.assertEqual(rc, 2, out)
        self.assertIn("cpp not found", out)
        self.assertFatal(out)

    def test_settings_errors_stop_the_run(self):
        self.deck(TWO.replace("op1 dc\n", "o1 options rawfmt=bogus\nop1 dc\n"))
        self.use(never_called)
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 2, out)
        self.assertIn("options rawfmt=bogus: unknown output format", out)

    def test_strict_makes_a_netlist_warning_an_error(self):
        self.deck(TWO.replace("op1 dc\n", "o1 options bogus=3\nop1 dc\n"))
        self.use(synth_runner())
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 0, out)
        self.assertIn("Warning from spectre during circuit read-in.", out)
        self.use(never_called)
        rc, out, _ = self.spectre(["rc.scs"], {"strict": True})
        self.assertEqual(rc, 2, out)
        self.assertIn("Error from spectre during circuit read-in.\n    %s:6: options bogus=3: unknown to vamos"
                      % os.path.join(self.tmp, "rc.scs"), out)

    def test_a_results_path_that_is_a_file(self):
        self.deck(TWO)
        self.write("rc.raw", "a file")
        self.use(never_called)
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 2, out)
        self.assertIn("is a file; the psf results are a directory", out)
        self.assertEqual(self.read("rc.raw"), "a file")

    def test_a_run_directory_that_cannot_be_created(self):
        self.deck(TWO)
        self.write("afile", "not a directory")
        self.use(never_called)
        rc, out, err = self.spectre(["rc.scs", "-raw", "afile/x.raw"])
        self.assertEqual((rc, err), (2, ""))
        self.assertIn("Error from spectre during initial setup.\n    cannot create the run directory in %s"
                      % os.path.join(self.tmp, "afile"), out)
        self.assertFatal(out)

    def test_an_internal_error_in_the_runner(self):
        self.deck(TWO)

        def boom(nl, plan, sigmap, run_dir, job, log):
            with open(os.path.join(run_dir, "vamos.sim"), "w") as fh:
                fh.write("deck\n")
            raise RuntimeError("boom")
        self.use(boom)
        rc, out, err = self.spectre(["rc.scs"])
        self.assertEqual((rc, err), (2, ""))
        m = re.search(r"Error from spectre during setup\.\n    internal error: RuntimeError: boom "
                      r"\(the traceback is in (\S+)\)", out)
        self.assertIsNotNone(m, out)
        self.assertEqual(os.path.basename(m.group(1)), flow.TRACEBACK_NAME)
        with open(m.group(1)) as fh:
            self.assertIn("Traceback (most recent call last)", fh.read())
        self.assertNotIn("Traceback", out)
        self.assertIn("run directory kept: %s" % os.path.dirname(m.group(1)), out)
        self.assertFatal(out)

    def test_an_internal_error_before_the_run_directory(self):
        self.deck(TWO)
        self.use(never_called)
        orig = flow.banner.provenance

        def bad(*a, **k):
            raise KeyError("header")
        flow.banner.provenance = bad
        try:
            rc, out, _ = self.spectre(["rc.scs", "+log", "l.log"])
            rc2, out2, _ = self.spectre(["rc.scs"])
        finally:
            flow.banner.provenance = orig
        self.assertEqual((rc, rc2), (2, 2))
        self.assertIn("internal error: KeyError: 'header' (the traceback is in %s)" % os.path.join(self.tmp, "l.log"),
                      out)
        text = self.read("l.log")
        self.assertIn("Traceback (most recent call last)", text)
        self.assertNotIn("Traceback", out)
        m = re.search(r"\(the traceback is in (\S+vamos-spectre-\S+\.traceback)\)", out2)
        self.assertIsNotNone(m, out2)
        try:
            with open(m.group(1)) as fh:
                self.assertIn("KeyError", fh.read())
        finally:
            os.remove(m.group(1))
        self.assertEqual(self.run_dirs(), [])


# =====================================================================================================
# 3-6: the input, the log, cpp, the duplicate option note
# =====================================================================================================

class TestInput(FlowCase):
    def test_stdin(self):
        calls: list = []
        self.use(synth_runner(calls=calls))
        rc, out, _ = self.spectre([], stdin=TWO.replace("op1 dc\n", "o1 options bogus=3\nop1 dc\n"))
        self.assertEqual(rc, 0, out)
        self.assertIn("Simulating `stdin' on ", out)
        self.assertIn("    stdin:6: options bogus=3: unknown to vamos", out)      # origins stdin:<line> (§1)
        self.assertIn("stdin", calls[0].listing)                                    # the copy <run>/stdin
        self.assertTrue(os.path.basename(calls[0].run_dir).startswith("stdin.run."))
        self.check_results(os.path.join(self.tmp, "stdin.raw"))                      # %C = stdin (§2.4)
        lines = [ln for ln in TWO.splitlines()]
        self.assertEqual(psfcheck.check(os.path.join(self.tmp, "stdin.raw", "op1.dc")).header["design"], lines[0])

    def test_stdin_include_resolves_against_the_cwd(self):
        self.write("inc.scs", "R2 (out 0) resistor r=1k\n")
        self.use(synth_runner())
        rc, out, _ = self.spectre([], stdin=TWO.replace("R2 (out 0) resistor r=1k\n", "include \"inc.scs\"\n"))
        self.assertEqual(rc, 0, out)

    def test_log_both_and_file_only(self):
        self.deck(TWO)
        self.use(synth_runner())
        rc, out, err = self.spectre(["+log", "both.log", "rc.scs"])
        self.assertEqual((rc, err), (0, ""))
        self.assertEqual(out, self.read("both.log"))
        self.assertTrailerOk(out)
        rc, out, err = self.spectre(["=log", "%C:r.out", "rc.scs"])
        self.assertEqual((rc, out, err), (0, "", ""))
        self.assertTrailerOk(self.read("rc.out"))
        rc, out, err = self.spectre(["-outdir", "od", "=log", "o.log", "rc.scs"])       # the log in -outdir
        self.assertEqual((rc, out, err), (0, "", ""))
        self.assertTrailerOk(self.read("od/o.log"))

    def test_cpp_directive_without_E(self):
        self.deck("// directive\n#define RVAL 1k\nsimulator lang=spectre\nR1 (a 0) resistor r=1k\nop1 dc\n")
        self.use(never_called)
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 2, out)
        self.assertIn("%s:2: CPP directive without -E: #define is not processed" % os.path.join(self.tmp, "rc.scs"), out)

    @needs_cpp
    def test_cpp_runs_with_E(self):
        self.deck("// cpp title\n#define RVAL 2k\nsimulator lang=spectre\nV1 (a 0) vsource dc=1\n"
                  "R1 (a 0) resistor r=RVAL\nop1 dc\n")
        calls: list = []
        self.use(synth_runner(calls=calls))
        rc, out, _ = self.spectre(["-E", "rc.scs"], {"verbose": True})
        self.assertEqual(rc, 0, out)
        self.assertIn("cpp.out", calls[0].listing)
        self.assertRegex(out, r"\n\S*cpp -traditional-cpp -nostdinc -undef \S+rc\.scs \S+cpp\.out\n")
        self.assertRegex(out.split("\n\n", 1)[0], r"\n  cpp +")                 # the provenance row
        self.assertEqual(psfcheck.check(os.path.join(self.tmp, "rc.raw", "op1.dc")).header["design"], "// cpp title")

    @needs_cpp
    def test_cpp_macro_inside_a_quoted_spice_expression_is_warned(self):
        self.deck("// macros\n#define RV 2k\nsimulator lang=spectre\nV1 (a 0) vsource dc=1\nR1 (a 0) resistor r=RV\n"
                  "simulator lang=spice\n.param rr='RV*2'\nsimulator lang=spectre\nop1 dc\n")
        self.use(synth_runner())
        rc, out, _ = self.spectre(["-E", "rc.scs"])
        self.assertIn("%s:7: macro RV inside the quoted expression 'RV*2' is not expanded by cpp"
                      % os.path.join(self.tmp, "rc.scs"), out)
        self.assertIn(rc, (0, 2), out)

    def test_the_duplicate_option_note_is_the_parsers(self):
        self.deck(TWO.replace("op1 dc\n", "o1 options rforce=1 pivrel=1e-3 bogus=3 rcut=0\nop1 dc\n"))
        self.use(synth_runner())
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count("rforce=1"), 1, out)
        self.assertIn("options rforce=1: not applied (§3.10)", out)
        self.assertNotIn("rforce=1 has no effect here", out)
        self.assertNotIn("pivrel=0.001 has no effect here", out)
        self.assertEqual(out.count("bogus=3"), 1, out)
        # rcut's silent default is infinite (§3.10, ref19 pp.196-197) in the parser as in the plan: rcut=0
        # is the parser's warning, and plan.build's message for it is the duplicate dropped
        self.assertEqual(out.count("rcut=0"), 1, out)
        self.assertIn("options rcut=0: element reduction is not applied (§3.10)", out)

    def test_the_duplicate_info_note_is_the_parsers(self):
        # the q.54 rule for info statements (E2's seam): the parser's "<name> info: the info file is not
        # written" is kept and plan.build's note action for the same statement dropped
        self.deck(TWO.replace("op1 dc\n", "myinfo info what=oppoint where=rawfile\nop1 dc\n"))
        self.use(synth_runner())
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count("file is not written"), 1, out)
        self.assertIn("myinfo info: the info file is not written", out)

    def test_engine_dependent_dispositions(self):
        self.write("myres.va", '`include "disciplines.vams"\nmodule myres(p, n);\n  inout p, n;\n  electrical p, n;\n'
                               '  parameter real r = 1k;\n  analog I(p, n) <+ V(p, n) / r;\nendmodule\n')
        self.deck(TWO.replace("op1 dc\n", "ahdl_include \"myres.va\"\nR9 (out 0) myres r=2k\nop1 dc\n"))
        self.use(synth_runner())
        self.use(lambda *a: EngineResult(rc=2), engine="xyce")
        os.environ["CDS_AHDL_FINISH_MODE"] = "1"
        os.environ["CDS_AHDL_IDT_SCALE"] = "1.0"                         # the documented default: silent
        os.environ["CDS_VLOGA_INCLUDE"] = "inc1:inc2"
        rc, out, _ = self.spectre(["rc.scs", "-va,define", "HALF"], {"analog": "xyce"})
        self.assertIn("    -va,define HALF: not applied on Xyce, whose .hdl compile takes no macro definitions", out)
        self.assertIn("    CDS_VLOGA_INCLUDE=inc1:inc2: not applied on Xyce", out)
        self.assertIn("Warning from spectre during initial setup.\n    CDS_AHDL_FINISH_MODE=1: not applied", out)
        self.assertNotIn("CDS_AHDL_IDT_SCALE", out)
        rc, out, _ = self.spectre(["rc.scs", "-va,define", "HALF"])        # VACASK: openvaf-r takes them
        self.assertEqual(rc, 0, out)
        self.assertNotIn("-va,define HALF", out)
        self.assertNotIn("CDS_VLOGA_INCLUDE", out)
        self.assertIn("CDS_AHDL_FINISH_MODE=1: not applied", out)
        rc, out, _ = self.spectre(["rc.scs"], {"strict": True})            # a warning: an error under strict
        self.assertEqual(rc, 2, out)
        self.assertIn("Error from spectre during initial setup.\n    CDS_AHDL_FINISH_MODE=1", out)
        self.deck(TWO)                                                   # no Verilog-A: they change nothing
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 0, out)
        self.assertNotIn("CDS_AHDL", out)

    def test_the_inventory(self):
        self.deck(RC)
        self.use(synth_runner())
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 0, out)
        inv = out.split("Circuit inventory:\n", 1)[1].split("\n")
        self.assertEqual(inv[:4], ["              nodes 2", "          capacitor 1", "           resistor 1",
                                   "            vsource 1"])

    def test_e2e9_default_notes_and_maxnotes(self):
        # §12 phase 2: e2e 9's 20 default notes (nrd/nrs on 8 devices, w/l on 2) are correct per §3.9 and read
        # in full by default: the options statement's maxnotes=5 is per analysis and "has no effect on notices
        # issued as part of parsing the netlist" (ref19 p.205), and v1 sets no default; -maxnotes N caps them
        # per message id (§8.7: the digits of M1..M8 and of the line numbers make one id) with one notice
        d = spectre_fixture("e2e", "09_model_group")
        for f in os.listdir(d):
            if os.path.isfile(os.path.join(d, f)):
                shutil.copyfile(os.path.join(d, f), os.path.join(self.tmp, f))
        self.use(synth_runner())
        rc, out, _ = self.spectre(["deck.scs"])
        self.assertEqual(rc, 0, out)
        self.assertEqual((out.count("nrd=0.0 written on the instance"), out.count("nrs=0.0 written on the instance")),
                         (8, 8))
        self.assertEqual(out.count("master's default 5e-06 m is written"), 2)
        rc, out, _ = self.spectre(["deck.scs", "-maxnotes", "2"])
        self.assertEqual(rc, 0, out)
        self.assertEqual((out.count("nrd=0.0 written on the instance"), out.count("nrs=0.0 written on the instance")),
                         (2, 2))
        self.assertEqual(out.count("further notices of this kind are suppressed (-maxnotes 2)"), 2)

    def test_config_fragments(self):
        self.deck(TWO.replace("R2 (out 0) resistor r=1k", "R2 (out 0) resistor r=rr"))
        self.write("pre.scs", "parameters rr=2k\n")
        self.write("post.scs", "op9 dc\n")
        self.use(synth_runner())
        rc, out, _ = self.spectre(["+pre_config", "pre.scs", "+config", "post.scs", "rc.scs"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.check_results(os.path.join(self.tmp, "rc.raw")).keys(),
                         ["op1-dc", "op2-dc", "tr1-tran", "op9-dc"])

    def test_the_netlists_maxwarns_governs_its_own_warnings(self):
        self.deck(TWO.replace("op1 dc\n", "o1 options maxwarns=1\no2 options foo=1\no3 options foo=2\nop1 dc\n"))
        self.use(synth_runner())
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count("unknown to vamos"), 1, out)
        self.assertIn("further warnings of this kind are suppressed (-maxwarns 1)", out)
        self.assertEqual(self.assertTrailerOk(out)[1], 2)              # both are counted (§8.7)

    def test_state_files_are_read_against_the_cwd(self):
        self.write("ns.ic", "out 0.5\n")
        self.deck(TWO.replace("op1 dc\n", "op1 dc readns=\"ns.ic\"\n"))
        calls: list = []
        self.use(synth_runner(calls=calls))
        elsewhere = tempfile.mkdtemp(prefix="vamos-test-cwd-")
        self.addCleanup(shutil.rmtree, elsewhere, True)
        os.chdir(elsewhere)                                    # the process cwd is not the job's
        try:
            rc, out, _ = self.spectre(["rc.scs"])
        finally:
            os.chdir(self.tmp)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("not found", out)
        step = [a.step for a in calls[0].plan.actions if a.step is not None and a.step.name == "op1"][0]
        self.assertEqual(step.args["readns"], os.path.join(self.tmp, "ns.ic"))


# =====================================================================================================
# 2.7: signals (a child python running flow.run)
# =====================================================================================================

CHILD = r'''
import os, sys
sys.path.insert(0, %(root)r)
sys.path.insert(0, %(tests)r)
os.chdir(%(tmp)r)
import test_spectre_flow as T
sys.exit(T.child_main(sys.argv[1:]))
'''

ENGINE = "import time\nfor _ in range(1200):\n    time.sleep(0.05)\n"
# an engine still running after the forwarded SIGTERM: the second signal reaches the runner (§2.7)
STUBBORN = ("import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nfor _ in range(1200):\n"
            "    time.sleep(0.05)\n")


class _Counting(proc.Interrupts):
    """proc.Interrupts telling a second signal (stop.Stop) from the grace-expiry kill."""

    second = False

    def _on_signal(self, signum, frame):
        if self.signum is not None:
            self.second = True
        super()._on_signal(signum, frame)


def _marker(path: str) -> None:
    with open(path, "w") as fh:
        fh.write(str(os.getpid()))


def _proc_state(pid: int) -> str:
    """The /proc/<pid>/stat state letter (R S T Z ...), or "gone"."""
    try:
        with open("/proc/%d/stat" % pid) as fh:
            return fh.read().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return "gone"


def _wait_state(pid: int, states: str, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _proc_state(pid) in states:
            return True
        time.sleep(0.05)
    return False


def child_main(argv: List[str]) -> int:
    """The child of TestSignals: `mode marker sleep deck-args...`."""
    import time as _time
    mode, marker, sleep = argv[0], argv[1], float(argv[2])
    tools.invoked = "spectre"
    if mode == "ignore-hup":
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    if mode in ("parse", "ignore-hup"):
        real_parse = flow.nspectre.parse

        def slow_parse(*a, **k):
            _marker(marker)
            _time.sleep(sleep)
            return real_parse(*a, **k)
        flow.nspectre.parse = slow_parse
        runner = synth_runner()
    elif mode in ("engine", "engine-stubborn"):
        synth = synth_runner(rc=3, status={"vamos_a3": "interrupted"}, truncate={"vamos_a3": 3})
        script = ENGINE if mode == "engine" else STUBBORN

        def runner(nl, plan, sigmap, run_dir, job, log):
            with _Counting(stop.HANDLED, stop.FORWARD, flags=stop.FLAGS, grace=30.0) as intr:
                p = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.DEVNULL,
                                     preexec_fn=proc.child_setup())
                intr.attach(p)
                _marker(marker)
                p.wait()
            if intr.second:
                raise stop.Stop(intr.signum)
            if intr.signum is None:
                return synth_runner()(nl, plan, sigmap, run_dir, job, log)
            return synth(nl, plan, sigmap, run_dir, job, log)
    elif mode.startswith("convert"):                   # convert[:k]: sleep after the k-th file (1)
        real_write = flow.psf.write_analysis
        state = {"n": 0}
        at = int(mode.split(":")[1]) if ":" in mode else 1

        def slow_write(*a, **k):                       # the k-th file written, then the marker and the sleep
            real_write(*a, **k)
            state["n"] += 1
            if state["n"] == at:
                _marker(marker)
                _time.sleep(sleep)
        flow.psf.write_analysis = slow_write
        # convert-after-rc3: the engine run ended with status 3 (a signal stopped it), so the next stop
        # signal is the second one (§2.7, X's seam): the conversion stops at once
        runner = (synth_runner(rc=3, status={"vamos_a3": "interrupted"}, truncate={"vamos_a3": 3})
                  if mode.startswith("convert-after-rc3") else synth_runner())
    else:
        raise SystemExit("unknown mode %r" % mode)
    sys.modules["vamos.spectre.run_vacask"] = types.SimpleNamespace(run=runner)
    job = personality.build_job(argv[3:], os.environ, os.getcwd(), "spectre")
    return flow.run(job, {})


@unittest.skipUnless(POSIX_SIGNALS, "POSIX signals")
class TestSignals(FlowCase):
    def child(self, mode: str, sleep: float, signals: Sequence[int], deck: str = TWO, gap: float = 0.3,
              args: Sequence[str] = ("rc.scs",), before=None) -> Tuple[int, str]:
        """Run child_main(mode) in a python child, send `signals` once it reached its marker (gap
        seconds apart) -> (exit status, output)."""
        self.deck(deck)
        marker = os.path.join(self.tmp, "marker")
        code = CHILD % {"root": ROOT, "tests": TESTS, "tmp": self.tmp}
        env = dict(os.environ)
        if before is not None:
            before(env)
        p = subprocess.Popen([sys.executable, "-c", code, mode, marker, str(sleep)] + list(args),
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        try:
            deadline = time.time() + 120
            while not os.path.exists(marker) and time.time() < deadline and p.poll() is None:
                time.sleep(0.05)
            self.assertTrue(os.path.exists(marker), "the child never reached its marker:\n%s"
                            % (p.communicate(timeout=60)[0].decode("utf-8", "replace") if p.poll() is not None else ""))
            time.sleep(0.2)
            for sig in signals:
                p.send_signal(sig)
                time.sleep(gap)
            out, _ = p.communicate(timeout=120)
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
        return p.returncode, out.decode("utf-8", "replace")

    def test_before_the_engine_each_stop_signal_is_status_3(self):
        for name in ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT"):
            with self.subTest(signal=name):
                rc, out = self.child("parse", 60, [getattr(signal, name)])
                self.assertEqual(rc, 3, out)
                self.assertIn("the run was stopped by %s" % name, out)
                self.assertTrue(out.endswith(TRAILER_FATAL), out[-400:])
                self.assertFalse(os.path.exists(os.path.join(self.tmp, "rc.raw", "logFile")))
                os.remove(os.path.join(self.tmp, "marker"))

    def test_an_ignored_signal_stays_ignored(self):
        rc, out = self.child("ignore-hup", 1.5, [signal.SIGHUP])
        self.assertEqual(rc, 0, out)
        self.assertNotIn("stopped by", out)

    def test_usr1_prints_the_status_line_and_usr2_the_note(self):
        rc, out = self.child("parse", 1.5, [signal.SIGUSR1, signal.SIGUSR2])
        self.assertEqual(rc, 0, out)
        self.assertIn("spectre status: no analysis running (setting up), elapsed = ", out)
        self.assertIn("Notice from spectre during circuit read-in.\n    checkpoint not supported\n", out)
        self.assertTrue(TRAILER_OK.search(out), out[-400:])

    def test_a_signal_while_the_engine_runs_converts_the_finished_and_the_interrupted(self):
        rc, out = self.child("engine", 0, [signal.SIGINT])
        self.assertEqual(rc, 3, out)
        d = os.path.join(self.tmp, "rc.raw")
        self.assertEqual(self.check_results(d).keys(), ["op1-dc", "op2-dc", "tr1-tran"])
        self.assertEqual(len(psfcheck.check(os.path.join(d, "tr1.tran.tran")).sweep_values()), 3)
        self.assertTrue(out.endswith(TRAILER_FATAL), out[-400:])

    def test_a_second_signal_while_the_engine_runs_is_3_without_converting(self):
        rc, out = self.child("engine-stubborn", 0, [signal.SIGTERM, signal.SIGTERM], gap=0.5)
        self.assertEqual(rc, 3, out)
        self.assertIn("stopped by a second SIGTERM", out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "rc.raw", "logFile")))
        self.assertTrue(out.endswith(TRAILER_FATAL), out[-400:])

    def test_a_signal_during_the_conversion_keeps_what_is_written(self):
        rc, out = self.child("convert", 1.5, [signal.SIGINT])
        self.assertEqual(rc, 3, out)
        d = os.path.join(self.tmp, "rc.raw")
        self.assertEqual(self.check_results(d).keys(), ["op1-dc"])
        self.assertEqual(sorted(os.listdir(d)), ["logFile", "op1.dc"])
        self.assertIn("the conversion was stopped by SIGINT: the analyses converted before it are written and "
                      "listed in logFile", out)
        self.assertTrue(out.endswith(TRAILER_FATAL), out[-400:])

    def test_a_signal_inside_a_sweep_block_lists_only_the_complete_blocks(self):
        rc, out = self.child("convert:4", 1.5, [signal.SIGTERM], deck=RC)     # after swp-000_tr, the 4th file
        self.assertEqual(rc, 3, out)
        d = os.path.join(self.tmp, "rc.raw")
        self.assertEqual(self.check_results(d).keys(), ["myop-dc", "myac-ac"])
        self.assertEqual(sorted(os.listdir(d)), ["logFile", "myac.ac", "myop.dc", "swp-000_tr.tran.tran",
                                                 "swp_tr.sweep"])                # written, not listed
        self.assertIn("the conversion was stopped by SIGTERM", out)

    def test_a_second_signal_during_the_conversion_stops_at_once(self):
        rc, out = self.child("convert", 30, [signal.SIGINT, signal.SIGINT])
        self.assertEqual(rc, 3, out)
        log = psfcheck.check_logfile(os.path.join(self.tmp, "rc.raw", "logFile"))
        self.assertEqual(log.keys(), [])                     # the file in hand is not listed
        self.assertIn("the conversion was stopped by SIGINT", out)
        self.assertTrue(out.endswith(TRAILER_FATAL), out[-400:])

    def test_after_an_engine_run_a_signal_stopped_the_next_one_is_the_second(self):
        # §2.7 (X's seam, phase 2's integration): the engine run ended with status 3, its signal the first;
        # one more stop signal during the conversion stops it at once, as a second one does (the
        # file in hand not listed), where after a status-0 run one would end it before the next file
        rc, out = self.child("convert-after-rc3", 30, [signal.SIGINT])
        self.assertEqual(rc, 3, out)
        log = psfcheck.check_logfile(os.path.join(self.tmp, "rc.raw", "logFile"))
        self.assertEqual(log.keys(), [])
        self.assertIn("the conversion was stopped by SIGINT", out)
        self.assertTrue(out.endswith(TRAILER_FATAL), out[-400:])

    @unittest.skipUnless(os.path.isdir("/proc") and hasattr(signal, "SIGTSTP"), "/proc/<pid>/stat and SIGTSTP")
    def test_ctrl_z_stops_vamos_before_the_engine_and_sigcont_resumes_it(self):
        self.deck(TWO)
        marker = os.path.join(self.tmp, "marker")
        code = CHILD % {"root": ROOT, "tests": TESTS, "tmp": self.tmp}
        p = subprocess.Popen([sys.executable, "-c", code, "parse", marker, "2", "rc.scs"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            deadline = time.time() + 120
            while not os.path.exists(marker) and time.time() < deadline and p.poll() is None:
                time.sleep(0.05)
            p.send_signal(signal.SIGTSTP)
            self.assertTrue(_wait_state(p.pid, "T"), "SIGTSTP did not stop vamos")
            time.sleep(2.5)                                  # stopped: the parse's sleep would have ended
            self.assertEqual(_proc_state(p.pid), "T")
            p.send_signal(signal.SIGCONT)
            out, _ = p.communicate(timeout=120)
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
        self.assertEqual(p.returncode, 0, out)

    def test_usr1_during_the_conversion_names_the_result(self):
        rc, out = self.child("convert", 1.5, [signal.SIGUSR1])
        self.assertEqual(rc, 0, out)
        self.assertIn("spectre status: no analysis running, writing the results of `op1', elapsed = ", out)


# =====================================================================================================
# The integration stage: the real runners and engines
# =====================================================================================================

class TestIntegration(FlowCase):
    """Runs through bin/vamos with the real runners (vamos/spectre/run_vacask.py, run_xyce.py) and the
    engines (the integration stage released the skips; each test is gated on its engine).  The signal
    tests wait for the tran's rawfile in the run directory before signalling, so a slow start or a
    loaded machine cannot send the signal before the engine runs it."""

    def vamos(self, args: Sequence[str], timeout: int = 600) -> subprocess.CompletedProcess:
        return subprocess.run([LAUNCHER, "-spectre"] + list(args), cwd=self.tmp, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                              timeout=timeout)

    def started(self, args: Sequence[str]) -> subprocess.Popen:
        return subprocess.Popen([LAUNCHER, "-spectre"] + list(args), cwd=self.tmp, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)

    def wait_for(self, name: str, p: subprocess.Popen, min_bytes: int = 1, timeout: float = 300) -> Optional[str]:
        """<run>/<name> once it holds min_bytes, while the run goes on; None when it never does."""
        end = time.time() + timeout
        while time.time() < end and p.poll() is None:
            for path in glob.glob(os.path.join(self.tmp, "*.run.*", name)):
                try:
                    if os.path.getsize(path) >= min_bytes:
                        return path
                except OSError:
                    pass
            time.sleep(0.05)
        return None

    @staticmethod
    def stop(p: subprocess.Popen) -> None:
        if p.poll() is None:
            p.kill()
            p.communicate()

    @needs_vacask
    @needs_xyce
    def test_the_worked_example_on_both_engines(self):
        self.deck(RC)
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                r = self.vamos(["rc.scs", "--vamos-analog=" + engine])
                self.assertEqual(r.returncode, 0, r.stdout)
                d = os.path.join(self.tmp, "rc.raw")
                self.assertEqual(sorted(os.listdir(d)), RC_FILES)
                self.check_results(d)
                tr = psfcheck.check(os.path.join(d, "swp-000_tr.tran.tran"))
                self.assertAlmostEqual(tr.at("out", 5e-3), 0.98173, delta=2e-5)       # E74
                shutil.rmtree(d)

    @needs_vacask
    @needs_xyce
    def test_a_step_grid_a_rounding_error_short_of_stop(self):
        """§5.2: 0..0.3 step 0.1 has 4 points in the plan (and on Xyce), where VACASK's step= runs 3
        (0.3/0.1 = 2.9999999999999996); render prints the plan's values for VACASK, so both engines run 4
        sweep points and 8 dc points and the run exits 0 (before: status 1 with no message)."""
        self.deck("// step grids\nsimulator lang=spectre\nV1 (in 0) vsource dc=1\nR1 (in out) resistor r=1k\n"
                  "R2 (out 0) resistor r=1k\nswp sweep dev=V1 param=dc start=0 stop=0.3 step=0.1 {\n  op1 dc\n}\n"
                  "dc1 dc dev=V1 param=dc start=0 stop=0.7 step=0.1\n")
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                r = self.vamos(["rc.scs", "--vamos-analog=" + engine])
                self.assertEqual(r.returncode, 0, r.stdout)
                d = os.path.join(self.tmp, "rc.raw")
                log = self.check_results(d)
                self.assertEqual(sorted(f for f in os.listdir(d) if f.startswith("swp-")),
                                 ["swp-%03d_op1.dc" % k for k in range(4)])
                dc1 = psfcheck.check(os.path.join(d, "dc1.dc"))
                self.assertEqual(len(dc1.sweep_values()), 8)
                self.assertAlmostEqual(dc1.column("out")[-1], 0.35, delta=1e-9)
                self.assertAlmostEqual(psfcheck.check(os.path.join(d, "swp-003_op1.dc")).value("out"), 0.15,
                                       delta=1e-9)
                self.assertIn("dc1-dc", log.keys())
                shutil.rmtree(d)

    @needs_vacask
    @needs_xyce
    def test_noise_without_iprobe_at_a_node_inside_a_subckt(self):
        """§5.6: vamos_nin<k> goes into the body that holds the output net (I2.Mid), and the step names it
        by its path (VACASK in="I2:vamos_nin1"; Xyce AC 1 0 in the body, .NOISE naming XI2:VAMOS_NIN1);
        before phase 2's fix VACASK aborted the analysis and Xyce segfaulted (H).  out = sqrt(4kT*R) with R the
        resistance seen at Mid, (1k + 1k||1k) || 2k, at 27 C; an output across two nodes of the body
        (I2.Mid, I2.M2) runs on VACASK and is refused on Xyce (it aborts on .NOISE V(a,b))."""
        deck = ("// noise inside a subckt\nsimulator lang=spectre\nv1 (in 0) vsource dc=1 mag=1\n"
                "r1 (in out) resistor r=1k\nr2 (out 0) resistor r=1k\nsubckt cell (a)\n  r3 (a Mid) resistor r=1k\n"
                "  r4 (Mid M2) resistor r=1k\n  r5 (M2 0) resistor r=1k\nends cell\nI2 (out) cell\n"
                "nz (I2.Mid 0) noise start=1 stop=10 dec=1\n")
        want = math.sqrt(4 * 1.380649e-23 * 300.15 * (1500.0 * 2000.0 / 3500.0))
        self.deck(deck)
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                r = self.vamos(["rc.scs", "--vamos-analog=" + engine])
                self.assertEqual(r.returncode, 0, r.stdout)
                d = os.path.join(self.tmp, "rc.raw")
                self.check_results(d)
                nz = psfcheck.check(os.path.join(d, "nz.noise"))
                self.assertAlmostEqual(nz.column("out")[0] / want, 1.0, delta=1e-3)
                self.assertNotIn("in", nz.names())
                shutil.rmtree(d)
        self.deck(deck.replace("nz (I2.Mid 0)", "nz (I2.Mid I2.M2)"))
        r = self.vamos(["rc.scs", "--vamos-analog=xyce"])
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("the output across two nodes (I2.Mid, I2.M2) is not supported on Xyce in v1", r.stdout)
        r = self.vamos(["rc.scs"])
        self.assertEqual(r.returncode, 0, r.stdout)
        shutil.rmtree(os.path.join(self.tmp, "rc.raw"))
        # a given hierarchical iprobe= (§5.6: "a given iprobe= inside a subckt (X1.V2)"), which the parser
        # now reads as an instance path: vb in series between Mid and M2, so gain = V(Mid)/vb = 0.6 and the
        # output noise is that of 1.5k || 1k (vb is a short for noise)
        self.deck(deck.replace("  r4 (Mid M2) resistor r=1k\n", "  vb (Mid M2) vsource dc=0\n")
                  .replace("noise start=1", "noise iprobe=I2.vb start=1"))
        want = math.sqrt(4 * 1.380649e-23 * 300.15 * 600.0)
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine, iprobe="I2.vb"):
                r = self.vamos(["rc.scs", "--vamos-analog=" + engine])
                self.assertEqual(r.returncode, 0, r.stdout)
                d = os.path.join(self.tmp, "rc.raw")
                self.check_results(d)
                nz = psfcheck.check(os.path.join(d, "nz.noise"))
                self.assertAlmostEqual(nz.column("out")[0] / want, 1.0, delta=1e-3)
                self.assertAlmostEqual(nz.column("gain")[0], 0.6, delta=1e-6)
                self.assertAlmostEqual(nz.column("in")[0] / (want / 0.6), 1.0, delta=1e-3)
                shutil.rmtree(d)

    @needs_vacask
    @needs_xyce
    def test_the_guards_behind_the_probes(self):
        """§12 phase 2: the emitters' guards (xyce's "needs a probe", VACASK's "has no branch current") stay
        unreachable through the flow: saved terminal currents of an isnoisy=no resistor (Xyce's G form, a
        probe), an r=0 short with isnoisy=no (Xyce's R=0, before: a G form of 1/0 and the guard), isnoisy=0
        (read as no on both sides) and a vcvs and its load, with an ac step (VACASK probes the resistors)."""
        self.deck("// probe guards\nsimulator lang=spectre\nV1 (a 0) vsource dc=1\nR1 (a b) resistor r=1k isnoisy=no\n"
                  "R2 (b c) resistor r=0 isnoisy=no\nR3 (c 0) resistor r=1k isnoisy=0\nE1 (e 0 a 0) vcvs gain=1\n"
                  "Re (e 0) resistor r=1k\nop1 dc\nac1 ac start=1 stop=10 dec=1\nsave R1:1 R2:1 R3:1 E1:p Re:1\n")
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                r = self.vamos(["rc.scs", "--vamos-analog=" + engine])
                self.assertEqual(r.returncode, 0, r.stdout)
                d = os.path.join(self.tmp, "rc.raw")
                self.check_results(d)
                op = psfcheck.check(os.path.join(d, "op1.dc"))
                for name, want in (("R1:1", 5e-4), ("R2:1", 5e-4), ("R3:1", 5e-4), ("E1:p", -1e-3), ("Re:1", 1e-3)):
                    self.assertAlmostEqual(op.value(name), want, delta=1e-9, msg=name)
                shutil.rmtree(d)

    @needs_vacask
    @needs_xyce
    def test_spice_mode_controlled_sources(self):
        """§4.3 "Elements. As spice.py": SPICE-mode E/G/F/H lines with positional values (phase 2's
        integration: the reader refused every one, "esource vcvs needs gain="): out = -1 (E, gain -1),
        o2 = -1 (G, 1 mS into 1k), o3 = -2 (F, gain 2 of 1 mA), o4 = 1 (H, 1k of 1 mA) on both engines."""
        self.deck("* spice controlled sources\nv1 in 0 dc 1\nr1 in 0 1k\nvs in x 0\nrx x 0 1k\nesource out 0 in 0 -1\n"
                  "rout out 0 1k\ng1 o2 0 in 0 1m\nr2 o2 0 1k\nf1 o3 0 vs 2\nr3 o3 0 1k\nh1 o4 0 vs 1k\nr4 o4 0 1k\n"
                  ".op\n.end\n", name="ctl.sp")
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                r = self.vamos(["ctl.sp", "--vamos-analog=" + engine])
                self.assertEqual(r.returncode, 0, r.stdout)
                d = os.path.join(self.tmp, "ctl.raw")
                self.check_results(d)
                op = psfcheck.check(os.path.join(d, "opBegin.dc"))
                for name, want in (("out", -1.0), ("o2", -1.0), ("o3", -2.0), ("o4", 1.0)):
                    self.assertAlmostEqual(op.value(name), want, delta=1e-9, msg=name)
                shutil.rmtree(d)

    @needs_vacask
    @needs_xyce
    def test_an_ac_parameter_sweep_inside_a_sweep_block(self):
        """§7.2 ".STEP lines innermost first" (X's seam): an ac parameter sweep at a fixed frequency is the
        innermost level, so Xyce's first .STEP; each leaf of the block holds its rval and every c, out =
        1/(1 + j w R C) at 100 Hz, on both engines (before the fix Xyce's leaves mixed the two rvals)."""
        self.deck("// ac parameter sweep in a block\nsimulator lang=spectre\nparameters rval=1k\n"
                  "V1 (in 0) vsource dc=0 mag=1\nR1 (in out) resistor r=rval\nC1 (out 0) capacitor c=1u\n"
                  "swp sweep param=rval values=[1k 10k] {\n  ac1 ac dev=C1 param=c values=[1u 2u 3u] freq=100\n}\n")
        w = 2 * math.pi * 100.0
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                r = self.vamos(["rc.scs", "--vamos-analog=" + engine])
                self.assertEqual(r.returncode, 0, r.stdout)
                d = os.path.join(self.tmp, "rc.raw")
                self.check_results(d)
                for k, rv in enumerate((1e3, 1e4)):
                    leaf = psfcheck.check(os.path.join(d, "swp-%03d_ac1.ac" % k))
                    self.assertEqual(len(leaf.sweep_values()), 3)
                    for c, got in zip((1e-6, 2e-6, 3e-6), leaf.column("out")):
                        want = 1.0 / complex(1.0, w * rv * c)
                        self.assertAlmostEqual(abs(complex(got) - want), 0.0, delta=1e-6 * abs(want),
                                               msg=(engine, rv, c))
                shutil.rmtree(d)

    @unittest.skipUnless(POSIX_SIGNALS, "POSIX signals")
    @needs_vacask
    def test_sigint_during_a_long_vacask_tran(self):
        self.deck(TWO.replace("tr1 tran stop=1m", "tr1 tran stop=10 maxstep=1u"))
        p = self.started(["rc.scs"])
        try:
            if self.wait_for("vamos_a3.raw", p, min_bytes=4096) is None:      # tr1 is the third action
                self.fail("the tran never got going (no <run>/vamos_a3.raw):\n" + (p.communicate()[0] or ""))
            p.send_signal(signal.SIGINT)
            out, _ = p.communicate(timeout=120)
        finally:
            self.stop(p)
        self.assertEqual(p.returncode, 3, out)
        self.assertTrue(out.endswith(TRAILER_FATAL), out)
        self.assertEqual(self.check_results(os.path.join(self.tmp, "rc.raw")).keys(), ["op1-dc", "op2-dc", "tr1-tran"])
        self.assertIn("run directory kept: ", out)

    @unittest.skipUnless(POSIX_SIGNALS, "POSIX signals")
    @needs_vacask
    def test_sigusr1_during_a_long_vacask_tran(self):
        self.deck(TWO.replace("tr1 tran stop=1m", "tr1 tran stop=1 maxstep=1u"))
        p = self.started(["rc.scs"])
        try:
            if self.wait_for("vamos_a3.raw", p, min_bytes=1) is None:
                self.fail("the tran never started (no <run>/vamos_a3.raw):\n" + (p.communicate()[0] or ""))
            p.send_signal(signal.SIGUSR1)
            out, _ = p.communicate(timeout=1200)
        finally:
            self.stop(p)
        self.assertEqual(p.returncode, 0, out)
        self.assertIn("spectre status: analysis `tr1' running", out)
        self.assertTrue(TRAILER_OK.search(out), out)


if __name__ == "__main__":
    unittest.main()
