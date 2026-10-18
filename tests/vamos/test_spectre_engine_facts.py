"""Engine facts: the T1 tier of docs/VAMOS_SPECTRE_DESIGN.md (§11 T1, §13).

Every §13 row that its introduction lists as an engine fact (E1-E13, E15-E43, E45's engine
half, E47, E51, E63-E75, E77, E81, E82, E84, E95, E96; E102-E109 and E114, harvested in
phase 2; E115-E129 and E131, harvested in phase 2c, with R-emit-19's added facts in E116,
E120 and E125, and E105's and E106's time points stated directly) lives in
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
A run: name (the test id suffix), tool (vacask | xyce | openvaf | cpp | exe | python: this
interpreter on a script of the row), deck (the input file, copied with every other file of the
directory into a scratch copy), args (extra argv; "{deck}" stands for the deck, which is
appended when absent), cwd (a sub-directory of the copy to run in, or a directory read in place
such as {cmc}/<tree>/code: the run's logs then stay in the copy), env, gates (extra gate names),
launcher ("second": the other Xyce launcher, /usr/local/bin/Xyce), plain_env (no env_for and no
LD_LIBRARY_PATH), subst (files in which @CWD@ becomes the scratch copy's path, @CMC@
VAMOS_CMC_EXAMPLES and @VACASK_SRC@ spectre_testlib.vacask_src()), links and symlinks ({new:
existing}: hard and symbolic links made in the copy before the run), program (tool exe),
timeout, signal ({after, name, grace}: the run is signalled), status (an int, a list of ints, or
a signal name for a death by that signal), output ({contains, absent, regex} on stdout+stderr),
stderr ({contains, absent, empty}), files ({present, absent}), checks (below), record_only
(observed and logged, never asserted), note.  In args, env values and cwd, {tmp} is the scratch
copy, {repo} the repository, {psf_parser} spectre_testlib.psf_parser_dir(), {vacask_src}
spectre_testlib.vacask_src() and {cmc} VAMOS_CMC_EXAMPLES.
A check names a file: "raw" (a rawfile, with "plot" index; keys names, names_include,
names_exclude, names_sorted, ambiguous (names Raw.index cannot tell apart, E84), points,
min_points, plots, declared, no_header, plotname_contains, col + row|at|select|column|minus
+ value|values + rel|abs, restarts [+ axis], readable, fix_points, max_last_time, times: the
sweep points in [from, to] with count, min_step and max_step ranges and a gap [t, d], E105 and
E106), "prn" (a Xyce standard table; keys columns, rows, min_rows, col + row + value, column,
index, read_prn: vamos's rawfile.read_prn reads every row) or "text" (keys contains, absent,
regex, line + equals, first_unmarked).  TestRowForm checks every row's keys on both legs, so a
misspelt one fails instead of passing unread.  A run may also carry
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
              "cpp": ("needs_cpp",), "exe": (), "python": ()}


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


def cmc_examples():
    return (getattr(_spectre_lib, "cmc_examples", None) or (lambda: os.environ.get("VAMOS_CMC_EXAMPLES", "")))()


def placeholders(wd):
    """What {name} stands for in a run's args, env values and cwd (and @NAME@ in its subst files)."""
    lib = _spectre_lib
    return {
        "tmp": wd,
        "repo": os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "psf_parser": lib.psf_parser_dir() if lib is not None else "",
        "vacask_src": lib.vacask_src() if lib is not None else os.environ.get("VAMOS_VACASK_SRC", ""),
        "cmc": cmc_examples(),
    }


def expand(text, wd, deck=None):
    out = text if deck is None else text.replace("{deck}", deck)
    for k, v in placeholders(wd).items():
        out = out.replace("{%s}" % k, v)
    return out


def prepare(row_dir, run, wd):
    """The scratch copy of a row for one run: the directory copied, the subst files' @CWD@ (the
    copy), @CMC@ and @VACASK_SRC@ replaced, the hard links of `links` and the symbolic links of
    `symlinks` ({new: existing}, both relative to the copy) made."""
    shutil.copytree(row_dir, wd)
    ph = placeholders(wd)
    for rel in (run.get("subst") or []):
        p = os.path.join(wd, rel)
        with open(p) as fh:
            text = fh.read()
        text = text.replace("@CWD@", wd).replace("@CMC@", ph["cmc"]).replace("@VACASK_SRC@", ph["vacask_src"])
        with open(p, "w") as fh:
            fh.write(text)
    for key, make in (("links", os.link), ("symlinks", os.symlink)):
        for new, existing in (run.get(key) or {}).items():
            path = os.path.join(wd, new)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            make(os.path.join(wd, existing), path)


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
    if tool == "python":                               # this interpreter: a row's script
        return sys.executable, dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
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
    cwd = os.path.join(wd, expand(run["cwd"], wd)) if run.get("cwd") else wd
    deck = run.get("deck")
    args = [expand(a, wd, deck or "") for a in run.get("args", [])]
    if deck and not any("{deck}" in a for a in run.get("args", [])):
        args.append(deck)
    cmd = [program] + args
    env = dict(env)
    for k, v in (run.get("env") or {}).items():
        env[k] = expand(v, wd)
    if run["tool"] == "xyce":
        env.setdefault("PYMS_CACHE", os.path.join(os.path.dirname(wd), "pyms"))
    inside = os.path.realpath(cwd) == os.path.realpath(wd) or os.path.realpath(cwd).startswith(
        os.path.realpath(wd) + os.sep)
    logs = cwd if inside else wd                       # a cwd read in place (a CMC tree) is never written
    out_path, err_path = os.path.join(logs, "vamos_run.out"), os.path.join(logs, "vamos_run.err")
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
        prepare(row_dir, run, wd)
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
        if "times" in chk:
            self.check_times(raw, chk["times"], ctx)
        if "col" in chk:
            self.check_column(raw, chk, ctx)

    def check_times(self, raw, spec, ctx):
        """Where an engine put its points (E105, E106): of the sweep values (time unless `axis`
        names another) in [from, to], `count` [lo, hi] how many there are, `min_step` and
        `max_step` [lo, hi] the smallest and the largest step between two of them; `gap` [t, d]: no
        point of the whole axis lies closer than d to t."""
        xs = [real(v) for v in self.values(raw, spec.get("axis", "time"))]
        lo, hi = spec.get("from", -float("inf")), spec.get("to", float("inf"))
        win = [x for x in xs if lo <= x <= hi]
        steps = [b - a for a, b in zip(win, win[1:])]
        self.note(row=ctx, window=[lo, hi], times=win[:40], points=len(win))
        if "count" in spec:
            n0, n1 = spec["count"]
            self.assertTrue(n0 <= len(win) <= n1, "%s: %d points in [%r, %r], expected %d to %d: %r"
                            % (ctx, len(win), lo, hi, n0, n1, win[:40]))
        for key, pick in (("min_step", min), ("max_step", max)):
            if key in spec:
                a, b = spec[key]
                self.assertTrue(steps, "%s: fewer than two points in [%r, %r]: %r" % (ctx, lo, hi, win))
                got = pick(steps)
                self.assertTrue(a <= got <= b, "%s: %s in [%r, %r] is %r, expected %r to %r (points %r)"
                                % (ctx, key, lo, hi, got, a, b, win[:40]))
        if "gap" in spec:
            t, d = spec["gap"]
            near = [x for x in xs if abs(x - t) < d]
            self.assertEqual(near, [], "%s: points within %r of %r" % (ctx, d, t))

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
        if chk.get("read_prn"):                            # vamos's reader takes the table as it is (E124)
            plots = rawfile.read_prn(path)
            self.assertEqual(sum(len(p.points) for p in plots), len(rows), "%s: rawfile.read_prn reads %s rows"
                             % (ctx, [len(p.points) for p in plots]))
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


# -- the rows' form, on both legs ---------------------------------------------------

ROW_KEYS = {"row", "experiment", "result", "rules", "runs", "cross", "provenance"}
RUN_KEYS = {"name", "tool", "deck", "args", "cwd", "env", "gates", "launcher", "plain_env", "subst", "links", "symlinks",
            "program", "engine_env", "timeout", "signal", "status", "output", "stderr", "files", "checks",
            "record_only", "note", "in_run", "stopped_within", "group"}
TEXT_SPEC_KEYS = {"contains", "absent", "regex", "empty"}
CHECK_KEYS = {
    "raw": {"raw", "plot", "names", "names_include", "names_exclude", "names_sorted", "ambiguous", "points",
            "min_points", "plots", "declared", "no_header", "plotname_contains", "col", "row", "at", "select",
            "column", "values", "value", "minus", "rel", "abs", "axis", "axis_index", "restarts", "readable",
            "fix_points", "max_last_time", "times", "note"},
    "prn": {"prn", "columns", "rows", "min_rows", "col", "row", "value", "column", "index", "rel", "abs", "read_prn",
            "note"},
    "text": {"text", "contains", "absent", "regex", "line", "equals", "first_unmarked", "note"},
}
TIMES_KEYS = {"axis", "from", "to", "count", "min_step", "max_step", "gap"}


def row_problems(name, spec, row_dir):
    """The authoring errors of one row's expect.json: keys this module does not read (a misspelt
    check would pass unread), unknown tools and gates, decks the directory lacks; [] when well formed."""
    out = []
    extra = set(spec) - ROW_KEYS
    if extra:
        out.append("%s: unknown keys %s" % (name, sorted(extra)))
    for run in spec.get("runs", []):
        where = "%s/%s" % (name, run.get("name"))
        extra = set(run) - RUN_KEYS
        if extra:
            out.append("%s: unknown run keys %s" % (where, sorted(extra)))
        if run.get("tool") not in TOOL_GATES:
            out.append("%s: unknown tool %r" % (where, run.get("tool")))
        for g in run.get("gates", []):
            if gate(g) is None or getattr(gate(g)(lambda: None), "__unittest_skip_why__", "").startswith("unknown gate"):
                out.append("%s: unknown gate %r" % (where, g))
        if run.get("deck") and not run.get("in_run"):
            base = os.path.join(row_dir, run.get("cwd") or "")
            if "{" not in (run.get("cwd") or "") and not os.path.isfile(os.path.join(base, run["deck"])):
                out.append("%s: no deck %s" % (where, run["deck"]))
        for key in ("output", "stderr"):
            extra = set(run.get(key) or {}) - TEXT_SPEC_KEYS
            if extra:
                out.append("%s: unknown %s keys %s" % (where, key, sorted(extra)))
        extra = set(run.get("files") or {}) - {"present", "absent"}
        if extra:
            out.append("%s: unknown files keys %s" % (where, sorted(extra)))
        for k, chk in enumerate(run.get("checks", [])):
            kinds = [kind for kind in CHECK_KEYS if kind in chk]
            if len(kinds) != 1:
                out.append("%s check %d: names %s, expected one of raw, prn, text" % (where, k, kinds))
                continue
            extra = set(chk) - CHECK_KEYS[kinds[0]]
            if extra:
                out.append("%s check %d: unknown %s keys %s" % (where, k, kinds[0], sorted(extra)))
            extra = set(chk.get("times") or {}) - TIMES_KEYS
            if extra:
                out.append("%s check %d: unknown times keys %s" % (where, k, sorted(extra)))
    return out


class TestRowForm(unittest.TestCase):
    """Both legs, no engine: every row's expect.json is well formed for this module (the engine runs are
    WSL's, so a misspelt key would otherwise first show there, or never)."""

    def test_the_t1_rows_are_harvested(self):
        """§13's tier list of the T1 rows, every one with its directory (E115-E129 and E131: phase 2c,
        R-tests-20 / R-emit-19); E14, E44 and the other rows are other tiers."""
        want = (list(range(1, 14)) + list(range(15, 44)) + [45, 47, 51] + list(range(63, 76)) + [77, 81, 82, 84, 95, 96]
                + list(range(102, 110)) + [114] + list(range(115, 130)) + [131])
        got = sorted(int(os.path.basename(d)[1:]) for d, _ in load_rows())
        self.assertEqual(sorted(set(want) - set(got)), [], "T1 rows without a directory")
        self.assertEqual(sorted(set(got) - set(want)), [], "directories of rows that are no T1 row")
        for d, spec in load_rows():
            self.assertEqual(spec["row"], "E%d" % int(os.path.basename(d)[1:]), d)

    def test_every_row(self):
        rows = load_rows()
        for row_dir, spec in rows:
            with self.subTest(row=os.path.basename(row_dir)):
                self.assertEqual(row_problems(os.path.basename(row_dir), spec, row_dir), [])

    def test_a_misspelt_key_is_a_problem(self):
        spec = {"row": "E0", "runs": [{"name": "r", "tool": "xyce", "checks": [{"raw": "a.raw", "point": 3},
                                                                                 {"raw": "a.raw", "times": {"gaps": []}}],
                                       "output": {"contain": []}, "gates": ["needs_nothing"]}]}
        probs = row_problems("E0", spec, FACTS)
        self.assertTrue(any("unknown raw keys ['point']" in p for p in probs), probs)
        self.assertTrue(any("unknown times keys ['gaps']" in p for p in probs), probs)
        self.assertTrue(any("unknown output keys ['contain']" in p for p in probs), probs)
        self.assertTrue(any("unknown gate 'needs_nothing'" in p for p in probs), probs)


class TestTimesCheck(FactCase):
    """Both legs, no engine: the `times` check that states E105 (the step size across a knee) and E106
    (no point at a breakpoint) directly, on a synthetic plot."""

    def plot(self, times):
        raw = rawfile.Raw()
        raw.variables = [("time", "time"), ("v(a)", "voltage")]
        raw.points = [(t, 0.0) for t in times]
        return raw

    def test_holds_and_fails(self):
        knee = self.plot([1.4744e-3, 1.5144e-3, 1.5544e-3, 1.5944e-3, 1.6344e-3])
        self.check_times(knee, {"from": 1.5e-3, "to": 1.61e-3, "count": [3, 3], "min_step": [3.99e-5, 4.01e-5],
                                "max_step": [3.99e-5, 4.01e-5]}, "knee")
        self.check_times(knee, {"gap": [1.535e-3, 1e-6]}, "gap")
        for spec, what in (({"from": 1.5e-3, "to": 1.61e-3, "count": [4, 10]}, "3 points in"),
                           ({"from": 1.5e-3, "to": 1.61e-3, "min_step": [0.0, 1e-6]}, "min_step in"),
                           ({"from": 1.5e-3, "to": 1.61e-3, "max_step": [0.0, 1e-5]}, "max_step in"),
                           ({"from": 1.55e-3, "to": 1.56e-3, "min_step": [0.0, 1.0]}, "fewer than two points"),
                           ({"gap": [1.5544e-3, 1e-6]}, "points within")):
            with self.subTest(what=what):
                with self.assertRaises(AssertionError) as cm:
                    self.check_times(knee, spec, "knee")
                self.assertIn(what, str(cm.exception))


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


@unittest.skipUnless(LINUX, "pipes and pseudo-terminals: Linux/WSL")
class TestStdoutBuffering(FactCase):
    """E115's and E123's output halves: when the engines' lines arrive through a pipe, a
    pseudo-terminal (unsized, and 80 columns wide) and a regular file.  §7.1: run_vacask reads VACASK
    through an unsized pty, line-buffered and without a progress bar; §2.7: SIGUSR1's status line
    reads Xyce's last `Percent complete` from the deck's log while Xyce runs."""

    def copy(self, row):
        wd = os.path.join(self.tmp, row)
        shutil.copytree(os.path.join(FACTS, row), wd)
        return wd

    def through_a_pty(self, cmd, wd, env, cols=None):
        """(rc, exit time, [(time, bytes)]) of cmd with its stdout and stderr on a fresh pty."""
        import fcntl
        import pty
        import select
        import struct
        import termios
        master, slave = pty.openpty()
        if cols:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, cols, 0, 0))
        t0 = time.time()
        proc = subprocess.Popen(cmd, cwd=wd, env=env, stdin=subprocess.DEVNULL, stdout=slave, stderr=slave,
                                start_new_session=True)
        os.close(slave)
        chunks = []
        try:
            while True:
                ready, _, _ = select.select([master], [], [], 0.5)
                if ready:
                    try:
                        data = os.read(master, 1 << 16)
                    except OSError:                      # EIO: every writer of the pty is gone
                        break
                    if not data:
                        break
                    chunks.append((time.time() - t0, data))
                elif proc.poll() is not None:
                    break
            rc = proc.wait(300)
        finally:
            os.close(master)
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        return rc, time.time() - t0, chunks

    @needs_vacask
    def test_vacask_holds_its_lines_in_a_pipe(self):
        wd = self.copy("E115")
        t0 = time.time()
        proc = subprocess.Popen([engines.vacask_bin(), "long.sim"], cwd=wd, env=engine_env("vacask"),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
        reads = []
        while True:
            data = os.read(proc.stdout.fileno(), 1 << 16)
            if not data:
                break
            reads.append((time.time() - t0, data))
        rc = proc.wait(300)
        t_exit = time.time() - t0
        err = proc.stderr.read()
        proc.stdout.close()
        proc.stderr.close()
        text = b"".join(d for _, d in reads).decode(errors="replace")
        self.note(row="E115/pipe", rc=rc, exit=t_exit, reads=[(round(t, 2), len(d)) for t, d in reads])
        self.assertEqual(rc, 0, text[-1500:])
        self.assertIn("Running analysis 'tr1'.", text)
        self.assertEqual(err, b"")
        self.assertGreater(t_exit, 2.0, "the run is too short to tell (%.2f s)" % t_exit)
        self.assertGreater(reads[0][0], t_exit - 0.5, "VACASK's first line came %.2f s before its exit (%.2f s)"
                           % (t_exit - reads[0][0], t_exit))

    @needs_vacask
    def test_vacask_lines_arrive_as_printed_on_a_pty_without_a_bar(self):
        wd = self.copy("E115")
        rc, t_exit, chunks = self.through_a_pty([engines.vacask_bin(), "long.sim"], wd, engine_env("vacask"))
        text = b"".join(d for _, d in chunks).decode(errors="replace")
        at = [t for t, d in chunks if b"Running analysis 'tr1'." in d]
        self.note(row="E115/pty", rc=rc, exit=t_exit, tr1_line=at, chunks=len(chunks))
        self.assertEqual(rc, 0, text[-1500:])
        self.assertTrue(at, text[-1500:])
        self.assertLess(at[0], t_exit - 1.0, "the tr1 line came at %.2f s of %.2f s" % (at[0], t_exit))
        self.assertNotRegex(text, r"\|=+ *\d+% *=* *\|", "a progress bar on an unsized pty")

    @needs_vacask
    def test_vacask_progress_bar_needs_a_terminal_width(self):
        wd = self.copy("E115")
        rc, t_exit, chunks = self.through_a_pty([engines.vacask_bin(), "long.sim"], wd, engine_env("vacask"), cols=80)
        text = b"".join(d for _, d in chunks).decode(errors="replace")
        self.note(row="E115/pty80", rc=rc, exit=t_exit, tail=text[-300:])
        self.assertEqual(rc, 0, text[-1500:])
        self.assertRegex(text, r"\|=+ *\d+% *=* *\| [0-9.]+s @ [0-9.e+-]+s\r")

    @needs_xyce
    def test_xyce_stdout_into_a_file_grows_while_it_runs(self):
        """2026-10-17: Xyce writes its stdout into a file line by line (its Percent complete lines are
        there while it runs), where the design's E123 row records block buffering."""
        wd = self.copy("E123")
        out = os.path.join(wd, "xlong.out")
        seen = None
        with open(out, "w") as fh:
            t0 = time.time()
            proc = subprocess.Popen([engines.xyce_bin(), "xlong.cir"], cwd=wd, env=engine_env("xyce"),
                                    stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            try:
                while proc.poll() is None:
                    time.sleep(0.2)
                    if seen is None and "Percent complete" in _read(out):
                        seen = time.time() - t0
                rc = proc.wait(300)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
        t_exit = time.time() - t0
        self.note(row="E123/file", rc=rc, exit=t_exit, first_percent=seen)
        self.assertEqual(rc, 0, _read(out)[-1500:])
        self.assertGreater(t_exit, 2.0, "the run is too short to tell (%.2f s)" % t_exit)
        self.assertIsNotNone(seen, "no Percent complete in the file before Xyce exited")
        self.assertLess(seen, t_exit - 1.0, "the first Percent complete reached the file at %.2f s of %.2f s"
                        % (seen, t_exit))


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
            prepare(row_dir, run, wd)
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
