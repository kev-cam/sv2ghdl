"""Phase-0 contract tests: names, port maps, the Job ams record, option names.

    python3 -m unittest discover -s tests/vamos -p 'test_ams_contract.py' -v
"""

import json
import unittest

from vamos_testlib import TempDir  # noqa: F401  (also puts ROOT on sys.path)

from vamos import notes  # noqa: E402
from vamos.ams import names, portmap  # noqa: E402
from vamos.ams.model import AUTO, INPUT, LOGIC, CutCell, CutPort, PortMap  # noqa: E402
from vamos.job import Job  # noqa: E402
from vamos.optable import Opt, Table  # noqa: E402


class TestNames(unittest.TestCase):
    def test_node_and_derived_names_do_not_collide(self):
        a = names.NameAllocator(["n_dut_a_d"])         # a user node named like a derived one
        n = names.node(a, "tb.dut.a")
        self.assertEqual(n, "n_dut_a_1")
        self.assertFalse(a.free("n_dut_a_1_d"))
        self.assertFalse(a.free("n_dut_a_1_e"))

    def test_bit_vs_scalar(self):
        a = names.NameAllocator()
        n1 = names.node(a, "tb.u.a_1")                 # scalar port a_1
        n2 = names.node(a, "tb.u.a[1]")                # bit 1 of a
        self.assertNotEqual(n1, n2)

    def test_bridge_base_charset_and_hash(self):
        self.assertEqual(names.bridge_base("tb.dut.din<3>"), "tb.dut.din<3>")
        self.assertEqual(names.bridge_base("tb.g[0].u.a b"), "tb.g[0].u.a_b")
        long = "tb." + "x" * 300
        b = names.bridge_base(long)
        self.assertLessEqual(len(b), names.MAX_BRIDGE)
        self.assertNotEqual(b, names.bridge_base(long + "y"))
        self.assertEqual(names.bridge(b, "en"), b + "__e")

    def test_paths(self):
        self.assertEqual(names.boundary_path(["w1", "u3"], names.vb_signal(2, 0, "a")), ".w1.u3.vb2_0_a")
        self.assertEqual(names.path_name("TB", ["W1", "u3"]), ":tb:w1:u3:")


class TestGlobs(unittest.TestCase):
    def test_only_star_is_special(self):
        from vamos.ams import globs
        self.assertTrue(globs.match("tb.dut.din[3]", "tb.dut.din[3]"))
        self.assertTrue(globs.match("tb.dut.din[3]", "TB.DUT.DIN[3]"))
        self.assertFalse(globs.match("tb.dut.din[3]", "tb.dut.din3"))
        self.assertTrue(globs.match("top.V*", "top.vref"))
        self.assertTrue(globs.match("tb.*.a<1>", "tb.x.y.a<1>"))
        self.assertFalse(globs.match("tb.?", "tb.a"))


class TestBridges(unittest.TestCase):
    def test_build_bridges_and_lines(self):
        from vamos.ams import model as m
        cell = m.CutCell("inv_sp", "spice", "inv_sp",
                         [m.CutPort(0, "a", m.LOGIC, m.INPUT, m.INPUT), m.CutPort(1, "y", m.LOGIC, m.OUTPUT, m.OUTPUT),
                          m.CutPort(2, "pad", m.LOGIC, m.INOUT, m.INOUT)])
        inst = m.CutInstance(["w1", "u3"], "tb.w1.u3", ":tb:w1:u3:", "inv_sp", "inv_sp__d631", "inv_sp")
        ana = m.CutAnalysis("tb", {"inv_sp": cell}, [inst])
        nodes = [m.AnalogNode("n_w1_u3_a", "tb.w1.u3.a", [], "k0", [m.PortRef(0, 0, 0)], m.D2A,
                              host=m.PortRef(0, 0, 0), d2a=m.D2A_IE(1.8, 0.0, rise=2e-11)),
                 m.AnalogNode("n_w1_u3_y", "tb.w1.u3.y", [], "k1", [m.PortRef(0, 1, 0)], m.A2D,
                              host=m.PortRef(0, 1, 0)),
                 m.AnalogNode("n_w1_u3_pad", "tb.w1.u3.pad", [], "k2", [m.PortRef(0, 2, 0)], m.BIDIR,
                              host=m.PortRef(0, 2, 0), d2a=m.D2A_IE(1.8, 0.0)),
                 m.AnalogNode("n_x", "tb.x", [], None, [], m.THROUGH)]
        br = names.build_bridges(m.AmsPlan(ana, nodes))
        got = [(b.kind, b.name, b.vhdl_path, b.node) for b in br]
        self.assertEqual(got, [
            ("d2a", "tb.w1.u3.a__d", ".w1.u3.vb0_0_d", "n_w1_u3_a_d"),
            ("en", "tb.w1.u3.a__e", ".w1.u3.vb0_0_e", "n_w1_u3_a_e"),
            ("a2d", "tb.w1.u3.y__a", ".w1.u3.vb1_0_a", "n_w1_u3_y"),
            ("d2a", "tb.w1.u3.pad__d", ".w1.u3.vb2_0_d", "n_w1_u3_pad_d"),
            ("en", "tb.w1.u3.pad__e", ".w1.u3.vb2_0_e", "n_w1_u3_pad_e"),
            ("a2d", "tb.w1.u3.pad__a", ".w1.u3.vb2_0_a", "n_w1_u3_pad")])
        self.assertEqual(names.boundary_line(br[0]), "D2A .w1.u3.vb0_0_d tb.w1.u3.a__d rise=2e-11 fall=1e-11")
        self.assertEqual(names.boundary_line(br[2]), "A2D .w1.u3.vb1_0_a tb.w1.u3.y__a")


class TestPortMap(unittest.TestCase):
    def cell(self, ports, **pm):
        return CutCell("dac", "spice", "dac", ports, PortMap(**pm))

    def test_by_name_with_bus_format(self):
        c = self.cell([CutPort(0, "d", LOGIC, AUTO, "[1:0]", 1, 0), CutPort(1, "out", LOGIC, AUTO)],
                      bus_formats=["<%d>"])
        m = portmap.bind_bits(c, None, [(1, 0), None], ["d<1>", "d<0>", "out", "vdd"])
        self.assertEqual(m, {(0, 1): "d<1>", (0, 0): "d<0>", (1, 0): "out"})

    def test_ascending_range_offsets(self):
        self.assertEqual(portmap.bits((0, 3)), [0, 1, 2, 3])
        self.assertEqual(portmap.offset(0, (0, 3)), 3)
        self.assertEqual(portmap.bit_of(3, (0, 3)), 0)
        self.assertEqual(portmap.offset(4, (4, 1)), 3)

    def test_by_position_and_explicit(self):
        c = self.cell([CutPort(0, "a", LOGIC, INPUT), CutPort(1, "b", LOGIC, INPUT)],
                      default="snps_by_position", explicit={"b": "snps_open"})
        m = portmap.bind_bits(c, None, [None, None], ["x", "y"])
        self.assertEqual(m[(0, 0)], "x")
        self.assertIsNone(m[(1, 0)])

    def test_unmapped_bit_is_an_error(self):
        c = self.cell([CutPort(0, "q", LOGIC, INPUT)])
        with self.assertRaises(notes.NoteError):
            portmap.bind_bits(c, None, [None], ["z"])


class TestJobRecord(unittest.TestCase):
    def test_round_trip_with_and_without_ams(self):
        j = Job(personality="vcs")
        self.assertEqual(Job.from_json(j.to_json()), j)
        j.ams = {"version": 1, "engine": "vacask", "deck": "ams/deck/vamos.sim"}
        j2 = Job.from_json(j.to_json())
        self.assertEqual(j2, j)
        self.assertEqual(j2.ams["engine"], "vacask")

    def test_newer_daidir_says_recompile(self):
        from vamos.job import JobVersionError
        d = json.loads(Job(personality="vcs").to_json())
        d["from_the_future"] = 1
        with self.assertRaises(JobVersionError):
            Job.from_json(json.dumps(d))
        d = json.loads(Job(personality="vcs").to_json())
        d["schema"] = 99
        with self.assertRaises(JobVersionError):
            Job.from_json(json.dumps(d))

    def test_phase0_daidir_without_schema_still_loads(self):
        d = json.loads(Job(personality="vcs").to_json())
        for k in ("schema", "ams", "ams_control", "precision"):
            del d[k]
        self.assertEqual(Job.from_json(json.dumps(d)).personality, "vcs")


class TestNumbers(unittest.TestCase):
    def test_suffixes(self):
        from vamos.netlist.numbers import fmt, parse_number as n
        cases = {"1.5n": 1.5e-9, "500mV": 0.5, "1meg": 1e6, "1MEG": 1e6, "2x": 2e6, "1mil": 25.4e-6,
                 "10ns": 1e-8, "1.2v": 1.2, "3": 3.0, ".5": 0.5, "1e3k": 1e6, "-2u": -2e-6,
                 "10ohm": 10.0, "4f": 4e-15, "1a": 1e-18, "1t": 1e12, "1g": 1e9, "1M": 1e-3}
        for s, v in cases.items():
            self.assertAlmostEqual(n(s), v, delta=abs(v) * 1e-12, msg=s)
        for bad in ("", "x1", "1.2.3", "--1"):
            with self.assertRaises(ValueError, msg=bad):
                n(bad)
        self.assertEqual(fmt(1e6), "1000000.0")
        self.assertEqual(fmt(-0.0), "0.0")


class TestReport(unittest.TestCase):
    """report.py (§3.6): Top-Net, and snps_open private nodes with their shunts."""

    def node(self, aliases, canonical="tb.u.y"):
        from vamos.ams import model as m
        return m.AnalogNode("n_u_y", canonical, list(aliases), "k0", [m.PortRef(0, 0, 0)], m.A2D)

    def test_top_net_names_the_bit_not_the_bus(self):
        from vamos.ams.report import _top_net
        for aliases, want in ((["tb.arr", "tb.arr[0]"], "tb.arr[0]"),
                              (["tb.arr[1]", "tb.arr"], "tb.arr[1]"),
                              (["tb.ca[1].a", "tb.clk", "tb.g[0].u.a"], "tb.clk"),
                              (["tb.bus"], "tb.bus"),                       # no bit alias known
                              (["tb.g[1].w", "tb.g[1].w[0]"], "tb.g[1].w[0]"),
                              (["tb.w.x[2]", "tb.v", "tb.v[3]"], "tb.v[3]"),
                              ([], "tb.u.y")):
            self.assertEqual(_top_net(self.node(aliases)), want, aliases)

    def test_snps_open_nodes_and_shunts_are_listed(self):
        from vamos.ams import model as m, report
        from vamos.ams.deck import OpenPort
        plan = m.AmsPlan(m.CutAnalysis("tb"), [])
        text = report.text(plan, "vacask", [OpenPort("tb.u1", "NC", "nc_xv_u1_nc", "rsh_nc_xv_u1_nc")])
        self.assertIn("// node=tb.u1.NC: snps_open (port_connect), private node nc_xv_u1_nc\n"
                      "// shunt rsh_nc_xv_u1_nc 1e12 ohm to ground", text)

    def test_deck_records_snps_open_ports(self):
        """deck._x_instances: a port_connect'ed snps_open port gets a private node with a
        shunt, and both are recorded for the report."""
        from vamos.ams import deck, model as m
        from vamos.ams.config import AmsConfig
        from vamos.netlist import ir
        nl = ir.Netlist(body=[ir.Subckt("gates", ["a", "nc"], body=[
            ir.Instance("m1", "m", ["d", "nc", "0", "0"], master="n")])], spelling={"nc": "NC"})
        cell = m.CutCell("gates", "spice", "gates", [m.CutPort(0, "a", m.LOGIC, m.INPUT, m.INPUT)],
                         connects={"nc": "snps_open"})
        inst = m.CutInstance(["u1"], "tb.u1", ":tb:u1:", "gates", "gates", "gates", spice={(0, 0): "a"})
        ref = m.PortRef(0, 0, 0)
        plan = m.AmsPlan(m.CutAnalysis("tb", {"gates": cell}, [inst]),
                         [m.AnalogNode("n_u1_a", "tb.u1.a", [], "k", [ref], m.D2A, host=ref)])
        ctx = deck._Ctx(nl, plan, AmsConfig(), "xyce", names.NameAllocator(deck.seed_names(nl)),
                        m.RuleHits())
        extra = deck._x_instances(ctx)
        self.assertEqual([(o.vpath, o.port, o.node, o.shunt) for o in ctx.opens],
                         [("tb.u1", "NC", "nc_xv_u1_nc", "rsh_nc_xv_u1_nc")])
        self.assertIn(("rsh_nc_xv_u1_nc", ["nc_xv_u1_nc", "0"]), [(i.name, i.nodes) for i in extra])
        self.assertEqual(extra[-1].nodes, ["n_u1_a", "nc_xv_u1_nc"])


class TestDeckSources(TempDir):
    """deck.code_sources / enable_nodes read both engines' bridge-source lines."""

    VACASK = ('vd_n_a (n_a_d 0) vamos_vsource type="pwl" '
              'file="code:libcosim_bridge.so:vacask_bridge_init:d2a:tb.u.a__d"\n'
              've_n_a (n_a_e 0) vamos_vsource type="pwl" '
              'file="code:libcosim_bridge.so:vacask_bridge_init:d2a:tb.u.a__e"\n'
              'ia_n_y (n_y 0) vamos_isource type="pwl" '
              'file="code:libcosim_bridge.so:vacask_bridge_init:a2d:tb.u.y__a"\n'
              'rsh_n_a (n_a 0) vamos_resistor r=1000000000000.0\n')
    XYCE = ('vd_n_u1_code_0_ n_u1_code_0__d 0 PWL FILE '
            '"code:libcosim_bridge.so:nvc_bridge_init:d2a:tb.u1.code<0>__d"\n'
            've_n_u1_code_0_ n_u1_code_0__e 0 PWL FILE '
            '"code:libcosim_bridge.so:nvc_bridge_init:d2a:tb.u1.code<0>__e"\n'
            'ia_n_y n_y 0 PWL FILE "code:libcosim_bridge.so:nvc_bridge_init:a2d:tb.u.y__a"\n'
            'bg_n_u1_code_0_ n_u1_code_0__d n_u1_code_0_ I={v(n_u1_code_0__e)*(v(n_u1_code_0__d)'
            '-v(n_u1_code_0_))/500.7}\n')

    def test_both_engines(self):
        from vamos.ams import deck
        v = deck.code_sources(self.write("v.sim", self.VACASK))
        self.assertEqual(v, [("D2A", "tb.u.a__d", "n_a_d"), ("D2A", "tb.u.a__e", "n_a_e"),
                             ("A2D", "tb.u.y__a", "n_y")])
        self.assertEqual(deck.deck_uris(self.write("v2.sim", self.VACASK)),
                         [("D2A", "tb.u.a__d"), ("D2A", "tb.u.a__e"), ("A2D", "tb.u.y__a")])
        self.assertEqual(deck.enable_nodes(self.write("v3.sim", self.VACASK)), ["n_a_e"])
        self.assertEqual(deck.enable_nodes(self.write("x.cir", self.XYCE)), ["n_u1_code_0__e"])


class TestChatterFilter(unittest.TestCase):
    """backends/cosim.py: Xyce's one-terminal warning about vamos's D2A enable nodes is
    dropped however Xyce word-wraps it; anything else reaches the user unchanged."""

    WRAPPED = ["Netlist warning: Voltage Node (N_U1_CODE_0__E) connected to only 1 device",
               " Terminal"]

    def run_filter(self, lines, quiet=("n_u1_code_0__e", "n_u1_in_e", "n_" + "x" * 70 + "_e")):
        from vamos.backends.cosim import ChatterFilter
        out = []
        f = ChatterFilter(out.append, "/d/vamos.cir", quiet_nodes=quiet)
        for ln in lines:
            f(ln)
        f.flush()
        return out

    def test_wrapped_one_line_and_long_names_are_dropped(self):
        long_name = "N_" + "X" * 70 + "_E"
        lines = (["0.000000 a=1"] + self.WRAPPED + [""] +
                 ["Netlist warning: Voltage Node (N_U1_IN_E) connected to only 1 device Terminal"] +
                 ["Netlist warning: Voltage Node", " (%s)" % long_name, " connected to only 1 device",
                  " Terminal"] + ["FINISH called"])
        self.assertEqual(self.run_filter(lines), ["0.000000 a=1", "FINISH called"])

    def test_other_nodes_and_messages_are_kept(self):
        user = ["Netlist warning: Voltage Node (MYNODE_E) connected to only 1 device", " Terminal"]
        dc = ["Netlist warning: Voltage Node (N_U1_CODE_0__E) does not have a DC path to",
              " ground"]
        self.assertEqual(self.run_filter(user + ["x"]), user + ["x"])
        self.assertEqual(self.run_filter(dc + ["x"]), dc + ["x"])

    def test_interrupted_or_unfinished_messages_are_printed_in_order(self):
        head = self.WRAPPED[0]
        self.assertEqual(self.run_filter([head, "0.000000 y=0", " Terminal"]),
                         [head, "0.000000 y=0", " Terminal"])
        self.assertEqual(self.run_filter([head]), [head])                 # flush() at the end
        # a complete message is dropped; a later indented line is ordinary output
        self.assertEqual(self.run_filter([head, " Terminal", " more"]), [" more"])

    def test_engine_chatter_still_dropped(self):
        self.assertEqual(self.run_filter(["[cosim_bridge] bound 3", "", "/d/vamos.cir", "", "ok"]),
                         ["ok"])


class TestOptNames(unittest.TestCase):
    def test_eq_name_with_equals_is_rejected(self):
        with self.assertRaises(AssertionError):
            Opt("-ad=", "eq")

    def test_ad_forms(self):
        t = Table([Opt("-ad", "flag"), Opt("-ad", "eq"), Opt("+ad", "flag"), Opt("+ad=", "prefix"),
                   Opt("-adopt", "next"), Opt("-ad_iereport", "flag")])
        self.assertEqual(t.match("-ad").arity, "flag")
        self.assertEqual(t.match("-ad=vcsAD.init").arity, "eq")
        self.assertEqual(t.match("+ad=x.init").arity, "prefix")
        self.assertEqual(t.match("-adopt").name, "-adopt")
        self.assertEqual(t.match("-ad_iereport").name, "-ad_iereport")


if __name__ == "__main__":
    unittest.main()
