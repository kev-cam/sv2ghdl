"""Regression tests for the deck-group fixes that need no simulator (they run on Cygwin
Python 3.9 too): deck.py's port_connect wiring and net resolution (§2.2, §4.7), the
multi-view range-parameter exemption (§4.7), ie_reference_voltage and unevaluable
supplies in deck._levels / supply.SupplyGraph (§3.3), the save checks and the XA cfg
probe patterns (§4.7), engines.problems (§6), flow.py's left-out-cell and maxstep-note
helpers (§1, §4.3.2) and cut._warnings' variable warning (§5.4).

The end-to-end counterparts, on both engines, are in test_ams_e2e_deckfix.py.
"""

import copy
import os
import stat
import unittest

from vamos_testlib import TempDir

from vamos.ams import cut, deck, engines, flow, initfile, rules, shells  # noqa: E402
from vamos.ams.config import AmsConfig, IeRule  # noqa: E402
from vamos.ams.model import (A2D, D2A, INPUT, LOGIC, OUTPUT, STRONG, THROUGH, AmsPlan,  # noqa: E402
                             AnalogNode, CutAnalysis, CutCell, CutInstance, CutPort, Driver, Net,
                             PortRef, RuleHits)
from vamos.ams.names import NameAllocator  # noqa: E402
from vamos.ams.supply import TOP, SupplyGraph  # noqa: E402
from vamos.netlist import ir  # noqa: E402
from vamos.netlist.expr_ast import Binary, Name, Num  # noqa: E402
from vamos.notes import Note, error, note  # noqa: E402


# -- fixtures --------------------------------------------------------------------------------

def inv_subckt(name="inv", gnd_port=False):
    """inv (a y vdd vss), or with gnd_port inv (a y vdd gnd) after the ground pass."""
    body = [ir.Instance("mp", "m", ["y", "a", "vdd", "vdd"], master="pch"),
            ir.Instance("mn", "m", ["y", "a", "0" if gnd_port else "vss", "0" if gnd_port else "vss"],
                        master="nch"),
            ir.Instance("r1", "r", ["y", "mid"], value=Num(1.0)),
            ir.Instance("c1", "c", ["mid", "0"], value=Num(1e-15))]
    if gnd_port:
        return ir.Subckt(name, ["a", "y", "vdd"], body=body, orig_ports=["a", "y", "vdd", "gnd"],
                         gnd_ports=[3])
    return ir.Subckt(name, ["a", "y", "vdd", "vss"], body=body, orig_ports=["a", "y", "vdd", "vss"])


def vsrc(name, node, value):
    v = value if not isinstance(value, (int, float)) else Num(float(value))
    return ir.Instance(name, "v", [node, "0"], source=ir.Source(dc=v), origin="cells.sp:9")


def netlist(*extra, sub=None, globals_=("vg",)):
    nl = ir.Netlist()
    nl.body = [sub or inv_subckt(), vsrc("vcore", "vdd_core", 1.2), vsrc("vio", "vdd_io", 3.3),
               vsrc("vgs", "vg", 2.5)] + list(extra)
    nl.globals = list(globals_)
    return nl


def two_inverters(sub_name="inv", vpaths=("tb.u1", "tb.u2")):
    """Two SPICE-only inverters whose a/y are Verilog ports (D2A in, A2D out); vdd/vss
    are left to port_connect (shells.py removes them from the shell)."""
    cell = CutCell("inv", "spice", sub_name, [CutPort(0, "a", LOGIC, INPUT, INPUT),
                                              CutPort(1, "y", LOGIC, OUTPUT, OUTPUT)])
    insts, nets, nodes = [], [], []
    for k, vp in enumerate(vpaths):
        u = vp.rsplit(".", 1)[1]
        insts.append(CutInstance([u], vp, ":tb:%s:" % u, "inv", "inv", sub_name,
                                 spice={(0, 0): "a", (1, 0): "y"}))
        for p, (port, role) in enumerate((("a", D2A), ("y", A2D))):
            ref = PortRef(k, p, 0)
            key = "k_%s_%s" % (u, port)
            alias = "tb.%s%d" % (port, k + 1)
            nets.append(Net(key, [alias], [ref], drivers=[Driver(STRONG, "tb:p")] if role == D2A else [],
                            readers=1 if role == A2D else 0))
            nodes.append(AnalogNode("n_%s_%s" % (u, port), "%s.%s" % (vp, port), [alias], key, [ref], role,
                                    host=ref, shunt=True))
    return AmsPlan(CutAnalysis("tb", {"inv": cell}, insts, nets=nets), nodes)


class _Deck(TempDir):
    """Builds a cfg from control text (initfile) and runs deck stages on it."""

    def cfg(self, text):
        path = self.write("vcsAD.init", "choose xa cells.sp;\n" + text)
        cfg = initfile.parse_control([path], self.tmp, {})
        self.assertFalse([n for n in cfg.notes if n.severity == "error"], cfg.notes)
        return cfg

    def ctx(self, text, nl=None, plan=None):
        nl = nl or netlist()
        plan = plan or two_inverters()
        cfg = self.cfg(text)
        for cell in plan.analysis.cells.values():
            sub = nl.subckts()[cell.subckt]
            cell.connects = shells._connects(cfg, [cell.name, sub.name], sub, False)[0]
        hits = RuleHits()
        c = deck._Ctx(nl, plan, cfg, "vacask", NameAllocator(deck.seed_names(nl)), hits)
        return c

    def wire(self, text, nl=None, plan=None):
        c = self.ctx(text, nl, plan)
        extra = deck._x_instances(c)
        return c, {i.name: i.nodes for i in extra if i.kind == "x"}

    @staticmethod
    def errors(c):
        return [n.message for n in c.notes if n.severity == "error"]

    @staticmethod
    def of(c, severity):
        return [n for n in c.notes if n.severity == severity]


# -- Ideck-01: port_connect -cell c -inst <path> ---------------------------------------------

class TestPortConnectInst(_Deck):
    MIXED = ("port_connect -cell inv (vdd => vdd_io, vss => 0);\n"
             "port_connect -cell inv -inst tb.u1 (vdd => vdd_core);\n")

    def test_inst_statement_wires_that_instance(self):
        c, x = self.wire(self.MIXED)
        self.assertEqual(self.errors(c), [])
        self.assertEqual(x["xv_u1"], ["n_u1_a", "n_u1_y", "vdd_core", "0"])
        self.assertEqual(x["xv_u2"], ["n_u2_a", "n_u2_y", "vdd_io", "0"])
        self.assertTrue(c.hits.hit("port_connect_inst#1"))
        self.assertEqual(rules.unmatched(c.cfg, c.hits), [])

    def test_inst_beats_a_later_cell_level_statement(self):
        c, x = self.wire("port_connect -cell inv -inst tb.u1 (vdd => vdd_core);\n"
                         "port_connect -cell inv (vdd => vdd_io, vss => 0);\n")
        self.assertEqual(self.errors(c), [])
        self.assertEqual(x["xv_u1"][2:], ["vdd_core", "0"])
        self.assertEqual(x["xv_u2"][2:], ["vdd_io", "0"])

    def test_in_group_inst_form(self):
        c, x = self.wire("port_connect -cell inv (vdd => vdd_io, vss => 0);\n"
                         "port_connect -cell inv (-inst tb.u1 vdd => vdd_core);\n")
        self.assertEqual(self.errors(c), [])
        self.assertEqual(x["xv_u1"][2:], ["vdd_core", "0"])
        self.assertEqual(x["xv_u2"][2:], ["vdd_io", "0"])

    def test_inst_statements_only(self):
        c, x = self.wire("port_connect -cell inv -inst tb.u1 (vdd => vdd_core, vss => 0);\n"
                         "port_connect -cell inv -inst tb.u2 (vdd => vdd_io, vss => 0);\n")
        self.assertEqual(self.errors(c), [])
        self.assertEqual(x["xv_u1"][2:], ["vdd_core", "0"])
        self.assertEqual(x["xv_u2"][2:], ["vdd_io", "0"])

    def test_inst_glob_and_subckt_name(self):
        plan = two_inverters()
        c, x = self.wire("port_connect -cell inv -inst tb.u* (vdd => vdd_core, vss => 0);\n", plan=plan)
        self.assertEqual(self.errors(c), [])
        self.assertEqual((x["xv_u1"][2], x["xv_u2"][2]), ("vdd_core", "vdd_core"))

    def test_uncovered_instance_names_the_inst_statement(self):
        c, x = self.wire("port_connect -cell inv -inst tb.u1 (vdd => vdd_core, vss => 0);\n")
        errs = self.errors(c)
        self.assertEqual(len(errs), 2, errs)
        self.assertEqual(errs[0], "port vdd of subckt inv is port_connect'ed only by -inst statements "
                                  "that do not match tb.u2 (port_connect -cell inv -inst tb.u1 (vdd => "
                                  "vdd_core) at vcsAD.init:2); add a cell-level port_connect -cell inv "
                                  "or an -inst for tb.u2")
        self.assertNotIn("no port_connect", " ".join(errs))

    def test_later_inst_statement_wins_with_a_note(self):
        c, x = self.wire("port_connect -cell inv (vdd => vdd_io, vss => 0);\n"
                         "port_connect -cell inv -inst tb.u1 (vdd => vdd_core);\n"
                         "port_connect -cell inv -inst tb.u* (vdd => vg);\n")
        self.assertEqual(self.errors(c), [])
        self.assertEqual((x["xv_u1"][2], x["xv_u2"][2]), ("vg", "vg"))
        notes = [n.message for n in self.of(c, "note")]
        self.assertIn("port_connect -cell inv -inst tb.u* (vdd => vg) replaces port_connect -cell inv "
                      "-inst tb.u1 (vdd => vdd_core) (vcsAD.init:3) for tb.u1 (the later command wins)",
                      notes)

    def multi_view(self, live_vdd_u1):
        """inv as a multi-view cell named mv (subckt inv) whose Verilog port vdd maps to the
        SPICE vdd: u2's vdd bit is bridged; u1's is passive (port_connect'ed) unless live_vdd_u1."""
        plan = two_inverters()
        cell = plan.analysis.cells.pop("inv")
        cell.name, cell.view = "mv", "multi"
        cell.ports.append(CutPort(2, "vdd", LOGIC, INPUT, INPUT))
        plan.analysis.cells["mv"] = cell
        for k, ci in enumerate(plan.analysis.instances):
            ci.cell = "mv"
            ci.spice[(2, 0)] = "vdd"
            if k == 1 or live_vdd_u1:
                ref = PortRef(k, 2, 0)
                plan.nodes.append(AnalogNode("n_%s_vdd" % ci.labels[0], ci.vpath + ".vdd", [], "kv%d" % k,
                                             [ref], D2A, host=ref))
        return plan

    def test_multi_view_inst_override_of_a_verilog_port(self):
        c, x = self.wire("port_connect -cell mv -inst tb.u1 (vdd => vdd_core, vss => 0);\n"
                         "port_connect -cell mv -inst tb.u2 (vss => 0);\n", plan=self.multi_view(False))
        self.assertEqual(self.errors(c), [])
        self.assertEqual(x["xv_u1"], ["n_u1_a", "n_u1_y", "vdd_core", "0"])
        self.assertEqual(x["xv_u2"], ["n_u2_a", "n_u2_y", "n_u2_vdd", "0"])

    def test_multi_view_statement_naming_the_subckt_is_refused(self):
        """An -inst statement whose -cell names the subckt (inv) of multi-view cell mv: the
        cut matches -cell against the subckt too (cut._Analyser._passive), so u1's vdd bit is
        passive and the deck wires it (the name is historical: this used to be refused).  A
        bit the cut did bridge is still never wired twice (the backstop error)."""
        c, x = self.wire("port_connect -cell inv -inst tb.u1 (vdd => vdd_core);\n"
                         "port_connect -cell mv (vss => 0);\n", plan=self.multi_view(False))
        self.assertEqual(self.errors(c), [])
        self.assertEqual(x["xv_u1"], ["n_u1_a", "n_u1_y", "vdd_core", "0"])
        self.assertEqual(x["xv_u2"], ["n_u2_a", "n_u2_y", "n_u2_vdd", "0"])
        c, x = self.wire("port_connect -cell inv -inst tb.u1 (vdd => vdd_core);\n"
                         "port_connect -cell mv (vss => 0);\n", plan=self.multi_view(True))
        self.assertEqual(self.errors(c), [
            "port_connect -cell inv -inst tb.u1 (vdd => vdd_core): port vdd of SPICE instance tb.u1 is also "
            "connected from Verilog, and the digital cut bridged that bit (an internal disagreement between "
            "the cut and the deck; please report it)"])

    def test_later_cell_level_statement_is_noted_once(self):
        c, x = self.wire("port_connect -cell inv (vdd => vdd_io, vss => 0);\n"
                         "port_connect -cell inv (vdd => vdd_core);\n")
        self.assertEqual(self.errors(c), [])
        self.assertEqual((x["xv_u1"][2], x["xv_u2"][2]), ("vdd_core", "vdd_core"))
        notes = [n for n in self.of(c, "note") if "replaces" in n.message]
        self.assertEqual(len(notes), 1, notes)
        self.assertEqual(notes[0].origin, "vcsAD.init:3")


# -- Ideck-02: port_connect nets that are not in the deck -----------------------------------

class TestPortConnectNets(_Deck):
    def net(self, conn, extra_cfg=""):
        c, x = self.wire("port_connect -cell inv (%s, vss => 0);\n%s" % (conn, extra_cfg))
        return c, x

    def test_typo_is_an_error_with_the_nearest_net(self):
        c, x = self.net("vdd => vdd_typo")
        self.assertEqual(set(self.errors(c)), {
            "port_connect -cell inv (vdd => vdd_typo): no net vdd_typo in the deck; a port_connect net is "
            "a .global or top-level net of the netlist, ground, or <SPICE instance>.<port> of a port a "
            "Verilog net connects (a Verilog-only net is not in v1); nearest: vdd_io, vdd_core"})
        self.assertEqual(self.of(c, "error")[0].origin, "vcsAD.init:2")

    def test_node_inside_a_spice_instance_is_an_error(self):
        c, x = self.net("vdd => tb.u2.mid")
        self.assertEqual(set(self.errors(c)), {
            "port_connect -cell inv (vdd => tb.u2.mid): tb.u2.mid is node mid inside SPICE instance "
            "tb.u2; in v1 a port_connect net is a .global or top-level net of the netlist, ground, or "
            "<SPICE instance>.<port> of a port a Verilog net connects: make mid a port of its subckt "
            "and connect it from Verilog, or declare it .global"})

    def test_real_connection_is_an_error(self):
        c, x = self.net("real vdd => tb.my_vdd")
        self.assertEqual(self.errors(c), [          # once per statement, not per instance
            "port_connect -cell inv (real vdd => tb.my_vdd): real-number interface elements are not "
            "supported in v1"])

    def test_verilog_only_net_is_an_error(self):
        c, x = self.net("vdd => tb.vdd_w")
        self.assertTrue(self.errors(c))
        self.assertIn("no net tb.vdd_w in the deck", self.errors(c)[0])

    def test_nets_that_exist(self):
        for conn, want in (("vdd => vdd_core", "vdd_core"), ("vdd => tb.vdd_core", "vdd_core"),
                           ("vdd => VDD_IO", "vdd_io"), ("vdd => vg", "vg"), ("vdd => gnd", "0"),
                           ("vdd => tb.gnd", "0"), ("vdd => tb.a2", "n_u2_a"), ("vdd => tb.u1.y", "n_u1_y")):
            with self.subTest(conn=conn):
                c, x = self.net(conn)
                self.assertEqual(self.errors(c), [])
                self.assertEqual(x["xv_u1"][2], want)

    def test_floating_net_warning(self):
        nl = netlist(ir.Instance("rf", "r", ["vfl", "vfl2"], value=Num(1e3)))
        c, x = self.wire("port_connect -cell inv (vdd => vfl, vss => 0);\n", nl=nl)
        self.assertEqual(self.errors(c), [])
        deck._levels(c, copy.deepcopy(nl), [ir.Instance(k, "x", v, master="inv") for k, v in x.items()])
        deck._floating_connects(c)
        warns = [n.message for n in self.of(c, "warning") if "floats" in n.message]
        self.assertEqual(warns, ["port_connect -cell inv (vdd => vfl): no voltage source reaches vfl "
                                 "(port vdd of tb.u1): the port floats"])

    def test_sourced_nets_do_not_warn(self):
        e = ir.Instance("e1", "e", ["vreg", "0", "vdd_io", "0"], value=Num(0.5))
        for conn, nl in (("vdd => vdd_core", netlist()), ("vdd => vreg", netlist(e))):
            with self.subTest(conn=conn):
                c, x = self.wire("port_connect -cell inv (%s, vss => 0);\n" % conn, nl=nl)
                deck._levels(c, copy.deepcopy(nl), [ir.Instance(k, "x", v, master="inv")
                                                     for k, v in x.items()])
                deck._floating_connects(c)
                self.assertFalse([n for n in c.notes if "floats" in n.message], c.notes)


# -- Ideck-06: a ground-alias port ------------------------------------------------------------

class TestGroundAliasPort(_Deck):
    def gnd(self, conn):
        return self.wire("port_connect -cell inv (vdd => vdd_core, %s);\n" % conn,
                         nl=netlist(sub=inv_subckt(gnd_port=True)))

    def test_non_ground_net_is_an_error(self):
        c, x = self.gnd("gnd => vdd_io")
        self.assertEqual(self.errors(c), [
            "port_connect -cell inv (gnd => vdd_io): port gnd of subckt inv is a ground alias, which is "
            "ground inside the subckt, so it cannot be connected to vdd_io; connect it to ground or "
            "rename the port"])

    def test_ground_is_silent_and_open_is_a_warning(self):
        c, x = self.gnd("gnd => gnd")
        self.assertEqual([n for n in c.notes if n.severity != "note"], [])
        self.assertEqual(x["xv_u1"], ["n_u1_a", "n_u1_y", "vdd_core"])
        c, x = self.gnd("gnd => snps_open")
        self.assertEqual(self.errors(c), [])
        self.assertTrue(any("snps_open cannot leave it open" in n.message for n in self.of(c, "warning")))


# -- Ideck-03: multi-view parameter overrides ---------------------------------------------------

class TestRangeParams(unittest.TestCase):
    def cell(self, ranges, params):
        ports = [CutPort(k, "p%d" % k, LOGIC, INPUT, INPUT, r) for k, r in enumerate(ranges)]
        return CutCell("dac", "multi", "dac", ports, params=params)

    def test_whole_identifiers_only(self):
        c = self.cell(["[NG-1:0]", None], {"NG": "2", "G": "1"})
        self.assertEqual(deck.range_params(c), {"NG"})

    def test_through_other_parameters(self):
        c = self.cell(["[W-1:0]"], {"N": "2", "W": "N * 2", "G": "1", "S": '"W"'})
        self.assertEqual(deck.range_params(c), {"W", "N"})

    def test_case_and_system_functions(self):
        c = self.cell(["[n-1:0]", "[$clog2(DEPTH)-1:0]"], {"N": "2", "DEPTH": "8", "clog2": "1"})
        self.assertEqual(deck.range_params(c), {"DEPTH"})

    def test_override_of_a_parameter_named_inside_another_is_refused(self):
        cell = CutCell("dac", "multi", "dac", [CutPort(0, "d", LOGIC, INPUT, INPUT, "[NG-1:0]"),
                                               CutPort(1, "y", LOGIC, OUTPUT, OUTPUT)],
                       params={"NG": "2", "G": "1"})
        nl = ir.Netlist(body=[ir.Subckt("dac", ["d[1]", "d[0]", "y"], body=[
            ir.Instance("r1", "r", ["d[1]", "y"], value=Num(1e3)),
            ir.Instance("r2", "r", ["d[0]", "y"], value=Num(1e3))], orig_ports=["d[1]", "d[0]", "y"])])
        nodes = []
        for k, (port, bit, sp) in enumerate(((0, 0, "d[0]"), (0, 1, "d[1]"), (1, 0, "y"))):
            ref = PortRef(0, port, bit)
            nodes.append(AnalogNode("n%d" % k, "tb.u1." + sp, [], "k%d" % k, [ref], D2A if port == 0 else A2D,
                                    host=ref))
        for params, want in (({"NG": "2", "G": "3"}, ["parameter override G=3 on SPICE instance tb.u1 is "
                                                       "not passed to subckt dac"]),
                             ({"NG": "2", "G": "1"}, [])):
            with self.subTest(params=params):
                inst = CutInstance(["u1"], "tb.u1", ":tb:u1:", "dac", "dac", "dac",
                                   spice={(0, 0): "d[0]", (0, 1): "d[1]", (1, 0): "y"}, params=dict(params))
                plan = AmsPlan(CutAnalysis("tb", {"dac": cell}, [inst]), [copy.copy(n) for n in nodes])
                c = deck._Ctx(nl, plan, AmsConfig(), "vacask", NameAllocator(deck.seed_names(nl)),
                              RuleHits())
                deck._x_instances(c)
                self.assertEqual([n.message for n in c.notes if n.severity == "error"], want)


# -- Ideck-07 / Ideck-08: ie_reference_voltage and unevaluable supplies -----------------------

class TestReferenceVoltage(_Deck):
    def levels(self, text, *extra, plan=None):
        nl = netlist(*extra)
        c, x = self.wire(text, nl=nl, plan=plan or two_inverters(vpaths=("tb.u1",)))
        self.assertEqual(self.errors(c), [])
        deck._levels(c, copy.deepcopy(nl), [ir.Instance(k, "x", v, master="inv") for k, v in x.items()])
        return c, {n.canonical: n for n in c.plan.nodes}

    REG = (ir.Instance("v5", "v", ["v5", "0"], source=ir.Source(dc=Num(5.0))),
           ir.Instance("e1", "e", ["vreg", "0", "v5", "0"], value=Num(0.3)))

    def test_reference_applies_without_a_traced_source(self):
        """PAMS p106: the regulator output is not an ideal source; the reference sets the
        levels anyway (it used to fall to the highest deck source, 5 V)."""
        c, n = self.levels("port_connect -cell inv (vdd => vreg, vss => 0);\n"
                           "ie_reference_voltage node=vreg voltage=1.5;\n", *self.REG)
        self.assertEqual(self.errors(c), [])
        self.assertEqual(n["tb.u1.a"].d2a.hiv, 1.5)
        self.assertEqual((n["tb.u1.y"].a2d.loth, n["tb.u1.y"].a2d.hith), (0.75, 0.75))
        self.assertIn("levels: reference ie_reference_voltage vreg", n["tb.u1.a"].report)
        self.assertEqual(rules.unmatched(c.cfg, c.hits), [])

    def test_without_the_reference_the_highest_source_decides(self):
        c, n = self.levels("port_connect -cell inv (vdd => vreg, vss => 0);\n", *self.REG)
        self.assertEqual(n["tb.u1.a"].d2a.hiv, 5.0)

    def test_last_entry_on_a_net_wins_and_nothing_is_tnf(self):
        c, n = self.levels("port_connect -cell inv (vdd => vdd_core, vss => 0);\n"
                           "ie_reference_voltage node=vdd_core voltage=1.0;\n"
                           "ie_reference_voltage node=tb.vdd_core voltage=0.9;\n")
        self.assertEqual(n["tb.u1.a"].d2a.hiv, 0.9)
        self.assertEqual([x.message for x in self.of(c, "note") if "replaced" in x.message],
                         ["ie_reference_voltage node=vdd_core is replaced by node=tb.vdd_core at "
                          "vcsAD.init:4 (the same net; the last command is used)"])
        self.assertEqual(rules.unmatched(c.cfg, c.hits), [])

    def test_entry_no_trace_reaches_is_a_warning_not_tnf(self):
        c, n = self.levels("port_connect -cell inv (vdd => vdd_core, vss => 0);\n"
                           "ie_reference_voltage node=vdd_core voltage=1.0;\n"
                           "ie_reference_voltage node=vdd_io voltage=2.5;\n")
        self.assertEqual(n["tb.u1.a"].d2a.hiv, 1.0)
        warns = [x for x in self.of(c, "warning") if "ie_reference_voltage" in x.message]
        self.assertEqual([(w.origin, w.message.split(":")[0]) for w in warns],
                         [("vcsAD.init:4", "ie_reference_voltage node=vdd_io")])
        self.assertEqual(rules.unmatched(c.cfg, c.hits), [])

    def test_reference_outranks_the_source_of_its_stage(self):
        c, n = self.levels("port_connect -cell inv (vdd => vdd_core, vss => 0);\n"
                           "ie_reference_voltage node=vdd_core voltage=1.0;\n")
        self.assertEqual(n["tb.u1.a"].d2a.hiv, 1.0)

    def test_dynamic_reference_is_an_error_only_where_used(self):
        text = ("port_connect -cell inv (vdd => vreg, vss => 0);\n"
                "ie_reference_voltage node=vreg;\n")
        nl = netlist(*self.REG)
        c, x = self.wire(text, nl=nl, plan=two_inverters(vpaths=("tb.u1",)))
        deck._levels(c, copy.deepcopy(nl), [ir.Instance(k, "x", v, master="inv") for k, v in x.items()])
        self.assertEqual(self.errors(c), [
            "ie_reference_voltage node=vreg has no voltage= and vreg is not a constant supply, so the "
            "levels of tb.u1.a, tb.u1.y would follow it (dynamic references are not in v1); give voltage="])
        c, n = self.levels(text + "d2a hiv=1.5 lov=0 node=tb.u1.a;\na2d loth=0.7 hith=0.8 node=tb.u1.y;\n",
                           *self.REG)
        self.assertEqual(self.errors(c), [])

    def test_unevaluable_supply_is_an_error_naming_it(self):
        temper = ir.Instance("vt", "v", ["vdd_t", "0"], origin="cells.sp:5",
                             source=ir.Source(dc=Binary("*", Num(1.2), Name("temper"))))
        nl = netlist(temper)
        c, x = self.wire("port_connect -cell inv (vdd => vdd_t, vss => 0);\n", nl=nl,
                         plan=two_inverters(vpaths=("tb.u1",)))
        deck._levels(c, copy.deepcopy(nl), [ir.Instance(k, "x", v, master="inv") for k, v in x.items()])
        errs = self.of(c, "error")
        self.assertEqual(len(errs), 1, c.notes)
        self.assertEqual(errs[0].origin, "cells.sp:5")
        self.assertTrue(errs[0].message.startswith("V source vt cannot be evaluated ("), errs)
        self.assertIn("the supply trace of tb.u1.a, tb.u1.y reaches it", errs[0].message)
        self.assertFalse([n for n in c.notes if "3.3 V fallback" in n.message], c.notes)
        # levels given by rules do not use the reference: no error
        c2, n = self.levels("port_connect -cell inv (vdd => vdd_t, vss => 0);\n"
                            "d2a hiv=1.2 lov=0 node=tb.u1.a;\na2d loth=0.6 hith=0.6 node=tb.u1.y;\n", temper)
        self.assertEqual(self.errors(c2), [])

    def test_unevaluable_source_blocks_the_highest_source_step(self):
        temper = ir.Instance("vt", "v", ["vt_n", "0"], origin="cells.sp:5",
                             source=ir.Source(dc=Binary("*", Num(1.2), Name("temper"))))
        nl = netlist(temper)
        c, x = self.wire("port_connect -cell inv (vdd => vfl, vss => 0);\n",
                         nl=netlist(temper, ir.Instance("rf", "r", ["vfl", "vfl2"], value=Num(1e3))),
                         plan=two_inverters(vpaths=("tb.u1",)))
        deck._levels(c, copy.deepcopy(c.nl), [ir.Instance(k, "x", v, master="inv") for k, v in x.items()])
        errs = self.errors(c)
        self.assertEqual(len(errs), 1, c.notes)
        self.assertIn("V source vt cannot be evaluated", errs[0])
        self.assertIn("highest deck source", errs[0])


class TestSupplyGraphStops(unittest.TestCase):
    def graph(self, *extra, refs=None):
        sub = inv_subckt()
        nl = ir.Netlist(body=[sub] + list(extra))
        cut_ = [ir.Instance("xv_u1", "x", ["a", "y", "vdd", "0"], master="inv")]
        return SupplyGraph(nl, cut_, refs=refs)

    def test_reference_node_stops_the_trace(self):
        g = self.graph(ir.Instance("v5", "v", ["v5", "0"], source=ir.Source(dc=Num(5.0))),
                       ir.Instance("rr", "r", ["v5", "vdd"], value=Num(10.0)),
                       refs={(TOP, "vdd"): (1.5, "vdd", 0)})
        r = g.trace(g.node("a"))
        self.assertEqual((r.vdd, r.ref_name, r.ref_index, r.ref_dynamic), (1.5, "vdd", 0, False))
        g2 = self.graph(ir.Instance("v5", "v", ["v5", "0"], source=ir.Source(dc=Num(5.0))),
                        ir.Instance("rr", "r", ["v5", "vdd"], value=Num(10.0)))
        self.assertEqual((g2.trace(g2.node("a")).vdd, g2.trace(g2.node("a")).ref_name), (5.0, None))

    def test_unknown_reference_at_the_start_is_not_a_hit(self):
        g = self.graph(ir.Instance("v1", "v", ["vdd", "0"], source=ir.Source(dc=Num(1.2))),
                       refs={(TOP, "y"): (None, "y", 3)})
        r = g.trace(g.node("y"))
        self.assertEqual((r.vdd, r.ref_name), (1.2, None))
        r = g.trace(g.node("a"))                     # reaches y in stage 2: dynamic reference
        self.assertEqual((r.ref_name, r.ref_dynamic), ("y", True))

    def test_unevaluable_source_is_reported_by_the_trace(self):
        g = self.graph(ir.Instance("vt", "v", ["vdd", "0"],
                                   source=ir.Source(dc=Binary("*", Num(1.2), Name("temper")))))
        r = g.trace(g.node("a"))
        self.assertIsNotNone(r)
        self.assertEqual([lv.source for lv in r.unevaluable], ["vt"])
        self.assertEqual([lv.source for lv in g.unevaluable_sources()], ["vt"])
        self.assertTrue(g.touches_source(g.node("vdd")))
        self.assertFalse(g.touches_source(g.node("a")))


# -- Ideck-09: saves ----------------------------------------------------------------------------

class TestSaves(_Deck):
    def saves(self, probes, xa=None, nl=None):
        nl = nl or netlist(ir.Subckt("div", ["p", "q"], orig_ports=["p", "q"], body=[
            ir.Instance("r1", "r", ["p", "m"], value=Num(1e3)), ir.Instance("r2", "r", ["m", "q"], value=Num(1e3)),
            ir.Instance("vi", "v", ["m", "0"], source=ir.Source(dc=Num(0.5)))]),
            ir.Instance("xd", "x", ["vdd_core", "dout"], master="div"))
        c, x = self.wire("port_connect -cell inv (vdd => vdd_core, vss => 0);\n", nl=nl)
        dk = copy.deepcopy(nl)
        dk.body += [ir.Instance(k, "x", v, master="inv") for k, v in x.items()]
        dk.probes = list(probes)
        if xa is not None:
            c.cfg.xa = dict(xa)
        deck._saves(c, dk)
        return c, dk.probes

    def test_unknown_node_is_an_error(self):
        c, p = self.saves([("tran", "v", "nosuch"), ("tran", "v", "vdd_core")])
        self.assertEqual(self.errors(c), ["v(nosuch): the deck has no node nosuch (a top-level or .global "
                                          "net, or <X instance>.<node>)"])
        c, p = self.saves([("tran", "v", "xd.nosuch")])
        self.assertEqual(len(self.errors(c)), 1)

    def test_port_alias_is_saved_as_its_node(self):
        c, p = self.saves([("tran", "v", "xd.p"), ("tran", "v", "xd.m"), ("tran", "v", "xv_u1.a,xd.q")])
        self.assertEqual(self.errors(c), [])
        self.assertEqual(p[:3], [("tran", "v", "vdd_core"), ("tran", "v", "xd.m"),
                                 ("tran", "v", "n_u1_a,dout")])

    def test_xa_patterns_are_resolved(self):
        xa = {"probe_v": [("tb.u1.*", "xa.cfg:1"), ("tb.nosuch*", "xa.cfg:2"), ("xd.m", "xa.cfg:3")],
              "probe_i": [("xd.*", "xa.cfg:4")], "case": None}
        c, p = self.saves([], xa=xa)
        self.assertEqual(self.errors(c), [])
        got = {t for _, k, t in p if k == "v"}
        self.assertTrue({"n_u1_a", "n_u1_y", "vdd_core", "xv_u1.mid", "xd.m"} <= got, p)
        self.assertNotIn("*", got)
        self.assertIn(("tran", "i", "xd.vi"), p)
        warns = [(w.origin, w.message) for w in self.of(c, "warning")]
        self.assertIn(("xa.cfg:2", "probe_waveform_voltage tb.nosuch* matches no node of the deck, so it "
                                   "saves nothing (names are VCS paths: <Verilog instance path>.<node> "
                                   "inside a SPICE instance, a netlist net by its own name)"), warns)
        self.assertTrue(any(o == "xa.cfg:4" and "xd.r1" in m for o, m in warns), warns)

    def test_star_pattern_saves_everything(self):
        c, p = self.saves([], xa={"probe_v": [("*", "xa.cfg:1")], "probe_i": [], "case": None})
        self.assertEqual(p, [("tran", "v", "*")])


# -- Ideck-10: engine overrides -----------------------------------------------------------------

class TestEngineOverrides(TempDir):
    def test_bad_overrides_are_problems_and_never_replaced(self):
        os.environ["VAMOS_OPENVAF"] = os.path.join(self.tmp, "no-openvaf")
        os.environ["VAMOS_XYCE"] = os.path.join(self.tmp, "no-xyce")
        os.environ["VAMOS_XYCE_LIBS"] = self.tmp + os.pathsep + os.path.join(self.tmp, "nodir")
        self.assertEqual(engines.openvaf(), os.path.join(self.tmp, "no-openvaf"))
        self.assertEqual(engines.xyce_bin(), os.path.join(self.tmp, "no-xyce"))
        self.assertEqual(engines.problems("vacask"),
                         ["VAMOS_OPENVAF=%s is not an executable" % os.path.join(self.tmp, "no-openvaf")])
        self.assertEqual(engines.problems("xyce"), [
            "VAMOS_XYCE=%s is not an executable" % os.path.join(self.tmp, "no-xyce"),
            "VAMOS_XYCE_LIBS=%s: %s is not a directory" % (os.environ["VAMOS_XYCE_LIBS"],
                                                            os.path.join(self.tmp, "nodir"))])

    def test_good_overrides_are_no_problem(self):
        exe = self.write("bin/tool", "#!/bin/sh\nexit 0\n")
        os.chmod(exe, os.stat(exe).st_mode | stat.S_IXUSR)
        if not os.access(exe, os.X_OK):
            self.skipTest("no executable bit on this file system")
        os.environ["VAMOS_OPENVAF"] = exe
        os.environ["VAMOS_XYCE"] = exe
        os.environ["VAMOS_VACASK_HOME"] = self.tmp
        self.assertEqual(engines.problems("vacask"), [])
        self.assertEqual(engines.problems("xyce"), [])
        for k in ("VAMOS_OPENVAF", "VAMOS_XYCE", "VAMOS_VACASK_HOME"):
            del os.environ[k]
        self.assertEqual(engines.problems("vacask"), [])

    def test_compile_va_reports_instead_of_raising(self):
        os.environ["VAMOS_OPENVAF"] = os.path.join(self.tmp, "no-openvaf")
        nl = netlist()
        c = deck._Ctx(nl, two_inverters(), AmsConfig(), "vacask", NameAllocator(deck.seed_names(nl)),
                      RuleHits())
        self.assertEqual(deck._compile_va(c, copy.deepcopy(nl), os.path.join(self.tmp, "d")), [])
        self.assertEqual(len(c.notes), 1)
        self.assertTrue(c.notes[0].message.startswith("cannot run openvaf-r %s (VAMOS_OPENVAF): "
                                                      % os.path.join(self.tmp, "no-openvaf")), c.notes)


# -- Ideck-05 / Ideck-11: flow.py helpers ------------------------------------------------------

class TestFlowHelpers(unittest.TestCase):
    LEFT = {"inv": note("cells.sp:5", "subckt inv is left out: s1: an S-parameter element (HSPICE S) "
                        "is not supported (cells.sp:8); instantiating it is an error")}

    def test_left_out_subckt_errors_name_the_reason(self):
        notes = [error("tb.sv:5", "module inv not found in Verilog sources or SPICE netlists"),
                 error("tb.sv:6", "module myinv: use_spice binds it to subckt INV, which is not in the "
                       "SPICE netlists"),
                 error("tb.sv:2", "cell mv: no SPICE subckt inv in the netlists"),
                 error("tb.sv:7", "module other not found in Verilog sources or SPICE netlists"),
                 note("vcsAD.init:2", "cell myinv not instantiated; ignored")]
        out = flow.left_out_cells(notes, self.LEFT)
        why = "s1: an S-parameter element (HSPICE S) is not supported (cells.sp:8)"
        self.assertEqual([(n.severity, n.origin, n.message) for n in out], [
            ("error", "tb.sv:5", "cell inv: subckt inv cannot be simulated: " + why),
            ("error", "tb.sv:6", "cell myinv: subckt INV cannot be simulated: " + why),
            ("error", "tb.sv:2", "cell mv: subckt inv cannot be simulated: " + why),
            ("error", "tb.sv:7", "module other not found in Verilog sources or SPICE netlists"),
            ("note", "vcsAD.init:2", "cell myinv not instantiated; ignored")])
        self.assertEqual(flow.left_out_cells(notes, {}), notes)

    def test_maxstep_note_dropped_with_the_option(self):
        ms = note("cells.sp:11", ".tran: maximum time step 5e-10 s, HSPICE's bound without .option "
                  "delmax: min(TSTOP/50, RMAX*TSTEP) = min(1e-09, 5*1e-10) with RMAX=5, its default "
                  "under DVDT=4 and LVLTIM=1; .option delmax or --vamos-analog-maxstep sets another")
        other = note("cells.sp:2", "something else")
        self.assertEqual(flow._netlist_notes([ms, other], {}), [ms, other])
        self.assertEqual(flow._netlist_notes([ms, other], {"analog_maxstep": "1n"}), [other])

    def test_parser_note_text_is_what_flow_drops(self):
        """flow._netlist_notes matches the parser's note by its first words: pin them."""
        from vamos.netlist import spice
        nl = spice.parse([], [("v1 a 0 1", "x:1"), ("r1 a 0 1k", "x:2"), (".tran 1n 100n", "x:3")],
                         os.getcwd(), ir.ParseOpts())
        self.assertTrue([n for n in nl.notes if n.message.startswith(flow._MAXSTEP_NOTE)], nl.notes)


class TestAnalysisNotes(_Deck):
    def analysis(self, opts, tran=True):
        nl = netlist()
        if tran:
            nl.analyses = [ir.Analysis("tran", {"step": 1e-10, "stop": 1e-7, "start": 0.0, "maxstep": 5e-10},
                                       origin="cells.sp:11")]
        c = deck._Ctx(nl, two_inverters(), AmsConfig(), "vacask", NameAllocator(deck.seed_names(nl)),
                      RuleHits())
        dk = copy.deepcopy(nl)
        stop, synth = deck._analysis(c, dk, opts)
        return c, dk

    def test_maxstep_option_is_noted_with_its_value(self):
        c, dk = self.analysis({"analog_maxstep": "1n"})
        self.assertEqual(dk.analyses[0].args["maxstep"], 1e-9)
        self.assertEqual([(n.origin, n.message) for n in c.notes],
                         [("cells.sp:11", ".tran: maximum time step 1e-09 s, from --vamos-analog-maxstep "
                                          "(in place of 5e-10 s)")])

    def test_stop_clamp_warning_is_not_repeated(self):
        c, dk = self.analysis({"analog_stop": "10000"}, tran=False)
        self.assertEqual(dk.analyses[0].args["stop"], deck.MAX_STOP)
        self.assertFalse([n for n in c.notes if "clamped" in n.message], c.notes)
        notes = []
        self.assertEqual(deck.analog_stop({"analog_stop": "10000"}, notes), deck.MAX_STOP)
        self.assertEqual(len(notes), 1)                  # flow.py prints this one at step 5


# -- Ideck-12: the variable warning ---------------------------------------------------------------

class TestVariableWarning(unittest.TestCase):
    def warn(self, role, nports):
        cell = CutCell("inv", "spice", "inv", [CutPort(0, "a", LOGIC, INPUT, INPUT),
                                               CutPort(1, "y", LOGIC, OUTPUT, OUTPUT)])
        insts = [CutInstance([u], "tb." + u, ":tb:%s:" % u, "inv", "inv", "inv") for u in ("u1", "u2")]
        ports = [PortRef(0, 1, 0), PortRef(1, 0, 0)][:nports]
        net = Net("k", ["tb.link"], ports, variable=True)
        ana = CutAnalysis("tb", {"inv": cell}, insts, nets=[net])
        node = AnalogNode("n_u1_y", "tb.u1.y", ["tb.link"], "k", ports, role, host=ports[0])
        notes = []
        cut._warnings(ana, None, [node], {"k": node}, notes)
        return [n.message for n in notes if n.severity == "warning"]

    def test_one_spice_port_is_not_warned(self):
        for role in (THROUGH, A2D, D2A):
            with self.subTest(role=role):
                self.assertEqual(self.warn(role, 1), [])

    def test_several_spice_ports_are_warned_with_the_fix(self):
        for role in (THROUGH, A2D, D2A):
            with self.subTest(role=role):
                self.assertEqual(self.warn(role, 2), [
                    "variable tb.u1.y joins SPICE ports in analog; VCS digitises it: tb.u1.y, tb.u2.a "
                    "share one analog node here, where VCS gives each SPICE port an interface element "
                    "of its own; declare tb.u1.y a wire if the analog connection is intended, or connect "
                    "each SPICE port to a net of its own (wire w = y;) to get VCS's digital connection"])


if __name__ == "__main__":
    unittest.main()
