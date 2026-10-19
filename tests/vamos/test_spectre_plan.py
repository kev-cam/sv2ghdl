"""S2 T0 tests of netlist/plan.py (docs/VAMOS_SPECTRE_DESIGN.md §5, §11.1 "Plan"); both legs, no engine.

    cd tests/vamos && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_spectre_plan

The IR is built by hand from the frozen dataclasses (phase 0), with numeric expressions or ones in
the hspice dialect where the Spectre semantics do not differ, and plan.DIALECT set to "hspice" for
the test (the Spectre evaluator is S1's).  Tests that need another owner's function (S1's
select_bin_spectre / resolve_source, S3's dialect="spectre" leg of tables.path_envs) are marked as
S6 integration tests and skipped with a reason naming the owner.
"""

import copy
import math
import os
import shutil
import tempfile
import unittest

from vamos_testlib import ROOT  # noqa: F401  (puts the repository root on sys.path)

from vamos.netlist import expr as X  # noqa: E402
from vamos.netlist import plan as PL  # noqa: E402
from vamos.netlist import signals as SG  # noqa: E402
from vamos.netlist import spectre  # noqa: E402
from vamos.netlist import tables as T  # noqa: E402
from vamos.netlist.expr_ast import Binary, Name, Num, Str  # noqa: E402
from vamos.netlist.ir import (Analysis, Cond, Instance, Model, Netlist, Param, ParamTest, Source,  # noqa: E402
                              Subckt, SweepSpec, VaModule)
from vamos.notes import ERROR, NOTE, WARNING, NoteError  # noqa: E402
from vamos.spectre.job import Settings  # noqa: E402

E2E11 = os.path.join(ROOT, "tests", "vamos", "fixtures", "spectre", "e2e", "11_exit_status")

P = X.parse


# -- builders -----------------------------------------------------------------------------------

def N(v):
    return Num(float(v))


def R(name, a, b, value, origin="t.scs:1", **params):
    return Instance(name, "r", [a, b], value=P(value) if isinstance(value, str) else N(value),
                    params={k: (P(v) if isinstance(v, str) else N(v)) for k, v in params.items()},
                    origin=origin, prim="resistor")


def C(name, a, b, value, **params):
    return Instance(name, "c", [a, b], value=N(value),
                    params={k: N(v) for k, v in params.items()}, origin="t.scs:1", prim="capacitor")


def V(name, p, n, dc=None, wave=None, spectre=None, **args):
    src = Source(dc=None if dc is None else N(dc), wave=wave, args={k: N(v) for k, v in args.items()},
                 spectre=spectre or {})
    return Instance(name, "v", [p, n], source=src, origin="t.scs:2", prim="vsource")


def I(name, p, n, dc=None, **args):                                    # noqa: E743
    src = Source(dc=None if dc is None else N(dc), args={k: N(v) for k, v in args.items()})
    return Instance(name, "i", [p, n], source=src, origin="t.scs:2", prim="isource")


def pulse_args():
    return dict(v1=0.0, v2=1.0, td=1e-3, tr=1e-6, tf=1e-6, pw=4e-3, per=10e-3)


def card(name, kind, prim, level=None, origin="t.scs:3", **params):
    """A card; a str value is an expression, a Str an enumeration (as the parser stores them)."""
    return Model(name, kind, level,
                 {k: (v if isinstance(v, Str) else P(v) if isinstance(v, str) else N(v)) for k, v in params.items()},
                 origin, prim=prim)


def mos(name, d, g, s, b, master, **params):
    return Instance(name, "m", [d, g, s, b], master=master,
                    params={k: (P(v) if isinstance(v, str) else N(v)) for k, v in params.items()},
                    origin="t.scs:4", prim="mos1")


def an(kind, name, origin="t.scs:9", sweep=None, nodes=(), children=(), **args):
    a = {}
    for k, v in args.items():
        a[k] = Str(v) if isinstance(v, str) else (N(v) if isinstance(v, (int, float)) else v)
    return Analysis(kind, a, origin, name=name, sweep=sweep, nodes=list(nodes), children=list(children))


def sw(target, name="", param="", mode="", start=None, stop=None, step=None, count=None, values=()):
    return SweepSpec(target, name, param, mode, None if start is None else N(start),
                     None if stop is None else N(stop), None if step is None else N(step), count,
                     [N(v) for v in values], "t.scs:9")


def rc_netlist(analyses=(), saves=(), rval=1000.0):
    """§7.3's worked example: V1 pulse (derived dc), X1 (in out) rcsec r=rval, parameters rval."""
    nl = Netlist(title="RC", dialect="spectre", temp=27.0, tnom=27.0)
    nl.body.append(Param("rval", N(rval), "t.scs:3"))
    nl.values["rval"] = rval
    nl.body.append(V("V1", "in", "0", wave="pulse", spectre={"type": Str("pulse"), "val0": N(0), "val1": N(1)},
                     **pulse_args()))
    nl.body.append(Instance("X1", "x", ["in", "out"], master="rcsec", params={"r": Name("rval")}, origin="t.scs:5"))
    nl.body.append(Subckt("rcsec", ["a", "b"], [Param("r", N(1000.0))],
                          [R("R1", "a", "b", "r", origin="t.scs:7"), C("C1", "b", "0", 1e-6)], origin="t.scs:6"))
    nl.analyses = list(analyses)
    nl.saves = list(saves)
    return nl


def texts(notes):
    return [n.text() for n in notes]


def errors(exc):
    return [n.message for n in exc.notes if n.severity == ERROR]


class PlanCase(unittest.TestCase):
    def setUp(self):
        self._dialect = PL.DIALECT
        PL.DIALECT = "hspice"           # the Spectre evaluator is S1's; the semantics used here agree

    def tearDown(self):
        PL.DIALECT = self._dialect

    def build(self, nl, engine="vacask", settings=None):
        return PL.build(nl, engine, settings or Settings())

    def fails(self, nl, engine="vacask", settings=None):
        with self.assertRaises(NoteError) as cm:
            PL.build(nl, engine, settings or Settings())
        return errors(cm.exception)

    def steps(self, plan):
        return [a.step for a in plan.actions if a.op == "analysis"]


# -- §5.1 ordering, actions, targets ----------------------------------------------------------------

class TestActions(PlanCase):
    def test_worked_example_actions(self):
        nl = rc_netlist([
            an("dc", "myop"),
            an("ac", "myac", sweep=sw("freq", mode="dec", start=1.0, stop=1e6, count=10)),
            an("sweep", "swp", sweep=sw("param", param="rval", mode="values", values=[1000.0, 2000.0]),
               children=[an("tran", "tr", stop=5e-3)]),
        ])
        plan = self.build(nl)
        self.assertEqual([a.op for a in plan.actions], ["options", "var", "analysis", "analysis", "analysis"])
        first = plan.actions[0]
        self.assertEqual(first.args, {"temp": 27.0, "tnom": 27.0, "reltol": 1e-3, "vabstol": 1e-6,
                                      "iabstol": 1e-12, "gmin": 1e-12})
        self.assertEqual(plan.options, first.args)
        self.assertEqual(plan.actions[1].args, {"rval": 1000.0})
        self.assertEqual(plan.variables, {"rval": 1000.0})
        self.assertEqual(plan.dependents, {"rval"})
        op, ac, tr = self.steps(plan)
        self.assertEqual((op.id, op.kind, op.name, op.own, op.context), ("vamos_a1", "dc", "myop", None, []))
        self.assertEqual((ac.id, ac.kind), ("vamos_a2", "ac"))
        self.assertEqual(ac.own.target, ("freq", "", ""))
        self.assertEqual((ac.own.id, ac.own.spec.mode, ac.own.spec.count), ("vamos_w1", "dec", 10))
        self.assertEqual((ac.own.label, ac.own.desc, ac.own.units), ("freq", "freq", "Hz"))
        self.assertEqual((tr.id, tr.kind, tr.name), ("vamos_a3", "tran", "tr"))
        self.assertEqual(len(tr.context), 1)
        lvl = tr.context[0]
        self.assertEqual((lvl.name, lvl.id, lvl.target, lvl.continuation), ("swp", "vamos_w2", ("variable", "rval", ""), 0))
        self.assertEqual([e.value for e in lvl.spec.values], [1000.0, 2000.0])
        self.assertEqual((lvl.label, lvl.desc, lvl.units), ("rval", "rval", ""))
        self.assertEqual(tr.args["stop"], 5e-3)
        self.assertEqual(tr.args["maxstep"], 1e-4)                # T/50
        self.assertEqual(tr.args["errpreset"], "moderate")
        self.assertEqual(tr.options, first.args)
        for a in plan.actions:
            if a.step is not None:
                for lvl in a.step.context + ([a.step.own] if a.step.own else []):
                    self.assertEqual(len(lvl.target), 3)
        self.assertEqual(plan.notes, [])
        self.assertEqual(plan.engine, "vacask")

    def test_no_analyses_runs_nothing_with_a_notice(self):
        plan = self.build(rc_netlist())
        self.assertEqual([a.op for a in plan.actions], ["options"])
        self.assertEqual([n.severity for n in plan.notes], [NOTE])
        self.assertIn("no analyses", plan.notes[0].message)

    def test_engine_is_checked(self):
        with self.assertRaises(ValueError):
            PL.build(rc_netlist(), "ngspice", Settings())

    def test_info_is_a_note_action_and_montecarlo_an_error(self):
        plan = self.build(rc_netlist([an("info", "inf", what="parameters"), an("dc", "op1")]))
        note = plan.actions[1]
        self.assertEqual(note.op, "note")
        self.assertEqual(note.args["note"].severity, NOTE)
        self.assertIn("inf", note.args["note"].message)
        self.assertIn("montecarlo", self.fails(rc_netlist([an("montecarlo", "mc", children=[an("dc", "op1")])]))[0])
        self.assertIn("altergroup", self.fails(rc_netlist([an("altergroup", "ag")]))[0])

    def test_var_action_only_when_a_variable_exists(self):
        plan = self.build(rc_netlist([an("dc", "op1")]))
        self.assertEqual([a.op for a in plan.actions], ["options", "analysis"])
        self.assertEqual(plan.variables, {})


class TestOptions(PlanCase):
    def test_netlist_options_and_dispositions(self):
        nl = rc_netlist([an("dc", "op1")])
        nl.temp, nl.tnom = 50.0, 25.0
        nl.options = {"reltol": N(1e-4), "chargeabstol": N(1e-20), "scale": N(2.0), "rcut": N(1e9),
                      "homotopy": Str("gmin"), "wibble": N(1.0), "multithread": Str("on"), "save": Str("allpub"),
                      "rawfmt": Str("psfascii"), "maxwarns": N(5), "rabsshort": N(0.0), "dcut": Str("all")}
        plan = self.build(nl)
        self.assertEqual(plan.options, {"temp": 50.0, "tnom": 25.0, "reltol": 1e-4, "vabstol": 1e-6, "iabstol": 1e-12,
                                        "gmin": 1e-12, "chargeabstol": 1e-20})
        msgs = texts(plan.notes)
        self.assertTrue(any(m.startswith("warning") and "scale=2" in m for m in msgs), msgs)
        self.assertTrue(any(m.startswith("warning") and "rcut=" in m and "not applied" in m for m in msgs), msgs)
        self.assertTrue(any(m.startswith("warning") and "dcut=" in m for m in msgs), msgs)
        self.assertTrue(any(m.startswith("note") and "homotopy=gmin" in m for m in msgs), msgs)
        self.assertTrue(any(m.startswith("warning") and "wibble" in m for m in msgs), msgs)
        self.assertFalse(any("multithread" in m or "save" in m or "rawfmt" in m or "maxwarns" in m
                             or "rabsshort" in m for m in msgs), msgs)

    def test_settings_win_over_the_netlist(self):
        nl = rc_netlist([an("dc", "op1")])
        nl.options = {"reltol": N(1e-4), "gmin": N(1e-10)}
        plan = self.build(nl, settings=Settings(options={"reltol": 1e-5}))
        self.assertEqual((plan.options["reltol"], plan.options["gmin"]), (1e-5, 1e-10))

    def test_scalem_and_tempeffects_are_errors(self):
        nl = rc_netlist([an("dc", "op1")])
        nl.options = {"scalem": N(2.0)}
        self.assertIn("scalem", self.fails(nl)[0])
        nl.options = {"tempeffects": Str("vt")}
        self.assertIn("tempeffects", self.fails(nl)[0])

    def test_paramdefault_options_and_analysis_lines(self):
        nl = rc_netlist([an("tran", "tr", stop=1e-3)])
        s = Settings(paramdefault=[("options", "gmin", "1e-10"), ("tran", "maxstep", "1e-6"), ("tran", "stop", "9")])
        plan = self.build(nl, settings=s)
        self.assertEqual(plan.options["gmin"], 1e-10)
        tr = self.steps(plan)[0]
        self.assertEqual(tr.args["maxstep"], 1e-6)              # min(user default, T/50)
        self.assertEqual(tr.args["stop"], 1e-3)                 # the netlist's value wins over +paramdefault

    def test_set_statement_gives_an_options_action(self):
        nl = rc_netlist([an("dc", "op1"), an("set", "s1", temp=50.0, gmin=1e-10, save="allpub"), an("dc", "op2")])
        plan = self.build(nl)
        ops = [a.op for a in plan.actions]
        self.assertEqual(ops, ["options", "analysis", "options", "analysis"])
        self.assertEqual(plan.actions[2].args, {"temp": 50.0, "gmin": 1e-10})
        op1, op2 = self.steps(plan)
        self.assertEqual((op1.options["temp"], op2.options["temp"], op2.options["gmin"]), (27.0, 50.0, 1e-10))
        self.assertTrue(any("save" in n.message and n.severity == WARNING for n in plan.notes), texts(plan.notes))

    def test_set_inside_a_sweep_block_is_an_error(self):
        nl = rc_netlist([an("sweep", "s", sweep=sw("param", param="rval", mode="values", values=[1e3, 2e3]),
                            children=[an("set", "s1", temp=50.0), an("dc", "op1")])])
        self.assertTrue(any("set s1 inside a sweep" in e for e in self.fails(nl)))

    def test_user_options_hold_the_keys_the_run_set(self):
        # D2 (R-plan-20, R-emit-15, R-run-15): AnalysisStep.user_options, the option keys the netlist (options,
        # set, alter), +paramdefault or the command line set, and a tran's own reltol/relref/lteratio/errpreset/
        # method; the emitters note on Xyce only what is in it.  Spectre's defaults are never in it, nor the output
        # settings job.settings writes into Settings.options itself (rawfmt, rawfile, maxwarns, maxnotes)
        plan = self.build(rc_netlist([an("dc", "op1"), an("tran", "tr", stop=1e-3)]),
                          settings=Settings(options={"rawfmt": Str("psfascii")}))
        self.assertEqual([s.user_options for s in self.steps(plan)], [set(), set()])
        nl = rc_netlist([an("dc", "op1"), an("set", "s1", gmin=1e-10), an("alter", "a1", param="temp", value=50.0),
                         an("tran", "tr", stop=1e-3, errpreset="liberal", lteratio=4.0), an("dc", "op2")])
        nl.options = {"reltol": N(1e-4), "save": Str("allpub")}
        settings = Settings(options={"vabstol": 1e-7, "rawfmt": Str("psfascii")},
                            paramdefault=[("options", "iabstol", "1e-13"), ("tran", "relref", "alllocal")])
        op1, tr, op2 = self.steps(self.build(nl, settings=settings))
        self.assertEqual(op1.user_options, {"reltol", "save", "vabstol", "iabstol"})
        self.assertEqual(tr.user_options, {"reltol", "save", "vabstol", "iabstol", "gmin", "temp", "errpreset",
                                           "lteratio", "relref"})
        self.assertEqual(op2.user_options, {"reltol", "save", "vabstol", "iabstol", "gmin", "temp"})
        self.assertIsNot(op1.user_options, op2.user_options)
        for s in (Settings(errpreset="liberal"), Settings(aps="conservative")):
            tr = self.steps(self.build(rc_netlist([an("tran", "tr", stop=1e-3)]), settings=s))[0]
            self.assertEqual(tr.user_options, {"errpreset"})
        tr = self.steps(self.build(rc_netlist([an("tran", "tr", stop=1e-3, method="gear2", relref="pointlocal")])))[0]
        self.assertEqual(tr.user_options, {"method", "relref"})
        self.assertEqual(PL.AnalysisStep("vamos_a1", "op1", "dc").user_options, set())


# -- §5.2 grids -------------------------------------------------------------------------------------------

class TestGrids(PlanCase):
    def level(self, spec, kind="dc"):
        plan = self.build(rc_netlist([an(kind, "a1", sweep=spec)]))
        return self.steps(plan)[0].own

    def test_dec_grid_4hz_to_4_19mhz(self):
        lvl = self.level(sw("freq", mode="dec", start=4.0, stop=4.19e6, count=20), "ac")
        self.assertEqual(lvl.spec.mode, "log")                  # a computed log grid: values filled, PSF grid 3 (§8.3)
        pts = [e.value for e in lvl.spec.values]
        self.assertEqual(len(pts), 121)                         # 120.4 -> 120 intervals, 121 points [E21]
        self.assertEqual(pts[-1], 4.19e6)
        for k in range(120):
            self.assertAlmostEqual(pts[k], 4.0 * 10 ** (k / 20.0), delta=1e-9 * pts[k])
        self.assertTrue(all(isinstance(e, Num) for e in lvl.spec.values))
        self.assertEqual(lvl.spec.start.value, 4.0)

    def test_integral_dec_span_keeps_dec(self):
        lvl = self.level(sw("freq", mode="dec", start=1.0, stop=1e6, count=10), "ac")
        self.assertEqual((lvl.spec.mode, lvl.spec.count, lvl.spec.start.value, lvl.spec.stop.value), ("dec", 10, 1.0, 1e6))
        self.assertEqual(len(PL.grid_points(lvl.spec)), 61)
        self.assertEqual(PL.grid_points(lvl.spec)[-1], 1e6)

    def test_log_is_expanded(self):
        lvl = self.level(sw("freq", mode="log", start=1.0, stop=1000.0, count=3), "ac")
        self.assertEqual(lvl.spec.mode, "log")
        self.assertEqual([round(e.value, 9) for e in lvl.spec.values], [1.0, 10.0, 100.0, 1000.0])
        self.assertEqual(PL.grid_points(lvl.spec), [e.value for e in lvl.spec.values])

    def test_center_span_without_a_step_parameter(self):
        # §5.2: the parser keeps center -/+ span/2 unfolded (spectre.sweep_spec); the ratio rule's linear
        # choice is lin=50, its logarithmic choice an error, since center/span cannot describe a log grid
        # (§14 q.11; phase 1b)
        def cs(center, span):
            half = Binary("/", N(span), N(2.0))
            return SweepSpec("dev", "V1", "dc", "", Binary("-", N(center), half), Binary("+", N(center), half),
                             None, None, [], "t.scs:9")
        self.assertTrue(PL.center_span(cs(1.0, 1.0)))
        self.assertFalse(PL.center_span(sw("dev", "V1", "dc", start=0.5, stop=1.5)))
        lvl = self.level(cs(1.0, 1.0))
        self.assertEqual((lvl.spec.mode, lvl.spec.count, lvl.spec.start.value, lvl.spec.stop.value), ("lin", 50, 0.5, 1.5))
        errs = self.fails(rc_netlist([an("dc", "a1", sweep=cs(5.5, 9.0))]))
        self.assertTrue(any("center/span without a step parameter" in e and "logarithmic" in e for e in errs), errs)
        log = self.level(sw("dev", "V1", "dc", start=1.0, stop=10.0))     # the same bounds as start/stop: log
        self.assertEqual(log.spec.mode, "log")

    def test_no_step_parameter(self):
        lin = self.level(sw("freq", start=0.0, stop=5.0), "ac")
        self.assertEqual((lin.spec.mode, lin.spec.count), ("lin", 50))
        lin2 = self.level(sw("freq", start=1.0, stop=9.0), "ac")
        self.assertEqual((lin2.spec.mode, lin2.spec.count), ("lin", 50))
        log = self.level(sw("freq", start=1.0, stop=100.0), "ac")
        self.assertEqual(log.spec.mode, "log")
        self.assertEqual(len(log.spec.values), 51)
        self.assertEqual(log.spec.values[-1].value, 100.0)

    def test_lin_step_values_stay_native(self):
        lin = self.level(sw("dev", "V1", "dc", "lin", start=0.0, stop=1.0, count=4))
        self.assertEqual((lin.spec.mode, lin.spec.count), ("lin", 4))
        self.assertEqual(PL.grid_points(lin.spec), [0.0, 0.25, 0.5, 0.75, 1.0])
        st = self.level(sw("dev", "V1", "dc", "step", start=0.0, stop=1.0, step=0.5))
        self.assertEqual((st.spec.mode, st.spec.step.value), ("step", 0.5))
        self.assertEqual(PL.grid_points(st.spec), [0.0, 0.5, 1.0])
        vals = self.level(sw("dev", "V1", "dc", "values", values=[0.1, 0.2]))
        self.assertEqual([e.value for e in vals.spec.values], [0.1, 0.2])

    def test_expressions_in_bounds_are_evaluated(self):
        nl = rc_netlist([an("dc", "a1", sweep=SweepSpec("dev", "V1", "dc", "lin", P("rval/1000"), P("rval/500"), None, 2))])
        lvl = self.steps(self.build(nl))[0].own
        self.assertEqual((lvl.spec.start.value, lvl.spec.stop.value), (1.0, 2.0))

    def test_log_of_a_non_positive_bound_is_an_error(self):
        errs = self.fails(rc_netlist([an("ac", "a1", sweep=sw("freq", mode="dec", start=0.0, stop=1e3, count=10))]))
        self.assertTrue(any("start > 0" in e for e in errs), errs)

    def test_start_defaults_to_zero(self):
        # R-plan-9: start=0 is the documented default of dc, ac, noise, sweep and xf [M ref19 pp.43, 64, 182, 406,
        # 439]; UG p.67: 'A linear sweep starts at zero (the default)'
        st = self.level(sw("dev", "V1", "dc", "step", stop=2.0, step=0.5))
        self.assertEqual((st.spec.start, PL.grid_points(st.spec)), (Num(0.0), [0.0, 0.5, 1.0, 1.5, 2.0]))
        ac = self.level(sw("freq", mode="lin", stop=1e6, count=100), "ac")
        pts = PL.grid_points(ac.spec)
        self.assertEqual((len(pts), pts[0], pts[1], pts[-1]), (101, 0.0, 1e4, 1e6))
        nl = rc_netlist([an("sweep", "s", sweep=sw("param", param="temp", mode="step", stop=100.0, step=50.0),
                            children=[an("dc", "op1")])])
        self.assertEqual(PL.grid_points(self.steps(self.build(nl))[0].context[0].spec), [0.0, 50.0, 100.0])
        lin = self.level(sw("dev", "V1", "dc", stop=5.0))        # no step parameter: the ratio rule's linear grid
        self.assertEqual((lin.spec.mode, lin.spec.count, lin.spec.start), ("lin", 50, Num(0.0)))
        errs = self.fails(rc_netlist([an("ac", "a1", sweep=sw("freq", mode="dec", stop=1e3, count=10))]))
        self.assertTrue(any("start > 0" in e and "start=0" in e for e in errs), errs)   # a log sweep from 0
        errs = self.fails(rc_netlist([an("dc", "a1", sweep=sw("dev", "V1", "dc", "step", start=0.0, step=0.5))]))
        self.assertTrue(any("needs stop" in e for e in errs), errs)                    # stop has no default

    def test_bounds_reading_a_variable_are_an_error(self):
        nl = rc_netlist([an("sweep", "s", sweep=sw("param", param="rval", mode="values", values=[1e3, 2e3]),
                            children=[an("ac", "a1", sweep=SweepSpec("freq", "", "", "lin", N(1.0), P("rval"), None, 2))])])
        self.assertTrue(any("reads rval" in e for e in self.fails(nl)))


# -- §5.2 targets -----------------------------------------------------------------------------------------

class TestTargets(PlanCase):
    def netlist(self):
        nl = rc_netlist()
        nl.body.insert(1, R("R0", "in", "mid", 2000.0, w=1e-6, l=2e-6))
        nl.body.insert(2, card("nch", "nmos", "mos1", 1.0, vto=0.5))
        nl.body.insert(3, card("rmod", "r", "resistor", None, rsh=100.0, tc1=1e-3))
        nl.body.insert(4, mos("M1", "mid", "in", "0", "0", "nch", w=10e-6, l=1e-6))
        nl.body.insert(5, I("I1", "mid", "0", dc=1e-3))
        return nl

    def own(self, spec, engine="vacask"):
        nl = self.netlist()
        nl.analyses = [an("dc", "a1", sweep=spec)]
        plan = self.build(nl, engine)
        return self.steps(plan)[0].own, plan

    def test_dev_model_sub_param_temp_freq(self):
        lvl, _ = self.own(sw("dev", "R0", "r", "values", values=[1e3, 2e3]))
        self.assertEqual((lvl.target, lvl.label, lvl.desc, lvl.units), (("instance", "R0", "r"), "r", "R0:r", "Ohm"))
        lvl, _ = self.own(sw("mod", "nch", "vto", "values", values=[0.4, 0.6]))
        # R-plan-14: a leaf's label is the parameter (§5.2), the description names the card (phase 2 pinned
        # the label "nch:vto" here)
        self.assertEqual((lvl.target, lvl.label, lvl.desc, lvl.units), (("model", "nch", "vto"), "vto", "nch:vto", ""))
        lvl, plan = self.own(sw("sub", "X1", "r", "values", values=[1e3, 2e3]))
        self.assertEqual((lvl.target, lvl.label, lvl.desc), (("instance", "X1", "r"), "r", "X1:r"))
        self.assertEqual(plan.overridden, {"rcsec": {"r"}})
        lvl, plan = self.own(sw("param", param="temp", mode="values", values=[0.0, 100.0]))
        self.assertEqual((lvl.target, lvl.label, lvl.units), (("option", "temp", ""), "temp", "C"))
        self.assertEqual(plan.variables, {})
        lvl, plan = self.own(sw("param", param="rval", mode="values", values=[1e3, 2e3]))
        self.assertEqual((lvl.target, lvl.label, lvl.units), (("variable", "rval", ""), "rval", ""))
        self.assertEqual(plan.variables, {"rval": 1000.0})
        self.assertEqual(plan.actions[1].op, "var")
        lvl, _ = self.own(sw("dev", "V1", "dc", "lin", start=0.0, stop=1.0, count=10))
        self.assertEqual((lvl.target, lvl.label, lvl.desc, lvl.units), (("instance", "V1", "dc"), "dc", "V1:dc", "V"))
        lvl, _ = self.own(sw("dev", "I1", "dc", "lin", start=0.0, stop=1e-3, count=10))
        self.assertEqual(lvl.units, "A")
        lvl, _ = self.own(sw("dev", "M1", "w", "values", values=[5e-6, 20e-6]))
        self.assertEqual((lvl.target, lvl.units), (("instance", "M1", "w"), "m"))

    def test_parent_label_names_the_device(self):
        nl = self.netlist()
        nl.analyses = [an("sweep", "s", sweep=sw("dev", "R0", "r", "values", values=[1e3, 2e3]),
                          children=[an("dc", "op1"), an("tran", "tr", stop=1e-3)])]
        plan = self.build(nl)
        op1, tr = self.steps(plan)
        self.assertEqual((op1.context[0].label, op1.context[0].desc, op1.context[0].units), ("R0:r", "R0:r", "Ohm"))
        self.assertEqual((op1.context[0].id, tr.context[0].id), ("vamos_w1", "vamos_w2"))   # one statement per child
        self.assertEqual((op1.context[0].name, op1.context[0].continuation), ("s", 0))

    def test_instance_renames_follow_the_masters(self):
        nl = self.netlist()
        nl.body.insert(2, card("nch3", "nmos", "bsim3v3", 49.0))
        nl.body.insert(3, Instance("M3", "m", ["mid", "in", "0", "0"], master="nch3",
                                   params={"w": N(5e-6), "l": N(1e-6), "nqsmod": N(0.0)}, origin="t.scs:4", prim="bsim3v3"))
        nl.analyses = [an("alter", "al", dev="M3", param="nqsmod", value=1.0), an("dc", "op1")]
        plan = self.build(nl)
        alt = [a for a in plan.actions if a.op == "alter"][0]
        self.assertEqual(alt.args, {"target": ("instance", "M3", "instance_nqsmod"), "value": 1.0})

    def test_model_group_cannot_be_swept(self):
        nl = self.netlist()
        nl.body.insert(2, card("grp.1", "nmos", "mos1", 1.0, vto=0.5))
        nl.body[2].base, nl.body[2].bin_index, nl.body[2].bin_rule = "grp", 1, "spectre"
        nl.analyses = [an("dc", "a1", sweep=sw("mod", "grp", "vto", "values", values=[0.4, 0.6]))]
        self.assertTrue(any("model group" in e for e in self.fails(nl)))

    def test_unknown_targets_are_errors(self):
        nl = self.netlist()
        nl.analyses = [an("dc", "a1", sweep=sw("dev", "R9", "r", "values", values=[1.0]))]
        self.assertTrue(any("no instance R9" in e for e in self.fails(nl)))
        nl.analyses = [an("dc", "a1", sweep=sw("param", param="nosuch", mode="values", values=[1.0]))]
        self.assertTrue(any("not a top-level parameter" in e for e in self.fails(nl)))
        nl.analyses = [an("dc", "a1", sweep=sw("mod", "nch", "nosuch", "values", values=[1.0]))]
        self.assertTrue(any("not a parameter of model nch" in e for e in self.fails(nl)))
        nl.analyses = [an("dc", "a1", sweep=sw("sub", "X1", "zz", "values", values=[1.0]))]
        self.assertTrue(any("not a parameter of subckt rcsec" in e for e in self.fails(nl)))

    def test_folded_and_stripped_parameters_are_refused(self):
        nl = self.netlist()
        nl.analyses = [an("dc", "a1", sweep=sw("mod", "rmod", "rsh", "values", values=[1.0, 2.0]))]
        self.assertTrue(any("rsh of model rmod is folded" in e for e in self.fails(nl)))
        nl.analyses = [an("dc", "a1", sweep=sw("mod", "nch", "w", "values", values=[1e-6, 2e-6]))]
        self.assertTrue(any("w of model nch is folded" in e for e in self.fails(nl)))
        nl.analyses = [an("dc", "a1", sweep=sw("mod", "rmod", "thresh", "values", values=[1.0]))]
        self.assertTrue(any("thresh of model rmod is stripped" in e for e in self.fails(nl)))
        nl.body[1].folded = ["w", "l"]                           # R0's r was computed from w and l
        nl.analyses = [an("dc", "a1", sweep=sw("dev", "R0", "w", "values", values=[1e-6, 2e-6]))]
        self.assertTrue(any("Instance.folded" in e for e in self.fails(nl)))
        nl.analyses = [an("alter", "al", dev="R0", param="l", value=3e-6), an("dc", "op1")]
        self.assertTrue(any("Instance.folded" in e for e in self.fails(nl)))
        nl.analyses = [an("dc", "a1", sweep=sw("dev", "M1", "w", "values", values=[5e-6, 20e-6]))]
        self.build(nl)                                           # dev=M1 param=w runs

    def test_tnom_sweep_is_an_error_on_xyce(self):
        nl = self.netlist()
        nl.analyses = [an("dc", "a1", sweep=sw("param", param="tnom", mode="values", values=[0.0, 100.0]))]
        self.build(nl, "vacask")
        self.assertTrue(any("tnom" in e for e in self.fails(nl, "xyce")))

    def test_sweep_labels_keep_the_parameter_as_written(self):
        # R-plan-14: the PSF sweep variable and the descriptions name the parameter the netlist wrote, never the
        # engine's spelling the target carries (Spectre has no renames: mysweep_dc1.sweep's SWEEP "R1:r")
        nl = self.netlist()
        nl.body.insert(2, card("dm", "d", "diode", 1.0, ik=1e-3))                 # ik renames to ikf
        nl.body.insert(3, Instance("D1", "d", ["mid", "0"], master="dm", origin="t.scs:4", prim="diode"))
        nl.analyses = [an("dc", "a1", sweep=sw("mod", "dm", "ik", "values", values=[1e-3, 2e-3]))]
        lvl = self.steps(self.build(nl))[0].own
        self.assertEqual((lvl.target, lvl.label, lvl.desc), (("model", "dm", "ikf"), "ik", "dm:ik"))
        nl.analyses = [an("dc", "a1", sweep=sw("dev", "D1", "trise", "values", values=[0.0, 10.0]))]
        lvl = self.steps(self.build(nl))[0].own
        self.assertEqual((lvl.target, lvl.label, lvl.desc), (("instance", "D1", "dtemp"), "trise", "D1:trise"))
        for spec, label in ((sw("dev", "D1", "trise", "values", values=[0.0, 10.0]), "D1:trise"),
                            (sw("mod", "dm", "ik", "values", values=[1e-3, 2e-3]), "dm:ik")):
            nl.analyses = [an("sweep", "s", sweep=spec, children=[an("dc", "op1")])]
            ctx = self.steps(self.build(nl))[0].context[0]
            self.assertEqual((ctx.label, ctx.desc), (label, label))

    def test_dev_on_a_subckt_instance_takes_the_sub_rules(self):
        # R-plan-3: dev=X1 param=r of a subckt instance is sub=X1's target: the subckt parameter check,
        # plan.overridden and the per-point condition rule (Xyce froze the if branch: a silently wrong answer)
        def netlist(cond):
            nl = rc_netlist()
            if cond:
                nl.body[-1].body.append(Cond([(P("r > 1500"), [R("Rb", "a", "0", 10e3)])], [], "t.scs:12"))
            return nl
        for engine in ("vacask", "xyce"):
            for kind in ("dev", "sub"):
                nl = netlist(True)
                nl.analyses = [an("dc", "a1", sweep=sw(kind, "X1", "r", "values", values=[1e3, 2e3]))]
                errs = self.fails(nl, engine)
                self.assertTrue(any("changes a structural condition on path X1" in e for e in errs), (kind, engine, errs))
            nl = netlist(False)
            nl.analyses = [an("dc", "a1", sweep=sw("dev", "X1", "r", "values", values=[1e3, 2e3]))]
            plan = self.build(nl, engine)
            self.assertEqual((plan.overridden, self.steps(plan)[0].own.target), ({"rcsec": {"r"}}, ("instance", "X1", "r")))
            nl.analyses = [an("dc", "a1", sweep=sw("dev", "X1", "zz", "values", values=[1.0, 2.0]))]
            self.assertTrue(any("zz is not a parameter of subckt rcsec" in e for e in self.fails(nl, engine)))
            nl = netlist(True)
            nl.analyses = [an("dc", "op1"), an("alter", "al", dev="X1", param="r", value=3e3), an("dc", "op2")]
            self.assertTrue(any("alter al changes a structural condition" in e for e in self.fails(nl, engine)))

    def test_a_verilog_a_target_is_checked_against_the_module(self):
        # R-plan-21, R-tests-7: a dev= or mod= target of a Verilog-A instance or card refuses what the instance line
        # refuses (spectre.va_params, §3.9): case-insensitive, m allowed, unchecked when the parameter list is
        # unknown (VaModule.params None, §3.1's note); e2e 11's bind_fail ended 1 on VACASK and 2 on Xyce
        def netlist(params):
            nl = rc_netlist()
            nl.va_modules["vres"] = VaModule("vres", "/x/vres.va", params)
            nl.body.insert(1, Model("m_vr", "y", None, {"r": N(2e3)}, "t.scs:3", prim="vres"))
            nl.body.insert(2, Instance("X9", "y", ["in", "0"], master="vres", params={}, origin="t.scs:4", prim="vres"))
            return nl
        for engine in ("vacask", "xyce"):
            nl = netlist(["r"])
            for a in (an("dc", "sw1", sweep=sw("dev", "X9", "foo", "values", values=[1.0, 2.0])),
                      an("alter", "a1", dev="X9", param="foo", value=1.0),
                      an("alter", "a2", mod="m_vr", param="foo", value=1.0)):
                nl.analyses = [a, an("dc", "op1")]
                errs = self.fails(nl, engine)
                self.assertTrue(any("foo is not a parameter of Verilog-A module vres (declared: r)" in e for e in errs),
                                (engine, a.name, errs))
            for p in ("r", "R", "m"):
                nl.analyses = [an("dc", "sw1", sweep=sw("dev", "X9", p, "values", values=[1.0, 2.0]))]
                self.build(nl, engine)
            nl = netlist(None)
            nl.analyses = [an("dc", "sw1", sweep=sw("dev", "X9", "foo", "values", values=[1.0, 2.0]))]
            self.build(nl, engine)                              # the list is unknown: unchecked


class TestXycePerPath(PlanCase):
    def netlist(self, copies=2):
        """outer (a b): xi (a b) inner r=1k; Vs (b 0) vsource; inner (a b): R1 C1.  x1, x2 are outers."""
        nl = Netlist(title="two paths", dialect="spectre", temp=27.0, tnom=27.0)
        nl.body.append(V("V1", "in", "0", dc=1.0))
        nl.body.append(Subckt("inner", ["a", "b"], [Param("r", N(1000.0))],
                              [R("R1", "a", "b", "r", origin="t.scs:7"), C("C1", "b", "0", 1e-6)], origin="t.scs:6"))
        nl.body.append(Subckt("outer", ["a", "b"], [],
                              [Instance("xi", "x", ["a", "b"], master="inner", params={"r": N(1000.0)}, origin="t.scs:10"),
                               V("Vs", "b", "0", dc=0.5)], origin="t.scs:9"))
        for k in range(copies):
            nl.body.append(Instance("x%d" % (k + 1), "x", ["in", "o%d" % (k + 1)], master="outer", origin="t.scs:5"))
        return nl

    def test_alter_inside_a_shared_subckt(self):
        nl = self.netlist()
        nl.analyses = [an("alter", "al", dev="x1.xi.R1", param="r", value=500.0), an("dc", "op1")]
        plan = self.build(nl, "vacask")
        alt = [a for a in plan.actions if a.op == "alter"][0]
        self.assertEqual(alt.args, {"target": ("instance", "x1.xi.R1", "r"), "value": 500.0})
        errs = self.fails(nl, "xyce")
        self.assertTrue(any("2 instance paths reach (x1.xi, x2.xi)" in e for e in errs), errs)
        nl1 = self.netlist(copies=1)
        nl1.analyses = list(nl.analyses)
        self.build(nl1, "xyce")                                  # one path runs

    def test_noise_input_inside_a_shared_subckt(self):
        """§5.6 iprobe= inside a subckt: VACASK names the path (in="'x1:Vs'"); Xyce prints AC 1 0 into the
        subckt body, so §5.3's per-path rule applies there too (S3's seam; merge, phase 1)."""
        nl = self.netlist()
        freq = sw("freq", mode="dec", start=1.0, stop=1e3, count=10)
        nl.analyses = [an("noise", "nz", nodes=["in"], iprobe="x1.Vs", sweep=freq)]
        st = self.steps(self.build(nl, "vacask"))[0]
        self.assertEqual((st.noise_input, st.args["p"], st.args["n"]), ("x1.Vs", "in", "0"))
        errs = self.fails(nl, "xyce")
        self.assertTrue(any("on Xyce x1.Vs lies in subckt outer, which 2 instance paths reach (x1, x2)" in e
                            for e in errs), errs)
        nl1 = self.netlist(copies=1)
        nl1.analyses = list(nl.analyses)
        self.assertEqual(self.steps(self.build(nl1, "xyce"))[0].noise_input, "x1.Vs")    # one path runs

    def test_sub_hook_and_source_field_sweeps(self):
        nl = self.netlist()
        nl.analyses = [an("dc", "a1", sweep=sw("sub", "x1.xi", "r", "values", values=[1e3, 2e3]))]
        self.build(nl, "vacask")
        self.assertTrue(any("on Xyce x1.xi lies in subckt outer" in e for e in self.fails(nl, "xyce")))
        nl.analyses = [an("dc", "a1", sweep=sw("sub", "x1", "r", "values", values=[1e3, 2e3]))]
        self.assertTrue(any("not a parameter of subckt outer" in e for e in self.fails(nl, "xyce")))
        nl.analyses = [an("dc", "a1", sweep=sw("dev", "x1.Vs", "dc", "values", values=[0.1, 0.2]))]
        self.build(nl, "vacask")
        self.assertTrue(any("x1.Vs" in e for e in self.fails(nl, "xyce")))
        nl.analyses = [an("dc", "a1", sweep=sw("dev", "x1.xi.R1", "r", "values", values=[1e3, 2e3]))]
        self.build(nl, "xyce")                                   # a plain dev= sweep is per path on Xyce (.DC X1:XI:R1:R)
        nl1 = self.netlist(copies=1)
        nl1.analyses = [an("dc", "a1", sweep=sw("sub", "x1.xi", "r", "values", values=[1e3, 2e3]))]
        self.build(nl1, "xyce")                                  # one path: the hook is per path


# -- §5.3 sweep blocks, alters and source states -----------------------------------------------------------

class TestSweepBlocks(PlanCase):
    def test_continuation(self):
        nl = rc_netlist([an("sweep", "s", sweep=sw("param", param="rval", mode="values", values=[1e3, 2e3]),
                            children=[an("dc", "op1"), an("dc", "op2", restart="no"),
                                      an("dc", "sw1", sweep=sw("dev", "V1", "dc", "lin", start=0.0, stop=1.0, count=2)),
                                      an("dc", "sw2", swpuseprevic="no",
                                         sweep=sw("dev", "V1", "dc", "lin", start=0.0, stop=1.0, count=2))])])
        op1, op2, sw1, sw2 = self.steps(self.build(nl))
        self.assertEqual([s.context[0].continuation for s in (op1, op2, sw1, sw2)], [0, 1, 0, 0])
        self.assertEqual((sw1.own.continuation, sw2.own.continuation), (1, 0))
        self.assertEqual([s.context[0].id for s in (op1, op2, sw1, sw2)], ["vamos_w1", "vamos_w2", "vamos_w3", "vamos_w5"])
        self.assertEqual((sw1.own.id, sw2.own.id), ("vamos_w4", "vamos_w6"))
        self.assertEqual([s.id for s in (op1, op2, sw1, sw2)], ["vamos_a1", "vamos_a2", "vamos_a3", "vamos_a4"])

    def test_nested_blocks_outer_first(self):
        nl = rc_netlist([an("sweep", "outer", sweep=sw("param", param="temp", mode="values", values=[0.0, 50.0]),
                            children=[an("sweep", "inner", sweep=sw("param", param="rval", mode="values", values=[1e3, 2e3]),
                                         children=[an("tran", "tr", stop=1e-3)])])])
        tr = self.steps(self.build(nl))[0]
        self.assertEqual([lvl.name for lvl in tr.context], ["outer", "inner"])
        self.assertEqual([lvl.target for lvl in tr.context], [("option", "temp", ""), ("variable", "rval", "")])

    def test_order_dependent_children_are_errors(self):
        spec = sw("param", param="rval", mode="values", values=[1e3, 2e3])
        for child, text in ((an("alter", "al", param="rval", value=3e3), "alter al inside a sweep"),
                            (an("montecarlo", "mc"), "montecarlo"),
                            (an("ac", "a1", prevoppoint="yes", sweep=sw("freq", mode="lin", start=1.0, stop=2.0, count=1)),
                             "prevoppoint inside a sweep"),
                            (an("dc", "op1", write="s.dat"), "write inside a sweep"),
                            (an("tran", "tr", stop=1e-3, readns="n.dat"), "readns inside a sweep")):
            nl = rc_netlist([an("sweep", "s", sweep=spec, children=[child])])
            errs = self.fails(nl)
            self.assertTrue(any(text in e for e in errs), (text, errs))
        nl = rc_netlist([an("sweep", "s", sweep=spec, children=[an("info", "i1"), an("dc", "op1")])])
        plan = self.build(nl)
        self.assertEqual([a.op for a in plan.actions], ["options", "var", "note", "analysis"])

    def test_ac_noise_xf_parameter_sweeps_restart_from_scratch(self):
        # R-plan-15: restart=yes, the default of ac, noise and xf, 'Restart the DC solution from scratch if any
        # condition has changed' [M ref19 p.45, p.184, p.441]: continuation 0, 1 with restart=no, as the sweep
        # blocks have it (§5.2); the frequency axis keeps 1 (E29: the same point got another bias point)
        par = sw("dev", "V1", "dc", "values", values=[0.0, 1.0])
        for kind in ("ac", "noise", "xf"):
            st = self.steps(self.build(rc_netlist([an(kind, "a1", nodes=["out"], freq=1e3, sweep=par)])))[0]
            self.assertEqual(st.own.continuation, 0, kind)
            st = self.steps(self.build(rc_netlist([an(kind, "a1", nodes=["out"], freq=1e3, restart="no", sweep=par)])))[0]
            self.assertEqual(st.own.continuation, 1, kind)
            st = self.steps(self.build(rc_netlist([an(kind, "a1", nodes=["out"],
                                                      sweep=sw("freq", mode="dec", start=1.0, stop=1e3, count=1))])))[0]
            self.assertEqual(st.own.continuation, 1, kind)


class TestAlters(PlanCase):
    def test_alter_targets(self):
        nl = rc_netlist([an("dc", "op1"),
                         an("alter", "a1", param="rval", value=2000.0),
                         an("alter", "a2", param="temp", value=50.0),
                         an("alter", "a3", sub="X1", param="r", value=3000.0),
                         an("alter", "a4", dev="X1.R1", param="r", value=4000.0),
                         an("dc", "op2")])
        # phase 2c3 (G1): on VACASK the pending changes of rval and temp re-evaluate X1's r=rval and R1's r=r at op2
        # (measured: op2 out = 0.333, R1 = 2k, where Xyce and Spectre keep a4's 4k, out = 0.2), so VACASK refused the
        # deck.  Phase 2c4 (H1): the VACASK deck prints both bindings through switchable hooks, which a re-evaluation
        # keeps (op2 0.2, test_spectre_emit.TestFollowUpRuns2c4), so the plan builds on both engines again, with the
        # same actions (TestFollowUp2c4 pins the VACASK deck's hooks)
        for engine in ("vacask", "xyce"):
            plan = self.build(nl, engine)
            alters = [a.args for a in plan.actions if a.op == "alter"]
            self.assertEqual(alters, [{"target": ("variable", "rval", ""), "value": 2000.0},
                                      {"target": ("option", "temp", ""), "value": 50.0},
                                      {"target": ("instance", "X1", "r"), "value": 3000.0},
                                      {"target": ("instance", "X1.R1", "r"), "value": 4000.0}], engine)
            self.assertEqual([a.op for a in plan.actions],
                             ["options", "var", "analysis", "alter", "alter", "alter", "alter", "analysis"], engine)
            op1, op2 = self.steps(plan)
            self.assertEqual((op1.options["temp"], op2.options["temp"]), (27.0, 50.0), engine)
            self.assertEqual(plan.variables, {"rval": 1000.0}, engine)       # nominal, never the altered value
            self.assertEqual(plan.overridden, {"rcsec": {"r"}}, engine)

    def test_alter_of_a_model_and_of_a_group(self):
        nl = rc_netlist([an("dc", "op1")])
        nl.body.insert(1, card("nch", "nmos", "mos1", 1.0, vto=0.5))
        nl.body.insert(2, mos("M1", "in", "in", "0", "0", "nch", w=1e-6, l=1e-6))
        nl.analyses = [an("alter", "a1", mod="nch", param="vto", value=0.7), an("dc", "op1")]
        plan = self.build(nl)
        self.assertEqual([a.args for a in plan.actions if a.op == "alter"], [{"target": ("model", "nch", "vto"), "value": 0.7}])

    def test_alter_value_reading_a_variable_is_an_error(self):
        nl = rc_netlist([an("alter", "a1", dev="X1.R1", param="r", value=P("rval*2")),
                         an("dc", "a2", sweep=sw("param", param="rval", mode="values", values=[1e3, 2e3]))])
        self.assertTrue(any("reads rval" in e for e in self.fails(nl)))

    def test_alter_temp_re_evaluates_what_reads_it(self):
        nl = rc_netlist([an("dc", "op1"), an("alter", "a1", param="temp", value=60.0), an("dc", "op2")])
        nl.body.insert(1, R("Rt", "in", "0", "1k/(temper-60)"))
        errs = self.fails(nl)
        self.assertTrue(any("alter a1" in e and "Rt" in e and "division by zero" in e for e in errs), errs)
        nl.analyses = [an("dc", "op1")]
        self.build(nl)                                           # fine at 27 C

    def test_vacask_refuses_a_sweep_of_a_target_bound_to_an_expression(self):
        # phase 1b: VACASK fails a sweep of an instance parameter bound to an expression ("parameter 'r' of
        # instance 'X2:R1' is bound to an expression. Failed to bind sweep parameters.", rc 0, no rawfile) while
        # an alter of it works; Xyce sweeps it natively (.DC X2:R1:R).  Phase 2c4 (H1): the VACASK deck sweeps the
        # parameter's switchable hook (vamos_av<k> with vamos_af<k> set), so the refusal is gone and both sweeps build
        # on VACASK as on Xyce (TestFollowUp2c4 pins the deck text, test_spectre_emit.TestFollowUpRuns2c4 the values)
        from vamos.netlist import vacask
        a = an("dc", "s1", sweep=sw("dev", "X1.R1", "r", mode="values", values=[1e3, 2e3]))
        for engine in ("vacask", "xyce"):
            self.build(rc_netlist([a]), engine)
        nl = rc_netlist([a])
        plan = self.build(nl, "vacask")
        nl2, sm, _notes = SG.resolve(nl, plan)
        text = vacask.render(nl2, plan=plan, sigmap=sm)
        self.assertIn("R1 (a b) vamos_resistor r=(vamos_hf1 ? vamos_hv1 : r)", text)
        self.assertIn('sweep vamos_w1 variable="vamos_av1" values=[1000.0, 2000.0]', text)
        self.assertNotIn('instance="X1:R1"', text)
        self.build(rc_netlist([an("alter", "a1", dev="X1.R1", param="r", value=2e3)]), "vacask")
        nl = rc_netlist([an("dc", "s1", sweep=sw("dev", "Rt", "r", mode="values", values=[1e3, 2e3]))])
        nl.body.insert(1, R("Rt", "in", "0", "rval*2"))
        for engine in ("vacask", "xyce"):
            self.build(nl, engine)
        nl.body[1] = R("Rt", "in", "0", 2000.0)                 # a constant: fine on both
        self.build(nl, "vacask")
        self.build(nl, "xyce")

    def test_source_field_alter_carries_the_resolved_source(self):
        # §5.3: the source re-resolved by spectre.resolve_source (S1) with the altered field (released in phase 1b)
        nl = rc_netlist([an("alter", "a1", dev="V1", param="val1", value=2.0), an("tran", "tr", stop=1e-3)])
        plan = self.build(nl)
        alt = [a for a in plan.actions if a.op == "alter"][0]
        self.assertEqual(alt.args["target"], ("instance", "V1", "val1"))
        src = alt.args["source"]
        self.assertEqual((src.wave, src.args["v2"], src.spectre["val1"], src.spectre["val0"]),
                         ("pulse", N(2.0), N(2.0), N(0.0)))
        self.assertIsNone(src.dc)                                 # the dc stays derived


class TestSourceStates(PlanCase):
    def test_given_dc_switches_around_every_tran_on_vacask(self):
        nl = rc_netlist([an("dc", "op1"), an("tran", "tr", stop=1e-3)])
        nl.body[1].source.dc = N(0.9)                            # V1: a pulse whose dc is given
        plan = self.build(nl, "vacask")
        ops = [(a.op, a.args.get("path"), a.args.get("type")) for a in plan.actions]
        self.assertEqual(ops, [("options", None, None), ("analysis", None, None),
                               ("source", "V1", "pulse"), ("analysis", None, None), ("source", "V1", "dc")])
        src = [a for a in plan.actions if a.op == "source"][0].args["source"]
        self.assertIs(src, nl.body[1].source)
        plan_x = self.build(nl, "xyce")
        self.assertEqual([a.op for a in plan_x.actions], ["options", "analysis", "analysis"])

    def test_derived_dc_needs_no_switching(self):
        nl = rc_netlist([an("tran", "tr", stop=1e-3)])
        plan = self.build(nl, "vacask")
        self.assertEqual([a.op for a in plan.actions], ["options", "analysis"])

    def test_sweep_of_a_derived_dc_brackets_the_analysis(self):
        nl = rc_netlist([an("dc", "sw1", sweep=sw("dev", "V1", "dc", "lin", start=0.0, stop=1.0, count=4))])
        plan = self.build(nl, "vacask")
        ops = [(a.op, a.args.get("type")) for a in plan.actions]
        self.assertEqual(ops, [("options", None), ("source", "dc"), ("analysis", None), ("source", "pulse")])
        self.assertEqual(plan.actions[1].args["path"], "V1")
        plan_x = self.build(nl, "xyce")
        self.assertEqual([a.op for a in plan_x.actions], ["options", "analysis"])

    def test_no_dc_bracket_around_a_tran_in_a_sweep_of_a_derived_dc(self):
        # R-emit-2: the waveform acts in tran (§3.8.1; ref5 p.686: dc, m and the tc's are the DC parameters), so a
        # tran under a sweep of a derived-dc source's dc keeps its waveform on VACASK, as on Xyce; the op beside it
        # is bracketed as before, and the plan notes once that the swept dc changes nothing in the tran
        nl = rc_netlist([an("sweep", "sw1", sweep=sw("dev", "V1", "dc", "values", values=[0.7, 0.9]),
                            children=[an("tran", "tr1", stop=1e-3), an("dc", "op1")])])
        plan = self.build(nl, "vacask")
        ops = [(a.op, a.args.get("type"), a.step.name if a.step else None) for a in plan.actions]
        self.assertEqual(ops, [("options", None, None), ("analysis", None, "tr1"), ("source", "dc", None),
                               ("analysis", None, "op1"), ("source", "pulse", None)])
        notes = [n.message for n in plan.notes if "tr1" in n.message and "V1" in n.message]
        self.assertEqual(len(notes), 1, plan.notes)
        self.assertIn("waveform", notes[0])
        self.assertEqual([n.severity for n in plan.notes if n.message in notes], [NOTE])


# -- §5.4 transient settings ---------------------------------------------------------------------------------

class TestTran(PlanCase):
    def tran(self, engine="vacask", settings=None, reltol=None, **args):
        nl = rc_netlist([an("tran", "tr", **args)])
        if reltol is not None:
            nl.options["reltol"] = N(reltol)
        plan = self.build(nl, engine, settings)
        return self.steps(plan)[0], plan

    def test_step_defaults_to_a_thousandth_of_the_span(self):
        """Spectre's tran step defaults to 0.001 (stop - start) [ref19]; §7.1/§7.2's worked example prints
        step=5e-06 and .TRAN 5e-06 for `tran stop=5m`, and Xyce's .TRAN needs the value (S3), so the plan
        carries it (merge, phase 1)."""
        tr, _ = self.tran(stop=5e-3)
        self.assertEqual(tr.args["step"], 5e-6)
        tr, _ = self.tran(stop=5e-3, step=1e-6)
        self.assertEqual(tr.args["step"], 1e-6)                   # a given step is kept

    def test_errpreset_rows(self):
        lib, plan = self.tran(stop=1e-3, errpreset="liberal")
        self.assertEqual((lib.args["reltol"], lib.args["relref"], lib.args["method"], lib.args["maxstep"], lib.args["lteratio"]),
                         (1e-2, "sigglobal", "trapgear2", 2e-5, 3.5))
        self.assertTrue(any("trapgear2" in n.message and "trap" in n.message and n.severity == NOTE for n in plan.notes))
        mod, _ = self.tran(stop=1e-3)
        self.assertEqual((mod.args["errpreset"], mod.args["reltol"], mod.args["relref"], mod.args["method"], mod.args["maxstep"],
                          mod.args["lteratio"]), ("moderate", 1e-3, "sigglobal", "traponly", 2e-5, 3.5))
        con, _ = self.tran(stop=1e-3, errpreset="conservative")
        self.assertEqual((con.args["reltol"], con.args["relref"], con.args["method"], con.args["maxstep"], con.args["lteratio"]),
                         (1e-4, "alllocal", "gear2only", 1e-5, 10.0))

    def test_reltol_scaling_and_cap(self):
        lib, _ = self.tran(stop=1e-3, errpreset="liberal", reltol=5e-3)
        self.assertEqual(lib.args["reltol"], 1e-2)               # 5e-2 capped at 0.01
        con, _ = self.tran(stop=1e-3, errpreset="conservative", reltol=1e-2)
        self.assertEqual(con.args["reltol"], 1e-3)

    def test_own_settings_win_and_maxstep_is_the_minimum(self):
        st, _ = self.tran(stop=1e-3, errpreset="liberal", method="gear2", relref="pointlocal", lteratio=2.0, maxstep=1e-6)
        self.assertEqual((st.args["method"], st.args["relref"], st.args["lteratio"], st.args["maxstep"]),
                         ("gear2", "pointlocal", 2.0, 1e-6))
        st2, _ = self.tran(stop=1e-3, maxstep=1e-3)
        self.assertEqual(st2.args["maxstep"], 2e-5)

    def test_aps_and_errpreset_settings(self):
        st, _ = self.tran(stop=1e-3, errpreset="liberal", settings=Settings(aps="conservative"))
        self.assertEqual(st.args["errpreset"], "conservative")
        st, _ = self.tran(stop=1e-3, settings=Settings(errpreset="liberal"))
        self.assertEqual(st.args["errpreset"], "liberal")
        st, _ = self.tran(stop=1e-3, errpreset="conservative", settings=Settings(errpreset="liberal"))
        self.assertEqual(st.args["errpreset"], "conservative")   # the tran's own beats +errpreset= [I, §14 q.25]

    def test_tran_errors(self):
        self.assertTrue(any("stop is required" in e for e in self.fails(rc_netlist([an("tran", "tr")]))))
        self.assertTrue(any("start=" in e for e in self.fails(rc_netlist([an("tran", "tr", stop=1e-3, start=1e-4)]))))
        self.assertTrue(any("maxstepratio" in e for e in self.fails(rc_netlist([an("tran", "tr", stop=1e-3, maxstepratio=2.0)]))))
        self.assertTrue(any("noisefmax" in e for e in self.fails(rc_netlist([an("tran", "tr", stop=1e-3, noisefmax=1e9)]))))
        self.assertTrue(any("errpreset=wild" in e for e in self.fails(rc_netlist([an("tran", "tr", stop=1e-3, errpreset="wild")]))))
        self.assertTrue(any("ic=" in e for e in self.fails(rc_netlist([an("tran", "tr", stop=1e-3, ic="odd")]))))

    def test_skipdc_values_other_than_no_and_yes_are_errors(self):
        # R-plan-2: ref19 p.417 lists no yes useprevic waveless rampup autodc sigrampup dcrampup; §0 runs no and yes
        # only (rampup starts from a zero solution with ramping sources, pp.428-429: run as a plain DC start, the
        # waveform was silently wrong); a value Spectre does not list is an error too
        for v in ("useprevic", "waveless", "rampup", "autodc", "sigrampup", "dcrampup"):
            for engine in ("vacask", "xyce"):
                errs = self.fails(rc_netlist([an("tran", "tr", stop=1e-3, skipdc=v)]), engine)
                self.assertTrue(any("tran tr: skipdc=%s is not supported" % v in e for e in errs), (v, engine, errs))
        for v, text in (("bogus", "skipdc=bogus is not one of"), (1.0, "skipdc=1 is not one of")):
            errs = self.fails(rc_netlist([an("tran", "tr", stop=1e-3, skipdc=v)]))
            self.assertTrue(any(text in e for e in errs), errs)
        for v in ("no", "yes", "NO", "Yes"):
            st, _ = self.tran(stop=1e-3, skipdc=v)
            self.assertEqual(st.args["skipdc"], v.lower())          # the emitters read "yes" (uic)

    def test_xyce_tolerance_note(self):
        # D2 (R-plan-20): the note only for a tran whose tolerances or method the run set: an options reltol, the
        # tran's own reltol/relref/lteratio/errpreset/method, +paramdefault, +errpreset or +aps; a run that sets
        # none prints none (phase 2 noted every tran on Xyce: this assertion changed with D2)
        st, plan = self.tran("xyce", stop=1e-3)
        self.assertFalse(any("Xyce takes the method" in n.message for n in plan.notes), plan.notes)
        for kw in ({"errpreset": "liberal"}, {"method": "gear2"}, {"relref": "alllocal"}, {"lteratio": 4.0},
                   {"reltol": 1e-4}, {"settings": Settings(aps="liberal")}, {"settings": Settings(errpreset="moderate")},
                   {"settings": Settings(paramdefault=[("tran", "method", "gear2")])}):
            st, plan = self.tran("xyce", stop=1e-3, **kw)
            self.assertEqual(len([n for n in plan.notes if "Xyce takes the method" in n.message]), 1, kw)
        st, plan = self.tran("vacask", stop=1e-3, errpreset="conservative")
        self.assertFalse(any("Xyce" in n.message for n in plan.notes))

    def test_write_and_state_files(self):
        st, _ = self.tran(stop=1e-3, write="s.ic", readns="n.ns")
        self.assertTrue(st.full_solution)
        self.assertEqual((st.args["write"], st.args["readns"]), ("s.ic", "n.ns"))
        st2, _ = self.tran(stop=1e-3)
        self.assertFalse(st2.full_solution)

    def test_prevoppoint_and_useprevic(self):
        nl = rc_netlist([an("tran", "tr", stop=1e-3),
                         an("ac", "a1", prevoppoint="yes", sweep=sw("freq", mode="dec", start=1.0, stop=10.0, count=1)),
                         an("tran", "tr2", stop=1e-3, useprevic="yes")])
        tr, ac, tr2 = self.steps(self.build(nl, "vacask"))
        self.assertEqual((tr.stores, ac.uses, ac.stores, tr2.uses), ("vamos_s1", "vamos_s1", "vamos_s2", "vamos_s2"))
        trx, acx, tr2x = self.steps(self.build(nl, "xyce"))
        self.assertEqual((trx.stores, acx.uses, tr2x.uses), (None, None, None))
        plan_x = self.build(nl, "xyce")
        self.assertTrue(any("Xyce recomputes the operating point" in n.message and n.severity == WARNING for n in plan_x.notes))

    def test_device_ic_on_c_or_l(self):
        nl = rc_netlist([an("tran", "tr", stop=1e-3)])
        nl.body[-1].body[1].params["ic"] = N(1.0)                # C1 ic=1
        self.assertTrue(any("device ic=" in e for e in self.fails(nl)))
        nl.analyses = [an("tran", "tr", stop=1e-3, ic="node")]
        self.build(nl)


# -- §3.5, §3.6, §3.7 evaluation per path and state ------------------------------------------------------------

class TestEvaluation(PlanCase):
    def test_out_of_domain_at_a_sweep_point_names_the_path_and_point(self):
        nl = rc_netlist([an("dc", "a1", sweep=sw("param", param="rval", mode="values", values=[1e3, 2e3]))])
        nl.body[-1].body[0].value = P("1k/(r-2000)")             # X1.R1: fine at 1k, division by zero at 2k
        errs = self.fails(nl)
        self.assertTrue(any("rval=2000" in e and "path X1" in e and "R1" in e for e in errs), errs)
        nl.analyses = [an("dc", "a1", sweep=sw("param", param="rval", mode="values", values=[1e3, 1.5e3]))]
        self.build(nl)

    def test_subckt_parameter_failing_on_a_path(self):
        nl = rc_netlist([an("dc", "a1", sweep=sw("sub", "X1", "r", "values", values=[1e3, 0.0]))])
        nl.body[-1].body[0].value = P("1k/r")
        errs = self.fails(nl)
        self.assertTrue(any("X1:r=0" in e for e in errs), errs)

    def test_paramtest_per_path_and_state(self):
        nl = rc_netlist()
        nl.body[-1].body.append(ParamTest("pt", [("warnif", P("r > 500")), ("errorif", P("r > 1500")), ("printif", P("r > 0"))],
                                          "r is large", "", "t.scs:8"))
        nl.analyses = [an("dc", "op1")]
        plan = self.build(nl)
        sev = sorted((n.severity, "path X1" in n.message) for n in plan.notes if "paramtest pt" in n.message)
        self.assertEqual(sev, [(NOTE, True), (WARNING, True)])
        nl.analyses = [an("dc", "a1", sweep=sw("param", param="rval", mode="values", values=[1e3, 2e3]))]
        errs = self.fails(nl)
        self.assertTrue(any("paramtest pt on path X1" in e and "r is large" in e for e in errs), errs)
        nl.body[-1].body[-1] = ParamTest("pt2", [], "stop here", "fatal", "t.scs:8")
        nl.analyses = [an("dc", "op1")]
        self.assertTrue(any("stop here (fatal)" in e for e in self.fails(nl)))
        nl.body[-1].body[-1] = ParamTest("pt3", [], "fyi", "status", "t.scs:8")
        plan = self.build(nl)
        self.assertTrue(any(n.severity == NOTE and "fyi" in n.message for n in plan.notes))

    def test_condition_change_is_an_error(self):
        def netlist(values):
            nl = rc_netlist()
            nl.body.append(Cond([(P("rval > 1500"), [R("Rbig", "in", "0", 10e3)])], [R("Rsmall", "in", "0", 1e3)], "t.scs:12"))
            nl.analyses = [an("dc", "a1", sweep=sw("param", param="rval", mode="values", values=values))]
            return nl
        self.build(netlist([1000.0, 1200.0]))                    # inside one branch: runs
        errs = self.fails(netlist([1000.0, 2000.0]))
        self.assertTrue(any("changes a structural condition" in e and "rval=2000" in e for e in errs), errs)
        nl = netlist([1000.0])
        nl.analyses = [an("dc", "op1"), an("alter", "al", param="rval", value=2000.0), an("dc", "op2")]
        self.assertTrue(any("alter al changes a structural condition" in e for e in self.fails(nl)))

    def test_condition_on_a_subckt_parameter_and_temp(self):
        nl = rc_netlist()
        nl.body[-1].body.append(Cond([(P("r > 1500"), [R("Rb", "a", "0", 10e3)])], [], "t.scs:12"))
        nl.analyses = [an("dc", "a1", sweep=sw("sub", "X1", "r", "values", values=[1e3, 2e3]))]
        self.assertTrue(any("path X1" in e for e in self.fails(nl)))
        nl = rc_netlist()
        nl.body.append(Cond([(P("temper > 50"), [R("Rh", "in", "0", 10e3)])], [], "t.scs:12"))
        nl.analyses = [an("dc", "a1", sweep=sw("param", param="temp", mode="values", values=[0.0, 100.0]))]
        self.assertTrue(any("changes a structural condition" in e for e in self.fails(nl)))
        nl.analyses = [an("dc", "a1", sweep=sw("param", param="temp", mode="values", values=[0.0, 40.0]))]
        self.build(nl)

    def test_dependents_and_analysis_parameters(self):
        nl = rc_netlist([an("tran", "tr", stop=P("rdep*1e-6"))])
        nl.body.insert(1, Param("rdep", P("rval*2"), "t.scs:3"))
        nl.values["rdep"] = 2000.0
        plan = self.build(nl)
        self.assertEqual(plan.dependents, set())
        self.assertEqual(self.steps(plan)[0].args["stop"], 2e-3)
        nl.analyses.append(an("dc", "a1", sweep=sw("param", param="rval", mode="values", values=[1e3, 2e3])))
        errs = self.fails(nl)
        self.assertTrue(any("tran tr stop" in e and "reads rdep" in e for e in errs), errs)
        nl.analyses = [an("dc", "a1", sweep=sw("param", param="rval", mode="values", values=[1e3, 2e3]))]
        plan = self.build(nl)
        self.assertEqual(plan.dependents, {"rval", "rdep"})

    def test_ic_value_reading_a_variable(self):
        nl = rc_netlist([an("dc", "a1", sweep=sw("param", param="rval", mode="values", values=[1e3, 2e3]))])
        nl.ics["out"] = P("rval/1000")
        self.assertTrue(any("ic out" in e and "reads rval" in e for e in self.fails(nl)))
        nl.ics["out"] = P("1/0")
        del nl.analyses[:]
        nl.analyses = [an("dc", "op1")]
        self.assertTrue(any("ic out" in e for e in self.fails(nl)))

    def test_every_sweep_point_of_a_product(self):
        nl = rc_netlist([an("sweep", "s", sweep=sw("param", param="temp", mode="values", values=[0.0, 100.0]),
                            children=[an("dc", "a1", sweep=sw("param", param="rval", mode="values", values=[1e3, 2e3]))])])
        nl.body.insert(1, R("Rx", "in", "0", "1k/((rval-2000)+(temper-100))"))   # zero only at the corner
        errs = self.fails(nl)
        self.assertTrue(any("temp=100" in e and "rval=2000" in e for e in errs), errs)

    def test_bin_change_is_an_error(self):
        # §3.7: a sweep of M1's l from entry nch.1 (l < 1u) into nch.2 (l >= 1u) changes the selected card
        # (tables.select_bin_spectre, S3; released in phase 1b)
        def group_netlist(l_values):
            nl = rc_netlist([an("dc", "s1", sweep=sw("dev", "M1", "l", mode="values", values=l_values))])
            for k, lmin, lmax, vth in ((1, 0.1e-6, 1e-6, 0.4), (2, 1e-6, 10e-6, 0.5)):
                m = card("nch.%d" % k, "nmos", "bsim3v3", 49, lmin=lmin, lmax=lmax, wmin=0.1e-6, wmax=1e-4,
                         vtho=vth, tox=4e-9)
                m.base, m.bin_index, m.bin_rule = "nch", k, "spectre"
                nl.body.insert(1, m)
            nl.body.insert(3, mos("M1", "in", "in", "0", "0", "nch", w=2e-6, l=0.5e-6))
            nl.body[3].prim = "bsim3v3"
            return nl
        errs = self.fails(group_netlist([0.5e-6, 2e-6]))
        self.assertTrue(any("changes the bin" in e and "nch.2" in e and "nominal nch.1" in e for e in errs), errs)
        self.build(group_netlist([0.5e-6, 0.8e-6]))                # inside one entry: fine

    def test_a_model_group_instance_needs_both_w_and_l(self):
        # D4 (R-plan-16): 'You must specify both length (l) and width (w) on the device instance line to enable
        # automatic model selection' [M ref5 p.203]: an instance of a model group without either is an error naming
        # the instance, the group and the parameter, not a selection with the master's default geometry
        def group_netlist(analyses, **geom):
            nl = rc_netlist(analyses)
            for k, lmin, lmax, vth in ((1, 0.1e-6, 1e-6, 0.4), (2, 1e-6, 10e-6, 0.5)):
                m = card("nch.%d" % k, "nmos", "bsim3v3", 49, lmin=lmin, lmax=lmax, wmin=0.1e-6, wmax=1e-4,
                         vtho=vth, tox=4e-9)
                m.base, m.bin_index, m.bin_rule = "nch", k, "spectre"
                nl.body.insert(1, m)
            nl.body.insert(3, mos("M1", "in", "in", "0", "0", "nch", **geom))
            nl.body[3].prim = "bsim3v3"
            return nl
        for engine in ("vacask", "xyce"):
            for geom, missing in (({"l": 0.5e-6}, "w"), ({"w": 2e-6}, "l"), ({}, "w")):
                errs = self.fails(group_netlist([an("dc", "op1")], **geom), engine)
                hits = [e for e in errs if "instance M1" in e and "model group nch" in e and "no %s" % missing in e
                        and "ref5 p.203" in e]
                self.assertEqual(len(hits), 1, (geom, errs))
            sweep = an("dc", "s1", sweep=sw("param", param="rval", mode="values", values=[1e3, 2e3]))
            nl = group_netlist([sweep], l=0.5e-6)                # every point re-checks the instance: one error
            self.assertEqual(len([e for e in self.fails(nl, engine) if "ref5 p.203" in e]), 1)
            self.build(group_netlist([an("dc", "op1")], w=2e-6, l=0.5e-6), engine)


# -- §3.9 run-dependent card rules, §3.8.1 sources --------------------------------------------------------------

class TestRunDependent(PlanCase):
    def mos_netlist(self, analyses, **card_params):
        nl = rc_netlist(analyses)
        nl.body.insert(1, card("nch", "nmos", "mos1", 1.0, **card_params))
        nl.body.insert(2, mos("M1", "in", "in", "0", "0", "nch", w=1e-6, l=1e-6))
        return nl

    def warnings(self, plan, text):
        return [n for n in plan.notes if n.severity == WARNING and text in n.message]

    def test_capmod_warning_only_when_an_ac_noise_xf_or_tran_runs(self):
        self.assertEqual(self.warnings(self.build(self.mos_netlist([an("dc", "op1")])), "capmod"), [])
        w = self.warnings(self.build(self.mos_netlist([an("tran", "tr", stop=1e-3)])), "capmod")
        self.assertEqual(len(w), 1)
        self.assertIn("model nch (mos1): capmod absent", w[0].message)
        self.assertIn("tran", w[0].message)
        w = self.warnings(self.build(self.mos_netlist([an("ac", "a1", sweep=sw("freq", mode="lin", start=1.0, stop=2.0, count=1))],
                                                      capmod=Str("bsim"))), "capmod")
        self.assertEqual(len(w), 1)
        self.assertEqual(self.warnings(self.build(self.mos_netlist([an("tran", "tr", stop=1e-3)], capmod=Str("meyer"))),
                                       "capmod"), [])

    def test_noise_only_rows(self):
        nl = rc_netlist([an("dc", "op1")])
        nl.body.insert(1, card("rmod", "r", "resistor", None, kf=1e-20))
        nl.body.insert(2, Instance("R2", "r", ["in", "0"], master="rmod", value=N(1e3), origin="t.scs:4", prim="resistor"))
        self.assertEqual(self.warnings(self.build(nl), "kf"), [])
        nl.analyses = [an("noise", "nz", nodes=["out"], sweep=sw("freq", mode="lin", start=1.0, stop=2.0, count=1))]
        w = self.warnings(self.build(nl), "kf")
        self.assertEqual(len(w), 1)
        self.assertIn("noise", w[0].message)

    def test_noise_only_rows_are_silent_for_a_parameter_the_card_leaves_out(self):
        # §10's grammar (tables.rule_for): a row reaches an absent parameter only through match "absent",
        # a `when` on it, or a default row; the noise-only rows warn about a written value (§3.9: weexp and
        # leexp "silently at 0, else with the noise warning"; kf "passed with the noise warning")
        noise = [an("noise", "nz", nodes=["out"], sweep=sw("freq", mode="lin", start=1.0, stop=2.0, count=1))]

        def resistor_netlist(**params):
            nl = rc_netlist(noise)
            nl.body.insert(1, card("rmod", "r", "resistor", None, **params))
            nl.body.insert(2, Instance("R2", "r", ["in", "0"], master="rmod", value=N(1e3), origin="t.scs:4",
                                       prim="resistor"))
            return nl
        self.assertEqual(self.warnings(self.build(resistor_netlist()), "absent"), [])
        self.assertEqual(self.warnings(self.build(resistor_netlist(weexp=0.0, leexp=0.0)), "exp"), [])
        w = self.warnings(self.build(resistor_netlist(weexp=0.5)), "weexp")
        self.assertEqual(len(w), 1)
        self.assertIn("weexp=0.5", w[0].message)
        self.assertEqual([n.message for n in self.build(self.mos_netlist(noise)).notes
                          if n.severity == WARNING and any(k in n.message for k in ("kf", "ef ", "wnoi"))], [])

    def test_diode_eg_at_temp_other_than_tnom(self):
        def netlist(temp, **params):
            nl = rc_netlist([an("dc", "op1")])
            nl.temp = temp
            nl.body.insert(1, card("dm", "d", "diode", 1.0, **params))
            nl.body.insert(2, Instance("D1", "d", ["in", "0"], master="dm", origin="t.scs:4", prim="diode"))
            return nl
        self.assertEqual(self.warnings(self.build(netlist(27.0)), "eg absent"), [])
        w = self.warnings(self.build(netlist(85.0)), "eg absent")
        self.assertEqual(len(w), 1)
        self.assertIn("temp=85", w[0].message)
        self.assertEqual(self.warnings(self.build(netlist(85.0, tnom=85.0)), "eg absent"), [])
        self.assertEqual(self.warnings(self.build(netlist(85.0, eg=1.11)), "eg"), [])

    def test_eg_warning_follows_every_temperature_the_card_runs_at(self):
        # R-plan-6: a temperature sweep (an analysis's own or a sweep block), an instance trise (given, swept with
        # dev= or altered) and a tnom sweep run the card at temp != tnom as an alter of temp does (§0's approximation)
        def netlist(analyses, trise=None, **params):
            nl = rc_netlist(analyses)
            nl.body.insert(1, card("dm", "d", "diode", 1.0, **params))
            p = {} if trise is None else {"trise": N(trise)}
            nl.body.insert(2, Instance("D1", "d", ["in", "0"], master="dm", params=p, origin="t.scs:4", prim="diode"))
            return nl
        temp = sw("param", param="temp", mode="values", values=[0.0, 100.0])
        cases = (
            ("dc sweep", [an("dc", "swt", sweep=temp)], None, "swt runs at temp=0 "),
            ("sweep block", [an("sweep", "swb", sweep=temp, children=[an("dc", "op1")])], None, "op1 runs at temp=0 "),
            ("trise", [an("dc", "op1")], 50.0, "op1 runs instance D1 at temp=77 (trise=50) "),
            ("dev= trise sweep", [an("dc", "swr", sweep=sw("dev", "D1", "trise", "values", values=[0.0, 20.0]))], None,
             "swr runs instance D1 at temp=47 (trise=20) "),
            ("trise alter", [an("alter", "al", dev="D1", param="trise", value=10.0), an("dc", "op1")], None,
             "op1 runs instance D1 at temp=37 (trise=10) "),
            ("tnom sweep", [an("dc", "swn", sweep=sw("param", param="tnom", mode="values", values=[27.0, 50.0]))], None,
             "swn runs at temp=27 where the card's tnom is 50 "),
        )
        for what, analyses, trise, text in cases:
            w = self.warnings(self.build(netlist(analyses, trise)), "eg absent")
            self.assertEqual(len(w), 1, (what, [n.message for n in w]))
            self.assertIn("model dm (diode): eg absent", w[0].message)
            self.assertIn(text, w[0].message, what)
        # the card's eg given, or every temperature at the card's tnom: no warning
        self.assertEqual(self.warnings(self.build(netlist([an("dc", "swt", sweep=temp)], eg=1.11)), "eg"), [])
        same = sw("param", param="temp", mode="values", values=[27.0])
        self.assertEqual(self.warnings(self.build(netlist([an("dc", "swt", sweep=same)])), "eg absent"), [])
        self.assertEqual(self.warnings(self.build(netlist([an("dc", "op1")], 0.0)), "eg absent"), [])
        nl = netlist([an("dc", "swn", sweep=sw("param", param="tnom", mode="values", values=[27.0, 50.0]))], tnom=27.0)
        self.assertEqual(self.warnings(self.build(nl), "eg absent"), [])       # the card's own tnom does not move

    def test_source_run_dependent_errors(self):
        nl = rc_netlist([an("xf", "x1", nodes=["out"], sweep=sw("freq", mode="lin", start=1.0, stop=2.0, count=1))])
        nl.body[1].source.spectre["xfmag"] = N(2.0)
        self.assertTrue(any("xfmag" in e for e in self.fails(nl)))
        nl.body[1].source.spectre["xfmag"] = N(1.0)
        self.build(nl)
        nl.body[1].source.spectre["noisefile"] = Str("n.dat")
        self.build(nl)                                           # silent without a noise analysis
        nl.analyses = [an("noise", "nz", nodes=["out"], sweep=sw("freq", mode="lin", start=1.0, stop=2.0, count=1))]
        self.assertTrue(any("noisefile" in e for e in self.fails(nl)))

    def test_rule_lookup(self):
        row = T.SPECTRE_MASTERS["mos1"]
        self.assertEqual(PL.rule_for(row.params, "capmod", {}).warn, "analyses=ac,noise,xf,tran")
        self.assertEqual(PL.rule_for(row.params, "capmod", {"capmod": Str("meyer")}).warn, "")
        self.assertEqual(PL.rule_for(row.params, "capmod", {"capmod": Str("yang")}).action, "error")
        res = T.SPECTRE_MASTERS["resistor"]
        self.assertEqual(PL.rule_for(res.params, "scaler", {"scaler": N(1.0)}).action, "strip")
        self.assertEqual(PL.rule_for(res.params, "scaler", {"scaler": N(2.0)}).action, "error")
        self.assertEqual(PL.rule_for(res.params, "af", {"kf": N(1e-20)}).action, "default")
        self.assertEqual(PL.rule_for(res.params, "af", {"kf": N(1e-20), "af": N(1.5)}).action, "pass")
        bjt = T.SPECTRE_MASTERS["bjt"]
        self.assertEqual(PL.rule_for(bjt.params, "mje", {}).value, "1/3")


# -- §3.11 noise and xf -------------------------------------------------------------------------------------------

class TestNoiseXf(PlanCase):
    def freq(self):
        return sw("freq", mode="dec", start=1.0, stop=1e3, count=10)

    def test_noise_input(self):
        nl = rc_netlist([an("dc", "op1"), an("noise", "nz", nodes=["out"], sweep=self.freq())])
        st = self.steps(self.build(nl))[1]
        self.assertEqual((st.id, st.noise_input), ("vamos_a2", "vamos_nin2"))
        self.assertEqual((st.args["p"], st.args["n"]), ("out", "0"))     # §3.11 `p n` in args; n = ground
        nl.analyses = [an("noise", "nz", nodes=["out", "0"], iprobe="V1", sweep=self.freq())]
        st = self.steps(self.build(nl))[0]
        self.assertEqual(st.noise_input, "V1")
        nl.body.append(Instance("IP", "v", ["in", "mid"], source=Source(dc=N(0.0)), origin="t.scs:4", prim="iprobe"))
        nl.analyses = [an("noise", "nz", nodes=["out"], iprobe="IP", sweep=self.freq())]
        self.assertTrue(any("iprobe=IP" in e for e in self.fails(nl)))
        nl.analyses = [an("noise", "nz", oprobe="V1", sweep=self.freq())]
        self.assertTrue(any("current output" in e for e in self.fails(nl)))
        nl.analyses = [an("noise", "nz", oprobe="X1.R1", sweep=self.freq())]
        st = self.steps(self.build(nl))[0]
        self.assertEqual((st.args["p"], st.args["n"]), ("in", "out"))   # the probe's terminals as IR node paths
        nl.body.append(Instance("E1", "e", ["o2", "0", "in", "0"], value=N(2.0), origin="t.scs:4", prim="vcvs"))
        nl.analyses = [an("noise", "nz", oprobe="E1", oportv=2.0, sweep=self.freq())]
        self.assertTrue(any("oportv=2" in e for e in self.fails(nl)))
        nl.analyses = [an("noise", "nz", oprobe="E1", sweep=self.freq())]
        self.build(nl)
        nl.analyses = [an("noise", "nz", sweep=self.freq())]
        self.assertTrue(any("output node pair" in e for e in self.fails(nl)))

    def test_an_output_node_named_through_a_port_is_its_net(self):
        # R-plan-10: (X1.b 0) names the net X1's port b is bound to (out), as oprobe's terminals are resolved: the
        # engines know no node X1:b ("Node 'X1:y' not found", "Output function variable X1:Y not found")
        for kind in ("noise", "xf"):
            nl = rc_netlist([an(kind, "nz", nodes=["X1.b"], sweep=self.freq())])
            st = self.steps(self.build(nl))[0]
            self.assertEqual((st.args["p"], st.args["n"]), ("out", "0"), kind)
            nl.analyses = [an(kind, "nz", nodes=["X1.b", "X1.a"], sweep=self.freq())]
            st = self.steps(self.build(nl))[0]
            self.assertEqual((st.args["p"], st.args["n"]), ("out", "in"), kind)
            nl.analyses = [an(kind, "nz", nodes=["X1.zz", "out"], sweep=self.freq())]   # not a port: as written
            st = self.steps(self.build(nl))[0]
            self.assertEqual((st.args["p"], st.args["n"]), ("X1.zz", "out"), kind)

    def test_ac_errors(self):
        nl = rc_netlist([an("ac", "a1", skipdc="yes", sweep=self.freq())])
        self.assertTrue(any("skipdc=yes" in e for e in self.fails(nl)))
        # R-plan-2: ac, noise and xf take skipdc no|yes only [M ref19 p.44]
        for kind in ("ac", "noise", "xf"):
            nl.analyses = [an(kind, "a1", nodes=["out"], skipdc="rampup", sweep=self.freq())]
            self.assertTrue(any("skipdc=rampup is not one of no, yes" in e for e in self.fails(nl)), kind)
            nl.analyses = [an(kind, "a1", nodes=["out"], skipdc="no", sweep=self.freq())]
            self.build(nl)
        nl.analyses = [an("ac", "a1", perturbation="nonlinear", sweep=self.freq())]
        self.assertTrue(any("perturbation" in e for e in self.fails(nl)))
        nl.analyses = [an("ac", "a1")]
        self.assertTrue(any("frequency sweep" in e for e in self.fails(nl)))
        nl.analyses = [an("ac", "a1", sweep=sw("dev", "V1", "dc", "lin", start=0.0, stop=1.0, count=2))]
        self.assertTrue(any("needs freq=" in e for e in self.fails(nl)))
        nl.analyses = [an("ac", "a1", freq=1e3, sweep=sw("dev", "V1", "dc", "lin", start=0.0, stop=1.0, count=2))]
        st = self.steps(self.build(nl))[0]
        self.assertEqual((st.own.target, st.args["freq"]), (("instance", "V1", "dc"), 1e3))

    def test_xf(self):
        nl = rc_netlist([an("xf", "x1", nodes=["out"], sweep=self.freq())])
        st = self.steps(self.build(nl, "vacask"))[0]
        self.assertEqual((st.args["p"], st.args["n"]), ("out", "0"))
        self.assertTrue(any("xf is not available on Xyce" in e for e in self.fails(nl, "xyce")))
        nl.analyses = [an("xf", "x1", nodes=["out"], stimuli="nodes_and_terminals", sweep=self.freq())]
        self.assertTrue(any("stimuli" in e for e in self.fails(nl)))
        nl.analyses = [an("xf", "x1", probe="V1", sweep=self.freq())]
        self.assertTrue(any("current probe" in e for e in self.fails(nl)))

    def test_dc_dispositions(self):
        nl = rc_netlist([an("dc", "op1", force="all", hysteresis="yes", oppoint="rawfile", homotopy="gmin")])
        errs = self.fails(nl)
        self.assertTrue(any("force=all" in e for e in errs))
        self.assertTrue(any("hysteresis=yes" in e for e in errs))
        nl.analyses = [an("dc", "op1", oppoint="rawfile", homotopy="gmin", print="yes", maxiters=50.0)]
        plan = self.build(nl)
        msgs = texts(plan.notes)
        self.assertTrue(any(m.startswith("note") and "oppoint=rawfile" in m for m in msgs), msgs)
        self.assertTrue(any(m.startswith("note") and "homotopy" in m for m in msgs), msgs)
        self.assertEqual(self.steps(plan)[0].args["maxiters"], 50.0)


# -- phase 2c: state files written and read in one run (D9) ----------------------------------------------------

class TestStateFiles(PlanCase):
    """D9 (R-plan-7, R-emit-1): vamos reads a state file before the run, so a file an EARLIER analysis of the run
    writes (write=, writefinal=) would be a previous run's or none: a readic= or read= of it is an error naming
    both analyses; a readns= of it a warning naming both, and the file is not read (the netlist's nodesets); the
    same-analysis idiom readns=x write=x stays (§5.4).  Paths compare as the flow resolves them: a write per
    §2.5 (-outdir for a name without /), a read against the cwd (§14 q.3)."""

    def setUp(self):
        super().setUp()
        self.d = tempfile.mkdtemp(prefix="vamos_plan_sf_")
        with open(os.path.join(self.d, "t1.ic"), "w") as fh:          # a previous run's file
            fh.write("# State file generated by Spectre\nout 0.6321\n")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)
        super().tearDown()

    def run_in(self, nl, engine="vacask", settings=None, **kw):
        return PL.build(nl, engine, settings or Settings(), cwd=self.d, **kw)

    def fails_in(self, nl, engine="vacask", settings=None, **kw):
        with self.assertRaises(NoteError) as cm:
            self.run_in(nl, engine, settings, **kw)
        return errors(cm.exception)

    def chain(self, first, second):
        return rc_netlist([an(first[0], "tr1", stop=1e-3, **first[1]), an("alter", "off", dev="V1", param="val1", value=0.0),
                           an(second[0], "tr2", stop=1e-3, **second[1])])

    def test_reading_a_file_an_earlier_analysis_writes_is_an_error(self):
        for engine in ("vacask", "xyce"):
            for wkey, rkey in (("writefinal", "readic"), ("write", "readic"), ("writefinal", "read")):
                nl = self.chain(("tran", {wkey: "t1.ic", "ic": "node"}), ("tran", {rkey: "t1.ic"}))
                errs = self.fails_in(nl, engine)
                hits = [e for e in errs if "tran tr2: %s=t1.ic" % rkey in e and "tran tr1" in e and "%s=t1.ic" % wkey in e]
                self.assertEqual(len(hits), 1, (engine, wkey, rkey, errs))
            nl = rc_netlist([an("dc", "op1", write="s.dc"), an("tran", "tr2", stop=1e-3, readic="s.dc")])
            self.assertTrue(any("tran tr2: readic=s.dc" in e and "dc op1" in e for e in self.fails_in(nl, engine)))

    def test_the_file_of_a_later_analysis_or_of_the_same_one_is_read_as_spectre_reads_it(self):
        # tr1 reads what a previous run left, as Spectre does, before tr2 rewrites it; one analysis reading and
        # writing the same file is §5.4's idiom
        nl = rc_netlist([an("tran", "tr1", stop=1e-3, readic="t1.ic"), an("tran", "tr2", stop=1e-3, writefinal="t1.ic")])
        plan = self.run_in(nl)
        self.assertEqual(self.steps(plan)[0].args["readic"], "t1.ic")
        nl = rc_netlist([an("dc", "op1", readns="t1.ic", write="t1.ic"), an("tran", "tr1", stop=1e-3, readic="t1.ic",
                                                                              writefinal="x.ic")])
        errs = self.fails_in(nl)
        self.assertTrue(any("tran tr1: readic=t1.ic" in e and "dc op1" in e for e in errs), errs)
        plan = self.run_in(rc_netlist([an("dc", "op1", readns="t1.ic", write="t1.ic")]))
        self.assertEqual((self.steps(plan)[0].args["readns"], [n.message for n in plan.notes]), ("t1.ic", []))

    def test_a_readns_of_such_a_file_is_a_warning_and_the_file_is_not_read(self):
        # UG p.204's Drift/XferVsTemp example still runs: the later analysis uses the netlist's nodesets
        nl = rc_netlist([an("dc", "drift", readns="t1.ic", write="t1.ic"), an("dc", "op2", readns="t1.ic")])
        for engine in ("vacask", "xyce"):
            plan = self.run_in(copy.deepcopy(nl), engine)
            drift, op2 = self.steps(plan)
            w = [n.message for n in plan.notes if n.severity == WARNING]
            self.assertEqual(len(w), 1, w)
            self.assertIn("dc op2: readns=t1.ic", w[0])
            self.assertIn("dc drift", w[0])
            self.assertIn("is not read", w[0])
            self.assertNotIn("readns", op2.args)
            self.assertIsNone(T.state_file_nodes(op2.args, T.STATE_NS_KEYS))     # never the stale file
            self.assertEqual(drift.args["readns"], "t1.ic")                     # the idiom keeps its read
            self.assertIsNotNone(T.state_file_nodes(dict(drift.args, readns=os.path.join(self.d, "t1.ic")),
                                                    T.STATE_NS_KEYS))

    def test_paths_compare_as_the_flow_resolves_them(self):
        absolute = os.path.join(self.d, "t1.ic")
        for w, r, settings, kw, same in (
                ("t1.ic", absolute, None, {}, True),                         # relative and absolute: one file
                ("./t1.ic", "t1.ic", None, {}, True),
                ("t1.ic", "t1.ic", Settings(outdir="out"), {}, False),       # §2.5: the write goes into -outdir
                ("t1.ic", "out/t1.ic", Settings(outdir="out"), {}, True),
                ("t1.ic", "res/t1.ic", None, {"outdir": os.path.join(self.d, "res")}, True),   # the flow's -outdir
                ("sub/t1.ic", "sub/t1.ic", Settings(outdir="out"), {}, True)):   # a name with / is not moved
            nl = self.chain(("tran", {"writefinal": w}), ("tran", {"readic": r}))
            if same:
                self.assertTrue(any("tran tr2: readic=" in e for e in self.fails_in(nl, settings=settings, **kw)),
                                (w, r))
            else:
                self.run_in(nl, settings=settings, **kw)

    def test_without_a_cwd_the_process_cwd_resolves_relative_names(self):
        nl = self.chain(("tran", {"writefinal": "t1.ic"}), ("tran", {"readic": "t1.ic"}))
        self.assertTrue(any("tran tr2: readic=t1.ic" in e for e in self.fails(nl)))


# -- phase 2c: parsed decks (the Spectre dialect, no engine) ----------------------------------------------------

def parse_text(text, name="deck.scs", files=()):
    d = tempfile.mkdtemp(prefix="vamos_plan_deck_")
    try:
        for src in files:
            shutil.copyfile(src, os.path.join(d, os.path.basename(src)))
        path = os.path.join(d, name)
        with open(path, "w") as fh:
            fh.write(text)
        return spectre.parse(path, d, spectre.SpectreParseOpts())
    finally:
        shutil.rmtree(d, ignore_errors=True)


GROUP_WITHOUT_WL = """simulator lang=spectre
Vg (g 0) vsource dc=1.2
Vd (d 0) vsource dc=1.2
M1 (d g 0 0) nch
model nch bsim3v3 {
  1: type=n vtho=0.4 tox=4n toxm=4n u0=0.04 lmin=0.1u lmax=10u wmin=0.1u wmax=10u
  2: type=n vtho=0.5 tox=4n toxm=4n u0=0.04 lmin=0.1u lmax=10u wmin=10u wmax=100u
}
op1 dc
"""


class TestParsedDecks(unittest.TestCase):
    """Decks through spectre.parse and plan.build in the Spectre dialect (plan.DIALECT "spectre")."""

    def test_e2e11_bind_decks(self):
        # R-plan-21, R-tests-7, R-tests-14: dev=X1 param=foo on vres (declares r) is refused before any engine runs
        # (bind_refused.scs, status 2 on both engines); e2e 11's VACASK bind failure (bind_fail.scs, E28) moved to
        # a module whose parameter list vamos cannot read (a *.vams header's macro, §3.1): unchecked, with the note
        with open(os.path.join(E2E11, "bind_refused.scs")) as fh:
            nl = parse_text(fh.read(), "bind_refused.scs", files=[os.path.join(E2E11, "vres.va")])
        for engine in ("vacask", "xyce"):
            with self.assertRaises(NoteError) as cm:
                PL.build(nl, engine, Settings())
            self.assertTrue(any("dc sw1: foo is not a parameter of Verilog-A module vres (declared: r)" in e
                                for e in errors(cm.exception)), engine)
        with open(os.path.join(E2E11, "bind_fail.scs")) as fh:
            nl = parse_text(fh.read(), "bind_fail.scs", files=[os.path.join(E2E11, f) for f in
                                                              ("vres_unknown.va", "vres_unknown.vams")])
        self.assertTrue(any("vres_unknown: its parameter list is unknown" in n.message for n in nl.notes))
        for engine in ("vacask", "xyce"):
            plan = PL.build(nl, engine, Settings())
            own = [a.step.own for a in plan.actions if a.step is not None and a.step.own is not None]
            self.assertEqual([lvl.target for lvl in own], [("instance", "X1", "foo")], engine)

    def test_a_parsed_group_instance_without_w_or_l_is_an_error(self):
        # D4: spectre.py's mos_defaults gives a Spectre model group's instance without w or l no master default
        # (before phase 2c the parser wrote 5e-06 m with a note), so plan.build refuses it
        nl = parse_text(GROUP_WITHOUT_WL)
        self.assertFalse(any("master's default" in n.message for n in nl.notes), [n.message for n in nl.notes])
        for engine in ("vacask", "xyce"):
            with self.assertRaises(NoteError) as cm:
                PL.build(nl, engine, Settings())
            hits = [e for e in errors(cm.exception) if "instance M1" in e and "model group nch has no w or l" in e
                    and "ref5 p.203" in e]
            self.assertEqual(len(hits), 1, errors(cm.exception))

    def test_build_and_resolve_leave_the_input_netlist_unchanged(self):
        # R-plan-17 (carried, not a problem; pinned): plan.build and signals.resolve restore every IR field they
        # touch, and a second resolve gives the same noise input and signal map
        nl = parse_text("""simulator lang=spectre
parameters rv=1k
V1 (in 0) vsource dc=1 mag=1
X1 (in out) sec r=rv
subckt sec (a b)
  parameters r=1k
  if (temp > 50) {
    R1 (a b) resistor r=r*2
  } else {
    R1 (a b) resistor r=r
  }
  R2 (b n1) resistor r=1k
  C1 (n1 0) capacitor c=1n
ends sec
sw dc param=rv values=[1k 2k]
hot alter param=temp value=27
nz (X1.n1 X1.b) noise start=1 stop=1k dec=1
""")
        before = repr(nl)
        for engine in ("vacask", "xyce"):
            plan = PL.build(nl, engine, Settings())
            self.assertEqual(repr(nl), before)
            a = SG.resolve(nl, plan)
            b = SG.resolve(nl, plan)
            self.assertEqual(repr(nl), before)
            self.assertEqual((a[1], [s.noise_input for s in (x.step for x in plan.actions) if s is not None]),
                             (b[1], [s.noise_input for s in (x.step for x in plan.actions) if s is not None]))


# -- phase 2c2: the four follow-up items (parsed decks, both engines' plans, no engine) ---------------------------

DIVIDER = """simulator lang=spectre
parameters rv=1k
V1 (in 0) vsource dc=1
R1 (in out) resistor r=rv
R2 (out 0) resistor r=1k
save out
"""
HALF = """simulator lang=spectre
subckt half (a b)
parameters rr=1k
R1 (a b) resistor r=rr
ends half
V1 (in 0) vsource dc=1
X1 (in out) half rr=1k
X2 (in out2) half rr=2k
R2 (out 0) resistor r=1k
R3 (out2 0) resistor r=1k
save out
"""
ISNOISY = """simulator lang=spectre
V1 (a 0) vsource dc=1
R1 (a b) resistor r=1k
R2 (b 0) resistor r=1k
save R1:1 b
op1 dc
"""
SHORT = """simulator lang=spectre
V1 (a 0) vsource dc=1
R1 (a b) resistor r=0
R2 (b 0) resistor r=1k
save R1:1 b
op1 dc
"""
CONTROLLED = """simulator lang=spectre
parameters g=2 gg=1m rr=1k fg=2 kk=0.5 ar=1 vv=1
V1 (in 0) vsource dc=vv
E1 (out 0 in 0) vcvs gain=g
R1 (out 0) resistor r=1k
Vc (c 0) vsource dc=1
G1 (0 o1 c 0) vccs gm=gg
Ro1 (o1 0) resistor r=1k
Vs (c s1) vsource dc=0
Rs1 (s1 0) resistor r=1k
H1 (o2 0) ccvs rm=rr probe=Vs
F1 (0 o4) cccs gain=fg probe=Vs
Ro4 (o4 0) resistor r=1k
Iac (0 p) isource dc=0 mag=1m
L1 (p 0) inductor l=1m
L2 (s 0) inductor l=9m
K1 mutual_inductor coupling=kk ind1=L1 ind2=L2
Rd (in a) resistor r=1k
D1 (a 0) dmod area=ar
model dmod diode is=1e-14
E2 (out2 0 in 0) vcvs gain=2
R2 (out2 0) resistor r=1k
save out o1 o2 o4 a s
"""


class TestFollowUp2c2(unittest.TestCase):
    """Phase 2c2: F1 a resistance a sweep or alter drives to 0 (§3.8's error per state), F2 R-plan-19 (an alter or
    a sweep of isnoisy, a short's parameters), F3 E102's bound-expression check for every value parameter."""

    def fails(self, text, engine):
        nl = parse_text(text)
        with self.assertRaises(NoteError) as cm:
            PL.build(nl, engine, Settings())
        return errors(cm.exception)

    def builds(self, text, engine):
        return PL.build(parse_text(text), engine, Settings())

    # -- F1 ------------------------------------------------------------------------------------------------------
    def test_a_resistance_swept_or_altered_to_zero_is_an_error_naming_the_instance_and_the_sweep(self):
        """Xyce gave out = 0 at rv = 0 (an open circuit, exit 0, no message) where Spectre's short gives 1 V, and
        VACASK failed the point with NaN (status 1): status 2 before any engine runs now, on both engines."""
        fixed = DIVIDER.replace("r=rv", "r=1k")                 # dev= targets of a constant r (E102 aside)
        cases = [
            (DIVIDER, "sw1 sweep param=rv stop=3k step=1k {\n  op1 dc\n}\n", "dc op1 at rv=0 (sweep sw1)",
             "r=rv evaluates to 0"),
            (DIVIDER, "dc1 dc param=rv stop=3k step=1k\n", "dc dc1 at rv=0", "r=rv evaluates to 0"),
            (fixed, "dc1 dc dev=R1 param=r start=0 stop=2k step=1k\n", "dc dc1 at R1:r=0", "is 0"),
            (fixed, "sw1 sweep dev=R1 param=r values=[1k 0] {\n  op1 dc\n}\n", "dc op1 at R1:r=0 (sweep sw1)", "is 0"),
            (DIVIDER, "op1 dc\na1 alter param=rv value=0\nop2 dc\n", "dc op2 (after alter a1)", "r=rv evaluates to 0"),
            (fixed, "op1 dc\na1 alter dev=R1 param=r value=0\nop2 dc\n", "dc op2 (after alter a1)", "is 0"),
            # the alter that made it 0 is named, not a later one that changed something else
            (DIVIDER, "op1 dc\na1 alter param=rv value=0\na2 alter param=temp value=50\nop2 dc\n",
             "dc op2 (after alter a1)", "r=rv evaluates to 0"),
            (DIVIDER.replace("rv=1k", "rv=1k rw=1k").replace("R2 (out 0) resistor r=1k", "R2 (out 0) resistor r=rw"),
             "a1 alter param=rv value=0\nsw1 sweep param=rw values=[1k 2k] {\n  op1 dc\n}\n",
             "dc op1 at rw=1000 (sweep sw1; after alter a1)", "r=rv evaluates to 0"),
        ]
        for head, tail, where, what in cases:
            for engine in ("vacask", "xyce"):
                with self.subTest(tail=tail, engine=engine):
                    errs = self.fails(head + tail, engine)
                    hits = [e for e in errs if e.startswith(where + ": resistor R1 on path top level: ") and what in e
                            and "a non-constant r that evaluates to 0 is an error in v1" in e]
                    self.assertEqual(len(hits), 1, errs)

    def test_a_non_constant_r_at_zero_where_an_analysis_runs(self):
        """§3.8 at the nominal state: parameters rv=0 with r=rv is the error (VACASK failed it with NaN); a 0 that no
        analysis runs at is not: a nominal the sweep leaves (it runs at 1k-3k only), an alter undone before the next
        analysis."""
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                errs = self.fails(DIVIDER.replace("rv=1k", "rv=0") + "op1 dc\n", engine)
                self.assertTrue(any(e.startswith("dc op1: resistor R1 on path top level: r=rv evaluates to 0") for e in errs),
                                errs)
                self.builds(DIVIDER.replace("rv=1k", "rv=0") + "sw1 sweep param=rv start=1k stop=3k step=1k {\n"
                            "  op1 dc\n}\n", engine)
                self.builds(DIVIDER + "op1 dc\na1 alter param=rv value=0\na2 alter param=rv value=2k\nop2 dc\n", engine)

    def test_what_stays_allowed(self):
        """A static r=0 stays a short; a sweep that never reaches 0 is unaffected; one error per resistor.  On Xyce
        an alter of r itself replaces the expression (the state replay prints R1 1000), so a later sweep of rv through
        0 leaves R1 at 1k (out = 0.5 at every point, measured); VACASK re-binds r=rv when rv changes (measured: the
        alter's 1k is lost, NaN at rv = 0), which this rule does not model (an open point of 2c2).  Phase 2c3 (G1)
        refuses that deck (altthen.scs) on both engines: which of the two readings is Spectre's is not known."""
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                self.builds(DIVIDER.replace("r=rv", "r=0") + "sw1 sweep param=rv stop=3k step=1k {\n  op1 dc\n}\n", engine)
                self.builds(DIVIDER + "sw1 sweep param=rv start=1k stop=3k step=1k {\n  op1 dc\n}\n", engine)
                self.builds(DIVIDER.replace("r=rv", "r=1k") + "dc1 dc dev=R1 param=r start=1k stop=3k step=1k\n", engine)
                errs = self.fails(DIVIDER + "dc1 dc param=rv values=[0 1k 0]\ndc2 dc param=rv values=[0 2k]\n", engine)
                self.assertEqual(len([e for e in errs if "resistor R1" in e]), 1, errs)
        # 2c2 built this on Xyce (the replay's 1k at every point); phase 2c3's G1 refuses it there too, and F1 does not
        # report R1, whose r the alter sets
        errs = self.fails(DIVIDER + "a1 alter dev=R1 param=r value=1k\ndc1 dc param=rv stop=3k step=1k\n", "xyce")
        self.assertTrue(any(e.startswith("dc dc1 at rv=0: alter a1 set r of R1, which the netlist binds to the expression "
                                         "rv") for e in errs), errs)
        self.assertFalse(any("resistor R1" in e for e in errs), errs)

    def test_per_path_through_a_subckt(self):
        """sub=X1 drives X1.R1 to 0 and leaves X2.R1 (rr=2k) alone: the error names path X1 only."""
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                errs = self.fails(HALF + "sw1 sweep sub=X1 param=rr start=0 stop=2k step=1k {\n  op1 dc\n}\n", engine)
                hits = [e for e in errs if "resistor R1 on path" in e]
                self.assertEqual(len(hits), 1, errs)
                self.assertTrue(hits[0].startswith("dc op1 at X1:rr=0 (sweep sw1): resistor R1 on path X1: r=rr "
                                                   "evaluates to 0"), hits)
                self.builds(HALF + "sw1 sweep sub=X1 param=rr start=1k stop=2k step=1k {\n  op1 dc\n}\n", engine)

    # -- F2 ------------------------------------------------------------------------------------------------------
    def test_an_alter_of_isnoisy_is_mapped_and_a_sweep_of_it_refused(self):
        """R-plan-19: VACASK reported `Parameter 'isnoisy' not found' at the alter and Xyce `the current of the
        noise-free G form of R1 needs a probe (signals.resolve)'; the alter now runs (the four words or a number),
        a sweep of the switch is an error in v1 on both engines, and so is a value that is no switch."""
        for engine in ("vacask", "xyce"):
            for value in ("0", "no", "yes", "1", "false"):
                with self.subTest(engine=engine, value=value):
                    plan = self.builds(ISNOISY + "a1 alter dev=R1 param=isnoisy value=%s\nop2 dc\n" % value, engine)
                    alter = [a for a in plan.actions if a.op == "alter"]
                    self.assertEqual([a.args["target"] for a in alter], [("instance", "R1", "isnoisy")])
                    self.assertEqual(PL.noise_switch_off(alter[0].args["value"]), value in ("0", "no", "false"))
            with self.subTest(engine=engine, construct="sweep"):
                errs = self.fails(ISNOISY + "sw1 sweep dev=R1 param=isnoisy values=[1 0] {\n  op2 dc\n}\n", engine)
                self.assertTrue(any("a sweep of the isnoisy of R1 is not supported in v1" in e for e in errs), errs)
            with self.subTest(engine=engine, construct="no switch"):
                errs = self.fails(ISNOISY + "a1 alter dev=R1 param=isnoisy value=maybe\nop2 dc\n", engine)
                self.assertTrue(any("isnoisy=maybe of R1 is not yes, no, true, false or a number" in e for e in errs), errs)

    def test_the_parameters_of_a_short(self):
        """R-plan-19: an r=0 resistor is a 0 V source on VACASK (`Parameter 'r' not found' at the alter) and R=0 on
        Xyce, which makes it a short when it reads the deck (a .DC R1:R from 1k left b at 1 V): a sweep of any of
        its parameters is an error on both engines, an alter on VACASK; Xyce's state replay runs the alter."""
        for param, value in (("r", "1k"), ("m", "2"), ("isnoisy", "no")):
            with self.subTest(param=param):
                deck = SHORT + "a1 alter dev=R1 param=%s value=%s\nop2 dc\n" % (param, value)
                errs = self.fails(deck, "vacask")
                self.assertTrue(any("alter a1: R1 prints as a short" in e and "an alter of its %s is not supported in v1 "
                                    "on VACASK (Xyce runs it)" % param in e for e in errs), errs)
                self.builds(deck, "xyce")
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine, construct="sweep"):
                errs = self.fails(SHORT + "dc1 dc dev=R1 param=r start=1k stop=3k step=1k\n", engine)
                self.assertTrue(any("dc dc1: R1 prints as a short" in e and "a sweep of its r is not supported in v1" in e
                                    for e in errs), errs)
            with self.subTest(engine=engine, construct="the static short"):
                self.builds(SHORT, engine)

    # -- F3 ------------------------------------------------------------------------------------------------------
    def test_vacask_refuses_a_sweep_of_any_value_parameter_bound_to_an_expression(self):
        """E102's rule knew r, c and l: `vcvs gain=g' swept with dev= failed at VACASK's run (exit 1, `bound to an
        expression'); every master's value parameter and a source field got the status 2 before the run on VACASK
        (2c2), while Xyce, which sweeps them (.DC through hooks), plans them as before.  Phase 2c4 (H1): the VACASK
        deck prints each one through a switchable hook and sweeps the hook's value (vamos_av<k>, its flag vamos_af<k>
        set), so all of them build on VACASK too and run with Xyce's values (test_spectre_emit.TestFollowUpRuns2c4)."""
        sweeps = [("dc1 dc dev=E1 param=gain values=[1 2 3]\n", "gain=(vamos_af1 ? vamos_av1 : g)"),
                  ("dc1 dc dev=G1 param=gm values=[1m 2m]\n", "gain=(vamos_af1 ? vamos_av1 : gg)"),
                  ("dc1 dc dev=H1 param=rm values=[1k 2k]\n", "gain=(vamos_af1 ? vamos_av1 : rr)"),
                  ("sw1 sweep dev=F1 param=gain values=[2 3] {\n  op1 dc\n}\n", "gain=(vamos_af1 ? vamos_av1 : fg)"),
                  ("ac1 ac dev=K1 param=coupling values=[0.25 0.5] freq=1k\n", "k=(vamos_af1 ? vamos_av1 : kk)"),
                  ("dc1 dc dev=D1 param=area values=[1 2]\n", "area=(vamos_af1 ? vamos_av1 : ar)"),
                  ("dc1 dc dev=V1 param=dc values=[1 2]\n", "dc=(vamos_af1 ? vamos_av1 : vv)")]
        for tail, hooked in sweeps:
            with self.subTest(tail=tail):
                text = vacask_deck(CONTROLLED + tail)
                self.assertIn(hooked, text)
                self.assertIn('sweep vamos_w1 variable="vamos_av1" ', text)
                self.builds(CONTROLLED + tail, "xyce")
        for engine in ("vacask", "xyce"):                       # a constant gain sweeps; an alter of a bound one runs
            with self.subTest(engine=engine):
                self.builds(CONTROLLED + "dc1 dc dev=E2 param=gain values=[1 2 3]\n", engine)
                self.builds(CONTROLLED + "op1 dc\na1 alter dev=E1 param=gain value=3\nop2 dc\n", engine)


# -- phase 2c3: G1 an altered parameter the netlist binds to an expression, G2 the cost of F1's check, G3 a constant
# expression equal to 0 (parsed decks, both engines' plans, no engine) --------------------------------------------

UNRELATED = DIVIDER.replace("rv=1k", "rv=1k other=1 rc=1k") + "R3 (in 0) resistor r=other*1k\n"
TEMP_R = DIVIDER.replace("r=rv", "r=rv*(1+0.001*(temp-27))")
CARD_DIODE = """simulator lang=spectre
parameters isv=1e-14 other=1
I1 (0 d) isource dc=1m
D1 (d 0) dmod
model dmod diode is=isv
R3 (d 0) resistor r=other*1G
save d
"""
ONE_HALF = """simulator lang=spectre
parameters rv=1k other=1
subckt half (a b)
parameters rr=1k q=1
R1 (a b) resistor r=rr
R9 (a 0) resistor r=q*1k
ends half
V1 (in 0) vsource dc=1
X1 (in out) half rr=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out
"""
MIXED = """simulator lang=spectre
parameters rv=1k rw=1k
V1 (in 0) vsource dc=1
R1 (in out) resistor r=%s
R2 (out 0) resistor r=rw
R3 (in 0) resistor r=rv
save out
"""
G1_CHANGED = ("whether Spectre keeps the altered value or re-evaluates the expression is not known (VACASK "
              "re-evaluates it, Xyce keeps the alter): not supported in v1")
# phase 2c5 (R4): for a target vamos's VACASK deck prints through a switchable hook (2c4's H1) that deck keeps the alter
G1_CHANGED_HOOKED = ("whether Spectre keeps the altered value or re-evaluates the expression is not known (VACASK and "
                     "Xyce keep the alter): not supported in v1")
G1_VACASK = ("VACASK re-evaluates such a binding at the next analysis after a change of a netlist parameter, temp or tnom",
             "so the alter's value is lost (Xyce keeps it, as Spectre does while nothing the expression reads changes): "
             "not supported on VACASK in v1 (Xyce runs it)")


def chain(n):
    """n resistors r=rv*k in a ladder, then R1 r=1k (2c2 skeptic's perf decks, smaller)."""
    lines = ["simulator lang=spectre", "parameters rv=1k", "V1 (n0 0) vsource dc=1"]
    lines += ["R%d (n%d n%d) resistor r=rv*%d" % (k + 1, k - 1, k, k) for k in range(1, n + 1)]
    lines += ["R1 (n%d 0) resistor r=1k" % n, "save n%d" % n]
    return "\n".join(lines) + "\n"


class _Counting:
    """plan.build's walks (tables.path_envs with strict=True, one per parameter state _check_paths evaluates) and
    plan._ev's evaluations of the watched expressions (by identity), while the context is active."""

    def __init__(self, watch=()):
        self.watch = {id(e) for e in watch}
        self.walks = self.evals = 0
        self._path_envs, self._ev = T.path_envs, PL._ev

    def __enter__(self):
        def path_envs(*a, **kw):
            if kw.get("strict"):
                self.walks += 1
            return self._path_envs(*a, **kw)

        def ev(e, *a, **kw):
            if id(e) in self.watch:
                self.evals += 1
            return self._ev(e, *a, **kw)
        T.path_envs, PL._ev = path_envs, ev
        return self

    def __exit__(self, *exc):
        T.path_envs, PL._ev = self._path_envs, self._ev


class TestFollowUp2c3(unittest.TestCase):
    """Phase 2c3: G1 an alter of a parameter the netlist binds to an expression, then a change of what the expression
    reads (VACASK re-evaluated the binding, Xyce's replay kept the alter: different results, exit 0, no message); G2
    F1's per-state zero check walking every resistor at every point of a dev= sweep of a resistor's r (0.0045 s ->
    4.07 s for 401 resistors and 1001 points); G3 a constant expression equal to 0 (r=1k-1k) refused by F1 where
    Spectre and Xyce make it a short."""

    def fails(self, text, engine):
        nl = parse_text(text)
        with self.assertRaises(NoteError) as cm:
            PL.build(nl, engine, Settings())
        return errors(cm.exception)

    def builds(self, text, engine):
        return PL.build(parse_text(text), engine, Settings())

    @staticmethod
    def hits(errs, where, *pieces):
        return [e for e in errs if e.startswith(where + ": ") and all(p in e for p in pieces)]

    # -- G1 ------------------------------------------------------------------------------------------------------
    def test_g1_a_change_of_what_an_altered_binding_reads_is_an_error_on_both_engines(self):
        """ref19 p.54 and UG pp.190-191 do not say whether an alter replaces the binding or a later change of its
        inputs re-evaluates it: altthen2 gave out 0.333, 0.25 on VACASK and 0.5, 0.5 on Xyce, p_altsw [1, 2, 3] and
        [3, 3, 3], altthen NaN on VACASK; status 2 before any engine runs now, naming the alter, the instance or card
        and its parameter, the expression and the change, at the first point where the readings differ.  Phase 2c5
        (R4): every target here is one vamos's VACASK deck prints through a switchable hook (2c4), which keeps the
        alter, so the message's parenthesis says both engines keep it (G1_CHANGED_HOOKED; G1_CHANGED's
        `VACASK re-evaluates it' stays for a target without a hook, TestFollowUp2c5)."""
        cases = [
            (DIVIDER + "a1 alter dev=R1 param=r value=1k\ndc1 dc param=rv stop=3k step=1k\n",              # altthen
             "dc dc1 at rv=0", "alter a1 set r of R1", "expression rv", "dc dc1 (its sweep of rv)"),
            (DIVIDER + "a1 alter dev=R1 param=r value=1k\ndc1 dc param=rv start=2k stop=3k step=1k\n",     # altthen2
             "dc dc1 at rv=2000", "alter a1 set r of R1", "expression rv", "dc dc1 (its sweep of rv)"),
            (CONTROLLED + "op1 dc\na1 alter dev=E1 param=gain value=3\nop2 dc\ndc2 dc param=g values=[1 2 3]\n",  # p_altsw
             "dc dc2 at g=1", "alter a1 set gain of E1", "expression g", "dc dc2 (its sweep of g)"),
            (DIVIDER + "op0 dc\na1 alter dev=R1 param=r value=3k\na2 alter param=rv value=2k\nop1 dc\n",
             "dc op1", "alter a1 set r of R1", "expression rv", "alter a2"),
            # changed and changed back before the analysis: a re-evaluation would give rv's 1k there, the alter 3k
            (DIVIDER + "op0 dc\na1 alter dev=R1 param=r value=3k\na2 alter param=rv value=2k\na3 alter param=rv "
             "value=1k\nop1 dc\n", "dc op1", "alter a1 set r of R1", "alter a2"),
            (DIVIDER + "a1 alter dev=R1 param=r value=1k\nsw1 sweep param=rv values=[2k 3k] {\n  op1 dc\n}\n",
             "dc op1 at rv=2000", "alter a1 set r of R1", "sweep sw1"),
            (CARD_DIODE + "op1 dc\na1 alter mod=dmod param=is value=1e-13\ndc1 dc param=isv values=[1e-14 1e-12]\n",
             "dc dc1 at isv=1e-12", "alter a1 set is of model dmod", "expression isv", "dc dc1 (its sweep of isv)"),
            (TEMP_R + "a1 alter dev=R1 param=r value=2k\na2 alter param=temp value=50\nop1 dc\n",
             "dc op1", "alter a1 set r of R1", "alter a2", "(it reads rv, temp)"),
            (TEMP_R + "a1 alter dev=R1 param=r value=2k\ns1 set temp=50\nop1 dc\n", "dc op1", "alter a1", "set s1"),
            (ONE_HALF + "a1 alter dev=X1.R1 param=r value=3k\nsw1 sweep sub=X1 param=rr values=[1k 2k] {\n  op1 dc\n}\n",
             "dc op1 at X1:rr=2000", "alter a1 set r of X1.R1", "expression rr", "sweep sw1"),
            (ONE_HALF.replace("half rr=1k", "half rr=rv") + "a1 alter sub=X1 param=rr value=3k\ndc1 dc param=rv "
             "values=[1k 2k]\n", "dc dc1 at rv=2000", "alter a1 set rr of X1", "expression rv", "dc dc1 (its sweep of rv)"),
        ]
        for deck, where, *pieces in cases:
            for engine in ("vacask", "xyce"):
                with self.subTest(deck=deck.splitlines()[-1], engine=engine):
                    errs = self.fails(deck, engine)
                    self.assertEqual(len(self.hits(errs, where, G1_CHANGED_HOOKED, *pieces)), 1, errs)

    def test_g1_what_stays_allowed_on_both_engines(self):
        """An alter whose binding's inputs never change afterwards keeps the matching results of today (m01, m06,
        m07, m15 of X-follow2's measurements); a constant or a constant expression is no binding; an alter supersedes
        an earlier one; where the expression's value equals the alter's the two readings agree."""
        tails = ["a1 alter dev=R1 param=r value=3k\nop1 dc\n",
                 "op0 dc\na1 alter dev=R1 param=r value=3k\ndc1 dc dev=R2 param=r values=[1k 3k]\nop2 dc\n",
                 "op0 dc\na1 alter dev=R1 param=r value=3k\na2 alter param=rv value=1k\nop1 dc\n",
                 "dc1 dc param=rv values=[1k 2k]\na1 alter dev=R1 param=r value=3k\nop2 dc\n",
                 "a1 alter dev=R1 param=r value=2k\ndc1 dc param=rv values=[2k]\n",
                 "a1 alter param=rv value=2k\ndc1 dc param=rv values=[1k 3k]\n",
                 "op0 dc\na1 alter dev=R1 param=r value=3k\na2 alter dev=R1 param=r value=4k\nop1 dc\n",
                 "op0 dc\na1 alter dev=R1 param=r value=3k\ndc1 dc dev=V1 param=dc values=[1 2]\ntr1 tran stop=1u\n"]
        for engine in ("vacask", "xyce"):
            for tail in tails:
                with self.subTest(tail=tail, engine=engine):
                    self.builds(DIVIDER + tail, engine)
            with self.subTest(engine=engine, construct="constants"):
                for r in ("2k", "2*500"):
                    self.builds(DIVIDER.replace("r=rv", "r=" + r) + "a1 alter dev=R1 param=r value=3k\n"
                                "dc1 dc param=rv values=[1k 2k]\n", engine)
                self.builds(CONTROLLED + "op1 dc\na1 alter dev=E2 param=gain value=3\ndc2 dc param=g values=[1 2 3]\n",
                            engine)
        # Xyce: an alter after the change sets r again; on VACASK the pending change re-evaluates it at op1 (below)
        self.builds(DIVIDER + "a1 alter dev=R1 param=r value=3k\na2 alter param=rv value=2k\na3 alter dev=R1 param=r "
                    "value=3k\nop1 dc\n", "xyce")

    def test_g1_vacask_re_evaluates_a_binding_whenever_a_parameter_temp_or_tnom_changes(self):
        """Measured (X-follow2 m02-m16): VACASK re-propagates every parameter its netlist binds to an expression at the
        next elaboration after any change of a variable, temp or tnom (and, inside a subckt instance, of that
        instance's parameters), so the alter is lost although nothing the expression reads changed: out 0.5 where
        Xyce, and Spectre under either reading, keep the altered 3k (0.25).  Status 2 on VACASK in 2c3; Xyce runs them.
        Phase 2c4 (H1): the VACASK deck prints the binding through a switchable hook, which the re-propagation keeps,
        and the alter as a var statement: every one builds on VACASK now (the values, Xyce's, are
        test_spectre_emit.TestFollowUpRuns2c4's), and (where, changer) name the change VACASK re-elaborates on."""
        cases = [(UNRELATED + "op0 dc\na1 alter dev=R1 param=r value=3k\na2 alter param=other value=2\nop1 dc\n",
                  "dc op1", "alter a2"),
                 (UNRELATED + "op0 dc\na1 alter dev=R1 param=r value=3k\ndc1 dc param=other values=[1 2]\nop2 dc\n",
                  "dc dc1 at other=2", "dc dc1 (its sweep of other)"),
                 (UNRELATED + "op0 dc\na1 alter dev=R1 param=r value=3k\nsw1 sweep param=other values=[1 2] {\n"
                  "  opx dc\n}\n", "dc opx at other=2", "sweep sw1"),
                 (UNRELATED + "op0 dc\na1 alter dev=R1 param=r value=3k\na2 alter param=temp value=50\nop1 dc\n",
                  "dc op1", "alter a2"),
                 (UNRELATED + "op0 dc\na1 alter dev=R1 param=r value=3k\ndc1 dc param=temp values=[27 50]\nop2 dc\n",
                  "dc dc1 at temp=50", "dc dc1 (its sweep of temp)"),
                 (UNRELATED + "op0 dc\na1 alter dev=R1 param=r value=3k\ns1 set temp=50\nop1 dc\n", "dc op1", "set s1"),
                 (UNRELATED.replace("r=rv", "r=rc") + "op0 dc\na1 alter dev=R1 param=r value=3k\na2 alter param=other "
                  "value=2\nop1 dc\n", "dc op1", "alter a2"),
                 (CARD_DIODE + "op0 dc\na1 alter mod=dmod param=is value=1e-13\na2 alter param=other value=2\nop1 dc\n",
                  "dc op1", "alter a2"),
                 (ONE_HALF + "op0 dc\na1 alter dev=X1.R1 param=r value=3k\ndc1 dc sub=X1 param=q values=[1 2]\nop2 dc\n",
                  "dc dc1 at X1:q=2", "dc dc1 (its sweep of X1:q)"),
                 (ONE_HALF + "op0 dc\na1 alter dev=X1.R1 param=r value=3k\na2 alter param=other value=2\nop1 dc\n",
                  "dc op1", "alter a2"),
                 # the holder's m (its $mfactor) changes: VACASK propagates X1 again (q_holder_m_alter: op1 0.667
                 # where Xyce gives 0.4)
                 (ONE_HALF + "op0 dc\na1 alter dev=X1.R1 param=r value=3k\na2 alter dev=X1 param=m value=2\nop1 dc\n",
                  "dc op1", "alter a2 changes X1:m"),
                 (ONE_HALF + "op0 dc\na1 alter dev=X1.R1 param=r value=3k\ndc1 dc dev=X1 param=m values=[1 2]\n"
                  "op2 dc\n", "dc dc1 at X1:m=2", "dc dc1 (its sweep of X1:m) changes X1:m"),
                 # a change before the alter that no analysis has applied yet: VACASK applies it at op1
                 (UNRELATED + "op0 dc\na0 alter param=other value=2\na1 alter dev=R1 param=r value=3k\nop1 dc\n",
                  "dc op1", "alter a0"),
                 (DIVIDER + "op0 dc\na0 alter param=rv value=2k\na1 alter dev=R1 param=r value=3k\nop1 dc\n",
                  "dc op1", "alter a0"),
                 (DIVIDER + "a1 alter dev=R1 param=r value=3k\na2 alter param=rv value=2k\na3 alter dev=R1 param=r "
                  "value=3k\nop1 dc\n", "dc op1", "alter a2")]
        for deck, where, changer in cases:
            with self.subTest(deck=deck.splitlines()[-2:], where=where, changer=changer):
                plan = self.builds(deck, "vacask")
                self.assertFalse([n for n in plan.notes if "re-evaluates such a binding" in n.message], plan.notes)
                text = vacask_deck(deck)
                self.assertIn("  var vamos_af1=1 vamos_av1=", text)
                self.assertIn("? vamos_av1 : " if "X1.R1" not in deck else "(vamos_hf1 ? vamos_hv1 : rr)", text)
                self.builds(deck, "xyce")
        # nothing VACASK re-elaborates on changes: no error (an instance sweep, a sweep before the alter, an alter of a
        # variable before the first elaboration)
        for tail in ("op0 dc\na1 alter dev=R1 param=r value=3k\ndc1 dc dev=R2 param=r values=[1k 3k]\nop2 dc\n",
                     "dc1 dc param=other values=[1 2]\na1 alter dev=R1 param=r value=3k\nop2 dc\n",
                     "a0 alter param=other value=2\na1 alter dev=R1 param=r value=3k\nop1 dc\n"):
            with self.subTest(tail=tail):
                self.builds(UNRELATED + tail, "vacask")

    # -- G2 ------------------------------------------------------------------------------------------------------
    def walks(self, text, engine):
        with _Counting() as c:
            PL.build(parse_text(text), engine, Settings())
        return c.walks

    def test_g2_a_dev_sweep_or_an_alter_of_a_resistance_walks_no_more_states(self):
        """Since 2c2 (F1) every point of a dev= sweep of a resistor's r walked every instance path (4.07 s against
        0.0045 s for 401 resistors and 1001 points): the swept value changes that resistor alone, so the per-point check
        looks at it, and the walks are those of the deck without the sweep or the alters."""
        deck = chain(40)
        for engine in ("vacask", "xyce"):
            op = self.walks(deck + "op1 dc\n", engine)
            for tail in ("dc1 dc dev=R1 param=r start=1k stop=101k lin=200\n",
                         "sw1 sweep dev=R1 param=r values=[1k 2k 3k] {\n  op1 dc\n}\n",
                         "ac1 ac dev=R1 param=r values=[1k 2k] freq=1k\n",
                         "op1 dc\na1 alter dev=R1 param=r value=2k\nop2 dc\na2 alter dev=R1 param=r value=3k\nop3 dc\n"):
                with self.subTest(engine=engine, tail=tail):
                    self.assertEqual(self.walks(deck + tail, engine), op)

    def test_g2_each_resistance_is_evaluated_once_per_walked_state(self):
        """The zero check reads the value the walk computed for every instance expression (§3.5) instead of evaluating
        r a second time (2.21 s -> 4.06 s for a 1001-point param= sweep of 400 resistors in 2c2)."""
        for engine in ("vacask", "xyce"):
            nl = parse_text(chain(10) + "dc1 dc param=rv start=1k stop=2k lin=10\n")
            r6 = next(it for it in nl.body if isinstance(it, Instance) and it.name == "R6")
            with _Counting([r6.value]) as c:
                PL.build(nl, engine, Settings())
            self.assertGreaterEqual(c.walks, 11, engine)
            self.assertEqual(c.evals, c.walks, engine)

    def test_g2_the_zero_check_names_the_same_points(self):
        """The F1 errors of decks that mix a dev= level of r with walked levels and alters, as 2c2 reported them (the
        F1 tests above pin the other cases)."""
        tail = "is 0, and vamos prints a short only for the constant r=0"
        cases = [
            (MIXED % "1k" + "sw1 sweep param=rw values=[1k 2k] {\n  dc1 dc dev=R1 param=r values=[1k 0]\n}\n",
             {"vacask": ["dc dc1 at rw=1000, R1:r=0 (sweep sw1): resistor R1 on path top level: its r, set by a sweep "
                         "or alter, " + tail]}),
            (MIXED % "1k" + "sw1 sweep dev=R1 param=r values=[1k 0] {\n  dc1 dc param=rw values=[1k 2k]\n}\n",
             {"vacask": ["dc dc1 at R1:r=0, rw=1000 (sweep sw1): resistor R1 on path top level: its r, set by a sweep "
                         "or alter, " + tail]}),
            (MIXED % "1k" + "sw1 sweep dev=R1 param=r values=[1k 2k] {\n  dc1 dc param=rw values=[1k 0]\n}\n",
             {"vacask": ["dc dc1 at R1:r=1000, rw=0 (sweep sw1): resistor R2 on path top level: r=rw evaluates to 0"]}),
            (MIXED % "1k" + "sw1 sweep dev=R1 param=r values=[1k 0] {\n  dc1 dc param=rv values=[0 1k]\n}\n",
             {"vacask": ["dc dc1 at R1:r=1000, rv=0 (sweep sw1): resistor R3 on path top level: r=rv evaluates to 0",
                         "dc dc1 at R1:r=0, rv=0 (sweep sw1): resistor R1 on path top level: its r, set by a sweep or "
                         "alter, " + tail]}),
            (MIXED.replace("rv=1k", "rv=0") % "rv" + "dc1 dc dev=R1 param=r values=[1k 2k]\n",
             {"xyce": ["dc dc1 at R1:r=1000: resistor R3 on path top level: r=rv evaluates to 0"]}),
            (MIXED % "1k" + "a1 alter dev=R1 param=r value=0\ndc1 dc dev=R1 param=r values=[1k 2k]\nop2 dc\n",
             {"vacask": ["dc op2 (after alter a1): resistor R1 on path top level: its r, set by a sweep or alter, "
                         + tail]}),
            (MIXED % "1k" + "op1 dc\na1 alter dev=R1 param=r value=0\na2 alter dev=R1 param=r value=1k\nop2 dc\n",
             {"vacask": []}),
            (HALF + "sw1 sweep sub=X2 param=rr values=[1k 0] {\n  dc1 dc dev=X1.R1 param=r values=[1k 0]\n}\n",
             {"xyce": ["dc dc1 at X2:rr=1000, X1.R1:r=0 (sweep sw1): resistor R1 on path X1: its r, set by a sweep or "
                       "alter, " + tail,
                       "dc dc1 at X2:rr=0, X1.R1:r=1000 (sweep sw1): resistor R1 on path X2: r=rr evaluates to 0"]}),
            (MIXED % "1k" + "sw1 sweep dev=R1 param=r values=[0 1k] {\n  dc1 dc dev=R1 param=r values=[2k 3k]\n}\n",
             {"vacask": [], "xyce": []}),
        ]
        for deck, want in cases:
            for engine, expected in want.items():
                with self.subTest(deck=deck.splitlines()[-1], engine=engine):
                    try:
                        PL.build(parse_text(deck), engine, Settings())
                        got = []
                    except NoteError as exc:
                        got = [e for e in errors(exc) if "resistor R" in e]
                    self.assertEqual(len(got), len(expected), got)
                    for g, e in zip(got, expected):
                        self.assertTrue(g.startswith(e), (g, e))

    # -- G3 ------------------------------------------------------------------------------------------------------
    def test_g3_a_constant_expression_equal_to_0_is_the_short(self):
        """r=1k-1k, r=(0), r=2*0 ...: F1 refused them on both engines (status 2), where Spectre makes them a short and
        Xyce had printed the short; now they are the static short of §4.5 item 10: plan.is_short holds, the render copy
        carries the constant 0 (signals.resolve), and both emitters print exactly the r=0 deck's netlist."""
        from vamos.netlist import vacask
        from vamos.spectre import run_xyce

        def rendered(text, engine):
            nl = parse_text(text)
            plan = PL.build(nl, engine, Settings())
            nl2, sm, _notes = SG.resolve(nl, plan)
            if engine == "vacask":
                return nl, nl2, vacask.render(nl2, plan=plan, sigmap=sm)
            return nl, nl2, "\n".join(d.text for d in run_xyce.render_decks(nl2, plan, sm)[0])
        deck = SHORT + "dc1 dc dev=V1 param=dc values=[1 2]\n"
        for engine in ("vacask", "xyce"):
            _, _, ref = rendered(deck, engine)
            for r in ("1k-1k", "(0)", "2*0", "(1k-1k)*2", "-(1k-1k)", "0*1k", "abs(0)", "min(0, 1)"):
                with self.subTest(engine=engine, r=r):
                    nl, nl2, text = rendered(deck.replace("r=0\n", "r=%s\n" % r), engine)
                    r1 = next(it for it in nl.body if isinstance(it, Instance) and it.name == "R1")
                    self.assertTrue(PL.is_short(r1))
                    self.assertEqual(next(it for it in nl2.body if isinstance(it, Instance) and it.name == "R1").value,
                                     Num(0.0))
                    if r != "(0)":                              # the parser folds (0); the input IR is not changed
                        self.assertNotEqual(r1.value, Num(0.0))
                    self.assertEqual(text, ref)
        # an expression that reads a name, or temp, keeps F1's rule; so does the short's parameters' rule
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                errs = self.fails(DIVIDER.replace("r=rv", "r=0*rv") + "op1 dc\n", engine)
                self.assertTrue(any(e.startswith("dc op1: resistor R1 on path top level: r=0.0 * rv evaluates to 0")
                                    for e in errs), errs)
                errs = self.fails(DIVIDER.replace("r=rv", "r=(temp-27)*1k") + "op1 dc\n", engine)
                self.assertTrue(any("resistor R1 on path top level: r=" in e and "evaluates to 0" in e for e in errs), errs)
                errs = self.fails(SHORT.replace("r=0\n", "r=1k-1k\n") + "dc1 dc dev=R1 param=r start=1k stop=3k "
                                  "step=1k\n", engine)
                self.assertTrue(any("dc dc1: R1 prints as a short" in e for e in errs), errs)
        errs = self.fails(SHORT.replace("r=0\n", "r=2*0\n") + "a1 alter dev=R1 param=r value=1k\nop2 dc\n", "vacask")
        self.assertTrue(any("alter a1: R1 prints as a short" in e for e in errs), errs)
        self.builds(SHORT.replace("r=0\n", "r=2*0\n") + "a1 alter dev=R1 param=r value=1k\nop2 dc\n", "xyce")


# -- phase 2c4: H1 VACASK's switchable hooks, H2 rule (a) for a netlist parameter and a subckt default, H3 a subckt
# instance's m swept on Xyce, H4 the multiplier message (parsed decks, both engines' plans and renders, no engine) --

def vacask_deck(text, files=()):
    """The VACASK deck vamos renders for a Spectre deck (plan.build, signals.resolve, vacask.render; no engine)."""
    from vamos.netlist import vacask
    nl = parse_text(text, files=files)
    plan = PL.build(nl, "vacask", Settings())
    nl2, sm, _notes = SG.resolve(nl, plan)
    return vacask.render(nl2, plan=plan, sigmap=sm)


def xyce_decks(text):
    """The per-analysis Xyce decks vamos renders for a Spectre deck (run_xyce.render_decks; no engine)."""
    from vamos.spectre import run_xyce
    nl = parse_text(text)
    plan = PL.build(nl, "xyce", Settings())
    nl2, sm, _notes = SG.resolve(nl, plan)
    return [d.text for d in run_xyce.render_decks(nl2, plan, sm)[0]]


SRC_DC = DIVIDER.replace("rv=1k", "rv=1k vv=1 mm=1 other=1").replace("V1 (in 0) vsource dc=1", "V1 (in 0) vsource dc=vv") \
    .replace("R1 (in out) resistor r=rv", "R1 (in out) resistor r=1k m=mm") + "R3 (in 0) resistor r=other*1k\n"
XLINE = """simulator lang=spectre
parameters rv=1k other=1
subckt half (a b)
parameters rr=1k
R1 (a b) resistor r=rr
ends half
V1 (in 0) vsource dc=1
X1 (in out) half rr=rv
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out
"""
NESTED = """simulator lang=spectre
parameters other=1
subckt inner (a b)
parameters rr=1k
R1 (a b) resistor r=rr
ends inner
subckt outer (a b)
parameters p=1
X1 (a b) inner rr=p*1k
ends outer
V1 (in 0) vsource dc=1
X0 (in out) outer p=1
X0b (in outb) outer p=2
R2 (out 0) resistor r=1k
R4 (outb 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out outb
"""
PULSE_RISE = """simulator lang=spectre
parameters rt=1n other=1
V1 (in 0) vsource type=pulse val0=0 val1=1 delay=1u rise=rt fall=1n width=1u period=4u
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out
"""
EXPR_PARAM = """simulator lang=spectre
parameters rv=1k other=1 rw=2*rv rk=2k
V1 (in 0) vsource dc=1
R1 (in out) resistor r=%s
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out
"""
SUB_DEFAULT = """simulator lang=spectre
parameters qv=1 other=1
subckt half (a b)
parameters q=1 rr=%s
R1 (a b) resistor r=rr
R9 (a 0) resistor r=q*1k
ends half
V1 (in 0) vsource dc=1
X1 (in out) half%s
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out
"""
HOLDER_M = """simulator lang=spectre
subckt half (a b)
parameters rr=1k
R1 (a b) resistor r=rr
ends half
V1 (in 0) vsource dc=1
X1 (in out) half rr=1k
R2 (out 0) resistor r=1k
save out
"""
G2C4_BOTH_KEEP = ("whether Spectre keeps the altered value or re-evaluates the expression is not known (VACASK and Xyce "
                  "keep the alter): not supported in v1 (§5.3; alter what the expression reads instead)")
COND_DUP = """simulator lang=spectre
parameters other=1
subckt half (a b)
parameters rr=1k sel=0
if (sel > 0) {
R1 (a b) resistor r=1k
} else {
R1 (a b) resistor r=rr
}
ends half
V1 (in 0) vsource dc=1
X1 (in out) half rr=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out
"""
LIFTED = """simulator lang=spectre
parameters other=1
subckt outer (a b)
parameters p=1
subckt inner (c d)
R1 (c d) resistor r=p*1k
ends inner
X1 (a b) inner
ends outer
V1 (in 0) vsource dc=1
X0 (in out) outer p=1
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out
"""
VA_HOOK = """simulator lang=spectre
ahdl_include "vres2c.va"
parameters rv=1k rm=1k other=1
V1 (a 0) vsource dc=1
X1 (a b) vres2c r=rv
R2 (b 0) resistor r=1k
model vm vres2c r=rm
X2 (a c) vm
R3 (c 0) resistor r=1k
R4 (a 0) resistor r=other*1k
save b c
"""
VRES2C_VA = """`include "disciplines.vams"
module vres2c(a, b);
  inout a, b; electrical a, b;
  parameter real r = 2000.0;
  analog I(a, b) <+ V(a, b) / r;
endmodule
"""


class TestFollowUp2c4(unittest.TestCase):
    """Phase 2c4: H1 VACASK re-evaluates every parameter its netlist binds to an expression at its elaborations, so 2c3
    refused an alter of one there (rule (b)) and E151 lost one before the first analysis; the deck now prints such a
    parameter as (vamos_af<k> ? vamos_av<k> : expr) and the alter as var vamos_af<k>=1 vamos_av<k>=value, a dev=, sub=
    or mod= sweep of it as a sweep of vamos_av<k>.  H2 the netlist-parameter and subckt-default forms of G1's rule (a).
    H3 Xyce's .DC of a subckt instance's m (its hook's nominal was 0).  H4 the multiplier message's master names."""

    def fails(self, text, engine):
        nl = parse_text(text)
        with self.assertRaises(NoteError) as cm:
            PL.build(nl, engine, Settings())
        return errors(cm.exception)

    def builds(self, text, engine):
        return PL.build(parse_text(text), engine, Settings())

    def has(self, text, *pieces, absent=()):
        for p in pieces:
            self.assertIn(p, text)
        for p in absent:
            self.assertNotIn(p, text)

    # -- H1 ------------------------------------------------------------------------------------------------------
    def test_h1_vacask_prints_an_alter_of_a_bound_parameter_through_a_switchable_hook(self):
        """2c3's rule-(b) decks (TestFollowUp2c3's VACASK cases, TestAlters' deck) and the E151 decks (2c3 skeptic's
        v01-v10, u01-u04): VACASK gave 0.5, 0.333, 2, d 0.6130 where Xyce gives 0.25, 0.2, 3, 0.5488, so plan.build
        refused the first set on VACASK and accepted the second; both now build on VACASK, which prints the bound
        parameter through its hook and the alter as var statements (Xyce's plans are unchanged)."""
        r1 = "R1 (in out) vamos_resistor r=(vamos_af1 ? vamos_av1 : rv)"
        alt = "  var vamos_af1=1 vamos_av1=3000.0"
        decl = "  var vamos_af1=0 vamos_av1=0"
        top = (r1, alt, decl)
        body = ("  parameters vamos_hf1=0", "  parameters vamos_hv1=0",
                "  R1 (a b) vamos_resistor r=(vamos_hf1 ? vamos_hv1 : rr) $mfactor=$mfactor",
                "X1 (in out) half rr=1000.0 vamos_hf1=vamos_af1 vamos_hv1=vamos_av1", alt, decl)
        a1 = "op0 dc\na1 alter dev=R1 param=r value=3k\n"
        h1 = "op0 dc\na1 alter dev=X1.R1 param=r value=3k\n"
        cases = [
            (UNRELATED + a1 + "a2 alter param=other value=2\nop1 dc\n", top),
            (UNRELATED + a1 + "dc1 dc param=other values=[1 2]\nop2 dc\n", top),
            (UNRELATED + a1 + "sw1 sweep param=other values=[1 2] {\n  opx dc\n}\n", top),
            (UNRELATED + a1 + "a2 alter param=temp value=50\nop1 dc\n", top),
            (UNRELATED + a1 + "dc1 dc param=temp values=[27 50]\nop2 dc\n", top),
            (UNRELATED + a1 + "s1 set temp=50\nop1 dc\n", top),
            (UNRELATED.replace("r=rv", "r=rc") + a1 + "a2 alter param=other value=2\nop1 dc\n",
             ("R1 (in out) vamos_resistor r=(vamos_af1 ? vamos_av1 : rc)", alt)),
            (CARD_DIODE + "op0 dc\na1 alter mod=dmod param=is value=1e-13\na2 alter param=other value=2\nop1 dc\n",
             ("is=(vamos_af1 ? vamos_av1 : isv)", "  var vamos_af1=1 vamos_av1=1e-13", decl)),
            (ONE_HALF + h1 + "dc1 dc sub=X1 param=q values=[1 2]\nop2 dc\n", body),
            (ONE_HALF + h1 + "a2 alter param=other value=2\nop1 dc\n", body),
            (ONE_HALF + h1 + "a2 alter dev=X1 param=m value=2\nop1 dc\n", body),
            (ONE_HALF + h1 + "dc1 dc dev=X1 param=m values=[1 2]\nop2 dc\n", body),
            (UNRELATED + "op0 dc\na0 alter param=other value=2\na1 alter dev=R1 param=r value=3k\nop1 dc\n", top),
            (DIVIDER + "op0 dc\na0 alter param=rv value=2k\na1 alter dev=R1 param=r value=3k\nop1 dc\n", top),
            (DIVIDER + "a1 alter dev=R1 param=r value=3k\na2 alter param=rv value=2k\na3 alter dev=R1 param=r "
             "value=3k\nop1 dc\n", top),
            # TestAlters' deck: a3 the X-line binding r=rval, a4 the body's R1 r=r, its hook passed down X1's line
            (DIVIDER.replace("R1 (in out) resistor r=rv", "X1 (in out) rcsec r=rval").replace("rv=1k", "rval=1k")
             + "subckt rcsec (a b)\nparameters r=1k\nR1 (a b) resistor r=r\nC1 (b 0) capacitor c=1u\nends rcsec\n"
             "op1 dc\na1 alter param=rval value=2000\na2 alter param=temp value=50\na3 alter sub=X1 param=r value=3000\n"
             "a4 alter dev=X1.R1 param=r value=4000\nop2 dc\n",
             ("X1 (in out) rcsec r=(vamos_af1 ? vamos_av1 : rval) vamos_hf1=vamos_af2 vamos_hv1=vamos_av2",
              "  R1 (a b) vamos_resistor r=(vamos_hf1 ? vamos_hv1 : r) $mfactor=$mfactor",
              "  var vamos_af1=1 vamos_av1=3000.0", "  var vamos_af2=1 vamos_av2=4000.0",
              "  var vamos_af1=0 vamos_av1=0 vamos_af2=0 vamos_av2=0")),
            # E151: an alter before the first analysis with temp or tnom other than 27 at VACASK's first elaboration
            (UNRELATED + "o1 options temp=50\na1 alter dev=R1 param=r value=3k\nop1 dc\n", top),
            (UNRELATED + "o1 options tnom=25\na1 alter dev=R1 param=r value=3k\nop1 dc\n", top),
            (CONTROLLED + "o1 options temp=50\na1 alter dev=E1 param=gain value=3\nop1 dc\n",
             ("E1 (out 0 in 0) vamos_vcvs gain=(vamos_af1 ? vamos_av1 : g)", "  var vamos_af1=1 vamos_av1=3.0")),
            (CARD_DIODE + "o1 options temp=50\na1 alter mod=dmod param=is value=1e-13\nop1 dc\n",
             ("is=(vamos_af1 ? vamos_av1 : isv)", "  var vamos_af1=1 vamos_av1=1e-13")),
            (UNRELATED + "o1 options temp=50\na1 alter dev=R1 param=r value=3k\ntr1 tran stop=1u\n", top),
            (UNRELATED + "a0 alter param=temp value=50\na1 alter dev=R1 param=r value=3k\nop1 dc\nop2 dc\n", top),
            (UNRELATED + "a0 alter param=other value=2\na9 alter param=temp value=50\na1 alter dev=R1 param=r "
             "value=3k\nop1 dc\n", top),
            (UNRELATED + "s0 set temp=50\na1 alter dev=R1 param=r value=3k\nop1 dc\n", top),
            (UNRELATED + "a0 alter param=tnom value=50\na1 alter dev=R1 param=r value=3k\nop1 dc\n", top),
            (UNRELATED + "a0 alter param=temp value=50\na1 alter dev=R2 param=r value=1k\na2 alter dev=R1 param=r "
             "value=3k\nop1 dc\n", top + ('  alter instance("R2") r=1000.0',)),
            # the other kinds G1 tracks: a source field, an m, an X-line binding (sub=), a site two levels down
            (SRC_DC + "op0 dc\na1 alter dev=V1 param=dc value=2\na2 alter param=other value=2\nop1 dc\n",
             ('V1 (in 0) vamos_vsource type="dc" dc=(vamos_af1 ? vamos_av1 : vv)', "  var vamos_af1=1 vamos_av1=2.0",
              '  alter instance("V1") type="dc" dc=2.0')),
            (SRC_DC + "op0 dc\na1 alter dev=R1 param=m value=2\na2 alter param=other value=2\nop1 dc\n",
             ("R1 (in out) vamos_resistor r=1000.0 $mfactor=(vamos_af1 ? vamos_av1 : mm)",
              "  var vamos_af1=1 vamos_av1=2.0")),
            (XLINE + "op0 dc\na1 alter sub=X1 param=rr value=3k\na2 alter param=other value=2\nop1 dc\n",
             ("X1 (in out) half rr=(vamos_af1 ? vamos_av1 : rv)", alt)),
            (NESTED + "op0 dc\na1 alter dev=X0.X1.R1 param=r value=3k\na2 alter param=other value=2\nop1 dc\n",
             ("  R1 (a b) vamos_resistor r=(vamos_hf1 ? vamos_hv1 : rr) $mfactor=$mfactor",
              "  X1 (a b) inner rr=p*1000.0 vamos_hf1=vamos_hf2 vamos_hv1=vamos_hv2 $mfactor=$mfactor",
              "X0 (in out) outer p=1.0 vamos_hf2=vamos_af1 vamos_hv2=vamos_av1", "X0b (in outb) outer p=2.0\n", alt)),
            # one of two instance paths: X1's line passes the hook, X2's keeps the default (VACASK alters per path)
            (HALF + "op0 dc\na1 alter dev=X1.R1 param=r value=3k\na2 alter param=temp value=50\nop1 dc\n",
             ("X1 (in out) half rr=1000.0 vamos_hf1=vamos_af1 vamos_hv1=vamos_av1", "X2 (in out2) half rr=2000.0\n",
              "  R1 (a b) vamos_resistor r=(vamos_hf1 ? vamos_hv1 : rr) $mfactor=$mfactor", alt)),
        ]
        for deck, pieces in cases:
            with self.subTest(deck=deck.splitlines()[-3:]):
                text = vacask_deck(deck)
                self.has(text, *pieces, absent=('alter instance("R1") r=', 'alter instance("X1:R1")',
                                                'alter instance("X0:X1:R1")', 'alter model("m_dmod")',
                                                'alter instance("E1")', 'alter instance("X1") rr='))
                if "X0b (in outb)" not in deck and "X2 (in out2)" not in deck:   # Xyce: one path only (§5.3)
                    self.builds(deck, "xyce")
        # what VACASK does not re-evaluate stays as it was: a constant binding's alter
        text = vacask_deck(DIVIDER.replace("r=rv", "r=2k") + "op0 dc\na1 alter dev=R1 param=r value=3k\nop1 dc\n")
        self.has(text, "R1 (in out) vamos_resistor r=2000.0", '  alter instance("R1") r=3000.0', absent=("vamos_af",))

    def test_h1_g1_rule_a_stays_an_error_on_both_engines_for_every_hooked_kind(self):
        """Rule (a), a change after the alter of what the expression reads: Spectre's reading is not known (VACASK's
        own is re-evaluation, Xyce keeps the alter, and so does VACASK through the hook): status 2 on both.  Phase 2c5
        (R4): the message says so, `(VACASK and Xyce keep the alter)' (G1_CHANGED_HOOKED)."""
        cases = [(SRC_DC + "op0 dc\na1 alter dev=V1 param=dc value=2\na2 alter param=vv value=3\nop1 dc\n", "dc op1",
                  "alter a1 set dc of V1", "alter a2 changes vv"),
                 (SRC_DC + "op0 dc\na1 alter dev=R1 param=m value=2\ndc1 dc param=mm values=[1 3]\n", "dc dc1 at mm=3",
                  "alter a1 set m of R1", "dc dc1 (its sweep of mm) changes mm"),
                 (XLINE + "a1 alter sub=X1 param=rr value=3k\ndc1 dc param=rv values=[1k 2k]\n", "dc dc1 at rv=2000",
                  "alter a1 set rr of X1", "dc dc1 (its sweep of rv) changes rv")]
        for deck, where, *pieces in cases:
            for engine in ("vacask", "xyce"):
                with self.subTest(deck=deck.splitlines()[-2:], engine=engine):
                    errs = self.fails(deck, engine)
                    self.assertEqual(len([e for e in errs if e.startswith(where + ": ") and G1_CHANGED_HOOKED in e
                                          and all(p in e for p in pieces)]), 1, errs)

    def test_h1_a_dev_sub_or_mod_sweep_of_a_bound_parameter_sweeps_its_hook_on_vacask(self):
        """E102 and F3: VACASK cannot sweep a parameter bound to an expression ('is bound to an expression', exit 1),
        so plan.build refused a dev= sweep of one there, and a sub= sweep of an X-line binding and a mod= sweep of a
        card's (2c2's open runs) failed at VACASK's run; the deck now sweeps the hook's value with its flag set.  Phase
        2c7 (L3) re-decided the flag's statement: it sets the value variable to the sweep's first point too (VACASK set
        a field up at the declared 0 before the first point, which stopped it for an exp's td2 or a sine's freq)."""
        cases = [
            (DIVIDER + "dc1 dc dev=R1 param=r values=[1k 3k]\n", "R1 (in out) vamos_resistor r=(vamos_af1 ? vamos_av1 : rv)",
             "1000.0"),
            (CONTROLLED + "dc1 dc dev=E1 param=gain values=[1 2 3]\n", "E1 (out 0 in 0) vamos_vcvs gain=(vamos_af1 ? vamos_av1 : g)",
             "1.0"),
            (CONTROLLED + "dc1 dc dev=G1 param=gm values=[1m 2m]\n", "gain=(vamos_af1 ? vamos_av1 : gg)", "0.001"),
            (CONTROLLED + "dc1 dc dev=H1 param=rm values=[1k 2k]\n", "gain=(vamos_af1 ? vamos_av1 : rr)", "1000.0"),
            (CONTROLLED + "dc1 dc dev=D1 param=area values=[1 2]\n", "D1 (a 0) m_dmod area=(vamos_af1 ? vamos_av1 : ar)", "1.0"),
            (CONTROLLED + "dc1 dc dev=V1 param=dc values=[1 2]\n", 'V1 (in 0) vamos_vsource type="dc" dc=(vamos_af1 ? vamos_av1 : vv)',
             "1.0"),
            (CONTROLLED + "ac1 ac dev=K1 param=coupling values=[0.25 0.5] freq=1k\n", "k=(vamos_af1 ? vamos_av1 : kk)", "0.25"),
            (XLINE + "dc1 dc sub=X1 param=rr values=[1k 3k]\n", "X1 (in out) half rr=(vamos_af1 ? vamos_av1 : rv)", "1000.0"),
            (CARD_DIODE + "dc1 dc mod=dmod param=is values=[1e-14 1e-13]\n", "is=(vamos_af1 ? vamos_av1 : isv)", "1e-14"),
        ]
        for deck, line, first in cases:
            with self.subTest(deck=deck.splitlines()[-1]):
                text = vacask_deck(deck)
                lines = text.splitlines()
                self.assertIn(line, text)
                k = next(i for i, ln in enumerate(lines) if 'variable="vamos_av1"' in ln)
                self.assertEqual(lines[k - 1], "  var vamos_af1=1 vamos_av1=" + first)
                self.assertTrue(lines[k + 1].lstrip().startswith("analysis vamos_a1 "), lines[k + 1])
                self.assertEqual(lines[k + 2], "  var vamos_af1=0")
                self.assertNotIn('instance="', text)
                self.assertNotIn('model="m_dmod"', text)
                self.builds(deck, "xyce")
        # an alter in force: the flag stays set around the sweep, and VACASK restores the swept value after it
        text = vacask_deck(DIVIDER + "a1 alter dev=R1 param=r value=3k\ndc1 dc dev=R1 param=r values=[1k 2k]\nop2 dc\n")
        self.assertIn("  var vamos_af1=1 vamos_av1=3000.0\n  save v(out)\n  sweep vamos_w1 variable=\"vamos_av1\" "
                      "values=[1000.0, 2000.0] continuation=1\n    analysis vamos_a1 op\n  analysis vamos_a2 op\n", text)
        # a sweep block: the flag around each child's nest
        text = vacask_deck(DIVIDER + "sw1 sweep dev=R1 param=r values=[1k 3k] {\n  op1 dc\n}\n")
        self.assertIn("  var vamos_af1=1 vamos_av1=1000.0\n  sweep vamos_w1 variable=\"vamos_av1\" values=[1000.0, 3000.0] "
                      "continuation=0\n    analysis vamos_a1 op\n  var vamos_af1=0\n", text)
        # a constant binding keeps VACASK's own instance sweep
        text = vacask_deck(DIVIDER.replace("r=rv", "r=2k") + "dc1 dc dev=R1 param=r values=[1k 3k]\n")
        self.has(text, 'sweep vamos_w1 instance="R1" parameter="r"', absent=("vamos_af",))

    def test_h1_what_the_hook_does_not_cover_keeps_its_refusal(self):
        """A source field the parser transforms (a pulse's rise becomes (rise == 0 ? transres : rise)) has no single
        place to hook: VACASK keeps rule (b) for it, now with E151's first elaboration as a trigger, and E102's refusal
        of a sweep of it; Xyce runs them."""
        cases = [(PULSE_RISE + "op0 dc\na1 alter dev=V1 param=rise value=2n\na2 alter param=other value=2\ntr1 tran "
                  "stop=3u\n", "tran tr1", "alter a2 changes other"),
                 (PULSE_RISE + "o1 options temp=50\na1 alter dev=V1 param=rise value=2n\ntr1 tran stop=3u\n", "tran tr1",
                  "the temp=50 in force at VACASK's first elaboration changes temp")]
        for deck, where, changer in cases:
            with self.subTest(deck=deck.splitlines()[-2:]):
                errs = self.fails(deck, "vacask")
                self.assertEqual(len([e for e in errs if e.startswith(where + ": on VACASK, alter a1 set rise of V1, "
                                      "which the netlist binds to the expression rt") and changer in e
                                      and all(p in e for p in G1_VACASK)]), 1, errs)
                self.builds(deck, "xyce")
        errs = self.fails(PULSE_RISE + "dc1 dc dev=V1 param=rise values=[1n 2n]\n", "vacask")
        self.assertTrue(any("on VACASK rise of V1 is bound to the expression rt" in e for e in errs), errs)
        # temp 27 at the first elaboration, or an analysis before the alter: nothing VACASK re-elaborates on
        for tail in ("o1 options temp=27\na1 alter dev=V1 param=rise value=2n\ntr1 tran stop=3u\n",
                     "o1 options temp=50\nop0 dc\na1 alter dev=V1 param=rise value=2n\ntr1 tran stop=3u\n"):
            with self.subTest(tail=tail):
                self.builds(PULSE_RISE + tail, "vacask")

    def test_h1_a_variable_defined_by_an_expression_follows_its_inputs_on_vacask(self):
        """parameters rw=2*rv with rw a plan variable (an alter targets it) printed as rw=vamos_v_rw, so a sweep of rv
        before the alter left rw at its nominal on VACASK (out 0.333, 0.333 where Xyce gives 0.333, 0.2): rw now reads
        its expression until an alter or sweep sets the variable."""
        text = vacask_deck(EXPR_PARAM % "rw" + "dc1 dc param=rv values=[1k 2k]\na1 alter param=rw value=3k\nop1 dc\n")
        self.has(text, "parameters rv=vamos_v_rv\n", "parameters rw=(vamos_af1 ? vamos_v_rw : 2.0*rv)\n",
                 "  var vamos_af1=0\n", "  var vamos_af1=1 vamos_v_rw=3000.0\n")
        text = vacask_deck(EXPR_PARAM % "rw" + "dc1 dc param=rv values=[1k 2k]\ndc2 dc param=rw values=[1k 2k]\nop1 dc\n")
        self.assertIn("  var vamos_af1=1\n  sweep vamos_w2 variable=\"vamos_v_rw\" values=[1000.0, 2000.0] continuation=1\n"
                      "    analysis vamos_a2 op\n  var vamos_af1=0\n", text)
        text = vacask_deck(EXPR_PARAM % "rk" + "a1 alter param=rk value=3k\ndc1 dc param=rv values=[1k 2k]\n")
        self.has(text, "parameters rk=vamos_v_rk\n", "  var vamos_v_rk=3000.0\n", absent=("vamos_af",))

    def test_h1_the_hook_on_branches_lifted_definitions_cards_in_bodies_verilog_a_and_coupled_inductors(self):
        """An R1 in both branches of an if (the taken one, else, reads rr; the first match, if, is the constant): both
        print through the hook, so that the alter's var reaches whichever runs; a nested definition lifted to top level
        (outer__inner) declares the site's parameters; a netlist parameter named vamos_af1 moves the hook to k=2; a card
        defined in a subckt; a Verilog-A instance's and card's parameter on their per-instance cards; a coupled
        inductor's l inside its fold (it was refused: setting l dropped the fold)."""
        a2 = "a2 alter param=other value=2\nop1 dc\n"
        cases = [
            (COND_DUP + "op0 dc\na1 alter dev=X1.R1 param=r value=3k\n" + a2,
             ("    R1 (a b) vamos_resistor r=(vamos_hf1 ? vamos_hv1 : 1000.0) $mfactor=$mfactor",
              "    R1 (a b) vamos_resistor r=(vamos_hf1 ? vamos_hv1 : rr) $mfactor=$mfactor",
              "X1 (in out) half rr=1000.0 vamos_hf1=vamos_af1 vamos_hv1=vamos_av1")),
            (LIFTED + "op0 dc\na1 alter dev=X0.X1.R1 param=r value=3k\n" + a2,
             ("  X1 (a b) outer__inner p=p vamos_hf1=vamos_hf2 vamos_hv1=vamos_hv2 $mfactor=$mfactor",
              "X0 (in out) outer p=1.0 vamos_hf2=vamos_af1 vamos_hv2=vamos_av1",
              "subckt outer__inner (c d)\n  parameters p=0.0\n  parameters vamos_hf1=0\n  parameters vamos_hv1=0\n",
              "  R1 (c d) vamos_resistor r=(vamos_hf1 ? vamos_hv1 : p*1000.0) $mfactor=$mfactor")),
            (UNRELATED.replace("rc=1k", "rc=1k vamos_af1=5 vamos_av1=7") + "op0 dc\na1 alter dev=R1 param=r value=3k\n"
             + a2, ("R1 (in out) vamos_resistor r=(vamos_af2 ? vamos_av2 : rv)", "  var vamos_af2=0 vamos_av2=0",
                    "  var vamos_af2=1 vamos_av2=3000.0", "parameters vamos_af1=5.0")),
            ("simulator lang=spectre\nparameters other=1\nsubckt dsub (a)\nparameters isx=1e-14\nmodel dmod diode is=isx\n"
             "D1 (a 0) dmod\nends dsub\nI1 (0 d) isource dc=1m\nX1 (d) dsub\nR3 (d 0) resistor r=other*1G\nsave d\n"
             "op0 dc\na1 alter mod=dmod param=is value=1e-13\n" + a2,
             ("  model m_dmod sp_diode level=1.0 is=(vamos_af1 ? vamos_av1 : isx) eg=1.124481",
              "  var vamos_af1=1 vamos_av1=1e-13")),
            ("simulator lang=spectre\nparameters lv=1m other=1\nIac (0 p) isource dc=0 mag=1m\nL1 (p 0) inductor l=lv "
             "m=2\nL2 (s 0) inductor l=9m\nK1 mutual_inductor coupling=0.5 ind1=L1 ind2=L2\nR3 (p 0) resistor "
             "r=other*1G\nsave s\nac1 ac start=1k stop=1k lin=1\na1 alter dev=L1 param=l value=4m\na2 alter param=other "
             "value=2\nac2 ac start=1k stop=1k lin=1\n",
             ("vamos_inductor l=((vamos_af1 ? vamos_av1 : lv)/2.0)", "  var vamos_af1=1 vamos_av1=0.004")),
        ]
        for deck, pieces in cases:
            with self.subTest(deck=deck.splitlines()[-3:]):
                self.has(vacask_deck(deck), *pieces)
                self.builds(deck, "xyce")
        d = tempfile.mkdtemp(prefix="vamos_plan_va_")
        try:
            va = os.path.join(d, "vres2c.va")
            with open(va, "w") as fh:
                fh.write(VRES2C_VA)
            deck = (VA_HOOK + "sw1 dc dev=X1 param=r values=[1k 3k]\nsw2 dc mod=vm param=r values=[1k 3k]\nop1 dc\n"
                    "al alter dev=X1 param=r value=3k\nam alter mod=vm param=r value=3k\n" + a2.replace("op1", "op2"))
            text = vacask_deck(deck, files=[va])
            self.has(text, "model X1__va vres2c r=(vamos_af1 ? vamos_av1 : rv)",
                     "model X2__va vres2c r=(vamos_af2 ? vamos_av2 : rm)",
                     '  sweep vamos_w1 variable="vamos_av1" values=[1000.0, 3000.0] continuation=1',
                     '  sweep vamos_w2 variable="vamos_av2" values=[1000.0, 3000.0] continuation=1',
                     "  var vamos_af1=1 vamos_av1=3000.0", "  var vamos_af2=1 vamos_av2=3000.0",
                     absent=('alter model("X1__va")', 'alter model("X2__va")', 'model="X2__va"'))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_h1_a_model_group_s_alter_hooks_every_entry(self):
        """A mod= target names every entry of a model group: when one entry binds the parameter to an expression, all of
        them print through the hook (the alter is a var statement, which an entry left constant would never see: its
        vtho stayed 0.45 where the alter sets 0.6); an entry that leaves the parameter out cannot take the hook, so the
        target keeps G1's rule (b) on VACASK."""
        group = ("simulator lang=spectre\nparameters vt=0.40 other=1\nmodel nch bsim4 {\n"
                 "  1: type=n lmin=0.1u lmax=1u wmin=0.1u wmax=10u vtho=vt toxe=2n\n"
                 "  2: type=n lmin=1u lmax=10u wmin=0.1u wmax=10u vtho=0.45 toxe=2n\n}\n"
                 "Vg (g 0) vsource dc=1.2\nVd1 (d1 0) vsource dc=1.2\nVd2 (d2 0) vsource dc=1.2\n"
                 "M1 (d1 g 0 0) nch w=0.5u l=0.5u\nM2 (d2 g 0 0) nch w=0.5u l=2u\nR3 (g 0) resistor r=other*1G\n"
                 "save Vd1:p Vd2:p\n")
        tail = "op0 dc\na1 alter mod=nch param=vtho value=0.6\na2 alter param=other value=2\nop1 dc\n"
        text = vacask_deck(group + tail)
        self.has(text, "model 'm_nch.1' sp_bsim4v8 type=1.0 vth0=(vamos_af1 ? vamos_av1 : vt) ",
                 "model 'm_nch.2' sp_bsim4v8 type=1.0 vth0=(vamos_af1 ? vamos_av1 : 0.45) ",
                 "  var vamos_af1=1 vamos_av1=0.6", absent=('alter model(',))
        self.builds(group + tail, "xyce")
        lacking = group.replace("vtho=0.45 toxe=2n", "toxe=2n")
        errs = self.fails(lacking + tail, "vacask")
        self.assertTrue(any(e.startswith("dc op1: on VACASK, alter a1 set vtho of model nch, which the netlist binds to "
                                         "the expression vt") for e in errs), errs)
        self.builds(lacking + tail, "xyce")

    def test_h1_xyce_replays_an_alter_on_every_branch_s_instance(self):
        """run_xyce.find_instance's first match of X1.R1 was the if branch's R1, which the run does not take: the replay
        altered it and Xyce's op1 stayed 0.5 where Spectre (and VACASK) give 0.25; apply_alter now sets the R1 of the
        path the run takes (phase 2c5, R2: run_xyce.taken_instance; 2c4 set every R1, find_instances, which reached the
        untaken branch's definitions too), so the taken else branch prints 3000; find_instances still lists both."""
        from vamos.spectre import run_xyce
        decks = xyce_decks(COND_DUP + "op0 dc\na1 alter dev=X1.R1 param=r value=3k\nop1 dc\n")
        self.assertIn("R1 a b {rr} m={vamos_mfactor}\n", decks[0])
        self.assertIn("R1 a b 3000.0 m={vamos_mfactor}\n", decks[1])
        nl = parse_text(COND_DUP + "op0 dc\na1 alter dev=X1.R1 param=r value=3k\nop1 dc\n")
        self.assertEqual(len(run_xyce.find_instances(nl, "X1.R1")), 2)
        self.assertIs(run_xyce.find_instance(nl, "X1.R1"), run_xyce.find_instances(nl, "X1.R1")[0])

    # -- H2 ------------------------------------------------------------------------------------------------------
    def test_h2_an_alter_of_a_parameter_defined_by_an_expression_then_a_change_of_its_inputs(self):
        """2c3 skeptic's u08 (parameters rw=2*rv; alter param=rw; a sweep of rv) and u06 (a sub= alter of rr whose
        default is q*1k; a sweep of q): both engines keep the alter (0.25, 0.25), whether Spectre re-evaluates is as
        unknown as for G1: status 2 before any engine runs, on both engines, in G1's rule-(a) form."""
        cases = [
            (EXPR_PARAM % "rw" + "a1 alter param=rw value=3k\ndc1 dc param=rv values=[1k 2k]\n", "dc dc1 at rv=2000",
             "alter a1 set rw, which the netlist binds to the expression 2.0 * rv (it reads rv)",
             "dc dc1 (its sweep of rv) changes rv after the alter"),
            (EXPR_PARAM % "rw" + "op0 dc\na1 alter param=rw value=3k\na2 alter param=rv value=2k\nop1 dc\n", "dc op1",
             "alter a1 set rw, which", "alter a2 changes rv after the alter"),
            (EXPR_PARAM % "rw" + "a1 alter param=rw value=3k\nsw1 sweep param=rv values=[1k 2k] {\n  op1 dc\n}\n",
             "dc op1 at rv=2000", "alter a1 set rw, which", "sweep sw1 changes rv"),
            (EXPR_PARAM.replace("rw=2*rv", "rw=2*rv*(1+0.001*(temp-27))") % "rw" + "a1 alter param=rw value=3k\n"
             "a2 alter param=temp value=50\nop1 dc\n", "dc op1", "(it reads rv, temp)", "alter a2 changes temp"),
            (SUB_DEFAULT % ("q*1k", "") + "a1 alter sub=X1 param=rr value=3k\ndc1 dc sub=X1 param=q values=[1 2]\n",
             "dc dc1 at X1:q=2", "alter a1 set rr of X1, which subckt half binds to the default q * 1000.0 (it reads q)",
             "dc dc1 (its sweep of X1:q) changes q after the alter"),
            (SUB_DEFAULT % ("q*1k", "") + "op0 dc\na1 alter sub=X1 param=rr value=3k\na2 alter sub=X1 param=q value=2\n"
             "op1 dc\n", "dc op1", "alter a1 set rr of X1, which", "alter a2 changes q"),
            (SUB_DEFAULT % ("q*1k", " q=qv") + "a1 alter sub=X1 param=rr value=3k\ndc1 dc param=qv values=[1 2]\n",
             "dc dc1 at qv=2", "alter a1 set rr of X1, which", "dc dc1 (its sweep of qv) changes q"),
        ]
        for deck, where, *pieces in cases:
            for engine in ("vacask", "xyce"):
                with self.subTest(deck=deck.splitlines()[-2:], engine=engine):
                    errs = self.fails(deck, engine)
                    hits = [e for e in errs if e.startswith(where + ": ") and G2C4_BOTH_KEEP in e
                            and all(p in e for p in pieces)]
                    self.assertEqual(len(hits), 1, errs)
        # controls: a constant definition; inputs that never change after the alter; a change before it; a value
        # equal to the alter's; a constant subckt default; a default whose inputs stay
        for deck in (EXPR_PARAM % "rk" + "a1 alter param=rk value=3k\ndc1 dc param=rv values=[1k 2k]\n",
                     EXPR_PARAM % "rw" + "a1 alter param=rw value=3k\nop1 dc\ndc1 dc param=other values=[1 2]\n",
                     EXPR_PARAM % "rw" + "dc1 dc param=rv values=[1k 2k]\na1 alter param=rw value=3k\nop1 dc\n",
                     EXPR_PARAM % "rw" + "a1 alter param=rw value=3k\na2 alter param=rv value=1.5k\nop1 dc\n",
                     SUB_DEFAULT % ("1k", "") + "a1 alter sub=X1 param=rr value=3k\ndc1 dc sub=X1 param=q values=[1 2]\n",
                     SUB_DEFAULT % ("q*1k", "") + "a1 alter sub=X1 param=rr value=3k\nop1 dc\ndc1 dc param=other "
                     "values=[1 2]\n"):
            for engine in ("vacask", "xyce"):
                with self.subTest(deck=deck.splitlines()[-2:], engine=engine):
                    self.builds(deck, engine)

    # -- H3 ------------------------------------------------------------------------------------------------------
    def test_h3_a_sweep_of_a_subckt_instance_m_hooks_vamos_mfactor_from_its_nominal_on_xyce(self):
        """q_holder_m: Xyce ended with status 2, `Device instance X1:R1: Multiplicity Factor (M) must be non-negative':
        the .param hook of X1's m had the nominal 0 (no m on the line, none in the subckt), and the X line passed it as an
        X-line M= besides vamos_mfactor; now the hook starts at the instance's m (1 when absent) and only vamos_mfactor,
        which every device of the body multiplies by, reads it."""
        cases = [(HOLDER_M + "op0 dc\ndc1 dc dev=X1 param=m values=[1 2]\nop2 dc\n", 1, ".param vamos_x_X1_m=1.0",
                  "X1 in out half rr=1000.0 vamos_mfactor={vamos_x_X1_m}"),
                 (HOLDER_M + "sw1 sweep dev=X1 param=m values=[1 2] {\n  op1 dc\n}\n", 0, ".param vamos_x_X1_m=1.0",
                  "X1 in out half rr=1000.0 vamos_mfactor={vamos_x_X1_m}"),
                 (HOLDER_M.replace("half rr=1k", "half rr=1k m=2") + "dc1 dc dev=X1 param=m values=[1 2]\n", 0,
                  ".param vamos_x_X1_m=2.0", "X1 in out half rr=1000.0 vamos_mfactor={vamos_x_X1_m}"),
                 (HOLDER_M.replace("X1 (in out) half rr=1k", "X0 (in out) outer").replace(
                     "V1 (in 0)", "subckt outer (a b)\nX1 (a b) half rr=1k\nends outer\nV1 (in 0)")
                  + "dc1 dc dev=X0.X1 param=m values=[1 2]\n", 0, ".param vamos_x_X0_X1_m=1.0",
                  "X1 a b half rr=1000.0 vamos_mfactor={vamos_mfactor*vamos_x_X0_X1_m}")]
        for deck, k, param, xline in cases:
            with self.subTest(deck=deck.splitlines()[-1]):
                text = xyce_decks(deck)[k]
                self.assertIn(param + "\n", text)
                self.assertIn(xline + "\n", text)
                self.assertNotIn(" m={vamos_x_", text)
                self.assertNotIn(".param vamos_x_X1_m=0.0", text)

    # -- H4 ------------------------------------------------------------------------------------------------------
    def test_h4_the_multiplier_message_names_the_spectre_master(self):
        """vacask.mfactor_param fell back to the kind letter (`the multiplier of a v ...'), tables.ELEMENT_NAME naming
        only the model kinds: every IR kind now has its Spectre master name there, the seven model kinds unchanged."""
        want = {"m": "MOS", "q": "BJT", "j": "JFET", "d": "diode", "r": "resistor", "c": "capacitor", "l": "inductor",
                "v": "vsource", "i": "isource", "e": "vcvs", "g": "vccs", "f": "cccs", "h": "ccvs",
                "k": "mutual_inductor", "b": "bsource", "x": "subckt", "y": "Verilog-A module"}
        for kind, name in want.items():
            self.assertEqual(T.ELEMENT_NAME.get(kind), name, kind)
        head = CONTROLLED.replace("save out o1 o2 o4 a s\n", "save out\n")
        for tail, inst, master in (("op1 dc\na1 alter dev=V1 param=m value=2\nop2 dc\n", "V1", "vsource"),
                                   ("op1 dc\na1 alter dev=E1 param=m value=2\nop2 dc\n", "E1", "vcvs"),
                                   ("op1 dc\na1 alter dev=H1 param=m value=2\nop2 dc\n", "H1", "ccvs"),
                                   ("dc1 dc dev=V1 param=m values=[1 2]\n", "V1", "vsource")):
            with self.subTest(tail=tail):
                with self.assertRaises(NoteError) as cm:
                    vacask_deck(head + tail)
                msgs = errors(cm.exception)
                self.assertTrue(any(m.endswith("%s: the multiplier of a %s is not a device parameter on VACASK (folded "
                                               "into the element, or without effect); a sweep or alter of m is not "
                                               "supported in v1" % (inst, master)) for m in msgs), msgs)


# -- phase 2c5: R1 a hooked alter or sweep is a VACASK variable change for rule (b), R2 Xyce's replay on the taken
# branch only, R3 the hooked multiplier of an instance in a body, R4 control-block source statements and G1's
# rule-(a) message for hooked targets (parsed decks, both engines' plans and renders, no engine) -----------------------

RISE_RV = """simulator lang=spectre
parameters rt=1u rv=1k other=1
V1 (in 0) vsource type=pulse val0=0 val1=1 delay=1u rise=rt fall=1u width=5u period=20u
R1 (in out) resistor r=rv
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
"""
CELL_RISE = """simulator lang=spectre
parameters rt=1u other=1
subckt cell (o)
parameters rr=1k
V1 (i 0) vsource type=pulse val0=0 val1=1 delay=1u rise=rt fall=1u width=5u period=20u
R1 (i o) resistor r=rr
R2 (o 0) resistor r=1k
ends cell
X1 (out) cell rr=1k
R3 (out 0) resistor r=other*1G
save out
"""
NOISY_RV = """simulator lang=spectre
parameters nflag=1 rv=1k other=1
V1 (in 0) vsource dc=0 mag=1
R1 (in out) resistor r=1k isnoisy=nflag
R2 (out 0) resistor r=rv
R3 (in 0) resistor r=other*1k
save out
"""
GROUP_LACKING = """simulator lang=spectre
parameters vt=0.40 other=1 rv=1k
model nch bsim4 {
  1: type=n lmin=0.1u lmax=1u wmin=0.1u wmax=10u vtho=vt toxe=2n
  2: type=n lmin=1u lmax=10u wmin=0.1u wmax=10u toxe=2n
}
Vg (g 0) vsource dc=1.2
Vd1 (d1 0) vsource dc=1.2
Vd2 (d2 0) vsource dc=1.2
M1 (d1 g 0 0) nch w=0.5u l=0.5u
M2 (d2 g 0 0) nch w=0.5u l=2u
V5 (i5 0) vsource dc=1
R5 (i5 o5) resistor r=rv
R6 (o5 0) resistor r=1k
R3 (g 0) resistor r=other*1G
save Vd1:p Vd2:p o5
"""
COND_X = """simulator lang=spectre
parameters sel=%s
subckt half (a b)
R1 (a b) resistor r=1k
ends half
subckt other (a b)
R1 (a b) resistor r=1k
ends other
V1 (in 0) vsource dc=1
if (sel > 0) {
X1 (in out) %s
} else {
X1 (in out) %s
}
X2 (in out2) other
R2 (out 0) resistor r=1k
R4 (out2 0) resistor r=1k
save out out2
op0 dc
a1 alter dev=X1.R1 param=r value=3k
op1 dc
"""
BODY_M = """simulator lang=spectre
parameters other=1
subckt half (a b)
R1 (a b) resistor r=1k
ends half
V1 (in 0) vsource dc=1
X1 (in out) half
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out
"""
EXP_TD = """simulator lang=spectre
parameters td=1u t2=10u rv=1k other=1
V1 (in 0) vsource type=exp val0=0 val1=1 td1=td tau1=0.5u td2=%s tau2=1u
R1 (in out) resistor r=rv
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
"""
A1_RISE = "a1 alter dev=V1 param=rise value=2u\n"
A2_R1 = "a2 alter dev=R1 param=r value=3k\n"
TR1 = "tr1 tran stop=3u\n"
HOOK_VAR = "which the VACASK deck sets through a variable"


def subckt_block(text, name):
    """The .subckt NAME ... .ends NAME block of a Xyce deck."""
    start = text.index(".subckt %s " % name)
    return text[start:text.index(".ends %s" % name, start)]


class TestFollowUp2c5(unittest.TestCase):
    """Phase 2c5, the repair of 2c4: R1 2c4's hooked alter or sweep prints var statements, which VACASK re-evaluates
    every binding after, so an earlier alter of a target the hook does not print (a pulse's rise, an isnoisy expression,
    a model group lacking the parameter) was lost (status 0, VACASK in 0.5 where Xyce 0.25): such a change now counts
    for rule (b), and a hooked alter no longer counts as VACASK's first elaboration (O01 was refused).  R2 Xyce's replay
    alters the taken branch's instance only (2c4's find_instances altered the untaken branch's subckt too: out2 0.25
    where 0.5).  R3 the m of an instance in a body ($mfactor=$mfactor) prints through a hook on VACASK (an alter was
    lost after any change, 0.5 where 0.667; a sweep failed, exit 1).  R4 a control-block statement leaves out every
    field that reads a name, derived or not, or plan.build refuses it (VACASK exit 1, `Variable or constant 'rt' not
    defined'); G1's rule-(a) message names what vamos's VACASK deck does for a hooked target."""

    def fails(self, text, engine):
        nl = parse_text(text)
        with self.assertRaises(NoteError) as cm:
            PL.build(nl, engine, Settings())
        return errors(cm.exception)

    def builds(self, text, engine):
        return PL.build(parse_text(text), engine, Settings())

    def has(self, text, *pieces, absent=()):
        for p in pieces:
            self.assertIn(p, text)
        for p in absent:
            self.assertNotIn(p, text)

    # -- R1 ------------------------------------------------------------------------------------------------------
    def test_r1_a_hooked_alter_or_sweep_is_a_variable_change_for_rule_b_on_vacask(self):
        """S-2c4's A01-A11 (VACASK status 0 with the alter of the pulse's rise, the isnoisy expression or the model
        group's vtho lost: in 0.5 against 0.25, noise 3.526e-9 against 1.763e-9, Vd1:p -1.185e-4 against -6.555e-5):
        the hook's var statements re-evaluate every binding, so rule (b) refuses them on VACASK, naming the change;
        Xyce runs them.  A12: the flag's reset after a hooked sweep is a change the next analysis elaborates."""
        cases = [
            ("A01", RISE_RV + A1_RISE + A2_R1 + TR1, "tran tr1", "rise of V1", "rt", "alter a2 changes r of R1, "),
            ("A02", RISE_RV + A1_RISE + "dc1 dc dev=R1 param=r values=[1k 3k]\n" + TR1, "dc dc1 at R1:r=1000",
             "rise of V1", "rt", "dc dc1 (its sweep of R1:r) changes r of R1, "),
            ("A03", RISE_RV + "op0 dc\n" + A1_RISE + A2_R1 + TR1, "tran tr1", "rise of V1", "rt", "alter a2 changes r of R1, "),
            ("A04", CELL_RISE + "a1 alter dev=X1.V1 param=rise value=2u\na2 alter dev=X1.R1 param=r value=3k\n" + TR1,
             "tran tr1", "rise of X1.V1", "rt", "alter a2 changes r of X1.R1, "),
            ("A06", RISE_RV + "op0 dc\n" + A2_R1 + A1_RISE + TR1, "tran tr1", "rise of V1", "rt", "alter a2 changes r of R1, "),
            ("A07", RISE_RV + A1_RISE + "tr0 tran stop=3u\n" + A2_R1 + TR1, "tran tr1", "rise of V1", "rt",
             "alter a2 changes r of R1, "),
            ("A09", NOISY_RV + "a1 alter dev=R1 param=isnoisy value=0\na2 alter dev=R2 param=r value=3k\n"
             "n1 (out 0) noise start=1k stop=1k lin=1 iprobe=V1\n", "noise n1", "isnoisy of R1", "nflag",
             "alter a2 changes r of R2, "),
            ("A10", RISE_RV + A1_RISE + "sw1 sweep dev=R1 param=r values=[1k 3k] {\n  op1 dc\n}\n" + TR1,
             "dc op1 at R1:r=1000", "rise of V1", "rt", "sweep sw1 changes r of R1, "),
            ("A11", GROUP_LACKING + "op0 dc\na1 alter mod=nch param=vtho value=0.6\na2 alter dev=R5 param=r value=3k\n"
             "op1 dc\n", "dc op1", "vtho of model nch", "vt", "alter a2 changes r of R5, "),
            ("A12", RISE_RV + "dc1 dc dev=R1 param=r values=[1k 3k]\n" + A1_RISE + TR1, "tran tr1", "rise of V1", "rt",
             "dc dc1 (its sweep of R1:r) changes r of R1, "),
            # a hooked source alter prints alter instance after its var, so it is VACASK's first elaboration: the
            # variable change after it is pending when the rise is altered
            ("A13", RISE_RV.replace("other=1", "other=1 vv=1") + "V5 (i5 0) vsource dc=vv\nR5 (i5 0) resistor r=1k\n"
             "a0 alter dev=V5 param=dc value=2\na1 alter param=other value=2\na2 alter dev=V1 param=rise value=2u\n" + TR1,
             "tran tr1", "rise of V1", "rt", "alter a1 changes other"),
            # X-2c5's X07 and X13: the reset of a hooked source field's flag, of a hooked variable's (rw=2*rv, rv a plan
            # variable too), after its sweep
            ("X07", RISE_RV.replace("other=1", "other=1 vv=1") + "V5 (i5 0) vsource dc=vv\nR5 (i5 0) resistor r=1k\n"
             "dc1 dc dev=V5 param=dc values=[1 2]\n" + A1_RISE + TR1, "tran tr1", "rise of V1", "rt",
             "dc dc1 (its sweep of V5:dc) changes dc of V5, "),
            ("X13", RISE_RV.replace("other=1", "other=1 rw=2*rv").replace("r=rv", "r=rw")
             + "dc0 dc param=rv values=[1k 2k]\ndc1 dc param=rw values=[1k 3k]\n" + A1_RISE + TR1, "tran tr1",
             "rise of V1", "rt", "dc dc1 (its sweep of rw) changes rw, "),
            # X-2c5's X02: isnoisy=nflag altered with a word, then a variable change (2c4 tracked no word binding)
            ("X02", NOISY_RV + "a1 alter dev=R1 param=isnoisy value=no\na2 alter param=other value=2\n"
             "n1 (out 0) noise start=1k stop=1k lin=1 iprobe=V1\n", "noise n1", "isnoisy of R1", "nflag",
             "alter a2 changes other"),
        ]
        for name, deck, where, what, expr, change in cases:
            with self.subTest(deck=name):
                errs = self.fails(deck, "vacask")
                alter = "a2" if name == "A13" else "a1"
                head = "%s: on VACASK, alter %s set %s, which the netlist binds to the expression %s (it reads %s); " % (
                    where, alter, what, expr, expr)
                hits = [e for e in errs if e.startswith(head) and change in e and all(p in e for p in G1_VACASK)]
                self.assertEqual(len(hits), 1, errs)
                if name not in ("A13", "X02"):
                    self.assertIn(change + HOOK_VAR + ", and VACASK re-evaluates", hits[0])
                self.builds(deck, "xyce")

    def test_r1_what_vacask_keeps_builds_and_a_hooked_alter_does_not_elaborate(self):
        """The controls (S-2c4's A01c, A05, A07c, A09c, A11c): a constant target's alter, a hooked alter before the rise
        alter and VACASK's first elaboration, a hooked alter of a constant binding; O01 (a hooked alter, a variable,
        then the rise, the first elaboration taking every var) was refused because 2c4 took the hooked alter as VACASK's
        first elaboration; A12c the reset elaborated by an analysis before the alter; A14 a hooked sweep under an alter
        of its target, which leaves the flag set (no reset)."""
        decks = [RISE_RV.replace("R3 (in 0)", "R5 (out 0) resistor r=1G\nR3 (in 0)") + A1_RISE
                 + "a2 alter dev=R5 param=r value=2G\n" + TR1,                                              # A01c
                 RISE_RV + A2_R1 + A1_RISE + TR1,                                                          # A05
                 RISE_RV.replace("r=rv", "r=1k") + A1_RISE + "tr0 tran stop=3u\n" + A2_R1 + TR1,           # A07c
                 NOISY_RV.replace("r=rv", "r=3k").replace("R3 (in 0)", "R5 (out 0) resistor r=1G\nR3 (in 0)")
                 + "a1 alter dev=R1 param=isnoisy value=0\na2 alter dev=R5 param=r value=2G\n"
                 "n1 (out 0) noise start=1k stop=1k lin=1 iprobe=V1\n",                                    # A09c
                 GROUP_LACKING + "op0 dc\na1 alter mod=nch param=vtho value=0.6\nop1 dc\n",                 # A11c
                 RISE_RV + "a0 alter dev=R1 param=r value=3k\na1 alter param=other value=2\na2 alter dev=V1 param=rise "
                 "value=2u\n" + TR1,                                                                       # O01
                 RISE_RV + "dc1 dc dev=R1 param=r values=[1k 3k]\nop9 dc\n" + A1_RISE + TR1,               # A12c
                 RISE_RV + "a0 alter dev=R1 param=r value=3k\ndc1 dc dev=R1 param=r values=[1k 3k]\n" + A1_RISE + TR1,
                 RISE_RV.replace("other=1", "other=1 rw=2*rv").replace("r=rv", "r=rw")
                 + "dc0 dc param=rv values=[1k 2k]\ndc1 dc param=rw values=[1k 3k]\nop9 dc\n" + A1_RISE + TR1,   # X13c
                 NOISY_RV + "a1 alter dev=R1 param=isnoisy value=no\n"
                 "n1 (out 0) noise start=1k stop=1k lin=1 iprobe=V1\n"]                                    # X03
        for deck in decks:
            for engine in ("vacask", "xyce"):
                with self.subTest(deck=deck.splitlines()[-4:], engine=engine):
                    self.builds(deck, engine)
        text = vacask_deck(decks[5])
        self.has(text, "  var vamos_af1=1 vamos_av1=3000.0\n  var vamos_v_other=2.0\n  alter instance(\"V1\") type=\"pulse\"")

    # -- R2 ------------------------------------------------------------------------------------------------------
    def test_r2_xyce_replays_an_alter_on_the_taken_branch_s_instance_only(self):
        """S-2c4's B01-B04: X1 is half in the taken branch and other in the untaken one, X2 another instance of
        other: 2c4's find_instances altered both definitions, so X2.R1 took the alter (out2 0.25 where Spectre and VACASK
        give 0.5; B03, with the branches swapped, was wrong before 2c4 too).  The replay alters the instance on the taken
        path only: half's R1 3000, other's 1000."""
        for name, deck, taken in (("B01", COND_X % ("1", "half", "other"), "half"),
                                  ("B03", COND_X % ("0", "other", "half"), "half"),
                                  ("B04", COND_X.replace("R1 (a b) resistor r=1k", "parameters rr=1k\nR1 (a b) resistor r=rr")
                                   .replace("parameters sel=%s", "parameters sel=%s other=1")
                                   .replace("save out out2", "R3 (in 0) resistor r=other*1k\nsave out out2")
                                   .replace("op1 dc", "a2 alter param=other value=2\nop1 dc") % ("1", "half", "other"),
                                   "half")):
            with self.subTest(deck=name):
                decks = xyce_decks(deck)
                self.assertEqual(len(decks), 2)
                self.assertNotIn("3000.0", decks[0])
                self.assertIn("R1 a b 3000.0 m={vamos_mfactor}\n", subckt_block(decks[1], taken))
                self.assertNotIn("3000.0", subckt_block(decks[1], "other"))
                self.assertIn("X2 in out2 other\n", decks[1])
        b02 = COND_X.replace("if (sel > 0) {", "subckt top (a b)\nparameters sel=1\nif (sel > 0) {") \
            .replace("X1 (in out) %s\n}\nX2", "X1 (a b) %s\n}\nends top\nXT (in out) top\nX2") \
            .replace("X1 (in out) %s\n} else", "X1 (a b) %s\n} else").replace("dev=X1.R1", "dev=XT.X1.R1")
        decks = xyce_decks(b02 % ("1", "half", "other"))
        self.assertIn("R1 a b 3000.0 m={vamos_mfactor}\n", subckt_block(decks[1], "half"))
        self.assertNotIn("3000.0", subckt_block(decks[1], "other"))
        # E155's case, both branches in one body: the taken else branch's R1 is altered, the if branch's is not
        from vamos.spectre import run_xyce
        nl = parse_text(COND_DUP + "op0 dc\na1 alter dev=X1.R1 param=r value=3k\nop1 dc\n")
        plan = PL.build(nl, "xyce", Settings())
        nl2, _sm, _notes = SG.resolve(nl, plan)
        act = next(a for a in plan.actions if a.op == "alter")
        after = run_xyce.replay(nl2, act)
        first, second = run_xyce.find_instances(after, "X1.R1")
        self.assertEqual((first.value, second.value), (Num(1000.0), Num(3000.0)))

    def test_r2_the_replay_never_alters_a_definition_another_path_uses(self):
        """A target in a subckt two instance paths reach is refused by plan.build on Xyce (§5.3); an alter that reaches
        the replay anyway (built by hand) is refused there, status 2, and never changes the shared definition."""
        from vamos.spectre import run_xyce
        text = HALF + "op1 dc\n"
        nl = parse_text(text)
        act = PL.Action("alter", {"target": ("instance", "X1.R1", "r"), "value": 3000.0}, None, "deck.scs:20")
        with self.assertRaises(NoteError) as cm:
            run_xyce.replay(nl, act)
        msgs = errors(cm.exception)
        self.assertTrue(any("X1.R1 lies in subckt half, which 2 instance paths reach (X1, X2)" in m for m in msgs), msgs)
        self.assertEqual(run_xyce.find_instance(nl, "X1.R1").value, Name("rr"))

    # -- R3 ------------------------------------------------------------------------------------------------------
    def test_r3_vacask_prints_the_multiplier_of_an_instance_in_a_body_through_a_hook(self):
        """S-2c4's G01-G06: the body's R1 printed $mfactor=$mfactor (the holder's multiplier, an expression VACASK
        re-evaluates), so `alter instance("X1:R1") $mfactor=2.0' was lost after any change (0.5 where Xyce gives 0.667)
        and a sweep of it failed (`parameter '$mfactor' ... is bound to an expression', exit 1); the multiplier now reads
        the site's hook, ($mfactor*(vamos_hf1 ? vamos_hv1 : 1.0)), which X1's line passes."""
        r1 = "  R1 (a b) vamos_resistor r=1000.0 $mfactor=($mfactor*(vamos_hf1 ? vamos_hv1 : 1.0))"
        x1 = "X1 (in out) half vamos_hf1=vamos_af1 vamos_hv1=vamos_av1"
        alt = "  var vamos_af1=1 vamos_av1=2.0"
        cases = [(BODY_M + "op0 dc\na1 alter dev=X1.R1 param=m value=2\na2 alter param=other value=2\nop1 dc\n",
                  (r1, x1, alt)),                                                                           # G01
                 (BODY_M + "op0 dc\na1 alter dev=X1.R1 param=m value=2\nop1 dc\n", (r1, x1, alt)),          # G01c
                 (BODY_M.replace("r=1k\nends", "r=1k m=1\nends") + "op0 dc\na1 alter dev=X1.R1 param=m value=2\n"
                  "a2 alter param=other value=2\nop1 dc\n", (r1, x1, alt)),                                # G02
                 (BODY_M + "op0 dc\na1 alter dev=X1.R1 param=m value=2\na2 alter param=temp value=50\nop1 dc\n",
                  (r1, x1, alt)),                                                                           # G05
                 (BODY_M + "dc1 dc dev=X1.R1 param=m values=[1 2]\n",                   # (2c7, L3: the first point)
                  (r1, x1, '  var vamos_af1=1 vamos_av1=1.0\n  sweep vamos_w1 variable="vamos_av1" values=[1.0, 2.0] '
                   "continuation=1\n    analysis vamos_a1 op\n  var vamos_af1=0\n")),                      # G06
                 (BODY_M.replace("subckt half (a b)\nR1", "subckt inner (a b)\nR1").replace("ends half", "ends inner\n"
                  "subckt outer (a b)\nX1 (a b) inner\nends outer").replace("X1 (in out) half", "X0 (in out) outer")
                  + "op0 dc\na1 alter dev=X0.X1 param=m value=2\na2 alter param=other value=2\nop1 dc\n",
                  ("  X1 (a b) inner $mfactor=($mfactor*(vamos_hf1 ? vamos_hv1 : 1.0))",
                   "X0 (in out) outer vamos_hf1=vamos_af1 vamos_hv1=vamos_av1", alt)),                     # G04
                 # X1's m altered and swept before (no m on its line): the holder's $mfactor and the hook multiply
                 (BODY_M + "op0 dc\na0 alter dev=X1 param=m value=3\na1 alter dev=X1.R1 param=m value=2\n"
                  "dc1 dc dev=X1 param=m values=[1 2]\nop1 dc\n", (r1, x1, alt, '  alter instance("X1") $mfactor=3.0'))]
        for deck, pieces in cases:
            with self.subTest(deck=deck.splitlines()[-4:]):
                self.has(vacask_deck(deck), *pieces, absent=('alter instance("X1:R1")', 'alter instance("X0:X1")',
                                                             'instance="X1:R1"', 'instance="X0:X1"'))
                self.builds(deck, "xyce")
        # a top-level instance's m is no binding: VACASK's own alter, as before
        text = vacask_deck(DIVIDER + "op0 dc\na1 alter dev=R1 param=m value=2\nop1 dc\n")
        self.has(text, '  alter instance("R1") $mfactor=2.0', absent=("vamos_af",))
        # an X line on the path that gives m: VACASK's $mfactor of the device carries it, and the refusal (TestVacaskSpectre,
        # phase 2c) stands, status 2 before any engine runs; Xyce runs it
        deck = (BODY_M.replace("X1 (in out) half", "X1 (in out) half m=3") + "op0 dc\na1 alter dev=X1.R1 param=m "
                "value=2\na2 alter param=other value=2\nop1 dc\n")
        with self.assertRaises(NoteError) as cm:
            vacask_deck(deck)
        self.assertTrue(any("X1.R1: VACASK sets the device's $mfactor, which also carries the multiplier of the enclosing "
                            "instance X1 (m=3.0)" in m for m in errors(cm.exception)), errors(cm.exception))
        self.builds(deck, "xyce")

    # -- R4 ------------------------------------------------------------------------------------------------------
    def test_r4_a_control_block_statement_leaves_out_every_field_that_reads_a_name(self):
        """S-2c4's probe1: a pulse whose rise is bound (derived (rt == 0 ? transres : rt)) and an exp whose td2 reads
        td (printed relative, td2-td1) printed the derived field into the control block's alter, and VACASK stopped
        (`Variable or constant 'rt' not defined', exit 1); a field that reads a name is now left out of every alter and
        source switch, where the instance's binding keeps it (and a hooked field it is derived from gives the new
        value)."""
        cases = [
            # x_pulse_val1_rise_bound
            (RISE_RV.replace("rv=1k ", "").replace("r=rv", "r=1k") + "a1 alter dev=V1 param=val1 value=2\n" + TR1,
             '  alter instance("V1") type="pulse" val0=0.0 val1=2.0 delay=1e-06 fall=1e-06 width=5e-06 period=2e-05\n'),
            # x_pulse_width: width hooked, delay=wd and rise=rt left out
            (RISE_RV.replace("rt=1u", "wd=1u rt=1u").replace("delay=1u", "delay=wd").replace("width=5u", "width=wd")
             + "a1 alter dev=V1 param=width value=2u\na2 alter param=other value=2\n" + TR1,
             '  var vamos_af1=1 vamos_av1=2e-06\n  alter instance("V1") type="pulse" val0=0.0 val1=1.0 fall=1e-06 '
             'width=2e-06 period=2e-05\n'),
            # x_exp_td2: td2 hooked, its relative value td2-td1 reads td: left out, the hook gives 2u - td
            (EXP_TD % "t2" + "a1 alter dev=V1 param=td2 value=2u\na2 alter param=other value=2\n" + TR1,
             '  var vamos_af1=1 vamos_av1=2e-06\n  alter instance("V1") type="exp" val0=0.0 val1=1.0 tau1=5e-07 '
             'tau2=1e-06\n'),
            # a given dc beside a bound rise, switched around the tran
            (RISE_RV.replace("type=pulse", "type=pulse dc=0.5") + TR1,
             '  alter instance("V1") type="pulse" val0=0.0 val1=1.0 delay=1e-06 fall=1e-06 width=5e-06 period=2e-05 '
             'dc=0.5\n'),
            # a pwl whose point reads vp, switched around the tran: its wave is left out
            (RISE_RV.replace("rt=1u", "vp=1").replace("type=pulse val0=0 val1=1 delay=1u rise=rt fall=1u width=5u "
                                                      "period=20u", "type=pwl dc=0 wave=[0 0 1u vp 3u vp]") + TR1,
             '  alter instance("V1") type="pwl" delay=0.0 dc=0.0\n'),
        ]
        for deck, line in cases:
            with self.subTest(deck=deck.splitlines()[-3:]):
                text = vacask_deck(deck)
                self.assertIn(line, text)
                ctl = text[text.index("\ncontrol\n"):]
                for name in ("rt", "td", "wd", "vp", "t2"):
                    self.assertNotIn("=%s" % name, ctl.replace("vamos_v_", ""))
                    self.assertNotIn("-%s" % name, ctl)
                self.builds(deck, "xyce")

    def test_r4_a_field_the_statement_cannot_leave_out_is_refused_on_vacask(self):
        """An exp whose td2 is a constant (no hook) beside td1=td: the alter of td2 makes the printed relative td2
        2u - td, which the control block cannot read, and leaving it out would keep 10u - td; and a field the instance
        line does not print at all (X-2c5's X14, X16: an alter of the type of a dc source whose pulse fields read vv, of
        a pulse to a sine whose ampl reads vv), which 2c4 left out silently, VACASK taking its default: status 2 on
        VACASK before any engine runs; Xyce runs them."""
        x14 = (RISE_RV.replace("rt=1u", "vv=2").replace("type=pulse val0=0 val1=1 delay=1u rise=rt",
                                                         "type=dc dc=0 val0=0 val1=vv delay=1u rise=1u")
               + "a1 alter dev=V1 param=type value=pulse\n" + TR1)
        x16 = (RISE_RV.replace("rt=1u", "vv=1").replace("val1=1 delay=1u rise=rt fall=1u width=5u period=20u",
                                                         "val1=vv delay=1u rise=1u fall=1u width=5u period=20u ampl=vv "
                                                         "freq=1meg")
               + "a1 alter dev=V1 param=type value=sine\n" + TR1)
        for deck, field in ((EXP_TD % "10u" + "a1 alter dev=V1 param=td2 value=2u\n" + TR1, "td2"),
                            (EXP_TD.replace("td1=td", "td1=1u").replace("td2=%s", "td2=t2") % ()
                             + "a1 alter dev=V1 param=td1 value=2u\n" + TR1, "td2"),
                            (x14, "val1"), (x16, "ampl")):
            with self.subTest(deck=deck.splitlines()[-2:]):
                errs = self.fails(deck, "vacask")
                self.assertTrue(any(e.startswith("alter a1: on VACASK the control block's alter of V1 would print %s=" % field)
                                    and "not supported on VACASK in v1 (Xyce runs it)" in e for e in errs), errs)
                self.builds(deck, "xyce")
        # X15: the same type switch with constant fields prints them all
        text = vacask_deck(x14.replace("val1=vv", "val1=2"))
        self.assertIn('  alter instance("V1") type="pulse" val0=0.0 val1=2.0 delay=1e-06 rise=1e-06 fall=1e-06 '
                      'width=5e-06 period=2e-05', text)

    def test_r4_g1_rule_a_names_what_vamos_s_vacask_deck_does(self):
        """Rule (a)'s message said `(VACASK re-evaluates it, Xyce keeps the alter)' for every binding; vamos's VACASK
        deck keeps the alter of a hooked target through its hook, so the message says both engines keep it there, and
        keeps the old words for a target the hook does not print (a pulse's rise)."""
        hooked = [(DIVIDER + "a1 alter dev=R1 param=r value=1k\ndc1 dc param=rv start=2k stop=3k step=1k\n",
                   "dc dc1 at rv=2000: alter a1 set r of R1, which the netlist binds to the expression rv"),
                  (SRC_DC + "op0 dc\na1 alter dev=V1 param=dc value=2\na2 alter param=vv value=3\nop1 dc\n",
                   "dc op1: alter a1 set dc of V1, which the netlist binds to the expression vv"),
                  (BODY_M.replace("X1 (in out) half", "X1 (in out) half m=other") + "op0 dc\na1 alter dev=X1 param=m "
                   "value=2\na2 alter param=other value=3\nop1 dc\n",
                   "dc op1: alter a1 set m of X1, which the netlist binds to the expression other")]
        plain = [(PULSE_RISE + "op0 dc\na1 alter dev=V1 param=rise value=2n\na2 alter param=rt value=3n\ntr1 tran "
                  "stop=3u\n", "tran tr1: alter a1 set rise of V1, which the netlist binds to the expression rt")]
        for cases, words in ((hooked, G2C4_BOTH_KEEP), (plain, G1_CHANGED)):
            for deck, head in cases:
                for engine in ("vacask", "xyce"):
                    with self.subTest(deck=deck.splitlines()[-2:], engine=engine):
                        errs = self.fails(deck, engine)
                        self.assertEqual(len([e for e in errs if e.startswith(head) and words in e]), 1, errs)


# -- phase 2c6, the repair of 2c4 and 2c5: K1 G1 default-deny over every value type and alter form (a pwl wave's list, a
# source's derived field, a card's or a Verilog-A instance's other spelling, the sweeps of a bound target), K2 the taken
# branch's source in VACASK's control block (parsed decks, both engines' plans and renders, no engine) -------------------

# S-2c5's decks: N601-N606 and N427 on the small divider, N701-N705 and N8xx with a subckt and a second source
PWL_SMALL = """simulator lang=spectre
parameters vp=1 rv=1k other=1
V1 (in 0) vsource type=pwl wave=[0 0 1u vp 3u vp]
R1 (in out) resistor r=rv
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
"""
PWL_BIG = """simulator lang=spectre
parameters vp=1 rv=1k other=1 vv=1
subckt half (a b)
R1 (a b) resistor r=1k
ends half
V1 (in 0) vsource type=pwl wave=[0 0 1u vp 3u vp]
R1 (in out) resistor r=rv
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
V5 (i5 0) vsource dc=vv
X5 (i5 0) half
save in out
"""
PWL_XLINE = """simulator lang=spectre
parameters vp=1 rq=1k other=1
subckt half (a b)
parameters rr=1k
R1 (a b) resistor r=rr
ends half
V1 (in 0) vsource type=pwl wave=[0 0 1u vp 3u vp]
X1 (in out) half rr=rq
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
"""
PWL_BODYM = """simulator lang=spectre
parameters vp=1 other=1
subckt half (a b)
R1 (a b) resistor r=1k
ends half
V1 (in 0) vsource type=pwl wave=[0 0 1u vp 3u vp]
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
V5 (i5 0) vsource dc=1
X5 (i5 0) half
R3 (in 0) resistor r=other*1k
save in out
"""
WAVE_A1 = "a1 alter dev=V1 param=wave value=[0 0 1u 2 3u 2]\n"
WAVE_TR1 = "tr1 tran stop=3u\n"
WAVE_EXPR = "[0.0 0.0 1e-06 vp 3e-06 vp]"
# (name, deck, where, the change the rule-(b) message names); every one ran on VACASK with in(2u) = 1 where Xyce and the
# analytic value give 2, status 0 on both engines (S-2c5)
K1_DECKS = [
    ("N427", PWL_SMALL.replace("r=rv", "r=1k").replace(" rv=1k", "") + WAVE_A1 + "a2 alter param=other value=2\n"
     + WAVE_TR1, "tran tr1", "alter a2 changes other, "),
    ("N601", PWL_SMALL + WAVE_A1 + "a2 alter dev=R1 param=r value=3k\n" + WAVE_TR1, "tran tr1",
     "alter a2 changes r of R1, which the VACASK deck sets through a variable, "),
    ("N602", PWL_SMALL + WAVE_A1 + "a2 alter param=temp value=50\n" + WAVE_TR1, "tran tr1", "alter a2 changes temp, "),
    ("N603", PWL_SMALL + "op0 dc\n" + WAVE_A1 + "a2 alter param=other value=2\n" + WAVE_TR1, "tran tr1",
     "alter a2 changes other, "),
    ("N606", PWL_SMALL + WAVE_A1 + "dc1 dc param=other values=[1 2]\n" + WAVE_TR1, "dc dc1 at other=2",
     "dc dc1 (its sweep of other) changes other, "),
    ("N701", PWL_BIG + WAVE_A1 + "a2 alter dev=R1 param=r value=3k\n" + WAVE_TR1, "tran tr1",
     "alter a2 changes r of R1, which the VACASK deck sets through a variable, "),
    ("N702", PWL_BIG + WAVE_A1 + "dc1 dc dev=R1 param=r values=[1k 3k]\n" + WAVE_TR1, "dc dc1 at R1:r=1000",
     "dc dc1 (its sweep of R1:r) changes r of R1, which the VACASK deck sets through a variable, "),
    ("N703", PWL_BIG + WAVE_A1 + "a2 alter dev=X5.R1 param=m value=2\n" + WAVE_TR1, "tran tr1",
     "alter a2 changes m of X5.R1, which the VACASK deck sets through a variable, "),
    ("N704", PWL_BIG + WAVE_A1 + "a2 alter dev=V5 param=dc value=2\n" + WAVE_TR1, "tran tr1",
     "alter a2 changes dc of V5, which the VACASK deck sets through a variable, "),
    ("N705", PWL_BIG + "op0 dc\n" + WAVE_A1 + "a2 alter dev=R1 param=r value=3k\n" + WAVE_TR1, "tran tr1",
     "alter a2 changes r of R1, which the VACASK deck sets through a variable, "),
    ("N803", PWL_SMALL.replace("rv=1k", "rv=1k rw=2*rv").replace("r=rv", "r=rw") + "dc0 dc param=rv values=[1k 2k]\n"
     + WAVE_A1 + "a2 alter param=rw value=3k\n" + WAVE_TR1, "tran tr1",
     "alter a2 changes rw, which the VACASK deck sets through a variable, "),
    ("N804", PWL_XLINE + WAVE_A1 + "a2 alter dev=X1 param=rr value=3k\n" + WAVE_TR1, "tran tr1",
     "alter a2 changes rr of X1, which the VACASK deck sets through a variable, "),
    ("N805", PWL_BODYM + WAVE_A1 + "dc1 dc dev=X5.R1 param=m values=[1 2]\n" + WAVE_TR1, "dc dc1 at X5.R1:m=1",
     "dc dc1 (its sweep of X5.R1:m) changes m of X5.R1, which the VACASK deck sets through a variable, "),
    ("N806", PWL_BODYM + "op0 dc\na2 alter dev=X5.R1 param=m value=2\n" + WAVE_A1 + WAVE_TR1, "tran tr1",
     "alter a2 changes m of X5.R1, which the VACASK deck sets through a variable, "),
]
# N605: the alter, then a change of what the wave reads (VACASK 3 against Xyce 2)
K1_N605 = PWL_SMALL + WAVE_A1 + "a2 alter param=vp value=3\n" + WAVE_TR1
SINE_2C6 = """simulator lang=spectre
parameters vv=0.5 other=1
V1 (in 0) vsource type=sine dc=vv ampl=0.5 freq=1e6
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
"""
EXP_2C6 = """simulator lang=spectre
parameters td=1u t2=10u rv=1k other=1
V1 (in 0) vsource type=exp val0=0 val1=1 td1=%s tau1=0.5u td2=%s tau2=1u
R1 (in out) resistor r=rv
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
"""
GROUP_2C6 = """simulator lang=spectre
parameters vt=0.40 other=1 rv=1k
model nch bsim4 {
  1: type=n lmin=0.1u lmax=1u wmin=0.1u wmax=10u %s=vt toxe=2n
  2: type=n lmin=1u lmax=10u wmin=0.1u wmax=10u toxe=2n
}
Vg (g 0) vsource dc=1.2
Vd1 (d1 0) vsource dc=1.2
Vd2 (d2 0) vsource dc=1.2
M1 (d1 g 0 0) nch w=0.5u l=0.5u
M2 (d2 g 0 0) nch w=0.5u l=2u
V5 (i5 0) vsource dc=1
R5 (i5 o5) resistor r=rv
R6 (o5 0) resistor r=1k
R3 (g 0) resistor r=other*1G
save Vd1:p Vd2:p o5
"""
CARD_2C6 = """simulator lang=spectre
parameters vt=0.40 other=1 rv=1k
model nch bsim4 type=n %s=vt toxe=2n
Vg (g 0) vsource dc=1.2
Vd1 (d1 0) vsource dc=1.2
M1 (d1 g 0 0) nch w=0.5u l=0.5u
V5 (i5 0) vsource dc=1
R5 (i5 o5) resistor r=rv
R6 (o5 0) resistor r=1k
R3 (g 0) resistor r=other*1G
save Vd1:p o5
"""
DIODE_2C6 = """simulator lang=spectre
parameters pbv=0.8 isv=1e-14 other=1
I1 (0 d) isource dc=1m
D1 (d 0) dmod
model dmod diode is=isv pb=pbv cjo=1p
R3 (d 0) resistor r=other*1G
save d
"""
VA_2C6 = """simulator lang=spectre
ahdl_include "vres2c.va"
parameters rv=1k rm=1k other=1
V1 (a 0) vsource dc=1
X1 (a b) vres2c R=rv
R2 (b 0) resistor r=1k
model vm vres2c R=rm
X2 (a c) vm
R4 (c 0) resistor r=1k
R5 (a 0) resistor r=other*1k
save b c
"""
DIV_2C6 = """simulator lang=spectre
parameters rv=1k other=1 mm=1
V1 (in 0) vsource dc=1
R1 (in out) resistor r=rv
R2 (out 0) resistor r=1k m=mm
R3 (in 0) resistor r=other*1k
save in out
"""
# K2 (S-2c5's N607): V1 in both branches of an if, val1 bound to vv in the else branch only
COND_SRC_2C6 = """simulator lang=spectre
parameters sel=%s vv=2 other=1
if (sel > 0) {
V1 (in 0) vsource type=pulse val0=0 val1=1 delay=0.5u rise=0.5u fall=0.5u width=1u period=20u
} else {
V1 (in 0) vsource type=pulse val0=0 val1=vv delay=0.5u rise=0.5u fall=0.5u width=1u period=20u
}
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
a1 alter dev=V1 param=width value=2u
a2 alter param=other value=2
tr1 tran stop=4u
"""
G1_KEEP_2C6 = ("whether Spectre keeps the altered value or re-evaluates the expression is not known (VACASK and Xyce keep "
               "the alter): not supported in v1 (§5.3; alter what the expression reads instead)")


class TestFollowUp2c6(unittest.TestCase):
    """Phase 2c6, the repair of 2c4 and 2c5.  K1 G1 was not default-deny: an alter of a pwl source's wave (a list value)
    was never tracked (plan._alter passed only floats to _bound_alter, _bound_expr returned None for a list), so after
    any change VACASK re-elaborates on (a hooked alter or sweep, a variable, temp) VACASK re-evaluated the instance line's
    wave=[... vp ...] and lost the alter with status 0 (S-2c5's N427, N601-N606, N701-N705, N803-N806: in 1 where 2).  The
    audit of every value type and alter form found more of the same: a sine's sinedc that defaults to a bound dc, an exp's
    td2 the instance line derives through a hook, a card's other spelling (vth0, vtho), a Verilog-A instance's parameter in
    another case, a sweep of a bound target around a sweep of what it reads, a sweep of a field the instance line derives
    (VACASK exit 1) and of an exp's delay (both engines wrong).  K2 VACASK's control block took the untaken branch's
    source as the base of its statements (N607: `(internal)' at render)."""

    def fails(self, text, engine, files=()):
        nl = parse_text(text, files=files)
        with self.assertRaises(NoteError) as cm:
            PL.build(nl, engine, Settings())
        return errors(cm.exception)

    def builds(self, text, engine, files=()):
        return PL.build(parse_text(text, files=files), engine, Settings())

    def one(self, errs, head, *pieces):
        hits = [e for e in errs if e.startswith(head) and all(p in e for p in pieces)]
        self.assertEqual(len(hits), 1, "%s\n%s" % (head, "\n".join(errs)))
        return hits[0]

    # -- K1: the pwl wave, a list value ----------------------------------------------------------------------------
    def test_k1_an_alter_of_a_pwl_wave_is_tracked_elementwise(self):
        """S-2c5's decks: rule (b) refuses each on VACASK (status 2 before any engine runs), naming the wave's alter, its
        binding and the change; Xyce runs them.  N605 changes what the wave reads: rule (a), on both engines."""
        for name, deck, where, change in K1_DECKS:
            with self.subTest(deck=name):
                errs = self.fails(deck, "vacask")
                head = ("%s: on VACASK, alter %s set wave of V1, which the netlist binds to the expression %s (it reads "
                        "vp); " % (where, "a1", WAVE_EXPR))
                self.one(errs, head, change + "and VACASK re-evaluates", *G1_VACASK)
                self.builds(deck, "xyce")
        for engine in ("vacask", "xyce"):
            with self.subTest(deck="N605", engine=engine):
                errs = self.fails(K1_N605, engine)
                self.one(errs, "tran tr1: alter a1 set wave of V1, which the netlist binds to the expression %s (it reads "
                         "vp), and alter a2 changes vp after the alter: " % WAVE_EXPR, G1_CHANGED)

    def test_k1_the_wave_compares_point_by_point(self):
        """A wave of another length differs; one that reads a time point, an isource's, one in a body whose holder's
        parameter changes, one with a given dc (switched around the tran), one altered again after the change: refused on
        VACASK; the same values as the binding gives, the controls of S-2c5 (no later change, a constant wave, the hooked
        alter first, a constant alter after) and an alter equal to the binding's values build on both engines."""
        refused = [
            (PWL_SMALL + "a1 alter dev=V1 param=wave value=[0 0 1u 2 2u 2 3u 2]\na2 alter param=other value=2\n" + WAVE_TR1,
             "tran tr1: on VACASK, alter a1 set wave of V1", "alter a2 changes other, "),
            (PWL_SMALL.replace("vp=1", "tp=1u").replace("1u vp 3u vp", "tp 1 3u 1") + "a1 alter dev=V1 param=wave "
             "value=[0 0 2u 1 3u 1]\na2 alter param=other value=2\n" + WAVE_TR1,
             "tran tr1: on VACASK, alter a1 set wave of V1, which the netlist binds to the expression [0.0 0.0 tp 1.0 "
             "3e-06 1.0] (it reads tp)", "alter a2 changes other, "),
            ("simulator lang=spectre\nparameters vp=1m other=1\nI1 (0 in) isource type=pwl wave=[0 0 1u vp 3u vp]\n"
             "R1 (in 0) resistor r=1k\nR3 (in 0) resistor r=other*1G\nsave in\n"
             "a1 alter dev=I1 param=wave value=[0 0 1u 2m 3u 2m]\na2 alter param=other value=2\n" + WAVE_TR1,
             "tran tr1: on VACASK, alter a1 set wave of I1", "alter a2 changes other, "),
            ("simulator lang=spectre\nparameters vp=1 other=1\nsubckt cell (o)\nparameters q=1\n"
             "V1 (i 0) vsource type=pwl wave=[0 0 1u vp 3u vp]\nR1 (i o) resistor r=1k\nR2 (o 0) resistor r=q*1k\n"
             "ends cell\nX1 (out) cell q=1\nR3 (out 0) resistor r=other*1G\nsave out\n"
             "a1 alter dev=X1.V1 param=wave value=[0 0 1u 2 3u 2]\na2 alter sub=X1 param=q value=1.5\n" + WAVE_TR1,
             "tran tr1: on VACASK, alter a1 set wave of X1.V1", "alter a2 changes X1:q, "),
            (PWL_SMALL.replace("type=pwl", "type=pwl dc=0") + WAVE_A1 + "a2 alter param=other value=2\n" + WAVE_TR1,
             "tran tr1: on VACASK, alter a1 set wave of V1", "alter a2 changes other, "),
            (PWL_SMALL + WAVE_A1 + "a2 alter param=other value=2\na3 alter dev=V1 param=delay value=0\n" + WAVE_TR1,
             "tran tr1: on VACASK, alter a1 set wave of V1", "alter a2 changes other, ")]
        for deck, head, change in refused:
            with self.subTest(deck=deck.splitlines()[-4:]):
                self.one(self.fails(deck, "vacask"), head, change, *G1_VACASK)
                self.builds(deck, "xyce")
        body = ("simulator lang=spectre\nparameters vp=1 other=1\nsubckt cell (o)\nparameters q=1\n"
                "V1 (i 0) vsource type=pwl wave=[0 0 1u (vp*q) 3u (vp*q)]\nR1 (i o) resistor r=1k\nR2 (o 0) resistor "
                "r=1k\nends cell\nX1 (out) cell q=1\nR3 (out 0) resistor r=other*1G\nsave out\n"
                "a1 alter dev=X1.V1 param=wave value=[0 0 1u 2 3u 2]\na2 alter sub=X1 param=q value=1.5\n" + WAVE_TR1)
        for engine in ("vacask", "xyce"):                       # the wave reads the holder's q, which a2 changes
            with self.subTest(deck="body wave reads q", engine=engine):
                self.one(self.fails(body, engine), "tran tr1: alter a1 set wave of X1.V1, which the netlist binds to the "
                         "expression [0.0 0.0 1e-06 vp * q 3e-06 vp * q] (it reads q, vp), and alter a2 changes q after the "
                         "alter: ", G1_CHANGED)
        controls = [PWL_SMALL.replace("r=rv", "r=1k").replace(" rv=1k", "") + WAVE_A1 + WAVE_TR1,          # N428
                    PWL_SMALL.replace("1u vp 3u vp", "1u 1 3u 1") + WAVE_A1 + "a2 alter param=other value=2\n"
                    + WAVE_TR1,                                                                              # N604
                    PWL_BIG + "a2 alter dev=R1 param=r value=3k\n" + WAVE_A1 + WAVE_TR1,                      # N706
                    PWL_BIG + WAVE_A1 + "a2 alter dev=R2 param=r value=3k\n" + WAVE_TR1,                      # N707
                    PWL_SMALL + "a1 alter dev=V1 param=wave value=[0 0 1u 1 3u 1]\na2 alter param=other value=2\n"
                    + WAVE_TR1]
        for deck in controls:
            for engine in ("vacask", "xyce"):
                with self.subTest(deck=deck.splitlines()[-3:], engine=engine):
                    self.builds(deck, engine)

    # -- K1: the alter forms ---------------------------------------------------------------------------------------
    def test_k1_dev_source_fields_of_every_value_type(self):
        """A number (a pulse's rise, 2c5's A01), the list above, a field the netlist leaves to its default when that default
        is bound (a sine's sinedc, whose default is the dc vv: VACASK's instance line prints sinedc=vv), a field the
        instance line derives through another's hook (an exp's td2 relative to td1, td2 hooked, td1 a constant altered:
        the line's td2 gives 1u where the statement prints 1.5u), an enumeration (the type) and a quoted value G1
        cannot compare: tracked, or refused at the first change VACASK re-elaborates on."""
        sine_a1 = "a1 alter dev=V1 param=sinedc value=1\n"
        tr1 = "tr1 tran stop=1u\n"
        errs = self.fails(SINE_2C6 + sine_a1 + "a2 alter param=other value=2\n" + tr1, "vacask")
        self.one(errs, "tran tr1: on VACASK, alter a1 set sinedc of V1, whose default, the dc, the netlist binds to the "
                 "expression vv (it reads vv); alter a2 changes other, ", *G1_VACASK)
        self.builds(SINE_2C6 + sine_a1 + "a2 alter param=other value=2\n" + tr1, "xyce")
        for engine in ("vacask", "xyce"):
            with self.subTest(construct="sinedc, then vv", engine=engine):
                errs = self.fails(SINE_2C6 + sine_a1 + "a2 alter param=vv value=0.25\n" + tr1, engine)
                self.one(errs, "tran tr1: alter a1 set sinedc of V1, whose default, the dc, the netlist binds to the "
                         "expression vv (it reads vv), and alter a2 changes vv after the alter: ", G1_CHANGED)
            with self.subTest(construct="sinedc, no change", engine=engine):
                self.builds(SINE_2C6 + sine_a1 + tr1, engine)
        exp = EXP_2C6 % ("1u", "t2") + "a1 alter dev=V1 param=td2 value=2u\na2 alter dev=V1 param=td1 value=0.5u\n"
        errs = self.fails(exp + "a3 alter param=other value=2\ntr1 tran stop=3u\n", "vacask")
        self.one(errs, "tran tr1: on VACASK, alter a2 set td1 of V1, so the control block's statement of V1 prints "
                 "td2=1.5e-06, which its instance line binds to the expression t2 - 1e-06 (it reads t2); alter a3 changes "
                 "other, ", *G1_VACASK)
        self.builds(exp + "a3 alter param=other value=2\ntr1 tran stop=3u\n", "xyce")
        for engine in ("vacask", "xyce"):
            with self.subTest(construct="td2 hooked, td1 altered, no change", engine=engine):
                self.builds(exp + "tr1 tran stop=3u\n", engine)
            with self.subTest(construct="type", engine=engine):
                self.builds(RISE_RV.replace("type=pulse val0=0", "type=pulse val0=rt*1e6-1") + "a1 alter dev=V1 param=type "
                            "value=dc\na2 alter param=other value=2\nop1 dc\n", engine)
        errs = self.fails(RISE_RV + 'a1 alter dev=V1 param=rise value="2u"\na2 alter param=other value=2\n' + TR1, "vacask")
        self.one(errs, "tran tr1: on VACASK, alter a1 set rise of V1, which the netlist binds to the expression rt (it reads "
                 "rt); alter a2 changes other, ", "vamos cannot compare the alter's value 2u with it",
                 "not supported on VACASK in v1 (Xyce runs it)")
        # a hooked field altered, then swept through its hook (S-2c4's e12 in a body, e13): the sweep's values are the
        # field's meant values there, which the statement's constant is not (an over-refusal of this round's first cut)
        for engine in ("vacask", "xyce"):
            with self.subTest(construct="hooked field altered, then swept", engine=engine):
                self.builds("simulator lang=spectre\nparameters other=1\nsubckt cell (o)\nparameters vd=1\n"
                            "V1 (i 0) vsource dc=vd\nR1 (i o) resistor r=1k\nR2 (o 0) resistor r=1k\nends cell\n"
                            "X1 (out) cell vd=1\nR3 (out 0) resistor r=other*1G\nsave out\nop0 dc\n"
                            "a1 alter dev=X1.V1 param=dc value=2\na2 alter param=other value=2\nop1 dc\n"
                            "dc1 dc dev=X1.V1 param=dc values=[3 4]\nop2 dc\n", engine)
                self.builds("simulator lang=spectre\nparameters mm=1 other=1\nV1 (in 0) vsource dc=0 mag=mm\n"
                            "R1 (in out) resistor r=1k\nR2 (out 0) resistor r=1k\nR3 (in 0) resistor r=other*1k\nsave out\n"
                            "ac0 ac start=1k stop=1k lin=1\na1 alter dev=V1 param=mag value=4\na2 alter param=other value=2\n"
                            "ac1 ac start=1k stop=1k lin=1\nac2 ac dev=V1 param=mag values=[2 6] freq=1k\n", engine)
        # the exp's td2 hooked and swept after its constant td1 is altered and a variable changes: the line's td2,
        # (vamos_af1 ? vamos_av1 : t2) - 1e-06, gives the swept value - 1u where the statement's delay 0.5u needs - 0.5u
        errs = self.fails(exp + "a3 alter param=other value=2\nsw1 sweep dev=V1 param=td2 values=[2u 3u] {\n  tr1 tran "
                          "stop=3u\n}\n", "vacask")
        self.one(errs, "tran tr1 at V1:td2=2e-06: on VACASK, alter a2 set td1 of V1, so the control block's statement of V1 "
                 "prints td2=1.5e-06, which its instance line binds to the expression t2 - 1e-06 (it reads t2)", *G1_VACASK)

    def test_k1_dev_instance_parameters_and_m(self):
        """A Verilog-A instance's parameter written in another case (R=rv, an alter of r) was not found, so G1's rule (a)
        never saw it; it now is, on both engines, as for the same case (2c4).  The other instance forms keep their rules:
        a hooked value parameter (rule (a) both), a top-level m bound to mm (rule (a) both), a body m without one
        (hooked, VACASK keeps it), an isnoisy word (rule (b) on VACASK, 2c5's X02)."""
        d = tempfile.mkdtemp(prefix="vamos_plan_va_")
        try:
            va = os.path.join(d, "vres2c.va")
            with open(va, "w") as fh:
                fh.write(VRES2C_VA)
            for engine in ("vacask", "xyce"):
                with self.subTest(construct="Verilog-A R=rv", engine=engine):
                    errs = self.fails(VA_2C6 + "op0 dc\na1 alter dev=X1 param=r value=3k\na2 alter param=rv value=2k\n"
                                      "op1 dc\n", engine, files=[va])
                    self.one(errs, "dc op1: alter a1 set r of X1, which the netlist binds to the expression rv (it reads "
                             "rv), and alter a2 changes rv after the alter: ", G1_KEEP_2C6)
                with self.subTest(construct="Verilog-A card R=rm", engine=engine):
                    errs = self.fails(VA_2C6 + "op0 dc\na1 alter mod=vm param=r value=3k\na2 alter param=rm value=2k\n"
                                      "op1 dc\n", engine, files=[va])
                    self.one(errs, "dc op1: alter a1 set r of model vm, which the netlist binds to the expression rm (it "
                             "reads rm), and alter a2 changes rm after the alter: ")
        finally:
            shutil.rmtree(d, ignore_errors=True)
        for engine in ("vacask", "xyce"):
            with self.subTest(construct="r=rv", engine=engine):
                self.one(self.fails(DIV_2C6 + "op0 dc\na1 alter dev=R1 param=r value=3k\na2 alter param=rv value=2k\n"
                                    "op1 dc\n", engine),
                         "dc op1: alter a1 set r of R1, which the netlist binds to the expression rv (it reads rv)",
                         G1_KEEP_2C6)
            with self.subTest(construct="m=mm", engine=engine):
                self.one(self.fails(DIV_2C6 + "op0 dc\na1 alter dev=R2 param=m value=2\na2 alter param=mm value=3\n"
                                    "op1 dc\n", engine),
                         "dc op1: alter a1 set m of R2, which the netlist binds to the expression mm (it reads mm)",
                         G1_KEEP_2C6)
            with self.subTest(construct="body m", engine=engine):
                self.builds(BODY_M + "op0 dc\na1 alter dev=X1.R1 param=m value=2\na2 alter param=other value=2\nop1 dc\n",
                            engine)

    def test_k1_mod_card_parameters_under_either_spelling(self):
        """bsim4's vtho is a rename of vth0, and both are rows (the diode's pb of vj): an alter naming the other spelling
        than the card's was not tracked, so after a hooked change VACASK lost the alter of the group whose entry 2 lacks
        it (op1 Vd1:p -1.185e-4 where Xyce -6.555e-5, status 0), and rule (a) missed it on both engines."""
        tail = "op0 dc\na1 alter mod=nch param=%s value=0.6\na2 alter dev=R5 param=r value=3k\nop1 dc\n"
        for card, alter in (("vtho", "vth0"), ("vth0", "vtho"), ("vtho", "vtho")):
            with self.subTest(card=card, alter=alter):
                errs = self.fails(GROUP_2C6 % card + tail % alter, "vacask")
                self.one(errs, "dc op1: on VACASK, alter a1 set %s of model nch, which the netlist binds to the expression "
                         "vt (it reads vt); alter a2 changes r of R5, which the VACASK deck sets through a variable, "
                         % alter, *G1_VACASK)
                self.builds(GROUP_2C6 % card + tail % alter, "xyce")
        for card, alter in (("vtho", "vth0"), ("vth0", "vtho")):
            for engine in ("vacask", "xyce"):
                with self.subTest(card=card, alter=alter, engine=engine):
                    errs = self.fails(CARD_2C6 % card + "op0 dc\na1 alter mod=nch param=%s value=0.6\na2 alter param=vt "
                                      "value=0.5\nop1 dc\n" % alter, engine)
                    self.one(errs, "dc op1: alter a1 set %s of model nch, which the netlist binds to the expression vt (it "
                             "reads vt), and alter a2 changes vt after the alter: " % alter, G1_KEEP_2C6)
        for engine in ("vacask", "xyce"):
            with self.subTest(card="pb", alter="vj", engine=engine):
                errs = self.fails(DIODE_2C6 + "op0 dc\na1 alter mod=dmod param=vj value=0.9\na2 alter param=pbv value=0.7\n"
                                  "ac1 ac start=1k stop=1k lin=1\n", engine)
                self.one(errs, "ac ac1: alter a1 set vj of model dmod, which the netlist binds to the expression pbv (it "
                         "reads pbv), and alter a2 changes pbv after the alter: ", G1_KEEP_2C6)

    def test_k1_sub_and_param_targets_keep_their_rules(self):
        """sub= an X-line binding (hooked: rule (a) on both engines), a subckt default the X line leaves and a netlist
        parameter defined by an expression (2c4's H2: rule (a), both engines keep the alter), and an alter of each with
        no later change of what it reads, which builds on both."""
        for engine in ("vacask", "xyce"):
            with self.subTest(construct="X-line binding", engine=engine):
                self.one(self.fails(XLINE + "op0 dc\na1 alter sub=X1 param=rr value=3k\na2 alter param=rv value=2k\n"
                                    "op1 dc\n", engine),
                         "dc op1: alter a1 set rr of X1, which the netlist binds to the expression rv (it reads rv)",
                         G1_KEEP_2C6)
                self.builds(XLINE + "op0 dc\na1 alter sub=X1 param=rr value=3k\na2 alter param=other value=2\nop1 dc\n",
                            engine)
            with self.subTest(construct="subckt default", engine=engine):
                self.one(self.fails(SUB_DEFAULT % ("q*1k", "") + "op0 dc\na1 alter sub=X1 param=rr value=3k\n"
                                    "a2 alter sub=X1 param=q value=2\nop1 dc\n", engine),
                         "dc op1: alter a1 set rr of X1, which subckt half binds to the default q * 1000.0 (it reads q)",
                         G1_KEEP_2C6)
            with self.subTest(construct="parameter", engine=engine):
                self.one(self.fails(EXPR_PARAM % "rw" + "op0 dc\na1 alter param=rw value=3k\na2 alter param=rv value=2k\n"
                                    "op1 dc\n", engine),
                         "dc op1: alter a1 set rw, which the netlist binds to the expression 2.0 * rv (it reads rv)",
                         G1_KEEP_2C6)
                self.builds(EXPR_PARAM % "rw" + "op0 dc\na1 alter param=rw value=3k\na2 alter param=other value=2\n"
                            "op1 dc\n", engine)

    # -- K1: the sweeps --------------------------------------------------------------------------------------------
    def test_k1_a_sweep_of_a_bound_target(self):
        """Rule (a) for a sweep: a target bound to an expression swept around a sweep of what it reads (R1's r=rv around rv,
        an X-line rr=rv around rv, a source's dc=vv around vv, a resistor reading temp around temp): VACASK kept the swept
        value, Xyce re-evaluated the expression at some points (out 0.333 where 0.5; in 2 where 1; out 0.333 at both
        points), status 0 on both: status 2 on both now; a sweep of rv around a sweep of r, or of something r does not
        read, builds.  A sweep of a field the VACASK instance line derives from a bound one (sinedc from dc=vv; an exp's
        delay from td1=td) stopped VACASK (exit 1, `is bound to an expression'): refused there.  An exp's delay is folded
        into its td1 and td2, so a sweep of it set VACASK's absolute td1 and nothing on Xyce: refused on both."""
        div = DIV_2C6.replace(" m=mm", "")
        words = ("whether Spectre keeps the swept value or re-evaluates the expression is not known (VACASK keeps it, Xyce "
                 "re-evaluates the expression at some points): not supported in v1 (§5.3; sweep what the expression reads "
                 "instead)")
        nests = [(div + "sw1 sweep dev=R1 param=r values=[1k 3k] {\n  dc1 dc param=rv values=[1k 2k]\n}\n",
                  "dc dc1 at R1:r=1000, rv=2000: sweep sw1 sets r of R1, which the netlist binds to the expression rv (it "
                  "reads rv), and dc dc1 (its sweep of rv) changes rv inside it: "),
                 (XLINE + "sw1 sweep sub=X1 param=rr values=[1k 3k] {\n  dc1 dc param=rv values=[1k 2k]\n}\n",
                  "dc dc1 at X1:rr=1000, rv=2000: sweep sw1 sets rr of X1, which the netlist binds to the expression rv (it "
                  "reads rv), and dc dc1 (its sweep of rv) changes rv inside it: "),
                 (SRC_DC + "sw1 sweep dev=V1 param=dc values=[1 3] {\n  dc1 dc param=vv values=[1 2]\n}\n",
                  "dc dc1 at V1:dc=1, vv=2: sweep sw1 sets dc of V1, which the netlist binds to the expression vv (it reads "
                  "vv), and dc dc1 (its sweep of vv) changes vv inside it: "),
                 (TEMP_R.replace("r=rv*", "r=1k*") + "sw1 sweep dev=R1 param=r values=[1k 3k] {\n  dc1 dc param=temp "
                  "values=[27 127]\n}\n", "dc dc1 at R1:r=1000, temp=127: sweep sw1 sets r of R1, which the netlist binds to "
                  "the expression 1000.0 * (1.0 + 0.001 * (temper - 27.0)) (it reads temp), and dc dc1 (its sweep of "
                  "temp) changes temp inside it: ")]
        for engine in ("vacask", "xyce"):
            for deck, head in nests:
                with self.subTest(construct=head[:50], engine=engine):
                    self.one(self.fails(deck, engine), head, words)
            with self.subTest(construct="rv around r, other inside r", engine=engine):
                self.builds(div + "sw1 sweep param=rv values=[1k 2k] {\n  dc1 dc dev=R1 param=r values=[1k 3k]\n}\n", engine)
                self.builds(div + "sw1 sweep dev=R1 param=r values=[1k 3k] {\n  dc1 dc param=other values=[1 2]\n}\n",
                            engine)
            with self.subTest(construct="exp delay", engine=engine):
                for td1 in ("td", "1u"):
                    errs = self.fails(EXP_2C6 % (td1, "10u") + "sw1 sweep dev=V1 param=delay values=[0 0.5u] {\n"
                                      "  tr1 tran stop=3u\n}\n", engine)
                    self.one(errs, "sweep sw1: the delay of the exp source V1 is folded into its td1 and td2 (§3.8.1)",
                             "not supported in v1")
        errs = self.fails(SINE_2C6 + "sw1 sweep dev=V1 param=sinedc values=[0.25 1] {\n  tr1 tran stop=1u\n}\n", "vacask")
        self.one(errs, "sweep sw1: on VACASK sinedc of V1 is printed on its instance line as sinedc=vv, which reads vv: "
                 "VACASK cannot sweep such a parameter ('is bound to an expression')")
        self.builds(SINE_2C6 + "sw1 sweep dev=V1 param=sinedc values=[0.25 1] {\n  tr1 tran stop=1u\n}\n", "xyce")

    # -- K2 --------------------------------------------------------------------------------------------------------
    def test_k2_vacask_s_control_block_takes_the_taken_branch_s_source(self):
        """N607: V1 in both branches of an if, the else taken (val1=vv), then an alter of its width: plan.build's R4 check
        took the taken V1 and passed, the render took vacask._resolve's first match (the if branch, val1=1) and refused
        with an `(internal)' message.  Each branch taken in turn now prints the taken V1's statement: the else's leaves
        val1=vv out (the instance keeps it), the if's prints val1=1.0; Xyce's plan is unchanged."""
        for sel, line in (("0", '  alter instance("V1") type="pulse" val0=0.0 delay=5e-07 rise=5e-07 fall=5e-07 '
                                'width=2e-06 period=2e-05\n'),
                          ("1", '  alter instance("V1") type="pulse" val0=0.0 val1=1.0 delay=5e-07 rise=5e-07 fall=5e-07 '
                                'width=2e-06 period=2e-05\n')):
            with self.subTest(sel=sel):
                self.assertIn(line, vacask_deck(COND_SRC_2C6 % sel))
                self.builds(COND_SRC_2C6 % sel, "xyce")


# -- phase 2c7: L1 the branch the run takes, with the plan's variables, at each statement; L2 §3.7's nominal structure
# is the netlist's own; L3 a hooked sweep's value before its flag; L4 the source constraints at every state the plan
# walks (parsed decks, both engines' plans and renders, no engine) -----------------------------------------------------

# V1 in both branches of an if (the else's val1 reads vv); %s: the condition, then the statements before a1
COND_2C7 = """simulator lang=spectre
parameters sel=0 vv=2 other=1
if (%s) {
V1 (in 0) vsource type=pulse val0=0 val1=1 delay=0.5u rise=0.5u fall=0.5u width=1u period=20u
} else {
V1 (in 0) vsource type=pulse val0=0 val1=vv delay=0.5u rise=0.5u fall=0.5u width=1u period=20u
}
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
%sa1 alter dev=V1 param=width value=2u
a2 alter param=other value=2
tr1 tran stop=4u
"""
# the control block's statement of the taken V1: the else's leaves val1=vv out (its instance keeps it), the if's prints 1
ELSE_LINE_2C7 = '  alter instance("V1") type="pulse" val0=0.0 delay=5e-07 rise=5e-07 fall=5e-07 width=2e-06 period=2e-05\n'
IF_LINE_2C7 = ('  alter instance("V1") type="pulse" val0=0.0 val1=1.0 delay=5e-07 rise=5e-07 fall=5e-07 width=2e-06 '
               'period=2e-05\n')
# S-2c6's K2b4 (the condition reads other, which a2 alters), K2b1 (sel swept by a dc that keeps the else), K2b2 (sel
# altered to -1), K2b3 (sel=1, altered to 2: the if kept), and the if kept by the condition on other
L1_DECKS_2C7 = [
    ("K2b4", COND_2C7 % ("other > 5", ""), ELSE_LINE_2C7),
    ("K2b1", COND_2C7 % ("sel > 0", "dc0 dc param=sel values=[-1 -0.5]\n"), ELSE_LINE_2C7),
    ("K2b2", COND_2C7 % ("sel > 0", "a0 alter param=sel value=-1\n"), ELSE_LINE_2C7),
    ("K2b3", (COND_2C7 % ("sel > 0", "a0 alter param=sel value=2\n")).replace("sel=0", "sel=1"), IF_LINE_2C7),
    ("if on other", COND_2C7 % ("other > 0.5", ""), IF_LINE_2C7)]
# X1 of two subckts in an if: the path X1.Z1 names a resistor in cella, an inductor in cellb (sel=0 takes cellb)
KINDS_2C7 = """simulator lang=spectre
parameters sel=%s other=1
subckt cella (a b)
Z1 (a b) resistor r=1k
ends cella
subckt cellb (a b)
Z1 (a b) inductor l=1u
ends cellb
V1 (in 0) vsource dc=1
if (sel > 0) {
X1 (in out) cella
} else {
X1 (in out) cellb
}
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out X1.Z1:1
%sop1 dc
"""
# X1 in both branches of an if, each X line with its own m (%s, %s); an alter of the body R1's m
XM_2C7 = """simulator lang=spectre
parameters sel=%s other=1
subckt half (a b)
R1 (a b) resistor r=1k
ends half
V1 (in 0) vsource dc=1
if (sel > 0) {
X1 (in out) half%s
} else {
X1 (in out) half%s
}
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out
op0 dc
a1 alter dev=X1.R1 param=m value=2
op1 dc
"""
# X1 of two subckts: L1 coupled by K1 in cella, alone (with m=2) in cellb (sel=0 takes cellb); an alter of X1.L1's l
COUPLED_2C7 = """simulator lang=spectre
parameters sel=%s other=1
subckt cella (a b)
L1 (a b) inductor l=1m
L2 (b c) inductor l=1m
K1 mutual_inductor coupling=0.5 ind1=L1 ind2=L2
R9 (b 0) resistor r=1k
R10 (c 0) resistor r=1k
ends cella
subckt cellb (a b)
L1 (a b) inductor l=1m m=2
R9 (b 0) resistor r=1k
ends cellb
V1 (in 0) vsource dc=0 mag=1
if (sel > 0) {
X1 (in out) cella
} else {
X1 (in out) cellb
}
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out
ac0 ac start=1k stop=1k lin=1
a1 alter dev=X1.L1 param=l value=2m
ac1 ac start=1k stop=1k lin=1
"""
# S-2c6's H05h: constant fields in both branches; sel altered to 1 before the first analysis (Spectre's if branch would
# be taken, in(2u) = 1; both engines gave 2, the alter's source state taken from the untaken else)
H05H_2C7 = """simulator lang=spectre
parameters sel=0 other=1
if (sel > 0) {
V1 (in 0) vsource type=pulse val0=0 val1=1 delay=0.5u rise=0.5u fall=0.5u width=1u period=20u
} else {
V1 (in 0) vsource type=pulse val0=0 val1=2 delay=0.5u rise=0.5u fall=0.5u width=1u period=20u
}
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
a0 alter param=sel value=1
a1 alter dev=V1 param=width value=2u
tr1 tran stop=3u
"""
STRUCT_2C7 = "vamos refuses a sweep or alter that changes an if branch, as Spectre does (§3.7, §14 q.13)"
# R8 in both branches of an if reading temp, a subckt whose if reads its s
TEMP_COND_2C7 = """simulator lang=spectre
parameters other=1
V1 (in 0) vsource dc=1
if (temp > 40) {
R8 (in o8) resistor r=1k
} else {
R8 (in o8) resistor r=3k
}
R9 (o8 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save o8
"""
BODY_COND_2C7 = """simulator lang=spectre
parameters other=1
subckt cellc (o)
parameters s=0
V1 (i 0) vsource dc=1
if (s > 0) {
R1 (i o) resistor r=1k
} else {
R1 (i o) resistor r=3k
}
R2 (o 0) resistor r=1k
ends cellc
X1 (o1) cellc
R3 (o1 0) resistor r=other*1G
save o1
"""
# S-2c6's S04, E06, E08 (a hooked dev= sweep of a bound td2 or freq) and R0224 (td2 swept inside a sweep of t2)
EXP_TD2_2C7 = """simulator lang=spectre
parameters t2=2u other=1
V1 (in 0) vsource type=exp val0=0 val1=1 td1=0.5u tau1=2u td2=t2 tau2=2u
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
"""
SINE_FR_2C7 = """simulator lang=spectre
parameters fr=100k other=1
V1 (in 0) vsource type=sine sinedc=0 ampl=0.5 freq=fr
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
"""
SW_TD2_2C7 = "sw1 sweep dev=V1 param=td2 values=[1.5u 2.5u] {\n  tr1 tran stop=3u\n}\n"
R0224_2C7 = """simulator lang=spectre
parameters rv=1k other=1 td=0.5u tau=2u t2=2u
I1 (0 in) isource type=exp val0=0 val1=0.001 td1=td tau1=tau td2=t2 tau2=tau
R0 (in 0) resistor r=1k
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
sw1 sweep param=t2 values=[2 3] {
  dc1 dc dev=I1 param=td2 values=[2.5e-06 3.75e-06]
}
a1 alter dev=I1 param=tau1 value=3e-06
a2 alter dev=R2 param=r value=500
dc2 dc dev=R1 param=r values=[1000 3000]
tr1 tran stop=3u
"""
# L4: a pulse whose width, rise and fall read wd and rt (period 20u); a sine whose freq reads fr; an exp whose td2
# reads t2; pwls whose time points read tp and t0
PULSE_WD_2C7 = """simulator lang=spectre
parameters wd=1u rt=0.5u other=1
V1 (in 0) vsource type=pulse val0=0 val1=1 delay=0.5u rise=rt fall=rt width=wd period=20u%s
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
"""
EXP_T2_2C7 = """simulator lang=spectre
parameters t2=2u other=1
V1 (in 0) vsource type=exp val0=0 val1=1 td1=0.5u tau1=1u td2=t2 tau2=1u
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
"""
PWL_TP_2C7 = """simulator lang=spectre
parameters tp=1u t0=0 other=1
V1 (in 0) vsource type=pwl wave=[t0 0 tp 1 3u 1]
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
"""
TR_2C7 = "tr1 tran stop=3u\n"
VACASK_ONLY_2C7 = "not supported on VACASK in v1 (Xyce runs it)"
PERIOD_2C7 = ("an explicit period no longer than rise+fall+width is an error (§3.8.1; VACASK stops at its setup with "
              "'Period of pulse transient must be greater than rise+fall+width')")


class TestFollowUp2c7(unittest.TestCase):
    """Phase 2c7.  L1 (S-2c6's K2 refuted): vacask._Deck.run_instance resolved the taken branch on the render copy, whose
    values hold no plan variable, so an if whose condition read an altered or swept netlist parameter fell back to
    _resolve's first match, the untaken branch (K2b1, K2b2, K2b4: an `(internal)' refusal); the current saves and the X
    lines' m and coupled-inductor lookups took the first match too.  L2 (H05h): §3.7's nominal structure was the first
    state plan.build walked, so an alter of a condition's parameter before the first analysis was not refused and the
    source alter's state came from the untaken branch (in 2 where 1 on both engines).  L3 (S04, E06, E08, R0224): a
    hooked sweep set its flag while its value was still 0, and VACASK's setup refused td2 or freq at 0 (exit 1).  L4
    (R0080): a state that breaks a source constraint VACASK checks at its setup (a pulse's width and edges longer than
    its period) reached VACASK, which stopped with exit 1, while Xyce ran it."""

    def fails(self, text, engine):
        nl = parse_text(text)
        with self.assertRaises(NoteError) as cm:
            PL.build(nl, engine, Settings())
        return errors(cm.exception)

    def builds(self, text, engine):
        return PL.build(parse_text(text), engine, Settings())

    def one(self, errs, head, *pieces):
        hits = [e for e in errs if e.startswith(head) and all(p in e for p in pieces)]
        self.assertEqual(len(hits), 1, "%s\n%s" % (head, "\n".join(errs)))
        return hits[0]

    def render_fails(self, text):
        with self.assertRaises(NoteError) as cm:
            vacask_deck(text)
        return errors(cm.exception)

    # -- L1 ---------------------------------------------------------------------------------------------------------
    def test_l1_the_control_block_takes_the_branch_the_run_takes_with_the_plan_s_variables(self):
        """K2b4, K2b1, K2b2: the condition reads a netlist parameter the deck alters or sweeps (a plan variable, absent from
        the render copy's values), the else kept: refused with N607's `(internal)' message before, as the render took the
        if's V1; now the else's statement (val1=vv left out).  K2b3 and an if kept by a condition on other print the if's
        val1=1.0; Xyce's plan builds."""
        for name, deck, line in L1_DECKS_2C7:
            with self.subTest(deck=name):
                self.assertIn(line, vacask_deck(deck))
                self.builds(deck, "xyce")

    def test_l1_a_failure_to_resolve_the_run_s_branch_is_reported(self):
        """The render resolves the taken branch at each statement's state; a render copy whose paths do not resolve there
        is an error naming it, never _resolve's first match (2c6 swallowed the EvalError and printed the untaken V1)."""
        from vamos.netlist import vacask
        nl = parse_text(COND_2C7 % ("sel > 0", ""))
        plan = PL.build(nl, "vacask", Settings())
        nl2, sm, _notes = SG.resolve(nl, plan)
        del nl2.values["sel"]                     # sel is no plan variable: the copy's condition no longer evaluates
        next(p for p in nl2.body if isinstance(p, Param) and p.name == "sel").expr = P("nosuch*2")
        with self.assertRaises(NoteError) as cm:
            vacask.render(nl2, plan=plan, sigmap=sm)
        errs = errors(cm.exception)
        self.assertTrue(any("the instance paths the run takes do not resolve" in e and "sel" in e for e in errs), errs)
        self.assertFalse(any("would print val1" in e for e in errs), errs)

    def test_l1_a_current_save_names_the_kind_on_the_taken_branch(self):
        """An if and its else hold the same name with different kinds (X1.Z1, a resistor in cella, an inductor in cellb):
        the current save of X1.Z1 took the first match's resistor on both engines (VACASK `Output variable 'i' of instance
        'X1:Z1' not found', Xyce `undefined symbol in .PRINT command: device X1:RZ1' where cellb is taken); now each engine
        prints the taken kind's current: VACASK i('X1:Z1') and its flow(br) column, Xyce I(X1:LZ1)."""
        from vamos.netlist import vacask
        from vamos.spectre import run_xyce
        cases = [("0", "", "i('X1:Z1')", "X1:Z1:flow(br)", "I(X1:LZ1)"),
                 ("1", "", "p('X1:Z1', i)", "X1:Z1.i", "I(X1:RZ1)"),
                 ("0", "a0 alter param=sel value=-1\n", "i('X1:Z1')", "X1:Z1:flow(br)", "I(X1:LZ1)")]
        for sel, pre, directive, column, xcol in cases:
            deck = KINDS_2C7 % (sel, pre)
            with self.subTest(sel=sel, pre=pre):
                nl = parse_text(deck)
                plan = PL.build(nl, "vacask", Settings())
                nl2, sm, _notes = SG.resolve(nl, plan)
                text = vacask.render(nl2, plan=plan, sigmap=sm)
                save = [ln for ln in text.splitlines() if ln.startswith("  save ")]
                self.assertEqual(save, ["  save v(out) " + directive], text)
                names = vacask.names_for(nl2, plan, sm)
                self.assertIn(column, [c for m in names.values() for c in m.values()], names)
                nl = parse_text(deck)
                plan = PL.build(nl, "xyce", Settings())
                nl2, sm, _notes = SG.resolve(nl, plan)
                decks, _names = run_xyce.render_decks(nl2, plan, sm)
                self.assertIn(xcol, decks[-1].text)
        # the same name with two kinds in the if and its else of one body, which spectre.parse refuses (ref19 p.491 asks
        # for one primitive) and a hand-made IR may hold: the else's Z2 renamed Z1 after the parse
        direct = ("simulator lang=spectre\nparameters sel=%s other=1\nV1 (in 0) vsource dc=1\nif (sel > 0) {\n"
                  "Z1 (in out) resistor r=1k\n} else {\nZ2 (in out) inductor l=1u\n}\nR2 (out 0) resistor r=1k\n"
                  "R3 (in 0) resistor r=other*1k\nsave out Z1:1\nop1 dc\n")
        for sel, directive, column, xcol in (("0", "i(Z1)", "Z1:flow(br)", "I(LZ1)"), ("1", "p(Z1, i)", "Z1.i", "I(RZ1)")):
            with self.subTest(direct=sel):
                for engine in ("vacask", "xyce"):
                    nl = parse_text(direct % sel)
                    cond = next(it for it in nl.body if isinstance(it, Cond))
                    cond.default[0].name = "Z1"
                    plan = PL.build(nl, engine, Settings())
                    nl2, sm, _notes = SG.resolve(nl, plan)
                    if engine == "vacask":
                        text = vacask.render(nl2, plan=plan, sigmap=sm)
                        self.assertIn("  save v(out) %s\n" % directive, text)
                        self.assertIn(column, [c for m in vacask.names_for(nl2, plan, sm).values() for c in m.values()])
                    else:
                        self.assertIn(xcol, run_xyce.render_decks(nl2, plan, sm)[0][-1].text)

    def test_l1_the_x_lines_m_and_a_coupled_inductor_on_the_taken_branch(self):
        """X lines in the two branches carry different m: an alter of X1.R1's m is refused on VACASK where the taken holder
        writes m (its $mfactor carries it), the message naming the taken X line's m (m=3.0 with the else taken; 2c6 named
        the first match's m=2.0); with m on one X line only it prints through the body's hook and builds.  X1.L1 coupled in
        cella, alone in cellb: an alter of its l was refused on VACASK for the first match's fold where cellb is taken; it
        prints alter instance("X1:L1") l=0.002 now, and stays refused where cella is taken.  Xyce's plans build."""
        for sel, want in (("0", "m=3.0"), ("1", "m=2.0")):
            with self.subTest(sel=sel, m="both"):
                errs = self.render_fails(XM_2C7 % (sel, " m=2", " m=3"))
                self.assertTrue(any(e.startswith("X1.R1: VACASK sets the device's $mfactor, which also carries the "
                                                 "multiplier of the enclosing instance X1 (%s)" % want) for e in errs), errs)
                self.builds(XM_2C7 % (sel, " m=2", " m=3"), "xyce")
        for sel in ("0", "1"):
            with self.subTest(sel=sel, m="else only"):
                self.assertIn("$mfactor=($mfactor*(vamos_hf1 ? vamos_hv1 : 1.0))", vacask_deck(XM_2C7 % (sel, "", " m=3")))
                self.builds(XM_2C7 % (sel, "", " m=3"), "xyce")
        self.assertIn('  alter instance("X1:L1") l=0.002\n', vacask_deck(COUPLED_2C7 % "0"))
        errs = self.render_fails(COUPLED_2C7 % "1")
        self.assertTrue(any(e.startswith("X1.L1: a coupled inductor's multiplier and TCs are folded into its l on VACASK")
                            for e in errs), errs)
        for sel in ("0", "1"):
            self.builds(COUPLED_2C7 % sel, "xyce")

    # -- L2 ---------------------------------------------------------------------------------------------------------
    def test_l2_an_alter_before_the_first_analysis_that_changes_a_branch_or_a_bin_is_refused(self):
        """§3.7's nominal structure is the netlist's own state, before any control statement: an alter of what a
        condition reads that changes its branch before the first analysis is refused on both engines, naming the alter,
        the condition and the branches (H05h, S-2c6's H05 with val1=vv, temp, a subckt's s); so is an alter of an
        instance's l that changes the bin it selects.  2c6 recorded the first walk as the nominal one.  Phase 2c8 (M4)
        re-decided the condition's text to the netlist's spelling: sel > 0 and s > 0 (2c7 printed sel > 0.0, s > 0.0)."""
        for engine in ("vacask", "xyce"):
            with self.subTest(deck="H05h", engine=engine):
                self.one(self.fails(H05H_2C7, engine), "alter a0 changes a structural condition on path top level: the "
                         "condition sel > 0 takes its if branch, where it takes its else branch at the netlist's own "
                         "values: ", STRUCT_2C7)
            with self.subTest(deck="H05", engine=engine):
                h05 = (COND_2C7 % ("sel > 0", "a0 alter param=sel value=1\n"))
                self.one(self.fails(h05, engine), "alter a0 changes a structural condition on path top level: the "
                         "condition sel > 0 takes its if branch, where it takes its else branch at the netlist's own "
                         "values: ", STRUCT_2C7)
            with self.subTest(deck="temp", engine=engine):
                self.one(self.fails(TEMP_COND_2C7 + "a0 alter param=temp value=50\nop1 dc\n", engine),
                         "alter a0 changes a structural condition on path top level: the condition ", "takes its if "
                         "branch, where it takes its else branch at the netlist's own values: ", STRUCT_2C7)
            with self.subTest(deck="body", engine=engine):
                self.one(self.fails(BODY_COND_2C7 + "a0 alter sub=X1 param=s value=1\nop1 dc\n", engine),
                         "alter a0 changes a structural condition on path X1: the condition s > 0 takes its if branch, "
                         "where it takes its else branch at the netlist's own values: ", STRUCT_2C7)
            with self.subTest(deck="bin", engine=engine):
                errs = self.fails(GROUP_2C6 % "vtho" + "a0 alter dev=M1 param=l value=2u\nop1 dc\n", engine)
                self.assertTrue(any(e.startswith("alter a0 changes the bin instance M1 on path top level selects (nch.2, "
                                                 "nominal nch.1)") for e in errs), errs)

    def test_l2_alters_that_keep_the_branches_build_with_today_s_plans(self):
        """The controls: an alter of the condition's parameter that keeps its branch (K2b2, K2b3, temp 30 under 40), of
        another parameter first, of a resistor in a branch, an op before a change (refused, as before), each on both engines;
        the alter's source state is the taken V1's (the if's val1=1.0 in K2b3)."""
        keep = [COND_2C7 % ("sel > 0", "a0 alter param=sel value=-1\n"),
                (COND_2C7 % ("sel > 0", "a0 alter param=sel value=2\n")).replace("sel=0", "sel=1"),
                COND_2C7 % ("sel > 0", "a0 alter param=other value=3\n"),
                TEMP_COND_2C7 + "a0 alter param=temp value=30\nop1 dc\n",
                TEMP_COND_2C7 + "a0 alter dev=R8 param=r value=2k\nop1 dc\n",
                BODY_COND_2C7 + "a0 alter sub=X1 param=s value=-1\nop1 dc\n"]
        for engine in ("vacask", "xyce"):
            for deck in keep:
                with self.subTest(deck=deck.splitlines()[-4:], engine=engine):
                    self.builds(deck, engine)
            with self.subTest(deck="op first", engine=engine):
                self.one(self.fails(TEMP_COND_2C7 + "op0 dc\na0 alter param=temp value=50\nop1 dc\n", engine),
                         "alter a0 changes a structural condition on path top level: ", STRUCT_2C7)
        self.assertIn(IF_LINE_2C7, vacask_deck(keep[1]))

    # -- L3 ---------------------------------------------------------------------------------------------------------
    def test_l3_a_hooked_sweep_sets_its_value_variable_before_its_flag(self):
        """The control block set vamos_af<k>=1 before a hooked sweep while vamos_av<k> was still 0, and VACASK's setup of the
        swept field at 0 stopped it (`Parameter td2 of exponential transient must be greater than 0', `Frequency of
        sinusoidal transient must be greater than 0'): the flag's var statement now also sets the value variable to the
        sweep's first point.  A hook whose flag an alter set is not printed again; Xyce refuses a sweep of td2 at render
        (its deck prints a derived value for it) and runs the sine."""
        cases = [("S04", EXP_TD2_2C7 + SW_TD2_2C7, "1.5e-06", 'variable="vamos_av1" values=[1.5e-06, 2.5e-06]'),
                 ("E06", SINE_FR_2C7 + "sw1 sweep dev=V1 param=freq values=[100k 200k] {\n  tr1 tran stop=3u\n}\n",
                  "100000.0", 'variable="vamos_av1" values=[100000.0, 200000.0]'),
                 ("E08", EXP_TD2_2C7 + "dc1 dc dev=V1 param=td2 values=[1.5u 2.5u]\ntr1 tran stop=3u\n", "1.5e-06",
                  'variable="vamos_av1" values=[1.5e-06, 2.5e-06]'),
                 ("R0224", R0224_2C7, "2.5e-06", 'variable="vamos_v_t2" values=[2.0, 3.0]')]
        for name, deck, first, sweep in cases:
            with self.subTest(deck=name):
                text = vacask_deck(deck)
                self.assertIn("  var vamos_af1=1 vamos_av1=%s\n  sweep vamos_w1 %s" % (first, sweep), text)
                self.assertNotIn("  var vamos_af1=1\n", text)
        text = vacask_deck(EXP_TD2_2C7 + "a0 alter dev=V1 param=td2 value=1.8u\n" + SW_TD2_2C7)
        self.assertIn('  var vamos_af1=1 vamos_av1=1.8e-06\n  alter instance("V1") type="exp"', text)
        self.assertEqual(text.count("  var vamos_af1=1"), 1, text)       # the alter's: the sweep keeps its flag on
        self.assertNotIn("vamos_av1=1.5e-06", text)
        with self.assertRaises(NoteError) as cm:
            xyce_decks(EXP_TD2_2C7 + SW_TD2_2C7)
        self.assertTrue(any("the vsource field td2 cannot be swept on Xyce in v1" in e for e in errors(cm.exception)))
        self.assertEqual(len(xyce_decks(cases[1][1])), 1)

    # -- L4 ---------------------------------------------------------------------------------------------------------
    def test_l4_a_state_that_breaks_a_source_constraint_is_refused(self):
        """The constraints §3.8.1 states, which VACASK's setup checks too, on both engines in every state a tran runs in (an
        alter, a param= or dev= sweep point): a pulse's period no longer than its rise+fall+width (S-2c6's R0080: width=wd
        altered to 2 s under period=20u), a negative rise, pwl time points that do not increase, an exp whose td2 is not
        later than its td1.  The ones VACASK alone checks (a sine's freq of 0; phase 2c8 re-decided P06, a negative freq,
        and P10, a pwl's first time point before 0, to both engines, as vamos refuses the constants on both), and any of
        them at VACASK's first elaboration (an alter instance after a var made the source invalid): on VACASK only, Xyce
        running them.  Phase 2c8 re-decided P04, an op whose dc is derived, to both engines: both read the waveform's
        value at t = 0."""
        r0080 = (PULSE_WD_2C7 % " dc=0.5" + "a1 alter param=rt value=2.5e-07\na2 alter param=wd value=2\n"
                 "sw1 sweep param=other values=[1 2] {\n  tr1 tran stop=3u\n}\na3 alter dev=R2 param=r value=2000\n"
                 "a4 alter param=other value=3\ndc1 dc dev=R3 param=r values=[1000 2000]\ntr2 tran stop=3u\n")
        both = [("R0080", r0080, "tran tr1 at other=1 (sweep sw1; after alter a2): the pulse of V1 has period=2e-05 s, not "
                 "longer than rise+fall+width = 2.0000005 s: ", PERIOD_2C7),
                ("P01", PULSE_WD_2C7 % "" + "a1 alter param=wd value=30u\n" + TR_2C7, "tran tr1 (after alter a1): the pulse "
                 "of V1 has period=2e-05 s, not longer than rise+fall+width = 3.1e-05 s: ", PERIOD_2C7),
                ("P02", PULSE_WD_2C7.replace("width=wd", "width=1u") % "" + "sw1 sweep dev=V1 param=width values=[1u 25u] "
                 "{\n  tr1 tran stop=3u\n}\n", "tran tr1 at V1:width=2.5e-05 (sweep sw1): the pulse of V1 has period=2e-05 "
                 "s, not longer than rise+fall+width = 2.6e-05 s: ", PERIOD_2C7),
                ("P11", PULSE_WD_2C7 % "" + "sw1 sweep param=wd values=[1u 25u] {\n  tr1 tran stop=3u\n}\n",
                 "tran tr1 at wd=2.5e-05 (sweep sw1): the pulse of V1 has period=2e-05 s, not longer than rise+fall+width = "
                 "2.6e-05 s: ", PERIOD_2C7),
                ("P08", PULSE_WD_2C7 % "" + "a1 alter param=rt value=-0.1u\n" + TR_2C7, "tran tr1 (after alter a1): the "
                 "pulse of V1 has rise=-1e-07 s: ", "a negative rise is an error (§3.8.1; VACASK stops at its setup with "
                 "'Rise time of pulse transient must be grater than 0')"),
                ("P09", PWL_TP_2C7 + "a1 alter param=tp value=4u\n" + TR_2C7, "tran tr1 (after alter a1): the pwl of V1 "
                 "has time points that do not increase (4e-06 s, then 3e-06 s): ", "pwl time points must increase (§3.8.1; "
                 "VACASK stops at its setup with 'Pwl scale is not strictly monotonic')"),
                ("P07", EXP_T2_2C7 + "a1 alter param=t2 value=0.25u\n" + TR_2C7, "tran tr1 (after alter a1): the exp of V1 "
                 "has td2=2.5e-07 s, not later than its td1=5e-07 s: ", "an exp whose td2 is not later than its td1 is an "
                 "error in v1"),
                ("P13", SINE_FR_2C7.replace("freq=fr", "freq=fr damp=1e5") + "a1 alter param=fr value=0\n" + TR_2C7,
                 "tran tr1 (after alter a1): the sine of V1 has freq=0 and damp=100000: ", "a sine with freq=0 and damp != 0 "
                 "is not a constant: give the frequency (§3.8.1"),
                # phase 2c8 (M4, M1) re-decided P06 and P10 from VACASK only: vamos refuses a constant negative freq and a
                # constant pwl time point before 0 on both engines, so it refuses them in every state on both
                ("P06", SINE_FR_2C7 + "a1 alter param=fr value=-100k\n" + TR_2C7, "tran tr1 (after alter a1): the sine "
                 "of V1 has freq=-100000 Hz: ", FREQ_2C8),
                ("P10", PWL_TP_2C7 + "a1 alter param=t0 value=-1u\n" + TR_2C7, "tran tr1 (after alter a1): the pwl of V1 "
                 "has its first time point at -1e-06 s: ", FIRST_2C8),
                # phase 2c8 (M1, M4) re-decided P04 from VACASK only: an op whose dc is derived reads the waveform (its
                # value at t = 0) on both engines, so the rule refuses the state on both, as it refuses the constant
                ("P04", PULSE_WD_2C7 % "" + "a1 alter param=wd value=30u\nop1 dc\n", "dc op1 (after alter a1): the pulse of "
                 "V1 has period=2e-05 s, not longer than rise+fall+width = 3.1e-05 s: ", PERIOD_2C7.split("; ")[0]
                 + "; its dc is the waveform's value at t = 0)")]
        for name, deck, head, tail in both:
            for engine in ("vacask", "xyce"):
                with self.subTest(deck=name, engine=engine):
                    self.one(self.fails(deck, engine), head, tail)
        vacask_only = [
            ("P05", SINE_FR_2C7.replace("sinedc=0", "sinedc=0.2").replace("freq=fr", "freq=fr sinephase=30")
             + "a1 alter param=fr value=0\n" + TR_2C7, "tran tr1 (after alter a1): on VACASK the sine of V1 has freq=0 Hz, ",
             "'Frequency of sinusoidal transient must be greater than 0'"),
            ("E06z", SINE_FR_2C7 + "sw1 sweep dev=V1 param=freq values=[0 100k] {\n  tr1 tran stop=3u\n}\n",
             "tran tr1 at V1:freq=0 (sweep sw1): on VACASK the sine of V1 has freq=0 Hz, ", "greater than 0"),
            ("P12", PULSE_WD_2C7 % "" + "a1 alter param=wd value=30u\na2 alter dev=R2 param=r value=2k\na3 alter param=wd "
             "value=1u\n" + TR_2C7, "alter a2, VACASK's first elaboration (after alter a1): on VACASK the pulse of V1 has "
             "period=2e-05 s, not longer than rise+fall+width = 3.1e-05 s, ", "'Period of pulse transient must be greater "
             "than rise+fall+width'")]
        for name, deck, head, piece in vacask_only:
            with self.subTest(deck=name, engine="vacask"):
                self.one(self.fails(deck, "vacask"), head, piece, VACASK_ONLY_2C7)
            with self.subTest(deck=name, engine="xyce"):
                self.builds(deck, "xyce")

    def test_l4_the_controls_build_on_both_engines(self):
        """A valid width, freq, td2 or time point after the alter; a given dc whose invalid pulse no tran sets up (VACASK
        prints type="dc"); an invalid state an alter leaves before the next one makes it valid (no setup between, VACASK's
        first elaboration being the tran's); the hooked sweeps of L3."""
        decks = [PULSE_WD_2C7 % "" + "a1 alter param=wd value=1.5u\n" + TR_2C7,
                 PULSE_WD_2C7 % " dc=0.5" + "a1 alter param=wd value=30u\nop1 dc\n",
                 PULSE_WD_2C7 % "" + "a1 alter param=wd value=30u\na2 alter param=wd value=1u\n" + TR_2C7,
                 SINE_FR_2C7 + "a1 alter param=fr value=200k\n" + TR_2C7,
                 EXP_T2_2C7 + "a1 alter param=t2 value=1u\n" + TR_2C7,
                 PWL_TP_2C7 + "a1 alter param=tp value=2u\n" + TR_2C7,
                 EXP_TD2_2C7 + SW_TD2_2C7,
                 SINE_FR_2C7 + "sw1 sweep dev=V1 param=freq values=[100k 200k] {\n  tr1 tran stop=3u\n}\n",
                 R0224_2C7]
        for deck in decks:
            for engine in ("vacask", "xyce"):
                with self.subTest(deck=deck.splitlines()[-3:], engine=engine):
                    self.builds(deck, engine)

    def test_l4_a_field_that_is_no_number_breaks_no_constraint(self):
        """A width altered to a word (S-2c6's Q7, `value="2u"') is no number the constraints can read: the plan builds as
        before (the open wrong-type case), where this round's first cut read the missing width as its default 1e30 and
        refused a period `not longer than rise+fall+width = 1e+30 s'; a scalar wave (Q5) or a switch to a pwl without one
        (T6) is spectre.resolve_source's own error, without a constraint's message beside it."""
        q7 = (PULSE_WD_2C7.replace("width=wd", "width=1u") % "" + 'a1 alter dev=V1 param=width value="2u"\n'
              "a2 alter param=other value=2\n" + TR_2C7)
        for engine in ("vacask", "xyce"):
            with self.subTest(deck="Q7", engine=engine):
                try:
                    self.builds(q7, engine)
                except NoteError as exc:
                    self.assertFalse(any("rise+fall+width" in e for e in errors(exc)), errors(exc))
            for name, deck in (("Q5", PWL_TP_2C7 + "a1 alter dev=V1 param=wave value=2\n" + TR_2C7),
                               ("T6", PULSE_WD_2C7 % "" + "a1 alter dev=V1 param=type value=pwl\n" + TR_2C7)):
                with self.subTest(deck=name, engine=engine):
                    errs = self.fails(deck, engine)
                    self.assertTrue(any("type=pwl needs wave=[t0 v0 t1 v1 ...]" in e for e in errs), errs)
                    self.assertFalse(any("time point" in e for e in errs), errs)


# -- phase 2c8 ------------------------------------------------------------------------------------------------------
# The 2c7 skeptic's decks (its sets p7c, p7d, p7e, g7a, g7b): V1 drives in, out = in/2
SKEPTIC_HEAD_2C8 = "simulator lang=spectre\nparameters other=1 ft=0.5u wd=2u tau=1u fr=100k\n"
SKEPTIC_LOAD_2C8 = ("R1 (in out) resistor r=1k\nR2 (out 0) resistor r=1k\nR3 (in 0) resistor r=other*1k\n"
                    "save in out\n")
TR_2C8 = "tr1 tran stop=3.5u maxstep=2n\n"


def pulse_2c8(fall="0.5u", width="2u", tail=TR_2C8, extra=""):
    return (SKEPTIC_HEAD_2C8 + "V1 (in 0) vsource type=pulse val0=0 val1=1 delay=0.5u rise=0.5u fall=%s width=%s "
            "period=20u%s\n" % (fall, width, extra) + SKEPTIC_LOAD_2C8 + tail)


def exp_2c8(tau1="1u", tau2="1u", tail=TR_2C8):
    return (SKEPTIC_HEAD_2C8 + "V1 (in 0) vsource type=exp val0=0 val1=1 td1=0.5u tau1=%s td2=2u tau2=%s\n"
            % (tau1, tau2) + SKEPTIC_LOAD_2C8 + tail)


def sine_2c8(fields="sinedc=0.2 ampl=0.5 freq=100k", tail=TR_2C8):
    return SKEPTIC_HEAD_2C8 + "V1 (in 0) vsource type=sine %s\n" % fields + SKEPTIC_LOAD_2C8 + tail


def pwl_2c8(wave="[0 0 1u 1 3u 1]", extra="", tail=TR_2C8, params=""):
    return (SKEPTIC_HEAD_2C8.replace("fr=100k", "fr=100k t0=0 st=1" + params) + "V1 (in 0) vsource type=pwl wave=%s%s\n"
            % (wave, extra) + SKEPTIC_LOAD_2C8 + tail)


def sweep_2c8(head, values):
    return "sw1 sweep %s values=[%s] {\n  tr1 tran stop=3.5u maxstep=2n\n}\n" % (head, values)


# The rules (plan._SRC_RULES), as a state's refusal words them on both engines in a tran
FALL_2C8 = ("a negative fall is an error (§3.8.1; VACASK would stay at val1, as it reads a fall at or below 0 as none, "
            "and Xyce would end val1 early)")
WIDTH_2C8 = ("a negative width is an error in v1 (Spectre's reading is not known; VACASK and Xyce would make different "
             "waveforms of it)")
TAU_2C8 = ("an exp time constant of 0 is an error in v1 (ref5 p.685 gives no exp formula that defines it; VACASK would "
           "divide by it at the exp's start and stop with 'NaN found in vector', and Xyce would make a step or, for "
           "some start times, stop with 'Time step too small')")
FREQ_2C8 = ("a sine with a negative freq is an error in v1, as the constant is (Spectre's reading is not known; VACASK "
            "stops at its setup with 'Frequency of sinusoidal transient must be greater than 0')")
FIRST_2C8 = ("a pwl time point before 0 is an error, as the constant is (§3.8.1; VACASK stops at its setup with 'First "
             "timepoint must be >=0')")
POINTS_2C8 = ("a pwl needs two or more time points, as the constant does (§3.8.1; VACASK stops at its setup with 'Pwl "
              "waveform needs at least two points')")
RISE_2C8 = ("a negative rise is an error (§3.8.1; VACASK stops at its setup with 'Rise time of pulse transient must be "
            "grater than 0')")
PERIOD0_2C8 = ("an explicit period no longer than rise+fall+width is an error (§3.8.1; VACASK would run a period of 0 or "
               "less as a single pulse)")
# VACASK's spelling of the sine fields whose name differs from Spectre's (vacask.SOURCE_FIELDS)
VACASK_FIELD_2C8 = {"damp": "theta", "sinephase": "tdphase"}
# S-2c7's S0290 (g7a): fall=rt altered to -1e-07, then the width (past the period, back to 2u), R2 2k
S0290_2C8 = """simulator lang=spectre
parameters other=1 wd=1u rt=0.5u per=20u fr=100k dm=0 td=0.5u t2=2u tp=1u t0=0
V1 (in 0) vsource type=pulse val0=0 val1=1 delay=0.5u rise=0.5u fall=rt width=1u period=per mag=1
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
a1 alter param=rt value=-1e-07
a2 alter dev=V1 param=width value=3e-05
a3 alter dev=R2 param=r value=2000
a4 alter dev=V1 param=width value=2e-06
tr1 tran stop=3u maxstep=2n
tr2 tran stop=3u maxstep=2n
"""
S0290_REF_2C8 = """simulator lang=spectre
parameters other=1 wd=1u rt=-1e-07 per=20u fr=100k dm=0 td=0.5u t2=2u tp=1u t0=0
V1 (in 0) vsource type=pulse val0=0 val1=1 delay=0.5u rise=0.5u fall=rt width=2e-06 period=per mag=1
R1 (in out) resistor r=1k
R2 (out 0) resistor r=2000
R3 (in 0) resistor r=other*1k
save in out
ref tran stop=3u maxstep=2n
"""
# S-2c7's S0013 (g7a): a sine with a given dc whose freq an alter sets to 0; its reference S0013__op1 and T0255's
S0013_2C8 = """simulator lang=spectre
parameters other=1 wd=1u rt=0.5u per=20u fr=100k dm=0 td=0.5u t2=2u tp=1u t0=0
V1 (in 0) vsource type=sine sinedc=0 ampl=0.5 freq=fr sinephase=30 dc=0.5
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
a1 alter dev=V1 param=freq value=0
tr1 tran stop=3u maxstep=2n
op1 dc
tr2 tran stop=3u maxstep=2n
"""
S0013_REF_2C8 = S0013_2C8.replace("freq=fr sinephase=30", "freq=0 sinephase=30").split("a1 alter")[0] + "ref dc\n"
T0255_REF_2C8 = """simulator lang=spectre
parameters other=1 t2=2u td=0.5u tau=2u fr=125000 rt=0.5u wd=1u per=20u
V1 (in 0) vsource type=sine sinedc=0 ampl=0.5 freq=0 dc=0.25
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
ref dc
"""
# S-2c7's S0110 (g7a): a dev= sweep of a sine's freq to -50k
S0110_2C8 = """simulator lang=spectre
parameters other=1 wd=1u rt=0.5u per=20u fr=100k dm=0 td=0.5u t2=2u tp=1u t0=0
V1 (in 0) vsource type=sine sinedc=0.2 ampl=0.5 freq=fr
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
sw1 sweep dev=V1 param=freq values=[200000 -50000] {
  tr1 tran stop=3u maxstep=2n
}
tr2 tran stop=3u maxstep=2n
tr3 tran stop=3u maxstep=2n
"""
# the sine of constant freq 0 as both engines print it: the sine itself at 1e-30 Hz (sinedc, ampl, phase), its dc given
CSINE_VK_2C8 = "sinedc=%s ampl=%s freq=1e-30 delay=0.0 theta=0.0 tdphase=%s dc=%s"
CSINE_XY_2C8 = "DC %s SIN(%s %s 1e-30 0.0 0.0 %s)"
# Xyce's derived DC of that sine without a given dc, its value at t = 0: 0.2 + 0.5 sin(30 deg)
C30B_2C8 = repr(0.2 + 0.5 * math.sin(30.0 * (math.pi / 180.0)))


class TestFollowUp2c8(unittest.TestCase):
    """Phase 2c8 (the 2c7 skeptic's M1-M4).  M1: a negative pulse fall reached both engines through a netlist parameter,
    a param= sweep or a dev= sweep (status 0, VACASK never falling, Xyce ending the plateau early) while the constant was
    refused; a negative width ran everywhere (VACASK and Xyce made different waveforms of it), and a dev= sweep of a rise
    or a fall through 0 printed the raw 0 (VACASK: no fall, or exit 1 for the rise).  M2: a sine whose freq is the
    constant 0 lost its given dc in every DC-type solve (spectre.resolve_source's fold replaces it).  M3: an exp time
    constant of 0 stopped VACASK with NaN.  M4: a negative freq was refused on VACASK only, `(Xyce runs it)', where Xyce
    refused the constant and ran a sweep to it; the §3.7 message spelled temp as temper and 40 as 40.0; the period
    message claimed VACASK stops at a period of 0, which it runs; a stretch of -1 printed `-0 s'.  Found by this round's
    survey of every field driven to 0 or below: Xyce's derived dc with a negative delay was the waveform's first value,
    not its value at t = 0 (VACASK, computing its own, was right)."""

    def fails(self, text, engine):
        nl = parse_text(text)
        with self.assertRaises(NoteError) as cm:
            PL.build(nl, engine, Settings())
        return errors(cm.exception)

    def fails_any(self, text, engine):
        """The errors of the parse or, past it, of plan.build."""
        try:
            nl = parse_text(text)
        except NoteError as exc:
            return errors(exc)
        with self.assertRaises(NoteError) as cm:
            PL.build(nl, engine, Settings())
        return errors(cm.exception)

    def builds(self, text, engine):
        return PL.build(parse_text(text), engine, Settings())

    def xyce_dc(self, deck_text):
        """The DC value Xyce's deck prints for V1 (V1 in 0 DC <value> ...)."""
        line = next(ln for ln in deck_text.splitlines() if ln.startswith("V1 in 0 DC "))
        return float(line.split()[4])

    def one(self, errs, head, *pieces):
        hits = [e for e in errs if e.startswith(head) and all(p in e for p in pieces)]
        self.assertEqual(len(hits), 1, "%s\n%s" % (head, "\n".join(errs)))
        return hits[0]

    def refused_both(self, cases):
        for name, deck, head, rule in cases:
            for engine in ("vacask", "xyce"):
                with self.subTest(deck=name, engine=engine):
                    self.one(self.fails(deck, engine), head, rule)

    # -- M1 ---------------------------------------------------------------------------------------------------------
    def test_m1_a_negative_fall_is_refused_in_every_state_on_both_engines(self):
        """The constant (N01) and every route to a negative fall: an alter of the netlist parameter it reads (N07, N15),
        an alter of the field (N09: tables.wave's render message before), a dev= sweep (N10) and a param= sweep (N12),
        S0290 and its alter-free reference: one refusal per deck on each engine, with the constant's wording."""
        fall = "the pulse of V1 has fall=-1e-07 s: "
        self.refused_both([
            ("N01", pulse_2c8("-0.1u"), "tran tr1: " + fall, FALL_2C8),
            ("N07", pulse_2c8("ft", tail="a1 alter param=ft value=-0.1u\n" + TR_2C8), "tran tr1 (after alter a1): "
             + fall, FALL_2C8),
            ("N09", pulse_2c8(tail="a1 alter dev=V1 param=fall value=-0.1u\n" + TR_2C8), "tran tr1 (after alter a1): "
             + fall, FALL_2C8),
            ("N10", pulse_2c8(tail=sweep_2c8("dev=V1 param=fall", "-0.1u 0.5u")), "tran tr1 at V1:fall=-1e-07 (sweep "
             "sw1): " + fall, FALL_2C8),
            ("N12", pulse_2c8("ft", tail=sweep_2c8("param=ft", "0.5u -0.1u")), "tran tr1 at ft=-1e-07 (sweep sw1): "
             + fall, FALL_2C8),
            ("S0290", S0290_2C8, "tran tr1 (after alter a1): " + fall, FALL_2C8),
            ("S0290__tr1", S0290_REF_2C8, "tran ref: " + fall, FALL_2C8)])

    def test_m1_a_negative_width_is_refused_in_every_state_on_both_engines(self):
        """The constant (S-2c7's C01) and each route to it (N02 an alter of wd, an alter of the field, a dev= and a param=
        sweep): refused on both engines, which made different waveforms of it (VACASK val0 after the rise, Xyce val0
        throughout).  A width of 0 (a triangle on both engines) builds."""
        width = "the pulse of V1 has width=-1e-06 s: "
        self.refused_both([
            ("C01", pulse_2c8(width="-1u"), "tran tr1: " + width, WIDTH_2C8),
            ("N02", pulse_2c8(width="wd", tail="a1 alter param=wd value=-1u\n" + TR_2C8), "tran tr1 (after alter a1): "
             + width, WIDTH_2C8),
            ("altd", pulse_2c8(tail="a1 alter dev=V1 param=width value=-1u\n" + TR_2C8), "tran tr1 (after alter a1): "
             + width, WIDTH_2C8),
            ("swd", pulse_2c8(tail=sweep_2c8("dev=V1 param=width", "2u -1u")), "tran tr1 at V1:width=-1e-06 (sweep "
             "sw1): " + width, WIDTH_2C8),
            ("swp", pulse_2c8(width="wd", tail=sweep_2c8("param=wd", "2u -1u")), "tran tr1 at wd=-1e-06 (sweep sw1): "
             + width, WIDTH_2C8)])
        for engine in ("vacask", "xyce"):
            with self.subTest(width=0, engine=engine):
                self.builds(pulse_2c8(width="wd", tail="a1 alter param=wd value=0\n" + TR_2C8), engine)

    def test_m1_the_constant_rules_hold_in_every_state_per_field(self):
        """Every field whose constant value vamos refuses, through the routes 2c7 left open: a negative rise through a
        dev= sweep and as a constant; a period no longer than rise+fall+width through a dev= sweep; an exp's td2 before
        its td1 as a constant; pwl time points that do not increase as a constant (spectre.resolve_source's), one before 0
        through a parameter (VACASK only before) and a single time point (VACASK only before, while Xyce refused it at
        render): one refusal per deck on both engines."""
        self.refused_both([
            ("rise swd", pulse_2c8(tail=sweep_2c8("dev=V1 param=rise", "0.5u -0.1u")), "tran tr1 at V1:rise=-1e-07 (sweep "
             "sw1): the pulse of V1 has rise=-1e-07 s: ", RISE_2C8),
            ("rise const", pulse_2c8().replace("rise=0.5u", "rise=-0.1u"), "tran tr1: the pulse of V1 has rise=-1e-07 s: ",
             RISE_2C8),
            ("period swd", pulse_2c8(tail=sweep_2c8("dev=V1 param=period", "20u 2u")), "tran tr1 at V1:period=2e-06 "
             "(sweep sw1): the pulse of V1 has period=2e-06 s, not longer than rise+fall+width = 3e-06 s: ", PERIOD_2C7),
            ("pwl first altp", pwl_2c8("[t0 0 1u 1 3u 1]", tail="a1 alter param=t0 value=-0.5u\n" + TR_2C8),
             "tran tr1 (after alter a1): the pwl of V1 has its first time point at -5e-07 s: ", FIRST_2C8),
            ("pwl first swp", pwl_2c8("[t0 0 1u 1 3u 1]", tail=sweep_2c8("param=t0", "0 -0.5u")), "tran tr1 at t0=-5e-07 "
             "(sweep sw1): the pwl of V1 has its first time point at -5e-07 s: ", FIRST_2C8),
            ("pwl points", pwl_2c8(tail="a1 alter dev=V1 param=wave value=[0 1]\n" + TR_2C8), "tran tr1 (after alter a1): "
             "the pwl of V1 has only one time point: ", POINTS_2C8)])
        for engine in ("vacask", "xyce"):
            with self.subTest(td2="const", engine=engine):
                errs = self.fails_any(exp_2c8().replace("td2=2u", "td2=0.4u"), engine)
                self.assertTrue(any("td2" in e and "td1" in e for e in errs), errs)
            with self.subTest(times="const", engine=engine):
                errs = self.fails_any(pwl_2c8("[0 0 2u 1 1u 1]"), engine)
                self.assertTrue(any("pwl time points must increase" in e for e in errs), errs)

    def test_m1_m3_a_constant_is_refused_with_no_analysis_too(self):
        """A netlist that runs no analysis (`nothing to run', status 0 on both engines): plan.build checked no state, so
        a constant negative fall, width or rise, a negative freq, an exp time constant of 0 or a pwl time point before 0
        passed, where the parse refuses a constant period or pwl times that do not increase whatever runs: each is the
        rule's refusal on both engines now, at the netlist's values, as the parse's.  A field that reads a parameter, a
        constant sine of freq 0 and a valid source build as before."""
        cases = [("fall", pulse_2c8("-0.1u", tail=""), "the pulse of V1 has fall=-1e-07 s: ",
                  "a negative fall is an error (§3.8.1)"),
                 ("width", pulse_2c8(width="-1u", tail=""), "the pulse of V1 has width=-1e-06 s: ",
                  "a negative width is an error in v1 (Spectre's reading is not known)"),
                 ("rise", pulse_2c8(tail="").replace("rise=0.5u", "rise=-0.1u"), "the pulse of V1 has rise=-1e-07 s: ",
                  "a negative rise is an error (§3.8.1)"),
                 ("tau1", exp_2c8("0", tail=""), "the exp of V1 has tau1=0 s: ",
                  "an exp time constant of 0 is an error in v1 (ref5 p.685 gives no exp formula that defines it)"),
                 ("tau2", exp_2c8(tau2="0", tail=""), "the exp of V1 has tau2=0 s: ",
                  "an exp time constant of 0 is an error in v1 (ref5 p.685 gives no exp formula that defines it)"),
                 ("freq", sine_2c8("sinedc=0.2 ampl=0.5 freq=-50k", tail=""), "the sine of V1 has freq=-50000 Hz: ",
                  "a sine with a negative freq is an error in v1, as the constant is (Spectre's reading is not known)"),
                 ("first", pwl_2c8("[-0.5u 0 1u 1 3u 1]", tail=""), "the pwl of V1 has its first time point at -5e-07 s: ",
                  "a pwl time point before 0 is an error, as the constant is (§3.8.1)")]
        for name, deck, what, rule in cases:
            for engine in ("vacask", "xyce"):
                with self.subTest(field=name, engine=engine):
                    self.assertEqual(self.one(self.fails(deck, engine), "the netlist: " + what, rule),
                                     "the netlist: " + what + rule)
        builds = [pulse_2c8("ft", tail="").replace("ft=0.5u", "ft=-0.1u"), sine_2c8("sinedc=0.2 ampl=0.5 freq=0 dc=0.25",
                                                                                     tail=""), pulse_2c8(tail="")]
        for deck in builds:
            for engine in ("vacask", "xyce"):
                with self.subTest(builds=deck.splitlines()[2], engine=engine):
                    self.builds(deck, engine)

    def test_m1_a_negative_delay_or_time_constant_and_a_zero_width_build(self):
        """The fields whose constant vamos runs, and both engines run alike (S-2c7's C03, C04, C06; N03, N04, N06): a
        negative delay of a pulse, a sine, an exp or a pwl and a negative tau through each route build on both."""
        decks = [pulse_2c8().replace("delay=0.5u", "delay=-0.2u"),
                 pulse_2c8().replace("delay=0.5u", "delay=dl").replace("ft=0.5u", "ft=0.5u dl=0.5u")
                 .replace(TR_2C8, "a1 alter param=dl value=-0.2u\n" + TR_2C8),
                 sine_2c8("sinedc=0.1 ampl=0.5 freq=100k delay=-0.3u"),
                 exp_2c8("-1u"), exp_2c8("tau", tail="a1 alter param=tau value=-1u\n" + TR_2C8),
                 exp_2c8(tail="a1 alter dev=V1 param=tau2 value=-1u\n" + TR_2C8),
                 pwl_2c8(extra=" delay=-0.3u")]
        for deck in decks:
            for engine in ("vacask", "xyce"):
                with self.subTest(deck=deck.splitlines()[2], engine=engine):
                    self.builds(deck, engine)

    def test_m1_a_swept_rise_or_fall_of_0_is_spectre_s_default_edge(self):
        """§3.8.1: a sweep value of 0 of a pulse's rise or fall is the default edge transres = 1e-9 x the shortest tran
        stop, which VACASK's sweep and Xyce's .STEP now print for it (the raw 0 before: VACASK read the fall as none and
        stopped at a rise of 0); the PSF grid keeps the 0 (the plan's own values)."""
        for param in ("fall", "rise"):
            deck = pulse_2c8(tail=sweep_2c8("dev=V1 param=%s" % param, "0 0.5u"))
            with self.subTest(param=param, engine="vacask"):
                self.assertIn('sweep vamos_w1 instance="V1" parameter="%s" values=[3.5e-15, 5e-07]' % param,
                              vacask_deck(deck))
            with self.subTest(param=param, engine="xyce"):
                self.assertIn(".STEP vamos_x_V1_%s LIST 3.5e-15 5e-07" % param, xyce_decks(deck)[0])
            plan = self.builds(deck, "vacask")
            step = next(a.step for a in plan.actions if a.op == "analysis")
            self.assertEqual(PL.grid_points(step.context[0].spec), [0.0, 5e-07])
        deck = pulse_2c8(tail="sw1 sweep dev=V1 param=fall start=0 stop=1u step=0.5u {\n  tr1 tran stop=3.5u "
                              "maxstep=2n\n}\n")
        self.assertIn('parameter="fall" values=[3.5e-15, 5e-07, 1e-06]', vacask_deck(deck))
        self.assertIn(".STEP vamos_x_V1_fall LIST 3.5e-15 5e-07 1e-06", xyce_decks(deck)[0])
        # no tran: transres is 1e-9 (spectre.resolve_source's edge without a tran stop), no edge acting in a DC analysis
        deck = pulse_2c8(tail="dc1 dc dev=V1 param=rise values=[0 0.5u]\n")
        self.assertIn('parameter="rise" values=[1e-09, 5e-07]', vacask_deck(deck))

    # -- M2 ---------------------------------------------------------------------------------------------------------
    def test_m2_a_constant_sine_keeps_its_given_dc_for_the_dc_solves(self):
        """S-2c7's Z01 (freq=0 with dc=0.25 on the instance line): the DC-type solves read the given dc and the tran the
        waveform's constant 0.  Both engines print the sine itself at freq 1e-30 (its value sinedc + ampl*sin(sinephase)
        at every time point), VACASK under type="dc" switched to its waveform around the tran, as any waveform with a
        given dc, Xyce with DC 0.25; the alter-free references S0013__op1 and T0255's print the given dc.  2c7 printed
        type="dc" dc=0.0 and DC 0.0."""
        z01 = sine_2c8("sinedc=0 ampl=0.5 freq=0 dc=0.25", "op1 dc\n" + TR_2C8)
        text = vacask_deck(z01)
        self.assertIn('V1 (in 0) vamos_vsource type="dc" ' + CSINE_VK_2C8 % ("0.0", "0.5", "0.0", "0.25"), text)
        self.assertIn('  alter instance("V1") type="sine" ' + CSINE_VK_2C8 % ("0.0", "0.5", "0.0", "0.25") + "\n", text)
        decks = xyce_decks(z01)
        self.assertEqual(len(decks), 2)
        for d in decks:
            self.assertIn("V1 in 0 " + CSINE_XY_2C8 % ("0.25", "0.0", "0.5", "0.0"), d)
        for ref, dc, phase in ((S0013_REF_2C8, "0.5", "30.0"), (T0255_REF_2C8, "0.25", "0.0")):
            with self.subTest(ref=ref.splitlines()[2]):
                self.assertIn(CSINE_VK_2C8 % ("0.0", "0.5", phase, dc), vacask_deck(ref))
                self.assertIn(CSINE_XY_2C8 % (dc, "0.0", "0.5", phase), xyce_decks(ref)[0])

    def test_m2_an_alter_of_freq_to_0_keeps_the_given_dc(self):
        """Z02 and Z06 (an alter of freq to 0 under dc=0.25) and S0013 (sinephase=30, dc=0.5): the alter's source is the
        constant sine with the given dc, on VACASK an alter instance of the sine at 1e-30 Hz under type="dc", on Xyce
        DC <dc> with it, in every deck after the alter (2c7: dc=0.0, the given dc lost)."""
        z06 = sine_2c8("sinedc=0 ampl=0.5 freq=100k dc=0.25", "op0 dc\na1 alter dev=V1 param=freq value=0\nop1 dc\n")
        self.assertIn('  alter instance("V1") type="dc" ' + CSINE_VK_2C8 % ("0.0", "0.5", "0.0", "0.25"),
                      vacask_deck(z06))
        decks = xyce_decks(z06)
        self.assertIn("SIN(0.0 0.5 100000.0", decks[0])
        self.assertIn(CSINE_XY_2C8 % ("0.25", "0.0", "0.5", "0.0"), decks[1])
        text = vacask_deck(S0013_2C8)
        self.assertIn('  alter instance("V1") type="dc" ' + CSINE_VK_2C8 % ("0.0", "0.5", "30.0", "0.5"), text)
        self.assertIn('  alter instance("V1") type="sine" ' + CSINE_VK_2C8 % ("0.0", "0.5", "30.0", "0.5"), text)
        for d in xyce_decks(S0013_2C8):
            self.assertIn(CSINE_XY_2C8 % ("0.5", "0.0", "0.5", "30.0"), d)

    def test_m2_a_constant_sine_without_a_dc_and_its_dc_sweeps(self):
        """Without a given dc the sine's t = 0 value is the dc (VACASK type="sine", Xyce DC <the constant>), so a dc
        sweep of it switches it to type="dc" around a DC analysis and leaves it alone in a tran, where the waveform acts
        (2c7's type="dc" took the swept dc into the tran)."""
        text = vacask_deck(sine_2c8("sinedc=0.2 ampl=0.5 freq=0 sinephase=30", "op1 dc\n" + TR_2C8))
        self.assertIn('V1 (in 0) vamos_vsource type="sine" sinedc=0.2 ampl=0.5 freq=1e-30 delay=0.0 theta=0.0 '
                      'tdphase=30.0\n', text)
        self.assertIn("V1 in 0 DC %s SIN(0.2 0.5 1e-30 0.0 0.0 30.0)" % C30B_2C8,
                      xyce_decks(sine_2c8("sinedc=0.2 ampl=0.5 freq=0 sinephase=30", "op1 dc\n"))[0])
        text = vacask_deck(sine_2c8("sinedc=0.2 ampl=0.5 freq=0", "dc1 dc dev=V1 param=dc values=[0.1 0.3]\n"))
        self.assertIn('  alter instance("V1") type="dc" ', text)
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                self.builds(sine_2c8("sinedc=0.2 ampl=0.5 freq=0", sweep_2c8("dev=V1 param=dc", "0.1 0.3")), engine)

    def test_m2_a_constant_sine_s_fields_sweep_and_its_damp_stays_0(self):
        """A dev= sweep of the freq, ampl, sinedc, sinephase or delay of a sine whose freq is the constant 0 printed
        nothing the sweep could reach (2c7: the folded dc, the nominal constant at every point, status 0): the sine
        itself prints now, so each field's sweep reaches it on both engines (a freq point other than 0 a real sine, a
        point of 0 the constant, printed as its own 1e-30: VACASK stops at a sine of freq 0).  A damp point other than 0
        makes it what the constant's rule refuses, a sine with freq=0 and damp != 0, on both engines, unless a sweep of
        its freq keeps it away from 0.  A sine of another freq swept to 0 stays VACASK's refusal (Xyce runs it)."""
        fields = "sinedc=0.2 ampl=0.5 freq=0 sinephase=30"
        for field, values in (("ampl", "0.5 1"), ("sinedc", "0 0.2"), ("sinephase", "0 90"), ("delay", "0 0.3u"),
                              ("freq", "100k 200k"), ("damp", "0 0")):
            deck = sine_2c8(fields, sweep_2c8("dev=V1 param=%s" % field, values))
            for engine in ("vacask", "xyce"):
                with self.subTest(field=field, engine=engine):
                    self.builds(deck, engine)
            with self.subTest(field=field, engine="decks"):
                self.assertIn('sweep vamos_w1 instance="V1" parameter="%s"' % VACASK_FIELD_2C8.get(field, field),
                              vacask_deck(deck))
                self.assertIn(".STEP vamos_x_V1_%s LIST" % field, xyce_decks(deck)[0])
        damp = sine_2c8(fields, sweep_2c8("dev=V1 param=damp", "0 1e5"))
        for engine in ("vacask", "xyce"):
            with self.subTest(damp=engine):
                self.one(self.fails(damp, engine), "sweep sw1 at V1:damp=100000: the sine of V1 has freq=0 and damp=100000: ",
                         "a sine with freq=0 and damp != 0 is not a constant: give the frequency (§3.8.1)")
        freq0 = sine_2c8(fields, sweep_2c8("dev=V1 param=freq", "0 100k"))
        for engine in ("vacask", "xyce"):
            with self.subTest(freq0=engine):
                self.builds(freq0, engine)
        self.assertIn('sweep vamos_w1 instance="V1" parameter="freq" values=[1e-30, 100000.0]', vacask_deck(freq0))
        self.assertIn(".STEP vamos_x_V1_freq LIST 1e-30 100000.0", xyce_decks(freq0)[0])
        nested = sine_2c8(fields, "sw0 sweep dev=V1 param=freq values=[100k 200k] {\n" + sweep_2c8("dev=V1 param=damp",
                                                                                                  "0 1e5") + "}\n")
        for engine in ("vacask", "xyce"):
            with self.subTest(nested=engine):
                self.builds(nested, engine)                     # a damped sine at every point
        other = sine_2c8("sinedc=0.2 ampl=0.5 freq=100k sinephase=30", sweep_2c8("dev=V1 param=freq", "0 100k"))
        self.builds(other, "xyce")
        self.one(self.fails(other, "vacask"), "tran tr1 at V1:freq=0 (sweep sw1): on VACASK the sine of V1 has freq=0 Hz, ",
                 "not supported on VACASK in v1 (Xyce runs it)")
        dc = sine_2c8(fields, "dc1 dc dev=V1 param=sinedc values=[0 1]\n")          # its derived dc follows sinedc
        aa = sine_2c8("sinedc=0.2 ampl=aa freq=0 sinephase=30", sweep_2c8("param=aa", "0.5 1")).replace(
            "fr=100k", "fr=100k aa=0.5")                                            # a field written as a parameter
        for engine in ("vacask", "xyce"):
            with self.subTest(dc=engine):
                self.builds(dc, engine)
                self.builds(aa, engine)
        self.assertIn('sinedc=0.2 ampl=aa freq=1e-30 delay=0.0 theta=0.0 tdphase=30.0\n', vacask_deck(aa))
        self.assertIn("SIN(0.2 {aa} 1e-30 0.0 0.0 30.0)", xyce_decks(aa)[0])

    # -- M3 ---------------------------------------------------------------------------------------------------------
    def test_m3_an_exp_time_constant_of_0_is_refused_on_both_engines(self):
        """Spectre's ref5 p.685 gives the exp's fields and no formula, so tau1 = 0 is no documented step: the constant
        (S-2c7's C05), an alter of the parameter it reads (N05), an alter of the field and a param= sweep, for tau1 and
        tau2, are refused on both engines naming the field (VACASK stopped with NaN, Xyce made a step), and so is an op
        whose derived dc, the waveform's value at t = 0, reads it."""
        t1, t2 = "the exp of V1 has tau1=0 s: ", "the exp of V1 has tau2=0 s: "
        self.refused_both([
            ("C05", exp_2c8("0"), "tran tr1: " + t1, TAU_2C8),
            ("N05", exp_2c8("tau", tail="a1 alter param=tau value=0\n" + TR_2C8), "tran tr1 (after alter a1): " + t1,
             TAU_2C8),
            ("altd1", exp_2c8(tail="a1 alter dev=V1 param=tau1 value=0\n" + TR_2C8), "tran tr1 (after alter a1): " + t1,
             TAU_2C8),
            ("swp1", exp_2c8("tau", tail=sweep_2c8("param=tau", "1u 0")), "tran tr1 at tau=0 (sweep sw1): " + t1, TAU_2C8),
            # re-decided in phase 2c9 (N5): tau2 has its own clause (the fall's start; Xyce stops), 2c8 gave it tau1's
            ("tau2 const", exp_2c8(tau2="0"), "tran tr1: " + t2, TAU2_2C9),
            ("tau2 altp", exp_2c8(tau2="tau", tail="a1 alter param=tau value=0\n" + TR_2C8), "tran tr1 (after alter a1): "
             + t2, TAU2_2C9)])
        # the constant, as every constant vamos refuses, whatever the analysis (an op alone); a negative width too
        self.refused_both([
            ("C05 op", exp_2c8("0", tail="op1 dc\n"), "dc op1: " + t1, TAU_2C8.split("; ")[0] + ")"),
            ("C01 op", pulse_2c8(width="-1u", tail="op1 dc\n"), "dc op1: the pulse of V1 has width=-1e-06 s: ",
             WIDTH_2C8.split("; ")[0] + ")")])
        # an op whose dc is derived, the waveform's value at t = 0, which both engines take from the waveform: both
        # (VACASK divided by the time constant at an exp that starts at t = 0, NaN, where Xyce printed the first value);
        # a given dc reads no waveform outside a tran and builds
        alter = "a1 alter param=tau value=0\nop1 dc\n"
        dcs = [("td1=0", exp_2c8("tau", tail=alter).replace("td1=0.5u", "td1=0"), t1),
               ("td1<0", exp_2c8("tau", tail=alter).replace("td1=0.5u", "td1=-0.2u"), t1),
               ("td1>0", exp_2c8("tau", tail=alter), t1),
               ("tau2", exp_2c8(tau2="tau", tail=alter).replace("td2=2u", "td2=2u delay=-2u"), t2)]
        for name, deck, what in dcs:
            for engine in ("vacask", "xyce"):
                with self.subTest(op=name, engine=engine):
                    self.assertEqual(self.one(self.fails(deck, engine), "dc op1 (after alter a1): " + what),
                                     "dc op1 (after alter a1): " + what + TAU_2C8.split("; ")[0]
                                     + "; its dc is the waveform's value at t = 0)")
        given = exp_2c8("tau", tail=alter).replace("td1=0.5u", "td1=0").replace("tau1=tau", "tau1=tau dc=0.3")
        for engine in ("vacask", "xyce"):
            with self.subTest(builds="given dc", engine=engine):
                self.builds(given, engine)
        # a dev= sweep of a time constant: Xyce refused it at render, `the deck prints a derived value for it', which
        # only an exp's td1, td2 and delay are; it sweeps the field's hook now, as VACASK sweeps the field
        deck = exp_2c8(tail=sweep_2c8("dev=V1 param=tau1", "1u 2u"))
        self.assertIn(".STEP vamos_x_V1_tau1 LIST 1e-06 2e-06", xyce_decks(deck)[0])
        self.assertIn('sweep vamos_w1 instance="V1" parameter="tau1" values=[1e-06, 2e-06]', vacask_deck(deck))

    # -- M4 ---------------------------------------------------------------------------------------------------------
    def test_m4_a_negative_freq_is_one_rule_on_both_engines(self):
        """N13 (an alter of freq to -50k: VACASK `(Xyce runs it)', Xyce `SIN frequency -50000.0 must be > 0' at render),
        N14 and S0110 (a dev= sweep to -50k: Xyce ran it), an alter of the netlist parameter it reads: one refusal on both
        engines with the constant's rule.  An alter to a number before an op alone: both engines (Xyce refused the
        constant at render), never `(Xyce runs it)'.  freq = 0 on a sine (a parameter or a sweep point) stays VACASK's
        alone: Xyce runs the constant."""
        neg = "the sine of V1 has freq=-50000 Hz: "
        self.refused_both([
            ("N13", sine_2c8(tail="a1 alter dev=V1 param=freq value=-50k\n" + TR_2C8), "tran tr1 (after alter a1): " + neg,
             FREQ_2C8),
            ("N14", sine_2c8(tail=sweep_2c8("dev=V1 param=freq", "100k -50k")), "tran tr1 at V1:freq=-50000 (sweep sw1): "
             + neg, FREQ_2C8),
            ("S0110", S0110_2C8, "tran tr1 at V1:freq=-50000 (sweep sw1): " + neg, FREQ_2C8),
            ("altp", sine_2c8("sinedc=0.2 ampl=0.5 freq=fr", "a1 alter param=fr value=-50k\n" + TR_2C8),
             "tran tr1 (after alter a1): " + neg, FREQ_2C8)])
        op = sine_2c8(tail="a1 alter dev=V1 param=freq value=-50k\nop1 dc\n")
        for engine in ("vacask", "xyce"):
            with self.subTest(op=engine):
                err = self.one(self.fails(op, engine), "dc op1 (after alter a1): " + neg)
                self.assertNotIn("Xyce runs it", err)
        zero = sine_2c8("sinedc=0.2 ampl=0.5 freq=fr", "a1 alter param=fr value=0\n" + TR_2C8)
        self.one(self.fails(zero, "vacask"), "tran tr1 (after alter a1): on VACASK the sine of V1 has freq=0 Hz, ",
                 "not supported on VACASK in v1 (Xyce runs it)")
        self.builds(zero, "xyce")

    def test_m4_the_messages_say_what_is_true(self):
        """§3.7's message names a condition as the netlist spells it (`temp > 40', `sel > 0'; 2c7: `temper > 40.0',
        `sel > 0.0'); a period of 0 is refused as VACASK runs it, a single pulse (2c7: `VACASK stops at its setup'); a
        stretch of -1 prints its first time point as 0 s, not -0 s."""
        tmp = TEMP_COND_2C7 + "a0 alter param=temp value=50\nop1 dc\n"
        self.one(self.fails(tmp, "vacask"), "alter a0 changes a structural condition on path top level: the condition "
                 "temp > 40 takes its if branch, where it takes its else branch at the netlist's own values: ", STRUCT_2C7)
        for engine in ("vacask", "xyce"):
            with self.subTest(deck="H05h", engine=engine):
                self.one(self.fails(H05H_2C7, engine), "alter a0 changes a structural condition on path top level: the "
                         "condition sel > 0 takes its if branch, ")
        p723 = PULSE_WD_2C7.replace("period=20u", "period=per").replace("wd=1u", "wd=1u per=20u") % "" + \
            "a1 alter param=per value=0\n" + TR_2C7
        p712 = PWL_TP_2C7.replace("3u 1]", "3u 1] stretch=st").replace("t0=0", "t0=0 st=1") + \
            "a1 alter param=st value=-1\n" + TR_2C7
        for engine in ("vacask", "xyce"):
            with self.subTest(deck="P723", engine=engine):
                err = self.one(self.fails(p723, engine), "tran tr1 (after alter a1): the pulse of V1 has period=0 s, not "
                               "longer than rise+fall+width = 2e-06 s: ", PERIOD0_2C8)
                self.assertNotIn("stops at its setup", err)
            with self.subTest(deck="P712", engine=engine):
                err = self.one(self.fails(p712, engine), "tran tr1 (after alter a1): the pwl of V1 has time points that do "
                               "not increase (0 s, then -1e-06 s): ")
                self.assertNotIn("-0 s", err)

    def test_m4_a_derived_dc_with_a_negative_delay_is_the_waveform_s_t0_value_on_xyce(self):
        """Xyce's derived dc (tables.wave_t0, its operating point without DC being 0 [E70]) read the first value of a
        pulse, sine, exp or pwl whatever its delay, where a negative delay puts t = 0 inside the waveform (VACASK, which
        computes its own, was right): a pulse with delay -0.2u is at 0.4 of its 0.5u rise, a sine with delay -0.3u at
        0.1 + 0.5 sin(2 pi 100k 0.3u), an exp whose td1 is -0.5u at 1 - exp(-0.5), a pwl with delay -0.5u at 0.5; the
        same through a netlist parameter the delay reads, altered (a ternary on the delay in Xyce's DC)."""
        cases = [(pulse_2c8(tail="op1 dc\n").replace("delay=0.5u", "delay=-0.2u"), 0.4),
                 (sine_2c8("sinedc=0.1 ampl=0.5 freq=100k delay=-0.3u", "op1 dc\n"),
                  0.1 + 0.5 * math.sin(2 * math.pi * 1e5 * 0.3e-6)),
                 (pwl_2c8(extra=" delay=-0.5u", tail="op1 dc\n"), 0.5),
                 (exp_2c8(tail="op1 dc\n").replace("td1=0.5u", "td1=-0.5u"), 1 - math.exp(-0.5)),
                 (pulse_2c8(tail="op1 dc\n").replace("delay=0.5u", "delay=-20.3u"), 0.6),     # a period on: 0.3u in
                 (pulse_2c8(tail="op1 dc\n"), 0.0)]                                            # delay 0.5u: val0
        for deck, want in cases:
            with self.subTest(deck=deck.splitlines()[2]):
                self.assertAlmostEqual(self.xyce_dc(xyce_decks(deck)[0]), want, delta=1e-12)
        self.assertIn("V1 in 0 DC 0.0 PULSE", xyce_decks(pulse_2c8(tail="op1 dc\n"))[0])   # the text 2c7 printed
        bound = pulse_2c8(tail="op0 dc\na1 alter param=dl value=-0.2u\nop1 dc\n").replace("delay=0.5u", "delay=dl") \
            .replace("ft=0.5u", "ft=0.5u dl=0.5u")
        decks = xyce_decks(bound)
        self.assertNotIn("V1 in 0 DC 0.0 PULSE", decks[1])
        self.assertIn("dl", next(ln for ln in decks[1].splitlines() if ln.startswith("V1 in 0 DC ")))
        # a pwl whose delay reads a parameter altered below 0: Xyce's dc is the value between the points around t = 0, a
        # ternary on the shifted time points (0.5 between (-0.5u, 0) and (0.5u, 1)); a first time point that reads a
        # parameter altered below 0 is the rule's refusal on both engines (an op whose dc is derived reads the waveform)
        pwl = pwl_2c8(extra=" delay=dl", tail="op0 dc\na1 alter param=dl value=-0.5u\nop1 dc\n", params=" dl=0")
        line = next(ln for ln in xyce_decks(pwl)[1].splitlines() if ln.startswith("V1 in 0 DC "))
        self.assertIn("dl>=0.0", line.replace(" ", ""))
        self.builds(pwl, "vacask")
        first = pwl_2c8("[t0 0 1u 1 3u 1]", tail="op0 dc\na1 alter param=t0 value=-0.5u\nop1 dc\n")
        for engine in ("vacask", "xyce"):
            with self.subTest(first=engine):
                self.assertEqual(self.one(self.fails(first, engine), "dc op1 (after alter a1): the pwl of V1 "),
                                 "dc op1 (after alter a1): the pwl of V1 has its first time point at -5e-07 s: "
                                 + FIRST_2C8.split("; ")[0] + "; its dc is the waveform's value at t = 0)")


# -- phase 2c9 ------------------------------------------------------------------------------------------------------
# The 2c8 skeptic's decks (its sets p8, p8b, p8c, p8e, g8sa, g8sb), on the 2c8 head and load
SINE_2C9 = "sinedc=0.2 ampl=0.5 freq=200k sinephase=30"
ALTER0_2C9 = "a1 alter dev=V1 param=freq value=0\n"
TRAN2_2C9 = "tr2 tran stop=3.5u maxstep=2n"


def block_2c9(head, values, body="tr1 tran stop=3.5u maxstep=2n"):
    """A sweep block (head: what it sweeps) around one analysis line."""
    return "sw1 sweep %s values=[%s] {\n  %s\n}\n" % (head, values, body)


def src_2c9(line, tail):
    """The 2c8 head and load around a source line of its own."""
    return SKEPTIC_HEAD_2C8 + line + "\n" + SKEPTIC_LOAD_2C8 + tail


# P873, Q05, P870, P871, P874 (the sine an alter made the constant sine, then a sweep of its freq through 0) and P872
# (the constant sine an alter made a real sine, then the same sweep)
P873_2C9 = sine_2c8(SINE_2C9, ALTER0_2C9 + "dc1 dc dev=V1 param=freq values=[0 100k]\n")
Q05_2C9 = sine_2c8(SINE_2C9, ALTER0_2C9 + block_2c9("dev=V1 param=freq", "0 100k", "op1 dc"))
P870_2C9 = sine_2c8(SINE_2C9, ALTER0_2C9 + block_2c9("dev=V1 param=freq", "0 100k"))
P871_2C9 = sine_2c8(SINE_2C9 + " dc=0.25", ALTER0_2C9 + "op1 dc\n" + block_2c9("dev=V1 param=freq", "0 100k", TRAN2_2C9))
P874_2C9 = src_2c9("V1 (in 0) vsource type=dc dc=0.25 sinedc=0.2 ampl=0.5 sinephase=30",
                   "a1 alter dev=V1 param=type value=sine\nop1 dc\n" + block_2c9("dev=V1 param=freq", "0 100k", TRAN2_2C9))
P872_2C9 = sine_2c8("sinedc=0.2 ampl=0.5 freq=0 sinephase=30", "a1 alter dev=V1 param=freq value=100k\n"
                    + block_2c9("dev=V1 param=freq", "0 200k"))
# the tau rules' clauses in a tran (plan._SRC_RULES), measured on VACASK 0.3.4-91 and Xyce (phase 2c9's decks:
# T1 tau1=0, VACASK `NaN found in vector' at delay + td1 = 0.5 us, Xyce a step; T2 tau2=0, VACASK `NaN found in vector'
# at delay + td2 = 2 us, Xyce `Time step too small near step number: 1036', exit 1)
TAU1_2C9 = TAU_2C8
TAU2_2C9 = ("an exp time constant of 0 is an error in v1 (ref5 p.685 gives no exp formula that defines it; VACASK would "
            "divide by it at the fall's start, delay + td2, and stop with 'NaN found in vector', and Xyce would stop "
            "with 'Time step too small')")
# G20073 (g8sb): a sine whose freq reads qfr, 0 at the netlist's values, every analysis sweeping it away from 0
QFR_HEAD_2C9 = "simulator lang=spectre\nparameters other=1 qfr=%s\n"
QFR_SINE_2C9 = "V1 (in 0) vsource type=sine sinedc=0 ampl=0.5 freq=qfr sinephase=90%s\n"
SETUP_2C9 = ("on VACASK the sine of V1 has freq=0 Hz, and VACASK stops at its setup with 'Frequency of sinusoidal "
             "transient must be greater than 0': not supported on VACASK in v1 (Xyce runs it)")


def qfr_2c9(tail, nominal="0", extra=""):
    return QFR_HEAD_2C9 % nominal + QFR_SINE_2C9 % extra + SKEPTIC_LOAD_2C8 + tail


class TestFollowUp2c9(unittest.TestCase):
    """Phase 2c9 (the 2c8 skeptic's open items).  N1: VACASK's deck chose the 1e-30 of a constant sine's swept freq of 0
    from the instance line, where plan.build reads the state the alters made, so a sine an alter made the constant sine
    printed values=[0.0, ...] and VACASK stopped ('Frequency of sinusoidal transient must be greater than 0'; P873 and
    Q05 ran right in 2c7).  N3: a dev= sweep of dc left a sinedc that defaults to dc where it was (P824, P825).  N4:
    Xyce's PULSE without PER repeats at TSTOP, so a pulse without a period whose delay is negative repeated inside the
    tran (P868, G10127).  N5: the tau2 refusal's clause was tau1's, and VACASK's setup before a sweep's first point at
    the values in force (the netlist's own, G20073) stopped it with no refusal."""

    fails = TestFollowUp2c8.fails
    builds = TestFollowUp2c8.builds
    one = TestFollowUp2c8.one

    def deck_line(self, text, head):
        hits = [ln for ln in text.splitlines() if ln.strip().startswith(head)]
        self.assertEqual(len(hits), 1, "%s\n%s" % (head, text))
        return hits[0].strip()

    # -- N1 ---------------------------------------------------------------------------------------------------------
    def test_n1_the_shared_predicate_reads_the_state_s_source(self):
        """plan.swept_value is the substitution every consumer applies (plan.build's source states, VACASK's sweep
        statements, Xyce's .STEP/.DC lists): a rise or fall of 0 of a pulse is transres, a freq of 0 of the constant
        sine its CONSTANT_SINE_FREQ, every other value, field or state as it is."""
        pulse = spectre.resolve_source({"type": Str("pulse"), "rise": N(1e-6)}, "pulse", [3.5e-6], [])
        csine = T.constant_sine(spectre.resolve_source({"type": Str("sine"), "ampl": N(0.5)}, "sine", [], []))
        real = spectre.resolve_source({"type": Str("sine"), "freq": N(1e5)}, "sine", [], [])
        dcsrc = spectre.resolve_source({"type": Str("dc"), "dc": N(1.0), "rise": N(1e-6)}, "dc", [], [])
        self.assertTrue(T.is_constant_sine(csine))
        cases = [("rise", pulse, 0.0, 3.5e-15), ("fall", pulse, 0.0, 3.5e-15), ("rise", pulse, 1e-6, 1e-6),
                 ("freq", csine, 0.0, T.CONSTANT_SINE_FREQ), ("freq", csine, 1e5, 1e5), ("freq", real, 0.0, 0.0),
                 ("rise", dcsrc, 0.0, 0.0), ("dc", pulse, 0.0, 0.0), ("freq", None, 0.0, 0.0)]
        for field, src, v, want in cases:
            with self.subTest(field=field, v=v, wave=getattr(src, "wave", None)):
                self.assertEqual(PL.swept_value(field, src, v, 3.5e-15), want)
        spec = SweepSpec("dev", "V1", "freq", "values", values=[N(0), N(1e5)])
        self.assertEqual(PL.swept_points("freq", csine, spec, 3.5e-15), [T.CONSTANT_SINE_FREQ, 1e5])
        self.assertIsNone(PL.swept_points("freq", real, spec, 3.5e-15))      # nothing to substitute: the grid as it is

    def test_n1_a_sweep_of_the_freq_of_a_sine_an_alter_made_constant_reads_the_alter_s_state(self):
        """P873 (a dc analysis), Q05 (a sweep block around an op), P870 (around a tran), P871 (a given dc) and P874 (an
        alter of the type to sine without a freq): the freq point of 0 is the constant's 1e-30 on VACASK as on Xyce
        (2c8's VACASK deck read the instance line's sine of 200 kHz and printed 0.0, VACASK exit 1)."""
        for name, deck, kw in (("P873", P873_2C9, ".DC"), ("Q05", Q05_2C9, ".STEP"), ("P870", P870_2C9, ".STEP"),
                               ("P871", P871_2C9, ".STEP"), ("P874", P874_2C9, ".STEP")):
            with self.subTest(deck=name, engine="vacask"):
                self.assertIn('instance="V1" parameter="freq" values=[1e-30, 100000.0]', vacask_deck(deck))
            with self.subTest(deck=name, engine="xyce"):
                self.assertTrue(any("%s vamos_x_V1_freq LIST 1e-30 100000.0" % kw in d for d in xyce_decks(deck)), name)

    def test_n1_the_constant_sine_an_alter_made_a_real_sine_sweeps_its_freq_as_a_sine(self):
        """P872: the instance line's constant sine altered to 100 kHz is a real sine in the sweep's state, whose freq
        of 0 is the sine at freq 0: VACASK's refusal alone, as plan.build gives it (the deck no longer prints the 1e-30
        of the instance line's constant), Xyce's SIN at FREQ 0 the constant."""
        self.one(self.fails(P872_2C9, "vacask"), "tran tr1 at V1:freq=0 (sweep sw1; after alter a1): on VACASK the sine "
                 "of V1 has freq=0 Hz, ", "not supported on VACASK in v1 (Xyce runs it)")
        self.assertIn(".STEP vamos_x_V1_freq LIST 0.0 200000.0", xyce_decks(P872_2C9)[0])

    def test_n1_a_rise_or_fall_of_0_reads_the_state_s_waveform(self):
        """A dc source whose type an alter makes a pulse: a swept rise or fall of 0 is transres on both engines in the
        pulse's state, as plan.build's source state reads it; so is one of a pulse whose fall an alter set (P878)."""
        line = "V1 (in 0) vsource type=dc dc=0 val0=0 val1=1 delay=0.5u rise=0.5u fall=0.5u width=2u period=20u"
        for param in ("rise", "fall"):
            deck = src_2c9(line, "a1 alter dev=V1 param=type value=pulse\n" + block_2c9("dev=V1 param=%s" % param,
                                                                                    "0 0.5u"))
            with self.subTest(param=param):
                self.assertIn('parameter="%s" values=[3.5e-15, 5e-07]' % param, vacask_deck(deck))
                self.assertIn(".STEP vamos_x_V1_%s LIST 3.5e-15 5e-07" % param, xyce_decks(deck)[0])
        p878 = pulse_2c8(tail="a1 alter dev=V1 param=fall value=0\n" + block_2c9("dev=V1 param=fall", "0 0.3u"))
        self.assertIn('parameter="fall" values=[3.5e-15, 3e-07]', vacask_deck(p878))
        self.assertIn(".STEP vamos_x_V1_fall LIST 3.5e-15 3e-07", xyce_decks(p878)[0])

    # -- N3 ---------------------------------------------------------------------------------------------------------
    def test_n3_a_dev_sweep_of_dc_moves_a_sinedc_that_defaults_to_dc(self):
        """ref5 p.684: sinedc defaults to dc, the one source field whose default is another field (§3.8.1's table:
        width and period default to a constant, rise and fall to the tran's transres, an absent dc is the derived t = 0
        value, which follows its fields already).  P824 and P825 (a sweep block of dc around a tran): VACASK sweeps the
        sine's sinedc there (its dc acts under type="dc" only), Xyce's SIN reads the dc's hook as its V0; 2c8 left the
        sine centred on the instance line's 0.25 at the 0.5 point on both engines.  The same without a dc and on an
        isource; a DC analysis sweeps dc as before, a given sinedc stays, and a dc that reads a name prints both places
        through its hook on VACASK."""
        cases = [("P824", "ampl=0.5 freq=100k dc=0.25", "100000.0", "0.0"),
                 ("P825", "ampl=0.5 freq=0 sinephase=30 dc=0.25", "1e-30", "30.0"),
                 ("nodc", "ampl=0.5 freq=100k", "100000.0", "0.0")]
        for name, fields, freq, phase in cases:
            deck = sine_2c8(fields, block_2c9("dev=V1 param=dc", "0.25 0.5"))
            with self.subTest(deck=name, engine="vacask"):
                self.assertIn('sweep vamos_w1 instance="V1" parameter="sinedc" values=[0.25, 0.5]', vacask_deck(deck))
                self.assertFalse(any("changes nothing in the transient" in n.message
                                     for n in self.builds(deck, "vacask").notes))
            with self.subTest(deck=name, engine="xyce"):
                self.assertIn("V1 in 0 DC {vamos_x_V1_dc} SIN({vamos_x_V1_dc} 0.5 %s 0.0 0.0 %s)" % (freq, phase),
                              xyce_decks(deck)[0])
        isrc = src_2c9("I1 (0 in) isource type=sine ampl=0.5m freq=100k dc=0.25m",
                       block_2c9("dev=I1 param=dc", "0.25m 0.5m"))
        self.assertIn('sweep vamos_w1 instance="I1" parameter="sinedc" values=[0.00025, 0.0005]', vacask_deck(isrc))
        self.assertIn("I1 0 in DC {vamos_x_I1_dc} SIN({vamos_x_I1_dc} 0.0005 100000.0", xyce_decks(isrc)[0])
        # a sweep block around an op and a tran: the op sweeps dc (type="dc"), the tran the sinedc
        both = sine_2c8("ampl=0.5 freq=100k dc=0.25", "sw1 sweep dev=V1 param=dc values=[0.25 0.5] {\n  op1 dc\n"
                        "  tr1 tran stop=3.5u maxstep=2n\n}\n")
        text = vacask_deck(both)
        self.assertIn('sweep vamos_w1 instance="V1" parameter="dc" values=[0.25, 0.5]', text)
        self.assertIn('sweep vamos_w2 instance="V1" parameter="sinedc" values=[0.25, 0.5]', text)
        decks = xyce_decks(both)
        self.assertIn("SIN({vamos_x_V1_dc} 0.5 100000.0", decks[1])
        # a dc analysis: the DC solve reads dc, on VACASK under type="dc"
        dc1 = sine_2c8("ampl=0.5 freq=100k dc=0.25", "dc1 dc dev=V1 param=dc values=[0.25 0.5]\n")
        self.assertIn('sweep vamos_w1 instance="V1" parameter="dc" values=[0.25, 0.5]', vacask_deck(dc1))
        # a given sinedc does not follow dc: the swept dc changes nothing in the tran, as 2c8 noted
        given = sine_2c8("sinedc=0.1 ampl=0.5 freq=100k dc=0.25", block_2c9("dev=V1 param=dc", "0.25 0.5"))
        self.assertIn('sweep vamos_w1 instance="V1" parameter="dc" values=[0.25, 0.5]', vacask_deck(given))
        self.assertIn("V1 in 0 DC {vamos_x_V1_dc} SIN(0.1 0.5 100000.0", xyce_decks(given)[0])
        self.assertTrue(any("changes nothing in the transient" in n.message for n in self.builds(given, "vacask").notes))
        # dc=vv: the hook prints both places on VACASK (sinedc=(hook) dc=(hook)), which the hook's sweep moves together
        hooked = sine_2c8("ampl=0.5 freq=100k dc=vv", block_2c9("dev=V1 param=dc", "0.25 0.5")).replace(
            "fr=100k", "fr=100k vv=0.25")
        text = vacask_deck(hooked)
        line = self.deck_line(text, "V1 (in 0)")
        self.assertEqual(line.count("vamos_af1 ? vamos_av1 : vv"), 2, line)
        self.assertIn('sweep vamos_w1 variable="vamos_av1" values=[0.25, 0.5]', text)
        self.assertIn("V1 in 0 DC {vamos_x_V1_dc} SIN({vamos_x_V1_dc} 0.5 100000.0", xyce_decks(hooked)[0])
        # an alter of dc before the sweep keeps sinedc at its default (the alter's re-resolved source); an alter of
        # sinedc gives it: the dc sweep leaves it; a sweep of sinedc around the dc sweep gives it at every point
        fields = "ampl=0.5 freq=100k dc=0.25"
        cases = [("alter dc", "a1 alter dev=V1 param=dc value=0.4\n" + block_2c9("dev=V1 param=dc", "0.25 0.5"),
                  'sweep vamos_w1 instance="V1" parameter="sinedc"', "SIN({vamos_x_V1_dc} 0.5"),
                 ("alter sinedc", "a1 alter dev=V1 param=sinedc value=0.1\n" + block_2c9("dev=V1 param=dc", "0.25 0.5"),
                  'sweep vamos_w1 instance="V1" parameter="dc"', "SIN(0.1 0.5"),
                 ("both", "sw0 sweep dev=V1 param=sinedc values=[0 0.1] {\n" + block_2c9("dev=V1 param=dc", "0.25 0.5")
                  + "}\n", 'sweep vamos_w2 instance="V1" parameter="dc"', "SIN({vamos_x_V1_sinedc} 0.5")]
        for name, tail, vk, xy in cases:
            with self.subTest(deck=name):
                deck = sine_2c8(fields, tail)
                self.assertIn(vk, vacask_deck(deck))
                self.assertIn(xy, xyce_decks(deck)[0])

    # -- N4 ---------------------------------------------------------------------------------------------------------
    def test_n4_a_pulse_without_a_period_cannot_repeat_on_xyce(self):
        """Xyce's PULSE without PER takes PER = TSTOP (N_DEV_SourceData.C, PulseData::initializeSource), so a pulse
        whose delay is negative started again at delay + TSTOP, inside the tran (P868: in(1.35 us) = 1 where 0; G10127,
        an isource): a Spectre pulse without a period whose delay is not a constant >= 0 prints PER 2e30 on Xyce, which
        no tran reaches.  A constant delay >= 0 (the second pulse after TSTOP) and an explicit period print as before;
        VACASK, a period of 0 a single pulse, and the HSPICE path are unchanged."""
        line = "V1 (in 0) vsource type=pulse val0=0 val1=1 delay=%s rise=0.5u fall=0.5u width=2u"
        p868 = src_2c9(line % "-2.7u", "op1 dc\n" + TR_2C8)
        for d in xyce_decks(p868):
            self.assertIn("PULSE(0.0 1.0 -2.7e-06 5e-07 5e-07 2e-06 2e+30)\n", d)
        self.assertIn('type="pulse" val0=0.0 val1=1.0 delay=-2.7e-06 rise=5e-07 fall=5e-07 width=2e-06\n',
                      vacask_deck(p868))
        g10127 = src_2c9("I1 (0 in) isource type=pulse val0=0 val1=0.001 delay=-0.2u rise=0.5u fall=0.5u width=1u "
                         "dc=0.0003", TR_2C8 + "op2 dc\n")
        self.assertIn("I1 0 in DC 0.0003 PULSE(0.0 0.001 -2e-07 5e-07 5e-07 1e-06 2e+30)\n", xyce_decks(g10127)[0])
        dl = src_2c9(line % "dl", "a1 alter param=dl value=-2.7u\n" + TR_2C8).replace("fr=100k", "fr=100k dl=0.5u")
        self.assertIn("PULSE(0.0 1.0 {dl} 5e-07 5e-07 2e-06 2e+30)\n", xyce_decks(dl)[0])
        swept = src_2c9(line % "0.5u", block_2c9("dev=V1 param=delay", "0.5u -2.7u"))
        self.assertIn("PULSE(0.0 1.0 {vamos_x_V1_delay} 5e-07 5e-07 2e-06 2e+30)\n", xyce_decks(swept)[0])
        self.assertIn("PULSE(0.0 1.0 5e-07 5e-07 5e-07 2e-06)\n", xyce_decks(src_2c9(line % "0.5u", TR_2C8))[0])
        self.assertIn("PULSE(0.0 1.0 -2.7e-06 5e-07 5e-07 2e-06 2e-05)\n",
                      xyce_decks(src_2c9(line % "-2.7u" + " period=20u", TR_2C8))[0])
        from vamos.netlist import xyce
        nl = Netlist(title="hspice")
        nl.body = [Instance("v1", "v", ["a", "0"], source=Source(wave="pulse", args={
            "v1": N(0), "v2": N(1), "td": N(-2.7e-6), "tr": N(5e-7), "tf": N(5e-7), "pw": N(2e-6)}), origin="t.sp:2"),
            R("r1", "a", "0", 1000.0)]
        nl.analyses = [Analysis("tran", {"step": 1e-9, "stop": 3.5e-6}, "t.sp:9")]
        self.assertIn("v1 a 0 PULSE(0.0 1.0 -2.7e-06 5e-07 5e-07 2e-06)\n", xyce.render(nl))

    # -- N5 ---------------------------------------------------------------------------------------------------------
    def test_n5_tau1_and_tau2_have_their_own_clauses(self):
        """tau1 = 0: VACASK divides by it at the exp's start (`NaN found in vector'), Xyce makes a step; tau2 = 0: VACASK
        divides by it at the fall's start, delay + td2, Xyce stops with `Time step too small' (2c8 gave tau2 tau1's
        clause).  On both engines, in a tran; the constant, the derived dc and no-analysis forms keep the rule's words."""
        for engine in ("vacask", "xyce"):
            with self.subTest(tau=1, engine=engine):
                self.assertEqual(self.one(self.fails(exp_2c8("0"), engine), "tran tr1: the exp of V1 has tau1=0 s: "),
                                 "tran tr1: the exp of V1 has tau1=0 s: " + TAU1_2C9)
            with self.subTest(tau=2, engine=engine):
                self.assertEqual(self.one(self.fails(exp_2c8(tau2="0"), engine), "tran tr1: the exp of V1 has tau2=0 s: "),
                                 "tran tr1: the exp of V1 has tau2=0 s: " + TAU2_2C9)
            with self.subTest(tau="2 altp", engine=engine):
                deck = exp_2c8(tau2="tau", tail="a1 alter param=tau value=0\n" + TR_2C8)
                self.assertEqual(self.one(self.fails(deck, engine), "tran tr1 (after alter a1): the exp of V1 has tau2"),
                                 "tran tr1 (after alter a1): the exp of V1 has tau2=0 s: " + TAU2_2C9)

    def test_n5_vacask_s_setup_before_a_sweep_s_first_point_is_checked(self):
        """VACASK sets every source up in the values in force before a sweep's first point (at the netlist's values for
        its first analysis, after a change otherwise; measured in phase 2c9, decks V4, V7, V8): G20073 (qfr=0 at
        the netlist's values, swept to 100k and 200k) stopped VACASK with `Initial circuit setup failed', exit 1, with no
        refusal.  Refused on VACASK now, Xyce running it; so are a tran in the sweep (a given dc's waveform switched on
        before it), a dc analysis of qfr, and an alter of qfr to 0 before the sweep, before or after another analysis.
        A given dc around an op (type="dc": VACASK does not set the sine up) and an alter of qfr away from 0 before the
        sweep build on VACASK."""
        g20073 = qfr_2c9(block_2c9("param=qfr", "100k 200k", "op1 dc"))
        self.assertEqual(self.one(self.fails(g20073, "vacask"), "dc op1, VACASK's setup before the first point of sweep "
                                  "sw1: "), "dc op1, VACASK's setup before the first point of sweep sw1: " + SETUP_2C9)
        self.builds(g20073, "xyce")
        refused = [("tran", qfr_2c9(block_2c9("param=qfr", "100k 200k")), "tran tr1, VACASK's setup before the first "
                    "point of sweep sw1: "),
                   ("given tran", qfr_2c9(block_2c9("param=qfr", "100k 200k"), extra=" dc=0.1"), "tran tr1, VACASK's "
                    "setup before the first point of sweep sw1: "),
                   ("dc analysis", qfr_2c9("dc1 dc param=qfr values=[100k 200k]\n"), "dc dc1, VACASK's setup before "
                    "the first point of its sweep of qfr: "),
                   ("alter", qfr_2c9("a1 alter param=qfr value=0\n" + block_2c9("param=qfr", "100k", "op1 dc"), "50k"),
                    "dc op1, VACASK's setup before the first point of sweep sw1 (after alter a1): "),
                   ("after op0", qfr_2c9("op0 dc\na1 alter param=qfr value=0\n" + block_2c9("param=qfr", "100k 200k",
                                                                                           "op1 dc"), "100k"),
                    "dc op1, VACASK's setup before the first point of sweep sw1 (after alter a1): ")]
        for name, deck, head in refused:
            with self.subTest(deck=name):
                self.assertEqual(self.one(self.fails(deck, "vacask"), head), head + SETUP_2C9)
                self.builds(deck, "xyce")
        for name, deck in (("given op", qfr_2c9(block_2c9("param=qfr", "100k 200k", "op1 dc"), extra=" dc=0.1")),
                           ("alter away", qfr_2c9("a1 alter param=qfr value=100k\n" + block_2c9("param=qfr", "100k 200k",
                                                                                               "op1 dc")))):
            with self.subTest(builds=name):
                self.builds(deck, "vacask")


class TestDriver2c9(unittest.TestCase):
    """The driver's two fixes after the 2c9 skeptic.  A source's mag defaults to 0 when the netlist gives only phase
    (ref5 p.685: 'mag=0 V  Small signal voltage'): the parser and a sweep's source state used 1, so phase=45 alone
    excited the ac on both engines (the 2c9 skeptic's K61).  And tau1's refusal clause: Xyce makes a step or, for some
    start times, stops with 'Time step too small' (the 2c9 skeptic's td1=1u and td1=0.7u)."""

    def test_a_phase_alone_excites_nothing(self):
        src = spectre.resolve_source({"type": Str("dc"), "dc": N(0.0), "phase": N(45.0)}, "dc", [], [])
        self.assertEqual(src.ac, (N(0.0), N(45.0)))
        src = spectre.resolve_source({"type": Str("dc"), "dc": N(0.0), "mag": N(1.0), "phase": N(45.0)}, "dc", [], [])
        self.assertEqual(src.ac, (N(1.0), N(45.0)))
        src = spectre.resolve_source({"type": Str("dc"), "dc": N(0.0), "mag": N(2.0)}, "dc", [], [])
        self.assertEqual(src.ac, (N(2.0), N(0.0)))
        self.assertIsNone(spectre.resolve_source({"type": Str("dc"), "dc": N(0.0)}, "dc", [], []).ac)

    def test_tau1_s_clause_names_both_xyce_outcomes(self):
        clause = PL._SRC_RULES["tau1"][2]
        self.assertIn("Xyce would make a step or, for some start times, stop with 'Time step too small'", clause)
        self.assertNotEqual(clause, PL._SRC_RULES["tau2"][2])


if __name__ == "__main__":
    unittest.main()
