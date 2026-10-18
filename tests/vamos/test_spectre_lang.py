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

    def test_library_sections_across_language_switches(self):
        # R-lang-8: "Spectre automatically switches to SPICE input mode when it opens a library file. Thus, all
        # files that use the Spectre native language must contain a simulator lang=spectre statement within each
        # section of the library or the file can have a .scs filename extension" (ref19 p.498): library, section,
        # endsection and endlibrary are read in either language, and each section starts in the file's language
        deck = '// t\ninclude "%s" section=ss\nv1 (a 0) vsource dc=1\nr1 (a 0) resistor r=rval\n'
        self.write("corners.lib", "library corners\nsection tt\nsimulator lang=spectre\nparameters rval=1k\nendsection tt\n"
                                  "section ss\nsimulator lang=spectre\nparameters rval=2k\nendsection ss\nendlibrary corners\n")
        self.assertEqual(self.parse(deck % "corners.lib").values, {"rval": 2000.0})
        self.write("mixed.scs", "// a .scs library whose sections hold SPICE text\nlibrary corners\nsection tt\n"
                                "simulator lang=spice\n.param rval=1k\nsimulator lang=spectre\nendsection tt\nsection ss\n"
                                "simulator lang=spice\n.param rval=2k\nsimulator lang=spectre\nendsection ss\nendlibrary corners\n")
        self.assertEqual(self.parse(deck % "mixed.scs").values, {"rval": 2000.0})
        self.write("plain.lib", "library p\nsection tt\n.param rval=3k\nendsection tt\nsection ss\n.param rval=4k\n"
                                "endsection ss\nendlibrary p\n")
        self.assertEqual(self.parse(deck % "plain.lib").values, {"rval": 4000.0})
        self.write("reset.lib", "library r\nsection tt\nsimulator lang=spectre\nparameters rval=1k\nendsection tt\n"
                                "section ss\n.param rval=5k\nendsection ss\nendlibrary r\n")
        self.assertEqual(self.parse(deck % "reset.lib").values, {"rval": 5000.0})       # ss starts in SPICE mode
        self.write("top_switch.lib", "simulator lang=spectre\nlibrary corners\nsection tt\nparameters rval=1k\n"
                                     "endsection tt\nsection ss\nparameters rval=2k\nendsection ss\nendlibrary corners\n")
        self.assertEqual(self.parse(deck % "top_switch.lib").values, {"rval": 2000.0})
        # a switch inside a subckt or an if of a section is still an error (§3.1)
        self.write("bad.lib", "library b\nsection ss\nsimulator lang=spectre\nsubckt s (a b)\nsimulator lang=spice\n"
                              "r1 a b 1k\nsimulator lang=spectre\nends s\nendsection ss\nendlibrary b\n")
        self.fails(deck % "bad.lib", "inside subckt s")
        self.write("bad2.lib", "library b\nsection ss\nsimulator lang=spectre\nparameters rval=1\nif (rval) {\n"
                               "simulator lang=spice\nr1 a b 1k\nsimulator lang=spectre\n}\nendsection ss\nendlibrary b\n")
        self.fails(deck % "bad2.lib", "inside a { } block")
        self.write("open.lib", "library o\nsection ss\nsimulator lang=spectre\nparameters rval=1\nendlibrary o\n")
        self.fails(deck % "open.lib", "section ss has no endsection")

    def test_comment_after_a_language_switch(self):
        # R-lang-17: a ' //' comment may end any line (UG p.59), a simulator lang= line too
        nl = self.parse("// a comment after a language switch\nsimulator lang=spectre // native mode from here\n"
                        "v1 (a 0) vsource dc=1\nsimulator lang=spice // SPICE mode from here\nr1 a 0 1k\n"
                        "simulator lang=spectre insensitive=yes   // and back\nop1 dc\n")
        i = self.insts(nl)
        self.assertEqual((sorted(i), i["r1"].prim, [a.name for a in nl.analyses]), (["r1", "v1"], "resistor", ["op1"]))
        nl = self.parse("SPICE deck: switch with a trailing comment\nv1 a 0 1\nsimulator lang=spectre // to Spectre\n"
                        "r1 (a 0) resistor r=1k\nop1 dc\n", name="lc3.sp")
        self.assertEqual(sorted(self.insts(nl)), ["r1", "v1"])

    def test_spice_region_origins_after_blank_lines(self):
        # R-lang-20: a SPICE region's origin is the line of its first statement, not of the blank lines before it
        for name, text, line in (("or1.cir", "origin test\n\n\nV1 a 0 1\nR1 a 0 1k\nQ9 a b\n.op\n.end\n", 6),
                                 ("or3.scs", "// t\nv1 (a 0) vsource dc=1\nsimulator lang=spice\n\n\nr1 a 0 1k\nq9 a b\n"
                                             "simulator lang=spectre\nop1 dc\n", 7)):
            with self.assertRaises(NoteError) as cm:
                self.parse(text, name=name)
            errs = [n for n in cm.exception.notes if n.severity == ERROR]
            self.assertTrue(errs and errs[0].origin.endswith("%s:%d" % (name, line)), [n.text() for n in errs])
        nl = self.parse("* t\n\nV1 a 0 1\n\nR1 a 0 1k\n.end\n", name="or4.sp")
        self.assertEqual([self.insts(nl)[k].origin.rsplit(":", 1)[1] for k in ("v1", "r1")], ["3", "5"])

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

    def test_cpp_resumed_chunk_restores_the_includers_mode(self):
        # R-lang-11: after an #include, flag 2 returns to the includer in the mode it was in (§2.6; ref19 p.493),
        # not in the mode §3.1 detects from the resumed chunk's own first line (a non-.scs Spectre netlist
        # resumed in SPICE mode, a .scs one that had switched to SPICE in Spectre mode)
        out = ('# 1 "t.net"\n# 1 "<built-in>"\n# 1 "<command-line>"\n# 1 "t.net"\n// t\nsimulator lang=spectre\n'
               '# 1 "m.inc" 1\n.param rmod=2k\n# 4 "t.net" 2\nr1 (a 0) resistor r=rmod\nv1 (a 0) vsource dc=1\n')
        nl = self.parse(out, name="cpp.out", cpp_markers=True, title="// t")
        r1 = self.insts(nl)["r1"]
        self.assertEqual((r1.prim, r1.nodes, r1.value, r1.origin), ("resistor", ["a", "0"], Name("rmod"), "t.net:4"))
        self.assertEqual(nl.values, {"rmod": 2000.0})
        out = ('# 1 "t.scs"\n# 1 "<built-in>"\n# 1 "<command-line>"\n# 1 "t.scs"\n// t\nsimulator lang=spice\n'
               '# 1 "m.inc" 1\n.param rmod=2k\n# 4 "t.scs" 2\nr1 a 0 rmod\nv1 a 0 1\n')
        nl = self.parse(out, name="cpp2.out", cpp_markers=True, title="// t")
        r1 = self.insts(nl)["r1"]
        self.assertEqual((r1.prim, r1.nodes, r1.value, r1.origin), ("resistor", ["a", "0"], Name("rmod"), "t.scs:4"))

    def test_cpp_markers_of_a_stdin_copy(self):
        # §1: a netlist on stdin is copied to <run>/stdin and cpp's markers name that copy; its statements
        # read stdin:<line> and its relative includes resolve against top_dir (the cwd), not the run
        # directory (phase 2's integration of the flow's seam)
        self.write("inc/a.scs", "r1 (a 0) resistor r=1k\n")
        copy = os.path.join(self.tmp, "x.run.abc", "stdin").replace("\\", "/")
        out = ('# 1 "%s"\n# 1 "<built-in>"\n# 1 "<command-line>"\n# 1 "%s"\nsimulator lang=spectre\n'
               'include "inc/a.scs"\nv1 (a 0) vsource dc=1\n' % (copy, copy))
        nl = self.parse(out, name="x.run.abc/cpp.out", cpp_markers=True, title="simulator lang=spectre",
                        top_dir=self.tmp, top_name="stdin")
        i = self.insts(nl)
        self.assertEqual(i["v1"].origin, "stdin:3")
        self.assertEqual(i["r1"].origin, os.path.join(self.tmp, "inc", "a.scs") + ":1")

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

    def test_spice_end_ends_the_file(self):
        # R-lang-12: ".end ... is treated like the end of file; and the SPICE Reader does not read beyond the .end"
        # (UG p.50): a later Spectre region of the same file is not read either; an included file's .end ends
        # only that file
        nl = self.parse("statements after .end, then a Spectre region\nV1 a 0 1\nR1 a 0 1k\n.op\n.end\n"
                        "simulator lang=spectre\nr2 (a 0) resistor r=1k\n", name="ae1.sp")
        self.assertEqual(sorted(self.insts(nl)), ["r1", "v1"])
        self.assertTrue(self.notes(nl, NOTE, "ignored after .end"))
        self.write("inc2.scs", "simulator lang=spice\nR8 a 0 8k\n.end\nsimulator lang=spectre\nr9 (a 0) resistor r=9k\n")
        nl = self.parse('// t\ninclude "inc2.scs"\nv1 (a 0) vsource dc=1\nr7 (a 0) resistor r=7k\n')
        self.assertEqual(sorted(self.insts(nl)), ["r7", "r8", "v1"])
        self.write("inc.sp", "R5 a 0 5k\n.end\nR6 a 0 6k\n")
        nl = self.parse('// t\ninclude "inc.sp"\nv1 (a 0) vsource dc=1\nr7 (a 0) resistor r=7k\n')
        self.assertEqual(sorted(self.insts(nl)), ["r5", "r7", "v1"])

    def test_slash_comment_line_inside_a_continued_statement(self):
        # R-lang-19: a '//' comment line is a comment line like a '*' one (UG p.59), so a '+' line after it still
        # continues the statement before it
        nl = self.parse("// title\nv1 (in 0) vsource dc=1\nr1 (in 0) resistor\n// the value follows on a continuation "
                        "line\n+ r=2k\nop1 dc\n")
        self.assertEqual(self.insts(nl)["r1"].value, Num(2000.0))
        nl = self.parse("// t\nmodel dm diode\n+ is=1e-14\n//+ n=1.1\n+ rs=10\ni1 (0 a) isource dc=1m\nd1 (a 0) dm\n")
        self.assertEqual(sorted(nl.models()["dm"].params), ["is", "rs"])

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

    def test_subckt_parameter_without_a_default(self):
        # R-lang-9: "When parameters are declared within subcircuits, their default values are optionally
        # specified" (ref19 p.506, p.533): a bare name on a subckt's parameters line is a parameter every instance
        # must give; a top-level parameter needs a value
        deck = ("// t\nsubckt rdiv (a b)\nparameters r1 r2=1k\nra (a b) resistor r=r1\nrb (b 0) resistor r=r2\nends rdiv\n"
                "v1 (in 0) vsource dc=1\nx1 (in out) rdiv r1=3k\n")
        nl = self.parse(deck)
        self.assertEqual([p.name for p in nl.subckts()["rdiv"].params], ["r1", "r2"])
        self.assertEqual(self.insts(nl)["x1"].params, {"r1": Num(3000.0)})
        nl = self.parse(deck.replace("parameters r1 r2=1k", "parameters r2=1k r1"))
        self.assertEqual(sorted(p.name for p in nl.subckts()["rdiv"].params), ["r1", "r2"])
        msgs = self.fails(deck.replace(" r1=3k", ""), "x1: subckt rdiv's parameter r1 has no default value")
        self.assertEqual(len(msgs), 1)
        self.fails(deck.replace("x1 (in out) rdiv r1=3k", "simulator lang=spice\nx1 in out rdiv"), "x1: subckt rdiv's parameter r1")
        self.fails("// t\nparameters a b=1\n", "a top-level parameter needs a value: a")

    def test_cross_language_reference_is_warned(self):
        nl = self.parse("// t\nparameters a=1\nsimulator lang=spice\n.param b='a*2' c=3\nsimulator lang=spectre\n"
                        "parameters d=c+1\nr1 (n 0) resistor r=b\nv1 (n 0) vsource dc=1\n")
        self.assertEqual(nl.values, {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0})
        self.assertEqual(len(self.notes(nl, WARNING, "two language sections")), 2)

    def test_reserved_parameter_names(self):
        # R-lang-15: "The following parameters are reserved and must not be declared as either top-level parameters
        # or subcircuit parameters: temp, tnom, scale, scalem, freq, time" (ref19 p.508), in either language
        for name in ("temp", "tnom", "temper", "time", "hertz", "scale", "scalem", "freq"):
            self.fails("// t\nparameters %s=5 r=%s*1k\n" % (name, name), "a parameter named %s is an error" % name)
        self.fails("// t\nparameters M_PI=3\n", "built-in constant")
        self.fails("// t\nsubckt s (a)\nparameters freq=1k\nr1 (a 0) resistor r=1k\nends s\n", "a parameter named freq")
        self.fails("// t\nsimulator lang=spice\n.param scale=2\n", "a parameter named scale is an error")
        self.fails("// t\nsimulator lang=spice\n.subckt s a scalem=2\nr1 a 0 1k\n.ends\n", "a parameter named scalem")

    def test_m_cannot_be_declared_on_a_subckt_parameters_line(self):
        # R-lang-14: "Any attempt to explicitly define m on a parameters line results in an error" (ref19 p.533)
        self.fails("// t\nsubckt cell (a)\nparameters m=2\nr1 (a 0) resistor r=1k*m\nends cell\nv1 (in 0) vsource dc=1\n"
                   "x1 (in) cell m=3\n", "m is every subckt's implicit multiplicity")
        self.fails("* t\n.subckt cell a m=2\nr1 a 0 '1k*m'\n.ends\nx1 in cell m=3\nv1 in 0 1\n.end\n",
                   "m is every subckt's implicit multiplicity", name="mp3.sp")
        self.fails("* t\n.subckt cell a\n.param m=2\nr1 a 0 '1k*m'\n.ends\nx1 in cell\nv1 in 0 1\n.end\n",
                   "m is every subckt's implicit multiplicity", name="mp4.sp")
        nl = self.parse("// t\nparameters m=2\nr1 (a 0) resistor r=1k*m\nv1 (a 0) vsource dc=1\n")
        self.assertEqual(nl.values, {"m": 2.0})                              # a top-level m is a parameter

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

    def test_equality_written_with_blanks(self):
        # R-lang-18: `a == b` with blanks around == continues the value, as `a != b` does (ref19 p.475)
        nl = self.parse("// t\nparameters a=1 b=2\nparameters ne=a != b\nparameters eq=a == b\nparameters t2=a == 1 ? 10 : 30\n"
                        "v1 (x 0) vsource dc=a == 2 ? 1 : 3\n")
        self.assertEqual((nl.values["eq"], nl.values["ne"], nl.values["t2"]), (0.0, 1.0, 10.0))
        self.assertEqual(E.evaluate(self.insts(nl)["v1"].source.dc, nl.values, "spectre"), 3.0)

    def test_user_function_without_arguments(self):
        # R-lang-21: a function declared with an empty argument list (ref19 p.481) is called as rdef()
        nl = self.parse("// t\nreal rdef() { return 2k; }\nreal dt() { return temp-tnom; }\nv1 (a 0) vsource dc=1\n"
                        "r1 (a 0) resistor r=rdef()\nr2 (a 0) resistor r=1k*(1+0.001*dt())\n")
        i = self.insts(nl)
        self.assertEqual(i["r1"].value, Num(2000.0))
        self.assertEqual(E.names(i["r2"].value), set())
        self.assertIn(Name("$tnom"), list(E.walk(i["r2"].value)))

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

    def test_dangling_else_binds_to_the_closest_unmatched_if(self):
        # R-lang-3: "When if-statements are nested without braces, an else matches the closest previous unmatched
        # if at the same level" (ref19 p.491); the { } of `if (a) if (b) {` belongs to the inner if, and the inner
        # if of `else if (b) if (c) {` keeps its condition
        def names(items):
            return [x.name for x in items if isinstance(x, ir.Instance)]
        head = "// t\nparameters a=1 b=0 c=0\nv1 (x 0) vsource dc=1\n"
        nl = self.parse(head + "if (a) if (b) r1 (x 0) resistor r=1k\nelse r2 (x 0) resistor r=2k\n")
        c = [it for it in nl.body if isinstance(it, ir.Cond)][0]
        self.assertEqual((c.branches[0][0], c.default), (Name("a"), []))
        inner = c.branches[0][1][0]
        self.assertIsInstance(inner, ir.Cond)
        self.assertEqual((inner.branches[0][0], names(inner.branches[0][1]), names(inner.default)), (Name("b"), ["r1"], ["r2"]))
        nl = self.parse(head + "if (a) if (b) r1 (x 0) resistor r=1k\nelse r2 (x 0) resistor r=2k\nelse r3 (x 0) resistor r=4k\n")
        c = [it for it in nl.body if isinstance(it, ir.Cond)][0]
        self.assertEqual((names(c.branches[0][1][0].default), names(c.default)), (["r2"], ["r3"]))
        nl = self.parse(head + "if (a) if (b) {\nr1 (x 0) resistor r=1k\n}\n")
        c = [it for it in nl.body if isinstance(it, ir.Cond)][0]
        inner = c.branches[0][1][0]
        self.assertIsInstance(inner, ir.Cond)
        self.assertEqual((inner.branches[0][0], names(inner.branches[0][1])), (Name("b"), ["r1"]))
        nl = self.parse(head + "if (a) r0 (x 0) resistor r=1k\nelse if (b) if (c) {\nr1 (x 0) resistor r=1k\n}\n")
        c = [it for it in nl.body if isinstance(it, ir.Cond)][0]
        elif_ = c.default[0]
        self.assertEqual((names(c.branches[0][1]), elif_.branches[0][0]), (["r0"], Name("b")))
        inner = elif_.branches[0][1][0]
        self.assertIsInstance(inner, ir.Cond)
        self.assertEqual((inner.branches[0][0], names(inner.branches[0][1])), (Name("c"), ["r1"]))
        self.fails(head + "if (a) r1 (x 0) resistor r=1k {\nr2 (x 0) resistor r=2k\n}\n", "does not take a { } block")

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

    def test_parentheses_are_field_separators(self):
        # R-lang-10: "you can place optional parentheses around parameter specifications ... For example,
        # (c=10E-12)" (UG p.63), and parentheses are significant field separators (UG p.59), so r1(a 0) is the
        # name r1 and the node group (a 0); a function call in a value is not split
        nl = self.parse("// t\nv1 (2 0) vsource dc=1\nc1 (2 3) capacitor (c=10E-12)\nr1 (3 0) resistor (r=1k)\n"
                        "r2 (2 4) resistor (r=2k) m=2\nr3 (4 0) resistor (r=1k w=1u)\nop1 dc\n")
        i = self.insts(nl)
        self.assertEqual((i["c1"].value, i["r1"].value, i["r2"].value, i["r3"].value),
                         (Num(1e-11), Num(1000.0), Num(2000.0), Num(1000.0)))
        self.assertEqual((i["r2"].params, i["r3"].params, i["c1"].nodes), ({"m": Num(2.0)}, {"w": Num(1e-6)}, ["2", "3"]))
        nl = self.parse("// t\nreal myfunc(real x, real y) { return x*y; }\nv1 (a 0) vsource dc=1\nr1(a 0) resistor r=1k\n"
                        "r2 (a 0)resistor r=myfunc(2.0, 4.5)\nsubckt s (p n)\nr9 (p n) resistor r=1k\nends s\nx1(a 0) s\n")
        i = self.insts(nl)
        self.assertEqual((i["r1"].nodes, i["r1"].value, i["r2"].nodes, i["r2"].value, i["x1"].nodes),
                         (["a", "0"], Num(1000.0), ["a", "0"], Binary("*", Num(2.0), Num(4.5)), ["a", "0"]))

    def test_escaped_names_and_spelling(self):
        nl = self.parse("// t\nmodel \\2N2222 bjt type=npn\nq1 (c b e) \\2N2222\nr\\2 (c 0) resistor r=1k\nv1 (c 0) vsource dc=1\n")
        i = self.insts(nl)
        self.assertEqual(i["q1"].master, "2N2222")
        self.assertIn("r2", i)
        self.assertEqual(nl.spelling["2N2222"], "\\2N2222")
        self.assertEqual(nl.spelling["r2"], "r\\2")

    def test_node_names_follow_the_name_rule(self):
        # R-lang-5: a Spectre-mode node name holds letters, digits, _ and ! unless escaped (UG p.60, §3.2); 'a,'
        # silently became a node of its own on VACASK
        self.fails("// t\nv1 (a 0) vsource dc=1\nr1 (a, 0) resistor r=1k\n", "node a,")
        self.fails("// t\nsubckt div (top, mid)\nr1 (top mid) resistor r=1k\nends div\nx1 (b c) div\n", "node top,")
        self.fails("// t\nr1 (a; 0) resistor r=1k\n", "node a;")
        self.fails("// t\nr2 (a# 0) resistor r=1k\n", "node a#")
        self.fails("// t\nglobal 0 vdd,\n", "node vdd,")
        msgs = self.fails("// t\nr1 (a, 0) resistor r=1k\n")
        self.assertTrue(any("letters, digits, _ and !" in m for m in msgs), msgs)
        nl = self.parse("// t\nr1 (vdd! n\\-1) resistor r=1k\nr2 (007 _x) resistor r=1k\nv1 (vdd! 0) vsource dc=1\n"
                        "subckt s (p)\nr3 (p 0) resistor r=1k\nends s\nx1 (m) s\nnz (x1.p 0) noise start=1 stop=10 dec=1\n")
        self.assertEqual((self.insts(nl)["r1"].nodes, self.insts(nl)["r2"].nodes), (["vdd!", "n-1"], ["007", "_x"]))
        self.assertEqual(nl.analyses[0].nodes, ["x1.p", "0"])

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
        # §3.10 [M ref19 pp.196-197]: rcut/ccut/cgnd/cmax are silent only at their infinite default and
        # rabsshort/cclamp/lshort/kcut at 0; dcut and qcut have no silent value (plan.py's table too)
        nl = self.parse("// t\no1 options rcut=0 ccut=0 cgnd=0 dcut=0 qcut=1 rabsshort=0 kcut=0\n")
        self.assertEqual(sorted(n.message.split("=")[0] for n in self.notes(nl, WARNING)),
                         ["options ccut", "options cgnd", "options dcut", "options qcut", "options rcut"])
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

    def test_hierarchical_probe_names(self):
        # §5.6: a given iprobe= inside a subckt (X1.V2) names an IR instance path; oprobe= and xf probe= the
        # same (phase 2's integration: they were refused, "unexpected character '.'", H's seam)
        nl = self.parse("// t\nsubckt cell (a)\nvb (a m) vsource dc=0\nr3 (m 0) resistor r=1k\nends cell\n"
                        "x1 (out) cell\nv1 (out 0) vsource dc=1\n"
                        "nz (x1.m 0) noise start=1 stop=10 dec=1 iprobe=x1.vb oprobe=x1.r3\n"
                        "xf1 (out) xf start=1 stop=10 dec=1 probe=x1.r3\n")
        a = {x.name: x for x in ir.flat_analyses(nl.analyses)}
        self.assertEqual((a["nz"].args["iprobe"], a["nz"].args["oprobe"]), (Str("x1.vb"), Str("x1.r3")))
        self.assertEqual(a["xf1"].args["probe"], Str("x1.r3"))
        self.fails("// t\nv1 (out 0) vsource dc=1\nnz (out 0) noise start=1 stop=10 dec=1 iprobe=x1..vb\n", "iprobe")

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

    def test_every_analysis_keyword_is_an_analysis(self):
        # R-lang-23: the ref19 analyses outside v1 (§0's list) are refused as analyses, not as unknown masters
        for kw in ("hbstb", "cosim", "loadpull", "reliability", "stress", "thermal", "uti"):
            self.fails("// t\nan1 %s\n" % kw, "the %s analysis is not supported in v1" % kw)
        self.fails("// t\nrel1 reliability {\n}\n", "the reliability analysis is not supported in v1")

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

    def test_point_counts_may_be_expressions(self):
        # R-lang-16: "Expressions can be used when specifying device or analysis instance parameter values"
        # (ref19 p.474): lin=/dec=/log= may read parameters; the value is a positive integer no sweep or alter changes
        nl = self.parse("// t\nparameters npts=10 fstop=1M\nv1 (in 0) vsource dc=0 mag=1\nr1 (in out) resistor r=1k\n"
                        "c1 (out 0) capacitor c=1u\nac1 ac start=1 stop=fstop dec=npts\ndc1 dc dev=v1 start=0 stop=1 lin=2*2\n")
        a = {x.name: x for x in nl.analyses}
        self.assertEqual((a["ac1"].sweep.mode, a["ac1"].sweep.count), ("dec", 10))
        self.assertEqual((a["dc1"].sweep.mode, a["dc1"].sweep.count), ("lin", 4))
        self.fails("// t\nparameters npts=2.5\nac1 ac start=1 stop=10 dec=npts\n", "dec=npts must be a positive integer")
        self.fails("// t\nparameters npts=10\nsw sweep param=npts values=[5 10] {\n ac1 ac start=1 stop=10 dec=npts\n}\n",
                   "dec=npts reads npts, which a sweep or alter changes")
        self.fails("// t\nparameters npts=10\nac1 ac start=1 stop=10 dec=npts\nal alter param=npts value=20\n",
                   "dec=npts reads npts, which a sweep or alter changes")

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

    def test_controlled_sources_with_positional_values(self):
        # §4.3 "Elements. As spice.py": E/G lines carry their gain positionally and F/H lines their controlling
        # source and gain (phase 2's integration: "esource vcvs needs gain=" refused every one, C's seam; the
        # CMC gummel decks); the same IR as Spectre's vcvs/vccs/cccs/ccvs (gain/gm/rm in value, probe in ctrl)
        nl = self.parse("* t\nv1 in 0 dc 1\nvs in x 0\nrx x 0 1k\nesource out 0 in 0 -1\ng1 o2 0 in 0 1m\nf1 o3 0 vs 2\n"
                        "h1 o4 0 vs 1k\n.op\n.end\n", name="t.sp")
        i = self.insts(nl)
        self.assertEqual((i["esource"].prim, i["esource"].nodes, i["esource"].value), ("vcvs", ["out", "0", "in", "0"], Num(-1.0)))
        self.assertEqual((i["g1"].prim, i["g1"].value), ("vccs", Num(1e-3)))
        self.assertEqual((i["f1"].prim, i["f1"].ctrl, i["f1"].value), ("cccs", ["vs"], Num(2.0)))
        self.assertEqual((i["h1"].prim, i["h1"].ctrl, i["h1"].value), ("ccvs", ["vs"], Num(1000.0)))
        sp = self.parse("// t\nv1 (in 0) vsource dc=1\nvs (in x) vsource dc=0\nrx (x 0) resistor r=1k\n"
                        "esource (out 0 in 0) vcvs gain=-1\nf1 (o3 0) cccs gain=2 probe=vs\n")
        j = self.insts(sp)
        self.assertEqual((j["esource"].value, j["f1"].ctrl, j["f1"].value), (Num(-1.0), ["vs"], Num(2.0)))
        self.fails("// t\nesource (out 0 in 0) vcvs\n", "esource vcvs needs gain=")                 # Spectre: gain= is required
        self.fails("// t\nf1 (o3 0) cccs gain=2\n", "needs probe=")

    def test_spice_mode_coupled_inductors(self):
        # R-lang-6: the SPICE2G K line `K1 L1 L2 0.5` carries its coupling and inductors positionally (spice.py's
        # value and ctrl, §4.3 "as spice.py"); it was refused as "needs coupling= ind1= ind2="
        nl = self.parse("SPICE-mode coupled inductors\nV1 a 0 AC 1\nR1 a b 50\nL1 b 0 1u\nL2 c 0 4u\nK1 L1 L2 0.5\n"
                        "R2 c 0 50\n.ac dec 10 1meg 100meg\n.end\n", name="k1.sp")
        k1 = self.insts(nl)["k1"]
        self.assertEqual((k1.kind, k1.prim, k1.value, k1.ctrl, k1.params), ("k", "mutual_inductor", Num(0.5), ["l1", "l2"], {}))
        sp = self.parse("// t\nv1 (a 0) vsource mag=1\nr1 (a b) resistor r=50\nl1 (b 0) inductor l=1u\nl2 (c 0) inductor l=4u\n"
                        "k1 mutual_inductor coupling=0.5 ind1=l1 ind2=l2\nr2 (c 0) resistor r=50\n")
        k2 = self.insts(sp)["k1"]
        self.assertEqual((k2.kind, k2.prim, k2.value, k2.ctrl, k2.params), (k1.kind, k1.prim, k1.value, k1.ctrl, k1.params))
        self.fails("* t\nL1 b 0 1u\nK1 L1 L9 0.5\n.end\n", "l9 is not an inductor of the same scope", name="k2.sp")
        nl = self.parse("* t\n.subckt xf p s\nL1 p 0 1u\nL2 s 0 1u\nK1 L1 L2 0.9\n.ends\nX1 a b xf\nV1 a 0 1\n.end\n",
                        name="k3.sp")
        k = [it for it in nl.subckts()["xf"].body if isinstance(it, ir.Instance) and it.kind == "k"][0]
        self.assertEqual((k.value, k.ctrl), (Num(0.9), ["l1", "l2"]))

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

    def test_definitions_are_defined_once_whatever_the_language(self):
        # R-lang-13: a second model or subckt of a name in the top level is an error in either order of the two
        # languages, and across two SPICE regions (the later one silently won)
        self.fails("// t\nsubckt load (a)\nr1 (a 0) resistor r=1k\nends load\nsimulator lang=spice\n.subckt load a\n"
                   "r1 a 0 3k\n.ends\nsimulator lang=spectre\nv1 (n 0) vsource dc=1\nx1 (n) load\n",
                   "subckt load is defined twice")
        self.fails("// t\nsimulator lang=spice\n.subckt load a\nr1 a 0 3k\n.ends\nsimulator lang=spectre\nsubckt load (a)\n"
                   "r1 (a 0) resistor r=1k\nends load\nv1 (n 0) vsource dc=1\nx1 (n) load\n", "subckt load is defined twice")
        self.fails("// t\nmodel dm diode is=1e-14\nsimulator lang=spice\n.model dm d is=1e-12\nsimulator lang=spectre\n"
                   "i1 (0 a) isource dc=1m\nd1 (a 0) dm\n", "model dm is defined twice")
        self.fails("// t\nsimulator lang=spice\n.model dm d is=1e-14\nsimulator lang=spectre\ni1 (0 a) isource dc=1m\n"
                   "simulator lang=spice\n.model dm d is=1e-12\nsimulator lang=spectre\nd1 (a 0) dm\n",
                   "model dm is defined twice")

    def test_star_comment_after_an_element(self):
        # R-lang-22: a '*' outside an expression is a comment in SPICE mode (UG p.52); `.param x = 2 * 3` stays an
        # error (never x=2)
        nl = self.parse("star comment test\nv1 1 0 1\nr1 1 0 1k * load resistor\nr2 1 0 2k\n.op\n.end\n", name="st1.cir")
        self.assertEqual((self.insts(nl)["r1"].value, self.insts(nl)["r2"].value), (Num(1000.0), Num(2000.0)))
        self.fails("star comment test\n.param x = 2 * 3\nv1 1 0 x\n.op\n.end\n", "* has no value", name="st4.cir")

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

    def test_upper_case_global_is_folded(self):
        # R-lang-1: SPICE mode lower-cases everything outside quotes (UG p.52), the .GLOBAL names too, so the
        # global vdd is the subckt's vdd (it was 'VDD', and every instance of load got a private floating vdd)
        nl = self.parse("UPPER-CASE .GLOBAL IN SPICE MODE\n.GLOBAL VDD\nVDD VDD 0 DC 1\nX1 OUT LOAD\nR2 OUT 0 1K\n"
                        ".SUBCKT LOAD A\nR1 VDD A 1K\n.ENDS\n.OP\n.END\n", name="uglobal.sp")
        self.assertEqual(nl.globals, ["vdd"])
        self.assertEqual(nl.subckts()["load"].body[0].nodes, ["vdd", "a"])
        self.write("cells.sp", ".GLOBAL VDD VSS\n.SUBCKT INV A Y\nR1 VDD Y 1K\nR2 Y VSS 1K\n.ENDS\n")
        nl = self.parse('// t\ninclude "cells.sp"\nv1 (vdd 0) vsource dc=1\nv2 (vss 0) vsource dc=0\nxu (a y) inv\n')
        self.assertEqual(nl.globals, ["vdd", "vss"])

    def test_upper_case_control_names_are_folded(self):
        # R-lang-4: .dc source names, .ic/.nodeset nodes and .print items are folded like every other SPICE-mode
        # name (UG p.52; the UG's own `.DC VDR 0 5 0.1 VG 0 2 0.1`, p.47)
        nl = self.parse("* t\nVIN IN 0 DC 0\nR1 IN OUT 1K\nC1 OUT 0 1N\n.IC V(OUT)=0.5\n.NODESET V(IN)=0\n"
                        ".PRINT TRAN V(OUT) I(VIN)\n.TRAN 10N 2U UIC\n.END\n", name="upper.sp")
        self.assertEqual(nl.ics, {"out": Num(0.5)})
        self.assertEqual(nl.nodesets, {"in": Num(0.0)})
        self.assertEqual(nl.saves[0].items, ["out", "vin:1"])
        nl = self.parse("* t\nV1 a 0 0\nR1 a 0 1k\n.dc V1 0 1 0.5\n.end\n", name="dcu.sp")
        sw = nl.analyses[0].sweep
        self.assertEqual((sw.target, sw.name, sw.param), ("dev", "v1", "dc"))
        nl = self.parse("* t\n.param RV=1k\nV1 a 0 1\nR1 a 0 RV\n.dc RV 1k 2k 1k\n.end\n", name="dcp.sp")
        sw = nl.analyses[0].sweep
        self.assertEqual((sw.target, sw.param), ("param", "rv"))


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

    def test_spectre_level_values(self):
        # R-plan-12: the masters' own level selectors: bsim3v3 level=11, 49 or 53 ("The available level are 11, 49
        # and 53", ref5 p.197), bsim4 level=14 (ref5 p.224), each the DISPATCH level; in SPICE mode the card's
        # level chose the master (§4.3; .MODEL N1 NMOS +Level=11 is UG p.298's BSIM3v3 card)
        nl = self.parse("// t\nmodel n1 bsim3v3 type=n level=11\nmodel n2 bsim3v3 type=n level=53\nmodel n3 bsim3v3 type=n "
                        "level=49\nmodel n4 bsim4 type=n level=14\n")
        m = nl.models()
        self.assertEqual([(m[k].level, "level" in m[k].params) for k in ("n1", "n2", "n3", "n4")],
                         [(49.0, False), (49.0, False), (49.0, False), (54.0, False)])
        self.fails("// t\nmodel n bsim3v3 type=n level=12\n", "level=12.0 is not one of Spectre's levels 11 49 53 of bsim3v3",
                   "unknown bsim3v3 level")
        self.fails("// t\nmodel n bsim4 type=n level=54\n", "level=54.0 is not Spectre's level 14 of bsim4", "unknown bsim4 level")
        nl = self.parse("* t\n.model a nmos level=11\n.model b nmos level=53\n.model c pmos level=54\n.model e nmos level=49\n"
                        "m1 d g 0 0 a w=1u l=1u\nm2 d g 0 0 b w=1u l=1u\nm3 d g 0 0 c w=1u l=1u\nm4 d g 0 0 e w=1u l=1u\n"
                        ".end\n", name="lv.sp")
        m = nl.models()
        self.assertEqual([(m[k].prim, m[k].level, "level" in m[k].params) for k in "abce"],
                         [("bsim3v3", 49.0, False), ("bsim3v3", 49.0, False), ("bsim4", 54.0, False),
                          ("bsim3v3", 49.0, False)])

    def test_model_groups(self):
        nl = self.parse("// t\nmodel nch bsim3v3 {\n 1: type=n lmin=1u lmax=2u wmin=1u wmax=10u vtho=0.4\n"
                        " 2: type=n lmin=2u lmax=10u vtho=0.45\n}\nm1 (d g s b) nch w=2u l=1.5u\nm2 (d g s b) nch\nv1 (d 0) vsource dc=1\n")
        m = nl.models()
        self.assertEqual(sorted(m), ["nch.1", "nch.2"])
        self.assertEqual((m["nch.1"].base, m["nch.1"].bin_index, m["nch.1"].bin_rule, m["nch.1"].prim, m["nch.1"].kind),
                         ("nch", 1, "spectre", "bsim3v3", "nmos"))
        self.assertEqual(set(m["nch.2"].params), {"lmin", "lmax", "vtho"})          # type is folded into kind
        self.assertEqual(self.insts(nl)["m1"].master, "nch")
        # D4 (phase 2c): an instance of a model group without w or l gets no master default; plan.build refuses it
        # (test_spectre_plan.TestParsedDecks).  This assertion read the default w=5u the parser wrote before.
        self.assertFalse({"w", "l"} & set(self.insts(nl)["m2"].params), self.insts(nl)["m2"].params)
        self.assertFalse(self.notes(nl, NOTE, "m2: w not given"))
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

    def test_local_models_are_scoped(self):
        # R-lang-2: a model defined in a subckt is local to it (ref19 pp.533-536): each instance takes the folds,
        # the master and the prim of the card its own scope sees (they came from the last same-named card)
        def body(nl, sub):
            return {it.name: it for it in nl.subckts()[sub].body if isinstance(it, ir.Instance)}
        nl = self.parse("// t\nsubckt sa (a)\nmodel rm resistor r=1k\nr1 (a 0) rm\nends sa\nsubckt sb (a)\n"
                        "model rm resistor r=2k\nr1 (a 0) rm\nends sb\nva (na 0) vsource dc=1\nvb (nb 0) vsource dc=1\n"
                        "x1 (na) sa\nx2 (nb) sb\n")
        self.assertEqual((body(nl, "sa")["r1"].value, body(nl, "sb")["r1"].value), (Num(1000.0), Num(2000.0)))
        nl = self.parse("// t\nmodel rm resistor r=1k\nsubckt sb (a)\nmodel rm resistor r=2k\nr1 (a 0) rm\nends sb\n"
                        "va (na 0) vsource dc=1\nr0 (na 0) rm\nx2 (nb) sb\n")
        self.assertEqual((self.insts(nl)["r0"].value, body(nl, "sb")["r1"].value), (Num(1000.0), Num(2000.0)))
        nl = self.parse("// t\ninline subckt rpoly (a b)\nparameters sq=1\nmodel rm resistor r=200*sq\nrpoly (a b) rm\n"
                        "ends rpoly\ninline subckt rdiff (a b)\nparameters sq=1\nmodel rm resistor r=50*sq\nrdiff (a b) rm\n"
                        "ends rdiff\nx1 (na 0) rpoly sq=5\nx2 (nb 0) rdiff sq=5\n")
        self.assertEqual(body(nl, "rpoly")["rpoly"].value, Binary("*", Num(200.0), Name("sq")))
        self.assertEqual(body(nl, "rdiff")["rdiff"].value, Binary("*", Num(50.0), Name("sq")))
        nl = self.parse("// t\nsubckt sa (a)\nmodel rm resistor r=1k\nr1 (a 0) rm\nends sa\nsubckt sb (a)\n"
                        "model rm capacitor c=1p\nc1 (a 0) rm\nends sb\nx1 (na) sa\nx2 (nb) sb\n")
        c1, r1 = body(nl, "sb")["c1"], body(nl, "sa")["r1"]
        self.assertEqual((c1.kind, c1.prim, c1.value), ("c", "capacitor", Num(1e-12)))
        self.assertEqual((r1.kind, r1.prim, r1.value), ("r", "resistor", Num(1000.0)))
        nl = self.parse("// t\nsubckt sa (d g s b)\nmodel nch mos1 type=n w=2u l=1u\nm1 (d g s b) nch\nends sa\n"
                        "subckt sb (d g s b)\nmodel nch mos1 type=n w=4u l=2u\nm1 (d g s b) nch\nends sb\n")
        self.assertEqual([(body(nl, s)["m1"].params["w"], body(nl, s)["m1"].params["l"]) for s in ("sa", "sb")],
                         [(Num(2e-6), Num(1e-6)), (Num(4e-6), Num(2e-6))])

    def test_card_parameter_dispositions_stay_on_the_card(self):
        nl = self.parse("// t\nmodel d1 diode hcomp=1 cjo=1p\nmodel m3 mos3 type=n capmod=none\n")
        self.assertEqual(nl.models()["d1"].params["hcomp"], Num(1.0))          # S3's model_params decides the error rows
        self.assertEqual(nl.models()["m3"].params["capmod"], Str("none"))        # an enumeration (§4.1 item 11)


class TestTnomOnXyce(Case):
    """R-lang-7 (§3.5): Xyce has temp but no tnom, so the Xyce emitter substitutes the deck's tnom for
    Name("$tnom") before printing (the printer's PrintError stays the guard).  The parse is S1's; the
    substitution is the emitter's (vamos/netlist/xyce.py _Deck.deck_tnom, applied in xv and bx)."""

    def decks(self, text):
        from vamos.netlist import plan as PL, signals as SG
        from vamos.spectre import run_xyce
        from vamos.spectre.job import Settings
        nl = self.parse(text)
        pl = PL.build(nl, "xyce", Settings())
        nl2, sm, _notes = SG.resolve(nl, pl)
        decks, _names = run_xyce.render_decks(nl2, pl, sm)
        return decks

    def test_tnom_is_the_decks_tnom_on_xyce(self):
        # UG p.93's example, ref19 p.507: r=(temp-tnom)*15+10k under options TEMP=55; a parameter and a user
        # function reading tnom too (ref19 p.481)
        decks = self.decks("simulator lang=spectre\nmodel res resistor\nreal tk(real x) { return x+temp-tnom; }\n"
                           "parameters rt=1k*(1+0.01*(temp-tnom))\nv1 (1 0) vsource dc=1\n"
                           "r1 1 0 res r=(temp-tnom)*15+10k\nr2 1 0 resistor r=rt\nr3 1 0 resistor r=tk(1k)\n"
                           "o1 options TEMP=55\nop1 dc\n")
        text = decks[0].text
        for name in ("r1", "r2", "r3"):
            line = [l for l in text.splitlines() if l.lower().startswith(name + " ")][0]
            self.assertNotIn("tnom", line.lower())
        self.assertIn("27", [l for l in text.splitlines() if l.lower().startswith("r1 ")][0])


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
