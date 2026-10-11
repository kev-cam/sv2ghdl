"""Phase-0 contracts of the spectre personality (docs/VAMOS_SPECTRE_DESIGN.md §10, §11 T0, §12).

    python3 -m unittest discover -s tests/vamos -p 'test_spectre_contract.py' -v

Every phase-0 dataclass defines and constructs (E49's check of the §10 block), the ir.py field
order is pinned (§4.1: each new field appended after its class's last field, with a default, so
today's positional call sites bind), the stubs raise NotImplementedError, and the HSPICE-route
regression of §4's shared fix: Netlist.left_out is a real field that survives
dataclasses.replace.  Runs on both legs (Cygwin Python 3.9, WSL Python 3.14), no engine.
"""

import ast
import copy
import dataclasses
import functools
import os
import typing
import unittest

from vamos_testlib import ROOT, TempDir

from vamos import optable  # noqa: E402
from vamos.job import NOTED, Unmapped  # noqa: E402
from vamos.netlist import expr, ir, plan, signals, spectre, spice  # noqa: E402
from vamos.netlist.expr_ast import Name, Num, Str  # noqa: E402
from vamos.notes import NOTE, Note  # noqa: E402
from vamos.output import psf  # noqa: E402
from vamos.spectre import job as sjob  # noqa: E402
from vamos.spectre import results  # noqa: E402


def names(cls):
    return [f.name for f in dataclasses.fields(cls)]


# §10's ir.py block, transcribed: the existing fields, then the appended ones (§4.1)
IR_FIELDS = {
    "ParseOpts": ["dialect", "case", "parhier_local", "synth_step", "synth_stop", "search"],
    "Param": ["name", "expr", "origin"],
    "Model": ["name", "kind", "level", "params", "origin", "base", "bin_index",
              "bin_rule", "prim"],
    "Source": ["dc", "ac", "wave", "args", "points", "code_uri",
               "spectre"],
    "Instance": ["name", "kind", "nodes", "master", "value", "params", "source", "expr", "expr_kind",
                 "ctrl", "origin",
                 "prim", "folded"],
    "Subckt": ["name", "ports", "params", "body", "orig_ports", "gnd_ports", "origin", "spelling",
               "inline"],
    "Analysis": ["kind", "args", "origin",
                 "name", "sweep", "nodes", "children", "spice"],
    "VaModule": ["name", "path", "params", "attrs"],
    "SweepSpec": ["target", "name", "param", "mode", "start", "stop", "step", "count", "values", "origin"],
    "SaveSpec": ["items", "depth", "sigtype", "devtype", "subckt", "exclude", "probelvl", "time_window",
                 "ports", "filter", "origin"],
    "ParamTest": ["name", "tests", "message", "severity", "origin"],
    "Cond": ["branches", "default", "origin"],
    "Vary": ["param", "dist", "std", "n", "percent", "origin"],
    "Correlate": ["params", "devs", "cc", "origin"],
    "StatBlock": ["kind", "varies", "correlates", "truncate", "origin"],
    "Netlist": ["title", "body", "globals", "options", "temp", "tnom", "parhier", "analyses", "probes", "ics",
                "nodesets", "hdl", "values", "spelling", "notes",
                "dialect", "saves", "statistics", "left_out", "va_modules"],
}

# the positional prefix today's code uses: (class, fields before the Spectre additions)
FROZEN_PREFIX = {"Instance": 11, "Model": 7, "Subckt": 8, "Analysis": 3, "Param": 3, "Source": 6,
                 "Netlist": 15, "ParseOpts": 6}


class TestIrFieldOrder(unittest.TestCase):
    def test_field_order_is_pinned(self):
        for name, expected in IR_FIELDS.items():
            self.assertEqual(names(getattr(ir, name)), expected, name)

    def test_every_appended_field_has_a_default(self):
        for name, prefix in FROZEN_PREFIX.items():   # an existing class: every appended field defaults
            for f in dataclasses.fields(getattr(ir, name))[prefix:]:
                has_default = (f.default is not dataclasses.MISSING
                               or f.default_factory is not dataclasses.MISSING)
                self.assertTrue(has_default, "%s.%s" % (name, f.name))

    def test_new_classes_sit_above_item_and_item_lists_them(self):
        self.assertEqual(typing.get_args(ir.Item),
                         (ir.Instance, ir.Model, ir.Param, ir.Subckt, ir.Cond, ir.ParamTest))
        self.assertEqual(typing.get_args(ir.BranchItem), (ir.Instance, ir.Cond, ir.ParamTest))
        self.assertEqual(ir.ITEM_TYPES, (ir.Instance, ir.Model, ir.Param, ir.Subckt, ir.Cond, ir.ParamTest))
        self.assertEqual(ir.Value, typing.Union[ir.Expr, typing.List[ir.Expr]])

    def test_widened_ics_and_nodesets(self):
        hints = typing.get_type_hints(ir.Netlist)
        self.assertEqual(hints["ics"], typing.Dict[str, typing.Union[float, ir.Expr]])
        self.assertEqual(hints["nodesets"], typing.Dict[str, typing.Union[float, ir.Expr]])
        self.assertEqual(hints["left_out"], typing.Dict[str, Note])
        self.assertEqual(hints["va_modules"], typing.Dict[str, ir.VaModule])

    def test_ir_objects_are_mutable_and_unhashable(self):
        with self.assertRaises(TypeError):
            hash(ir.Instance("r1", "r", ["a", "0"]))
        self.assertEqual(hash(Num(1.0)), hash(Num(1.0)))

    def test_positional_call_sites_bind_the_frozen_prefix(self):
        """No call in vamos/ passes more positional arguments than the pre-Spectre field count, so
        the appended fields are never bound by position (§4.1)."""
        over = []
        for dp, _dn, fn in os.walk(os.path.join(ROOT, "vamos")):
            for f in fn:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(dp, f)
                with open(path, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read(), path)
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    fnode = node.func
                    nm = (fnode.id if isinstance(fnode, ast.Name)
                          else fnode.attr if isinstance(fnode, ast.Attribute) else None)
                    if nm in FROZEN_PREFIX and len(node.args) > FROZEN_PREFIX[nm]:
                        over.append("%s:%d %s(%d positional)" % (path, node.lineno, nm, len(node.args)))
        self.assertEqual(over, [])


class TestIrConstructs(unittest.TestCase):
    def test_todays_positional_call_sites_bind(self):
        s = ir.Subckt("s", ["a"], [], [], ["a"], [], "f:1", {"a": "A"})       # spice.py:3010, 8 arguments
        self.assertEqual((s.origin, s.spelling, s.inline), ("f:1", {"a": "A"}, False))
        m = ir.Model("m", "nmos", 1.0, {}, "f:2")                              # spice.py:1544, 5
        self.assertEqual((m.origin, m.base, m.bin_index, m.bin_rule, m.prim), ("f:2", None, None, None, ""))
        p = ir.Param("p", Num(1.0), "f:3")
        self.assertEqual(p.origin, "f:3")
        a = ir.Analysis("tran", {"stop": 1.0}, "f:4")
        self.assertEqual((a.origin, a.name, a.sweep, a.nodes, a.children, a.spice), ("f:4", "", None, [], [], False))
        i = ir.Instance("r1", "r", ["a", "0"])
        self.assertEqual((i.origin, i.prim, i.folded), ("", "", []))
        src = ir.Source(Num(1.0), (Num(1.0), Num(0.0)))
        self.assertEqual((src.wave, src.code_uri, src.spectre), (None, None, {}))
        nl = ir.Netlist()
        self.assertEqual((nl.dialect, nl.saves, nl.statistics, nl.left_out, nl.va_modules),
                         ("hspice", [], [], {}, {}))

    def test_defaults_are_fresh_per_instance(self):
        a, b = ir.Instance("a", "r", []), ir.Instance("b", "r", [])
        a.folded.append("w")
        self.assertEqual(b.folded, [])
        x, y = ir.Netlist(), ir.Netlist()
        x.left_out["s"] = Note(NOTE, "", "m")
        x.va_modules["m"] = ir.VaModule("m", "/m.va")
        self.assertEqual((y.left_out, y.va_modules), ({}, {}))
        s, t = ir.Source(), ir.Source()
        s.spectre["dc"] = Num(1.0)
        self.assertEqual(t.spectre, {})

    def test_new_classes_construct(self):
        vm = ir.VaModule("bsimcmg", "/pdk/bsimcmg.va", ["l", "w"], {"xyceModelGroup": "MOSFET"})
        self.assertEqual(vm.attrs["xyceModelGroup"], "MOSFET")
        self.assertIsNone(ir.VaModule("m", "/m.va").params)
        sw = ir.SweepSpec("dev", "r1", "r", "lin", Num(1.0), Num(2.0), Num(0.5))
        self.assertEqual((sw.count, sw.values, sw.origin), (None, [], ""))
        self.assertEqual(ir.SweepSpec("param", param="temp", mode="values", values=[Num(27.0)]).values, [Num(27.0)])
        sv = ir.SaveSpec(["out", "x1.mid"])
        self.assertEqual((sv.depth, sv.sigtype, sv.devtype, sv.subckt, sv.exclude, sv.probelvl, sv.time_window,
                          sv.ports, sv.filter, sv.origin),
                         (None, "node", None, None, [], None, [], False, "none", ""))
        pt = ir.ParamTest("chk", [("errorif", Name("r"))], "bad r", "error", "t.scs:3")
        self.assertEqual(pt.tests[0][0], "errorif")
        c = ir.Cond([(Name("sel"), [ir.Instance("q1", "q", ["c", "b", "e"])])])
        self.assertEqual((c.default, c.origin), ([], ""))
        v = ir.Vary("vth0", "gauss", Num(0.01), None, True)
        self.assertEqual((v.dist, v.percent, v.origin), ("gauss", True, ""))
        self.assertEqual(ir.Vary("p").dist, "gauss")
        co = ir.Correlate(["vth0", "u0"], ["m*"], Num(0.5))
        self.assertEqual(co.devs, ["m*"])
        sb = ir.StatBlock("process", [v], [co], Num(4.0))
        self.assertEqual((sb.kind, sb.varies, sb.correlates, sb.truncate), ("process", [v], [co], Num(4.0)))
        self.assertEqual(ir.StatBlock("mismatch").varies, [])

    def test_source_spectre_and_analysis_children(self):
        src = ir.Source(dc=Num(1.0), spectre={"dc": Num(1.0), "type": Str("dc"), "wave": [Num(0.0), Num(1.0)]})
        self.assertEqual(src.spectre["wave"], [Num(0.0), Num(1.0)])
        inner = ir.Analysis("tran", {"stop": Num(1e-3)}, "t.scs:9", name="tran1")
        sweep = ir.Analysis("sweep", {}, "t.scs:8", name="sw", children=[inner],
                            sweep=ir.SweepSpec("param", param="rval", mode="lin"))
        nl = ir.Netlist(analyses=[sweep], dialect="spectre")
        self.assertIsNone(nl.tran())                                     # top level only (§4.1 item 1)
        self.assertEqual(list(ir.flat_analyses(nl.analyses)), [sweep, inner])


class TestFlatWalks(unittest.TestCase):
    def test_flat_items_over_the_ug_p110_structure(self):
        """E56: Cond, Instance(npn10x10), Cond, Instance(npn20x20), Instance(npn_default), Model, ParamTest."""
        q10 = ir.Instance("q1", "q", ["c", "b", "e"], "npn10x10")
        q20 = ir.Instance("q1", "q", ["c", "b", "e"], "npn20x20")
        qd = ir.Instance("q1", "q", ["c", "b", "e"], "npn_default")
        inner = ir.Cond([(Name("area_sel"), [q20])], [qd])
        outer = ir.Cond([(Name("sel"), [q10])], [inner])
        model = ir.Model("npn_mod", "npn", None, {})
        pt = ir.ParamTest("chk")
        got = list(ir.flat_items([outer, model, pt]))
        self.assertEqual(got, [outer, q10, inner, q20, qd, model, pt])
        self.assertEqual([type(x).__name__ for x in got],
                         ["Cond", "Instance", "Cond", "Instance", "Instance", "Model", "ParamTest"])

    def test_flat_items_never_enters_a_subckt_body(self):
        inner = ir.Instance("r1", "r", ["a", "b"], value=Num(1.0))
        sub = ir.Subckt("s", ["a", "b"], body=[inner])
        x1 = ir.Instance("x1", "x", ["n", "0"], "s")
        self.assertEqual(list(ir.flat_items([sub, x1])), [sub, x1])

    def test_flat_items_accepts_every_item_type(self):
        items = [ir.Instance("r1", "r", []), ir.Model("m", "r", None, {}), ir.Param("p", Num(1.0)),
                 ir.Subckt("s", []), ir.Cond([]), ir.ParamTest("t")]
        self.assertEqual(list(ir.flat_items(items)), items)
        for it in items:
            self.assertIsInstance(it, ir.ITEM_TYPES)

    def test_flat_analyses_descends_nested_blocks(self):
        t = ir.Analysis("tran", name="t")
        s2 = ir.Analysis("sweep", name="s2", children=[t])
        mc = ir.Analysis("montecarlo", name="mc", children=[ir.Analysis("dc", name="d")])
        s1 = ir.Analysis("sweep", name="s1", children=[s2, mc])
        op = ir.Analysis("op", name="o")
        self.assertEqual([a.name for a in ir.flat_analyses([s1, op])], ["s1", "s2", "t", "mc", "d", "o"])
        self.assertEqual(list(ir.flat_analyses([])), [])


class TestNetlistLeftOut(TempDir):
    LIB = ("* t\n.subckt varcap a b\ncg a b q='1p*v(a,b)'\n.ends\n"
           ".subckt user a b\nxv a b varcap\n.ends\n.subckt good a b\nr1 a b 1k\n.ends\nx1 n 0 good\n")

    def test_left_out_is_a_field_that_survives_replace(self):
        nl = ir.Netlist(left_out={"user": Note(NOTE, "t.sp:5", "subckt user is left out: xv ...")})
        nl2 = dataclasses.replace(nl)
        self.assertEqual(nl2.left_out, nl.left_out)
        self.assertIsNot(nl2, nl)
        nl3 = copy.deepcopy(nl)
        self.assertEqual(nl3.left_out, nl.left_out)
        self.assertEqual(dataclasses.replace(ir.Netlist(), title="x").left_out, {})

    def test_hspice_route_left_out_survives_replace(self):
        """§4's shared fix: spice.parse stores the left-out subckts in Netlist.left_out, so a copy made
        with dataclasses.replace still answers spice.left_out (the dynamic attribute did not)."""
        path = self.write("t.sp", self.LIB)
        nl = spice.parse([path], [], self.tmp, ir.ParseOpts())
        self.assertEqual(sorted(spice.left_out(nl)), ["user", "varcap"])
        self.assertEqual(sorted(nl.left_out), ["user", "varcap"])
        self.assertTrue(all(isinstance(n, Note) for n in nl.left_out.values()))
        nl2 = dataclasses.replace(nl)
        self.assertEqual(sorted(spice.left_out(nl2)), ["user", "varcap"])
        self.assertEqual(spice.left_out(nl2), spice.left_out(nl))
        self.assertEqual(spice.left_out(copy.deepcopy(nl)), spice.left_out(nl))

    def test_hspice_route_dialect_and_va_modules_defaults(self):
        path = self.write("t.sp", "* t\nr1 a 0 1k\nv1 a 0 dc 1\n.tran 1n 1u\n")
        nl = spice.parse([path], [], self.tmp, ir.ParseOpts())
        self.assertEqual(nl.dialect, "hspice")                            # §4.1 item 9
        self.assertEqual((nl.saves, nl.statistics, nl.left_out), ([], [], {}))
        self.assertIsInstance(nl.va_modules, dict)
        for inst in nl.instances():
            self.assertEqual((inst.prim, inst.folded), ("", []))         # the HSPICE path leaves them


class TestSpectreModuleContracts(unittest.TestCase):
    def test_parse_opts(self):
        o = spectre.SpectreParseOpts()
        self.assertEqual(names(spectre.SpectreParseOpts),
                         ["search", "percent", "pre", "post", "cpp_markers", "title", "mts", "va_include",
                          "va_defines", "top_dir", "top_name"])
        self.assertEqual((o.search, o.percent, o.pre, o.post, o.cpp_markers, o.title, o.mts, o.va_include,
                          o.va_defines, o.top_dir, o.top_name),
                         ([], {}, [], [], False, None, True, [], [], None, None))
        o2 = spectre.SpectreParseOpts(["inc"], {"C": "t"}, [], [], True, "title", False, [], ["X=1"], "/d", "stdin")
        self.assertEqual((o2.cpp_markers, o2.mts, o2.top_name), (True, False, "stdin"))

    def test_stmt(self):
        self.assertEqual(names(spectre.Stmt),
                         ["kind", "name", "nodes", "master", "params", "children", "branches", "origin", "span"])
        s = spectre.Stmt("instance", "r1", ["a", "0"], "resistor", [("r", "1k")], origin="t.scs:2", span=(10, 30))
        self.assertEqual((s.children, s.branches, s.span), ([], [], (10, 30)))
        self.assertEqual(spectre.Stmt("if").span, (0, 0))
        top = spectre.Stmt("if", branches=[("sel == 1", [s])], children=[])
        self.assertEqual(top.branches[0][1][0].name, "r1")

    def test_stubs_raise_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            spectre.statements("r1 (a 0) resistor r=1k\n", "t.scs", title=False)
        with self.assertRaises(NotImplementedError):
            spectre.parse("t.scs", "/tmp", spectre.SpectreParseOpts())
        with self.assertRaises(NotImplementedError):
            spectre.resolve_source({"dc": Num(1.0)}, "dc", [1e-3], [])

    def test_number_is_the_spectre_dialect_of_expr_number(self):
        self.assertIsInstance(spectre.number, functools.partial)
        self.assertIs(spectre.number.func, expr.number)
        self.assertEqual(spectre.number.keywords, {"dialect": "spectre"})


class TestPlanContracts(unittest.TestCase):
    def test_dataclasses(self):
        self.assertEqual(names(plan.SweepLevel),
                         ["name", "id", "target", "spec", "continuation", "label", "desc", "units"])
        self.assertEqual(names(plan.AnalysisStep),
                         ["id", "name", "kind", "context", "own", "args", "options", "saves", "full_solution",
                          "noise_input", "stores", "uses"])
        self.assertEqual(names(plan.Action), ["op", "args", "step", "origin"])
        self.assertEqual(names(plan.RunPlan),
                         ["actions", "variables", "overridden", "options", "notes", "dependents", "engine"])
        lvl = plan.SweepLevel("", "vamos_w1", ("instance", "r1", "r"),
                              ir.SweepSpec("dev", "r1", "r", "lin", Num(1.0), Num(2.0), Num(0.5)))
        self.assertEqual((lvl.continuation, lvl.label, lvl.desc, lvl.units), (1, "", "", ""))
        step = plan.AnalysisStep("vamos_a1", "tran1", "tran", [lvl], None, {"stop": 1e-3}, {"temp": 27.0})
        self.assertEqual((step.saves, step.full_solution, step.noise_input, step.stores, step.uses),
                         (None, False, None, None, None))
        act = plan.Action("analysis", {}, step, "t.scs:9")
        self.assertEqual(plan.Action("note").args, {})
        rp = plan.RunPlan([act], {"rval": 1000.0})
        self.assertEqual((rp.overridden, rp.options, rp.notes, rp.dependents, rp.engine), ({}, {}, [], set(), "vacask"))
        self.assertEqual(len(("instance", "x1.r1", "r")), 3)              # every Target is a 3-tuple
        self.assertEqual(typing.get_args(plan.Target), (str, str, str))
        self.assertEqual(typing.get_args(plan.Setting), (float, str, typing.List[float]))

    def test_action_docstring_carries_the_key_table(self):
        doc = plan.Action.__doc__
        for op in ("options", "var", "alter", "source", "analysis", "note"):
            self.assertIn(op, doc)
        for key in ('"target"', '"value"', '"source"', '"path"', '"type"', '"note"'):
            self.assertIn(key, doc)

    def test_build_stub(self):
        with self.assertRaises(NotImplementedError):
            plan.build(ir.Netlist(dialect="spectre"), "vacask", sjob.Settings())


class TestSignalsContracts(unittest.TestCase):
    def test_dataclasses_and_types(self):
        self.assertEqual(names(signals.SignalMap), ["per_step", "allpub"])
        sm = signals.SignalMap()
        self.assertEqual((sm.per_step, sm.allpub), ({}, set()))
        sm.per_step["vamos_a1"] = [(("v", "out"), "out", "V"), (("i", "v1"), "V1:p", "I"),
                                   (("noise", "r1"), "R1", "R1"), (("onoise",), "out", "V/sqrt(Hz)")]
        sm.allpub.add("vamos_a1")
        self.assertEqual(signals.PSF_TYPES, ("V", "I", "V/sqrt(Hz)", "A/sqrt(Hz)", "V/V", "V/A"))
        self.assertEqual(typing.get_args(signals.Ref), (str, Ellipsis))

    def test_resolve_stub(self):
        with self.assertRaises(NotImplementedError):
            signals.resolve(ir.Netlist(dialect="spectre"), plan.RunPlan())


class TestJobContracts(unittest.TestCase):
    def test_spectre_job_fields(self):
        self.assertEqual(names(sjob.SpectreJob),
                         ["prog", "argv", "cwd", "netlist", "raw", "fmt", "outdir", "log_mode", "log_path", "percent",
                          "cpp", "defines", "undefines", "incdirs", "disable_cpp", "config", "pre_config",
                          "paramdefault", "classes", "maxwarns", "maxnotes", "maxwarnstolog", "maxnotestolog",
                          "errpreset", "aps", "mts", "ahdllibdir", "va_defines", "escchars", "action", "help_topic",
                          "unmapped", "notes", "defaults"])
        j = sjob.SpectreJob("spectre", ["x.scs"], "/tmp")                 # the only positional fields
        self.assertEqual((j.netlist, j.raw, j.fmt, j.outdir, j.log_mode, j.log_path, j.percent, j.cpp),
                         (None, None, None, None, "screen", None, {}, False))
        self.assertEqual((j.defines, j.undefines, j.incdirs, j.disable_cpp, j.config, j.pre_config, j.paramdefault,
                          j.classes), ([], [], [], False, [], [], [], {}))
        self.assertEqual((j.maxwarns, j.maxnotes, j.maxwarnstolog, j.maxnotestolog, j.errpreset, j.aps, j.mts,
                          j.ahdllibdir, j.va_defines, j.escchars, j.action, j.help_topic),
                         (None, None, None, None, None, None, True, None, [], False, "run", None))
        self.assertEqual((j.unmapped, j.notes, j.defaults), ([], [], {}))
        for f in dataclasses.fields(sjob.SpectreJob)[3:]:
            self.assertTrue(f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING,
                            f.name)

    def test_job_protocol_for_optable_scan(self):
        j = sjob.SpectreJob("spectre", [], "/tmp")
        j.note("-foo", NOTED, "x")
        self.assertEqual(j.unmapped, [Unmapped("-foo", NOTED, "x")])
        table = optable.Table([optable.Opt("+bar", act=NOTED, note="b")])
        seen = []
        optable.scan(table, ["+bar", "x.scs"], j, lambda job, tok: seen.append(tok),
                     lambda job, tok, args, i: 1)
        self.assertEqual(seen, ["x.scs"])
        self.assertEqual(j.unmapped, [Unmapped("-foo", NOTED, "x"), Unmapped("+bar", NOTED, "b")])

    def test_settings(self):
        self.assertEqual(names(sjob.Settings),
                         ["fmt", "raw", "outdir", "maxwarns", "maxnotes", "maxwarnstolog", "maxnotestolog",
                          "errpreset", "aps", "paramdefault", "options"])
        s = sjob.Settings()
        self.assertEqual((s.fmt, s.raw, s.outdir, s.maxwarns, s.maxnotes, s.maxwarnstolog, s.maxnotestolog,
                          s.errpreset, s.aps, s.paramdefault, s.options),
                         ("psfascii", "", None, None, None, None, None, None, None, [], {}))
        with self.assertRaises(NotImplementedError):
            sjob.settings(sjob.SpectreJob("spectre", [], "/tmp"), ir.Netlist())


class TestResultsContracts(unittest.TestCase):
    def test_dataclasses(self):
        self.assertEqual(names(results.EngineResult), ["rc", "status", "files", "failed_points", "names", "log"])
        self.assertEqual(names(results.Signal), ["name", "ptype", "units", "values", "members"])
        self.assertEqual(names(results.AnalysisResult),
                         ["key", "atype", "file", "parent", "tree", "sweep", "signals", "header", "description",
                          "swept", "data_type", "status", "rows"])
        er = results.EngineResult()
        self.assertEqual((er.rc, er.status, er.files, er.failed_points, er.names, er.log), (0, {}, {}, {}, {}, []))
        er.names["vamos_a1"] = {("v", "out"): "out"}
        sig = results.Signal("out", "V", "V", [1.0])
        self.assertIsNone(sig.members)
        self.assertEqual(results.Signal("R1", "R1", "", members=[("rn", []), ("total", [])]).members[1][0], "total")
        ar = results.AnalysisResult("myop-dc", "dc", "myop.dc")
        self.assertEqual((ar.parent, ar.tree, ar.sweep, ar.signals, ar.header, ar.description, ar.swept,
                          ar.data_type, ar.status, ar.rows), ("", "", None, [], {}, "", {}, "", "ok", None))
        leaf = results.AnalysisResult("s-000_t-tran", "tran", "s-000_t.tran.tran", "s_t-sweep", "leafNode",
                                      ("R1:r", "Ohm", 1, [1000.0]), [sig], {"start": 0.0}, "Transient Analysis",
                                      {"R1:r": 1000.0}, "swept_scalar", "ok", lambda: iter([(0.0, [1.0])]))
        self.assertEqual(list(leaf.rows()), [(0.0, [1.0])])


class TestPsfContracts(unittest.TestCase):
    def test_format_constants(self):
        self.assertEqual((psf.SIMULATOR, psf.LOG_GENERATOR, psf.SIM_MODE, psf.SIGNAL_NAME_TYPE),
                         ("spectre", "drlLog rev. 1.0", "Spectre", "spectre"))

    def test_head(self):
        self.assertEqual(names(psf.PsfHead),
                         ["version", "date", "design", "simulator", "psfversion", "precision", "prop_precision"])
        h = psf.PsfHead()
        self.assertEqual((h.version, h.date, h.design, h.simulator, h.psfversion, h.precision, h.prop_precision),
                         ("", "", "", "spectre", "1.4.0", "%.15e", "%#g"))
        self.assertEqual(psf.PsfHead("0.1.0", "1:07:28 PM, Tue Feb 2, 2021", "rc").design, "rc")

    def test_stubs(self):
        from datetime import datetime
        with self.assertRaises(NotImplementedError):
            psf.psf_string('net<3>', escchars=True)
        with self.assertRaises(NotImplementedError):
            psf.psf_date(datetime(2021, 2, 2, 13, 7, 28))


class TestOtherOwnersPhase0Dataclasses(unittest.TestCase):
    """The phase-0 dataclasses of tables.py and spice.py (§10), constructed as E49's check does."""

    def test_tables(self):
        from vamos.netlist import tables
        pr = tables.ParamRule("phi", "default", "0.7", "absent:phi,absent:nsub")
        self.assertEqual((pr.match, pr.warn, pr.cite), ((), "", ""))
        self.assertEqual(tables.ParamRule("capmod", "strip", match=("absent", "bsim"),
                                          warn="analyses=ac,noise,xf,tran").match, ("absent", "bsim"))
        mr = tables.MasterRow("mos1", "m")
        self.assertEqual((mr.level, mr.polarity_key, mr.kinds, mr.terminals, mr.geometry, mr.params, mr.instance),
                         (None, "", (), (), (), (), ()))
        pe = tables.PathEnv("", None, None, {})
        self.assertEqual((pe.items, pe.hidden), ([], set()))
        self.assertEqual(names(tables.ParamRule), ["name", "action", "value", "when", "match", "warn", "cite"])
        self.assertEqual(names(tables.MasterRow), ["master", "element", "level", "polarity_key", "kinds",
                                                   "terminals", "geometry", "params", "instance"])
        self.assertEqual(names(tables.PathEnv), ["path", "subckt", "scope", "env", "items", "hidden"])
        self.assertIsInstance(tables.SPECTRE_MASTERS, dict)

    def test_spice(self):
        d = spice.Decl("subckt", "s")
        self.assertEqual((d.master, d.ports, d.params, d.origin), ("", None, [], ""))
        fr = spice.FileRef(".include", "f.sp")
        self.assertEqual((fr.section, fr.subckt, fr.origin), (None, "", ""))
        fg = spice.Fragment()
        self.assertEqual((fg.items, fg.controls, fg.notes, fg.left_out, fg.pending), ([], [], [], {}, []))
        self.assertEqual(names(spice.Decl), ["kind", "name", "master", "ports", "params", "origin"])
        self.assertEqual(names(spice.FileRef), ["keyword", "path", "section", "subckt", "origin"])
        self.assertEqual(names(spice.Fragment), ["items", "controls", "notes", "left_out", "pending"])
        for fn in ("declare_fragment", "parse_fragment", "va_modules"):
            self.assertTrue(callable(getattr(spice, fn)), fn)
        self.assertTrue(hasattr(spice, "Resolver"))


if __name__ == "__main__":
    unittest.main()
