"""S6, phase 1b (docs/VAMOS_SPECTRE_DESIGN.md §12 "Phase 1b", §11 T2 e2e 1-10): the netlist chain
spectre.parse -> plan.build -> signals.resolve -> render on both engines for the e2e decks 1-10, without
flow.py, and for every later e2e deck that holds an engine_expect.json (phase 2; a deck without one is
CLI-level and not a chain case).

    cd tests/vamos && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_spectre_chain

Each case fixtures/spectre/e2e/<nn>_<slug>/ holds deck.scs (with its aux files: libraries, Verilog-A
modules, a second deck), expect.json (the Spectre-level checks of §11, phase 2's flow reads them) and
engine_expect.json: the same checks in engine terms, per engine: the exit status of the engine run (2 =
refused before any run, with the message), the steps with the engine columns names_for gives for the
SignalMap's Spectre names, and values at points of the engine's raw output.  render/<engine>/ holds the
rendered decks (goldens; VAMOS_REGEN_GOLDEN=1 rewrites them).

TestChainRenders (both legs, no engine)   every case renders on both engines, or is refused as
    engine_expect says; names_for agrees with render's names; the columns of engine_expect are the
    SignalMap's; the rendered decks equal the goldens
TestChainRuns (WSL; needs_vacask / needs_xyce)   the rendered decks run on the engines through
    vamos.ams.engines, the raw output (rawfile.read_all / read_prn) matches engine_expect

The Xyce state replay (§7.2: one deck per analysis, rendered from the IR with the alters up to that point
applied) is phase 2's runner's: `replay` here is vamos.spectre.run_xyce.replay (phase 1b kept its own
reference copy, which phase 2 replaced by the runner's), applied to the render copy for the decks of this
tier (Action.args: target, value, source).  The Verilog-A sources are named by their base names
(`hdl_beside`) and copied beside the decks with run_xyce.place_va, which also copies the files they
`include: Xyce's PyMS loses the parameters of two modules named by absolute paths (phase 1b, E: deck 08).
"""

import json
import os
import shutil
import unittest
from typing import Dict, List, Optional

from vamos_testlib import TempDir, run  # noqa: F401
from spectre_testlib import SPECTRE_FIXTURES, have_vacask, have_xyce  # noqa: E402

from vamos.netlist import ir, plan as PL, rawfile, signals as SG, spectre, vacask, xyce  # noqa: E402
from vamos.notes import ERROR, NoteError  # noqa: E402
from vamos.spectre import run_xyce  # noqa: E402  (the state replay and the Verilog-A placement, §7.2)
from vamos.spectre.job import Settings  # noqa: E402

E2E = os.path.join(SPECTRE_FIXTURES, "e2e")
DECKS = sorted(d for d in os.listdir(E2E) if os.path.isdir(os.path.join(E2E, d)) and d[:2].isdigit())
# the chain cases: every e2e directory that holds an engine_expect.json; a directory without one is a
# CLI-level deck (cpp, a command-line option), which phase 2's harness runs from its expect.json alone
CASES = [d for d in DECKS if os.path.isfile(os.path.join(E2E, d, "engine_expect.json"))]
ENGINES = ("vacask", "xyce")
REGEN = bool(os.environ.get("VAMOS_REGEN_GOLDEN"))


# -- the chain -------------------------------------------------------------------------------------------

class ChainResult:
    """One deck through the chain for one engine: the IR, the plan, the render copy, the SignalMap, the
    notes, the name map render filled, the one names_for computed, the rendered decks and the steps."""

    def __init__(self) -> None:
        self.nl: Optional[ir.Netlist] = None
        self.plan: Optional[PL.RunPlan] = None
        self.nl2: Optional[ir.Netlist] = None
        self.sigmap: Optional[SG.SignalMap] = None
        self.notes = []  # type: list
        self.names: Dict[str, Dict] = {}
        self.names_for: Dict[str, Dict] = {}
        self.decks: Dict[str, str] = {}
        self.steps: List[PL.AnalysisStep] = []
        self.hdl: List[str] = []                 # the parser's absolute Verilog-A paths

    def column(self, step_id: str, spectre_name: str) -> str:
        """The engine column of a Spectre signal name of one step (SignalMap -> Ref -> names)."""
        for ref, sname, _ptype in self.sigmap.per_step[step_id]:
            if sname == spectre_name:
                return self.names[step_id][ref]
        raise KeyError("%s: no signal %r in step %s (signals: %s)"
                       % (self.nl.title, spectre_name, step_id, [s[1] for s in self.sigmap.per_step[step_id]]))

    def signals(self, step_id: str) -> List[str]:
        return [s[1] for s in self.sigmap.per_step[step_id]]


def chain(deck: str, engine: str, hdl_beside: bool = True) -> ChainResult:
    """spectre.parse -> plan.build -> signals.resolve -> render for one engine; NoteError propagates (a
    refusal).  hdl_beside: the Verilog-A sources are named by their base names, to be copied beside the
    decks (see the module docstring)."""
    deck = os.path.abspath(deck)
    r = ChainResult()
    r.nl = spectre.parse(deck, os.path.dirname(deck), spectre.SpectreParseOpts())
    r.plan = PL.build(r.nl, engine, Settings())
    r.nl2, r.sigmap, r.notes = SG.resolve(r.nl, r.plan)
    r.hdl = list(r.nl2.hdl)
    if hdl_beside:
        r.nl2.hdl = [os.path.basename(h) for h in r.nl2.hdl]
    r.steps = [a.step for a in r.plan.actions if a.op == "analysis" and a.step is not None]
    if engine == "vacask":
        r.decks["vamos.sim"] = vacask.render(r.nl2, plan=r.plan, sigmap=r.sigmap, names=r.names)
        r.names_for = vacask.names_for(r.nl2, r.plan, r.sigmap)
    else:
        state = r.nl2
        for act in r.plan.actions:
            if act.op == "alter":
                state = replay(state, act)
            elif act.op == "analysis" and act.step is not None:
                r.decks[act.step.id + ".cir"] = xyce.render(state, step=act.step, sigmap=r.sigmap, names=r.names)
        r.names_for = xyce.names_for(r.nl2, r.plan, r.sigmap)
    return r


# §7.2's state replay, the runner's (phase 2): a copy of the netlist with one alter action applied in IR
# spellings; find_instance names an instance through the subckt definitions
replay = run_xyce.replay
find_instance = run_xyce.find_instance


# -- the engine runs ---------------------------------------------------------------------------------------

class EngineRun:
    def __init__(self) -> None:
        self.status = 0
        self.stdout = ""
        self.problems: List[str] = []
        self.plots: Dict[str, List[rawfile.Raw]] = {}      # step id -> the plots of its raw output


def run_engine(r: ChainResult, engine: str, d: str) -> EngineRun:
    """Write the decks (and the Verilog-A sources beside them) into d and run the engine through
    vamos.ams.engines (§7.1, §7.2); every step's raw output is read back."""
    from vamos.ams import engines
    os.makedirs(d, exist_ok=True)
    for fn, text in r.decks.items():
        with open(os.path.join(d, fn), "w") as fh:
            fh.write(text)
    placed = run_xyce.place_va(r.hdl, d)                  # each source with the files it `includes
    if r.hdl and r.nl2 is not None and list(r.nl2.hdl) == [os.path.basename(h) for h in r.hdl]:
        assert placed == list(r.nl2.hdl), (placed, r.nl2.hdl)   # the decks name what place_va placed
    env = engines.env_for(engine, None, dict(os.environ))
    res = EngineRun()
    if engine == "vacask":
        p = run([engines.vacask_bin(), "vamos.sim"], cwd=d, env=env)
        res.status, res.stdout = p.returncode, p.stdout
    else:
        env["PYMS_CACHE"] = os.path.join(d, "pyms")
        out = []
        for st in r.steps:
            p = run([engines.xyce_bin(), st.id + ".cir"], cwd=d, env=env)
            out.append(p.stdout)
            res.status = max(res.status, p.returncode)
            probs = [n.message for n in xyce.output_problems(p.stdout, r.nl2)]
            if probs:
                res.problems += probs
                res.status = max(res.status, 2)
        res.stdout = "\n".join(out)
    for st in r.steps:
        for ext in (".raw", ".prn"):
            path = os.path.join(d, st.id + ext)
            if os.path.exists(path):
                res.plots[st.id] = rawfile.read_prn(path) if ext == ".prn" else rawfile.read_all(path)
    return res


# -- the engine-level expectations -------------------------------------------------------------------------

AXES = ("time", "frequency", "freq")


def _real(v):
    return v.real if isinstance(v, complex) else v


def _close(got, want, rel: float, abs_: float) -> bool:
    if isinstance(want, (list, tuple)):
        g = complex(got)
        return _close(g.real, want[0], rel, abs_) and _close(g.imag, want[1], rel, abs_)
    return abs(_real(got) - want) <= abs_ + rel * abs(want)


def axis_index(plot: rawfile.Raw) -> Optional[int]:
    for k, (name, _typ) in enumerate(plot.variables):
        if name.lower() in AXES:
            return k
    return None


def select_rows(plot: rawfile.Raw, chk: dict) -> List[tuple]:
    """The rows of a plot a check reads: all of them, or those whose sweep columns hold the given values
    (VACASK's one plot per swept analysis, §7.1)."""
    rows = list(plot.points)
    for col, value in (chk.get("select") or {}).items():
        k = plot.exact(col)
        rows = [p for p in rows if _close(p[k], value, 1e-9, 0.0)]
    return rows


def groups(plot: rawfile.Raw) -> int:
    """The sweep groups of a VACASK plot: the restarts of its time/frequency axis, else its rows."""
    k = axis_index(plot)
    if k is None:
        return len(plot.points)
    n, prev = 0, None
    for p in plot.points:
        x = _real(p[k])
        if prev is None or x <= prev:
            n += 1
        prev = x
    return n


def value_at(plot: rawfile.Raw, rows: List[tuple], col: str, chk: dict):
    k = plot.exact(col)
    if "row" in chk:
        return rows[chk["row"]][k]
    if "at" in chk:
        a = axis_index(plot)
        if a is None:
            raise AssertionError("%s has no time/frequency axis for at=%s" % (plot.path, chk["at"]))
        x = chk["at"]
        xs = [_real(p[a]) for p in rows]
        for i in range(1, len(rows)):
            if xs[i - 1] <= x <= xs[i]:
                y0, y1 = rows[i - 1][k], rows[i][k]
                if xs[i] == xs[i - 1]:
                    return y1
                return y0 + (y1 - y0) * (x - xs[i - 1]) / (xs[i] - xs[i - 1])
        if rows and abs(xs[-1] - x) <= 1e-9 * max(abs(x), 1e-30):
            return rows[-1][k]
        raise AssertionError("%s: x=%s is outside %s..%s" % (plot.path, x, xs[0] if xs else None, xs[-1] if xs else None))
    return rows[0][k]


def check_steps(tc: unittest.TestCase, r: ChainResult, exp: dict) -> None:
    """The steps and the engine columns engine_expect names (names_for's, §6.4)."""
    steps = {s.id: s for s in r.steps}
    tc.assertEqual(sorted(steps), sorted(exp.get("steps", {})), "the plan's steps")
    for sid, se in exp.get("steps", {}).items():
        st = steps[sid]
        tc.assertEqual((st.name, st.kind), (se["name"], se["kind"]), sid)
        for sname, col in se.get("columns", {}).items():
            tc.assertEqual(r.column(sid, sname), col, "%s %s" % (sid, sname))
        if "signals" in se:
            tc.assertEqual(r.signals(sid), se["signals"], sid)


def check_run(tc: unittest.TestCase, r: ChainResult, res: EngineRun, exp: dict) -> None:
    """The engine run against engine_expect: the exit status, the columns in the raw output, the checks."""
    tc.assertEqual(res.status, exp["status"], "engine status; problems %s\n%s" % (res.problems, res.stdout[-2500:]))
    for sid, se in exp.get("steps", {}).items():
        plots = res.plots.get(sid)
        tc.assertTrue(plots, "no raw output of %s" % sid)
        for sname, col in se.get("columns", {}).items():
            plots[0].exact(col)                                   # KeyError when the engine did not write it
    for chk in exp.get("checks", []):
        sid = chk["step"]
        plots = res.plots[sid]
        label = json.dumps(chk)
        if "plots" in chk:
            tc.assertEqual(len(plots), chk["plots"], label)
            continue
        plot = plots[chk.get("plot", 0)]
        if "groups" in chk:
            tc.assertEqual(groups(plot), chk["groups"], label)
            continue
        if "absent" in chk:
            for col in chk["absent"]:
                tc.assertNotIn(col, plot.names(), label)
            continue
        rows = select_rows(plot, chk)
        if "points" in chk:
            tc.assertEqual(len(rows), chk["points"], label)
            continue
        rel, abs_ = chk.get("rel", 1e-6), chk.get("abs", 0.0)
        if "equal" in chk:
            cols = [r.column(sid, s) for s in chk["equal"]]
            vals = [value_at(plot, rows, c, chk) for c in cols]
            for v in vals[1:]:
                tc.assertTrue(_close(v, _real(vals[0]), rel, abs_), "%s: %s" % (label, vals))
            continue
        col = chk["col"] if "col" in chk else r.column(sid, chk["signal"])
        got = value_at(plot, rows, col, chk)
        tc.assertTrue(_close(got, chk["value"], rel, abs_), "%s: got %r" % (label, got))


# -- the cases -------------------------------------------------------------------------------------------

def case_dir(case: str) -> str:
    return os.path.join(E2E, case)


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def engine_cases(case: str):
    """(deck file, engine, expectation) for every engine entry of a case's engine_expect.json, the main
    deck first, then the `also` decks beside it (a second deck with its own expectations)."""
    exp = load_json(os.path.join(case_dir(case), "engine_expect.json"))
    out = []
    for engine in ENGINES:
        if engine in exp:
            out.append(("deck.scs", engine, exp[engine]))
    for also in exp.get("also", []):
        for engine in ENGINES:
            if engine in also:
                out.append((also["deck"], engine, also[engine]))
    return out


def refused(tc: unittest.TestCase, deck: str, engine: str, exp: dict) -> None:
    with tc.assertRaises(NoteError) as cm:
        chain(deck, engine)
    msgs = [n.message for n in cm.exception.notes if n.severity == ERROR]
    tc.assertTrue(any(exp["refused"] in m for m in msgs), "%s on %s: %r not in %s" % (deck, engine, exp["refused"], msgs))


def golden_dir(case: str, deck: str, engine: str) -> str:
    sub = engine if deck == "deck.scs" else "%s_%s" % (os.path.splitext(deck)[0], engine)
    return os.path.join(case_dir(case), "render", sub)


class TestChainRenders(unittest.TestCase):
    """Both legs: parse -> build -> resolve -> render, the names, the render goldens."""

    def check_case(self, case: str) -> None:
        for deck, engine, exp in engine_cases(case):
            with self.subTest(deck=deck, engine=engine):
                path = os.path.join(case_dir(case), deck)
                if exp.get("status") == 2 and "refused" in exp:
                    refused(self, path, engine, exp)
                    continue
                r = chain(path, engine)
                self.assertEqual(r.names_for, r.names, "names_for and render's names differ")
                check_steps(self, r, exp)
                self.assertFalse([n for n in r.plan.notes + r.notes if n.severity == ERROR])
                gdir = golden_dir(case, deck, engine)
                if REGEN:
                    shutil.rmtree(gdir, ignore_errors=True)
                    os.makedirs(gdir)
                    for fn, text in r.decks.items():
                        with open(os.path.join(gdir, fn), "w", newline="\n") as fh:
                            fh.write(text)
                self.assertEqual(sorted(os.listdir(gdir)), sorted(r.decks), "the rendered decks of %s" % gdir)
                for fn, text in r.decks.items():
                    with open(os.path.join(gdir, fn), encoding="utf-8") as fh:
                        want = fh.read()
                    self.assertEqual(text, want, "%s/%s differs from its golden" % (gdir, fn))

    def test_every_case_has_both_expectation_files(self):
        self.assertEqual([c[:2] for c in CASES if int(c[:2]) <= 10], ["%02d" % k for k in range(1, 11)], CASES)
        for case in CASES:
            exp = load_json(os.path.join(case_dir(case), "expect.json"))
            for key in ("argv", "engines", "status", "files", "values", "cross_engine", "messages"):
                self.assertIn(key, exp, case)
            self.assertTrue(os.path.isfile(os.path.join(case_dir(case), "engine_expect.json")), case)


def _render_test(case):
    def test(self):
        self.check_case(case)
    test.__name__ = "test_" + case
    test.__doc__ = "e2e %s renders on both engines (§11 T2 %d)" % (case, int(case[:2]))
    return test


for _case in CASES:
    setattr(TestChainRenders, "test_" + _case, _render_test(_case))


class TestChainRuns(TempDir):
    """WSL: the rendered decks on the engines against engine_expect.json (both engines when installed)."""

    def check_case(self, case: str) -> None:
        available = {"vacask": have_vacask(), "xyce": have_xyce()}
        if not any(available.values()):
            self.skipTest("needs VACASK or Xyce (Linux/WSL)")
        ran = 0
        for deck, engine, exp in engine_cases(case):
            if not available[engine]:
                continue
            with self.subTest(deck=deck, engine=engine):
                path = os.path.join(case_dir(case), deck)
                if exp.get("status") == 2 and "refused" in exp:
                    refused(self, path, engine, exp)
                    ran += 1
                    continue
                ran += 1
                r = chain(path, engine)
                d = os.path.join(self.tmp, "%s_%s_%s" % (case, os.path.splitext(deck)[0], engine))
                res = run_engine(r, engine, d)
                check_run(self, r, res, exp)
        self.assertTrue(ran)


def _run_test(case):
    def test(self):
        self.check_case(case)
    test.__name__ = "test_" + case
    test.__doc__ = "e2e %s runs on the installed engines (§11 T2 %d)" % (case, int(case[:2]))
    return test


for _case in CASES:
    setattr(TestChainRuns, "test_" + _case, _run_test(_case))


if __name__ == "__main__":
    unittest.main()
