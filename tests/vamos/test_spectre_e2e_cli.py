"""e2e 11-14, the command-line decks (docs/VAMOS_SPECTRE_DESIGN.md §11.2 items 11-14; phase 2): what
their expect.json cannot say.  test_spectre_e2e runs every expect.json (the exit status, the results
directory, the values); this module adds the trailers, the messages, the signals, the log and state
files and the third-party nutmeg readers.  WSL: needs_flow and an engine; every skip says why.

    cd tests/vamos && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_spectre_e2e_cli

TestExitStatus (11)     one failing analysis among good ones, a failing sweep point, a tran that aborts
                        mid-run (status 1, the others written, the "completes" trailer, the run
                        directory kept); a VACASK bind failure (status 1); a parse error (status 2, no
                        data file, the "terminated prematurely" trailer); SIGINT during a long tran
                        (status 3: op1 and the partial tr1 written and listed, the warning, the
                        trailer); SIGUSR1 (the status line naming the running analysis; the run
                        completes) (§2.7, §7.1, §7.2, §8.2, §8.7); phase 2c: the engine killed from
                        outside, then a SIGINT while tr1 is converted: a first signal (R-run-9)
TestScreenGone (11, 14) phase 2c, D1: the terminal hangs up, Ctrl-C kills the tee the screen goes through,
                        stdout closed at start (>&-), a reader that stops after one line (| head -1): the
                        run goes on without the screen and earns its status, nothing on stderr, no
                        traceback file (R-run-1, R-run-2)
TestAde (12)            the §9 ADE command line: psf/ holds logFile and the data files beside ADE's own
                        file, spectre.out ends with the trailer, spectre.ic and spectre.fc hold every
                        node (§8.6)
TestNutmeg (13)         -format nutascii and nutbin: one plot per analysis and leaf, loaded by ngspice
                        -b (load, setplot, print; vectors A and a apart, E87) and nutbin by VACASK's
                        python/rawfile.py rawread; no logFile (§8.5)
TestDefaultsStdin (14)  SPECTRE_DEFAULTS="+log %C:r.out" writes x.out, -outdir o writes o/x.raw, a
                        netlist on stdin (%C = stdin, the copy in the run directory) (§1, §2.2, §2.5)
"""

import glob
import importlib.util
import math
import os
import re
import select
import signal
import subprocess
import threading
import time
import unittest
from typing import Dict, List, Optional, Sequence, Tuple

import psfcheck  # noqa: E402
import spectre_testlib as T  # noqa: E402
from vamos_testlib import procs_under  # noqa: E402
from spectre_testlib import (LAUNCHER, SpectreCase, needs_flow, needs_ngspice, needs_vacask, needs_vacask_rawread,
                             ngspice_bin, spectre_fixture, vacask_src)  # noqa: E402

E2E = spectre_fixture("e2e")
SIGNALS = ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT", "SIGUSR1", "SIGUSR2")


def _default_signals() -> None:
    """In the child: every signal vamos handles at its default, as from an interactive shell (a
    suite run under nohup or as a background job inherits some ignored, and vamos keeps an
    inherited SIG_IGN on purpose, §2.7 E81)."""
    for name in SIGNALS:
        s = getattr(signal, name, None)
        if s is not None:
            signal.signal(s, signal.SIG_DFL)


def _engine_procs(d: str, name: str) -> List[Tuple[int, str]]:
    """(pid, argv[0]) of the processes whose cwd is under d and whose program is named `name` (the engine
    runs in the run directory)."""
    out = []
    for pid in procs_under(d):
        try:
            with open("/proc/%d/cmdline" % pid, "rb") as fh:
                argv0 = fh.read().split(b"\0", 1)[0].decode("utf-8", "replace")
        except OSError:
            continue
        if name in os.path.basename(argv0):
            out.append((pid, argv0))
    return out


def failure_messages(text: str, name: str, severities: Sequence[str] = ("error", "warning")) -> List[str]:
    """The messages of those severities whose text names `name` as a word."""
    pat = re.compile(r"(?<![\w-])%s(?![\w-])" % re.escape(name))
    return [body for sev, body in T.parse_messages(text) if sev in severities and pat.search(body)]


def read_state(path: str) -> Tuple[List[str], Dict[str, float]]:
    """A state file (§8.6): its `#` lines, and every `<name> <value>` entry (names as written)."""
    heads, vals = [], {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s = line.strip()
            if not s:
                continue
            if s.startswith("#"):
                heads.append(s)
                continue
            name, value = s.split(None, 1)
            vals[name] = float(value.split("#", 1)[0])
    return heads, vals


def decoded(names) -> Dict[str, str]:
    """name as written -> name with its backslash escapes removed (§3.2: net\\<3\\> is net<3>)."""
    return {re.sub(r"\\(.)", r"\1", n): n for n in names}


# -- ngspice (§11.2 13; E87) --------------------------------------------------------------------------------

_PLOT_LINE = re.compile(r"^\s*(?:Current\s+)?(\S+)\t(.*)$")
_PLOT_DESC = re.compile(r"\((\w[\w ]* Analysis `[^']*'.*)\)$")      # "<title> (<description>)", §8.5


def run_ngspice(d: str, raw: str, control: Sequence[str], name: str) -> str:
    """ngspice -b on a control-only script in d that loads `raw` and runs `control`; its output."""
    script = os.path.join(d, name + ".cir")
    with open(script, "w") as fh:
        fh.write("* vamos e2e 13: %s\n.control\nset nobreak\nset width=4000\nload %s\n%s\nquit\n.endc\n.end\n"
                 % (name, os.path.basename(raw), "\n".join(control)))
    p = subprocess.run([ngspice_bin(), "-b", os.path.basename(script)], cwd=os.path.dirname(raw),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, errors="replace",
                       timeout=300, stdin=subprocess.DEVNULL)
    return p.stdout


def ngspice_plots(d: str, raw: str) -> List[Tuple[str, str]]:
    """The plots ngspice loads from raw, in load order: [(ngspice's name, the plot's description)]."""
    out = run_ngspice(d, raw, ["setplot"], "list")
    if "List of plots available:" not in out:
        raise AssertionError("ngspice did not load %s:\n%s" % (raw, out))
    plots = []
    for line in out.split("List of plots available:", 1)[1].splitlines():
        m = _PLOT_LINE.match(line)
        if not m or m.group(1) == "const":
            continue
        d2 = _PLOT_DESC.search(m.group(2))
        plots.append((m.group(1), d2.group(1) if d2 else m.group(2)))
    return list(reversed(plots))                                          # ngspice lists the newest first


def _numbers(tokens: List[str]) -> List[object]:
    """The values of one table row: a token ending in "," is the real part of a complex value."""
    vals, i = [], 0
    while i < len(tokens):
        t = tokens[i]
        if t.endswith(",") and i + 1 < len(tokens):
            vals.append(complex(float(t[:-1]), float(tokens[i + 1])))
            i += 2
        else:
            vals.append(float(t))
            i += 1
    return vals


def ngspice_data(d: str, raw: str, plots: Sequence[str]) -> Dict[str, Dict[str, list]]:
    """{plot: {vector: values}} by `display` and `print all` per plot: a table keeps the vectors'
    case in its header; one-point vectors print as `name = value` lines in display order."""
    control = []
    for p in plots:
        control += ["echo ==plot %s" % p, "setplot %s" % p, "display", "echo ==print", "print all"]
    out = run_ngspice(d, raw, control, "print")
    data: Dict[str, Dict[str, list]] = {}
    for sect in out.split("==plot ")[1:]:
        name, rest = sect.split("\n", 1)
        shown, printed = rest.split("==print", 1)
        disp = [m.group(1) for m in re.finditer(r"^\s{4}(\S+)\s+:\s", shown, re.M)]
        vecs: Dict[str, list] = {}
        lines = printed.splitlines()
        header = next((k for k, ln in enumerate(lines) if ln.startswith("Index")), None)
        if header is not None:
            names = lines[header].split()[1:]
            cols: List[list] = [[] for _ in names]
            for ln in lines[header + 1:]:
                toks = ln.split()
                if not toks or not toks[0].isdigit():
                    continue
                for c, v in zip(cols, _numbers(toks[1:])):
                    c.append(v)
            vecs = dict(zip(names, cols))
        else:
            vals = [ln.split(" = ", 1)[1] for ln in lines if " = " in ln]
            for n, v in zip(disp, vals):
                vecs[n] = _numbers(v.replace(",", ", ").split())
        data[name.strip()] = vecs
    return data


def analysis_of(description: str) -> str:
    """The analysis name in a plot's description: DC Analysis `op1' -> op1 (§8.3, §8.5)."""
    m = re.search(r"`([^']*)'", description)
    return m.group(1) if m else description


def vacask_rawfile_module():
    """VACASK's python/rawfile.py (needs_vacask_rawread), loaded under its own module name."""
    path = os.path.join(vacask_src(), "python", "rawfile.py")
    spec = importlib.util.spec_from_file_location("vacask_python_rawfile", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def split_plots(raw: str, outdir: str) -> List[str]:
    """One file per plot of a binary rawfile, <outdir>/plot<k>.raw: VACASK writes one plot per file,
    and its rawread keeps a single header dict for a whole file, so every plot of a multi-plot file
    would get the last plot's names (its arrays are right).  The split reads only the header lines
    it needs to find each plot's end (No. Variables, No. Points, Flags)."""
    with open(raw, "rb") as fh:
        data = fh.read()
    out, pos, k = [], 0, 0
    while pos < len(data):
        start, nvars, npoints, cplx = pos, None, None, False
        while True:
            end = data.index(b"\n", pos)
            line = data[pos:end]
            pos = end + 1
            key, _, val = line.partition(b":")
            key = key.strip().lower()
            if key == b"no. variables":
                nvars = int(val)
            elif key == b"no. points":
                npoints = int(val)
            elif key == b"flags":
                cplx = b"complex" in val
            elif key == b"binary":
                break
        pos += npoints * nvars * (16 if cplx else 8)
        path = os.path.join(outdir, "plot%d.raw" % k)
        with open(path, "wb") as fh:
            fh.write(data[start:pos])
        out.append(path)
        k += 1
    return out


# -- the base class ----------------------------------------------------------------------------------------------

class CliCase(SpectreCase):
    """The decks' runs: a copy of the deck directory per engine, vamos -spectre in it."""

    def each_engine(self, engines: Sequence[str] = T.ENGINE_NAMES) -> List[str]:
        avail = [e for e in engines if T.GATES[T.ENGINE_GATES[e]][0]()]
        if not avail:
            self.skipTest("; ".join(T.GATES[T.ENGINE_GATES[e]][1] for e in engines))
        return avail

    def run_case(self, case: str, engine: str, *args: str, env: Optional[Dict[str, str]] = None,
                 stdin: Optional[str] = None, label: str = "") -> Tuple[str, subprocess.CompletedProcess]:
        d = self.copy_case(os.path.join(E2E, case), "%s_%s%s" % (case, engine, label))
        extra = dict(env or {})
        unset = [v for v in T.UNSET_VARS if v not in extra]
        p = self.run_spectre(d, *args, engine=engine, expect_rc=None, env=extra, unset=unset,
                             stdin=os.path.join(d, stdin) if stdin else os.devnull)
        return d, p

    def start(self, d: str, args: Sequence[str], engine: str) -> Tuple[subprocess.Popen, List[str]]:
        """vamos -spectre in its own session with every signal at its default; its output collected
        by a thread."""
        cmd = [LAUNCHER, "-spectre"] + list(args) + ["--vamos-analog=" + engine]
        env = self.child_env()
        for v in T.UNSET_VARS:
            env.pop(v, None)
        p = subprocess.Popen(cmd, cwd=d, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, universal_newlines=True, errors="replace",
                             start_new_session=True, preexec_fn=_default_signals)
        lines: List[str] = []
        reader = threading.Thread(target=lambda: lines.extend(p.stdout), daemon=True)
        reader.start()
        p.reader = reader  # type: ignore[attr-defined]
        self.addCleanup(self._stop, p)
        return p, lines

    @staticmethod
    def _stop(p: subprocess.Popen) -> None:
        if p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            p.wait(timeout=30)

    def finish(self, p: subprocess.Popen, lines: List[str], timeout: float) -> Tuple[int, str]:
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._stop(p)
            self.fail("vamos still ran %d s after the signal:\n%s" % (timeout, "".join(lines)))
        p.reader.join(timeout=30)  # type: ignore[attr-defined]
        return rc, "".join(lines)

    @staticmethod
    def wait_for(d: str, name: str, p: subprocess.Popen, min_bytes: int = 1, timeout: float = 300) -> Optional[str]:
        """A file of the run directory (<d>/<deck>.run.*/<name>, §1) once it holds min_bytes; None when
        vamos ends first or the time runs out."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for path in glob.glob(os.path.join(d, "*.run.*", name)):
                try:
                    if os.path.getsize(path) >= min_bytes:
                        return path
                except OSError:
                    pass
            if p.poll() is not None:
                return None
            time.sleep(0.05)
        return None

    def assert_completes(self, out: str) -> None:
        self.assertTrue(T.TRAILER_OK.match(T.trailer(out)), "the trailer is %r:\n%s" % (T.trailer(out), out))
        self.assertEqual(T.TRAILER_OK.match(T.trailer(out)).group(1), "spectre")

    def assert_fatal(self, out: str) -> None:
        self.assertEqual(T.trailer(out), "spectre terminated prematurely due to fatal error.", out)


# -- e2e 11: exit status and signals ---------------------------------------------------------------------------

@needs_flow
class TestExitStatus(CliCase):
    CASE = "11_exit_status"

    def results(self, d: str, deck: str) -> str:
        return os.path.join(d, os.path.splitext(deck)[0] + ".raw")

    def check_failed_run(self, d: str, p, deck: str, failed: str, keys: List[str]) -> psfcheck.LogFile:
        """Status 1: the "completes" trailer, a message naming the failed analysis, the run directory
        kept, and the logFile listing exactly the analyses that finished (§2.7, §8.2, §8.7)."""
        out = p.stdout
        self.assertEqual(p.returncode, 1, out)
        self.assert_completes(out)
        self.assertTrue(failure_messages(out, failed), "no error or warning names %s:\n%s" % (failed, out))
        self.assertIn("run directory kept", out)
        log = psfcheck.check_logfile(os.path.join(self.results(d, deck), "logFile"))
        self.assertEqual(log.keys(), keys)
        return log

    def test_one_failing_analysis_among_good_ones(self):
        for engine in self.each_engine():
            with self.subTest(engine=engine):
                d, p = self.run_case(self.CASE, engine, "deck.scs")
                self.check_failed_run(d, p, "deck.scs", "op2", ["op1-dc", "op3-dc", "ac1-ac"])

    def test_a_failing_sweep_point(self):
        """The leaf before the failed point written; the parent lists the complete prefix (§8.2).  Since phase 2c
        the sweep starts at the nominal g=0.5 again (E126 fixed, R-tests-1): out = -g/(1-g) = -1 there; it started
        at 0.75 (out -3) to dodge E126 before."""
        for engine in self.each_engine():
            with self.subTest(engine=engine):
                d, p = self.run_case(self.CASE, engine, "sweep_fail.scs")
                log = self.check_failed_run(d, p, "sweep_fail.scs", "op1", ["sw_op1-sweep", "sw-000_op1-dc", "op9-dc"])
                self.assertEqual(len(log.children["sw_op1-sweep"]), 1)
                self.assertEqual(log.by_key["sw-000_op1-dc"].props.get("g"), 0.5)
                leaf = psfcheck.check(os.path.join(self.results(d, "sweep_fail.scs"), "sw-000_op1.dc"))
                self.assertAlmostEqual(leaf.value("out"), -1.0, places=9)

    def test_a_tran_that_aborts_mid_run(self):
        """Status 1, not 0 (E66): the engine's partial tran is a failure, neither written nor listed."""
        for engine in self.each_engine():
            with self.subTest(engine=engine):
                d, p = self.run_case(self.CASE, engine, "tran_abort.scs")
                self.check_failed_run(d, p, "tran_abort.scs", "tr1", ["op1-dc", "op2-dc"])
                self.assertFalse(os.path.exists(os.path.join(self.results(d, "tran_abort.scs"), "tr1.tran.tran")))

    @needs_vacask
    def test_a_vacask_bind_failure(self):
        """VACASK reports the bind failure and exits 0 with no rawfile (E28): status 1, not 0."""
        d, p = self.run_case(self.CASE, "vacask", "bind_fail.scs")
        self.check_failed_run(d, p, "bind_fail.scs", "sw1", ["op1-dc", "op2-dc"])

    def test_a_parse_error(self):
        """Status 2 before any analysis: the error names the line, no data file, the fatal trailer."""
        for engine in self.each_engine():
            with self.subTest(engine=engine):
                d, p = self.run_case(self.CASE, engine, "parse_error.scs")
                out = p.stdout
                self.assertEqual(p.returncode, 2, out)
                self.assert_fatal(out)
                self.assertTrue([b for s, b in T.parse_messages(out) if s == "error" and "parse_error.scs:4" in b], out)
                res = self.results(d, "parse_error.scs")
                data = [f for f in os.listdir(res) if T.is_psf_data(f)] if os.path.isdir(res) else []
                self.assertEqual(data, [])
                if os.path.isfile(os.path.join(res, "logFile")):
                    self.assertEqual(psfcheck.check_logfile(os.path.join(res, "logFile")).keys(), [])

    def test_sigint_during_a_long_tran(self):
        """Status 3: the engine is stopped, op1 and the partial tr1 are converted and listed, a
        warning names tr1, the fatal trailer (§2.7, §8.2, §8.7; E40, E72, E81)."""
        for engine in self.each_engine():
            with self.subTest(engine=engine):
                d = self.copy_case(os.path.join(E2E, self.CASE), "sigint_" + engine)
                p, lines = self.start(d, ["sigint.scs"], engine)
                raw = self.wait_for(d, "vamos_a2.raw", p, min_bytes=65536)
                if raw is None:
                    self._stop(p)
                    self.fail("the tran never got going (no <run>/vamos_a2.raw of 64 KiB):\n" + "".join(lines))
                os.kill(p.pid, signal.SIGINT)
                rc, out = self.finish(p, lines, 300)
                self.assertEqual(rc, 3, out)
                self.assert_fatal(out)
                self.assertTrue(failure_messages(out, "tr1", ("warning",)), "no warning names tr1:\n" + out)
                self.assertIn("run directory kept", out)
                res = os.path.join(d, "sigint.raw")
                self.assertEqual(sorted(os.listdir(res)), ["logFile", "op1.dc", "tr1.tran.tran"])
                self.assertEqual(psfcheck.check_logfile(os.path.join(res, "logFile")).keys(), ["op1-dc", "tr1-tran"])
                self.assertEqual(psfcheck.check(os.path.join(res, "op1.dc")).value("n40"), 0.0)
                tr = psfcheck.check(os.path.join(res, "tr1.tran.tran"))
                xs = tr.sweep_values()
                self.assertGreaterEqual(tr.points, 2)
                self.assertEqual(xs[0], 0.0)
                self.assertTrue(0.0 < xs[-1] < 10.0, xs[-1])
                self.assertEqual(tr.header["stop"], 10.0)                      # the planned stop (§8.3)

    def test_after_the_engine_is_ended_from_outside_a_first_signal_finishes_the_file(self):
        """R-run-9 (phase 2c): the engine killed from outside vamos during the tran (status 3, and vamos got no
        signal), then one SIGINT while the interrupted tr1 is converted: that is the user's first signal, so the
        file in hand is finished and listed (before: it counted as a second one, and tr1 was dropped)."""
        for engine in self.each_engine():
            with self.subTest(engine=engine):
                d = self.copy_case(os.path.join(E2E, self.CASE), "killconv_" + engine)
                p, lines = self.start(d, ["sigint.scs"], engine)
                if self.wait_for(d, "vamos_a2.raw", p, min_bytes=8 << 20) is None:
                    self._stop(p)
                    self.fail("the tran never got going (no <run>/vamos_a2.raw of 8 MiB):\n" + "".join(lines))
                name = "vacask" if engine == "vacask" else "Xyce"
                pids = [pid for pid, _cmd in _engine_procs(d, name)]
                self.assertTrue(pids, "no %s process under %s" % (name, d))
                for pid in pids:
                    os.kill(pid, signal.SIGKILL)                   # from outside vamos
                tmp = os.path.join(d, "sigint.raw", "tr1.tran.tran.vamos-tmp")
                deadline = time.time() + 300
                while not os.path.exists(tmp) and p.poll() is None and time.time() < deadline:
                    time.sleep(0.01)
                if not os.path.exists(tmp):
                    self._stop(p)
                    self.fail("tr1's conversion was never seen (%s):\n%s" % (tmp, "".join(lines)))
                os.kill(p.pid, signal.SIGINT)
                rc, out = self.finish(p, lines, 300)
                self.assertEqual(rc, 3, out)
                self.assertIn("SIGKILL from outside vamos", out)
                res = os.path.join(d, "sigint.raw")
                self.assertEqual(psfcheck.check_logfile(os.path.join(res, "logFile")).keys(), ["op1-dc", "tr1-tran"],
                                 out)
                self.assertGreaterEqual(psfcheck.check(os.path.join(res, "tr1.tran.tran")).points, 2)
                self.assertIn("stopping after `tr1' is written; a second interrupt drops it", out)       # D3

    def test_sigusr1_prints_a_status_line_and_the_run_continues(self):
        for engine in self.each_engine():
            with self.subTest(engine=engine):
                d = self.copy_case(os.path.join(E2E, self.CASE), "usr1_" + engine)
                p, lines = self.start(d, ["usr1.scs"], engine)
                raw = self.wait_for(d, "vamos_a2.raw", p, min_bytes=1)
                if raw is None:
                    self._stop(p)
                    self.fail("the tran never started (no <run>/vamos_a2.raw):\n" + "".join(lines))
                os.kill(p.pid, signal.SIGUSR1)
                rc, out = self.finish(p, lines, 900)
                self.assertEqual(rc, 0, out)
                status = [ln for ln in out.splitlines() if ln.startswith("spectre status: ")]
                self.assertTrue(status, "no status line:\n" + out)
                self.assertIn("tr1", status[0])
                # R-tests-4 (phase 2c): the line printed while the engine runs the tran, not the conversion's
                # ("no analysis running, writing the results of `tr1'"), with the engine's progress
                self.assertIn("analysis `tr1' running", status[0])
                self.assertIn("time = " if engine == "vacask" else "Xyce deck", status[0])
                self.assert_completes(out)
                tr = psfcheck.check(os.path.join(d, "usr1.raw", "tr1.tran.tran"))
                self.assertAlmostEqual(tr.sweep_values()[-1], 0.5, delta=1e-12)


# -- phase 2c, D1: a screen that goes away (e2e 11's sigint.scs, e2e 14's x.scs) -----------------------------------

def _drain(fd: int, stop: threading.Event, into: List[bytes]) -> None:
    """Read a pty master until `stop` is set (select with a timeout, so the master can be closed after)."""
    while not stop.is_set():
        try:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if ready:
                into.append(os.read(fd, 65536))
        except OSError:
            return


@needs_flow
class TestScreenGone(CliCase):
    """R-run-1, R-run-2 (D1, interim choice (a)): the screen goes away and the run goes on without it,
    earning its status: 3 after a hangup or a Ctrl-C (the finished and the interrupted analyses converted),
    0 for a run that completes; nothing on stderr, no traceback file, never Python's exit status 120."""

    STATUS = "11_exit_status"
    QUICK = "14_defaults_stdin"

    def env(self) -> Dict[str, str]:
        env = self.child_env()
        for v in T.UNSET_VARS:
            env.pop(v, None)
        return env

    def tracebacks(self, d: str) -> List[str]:
        """The traceback files of a run: in its run directories, and in TMPDIR (child_env's, isolated)."""
        return (glob.glob(os.path.join(d, "*.run.*", "vamos-spectre.traceback"))
                + glob.glob(os.path.join(self.tmp, "tmp", "vamos-spectre-*.traceback")))

    def test_a_terminal_that_hangs_up(self):
        """vamos on a pty that is its controlling terminal; the master closed while the tran runs (SIGHUP,
        then EIO on every write to the terminal): status 3, op1 and the partial tr1 listed (before: 120)."""
        import fcntl
        import termios
        for engine in self.each_engine():
            with self.subTest(engine=engine):
                d = self.copy_case(os.path.join(E2E, self.STATUS), "hangup_" + engine)
                master, slave = os.openpty()

                def pre() -> None:
                    _default_signals()
                    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
                p = subprocess.Popen([LAUNCHER, "-spectre", "sigint.scs", "--vamos-analog=" + engine], cwd=d,
                                     env=self.env(), stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                                     preexec_fn=pre)
                os.close(slave)
                self.addCleanup(self._stop, p)
                stop = threading.Event()
                screen: List[bytes] = []
                reader = threading.Thread(target=_drain, args=(master, stop, screen), daemon=True)
                reader.start()
                try:
                    raw = self.wait_for(d, "vamos_a2.raw", p, min_bytes=65536)
                finally:
                    stop.set()
                    reader.join(timeout=30)
                    os.close(master)                           # the hangup
                text = b"".join(screen).decode("utf-8", "replace")
                if raw is None:
                    self.fail("the tran never got going (no <run>/vamos_a2.raw of 64 KiB):\n" + text)
                rc = p.wait(timeout=300)
                self.assertEqual(rc, 3, text)
                res = os.path.join(d, "sigint.raw")
                self.assertEqual(psfcheck.check_logfile(os.path.join(res, "logFile")).keys(), ["op1-dc", "tr1-tran"])
                self.assertEqual(self.tracebacks(d), [])

    def test_ctrl_c_under_tee(self):
        """`spectre sigint.scs | tee screen.log` and a Ctrl-C (SIGINT to the pipeline's process group): tee dies,
        vamos converts without its screen: status 3, op1 and the partial tr1 listed, stderr empty (before: 120,
        'Exception ignored ... BrokenPipeError' on stderr, nothing converted)."""
        for engine in self.each_engine():
            with self.subTest(engine=engine):
                d = self.copy_case(os.path.join(E2E, self.STATUS), "tee_" + engine)
                tee = subprocess.Popen(["tee", "screen.log"], cwd=d, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL,
                                       preexec_fn=lambda: (_default_signals(), os.setpgid(0, 0)))
                self.addCleanup(self._stop, tee)
                p = subprocess.Popen([LAUNCHER, "-spectre", "sigint.scs", "--vamos-analog=" + engine], cwd=d,
                                     env=self.env(), stdin=subprocess.DEVNULL, stdout=tee.stdin, stderr=subprocess.PIPE,
                                     universal_newlines=True, errors="replace",
                                     preexec_fn=lambda: (_default_signals(), os.setpgid(0, tee.pid)))
                tee.stdin.close()                              # vamos alone writes into the pipe
                self.addCleanup(self._stop, p)
                errs: List[str] = []
                reader = threading.Thread(target=lambda: errs.extend(p.stderr), daemon=True)
                reader.start()
                raw = self.wait_for(d, "vamos_a2.raw", p, min_bytes=65536)
                if raw is None:
                    self.fail("the tran never got going (no <run>/vamos_a2.raw of 64 KiB)")
                os.killpg(tee.pid, signal.SIGINT)              # Ctrl-C: the whole foreground process group
                rc = p.wait(timeout=300)
                reader.join(timeout=30)
                self.assertEqual(tee.wait(timeout=60), -signal.SIGINT)
                self.assertEqual((rc, "".join(errs)), (3, ""))
                res = os.path.join(d, "sigint.raw")
                self.assertEqual(psfcheck.check_logfile(os.path.join(res, "logFile")).keys(), ["op1-dc", "tr1-tran"])
                self.assertEqual(self.tracebacks(d), [])

    def test_a_closed_stdout(self):
        """`spectre x.scs >&-`: status 0 and the results (before: exit 2, nothing simulated, two traceback
        files in TMPDIR)."""
        for engine in self.each_engine():
            with self.subTest(engine=engine):
                d = self.copy_case(os.path.join(E2E, self.QUICK), "closed_" + engine)
                p = subprocess.run([LAUNCHER, "-spectre", "x.scs", "--vamos-analog=" + engine], cwd=d, env=self.env(),
                                   stdin=subprocess.DEVNULL, stderr=subprocess.PIPE, universal_newlines=True,
                                   errors="replace", timeout=600, preexec_fn=lambda: (_default_signals(), os.close(1)))
                self.assertEqual((p.returncode, p.stderr), (0, ""))
                self.assertEqual(psfcheck.check_logfile(os.path.join(d, "x.raw", "logFile")).keys(),
                                 ["op1-dc", "tr1-tran"])
                self.assertEqual(glob.glob(os.path.join(d, "x.run.*")), [])
                self.assertEqual(self.tracebacks(d), [])

    def test_a_reader_that_stops_after_the_first_line(self):
        """`spectre x.scs | head -1` (D1 (a)): the run goes on without the screen: status 0, the results, no
        run directory left, stderr empty, no traceback file (before: 120 and nothing converted)."""
        for engine in self.each_engine():
            with self.subTest(engine=engine):
                d = self.copy_case(os.path.join(E2E, self.QUICK), "head_" + engine)
                p = subprocess.Popen([LAUNCHER, "-spectre", "x.scs", "--vamos-analog=" + engine], cwd=d, env=self.env(),
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     universal_newlines=True, errors="replace", start_new_session=True,
                                     preexec_fn=_default_signals)
                self.addCleanup(self._stop, p)
                first = p.stdout.readline()
                p.stdout.close()                               # head -1 is done: the pipe has no reader
                rc = p.wait(timeout=600)
                err = p.stderr.read()
                p.stderr.close()
                self.assertTrue(first.startswith("vamos "), first)
                self.assertEqual((rc, err), (0, ""))
                self.assertEqual(psfcheck.check_logfile(os.path.join(d, "x.raw", "logFile")).keys(),
                                 ["op1-dc", "tr1-tran"])
                self.assertEqual(glob.glob(os.path.join(d, "x.run.*")), [])
                self.assertEqual(self.tracebacks(d), [])


# -- e2e 12: the ADE command line (§9) --------------------------------------------------------------------------

@needs_flow
class TestAde(CliCase):
    CASE = "12_ade"
    NODES = ("net<3>", "out", "I2.Mid")

    def test_the_ade_command_line(self):
        exp = self.load_expect(os.path.join(E2E, self.CASE, "expect.json"))
        for engine in self.each_engine():
            with self.subTest(engine=engine):
                d, p = self.run_case(self.CASE, engine, *exp["argv"])
                out = p.stdout
                self.assertEqual(p.returncode, 0, out)
                psf = os.path.join(d, "psf")
                self.assertEqual(sorted(os.listdir(psf)), ["artistLogFile", "logFile", "spectre.out", "tran.tran.tran"])
                with open(os.path.join(E2E, self.CASE, "psf", "artistLogFile"), encoding="utf-8") as a, \
                        open(os.path.join(psf, "artistLogFile"), encoding="utf-8") as b:
                    self.assertEqual(a.read(), b.read(), "ADE's file was touched")
                self.assertEqual(psfcheck.check_logfile(os.path.join(psf, "logFile"), escchars=True).keys(), ["tran-tran"])
                with open(os.path.join(psf, "spectre.out"), encoding="utf-8") as fh:
                    log = fh.read()
                self.assert_completes(log)
                self.assertEqual(T.trailer(log), T.trailer(out))                # +log: the screen and the file
                self.assertEqual(log.splitlines()[0], out.splitlines()[0])       # the provenance header
                for name, final in (("spectre.ic", False), ("spectre.fc", True)):
                    heads, vals = read_state(os.path.join(d, name))
                    self.assertEqual(heads[0], "# State file generated by Spectre from circuit file 'input.scs'")
                    self.assertRegex(heads[1], r"^# during 'tran' at \d{1,2}:\d\d:\d\d [AP]M, [a-z]{3} \d{1,2}, \d{4}\.$")
                    names = decoded(vals)
                    for node in self.NODES:
                        self.assertIn(node, names, "%s: no %s (%s)" % (name, node, sorted(vals)))
                    got = {n: vals[names[n]] for n in self.NODES}
                    if not final:
                        for n in self.NODES:
                            self.assertAlmostEqual(got[n], 0.2, delta=1e-6)          # the t = 0 state
                    else:
                        self.assertAlmostEqual(got["net<3>"], 0.2, delta=1e-3)
                        self.assertTrue(math.isclose(got["out"], 0.3215447023894986, rel_tol=5e-3), got)
                        self.assertTrue(math.isclose(got["I2.Mid"], 0.2751194136656607, rel_tol=5e-3), got)


# -- e2e 13: nutascii and nutbin (§8.5) -------------------------------------------------------------------------

@needs_flow
class TestNutmeg(CliCase):
    CASE = "13_nutmeg"
    PLOTS = ["op1", "swdc", "ac1", "tr1", "nz1", "sw-000_op2", "sw-001_op2"]

    def run_format(self, engine: str, fmt: str, deck: str = "deck.scs") -> Tuple[str, str]:
        d, p = self.run_case(self.CASE, engine, deck, "-format", fmt, label="_%s_%s" % (os.path.splitext(deck)[0], fmt))
        self.assertEqual(p.returncode, 0, p.stdout)
        raw = os.path.join(d, os.path.splitext(deck)[0] + ".raw")
        self.assertTrue(os.path.isfile(raw), "no results file %s:\n%s" % (raw, p.stdout))
        self.assertEqual([f for _, _, fs in os.walk(d) for f in fs if f == "logFile"], [], "a nutmeg run wrote a logFile")
        return d, raw

    def check_values(self, vec: Dict[str, Dict[str, list]], name_of: Dict[str, str]) -> None:
        """The deck's values (the divider of R1 = R2 = 1k from in = 1 V, §11.2 13): per plot by analysis."""
        def v(plot, vector):
            return vec[name_of[plot]][vector]
        self.assertAlmostEqual(v("op1", "out")[0], 0.5, delta=1e-6)
        self.assertAlmostEqual(v("op1", "in")[0], 1.0, delta=1e-6)
        self.assertEqual(len(v("swdc", "out")), 3)
        for got, want in zip(v("swdc", "out"), (0.0, 0.5, 1.0)):
            self.assertAlmostEqual(got, want, delta=1e-6)
        ac = complex(v("ac1", "out")[0])
        self.assertAlmostEqual(abs(ac), 0.5, delta=1e-5)
        self.assertEqual(len(v("ac1", "out")), 4)
        tr = v("tr1", "out")
        self.assertAlmostEqual(max(tr), 0.75, delta=5e-3)
        self.assertAlmostEqual(min(tr), 0.25, delta=5e-3)
        self.assertTrue(math.isclose(v("nz1", "out")[0], 2.879e-9, rel_tol=2e-3), v("nz1", "out"))
        self.assertAlmostEqual(v("sw-000_op2", "out")[0], 0.5, delta=1e-6)
        self.assertAlmostEqual(v("sw-001_op2", "out")[0], 0.75, delta=1e-6)

    @needs_ngspice
    def test_ngspice_loads_nutascii_and_nutbin(self):
        for engine in self.each_engine():
            for fmt in ("nutascii", "nutbin"):
                with self.subTest(engine=engine, format=fmt):
                    d, raw = self.run_format(engine, fmt)
                    plots = ngspice_plots(d, raw)
                    self.assertEqual([analysis_of(desc) for _, desc in plots], self.PLOTS, plots)
                    self.assertEqual(plots[0][1], "DC Analysis `op1'")             # §8.5: the PSF description
                    data = ngspice_data(d, raw, [n for n, _ in plots])
                    name_of = {analysis_of(desc): n for n, desc in plots}
                    self.check_values(data, name_of)

    @needs_ngspice
    @needs_vacask
    def test_ngspice_keeps_vectors_A_and_a_apart(self):
        d, raw = self.run_format("vacask", "nutascii", "case.scs")
        plots = ngspice_plots(d, raw)
        self.assertEqual([analysis_of(desc) for _, desc in plots], ["op1", "tr1"])
        data = ngspice_data(d, raw, [n for n, _ in plots])
        op, tr = data[plots[0][0]], data[plots[1][0]]
        self.assertEqual((op["A"][0], op["a"][0]), (1.0, 0.75))
        self.assertEqual((tr["A"][-1], tr["a"][-1]), (1.0, 0.75))

    @needs_vacask_rawread
    def test_vacask_rawread_reads_nutbin(self):
        """VACASK's rawread reads every plot (one file per plot, see split_plots): the names, the
        point counts the header declares and the values."""
        rf = vacask_rawfile_module()
        for engine in self.each_engine():
            with self.subTest(engine=engine):
                d, raw = self.run_format(engine, "nutbin")
                whole = rf.rawread(raw)
                self.assertEqual(len(whole.arrs), len(self.PLOTS))
                plots = []
                for k, path in enumerate(split_plots(raw, d)):
                    one = rf.rawread(path)
                    self.assertEqual(len(one.arrs), 1, path)
                    h, arr = one.arrs[0]
                    self.assertEqual(arr.shape, (h["no. points"], h["no. variables"]))
                    self.assertTrue((arr == whole.arrs[k][1]).all(), "plot %d reads differently alone" % k)
                    plots.append((h["plotname"].decode("ascii", "replace"), list(h["varnames"]), arr))
                self.assertEqual([analysis_of(n) for n, _, _ in plots], self.PLOTS)
                vec = {}
                for n, names, arr in plots:
                    vec[n] = {name: [arr[i, k] for i in range(arr.shape[0])] for k, name in enumerate(names)}
                self.check_values(vec, {analysis_of(n): n for n, _, _ in plots})


# -- e2e 14: the defaults variable, -outdir, stdin (§2.2, §2.5, §1) ---------------------------------------------

@needs_flow
class TestDefaultsStdin(CliCase):
    CASE = "14_defaults_stdin"
    DEFAULTS = {"SPECTRE_DEFAULTS": "+log %C:r.out"}

    def assert_log(self, path: str, screen: str) -> None:
        self.assertTrue(os.path.isfile(path), "no log %s:\n%s" % (path, screen))
        with open(path, encoding="utf-8") as fh:
            log = fh.read()
        self.assert_completes(log)
        self.assertEqual(T.trailer(log), T.trailer(screen))                    # +log: both destinations
        self.assertEqual(log.splitlines()[0], screen.splitlines()[0])

    def test_spectre_defaults_writes_x_out(self):
        for engine in self.each_engine():
            with self.subTest(engine=engine):
                d, p = self.run_case(self.CASE, engine, "x.scs", env=self.DEFAULTS)
                self.assertEqual(p.returncode, 0, p.stdout)
                self.assert_log(os.path.join(d, "x.out"), p.stdout)
                self.assertTrue(os.path.isdir(os.path.join(d, "x.raw")))
                self.assertEqual(glob.glob(os.path.join(d, "x.run.*")), [])     # removed after status 0 (§1)

    def test_outdir(self):
        """-outdir o moves the default results x.raw into o (§2.5)."""
        for engine in self.each_engine():
            with self.subTest(engine=engine):
                d, p = self.run_case(self.CASE, engine, "x.scs", "-outdir", "o")
                self.assertEqual(p.returncode, 0, p.stdout)
                self.assertFalse(os.path.exists(os.path.join(d, "x.raw")))
                log = psfcheck.check_logfile(os.path.join(d, "o", "x.raw", "logFile"))
                self.assertEqual(log.keys(), ["op1-dc", "tr1-tran"])
                self.assertEqual(glob.glob(os.path.join(d, "o", "x.run.*")) + glob.glob(os.path.join(d, "x.run.*")), [])

    def test_a_netlist_on_stdin(self):
        """%C is stdin: stdin.raw, the log stdin.out from the defaults' %C:r.out, and the copy of the
        input in the run directory, <run>/stdin (§1 step 3), kept by --vamos-keep."""
        with open(os.path.join(E2E, self.CASE, "x.scs"), encoding="utf-8") as fh:
            text = fh.read()
        for engine in self.each_engine():
            with self.subTest(engine=engine):
                d, p = self.run_case(self.CASE, engine, "--vamos-keep", env=self.DEFAULTS, stdin="x.scs")
                self.assertEqual(p.returncode, 0, p.stdout)
                self.assertIn("`stdin'", p.stdout)
                self.assert_log(os.path.join(d, "stdin.out"), p.stdout)
                log = psfcheck.check_logfile(os.path.join(d, "stdin.raw", "logFile"))
                self.assertEqual(log.keys(), ["op1-dc", "tr1-tran"])
                runs = glob.glob(os.path.join(d, "stdin.run.*"))
                self.assertEqual(len(runs), 1, runs)
                with open(os.path.join(runs[0], "stdin"), encoding="utf-8") as fh:
                    self.assertEqual(fh.read(), text)
                self.assertFalse(os.path.exists(os.path.join(d, "x.raw")))


if __name__ == "__main__":
    unittest.main()
