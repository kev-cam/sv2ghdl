"""HSPICE netlist parser: files, statements, parameters, ground pass, options, sources
(docs/VAMOS_AMS_DESIGN.md §4.3, §9 "SPICE parser").

    python3 -m unittest discover -s tests/vamos -p 'test_netlist_spice*.py' -v

Everything runs on Cygwin and WSL except TestSky130, which needs the sky130 PDK
installed under /opt/pdk/sky130A (WSL here).
"""

import os
import unittest

from vamos_testlib import TempDir, fixture

from vamos.netlist import expr as E  # noqa: E402
from vamos.netlist import ir, spice  # noqa: E402
from vamos.netlist.expr_ast import Binary, Call, Name, Num, Ternary  # noqa: E402
from vamos.netlist.expr import number as P  # noqa: E402  (correctly rounded, as spice.parse reads numbers)
from vamos.notes import NoteError  # noqa: E402

XHEEP = fixture("netlist", "spice_xheep")
XHEEP_CWD = os.path.join(XHEEP, "build", "openhwgroup.org_systems_core-v-mini-mcu_0", "sim-vcs")
XHEEP_ADC = "../../../hw/ip_examples/ams/analog/adc.sp"
LIBTREE = fixture("netlist", "spice_libtree")
SKY130 = os.environ.get("VAMOS_SKY130", "/opt/pdk/sky130A/libs.tech/ngspice")


def by_name(items, name):
    for it in items:
        if getattr(it, "name", None) == name:
            return it
    raise KeyError(name)


class _Base(TempDir):
    """Write a deck (its first line is the title) and parse it with cwd = the scratch dir."""

    def parse(self, text, extra=(), files=None, paths=None, **opts):
        for rel, body in (files or {}).items():
            self.write(rel, body)
        if paths is None:
            paths = [self.write("t.sp", text)]
        return spice.parse(paths, list(extra), self.tmp, ir.ParseOpts(**opts))

    def fails(self, text, *needles, **kw):
        with self.assertRaises(NoteError) as cm:
            self.parse(text, **kw)
        errs = [n for n in cm.exception.notes if n.severity == "error"]
        msg = "\n".join(n.text() for n in errs)
        self.assertTrue(errs, "no error")
        for nd in needles:
            self.assertIn(nd, msg)
        return errs

    @staticmethod
    def texts(nl, severity=None):
        return [n.text() for n in nl.notes if severity is None or n.severity == severity]

    def assertNote(self, nl, needle, severity="note"):
        found = [t for t in self.texts(nl, severity) if needle in t]
        self.assertTrue(found, "no %s with %r in:\n%s" % (severity, needle, "\n".join(self.texts(nl))))


# =============================================================================
# Lines and fields
# =============================================================================

class TestLexer(unittest.TestCase):
    def test_fields(self):
        lex = spice._lex
        self.assertEqual(lex("m1 d g s b n l = {l} w=1u"),
                         [(None, "m1"), (None, "d"), (None, "g"), (None, "s"), (None, "b"),
                          (None, "n"), ("l", "{l}"), ("w", "1u")])
        self.assertEqual(lex("v1 a 0 pulse(0 1 1n, 2n) ac=1,90"),
                         [(None, "v1"), (None, "a"), (None, "0"), (None, "pulse(0 1 1n, 2n)"),
                          ("ac", "1"), (None, "90")])
        self.assertEqual(lex(".ic v(b) = 'vh*2'"), [(None, ".ic"), ("v(b)", "'vh*2'")])
        self.assertEqual(lex("e1 o 0 vol='v(a) >= 1 ? 2 : 0'"),
                         [(None, "e1"), (None, "o"), (None, "0"), ("vol", "'v(a) >= 1 ? 2 : 0'")])
        self.assertEqual(lex(".subckt s a b params: w=1"),
                         [(None, ".subckt"), (None, "s"), (None, "a"), (None, "b"),
                          (None, "params:"), ("w", "1")])
        self.assertEqual(lex("r1 a b =1"), [(None, "r1"), (None, "a"), ("b", "1")])   # name = value
        for bad in ("r1 a b 'x", "r1 a b (1", "r1 a b 1)", "r1 = =1"):
            with self.assertRaises(spice._LexError, msg=bad):
                lex(bad)

    def test_comments_and_continuations(self):
        phys = ["r1 a b 1k $ HSPICE inline comment",
                "r2 a net$1 2k",                       # '$' inside a name is not a comment
                "r3 a b 3k ; semicolon comment",
                "r4 a b 4k // slash comment",
                "* comment line",
                ".model n nmos",
                "* a comment between continuation lines (sky130)",
                "",
                "+ level=1 $ trailing",
                "+ vto='0.7 $ not a comment'",
                "r5 a b \\",
                "5k",
                ".param p='1+\\\\",
                "   2'"]
        stmts, notes = spice._logical([(t, "f:%d" % (k + 1)) for k, t in enumerate(phys)])
        self.assertEqual(notes, [])
        got = [(s.text, s.origin) for s in stmts]
        self.assertEqual(got, [("r1 a b 1k", "f:1"), ("r2 a net$1 2k", "f:2"), ("r3 a b 3k", "f:3"),
                               ("r4 a b 4k", "f:4"),
                               (".model n nmos  level=1   vto='0.7 $ not a comment'", "f:6"),
                               ("r5 a b 5k", "f:11"), (".param p='1+2'", "f:13")])

    def test_continuation_without_statement(self):
        stmts, notes = spice._logical([("+ w=1", "f:1")])
        self.assertEqual(stmts, [])
        self.assertIn("continuation", notes[0].message)

    def test_fold_ground(self):
        for n in ("0", "gnd", "GND", "gnd!", "Ground", "00"):
            self.assertEqual(spice.fold_ground(n), "0", n)
        for n in ("vss", "gnd2", "x1.gnd", "10"):
            self.assertEqual(spice.fold_ground(n), n)


# =============================================================================
# Files (§4.3.1)
# =============================================================================

class TestFiles(_Base):
    def test_xheep_layout_cwd_relative(self):
        nl = spice.parse([XHEEP_ADC], [], XHEEP_CWD, ir.ParseOpts())
        self.assertEqual(nl.title, "** Copyright EPFL contributors.")
        self.assertEqual(sorted(m.name for m in nl.models().values()), ["nmos", "pmos"])
        self.assertEqual(nl.models()["nmos"].level, 54.0)
        self.assertEqual(nl.models()["pmos"].kind, "pmos")

    def test_xheep_wrong_cwd_lists_tried_paths(self):
        adc = os.path.join(XHEEP, "hw", "ip_examples", "ams", "analog", "adc.sp")
        with self.assertRaises(NoteError) as cm:
            spice.parse([adc], [], self.tmp, ir.ParseOpts())
        msg = "\n".join(n.text() for n in cm.exception.notes if n.severity == "error")
        self.assertIn("65nm_bulk.pm not found; tried", msg)
        self.assertIn(os.path.normpath(os.path.join(self.tmp, "../../../hw/ip_examples/ams/analog/"
                                                              "65nm_bulk.pm")), msg)

    def test_nested_includes_resolve_against_the_including_file(self):
        nl = self.parse("* deck\n.lib '%s' tt\nx1 d g 0 0 nfet w=2 l=0.5\nvd d 0 1\nvg g 0 1\n"
                        % os.path.join(LIBTREE, "lib.spice"))
        self.assertEqual(nl.values["corner_dvt"], 0.0)
        self.assertEqual(nl.values["vth_spread"], 0.0)
        self.assertEqual(nl.scale(), 1e-6)
        sub = nl.subckts()["nfet"]
        bins = [m for m in sub.body if isinstance(m, ir.Model)]
        self.assertEqual([(m.name, m.base, m.bin_index) for m in bins],
                         [("nfet__model.0", "nfet__model", 0), ("nfet__model.1", "nfet__model", 1)])
        m = by_name(sub.body, "mnfet")
        self.assertEqual(m.master, "nfet__model")
        self.assertEqual(m.params["l"], Name("l"))         # geometry stays unscaled, symbolic
        self.assertEqual([p.name for p in sub.params], ["l", "w", "nf", "ad", "as", "pd", "ps", "mult"])
        self.assertEqual(self.texts(nl), [])

    def test_ff_section(self):
        nl = self.parse("* deck\n.lib '%s' FF\n" % os.path.join(LIBTREE, "lib.spice"))
        self.assertEqual(nl.values["corner_dvt"], -0.05)
        self.assertAlmostEqual(nl.values["vth_spread"], -0.1)

    def test_missing_section(self):
        self.fails("* deck\n.lib '%s' ss\n" % os.path.join(LIBTREE, "lib.spice"),
                   "has no section ss", "(sections: ff tt)")

    def test_two_netlists_include_one_model_file(self):
        models = self.write("models.inc", ".model nch nmos level=1 vto=0.7\n")
        a = self.write("a.sp", "* netlist a\n.include 'models.inc'\n.subckt inva i o vdd\n"
                       "m1 o i 0 0 nch w=1u l=1u\n.ends\n")
        b = self.write("b.sp", ".include 'models.inc'\n.subckt invb i o vdd\nm1 o i 0 0 nch w=1u l=1u\n"
                       ".ends\n")
        nl = self.parse("", paths=[a, b])
        self.assertEqual(sorted(nl.subckts()), ["inva", "invb"])
        self.assertEqual(list(nl.models()), ["nch"])
        self.assertNote(nl, "%s already read at %s:2" % (models, a))
        self.assertEqual(nl.title, "* netlist a")       # line 1 of the second netlist is a statement

    def test_same_netlist_twice(self):
        a = self.write("a.sp", "* a\nr1 x 0 1k\n")
        nl = self.parse("", paths=[a, a])
        self.assertEqual(len(nl.instances()), 1)
        self.assertNote(nl, "already read")

    def test_duplicate_subckt_from_two_files_names_both(self):
        a = self.write("a.sp", "* a\n.subckt cell x\nr1 x 0 1k\n.ends\n")
        b = self.write("b.sp", ".subckt cell x\nr1 x 0 2k\n.ends\n")
        with self.assertRaises(NoteError) as cm:
            self.parse("", paths=[a, b])
        msg = "\n".join(n.text() for n in cm.exception.notes)
        self.assertIn(".subckt cell is defined twice: %s:2 and %s:1" % (a, b), msg)

    def test_duplicate_model(self):
        self.fails("* t\n.model n nmos level=1\n.model n nmos level=1\n", ".model n is defined twice")

    def test_end_then_netlist_commands(self):
        nl = self.parse("* t\nr1 a 0 1k\n.end\nr2 a 0 2k\n",
                        extra=[(".temp 45", "vcsAD.init:7"), (".param p=2", "vcsAD.init:8"),
                               ("+ q=3", "vcsAD.init:9")])
        self.assertEqual(nl.temp, 45.0)
        self.assertEqual(nl.values, {"p": 2.0, "q": 3.0})
        self.assertEqual([i.name for i in nl.instances()], ["r1"])
        self.assertNote(nl, "t.sp:4: ignored after .end (1 statement)")

    def test_end_in_include_ends_only_that_file(self):
        self.write("inc.sp", "r2 b 0 1k\n.end\nr9 b 0 9\n")
        nl = self.parse("* t\n.inc 'inc.sp'\nr1 a 0 1k\n")
        self.assertEqual([i.name for i in nl.instances()], ["r2", "r1"])

    def test_title_only_from_first_netlist(self):
        nl = self.parse(".param a=1\n.param b=2\n")
        self.assertEqual(nl.title, ".param a=1")
        self.assertEqual(nl.values, {"b": 2.0})
        self.assertNote(nl, "t.sp:1: line 1 is the title and is not parsed: .param a=1")
        nl = self.parse("Inverter test circuit\nr1 a 0 1k\n")
        self.assertEqual(self.texts(nl), [])

    def test_search_and_two_candidates(self):
        os.makedirs(os.path.join(self.tmp, "lib"))
        self.write("lib/m.inc", ".model n nmos level=1\n")
        nl = self.parse("* t\n.option search='lib'\n.inc 'm.inc'\n")
        self.assertIn("n", nl.models())
        nl = self.parse("* t\n.inc 'm.inc'\n", search=["lib"])
        self.assertIn("n", nl.models())
        self.write("sub/m.inc", ".model n nmos level=1\n")
        self.write("sub/top.sp", "* t\n.inc 'm.inc'\n")
        self.write("m.inc", ".model n nmos level=1\n")
        nl = self.parse("", paths=[os.path.join(self.tmp, "sub", "top.sp")])
        self.assertNote(nl, "include file m.inc: using %s (also found: %s)"
                        % (os.path.join(self.tmp, "m.inc"), os.path.join(self.tmp, "sub", "m.inc")))

    def test_lib_section_definitions_skipped_when_read_whole(self):
        nl = self.parse("* t\n.lib tt\nr9 a 0 9\n.endl tt\nr1 a 0 1\n.lib 't.sp' tt\n")
        self.assertEqual([i.name for i in nl.instances()], ["r1", "r9"])

    def test_nested_section_call_in_same_file(self):
        self.write("l.lib", ".lib base\n.param b=1\n.endl\n.lib tt\n.lib base\n.param t=2\n.endl tt\n")
        nl = self.parse("* t\n.lib 'l.lib' tt\n")
        self.assertEqual(nl.values, {"b": 1.0, "t": 2.0})

    def test_lib_without_endl(self):
        self.write("l.lib", ".lib tt\n.param a=1\n")
        self.fails("* t\n.lib 'l.lib' tt\n", ".lib tt has no .endl")

    def test_unsupported_dot_commands(self):
        self.fails("* t\n.alter\nr1 a 0 1\n", ".alter is not supported")
        self.fails("* t\n.if (a == 1)\nr1 a 0 1\n.endif\n", ".if is not supported")
        self.fails("* t\n.connect a b\n", ".connect is not supported")
        self.fails("* t\n.frobnicate\n", "unsupported dot-command .frobnicate")
        self.fails("* t\n.data d1\na b\n.enddata\n", ".data is not supported")
        nl = self.parse("* t\n.measure tran x find v(a) at=1n\n.noise v(o) vin\nr1 a 0 1\n")
        self.assertNote(nl, ".measure is not supported", "warning")
        self.assertNote(nl, ".noise ignored", "warning")

    def test_unterminated_subckt(self):
        self.fails("* t\n.subckt a x\nr1 x 0 1\n", ".subckt a has no .ends")
        self.fails("* t\n.ends\n", ".ends without .subckt")
        self.fails("* t\n.subckt a x\n.ends b\n", ".ends b closes .subckt a")

    def test_crlf_tabs_and_bom(self):
        path = os.path.join(self.tmp, "crlf.sp")
        with open(path, "wb") as fh:
            fh.write(b"\xef\xbb\xbf* title\r\nr1\ta\t0\t1k\r\n+\ttc1=1m\r\n.param\tp = 2\r\n")
        nl = self.parse("", paths=[path])
        self.assertEqual(nl.title, "* title")
        r1 = nl.instances()[0]
        self.assertEqual((r1.nodes, r1.value, r1.params), (["a", "0"], Num(1000.0), {"tc1": Num(P("1m"))}))
        self.assertEqual(nl.values, {"p": 2.0})

    def test_fragment_includes_resolve_against_cwd(self):
        self.write("m.inc", ".model n nmos level=1\n")
        nl = self.parse("* t\n", extra=[(".include m.inc", "vcsAD.init:3")])
        self.assertIn("n", nl.models())
        with self.assertRaises(NoteError) as cm:
            self.parse("* t\n", extra=[(".include nosuch.inc", "vcsAD.init:3")])
        self.assertEqual("vcsAD.init:3: include file nosuch.inc not found; tried %s"
                         % os.path.join(self.tmp, "nosuch.inc"), cm.exception.notes[0].text()[len("error: "):])

    def test_fragment_references_resolve_beside_their_control_file(self):
        """§4.3.1: netlist_commands lines are in the control file their origin names, so their
        .include/.lib/.option search are tried in the cwd, then in that file's directory."""
        self.write("ctl/m.inc", ".model n nmos level=1\n")
        self.write("ctl/lib/p.inc", ".param q=3\n")
        self.write("ctl/l.lib", ".lib tt\n.param t=2\n.endl tt\n")
        nl = self.parse("* t\n", extra=[(".include m.inc", "ctl/sub.init:3"),
                                         (".option search='lib'", "ctl/sub.init:4"),
                                         (".include p.inc", "ctl/sub.init:5"),
                                         (".lib 'l.lib' tt", "ctl/sub.init:6")])
        self.assertIn("n", nl.models())
        self.assertEqual(nl.values, {"q": 3.0, "t": 2.0})
        with self.assertRaises(NoteError) as cm:
            self.parse("* t\n", extra=[(".include nosuch.inc", "ctl/sub.init:3")])
        self.assertEqual(cm.exception.notes[0].text(), "error: ctl/sub.init:3: include file nosuch.inc not "
                         "found; tried %s, %s" % (os.path.join(self.tmp, "nosuch.inc"),
                                                  os.path.join(self.tmp, "ctl", "nosuch.inc")))

    def test_choose_netlist_beside_its_control_file(self):
        """§4.3.1: a choose netlist given with its origin, (path, "<control file>:<line>"), is
        tried in the cwd, then in the control file's directory (vcs -ad=ctl/sub.init, e2e 17),
        and a missing one is reported at the choose command."""
        self.write("ctl/sub.sp", "* sub\nr1 a 0 1k\n")
        nl = self.parse("", paths=[("sub.sp", "ctl/sub.init:1")])
        self.assertEqual((nl.title, [i.name for i in nl.instances()]), ("* sub", ["r1"]))
        self.assertEqual(self.texts(nl), [])
        nl = self.parse("", paths=[(os.path.join(self.tmp, "ctl", "sub.sp"), "elsewhere/x.init:1")])
        self.assertEqual(nl.title, "* sub")                         # an absolute path as written
        self.write("sub.sp", "* top\nr2 a 0 1k\n")                  # the cwd comes first
        nl = self.parse("", paths=[("sub.sp", "ctl/sub.init:1")])
        self.assertEqual([i.name for i in nl.instances()], ["r2"])
        self.assertNote(nl, "ctl/sub.init:1: netlist sub.sp: using %s (also found: %s)"
                        % (os.path.join(self.tmp, "sub.sp"), os.path.join(self.tmp, "ctl", "sub.sp")))
        errs = self.fails("", paths=[("nosuch.sp", "ctl/sub.init:1")])
        self.assertEqual([e.text() for e in errs],
                         ["error: ctl/sub.init:1: netlist nosuch.sp not found; tried %s, %s"
                          % (os.path.join(self.tmp, "nosuch.sp"), os.path.join(self.tmp, "ctl", "nosuch.sp"))])
        # a bare path has no control file: the cwd only
        self.write("ctl/only.sp", "* only\n")
        errs = self.fails("", paths=["only.sp"])
        self.assertEqual([e.text() for e in errs], ["error: only.sp: netlist only.sp not found; tried %s"
                                                    % os.path.join(self.tmp, "only.sp")])
        # the control file's directory also serves the netlist's own includes
        self.write("ctl/inc.sp", "* inc\n.include 'm.inc'\n")
        self.write("ctl/m.inc", "r3 b 0 1k\n")
        nl = self.parse("", paths=[("inc.sp", "ctl/sub.init:1")])
        self.assertEqual([i.name for i in nl.instances()], ["r3"])
        nl = self.parse("", paths=[("inc.sp", "ctl/sub.init:1"), ("ctl/inc.sp", "ctl/sub.init:2")])
        self.assertNote(nl, "ctl/sub.init:2: netlist ctl/inc.sp already read")

    def test_hdl_errors(self):
        self.fails("* t\n.hdl 'nosuch.va'\n", "Verilog-A file nosuch.va not found")
        self.write("empty.va", "// nothing\n")
        self.fails("* t\n.hdl 'empty.va'\n", "declares no Verilog-A module")


# =============================================================================
# Ground pass (§4.3.4)
# =============================================================================

class TestGround(_Base):
    def test_xheep_adc_verbatim(self):
        nl = spice.parse([XHEEP_ADC], [], XHEEP_CWD, ir.ParseOpts())
        self.assertEqual(nl.globals, ["vdd"])                      # .global VDD GND loses GND
        self.assertEqual([i.name for i in nl.instances()], ["v_vdd"])  # v_gnd GND 0 0 dropped
        self.assertNote(nl, "adc.sp:11: v_gnd: both terminals are ground; the source is dropped")
        subs = nl.subckts()
        adc = subs["ams_adc_1b"]
        self.assertEqual(adc.orig_ports, ["gnd", "out", "sel<1>", "sel<0>", "vdd"])
        self.assertEqual(adc.ports, ["out", "sel<1>", "sel<0>", "vdd"])
        self.assertEqual(adc.gnd_ports, [0])
        self.assertEqual(subs["demux"].gnd_ports, [4])
        inv = subs["inverter"]
        self.assertEqual((inv.orig_ports, inv.ports), (["gnd", "in", "out", "vdd"], ["in", "out", "vdd"]))
        self.assertEqual(by_name(inv.body, "mn0").nodes, ["out", "in", "0", "0"])
        xi4 = by_name(subs["demux"].body, "xi4")                   # XI4 GND SEL_1 SEL_1_not VDD INVERTER
        self.assertEqual((xi4.kind, xi4.master, xi4.nodes), ("x", "inverter", ["sel_1", "sel_1_not", "vdd"]))
        xi0 = by_name(adc.body, "xi0")
        self.assertEqual(xi0.nodes, ["net010", "net7", "net8", "net02", "vmux_out", "sel<0>", "sel<1>",
                                     "vdd"])
        v2 = by_name(adc.body, "v2")                               # V2 Vin GND SIN 600m 600m 1e6 250e-9
        self.assertEqual(v2.nodes, ["vin", "0"])
        self.assertEqual(v2.source.wave, "sin")
        self.assertEqual(v2.source.args, {"vo": Num(0.6), "va": Num(0.6), "freq": Num(1e6),
                                          "td": Num(250e-9), "theta": Num(0.0), "phase": Num(0.0)})
        i2 = by_name(subs["comparator"].body, "i2")                # I2 VDD net31 DC=200u
        self.assertEqual((i2.kind, i2.source.dc), ("i", Num(P("200u"))))
        self.assertEqual(nl.spelling["ams_adc_1b"], "AMS_ADC_1b")
        self.assertEqual(nl.spelling["sel<1>"], "SEL<1>")
        self.assertEqual(nl.spelling["gnd"], "GND")

    def test_pams_p27(self):
        nl = spice.parse([fixture("netlist", "spice_pams_p27", "test.spi")], [],
                         fixture("netlist", "spice_pams_p27"), ir.ParseOpts())
        self.assertEqual(nl.globals, ["vdd"])
        self.assertEqual([i.name for i in nl.instances()], ["vvdd"])          # vgnd gnd 0 dc 0 dropped
        self.assertNote(nl, "vgnd: both terminals are ground; the source is dropped")
        test = nl.subckts()["test"]
        self.assertEqual(by_name(test.body, "vref").nodes, ["ref", "0"])
        self.assertEqual(by_name(test.body, "x1").nodes, ["a", "b", "n1", "vdd"])
        cell1 = nl.subckts()["cell1"]
        self.assertEqual(by_name(cell1.body, "mn2").nodes, ["mid", "b", "0", "0"])
        self.assertEqual(by_name(cell1.body, "mn1").params, {"w": Num(1.0), "l": Num(0.35)})  # unscaled
        self.assertEqual(nl.options["scale"], Num(1e-6))
        self.assertEqual((nl.temp, nl.tnom), (27.0, 25.0))
        self.assertEqual(sorted(nl.models()), ["nch", "pch"])                   # the TT section only

    def test_pams_p349_stays_aliased_without_a_tie(self):
        nl = spice.parse([fixture("netlist", "spice_pams_p349", "01_test.spi")], [],
                         fixture("netlist", "spice_pams_p349"), ir.ParseOpts())
        self.assertEqual(nl.globals, ["vdd"])
        self.assertEqual(by_name(nl.instances(), "vsu").nodes, ["vdd", "0"])
        for s in ("sp1", "sp2"):
            self.assertEqual(by_name(nl.subckts()[s].body, "mn1").nodes, ["sp_out", "sp_in", "0", "0"])
        self.assertEqual(self.texts(nl), [])

    def test_collapsed_elements(self):
        nl = self.parse("* t\nr1 gnd 0 1k\nc1 0 GND 1p\nl1 gnd! 0 1n\ni1 0 0 1m\nd1 0 0 dmod\n"
                        "g1 0 0 a 0 1m\nf1 0 0 vs 2\ne0 0 0 a 0 0\nh0 0 0 vs 0\neb 0 0 vol='0'\n"
                        "gb 0 0 cur='v(a)'\nvs a 0 1\n.model dmod d\n")
        self.assertEqual([i.name for i in nl.instances()], ["vs"])
        for n in ("r1", "c1", "l1", "i1", "d1", "g1", "f1", "gb"):
            self.assertNote(nl, "%s: both terminals are ground; dropped" % n)
        for n in ("e0", "h0", "eb"):
            self.assertNote(nl, "%s: both terminals are ground and its value is 0; dropped" % n)

    def test_collapsed_sources_that_are_errors(self):
        self.fails("* t\nv1 gnd 0 1.2\n", "v1 is a voltage source between two ground nodes")
        self.fails("* t\nv1 gnd 0 0 ac 1\n", "v1 is a voltage source between two ground nodes")
        self.fails("* t\nv1 gnd 0 pulse(0 1)\n", "v1 is a voltage source between two ground nodes")
        self.fails("* t\ne1 0 0 a 0 2\nr1 a 0 1\n", "e1 drives a voltage between two ground nodes")
        self.fails("* t\nh1 gnd 0 vs 5\nvs a 0 1\n", "h1 drives a voltage")
        nl = self.parse("* t\n.param z=0\nv1 gnd 0 'z'\n")
        self.assertEqual(nl.instances(), [])

    def test_references_to_dropped_elements(self):
        self.fails("* t\n.subckt s a\nvs 0 gnd 0\nr1 a 0 1\n.ends\nx1 n s\n.print tran i(x1.vs)\n",
                   "i(x1.vs): the element was dropped")
        self.fails("* t\nvs 0 gnd 0\nf1 a 0 vs 2\nr1 a 0 1\n", "f1 refers to vs, which was dropped")
        self.fails("* t\nl1 0 gnd 1n\nl2 a 0 1n\nk1 l1 l2 0.9\nr1 a 0 1\n",
                   "k1 refers to l1, which was dropped")
        self.fails("* t\nvs 0 gnd 0\n.print tran i(vs)\n", "i(vs): the element was dropped")
        self.fails("* t\nvs 0 gnd 0\ng1 a 0 cur='i(vs)'\nr1 a 0 1\n", "g1 reads i(vs) of vs")

    def test_ground_port_actual(self):
        sub = "* t\n.subckt inv gnd in out vdd\nm1 out in gnd gnd n w=1u l=1u\n.ends\n" \
              ".model n nmos level=1\n"
        nl = self.parse(sub + "x1 0 a b vdd inv\nx2 gnd! a c vdd inv\n")
        self.assertEqual(by_name(nl.instances(), "x1").nodes, ["a", "b", "vdd"])
        self.assertEqual(by_name(nl.instances(), "x2").nodes, ["a", "c", "vdd"])
        self.fails(sub + "x1 vss a b vdd inv\n", "x1 connects vss to port gnd of subckt inv, which is "
                   "ground inside the subckt")
        self.fails(sub + "x1 a b vdd inv\n", "x1 has 3 nodes but subckt inv has 4 ports")

    def test_global_port_bound_to_another_net(self):
        sub = "* t\n.global vdd\n.subckt inv in out vdd\nr1 in out 1k\n.ends\n"
        self.parse(sub + "x1 a b vdd inv\n")
        self.fails(sub + "x1 a b vcore inv\n", "x1 binds port vdd of subckt inv, a .global net, to vcore",
                   "port_connect -cell inv (vdd => vdd)")

    def test_ground_in_expressions_and_probes(self):
        nl = self.parse("* t\ne1 o gnd vol='v(a,GND)*2 + v(x1.gnd)'\nr1 a 0 1\nr2 o 0 1\n"
                        ".print tran v(gnd) v(a,gnd!) i(r1)\n.ic v(a)=1 v(gnd)=0\n")
        e1 = by_name(nl.instances(), "e1")
        self.assertEqual((e1.kind, e1.nodes, e1.expr_kind), ("b", ["o", "0"], "v"))
        calls = E.node_calls(e1.expr)
        self.assertEqual(calls, [Call("v", (Name("a"), Name("0"))), Call("v", (Name("0"),))])
        self.assertEqual(nl.probes, [("tran", "v", "0"), ("tran", "v", "a,0"), ("tran", "i", "r1")])
        self.assertEqual(nl.ics, {"a": 1.0})

    def test_numeric_nodes(self):
        nl = self.parse("* t\nr1 007 00 1k\nr2 7 a{3} 1k\n")
        self.assertEqual(by_name(nl.instances(), "r1").nodes, ["7", "0"])
        self.assertEqual(by_name(nl.instances(), "r2").nodes, ["7", "a[3]"])


# =============================================================================
# Parameters (§4.3.3)
# =============================================================================

class TestParameters(_Base):
    def test_duplicates_last_wins_everywhere(self):
        nl = self.parse("* t\n.param a=1\n.param b='a*10'\n.param a=2\n.subckt s x w=1\n.param w=3\n"
                        "r1 x 0 w\n.ends\n")
        self.assertEqual(nl.values, {"a": 2.0, "b": 20.0})
        self.assertEqual([p.name for p in nl.body if isinstance(p, ir.Param)], ["a", "b"])
        self.assertNote(nl, "t.sp:4: parameter a is defined again (first at %s:2)"
                        % os.path.join(self.tmp, "t.sp"))
        s = nl.subckts()["s"]
        self.assertEqual([(p.name, p.expr) for p in s.params], [("w", Num(3.0))])
        self.assertNote(nl, "parameter w is defined again")

    def test_out_of_order_chain(self):
        nl = self.parse("* t\n.param b='a*2' c='b+1'\n.param a=g\n.param g=0.5\n"
                        ".subckt s x p='q*2' q=w\n.param w='g*4'\nr1 x 0 p\n.ends\n")
        self.assertEqual([p.name for p in nl.body if isinstance(p, ir.Param)], ["g", "a", "b", "c"])
        self.assertEqual(nl.values, {"g": 0.5, "a": 0.5, "b": 1.0, "c": 2.0})
        self.assertEqual([p.name for p in nl.subckts()["s"].params], ["w", "q", "p"])

    def test_cycle(self):
        self.fails("* t\n.param a='b+1' b='c+1'\n.param c='a+1'\n",
                   "parameters depend on each other in a cycle: a -> b -> c -> a")
        self.fails("* t\n.subckt s x\n.param p='p*2'\n.ends\n", "cycle: p -> p")

    def test_parhier_global_collisions(self):
        deck = "* t\n.param w=1 l=2 k=3\n.subckt s x w=5\n.param l=6\nr1 x 0 'w*l'\n.ends\nx1 a s k=7\n"
        errs = self.fails(deck)
        msgs = [e.message for e in errs]
        self.assertEqual(len(errs), 3)
        self.assertTrue(any("parameter w is defined at top level" in m and "subckt s" in m for m in msgs))
        self.assertTrue(any("parameter l is defined at top level" in m for m in msgs))
        self.assertTrue(any("parameter k" in m and "the override on x1" in m for m in msgs))
        nl = self.parse(deck, parhier_local=True)
        self.assertEqual(nl.parhier, "local")
        self.assertEqual(len([t for t in self.texts(nl, "note") if "local scoping" in t]), 3)
        nl = self.parse(deck.replace("* t\n", "* t\n.option parhier=local\n"))
        self.assertEqual((nl.parhier, self.texts(nl)), ("local", []))
        nl = self.parse("* t\n.param w=1\n.subckt s x v=5\nr1 x 0 v\n.ends\nx1 a s m=2\n")
        self.assertEqual(nl.parhier, "global")

    def test_evaluation(self):
        nl = self.parse("* t\n.param a=2 b='pow(a,3)+sqrt(-4)' t2='temper*2' t3='t2+1' s=\"str\"\n"
                        ".param g='agauss(1,0.1,3)'\n")
        self.assertEqual(nl.values, {"a": 2.0, "b": 6.0, "g": 1.0})
        self.fails("* t\n.param a='b+1'\n", "parameter a uses b, which is not defined at top level")
        self.fails("* t\n.param a='1/0'\n", "parameter a: division by zero")
        self.fails("* t\n.param a='v(x)'\n", "reads v(x)")

    def test_user_functions(self):
        nl = self.parse("* t\n.param f(x,y)='x*y+k' k=1\n.param a='f(2,3)'\n.subckt s n\n"
                        ".param g(z)='f(z,z)'\nr1 n 0 'g(2)'\n.ends\n")
        self.assertEqual(nl.values["a"], 7.0)
        r1 = nl.subckts()["s"].body[0]
        self.assertEqual(E.evaluate(r1.value, {"k": 1.0}), 5.0)
        self.fails("* t\n.param max(a,b)='a'\n", "redefines a built-in function")
        self.fails("* t\n.param a='nosuch(1)'\n", "unknown function nosuch()")

    def test_case_folding(self):
        nl = self.parse("* t\n.PARAM VSup=1.2\nV1 VDD 0 'VSUP'\n")
        self.assertEqual(nl.values, {"vsup": 1.2})
        self.assertEqual(by_name(nl.instances(), "v1").nodes, ["vdd", "0"])
        self.assertEqual(nl.spelling["vdd"], "VDD")
        nl = self.parse("* t\n.param VSup=1.2\nV1 VDD 0 'VSup'\n", case="sensitive")
        self.assertEqual(nl.values, {"VSup": 1.2})
        self.assertEqual(by_name(nl.instances(), "V1").nodes, ["VDD", "0"])


# =============================================================================
# Options (§4.3.5)
# =============================================================================

class TestOptions(_Base):
    def test_scale_kept_unscaled(self):
        nl = self.parse("* t\n.option scale=1e-6\n.model n nmos level=1\nm1 d g 0 0 n w=2 l=1 ad=4\n")
        m1 = by_name(nl.instances(), "m1")
        self.assertEqual(m1.params, {"w": Num(2.0), "l": Num(1.0), "ad": Num(4.0)})
        self.assertEqual(nl.scale(), 1e-6)

    def test_temp_and_tnom_defaults_are_independent(self):
        nl = self.parse("* t\nr1 a 0 1\n")
        self.assertEqual((nl.temp, nl.tnom), (25.0, 25.0))
        nl = self.parse("* t\n.param tt=50\n.temp tt\n")
        self.assertEqual((nl.temp, nl.tnom), (50.0, 25.0))
        nl = self.parse("* t\n.option tnom=30\n")
        self.assertEqual((nl.temp, nl.tnom), (25.0, 30.0))
        self.assertEqual(nl.options["tnom"], Num(30.0))
        nl = self.parse("* t\n.option spice\n")
        self.assertEqual((nl.temp, nl.tnom, nl.options), (27.0, 27.0, {"spice": Num(1.0)}))
        nl = self.parse("* t\n.temp 0 50 100\n")
        self.assertEqual(nl.temp, 0.0)
        self.assertNote(nl, "only the first (0) is simulated", "warning")

    def test_dispositions(self):
        nl = self.parse("* t\n.options post=2 probe reltol=1e-4 method=gear runlvl=5 gshunt=1e-12 "
                        "frob=1\n.option abstol=1p\n")
        self.assertEqual(sorted(nl.options), ["abstol", "method", "reltol"])
        self.assertEqual((nl.options["reltol"], nl.options["abstol"]), (Num(1e-4), Num(P("1p"))))
        self.assertEqual(nl.options["method"].text, "gear")
        self.assertNote(nl, ".option post ignored (output and listing control)")
        self.assertNote(nl, ".option runlvl ignored (simulator control")
        self.assertNote(nl, ".option gshunt ignored", "warning")
        self.assertNote(nl, ".option frob ignored (unknown to vamos)", "warning")
        self.fails("* t\n.option scalm=2\n", ".option scalm=2 is not supported")
        self.fails("* t\n.option geoshrink=0.9\n", ".option geoshrink=0.9 is not supported")
        self.parse("* t\n.option scalm=1\n")

    def test_dcap_and_spice_select_model_defaults(self):
        """.option dcap selects the junction-capacitance equations (tables.hspice_card): it is kept,
        not 'ignored (simulator control)'; .option spice says which SPICE defaults it brings."""
        nl = self.parse("* t\n.param d=1\n.option dcap=d\n")
        self.assertEqual(nl.options, {"dcap": Num(1.0)})
        self.assertFalse([t for t in self.texts(nl) if "dcap" in t])
        self.fails("* t\n.option dcap=4\n", ".option dcap=4 must be 1, 2 or 3")
        self.fails("* t\n.option dcap\n", ".option dcap needs a value")
        nl = self.parse("* t\n.option spice\n")
        self.assertNote(nl, ".option spice: temp and tnom default to 27, DCAP to 1, and the model defaults are "
                            "SPICE's (MOS CAPOP=0, LD=0, no NSUB default; BJT MJS=0)")

    def test_last_option_wins(self):
        nl = self.parse("* t\n.option scale=1e-6\n.option scale=2e-6\n")
        self.assertEqual(nl.scale(), 2e-6)
        self.assertNote(nl, ".option scale=2e-6 replaces 1e-6")

    def test_wl_and_mos_defaults(self):
        deck = "* t\n.model n nmos level=1\nm1 d g 0 0 n 2u 1u\n"
        self.assertEqual(by_name(self.parse(deck).instances(), "m1").params,
                         {"l": Num(P("2u")), "w": Num(P("1u"))})
        nl = self.parse(deck.replace("* t\n", "* t\n.option wl\n"))
        self.assertEqual(by_name(nl.instances(), "m1").params, {"w": Num(P("2u")), "l": Num(P("1u"))})
        nl = self.parse("* t\n.model n nmos level=1\nm1 d g 0 0 n\n")
        self.assertEqual(by_name(nl.instances(), "m1").params, {"l": Num(1e-4), "w": Num(1e-4)})
        self.assertNote(nl, "m1: L not given; the HSPICE default DEFL=0.0001 m is used")
        nl = self.parse("* t\n.option defl=0.5u defad=1p\n.model n nmos level=1\nm1 d g 0 0 n w=1u\n")
        self.assertEqual(by_name(nl.instances(), "m1").params,
                         {"w": Num(P("1u")), "l": Num(P("0.5u")), "ad": Num(P("1p"))})


# =============================================================================
# Models (§4.3.6, the parse side)
# =============================================================================

class TestModels(_Base):
    def test_kinds_levels_and_forms(self):
        nl = self.parse("* t\n.param lv=3\n.model N1 NMOS (LEVEL=1 VTO=0.7, KP=110u)\n"
                        ".model p1 pmos(level=54 version=4.5)\n.model d1 d is=1e-14 level=lv\n"
                        ".model q1 npn bf=100\n.model j1 pjf beta=1m\n.model r1 r rsh=10\n")
        ms = nl.models()
        self.assertEqual({k: (m.kind, m.level) for k, m in ms.items()},
                         {"n1": ("nmos", 1.0), "p1": ("pmos", 54.0), "d1": ("d", 3.0),
                          "q1": ("npn", None), "j1": ("pjf", None), "r1": ("r", None)})
        self.assertEqual(ms["n1"].params, {"level": Num(1.0), "vto": Num(0.7), "kp": Num(P("110u"))})
        self.assertEqual(ms["p1"].params["version"], Num(4.5))

    def test_binning(self):
        nl = self.parse("* t\n.model nch.1 nmos level=54 lmin=1u lmax=2u wmin=1u wmax=2u\n"
                        ".model nch.2 nmos level=54 lmin=2u lmax=3u wmin=1u wmax=2u\n"
                        ".model pch.1 pmos level=54 lmin=1u\nm1 d g 0 0 nch w=1u l=1u\n")
        ms = nl.models()
        self.assertEqual((ms["nch.1"].base, ms["nch.1"].bin_index), ("nch", 1))
        self.assertEqual((ms["nch.2"].base, ms["nch.2"].bin_index), ("nch", 2))
        self.assertEqual((ms["pch.1"].base, ms["pch.1"].bin_index), (None, None))  # bounds missing
        self.assertEqual(by_name(nl.instances(), "m1").master, "nch")
        self.fails("* t\n.model nch nmos level=1\n.model nch.1 nmos lmin=1 lmax=2 wmin=1 wmax=2\n",
                   "model nch is defined both as a card and as bins")

    def test_unsupported_type_left_out(self):
        nl = self.parse("* t\n.model o1 opt\n.model sw1 sw ron=1\nr1 a 0 1\n")
        self.assertNote(nl, "model o1 is left out: model type OPT is not supported")
        self.assertEqual(sorted(nl.models()), [])
        self.fails("* t\n.model sw1 sw ron=1\nm1 d g 0 0 sw1 w=1u l=1u\n",
                   "m1: model sw1 is left out: model type SW is not supported")

    def test_model_versus_parameter(self):
        nl = self.parse("* t\n.param capxx=1\nc1 1 2 capxx\nc2 1 2 capyy\n.model capxx c cap=1\n"
                        ".param capyy=2p\n")
        c1, c2 = by_name(nl.instances(), "c1"), by_name(nl.instances(), "c2")
        self.assertEqual((c1.master, c1.value), ("capxx", None))
        self.assertEqual((c2.master, c2.value), (None, Name("capyy")))
        self.fails("* t\n.model dd d\nr1 a 0 dd\n", "r1: model dd is a d model, not r")
        self.fails("* t\nm1 d g 0 0 nosuch w=1u l=1u\n", "m1: model nosuch not found")


# =============================================================================
# Elements (§4.2 conventions)
# =============================================================================

class TestElements(_Base):
    def test_passives(self):
        nl = self.parse("* t\n.model rm r rsh=1\nr1 a b 1k 0.001 0\nr2 a b r='2*1k' tc1=1m scale=2 m=3\n"
                        "r3 a b rm w=1u l=10u\nc1 a b c=1p ic=0.5\nl1 a b 1n\nk1 l1 l2 0.9\nl2 c d 2n\n"
                        "k2 l1 l2 k=0.5\nr4 a b 1k ac=1meg\n")
        r1, r2, r3 = (by_name(nl.instances(), n) for n in ("r1", "r2", "r3"))
        self.assertEqual((r1.value, r1.params), (Num(1000.0), {"tc1": Num(0.001), "tc2": Num(0.0)}))
        self.assertEqual(r2.value, Num(4000.0))
        self.assertEqual(r2.params, {"tc1": Num(P("1m")), "m": Num(3.0)})
        self.assertEqual((r3.master, r3.value, r3.params), ("rm", None, {"w": Num(P("1u")), "l": Num(P("10u"))}))
        c1 = by_name(nl.instances(), "c1")
        self.assertEqual((c1.value, c1.params), (Num(P("1p")), {"ic": Num(0.5)}))
        k1 = by_name(nl.instances(), "k1")
        self.assertEqual((k1.kind, k1.nodes, k1.ctrl, k1.value), ("k", [], ["l1", "l2"], Num(0.9)))
        self.assertEqual(by_name(nl.instances(), "k2").value, Num(0.5))
        self.assertNote(nl, "r4: AC=1000000.0 ignored")

    def test_d_exponents(self):
        """HSPICE (3-4): "Exponents are designated by D or E".  A signed D exponent was silently read
        as a unit letter and arithmetic: 1.0D+3 became 4.0, 50D-15 became 35.0, is=1.0D-14 -13.0."""
        nl = self.parse("* t\nv1 a 0 1\nr1 a b 1.0D+3\nr2 b 0 '2*1.0D+3'\n.param cl=50D-15\nc1 b 0 cl\n"
                        ".model dd d is=1.0D-14 n=1.0D+00 rs=1.0D+1\nd1 b 0 dd\n"
                        "v2 p 0 pulse(0 1 0 1.0D-10 1.0d-10 5D-9)\nr3 p 0 1k\n.tran 1.0D-10 1D-8\n")
        i = {x.name: x for x in nl.instances()}
        self.assertEqual((i["r1"].value, i["r2"].value), (Num(1000.0), Num(2000.0)))
        self.assertEqual(nl.values, {"cl": 5e-14})
        self.assertEqual(nl.models()["dd"].params, {"is": Num(1e-14), "n": Num(1.0), "rs": Num(10.0)})
        self.assertEqual((i["v2"].source.args["tr"], i["v2"].source.args["tf"], i["v2"].source.args["pw"]),
                         (Num(1e-10), Num(1e-10), Num(5e-9)))
        self.assertEqual((nl.tran().args["step"], nl.tran().args["stop"]), (1e-10, 1e-8))
        self.assertEqual(by_name(self.parse("* t\nr1 a 0 2.5D\n").instances(), "r1").value, Num(2.5))

    def test_unsupported_passives(self):
        self.fails("* t\nc1 a b q='v(a)*1p'\n", "c1: a charge-defined capacitor (Q=) is not supported")
        self.fails("* t\nr1 a b r='1k*v(a)'\n", "r1: r='1k*v(a)' reads v(a)")
        self.fails("* t\nl1 a b 1n r=1\n", "an inductor with a series resistance (R=) is not supported")
        self.fails("* t\nc1 a b poly 1 2\n", "POLY form")
        self.fails("* t\nr1 a b\n", "r1 needs a value or a model")

    def test_controlled_sources(self):
        nl = self.parse("* t\ne1 o 0 a b 2\ne2 o2 0 vcvs a 0 'k' scale=2\ng1 o 0 vccs a b 1m m=2\n"
                        "f1 o 0 vs 3\nh1 o3 0 ccvs vs 1k\ng2 o 0 cur='v(a)*1m' m=2\ne3 o4 0 value={v(a)}\n"
                        "vs a 0 1\n.param k=1\n")
        i = {x.name: x for x in nl.instances()}
        self.assertEqual((i["e1"].kind, i["e1"].nodes, i["e1"].value), ("e", ["o", "0", "a", "b"], Num(2.0)))
        self.assertEqual(i["e2"].value, Binary("*", Name("k"), Num(2.0)))
        self.assertEqual((i["g1"].value, i["g1"].params), (Num(P("1m")), {"m": Num(2.0)}))
        self.assertEqual((i["f1"].kind, i["f1"].ctrl, i["f1"].value), ("f", ["vs"], Num(3.0)))
        self.assertEqual((i["h1"].kind, i["h1"].ctrl), ("h", ["vs"]))
        self.assertEqual((i["g2"].kind, i["g2"].expr_kind, i["g2"].params), ("b", "i", {"m": Num(2.0)}))
        self.assertEqual((i["e3"].kind, i["e3"].expr_kind), ("b", "v"))
        for bad, needle in (("e1 o 0 poly(1) a 0 0 1", "the POLY form"),
                            ("e1 o 0 a 0 2 max=1", "MAX= is not supported"),
                            ("e1 o 0 cur='1'", "CUR= belongs on a G element"),
                            ("g1 o 0 vol='1'", "VOL= belongs on an E element"),
                            ("e1 o 0 laplace a 0 1 2", "the LAPLACE form"),
                            ("f1 o 0 vx 2\nvx a 0 1\nr9 a 0 1\ne9 vx2 0 a 0 1\nf2 o 0 e9 1",
                             "f2: the controlling element e9 is not a voltage source")):
            self.fails("* t\n%s\n" % bad, needle)

    def test_semiconductors(self):
        nl = self.parse("* t\n.model dm d\n.model qn npn\n.model jn njf\n.model nm nmos level=1\n"
                        "d1 a c dm 2 pj=3\nd2 a c dm area=4 off\nq1 c b e qn 1.5\nq2 c b e s qn\n"
                        "j1 d g s jn area=2\nm1 d g s b nm l=1u w=2u nf=2 ic=0.1,0.2,0.3\n")
        i = {x.name: x for x in nl.instances()}
        self.assertEqual((i["d1"].value, i["d1"].params), (Num(2.0), {"pj": Num(3.0)}))
        self.assertEqual(i["d2"].value, Num(4.0))
        self.assertNote(nl, "d2: OFF ignored", "warning")
        self.assertEqual((i["q1"].nodes, i["q1"].value), (["c", "b", "e"], Num(1.5)))
        self.assertEqual(i["q2"].nodes, ["c", "b", "e", "s"])
        self.assertEqual((i["j1"].nodes, i["j1"].value), (["d", "g", "s"], Num(2.0)))
        self.assertEqual(i["m1"].params, {"l": Num(P("1u")), "w": Num(P("2u")), "nf": Num(2.0)})
        self.assertNote(nl, "m1: IC=0.1,0.2,0.3 ignored", "warning")
        self.fails("* t\n.model nm nmos\nm1 d g s nm w=1u l=1u\n", "the bulk node is omitted")

    def test_subckt_instances_and_params(self):
        nl = self.parse("* t\n.subckt inv in out params: wn=1u\nr1 in out 'wn*1e9'\n.ends inv\n"
                        "x1 a b inv wn=2u M=4\nx2 a c inv params: wn=3u\n")
        x1, x2 = by_name(nl.instances(), "x1"), by_name(nl.instances(), "x2")
        self.assertEqual((x1.master, x1.nodes, x1.params), ("inv", ["a", "b"], {"wn": Num(P("2u")), "m": Num(4.0)}))
        self.assertEqual(x2.params, {"wn": Num(P("3u"))})
        self.assertEqual(nl.subckts()["inv"].params, [ir.Param("wn", Num(P("1u")), nl.subckts()["inv"].origin)])
        self.fails("* t\nx1 a b nosuch\n", "x1: subckt nosuch not found")
        self.fails("* t\n.subckt a x\nx1 x b\n.ends\n.subckt b y\nx2 y a\n.ends\n",
                   "instantiates itself")

    def test_nested_subckt_definitions(self):
        nl = self.parse("* t\n.subckt outer a\n.subckt inner b\nr1 b 0 1\n.ends inner\nx1 a inner\n"
                        ".ends outer\nx0 n outer\n")
        outer = nl.subckts()["outer"]
        self.assertEqual(sorted(nl.subckts()), ["outer"])
        inner = [s for s in outer.body if isinstance(s, ir.Subckt)][0]
        self.assertEqual(inner.name, "inner")
        self.assertEqual(by_name(outer.body, "x1").master, "inner")

    def test_verilog_a(self):
        self.write("res.va", "// a resistor\n`include \"disciplines.vams\"\nmodule MyRes(p, n);\n"
                   "inout p, n; electrical p, n; parameter real r = 1k;\n"
                   "analog I(p, n) <+ V(p, n) / r;\nendmodule\n")
        nl = self.parse("* t\n.hdl 'res.va'\nx1 a 0 myres r=2k\n.model mr myres r=3k tc=1\n"
                        "x2 a 0 mr\nx3 a 0 mr r=4k m=2\n")
        self.assertEqual(nl.hdl, [os.path.join(self.tmp, "res.va")])
        x1, x2, x3 = (by_name(nl.instances(), n) for n in ("x1", "x2", "x3"))
        self.assertEqual((x1.kind, x1.master, x1.params), ("y", "MyRes", {"r": Num(2000.0)}))
        # a card of the module is folded into the instances naming it
        self.assertEqual((x2.kind, x2.master, x2.params), ("y", "MyRes", {"r": Num(3000.0), "tc": Num(1.0)}))
        self.assertEqual(x3.params, {"r": Num(4000.0), "tc": Num(1.0), "m": Num(2.0)})
        self.assertEqual(nl.models(), {})

    def test_unsupported_letters(self):
        for line, needle in (("b1 a b buf", "IBIS"), ("s1 a b sp", "S-parameter"),
                             ("w1 a b c d n=1", "W-element"), ("t1 a 0 b 0 z0=50", "transmission line")):
            self.fails("* t\n%s\n" % line, needle)

    def test_library_subckt_with_unsupported_content_is_left_out(self):
        lib = ("* t\n.subckt varcap a b\ncg a b q='1p*v(a,b)'\n.ends\n"
               ".subckt user a b\nxv a b varcap\n.ends\n.subckt good a b\nr1 a b 1k\n.ends\n")
        nl = self.parse(lib + "x1 n 0 good\n")
        self.assertEqual(sorted(nl.subckts()), ["good"])
        self.assertEqual(sorted(spice.left_out(nl)), ["user", "varcap"])
        self.assertNote(nl, "subckt varcap is left out: cg: a charge-defined capacitor (Q=) is not "
                        "supported")
        self.assertNote(nl, "subckt user is left out: xv instantiates subckt varcap, which is left out")
        self.fails(lib + "x1 n 0 user\n", "x1: subckt user cannot be simulated by vamos")
        # a nested definition left out inside a kept parent; a bad element inside a subckt
        nl = self.parse("* t\n.subckt outer a\n.subckt inner b\nb1 b 0 buf\n.ends\nr1 a 0 1\n.ends\n"
                        ".subckt dup a\nr1 a 0 1\nr1 a 0 2\n.ends\n")
        self.assertEqual(sorted(nl.subckts()), ["outer"])
        self.assertEqual([type(i).__name__ for i in nl.subckts()["outer"].body], ["Instance"])
        self.assertNote(nl, "subckt inner is left out: b1: an IBIS I/O buffer")
        self.assertNote(nl, "subckt dup is left out: element r1 is defined twice")
        self.assertEqual(sorted(spice.left_out(nl)), ["dup"])          # top-level ones only

    def test_spelling_prefers_definitions(self):
        nl = self.parse("* t\nx1 Vdd A cell\n.subckt Cell a VDD\nr1 a vdd 1\n.ends\n")
        self.assertEqual((nl.spelling["cell"], nl.spelling["vdd"], nl.spelling["a"], nl.spelling["x1"]),
                         ("Cell", "VDD", "a", "x1"))

    def test_element_errors_at_top_level(self):
        self.fails("* t\nr1 a\n", "r1 needs 2 nodes")
        self.fails("* t\nr1 a b 1k\nr1 c d 2k\n", "element r1 is defined twice")
        self.fails("* t\nr1 a b 'v(a'\n", "r1")


# =============================================================================
# Sources (§4.3.8): every omitted and zero field
# =============================================================================

class TestSources(_Base):
    TRAN = ".tran 1n 1u\n"

    def src(self, line, tran=TRAN, **kw):
        nl = self.parse("* t\n.param tz=0 tp=2n\n%s%s\n" % (tran, line), **kw)
        return nl.instances()[0].source

    def test_pulse(self):
        s = self.src("v1 a 0 pulse(0 1)")
        self.assertEqual(s.args, {"v1": Num(0.0), "v2": Num(1.0), "td": Num(0.0), "tr": Num(1e-9),
                                  "tf": Num(1e-9), "pw": Num(1e-6)})
        s = self.src("v1 a 0 PULSE 0 1 -1n 0 'tz' 5n 20n")      # no parentheses
        self.assertEqual(s.args, {"v1": Num(0.0), "v2": Num(1.0), "td": Num(0.0), "tr": Num(1e-9),
                                  "tf": Num(1e-9), "pw": Num(P("5n")), "per": Num(P("20n"))})
        s = self.src("v1 a 0 pu(0 1 1n 0.1n 0.1n)")
        self.assertEqual((s.wave, s.args["pw"], "per" in s.args), ("pulse", Num(1e-6), False))
        s = self.src("v1 a 0 pulse(0 1 1n tp tp 5n)")
        self.assertEqual(s.args["tr"], Num(P("2n")))
        self.fails("* t\n.tran 1n 1u\nv1 a 0 pulse(0 1 0 1n 1n 5n 7n)\n",
                   "PULSE period 7e-09 is not longer than tr+tf+pw = 7e-09")
        self.fails("* t\n.tran 1n 1u\nv1 a 0 pulse(0 1 0 0 0 5n 6n)\n",
                   "PULSE period 6e-09 is not longer than tr+tf+pw = 7e-09 (after zero or omitted "
                   "edges became TSTEP = 1e-09)")
        self.fails("* t\nv1 a 0 pulse(0)\n", "PULSE takes 2 to 7 values, got 1")

    def test_pulse_edge_depends_on_subckt_parameter(self):
        nl = self.parse("* t\n.tran 1n 1u\n.subckt s a tr=0\nv1 a 0 pulse(0 1 0 tr tr)\n.ends\n")
        v1 = nl.subckts()["s"].body[0]
        self.assertEqual(v1.source.args["tr"],
                         Ternary(Binary("==", Name("tr"), Num(0.0)), Num(1e-9), Name("tr")))
        self.assertEqual(E.evaluate(v1.source.args["tr"], {"tr": 0.0}), 1e-9)
        self.assertEqual(E.evaluate(v1.source.args["tr"], {"tr": 3e-9}), 3e-9)

    def test_synthesised_step_and_stop_without_tran(self):
        s = self.src("v1 a 0 pulse(0 1)", tran="", synth_step=1e-11, synth_stop=3600.0)
        self.assertEqual((s.args["tr"], s.args["pw"]), (Num(1e-11), Num(3600.0)))

    def test_sin(self):
        s = self.src("v1 a 0 sin(0.6 0.6)")
        self.assertEqual(s.args, {"vo": Num(0.6), "va": Num(0.6), "freq": Num(1e6), "td": Num(0.0),
                                  "theta": Num(0.0), "phase": Num(0.0)})
        s = self.src("v1 a 0 SIN 0 1 1meg 1n 1e6 90 ac 1")
        self.assertEqual(s.args["phase"], Num(90.0))
        self.assertEqual(s.ac, (Num(1.0), Num(0.0)))

    def test_exp(self):
        s = self.src("v1 a 0 exp(0 1)")
        self.assertEqual(s.args, {"v1": Num(0.0), "v2": Num(1.0), "td1": Num(0.0), "tau1": Num(1e-9),
                                  "td2": Num(1e-9), "tau2": Num(1e-9)})
        s = self.src("v1 a 0 exp(0 1 5n 1n)")
        self.assertEqual(s.args["td2"], Num(P("5n") + 1e-9))
        s = self.src("v1 a 0 exp(0 1 5n 1n 20n 2n)")
        self.assertEqual((s.args["td2"], s.args["tau2"]), (Num(P("20n")), Num(P("2n"))))
        self.fails("* t\nv1 a 0 exp(0 1 5n 1n 5n 1n)\n", "EXP td2 (5e-09) must be after td1 (5e-09)")
        self.fails("* t\nv1 a 0 exp(0 1 0 0)\n", "EXP tau1 must be positive")

    def test_pwl(self):
        s = self.src("v1 a 0 pwl(0 0, 1n 1, 2n 1) td=1n")
        self.assertEqual(s.points, [(Num(0.0), Num(0.0)), (Num(1e-9), Num(1.0)), (Num(P("2n")), Num(1.0))])
        self.assertEqual(s.args, {"td": Num(1e-9)})
        s = self.src("v1 a 0 dc 0.3 pwl 1n 1 2n 0")              # HSPICE: DC is the time-zero value
        self.assertEqual(s.points[0], (Num(0.0), Num(0.3)))
        self.assertEqual(s.args, {"td": Num(0.0)})
        s = self.src("v1 a 0 pl(0 0 1 1n)")                        # value-time pairs
        self.assertEqual(s.points, [(Num(0.0), Num(0.0)), (Num(1e-9), Num(1.0))])
        self.fails("* t\nv1 a 0 pwl(0 0 1n 1 1n 2)\n", "PWL time points must increase (1e-09 then 1e-09)")
        self.fails("* t\nv1 a 0 pwl(0 0 2n 1 1n 2)\n", "PWL time points must increase")
        self.fails("* t\nv1 a 0 pwl(0 0 1n 1) r=0\n", "PWL repeat (R) is not supported")
        self.fails("* t\nv1 a 0 pwl(0 0 1n 1 R)\n", "PWL repeat (R) is not supported")
        self.fails("* t\nv1 a 0 pwl(0 0 1n)\n", "PWL needs time-value pairs")

    def test_dc_and_ac_forms(self):
        for line, dc, ac in (("v1 a 0 1.2", Num(1.2), None), ("v1 a 0 dc 1.2", Num(1.2), None),
                             ("v1 a 0 DC=vs", Name("vs"), None), ("v1 a 0", None, None),
                             ("v1 a 0 dc 0 ac 1", Num(0.0), (Num(1.0), Num(0.0))),
                             ("v1 a 0 ac=1,90", None, (Num(1.0), Num(90.0))),
                             ("v1 a 0 0.5 AC 2 45", Num(0.5), (Num(2.0), Num(45.0)))):
            nl = self.parse("* t\n.param vs=1\n%s\n" % line)
            s = nl.instances()[0].source
            self.assertEqual((s.dc, s.ac, s.wave), (dc, ac, None), line)
        nl = self.parse("* t\ni1 a 0 1m m=2\n")
        self.assertEqual(nl.instances()[0].params, {"m": Num(2.0)})
        self.fails("* t\nv1 a 0 1 m=2\n", "m= is not supported on a voltage source")
        self.fails("* t\nv1 a 0 sffm(0 1 1k)\n", "the SFFM source function is not supported")
        self.fails("* t\nv1 a 0 'v(b)'\n", "reads v(b)")


# =============================================================================
# Analyses, probes, initial conditions
# =============================================================================

class TestControls(_Base):
    def test_tran(self):
        nl = self.parse("* t\n.param tsim=3.3u\n.tran 1n 'tsim'\n.op\n.tran 2n 5n\n.ac dec 10 1 1g\n")
        self.assertEqual([(a.kind, a.args) for a in nl.analyses],
                         [("tran", {"step": 1e-9, "stop": P("3.3u"), "start": 0.0, "uic": False,
                                    "maxstep": 5e-9}),                  # only the first .tran runs
                          ("op", {}), ("tran", {"step": 2e-9, "stop": 5e-9, "start": 0.0, "uic": False}),
                          ("ac", {})])
        nl = self.parse("* t\n.tran 1n '1u/3' START=10n UIC\n.option delmax=0.1n\n")
        self.assertEqual(nl.tran().args, {"step": 1e-9, "stop": 1e-6 / 3, "start": P("10n"), "uic": True,
                                          "maxstep": P("0.1n")})
        nl = self.parse("* t\n.tran .1n 25n 1n 40n START=10n\n")
        self.assertEqual(nl.tran().args, {"step": P(".1n"), "stop": P("40n"), "start": P("10n"), "uic": False,
                                          "maxstep": 5e-10})
        self.assertNote(nl, ".tran with 2 intervals")
        self.fails("* t\n.tran 1n 100n 0 1n\n", "tstop values must increase")
        self.fails("* t\n.tran 1n 'tsim'\n", ".tran: unknown parameter 'tsim'")
        self.fails("* t\n.subckt s a\n.tran 1n 1u\n.ends\n", ".tran inside .subckt s is not supported")

    def maxstep(self, options="", tran=".tran 1n 1u"):
        nl = self.parse("* t\nr1 a 0 1k\n%s\n%s\n" % (tran, options))
        return nl.tran().args.get("maxstep"), nl

    def test_maximum_step_is_hspice_default(self):
        """Without .option delmax HSPICE bounds its internal step by min(TSTOP/50, RMAX*TSTEP),
        RMAX = 5 under its defaults DVDT=4 and LVLTIM=1, else 2 (Star-HSPICE 2001.2 manual 9-46,
        11-36).  The engines bound theirs by the run length only, which let VACASK step 0.34 ns
        across e2e 6's 0.45 ns inverter edge (crossings 20 ps late)."""
        v, nl = self.maxstep(tran=".tran 1p 60n")
        self.assertEqual(v, 5e-12)
        self.assertNote(nl, "t.sp:3: .tran: maximum time step 5e-12 s, HSPICE's bound without .option "
                        "delmax: min(TSTOP/50, RMAX*TSTEP) = min(1.2e-09, 5*1e-12) with RMAX=5, its default "
                        "under DVDT=4 and LVLTIM=1; .option delmax or --vamos-analog-maxstep sets another")
        self.assertEqual(self.maxstep()[0], 5e-9)
        self.assertEqual(self.maxstep(tran=".tran 1n 10n")[0], 2e-10)            # at least 50 steps
        self.assertEqual(self.maxstep(tran=".tran 1n 100n")[0], 2e-9)            # 1e-7/50, printed clean
        self.assertEqual(self.maxstep(tran=".tran 1n 25n .1n 40n")[0], 5e-10)    # the smallest increment
        self.assertEqual(self.maxstep(tran=".tran 1n 1u START=0.5u")[0], 5e-9)   # START plays no part
        nl = self.parse("* t\n.param tsim=3.3u ts=1n\nr1 a 0 1k\n.tran ts 'tsim'\n")
        self.assertEqual(nl.tran().args["maxstep"], 5e-9)

    def test_maximum_step_rmax_and_the_options_setting_its_default(self):
        """RMAX: the options apply in order, the last setting winning (METHOD=GEAR sets
        LVLTIM=2; ACCURATE DVDT=2 LVLTIM=3 RMAX=2; DVDT=3 LVLTIM=1 RMAX=2); RMAX never set is
        5 under DVDT=4 and LVLTIM=1, else 2."""
        for opts, want in (("", 5e-9), (".option method=gear", 2e-9),
                           (".option method=gear lvltim=1", 5e-9),       # a later LVLTIM wins
                           (".option lvltim=1 method=gear", 2e-9),
                           (".option method=trap", 5e-9),
                           (".option accurate", 2e-9), (".option accurate=0", 5e-9),
                           (".option accurate dvdt=4 lvltim=1", 2e-9),   # ACCURATE set RMAX=2 too
                           (".option dvdt=2", 2e-9), (".option dvdt=3", 2e-9), (".option dvdt=4", 5e-9),
                           (".option lvltim=3", 2e-9), (".option lvltim=2 dvdt=3", 2e-9),
                           (".option rmax=10", 1e-8), (".option rmax=10 accurate", 2e-9),
                           (".option accurate rmax=10", 1e-8), (".option rmax=10 dvdt=3", 2e-9),
                           (".option dvdt=3 rmax=10", 1e-8), (".option rmax=10 dvdt=2", 1e-8),
                           (".option rmax=100", 2e-8),                    # TSTOP/50 still bounds it
                           (".param r=3\n.option rmax=r", 3e-9)):
            self.assertEqual(self.maxstep(opts)[0], want, opts)
        self.assertNote(self.maxstep(".option rmax=10 accurate")[1], "with RMAX=2 (.option accurate);")
        self.assertNote(self.maxstep(".option dvdt=3")[1], "with RMAX=2 (.option dvdt=3);")
        v, nl = self.maxstep(".option method=gear")
        self.assertNote(nl, ".tran: maximum time step 2e-09 s, HSPICE's bound without .option delmax: "
                        "min(TSTOP/50, RMAX*TSTEP) = min(2e-08, 2*1e-09) with RMAX=2, its default under "
                        "DVDT=4 and LVLTIM=2 (.option method=gear)")
        v, nl = self.maxstep(".option rmax=10 dvdt=2")
        self.assertNote(nl, "with RMAX=10 (.option rmax);")
        self.assertNote(nl, "t.sp:4: .option dvdt: HSPICE's timestep algorithm is not modelled (the engine's "
                        "own step control applies); it counts only for RMAX, the TSTEP multiplier of the "
                        "maximum time step")
        self.assertFalse([t for t in self.texts(nl) if ".option dvdt ignored" in t])
        v, nl = self.maxstep(".option delmax=1n lvltim=3")         # with delmax RMAX plays no part
        self.assertNote(nl, ".option lvltim: HSPICE's timestep algorithm is not modelled (the engine's own "
                        "step control applies)")
        self.assertFalse([t for t in self.texts(nl) if "counts only" in t])
        self.fails("* t\n.tran 1n 1u\n.option rmax=0\n", ".option rmax=0 must be a positive number")
        self.fails("* t\n.tran 1n 1u\n.option rmax\n", ".option rmax needs a value")
        self.fails("* t\n.tran 1n 1u\n.option dvdt\n", ".option dvdt needs a value")
        self.fails("* t\n.tran 1n 1u\n.option lvltim=nosuch\n", "unknown parameter 'nosuch'")

    def test_maximum_step_from_delmax(self):
        """.option delmax is the maximum step as given (HSPICE then computes none: no TSTOP/50,
        no RMAX); it must be a positive number; without a .tran it is not applied (warning)."""
        self.assertEqual(self.maxstep(".option delmax=0.1n")[0], P("0.1n"))
        self.assertEqual(self.maxstep(".option delmax=50n")[0], P("50n"))
        v, nl = self.maxstep(".option delmax=0.1n rmax=3")
        self.assertEqual(v, P("0.1n"))
        self.assertNote(nl, "t.sp:4: .option rmax ignored: .option delmax sets the maximum time step")
        self.assertFalse([t for t in self.texts(nl) if "maximum time step 1e-10" in t])
        self.fails("* t\n.tran 1n 1u\n.option delmax\n", "t.sp:3: .option delmax needs a value")
        self.fails("* t\n.tran 1n 1u\n.option delmax=0\n", ".option delmax=0 must be a positive number")
        self.fails("* t\n.tran 1n 1u\n.option delmax=-1n\n", ".option delmax=-1n must be a positive number")
        nl = self.parse("* t\nr1 a 0 1k\n.option delmax=1n rmax=3\n")
        self.assertEqual(nl.analyses, [])
        self.assertNote(nl, "t.sp:3: .option delmax=1n ignored: the netlist has no .tran, and the analysis "
                        "vamos synthesises takes its maximum time step from --vamos-analog-maxstep", "warning")
        nl = self.parse("* t\nr1 a 0 1k\n.option rmax=3 dvdt=2\n")
        self.assertNote(nl, ".option rmax ignored: the netlist has no .tran")
        self.assertNote(nl, ".option dvdt: HSPICE's timestep algorithm is not modelled")
        self.assertFalse([t for t in self.texts(nl) if "counts only" in t or ".tran:" in t])

    def test_ic_nodeset_probes(self):
        nl = self.parse("* t\n.param vh=0.9\n.ic v(b)='vh' v(x1.n)=0.1\n.nodeset v(c)=1\n"
                        ".print tran v(out) i(v1) v(a,b)\n.probe v(*)\n.print tran vm(a) p(r1) "
                        "v(x*)\n.probe ac v(o)\n.dcvolt v(d)=2\n.print v(c d)\n")
        self.assertEqual(nl.ics, {"b": 0.9, "x1.n": 0.1, "d": 2.0})
        self.assertEqual(nl.nodesets, {"c": 1.0})
        self.assertEqual(nl.probes, [("tran", "v", "out"), ("tran", "i", "v1"), ("tran", "v", "a,b"),
                                     ("tran", "v", "*"), ("ac", "v", "o"), ("tran", "v", "c,d")])
        self.assertNote(nl, "vm(a) ignored", "warning")
        self.assertNote(nl, "v(x*) ignored (wildcards", "warning")
        self.fails("* t\n.ic v(a)=nosuch\n", "unknown parameter 'nosuch'")
        self.fails("* t\n.ic a=1\n", "write v(node)=value")

    def test_global_hdl_and_title(self):
        nl = self.parse("* t\n.global vdd gnd vss!\n.title My title\n")
        self.assertEqual(nl.globals, ["vdd", "vss!"])
        self.assertEqual(nl.title, "My title")


# =============================================================================
# The sky130 PDK (WSL: /opt/pdk/sky130A)
# =============================================================================

# Subckts of the tt corner that vamos cannot simulate: voltage-dependent
# resistors and charge-defined varactors, and the wrappers that use them.
SKY130_LEFT_OUT = {
    "sky130_fd_pr__cap_var_hvt", "sky130_fd_pr__cap_var_lvt", "sky130_fd_pr__nfet_20v0",
    "sky130_fd_pr__nfet_20v0_iso", "sky130_fd_pr__nfet_20v0_nvt", "sky130_fd_pr__nfet_20v0_reverse_iso",
    "sky130_fd_pr__nfet_20v0_zvt", "sky130_fd_pr__pfet_20v0", "sky130_fd_pr__res_high_po_0p35",
    "sky130_fd_pr__res_high_po_0p69", "sky130_fd_pr__res_high_po_1p41", "sky130_fd_pr__res_high_po_2p85",
    "sky130_fd_pr__res_high_po_5p73", "sky130_fd_pr__res_iso_pw", "sky130_fd_pr__res_xhigh_po",
    "sky130_fd_pr__res_xhigh_po_0p35", "sky130_fd_pr__res_xhigh_po_0p69", "sky130_fd_pr__res_xhigh_po_1p41",
    "sky130_fd_pr__res_xhigh_po_2p85", "sky130_fd_pr__res_xhigh_po_5p73", "sky130_fd_pr__res_xhigh_po__base",
}


@unittest.skipUnless(os.path.isfile(os.path.join(SKY130, "sky130.lib.spice")),
                     "needs the sky130 PDK (%s)" % SKY130)
class TestSky130(_Base):
    def test_tt_corner(self):
        nl = self.parse("* sky130 tt\n.lib '%s' tt\nxm1 d g 0 0 sky130_fd_pr__nfet_01v8 w=1 l=0.15\n"
                        "vd d 0 1.8\nvg g 0 1.8\n.tran 1n 10n\n"
                        % os.path.join(SKY130, "sky130.lib.spice"))
        self.assertNote(nl, ".tran: maximum time step 2e-10 s")
        notes = [t for t in self.texts(nl) if ".tran: maximum time step" not in t]
        self.assertFalse([t for t in notes if "defined again" in t or "already read" in t], notes)
        self.assertEqual(set(spice.left_out(nl)), SKY130_LEFT_OUT)
        self.assertEqual(len(notes), len(SKY130_LEFT_OUT))         # one note each, nothing else
        self.assertEqual(nl.scale(), 1e-6)                          # all.spice: .option scale=1.0u
        self.assertEqual(nl.values["mc_mm_switch"], 0.0)
        nf = nl.subckts()["sky130_fd_pr__nfet_01v8"]
        self.assertEqual(nf.ports, ["d", "g", "s", "b"])
        bins = [m for m in nf.body if isinstance(m, ir.Model)]
        self.assertEqual(len(bins), 180)
        self.assertEqual({m.base for m in bins}, {"sky130_fd_pr__nfet_01v8__model"})
        self.assertEqual(sorted(m.bin_index for m in bins), list(range(180)))
        self.assertTrue(all(m.kind == "nmos" and m.level == 54.0 for m in bins))
        m = by_name(nf.body, "msky130_fd_pr__nfet_01v8")
        self.assertEqual(m.master, "sky130_fd_pr__nfet_01v8__model")
        # the legacy wrapper names its subckt after the parameters
        sp = nl.subckts()["sky130_fd_pr__special_pfet_pass"]
        self.assertEqual(by_name(sp.body, "xsky130_fd_pr__special_pfet_pass").master,
                         "sky130_fd_pr__special_pfet_latch")
        self.assertEqual(by_name(nl.instances(), "xm1").nodes, ["d", "g", "0", "0"])


if __name__ == "__main__":
    unittest.main()
