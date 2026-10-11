"""S2 T0 tests of netlist/plan.py (docs/VAMOS_SPECTRE_DESIGN.md §5, §11.1 "Plan"); both legs, no engine.

    cd tests/vamos && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_spectre_plan

The IR is built by hand from the frozen dataclasses (phase 0), with numeric expressions or ones in
the hspice dialect where the Spectre semantics do not differ, and plan.DIALECT set to "hspice" for
the test (the Spectre evaluator is S1's).  Tests that need another owner's function (S1's
select_bin_spectre / resolve_source, S3's dialect="spectre" leg of tables.path_envs) are marked as
S6 integration tests and skipped with a reason naming the owner.
"""

import unittest

from vamos_testlib import ROOT  # noqa: F401  (puts the repository root on sys.path)

from vamos.netlist import expr as X  # noqa: E402
from vamos.netlist import plan as PL  # noqa: E402
from vamos.netlist import tables as T  # noqa: E402
from vamos.netlist.expr_ast import Binary, Name, Num, Str  # noqa: E402
from vamos.netlist.ir import (Analysis, Cond, Instance, Model, Netlist, Param, ParamTest, Source,  # noqa: E402
                              Subckt, SweepSpec)
from vamos.notes import ERROR, NOTE, WARNING, NoteError  # noqa: E402
from vamos.spectre.job import Settings  # noqa: E402

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
        self.assertEqual((lvl.target, lvl.label, lvl.units), (("model", "nch", "vto"), "nch:vto", ""))
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


class TestAlters(PlanCase):
    def test_alter_targets(self):
        nl = rc_netlist([an("dc", "op1"),
                         an("alter", "a1", param="rval", value=2000.0),
                         an("alter", "a2", param="temp", value=50.0),
                         an("alter", "a3", sub="X1", param="r", value=3000.0),
                         an("alter", "a4", dev="X1.R1", param="r", value=4000.0),
                         an("dc", "op2")])
        plan = self.build(nl)
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

    def test_xyce_tolerance_note(self):
        st, plan = self.tran("xyce", stop=1e-3)
        self.assertTrue(any("Xyce takes the method" in n.message for n in plan.notes))

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

    def test_ac_errors(self):
        nl = rc_netlist([an("ac", "a1", skipdc="yes", sweep=self.freq())])
        self.assertTrue(any("skipdc=yes" in e for e in self.fails(nl)))
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


if __name__ == "__main__":
    unittest.main()
