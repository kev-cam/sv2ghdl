"""Engine facts: the T1 tier of docs/VAMOS_SPECTRE_DESIGN.md (§11 T1, §13).

Every §13 row that its introduction lists as an engine fact (E1-E13, E15-E43, E45's engine
half, E47, E51, E63-E75, E77, E81, E82, E84, E95, E96) lives in
fixtures/spectre/engine_facts/E<nn>/: the deck(s) the fact was observed on and an
`expect.json` naming the tool, the command line and the facts the row states (values with
tolerances, messages, exit status).  This module runs them on today's engines through
`engines.vacask_bin()`/`xyce_bin()`/`openvaf()` and `engines.env_for` (§7.1, §7.2), so an
engine upgrade that changes a fact fails here and names the design rule that rests on it.

It runs on WSL only (both engines); on the Cygwin leg every test is a reported skip.  The
gates come from `spectre_testlib` (§11) when that module is present; otherwise the local
fallbacks below read the same environment (VAMOS_CPP, the engine locations).

expect.json
-----------
{
  "row": "E1", "experiment": "...", "result": "...",      # the §13 row, abbreviated
  "rules": ["§5.2 Restore", ...],                        # the design rules resting on it
  "runs": [ { ... } ],                                    # one entry per engine run
  "cross": [ { ... } ]                                    # optional: checks across runs
}
A run: name (the test id suffix), tool (vacask | xyce | openvaf | cpp | exe), deck (the
input file, copied with every other file of the directory into a scratch copy), args
(extra argv; "{deck}" stands for the deck, which is appended when absent), cwd (a
sub-directory of the copy to run in), env ({tmp} is the scratch copy), gates (extra gate
names), launcher ("second": the other Xyce launcher, /usr/local/bin/Xyce), plain_env (no
env_for and no LD_LIBRARY_PATH), subst (files in which @CWD@ becomes the scratch copy's
path), program (tool exe), timeout, signal ({after, name, grace}: the run is signalled),
status (an int, a list of ints, or a signal name for a death by that signal), output
({contains, absent, regex} on stdout+stderr), stderr ({contains, absent, empty}), files
({present, absent}), checks (below), record_only (observed and logged, never asserted),
note.
A check names a file: "raw" (a rawfile, with "plot" index; keys names, names_include,
names_exclude, names_sorted, ambiguous (names Raw.index cannot tell apart, E84), points,
min_points, plots, declared, no_header, plotname_contains, col + row|at|select|column|minus
+ value|values + rel|abs, restarts [+ axis], readable, fix_points, max_last_time), "prn" (a
Xyce standard table; keys columns, rows, min_rows, col + row + value, column, index) or
"text" (keys contains, absent, regex, line + equals, first_unmarked).  A run may also carry
in_run (continue in an earlier run's scratch copy; put both runs in one "group"),
stopped_within (seconds, with signal) and group (the test the run belongs to; the tool by
default).
Set VAMOS_FACTS_LOG=<file> to append every observation as JSON lines; running this file
with `observe E05 ...` prints what the engines produce for those rows without asserting.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import unittest

from vamos_testlib import FIXTURES, LINUX, TempDir, have_vacask, have_xyce, needs_vacask, needs_xyce  # noqa: E402
from vamos.ams import engines  # noqa: E402
from vamos.netlist import rawfile  # noqa: E402

try:
    import spectre_testlib as _spectre_lib  # S0F's gates (§11)
except ImportError:
    _spectre_lib = None

FACTS = os.path.join(FIXTURES, "spectre", "engine_facts")
SECOND_XYCE = "/usr/local/bin/Xyce"


# -- gates --------------------------------------------------------------------

def cpp_path():
    return os.environ.get("VAMOS_CPP") or "/usr/bin/cpp"


def have_cpp():
    return LINUX and os.access(cpp_path(), os.X_OK)


def _fallback_gates():
    return {
        "needs_vacask": needs_vacask,
        "needs_xyce": needs_xyce,
        "needs_cpp": unittest.skipUnless(have_cpp(), "needs cpp (WSL /usr/bin/cpp or VAMOS_CPP)"),
        # both Xyce launchers carry PyMS and load the same library under env_for [E96]
        "needs_pyms": unittest.skipUnless(have_xyce(), "needs Xyce with PyMS (Linux/WSL)"),
        "needs_openvaf": unittest.skipUnless(have_vacask() and engines.openvaf(),
                                             "needs openvaf-r (Linux/WSL)"),
    }


def gate(name):
    g = getattr(_spectre_lib, name, None) if _spectre_lib is not None else None
    if g is None:
        g = _fallback_gates().get(name)
    if g is None:
        return unittest.skip("unknown gate %s" % name)
    return g


TOOL_GATES = {"vacask": ("needs_vacask",), "xyce": ("needs_xyce",), "openvaf": ("needs_openvaf",),
              "cpp": ("needs_cpp",), "exe": ()}


# -- engine environment --------------------------------------------------------

def engine_env(engine, base=None):
    """engines.env_for for a standalone run: the phase-0 signature (nvc_libdir None, no bridge
    directory, §10) when it exists, else today's with a bridge directory that is not there."""
    base = dict(os.environ if base is None else base)
    try:
        return engines.env_for(engine, None, base)
    except TypeError:
        return engines.env_for(engine, "/nonexistent", base)


def second_xyce():
    """The other Xyce launcher (E68, E96), or None when there is only one."""
    main = engines.xyce_bin()
    for cand in (SECOND_XYCE, shutil.which("Xyce")):
        if cand and os.access(cand, os.X_OK) and (not main or os.path.realpath(cand) != os.path.realpath(main)):
            return cand
    return None


def tool_program(run):
    """(program, env) for a run; env is None when the run is skipped (reason in program)."""
    tool = run["tool"]
    if tool == "vacask":
        return engines.vacask_bin(), engine_env("vacask")
    if tool == "xyce":
        prog = engines.xyce_bin()
        if run.get("launcher") == "second":
            prog = second_xyce()
            if not prog:
                return "no second Xyce launcher (%s)" % SECOND_XYCE, None
        if run.get("plain_env"):
            env = dict(os.environ)
            env.pop("LD_LIBRARY_PATH", None)
            return prog, env
        return prog, engine_env("xyce")
    if tool == "openvaf":
        prog = engines.openvaf()
        return (prog, engine_env("vacask")) if prog else ("no openvaf-r", None)
    if tool == "cpp":
        return cpp_path(), dict(os.environ)
    if tool == "exe":
        prog = run["program"]
        if not os.access(prog, os.X_OK):
            return "%s is not executable" % prog, None
        return prog, (engine_env("vacask") if run.get("engine_env") == "vacask" else dict(os.environ))
    raise ValueError("unknown tool %r" % (tool,))


# -- running --------------------------------------------------------------------

class RunResult(object):
    def __init__(self, cmd, cwd, rc, stdout, stderr, stopped_in=None, signalled=None):
        self.cmd, self.cwd, self.rc = cmd, cwd, rc
        self.stdout, self.stderr = stdout, stderr
        self.output = stdout + ("\n" + stderr if stderr else "")
        self.stopped_in = stopped_in
        self.signalled = signalled

    def path(self, rel):
        return os.path.join(self.cwd, rel)


def _read(path):
    try:
        with open(path, errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def execute(program, env, run, wd):
    """Run one entry of expect.json in its scratch copy wd; returns a RunResult."""
    cwd = os.path.join(wd, run["cwd"]) if run.get("cwd") else wd
    deck = run.get("deck")
    args = [a.replace("{deck}", deck or "") for a in run.get("args", [])]
    if deck and not any("{deck}" in a for a in run.get("args", [])):
        args.append(deck)
    cmd = [program] + args
    env = dict(env)
    for k, v in (run.get("env") or {}).items():
        env[k] = v.replace("{tmp}", wd)
    if run["tool"] == "xyce":
        env.setdefault("PYMS_CACHE", os.path.join(os.path.dirname(wd), "pyms"))
    out_path, err_path = os.path.join(cwd, "vamos_run.out"), os.path.join(cwd, "vamos_run.err")
    stopped_in = None
    sig = run.get("signal")
    with open(out_path, "w") as out, open(err_path, "w") as err:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                start_new_session=True)
        try:
            if sig:
                time.sleep(float(sig.get("after", 3)))
                t0 = time.time()
                proc.send_signal(getattr(signal, "SIG" + sig["name"]))
                try:
                    rc = proc.wait(float(sig.get("grace", 20)))
                    stopped_in = time.time() - t0
                except subprocess.TimeoutExpired:
                    proc.kill()
                    rc = proc.wait()
            else:
                rc = proc.wait(float(run.get("timeout", 300)))
        except subprocess.TimeoutExpired:
            proc.kill()
            rc = proc.wait()
            raise
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    return RunResult(cmd, cwd, rc, _read(out_path), _read(err_path), stopped_in, sig and sig["name"])


# -- readers ---------------------------------------------------------------------

def read_prn(path):
    """A Xyce standard print table: (columns, rows of floats); the Index column included.
    Lines that are not numeric rows (the header, "End of Xyce(TM) ...") are skipped."""
    columns, rows = None, []
    with open(path, errors="replace") as fh:
        for line in fh:
            toks = line.split()
            if not toks:
                continue
            if columns is None:
                columns = toks
                continue
            try:
                rows.append([float(t) for t in toks])
            except ValueError:
                continue
    return columns or [], rows


def column_index(raw, name):
    """A rawfile column by its exact name first (nodes `A` and `a` are distinct, §4.6), then
    by rawfile.Raw.index's matching."""
    names = raw.names()
    if name in names and names.count(name) == 1:
        return names.index(name)
    return raw.index(name)


def real(v):
    return v.real if isinstance(v, complex) else v


def close(got, want, rel, abs_):
    if isinstance(want, (list, tuple)):
        want = complex(want[0], want[1])
    g = complex(got) if isinstance(want, complex) else real(got)
    return abs(g - want) <= abs_ + rel * abs(want)


def fmt(v):
    if isinstance(v, complex):
        return "(%r, %r)" % (v.real, v.imag)
    return repr(v)


# -- the checks -------------------------------------------------------------------

class FactCase(TempDir):
    """A scratch directory per test; the checks that expect.json entries name."""

    maxDiff = None

    def setUp(self):
        TempDir.setUp(self)
        self.observed = []

    def tearDown(self):
        log = os.environ.get("VAMOS_FACTS_LOG")
        if log and self.observed:
            with open(log, "a") as fh:
                for item in self.observed:
                    fh.write(json.dumps(item, default=str) + "\n")
        TempDir.tearDown(self)

    def note(self, **item):
        self.observed.append(item)

    # -- running ------------------------------------------------------------------

    def run_entry(self, row_dir, run, case):
        program, env = tool_program(run)
        if env is None:
            self.skipTest(program)
        if run.get("in_run"):                              # continue in an earlier run's copy
            wd = os.path.join(case, run["in_run"])
            if not os.path.isdir(wd):
                self.skipTest("run %s did not run" % run["in_run"])
            res = execute(program, env, run, wd)
            self.note(row=os.path.basename(row_dir), run=run["name"], cmd=res.cmd, rc=res.rc,
                      output_tail=res.output.strip().splitlines()[-6:])
            return res
        wd = os.path.join(case, run["name"])
        shutil.copytree(row_dir, wd)
        for rel in (run.get("subst") or []):
            p = os.path.join(wd, rel)
            with open(p) as fh:
                text = fh.read()
            with open(p, "w") as fh:
                fh.write(text.replace("@CWD@", wd))
        res = execute(program, env, run, wd)
        self.note(row=os.path.basename(row_dir), run=run["name"], cmd=res.cmd, rc=res.rc,
                  stopped_in=res.stopped_in, output_tail=res.output.strip().splitlines()[-6:])
        return res

    # -- assertions ---------------------------------------------------------------

    def check_status(self, res, want, ctx):
        if want is None:
            return
        if isinstance(want, str):                        # a death by this signal
            num = getattr(signal, "SIG" + want.replace("SIG", ""))
            self.assertIn(res.rc, (-num, 128 + num), "%s: expected a death by SIG%s, rc %s\n%s"
                          % (ctx, want, res.rc, res.output[-1500:]))
            return
        wants = want if isinstance(want, list) else [want]
        self.assertIn(res.rc, wants, "%s: exit status %s, expected %s\n%s" % (ctx, res.rc, wants, res.output[-1500:]))

    def check_text(self, text, spec, ctx):
        for s in spec.get("contains", []):
            self.assertIn(s, text, "%s: %r not in the output:\n%s" % (ctx, s, text[-2500:]))
        for s in spec.get("absent", []):
            self.assertNotIn(s, text, "%s: %r found in the output" % (ctx, s))
        for s in spec.get("regex", []):
            self.assertTrue(re.search(s, text), "%s: /%s/ not in the output:\n%s" % (ctx, s, text[-2500:]))
        if spec.get("empty"):
            self.assertEqual(text.strip(), "", "%s: expected nothing, got:\n%s" % (ctx, text[:1000]))

    def check_files(self, res, spec, ctx):
        for rel in spec.get("present", []):
            self.assertTrue(os.path.exists(res.path(rel)), "%s: %s was not written (%s)"
                            % (ctx, rel, sorted(os.listdir(res.cwd))))
        for rel in spec.get("absent", []):
            self.assertFalse(os.path.exists(res.path(rel)), "%s: %s exists but should not" % (ctx, rel))

    def plot(self, res, chk, ctx):
        path = res.path(chk["raw"])
        self.assertTrue(os.path.exists(path), "%s: %s missing (%s)" % (ctx, chk["raw"], sorted(os.listdir(res.cwd))))
        plots = rawfile.read_all(path)
        k = chk.get("plot", 0)
        self.assertLess(k, len(plots), "%s: %s has %d plot(s), no plot %d" % (ctx, chk["raw"], len(plots), k))
        return plots, plots[k]

    def values(self, raw, name):
        return [p[column_index(raw, name)] for p in raw.points]

    def check_raw(self, res, chk, ctx):
        ctx = "%s %s" % (ctx, chk["raw"])
        if chk.get("readable") is False:
            with self.assertRaises(rawfile.RawError, msg="%s: expected not a rawfile" % ctx):
                rawfile.read_all(res.path(chk["raw"]))
            return
        if chk.get("fix_points") is not None:
            changed = rawfile.fix_points(res.path(chk["raw"]))
            self.assertEqual(changed, chk["fix_points"], "%s: fix_points changed=%s" % (ctx, changed))
        plots, raw = self.plot(res, chk, ctx)
        self.note(row=ctx, plot=raw.plotname, names=raw.names(), points=len(raw.points), declared=raw.declared_points,
                  first=[fmt(v) for v in (raw.points[0] if raw.points else ())],
                  last=[fmt(v) for v in (raw.points[-1] if raw.points else ())])
        if "plots" in chk:
            self.assertEqual(len(plots), chk["plots"], "%s: %d plots" % (ctx, len(plots)))
        if "names" in chk:
            self.assertEqual(raw.names(), chk["names"], "%s: variables" % ctx)
        if "names_include" in chk:
            for n in chk["names_include"]:
                self.assertIn(n, raw.names(), "%s: no variable %r in %s" % (ctx, n, raw.names()))
        if "names_exclude" in chk:
            for n in chk["names_exclude"]:
                self.assertNotIn(n, raw.names(), "%s: variable %r present" % (ctx, n))
        if chk.get("names_sorted"):
            self.assertEqual(raw.names(), sorted(raw.names()), "%s: variables not bytewise sorted" % ctx)
        for n in chk.get("ambiguous", []):                 # Raw.index cannot choose; Raw.exact can (§4.6, E84)
            with self.assertRaises(KeyError, msg="%s: index(%r) chose one of several variables" % (ctx, n)):
                raw.index(n)
            self.assertEqual(raw.names()[raw.exact(n)], n, "%s: exact(%r)" % (ctx, n))
        if "points" in chk:
            self.assertEqual(len(raw.points), chk["points"], "%s: %d points" % (ctx, len(raw.points)))
        if "min_points" in chk:
            self.assertGreaterEqual(len(raw.points), chk["min_points"], "%s: %d points" % (ctx, len(raw.points)))
        if "declared" in chk:
            self.assertEqual(raw.declared_points, chk["declared"], "%s: No. Points: %r" % (ctx, raw.declared_points))
        if "no_header" in chk:
            self.assertNotIn(chk["no_header"], raw.header, "%s: header has %s" % (ctx, chk["no_header"]))
        if "plotname_contains" in chk:
            for s in chk["plotname_contains"]:
                self.assertIn(s, raw.plotname, "%s: plot name %r" % (ctx, raw.plotname))
        if "max_last_time" in chk:
            t = raw.last_time()
            self.assertIsNotNone(t, "%s: no points" % ctx)
            self.assertLess(t, chk["max_last_time"], "%s: last time %r" % (ctx, t))
            self.assertGreater(t, 0.0, "%s: last time %r" % (ctx, t))
        if "restarts" in chk:                              # groups of a swept plot: the x axis restarts
            xs = [real(v) for v in self.values(raw, chk.get("axis", raw.names()[chk.get("axis_index", 1)]))]
            groups = 1 + sum(1 for a, b in zip(xs, xs[1:]) if b < a)
            self.assertEqual(groups, chk["restarts"], "%s: %d groups" % (ctx, groups))
        if "col" in chk:
            self.check_column(raw, chk, ctx)

    def check_column(self, raw, chk, ctx):
        rel, abs_ = chk.get("rel", 1e-5), chk.get("abs", 1e-12)
        col = self.values(raw, chk["col"])
        if "minus" in chk:
            other = self.values(raw, chk["minus"])
            col = [a - b for a, b in zip(col, other)]
        rows = list(range(len(col)))
        if "select" in chk:                                # the rows where another column has a value
            sel = chk["select"]
            svals = self.values(raw, sel["col"])
            rows = [i for i in rows if close(svals[i], sel["value"], sel.get("rel", 1e-9), sel.get("abs", 0.0))]
            self.assertTrue(rows, "%s: no row with %s = %r" % (ctx, sel["col"], sel["value"]))
        if "at" in chk:                                    # interpolated on the time axis of the rows
            ts = [real(v) for v in self.values(raw, chk.get("axis", "time"))]
            sub = rawfile.Raw()
            sub.variables = [("time", "time"), ("y", "")]
            sub.points = [(ts[i], col[i]) for i in rows]
            got = sub.at("y", chk["at"])
            self.note(row=ctx, col=chk["col"], at=chk["at"], value=fmt(got))
            self.assertTrue(close(got, chk["value"], rel, abs_), "%s: %s(%r) = %s, expected %s (rel %g, abs %g)"
                            % (ctx, chk["col"], chk["at"], fmt(got), chk["value"], rel, abs_))
            return
        if "values" in chk or "column" in chk:             # the whole (selected) column
            want = chk.get("values", chk.get("column"))
            got = [col[i] for i in rows]
            self.note(row=ctx, col=chk["col"], column=[fmt(v) for v in got])
            self.assertEqual(len(got), len(want), "%s: %s has %d rows, expected %d: %s"
                             % (ctx, chk["col"], len(got), len(want), [fmt(v) for v in got]))
            for g, w in zip(got, want):
                self.assertTrue(close(g, w, rel, abs_), "%s: %s = %s, expected %s (rel %g, abs %g)"
                                % (ctx, chk["col"], [fmt(v) for v in got], want, rel, abs_))
            return
        i = chk.get("row", 0)
        self.assertTrue(-len(rows) <= i < len(rows), "%s: no row %d (%d rows)" % (ctx, i, len(rows)))
        got = col[rows[i]]
        self.note(row=ctx, col=chk["col"], index=i, value=fmt(got))
        self.assertTrue(close(got, chk["value"], rel, abs_), "%s: %s[%d] = %s, expected %s (rel %g, abs %g)"
                        % (ctx, chk["col"], i, fmt(got), chk["value"], rel, abs_))

    def check_prn(self, res, chk, ctx):
        ctx = "%s %s" % (ctx, chk["prn"])
        path = res.path(chk["prn"])
        self.assertTrue(os.path.exists(path), "%s: missing (%s)" % (ctx, sorted(os.listdir(res.cwd))))
        columns, rows = read_prn(path)
        self.note(row=ctx, columns=columns, rows=len(rows), first=rows[:1], last=rows[-1:])
        rel, abs_ = chk.get("rel", 1e-5), chk.get("abs", 1e-12)
        if "columns" in chk:
            self.assertEqual(columns, chk["columns"], "%s: columns" % ctx)
        if "rows" in chk:
            self.assertEqual(len(rows), chk["rows"], "%s: %d rows" % (ctx, len(rows)))
        if "min_rows" in chk:
            self.assertGreaterEqual(len(rows), chk["min_rows"], "%s: %d rows" % (ctx, len(rows)))
        if "index" in chk:
            got = [r[columns.index("Index")] for r in rows]
            self.assertEqual(got, [float(v) for v in chk["index"]], "%s: Index column" % ctx)
        if "col" in chk:
            self.assertIn(chk["col"], columns, "%s: no column %s in %s" % (ctx, chk["col"], columns))
            vals = [r[columns.index(chk["col"])] for r in rows]
            if "column" in chk:
                self.assertEqual(len(vals), len(chk["column"]), "%s: %s = %r" % (ctx, chk["col"], vals))
                for g, w in zip(vals, chk["column"]):
                    self.assertTrue(close(g, w, rel, abs_), "%s: %s = %r, expected %r" % (ctx, chk["col"], vals, chk["column"]))
            else:
                i = chk.get("row", 0)
                self.assertTrue(close(vals[i], chk["value"], rel, abs_), "%s: %s[%d] = %r, expected %r"
                                % (ctx, chk["col"], i, vals[i], chk["value"]))

    def check_textfile(self, res, chk, ctx):
        ctx = "%s %s" % (ctx, chk["text"])
        path = res.path(chk["text"])
        self.assertTrue(os.path.exists(path), "%s: missing" % ctx)
        text = _read(path)
        self.check_text(text, chk, ctx)
        lines = text.splitlines()
        if "line" in chk:
            self.assertEqual(lines[chk["line"]], chk["equals"], "%s: line %d" % (ctx, chk["line"]))
        if "first_unmarked" in chk:                        # the first line that is not a cpp marker
            body = [ln for ln in lines if not ln.startswith("# ")]
            self.assertTrue(body, "%s: only markers" % ctx)
            self.assertEqual(body[0], chk["first_unmarked"], "%s: first line after the markers" % ctx)

    def check_run(self, res, run, ctx):
        self.check_status(res, run.get("status"), ctx)
        if "output" in run:
            self.check_text(res.output, run["output"], ctx + " output")
        if "stderr" in run:
            self.check_text(res.stderr, run["stderr"], ctx + " stderr")
        if "files" in run:
            self.check_files(res, run["files"], ctx)
        if run.get("signal"):
            if "stopped_within" in run:
                self.assertIsNotNone(res.stopped_in, "%s: still running %s s after SIG%s" % (ctx, run["signal"].get("grace", 20), run["signal"]["name"]))
                self.assertLessEqual(res.stopped_in, run["stopped_within"], "%s: stopped %.2f s after SIG%s"
                                     % (ctx, res.stopped_in, run["signal"]["name"]))
        for chk in run.get("checks", []):
            if "raw" in chk:
                self.check_raw(res, chk, ctx)
            elif "prn" in chk:
                self.check_prn(res, chk, ctx)
            elif "text" in chk:
                self.check_textfile(res, chk, ctx)
            else:
                self.fail("%s: unknown check %r" % (ctx, chk))

    def check_cross(self, results, cross, ctx):
        """Values of two runs compared with each other (E69, E95)."""
        a, b = cross["a"], cross["b"]
        ra, rb = results.get(a["run"]), results.get(b["run"])
        if ra is None or rb is None:
            self.skipTest("%s: cross check needs runs %s and %s" % (ctx, a["run"], b["run"]))
        pa = rawfile.read_all(ra.path(a["raw"]))[a.get("plot", 0)]
        pb = rawfile.read_all(rb.path(b["raw"]))[b.get("plot", 0)]
        va = [real(v) * a.get("scale", 1.0) for v in self.values(pa, a["col"])]
        vb = [real(v) * b.get("scale", 1.0) for v in self.values(pb, b["col"])]
        self.note(row=ctx, cross=cross.get("name"), a=va, b=vb)
        self.assertEqual(len(va), len(vb), "%s: %d against %d values" % (ctx, len(va), len(vb)))
        for x, y in zip(va, vb):
            d = abs(x - y) / max(abs(x), abs(y), 1e-300)
            self.assertLessEqual(d, cross["rel"], "%s: %r against %r differ by %.3g (limit %g)" % (ctx, x, y, d, cross["rel"]))
            if "min_rel" in cross:
                self.assertGreaterEqual(d, cross["min_rel"], "%s: %r against %r differ by only %.3g (expected at least %g)"
                                        % (ctx, x, y, d, cross["min_rel"]))


# -- the table-driven tests -----------------------------------------------------

def load_rows():
    rows = []
    if not os.path.isdir(FACTS):
        return rows
    for name in sorted(os.listdir(FACTS)):
        p = os.path.join(FACTS, name, "expect.json")
        if os.path.isfile(p):
            with open(p) as fh:
                rows.append((os.path.join(FACTS, name), json.load(fh)))
    return rows


def _make_test(row_dir, spec, runs, cross):
    def test(self):
        case = os.path.join(self.tmp, "case")
        os.makedirs(os.path.join(case, "pyms"))
        results = {}
        for run in runs:
            ctx = "%s/%s" % (spec["row"], run["name"])
            with self.subTest(run=run["name"]):
                res = self.run_entry(row_dir, run, case)
                results[run["name"]] = res
                if run.get("record_only"):
                    self.note(row=ctx, record_only=True, rc=res.rc)
                    continue
                self.check_run(res, run, ctx)
        for c in cross:
            with self.subTest(cross=c.get("name")):
                self.check_cross(results, c, "%s/%s" % (spec["row"], c.get("name", "cross")))
    rules = "; ".join(spec.get("rules", []))
    test.__doc__ = "%s: %s -- rests on %s" % (spec["row"], spec.get("result", "")[:120], rules)
    return test


class TestEngineFacts(FactCase):
    """One test per §13 row and tool (test_E01_vacask ...); rows with cross-engine checks run
    both engines in one test."""


def _install():
    for row_dir, spec in load_rows():
        runs = spec.get("runs", [])
        cross = spec.get("cross", [])
        groups = {}
        if cross:
            groups["cross"] = runs
        else:
            for run in runs:                               # one test per tool; "group" overrides
                groups.setdefault(run.get("group", run["tool"]), []).append(run)
        for key, group in groups.items():
            fn = _make_test(row_dir, spec, group, cross if key == "cross" else [])
            names = set()
            for run in group:
                names.update(TOOL_GATES[run["tool"]])
                names.update(run.get("gates", []))
            for g in sorted(names):
                fn = gate(g)(fn)
            fn = unittest.skipUnless(LINUX, "engine facts run on Linux/WSL")(fn)
            setattr(TestEngineFacts, "test_%s_%s" % (os.path.basename(row_dir), key), fn)


_install()


# -- the facts that need more than a command line ---------------------------------

@unittest.skipUnless(LINUX, "signals and nohup: Linux/WSL")
class TestE81Dispositions(FactCase):
    """E81's other half: an inherited SIG_IGN keeps a signal from stopping the engine, and nohup
    leaves SIGHUP ignored (§2.7: a signal that was ignored at start stays ignored)."""

    @needs_vacask
    def test_sigint_ignored_at_start_does_not_stop_vacask(self):
        row_dir = os.path.join(FACTS, "E81")
        wd = os.path.join(self.tmp, "ign")
        shutil.copytree(row_dir, wd)
        env = engine_env("vacask")
        with open(os.path.join(wd, "out.txt"), "w") as out:
            proc = subprocess.Popen([engines.vacask_bin(), "rc.sim"], cwd=wd, env=env, stdin=subprocess.DEVNULL,
                                    stdout=out, stderr=subprocess.STDOUT, start_new_session=True,
                                    preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN))
            try:
                time.sleep(2.0)
                self.assertIsNone(proc.poll(), "VACASK ended before the signal")
                proc.send_signal(signal.SIGINT)
                time.sleep(1.5)
                still = proc.poll() is None
                self.note(row="E81/sig_ign", still_running_after_sigint=still)
                self.assertTrue(still, "VACASK stopped on a SIGINT it inherited as ignored")
            finally:
                proc.kill()
                proc.wait()

    def test_nohup_leaves_sighup_ignored(self):
        nohup = shutil.which("nohup")
        if not nohup:
            self.skipTest("no nohup")
        code = "import signal; print(int(signal.getsignal(signal.SIGHUP)))"
        with_nohup = subprocess.run([nohup, sys.executable, "-B", "-c", code], cwd=self.tmp, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, universal_newlines=True)
        plain = subprocess.run([sys.executable, "-B", "-c", code], cwd=self.tmp, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, universal_newlines=True)
        self.note(row="E81/nohup", nohup=with_nohup.stdout.strip(), plain=plain.stdout.strip())
        self.assertEqual(with_nohup.stdout.strip().splitlines()[-1], str(int(signal.SIG_IGN)))
        self.assertEqual(plain.stdout.strip().splitlines()[-1], str(int(signal.SIG_DFL)))


@unittest.skipUnless(LINUX, "engine environment: Linux/WSL")
class TestE68Environment(FactCase):
    """E68's environment half: env_for resolves the tools vamos runs (§7.1, §7.2)."""

    @needs_vacask
    def test_env_for_vacask_replaces_an_inherited_old_openvaf(self):
        if not engines.openvaf():
            self.skipTest("no openvaf-r")
        env = engine_env("vacask", dict(os.environ, SIM_OPENVAF="/opt/openvaf-r/openvaf-r"))
        self.assertEqual(env.get("SIM_OPENVAF"), engines.openvaf())
        self.assertTrue(env.get("SIM_MODULE_PATH"), "no SIM_MODULE_PATH")
        self.assertTrue(os.path.isdir(env["SIM_MODULE_PATH"]))

    @needs_xyce
    def test_xyce_in_use_knows_badmos3(self):
        """§7.2 / §11 T1: engines.xyce_bin() under env_for loads a library that knows BADMOS3 (E68, E96)."""
        wd = os.path.join(self.tmp, "badmos3")
        shutil.copytree(os.path.join(FACTS, "E68"), wd)
        run = {"name": "badmos3", "tool": "xyce", "deck": "m3.cir"}
        res = execute(engines.xyce_bin(), engine_env("xyce"), run, wd)
        self.note(row="E68/badmos3_smoke", cmd=res.cmd, rc=res.rc, output_tail=res.output.strip().splitlines()[-4:])
        self.assertEqual(res.rc, 0, res.output[-2000:])
        self.assertNotIn("No model parameter BADMOS3", res.output,
                         "the Xyce in use does not know BADMOS3: %s" % res.output[-2000:])
        self.assertIn("BADMOS3", res.output, "BADMOS3 not listed in the model summary: %s" % res.output[-2000:])

    @needs_xyce
    def test_env_for_xyce_puts_the_build_library_first(self):
        env = engine_env("xyce")
        dirs = [d for d in env["LD_LIBRARY_PATH"].split(os.pathsep) if d and d != "/"]
        self.assertTrue(dirs, env["LD_LIBRARY_PATH"])
        self.assertTrue(any(os.path.isfile(os.path.join(d, "libxyce.so")) for d in dirs),
                        "no libxyce.so under %s" % env["LD_LIBRARY_PATH"])
        self.assertEqual(dirs[:len(engines.xyce_libs())], engines.xyce_libs())


# -- observe mode -----------------------------------------------------------------

def observe(rows):
    """Run the named rows and print what the engines produce (no assertions)."""
    import tempfile
    wanted = set(rows)
    for row_dir, spec in load_rows():
        row = spec["row"]
        if wanted and row not in wanted and os.path.basename(row_dir) not in wanted:
            continue
        print("=" * 30, row, "-", spec.get("experiment", "")[:100])
        case = tempfile.mkdtemp(prefix="facts-%s-" % row)
        os.makedirs(os.path.join(case, "pyms"))
        for run in spec.get("runs", []):
            program, env = tool_program(run)
            if env is None:
                print("-- %s: SKIP %s" % (run["name"], program))
                continue
            wd = os.path.join(case, run["name"])
            shutil.copytree(row_dir, wd)
            for rel in (run.get("subst") or []):
                p = os.path.join(wd, rel)
                with open(p) as fh:
                    text = fh.read()
                with open(p, "w") as fh:
                    fh.write(text.replace("@CWD@", wd))
            try:
                res = execute(program, env, run, wd)
            except subprocess.TimeoutExpired as e:
                print("-- %s: TIMEOUT %s" % (run["name"], e))
                continue
            print("-- %s: %s\n   rc=%s stopped_in=%s" % (run["name"], " ".join(res.cmd), res.rc, res.stopped_in))
            lines = [ln for ln in res.output.splitlines() if ln.strip()
                     and not re.match(r"^(This is|\(c\)|https|OpenMP|CPUs|Using OpenBLAS|Simulating|Running analysis|\s*Number |\s*Elapsed)", ln)]
            for ln in lines[-25:]:
                print("   |", ln[:160])
            if res.stderr.strip():
                print("   stderr:", res.stderr.strip()[:500].replace("\n", "\n   stderr: "))
            for f in sorted(os.listdir(res.cwd)):
                p = os.path.join(res.cwd, f)
                if f.endswith(".raw"):
                    try:
                        for k, raw in enumerate(rawfile.read_all(p)):
                            print("   %s[%d] %r declared=%s points=%d names=%s" % (f, k, raw.plotname[:70], raw.declared_points,
                                                                                 len(raw.points), raw.names()))
                            for pt in raw.points[:3]:
                                print("      ", [fmt(v) for v in pt])
                            if len(raw.points) > 3:
                                print("       ...", [fmt(v) for v in raw.points[-1]])
                    except Exception as e:  # noqa: BLE001
                        print("   %s: unreadable: %s" % (f, e))
                elif f.endswith(".prn"):
                    cols, rws = read_prn(p)
                    print("   %s columns=%s rows=%d first=%s last=%s" % (f, cols, len(rws), rws[:1], rws[-1:]))
                elif f.endswith((".scs", ".txt")) and f not in ("vamos_run.out", "vamos_run.err") and run["tool"] == "cpp":
                    print("   %s:\n      %s" % (f, _read(p)[:1500].replace("\n", "\n      ")))
        shutil.rmtree(case, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "observe":
        observe(sys.argv[2:])
    else:
        unittest.main()
