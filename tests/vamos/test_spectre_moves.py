"""The spectre personality's phase-0 code moves (docs/VAMOS_SPECTRE_DESIGN.md §2.7, §4.6, §10, §12).

    rawfile: Raw.exact; scan and fix_scanned, moved from backends/cosim.py, which re-exports them
             as scan_raw and _fix_points; fix_points streaming through them; iter_rows; the
             read_prn stub (S4 implements it)
    proc:    Interrupts, child_setup and signal_name, moved from backends/nvc.py, which imports
             them back and keeps its own INTERRUPT_GRACE
    engines: choose_engine (moved from ams/flow.py, which wraps it), env_for(nvc_libdir=None),
             tool_rows (ams/flow.compile_tools is built on it, its rows unchanged)

    cd tests/vamos && python3 -m unittest test_spectre_moves -v

Both legs (Cygwin Python 3.9, WSL Python 3.14).  The tests that signal a real child run on
Linux only, where a signal interrupts a blocked wait at once (test_vamos_run.TestStreamSignals'
rule); the handler-table tests run everywhere.
"""

import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import unittest

from vamos_testlib import LINUX, ROOT, TempDir, fixture

from vamos import proc  # noqa: E402
from vamos.ams import engines, flow  # noqa: E402
from vamos.ams.config import AmsConfig, Choose  # noqa: E402
from vamos.backends import cosim, nvc  # noqa: E402
from vamos.job import Job  # noqa: E402
from vamos.netlist import rawfile  # noqa: E402

POSIX_SIGNALS = all(hasattr(signal, n) for n in ("SIGUSR1", "SIGUSR2", "SIGTERM", "SIGHUP")) and \
    hasattr(os, "kill")


def fx(name):
    return fixture("netlist", name)


FIXTURES = ("raw_vacask_tran.raw", "raw_vacask_tran_ascii.raw", "raw_xyce_tran.raw",
            "raw_xyce_tran_ascii.raw", "raw_vacask_ac.raw", "raw_vacask_ac_ascii.raw",
            "raw_xyce_ac.raw", "raw_vacask_op.raw")

# files that are not rawfiles, or in a layout vamos does not read (test_netlist_rawfile's cases)
BAD = {"empty.raw": b"", "text.raw": b"hello world\n",
       "lt.raw": b"Title: x\nFlags: real forward\nNo. Variables: 1\nNo. Points: 0\n"
                 b"Variables:\n\t0\ttime\ttime\nBinary:\n",
       "nodata.raw": b"Title: x\nNo. Variables: 1\nVariables:\n\t0\ttime\ttime\n",
       "vars.raw": b"Title: x\nNo. Variables: 2\nVariables:\n\t0\ttime\ttime\nValues:\n",
       "novars.raw": b"Title: x\nNo. Variables: 1\nBinary:\n",
       "junk.raw": b"Title: x\nNo. Variables: 1\nVariables:\n\t0\ttime\ttime\nValues:\n"
                   b"0\t1.0\nbad\n"}


def variants():
    """(name, bytes): every rawfile fixture in the shapes the point-count repair meets: the
    shapes test_vamos_run.TestCheckRaw covers (a blank, wrong, small, narrow or missing
    count, two plots, a truncated last point, CRLF), plus a count too long for its field, a
    count field with no space after the colon, two plots with a blank first count, and CRLF
    with a blank count."""
    out = []
    for name in FIXTURES:
        with open(fx(name), "rb") as fh:
            data = fh.read()
        out.append((name, data))
        s = data.index(b"No. Points: ") + len(b"No. Points: ")
        e = data.index(b"\n", s)
        for tag, val in (("blank", b""), ("wrong99", b"99"), ("small12", b"12"),
                         ("big", b"123456789012345678901")):
            out.append((name + ":" + tag, data[:s] + val.ljust(e - s) + data[e:]))
        head = data[:s - len(b"No. Points: ")]
        out.append((name + ":narrow", head + b"No. Points: 5" + data[e:]))
        out.append((name + ":narrow_nospace", head + b"No. Points:5" + data[e:]))
        ls = data.index(b"No. Points:")
        out.append((name + ":nocount", data[:ls] + data[data.index(b"\n", ls) + 1:]))
        second = data.replace(b"Transient Analysis", b"Second")
        out.append((name + ":two", data + second))
        out.append((name + ":two_blank", data[:s] + b"".ljust(e - s) + data[e:] + second))
        if b"Binary:" in data:
            out.append((name + ":truncated", data[:-12]))
        else:
            out.append((name + ":truncated", data[:data.rindex(b"\t")]))
            out.append((name + ":crlf", data.replace(b"\n", b"\r\n")))
            out.append((name + ":crlf_blank",
                        (data[:s] + b"".ljust(e - s) + data[e:]).replace(b"\n", b"\r\n")))
    return out


def old_fix_points(path):
    """rawfile.fix_points as it was before phase 0 (the whole file read, then rawfile._parse,
    which stays): the oracle the streaming fix_points must match, result and bytes."""
    with open(path, "rb") as fh:
        data = fh.read()
    if not data.strip():
        raise rawfile.RawError("%s: empty rawfile" % path)
    edits = []
    for plot in rawfile._parse(data, path):
        n = len(plot.raw.points)
        if plot.points_field is None:
            edits.append((plot.nvars_line_end, plot.nvars_line_end, b"No. Points: %d\n" % n))
            continue
        if plot.raw.declared_points == n:
            continue
        a, b, spaced = plot.points_field
        digits = b"%d" % n
        if len(digits) <= b - a:
            edits.append((a, b, digits.ljust(b - a)))
        else:
            edits.append((a, b, digits if spaced else b" " + digits))
    if not edits:
        return False
    out = bytearray()
    prev = 0
    for s, e, rep in sorted(edits):
        out += data[prev:s]
        out += rep
        prev = e
    out += data[prev:]
    with open(path, "wb") as fh:
        fh.write(bytes(out))
    return True


def blank(path, value=b""):
    """Rewrite the No. Points: value field, keeping its width (Xyce's paused state)."""
    with open(path, "rb") as fh:
        data = fh.read()
    start = data.index(b"No. Points: ") + len(b"No. Points: ")
    end = data.index(b"\n", start)
    with open(path, "wb") as fh:
        fh.write(data[:start] + value.ljust(end - start) + data[end:])


# -- rawfile.Raw.exact -----------------------------------------------------------------------

class TestRawExact(unittest.TestCase):
    def test_exact_is_case_sensitive_and_unaliased(self):
        """VACASK keeps case: nodes A and a are two columns; index('A') is ambiguous (E84),
        exact('A') is column 1 and exact('a') column 2."""
        r = rawfile.Raw()
        r.variables = [("time", "notype"), ("A", "notype"), ("a", "notype"),
                       ("v1:flow(br)", "notype"), ("x1:n", "notype")]
        r.points = [(0.0, 1.0, 2.0, 3.0, 4.0)]
        self.assertEqual([r.exact(n) for n in ("time", "A", "a", "v1:flow(br)", "x1:n")],
                         [0, 1, 2, 3, 4])
        with self.assertRaises(KeyError) as cm:
            r.index("A")
        self.assertIn("several", str(cm.exception))
        for miss in ("V(A)", "v(a)", "TIME", "Time", "v1", "i(v1)", " a", "a ", "x1.n", "X1:N", ""):
            with self.subTest(name=miss):
                with self.assertRaises(KeyError) as cm:
                    r.exact(miss)
                self.assertIn(repr(miss), str(cm.exception))
        # index() keeps its aliasing for vcs-ams
        self.assertEqual((r.index("i(v1)"), r.index("v(x1.n)"), r.index("TIME")), (3, 4, 0))

    def test_exact_on_the_engine_fixtures(self):
        x, v = rawfile.read(fx("raw_xyce_tran.raw")), rawfile.read(fx("raw_vacask_tran.raw"))
        self.assertEqual((x.exact("TIME"), x.exact("V(B)"), x.exact("I(V1)"), x.exact("V(X1:N)")),
                         (0, 2, 3, 4))
        self.assertEqual((v.exact("time"), v.exact("b"), v.exact("v1:flow(br)"), v.exact("x1:n")),
                         (0, 2, 3, 4))
        for r, miss in ((x, "v(b)"), (x, "time"), (v, "B"), (v, "i(v1)")):
            with self.assertRaises(KeyError):
                r.exact(miss)
        self.assertEqual(x.column("V(B)"), [p[x.exact("V(B)")] for p in x.points])


# -- the scan, moved from cosim ------------------------------------------------------------

class TestScanMoved(TempDir):
    def write(self, name, data):
        p = os.path.join(self.tmp, name.replace(":", "_"))
        with open(p, "wb") as fh:
            fh.write(data)
        return p

    def test_cosim_reexports_the_moved_functions(self):
        """The HSPICE-route regression of §11.1: cosim.scan_raw and cosim._fix_points are the
        moved functions, not copies; check_raw stays in cosim; netlist never imports a backend."""
        self.assertIs(cosim.scan_raw, rawfile.scan)
        self.assertIs(cosim._fix_points, rawfile.fix_scanned)
        self.assertIs(cosim.RawPlot, rawfile.RawPlot)
        self.assertEqual(cosim.check_raw.__module__, cosim.__name__)
        for fn in (rawfile.scan, rawfile.fix_scanned, rawfile.fix_points, rawfile.iter_rows,
                   rawfile.read_prn):
            self.assertEqual(fn.__module__, rawfile.__name__, fn)
        with open(rawfile.__file__) as fh:
            src = fh.read()
        self.assertNotIn("vamos.backends", src)
        self.assertNotIn("vamos.ams", src)

    def test_scan_counts_like_read(self):
        for name, data in variants():
            with self.subTest(case=name):
                p = self.write(name, data)
                plots = rawfile.scan(p)
                want = rawfile.read_all(p)
                self.assertEqual([x.n for x in plots], [len(r.points) for r in want])
                self.assertEqual([x.declared for x in plots], [r.declared_points for r in want])
                for x, r in zip(plots, want):
                    t = [v[0].real if r.complex else v[0] for v in r.points]
                    self.assertEqual((x.first, x.last), (t[0], t[-1]) if t else (None, None))
                    self.assertEqual(x.time_scale, r.variables[0][0].lower() == "time" or
                                     r.variables[0][1].lower() == "time")
                self.assertEqual([x.next_start < 0 for x in plots], [False] * (len(plots) - 1) + [True])

    def test_fix_points_is_the_old_result_streamed(self):
        """fix_points = fix_scanned(path, scan(path)): the same result and the same bytes as
        the whole-file algorithm it replaces, on every shape; idempotent; no temp file left."""
        for name, data in variants():
            with self.subTest(case=name):
                a, b = self.write(name + "_a", data), self.write(name + "_b", data)
                changed = rawfile.fix_points(a)
                self.assertEqual(changed, old_fix_points(b))
                with open(a, "rb") as fa, open(b, "rb") as fb:
                    self.assertEqual(fa.read(), fb.read())
                self.assertFalse(os.path.exists(a + ".vamos-fix"))
                self.assertFalse(rawfile.fix_points(a))
                self.assertFalse(rawfile.fix_scanned(a, rawfile.scan(a)))
                for r in rawfile.read_all(a):
                    self.assertEqual(r.declared_points, len(r.points))

    def test_fix_points_errors_as_before(self):
        for name, data in BAD.items():
            with self.subTest(case=name):
                p = self.write(name, data)
                for fn in (rawfile.fix_points, rawfile.scan, old_fix_points, cosim.check_raw):
                    with self.assertRaises(rawfile.RawError):
                        fn(p)

    def test_fix_scanned_rewrites_only_when_the_count_does_not_fit(self):
        p = os.path.join(self.tmp, "x.raw")
        shutil.copy(fx("raw_xyce_tran.raw"), p)             # Xyce's 18-blank field: in place
        blank(p)
        size = os.path.getsize(p)
        plots = rawfile.scan(p)
        self.assertEqual((plots[0].declared, plots[0].n), (None, 37))
        self.assertTrue(rawfile.fix_scanned(p, plots))
        self.assertEqual(os.path.getsize(p), size)
        with open(p, "rb") as fh, open(fx("raw_xyce_tran.raw"), "rb") as orig:
            self.assertEqual(fh.read(), orig.read())
        q = os.path.join(self.tmp, "y.raw")                 # a one-digit field: a longer count rewrites
        with open(fx("raw_vacask_tran.raw"), "rb") as fh:
            data = fh.read()
        field = re.compile(rb"No\. Points: *60 *\n")
        self.assertEqual(len(field.findall(data)), 1)
        with open(q, "wb") as fh:
            fh.write(field.sub(b"No. Points: 6\n", data))
        size = os.path.getsize(q)
        self.assertTrue(rawfile.fix_points(q))
        self.assertEqual(os.path.getsize(q), size + 1)
        self.assertFalse(os.path.exists(q + ".vamos-fix"))
        with open(q, "rb") as fh:
            self.assertEqual(fh.read(), field.sub(b"No. Points: 60\n", data))
        self.assertEqual(cosim.check_raw(q), (False, 1e-9))


# -- iter_rows -----------------------------------------------------------------------------

class TestIterRows(TempDir):
    def write(self, name, data):
        p = os.path.join(self.tmp, name.replace(":", "_"))
        with open(p, "wb") as fh:
            fh.write(data)
        return p

    def test_rows_match_read_all(self):
        """Every plot of every shape, every column selection: the values read_all gives
        (complex numbers in a complex plot), in the order asked, repeats included."""
        for name, data in variants():
            with self.subTest(case=name):
                p = self.write(name, data)
                raws = rawfile.read_all(p)
                for k, r in enumerate(raws):
                    nv = len(r.variables)
                    for cols in ([], list(range(nv)), list(reversed(range(nv))), [0, 0, nv - 1], [nv - 1]):
                        got = list(rawfile.iter_rows(p, k, cols))
                        want = [tuple(pt[c] for c in cols) for pt in r.points]
                        self.assertEqual(len(got), len(want), (k, cols))
                        # (repr: a wrong count over two binary plots reads header bytes as
                        # data, nan among them, in both readers alike; nan != nan)
                        self.assertEqual(repr(got), repr(want), (k, cols))
                        if r.complex and cols:
                            self.assertTrue(all(isinstance(v, complex) for row in got for v in row))

    def test_plot_and_column_errors(self):
        with open(fx("raw_vacask_tran.raw"), "rb") as fh:
            p = self.write("one.raw", fh.read())
        with self.assertRaises(rawfile.RawError) as cm:
            list(rawfile.iter_rows(p, 1, [0]))
        self.assertIn("no plot 1", str(cm.exception))
        for bad in (5, -1):
            with self.assertRaises(IndexError):
                list(rawfile.iter_rows(p, 0, [0, bad]))
        with open(fx("raw_xyce_tran.raw"), "rb") as fh:
            two = self.write("two.raw", fh.read() * 2)
        self.assertEqual(len(list(rawfile.iter_rows(two, 1, [0]))), 37)
        with self.assertRaises(rawfile.RawError):
            list(rawfile.iter_rows(two, 2, [0]))
        for name, data in BAD.items():
            with self.subTest(case=name):
                q = self.write(name, data)
                with self.assertRaises(rawfile.RawError):
                    list(rawfile.iter_rows(q, 0, [0]))

    def test_stopping_early_releases_the_file(self):
        """A consumer that stops leaves no mapping behind: the file can be repaired (rewritten
        in place) and removed at once, which a mapped file would refuse on Windows."""
        for name in ("raw_vacask_tran_ascii.raw", "raw_xyce_tran.raw"):
            with self.subTest(case=name):
                p = os.path.join(self.tmp, name)
                shutil.copy(fx(name), p)
                blank(p)
                it = rawfile.iter_rows(p, 0, [0, 1])
                first = next(it)
                it.close()
                self.assertEqual(first[0], 0.0)
                for row in rawfile.iter_rows(p, 0, [0]):
                    break
                self.assertTrue(rawfile.fix_points(p))
                self.assertEqual(len(list(rawfile.iter_rows(p, 0, [0]))), len(rawfile.read(p).points))
                os.remove(p)
                self.assertFalse(os.path.exists(p))

    def test_a_long_binary_plot(self):
        """A plot longer than any fixture, written like VACASK's: counted, streamed, the first
        and last rows right; iter_rows and scan agree with read_all."""
        import struct
        n, nv = 100000, 4
        rows = bytearray()
        for i in range(n):
            rows += struct.pack("<4d", i * 1e-9, float(i), -float(i), 1.0)
        head = (b"Title: long\nDate: now\nPlotname: Transient Analysis\nFlags: real\n"
                b"No. Variables: %d\nNo. Points: %d\nVariables:\n\t0\ttime\tnotype\n\t1\ta\tnotype\n"
                b"\t2\tb\tnotype\n\t3\tc\tnotype\nBinary:\n" % (nv, n))
        p = self.write("long.raw", head + bytes(rows))
        plots = rawfile.scan(p)
        self.assertEqual((plots[0].n, plots[0].declared, plots[0].first, plots[0].last),
                         (n, n, 0.0, (n - 1) * 1e-9))
        count, last = 0, None
        for row in rawfile.iter_rows(p, 0, [3, 0, 1]):
            if count == 0:
                self.assertEqual(row, (1.0, 0.0, 0.0))
            count, last = count + 1, row
        self.assertEqual((count, last), (n, (1.0, (n - 1) * 1e-9, float(n - 1))))
        r = rawfile.read(p)
        self.assertEqual(list(rawfile.iter_rows(p, 0, [2]))[-3:], [(v[2],) for v in r.points[-3:]])


# -- the read_prn stub ---------------------------------------------------------------------

class TestReadPrnStub(unittest.TestCase):
    def test_not_implemented_yet(self):
        with self.assertRaises(NotImplementedError) as cm:
            rawfile.read_prn("x.prn")
        self.assertIn("read_prn", str(cm.exception))


# -- proc.py ---------------------------------------------------------------------------------

class TestProc(TempDir):
    def test_nvc_imports_the_moved_machinery_back(self):
        self.assertIs(nvc.Interrupts, proc.Interrupts)
        self.assertIs(nvc.child_setup, proc.child_setup)
        self.assertIs(nvc.signal_name, proc.signal_name)
        self.assertIs(cosim.signal_name, proc.signal_name)
        self.assertFalse(hasattr(nvc, "_Interrupts"))
        self.assertFalse(hasattr(nvc, "_child_setup"))
        # nvc keeps its own grace (test_vamos_run sets nvc.INTERRUPT_GRACE): proc's is another name
        self.assertEqual((nvc.INTERRUPT_GRACE, proc.INTERRUPT_GRACE), (5.0, 5.0))
        saved = nvc.INTERRUPT_GRACE
        nvc.INTERRUPT_GRACE = 0.5
        try:
            self.assertEqual(proc.INTERRUPT_GRACE, 5.0)
        finally:
            nvc.INTERRUPT_GRACE = saved

    def test_stream_passes_the_nvc_grace_and_the_nvc_signals(self):
        """NvcBackend.stream makes Interrupts((INT, TERM, HUP), SIGINT, grace=nvc.INTERRUPT_GRACE)
        with the module variable read at the call (§2.7), and runs the child under child_setup."""
        made = []

        class Recording(proc.Interrupts):
            def __init__(self, *args, **kw):
                made.append((args, kw))
                super().__init__(*args, **kw)

        be = nvc.NvcBackend.__new__(nvc.NvcBackend)
        be.job, be.emit, be.workdir, be.waves = None, lambda s: None, self.tmp, None
        be.interrupted, be.killed = None, False
        out, err = [], []
        saved_cls, saved_grace = nvc.Interrupts, nvc.INTERRUPT_GRACE
        nvc.Interrupts, nvc.INTERRUPT_GRACE = Recording, 0.75
        try:
            rc, f = be.stream([sys.executable, "-c", "print('hello from the child')"],
                              dict(os.environ), self.tmp, out.append, err.append)
        finally:
            nvc.Interrupts, nvc.INTERRUPT_GRACE = saved_cls, saved_grace
        self.assertEqual((rc, out, err), (0, ["hello from the child"], []))
        self.assertEqual(made, [((nvc._HANDLED, signal.SIGINT), {"grace": 0.75})])
        self.assertEqual(nvc._HANDLED, tuple(getattr(signal, n) for n in ("SIGINT", "SIGTERM", "SIGHUP")
                                             if hasattr(signal, n)))
        self.assertEqual((be.interrupted, be.killed), (None, False))

    def test_signal_name(self):
        self.assertEqual(proc.signal_name(signal.SIGINT), "SIGINT")
        self.assertEqual(proc.signal_name(signal.SIGTERM), "SIGTERM")
        self.assertEqual(proc.signal_name(999999), "signal 999999")

    def test_attributes_and_defaults(self):
        intr = proc.Interrupts((signal.SIGINT, signal.SIGTERM), signal.SIGTERM)
        self.assertEqual((intr.handled, intr.forward, intr.flags, intr.grace),
                         ((signal.SIGINT, signal.SIGTERM), signal.SIGTERM, (), proc.INTERRUPT_GRACE))
        self.assertEqual((intr.signum, intr.killed, intr.proc, intr.take_flags()), (None, False, None, []))
        intr = proc.Interrupts([signal.SIGINT], signal.SIGINT, flags=[signal.SIGTERM], grace=0.25)
        self.assertEqual((intr.handled, intr.flags, intr.grace), ((signal.SIGINT,), (signal.SIGTERM,), 0.25))
        self.assertIsNone(proc.child_setup()) if not hasattr(os, "setpgid") else \
            self.assertTrue(callable(proc.child_setup()))

    @unittest.skipUnless(POSIX_SIGNALS, "POSIX signals")
    def test_handlers_installed_and_restored(self):
        """In the main thread, with the child never started: the handled signals get the
        forward handler, the flag signals the flag handler, SIGTSTP the stop handler, and
        every handler is restored on exit; an ignored signal stays ignored."""
        self.assertIs(threading.current_thread(), threading.main_thread())
        sigs = (signal.SIGINT, signal.SIGTERM, signal.SIGUSR1, signal.SIGUSR2, signal.SIGTSTP)
        inherited = {s: signal.getsignal(s) for s in sigs}
        for s in sigs:                      # a suite under nohup inherits SIG_IGN: not here
            if inherited[s] == signal.SIG_IGN:
                signal.signal(s, signal.SIG_DFL)
        try:
            before = [signal.getsignal(s) for s in sigs]
            signal.signal(signal.SIGUSR2, signal.SIG_IGN)
            intr = proc.Interrupts((signal.SIGINT, signal.SIGTERM), signal.SIGTERM,
                                   flags=(signal.SIGUSR1, signal.SIGUSR2))
            with intr:
                for s in (signal.SIGINT, signal.SIGTERM):
                    self.assertEqual(signal.getsignal(s), intr._on_signal, s)
                self.assertEqual(signal.getsignal(signal.SIGUSR1), intr._on_flag)
                self.assertEqual(signal.getsignal(signal.SIGUSR2), signal.SIG_IGN)     # stays ignored
                self.assertEqual(signal.getsignal(signal.SIGTSTP), intr._on_stop)
                if hasattr(signal, "SIGALRM"):
                    self.assertEqual(signal.getsignal(signal.SIGALRM), intr._on_alarm)
            after = [signal.getsignal(s) for s in sigs]
            self.assertEqual(after[:3] + after[4:], before[:3] + before[4:])
            self.assertEqual(after[3], signal.SIG_IGN)
            self.assertEqual((intr.signum, intr.killed), (None, False))
            # SIGTSTP listed among the handled signals is still the stop handler
            with proc.Interrupts((signal.SIGINT, signal.SIGTSTP), signal.SIGINT) as intr2:
                self.assertEqual(signal.getsignal(signal.SIGTSTP), intr2._on_stop)
                self.assertEqual(signal.getsignal(signal.SIGINT), intr2._on_signal)
            self.assertEqual(signal.getsignal(signal.SIGTSTP), before[4])
        finally:
            for s, h in inherited.items():
                signal.signal(s, h)

    @unittest.skipUnless(POSIX_SIGNALS, "POSIX signals")
    def test_flags_are_recorded_in_order_and_taken_once(self):
        inherited = {s: signal.getsignal(s) for s in (signal.SIGUSR1, signal.SIGUSR2)}
        for s, h in inherited.items():
            if h == signal.SIG_IGN:
                signal.signal(s, signal.SIG_DFL)
        try:
            with proc.Interrupts((signal.SIGTERM,), signal.SIGTERM,
                                 flags=(signal.SIGUSR1, signal.SIGUSR2)) as intr:
                for s in (signal.SIGUSR1, signal.SIGUSR2, signal.SIGUSR1):
                    os.kill(os.getpid(), s)
                    deadline = time.time() + 10
                    while time.time() < deadline and len(intr._flags) < 1:
                        time.sleep(0.01)
                    got = intr.take_flags()
                    self.assertEqual(got, [s])
                self.assertEqual(intr.take_flags(), [])
                self.assertEqual((intr.signum, intr.killed), (None, False))
        finally:
            for s, h in inherited.items():
                signal.signal(s, h)


@unittest.skipUnless(LINUX and POSIX_SIGNALS, "signals to a real child: Linux")
class TestProcChild(unittest.TestCase):
    """The moved machinery on a real child (the sleep and sh of the system), signalled through
    vamos's own pid, as test_vamos_run.TestStreamSignals does with nvc."""

    def spawn(self, *argv):
        p = subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, preexec_fn=proc.child_setup())
        self.addCleanup(self.reap, p)
        return p

    def reap(self, p):
        if p.poll() is None:
            p.kill()
            p.wait()

    def test_first_signal_is_forwarded_and_the_child_has_its_own_group(self):
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with self.subTest(signal=proc.signal_name(sig)):
                if signal.getsignal(sig) == signal.SIG_IGN:
                    self.skipTest("%s is ignored in this process" % proc.signal_name(sig))
                with proc.Interrupts((signal.SIGINT, signal.SIGTERM, signal.SIGHUP), signal.SIGTERM,
                                     grace=20) as intr:
                    p = self.spawn("sleep", "30")
                    intr.attach(p)
                    self.assertEqual(os.getpgid(p.pid), p.pid)          # its own process group
                    self.assertNotEqual(os.getpgid(p.pid), os.getpgid(0))
                    os.kill(os.getpid(), sig)
                    rc = p.wait(timeout=10)
                self.assertEqual(rc, -signal.SIGTERM)                   # forwarded as SIGTERM
                self.assertEqual((intr.signum, intr.killed), (sig, False))

    def test_grace_period_kills_a_stubborn_child(self):
        with proc.Interrupts((signal.SIGTERM,), signal.SIGTERM, grace=0.5) as intr:
            p = self.spawn("sh", "-c", 'trap "" TERM; sleep 30')
            intr.attach(p)
            time.sleep(0.2)                                             # the trap is in place
            t0 = time.time()
            os.kill(os.getpid(), signal.SIGTERM)
            rc = p.wait(timeout=10)
            took = time.time() - t0
        self.assertEqual((rc, intr.signum, intr.killed), (-signal.SIGKILL, signal.SIGTERM, True))
        self.assertLess(took, 5)

    def test_second_signal_kills(self):
        with proc.Interrupts((signal.SIGTERM, signal.SIGINT), signal.SIGTERM, grace=30) as intr:
            p = self.spawn("sh", "-c", 'trap "" TERM; sleep 30')
            intr.attach(p)
            time.sleep(0.2)
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(0.2)
            self.assertIsNone(p.poll())
            os.kill(os.getpid(), signal.SIGINT)
            rc = p.wait(timeout=10)
        self.assertEqual((rc, intr.signum, intr.killed), (-signal.SIGKILL, signal.SIGTERM, True))

    def test_a_signal_before_attach_is_passed_on_then(self):
        with proc.Interrupts((signal.SIGTERM,), signal.SIGTERM, grace=20) as intr:
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(0.05)
            self.assertEqual(intr.signum, signal.SIGTERM)
            p = self.spawn("sleep", "30")
            intr.attach(p)
            rc = p.wait(timeout=10)
        self.assertEqual((rc, intr.killed), (-signal.SIGTERM, False))

    def test_flags_never_reach_the_child(self):
        with proc.Interrupts((signal.SIGTERM,), signal.SIGTERM, flags=(signal.SIGUSR1, signal.SIGUSR2),
                             grace=20) as intr:
            p = self.spawn("sleep", "30")
            intr.attach(p)
            os.kill(os.getpid(), signal.SIGUSR1)
            os.kill(os.getpid(), signal.SIGUSR2)
            deadline = time.time() + 10
            while time.time() < deadline and len(intr._flags) < 2:
                time.sleep(0.01)
            self.assertEqual(intr.take_flags(), [signal.SIGUSR1, signal.SIGUSR2])
            self.assertIsNone(p.poll())                                 # still running
            self.assertIsNone(intr.signum)
            os.kill(os.getpid(), signal.SIGTERM)
            rc = p.wait(timeout=10)
        self.assertEqual((rc, intr.signum, intr.killed), (-signal.SIGTERM, signal.SIGTERM, False))

    def test_pdeathsig_ends_an_orphaned_child(self):
        """A child set up by child_setup gets SIGTERM when the vamos that started it dies."""
        script = ("import subprocess, sys\n"
                  "sys.path.insert(0, %r)\n"
                  "from vamos import proc\n"
                  "p = subprocess.Popen(['sleep', '60'], preexec_fn=proc.child_setup())\n"
                  "print(p.pid, flush=True)\n" % ROOT)
        r = subprocess.run([sys.executable, "-c", script], stdout=subprocess.PIPE,
                           universal_newlines=True, timeout=60)
        pid = int(r.stdout.strip())
        deadline = time.time() + 10
        alive = True
        while alive and time.time() < deadline:
            try:
                os.kill(pid, 0)
                time.sleep(0.05)
            except ProcessLookupError:
                alive = False
        if alive:
            os.kill(pid, signal.SIGKILL)
        self.assertFalse(alive, "the orphaned sleep %d outlived its parent" % pid)


# -- engines: choose_engine, env_for, tool_rows ----------------------------------------------

ENGINE_VARS = ("VAMOS_ANALOG", "LD_LIBRARY_PATH", "VAMOS_VACASK", "VAMOS_OPENVAF", "VAMOS_XYCE",
               "VAMOS_XYCE_LIBS", "VAMOS_VACASK_HOME", "VAMOS_VACASK_MODULE_PATH")

ERR = "%s=%s: the analog engine must be vacask or xyce"


class TestEnginesMoves(TempDir):
    def setUp(self):
        super().setUp()
        for v in ENGINE_VARS:
            os.environ.pop(v, None)

    def exe(self, name):
        p = os.path.join(self.tmp, name)
        with open(p, "w") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(p, 0o755)
        return p

    def test_choose_engine_precedence(self):
        ce = engines.choose_engine
        self.assertEqual(ce({}), "vacask")
        self.assertEqual(ce({}, None), "vacask")
        self.assertEqual(ce({}, "xyce"), "xyce")
        self.assertEqual(ce({}, "XYCE"), "xyce")
        self.assertEqual(ce({}, "xa"), "vacask")                   # a vendor engine: the default
        self.assertEqual(ce({}, ""), "vacask")
        self.assertEqual(ce({"analog": True}, "xyce"), "xyce")     # --vamos-analog with no value
        self.assertEqual(ce({"analog": ""}, "xyce"), "xyce")
        self.assertEqual(ce({"analog": "Xyce"}, "vacask"), "xyce")
        os.environ["VAMOS_ANALOG"] = "xyce"
        self.assertEqual(ce({}, "vacask"), "xyce")
        self.assertEqual(ce({"analog": "vacask"}, "xyce"), "vacask")
        os.environ["VAMOS_ANALOG"] = ""
        self.assertEqual(ce({}, "xyce"), "xyce")

    def test_choose_engine_errors_with_the_vcs_ams_text(self):
        with self.assertRaises(ValueError) as cm:
            engines.choose_engine({"analog": "spectre"}, "xyce")
        self.assertEqual(str(cm.exception), ERR % ("--vamos-analog", "spectre"))
        os.environ["VAMOS_ANALOG"] = "ngspice"
        with self.assertRaises(ValueError) as cm:
            engines.choose_engine({}, "xyce")
        self.assertEqual(str(cm.exception), ERR % ("VAMOS_ANALOG", "ngspice"))
        self.assertEqual(engines.choose_engine({"analog": "xyce"}), "xyce")   # the option wins first

    def test_flow_choose_engine_wraps_it(self):
        self.assertEqual(flow.choose_engine({}, None), "vacask")
        self.assertEqual(flow.choose_engine({}, AmsConfig()), "vacask")
        self.assertEqual(flow.choose_engine({}, AmsConfig(choose=Choose(engine="xyce"))), "xyce")
        self.assertEqual(flow.choose_engine({}, AmsConfig(choose=Choose(engine="XA"))), "vacask")
        self.assertEqual(flow.choose_engine({"analog": "vacask"}, AmsConfig(choose=Choose(engine="xyce"))),
                         "vacask")
        with self.assertRaises(flow.AmsError) as cm:
            flow.choose_engine({"analog": "spectre"}, None)
        self.assertEqual(str(cm.exception), ERR % ("--vamos-analog", "spectre"))
        self.assertIsInstance(cm.exception, nvc.BackendError)
        os.environ["VAMOS_ANALOG"] = "bad"
        with self.assertRaises(flow.AmsError) as cm:
            flow.choose_engine({}, AmsConfig(choose=Choose(engine="xyce")))
        self.assertEqual(str(cm.exception), ERR % ("VAMOS_ANALOG", "bad"))

    def test_env_for_without_an_nvc_library_directory(self):
        """nvc_libdir None: no bridge directory on LD_LIBRARY_PATH (today's dummy '/nonexistent'
        put '/' first, E68); a directory given: its bridge library's directory first, as before."""
        libs = engines.xyce_libs()
        self.assertEqual(engines.env_for("xyce")["LD_LIBRARY_PATH"], os.pathsep.join(libs))
        self.assertEqual(engines.env_for("xyce", None, {})["LD_LIBRARY_PATH"], os.pathsep.join(libs))
        old = engines.env_for("xyce", "/nonexistent")["LD_LIBRARY_PATH"]
        self.assertTrue(old.startswith("/" + os.pathsep), old)
        self.assertEqual(old, os.pathsep.join(["/"] + libs))
        libdir = os.path.join(self.tmp, "lib", "nvc")
        os.makedirs(libdir)
        with open(os.path.join(self.tmp, "lib", "libcosim_bridge.so"), "w") as fh:
            fh.write("")
        self.assertEqual(engines.env_for("xyce", libdir)["LD_LIBRARY_PATH"],
                         os.pathsep.join([os.path.join(self.tmp, "lib")] + libs))
        v = engines.env_for("vacask")
        self.assertEqual(v["LD_LIBRARY_PATH"], os.path.dirname(engines.vacask_cinterface()))
        self.assertEqual("SIM_MODULE_PATH" in v, engines.vacask_module_path() is not None)
        self.assertEqual("SIM_OPENVAF" in v, engines.openvaf() is not None)
        base = {"X": "1", "LD_LIBRARY_PATH": "/base"}
        out = engines.env_for("xyce", None, base)
        self.assertEqual(base, {"X": "1", "LD_LIBRARY_PATH": "/base"})     # not mutated
        self.assertEqual(out["X"], "1")
        self.assertEqual(out["LD_LIBRARY_PATH"], os.pathsep.join(libs))     # the process's, not base's
        os.environ["LD_LIBRARY_PATH"] = "/old"
        self.assertEqual(engines.env_for("xyce")["LD_LIBRARY_PATH"], os.pathsep.join(libs + ["/old"]))
        os.environ["VAMOS_XYCE_LIBS"] = os.pathsep.join(["/a", "/b"])
        self.assertEqual(engines.env_for("xyce", None)["LD_LIBRARY_PATH"],
                         os.pathsep.join(["/a", "/b", "/old"]))
        with self.assertRaises(ValueError):
            engines.env_for("ngspice")
        with self.assertRaises(ValueError):
            engines.env_for("ngspice", "/nonexistent")

    def test_tool_rows(self):
        vb, ov, xb = self.exe("vacask"), self.exe("openvaf-r"), self.exe("Xyce")
        os.environ["VAMOS_VACASK"], os.environ["VAMOS_OPENVAF"] = vb, ov
        self.assertEqual(engines.tool_rows("vacask"), [("VACASK", vb), ("OpenVAF-r", ov)])
        self.assertEqual(engines.tool_rows("vacask", "/x/Xyce"), [("VACASK", vb), ("OpenVAF-r", ov)])
        saved = engines.openvaf
        engines.openvaf = lambda: None                                  # no openvaf-r anywhere
        try:
            self.assertEqual(engines.tool_rows("vacask"), [("VACASK", vb)])
        finally:
            engines.openvaf = saved
        self.assertEqual(engines.tool_rows("xyce", "/x/Xyce"), [("Xyce", "/x/Xyce")])
        self.assertEqual(engines.tool_rows("xyce"), [("Xyce", engines.xyce_bin() or "Xyce")])
        os.environ["VAMOS_XYCE"] = xb
        self.assertEqual(engines.tool_rows("xyce"), [("Xyce", xb)])
        self.assertEqual(engines.tool_rows("xyce", "given"), [("Xyce", "given")])
        with self.assertRaises(ValueError):
            engines.tool_rows("ngspice")

    def test_compile_tools_is_tool_rows(self):
        """ams/flow.compile_tools gives the rows it always gave: tool_rows(engine, xyce_bin() or
        "Xyce"), the engine chosen as the compile chooses it, vacask when that choice fails."""
        vb, ov, xb = self.exe("vacask"), self.exe("openvaf-r"), self.exe("Xyce")
        os.environ["VAMOS_VACASK"], os.environ["VAMOS_OPENVAF"] = vb, ov
        job = Job("vcs", cwd=self.tmp)                                  # no control file
        self.assertEqual(flow.compile_tools(job, {}), [("VACASK", vb), ("OpenVAF-r", ov)])
        self.assertEqual(flow.compile_tools(job, {}), engines.tool_rows("vacask", engines.xyce_bin() or "Xyce"))
        self.assertEqual(flow.compile_tools(job, {"analog": "xyce"}), [("Xyce", engines.xyce_bin() or "Xyce")])
        os.environ["VAMOS_XYCE"] = xb
        self.assertEqual(flow.compile_tools(job, {"analog": "xyce"}), [("Xyce", xb)])
        self.assertEqual(flow.compile_tools(job, {"analog": "bad"}), [("VACASK", vb), ("OpenVAF-r", ov)])
        os.environ["VAMOS_ANALOG"] = "xyce"
        self.assertEqual(flow.compile_tools(job, {}), [("Xyce", xb)])
        os.environ.pop("VAMOS_ANALOG")
        with open(os.path.join(self.tmp, "vcsAD.init"), "w") as fh:
            fh.write("choose xyce rc.sp;\n")
        self.assertEqual(flow.compile_tools(job, {}), [("Xyce", xb)])
        with open(os.path.join(self.tmp, "vcsAD.init"), "w") as fh:
            fh.write("choose xa rc.sp;\n")
        self.assertEqual(flow.compile_tools(job, {}), [("VACASK", vb), ("OpenVAF-r", ov)])
        self.assertEqual(flow.compile_tools(job, {"analog": "xyce"}), [("Xyce", xb)])


if __name__ == "__main__":
    unittest.main()
