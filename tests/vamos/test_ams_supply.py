"""Reference-supply tracing (docs/VAMOS_AMS_DESIGN.md §3.3) on hand-built IR, and the
deck's level resolution order (supply nets, `d2a powernet` nodes, then the rest)."""

import copy
import unittest

from vamos_testlib import TempDir  # noqa: F401  (puts ROOT on sys.path)

from vamos.ams import deck, vhdl  # noqa: E402
from vamos.ams.config import AmsConfig, IeRule  # noqa: E402
from vamos.ams.model import (A2D, D2A, INOUT, INPUT, LOGIC, OUTPUT, POWERNET, PULL_DOWN,  # noqa: E402
                             PULL_UP, STRONG, SUPPLY0, SUPPLY1, AmsPlan, AnalogNode, CutAnalysis,
                             CutCell, CutInstance, CutPort, Driver, Net, PortRef, RuleHits)
from vamos.ams.names import NameAllocator  # noqa: E402
from vamos.ams.supply import TOP, SupplyGraph  # noqa: E402
from vamos.netlist import ir  # noqa: E402
from vamos.netlist.expr_ast import Name, Num  # noqa: E402


def ev(e, scope):
    if isinstance(e, Num):
        return e.value
    if isinstance(e, Name):
        return scope[e.name]
    raise ValueError("stub evaluator: %r" % (e,))


def inv(name="inv"):
    return ir.Subckt(name, ["in", "out", "vdd", "vss"], body=[
        ir.Instance("mp", "m", ["out", "in", "vdd", "vdd"], master="p1"),
        ir.Instance("mn", "m", ["out", "in", "vss", "vss"], master="n1")])


def vsrc(name, p, v, wave=None, points=()):
    if wave == "pwl":
        return ir.Instance(name, "v", [p, "0"], source=ir.Source(wave="pwl", points=list(points)))
    return ir.Instance(name, "v", [p, "0"], source=ir.Source(dc=Num(v)))


class TestSupplyTrace(unittest.TestCase):
    def deck(self):
        nl = ir.Netlist()
        nl.body = [inv(), vsrc("v_core", "vcore", 1.2), vsrc("v_io", "vio", 3.3),
                   ir.Instance("x_io", "x", ["b", "c", "vio", "0"], master="inv")]
        cut = [ir.Instance("xv_u1", "x", ["a", "y", "vcore", "0"], master="inv")]
        return SupplyGraph(nl, cut, evaluate=ev)

    def test_d2a_into_a_gate_reaches_the_cell_supply(self):
        g = self.deck()
        r = g.trace(g.node("a"))
        self.assertIsNotNone(r)
        self.assertEqual(r.vdd, 1.2)                 # not the 3.3 V IO supply
        self.assertEqual(r.source, "v_core")
        self.assertEqual(r.path[0], "a")

    def test_a2d_output_channel_region(self):
        g = self.deck()
        self.assertEqual(g.trace(g.node("y")).vdd, 1.2)
        self.assertEqual(g.trace(g.node("c")).vdd, 3.3)

    def test_highest_constant_and_values(self):
        g = self.deck()
        self.assertEqual(g.highest_constant(), (3.3, "v_io"))
        self.assertEqual(g.constant_value(g.node("vcore")), ("const", 1.2))
        self.assertEqual(g.constant_value(g.node("nowhere")), ("missing", None))
        self.assertEqual(g.constant_value(g.node("a"))[0], "dynamic")

    def test_no_supply_reached(self):
        nl = ir.Netlist()
        nl.body = [ir.Instance("r1", "r", ["a", "b"], value=Num(1e3))]
        g = SupplyGraph(nl, evaluate=ev)
        self.assertIsNone(g.trace(g.node("a")))
        self.assertIsNone(g.highest_constant())

    def test_dynamic_supply_uses_highest_level_with_warning(self):
        nl = ir.Netlist()
        nl.body = [inv(), vsrc("v_ramp", "vdd", None, "pwl", [(Num(0), Num(0)), (Num(1e-8), Num(1.8))])]
        g = SupplyGraph(nl, [ir.Instance("xv_u", "x", ["a", "y", "vdd", "0"], master="inv")],
                        evaluate=ev)
        r = g.trace(g.node("y"))
        self.assertEqual(r.vdd, 1.8)
        self.assertTrue(r.dynamic)
        self.assertTrue(any("varying" in n.message for n in g.notes))
        self.assertEqual(g.constant_value(g.node("vdd"))[0], "dynamic")

    def test_powernet_and_skip(self):
        nl = ir.Netlist()
        nl.body = [inv()]
        cut = [ir.Instance("xv_u", "x", ["a", "y", "pvdd", "0"], master="inv")]
        g = SupplyGraph(nl, cut, powernets={"pvdd": 0.9}, evaluate=ev)
        self.assertEqual(g.trace(g.node("y")).vdd, 0.9)
        g2 = SupplyGraph(nl, cut, powernets={"pvdd": 0.9}, skip=[(TOP, "pvdd")], evaluate=ev)
        self.assertIsNone(g2.trace(g2.node("y")))

    def test_subckt_parameter_supply(self):
        cell = ir.Subckt("cell", ["in", "out"], params=[ir.Param("vs", Num(2.5))], body=[
            ir.Instance("vint", "v", ["vint", "0"], source=ir.Source(dc=Name("vs"))),
            ir.Instance("r1", "r", ["out", "vint"], value=Num(1e3))])
        nl = ir.Netlist(body=[cell])
        g = SupplyGraph(nl, [ir.Instance("xv_c", "x", ["a", "y"], master="cell",
                                          params={"vs": Num(1.1)})], evaluate=ev)
        self.assertEqual(g.trace(g.node("y")).vdd, 1.1)

    def test_dynamic_powernet_is_a_varying_hit(self):
        """A `d2a powernet` with a varying digital value: a hit at its highest level, with
        the varying-source warning naming it; never a constant (dc_of, step 4)."""
        nl = ir.Netlist()
        nl.body = [inv()]
        cut = [ir.Instance("xv_u", "x", ["a", "y", "pvdd", "0"], master="inv")]
        g = SupplyGraph(nl, cut, dynamic_powernets={"pvdd": 1.2},
                        labels={"pvdd": "d2a powernet pvdd"}, evaluate=ev)
        r = g.trace(g.node("y"))
        self.assertEqual((r.vdd, r.source, r.dynamic), (1.2, "d2a powernet pvdd", True))
        self.assertTrue(any("varying" in n.message and n.origin == "d2a powernet pvdd"
                            for n in g.notes), g.notes)
        self.assertEqual(g.constant_value(g.node("pvdd")), ("dynamic", None))
        self.assertIsNone(g.highest_constant())
        both = SupplyGraph(nl, cut, powernets={"pvdd": 0.9}, dynamic_powernets={"pvdd": 1.2},
                           evaluate=ev)
        self.assertEqual(both.constant_value(both.node("pvdd")), ("const", 0.9))   # constant wins


# -- deck level resolution ---------------------------------------------------------------

# design.vhd as tgt-vhdl writes it: the constant ties of PAMS Method #2a
# (`assign vdd = 1'b1; assign vss = 1'b0;`), a varying driver, X, a delayed constant,
# a vector aggregate, a constant port association and an initial value.
DESIGN = """\
library ieee;
use ieee.std_logic_1164.all;
library sv2vhdl;
use sv2vhdl.logic3d_types_pkg.all;

-- Generated from Verilog module child (t.sv:20)
entity child is
  port (
    p : inout resolved_logic3d
  );
end entity;

-- Generated from Verilog module child (t.sv:20)
architecture from_verilog of child is
begin
end architecture;

-- Generated from Verilog module tb (t.sv:1)
entity tb is
end entity;

-- Generated from Verilog module tb (t.sv:1)
architecture from_verilog of tb is
  signal vdd : resolved_logic3d := L3D_X;  -- Declared at t.sv:2
  signal vss : resolved_logic3d := L3D_X;  -- Declared at t.sv:2
  signal en : resolved_logic3d := L3D_X;  -- Declared at t.sv:3
  signal a : logic3d := L3D_X;  -- Declared at t.sv:3
  signal vx : resolved_logic3d := L3D_X;  -- Declared at t.sv:4
  signal vw : resolved_logic3d := L3D_X;  -- Declared at t.sv:4
  signal vb : resolved_logic3d_vector(1 downto 0) := (others => L3D_X);  -- Declared at t.sv:5
  signal r : logic3d := L3D_1;  -- Declared at t.sv:6
begin
  process (all) is

  begin
    vdd <= L3D_1;
  end process;
  process (all) is

  begin
    vss <= L3D_0;
  end process;
  process (all) is

  begin
    en <= a;
  end process;
  process (all) is

  begin
    vx <= L3D_X;
  end process;
  process (all) is

  begin
    vw <= L3D_1 after 1 ns;
  end process;
  process (all) is

  begin
    vb <= (others => L3D_H);
  end process;
  -- Verilog instance: u
  u: entity work.child
    port map (
      p => L3D_1
    );
end architecture;
"""

ONE, ZERO, VARYING = "tb#0", "tb#1", "tb#2"


def drv(stmt, strength=STRONG, origin="tb:process"):
    return Driver(strength, origin, False, stmt)


class TestDriverLevels(unittest.TestCase):
    def setUp(self):
        self.d = vhdl.parse_text(DESIGN)

    def test_literals(self):
        for text, want in (("L3D_1", "1"), (" l3d_0 ", "0"), ("L3D_H", "1"), ("L3D_L", "0"),
                           ("(others => L3D_1)", "1"), ("L3D_X", None), ("L3D_Z", None),
                           ("L3D_W", None), ("a", None), ("L3D_1 after 1 ns", None),
                           ("logic3d_vector'(L3D_1, L3D_0)", None), ("", None)):
            self.assertEqual(deck.literal_level(text), want, text)

    def test_statement_drivers(self):
        lv = [deck.driver_level(self.d, drv("tb#%d" % i)) for i in range(6)]
        # vdd <= 1, vss <= 0, en <= a, vx <= X, vw <= 1 after 1 ns, vb <= (others => H)
        self.assertEqual(lv, ["1", "0", None, None, None, "1"])

    def test_port_association_initial_value_and_pulls(self):
        self.assertEqual(deck.driver_level(self.d, drv("tb#6", origin="tb:u (p => L3D_1) (tb)")), "1")
        self.assertIsNone(deck.driver_level(self.d, drv("tb#6", origin="tb:u (q => L3D_1) (tb)")))
        self.assertEqual(deck.driver_level(self.d, drv("tb#init:r", origin="tb:r initial value")), "1")
        self.assertEqual(deck.driver_level(self.d, drv("", PULL_UP)), "1")
        self.assertEqual(deck.driver_level(self.d, drv("", PULL_DOWN)), "0")
        for bad in ("", "tb#99", "nosuch#0", "tb#x"):
            self.assertIsNone(deck.driver_level(self.d, drv(bad)), bad)
        self.assertIsNone(deck.driver_level(None, drv(ONE)))

    def test_held_level(self):
        self.assertEqual(deck.held_level(self.d, [drv(ONE)]), "1")
        self.assertEqual(deck.held_level(self.d, [drv(ONE), drv(ONE)]), "1")
        self.assertIsNone(deck.held_level(self.d, [drv(ONE), drv(ZERO)]))       # contention
        self.assertIsNone(deck.held_level(self.d, [drv(ONE), drv(VARYING)]))
        self.assertEqual(deck.held_level(self.d, [drv(ZERO), drv("", PULL_UP)]), "0")   # strong wins
        self.assertEqual(deck.held_level(self.d, [drv("", PULL_UP)]), "1")
        self.assertIsNone(deck.held_level(self.d, []))


def inv_cell_plan(vdd_role, vdd_drivers, vss_role, vss_drivers):
    """tb.u1 = inv (in out vdd vss): in a D2A from tb.a, out an A2D read as tb.y, vdd/vss
    on tb.vdd/tb.vss with the given roles and drivers."""
    cell = CutCell("inv", "spice", "inv", [CutPort(0, "in", LOGIC, INPUT, INPUT),
                                           CutPort(1, "out", LOGIC, OUTPUT, OUTPUT),
                                           CutPort(2, "vdd", LOGIC, INOUT, INOUT),
                                           CutPort(3, "vss", LOGIC, INOUT, INOUT)])
    inst = CutInstance(["u1"], "tb.u1", ":tb:u1:", "inv", "inv", "inv",
                       spice={(0, 0): "in", (1, 0): "out", (2, 0): "vdd", (3, 0): "vss"})
    nets = [Net("k_in", ["tb.a"], [PortRef(0, 0, 0)], drivers=[drv(VARYING)]),
            Net("k_out", ["tb.y"], [PortRef(0, 1, 0)], readers=1),
            Net("k_vdd", ["tb.vdd"], [PortRef(0, 2, 0)], drivers=list(vdd_drivers)),
            Net("k_vss", ["tb.vss"], [PortRef(0, 3, 0)], drivers=list(vss_drivers))]
    ana = CutAnalysis("tb", {"inv": cell}, [inst], nets=nets)
    nodes = []
    for k, (port, role) in enumerate((("in", D2A), ("out", A2D), ("vdd", vdd_role), ("vss", vss_role))):
        ref = PortRef(0, k, 0)
        nodes.append(AnalogNode("n_u1_" + port, "tb.u1." + port, [nets[k].aliases[0]], nets[k].key,
                                [ref], role, host=ref, shunt=role != POWERNET))
    return AmsPlan(ana, nodes)


def inv_netlist():
    nl = ir.Netlist()
    nl.body = [ir.Subckt("inv", ["in", "out", "vdd", "vss"], body=[
        ir.Instance("mp", "m", ["out", "in", "vdd", "vdd"], master="pch"),
        ir.Instance("mn", "m", ["out", "in", "vss", "vss"], master="nch")])]
    return nl


def rule(kind, node, line, **params):
    return IeRule(kind, {k: str(v) for k, v in params.items()}, node=node, origin="vcsAD.init:%d" % line)


class TestDeckLevels(unittest.TestCase):
    """deck._levels: §3.3 evaluation order, the supply0 reference, PAMS Method #2a."""

    def levels(self, plan, rules_, design=None):
        nl = inv_netlist()
        ctx = deck._Ctx(nl, plan, AmsConfig(rules=list(rules_)), "vacask",
                        NameAllocator(deck.seed_names(nl)), RuleHits(), design)
        extra = deck._x_instances(ctx)
        deck._levels(ctx, copy.deepcopy(nl), [i for i in extra if i.kind == "x"])
        self.assertFalse([n for n in ctx.notes if n.severity == "error"], ctx.notes)
        return ctx, {n.canonical.rsplit(".", 1)[1]: n for n in plan.nodes}

    def fallback(self, ctx):
        return [n for n in ctx.notes if "3.3 V fallback" in n.message]

    def test_supply0_net_does_not_use_the_fallback(self):
        """supply1 vdd with a d2a rule, supply0 vss without: vss is 0 V, and no 3.3 V
        fallback warning (its lov never uses the reference); the signal IEs see vdd."""
        plan = inv_cell_plan(POWERNET, [drv("", SUPPLY1)], POWERNET, [drv("", SUPPLY0)])
        ctx, n = self.levels(plan, [rule("d2a", "tb.vdd", 2, hiv=1.2, lov=0)])
        self.assertEqual(self.fallback(ctx), [])
        self.assertEqual((n["vdd"].dc, n["vss"].dc), (1.2, 0.0))
        self.assertIn("levels: default lov=0.0 (the reference supply is not used)", n["vss"].report)
        self.assertFalse([r for r in n["vss"].report if "reference 3.3" in r], n["vss"].report)
        self.assertEqual((n["in"].d2a.hiv, n["in"].d2a.lov), (1.2, 0.0))
        self.assertEqual((n["out"].a2d.loth, n["out"].a2d.hith), (0.6, 0.6))

    def test_supply_levels_that_use_the_fallback_still_warn(self):
        """A supply1 net without a rule, and a supply0 lov given as a % of the span: both
        take the reference's vdd, which is the 3.3 V fallback here (no deck source)."""
        plan = inv_cell_plan(POWERNET, [drv("", SUPPLY1)], POWERNET, [drv("", SUPPLY0)])
        ctx, n = self.levels(plan, [rule("d2a", "tb.vss", 2, lov="10%")])
        self.assertEqual(sorted(w.origin for w in self.fallback(ctx)), ["tb.u1.vdd", "tb.u1.vss"])
        self.assertEqual(n["vdd"].dc, 3.3)
        self.assertAlmostEqual(n["vss"].dc, 0.33, delta=1e-12)
        self.assertIn("levels: reference 3.3 V fallback", n["vss"].report)

    def method_2a(self, vdd_stmt, vss_stmt, vss_hiv=1.2, design=True):
        plan = inv_cell_plan(D2A, [drv(vdd_stmt)], D2A, [drv(vss_stmt)])
        return self.levels(plan, [rule("d2a", "tb.vdd", 2, powernet="", hiv=1.2, lov=0),
                                  rule("d2a", "tb.vss", 3, powernet="", hiv=vss_hiv, lov=0)],
                           vhdl.parse_text(DESIGN) if design else None)

    def test_d2a_powernet_with_constant_drivers_is_a_constant_supply(self):
        """PAMS Method #2a (`assign vdd = 1'b1; assign vss = 1'b0;` with `d2a powernet
        hiv=1.2 lov=0`): the signal IEs get 1.2 V / 0.6 V, with no warning at all."""
        ctx, n = self.method_2a(ONE, ZERO)
        self.assertEqual([x for x in ctx.notes if x.severity != "note"], [])
        self.assertEqual((n["in"].d2a.hiv, n["in"].d2a.lov), (1.2, 0.0))
        self.assertEqual((n["out"].a2d.loth, n["out"].a2d.hith), (0.6, 0.6))
        self.assertTrue(any(r.startswith("levels: reference trace d2a powernet n_u1_vdd")
                            for r in n["in"].report), n["in"].report)
        self.assertIn("supply: constant 1.2 V (the digital side holds 1)", n["vdd"].report)
        self.assertIn("supply: constant 0.0 V (the digital side holds 0)", n["vss"].report)
        self.assertFalse(n["vdd"].shunt or n["vss"].shunt)
        self.assertTrue(n["in"].shunt and n["out"].shunt)

    def test_d2a_powernet_counts_at_the_level_it_is_held(self):
        """vss is held at 0 by its digital driver: it counts at its lov, not at its hiv."""
        ctx, n = self.method_2a(ONE, ZERO, vss_hiv=1.8)
        self.assertEqual(n["in"].d2a.hiv, 1.2)
        self.assertEqual(n["out"].a2d.hith, 0.6)

    def test_d2a_powernet_with_a_varying_driver_counts_its_highest_level(self):
        """A d2a powernet that follows a varying digital value is a varying supply: the
        trace counts its hiv and warns, naming it (never silent)."""
        ctx, n = self.method_2a(VARYING, ZERO)
        self.assertEqual(n["in"].d2a.hiv, 1.2)
        warns = [x for x in ctx.notes if x.severity == "warning"]
        self.assertTrue(warns and all("varying" in w.message for w in warns), ctx.notes)
        self.assertIn("d2a powernet n_u1_vdd", {w.origin for w in warns})
        self.assertTrue(any("not a proved constant" in r for r in n["vdd"].report), n["vdd"].report)
        self.assertEqual(self.fallback(ctx), [])

    def test_d2a_powernet_without_the_design_is_a_varying_supply(self):
        ctx, n = self.method_2a(ONE, ZERO, design=False)
        self.assertEqual(n["in"].d2a.hiv, 1.2)
        self.assertTrue(any("varying" in x.message for x in ctx.notes), ctx.notes)

    def test_plain_d2a_rules_are_not_supplies(self):
        """A d2a rule without powernet leaves the node a gated signal D2A, never a hit."""
        plan = inv_cell_plan(D2A, [drv(ONE)], D2A, [drv(ZERO)])
        ctx, n = self.levels(plan, [rule("d2a", "tb.vdd", 2, hiv=1.2, lov=0),
                                    rule("d2a", "tb.vss", 3, hiv=1.2, lov=0)],
                             vhdl.parse_text(DESIGN))
        self.assertEqual(n["in"].d2a.hiv, 3.3)                 # nothing else reached: fallback
        self.assertTrue(self.fallback(ctx))
        self.assertTrue(n["vdd"].shunt and n["vss"].shunt)


if __name__ == "__main__":
    unittest.main()
