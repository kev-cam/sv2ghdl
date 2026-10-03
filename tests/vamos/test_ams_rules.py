"""Interface-element rules: vamos/ams/rules.py (docs/VAMOS_AMS_DESIGN.md §3).

    python3 -m unittest discover -s tests/vamos -p 'test_ams_rules.py' -v

Node names below are what cut.py hands over (§3.1): the canonical name first,
then every cut-port spelling and every parent net the trace walked through.
"""

import unittest

from vamos_testlib import TempDir  # noqa: F401  (also puts ROOT on sys.path)

from vamos.ams import initfile, rules  # noqa: E402
from vamos.ams.config import TNF, IeRule  # noqa: E402
from vamos.ams.model import (A2D, BIDIR, D2A, DISABLED, POWERNET, REMOVED, THROUGH, A2D_IE,  # noqa: E402
                             D2A_IE, RuleHits)
from vamos.notes import ERROR, WARNING, has_errors, warning  # noqa: E402

VREF = (0.0, 1.8, None)
FALLBACK = warning("tb.u.a", "no supply traced: the 3.3 V VCS fallback is used")
VREF33 = (0.0, 3.3, FALLBACK)


def missing(_name):
    return ("missing", None)


def table(values):
    """dc_of over a dict: volts, or 'dynamic'."""
    def dc_of(name):
        v = values.get(name)
        if v is None:
            return ("missing", None)
        if v == "dynamic":
            return ("dynamic", None)
        return ("const", v)
    return dc_of


class Base(TempDir):
    def cfg(self, text, ini=None):
        before = []
        if ini is not None:
            self.write(initfile.INI_NAME, ini)
            before = [initfile.INI_NAME]
        self.write("vcsAD.init", "choose xa a.sp;\n" + text)
        cfg = initfile.parse_control(before + ["vcsAD.init"], self.tmp, {})
        self.assertFalse(has_errors(cfg.notes), [n.text() for n in cfg.notes])
        return cfg

    def d2a(self, cfg, names, ports=(), vref=VREF, dc_of=missing, hits=None, role=None):
        return rules.resolve(cfg, list(names), list(ports), vref, dc_of, hits or RuleHits(), role)[0]

    def a2d(self, cfg, names, ports=(), vref=VREF, dc_of=missing, hits=None, role=None):
        return rules.resolve(cfg, list(names), list(ports), vref, dc_of, hits or RuleHits(), role)[1]


class TestValues(unittest.TestCase):
    def test_levels_and_times(self):
        self.assertEqual(rules.parse_level("1.2"), (1.2, False))
        self.assertEqual(rules.parse_level("1.2V"), (1.2, False))
        self.assertAlmostEqual(rules.parse_level("500mV")[0], 0.5)
        self.assertEqual(rules.parse_level("90%"), (0.9, True))
        self.assertEqual(rules.parse_level("-0.5"), (-0.5, False))
        self.assertAlmostEqual(rules.parse_time("1.5n"), 1.5e-9)
        self.assertAlmostEqual(rules.parse_time("10ns"), 1e-8)
        for bad in ("abc", "", "1.2.3"):
            with self.assertRaises(ValueError):
                rules.parse_level(bad)
        with self.assertRaises(ValueError):
            rules.parse_time("10%")

    def test_check_rule_on_a_hand_built_rule(self):
        self.assertEqual(rules.check_rule(IeRule("d2a", {"hiv": "1", "rf_time": "2n"}, node="x")), [])
        self.assertTrue(has_errors(rules.check_rule(IeRule("e2r", {}, node="x"))))
        self.assertEqual(rules.selector_text(IeRule("a2d", cell="c", port="p")), "cell=c port=p")


class TestSelectors(Base):
    def test_vcs_globs_brackets_are_literal(self):
        cfg = self.cfg("d2a hiv=1.0 node=tb.dut.din[3];\n")
        self.assertEqual(self.d2a(cfg, ["tb.dut.din<3>", "tb.dut.din[3]", "tb.dut.din"]).hiv, 1.0)
        # fnmatch would read [3] as a character class and match din3
        self.assertEqual(self.d2a(cfg, ["tb.dut.din3"]).hiv, 1.8)
        self.assertEqual(self.d2a(cfg, ["TB.DUT.DIN[3]"]).hiv, 1.0)
        cfg = self.cfg("d2a hiv=1.0 node=tb.*.din<3>;\n")
        self.assertEqual(self.d2a(cfg, ["tb.a.b.din<3>"]).hiv, 1.0)
        # a pattern with '*' matches the canonical name only, never an alias
        self.assertEqual(self.d2a(cfg, ["tb.a.b.din_3", "tb.a.b.din<3>"]).hiv, 1.8)

    def test_per_bit_rules_in_three_spellings(self):
        cfg = self.cfg("bus_format <%d> _%d;\n"
                       "d2a hiv=1.1 node=tb.dut.din<1>;\n"         # SPICE <%d>
                       "d2a lov=0.1 node=tb.dut.din[1];\n"         # Verilog bit
                       "d2a rf_time=2n node=tb.dut.din;\n"         # the bare bus: every bit
                       "d2a hiv=1.3 node=tb.dut.q_1;\n")           # SPICE _%d
        hits = RuleHits()
        b1 = self.d2a(cfg, ["tb.dut.din<1>", "tb.dut.din[1]", "tb.dut.din"], hits=hits)
        self.assertEqual((b1.hiv, b1.lov, b1.rise, b1.fall), (1.1, 0.1, 2e-9, 2e-9))
        b0 = self.d2a(cfg, ["tb.dut.din<0>", "tb.dut.din[0]", "tb.dut.din"], hits=hits)
        self.assertEqual((b0.hiv, b0.lov, b0.rise), (1.8, 0.0, 2e-9))
        q1 = self.d2a(cfg, ["tb.dut.q_1", "tb.dut.q[1]", "tb.dut.q"], hits=hits)
        self.assertEqual(q1.hiv, 1.3)
        self.assertEqual(rules.unmatched(cfg, hits), [])

    def test_port_in_three_spellings(self):
        cfg = self.cfg("a2d loth=0.1 hith=0.2 cell=dac port=d<1>;\n"
                       "a2d loth=0.3 hith=0.4 cell=dac port=d[0];\n"
                       "a2d xband=2 midv_time=1n cell=da* port=d;\n"
                       "a2d midv_logic=0 midv_time=2n inst=tb.u* port=d*;\n")
        hits = RuleHits()
        p1 = ("dac", "tb.u1", "d<1>", "d[1]", "d")
        p0 = ("dac", "tb.u1", "d<0>", "d[0]", "d")
        a1 = self.a2d(cfg, ["tb.u1.d<1>"], [p1], hits=hits)
        self.assertEqual((a1.loth, a1.hith, a1.xband, a1.midv_time, a1.midv_logic), (0.1, 0.2, 2.0, 2e-9, "0"))
        a0 = self.a2d(cfg, ["tb.u1.d<0>"], [p0], hits=hits)
        self.assertEqual((a0.loth, a0.hith, a0.xband), (0.3, 0.4, 2.0))
        other = self.a2d(cfg, ["tb.w.q"], [("adc", "tb.w", "q", "q", "q")], hits=hits)
        self.assertEqual((other.loth, other.hith, other.xband), (0.9, 0.9, None))
        self.assertEqual(rules.unmatched(cfg, hits), [])

    def test_cell_and_inst_match_any_cut_port_on_the_net(self):
        cfg = self.cfg("d2a hiv=1.0 inst=tb.b port=in;\n")
        ports = [("buf", "tb.a", "in", "in", "in"), ("buf", "tb.b", "in", "in", "in")]
        self.assertEqual(self.d2a(cfg, ["tb.a.in", "tb.b.in"], ports).hiv, 1.0)

    def test_except_port(self):
        # PAMS p191: a later port=* rule that leaves VDDV12DIG alone
        cfg = self.cfg("a2d loth=0.4 hith=0.8 cell=F32 port=VDDV12DIG;\n"
                       "a2d loth=20% hith=80% cell=F32 port=* except_port=VDDV*;\n")
        a = self.a2d(cfg, ["tb.f.vddv12dig"], [("F32", "tb.f", "vddv12dig", "VDDV12DIG", "VDDV12DIG")])
        self.assertEqual((a.loth, a.hith), (0.4, 0.8))
        a = self.a2d(cfg, ["tb.f.a"], [("F32", "tb.f", "a", "a", "a")])
        self.assertAlmostEqual(a.loth, 0.36)
        self.assertAlmostEqual(a.hith, 1.44)

    def test_case(self):
        cfg = self.cfg("d2a hiv=1.0 node=TB.U.A;\n")
        self.assertEqual(self.d2a(cfg, ["tb.u.a"]).hiv, 1.0)
        cfg.xa["case"] = "sensitive"                        # set_sim_case -case sensitive
        self.assertEqual(self.d2a(cfg, ["tb.u.a"]).hiv, 1.8)
        self.assertEqual(self.d2a(cfg, ["TB.U.A"]).hiv, 1.0)


class TestAliases(Base):
    def test_powernet_on_the_verilog_net(self):
        # PAMS p25 Method #2a: the rule names the Verilog net, not the SPICE port
        cfg = self.cfg("d2a powernet hiv=1.2 lov=0 node=top.vdd;\n")
        hits = RuleHits()
        d, _a, notes = rules.resolve(cfg, ["top.i1.vdd", "top.vdd"], [("inv", "top.i1", "vdd", "vdd", "vdd")],
                                     VREF33, missing, hits, POWERNET)
        self.assertEqual((d.powernet, d.hiv, d.lov), (True, 1.2, 0.0))
        self.assertEqual(notes, [])                          # absolute levels: the fallback was not used
        self.assertEqual(rules.unmatched(cfg, hits), [])

    def test_bit_select_output(self):
        # cut output -> top.s[0] through a bit-select (cut.py sees through the LPM temporary)
        cfg = self.cfg("a2d loth=0.4 hith=1.0 node=top.s[0];\n")
        hits = RuleHits()
        a0 = self.a2d(cfg, ["top.u0.y", "top.s[0]", "top.s"], hits=hits)
        a1 = self.a2d(cfg, ["top.u1.y", "top.s[1]", "top.s"], hits=hits)
        self.assertEqual(((a0.loth, a0.hith), (a1.loth, a1.hith)), ((0.4, 1.0), (0.9, 0.9)))
        self.assertEqual(rules.unmatched(cfg, hits), [])

    def test_part_select_input(self):
        # .d(code[3:2]) on a d[1:0] input: d[0] is code[2], d[1] is code[3]
        cfg = self.cfg("d2a hiv=1.0 node=tb.code[3];\nd2a lov=0.2 node=tb.code;\n")
        b0 = self.d2a(cfg, ["tb.u.d_0", "tb.u.d[0]", "tb.u.d", "tb.code[2]", "tb.code"])
        b1 = self.d2a(cfg, ["tb.u.d_1", "tb.u.d[1]", "tb.u.d", "tb.code[3]", "tb.code"])
        self.assertEqual(((b0.hiv, b0.lov), (b1.hiv, b1.lov)), ((1.8, 0.2), (1.0, 0.2)))

    def test_vdd_names_a_verilog_net(self):
        cfg = self.cfg("d2a hiv=100% lov=0% vdd=tb.vddq node=tb.u.a;\n"
                       "a2d hith=50% loth=50% vdd=tb.vddq node=tb.u.y;\n"
                       "d2a hiv=90% lov=10% vdd=tb.vdd vss=tb.vss node=tb.u.b;\n")
        dc = table({"tb.vddq": 1.2, "tb.vdd": 2.0, "tb.vss": 0.5})
        hits = RuleHits()
        d, _a, notes = rules.resolve(cfg, ["tb.u.a"], [], VREF33, dc, hits, D2A)
        self.assertEqual((d.hiv, d.lov, notes), (1.2, 0.0, []))
        _d, a, notes = rules.resolve(cfg, ["tb.u.y"], [], VREF33, dc, hits, A2D)
        self.assertEqual((a.loth, a.hith, notes), (0.6, 0.6, []))
        d = self.d2a(cfg, ["tb.u.b"], dc_of=dc, hits=hits, role=D2A)
        self.assertAlmostEqual(d.hiv, 1.85)
        self.assertAlmostEqual(d.lov, 0.65)
        self.assertTrue(all(hits.hit(k) for k in ("vdd#0", "vdd#1", "vdd#2", "vss#2")))
        self.assertEqual(rules.unmatched(cfg, hits), [])

    def test_vdd_port(self):
        cfg = self.cfg("a2d hith=80% loth=20% cell=inva port=a1 vdd_port=vdda vss_port=../vssa;\n")
        ports = [("inva", "top.x1", "a1", "a1", "a1")]
        hits = RuleHits()
        a = self.a2d(cfg, ["top.x1.a1"], ports, dc_of=table({"top.x1.vdda": 2.0, "top.vssa": 0.5}), hits=hits)
        self.assertAlmostEqual(a.loth, 0.8)
        self.assertAlmostEqual(a.hith, 1.7)
        self.assertEqual(rules.unmatched(cfg, hits), [])
        hits = RuleHits()
        a = self.a2d(cfg, ["top.x1.a1"], ports, dc_of=table({"top.vssa": 0.5}), hits=hits)
        self.assertEqual((a.loth, a.hith), (0.9, 0.9))       # the rule is ignored, as VCS does
        (n,) = rules.unmatched(cfg, hits)
        self.assertIn('"vdd_port=vdda"', n.message)


class TestMerging(Base):
    def test_pams_p176_aliases_combine(self):
        cfg = self.cfg("d2a hiv=3.1v node=top.ia1.in;\nd2a lov=0.1v node=top.rst;\n"
                       "d2a rf_time=1.5n node=top.ia2.in;\n")
        names = ["top.ia1.in", "top.ia2.in", "top.rst", "top.ia3.in"]
        r = rules.resolve_detail(cfg, names, [], VREF, missing, RuleHits())
        t = rules.parse_time("1.5n")                         # 1.5 * 1e-9, as numbers.parse_number gives it
        self.assertEqual((r.d2a.hiv, r.d2a.lov, r.d2a.rise, r.d2a.fall), (3.1, 0.1, t, t))
        self.assertEqual(r.rules, [0, 1, 2])
        self.assertEqual(r.aliases, ["top.rst", "top.ia2.in"])
        self.assertEqual((r.origins["d2a.hiv"], r.origins["d2a.lov"], r.origins["d2a.rise_time"]),
                         ("vcsAD.init:2", "vcsAD.init:3", "vcsAD.init:4"))
        cfg = self.cfg("d2a hiv=3.1v node=top.ia1.in;\nd2a lov=0.1v node=top.rst;\n"
                       "d2a hiv=2.5 node=top.ia2.in;\n")
        self.assertEqual(self.d2a(cfg, names).hiv, 2.5)     # later rules override key by key

    def test_ini_first(self):
        cfg = self.cfg("a2d loth=1.35v hith=1.35v node=top.s[0];\n",
                       ini="a2d loth=1.65v hith=1.65v node=top.s[0];\na2d xband=3 midv_time=1n node=top.s;\n")
        a = self.a2d(cfg, ["top.u.y", "top.s[0]", "top.s"])
        self.assertEqual((a.loth, a.hith, a.xband), (1.35, 1.35, 3.0))

    def test_rf_time_and_delay_take_their_place_in_file_order(self):
        cfg = self.cfg("d2a rf_time=1n node=x;\nd2a rise_time=2n node=x;\n")
        d = self.d2a(cfg, ["x"])
        self.assertEqual((d.rise, d.fall), (2e-9, 1e-9))
        cfg = self.cfg("d2a rise_time=2n node=x;\nd2a rf_time=1n node=x;\n")
        d = self.d2a(cfg, ["x"])
        self.assertEqual((d.rise, d.fall), (1e-9, 1e-9))
        cfg = self.cfg("d2a delay=1n node=x;\nd2a fall_delay=3n node=x;\n")
        d = self.d2a(cfg, ["x"])
        self.assertEqual((d.delay_rise, d.delay_fall), (1e-9, rules.parse_time("3n")))

    def test_values(self):
        cfg = self.cfg("d2a rf_time=0 node=a;\nd2a delay=1n x2v=3 node=b;\n"
                       "d2a rise_delay=1n fall_delay=2n x2v=4 rise_time=5p fall_time=7p node=c;\n")
        a, b, c = (self.d2a(cfg, [n]) for n in "abc")
        self.assertEqual((a.rise, a.fall), (1e-15, 1e-15))   # clamped to 1 fs (a note at parse time)
        self.assertEqual((b.delay_rise, b.delay_fall, b.x2v, b.rise), (1e-9, 1e-9, 3, 1e-11))
        self.assertEqual((c.delay_rise, c.delay_fall, c.x2v, c.rise, c.fall), (1e-9, 2e-9, 4, 5e-12, 7e-12))

    def test_defaults(self):
        cfg = self.cfg("")
        d, a, notes = rules.resolve(cfg, ["x"], [], (0.2, 1.4, None), missing, RuleHits())
        self.assertEqual(d, D2A_IE(1.4, 0.2))
        self.assertEqual(a, A2D_IE(0.8, 0.8))
        self.assertEqual(notes, [])

    def test_midv_and_xband_window(self):
        cfg = self.cfg("a2d loth=0.6 hith=1.2 xband=4 midv_time=5n midv_logic=z node=a;\n")
        a = self.a2d(cfg, ["a"])
        self.assertEqual((a.xband, a.midv_time, a.midv_logic), (4.0, 5e-9, "Z"))  # Z releases the net
        (fall, rise) = rules.midv_windows(a)
        self.assertAlmostEqual(fall[0], 0.6)
        self.assertAlmostEqual(fall[1], 1.05)                # hith_hys = hith - (hith-loth)/xband
        self.assertAlmostEqual(rise[0], 0.75)                # loth_hys = loth + (hith-loth)/xband
        self.assertAlmostEqual(rise[1], 1.2)
        self.assertEqual(rules.midv_windows(A2D_IE(0.6, 1.2)), ((0.6, 1.2), (0.6, 1.2)))

    def test_loth_above_hith(self):
        cfg = self.cfg("a2d loth=1.2 node=a;\n")
        _d, a, notes = rules.resolve(cfg, ["a"], [], VREF, missing, RuleHits())
        (n,) = notes
        self.assertEqual((n.severity, n.origin), (ERROR, "vcsAD.init:2"))
        self.assertIn("loth=1.2 V is above hith=0.9 V", n.message)


class TestReference(Base):
    def test_fallback_warning_only_when_used(self):
        cfg = self.cfg("d2a hiv=1.2 lov=0 node=tb.u.a;\na2d loth=0.4 hith=0.8 node=tb.u.a;\n"
                       "d2a hiv=50% node=tb.u.b;\n")

        def notes(name, role):
            return rules.resolve(cfg, [name], [], VREF33, missing, RuleHits(), role)[2]

        for role in (D2A, A2D, BIDIR, None):
            self.assertEqual(notes("tb.u.a", role), [], role)
        self.assertEqual(notes("tb.u.c", D2A), [FALLBACK])  # default hiv = vdd
        self.assertEqual(notes("tb.u.c", A2D), [FALLBACK])  # default thresholds: 50 % of the span
        self.assertEqual(notes("tb.u.b", D2A), [FALLBACK])  # a % value
        r = rules.resolve_detail(cfg, ["tb.u.b"], [], VREF33, missing, RuleHits(), D2A)
        self.assertEqual((r.d2a.hiv, r.used_ref), (1.65, True))

    def test_dynamic_supply_is_an_error_once(self):
        cfg = self.cfg("d2a hiv=100% vdd=tb.vramp node=tb.u*;\n")
        dc = table({"tb.vramp": "dynamic"})
        hits = RuleHits()
        d1, _a, n1 = rules.resolve(cfg, ["tb.u1.a"], [], VREF, dc, hits, D2A)
        d2, _a, n2 = rules.resolve(cfg, ["tb.u2.a"], [], VREF, dc, hits, D2A)
        self.assertEqual([(n.severity, n.origin) for n in n1], [(ERROR, "vcsAD.init:2")])
        self.assertIn("vdd=tb.vramp is not a constant supply", n1[0].message)
        self.assertEqual(n2, [])
        self.assertEqual((d1.hiv, d2.hiv), (1.8, 1.8))
        self.assertEqual(rules.unmatched(cfg, hits), [])   # the supply exists: no TNF

    def test_ref_nodes(self):
        cfg = self.cfg("ie_reference_voltage node=A1 voltage=1.5;\nie_reference_voltage skip_node=vss;\n"
                       "ie_reference_voltage node=A2;\n")
        self.assertEqual(rules.ref_nodes(cfg), ([("A1", 1.5, 0), ("A2", None, 2)], ["vss"]))


class TestRemoveDisable(Base):
    def test_removal_last_match_wins(self):
        cfg = self.cfg("remove_d2a dc=2.5 node=top.V* ;\nremove_d2a node=top.Vref;\n")
        hits = RuleHits()
        self.assertEqual(rules.removal(cfg, ["top.Vref"], hits), (True, None))
        self.assertEqual(rules.removal(cfg, ["top.Vdd"], hits), (True, 2.5))
        self.assertEqual(rules.removal(cfg, ["top.u.a", "top.Vfoo"], hits), (False, None))
        self.assertEqual(rules.removal(cfg, ["top.u.b", "top.Vref"], hits), (True, None))
        self.assertEqual((hits.counts["remove_d2a#0"], hits.counts["remove_d2a#1"]), (2, 2))

    def test_disabled(self):
        cfg = self.cfg("disable_ie node=top.i1.outa;\ndisable_ie node=top.d*;\n")
        hits = RuleHits()
        self.assertTrue(rules.disabled(cfg, ["top.u.y", "top.i1.outa"], hits))
        self.assertTrue(rules.disabled(cfg, ["top.din"], hits))
        self.assertFalse(rules.disabled(cfg, ["top.q"], hits))
        self.assertEqual(rules.unmatched(cfg, hits), [])


class TestTnf(Base):
    def run_nodes(self, cfg, hits, *nodes):
        for names in nodes:
            rules.resolve(cfg, list(names), [], VREF, missing, hits)

    def test_unmatched_rule_is_an_error_with_nearest_names(self):
        cfg = self.cfg("d2a hiv=1.8 lov=0.0 node=top.fail;\nd2a hiv=1 node=top.dut.a_3;\n")
        hits = RuleHits()
        self.run_nodes(cfg, hits, ["top.dut.a_3"], ["top.dut.a_2"], ["top.fil"])
        (n,) = rules.unmatched(cfg, hits)
        self.assertEqual((n.severity, n.origin), (ERROR, "vcsAD.init:2"))
        self.assertIn("[%s]" % TNF, n.message)
        self.assertIn('"d2a" command, option target "node=top.fail" was not found', n.message)
        self.assertIn("nearest interface-element names: top.fil", n.message)
        # explicit candidates instead of the recorded ones
        (n,) = rules.unmatched(cfg, hits, names=["top.fall"])
        self.assertIn("nearest interface-element names: top.fall", n.message)

    def test_downgrade_and_upgrade(self):
        cfg = self.cfg("downgrade_to_warn MSV-IE-OPT-TNF;\nd2a hiv=1.8 node=top.fail;\n")
        (n,) = rules.unmatched(cfg, RuleHits())
        self.assertEqual(n.severity, WARNING)
        self.assertIn("there are no interface-element names", n.message)
        cfg = self.cfg("downgrade_to_warn MSV-IE-OPT-TNF;\nd2a hiv=1.8 node=top.fail;\n"
                       "upgrade_to_error MSV-IE-OPT-TNF;\n")
        (n,) = rules.unmatched(cfg, RuleHits())
        self.assertEqual(n.severity, ERROR)

    def test_every_selector_kind(self):
        cfg = self.cfg("remove_d2a node=top.nope;\ndisable_ie node=top.nada;\n"
                       "ie_reference_voltage node=n1 voltage=1;\nie_reference_voltage skip_node=s1;\n"
                       "ie_reference_voltage node=n2;\n"
                       "use_spice -cell c -inst top.a top.b;\n"
                       "port_connect -cell c -inst top.a (vdd => vdd);\nport_connect -cell c (vss => vss);\n"
                       "a2d hith=80% vdd=top.missing node=top.y;\n"
                       "a2d hith=70% cell=nocell port=*;\n")
        hits = RuleHits()
        self.assertEqual(rules.removal(cfg, ["top.y"], hits), (False, None))
        self.assertFalse(rules.disabled(cfg, ["top.y"], hits))
        a = self.a2d(cfg, ["top.y"], hits=hits)
        self.assertEqual(a.hith, 0.9)                        # the rule with a missing vdd= is ignored
        hits.mark("ref_voltage#2")                           # deck.py's trace found n2
        hits.mark("use_spice_inst#0.1")                      # cut.py found top.b
        got = [(n.origin, n.message.split(" option target ")[1].split('"')[1])
               for n in rules.unmatched(cfg, hits)]
        self.assertEqual(got, [("vcsAD.init:10", "vdd=top.missing"), ("vcsAD.init:11", "cell=nocell port=*"),
                               ("vcsAD.init:2", "node=top.nope"), ("vcsAD.init:3", "node=top.nada"),
                               ("vcsAD.init:4", "node=n1"), ("vcsAD.init:7", "-inst top.a"),
                               ("vcsAD.init:8", "-inst top.a")])

    def test_nearest_cells_and_instances(self):
        cfg = self.cfg("a2d hith=0.7 cell=adc_1b port=*;\nd2a hiv=1 inst=tb.dut.u2 port=a;\n")
        hits = RuleHits()
        rules.resolve(cfg, ["tb.dut.u1.a"], [("ams_adc_1b", "tb.dut.u1", "a", "a", "a")], VREF, missing, hits)
        cell_tnf, inst_tnf = rules.unmatched(cfg, hits)
        self.assertIn("nearest cells: ams_adc_1b", cell_tnf.message)
        self.assertIn("nearest instances: tb.dut.u1", inst_tnf.message)

    def test_role_filter(self):
        cfg = self.cfg("d2a hiv=1.0 node=tb.u.y;\n")
        hits = RuleHits()
        self.assertEqual(self.d2a(cfg, ["tb.u.y"], hits=hits, role=A2D).hiv, 1.8)
        (n,) = rules.unmatched(cfg, hits)                    # a d2a rule on an A2D-only node
        self.assertIn("node=tb.u.y", n.message)
        hits = RuleHits()
        self.assertEqual(self.d2a(cfg, ["tb.u.y"], hits=hits, role=BIDIR).hiv, 1.0)
        self.assertEqual(rules.unmatched(cfg, hits), [])
        hits = RuleHits()
        self.d2a(cfg, ["tb.u.y"], hits=hits, role=THROUGH)
        self.assertFalse(hits.hit("rule#0"))
        self.assertEqual(rules.kinds_of(POWERNET), ("d2a",))
        self.assertEqual(rules.kinds_of(BIDIR), ("a2d", "d2a"))

    def test_disabled_and_removed_nodes_mark_but_apply_nothing(self):
        # (hiv as a percentage: an absolute level beside vdd= is an error, PAMS p207)
        cfg = self.cfg("d2a hiv=100% vdd=tb.v node=tb.u.a;\na2d hith=0.3 node=tb.u.a;\n")
        for role in (DISABLED, REMOVED):
            hits = RuleHits()
            r = rules.resolve_detail(cfg, ["tb.u.a"], [], VREF, table({"tb.v": "dynamic"}), hits, role)
            self.assertEqual((r.d2a.hiv, r.a2d.hith, r.rules, r.notes), (1.8, 0.9, [], []))
            self.assertEqual(rules.unmatched(cfg, hits), [], role)


class TestPasteBack(Base):
    """Every report line pasted back into a control file selects the same node
    with the same levels and no TNF (§9)."""

    def test_round_trip(self):
        cfg = self.cfg("d2a hiv=90% lov=0.1 rise_time=20p fall_time=30p delay=1n x2v=4 node=tb.adc.sel<1>;\n"
                       "a2d loth=30% hith=70% xband=3 midv_time=2n midv_logic=1 node=tb.code[1];\n"
                       "d2a powernet hiv=1.2 node=top.vdd;\n"
                       "d2a rise_delay=1n fall_delay=2p node=tb.b;\n")
        names = ["tb.adc.sel<1>", "tb.adc.sel[1]", "tb.adc.sel", "tb.code[1]", "tb.code"]
        d, a, _ = rules.resolve(cfg, names, [], VREF, missing, RuleHits(), BIDIR)
        p = self.d2a(cfg, ["top.i1.vdd", "top.vdd"], role=POWERNET)
        b = self.d2a(cfg, ["tb.b"], role=D2A)
        lines = [rules.paste_line("d2a", names[0], d), rules.paste_line("a2d", names[0], a),
                 rules.paste_line("d2a", "top.i1.vdd", p), rules.paste_line("d2a", "tb.b", b)]
        self.assertEqual(lines[0], "d2a hiv=1.62 lov=0.1 rise_time=2e-11 fall_time=3e-11 delay=1e-09 x2v=4 "
                                   "node=tb.adc.sel<1>;")
        self.assertEqual(lines[2], "d2a powernet hiv=1.2 lov=0.0 rf_time=1e-11 x2v=0 node=top.i1.vdd;")
        # the old report.py-style lines (x2v always, midv_logic with midv_time) parse the same way
        lines += ["d2a hiv=1.8 lov=0.0 rf_time=1e-11 x2v=0 node=tb.u.a;",
                  "a2d loth=0.6 hith=1.2 midv_time=5e-09 midv_logic=X node=tb.u.y;"]
        cfg2 = self.cfg("\n".join(lines) + "\n")
        self.assertEqual(cfg2.notes, [])
        hits = RuleHits()
        d2, a2, notes = rules.resolve(cfg2, names, [], VREF33, missing, hits, BIDIR)
        self.assertEqual((d2, a2, notes), (d, a, []))        # levels come from the line, not the reference
        self.assertEqual(self.d2a(cfg2, ["top.i1.vdd", "top.vdd"], vref=VREF33, hits=hits, role=POWERNET), p)
        self.assertEqual(self.d2a(cfg2, ["tb.b"], vref=VREF33, hits=hits, role=D2A), b)
        self.assertEqual(self.d2a(cfg2, ["tb.u.a"], vref=VREF33, hits=hits, role=D2A), D2A_IE(1.8, 0.0))
        self.assertEqual(self.a2d(cfg2, ["tb.u.y"], vref=VREF33, hits=hits, role=A2D),
                         A2D_IE(0.6, 1.2, midv_time=5e-9))
        self.assertEqual(rules.unmatched(cfg2, hits), [])

    def test_paste_line_kinds(self):
        with self.assertRaises(ValueError):
            rules.paste_line("e2r", "x", D2A_IE(1.0, 0.0))
        self.assertEqual(rules.paste_line("a2d", "tb.y", A2D_IE(0.9, 0.9)), "a2d loth=0.9 hith=0.9 node=tb.y;")


if __name__ == "__main__":
    unittest.main()
