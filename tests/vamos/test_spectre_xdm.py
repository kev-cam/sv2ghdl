"""S6, phase 1b (docs/VAMOS_SPECTRE_DESIGN.md §11 T3a, §12 "Phase 1b"): the 12 Spectre decks of
Xyce_Regression Netlists/XDM/SPECTRE (fixtures/spectre/xdm/<CATEGORY>/*.cir.spectre) through the chain
spectre.parse -> plan.build -> signals.resolve -> render on both engines, compared with their hand-written
Xyce golds (*.cir) run on Xyce at XDM's tolerances (absTol 1e-5, relTol 1e-3, zeroTol 1e-10, from the
*.cir.sh run scripts), and vamos on VACASK with vamos on Xyce within 1 % of full scale.  XDM's pointwise
tolerances assume one stepping policy (its translated netlist and the gold run Xyce's defaults), while
vamos applies Spectre's maxstep = T/50 (§5.4), so the gold is re-run with vamos's .TRAN controls; the two
Xyce runs then take the same steps.  VACASK's grid differs from Xyce's, so the Xyce run is interpolated
at VACASK's time points and the tolerance is 1 % of the signal's full-scale amplitude (a pointwise
relative tolerance cannot be met at a sine's zero crossing across two grids); isolated outliers at a
breakpoint the reference run did not sample are allowed (TestXdmRuns.compare_all).

    cd tests/vamos && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_spectre_xdm

The name map (§11 T3a): the gold's `RR1`, `VV1`, `XX1:RR1`, `XI2:VL1` are Spectre's `R1`, `V1`, `X1.R1:1`,
`I2.VL1` (xdm prefixes every device with its letter).  Expected dispositions: `bsimsoi_translation` exits 2
naming bsimsoi on both engines (not a v1 master, §3.9); `case_sensitive_names` is refused on Xyce (r0 and
R0 differ only in case, §6.4) and runs on VACASK; `statistics_line` runs at nominal (§3.12); the info
statements and the options sensfile, checklimitdest, rforce, digits, cols and pivrel give notes (§3.6,
§3.10); `dc_directive`'s `dev=ia` without param= sweeps the source's dc (UG p.168).  A disagreement with a
gold is settled against the manual: the golds are XDM's own reading.

TestXdmChain (both legs)   every deck parses, plans and renders on both engines, or is refused as above;
    the notes; the gold columns map onto the SignalMap
TestXdmRuns (WSL)          vamos on Xyce against the gold run (XDM's tolerances); vamos on VACASK against
    the gold run (1 %)
"""

import glob
import json
import os
import shutil
import unittest
from typing import Dict, List, Optional, Tuple

from vamos_testlib import TempDir, run  # noqa: F401
from spectre_testlib import SPECTRE_FIXTURES, have_vacask, have_xyce  # noqa: E402
from test_spectre_chain import AXES, ChainResult, EngineRun, _real, axis_index, chain, run_engine  # noqa: E402

from vamos.netlist import rawfile  # noqa: E402
from vamos.notes import ERROR, NOTE, NoteError  # noqa: E402

XDM = os.path.join(SPECTRE_FIXTURES, "xdm")
DECKS = sorted(glob.glob(os.path.join(XDM, "*", "*.cir.spectre")))
NAMES = [os.path.basename(p)[:-len(".cir.spectre")] for p in DECKS]
ABS_TOL, REL_TOL, ZERO_TOL = 1e-5, 1e-3, 1e-10          # the *.cir.sh scripts' values
VACASK_REL = 0.01                                        # VACASK against vamos on Xyce, of full scale (§11 T3a)
OUTLIERS, OUTLIER_MAX = 0.02, 0.10                       # the full-scale comparison's outlier rule (compare_all)
ENGINES = ("vacask", "xyce")

# the expected refusals: deck -> engine -> an error substring
REFUSED = {
    "bsimsoi_translation": {"vacask": "bsimsoi", "xyce": "bsimsoi"},
    "case_sensitive_names": {"xyce": "differ only in case"},
}
# the ADE decks carry seven info statements and these simulatorOptions, each a note
ADE_INFO = ("finalTimeOP", "modelParameter", "element", "outputParameter", "designParamVals", "primitives", "subckts")
ADE_OPTIONS = ("sensfile", "checklimitdest", "rforce", "digits", "cols", "pivrel")
NOT_ADE = ("bsimsoi_translation", "dc_directive")


def deck_path(name: str) -> str:
    return next(p for p in DECKS if os.path.basename(p) == name + ".cir.spectre")


def gold_path(name: str) -> str:
    return deck_path(name)[:-len(".spectre")]


def gold_column_to_spectre(col: str) -> Tuple[str, str]:
    """A gold .PRINT column into (kind, Spectre name): V(XI2:VL1) -> ('v', 'I2.VL1'), I(XX1:RR1) -> ('i',
    'X1.R1:1'); xdm's device names repeat the letter (RR1, VV1, XX1, XI2 for I2)."""
    kind, inner = col[0].lower(), col[2:-1]
    parts = inner.split(":")
    if kind == "i":
        parts = [p[1:] if len(p) > 1 and p[0].upper() in "RLCVIXDQMK" else p for p in parts]
        return "i", ".".join(parts) + ":1"
    if len(parts) > 1:
        parts = [p[1:] if p[0].upper() == "X" else p for p in parts[:-1]] + parts[-1:]
    return "v", ".".join(parts)


def spectre_signal(r: ChainResult, step_id: str, want: str) -> Optional[str]:
    """The SignalMap's Spectre name matching a gold-derived name, case-insensitively (Xyce prints upper
    case); a current `X:1` also matches the vsource form `X:p`."""
    names = r.signals(step_id)
    low = {n.lower(): n for n in names}
    for cand in (want, want[:-2] + ":p" if want.endswith(":1") else want):
        if cand.lower() in low:
            return low[cand.lower()]
    return None


def interpolate(xs: List[float], ys: List[float], x: float) -> float:
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    if xs[hi] == xs[lo]:
        return ys[hi]
    return ys[lo] + (ys[hi] - ys[lo]) * (x - xs[lo]) / (xs[hi] - xs[lo])


def within(a: float, b: float, abs_tol: float, rel_tol: float, scale: Optional[float]) -> bool:
    """XDM's criterion: equal when |a - b| <= absTol or |a - b| <= relTol * |b| (a reference value below
    zeroTol is compared as 0); with scale, relTol applies to that full-scale amplitude instead."""
    if abs(b) < ZERO_TOL:
        b = 0.0
    return abs(a - b) <= abs_tol or abs(a - b) <= rel_tol * (abs(b) if scale is None else scale)


def compare(gold: rawfile.Raw, gold_col: str, plot: rawfile.Raw, col: str, abs_tol: float, rel_tol: float,
            full_scale: bool = False) -> Tuple[int, int, str]:
    """The vamos run against a reference run: at every vamos time point against the reference interpolated
    there when both have a time axis, else row by row; relTol against the reference value (XDM's pointwise
    criterion) or, full_scale, against the reference's largest magnitude (module docstring); returns (bad
    points, points, the worst point's text)."""
    gk, vk = gold.exact(gold_col), plot.exact(col)
    ga, va = axis_index(gold), axis_index(plot)
    gold_vals = [_real(p[gk]) for p in gold.points]
    scale = max([abs(g) for g in gold_vals] + [0.0])
    if ga is not None and va is not None:
        gxs = [_real(p[ga]) for p in gold.points]
        pairs = [(_real(p[vk]), interpolate(gxs, gold_vals, _real(p[va])), _real(p[va])) for p in plot.points]
    else:
        if len(plot.points) != len(gold.points):
            return len(gold.points), len(gold.points), "%d vamos points against %d gold points" % (len(plot.points), len(gold.points))
        pairs = [(_real(p[vk]), g, k) for k, (p, g) in enumerate(zip(plot.points, gold_vals))]
    bad, worst, worst_text = 0, -1.0, ""
    for a, b, x in pairs:
        if not within(a, b, abs_tol, rel_tol, scale if full_scale else None):
            bad += 1
        dev = abs(a - b) / max(scale, abs_tol)
        if dev > worst:
            worst, worst_text = dev, "at %s: vamos %r reference %r (|d| / full scale %.3g = %.3g)" % (x, a, b, scale, dev)
    return bad, len(pairs), worst_text


class TestXdmChain(unittest.TestCase):
    """Both legs: the chain on every deck, the refusals, the notes and the name map."""

    def test_twelve_decks(self):
        self.assertEqual(len(DECKS), 12, DECKS)

    def check_deck(self, name: str) -> None:
        path = deck_path(name)
        for engine in ENGINES:
            with self.subTest(engine=engine):
                refusal = REFUSED.get(name, {}).get(engine)
                if refusal:
                    with self.assertRaises(NoteError) as cm:
                        chain(path, engine)
                    msgs = [n.message for n in cm.exception.notes if n.severity == ERROR]
                    self.assertTrue(any(refusal in m for m in msgs), msgs)
                    continue
                r = chain(path, engine)
                self.assertEqual(r.names_for, r.names)
                self.assertEqual(len(r.steps), 1, [s.kind for s in r.steps])
                notes = [n.message for n in r.nl.notes + r.plan.notes + r.notes]
                if name not in NOT_ADE:
                    for info in ADE_INFO:
                        self.assertTrue(any(n.startswith(info + " info") for n in notes), (info, notes))
                    for opt in ADE_OPTIONS:
                        self.assertTrue(any(("options %s=" % opt) in n for n in notes), (opt, notes))
                if name == "statistics_line":
                    self.assertTrue(r.nl.statistics)                 # parsed; the run is nominal (§3.12)
                    self.assertEqual(r.steps[0].kind, "tran")
                if name == "dc_directive":
                    st = r.steps[0]
                    self.assertEqual((st.kind, st.own.target), ("dc", ("instance", "ia", "dc")))
                    self.assertEqual(st.own.spec.mode, "step")
                # every gold .PRINT column names a SignalMap signal
                gold = gold_columns(name)
                for col in gold:
                    kind, want = gold_column_to_spectre(col)
                    self.assertIsNotNone(spectre_signal(r, r.steps[0].id, want), (col, want, r.signals(r.steps[0].id)))


def gold_columns(name: str) -> List[str]:
    """The .PRINT columns of a gold deck (TRAN or DC), as written."""
    with open(gold_path(name), encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    out: List[str] = []
    k = 0
    while k < len(lines):
        line = lines[k].strip()
        if line.upper().startswith(".PRINT"):
            fields = line.split()[2:]
            k += 1
            while k < len(lines) and lines[k].strip().startswith("+"):
                fields += lines[k].strip()[1:].split()
                k += 1
            out += fields
            continue
        k += 1
    return out


def _chain_test(name):
    def test(self):
        self.check_deck(name)
    test.__name__ = "test_" + name
    test.__doc__ = "XDM %s through the chain on both engines (§11 T3a)" % name
    return test


for _name in NAMES:
    setattr(TestXdmChain, "test_" + _name, _chain_test(_name))


class TestXdmRuns(TempDir):
    """WSL: vamos on Xyce against the gold run (XDM's pointwise tolerances, the gold re-run with vamos's
    .TRAN controls so that both Xyce runs share a stepping policy, as XDM's own comparison assumes); vamos
    on VACASK against vamos on Xyce (1 % of full scale, the engines' time grids differ)."""

    def gold_run(self, name: str, tran_line: Optional[str]) -> rawfile.Raw:
        from vamos.ams import engines
        d = os.path.join(self.tmp, "gold_" + name)
        os.makedirs(d, exist_ok=True)
        src = os.path.dirname(gold_path(name))
        for fn in os.listdir(src):
            shutil.copy(os.path.join(src, fn), d)
        cir = os.path.basename(gold_path(name))
        if tran_line:
            with open(os.path.join(d, cir), encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
            lines = [tran_line if ln.strip().upper().startswith(".TRAN") else ln for ln in lines]
            with open(os.path.join(d, cir), "w") as fh:
                fh.write("\n".join(lines) + "\n")
        p = run([engines.xyce_bin(), cir], cwd=d, env=engines.env_for("xyce", None, dict(os.environ)))
        self.assertEqual(p.returncode, 0, "the gold %s failed:\n%s" % (cir, p.stdout[-2000:]))
        plots = rawfile.read_prn(os.path.join(d, cir + ".prn"))
        self.assertEqual(len(plots), 1)
        return plots[0]

    def vamos_run(self, name: str, engine: str):
        """The chain and the engine run of a deck; (ChainResult, the step's one plot)."""
        r = chain(deck_path(name), engine)
        res = run_engine(r, engine, os.path.join(self.tmp, "%s_%s" % (name, engine)))
        self.assertEqual(res.status, 0, "%s on %s: problems %s\n%s" % (name, engine, res.problems, res.stdout[-2500:]))
        plots = res.plots[r.steps[0].id]
        self.assertEqual(len(plots), 1)
        return r, plots[0]

    def compare_all(self, r: ChainResult, plot: rawfile.Raw, ref: rawfile.Raw, ref_cols: Dict[str, str],
                    abs_tol: float, rel_tol: float, full_scale: bool, label: str) -> None:
        """Every reference column against the vamos run; ref_cols: reference column -> Spectre name.  The
        pointwise comparison (two Xyce runs on one grid) allows no outlier; the full-scale one (VACASK against
        Xyce, two grids) allows OUTLIERS of the points outside the tolerance, none beyond OUTLIER_MAX: an
        isolated breakpoint the reference did not sample (an exp source's td2) or a knee the engines step
        differently (the clipper's diode, 5.8 % at 1.554 ms under tran_lteimplicit=0, §5.4, §14 q.22)."""
        sid = r.steps[0].id
        report, failed = [], False
        for col, want in ref_cols.items():
            sname = spectre_signal(r, sid, want)
            self.assertIsNotNone(sname, (col, want, r.signals(sid)))
            vcol = r.column(sid, sname)
            bad, n, worst = compare(ref, col, plot, vcol, abs_tol, rel_tol, full_scale)
            if full_scale:
                big, _n2, _w2 = compare(ref, col, plot, vcol, abs_tol, OUTLIER_MAX, True)
                ok = bad <= OUTLIERS * n and big == 0
            else:
                ok = bad == 0
            failed = failed or not ok
            report.append("%s%s -> %s (%s): %d of %d points outside absTol %g / relTol %g%s; worst %s"
                          % ("" if ok else "FAILED ", col, sname, vcol, bad, n, abs_tol, rel_tol,
                             " of full scale" if full_scale else "", worst))
        self.assertFalse(failed, "%s\n%s" % (label, "\n".join(report)))

    def check_deck(self, name: str) -> None:
        if not have_xyce():
            self.skipTest("needs Xyce for the gold run (Linux/WSL)")
        refused_on = REFUSED.get(name, {})
        xr = xplot = None
        tran_line = None
        if "xyce" in refused_on:
            with self.assertRaises(NoteError):
                chain(deck_path(name), "xyce")
        else:
            xr, xplot = self.vamos_run(name, "xyce")
            deck = xr.decks[xr.steps[0].id + ".cir"]
            tran_line = next((ln for ln in deck.splitlines() if ln.upper().startswith(".TRAN")), None)
        gold = self.gold_run(name, tran_line)
        gold_cols = {c: gold_column_to_spectre(c)[1] for c in gold.names() if c.lower() not in AXES}
        if xr is not None:
            with self.subTest(engine="xyce"):
                self.compare_all(xr, xplot, gold, gold_cols, ABS_TOL, REL_TOL, False, "vamos on Xyce against the gold run")
        if have_vacask():
            with self.subTest(engine="vacask"):
                if "vacask" in refused_on:
                    with self.assertRaises(NoteError):
                        chain(deck_path(name), "vacask")
                    return
                vr, vplot = self.vamos_run(name, "vacask")
                if xr is not None:
                    # the reference is vamos on Xyce: the same decks' semantics and time controls, read by the
                    # Xyce columns of the same Spectre signals
                    xid = xr.steps[0].id
                    ref, ref_cols = xplot, {xr.column(xid, spectre_signal(xr, xid, s)): s for s in gold_cols.values()}
                    label = "vamos on VACASK against vamos on Xyce"
                else:
                    ref, ref_cols, label = gold, gold_cols, "vamos on VACASK against the gold run"
                self.compare_all(vr, vplot, ref, ref_cols, ABS_TOL, VACASK_REL, True, label)


def _run_test(name):
    def test(self):
        self.check_deck(name)
    test.__name__ = "test_" + name
    test.__doc__ = "XDM %s: vamos on both engines against the gold run (§11 T3a)" % name
    return test


for _name in NAMES:
    setattr(TestXdmRuns, "test_" + _name, _run_test(_name))


if __name__ == "__main__":
    unittest.main()
