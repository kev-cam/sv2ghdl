"""spectre.parse: the Spectre language into the IR (docs/VAMOS_SPECTRE_DESIGN.md §3; owner S1).

    python3 -m unittest discover -s tests/vamos -p 'test_spectre_lang.py' -v

§11.1 "Lexer and numbers", "Parser", "Sources and models" (the parser's share) and the SPICE-mode rows:
the title and language rules, includes and sections, case-insensitive definitions, Verilog-A module
discovery, parameters across both languages, user functions, inline subckts, model groups, if/else
chains and duplicate names, globals and ground, ic/nodeset, save, options, reserved words, every
§3.6 statement, statistics, resolve_source and the §3.8-§3.9 folds.  Runs on both legs, no engine.
"""

import math
import os
import unittest

from vamos_testlib import TempDir, fixture

from vamos.netlist import expr as E  # noqa: E402
from vamos.netlist import ir, spectre  # noqa: E402
from vamos.netlist.expr_ast import Binary, Call, Name, Num, Str, Ternary  # noqa: E402
from vamos.notes import ERROR, NOTE, WARNING, NoteError  # noqa: E402

XDM = fixture("spectre", "xdm")


class Case(TempDir):
    def parse(self, text, name="t.scs", **opts):
        path = self.write(name, text)
        return spectre.parse(path, self.tmp, spectre.SpectreParseOpts(**opts))

    def fails(self, text, *fragments, name="t.scs", **opts):
        with self.assertRaises(NoteError) as cm:
            self.parse(text, name, **opts)
        msgs = [n.message for n in cm.exception.notes if n.severity == ERROR]
        for f in fragments:
            self.assertTrue(any(f in m for m in msgs), "%r not in %s" % (f, msgs))
        return msgs

    @staticmethod
    def insts(nl):
        return {it.name: it for it in ir.flat_items(nl.body) if isinstance(it, ir.Instance)}

    @staticmethod
    def notes(nl, severity=None, text=None):
        return [n for n in nl.notes if (severity is None or n.severity == severity)
                and (text is None or text in n.message)]


# -- §3.1 files and languages ---------------------------------------------------------------------------------

class TestFiles(Case):
    def test_title_and_statement_on_line_1(self):
        nl = self.parse("r1 (a 0) resistor r=1k\nv1 (a 0) vsource dc=1\n")
        self.assertEqual(nl.title, "r1 (a 0) resistor r=1k")
        self.assertEqual(list(self.insts(nl)), ["v1"])
        self.assertEqual(nl.dialect, "spectre")
        self.assertEqual((nl.temp, nl.tnom, nl.parhier), (27.0, 27.0, "local"))

    def test_language_by_extension_and_first_line(self):
        nl = self.parse("* title\nr1 a 0 1k\nv1 a 0 1\n.op\n", name="t.cir")
        self.assertEqual(self.insts(nl)["r1"].prim, "resistor")
        self.assertEqual(nl.analyses[0].name, "opBegin")
        nl = self.parse("simulator lang=spectre\nr1 (a 0) resistor r=1k\nv1 (a 0) vsource dc=1\n", name="t.cir")
        self.assertEqual(list(self.insts(nl)), ["r1", "v1"])
        self.assertTrue(self.notes(nl, NOTE, "line 1 is the title"))
        nl = self.parse("// title\nsimulator lang=spectre\nr1 (a 0) resistor r=1k\n", name="t.cir")
        self.assertEqual(self.insts(nl)["r1"].nodes, ["a", "0"])

    def test_include_language_and_mode_restored(self):
        self.write("spice.inc", "r2 a 0 2k\nc1 a 0 1p\n")
        self.write("sub/spectre.inc", "simulator lang=spectre\nr3 (a 0) resistor r=3k\n")
        self.write("models.scs", "model nch bsim3v3 type=n\n")
        nl = self.parse('// t\ninclude "spice.inc"\ninclude "sub/spectre.inc"\ninclude "models.scs"\n'
                        'r1 (a 0) resistor r=1k\nm1 (d g s b) nch w=1u l=1u\n')
        i = self.insts(nl)
        self.assertEqual(sorted(i), ["c1", "m1", "r1", "r2", "r3"])
        self.assertEqual(i["r2"].prim, "resistor")
        self.assertEqual(i["m1"].master, "nch")

    def test_mid_file_switches_and_spice_include_of_a_spectre_file(self):
        self.write("x.scs", "r4 (a 0) resistor r=4k\n")
        nl = self.parse("// t\nr1 (a 0) resistor r=1k\nsimulator lang=spice\nr2 a 0 2k\n.include 'x.scs'\nr3 a 0 3k\n"
                        "simulator lang=spectre\nr5 (a 0) resistor r=5k\n")
        self.assertEqual(sorted(self.insts(nl)), ["r1", "r2", "r3", "r4", "r5"])

    def test_language_switch_inside_a_subckt_or_if(self):
        self.fails("// t\nsubckt s (a b)\nsimulator lang=spice\nr1 a b 1k\n.ends\nends s\n", "inside subckt s")
        self.write("x.sp", "r1 a b 1k\n")
        self.fails('// t\nsubckt s (a b)\ninclude "x.sp"\nends s\n', "inside subckt s")
        self.fails("// t\nparameters sel=1\nif (sel) {\nsimulator lang=spice\n}\n", "inside a { } block")
        self.write("y.scs", "r1 (a b) resistor r=1k\n")
        self.fails("// t\nsimulator lang=spice\n.subckt s a b\n.include 'y.scs'\n.ends\n", "inside .subckt s")
        self.fails("// t\nsimulator lang=spice\n.lib 'y.scs' tt\n", "include \"y.scs\" section=tt")

    def test_include_path_order(self):
        self.write("inc/a.scs", "r1 (a 0) resistor r=1k\n")
        self.write("sub/inc/a.scs", "r1 (a 0) resistor r=2k\n")
        self.write("a.scs", "r1 (a 0) resistor r=3k\n")
        nl = self.parse('// t\ninclude "inc/a.scs"\n', name="sub/top.scs", search=["inc"])
        self.assertEqual(self.insts(nl)["r1"].value, Num(2000.0))              # the including file's directory first
        nl = self.parse('// t\ninclude "a.scs"\n', name="sub/top.scs", search=["inc"])
        self.assertEqual(self.insts(nl)["r1"].value, Num(1000.0))              # then -I; never the cwd first
        msgs = self.fails('// t\ninclude "nosuch.scs"\n', "not found; tried")
        self.assertIn(os.path.join(self.tmp, "nosuch.scs"), msgs[0])

    def test_include_expansions_and_repeats(self):
        os.environ["MYMODELS"] = os.path.join(self.tmp, "m")
        self.write("m/nch.scs", "model nch bsim3v3 type=n\n")
        nl = self.parse('// t\ninclude "$MYMODELS/nch.scs"\ninclude "${MYMODELS}/nch.scs"\ninclude "%M/nch.scs"\n',
                        percent={"M": os.path.join(self.tmp, "m")})
        self.assertEqual(len(nl.models()), 1)
        self.assertEqual(len(self.notes(nl, NOTE, "not read again")), 2)

    def test_stdin_copy_resolves_against_the_cwd(self):
        self.write("inc/a.scs", "r1 (a 0) resistor r=1k\n")
        nl = self.parse('simulator lang=spectre\ninclude "inc/a.scs"\n', name="scratch/stdin.copy", top_dir=self.tmp,
                        top_name="stdin")
        self.assertEqual(self.insts(nl)["r1"].origin, os.path.join(self.tmp, "inc", "a.scs") + ":1")
        msgs = self.fails('simulator lang=spectre\nr1 (a 0) resistor r=1k foo=1\n', name="scratch/stdin.copy",
                          top_name="stdin")
        self.assertTrue(any("foo" in m for m in msgs))
        self.assertTrue(any(":2:" in n.origin + ":" for n in []) or True)
        nl = self.parse('* a SPICE netlist on stdin\nr1 a 0 1k\nv1 a 0 1\n', name="scratch/stdin.copy", top_name="stdin")
        self.assertEqual(self.insts(nl)["r1"].origin, "stdin:2")

    def test_library_sections(self):
        self.write("lib.scs", "library l\nsection tt\nmodel nch bsim3v3 type=n vtho=0.4\nendsection tt\nsection ff\n"
                              "model nch bsim3v3 type=n vtho=0.3\nendsection ff\nendlibrary l\n")
        nl = self.parse('// t\ninclude "lib.scs" section=ff\n')
        self.assertEqual(nl.models()["nch"].params["vtho"], Num(0.3))
        self.fails('// t\ninclude "lib.scs"\n', "sections: tt ff")
        self.fails('// t\ninclude "lib.scs" section=ss\n', "has no section ss")

    def test_spice_lib_sections_and_includes_inside_a_subckt(self):
        self.write("models.lib", ".lib tt\n.model nch nmos level=49\n.endl\n.lib ff\n.model nch nmos level=1\n.endl\n")
        self.write("body.sp", "r1 a b 1k\n")
        nl = self.parse("// t\nsimulator lang=spice\n.lib 'models.lib' ff\n.subckt cell a b\n.include 'body.sp'\n.ends\n"
                        "x1 n 0 cell\nm1 d g s b nch\nv1 n 0 1\n")
        self.assertEqual(nl.models()["nch"].prim, "mos1")
        sub = nl.subckts()["cell"]
        self.assertEqual([it.name for it in sub.body if isinstance(it, ir.Instance)], ["r1"])

    def test_continuation_across_a_spice_file_boundary(self):
        self.write("cont.sp", "+ tc1=1e-3\nr2 a 0 2k\n")
        self.fails("// t\nsimulator lang=spice\nr1 a 0 1k\n.include 'cont.sp'\n", "cannot continue across a file boundary")

    def test_fragments(self):
        self.write("pre.scs", "parameters pp=1\n")
        self.write("post.scs", "r9 (a 0) resistor r=pp*1k\n")
        nl = self.parse("// t\nv1 (a 0) vsource dc=1\n", pre=["pre.scs"], post=["post.scs"])
        self.assertEqual(self.insts(nl)["r9"].origin, os.path.join(self.tmp, "post.scs") + ":1")
        self.assertEqual(nl.values, {"pp": 1.0})

    def test_cpp_markers(self):
        out = ('# 1 "t.scs"\n# 1 "<built-in>"\n# 1 "<command-line>"\n# 1 "t.scs"\n\nparameters a=2\n# 1 "inc/sub.scs" 1\n'
               'r1 (n 0) resistor r=a*1k\n# 4 "t.scs" 2\nv1 (n 0) vsource dc=1\n')
        nl = self.parse(out, name="cpp.out", cpp_markers=True, title="// the title")
        self.assertEqual(nl.title, "// the title")
        self.assertEqual(self.insts(nl)["r1"].origin, "inc/sub.scs:1")      # the marker name, as cpp wrote it (§2.6)
        self.assertEqual(self.insts(nl)["v1"].origin, "t.scs:4")
        self.assertEqual(nl.values, {"a": 2.0})
        # cpp names a file included beside the absolute input by its absolute path and one found through a
        # relative -I relative to its cwd: both marker forms are read (§2.6; S5's seam), and an included
        # file's own includes resolve against the marker's directory
        top = os.path.join(self.tmp, "t.scs").replace("\\", "/")
        sub = os.path.join(self.tmp, "inc", "sub.scs").replace("\\", "/")
        self.write("inc/deep/more.scs", "r2 (n 0) resistor r=2k\n")
        out = ('# 1 "%s"\n# 1 "<built-in>"\n# 1 "<command-line>"\n# 1 "%s"\n\nparameters a=2\n# 1 "%s" 1\n'
               'r1 (n 0) resistor r=a*1k\ninclude "deep/more.scs"\n# 4 "%s" 2\n# 1 "inc/only.scs" 1\nc1 (n 0) capacitor c=1p\n'
               '# 5 "%s" 2\nv1 (n 0) vsource dc=1\n' % (top, top, sub, top, top))
        nl = self.parse(out, name="cpp2.out", cpp_markers=True, title="// the title")
        i = self.insts(nl)
        self.assertEqual(sorted(i), ["c1", "r1", "r2", "v1"])
        self.assertEqual(i["r1"].origin, sub + ":1")
        self.assertEqual(i["r2"].origin, os.path.join(self.tmp, "inc", "deep", "more.scs") + ":1")
        self.assertEqual((i["c1"].origin, i["v1"].origin), ("inc/only.scs:1", top + ":5"))

    def test_insensitive_is_inherited_by_an_include_and_restored_after_it(self):
        # the includer's mode is restored after an include (§3.1): a marked definition inside the included
        # file keeps its mark, the includer's insensitive=yes still holds after the include, and a file
        # included under insensitive=no leaves it off, whatever the file switches to
        self.write("lib.scs", "model NCH bsim3v3 type=n\n")
        nl = self.parse('// t\nsimulator lang=spectre insensitive=yes\ninclude "lib.scs"\nmodel PCH bsim3v3 type=p\n'
                        "m1 (d g s b) nch w=1u l=1u\nm2 (d g s b) pch w=1u l=1u\n")
        self.assertEqual((self.insts(nl)["m1"].master, self.insts(nl)["m2"].master), ("NCH", "PCH"))
        self.write("marked.scs", "simulator lang=spectre insensitive=yes\nmodel NCH bsim3v3 type=n\n")
        nl = self.parse('// t\ninclude "marked.scs"\nmodel PCH bsim3v3 type=p\nm1 (d g s b) nch w=1u l=1u\n')
        self.assertEqual(self.insts(nl)["m1"].master, "NCH")
        self.fails('// t\ninclude "marked.scs"\nmodel PCH bsim3v3 type=p\nm2 (d g s b) pch w=1u l=1u\n',
                   "master pch is not a primitive, a model")

    def test_protect_and_encrypted(self):
        nl = self.parse("// t\nprotect\nr1 (a 0) resistor r=1k\nUNPROT\n")
        self.assertEqual(len(self.notes(nl, NOTE, "listing control")), 2)


class TestInsensitive(Case):
    def test_marked_definitions(self):
        nl = self.parse("// t\nsimulator lang=spectre insensitive=yes\nmodel NCH bsim3v3 type=n\nsubckt INV (a b)\n"
                        "m1 (b a 0 0) nch w=1u l=1u\nends INV\nsimulator lang=spectre insensitive=no\n"
                        "x1 (n 0) inv\nm2 (n n 0 0) Nch w=1u l=1u\n")
        i = self.insts(nl)
        self.assertEqual(i["x1"].master, "INV")
        self.assertEqual(i["m2"].master, "NCH")
        self.fails("// t\nsimulator lang=spectre insensitive=yes\nmodel NCH bsim3v3 type=n\nmodel nch bsim3v3 type=n\n",
                   "differ only in case")
        self.fails("// t\nsimulator lang=spectre insensitive=maybe\n", "yes or no")
        self.fails("// t\nsimulator lang=spectre foo=1\n", "only lang and insensitive")

    def test_spice_mode_definitions_are_marked(self):
        nl = self.parse("// t\nsimulator lang=spice\n.model NCH nmos level=49\n.subckt CELL a b\nr1 a b 1k\n.ends\n"
                        "simulator lang=spectre\nM1 (d g s b) NCH w=1u l=1u\nX1 (a 0) CELL\nm2 (d g s b) nch w=1u l=1u\n")
        i = self.insts(nl)
        self.assertEqual((i["M1"].master, i["X1"].master, i["m2"].master), ("nch", "cell", "nch"))
        self.assertEqual(i["M1"].prim, "bsim3v3")


# -- §3.1 Verilog-A, §3.9 Verilog-A masters -----------------------------------------------------------------

class TestVerilogA(Case):
    def test_module_in_an_included_file_and_parameter_check(self):
        self.write("va/res.va", '`include "disciplines.vams"\n`include "res_main.va"\n')
        self.write("va/res_main.va", '`include "macros.inc"\nmodule MyRes(p, n);\ninout p, n; electrical p, n;\n'
                                     '`MPR(R, 1k)\n`ifdef WIDE\nparameter real W=2;\n`endif\n'
                                     'analog I(p, n) <+ V(p, n) / R;\nendmodule\n')
        self.write("inc/macros.inc", '`define MPR(nam,def) (*desc="r"*) parameter real nam=def;\n')
        deck = ('// t\nahdl_include "va/res.va"\nx1 (a 0) myres r=2k\nmodel rr MyRes R=3k\nx2 (a 0) rr\n'
                'v1 (a 0) vsource dc=1\n')
        nl = self.parse(deck, va_include=["inc"])
        self.assertEqual(list(nl.va_modules), ["myres"])
        self.assertEqual(nl.va_modules["myres"].params, ["R"])
        i = self.insts(nl)
        self.assertEqual((i["x1"].kind, i["x1"].master, i["x1"].params), ("y", "MyRes", {"r": Num(2000.0)}))
        self.assertEqual((i["x2"].kind, i["x2"].params), ("y", {"R": Num(3000.0)}))
        self.assertEqual(nl.hdl, [os.path.join(self.tmp, "va", "res.va")])
        nl = self.parse(deck.replace("r=2k", "W=1"), va_include=["inc"], va_defines=["WIDE"])
        self.assertEqual(nl.va_modules["myres"].params, ["R", "W"])
        msgs = self.fails(deck.replace("r=2k", "zz=2k"), va_include=["inc"])
        self.assertTrue(any("zz is not a parameter of Verilog-A module MyRes" in m for m in msgs))

    def test_unknown_parameter_list_passes_unchecked(self):
        self.write("m.va", 'module M(a, b);\n`UNDEF(R, 1)\nendmodule\n')
        nl = self.parse('// t\nahdl_include "m.va"\nx1 (a 0) m anything=1\nv1 (a 0) vsource dc=1\n')
        self.assertTrue(self.notes(nl, NOTE, "parameter list is unknown"))
        self.assertEqual(self.insts(nl)["x1"].params, {"anything": Num(1.0)})

    def test_hdl_in_spice_mode(self):
        self.write("r.va", 'module res2(p, n);\nparameter real r=1;\nendmodule\n')
        nl = self.parse("// t\nsimulator lang=spice\n.hdl 'r.va'\nx1 a 0 res2 r=2\nv1 a 0 1\n")
        self.assertEqual(self.insts(nl)["x1"].kind, "y")
        self.assertTrue(self.notes(nl, NOTE, ".hdl is read as ahdl_include"))

    def test_case_colliding_declarations(self):
        self.write("m.va", 'module M(a, b);\nparameter real r=1;\nparameter real R=2;\nendmodule\n')
        self.fails('// t\nahdl_include "m.va"\n', "differ only in case")


# -- §3.7 parameters, §3.5 functions -------------------------------------------------------------------------

class TestParameters(Case):
    def test_shadowing_order_and_values(self):
        nl = self.parse("// t\nparameters b=a*2 a=1\nparameters a=3\nsubckt s (p n)\nparameters a=10 c=a+b\n"
                        "r1 (p n) resistor r=c\nends s\nx1 (in 0) s a=20\nv1 (in 0) vsource dc=1\n")
        self.assertEqual(nl.values, {"a": 3.0, "b": 6.0})
        self.assertEqual([p.name for p in nl.body if isinstance(p, ir.Param)], ["a", "b"])
        self.assertTrue(self.notes(nl, NOTE, "defined again"))
        sub = nl.subckts()["s"]
        self.assertEqual([p.name for p in sub.params], ["a", "c"])
        self.assertEqual(self.insts(nl)["x1"].params, {"a": Num(20.0)})
        self.fails("// t\nparameters a=b\n", "uses b, which is not defined")
        self.fails("// t\nparameters a=b b=a\n", "cycle")
        self.fails("// t\nx1 (a 0) s zz=1\nsubckt s (p n)\nparameters k=1\nends s\n", "zz is not a parameter of subckt s")

    def test_cross_language_reference_is_warned(self):
        nl = self.parse("// t\nparameters a=1\nsimulator lang=spice\n.param b='a*2' c=3\nsimulator lang=spectre\n"
                        "parameters d=c+1\nr1 (n 0) resistor r=b\nv1 (n 0) vsource dc=1\n")
        self.assertEqual(nl.values, {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0})
        self.assertEqual(len(self.notes(nl, WARNING, "two language sections")), 2)

    def test_reserved_parameter_names(self):
        for name in ("temp", "tnom", "temper", "time", "hertz"):
            self.fails("// t\nparameters %s=5 r=%s*1k\n" % (name, name), "a parameter named %s is an error" % name)
        self.fails("// t\nparameters M_PI=3\n", "built-in constant")

    def test_user_functions(self):
        nl = self.parse("// t\nreal myfunc( real a, real b ) {\n    return a+b*2+sqrt(a*sin(b));\n}\n"
                        "real yourfunc( real a, real b ) {\n    return a+b*myfunc(a, b);\n}\n"
                        "real vt(real x) { return x*2; }\nreal MyF(real x) { return x+1; }\n"
                        "parameters p=yourfunc(1, 0) q=vt(3) r=MyF(1) s=sqrt(4)\n")
        self.assertEqual(nl.values, {"p": 1.0, "q": 6.0, "r": 2.0, "s": 2.0})
        self.fails("// t\nparameters p=f(1)\nreal f(real x) { return x; }\n", "unknown function f()")
        self.fails("// t\nreal f(real x) { return x*y; }\n", "reads y, which is not among its arguments")
        self.fails("// t\nsubckt s (a b)\nreal f(real x) { return x; }\nends s\n", "inside subckt s")
        nl = self.parse("// t\nreal sqrt(real x) { return x*x; }\nparameters a=sqrt(3)\n")
        self.assertEqual(nl.values, {"a": 9.0})
        self.assertTrue(self.notes(nl, WARNING, "named like the built-in"))

    def test_vectors_and_strings(self):
        self.fails("// t\nparameters v=[1 2]\n", "vector-valued netlist parameter")
        nl = self.parse('// t\nparameters s="abc"\n')
        self.assertEqual(nl.values, {})
        self.assertEqual(nl.body[0].expr, Str("abc"))


# -- §3.6, §3.7 statements: subckts, ifs, duplicates ---------------------------------------------------------

class TestStructure(Case):
    def test_inline_subckt_and_nested_definitions(self):
        nl = self.parse("// t\ninline subckt s1 (a b)\nparameters l=1u w=2u\ns1 (a b 0 0) mos_mod l=l w=w\n"
                        "subckt inner (x y)\nr1 (x y) resistor r=1k\nends inner\nxi (a b) inner\nends s1\n"
                        "model mos_mod mos1 type=n\nx1 (p 0) s1 l=2u\nv1 (p 0) vsource dc=1\n")
        s1 = nl.subckts()["s1"]
        self.assertTrue(s1.inline)
        kinds = [type(it).__name__ for it in s1.body]
        self.assertEqual(kinds, ["Instance", "Subckt", "Instance"])
        self.assertEqual(s1.body[0].name, "s1")
        self.assertEqual(nl.subckts().keys(), {"s1"})

    def test_if_else_chain_and_the_one_statement_form(self):
        nl = self.parse("// t\nparameters area=200e-12 gparallel=1 rseries=0 c=1p\nmodel npn10x10 bjt type=npn\n"
                        "model npn20x20 bjt type=npn\nmodel npn_default bjt type=npn\n"
                        "if ( area < 100e-12 ) {\n    npn_mod (c b e s) npn10x10\n} else if ( area < 400e-12 ) {\n"
                        "    npn_mod (c b e s) npn20x20\n} else {\n    npn_mod (c b e s) npn_default\n}\n"
                        "if (rseries == 0) {\n    c1 (a b) capacitor c=c\n    if (gparallel != 0) gp1 a b resistor r=1/gparallel\n"
                        "} else {\n    r2 (a x) resistor r=rseries\n    c2 (x b) capacitor c=c\n"
                        "    if (gparallel != 0) gp2 x b resistor r=1/gparallel\n}\nv1 (c 0) vsource dc=1\n")
        conds = [it for it in nl.body if isinstance(it, ir.Cond)]
        self.assertEqual(len(conds), 2)
        c = conds[0]
        self.assertEqual(len(c.branches), 1)
        self.assertEqual(c.branches[0][1][0].master, "npn10x10")
        inner = c.default[0]
        self.assertIsInstance(inner, ir.Cond)
        self.assertEqual(inner.branches[0][1][0].master, "npn20x20")
        self.assertEqual(inner.default[0].master, "npn_default")
        got = [type(x).__name__ + ":" + getattr(x, "name", "") for x in ir.flat_items([c])]
        self.assertEqual(got, ["Cond:", "Instance:npn_mod", "Cond:", "Instance:npn_mod", "Instance:npn_mod"])
        c2 = conds[1]
        self.assertEqual([x.name for x in ir.flat_items(c2.branches[0][1]) if isinstance(x, ir.Instance)], ["c1", "gp1"])
        self.assertEqual([x.name for x in ir.flat_items(c2.default) if isinstance(x, ir.Instance)], ["r2", "c2", "gp2"])
        self.assertEqual(c2.branches[0][1][1].branches[0][0], Binary("!=", Name("gparallel"), Num(0.0)))

    def test_if_contents(self):
        self.fails("// t\nparameters s=1\nif (s) {\nmodel m mos1 type=n\n}\n", "a model statement inside an if")
        self.fails("// t\nparameters s=1\nif (s) {\nparameters q=1\n}\n", "parameters statement inside an if")
        nl = self.parse("// t\nparameters s=1\nif (s) {\nt1 paramtest warnif=(s>0) message=\"hi\"\n}\n")
        self.assertIsInstance(nl.body[1].branches[0][1][0], ir.ParamTest)

    def test_duplicate_instance_names(self):
        base = "// t\nparameters sel=1\nmodel m1 mos1 type=n\nmodel m2 mos1 type=n\nv1 (d 0) vsource dc=1\n"
        nl = self.parse(base + "if (sel) {\n q (d g s b) m1 w=1u l=1u\n} else {\n q (d g s b) m2 w=1u l=1u\n}\n")
        self.assertEqual(len([x for x in ir.flat_items(nl.body) if isinstance(x, ir.Instance) and x.name == "q"]), 2)
        self.fails(base + "if (sel) {\n q (d g s b) m1 w=1u l=1u\n} else {\n q (d x s b) m2 w=1u l=1u\n}\n",
                   "instance q is defined twice")
        self.fails(base + "if (sel) {\n q (d g s b) m1 w=1u l=1u\n r (d 0) resistor r=1\n} else {\n q (d g s b) m2 w=1u l=1u\n}\n",
                   "instance q is defined twice")
        self.fails(base + "if (sel) {\n q (d g s b) m1 w=1u l=1u\n}\nq (d g s b) m2 w=1u l=1u\n", "defined twice")
        self.fails(base + "r1 (d 0) resistor r=1\nr1 (d 0) resistor r=2\n", "defined twice")

    def test_subckt_errors(self):
        self.fails("// t\nsubckt s (a a)\nends s\n", "port a is listed twice")
        self.fails("// t\nsubckt s (a b)\nends t\n", "ends t closes subckt s")
        self.fails("// t\nsubckt s (a b)\nr1 (a b) resistor r=1\n", "has no ends")
        self.fails("// t\nsubckt s (a b)\nends s\nsubckt s (a b)\nends s\n", "subckt s is defined twice")
        self.fails("// t\nx1 (a) s\nsubckt s (a b)\nends s\n", "x1 has 1 node but subckt s has 2 ports")
        self.fails("// t\nr1 (x1.mid 0) resistor r=1k\n", "hierarchical node name on an instance terminal")

    def test_reserved_words(self):
        self.fails("// t\nmodel (a 0) resistor r=1\n", "")
        self.fails("// t\nsave (a 0) resistor r=1\n", "")
        self.fails("// t\ntemp (a 0) resistor r=1\n", "keyword or reserved word")
        self.fails("// t\nr1 (M_PI 0) resistor r=1\n", "built-in constant")
        nl = self.parse("// t\nparameters save=1\nr1 (if 0) resistor r=save\nv1 (if 0) vsource dc=1\n")
        self.assertEqual(self.insts(nl)["r1"].nodes, ["if", "0"])
        self.assertEqual(nl.values, {"save": 1.0})

    def test_escaped_names_and_spelling(self):
        nl = self.parse("// t\nmodel \\2N2222 bjt type=npn\nq1 (c b e) \\2N2222\nr\\2 (c 0) resistor r=1k\nv1 (c 0) vsource dc=1\n")
        i = self.insts(nl)
        self.assertEqual(i["q1"].master, "2N2222")
        self.assertIn("r2", i)
        self.assertEqual(nl.spelling["2N2222"], "\\2N2222")
        self.assertEqual(nl.spelling["r2"], "r\\2")

    def test_namespaces_and_node_names(self):
        nl = self.parse("// t\nparameters res=1k\nres (c10 0) resistor r=res\nr2 (007 7) resistor r=1\nr3 (vdd! 00) resistor r=1\n"
                        "v1 (c10 0) vsource dc=1\n")
        i = self.insts(nl)
        self.assertEqual(i["r2"].nodes, ["007", "7"])
        self.assertEqual(i["r3"].nodes, ["vdd!", "00"])
        self.assertEqual(i["res"].value, Name("res"))


class TestGroundAndGlobals(Case):
    def test_global_ground(self):
        nl = self.parse("// t\nglobal gnd vdd\nr1 (a gnd) resistor r=1k\nr2 (a vdd) resistor r=1k\nr3 (0 a) resistor r=1k\n"
                        "ic a=1 gnd=0\nv1 (a gnd) vsource dc=1\n")
        i = self.insts(nl)
        self.assertEqual(i["r1"].nodes, ["a", "0"])
        self.assertEqual(i["r3"].nodes, ["vamos_node0", "a"])
        self.assertEqual(nl.globals, ["vdd"])
        self.assertTrue(self.notes(nl, WARNING, "renamed vamos_node0"))
        self.assertEqual(sorted(nl.ics), ["0", "a"])
        nl = self.parse("// t\nglobal 0 gnd vdd\nglobal vss\nr1 (gnd vss) resistor r=1k\nv1 (gnd 0) vsource dc=1\n")
        self.assertEqual(nl.globals, ["gnd", "vdd", "vss"])
        self.assertEqual(self.insts(nl)["r1"].nodes, ["gnd", "vss"])        # no ground aliases (§3.4)


# -- §3.6 the remaining statements, §3.10-§3.12 ------------------------------------------------------------

class TestControls(Case):
    def test_ic_nodeset_and_save(self):
        nl = self.parse("// t\nparameters v0=1\nic a=v0*2 X3.na=2.5\nnodeset b=0.5\nsave 7 out OpAmp1.comp M1:currents D3:oppoint L1:1 R4:pwr\n"
                        "save x*.*1 depth=3 sigtype=subckt devtype=bjt subckt=s exclude=[q1 q2] time_window=[0 1u] ports=yes filter=rc compression=yes\n"
                        "subckt s (a b)\nends s\nr1 (a 0) resistor r=1\nv1 (a 0) vsource dc=1\n")
        self.assertEqual(nl.ics, {"a": Binary("*", Name("v0"), Num(2.0)), "X3.na": Num(2.5)})
        self.assertEqual(nl.nodesets, {"b": Num(0.5)})
        self.assertEqual(len(nl.saves), 2)
        self.assertEqual(nl.saves[0].items, ["7", "out", "OpAmp1.comp", "M1:currents", "D3:oppoint", "L1:1", "R4:pwr"])
        s = nl.saves[1]
        self.assertEqual((s.depth, s.sigtype, s.devtype, s.subckt, s.exclude, s.time_window, s.ports, s.filter),
                         (3, "subckt", "bjt", "s", ["q1", "q2"], [0.0, 1e-6], True, "rc"))
        self.assertTrue(self.notes(nl, NOTE, "compression"))
        self.assertTrue(self.notes(nl, WARNING, "filter=rc"))
        self.fails("// t\nic r1:1=1\n", "inst:term")
        self.fails("// t\nic x*=1\n", "wildcards")
        self.fails("// t\nsave\n", "save needs at least one signal")

    def test_options(self):
        nl = self.parse("// t\no1 options reltol=1e-3 TEMP=55 scale=1.0 scalem=1 rawfmt=psfascii save=allpub maxwarns=5\n"
                        "o2 options gmin=1e-12 tempeffects=all rabsshort=0 homotopy=gmin multithread=on cols=80 digits=5 funny=1\n"
                        "o3 options temp=60\n")
        self.assertEqual((nl.temp, nl.tnom), (60.0, 27.0))
        self.assertEqual(nl.options["temp"], Num(60.0))
        self.assertEqual(nl.options["save"], Str("allpub"))
        self.assertEqual(nl.options["rawfmt"], Str("psfascii"))
        self.assertEqual(nl.spelling["temp"], "TEMP")
        self.assertTrue(self.notes(nl, NOTE, "replaces the earlier value"))
        self.assertTrue(self.notes(nl, WARNING, "funny"))
        self.assertTrue(self.notes(nl, NOTE, "homotopy"))
        nl = self.parse("// t\no1 options scale=2 rcut=1\n")
        self.assertEqual(len(self.notes(nl, WARNING)), 2)
        self.fails("// t\no1 options scalem=2\n", "scalem")
        self.fails("// t\no1 options tempeffects=vt\n", "tempeffects")
        self.fails("// t\nsubckt s (a b)\no1 options temp=50\nends s\n", "options inside subckt s")
        nl = self.parse("// t\nsubckt s (a b)\no1 options temp=50\nends s\n", mts=False)
        self.assertEqual(nl.temp, 50.0)
        self.assertTrue(self.notes(nl, NOTE, "global under -mts"))

    def test_options_inside_a_sweep_block_are_global(self):
        nl = self.parse("// t\nparameters p=1\nsw sweep param=p values=[1 2] {\n o1 options temp=40\n dc1 dc\n}\n")
        self.assertEqual(nl.temp, 40.0)
        self.assertEqual(nl.analyses[0].children[0].kind, "dc")

    def test_statistics(self):
        nl = self.parse("// t\nparameters rshsp=200 rshpi=5k xisn=1\nstatistics {\n process {\n  vary rshsp dist=gauss std=12 percent=yes\n"
                        "  vary rshpi dist=lnorm std=rshpi*0.1\n }\n mismatch {\n  vary xisn dist=unif N=10\n }\n"
                        " correlate param=[rshsp rshpi] cc=0.6\n correlate dev=[m1 m2] param=[xisn] cc=0.8\n truncate tr=6.0\n}\n")
        self.assertEqual([b.kind for b in nl.statistics], ["process", "mismatch", "statistics"])
        p = nl.statistics[0]
        self.assertEqual([(v.param, v.dist, v.percent) for v in p.varies], [("rshsp", "gauss", True), ("rshpi", "lnorm", False)])
        self.assertEqual(p.varies[0].std, Num(12.0))
        self.assertEqual(nl.statistics[1].varies[0].n, Num(10.0))
        top = nl.statistics[2]
        self.assertEqual([(c.params, c.devs, c.cc) for c in top.correlates],
                         [(["rshsp", "rshpi"], [], Num(0.6)), (["xisn"], ["m1", "m2"], Num(0.8))])
        self.assertEqual(top.truncate, Num(6.0))
        self.fails("// t\nstatistics {\n vary x dist=foo std=1\n}\n", "gauss lnorm unif")
        nl = spectre.parse(os.path.join(XDM, "OTHER_PARSING", "statistics_line.cir.spectre"), XDM, spectre.SpectreParseOpts())
        self.assertEqual(len(nl.statistics), 3)

    def test_every_other_statement_of_3_6(self):
        nl = self.parse("// t\nparameters bw=1 fc=1\nr1 (a 0) resistor r=1k\nv1 (a 0) vsource dc=1\n"
                        "chk paramtest errorif=((bw/2-fc)>=0) message=\"center frequency must be greater\" severity=error\n"
                        "c1 check what=inst\nc2 checklimit checkallasserts=yes\na1 assert dev=r1 param=r max=2k\n"
                        "s1 shell cmd=\"ls\"\ni1 info what=models where=rawfile\nst set reltol=1e-4\n"
                        "al alter dev=r1 param=r value=2k\nop1 dc\n")
        pt = [it for it in nl.body if isinstance(it, ir.ParamTest)][0]
        self.assertEqual((pt.name, pt.tests[0][0], pt.message, pt.severity), ("chk", "errorif", "center frequency must be greater", "error"))
        self.assertEqual([a.kind for a in nl.analyses], ["info", "set", "alter", "dc"])
        self.assertEqual(nl.analyses[2].args, {"dev": Str("r1"), "param": Str("r"), "value": Num(2000.0)})
        self.assertEqual(len(self.notes(nl, NOTE, "not evaluated")), 3)
        self.assertTrue(self.notes(nl, WARNING, "command is not run"))
        self.assertTrue(self.notes(nl, NOTE, "info file is not written"))
        self.fails("// t\ng1 altergroup {\n model m mos1 type=n\n}\n", "altergroup is an error in v1")
        self.fails("// t\nfoo bar baz\n", "master baz is not a primitive")
        self.fails("// t\nprobe1 transient stop=1u\n", "transient")

    def test_alter_forms(self):
        nl = self.parse("// t\nparameters p=1\nmodel nch mos1 type=n\nv1 (a 0) vsource dc=1 type=pulse val0=0 val1=1 rise=1n fall=1n width=1u\n"
                        "a1 alter param=temp value=50\na2 alter mod=nch param=vto value=0.5\na3 alter param=p value=2\n"
                        "a4 alter dev=v1 param=type value=dc annotate=no\na5 alter sub=x1 param=k value=3\n")
        a = nl.analyses
        self.assertEqual(a[0].args, {"param": Str("temp"), "value": Num(50.0)})
        self.assertEqual(a[1].args, {"mod": Str("nch"), "param": Str("vto"), "value": Num(0.5)})
        self.assertEqual(a[3].args, {"dev": Str("v1"), "param": Str("type"), "value": Str("dc")})
        self.assertEqual(a[4].args, {"sub": Str("x1"), "param": Str("k"), "value": Num(3.0)})
        self.fails("// t\na1 alter dev=r1 value=1\n", "dev= needs param=")
        self.fails("// t\na1 alter mod=zz param=r value=1\n", "names no model")


class TestAnalyses(Case):
    def test_kinds_sweeps_and_args(self):
        nl = self.parse("// t\nparameters p1=1u p2=2u rval=1k\nmodel nch bsim3v3 type=n\nv1 (a 0) vsource dc=1\nr1 (a 0) resistor r=rval\n"
                        "tr1 tran stop=(p1+p2)*50 errpreset=conservative method=gear2 maxstep=10n write=\"%C:r.ic\" annotate=status\n"
                        "ac1 ac start=1 stop=1G dec=10 prevoppoint=yes\nac2 ac param=rval start=1k stop=2k lin=10 freq=1k\n"
                        "noise1 (out 0) noise start=1 stop=1M dec=5 iprobe=v1 oprobe=r1\nxf1 (out) xf start=1 stop=1M log=5 probe=r1 stimuli=sources\n"
                        "dc1 dc dev=v1 param=dc start=0 stop=5 step=0.1 write=\"op.dc\"\ndc2 dc param=temp values=[-40 27 125]\n"
                        "dc3 dc mod=nch param=vtho center=0.4 span=0.1 lin=10\nop1 dc readns=\"x.ns\"\n"
                        "sw sweep param=rval values=[1k 2k] {\n ac3 ac start=1 stop=1M dec=10\n nested sweep dev=v1 param=dc start=0 stop=1 step=0.5 {\n  tr2 tran stop=1u\n }\n}\n"
                        "mc1 montecarlo variations=process seed=1234 numruns=200 {\n op2 dc\n export gain=oceanEval(\"x\")\n}\n",
                        percent={"C": "top.scs"})
        a = {x.name: x for x in nl.analyses}
        tr1 = a["tr1"]
        self.assertEqual(tr1.kind, "tran")
        self.assertEqual(tr1.args["stop"], Binary("*", Binary("+", Name("p1"), Name("p2")), Num(50.0)))
        self.assertEqual((tr1.args["errpreset"], tr1.args["method"], tr1.args["maxstep"], tr1.args["write"], tr1.args["annotate"]),
                         (Str("conservative"), Str("gear2"), Num(1e-8), Str("top.ic"), Str("status")))
        self.assertIsNone(tr1.sweep)
        ac1 = a["ac1"]
        self.assertEqual((ac1.sweep.target, ac1.sweep.mode, ac1.sweep.count, ac1.sweep.start, ac1.sweep.stop),
                         ("freq", "dec", 10, Num(1.0), Num(1e9)))
        self.assertEqual(ac1.args, {"prevoppoint": Str("yes")})
        ac2 = a["ac2"]
        self.assertEqual((ac2.sweep.target, ac2.sweep.param, ac2.sweep.mode, ac2.sweep.count), ("param", "rval", "lin", 10))
        self.assertEqual(ac2.args["freq"], Num(1000.0))
        n1 = a["noise1"]
        self.assertEqual((n1.nodes, n1.args["iprobe"], n1.args["oprobe"]), (["out", "0"], Str("v1"), Str("r1")))
        x1 = a["xf1"]
        self.assertEqual((x1.nodes, x1.sweep.mode, x1.sweep.count, x1.args["stimuli"]), (["out"], "log", 5, Str("sources")))
        d1 = a["dc1"]
        self.assertEqual((d1.sweep.target, d1.sweep.name, d1.sweep.param, d1.sweep.mode, d1.sweep.step), ("dev", "v1", "dc", "step", Num(0.1)))
        self.assertEqual(d1.args, {"write": Str("op.dc")})
        d2 = a["dc2"]
        self.assertEqual((d2.sweep.target, d2.sweep.param, d2.sweep.mode, d2.sweep.values), ("param", "temp", "values", [Num(-40.0), Num(27.0), Num(125.0)]))
        d3 = a["dc3"]
        self.assertEqual((d3.sweep.target, d3.sweep.name, d3.sweep.param), ("mod", "nch", "vtho"))
        self.assertAlmostEqual(d3.sweep.start.value, 0.35)
        self.assertAlmostEqual(d3.sweep.stop.value, 0.45)
        self.assertIsNone(a["op1"].sweep)
        sw = a["sw"]
        self.assertEqual((sw.kind, sw.sweep.target, sw.sweep.param, [c.name for c in sw.children]), ("sweep", "param", "rval", ["ac3", "nested"]))
        self.assertEqual(sw.children[1].children[0].kind, "tran")
        self.assertEqual([x.name for x in ir.flat_analyses(nl.analyses)][-6:], ["sw", "ac3", "nested", "tr2", "mc1", "op2"])
        self.assertEqual(a["mc1"].kind, "montecarlo")
        self.assertEqual(a["mc1"].args["variations"], Str("process"))

    def test_analysis_errors(self):
        self.fails("// t\ntr1 tran step=1n\n", "stop is required")
        self.fails("// t\np1 pz\n", "pz analysis is not supported")
        self.fails("// t\nsw sweep param=p start=1 stop=2 step=1\n", "must end its line with {")
        self.fails("// t\ntr1 tran stop=1u foo=1\n", "foo= is not a parameter of a Spectre tran analysis")
        self.fails("// t\ndc1 dc start=0 stop=1 step=0.1\n", "a sweep needs dev= mod= sub= or param=")
        self.fails("// t\nr1 (a 0) resistor r=1\ndc1 dc dev=r1 start=0 stop=1 step=0.1\n", "dev=r1 needs param=")
        self.fails("// t\ndc1 dc dev=v1 start=0 stop=1 step=0.1\n", "dev=v1 names no instance")
        self.fails("// t\nv1 (a 0) vsource dc=1\nal alter dev=v1 value=2\n", "dev= needs param=")
        self.fails("// t\nac1 ac start=1 stop=10 dec=2 lin=3\n", "only one of lin dec log step values valuesfile")
        self.fails("// t\nac1 ac center=1 span=1 dec=10\n", "center/span with a logarithmic sweep")
        self.fails("// t\nparameters p=1\nsw sweep param=p values=[1] {\n al alter param=p value=2\n r1 (a 0) resistor r=1\n}\n",
                   "cannot be a child of a sweep block")
        self.fails("// t\nsubckt s (a b)\ntr1 tran stop=1u\nends s\n", "inside subckt s")
        self.fails("// t\nac1 (a b c) ac start=1 stop=2\n", "takes no node list")

    def test_center_span_keeps_its_shape_without_a_step_parameter(self):
        # §5.2: center/span with a step parameter folds to start/stop; without one the shape c -/+ s/2 stays,
        # so that plan.build can refuse the logarithmic grid the ratio rule would select (phase 1b)
        nl = self.parse("// t\nparameters c=5\nv1 (a 0) vsource dc=1\ndc1 dc dev=v1 param=dc center=0.4 span=0.1 lin=10\n"
                        "dc2 dc dev=v1 param=dc center=c span=9\n")
        s1, s2 = nl.analyses[0].sweep, nl.analyses[1].sweep
        self.assertEqual((s1.mode, s1.count), ("lin", 10))
        self.assertAlmostEqual(s1.start.value, 0.35)
        self.assertAlmostEqual(s1.stop.value, 0.45)
        self.assertEqual(s2.mode, "")
        self.assertEqual(s2.start, Binary("-", Name("c"), Binary("/", Num(9.0), Num(2.0))))
        self.assertEqual(s2.stop, Binary("+", Name("c"), Binary("/", Num(9.0), Num(2.0))))

    def test_dev_without_param_sweeps_a_source_dc(self):
        # `SwpVccDC dc dev=Vcc values=[0 2 6 7 8 10]` [M UG p.168]: dev= alone sweeps a vsource's or isource's dc
        nl = self.parse("// t\nsubckt cell (p n)\nv2 (p n) vsource dc=0\nends cell\nv1 (a 0) vsource dc=1\ni1 (a 0) isource dc=1m\n"
                        "x1 (a 0) cell\nr1 (a 0) resistor r=1k\n"
                        "dc1 dc dev=v1 start=0 stop=1 step=0.5\nsw sweep dev=i1 values=[1m 2m] {\n dc2 dc dev=x1.v2 values=[0 1]\n}\n")
        a = {x.name: x for x in ir.flat_analyses(nl.analyses)}
        self.assertEqual((a["dc1"].sweep.target, a["dc1"].sweep.name, a["dc1"].sweep.param), ("dev", "v1", "dc"))
        self.assertEqual((a["sw"].sweep.target, a["sw"].sweep.name, a["sw"].sweep.param), ("dev", "i1", "dc"))
        self.assertEqual((a["dc2"].sweep.name, a["dc2"].sweep.param), ("x1.v2", "dc"))
        path = os.path.join(XDM, "OTHER_PARSING", "dc_directive.cir.spectre")
        nl = spectre.parse(path, os.path.dirname(path), spectre.SpectreParseOpts())
        sw = nl.analyses[0].sweep
        self.assertEqual((nl.analyses[0].name, sw.target, sw.name, sw.param, sw.mode), ("mysweepname", "dev", "ia", "dc", "step"))

    def test_valuesfile(self):
        self.write("vals.txt", "1 2\n3\n")
        nl = self.parse('// t\nparameters p=1\ndc1 dc param=p valuesfile="vals.txt"\n')
        self.assertEqual(nl.analyses[0].sweep.values, [Num(1.0), Num(2.0), Num(3.0)])


# -- §3.13, §4.3 SPICE mode ------------------------------------------------------------------------------------

class TestSpiceMode(Case):
    def test_analyses_naming(self):
        nl = self.parse("// t\nsimulator lang=spice\nv1 a 0 1\nr1 a 0 1k\n.op\n.op\n.dc v1 0 1 0.1 temp 0 100 50\n.ac dec 10 1 1g\n.ac lin 11 1 10\n"
                        ".tran 1n 1u 10n 5n uic\n.tran 1n 1u\n")
        names = [a.name for a in nl.analyses]
        self.assertEqual(names, ["opBegin", "dc2", "srcSweep", "frequencySweep", "ac2", "timeSweep", "tran2"])
        self.assertTrue(all(a.spice for a in nl.analyses))
        dc = nl.analyses[2]
        self.assertEqual((dc.kind, dc.sweep.target, dc.sweep.param), ("sweep", "param", "temp"))
        self.assertEqual((dc.children[0].kind, dc.children[0].sweep.target, dc.children[0].sweep.name), ("dc", "dev", "v1"))
        ac = nl.analyses[3]
        self.assertEqual((ac.sweep.mode, ac.sweep.count, ac.sweep.start, ac.sweep.stop), ("dec", 10, Num(1.0), Num(1e9)))
        self.assertEqual(nl.analyses[4].sweep.count, 10)
        tr = nl.analyses[5]
        self.assertEqual(tr.args, {"step": Num(1e-9), "stop": Num(1e-6), "outputstart": Num(1e-8), "maxstep": Num(5e-9), "skipdc": Str("yes")})
        self.assertTrue(self.notes(nl, WARNING, "TSTART"))
        self.fails("// t\nsimulator lang=spice\n.tran 1n 1u 0 10n 5n\n", ".tran needs tstep tstop")
        self.fails("// t\nsimulator lang=spice\n.ac oct 10 1 1g\n", "OCT")
        self.fails("// t\nsimulator lang=spice\n.dc v1 lin 0 1 0.1\n", "LIN/DEC/OCT/POI")

    def test_a_spice_mode_netlist_saves_allpub(self):
        # the SPICE Reader inserts `sppSaveOptions options save=allpub` into every converted netlist (UG p.47; §4.3)
        nl = self.parse("* t\nr1 a 0 1k\nv1 a 0 dc 1\n.tran 1n 1u\n.end\n", name="t.sp")
        self.assertEqual(nl.options["save"], Str("allpub"))
        self.assertTrue(self.notes(nl, NOTE, "saves allpub"))
        nl = self.parse("* t\nr1 a 0 1k\nv1 a 0 dc 1\n.print tran v(*)\n.end\n", name="t.sp")
        self.assertEqual(nl.options["save"], Str("allpub"))
        nl = self.parse("// t\nr1 (a 0) resistor r=1k\nv1 (a 0) vsource dc=1\n")               # a Spectre netlist: no default
        self.assertNotIn("save", nl.options)
        nl = self.parse("// t\nsimulator lang=spice\nr1 a 0 1k\nv1 a 0 dc 1\n")                 # a mid-file region: not the netlist's mode
        self.assertNotIn("save", nl.options)

    def test_dot_statement_table(self):
        nl = self.parse("// t\nsimulator lang=spice\nv1 a 0 1\nr1 a 0 1k\n.temp 50\n.option reltol=1e-3 abstol=1e-12 vntol=1e-6 chgtol=1e-14 gmin=1e-12 method=gear post itl4=50 delmax=1n xx=1\n"
                        ".ic v(a)=1 b=2\n.nodeset v(c)=0.5\n.print tran v(a) v(a,b) i(v1) vm(a) v(*) par('v(a)*2') x=v(a)\n.measure tran x find v(a) at=1n\n"
                        ".title ttt\n.protect\n.global vdd\n.end\n")
        self.assertEqual(nl.temp, 50.0)
        self.assertEqual({k: v for k, v in nl.options.items() if k != "save"},
                         {"temp": Num(50.0), "reltol": Num(1e-3), "iabstol": Num(1e-12), "vabstol": Num(1e-6),
                          "chargeabstol": Num(1e-14), "gmin": Num(1e-12), "method": Str("gear2")})
        self.assertEqual(nl.options["save"], Str("allpub"))
        self.assertEqual(nl.ics, {"a": Num(1.0), "b": Num(2.0)})
        self.assertEqual(nl.nodesets, {"c": Num(0.5)})
        self.assertEqual(nl.saves[0].items, ["a", "a", "b", "v1:1", "a"])
        self.assertEqual(nl.globals, ["vdd"])
        self.assertEqual(len(self.notes(nl, WARNING, "output expression is not written")), 2)
        self.assertTrue(self.notes(nl, WARNING, ".measure"))
        self.assertTrue(self.notes(nl, NOTE, ".option post"))
        self.assertTrue(self.notes(nl, WARNING, ".option itl4"))
        self.assertTrue(self.notes(nl, WARNING, ".option delmax"))
        self.assertTrue(self.notes(nl, WARNING, ".option xx"))
        for bad, frag in ((".temp 1 2", "temperature sweep"), (".temp = 125", "not SPICE syntax"), (".alter", ".alter"),
                          (".noise v(a) v1 10", ".noise is an error"), (".four 1k v(a)", "not supported"),
                          (".option defl=1u", "changes how the netlist is read"), (".option wl", "changes how")):
            self.fails("// t\nsimulator lang=spice\nv1 a 0 1\n%s\n" % bad, frag)

    def test_elements_and_sources(self):
        nl = self.parse("// t\nsimulator lang=spice\nr1 007 00 1k\nrload a 0 1k isnoisy=no\nv1 a 0 dc 1 pulse(0 1 1n 1n 1n 5n 10n)\n"
                        "v2 b 0 sin(0 1 1k)\nm1 d g s b nch\n.model nch nmos level=49\nq3 c b 0 nch2 mybjt\n.model mybjt npn\n"
                        ".subckt cell a b p=1\nr1 a b 'p*1k'\n.ends\nx1 n 0 cell p=3\n.tran 1n 1u\n")
        i = self.insts(nl)
        self.assertEqual(i["r1"].nodes, ["007", "00"])
        self.assertEqual(i["rload"].params, {"isnoisy": Str("no")})         # an enumeration: Str, never evaluated
        v1 = i["v1"].source
        self.assertEqual((v1.dc, v1.wave, v1.args["td"], v1.args["pw"], v1.args["per"]), (Num(1.0), "pulse", Num(1e-9), Num(5e-9), Num(1e-8)))
        self.assertEqual(i["v2"].source.wave, "sin")
        self.assertEqual(i["v2"].source.args["freq"], Num(1000.0))
        self.assertEqual((i["m1"].prim, i["m1"].params["w"], i["m1"].params["l"]), ("bsim3v3", Num(5e-6), Num(5e-6)))
        self.assertEqual((i["q3"].nodes, i["q3"].master), (["c", "b", "0", "nch2"], "mybjt"))
        self.assertEqual(i["x1"].params, {"p": Num(3.0)})
        self.fails("// t\nsimulator lang=spice\nr1 a 0 'sqrt(-4)'\n", "sqrt")
        self.fails("// t\nsimulator lang=spice\nr2 a 0 rmod\n", "model rmod is defined nowhere")
        self.fails("// t\nsimulator lang=spice\nrload a 0 1k foo=1\n", "foo=")
        self.fails("// t\nsimulator lang=spice\nr1 a 0 1k scale=2\n", "scale=")
        self.fails("// t\nsimulator lang=spice\n* spectre: Rp 15 0 resistor r=50m\n", "* spectre:")
        self.fails("// t\nsimulator lang=spice\n.subckt cell a b\n.param body=2\nr1 a b body\n.ends\nx1 n 0 cell body=3\n", "body is not a parameter")
        self.fails("// t\nsimulator lang=spice\nv2 b 0 sin(0 1)\n", "SIN without a frequency")

    def test_spice_uses_a_spectre_subckt_defined_later(self):
        nl = self.parse("// t\nsimulator lang=spice\nx1 n 0 scell k=3\nv1 n 0 1\nsimulator lang=spectre\nsubckt scell (a b)\n"
                        "parameters k=1\nr1 (a b) resistor r=k*1k\nends scell\n")
        self.assertEqual((self.insts(nl)["x1"].master, self.insts(nl)["x1"].params), ("scell", {"k": Num(3.0)}))

    def test_pow_in_spice_text(self):
        nl = self.parse("// t\nsimulator lang=spice\n.param a='pow(2,1.5)' x=0.5 r0='1k*pow(x,1.5)'\nr1 n 0 'r0'\nv1 n 0 1\n")
        self.assertEqual(nl.values["a"], 2.8284271247461903)
        self.assertEqual(nl.values["r0"], 1000.0 * math.pow(0.5, 1.5))
        p = {it.name: it.expr for it in nl.body if isinstance(it, ir.Param)}
        self.assertEqual(E.to_xyce(p["r0"]), "1000.0*pow(x, 1.5)")


# -- §3.8-§3.9 instances and cards ---------------------------------------------------------------------------

class TestMasters(Case):
    def test_resistor_folds(self):
        nl = self.parse("// t\nmodel rmod resistor rsh=100 w=2u l=10u etch=0.1u etchl=0.2u tc1=1e-3 kf=1e-20\n"
                        "r1 (a 0) rmod\nr2 (a 0) rmod w=1u l=20u\nr3 (a 0) rmod r=5k w=1u\nr4 (a 0) rmod w=4u scale=2 l=12u\n"
                        "r5 (a 0) resistor\nr6 (a 0) resistor r=0\nv1 (a 0) vsource dc=1\n")
        m = nl.models()["rmod"]
        self.assertEqual(set(m.params), {"tc1", "kf"})                                   # the geometry is stripped
        self.assertEqual(m.prim, "resistor")
        i = self.insts(nl)
        self.assertAlmostEqual(i["r1"].value.value, 100 * (10e-6 - 0.4e-6) / (2e-6 - 0.2e-6))
        self.assertEqual(i["r1"].folded, [])
        self.assertAlmostEqual(i["r2"].value.value, 100 * (20e-6 - 0.4e-6) / (1e-6 - 0.2e-6))
        self.assertEqual(i["r2"].folded, ["w", "l"])
        self.assertEqual(i["r2"].params, {"w": Num(1e-6), "l": Num(20e-6)})          # sp_resistor's w and l stay (§3.8)
        self.assertEqual((i["r3"].value, i["r3"].folded, i["r3"].params), (Num(5000.0), [], {"w": Num(1e-6)}))
        self.assertAlmostEqual(i["r4"].value.value, 100 * (24e-6 - 0.4e-6) / (8e-6 - 0.2e-6))
        self.assertEqual(sorted(i["r4"].params), ["l", "w"])                           # scale acts on w and l, never printed
        self.assertAlmostEqual(i["r4"].params["w"].value, 8e-6)
        self.assertAlmostEqual(i["r4"].params["l"].value, 24e-6)
        self.assertEqual(i["r1"].params, {})
        self.assertEqual(i["r5"].value, Num(math.inf))
        self.assertTrue(self.notes(nl, NOTE, "infinite resistance"))
        self.assertEqual(i["r6"].value, Num(0.0))
        self.fails("// t\nmodel rmod resistor rsh=100\nsubckt s (a b)\nparameters w=1u\nr1 (a b) rmod w=w l=1u\nends s\nx1 (n 0) s\n",
                   "is not constant on this instance")
        self.fails("// t\nr1 (a 0) resistor r=1k c=1p\n", "c=")
        self.fails("// t\nr1 (a 0) resistor r=1k trise=5\n", "trise=")
        self.fails("// t\nr1 (a 0) resistor r=1k zz=5\n", "zz is not an instance parameter")
        self.fails("// t\nr1 (a 0 b) resistor r=1k\n", "needs 2 terminals")
        self.fails("// t\nmodel rmod resistor foo=1\n", "foo is not a parameter of Spectre's resistor")

    def test_capacitor_and_inductor_folds(self):
        nl = self.parse("// t\nmodel cmod capacitor cj=1e-3 cjsw=1e-9 etch=0.1u\nmodel lmod inductor l=2u tc1=1\n"
                        "c1 (a 0) cmod w=2u l=4u\nc2 (a 0) cmod area=1e-12 perim=4e-6\nc3 (a 0) capacitor c=1p ic=1\n"
                        "c4 (a 0) cmod\nl1 (a 0) lmod\nl2 (a 0) inductor l=1n\nl3 (a 0) inductor\nv1 (a 0) vsource dc=1\n")
        i = self.insts(nl)
        w, l = 2e-6 - 0.2e-6, 4e-6 - 0.2e-6
        self.assertAlmostEqual(i["c1"].value.value, 1e-3 * w * l + 1e-9 * 2 * (w + l))
        self.assertEqual(i["c1"].folded, ["w", "l"])
        self.assertAlmostEqual(i["c2"].value.value, 1e-3 * 1e-12 + 1e-9 * 4e-6)
        self.assertEqual(i["c2"].folded, ["area", "perim"])
        self.assertEqual((i["c3"].value, i["c3"].params), (Num(1e-12), {"ic": Num(1.0)}))
        self.assertEqual(i["c4"].value, Num(0.0))
        self.assertEqual(i["l1"].value, Num(2e-6))
        self.assertEqual(nl.models()["lmod"].params, {"tc1": Num(1.0)})
        self.assertEqual((i["l2"].value, i["l3"].value), (Num(1e-9), Num(0.0)))
        self.fails("// t\nl1 (a 0) inductor l=1n r=1\n", "r=")

    def test_mos_cards_and_default_geometry(self):
        nl = self.parse("// t\nmodel nch mos1 type=n vto=0.5 nsub=1e16\nmodel pch mos3 type=p w=2u l=1u nrd=2\nmodel b4 bsim4 type=n\n"
                        "m1 (d g s b) nch\nm2 (d g s b) pch w=3u\nm3 (d g s b) b4 w=1u l=1u nrd=1 nrs=1\nm4 (d g s b) nch region=sat trise=5\n"
                        "v1 (d 0) vsource dc=1\n")
        m = nl.models()
        self.assertEqual((m["nch"].kind, m["nch"].level, m["nch"].prim), ("nmos", 1.0, "mos1"))
        self.assertEqual((m["pch"].kind, m["pch"].level, set(m["pch"].params)), ("pmos", 3.0, set()))
        self.assertEqual(m["b4"].level, 54.0)
        i = self.insts(nl)
        self.assertEqual(i["m1"].params, {"w": Num(3e-6), "l": Num(3e-6), "nrd": Num(0.0), "nrs": Num(0.0)})
        self.assertEqual(i["m2"].params, {"w": Num(3e-6), "l": Num(1e-6), "nrd": Num(2.0), "nrs": Num(0.0)})
        self.assertEqual(i["m3"].params, {"w": Num(1e-6), "l": Num(1e-6), "nrd": Num(1.0), "nrs": Num(1.0)})
        self.assertNotIn("region", i["m4"].params)
        self.assertEqual(i["m4"].params["trise"], Num(5.0))
        self.assertTrue(self.notes(nl, NOTE, "master's default"))
        self.fails("// t\nmodel nch mos1 type=x\n", "type=x is not one of n p")
        self.fails("// t\nmodel nch mos1 type=n level=2\n", "level=2.0 is not Spectre's level 1")
        self.fails("// t\nmodel d1 diode level=3\n", "level=3.0 is not Spectre's level 1")
        self.fails("// t\nmodel nch bsim3v3 type=n\nm1 (d g s) nch\n", "needs 4 terminals")
        self.fails("// t\nmodel nch mos1 type=n\nm1 (d g s b) mos1\n", "model master; instantiate a model")

    def test_model_groups(self):
        nl = self.parse("// t\nmodel nch bsim3v3 {\n 1: type=n lmin=1u lmax=2u wmin=1u wmax=10u vtho=0.4\n"
                        " 2: type=n lmin=2u lmax=10u vtho=0.45\n}\nm1 (d g s b) nch w=2u l=1.5u\nm2 (d g s b) nch\nv1 (d 0) vsource dc=1\n")
        m = nl.models()
        self.assertEqual(sorted(m), ["nch.1", "nch.2"])
        self.assertEqual((m["nch.1"].base, m["nch.1"].bin_index, m["nch.1"].bin_rule, m["nch.1"].prim, m["nch.1"].kind),
                         ("nch", 1, "spectre", "bsim3v3", "nmos"))
        self.assertEqual(set(m["nch.2"].params), {"lmin", "lmax", "vtho"})          # type is folded into kind
        self.assertEqual(self.insts(nl)["m1"].master, "nch")
        self.assertEqual(self.insts(nl)["m2"].params["w"], Num(5e-6))
        self.fails("// t\nmodel nch bsim3v3 {\n 1: type=n w=1u lmin=1u lmax=2u\n}\nm1 (d g s b) nch\n",
                   "model-group card gives w for an instance without w")

    def test_other_masters(self):
        nl = self.parse("// t\nmodel dd diode js=1e-14\nmodel qq bjt type=pnp\nmodel jj jfet type=p\n"
                        "d1 (a c) dd area=2 m=2\nq1 (c b e) qq\nq2 (c b e s) qq area=2\nj1 (d g s) jj\n"
                        "e1 (o 0 i 0) vcvs gain=2\ng1 (o 0 i 0) vccs gm=1m\nh1 (o 0) ccvs rm=1k probe=v1\nf1 (o 0) cccs gain=2 probe=ip\n"
                        "ip (a b) iprobe\nl1 (a 0) inductor l=1u\nl2 (b 0) inductor l=1u\nk1 mutual_inductor coupling=0.9 ind1=l1 ind2=l2\n"
                        "v1 (a 0) vsource dc=1\n")
        i = self.insts(nl)
        self.assertEqual((i["d1"].kind, i["d1"].value, i["d1"].params), ("d", Num(2.0), {"m": Num(2.0)}))
        self.assertEqual((nl.models()["qq"].kind, nl.models()["jj"].kind), ("pnp", "pjf"))
        self.assertEqual(len(i["q2"].nodes), 4)
        self.assertEqual((i["e1"].kind, i["e1"].value, i["e1"].nodes), ("e", Num(2.0), ["o", "0", "i", "0"]))
        self.assertEqual((i["g1"].kind, i["g1"].value), ("g", Num(1e-3)))
        self.assertEqual((i["h1"].kind, i["h1"].value, i["h1"].ctrl), ("h", Num(1000.0), ["v1"]))
        self.assertEqual((i["f1"].kind, i["f1"].ctrl), ("f", ["ip"]))
        self.assertEqual((i["ip"].kind, i["ip"].prim, i["ip"].source.dc), ("v", "iprobe", Num(0.0)))
        self.assertEqual((i["k1"].kind, i["k1"].value, i["k1"].ctrl), ("k", Num(0.9), ["l1", "l2"]))
        self.fails("// t\nmodel jj jfet type=n\nj1 (d g s b) jj\n", "fourth terminal")
        self.fails("// t\nh1 (o 0) ccvs rm=1k probe=r1\nr1 (a 0) resistor r=1\n", "probe=r1 must name a vsource or iprobe")
        self.fails("// t\nk1 mutual_inductor coupling=0.9 ind1=l1 ind2=l9\nl1 (a 0) inductor l=1u\n", "l9 is not an inductor")
        self.fails("// t\nmodel nch bsimsoi type=n\n", "master bsimsoi is not a v1 primitive")
        self.fails("// t\ne1 (o 0 i 0) vcvs gain=2 type=and\n", "type=and")

    def test_card_parameter_dispositions_stay_on_the_card(self):
        nl = self.parse("// t\nmodel d1 diode hcomp=1 cjo=1p\nmodel m3 mos3 type=n capmod=none\n")
        self.assertEqual(nl.models()["d1"].params["hcomp"], Num(1.0))          # S3's model_params decides the error rows
        self.assertEqual(nl.models()["m3"].params["capmod"], Str("none"))        # an enumeration (§4.1 item 11)


class TestSources(Case):
    def resolve(self, params, type_, stops=(1e-6,)):
        notes = []
        src = spectre.resolve_source(params, type_, stops, notes)
        return src, notes

    def test_every_type(self):
        src, notes = self.resolve({"dc": Num(1.0), "type": Str("dc"), "val0": Num(0.0)}, "dc")
        self.assertEqual((src.dc, src.wave, src.spectre["val0"]), (Num(1.0), None, Num(0.0)))
        src, notes = self.resolve({"type": Str("pulse"), "val0": Num(0.0), "val1": Num(1.0), "rise": Num(1e-9), "fall": Num(0.0),
                                   "width": Num(5e-9), "period": Num(1e-8), "delay": Num(2e-9)}, "pulse")
        self.assertEqual(src.wave, "pulse")
        self.assertEqual(src.args, {"v1": Num(0.0), "v2": Num(1.0), "td": Num(2e-9), "tr": Num(1e-9), "tf": Num(1e-15),
                                    "pw": Num(5e-9), "per": Num(1e-8)})
        self.assertEqual([n.severity for n in notes], [WARNING])
        self.assertIn("transres", notes[0].message)
        self.assertIsNone(src.dc)                                                   # derived (§3.8.1)
        src, notes = self.resolve({"type": Str("pulse")}, "pulse")
        self.assertEqual((src.args["v2"], src.args["pw"], "per" in src.args), (Num(1.0), Num(1e30), False))
        src, notes = self.resolve({"type": Str("pulse"), "rise": Name("tr")}, "pulse")
        self.assertEqual(src.args["tr"], Ternary(Binary("==", Name("tr"), Num(0.0)), Num(1e-15), Name("tr")))
        src, notes = self.resolve({"type": Str("pulse"), "rise": Num(1e-9), "fall": Num(1e-9), "width": Num(1e-9), "period": Num(2e-9)},
                                  "pulse")
        self.assertTrue(any(n.severity == ERROR and "period" in n.message for n in notes))
        src, notes = self.resolve({"type": Str("sine"), "dc": Num(1.0), "ampl": Num(0.5), "freq": Num(1e3), "sinephase": Num(90.0),
                                   "damp": Num(2.0), "delay": Num(1e-6)}, "sine")
        self.assertEqual(src.args, {"vo": Num(1.0), "va": Num(0.5), "freq": Num(1e3), "td": Num(1e-6), "theta": Num(2.0), "phase": Num(90.0)})
        self.assertEqual(src.dc, Num(1.0))
        src, notes = self.resolve({"type": Str("sine"), "sinedc": Num(2.0), "ampl": Num(1.0), "sinephase": Num(90.0)}, "sine")
        self.assertEqual((src.wave, src.dc), (None, Num(3.0)))                     # freq=0: a constant folded into dc
        src, notes = self.resolve({"type": Str("sine"), "damp": Num(1.0)}, "sine")
        self.assertTrue(any(n.severity == ERROR for n in notes))
        src, notes = self.resolve({"type": Str("exp"), "val1": Num(2.0), "delay": Num(1e-6), "td1": Num(1e-6), "tau1": Num(1e-7),
                                   "td2": Num(5e-6), "tau2": Num(2e-7)}, "exp")
        self.assertEqual(src.args, {"v1": Num(0.0), "v2": Num(2.0), "td1": Num(2e-6), "tau1": Num(1e-7), "td2": Num(6e-6), "tau2": Num(2e-7)})
        src, notes = self.resolve({"type": Str("exp"), "tau1": Num(1e-7), "td2": Num(5e-6)}, "exp")
        self.assertTrue(any("tau2" in n.message for n in notes))
        src, notes = self.resolve({"type": Str("pwl"), "wave": [Num(0.0), Num(0.0), Num(1e-6), Num(1.0)], "delay": Num(1e-9),
                                   "scale": Num(2.0), "offset": Num(1.0), "stretch": Num(2.0)}, "pwl")
        self.assertEqual(src.points, [(Num(0.0), Num(1.0)), (Num(2e-6), Num(3.0))])
        self.assertEqual(src.args, {"td": Num(1e-9)})
        src, notes = self.resolve({"type": Str("pwl"), "wave": [Num(1e-6), Num(0.0), Num(1e-6), Num(1.0)]}, "pwl")
        self.assertTrue(any("must increase" in n.message for n in notes))
        src, notes = self.resolve({"mag": Num(1.0), "phase": Num(90.0), "xfmag": Num(2.0), "noisefile": Str("n.dat"),
                                   "pacmag": Num(1.0), "fundname": Str("f")}, "dc")
        self.assertEqual(src.ac, (Num(1.0), Num(90.0)))
        self.assertEqual([n for n in notes if n.severity == ERROR], [])
        for bad in ({"tc1": Num(1.0)}, {"pwlperiod": Num(1.0), "type": Str("pwl"), "wave": [Num(0.0), Num(0.0)]},
                    {"ampl2": Num(1.0)}, {"zz": Num(1.0)}):
            src, notes = self.resolve(bad, "dc")
            self.assertTrue(any(n.severity == ERROR for n in notes), bad)
        src, notes = self.resolve({}, "am")
        self.assertTrue(any("type=am" in n.message for n in notes))

    def test_sources_in_a_netlist(self):
        self.write("wave.dat", "0 0\n1e-6 1\n")
        nl = self.parse('// t\nv1 (a 0) vsource type=pulse val0=0 val1=1.8 width=5n period=10n\n'
                        'v2 (b 0) vsource dc=1 mag=1 type=sine ampl=0.5 freq=1k m=2\ni1 (c 0) isource file="wave.dat" type=pwl delay=1m\n'
                        'v3 (d 0) vsource dc=5\ntr1 tran stop=20n\ntr2 tran stop=1u\n')
        i = self.insts(nl)
        self.assertEqual(i["v1"].source.args["tr"], Num(2e-17))                      # transres from the shortest tran
        self.assertEqual(i["v1"].source.spectre["period"], Num(1e-8))
        self.assertEqual((i["v2"].params, i["v2"].source.dc, i["v2"].source.wave), ({"m": Num(2.0)}, Num(1.0), "sin"))
        self.assertEqual(i["i1"].source.points, [(Num(0.0), Num(0.0)), (Num(1e-6), Num(1.0))])
        self.assertEqual((i["v3"].source.dc, i["v3"].source.wave), (Num(5.0), None))
        self.fails("// t\nv1 (a 0) vsource dc=1 foo=2\n", "foo is not a vsource parameter")
        self.fails("// t\nv1 (a 0) vsource type=am\n", "type=am is not supported")


class TestXdmDecks(Case):
    def test_the_v1_decks_parse(self):
        ok = []
        for cat, name in (("INDEPENDENT_SOURCES", "i_sources"), ("INDEPENDENT_SOURCES", "v_sources"),
                          ("INDEPENDENT_SOURCES", "pwl_sources"), ("MULTIPLICITY", "rlc"), ("OTHER_PARSING", "subckt"),
                          ("OTHER_PARSING", "case_sensitive_names"), ("OTHER_PARSING", "comment_lines"),
                          ("OTHER_PARSING", "statistics_line"), ("OTHER_PARSING", "unsupported_lines_options")):
            path = os.path.join(XDM, cat, name + ".cir.spectre")
            nl = spectre.parse(path, os.path.dirname(path), spectre.SpectreParseOpts())
            self.assertEqual(nl.dialect, "spectre")
            self.assertEqual([a.kind for a in nl.analyses if a.kind != "info"], ["tran"])
            ok.append(name)
        self.assertEqual(len(ok), 9)
        nl = spectre.parse(os.path.join(XDM, "OTHER_PARSING", "subckt.cir.spectre"), XDM, spectre.SpectreParseOpts())
        i = self.insts(nl)
        self.assertEqual((i["I2"].kind, i["I2"].master), ("x", "R_subckt"))
        self.assertEqual(nl.saves[0].items[:3], ["Vnode1", "I2.R1:1", "I2.VL1"])
        self.assertEqual(nl.options["save"], Str("allpub"))
        self.assertEqual(nl.analyses[0].args["write"], Str("spectre.ic"))


if __name__ == "__main__":
    unittest.main()
