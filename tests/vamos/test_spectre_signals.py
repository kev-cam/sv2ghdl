"""S2 T0 tests of netlist/signals.py (docs/VAMOS_SPECTRE_DESIGN.md §6, §11.1 "Signals"); both legs, no engine.

    cd tests/vamos && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_spectre_signals

The IR and the RunPlan are built by hand from the frozen dataclasses (phase 0); plan.DIALECT is
set to "hspice" for the test (the Spectre evaluator is S1's).  Engine columns are render's (S3):
these tests check the Refs, the Spectre names, the psf types, the order and the render copies.
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
from vamos.netlist.expr_ast import Name, Num, Str  # noqa: E402
from vamos.netlist.ir import Analysis, Instance, Model, Netlist, Param, SaveSpec, Source, Subckt, SweepSpec  # noqa: E402
from vamos.netlist.plan import Action, AnalysisStep, RunPlan  # noqa: E402
from vamos.notes import ERROR, NOTE, WARNING  # noqa: E402

P = X.parse


def N(v):
    return Num(float(v))


def inst(name, kind, nodes, prim, **kw):
    params = {k: (Str(v) if isinstance(v, str) else N(v)) for k, v in kw.pop("params", {}).items()}
    return Instance(name, kind, list(nodes), params=params, origin="t.scs:1", prim=prim, **kw)


def vsrc(name, p, n, dc=0.0, prim="vsource"):
    return inst(name, "v", [p, n], prim, source=Source(dc=N(dc)))


def step(id, name, kind, **args):
    return AnalysisStep(id, name, kind, args=args)


def plan_of(*steps, engine="vacask", dependents=()):
    rp = RunPlan(engine=engine, dependents=set(dependents))
    rp.actions.append(Action("options", {"temp": 27.0}))
    for s in steps:
        rp.actions.append(Action("analysis", {}, s))
    return rp


def testbench():
    """A divider with a subckt, every current kind of §6.3 and a nested net.

      V1 (in 0) vsource dc=1; IP (in a) iprobe; L1 (a b) inductor; R1 (b out) resistor; R0 (out o2) r=0
      E1 (e1 0 out 0) vcvs; D1 (out 0) diode dm; M1 (out in 0 0) mos nch; X1 (out 0) rcsec r=1k
      I1 (out 0) isource dc=0; Rz (o2 0) r=inf (not printed)
      subckt rcsec (a b): R1 (a mid) resistor r=r; C1 (mid b) capacitor
    """
    nl = Netlist(title="tb", dialect="spectre", temp=27.0, tnom=27.0)
    nl.body += [
        Param("rval", N(1000.0), "t.scs:2"),
        Model("dm", "d", 1.0, {"is": N(1e-14)}, "t.scs:3", prim="diode"),
        Model("nch", "nmos", 1.0, {"vto": N(0.5)}, "t.scs:3", prim="mos1"),
        Model("rref", "r", None, {"tc1": N(1e-3)}, "t.scs:3", prim="resistor"),
        vsrc("V1", "in", "0", 1.0),
        inst("IP", "v", ["in", "a"], "iprobe", source=Source(dc=N(0.0))),
        inst("L1", "l", ["a", "b"], "inductor", value=N(1e-6)),
        inst("R1", "r", ["b", "out"], "resistor", value=N(1e3)),
        inst("R0", "r", ["out", "o2"], "resistor", value=N(0.0)),
        inst("E1", "e", ["e1", "0", "out", "0"], "vcvs", value=N(2.0)),
        inst("D1", "d", ["out", "0"], "diode", master="dm"),
        inst("M1", "m", ["out", "in", "0", "0"], "mos1", master="nch", params={"w": 1e-6, "l": 1e-6}),
        Instance("X1", "x", ["out", "0"], master="rcsec", params={"r": Name("rval")}, origin="t.scs:9"),
        inst("I1", "i", ["out", "0"], "isource", source=Source(dc=N(0.0))),
        inst("Rz", "r", ["o2", "0"], "resistor", value=N(float("inf"))),
        Subckt("rcsec", ["a", "b"], [Param("r", N(1000.0))],
               [inst("R1", "r", ["a", "mid"], "resistor", value=Name("r"), master="rref"),
                inst("C1", "c", ["mid", "b"], "capacitor", value=N(1e-9))], origin="t.scs:20"),
    ]
    nl.values["rval"] = 1000.0
    return nl


def names(sigs):
    return [name for _, name, _ in sigs]


def refs(sigs):
    return [ref for ref, _, _ in sigs]


def instances(body):
    return {it.name: it for it in body if isinstance(it, Instance)}


class SignalsCase(unittest.TestCase):
    def setUp(self):
        self._dialect = PL.DIALECT
        PL.DIALECT = "hspice"

    def tearDown(self):
        PL.DIALECT = self._dialect

    def resolve(self, nl, *steps, engine="vacask", saves=(), options=None, dependents=()):
        """saves: each a list of items, or a tuple (items, SaveSpec keywords)."""
        specs = []
        for s in saves:
            items, kw = (s if isinstance(s, tuple) else (s, {}))
            specs.append(SaveSpec(list(items), origin="t.scs:30", **kw))
        nl.saves = specs
        if options:
            nl.options.update({k: (Str(v) if isinstance(v, str) else N(v)) for k, v in options.items()})
        steps = steps or (step("vamos_a1", "op1", "dc"),)
        rp = plan_of(*steps, engine=engine, dependents=dependents)
        return SG.resolve(nl, rp)


# -- §6.1 allpub and the save modes ---------------------------------------------------------------------------

class TestAllpub(SignalsCase):
    def test_membership_and_order(self):
        nl = testbench()
        nl2, sm, notes = self.resolve(nl)
        sigs = sm.per_step["vamos_a1"]
        self.assertEqual(sm.allpub, {"vamos_a1"})
        self.assertEqual(names(sigs), ["a", "b", "e1", "in", "IP:in", "L1:1", "o2", "out", "V1:p", "X1.mid"])
        self.assertEqual(refs(sigs)[4:6], [("i", "IP"), ("i", "L1")])
        self.assertEqual(refs(sigs)[-1], ("v", "X1.mid"))
        types = {name: t for _, name, t in sigs}
        self.assertEqual((types["out"], types["V1:p"], types["X1.mid"]), ("V", "I", "I" if False else "V"))
        for bad in ("E1:p", "R0:1", "D1:a", "I1:sink", "0", "Rz:1"):
            self.assertNotIn(bad, names(sigs))
        self.assertEqual(notes, [])

    def test_case_insensitive_sort(self):
        nl = testbench()
        nl.body.append(inst("Rc", "r", ["Zed", "alpha"], "resistor", value=N(1.0)))
        _, sm, _ = self.resolve(nl)
        n = names(sm.per_step["vamos_a1"])
        self.assertLess(n.index("alpha"), n.index("b"))
        self.assertLess(n.index("X1.mid"), n.index("Zed"))

    def test_selected_with_only_currents_is_allpub_plus_the_currents(self):
        nl = testbench()
        _, sm, _ = self.resolve(nl, saves=[["R1:1", "V1:p"]])
        sigs = sm.per_step["vamos_a1"]
        self.assertEqual(sm.allpub, {"vamos_a1"})
        self.assertEqual(names(sigs)[-1], "R1:1")                # the extra current after the sorted defaults
        self.assertEqual(names(sigs).count("V1:p"), 1)           # a duplicate dropped, the first wins
        self.assertEqual(refs(sigs)[-1], ("i", "R1"))

    def test_selected_with_a_node_is_the_explicit_list(self):
        nl = testbench()
        _, sm, _ = self.resolve(nl, saves=[["out", "in", "V1:p", "out"]])
        self.assertEqual(sm.per_step["vamos_a1"], [(("v", "out"), "out", "V"), (("v", "in"), "in", "V"), (("i", "V1"), "V1:p", "I")])
        self.assertEqual(sm.allpub, set())

    def test_save_option_modes(self):
        nl = testbench()
        _, sm, notes = self.resolve(nl, saves=[["out"]], options={"save": "allpub"})
        self.assertEqual(sm.allpub, {"vamos_a1"})
        self.assertIn("out", names(sm.per_step["vamos_a1"]))
        _, sm, notes = self.resolve(testbench(), options={"save": "lvlpub", "nestlvl": 1.0})
        self.assertEqual(names(sm.per_step["vamos_a1"]), ["a", "b", "e1", "in", "IP:in", "L1:1", "o2", "out", "V1:p"])
        _, sm, notes = self.resolve(testbench(), options={"save": "lvlpub", "nestlvl": 2.0})
        self.assertIn("X1.mid", names(sm.per_step["vamos_a1"]))
        _, sm, notes = self.resolve(testbench(), options={"save": "all"})
        self.assertTrue(any(n.severity == NOTE and "internal nodes" in n.message for n in notes))
        _, sm, notes = self.resolve(testbench(), saves=[["V1:p"]], options={"save": "none"})
        self.assertEqual(names(sm.per_step["vamos_a1"]), ["a", "V1:p"])
        self.assertTrue(any("save=none" in n.message for n in notes))
        _, sm, notes = self.resolve(testbench(), options={"save": "nooutput"})
        self.assertEqual(sm.per_step["vamos_a1"], [])
        self.assertEqual(sm.allpub, set())

    def test_per_analysis_save(self):
        nl = testbench()
        _, sm, _ = self.resolve(nl, step("vamos_a1", "op1", "dc", save="allpub"), step("vamos_a2", "op2", "dc"),
                                saves=[["out"]])
        self.assertEqual(sm.allpub, {"vamos_a1"})
        self.assertEqual(names(sm.per_step["vamos_a2"]), ["out"])

    def test_output_option_warnings(self):
        _, _, notes = self.resolve(testbench(), options={"currents": "all", "subcktprobelvl": 1.0, "pwr": "all",
                                                         "useprobes": "yes", "saveahdlvars": "all", "savefilter": "rc"})
        msgs = [n.text() for n in notes]
        for text in ("currents=all", "subcktprobelvl", "pwr=all", "saveahdlvars", "savefilter=rc"):
            self.assertTrue(any(m.startswith("warning") and text in m for m in msgs), (text, msgs))
        self.assertTrue(any(m.startswith("note") and "useprobes" in m for m in msgs), msgs)


# -- §6.2 save items, §6.3 currents -----------------------------------------------------------------------------

class TestSaveItems(SignalsCase):
    def test_terminal_names_and_indices(self):
        nl = testbench()
        nl2, sm, notes = self.resolve(nl, saves=[["D1:a", "D1:1", "I1:sink", "M1:d", "V1:n", "L1:2", "R1:2", "L1:1",
                                                  "E1:p", "X1:1", "X1:a", "out"]])
        sigs = sm.per_step["vamos_a1"]
        by = {name: ref for ref, name, _ in sigs}
        self.assertEqual(by["D1:a"], ("i", "vamos_tp1"))
        self.assertNotIn("D1:1", by)                              # the same terminal: a duplicate, the first wins
        self.assertEqual(by["I1:sink"], ("i", "vamos_tp2"))
        self.assertEqual(by["M1:d"], ("i", "vamos_tp3"))
        self.assertEqual(by["V1:n"], ("i", "vamos_tp4"))         # §6.3: the negation, measured by a probe
        self.assertEqual(by["L1:2"], ("i", "vamos_tp5"))
        self.assertEqual(by["R1:2"], ("i", "vamos_tp6"))
        self.assertEqual(by["L1:1"], ("i", "L1"))
        self.assertEqual(by["E1:p"], ("i", "E1"))
        self.assertEqual(by["X1:1"], ("i", "vamos_tp7"))
        self.assertNotIn("X1:a", by)                              # the port by name is the same terminal
        self.assertEqual([t for _, name, t in sigs if name != "out"], ["I"] * 9)
        self.assertEqual(names(sigs), ["D1:a", "I1:sink", "M1:d", "V1:n", "L1:2", "R1:2", "L1:1", "E1:p", "X1:1", "out"])
        self.assertEqual(notes, [])
        # the copy holds the probes; the input IR is untouched
        top = instances(nl2.body)
        self.assertEqual(top["D1"].nodes, ["vamos_tn1", "0"])
        self.assertEqual(top["vamos_tp1"].nodes, ["out", "vamos_tn1"])
        self.assertEqual((top["vamos_tp1"].kind, top["vamos_tp1"].prim, top["vamos_tp1"].source.dc), ("v", "vsource", N(0.0)))
        self.assertEqual(top["V1"].nodes, ["in", "vamos_tn4"])
        self.assertEqual(top["vamos_tp4"].nodes, ["0", "vamos_tn4"])
        self.assertEqual(top["X1"].nodes, ["vamos_tn7", "0"])
        self.assertEqual(instances(nl.body)["D1"].nodes, ["out", "0"])
        self.assertNotIn("vamos_tp1", instances(nl.body))

    def test_native_resistor_current_and_the_xyce_g_form(self):
        nl = testbench()
        nl.body.append(inst("Rq", "r", ["out", "0"], "resistor", value=N(1e3), params={"isnoisy": "no"}))
        nl.body.append(inst("Rt", "r", ["out", "0"], "resistor", value=N(1e3), params={"isnoisy": "no", "tc1": 1e-3}))
        _, sm, _ = self.resolve(copy.deepcopy(nl), saves=[["out", "R1:1", "Rq:1", "Rt:1", "R0:1"]])
        self.assertEqual(refs(sm.per_step["vamos_a1"])[1:], [("i", "R1"), ("i", "Rq"), ("i", "Rt"), ("i", "R0")])
        _, sm, _ = self.resolve(copy.deepcopy(nl), saves=[["out", "R1:1", "Rq:1", "Rt:1", "R0:1"]], engine="xyce")
        self.assertEqual(refs(sm.per_step["vamos_a1"])[1:], [("i", "R1"), ("i", "vamos_tp1"), ("i", "Rt"), ("i", "R0")])

    def test_resistor_current_with_an_ac_or_xf_step_is_probed_on_vacask(self):
        """VACASK's ac and acxf save no resistor current (p(r, i) is an op/dc/tran opvar), so a run with such
        a step probes the resistor terminal on VACASK (§6.3's generic rule); Xyce keeps I(R1)."""
        nl = testbench()
        _, sm, _ = self.resolve(copy.deepcopy(nl), step("vamos_a1", "op1", "dc"), step("vamos_a2", "ac1", "ac"),
                                saves=[["out", "R1:1", "V1:p"]])
        self.assertEqual(refs(sm.per_step["vamos_a1"]), [("v", "out"), ("i", "vamos_tp1"), ("i", "V1")])
        _, sm, _ = self.resolve(copy.deepcopy(nl), step("vamos_a1", "op1", "dc"), step("vamos_a2", "ac1", "ac"),
                                saves=[["out", "R1:1", "V1:p"]], engine="xyce")
        self.assertEqual(refs(sm.per_step["vamos_a1"]), [("v", "out"), ("i", "R1"), ("i", "V1")])
        _, sm, _ = self.resolve(copy.deepcopy(nl), step("vamos_a1", "op1", "dc"), step("vamos_a2", "tr1", "tran"),
                                saves=[["out", "R1:1"]])
        self.assertEqual(refs(sm.per_step["vamos_a1"]), [("v", "out"), ("i", "R1")])   # no small-signal step: native

    def test_multiplied_instances_are_probed_on_vacask(self):
        """VACASK's native r1.i and l1:flow(br) of an instance with m=2 hold one unit's current (half of
        Spectre's R1:1 and of Xyce's I(R1), XDM rlc), so a multiplied instance gets a probe on VACASK
        (phase 1b); Xyce keeps the native total."""
        nl = testbench()
        for name, it in instances(nl.body).items():
            if name in ("R1", "L1"):
                it.params["m"] = N(2.0)
        _, sm, _ = self.resolve(copy.deepcopy(nl), saves=[["out", "R1:1", "L1:1", "V1:p"]])
        # allpub (computed first) probes L1 as vamos_tp1; the explicit R1:1 gets vamos_tp2
        self.assertEqual(refs(sm.per_step["vamos_a1"]), [("v", "out"), ("i", "vamos_tp2"), ("i", "vamos_tp1"), ("i", "V1")])
        _, sm, _ = self.resolve(copy.deepcopy(nl), saves=[["out", "R1:1", "L1:1", "V1:p"]], engine="xyce")
        self.assertEqual(refs(sm.per_step["vamos_a1"]), [("v", "out"), ("i", "R1"), ("i", "L1"), ("i", "V1")])
        # allpub lists the inductor's current through the same rule
        _, sm, _ = self.resolve(copy.deepcopy(nl), saves=[["L1:1"]])
        self.assertIn(("i", "vamos_tp1"), refs(sm.per_step["vamos_a1"]))
        self.assertNotIn(("i", "L1"), refs(sm.per_step["vamos_a1"]))

    def test_infinite_resistor_and_unmatched_items(self):
        nl = testbench()
        _, sm, notes = self.resolve(nl, saves=[["Rz:1", "nosuch", "nosuch:1", "D1:oppoint", "R1:pwr", "D1:zz", "out"]])
        self.assertEqual(names(sm.per_step["vamos_a1"]), ["out"])
        msgs = [n.text() for n in notes]
        self.assertEqual(len(msgs), 6)
        self.assertTrue(all(m.startswith("warning") for m in msgs), msgs)
        self.assertTrue(any("infinite resistor" in m for m in msgs), msgs)
        self.assertTrue(any("nosuch matches no node or instance" in m for m in msgs), msgs)
        self.assertTrue(any("oppoint" in m for m in msgs) and any("power" in m for m in msgs), msgs)

    def test_bare_component_name_and_currents(self):
        nl = testbench()
        _, sm, notes = self.resolve(nl, saves=[["out", "M1", "D1:currents", "X1:currents", "M1:currents"]])
        n = names(sm.per_step["vamos_a1"])
        # a two-terminal component: the first only; a subckt instance: every terminal [M ref19 p.518]
        self.assertEqual(n, ["out", "M1:d", "M1:g", "M1:s", "M1:b", "D1:a", "X1:1", "X1:2"])
        self.assertTrue(any("operating point of M1" in x.message and x.severity == WARNING for x in notes))

    def test_node_wins_over_an_instance_and_hierarchical_names(self):
        nl = testbench()
        nl.body.append(inst("out", "r", ["b", "0"], "resistor", value=N(1.0)))     # an instance named like a node
        _, sm, notes = self.resolve(nl, saves=[["out", "X1.mid", "X1.R1:1", "X1.C1:2", "X1.a"]])
        sigs = sm.per_step["vamos_a1"]
        self.assertEqual(refs(sigs), [("v", "out"), ("v", "X1.mid"), ("i", "X1.R1"), ("i", "X1.vamos_tp1")])
        self.assertEqual(names(sigs), ["out", "X1.mid", "X1.R1:1", "X1.C1:2"])   # X1.a is the net out: a duplicate
        self.assertEqual(notes, [])

    def test_port_alias_name_is_kept_as_written(self):
        nl = testbench()
        _, sm, _ = self.resolve(nl, saves=[["X1.a"]])
        self.assertEqual(sm.per_step["vamos_a1"], [(("v", "out"), "X1.a", "V")])

    def test_inline_subckt_component_takes_the_instance_name(self):
        nl = testbench()
        nl.body.append(Model("qm", "npn", 1.0, {"bf": N(100.0)}, "t.scs:3", prim="bjt"))
        nl.body.append(Subckt("npn_mod", ["c", "b", "e"], [],
                              [inst("npn_mod", "q", ["c", "b", "e"], "bjt", master="qm")], origin="t.scs:40", inline=True))
        nl.body.append(Instance("q1", "x", ["out", "in", "0"], master="npn_mod", origin="t.scs:41"))
        _, sm, notes = self.resolve(nl, saves=[["out", "q1:c", "q1.npn_mod:1", "q1:e"]])
        sigs = sm.per_step["vamos_a1"]
        # the component by the caller's name (UG p.111); its IR path names the same terminal: a duplicate
        self.assertEqual(sigs[1:], [(("i", "q1.vamos_tp1"), "q1:c", "I"), (("i", "q1.vamos_tp2"), "q1:e", "I")])
        self.assertEqual(notes, [])
        _, sm, _ = self.resolve(nl, step("vamos_a1", "nz", "noise", noise_input="V1"))
        self.assertIn("q1", names(sm.per_step["vamos_a1"]))                 # the STRUCT trace takes the caller's name too


class TestWildcards(SignalsCase):
    def test_star_and_depth(self):
        nl = testbench()
        _, sm, _ = self.resolve(nl, saves=[(["*"], {"depth": 1})])
        self.assertEqual(names(sm.per_step["vamos_a1"]), ["in", "a", "b", "out", "o2", "e1"])
        _, sm, _ = self.resolve(testbench(), saves=[["*"]])
        self.assertIn("X1.mid", names(sm.per_step["vamos_a1"]))
        _, sm, _ = self.resolve(testbench(), saves=[["X1.*"]])
        self.assertEqual(names(sm.per_step["vamos_a1"]), ["X1.mid"])
        _, sm, _ = self.resolve(testbench(), saves=[["?"]])
        self.assertEqual(names(sm.per_step["vamos_a1"]), ["a", "b"])

    def test_devtype_subckt_exclude(self):
        nl = testbench()
        _, sm, _ = self.resolve(nl, saves=[["out"], (["*:a"], {"devtype": "diode"})])
        self.assertEqual(names(sm.per_step["vamos_a1"]), ["out", "D1:a"])
        _, sm, _ = self.resolve(testbench(), saves=[["out"], (["*:*"], {"devtype": "diode"})])
        self.assertEqual(names(sm.per_step["vamos_a1"]), ["out", "D1:a", "D1:c"])
        _, sm, _ = self.resolve(testbench(), saves=[(["*"], {"subckt": "rcsec"})])
        self.assertEqual(names(sm.per_step["vamos_a1"]), ["X1.mid"])            # ports excluded without ports=yes
        _, sm, _ = self.resolve(testbench(), saves=[(["*"], {"subckt": "rcsec", "ports": True})])
        self.assertEqual(sm.per_step["vamos_a1"], [(("v", "X1.mid"), "X1.mid", "V"), (("v", "out"), "X1.a", "V")])
        _, sm, _ = self.resolve(testbench(), saves=[(["*"], {"subckt": "rcsec"})], options={"saveports": "yes"})
        self.assertEqual(names(sm.per_step["vamos_a1"]), ["X1.mid", "X1.a"])   # port b is ground: no signal
        _, sm, _ = self.resolve(testbench(), saves=[(["*"], {"depth": 1, "exclude": ["o*", "e?"]})])
        self.assertEqual(names(sm.per_step["vamos_a1"]), ["in", "a", "b"])
        _, sm, _ = self.resolve(testbench(), saves=[["out"], (["*"], {"sigtype": "subckt"})])
        self.assertEqual(names(sm.per_step["vamos_a1"]), ["out", "X1:1", "X1:2"])
        _, sm, notes = self.resolve(testbench(), saves=[["zz*"]])
        self.assertTrue(any("matches nothing" in n.message for n in notes))


# -- §5.6, §6.4, §8.4 noise and xf -----------------------------------------------------------------------------------

class TestNoiseXf(SignalsCase):
    def noise_step(self, inp=None):
        return AnalysisStep("vamos_a1", "nz", "noise", noise_input=inp or "vamos_nin1")

    def netlist_with_noise(self, nodes=("out",), **args):
        nl = testbench()
        a = {k: Str(v) for k, v in args.items()}
        nl.analyses = [Analysis("noise", a, "t.scs:50", name="nz", nodes=list(nodes),
                                sweep=SweepSpec("freq", mode="dec", start=N(1.0), stop=N(1e3), count=10))]
        return nl

    def test_structs_out_gain_in(self):
        nl = self.netlist_with_noise()
        nl2, sm, notes = self.resolve(nl, self.noise_step())
        sigs = sm.per_step["vamos_a1"]
        self.assertEqual(names(sigs), ["R1", "D1", "M1", "X1.R1", "out"])      # IR instance order, then out
        self.assertEqual(refs(sigs)[:4], [("noise", "R1"), ("noise", "D1"), ("noise", "M1"), ("noise", "X1.R1")])
        self.assertEqual([t for _, _, t in sigs], ["resistor", "dm", "nch", "rref", "V/sqrt(Hz)"])
        self.assertEqual(refs(sigs)[-1], ("onoise",))
        top = instances(nl2.body)
        nin = top["vamos_nin1"]
        self.assertEqual((nin.kind, nin.prim, nin.nodes, nin.source.dc), ("i", "isource", ["out", "0"], N(0.0)))
        self.assertNotIn("vamos_nin1", instances(nl.body))

    def test_input_probe_types(self):
        nl = self.netlist_with_noise(iprobe="V1")
        nl2, sm, _ = self.resolve(nl, self.noise_step("V1"))
        sigs = sm.per_step["vamos_a1"]
        self.assertEqual(names(sigs)[-3:], ["out", "gain", "in"])
        self.assertEqual([t for _, _, t in sigs][-3:], ["V/sqrt(Hz)", "V/V", "V/sqrt(Hz)"])
        self.assertEqual(refs(sigs)[-2:], [("gain",), ("inoise",)])
        self.assertNotIn("vamos_nin1", instances(nl2.body))
        nl = self.netlist_with_noise(iprobe="I1")
        _, sm, _ = self.resolve(nl, self.noise_step("I1"))
        self.assertEqual([t for _, _, t in sm.per_step["vamos_a1"]][-2:], ["V/A", "A/sqrt(Hz)"])

    def test_struct_type_collision(self):
        nl = self.netlist_with_noise()
        nl.body[1] = Model("V", "d", 1.0, {"is": N(1e-14)}, "t.scs:3", prim="diode")
        nl.body[10].master = "V"
        _, sm, notes = self.resolve(nl, self.noise_step())
        types = {name: t for _, name, t in sm.per_step["vamos_a1"]}
        self.assertEqual(types["D1"], "V_2")
        self.assertTrue(any("collides" in n.message and n.severity == NOTE for n in notes))

    def test_oprobe_output_and_subckt_output(self):
        nl = self.netlist_with_noise(nodes=(), oprobe="R1")
        nl2, _, notes = self.resolve(nl, AnalysisStep("vamos_a1", "nz", "noise", args={"oprobe": "R1"}, noise_input="vamos_nin1"))
        self.assertEqual(instances(nl2.body)["vamos_nin1"].nodes, ["b", "out"])
        nl = self.netlist_with_noise(nodes=("X1.mid",))
        nl2, _, notes = self.resolve(nl, self.noise_step())
        sub = [s for s in nl2.body if isinstance(s, Subckt)][0]
        self.assertEqual(instances(sub.body)["vamos_nin1"].nodes, ["mid", "0"])
        self.assertEqual(notes, [])
        nl = self.netlist_with_noise(nodes=("X1.mid", "in"))
        _, _, notes = self.resolve(nl, self.noise_step())
        self.assertTrue(any(n.severity == ERROR and "different subckt bodies" in n.message for n in notes))

    def test_xf_traces(self):
        nl = testbench()
        _, sm, _ = self.resolve(nl, AnalysisStep("vamos_a1", "x1", "xf"))
        sigs = sm.per_step["vamos_a1"]
        self.assertEqual(sigs, [(("tf", "V1"), "V1:p", "V/V"), (("tf", "I1"), "I1:sink", "V/A")])   # no iprobe, no short

    def test_noisy_exclusions(self):
        nl = self.netlist_with_noise()
        nl.body.append(inst("Rq", "r", ["out", "0"], "resistor", value=N(1e3), params={"isnoisy": "no"}))
        _, sm, _ = self.resolve(nl, self.noise_step())
        self.assertNotIn("Rq", names(sm.per_step["vamos_a1"]))
        self.assertNotIn("R0", names(sm.per_step["vamos_a1"]))
        self.assertNotIn("Rz", names(sm.per_step["vamos_a1"]))


# -- §3.10 ic / nodeset / state files, §4.1 render copies ------------------------------------------------------------

class TestNamesAndCopies(SignalsCase):
    def test_ic_and_nodeset_names(self):
        nl = testbench()
        nl.ics = {"out": N(1.0), "X1.mid": N(0.5), "nosuch": N(0.1), "X1.a": N(0.2)}
        nl.nodesets = {"o*": N(0.0)}
        _, _, notes = self.resolve(nl, saves=[["out"]])
        msgs = [n.text() for n in notes]
        self.assertEqual(len(msgs), 2, msgs)
        self.assertTrue(any(m.startswith("warning") and "ic nosuch" in m for m in msgs), msgs)
        self.assertTrue(any(m.startswith("error") and "nodeset o*" in m and "wildcards" in m for m in msgs), msgs)

    def test_state_files(self):
        nl = testbench()
        d = tempfile.mkdtemp(prefix="vamos_sig_")
        try:
            path = os.path.join(d, "n.ns")
            with open(path, "w") as fh:
                fh.write("# State file generated by Spectre\nout 1.0\nnosuch 0.5\nV1:p -1e-3\n")
            _, _, notes = self.resolve(nl, step("vamos_a1", "op1", "dc", readns=path),
                                       step("vamos_a2", "tr", "tran", readic="missing.ic"), saves=[["out"]])
        finally:
            shutil.rmtree(d, ignore_errors=True)
        msgs = [n.text() for n in notes]
        self.assertTrue(any(m.startswith("warning") and "op1 readns nosuch" in m for m in msgs), msgs)
        self.assertTrue(any(m.startswith("note") and "1 current entries" in m for m in msgs), msgs)
        self.assertTrue(any(m.startswith("note") and "missing.ic" in m and "not found" in m for m in msgs), msgs)

    def test_values_reduced_per_engine(self):
        nl = testbench()
        nl.body.insert(1, Param("rdep", P("rval*2"), "t.scs:2"))
        nl.values["rdep"] = 2000.0
        nl2, _, _ = self.resolve(copy.deepcopy(nl), dependents=("rval", "rdep"))
        self.assertEqual(nl2.values, {})
        nl3, _, _ = self.resolve(copy.deepcopy(nl), engine="xyce", dependents=("rval", "rdep"))
        self.assertEqual(nl3.values, {"rval": 1000.0, "rdep": 2000.0})
        self.assertEqual(nl.values, {"rval": 1000.0, "rdep": 2000.0})

    def test_the_copy_is_a_deep_copy(self):
        nl = testbench()
        nl2, _, _ = self.resolve(nl, saves=[["D1:a"]])
        self.assertIsNot(nl2, nl)
        self.assertIsNot(nl2.body[0], nl.body[0])
        self.assertEqual(len(nl2.body), len(nl.body) + 1)

    def test_signals_per_step_kinds(self):
        nl = testbench()
        _, sm, _ = self.resolve(nl, step("vamos_a1", "op1", "dc"), step("vamos_a2", "ac1", "ac"),
                                step("vamos_a3", "tr", "tran"), saves=[["out", "V1:p"]])
        self.assertEqual(set(sm.per_step), {"vamos_a1", "vamos_a2", "vamos_a3"})
        for sid in sm.per_step:
            self.assertEqual(sm.per_step[sid], [(("v", "out"), "out", "V"), (("i", "V1"), "V1:p", "I")])


class TestTypes(unittest.TestCase):
    def test_psf_type_constants(self):
        self.assertEqual(SG.PSF_TYPES, ("V", "I", "V/sqrt(Hz)", "A/sqrt(Hz)", "V/V", "V/A"))
        for t in SG.PSF_TYPES:
            self.assertIn(t, SG.PSF_FIXED_TYPES)


if __name__ == "__main__":
    unittest.main()
