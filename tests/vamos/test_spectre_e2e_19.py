"""e2e 19 (docs/VAMOS_SPECTRE_DESIGN.md §11.2 item 19, phase 2): the checks of
fixtures/spectre/e2e/19_set_va_define/ that its expect.json cannot say.

    cd tests/vamos && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_spectre_e2e_19

The deck sets `reltol=1e-4` between two operating points with a `set` statement and runs a tran with
errpreset=conservative after it; `-va,define VAMOS_HALF` selects the r of its Verilog-A resistor
(expect.json holds the values: 1/3 V on VACASK, 0.5 V on Xyce, where the option is only noted).

TestSetPlan (both legs, no engine)   the phase-1b chain: the `set` becomes an options action between
    the analyses (§5.3); each step's options carry the reltol in force and the tran its errpreset
    (§5.4: the set reltol x 0.1); VACASK's deck carries the options lines between the analyses, Xyce
    gets the tolerance note (§0, §3.10)
TestSetRun (WSL; needs_flow and an engine)   the PSF headers of the run (§8.3): `reltol` and
    `tolerance.relative` of each dc file, and the tran's effective settings
"""

import os
import shutil
import unittest

import psfcheck  # noqa: E402
import spectre_testlib
from spectre_testlib import SpectreCase, needs_vacask, needs_xyce, spectre_fixture  # noqa: E402
from vamos_testlib import ROOT  # noqa: E402

from vamos.netlist import plan as PL, signals as SG, spectre, vacask, xyce  # noqa: E402
from vamos.spectre.job import Settings  # noqa: E402

CASE = spectre_fixture("e2e", "19_set_va_define")
DECK = os.path.join(CASE, "deck.scs")
ARGV = ["deck.scs", "-va,define", "VAMOS_HALF"]

needs_flow = getattr(spectre_testlib, "needs_flow", None) or unittest.skipUnless(
    os.path.isfile(os.path.join(ROOT, "vamos", "spectre", "flow.py")),
    "needs vamos/spectre/flow.py (phase 2)")


def chain(engine):
    """parse -> plan -> signals for the deck (the phase-1b chain, §12)."""
    nl = spectre.parse(DECK, CASE, spectre.SpectreParseOpts(va_defines=["VAMOS_HALF"]))
    plan = PL.build(nl, engine, Settings())
    nl2, sigmap, _notes = SG.resolve(nl, plan)
    nl2.hdl = [os.path.basename(h) for h in nl2.hdl]
    return nl2, plan, sigmap


def steps(plan):
    return {a.step.name: a.step for a in plan.actions if a.op == "analysis" and a.step is not None}


class TestSetPlan(unittest.TestCase):
    """§5.3: `set` is an options action; §5.1: AnalysisStep.options holds the global options in force;
    §5.4: errpreset scales the reltol the user set."""

    def test_set_is_an_options_action_between_the_analyses(self):
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                _nl2, plan, _sm = chain(engine)
                ops = [(a.op, a.step.name if a.step is not None else dict(a.args)) for a in plan.actions
                       if a.op in ("options", "analysis")]
                self.assertEqual([o for o, _ in ops], ["options", "analysis", "options", "analysis", "analysis"],
                                 ops)
                self.assertEqual(ops[2][1], {"reltol": 1e-4}, ops)

    def test_step_options_and_the_tran_settings(self):
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                _nl2, plan, _sm = chain(engine)
                st = steps(plan)
                self.assertEqual(st["op1"].options["reltol"], 1e-3)
                self.assertEqual(st["op2"].options["reltol"], 1e-4)
                self.assertEqual(st["tr1"].options["reltol"], 1e-4)
                a = st["tr1"].args
                self.assertEqual(a["errpreset"], "conservative")
                self.assertAlmostEqual(a["reltol"], 1e-5, delta=1e-12)      # 0.1 x the set reltol (§5.4)
                self.assertEqual((a["method"], a["relref"], a["lteratio"]), ("gear2only", "alllocal", 10.0))
                self.assertAlmostEqual(a["maxstep"], 1e-8, delta=1e-20)     # T/100

    def test_vacask_options_lines_between_the_analyses(self):
        nl2, plan, sigmap = chain("vacask")
        lines = [ln.strip() for ln in vacask.render(nl2, plan=plan, sigmap=sigmap, names={}).splitlines()]
        want = ["analysis vamos_a1 op",
                "options reltol=0.0001",
                "analysis vamos_a2 op",
                'options reltol=1e-05 relref="alllocal" tran_method="gear2" tran_lteratio=10.0',
                "analysis vamos_a3 tran",
                'options reltol=0.0001 tran_method="trap" tran_lteratio=3.5']
        at = []
        for w in want:
            hits = [k for k, ln in enumerate(lines) if ln.startswith(w)]
            self.assertTrue(hits, "%r not in the VACASK deck:\n%s" % (w, "\n".join(lines)))
            at.append(hits[0])
        self.assertEqual(at, sorted(at), "the options lines are out of order:\n%s" % "\n".join(lines))

    def test_xyce_notes_the_tolerance(self):
        nl2, plan, sigmap = chain("xyce")
        st = steps(plan)
        notes = []
        xyce.render(nl2, notes=notes, step=st["op2"], sigmap=sigmap, names={})
        msgs = [n.message for n in notes if n.severity == "note"]
        self.assertTrue(any("option reltol=0.0001 is not mapped on Xyce" in m for m in msgs), msgs)


@needs_flow
class TestSetRun(SpectreCase):
    """WSL: the run's PSF headers (§8.3): the dc files carry the options reltol in force, the tran its
    effective errpreset settings (§5.1, §5.4)."""

    def check_headers(self, engine):
        d = os.path.join(self.tmp, "e2e19_" + engine)
        shutil.copytree(CASE, d)
        self.run_spectre(d, *ARGV, engine=engine)
        res = os.path.join(d, "deck.raw")
        op1 = psfcheck.check(os.path.join(res, "op1.dc")).header
        op2 = psfcheck.check(os.path.join(res, "op2.dc")).header
        tr1 = psfcheck.check(os.path.join(res, "tr1.tran.tran")).header
        self.assertEqual((op1["reltol"], op1["tolerance.relative"]), (1e-3, 1e-3))
        self.assertEqual((op2["reltol"], op2["tolerance.relative"]), (1e-4, 1e-4))
        self.assertEqual(tr1["tolerance.relative"], 1e-4)
        self.assertAlmostEqual(tr1["reltol"], 1e-5, delta=1e-12)
        self.assertEqual((tr1["errpreset"], tr1["method"], tr1["relref"]), ("conservative", "gear2only", "alllocal"))
        self.assertEqual(float(tr1["lteratio"]), 10.0)
        self.assertAlmostEqual(tr1["maxstep"], 1e-8, delta=1e-20)

    @needs_vacask
    def test_headers_vacask(self):
        self.check_headers("vacask")

    @needs_xyce
    def test_headers_xyce(self):
        self.check_headers("xyce")


if __name__ == "__main__":
    unittest.main()
