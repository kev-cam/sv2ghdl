"""spice.py's spectre-spice dialect and its shared fixes (docs/VAMOS_SPECTRE_DESIGN.md §3.13, §4.3; owner S1).

    python3 -m unittest discover -s tests/vamos -p 'test_spectre_spice.py' -v

declare_fragment (the first phase), parse_fragment (the second: every §4.3 switch point against a
hand-built resolver), va_modules with its preprocessor (§3.1), and the HSPICE-route regressions of
the shared fixes (§4): spice.parse finds a module in an `included file and fills Netlist.va_modules;
_colon_clashes walks Cond.  Runs on both legs, no engine.
"""

import os
import unittest

from vamos_testlib import TempDir, fixture

from vamos.netlist import expr as E  # noqa: E402
from vamos.netlist import ir, spice  # noqa: E402
from vamos.netlist.expr_ast import Call, Name, Num, Str  # noqa: E402
from vamos.notes import ERROR, NOTE, WARNING, NoteError  # noqa: E402

OPTS = ir.ParseOpts(dialect="spectre-spice", case="lower", parhier_local=True)


def lines(text, origin="t.sp", first=1):
    return [(l, "%s:%d" % (origin, k + first)) for k, l in enumerate(text.strip("\n").split("\n"))]


class Resolver:
    """A hand-built first-phase table (the spice.Resolver protocol)."""

    def __init__(self, subckts=(), models=(), params=(), modules=()):
        self.subs = {d.name: d for d in subckts}
        self.models = {d.name: d for d in models}
        self.params = {p: spice.Decl("param", p) for p in params}
        self.modules = {m.name.lower(): m for m in modules}

    def subckt(self, name):
        return self.subs.get(name)

    def model(self, name):
        return self.models.get(name)

    def param(self, name):
        return self.params.get(name)

    def va_module(self, name):
        return self.modules.get(name.lower())


def frag(text, resolver=None, cwd="."):
    return spice.parse_fragment(lines(text), cwd, OPTS, resolver or Resolver())


def errors(f):
    return [n.message for n in f.notes if n.severity == ERROR]


def insts(f):
    return {it.name: it for it in f.items if isinstance(it, ir.Instance)}


class TestDeclareFragment(unittest.TestCase):
    def test_names_and_file_statements(self):
        decls, refs = spice.declare_fragment(lines("""
.param rr=1k 'q'=2
.subckt cell a b p=1 q=2
.include 'sub.sp'
r1 a b rr
.ends
.model NCH nmos level=49 vth0=0.5
.model dd d is=1e-14
.model q1 npn bf=100
.model j1 njf
.model rm r rsh=10
.lib 'models.lib' tt
.hdl 'res.va'
.inc other.sp
"""), OPTS)
        self.assertEqual([(d.kind, d.name, d.master) for d in decls],
                         [("param", "rr", ""), ("subckt", "cell", ""), ("model", "nch", "bsim3v3"),
                          ("model", "dd", "diode"), ("model", "q1", "bjt"), ("model", "j1", "jfet"),
                          ("model", "rm", "resistor")])
        self.assertEqual((decls[1].ports, decls[1].params), (2, ["p", "q"]))
        self.assertEqual([(r.keyword, r.path, r.section, r.subckt) for r in refs],
                         [(".include", "sub.sp", None, "cell"), (".lib", "models.lib", "tt", ""),
                          (".hdl", "res.va", None, ""), (".inc", "other.sp", None, "")])
        self.assertEqual(refs[0].origin, "t.sp:3")

    def test_levels_and_unknown_types(self):
        decls, _ = spice.declare_fragment(lines(".model a nmos\n.model b pmos level=54\n.model c nmos level=lv\n"
                                                ".model d d level=3\n.model e myva r=1\n.lib sec2\n"), OPTS)
        self.assertEqual([d.master for d in decls], ["mos1", "bsim4", "nmos", "d", "myva"])

    def test_lib_call_inside_a_section(self):
        _, refs = spice.declare_fragment(lines(".lib other"), OPTS)
        self.assertEqual([(r.keyword, r.path, r.section) for r in refs], [(".lib", "", "other")])


class TestParseFragmentLexical(TempDir):
    def test_node_names_are_strings_and_only_0_is_ground(self):
        f = frag("r1 007 00 1k\nr2 gnd gnd! 1k\nr3 ground 7 1k\nr4 a 0 1k\n")
        self.assertEqual(errors(f), [])
        i = insts(f)
        self.assertEqual(i["r1"].nodes, ["007", "00"])
        self.assertEqual(i["r2"].nodes, ["gnd", "gnd!"])
        self.assertEqual(i["r3"].nodes, ["ground", "7"])
        self.assertEqual(i["r4"].nodes, ["a", "0"])
        self.assertEqual(i["r1"].prim, "resistor")

    def test_lower_case_and_spelling(self):
        f = frag("Rload A VDD 1k\n")
        self.assertEqual(list(insts(f)), ["rload"])
        self.assertEqual(insts(f)["rload"].nodes, ["a", "vdd"])

    def test_spectre_comment_is_an_error(self):
        f = frag("r1 a 0 1k\n* spectre: Rp 15 0 resistor r=50m\n*spectre: + tc=0.001\n")
        self.assertEqual(len(errors(f)), 2)
        self.assertIn("* spectre:", errors(f)[0])

    def test_continuation_across_a_file_boundary(self):
        phys = lines("r1 a 0 1k", "a.sp") + lines("+ tc1=1e-3", "b.sp", 7)
        f = spice.parse_fragment(phys, ".", OPTS, Resolver())
        self.assertTrue(any("cannot continue across a file boundary" in m for m in errors(f)))
        phys = lines("r1 a 0 1k \\", "a.sp") + lines("+tc1=1e-3", "b.sp", 7)
        f = spice.parse_fragment(phys, ".", OPTS, Resolver())
        self.assertTrue(any("cannot continue across a file boundary" in m for m in errors(f)))
        f = frag("r1 a 0 1k\n+ tc1=1e-3\n")
        self.assertEqual(errors(f), [])
        self.assertEqual(insts(f)["r1"].params["tc1"], Num(1e-3))

    def test_dialect_and_case_are_checked(self):
        with self.assertRaises(ValueError):
            spice.parse_fragment(lines("r1 a 0 1k"), ".", ir.ParseOpts(dialect="hspice"), Resolver())
        with self.assertRaises(ValueError):
            spice.parse_fragment(lines("r1 a 0 1k"), ".", ir.ParseOpts(dialect="spectre-spice"), Resolver())


class TestParseFragmentExpressions(TempDir):
    def test_spectre_semantics(self):
        f = frag(".param a='pow(2,1.5)' b='2**1.5' x=0.5 r0='1k*pow(x,1.5)' t=temp tn=tnom m=M_PI\n")
        self.assertEqual(errors(f), [])
        p = {it.name: it.expr for it in f.items if isinstance(it, ir.Param)}
        self.assertEqual(p["a"], Call("cpow", (Num(2.0), Num(1.5))))              # raw, unevaluated
        self.assertEqual(E.evaluate(p["a"], {}, "spectre-spice"), 2.8284271247461903)
        self.assertEqual(E.to_xyce(p["r0"]), "1000.0*pow(x, 1.5)")                # [E54]: C pow
        self.assertEqual(p["t"], Name("temper"))
        self.assertEqual(p["tn"], Name("$tnom"))
        self.assertEqual(p["m"].value, 3.141592653589793)

    def test_out_of_domain_constant_is_an_error_not_a_number(self):
        f = frag("r1 a 0 'sqrt(-4)'\nr2 a 0 '-2**2+10'\n")
        self.assertEqual(len(errors(f)), 1)
        self.assertIn("sqrt", errors(f)[0])
        self.assertEqual(insts(f)["r2"].value, Num(14.0))                             # (-2)**2 + 10

    def test_bare_temp_value_and_time(self):
        f = frag("r1 a 0 temp\nr2 a 0 TNOM\n")
        self.assertEqual(insts(f)["r1"].value, Name("temper"))
        self.assertEqual(insts(f)["r2"].value, Name("$tnom"))
        f = frag("r1 a 0 time\n")
        self.assertIn("behavioral", errors(f)[0])

    def test_par_grouping_and_user_functions(self):
        f = frag(".param f(a,b)='a*b' sqrt(x)='x*x'\n.param q=par'f(2,3)'\nr1 a 0 par 'sqrt(2)'\n")
        self.assertEqual([n.severity for n in f.notes if "sqrt" in n.message and n.severity == WARNING], [WARNING])
        p = {it.name: it.expr for it in f.items if isinstance(it, ir.Param)}
        self.assertEqual(p["q"], E.parse("2*3", "lower", "spectre-spice"))
        self.assertEqual(insts(f)["r1"].value, Num(4.0))

    def test_numbers_follow_the_spice_table(self):
        f = frag("r1 a 0 1M\nc1 a 0 1x\nr2 a 0 1.0D+3\n")
        self.assertEqual(insts(f)["r1"].value, Num(1e-3))
        self.assertEqual(insts(f)["c1"].value, Num(1.0))
        self.assertTrue(any(n.severity == WARNING and "1x" in n.message for n in f.notes))
        self.assertTrue(any("1.0D+3" in m for m in errors(f)))


class TestParseFragmentElements(TempDir):
    def test_prim_on_every_element(self):
        res = Resolver(models=[spice.Decl("model", "nch", "bsim3v3"), spice.Decl("model", "dd", "diode"),
                               spice.Decl("model", "qq", "bjt"), spice.Decl("model", "jj", "jfet")],
                       subckts=[spice.Decl("subckt", "cell", ports=2, params=["k"])])
        f = frag("v1 a 0 dc 1\ni1 a 0 dc 1\nr1 a 0 1k\nc1 a 0 1p\nl1 a 0 1n\ne1 b 0 a 0 2\ng1 b 0 a 0 2\n"
                 "h1 b 0 v1 2\nf1 b 0 v1 2\nk1 l1 l1 0.9\nd1 a 0 dd\nq1 c b 0 qq\nj1 d g s jj\nm1 d g s b nch\n"
                 "x1 a 0 cell k=1\n", res)
        self.assertEqual(errors(f), [])
        want = {"v1": "vsource", "i1": "isource", "r1": "resistor", "c1": "capacitor", "l1": "inductor",
                "e1": "vcvs", "g1": "vccs", "h1": "ccvs", "f1": "cccs", "k1": "mutual_inductor", "d1": "diode",
                "q1": "bjt", "j1": "jfet", "m1": "bsim3v3", "x1": "subckt"}
        self.assertEqual({k: v.prim for k, v in insts(f).items()}, want)
        self.assertEqual(insts(f)["m1"].params, {})                   # no DEFL/DEFW: §3.9 fills w/l
        self.assertEqual(insts(f)["x1"].master, "cell")

    def test_rcl_field_naming_nothing_is_a_model(self):
        f = frag("r1 a 0 rmod\nr2 a 0 rval\nr3 a 0 rmod 1k\n", Resolver(params=["rval"]))
        self.assertEqual(errors(f), [])
        i = insts(f)
        self.assertEqual((i["r1"].master, i["r1"].value), ("rmod", None))
        self.assertEqual((i["r2"].master, i["r2"].value), (None, Name("rval")))
        self.assertEqual((i["r3"].master, i["r3"].value), ("rmod", Num(1000.0)))
        self.assertEqual(f.pending, [("model", "rmod", "t.sp:1"), ("model", "rmod", "t.sp:3")])

    def test_q_fourth_field(self):
        f = frag("q3 c b 0 nch mybjt\nq4 c b 0 mybjt\nq5 c b e s qq\nq6 c b e qq\n",
                 Resolver(models=[spice.Decl("model", "qq", "bjt")]))
        i = insts(f)
        self.assertEqual((i["q3"].nodes, i["q3"].master), (["c", "b", "0", "nch"], "mybjt"))
        self.assertEqual((i["q4"].nodes, i["q4"].master), (["c", "b", "0"], "mybjt"))
        self.assertEqual((i["q5"].nodes, i["q5"].master, i["q5"].prim), (["c", "b", "e", "s"], "qq", "bjt"))
        self.assertEqual(i["q6"].nodes, ["c", "b", "e"])
        self.assertEqual([p[1] for p in f.pending], ["mybjt", "mybjt"])

    def test_appended_spectre_parameters(self):
        f = frag("rload a 0 1k isnoisy=no\nrbad a 0 1k foo=3\nm1 d g s b nch w=1u region=sat zz=1\n",
                 Resolver(models=[spice.Decl("model", "nch", "mos1")]))
        self.assertEqual(insts(f)["rload"].params, {"isnoisy": Str("no")})       # an enumeration: Str (§4.1 item 11)
        self.assertEqual(len(errors(f)), 2)
        self.assertIn("foo=", errors(f)[0])
        self.assertIn("zz=", errors(f)[1])

    def test_scale_on_rcl_is_an_error(self):
        f = frag("r1 a 0 1k scale=2\n")
        self.assertIn("scale=", errors(f)[0])

    def test_x_line_overrides_must_name_a_header_parameter(self):
        res = Resolver(subckts=[spice.Decl("subckt", "spectre_cell", ports=2, params=["k"])])
        f = frag(".subckt cell a b p=1\n.param body=2\nr1 a b 'p*body'\n.ends\nx1 n 0 cell p=3\nx2 n 0 cell body=3\n"
                 "x3 n 0 spectre_cell k=1\nx4 n 0 spectre_cell zz=1\nx5 n 0 0 spectre_cell\n", res)
        msgs = errors(f)
        self.assertEqual(len(msgs), 3, msgs)
        self.assertIn("x2: body is not a parameter", msgs[0])
        self.assertIn("x4: zz is not a parameter", msgs[1])
        self.assertIn("x5 has 3 nodes but subckt spectre_cell has 2 ports", msgs[2])
        sub = [it for it in f.items if isinstance(it, ir.Subckt)][0]
        self.assertEqual([p.name for p in sub.params], ["p", "body"])        # raw, unsorted
        self.assertEqual(insts(f)["x3"].prim, "subckt")

    def test_spectre_model_used_from_spice(self):
        res = Resolver(models=[spice.Decl("model", "nch", "bsim3v3")])
        f = frag("m1 d g s b NCH w=1u l=1u\n", res)
        self.assertEqual(errors(f), [])
        self.assertEqual((insts(f)["m1"].master, insts(f)["m1"].prim), ("nch", "bsim3v3"))

    def test_models_get_prim_and_bins_the_spectre_rule(self):
        f = frag(".model nch nmos level=49 vth0=0.5\n.model nch2 nmos\n.model nch3 pmos level=54\n.model dd d\n"
                 ".model bad d level=3\n.model b.1 nmos level=49 lmin=1u lmax=2u wmin=1u wmax=2u\n"
                 ".model b.2 nmos level=49 lmin=2u lmax=3u wmin=1u wmax=2u\n.model lv nmos level=lvl\n")
        models = {m.name: m for m in f.items if isinstance(m, ir.Model)}
        self.assertEqual(models["nch"].prim, "bsim3v3")
        self.assertEqual(models["nch2"].prim, "mos1")
        self.assertEqual(models["nch3"].prim, "bsim4")
        self.assertEqual(models["dd"].prim, "diode")
        self.assertNotIn("bad", models)
        self.assertTrue(any("level" in n.message and "bad" in n.message for n in f.notes))
        self.assertEqual((models["b.1"].bin_rule, models["b.1"].base, models["b.2"].bin_index), ("spectre", "b", 2))
        self.assertNotIn("lv", models)

    def test_sources_fill_spectre_names(self):
        f = frag("v1 a 0 dc 1 ac 1 0 pulse(0 1 1n 1n 1n 5n 10n)\nv2 a 0 sin(0 1)\ni1 a 0 pwl(0 0 1n 1) td=1n\n"
                 "v3 a 0 exp(0 1 1n 1n 5n 1n)\nv4 a 0 sin(0 1 1k)\nv5 a 0 pwl(0 0 1n 1) r=0\nv6 a 0 sffm(0 1 1k 2 10)\n")
        i = insts(f)
        self.assertEqual(i["v1"].source.spectre, {"dc": Num(1.0), "mag": Num(1.0), "phase": Num(0.0),
                                                  "type": Str("pulse"), "val0": Num(0.0), "val1": Num(1.0),
                                                  "delay": Num(1e-9), "rise": Num(1e-9), "fall": Num(1e-9),
                                                  "width": Num(5e-9), "period": Num(1e-8)})
        self.assertEqual(i["i1"].source.spectre, {"type": Str("pwl"), "wave": [Num(0.0), Num(0.0), Num(1e-9), Num(1.0)],
                                                  "delay": Num(1e-9)})
        self.assertEqual(i["v3"].source.spectre["td2"], Num(5e-9))
        self.assertEqual(i["v4"].source.spectre["type"], Str("sine"))
        self.assertEqual(i["v4"].source.spectre["freq"], Num(1000.0))
        msgs = errors(f)
        self.assertTrue(any("v2: SIN without a frequency" in m for m in msgs))
        self.assertTrue(any("v5" in m and "R=" in m for m in msgs))
        self.assertTrue(any("v6" in m and "SFFM" in m for m in msgs))
        self.assertEqual(i["v1"].prim, "vsource")

    def test_controls_are_returned_unevaluated(self):
        f = frag(".tran 1n 1u 10n 5n\n.option reltol=1e-3 post\n.ic v(a)=1 b=2\n.print tran v(a) i(v1)\n.temp 50\n"
                 ".global vdd\n.op\n.dc v1 0 1 0.1\n.ac dec 10 1 1g\n.nodeset v(c)=1\n.hdl 'x.va'\n.title t\n"
                 ".measure tran x find v(a) at=1n\n.protect\n.end\nr9 a 0 1k\n")
        self.assertEqual([c[0] for c in f.controls],
                         [".tran", ".option", ".ic", ".print", ".temp", ".global", ".op", ".dc", ".ac", ".nodeset",
                          ".hdl", ".title", ".measure", ".protect", ".end"])
        self.assertEqual(f.controls[0][1], [(None, "1n"), (None, "1u"), (None, "10n"), (None, "5n")])
        self.assertEqual(f.controls[1][1], [("reltol", "1e-3"), (None, "post")])
        self.assertEqual(f.controls[2][1], [("v(a)", "1"), ("b", "2")])
        self.assertNotIn("r9", insts(f))                                      # after .end
        self.assertEqual(errors(f), [])

    def test_controls_inside_a_subckt(self):
        f = frag(".subckt s a b\n.tran 1n 1u\n.option reltol=1e-3\nr1 a b 1k\n.ends\n")
        msgs = errors(f)
        self.assertEqual(len(msgs), 2)
        self.assertIn(".tran inside .subckt s", msgs[0])
        self.assertIn(".option inside .subckt s", msgs[1])

    def test_unspliced_include_is_an_error(self):
        f = frag(".include 'x.sp'\n")
        self.assertIn("not spliced", errors(f)[0])

    def test_left_out_and_pending(self):
        f = frag(".subckt bad a b\nx1 a b nosuch\n.ends\nr1 a 0 1k\n")
        self.assertIn("bad", f.left_out)
        self.assertEqual(errors(f), [])
        self.assertTrue(any(n.severity == NOTE and "left out" in n.message for n in f.notes))

    def test_verilog_a_module_through_the_resolver(self):
        vm = ir.VaModule("MyRes", "/x/myres.va", ["R"])
        f = frag("x1 a 0 myres r=2k\n.model mr myres r=3k\nx2 a 0 mr\n", Resolver(modules=[vm]))
        self.assertEqual(errors(f), [])
        i = insts(f)
        self.assertEqual((i["x1"].kind, i["x1"].master, i["x1"].prim), ("y", "MyRes", "MyRes"))
        self.assertEqual((i["x2"].kind, i["x2"].master, i["x2"].params), ("y", "MyRes", {"r": Num(3000.0)}))


class TestVaModules(TempDir):
    def test_include_followed_and_macros_expanded(self):
        self.write("inc/common.va", '`define MPR(nam,def) (*desc="x"*) parameter real nam=def;\n'
                                     '`define THING 2\n')
        self.write("lib/mod.va", '`include "disciplines.vams"\n`include "common.va"\n'
                                 '(*xyceModelGroup="MOSFET" xyceLevelNumber="77"*)\nmodule bsimx(d, g, s, b);\n'
                                 'inout d, g, s, b; electrical d, g, s, b;\n'
                                 '`MPR(L, 1e-6)\n`MPR(W,\n  1e-6)\nparameter integer NF = 1 from [1:inf);\n'
                                 'aliasparam l = L;\nlocalparam real hidden = 2;\n`ifdef FAST\nparameter real fast=1;\n'
                                 '`else\nparameter real slow=`THING;\n`endif\nanalog begin\n`MPR(notaparam, 1)\nend\n'
                                 'endmodule\n')
        self.write("top.va", '// top\n`include "mod.va"\n')
        mods, files = spice.va_modules(os.path.join(self.tmp, "top.va"), [os.path.join(self.tmp, "lib"),
                                                                           os.path.join(self.tmp, "inc")])
        self.assertEqual(list(mods), ["bsimx"])
        vm = mods["bsimx"]
        self.assertEqual(vm.name, "bsimx")
        self.assertEqual(vm.params, ["L", "W", "NF", "l", "slow"])
        self.assertEqual(vm.attrs, {"xyceModelGroup": "MOSFET", "xyceLevelNumber": "77"})
        self.assertEqual([os.path.basename(f) for f in files], ["top.va", "mod.va", "common.va"])
        mods, _ = spice.va_modules(os.path.join(self.tmp, "top.va"), [os.path.join(self.tmp, "lib"),
                                                                       os.path.join(self.tmp, "inc")], ["FAST"])
        self.assertEqual(mods["bsimx"].params, ["L", "W", "NF", "l", "fast"])

    def test_spectre_reading_of_a_cmc_header(self):
        # phase 2's integration (C's seams): every CMC tree writes its attributes after the port list (also
        # through an `attr() macro) and declares analog functions before its parameters; the Spectre dialects
        # read both (spectre_va_modules), the HSPICE route keeps va_modules' reading
        self.write("cmc.va", '`define attr(txt) (*txt*)\nmodule bsimq(d, g, s, b) `attr(xyceModelGroup="MOSFET" '
                             'xyceLevelNumber="77");\ninout d, g, s, b; electrical d, g, s, b;\n'
                             'analog function real lexp;\ninput x; real x;\nbegin lexp = exp(x); end\nendfunction\n'
                             'parameter real toxe = 3e-9;\nparameter real l = 1e-6;\nanalog begin\nI(d, s) <+ 0;\nend\n'
                             'endmodule\n')
        path = os.path.join(self.tmp, "cmc.va")
        mods, _ = spice.spectre_va_modules(path)
        self.assertEqual(mods["bsimq"].attrs, {"xyceModelGroup": "MOSFET", "xyceLevelNumber": "77"})
        self.assertEqual(mods["bsimq"].params, ["toxe", "l"])
        mods, _ = spice.va_modules(path)                                   # the HSPICE route: unchanged
        self.assertEqual((mods["bsimq"].attrs, mods["bsimq"].params), ({}, []))

    def test_unknown_parameter_list(self):
        self.write("m.va", 'module m(a, b);\n`MPR(R, 1)\nendmodule\n')
        mods, files = spice.va_modules(os.path.join(self.tmp, "m.va"))
        self.assertIsNone(mods["m"].params)
        self.assertIn("undefined macro", mods["m"].attrs["vamos_unknown_params"])
        self.write("u.va", '`ifdef X\nmodule u(a, b);\nparameter real r=1;\nendmodule\n')
        mods, _ = spice.va_modules(os.path.join(self.tmp, "u.va"))
        self.assertEqual(mods, {})
        self.write("n.va", '`include "nosuch.inc"\nmodule n(a, b);\nparameter real r=1;\nendmodule\n')
        mods, _ = spice.va_modules(os.path.join(self.tmp, "n.va"))
        self.assertIsNone(mods["n"].params)
        self.assertIn("not found", mods["n"].attrs["vamos_unknown_params"])

    def test_real_modules(self):
        for path, name, count in (("/usr/local/src/VACASK/devices/resistor.va", "resistor", 2),
                                  ("/usr/local/src/VACASK/devices/psp103v4/psp103.va", "psp103va", 782)):
            if not os.path.exists(path):
                self.skipTest("%s is not on this box" % path)
            mods, _ = spice.va_modules(path)
            self.assertIn(name, mods)
            self.assertEqual(len(mods[name].params or []), count, path)


class TestHspiceRouteRegressions(TempDir):
    def parse(self, text):
        return spice.parse([self.write("t.sp", text)], [], self.tmp, ir.ParseOpts())

    def test_hdl_module_in_an_included_file(self):
        """§4's shared fix: spice.parse (hspice) finds a module in an `included file and fills
        Netlist.va_modules; today's scan of the named file alone said 'declares no Verilog-A module'."""
        self.write("code/bsimcmg.va", '`define X 1\n`include "bsimcmg_main.va"\n')
        self.write("code/bsimcmg_main.va", 'module bsimcmg(d, g, s, e);\nparameter real L=1e-6;\nparameter real W=1e-6;\n'
                                           'endmodule\n')
        nl = self.parse("* t\n.hdl 'code/bsimcmg.va'\nx1 d g s e bsimcmg L=2e-6\nv1 d 0 1\n.tran 1n 10n\n")
        self.assertEqual(list(nl.va_modules), ["bsimcmg"])
        self.assertEqual(nl.va_modules["bsimcmg"].params, ["L", "W"])
        self.assertEqual(nl.instances()[0].kind, "y")
        self.assertEqual(nl.instances()[0].master, "bsimcmg")

    def test_hdl_without_a_module_is_still_an_error(self):
        self.write("empty.va", "// nothing\n")
        with self.assertRaises(NoteError) as cm:
            self.parse("* t\n.hdl 'empty.va'\n")
        self.assertIn("declares no Verilog-A module", str(cm.exception))

    def test_colon_clashes_walk_cond(self):
        """§4.3, §4.5 item 1: a net used only inside an if branch is compared too."""
        inner = ir.Instance("r1", "r", ["a", "mid"], value=Num(1.0))
        inner2 = ir.Instance("r2", "r", ["mid", "b"], value=Num(1.0))
        sub = ir.Subckt("cell", ["a", "b"], body=[ir.Cond([(Name("sel"), [inner])], [inner2])])
        x1 = ir.Instance("x1", "x", ["n", "0"], "cell")
        bad = ir.Instance("r9", "r", ["x1:mid", "0"], value=Num(1.0))
        nl = ir.Netlist(body=[sub, x1, ir.Cond([(Name("sel"), [bad])])])
        clashes = spice._colon_clashes(nl)
        self.assertEqual(len(clashes), 1)
        self.assertIn("x1:mid", clashes[0][1])
        nl2 = ir.Netlist(body=[sub, x1])
        self.assertEqual(spice._colon_clashes(nl2), [])

    def test_hspice_parse_unchanged_on_the_switch_points(self):
        nl = self.parse("* t\nr1 007 00 1k\nr2 gnd 7 rmod\n.model rmod r rsh=1\nm1 d g s b nch\n.model nch nmos\n"
                        "v1 d 0 pulse(0 1)\n.tran 1n 1u 10n\n")
        i = {x.name: x for x in nl.instances()}
        self.assertEqual(i["r1"].nodes, ["7", "0"])
        self.assertEqual(i["r2"].nodes, ["0", "7"])
        self.assertEqual(i["m1"].params["l"], Num(1e-4))                       # HSPICE's DEFL
        self.assertEqual(i["v1"].source.args["tr"], Num(1e-9))                 # TSTEP
        self.assertEqual(nl.analyses[0].args["start"], 1e-8)
        self.assertEqual(nl.dialect, "hspice")
        for x in nl.instances():
            self.assertEqual((x.prim, x.folded), ("", []))


if __name__ == "__main__":
    unittest.main()
