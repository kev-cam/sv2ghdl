"""S2 T0 tests of netlist/plan.py (docs/VAMOS_SPECTRE_DESIGN.md §5, §11.1 "Plan"); both legs, no engine.

    cd tests/vamos && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_spectre_plan

The IR is built by hand from the frozen dataclasses (phase 0), with numeric expressions or ones in
the hspice dialect where the Spectre semantics do not differ, and plan.DIALECT set to "hspice" for
the test (the Spectre evaluator is S1's).  Tests that need another owner's function (S1's
select_bin_spectre / resolve_source, S3's dialect="spectre" leg of tables.path_envs) are marked as
S6 integration tests and skipped with a reason naming the owner.
"""

import copy
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
        # (measured: op2 out = 0.333, R1 = 2k, where Xyce and Spectre keep a4's 4k, out = 0.2), so VACASK refuses the
        # deck; the plan's actions are the same on Xyce, which runs it
        errs = self.fails(nl, "vacask")
        for alter, obj in (("a3", "X1"), ("a4", "X1.R1")):
            self.assertTrue(any(e.startswith("dc op2: on VACASK, alter %s set r of %s, which the netlist binds" % (alter, obj))
                                for e in errs), errs)
        plan = self.build(nl, "xyce")
        alters = [a.args for a in plan.actions if a.op == "alter"]
        self.assertEqual(alters, [{"target": ("variable", "rval", ""), "value": 2000.0},
                                  {"target": ("option", "temp", ""), "value": 50.0},
                                  {"target": ("instance", "X1", "r"), "value": 3000.0},
                                  {"target": ("instance", "X1.R1", "r"), "value": 4000.0}])
        self.assertEqual([a.op for a in plan.actions], ["options", "var", "analysis", "alter", "alter", "alter", "alter", "analysis"])
        op1, op2 = self.steps(plan)
        self.assertEqual((op1.options["temp"], op2.options["temp"]), (27.0, 50.0))
        self.assertEqual(plan.variables, {"rval": 1000.0})       # nominal, never the altered value
        self.assertEqual(plan.overridden, {"rcsec": {"r"}})

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
        # an alter of it works; Xyce sweeps it natively (.DC X2:R1:R)
        a = an("dc", "s1", sweep=sw("dev", "X1.R1", "r", mode="values", values=[1e3, 2e3]))
        errs = self.fails(rc_netlist([a]), "vacask")
        self.assertTrue(any("X1.R1 is bound to the expression r" in e and "sub=" in e for e in errs), errs)
        self.build(rc_netlist([a]), "xyce")
        self.build(rc_netlist([an("alter", "a1", dev="X1.R1", param="r", value=2e3)]), "vacask")
        nl = rc_netlist([an("dc", "s1", sweep=sw("dev", "Rt", "r", mode="values", values=[1e3, 2e3]))])
        nl.body.insert(1, R("Rt", "in", "0", "rval*2"))
        errs = self.fails(nl, "vacask")
        self.assertTrue(any("Rt is bound to the expression rval * 2.0" in e and "reads rval" in e for e in errs), errs)
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
        expression'); every master's value parameter and a source field now get the status 2 before the run on
        VACASK, while Xyce, which sweeps them (.DC through hooks), plans them as before."""
        sweeps = [("dc1 dc dev=E1 param=gain values=[1 2 3]\n", "gain of E1 is bound to the expression g"),
                  ("dc1 dc dev=G1 param=gm values=[1m 2m]\n", "gm of G1 is bound to the expression gg"),
                  ("dc1 dc dev=H1 param=rm values=[1k 2k]\n", "rm of H1 is bound to the expression rr"),
                  ("sw1 sweep dev=F1 param=gain values=[2 3] {\n  op1 dc\n}\n", "gain of F1 is bound to the expression fg"),
                  ("ac1 ac dev=K1 param=coupling values=[0.25 0.5] freq=1k\n",
                   "coupling of K1 is bound to the expression kk"),
                  ("dc1 dc dev=D1 param=area values=[1 2]\n", "area of D1 is bound to the expression ar"),
                  ("dc1 dc dev=V1 param=dc values=[1 2]\n", "dc of V1 is bound to the expression vv")]
        for tail, msg in sweeps:
            with self.subTest(tail=tail):
                errs = self.fails(CONTROLLED + tail, "vacask")
                self.assertTrue(any(("on VACASK " + msg) in e and "sweep what it reads instead" in e for e in errs), errs)
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
        and its parameter, the expression and the change, at the first point where the readings differ."""
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
                    self.assertEqual(len(self.hits(errs, where, G1_CHANGED, *pieces)), 1, errs)

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
        Xyce, and Spectre under either reading, keep the altered 3k (0.25).  Status 2 on VACASK; Xyce runs them."""
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
            with self.subTest(deck=deck.splitlines()[-2:]):
                errs = self.fails(deck, "vacask")
                alter = "alter a3" if "a3 alter dev=R1" in deck else "alter a1"
                self.assertEqual(len(self.hits(errs, where, "on VACASK, %s set " % alter, *G1_VACASK, changer)), 1, errs)
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


if __name__ == "__main__":
    unittest.main()
