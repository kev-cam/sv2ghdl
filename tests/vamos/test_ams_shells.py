"""vcs-ams cut cells and shells (docs/VAMOS_AMS_DESIGN.md §5.1): vamos/ams/shells.py.

    python3 -m unittest discover -s tests/vamos -p 'test_ams_shells.py' -v

The pure tests run anywhere; TestShellsStack runs the real iverilog (cell set,
direction probe) and iverilog-sv2ghdl + nvc (what the markers do to design.vhd),
and the cut on what the translator makes of a built pp.v (probe step 2b).
"""

import copy
import inspect
import os
import re
import unittest

from vamos_testlib import TempDir, fixture, needs_stack, run

from vamos import tools  # noqa: E402
from vamos.ams import cut, names, shells, vhdl, verilog_ports as vp  # noqa: E402
from vamos.ams.config import AmsConfig, Choose, PortConnect, PortDir, UseSpice  # noqa: E402
from vamos.ams.model import (AUTO, D2A, INOUT, INPUT, LOGIC, OUTPUT, REAL, CutCell, CutPort,  # noqa: E402
                             RuleHits)
from vamos.job import Job, Source  # noqa: E402
from vamos.netlist.ir import Netlist, Subckt  # noqa: E402
from vamos.notes import ERROR, NOTE, NoteError  # noqa: E402


def cfg(**kw):
    return AmsConfig(choose=Choose("vacask"), **kw)


def netlist(*subs, spelling=None):
    return Netlist(body=list(subs), spelling=spelling or {})


def sub(name, ports, orig=None, gnd=()):
    return Subckt(name, list(ports), orig_ports=list(orig if orig is not None else ports), gnd_ports=list(gnd),
                  origin="cells.sp:1")


def port_tuples(ports):
    return [(p.index, p.verilog, p.declared, p.shell_dir, p.range_text, p.msb, p.lsb) for p in ports]


def read_fixture(name):
    with open(fixture("ams", name)) as fh:
        return fh.read()


class TestSpicePorts(unittest.TestCase):
    def test_bus_members_at_first_position(self):
        s = sub("dac", ["d<1>", "out", "d<0>", "vdd"])
        ports, notes = shells.spice_ports("dac", s, cfg(bus_formats=["<%d>"]), netlist(s), set(), {})
        self.assertEqual(notes, [])
        self.assertEqual(port_tuples(ports), [(0, "d", AUTO, INOUT, "[1:0]", 1, 0),
                                              (1, "out", AUTO, INOUT, None, None, None),
                                              (2, "vdd", AUTO, INOUT, None, None, None)])

    def test_default_format_and_ground(self):
        s = sub("x", ["a[0]", "a[1]", "y"], orig=["a[0]", "a[1]", "y", "gnd"], gnd=[3])
        ports, _ = shells.spice_ports("x", s, cfg(), netlist(s), set(), {})
        self.assertEqual(port_tuples(ports), [(0, "a", AUTO, INOUT, "[0:1]", 0, 1),
                                              (1, "y", AUTO, INOUT, None, None, None)])   # ground port gone

    def test_port_index_order_pams_addr4(self):
        s = sub("addr4", ["s_3", "a_2", "a_1", "a_0", "b_3", "b_2", "b_1", "b_0", "cin", "a_3", "s_2", "s_1",
                          "s_0", "cout"])
        c = cfg(bus_formats=["_%d"], use_spice=[UseSpice(cells=[("addr4", "")], index_order=[
            ("*", "same"), ("a", "dec"), ("s", "inc")])])
        ports, notes = shells.spice_ports("addr4", s, c, netlist(s), set(), {})
        self.assertEqual(notes, [])
        self.assertEqual([(p.verilog, p.range_text) for p in ports],
                         [("s", "[0:3]"), ("a", "[3:0]"), ("b", "[3:0]"), ("cin", None), ("cout", None)])
        c.use_spice[0].index_order = []
        ports, notes = shells.spice_ports("addr4", s, c, netlist(s), set(), {})
        self.assertEqual(len(notes), 1)
        self.assertIn("indexes of bus a are not monotonic", notes[0].message)
        self.assertIn("port_index_order", notes[0].message)

    def test_port_dir_connect_and_spelling(self):
        s = sub("dac", ["d<1>", "d<0>", "out", "vdd", "vss"])
        c = cfg(bus_formats=["<%d>"],
                port_dirs={"dac": PortDir("dac", {"d<0>": "input", "out": "output"}, "vcsAD.init:4"),
                           "*": PortDir("*", {"v*": "inout"}, "vcsAD.init:5")})
        nl = netlist(s, spelling={"out": "OUT", "d<1>": "D<1>", "d<0>": "D<0>"})
        ports, notes = shells.spice_ports("dac", s, c, nl, {"vss"}, {"vdd": "Vdd"})
        self.assertEqual(notes, [])
        self.assertEqual(port_tuples(ports), [(0, "D", INPUT, INPUT, "[1:0]", 1, 0),
                                              (1, "OUT", OUTPUT, OUTPUT, None, None, None),
                                              (2, "Vdd", INOUT, INOUT, None, None, None)])
        c.port_dirs = {"dac": PortDir("dac", {"nope": "input"}, "vcsAD.init:6")}
        _, notes = shells.spice_ports("dac", s, c, nl, set(), {})
        self.assertEqual([(n.origin, "has no port nope" in n.message) for n in notes], [("vcsAD.init:6", True)])

    def test_xheep_adc(self):
        # x-heep adc.sp: .subckt AMS_ADC_1b GND OUT SEL<1> SEL<0> VDD (GND removed by the ground pass),
        # control.init: port_connect (vdd => vdd, gnd => gnd); port_dir (input sel; output out); <%d>
        s = Subckt("ams_adc_1b", ["out", "sel<1>", "sel<0>", "vdd"],
                   orig_ports=["gnd", "out", "sel<1>", "sel<0>", "vdd"], gnd_ports=[0], origin="adc.sp:89")
        c = cfg(bus_formats=["<%d>"],
                port_connects=[PortConnect("ams_adc_1b", None, [("vdd", "vdd", False), ("gnd", "gnd", False)],
                                           "control.init:6")],
                port_dirs={"ams_adc_1b": PortDir("ams_adc_1b", {"sel": "input", "out": "output"}, "control.init:7")})
        nl = netlist(s, spelling={"out": "OUT", "sel<1>": "SEL<1>", "sel<0>": "SEL<0>", "vdd": "VDD"})
        conns, removed, notes = shells._connects(c, ["ams_adc_1b"], s, False)
        self.assertEqual((conns, removed, notes), ({"vdd": "vdd", "gnd": "gnd"}, {"vdd", "gnd"}, []))
        ports, notes = shells.spice_ports("ams_adc_1b", s, c, nl, removed, {"sel": "sel", "out": "out"})
        self.assertEqual(notes, [])
        self.assertEqual(port_tuples(ports), [(0, "out", OUTPUT, OUTPUT, None, None, None),
                                              (1, "sel", INPUT, INPUT, "[1:0]", 1, 0)])
        ports, _ = shells.spice_ports("ams_adc_1b", s, c, nl, removed, {})
        self.assertEqual([p.verilog for p in ports], ["OUT", "SEL"])   # no instance spelling: the netlist's

    def test_spellings_conflict(self):
        pp = vp.from_text("module t; inv_sp u1 (.Vdd(a)); inv_sp u2 (.VDD(b)); inv_sp u3 (.a(c)); endmodule\n")
        spell, notes = shells._spellings(vp.instantiations(pp), "inv_sp", pp)
        self.assertEqual(spell["a"], "a")
        self.assertEqual(len(notes), 1)
        self.assertIn("port vdd is spelled differently by its instances: VDD by t.u2", notes[0].message)
        self.assertIn("Vdd by t.u1", notes[0].message)

    def test_connects(self):
        s = sub("x", ["a", "vdd"], orig=["a", "vdd", "gnd"], gnd=[2])
        c = cfg(port_connects=[PortConnect("x", None, [("VDD", "vdd_net", False)], "i:1"),
                               PortConnect("x", "tb.u1", [("a", "snps_open", False)], "i:2"),
                               PortConnect("x", None, [("zz", "n", False)], "i:3")])
        conns, removed, notes = shells._connects(c, ["x"], s, False)
        self.assertEqual(conns, {"vdd": "vdd_net"})
        self.assertEqual(removed, {"vdd", "a"})
        self.assertEqual([n.origin for n in notes], ["i:3"])


class TestBindings(unittest.TestCase):
    def test_subckt_for(self):
        nl = netlist(sub("inv_sp", ["a"]), sub("spice_cpu", ["a"]), sub("cpu2", ["a"]))
        c = cfg(use_spice=[UseSpice(cells=[("CPU", "spice_cpu")]), UseSpice(cells=[("blk", "cpu2")],
                                                                              insts=["tb.b1"])])
        self.assertEqual(shells.subckt_for(c, nl, "cpu"), "spice_cpu")
        self.assertEqual(shells.subckt_for(c, nl, "INV_SP"), "inv_sp")
        self.assertEqual(shells.subckt_for(c, nl, "blk"), "cpu2")            # only -inst statements bind it
        self.assertIsNone(shells.subckt_for(c, nl, "nosuch"))
        self.assertEqual(shells.binding(c, "cpu"), "spice_cpu")
        self.assertEqual(shells.cell_globs(c), ["CPU", "blk"])


class TestShellText(unittest.TestCase):
    def test_spice_shell_markers(self):
        cell = CutCell("x_sp", "spice", "x_sp", [
            CutPort(0, "d", LOGIC, AUTO, INOUT, "[1:0]", 1, 0), CutPort(1, "d_0", LOGIC, AUTO, INOUT),
            CutPort(2, "a", LOGIC, INPUT, INPUT), CutPort(3, "y", LOGIC, OUTPUT, OUTPUT, "[0:1]", 0, 1),
            CutPort(4, "wire", LOGIC, AUTO, INOUT)])
        self.assertEqual(shells.spice_shell(cell), """module x_sp (d, d_0, a, y, \\wire );
  inout [1:0] d;
  inout d_0;
  input a;
  output [0:1] y;
  inout \\wire ;
  bufif1 vamos_ams_hiz_0 (d[1], 1'b0, 1'b0);
  bufif1 vamos_ams_hiz_1 (d[0], 1'b0, 1'b0);
  bufif1 vamos_ams_hiz_2 (d_0, 1'b0, 1'b0);
  bufif1 vamos_ams_hiz_3 (y[0], 1'b0, 1'b0);
  bufif1 vamos_ams_hiz_4 (y[1], 1'b0, 1'b0);
  bufif1 vamos_ams_hiz_5 (\\wire , 1'b0, 1'b0);
endmodule
""")

    def test_generate_loop_markers(self):
        lines = shells.markers([CutPort(0, "v", REAL, OUTPUT, OUTPUT), CutPort(1, "q", LOGIC, OUTPUT, OUTPUT,
                                                                             "[W-1:0]")])
        self.assertEqual(lines, [
            "  genvar vamos_ams_g_q;",
            "  for (vamos_ams_g_q = ((W-1) < (0) ? (W-1) : (0)); vamos_ams_g_q <= ((W-1) > (0) ? (W-1) : (0)); "
            "vamos_ams_g_q = vamos_ams_g_q + 1) begin : vamos_ams_hiz_q",
            "    bufif1 vamos_ams_hiz (q[vamos_ams_g_q], 1'b0, 1'b0);",
            "  end"])

    def test_header_shell(self):
        pp = vp.from_text("module dac #(parameter N = 4) (d, q, v);\n  localparam W = N + 1;\n"
                          "  input [W-1:0] d; output reg [N-1:0] q; output v; real v;\n"
                          "  always @* q = d;\nendmodule\n")
        h = vp.module_header(pp, "dac")
        ports = [CutPort(k, p.name, p.kind, p.direction, p.direction, p.range_text, p.msb, p.lsb)
                 for k, p in enumerate(h.ports)]
        text = shells.header_shell("dac", h, ports)
        self.assertEqual(text.splitlines()[:5], ["module dac #(parameter N = 4) (d, q, v);",
                                                 "  localparam W = N + 1;", "  input [W-1:0] d;",
                                                 "  output [N-1:0] q;", "  output real v;"])
        self.assertIn("begin : vamos_ams_hiz_q", text)
        self.assertNotIn("always", text)
        self.assertNotIn("reg", text)
        placeholder = shells.header_shell("dac", h, extra=["vamos_ams_probe_0 vamos_ams_probe ();"])
        self.assertNotIn("bufif1", placeholder)
        self.assertIn("  vamos_ams_probe_0 vamos_ams_probe ();\nendmodule", placeholder)

    def test_locator(self):
        pp = vp.from_text("module t; endmodule\n")
        loc = shells.Locator(pp, 3, {"inv_sp": (5, 9)}, {"drv": "buft_sp"})
        self.assertEqual([loc.where(1), loc.where(4), loc.where(6)],
                         ["pp.orig.v:1", "pp.v:4", "pp.v:6 (shell of inv_sp)"])
        n = loc.verbatim(vp.Diag("pp.v", 1, "error", "Unable to bind wire/reg/memory `drv.save' in `main'"))
        self.assertIn("(drv is an instance of SPICE cell buft_sp: hierarchical references into SPICE cells are "
                      "not supported)", n.message)
        n = loc.verbatim(vp.Diag("pp.v", 1, "error", "Unable to bind wire/reg/memory `other.save' in `main'"))
        self.assertNotIn("SPICE", n.message)

    def test_vid(self):
        self.assertEqual(shells.vid("a_1"), "a_1")
        self.assertEqual(shells.vid("wire"), "\\wire ")
        self.assertEqual(shells.vid("a.b"), "\\a.b ")


class TestClassify(unittest.TestCase):
    """The direction-probe parser on real iverilog output (fixtures captured in WSL)."""

    def test_inout_probe(self):
        hits, procs, params, other = shells.classify(vp.parse_diags(read_fixture("verilog_iv_probe_inout.txt"))[0])
        self.assertEqual([(h.cell, h.port, h.line, h.expr, h.kind) for h in hits],
                         [("inv_sp", "a", 7, "r1", "inout"), ("inv_sp", "y", 7, "l1", "inout"),
                          ("inv_sp", "b", 7, "v['sd2:'sd1]", "inout"), ("inv_sp", "b", 8, "{l1, r1}", "inout")])
        self.assertEqual((procs, params, other), ([], [], []))           # companions dropped

    def test_output_probe(self):
        hits, procs, params, other = shells.classify(vp.parse_diags(read_fixture("verilog_iv_probe_output.txt"))[0])
        self.assertEqual([(h.port, h.line, h.expr, h.kind) for h in hits], [("b", 8, "tbp.{l1, r1}", "output")])
        self.assertEqual(procs, [("r1", 9), ("r1", 9)])
        self.assertEqual((params, other), ([], []))

    def test_parameter_messages(self):
        hits, procs, params, other = shells.classify(vp.parse_diags(read_fixture("verilog_iv_params.txt"))[0])
        self.assertEqual([(d.line, d.kind) for d in params], [(3, "warning"), (4, "error"), (5, "error")])
        self.assertEqual((hits, procs, other), ([], [], []))

    def test_vars_in(self):
        self.assertEqual(shells.vars_in("{l1, r1}"), {"l1", "r1"})
        self.assertEqual(shells.vars_in("v['sd2:'sd1]"), {"v"})
        self.assertEqual(shells.vars_in("tb.u.rr[i]"), {"rr", "i"})
        self.assertEqual(shells.vars_in("1'd0"), set())


def spice_cell(name, ports, declared=None):
    """A SPICE-only CutCell whose ports are auto unless `declared` (port -> direction) says otherwise."""
    declared = declared or {}
    cps = [CutPort(k, p, LOGIC, declared.get(p, AUTO), declared.get(p, INOUT)) for k, p in enumerate(ports)]
    return CutCell(name=name, view="spice", subckt=name, ports=cps, origin="cells.sp:1")


def shell_result(*cells, multi=()):
    res = shells.ShellResult(path="pp.v", spice_only=[c.name for c in cells], multi_view=list(multi))
    for c in cells:
        res.cells[c.name] = c
    return res


# Every shape of probe step 2b: an auto port on an input port of its module, and what the
# module's instance connects that input port to.
WRAP_TB = """module tb;
  reg clk = 0;
  wire w;
  logic l;
  reg [1:0] rr;
  wire [1:0] ww;
  parameter P = 1'b1;
  assign w = clk;
  wrap_v uv (.a(clk));
  wrap_w uw (.a(w));
  wrap_e ue (.a(~w));
  wrap_k uk (.a(P));
  wrap_o uo (.a());
  wrap_y uy [1:0] (.a(ww));
  outer_v ov (.b(clk));
  outer_w ow (.b(w));
  wsel_v sv (.q(rr));
  wsel_w sw (.q(ww));
  wrap_m um1 (.a(w));
  wrap_m um2 (.a(l));
  wrap_c uc (.a({w, rr[0]}));
  wrap_cw ucw (.a({w, ww[0]}));
  wrap_p up (clk);
endmodule
module wrap_v (input a); src_sp u (.a(a)); endmodule
module wrap_w (input a); src_sp u (.a(a)); endmodule
module wrap_e (input a); src_sp u (.a(a)); endmodule
module wrap_k (input a); src_sp u (.a(a)); endmodule
module wrap_o (input a); src_sp u (.a(a)); endmodule
module wrap_y (input a); src_sp u (.a(a)); endmodule
module outer_v (input b); inner_v i (.a(b)); endmodule
module inner_v (input a); src_sp u (.a(a)); endmodule
module outer_w (input b); inner_w i (.a(b)); endmodule
module inner_w (input a); src_sp u (.a(a)); endmodule
module wsel_v (input [1:0] q); src_sp u (.a(q[1])); endmodule
module wsel_w (input [1:0] q); src_sp u (.a(q[1])); endmodule
module wrap_m (input a); src_sp u (.a(a)); endmodule
module wrap_c (input [1:0] a); bus_sp u (.d(a)); endmodule
module wrap_cw (input [1:0] a); bus_sp u (.d(a)); endmodule
module wrap_p (a); input a; src_sp u (a); endmodule
module wrap_out (output a); src_sp u (.a(a)); endmodule
module tb_unused;
  reg r;
  wrap_w u2 (.a(r));
endmodule
"""


def wrap_line(text):
    return 1 + WRAP_TB[:WRAP_TB.index(text)].count("\n")


class TestWrapperInputs(unittest.TestCase):
    """Direction probe step 2b (shells.wrapper_inputs), on the structural scan alone."""

    def needs(self, pp, res, top="tb", live=None):
        return shells.wrapper_inputs(pp, res, top, live)

    def test_every_shape(self):
        pp = vp.from_text(WRAP_TB)
        got = self.needs(pp, shell_result(spice_cell("src_sp", ["a"]), spice_cell("bus_sp", ["d"])))

        def at(scope, why):
            return "%s.u (pp.orig.v:%d), connected to %s" % (scope, wrap_line("module %s " % scope), why)

        o = wrap_line
        self.assertEqual(got[("src_sp", "a")], [
            at("wrap_v", "input port wrap_v.a, which tb.uv (pp.orig.v:%d) connects to variable clk" % o("wrap_v uv")),
            at("wrap_e", "input port wrap_e.a, which tb.ue (pp.orig.v:%d) connects to the expression ~w"
               % o("wrap_e ue")),
            at("wrap_k", "input port wrap_k.a, which tb.uk (pp.orig.v:%d) connects to constant P" % o("wrap_k uk")),
            at("wrap_o", "input port wrap_o.a, which tb.uo (pp.orig.v:%d) leaves unconnected" % o("wrap_o uo")),
            at("wrap_y", "input port wrap_y.a of the instance array tb.uy (pp.orig.v:%d) (iverilog coerces no "
                         "port of an instance array)" % o("wrap_y uy")),
            at("inner_v", "input port inner_v.a, which outer_v.i (pp.orig.v:%d) connects to input port outer_v.b, "
                          "which tb.ov (pp.orig.v:%d) connects to variable clk" % (o("module outer_v"), o("outer_v ov"))),
            at("wsel_v", "q[1], a select of input port wsel_v.q, which tb.sv (pp.orig.v:%d) connects to variable rr"
               % o("wsel_v sv")),
            at("wrap_m", "input port wrap_m.a, which tb.um2 (pp.orig.v:%d) connects to variable l" % o("wrap_m um2")),
            at("wrap_p", "input port wrap_p.a, which tb.up (pp.orig.v:%d) connects to variable clk" % o("wrap_p up")),
        ])
        # nets are collapsed into the input port (iverilog coerces it to inout): wrap_w, inner_w, wsel_w,
        # wrap_m's um1 (um2 decides), {w, ww[0]}; an output port is not an input port; tb_unused is not
        # under the top, so its variable on wrap_w does not count
        self.assertEqual(got[("bus_sp", "d")], [
            "wrap_c.u (pp.orig.v:%d), connected to input port wrap_c.a, which tb.uc (pp.orig.v:%d) connects to "
            "variable rr" % (o("module wrap_c "), o("wrap_c uc"))])
        self.assertEqual(sorted(got), [("bus_sp", "d"), ("src_sp", "a")])

    def test_live_masked_declared_and_top(self):
        pp = vp.from_text(WRAP_TB)
        res = shell_result(spice_cell("src_sp", ["a"]))
        live = {"src_sp": [wrap_line("module wrap_e "), wrap_line("module wrap_w ")]}
        self.assertEqual([w.split(",")[0] for w in self.needs(pp, res, live=live)[("src_sp", "a")]],
                         ["wrap_e.u (pp.orig.v:%d)" % wrap_line("module wrap_e ")])   # only elaborated instances
        res = shell_result(spice_cell("src_sp", ["a"]), multi=["wrap_v", "inner_v", "wrap_e", "wrap_k", "wrap_o",
                                                               "wrap_y", "wsel_v", "wrap_m"])
        self.assertEqual([w.split(",")[0] for w in self.needs(pp, res)[("src_sp", "a")]],
                         ["wrap_p.u (pp.orig.v:%d)" % wrap_line("module wrap_p ")])   # masked bodies do not count
        res = shell_result(spice_cell("src_sp", ["a"], declared={"a": INPUT}), spice_cell("bus_sp", ["d"]))
        self.assertEqual(sorted(self.needs(pp, res)), [("bus_sp", "d")])               # a declared port is the user's
        res = shell_result(spice_cell("src_sp", ["a"]))
        self.assertEqual(self.needs(pp, res, top="tb_unused"),                         # only what is under the top
                         {("src_sp", "a"): ["wrap_w.u (pp.orig.v:%d), connected to input port wrap_w.a, which "
                                            "tb_unused.u2 (pp.orig.v:%d) connects to variable r"
                                            % (wrap_line("module wrap_w "), wrap_line("wrap_w u2"))]})
        top = vp.from_text("module top2 (input a, output y);\n  src_sp u (.a(a));\n  src_sp v (.a(y));\nendmodule\n")
        res = shell_result(spice_cell("src_sp", ["a"]))
        self.assertEqual(self.needs(pp=top, res=res, top="top2"),
                         {("src_sp", "a"): ["top2.u (pp.orig.v:2), connected to input port top2.a of the top module"]})
        self.assertEqual(self.needs(pp=top, res=res, top="tb"), {})                   # top2 is never instantiated


def call_args(text, paren):
    """(positional count, keyword names) of the call whose "(" is text[paren]."""
    depth, parts, cur = 0, [], []
    for ch in text[paren:]:
        if ch in "([{":
            depth += 1
            if depth == 1:
                continue
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                break
        if depth == 1 and ch == ",":
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    parts = [p for p in parts + ["".join(cur).strip()] if p]
    kws = [m.group(1) for m in (re.match(r"(\w+)\s*=(?!=)", p) for p in parts) if m]
    return len(parts) - len(kws), kws


class TestDocs(unittest.TestCase):
    """What the module docstrings tell a reader about flow.py, the cut and the fixtures."""

    def flow_text(self):
        with open(os.path.join(os.path.dirname(shells.__file__), "flow.py"), encoding="utf-8") as fh:
            return fh.read()

    def test_usage_sample_follows_flow(self):
        # every call of the "Use from ams/flow.py" sample binds to the real signature and is one
        # flow.py makes (keywords included); every attribute it sets, flow.py sets the same way
        # (it once set pp.case_sensitive, which flow.py does not)
        doc = shells.__doc__
        lines = doc[doc.index("Use from ams/flow.py"):].splitlines()[1:]
        sample = "\n".join(ln[4:] for ln in lines if ln.startswith("    "))
        flow = self.flow_text()
        mods = {"verilog_ports": vp, "shells": shells, "cut": cut}
        seen = []
        for m in re.finditer(r"\b(verilog_ports|shells|cut)\.(\w+)\(", sample):
            fn = getattr(mods[m.group(1)], m.group(2), None)
            self.assertTrue(callable(fn), m.group(0))
            npos, kws = call_args(sample, m.end() - 1)
            try:
                inspect.signature(fn).bind(*([None] * npos), **{k: None for k in kws})
            except TypeError as e:
                self.fail("%s...): %s" % (m.group(0), e))
            self.assertTrue("%s.%s" % (m.group(1), m.group(2)) in flow, "flow.py makes no %s...) call" % m.group(0))
            for k in kws:
                self.assertTrue(re.search(r"\b%s=" % k, flow), "flow.py passes no %s= (%s...))" % (k, m.group(0)))
            seen.append(m.group(2))
        self.assertTrue({"preprocess", "find_top", "precheck", "build", "analyse", "assign_roles"} <= set(seen),
                        seen)
        sets = re.findall(r"^(\w+(?:\.\w+)+ = [^#\n]*?)\s*(?:#.*)?$", sample, re.M)
        self.assertEqual(sets, ["job.precision = pp.precision"])
        for s in sets:
            self.assertTrue(s in flow, "flow.py does not do %s" % s)
        self.assertFalse(re.search(r"\.case_sensitive\s*=", flow),   # the sample says flow.py leaves it False
                         "flow.py sets pp.case_sensitive")

    def test_port_buffers_are_the_cuts(self):
        # step 2b's account of a cell port behind a buffered wrapper input: tgt-vhdl draws the buffer
        # (PB_<label>_<port>, T8) and the cut joins it one way or stops with an error, as cut.py's
        # "port buffers" section says; nothing splits the net at the port any more
        self.assertTrue("\n  * port buffers:" in cut.__doc__, "cut.py has no port buffers section")
        for what, doc in (("module", shells.__doc__), ("wrapper_inputs", shells.wrapper_inputs.__doc__)):
            text = " ".join(doc.split())
            for phrase, want in (("splits the net", False), ('cut.py, "port buffers"', True),
                                 ("PB_<label>_<port>", True), ("one way", True)):
                self.assertEqual(phrase in text, want, "%s docstring: %r" % (what, phrase))

    def test_cut_fixture_readme_lists_every_case(self):
        # fixtures/vhdl/README.cut describes every cut_<case>/ (tb.v with shells in this module's
        # form); cut_portbuf was missing
        d = fixture("vhdl")
        with open(os.path.join(d, "README.cut"), encoding="utf-8") as fh:
            listed = set(re.findall(r"^  (\w+) {2,}\S", fh.read(), re.M))
        cases = {n[len("cut_"):] for n in os.listdir(d)
                 if n.startswith("cut_") and os.path.isfile(os.path.join(d, n, "tb.v"))}
        self.assertIn("portbuf", cases)
        self.assertEqual((sorted(cases - listed), sorted(listed - cases)), ([], []))


# -- the real tools ----------------------------------------------------------------

CELLSET_TB = """`timescale 1ns/1ps
module wrap (input a, output y);
  inv_sp u1 (.a(a), .y(y));
  dac ud (.d({a, a}), .v());
endmodule
module dac (input [1:0] d, output real v);
  nested_sp n ();
endmodule
module unused_mv (input a); endmodule
module tb;
  reg a = 0; wire y1, y2;
  wrap w1 (.a(a), .y(y1));
  wrap w2 (.a(a), .y(y2));
  generate if (0) begin : g0 never_sp q (); end endgenerate
endmodule
"""

XLAT_CELLS = """`timescale 1ns/1ps
module dac #(parameter N = 4) (input [N-1:0] d, output [N-1:0] q, output real v);
  assign q = d;
  always @* v = d * 0.1;
endmodule
module adc (vin, code);
  parameter K = 3;
  localparam W = K + 1;
  input vin; real vin;
  output [W-1:0] code;
  reg [W-1:0] code;
  always @(vin) code = vin * 2;
endmodule
"""

XLAT_TB = """`timescale 1ns/1ps
module tb;
  reg clk = 0;
  always #5 clk = ~clk;
  wire [3:0] v;
  wire [1:0] w;
  wire [1:0] padbus;
  wire pad1;
  wire [7:0] q8;
  wire [3:0] q4;
  wire [5:0] code;
  reg [7:0] d8 = 8'h5a;
  reg [3:0] d4 = 4'h3;
  wire real v4, v8;
  real vin = 0.25;                      // an undriven `wire real` trips tgt-vhdl (vin <= L3D_Z)
  assign pad1 = clk ? 1'bz : 1'b0;
  inv_sp u0 (.a(clk), .y(v[0]));
  genvar i;
  for (i = 1; i < 2; i = i + 1) begin : g
    inv_sp ug (.a(clk), .y(v[i]));
  end
  inv_sp ua [1:0] (.a(clk), .y(v[3:2]));
  inv_sp p0 (.a(clk), .y(w[0]));
  inv_sp p1 (.a(clk), .y(w[1]));
  pad_sp pd (.pad(pad1), .pbus(padbus));
  dac #(.N(8)) u8 (.d(d8), .q(q8), .v(v8));
  dac #(.N(4)) u4 (.d(d4), .q(q4), .v(v4));
  adc #(.K(5)) uc (.vin(vin), .code(code));
  initial #30 $finish;
endmodule
`default_nettype none
"""


def node_with(nodes, name):
    """The one analog node that has `name` (a cut-port bit or a net path) as its name or an alias."""
    got = [n for n in nodes if name == n.canonical or name in n.aliases]
    if len(got) != 1:
        raise AssertionError("%s is on %d nodes: %s" % (name, len(got), [(n.canonical, n.aliases) for n in nodes]))
    return got[0]


@needs_stack
class TestShellsStack(TempDir):
    def setUp(self):
        super().setUp()
        os.environ.setdefault("VAMOS_LAUNCHER", os.path.join(vp.tools.package_root(), "bin", "vamos"))

    # -- translating a built pp.v and cutting it, as flow.py does (probe step 2b) --

    def stack_env(self):
        nvc = tools.find_real("nvc")
        return dict(os.environ, NVC=nvc, NVC_LIBDIR=tools.nvc_libdir(nvc), IVERILOG=tools.find_real("iverilog"))

    def translate(self, path, out):
        """iverilog-sv2ghdl -g2012 -s tb `path` into `out` (flow.py step 8); design.vhd's text."""
        r = run([os.path.join(vp.tools.package_root(), "bin", "iverilog-sv2ghdl"), "-o", out, "-g2012",
                 "-s", "tb", path], cwd=self.tmp, env=self.stack_env())
        vhd = os.path.join(out, "design.vhd")
        self.assertTrue(r.returncode == 0 and os.path.isfile(vhd), r.stdout[-3000:])
        with open(vhd) as fh:
            return fh.read()

    def elaborate(self, out):
        nvc = tools.find_real("nvc")
        e = run([nvc, "--std=2040", "-L", tools.nvc_libdir(nvc), "-e", "tb"], cwd=out, env=self.stack_env())
        self.assertEqual(e.returncode, 0, e.stdout[-2000:])

    def cut_nodes(self, out, cells, nl, pp, directions=None):
        """cut.analyse + cut.assign_roles on <out>/design.vhd (flow.py step 9)."""
        ana = cut.analyse(vhdl.parse(os.path.join(out, "design.vhd")), "tb", cells, nl, cfg(), RuleHits(),
                          pp=pp, directions=directions)
        return cut.assign_roles(ana, names.NameAllocator(), lambda n: False, lambda n: (False, None),
                                directions=directions)

    def prep(self, files, sources=None, **jobkw):
        for name, text in files.items():
            self.write(name, text)
        srcs = sources or list(files)
        job = Job("vcs", cwd=self.tmp, sources=[Source(os.path.join(self.tmp, s), "sv") for s in srcs], **jobkw)
        pp = vp.preprocess(job, os.path.join(self.tmp, "ams", "pp.orig.v"))
        self.assertFalse([n for n in pp.notes if n.severity == ERROR], [n.text() for n in pp.notes])
        return job, pp

    def test_cell_set(self):
        job, pp = self.prep({"tb.v": CELLSET_TB})
        c = cfg(use_spice=[UseSpice(cells=[("dac", "")], origin="vcsAD.init:2"),
                           UseSpice(cells=[("unused_mv", "")], origin="vcsAD.init:3"),
                           UseSpice(cells=[("ghost_*", "")], origin="vcsAD.init:4")])
        nl = netlist(sub("inv_sp", ["a", "y"]), sub("dac", ["d[1]", "d[0]", "v"]), sub("unused_mv", ["a"]))
        top = vp.find_top(pp, job, shells.cell_globs(c))
        self.assertEqual(top, "tb")
        so, mv, notes = shells.cell_set(pp, top, c, nl)
        self.assertEqual((so, mv), (["inv_sp"], ["dac"]))           # nested_sp, never_sp: not elaborated
        self.assertEqual([(n.severity, n.origin, n.message) for n in notes],
                         [(NOTE, "tb.v:9", "cell unused_mv not instantiated under tb; ignored"),
                          (NOTE, "vcsAD.init:4", "cell ghost_* not instantiated; ignored")])
        _, pp2 = self.prep({"tb.v": CELLSET_TB.replace("endgenerate", "endgenerate\n  nosuch u9 ();")})
        so, mv, notes = shells.cell_set(pp2, "tb", c, nl)
        self.assertEqual([(n.severity, n.origin, n.message) for n in notes if n.severity == ERROR],
                         [(ERROR, "tb.v:15", "module nosuch not found in Verilog sources or SPICE netlists")])
        so, mv, notes = shells.cell_set(pp, "dac", c, nl)
        self.assertIn("SPICE-top designs are not supported", notes[0].message)

    def test_direction_probe(self):
        job, pp = self.prep({"tb.sv": """`timescale 1ns/1ps
module tb;
  reg clk = 0;
  always #5 clk = ~clk;
  logic yl;
  wire w;
  reg [1:0] code;
  wire [1:0] wv;
  initial code = 2'b01;
  inv_sp u1 (.A(clk), .Y(yl));
  inv_sp u2 (.A(w), .Y(w));
  dac2_sp ud (.d(code), .out(wv[0]), .pad(wv[1]));
  always @(yl) $display("%b", yl);
endmodule
"""})
        nl = netlist(sub("inv_sp", ["a", "y"]), sub("dac2_sp", ["d<1>", "d<0>", "out", "pad", "vdd"]))
        c = cfg(bus_formats=["<%d>"], port_connects=[PortConnect("dac2_sp", None, [("vdd", "vdd", False)], "i:1")])
        res = shells.build(pp, "tb", c, nl, job)
        self.assertEqual(res.notes, [])
        self.assertEqual([(p.verilog, p.declared, p.shell_dir, p.range_text) for p in res.cells["inv_sp"].ports],
                         [("A", AUTO, INPUT, None), ("Y", AUTO, OUTPUT, None)])
        self.assertEqual([(p.verilog, p.shell_dir, p.range_text) for p in res.cells["dac2_sp"].ports],
                         [("d", INPUT, "[1:0]"), ("out", INOUT, None), ("pad", INOUT, None)])
        self.assertIn("clk is assigned procedurally at tb.sv:3", res.directions["inv_sp"]["A"])   # the initializer
        self.assertTrue(res.directions["inv_sp"]["Y"].startswith("auto->output (tb.u1 (tb.sv:10), connected "
                                                                 "to variable yl"))
        self.assertEqual(res.directions["dac2_sp"]["out"], "auto->inout (VCS default; port_dir is faster)")
        self.assertEqual(res.removed["dac2_sp"], ["vdd"])
        self.assertEqual([(x.scope, x.name, x.origin) for x in res.instances["inv_sp"]],
                         [("tb", "u1", "tb.sv:10"), ("tb", "u2", "tb.sv:11")])
        with open(res.path) as fh:
            text = fh.read()
        self.assertEqual(text, res.text)
        self.assertEqual(text[:len(pp.text)].count("\n"), pp.text.count("\n"))    # user lines unchanged
        self.assertIn("`resetall\n`timescale 1ps/1ps\n`default_nettype wire\nmodule inv_sp (A, Y);\n"
                      "  input A;\n  output Y;\n  bufif1 vamos_ams_hiz_0 (Y, 1'b0, 1'b0);\nendmodule\n", text)
        first, last = res.shell_lines["inv_sp"]
        self.assertEqual(text.splitlines()[first - 1], "module inv_sp (A, Y);")
        self.assertEqual(text.splitlines()[last - 1], "endmodule")

    def test_mixed_instances_need_port_dir(self):
        job, pp = self.prep({"tb.sv": """`timescale 1ns/1ps
module tb;
  reg r = 0;
  logic l;
  always #5 r = ~r;
  inv_sp u1 (.a(r), .y());
  inv_sp u2 (.a(l), .y());
  always @(l) $display("%b", l);
endmodule
"""})
        with self.assertRaises(NoteError) as cm:
            shells.build(pp, "tb", cfg(), netlist(sub("inv_sp", ["a", "y"])), job)
        msg = cm.exception.notes[0].message
        self.assertIn("auto port a of SPICE cell inv_sp needs different directions", msg)
        self.assertIn("input at tb.u1 (tb.sv:6), connected to r; r is assigned procedurally at tb.sv:3", msg)
        self.assertIn("output at tb.u2 (tb.sv:7), connected to variable l", msg)
        self.assertIn("port_dir -cell inv_sp", msg)

    def test_wrapper_input_ports(self):
        """Probe step 2b with the real tools (e2e 11 with every port auto): an auto port on a wrapper's
        input port that the wrapper's instance drives from a variable becomes input (iverilog keeps the
        port an input); on a wire-driven wrapper (iverilog coerces the port to inout) it stays inout.  The
        built pp.v translates with no port buffer (wrap_e.a stays an `in` port on clk itself) and
        elaborates, and the cut puts both cell inputs on tb.clk's node.  With those auto ports left
        inout, their markers make the iverilog core buffer the wrapper ports: tgt-vhdl draws the buffers
        in tb (T8: PB_<label>_<port> <= clk) and the cut joins each one way, to the same node, the cell
        ports acting as inputs (shells.py module docstring, step 2b; cut.py, "port buffers")."""
        job, pp = self.prep({"tb.sv": """`timescale 1ns/1ps
module tb;
  reg clk = 1'b0;
  always #5 clk = ~clk;
  wire w, ve, q, vref;
  assign w = clk;
  wrap_e we (.a(clk), .y(ve));
  bgadc bg (.clk(clk), .vref(vref), .q(q));
  wrap_n wn (.a(w));
  always @(q) $display("%b", q);
endmodule
module wrap_e (input a, output y);
  src u (.a(a), .vo(y));
endmodule
module bgadc (input clk, output vref, output q);
  bg u1 (.vref(vref));
  adc u2 (.vin(vref), .clk(clk), .q(q));
endmodule
module wrap_n (input a);
  sink s (.a(a), .q());
endmodule
"""})
        nl = netlist(sub("src", ["a", "vo"]), sub("bg", ["vref"]), sub("adc", ["vin", "clk", "q"]),
                     sub("sink", ["a", "q"]))
        res = shells.build(pp, "tb", cfg(), nl, job)
        self.assertEqual(res.notes, [])
        self.assertEqual({(c, p.verilog): p.shell_dir for c, cell in res.cells.items() for p in cell.ports},
                         {("src", "a"): INPUT, ("src", "vo"): INOUT, ("bg", "vref"): INOUT, ("adc", "vin"): INOUT,
                          ("adc", "clk"): INPUT, ("adc", "q"): INOUT, ("sink", "a"): INOUT, ("sink", "q"): INOUT})
        self.assertEqual(res.directions["src"]["a"], "auto->input (wrap_e.u (tb.sv:13), connected to input port "
                                                     "wrap_e.a, which tb.we (tb.sv:7) connects to variable clk)")
        self.assertEqual(res.directions["adc"]["clk"], "auto->input (bgadc.u2 (tb.sv:17), connected to input port "
                                                       "bgadc.clk, which tb.bg (tb.sv:8) connects to variable clk)")
        self.assertEqual(res.directions["sink"]["a"], "auto->inout (VCS default; port_dir is faster)")

        out = os.path.join(self.tmp, "nvc")
        vhd = self.translate(res.path, out)
        self.assertRegex(vhd, r"entity wrap_n\w* is\s+port \(\s+a : inout resolved_logic3d")   # coerced
        self.assertRegex(vhd, r"entity wrap_e\w* is\s+port \(\s+a : in logic3d;")            # nothing drives it
        direct = r"\n\s*we: entity work\.wrap_e\w*\s+port map \(\s*a => clk,"    # one net: clk itself
        self.assertRegex(vhd, direct)
        self.assertNotIn("Port buffer", vhd)
        self.elaborate(out)
        nodes = self.cut_nodes(out, res.cells, nl, pp, res.directions)
        n = node_with(nodes, "tb.we.u.a")
        self.assertIs(n, node_with(nodes, "tb.bg.u2.clk"))
        self.assertIn("tb.clk", n.aliases)                       # a d2a rule on tb.clk reaches both cells
        self.assertEqual(n.role, D2A)
        self.assertIn("direction: auto→input (wrap_e.u (tb.sv:13), connected to input port wrap_e.a, which "
                      "tb.we (tb.sv:7) connects to variable clk) tb.we.u.a", n.report)

        # the shells as the probe would leave them without step 2b (src.a and adc.clk inout, with their
        # markers): the core buffers wrap_e.a and bgadc.clk, tgt-vhdl declares them inout and draws the
        # buffers in tb (T8), and the cut joins each one way: the node above, not a split
        bad = re.sub(r"(module (?:src|adc) \([^)]*\);\n(?:  \w+ \w+;\n)*?)  input (a|clk);\n",
                     lambda m: m.group(1) + "  inout %s;\n  bufif1 vamos_ams_hiz_9 (%s, 1'b0, 1'b0);\n"
                     % (m.group(2), m.group(2)), res.text)
        self.assertEqual(bad.count("vamos_ams_hiz_9"), 2)
        self.write("bad.v", bad)
        out = os.path.join(self.tmp, "nvc_bad")
        vbad = self.translate(os.path.join(self.tmp, "bad.v"), out)
        self.assertNotRegex(vbad, direct)
        self.assertRegex(vbad, r"entity wrap_e\w* is\s+port \(\s+a : inout resolved_logic3d;")
        self.assertRegex(vbad, r"entity bgadc\w* is\s+port \(\s+clk : inout resolved_logic3d;")
        self.assertIn("PB_we_a <= clk;", vbad)
        self.assertRegex(vbad, r"\bPB_bg\w*_clk <= clk;")
        self.elaborate(out)
        cells = copy.deepcopy(res.cells)
        for c, p in (("src", "a"), ("adc", "clk")):
            [cp] = [x for x in cells[c].ports if x.verilog == p]
            cp.shell_dir = INOUT
        nodes = self.cut_nodes(out, cells, nl, pp)
        nb = node_with(nodes, "tb.we.u.a")
        self.assertIs(nb, node_with(nodes, "tb.bg.u2.clk"))
        self.assertEqual((nb.canonical, nb.role, nb.aliases, len(nb.ports)),
                         (n.canonical, n.role, n.aliases, len(n.ports)))
        self.assertIn("direction: auto→inout→input (one-way port buffer: input port tb.we.a fed from variable "
                      "tb.clk) tb.we.u.a", nb.report)
        self.assertIn("direction: auto→inout→input (one-way port buffer: input port tb.bg.clk fed from variable "
                      "tb.clk) tb.bg.u2.clk", nb.report)

    def test_wrapper_input_unknown_variable(self):
        """Probe step 2b is structural: it knows only the variables the declaration scan records, so an
        auto port behind a wrapper input that a user-typed variable feeds stays inout (shells.py module
        docstring, step 2b).  Its marker makes the core buffer the port, and the cut joins that buffer
        one way: the cell port is on the variable's node, as an input.  Where the wrapper also reads the
        port, the cut stops with an error naming port_dir input instead of mis-simulating."""
        tb = """`timescale 1ns/1ps
module tb;
  typedef logic bit_t;
  bit_t clk = 1'b0;
  always #5 clk = ~clk;
  wire y, q;
  cin c1 (.a(clk));
  wrap w (.a(clk), .y(y), .q(q));
  always @(y or q) $display("%%b %%b", y, q);
endmodule
module wrap (input a, output y, output q);
  src u (.a(a), .vo(y));
  assign q = %s;
endmodule
"""
        nl = netlist(sub("src", ["a", "vo"]), sub("cin", ["a"]))
        for case, q in (("joined", "1'b0"), ("read", "~a")):
            with self.subTest(case=case):
                job, pp = self.prep({case + "/tb.sv": tb % q})
                res = shells.build(pp, "tb", cfg(), nl, job)
                self.assertEqual(res.notes, [])
                self.assertEqual([(p.verilog, p.declared, p.shell_dir) for p in res.cells["src"].ports],
                                 [("a", AUTO, INOUT), ("vo", AUTO, INOUT)])       # the scan misses bit_t clk
                self.assertEqual(res.directions["src"]["a"], "auto->inout (VCS default; port_dir is faster)")
                out = os.path.join(self.tmp, case, "nvc")
                vhd = self.translate(res.path, out)
                self.assertIn("PB_w_a <= clk;", vhd)
                self.assertRegex(vhd, r"entity wrap(?:__\w+)? is\s+port \(\s+a : inout resolved_logic3d;")
                self.elaborate(out)
                if case == "joined":
                    nodes = self.cut_nodes(out, res.cells, nl, pp, res.directions)
                    n = node_with(nodes, "tb.w.u.a")
                    self.assertIs(n, node_with(nodes, "tb.c1.a"))           # with clk's other SPICE input
                    self.assertIn("tb.clk", n.aliases)
                    self.assertEqual(n.role, D2A)
                    self.assertIn("direction: auto→inout→input (one-way port buffer: input port tb.w.a fed from "
                                  "tb.clk) tb.w.u.a", n.report)
                    continue
                with self.assertRaises(NoteError) as cm:
                    self.cut_nodes(out, res.cells, nl, pp, res.directions)
                self.assertEqual([n.message for n in cm.exception.notes if n.severity == ERROR], [
                    "port a of tb.w.u is inout behind input port tb.w.a, fed one way from tb.clk (a port buffer), "
                    "and tb.w reads that port: what the cell drives there would reach only the wrapper's side, "
                    "which vamos does not model; declare it input (port_dir -cell src (input a;)), or connect a "
                    "net to tb.w.a"])

    def test_wrapper_input_conflict(self):
        """An auto port needing input on a wrapper's input port (step 2b) and output on a read-only
        variable elsewhere: the per-cell error names both instances and suggests port_dir."""
        job, pp = self.prep({"tb.sv": """`timescale 1ns/1ps
module tb;
  reg r = 1'b0;
  logic l;
  always #5 r = ~r;
  wrap w (.a(r));
  inv_sp u2 (.a(l), .y());
  always @(l) $display("%b", l);
endmodule
module wrap (input a);
  inv_sp u1 (.a(a), .y());
endmodule
"""})
        with self.assertRaises(NoteError) as cm:
            shells.build(pp, "tb", cfg(), netlist(sub("inv_sp", ["a", "y"])), job)
        self.assertEqual([(n.severity, n.origin) for n in cm.exception.notes], [(ERROR, "cells.sp:1")])
        self.assertEqual(cm.exception.notes[0].message,
                         "auto port a of SPICE cell inv_sp needs different directions: input at wrap.u1 (tb.sv:11), "
                         "connected to input port wrap.a, which tb.w (tb.sv:6) connects to variable r; output at "
                         "tb.u2 (tb.sv:7), connected to variable l; declare it with port_dir -cell inv_sp (input a;) "
                         "or (output a;), or connect wires")

    def test_xheep_style_cell(self):
        job, pp = self.prep({"tb_top.sv": """`timescale 1ns/1ps
module tb_top;
  logic [1:0] sel;
  logic out;
  ams_adc_1b ams_adc_1b_i (
      .sel(sel),
      .out(out)
  );
  initial begin sel = 2'b00; #100 sel = 2'b11; end
  always @(out) $display("%t out=%b", $time, out);
endmodule
"""})
        s = Subckt("ams_adc_1b", ["out", "sel<1>", "sel<0>", "vdd"],
                   orig_ports=["gnd", "out", "sel<1>", "sel<0>", "vdd"], gnd_ports=[0], origin="adc.sp:89")
        c = cfg(bus_formats=["<%d>"],
                port_connects=[PortConnect("ams_adc_1b", None, [("vdd", "vdd", False), ("gnd", "gnd", False)],
                                           "control.init:6")],
                port_dirs={"ams_adc_1b": PortDir("ams_adc_1b", {"sel": "input", "out": "output"}, "control.init:7")})
        nl = netlist(s, spelling={"out": "OUT", "sel<1>": "SEL<1>", "sel<0>": "SEL<0>"})
        top = vp.find_top(pp, job, shells.cell_globs(c))
        res = shells.build(pp, top, c, nl, job)
        self.assertEqual((top, res.spice_only), ("tb_top", ["ams_adc_1b"]))
        self.assertIn("module ams_adc_1b (out, sel);\n  output out;\n  input [1:0] sel;\n"
                      "  bufif1 vamos_ams_hiz_0 (out, 1'b0, 1'b0);\nendmodule\n", res.text)
        self.assertEqual(res.directions, {})                     # no auto port
        self.assertEqual(res.instances["ams_adc_1b"][0].origin, "tb_top.sv:5")

    def test_declared_output_on_assigned_variable(self):
        job, pp = self.prep({"tb.sv": """`timescale 1ns/1ps
module tb;
  reg r = 0;
  inv_sp u1 (.a(1'b0), .y(r));
endmodule
"""})
        c = cfg(port_dirs={"inv_sp": PortDir("inv_sp", {"a": "input", "y": "output"}, "i:1")})
        with self.assertRaises(NoteError) as cm:
            shells.build(pp, "tb", c, netlist(sub("inv_sp", ["a", "y"])), job)
        self.assertEqual([(n.origin, n.message.split(";")[0]) for n in cm.exception.notes],
                         [("tb.sv:3", "variable r is assigned procedurally and also driven through a SPICE cell "
                                      "output")])

    def test_generate_array_and_constant_actuals(self):
        job, pp = self.prep({"tb.sv": """`timescale 1ns/1ps
module tb;
  reg [1:0] rr;
  logic [1:0] q;
  reg [1:0] r2;
  logic [1:0] q2;
  initial begin rr = 1; r2 = 2; end
  genvar i;
  for (i = 0; i < 2; i = i + 1) begin : g
    inv_sp ug (.a(rr[i]), .y(q[i]), .t(1'b1));
  end
  inv_sp ua [1:0] (.a(r2), .y(q2), .t(2'b10));
  always @(q or q2) $display("%b %b", q, q2);
endmodule
"""})
        res = shells.build(pp, "tb", cfg(), netlist(sub("inv_sp", ["a", "y", "t"])), job)
        self.assertEqual([(p.verilog, p.shell_dir) for p in res.cells["inv_sp"].ports],
                         [("a", INPUT), ("y", OUTPUT), ("t", INPUT)])
        self.assertIn("which an output cannot drive", res.directions["inv_sp"]["t"])

    def test_parameter_overrides(self):
        job, pp = self.prep({"tb.v": """`timescale 1ns/1ps
module tb;
  wire a, y;
  inv_sp #(5) u1 (.a(a), .y(y));
  inv_sp #(.W(3)) u2 (.a(a), .y(y));
endmodule
"""})
        nl = netlist(sub("inv_sp", ["a", "y"]))
        with self.assertRaises(NoteError) as cm:
            shells.build(pp, "tb", cfg(), nl, job)
        self.assertEqual([(n.origin, n.message) for n in cm.exception.notes],
                         [("tb.v:4", "parameter override on SPICE instance tb.u1 is not passed to subckt inv_sp"),
                          ("tb.v:5", "parameter override on SPICE instance tb.u2 is not passed to subckt inv_sp")])
        job, pp = self.prep({"tb.v": "`timescale 1ns/1ps\nmodule tb;\n  wire a, y;\n  inv_sp u3 (.a(a), .y(y));\n"
                                     "  defparam u3.Q = 2;\nendmodule\n"})
        with self.assertRaises(NoteError) as cm:
            shells.build(pp, "tb", cfg(), nl, job)
        self.assertEqual([(n.origin, n.message) for n in cm.exception.notes],
                         [("tb.v:5", "parameter override (Q) on SPICE instance tb.u3 is not passed to subckt "
                                     "inv_sp")])

    def test_bus_auto_ports_offset_and_ascending(self):
        job, pp = self.prep({"tb.v": """`timescale 1ns/1ps
module tb;
  wire [5:4] x;
  wire [0:1] y;
  wire [1:2] z;
  bus_sp ub (.d(x), .e(y), .f(z));
endmodule
"""})
        nl = netlist(sub("bus_sp", ["d<5>", "d<4>", "e<0>", "e<1>", "f<2>", "f<1>"]))
        c = cfg(bus_formats=["<%d>"], use_spice=[UseSpice(cells=[("bus_sp", "")], index_order=[("f", "inc")])])
        res = shells.build(pp, "tb", c, nl, job)                     # the final iverilog probe is clean
        self.assertEqual([(p.verilog, p.range_text, p.shell_dir) for p in res.cells["bus_sp"].ports],
                         [("d", "[5:4]", INOUT), ("e", "[0:1]", INOUT), ("f", "[1:2]", INOUT)])
        for frag in ("inout [5:4] d;", "inout [0:1] e;", "inout [1:2] f;", "vamos_ams_hiz_0 (d[5],",
                     "vamos_ams_hiz_1 (d[4],", "vamos_ams_hiz_2 (e[0],", "vamos_ams_hiz_3 (e[1],",
                     "vamos_ams_hiz_4 (f[1],", "vamos_ams_hiz_5 (f[2],"):
            self.assertIn(frag, res.text)

    def test_rename_binding_and_port_hint(self):
        job, pp = self.prep({"tb.v": "`timescale 1ns/1ps\nmodule tb;\n  wire i, o;\n"
                                     "  inv_v u (.IN(i), .y(o));\nendmodule\n"})
        nl = netlist(sub("inv_sp", ["a", "y", "vdd"]))
        c = cfg(use_spice=[UseSpice(cells=[("inv_v", "inv_sp")], port_map=[("in", "a"), ("*", "snps_by_name")])],
                port_connects=[PortConnect("inv_sp", None, [("vdd", "vdd", False)], "i:1")])
        res = shells.build(pp, "tb", c, nl, job)
        cell = res.cells["inv_v"]
        self.assertEqual((cell.subckt, [p.verilog for p in cell.ports]), ("inv_sp", ["IN", "y"]))
        self.assertEqual(cell.portmap.explicit, {"in": "a"})
        self.assertEqual(cell.connects, {"vdd": "vdd"})
        _, pp = self.prep({"tb.v": "`timescale 1ns/1ps\nmodule tb;\n  wire i, o;\n"
                                   "  inv_v u (.IN(i), .y(o), .vdd(i));\nendmodule\n"})
        with self.assertRaises(NoteError) as cm:
            shells.build(pp, "tb", c, nl, job)
        self.assertIn("SPICE port vdd of cell inv_v is port_connect'ed", cm.exception.notes[0].message)
        self.assertEqual(cm.exception.notes[0].origin, "tb.v:4")

    def test_masking_shapes_run(self):
        """Masking on the preprocessed stream (§5.1): a cell reached only through `include whose body
        defines a macro used later and tested by a later `ifdef, a cell in a -v file, two alternative
        definitions under `ifdef/`else.  pp.v must compile and run as the original does."""
        self.write("inc/cells.vh", "module dac (input [1:0] d, output real v);\n`define OFFSET 3\n"
                   "`define HAVE_DAC_MODEL\n  always @* v = d * 0.45;\nendmodule\n")
        self.write("lib.v", "module adc (input real vin, output [1:0] code);\n  assign code = 2'b01;\nendmodule\n")
        job, pp = self.prep({"tb.v": """`timescale 1ns/1ps
`include "cells.vh"
module other;
  initial #1 $display("val=%0d", `OFFSET + 1);
`ifdef HAVE_DAC_MODEL
  initial #1 $display("branch=model");
`else
  initial #1 $display("branch=fallback");
`endif
endmodule
`ifdef ALT
module alt (output y); assign y = 1'b1; endmodule
`else
module alt (output y); assign y = 1'b0; endmodule
`endif
module tb;
  reg [1:0] d = 2'b10;
  wire real v, vin;
  wire [1:0] code;
  wire y;
  dac u (.d(d), .v(v));
  adc a (.vin(vin), .code(code));
  alt al (y);
  other o ();
  initial #2 $display("y=%b", y);
endmodule
"""}, lib_files=[os.path.join(self.tmp, "lib.v")], incdirs=[os.path.join(self.tmp, "inc")])
        self.assertTrue(pp.modules["adc"][0].lib)
        nl = netlist(sub("dac", ["d[1]", "d[0]", "v"]), sub("adc", ["vin", "code[1]", "code[0]"]))
        c = cfg(use_spice=[UseSpice(cells=[("dac", ""), ("adc", "")])])
        res = shells.build(pp, vp.find_top(pp, job, shells.cell_globs(c)), c, nl, job)
        self.assertEqual(res.multi_view, ["dac", "adc"])
        self.assertEqual(res.text.count("module dac "), 1)          # only the shells remain
        self.assertEqual(res.text.count("module adc "), 1)
        self.assertNotIn("0.45", res.text)
        iv = tools.find_real("iverilog")
        vvp = os.path.join(os.path.dirname(iv), "vvp")
        r = run([iv, "-g2012", "-o", os.path.join(self.tmp, "x.vvp"), res.path], cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout)
        r = run([vvp, "-n", os.path.join(self.tmp, "x.vvp")], cwd=self.tmp)
        self.assertIn("val=4", r.stdout)
        self.assertIn("branch=model", r.stdout)
        self.assertIn("y=0", r.stdout)

    def test_translation_markers_and_variants(self):
        """iverilog-sv2ghdl on the built pp.v: inout markers give resolved ports, output markers keep the
        bit-select, generate-loop, instance-array and plain-instance connections, a parameterised
        multi-view cell yields one variant per width with generate-loop markers, a non-ANSI header with
        body parameters works, and `default_nettype none ahead of the shells is harmless."""
        job, pp = self.prep({"cells.v": XLAT_CELLS, "tb.v": XLAT_TB}, sources=["cells.v", "tb.v"])
        nl = netlist(sub("inv_sp", ["a", "y"]), sub("pad_sp", ["pad", "pbus<1>", "pbus<0>"]),
                     sub("dac", ["d<7>", "d<6>", "d<5>", "d<4>", "d<3>", "d<2>", "d<1>", "d<0>", "v"]),
                     sub("adc", ["vin", "code<5>", "code<4>", "code<3>", "code<2>", "code<1>", "code<0>"]))
        c = cfg(bus_formats=["<%d>"], use_spice=[UseSpice(cells=[("dac", ""), ("adc", "")])],
                port_dirs={"inv_sp": PortDir("inv_sp", {"a": "input", "y": "output"}, "i:2")})
        res = shells.build(pp, vp.find_top(pp, job, shells.cell_globs(c)), c, nl, job)
        self.assertEqual(res.multi_view, ["dac", "adc"])
        out = os.path.join(self.tmp, "nvc")
        nvc = tools.find_real("nvc")
        env = dict(os.environ, NVC=nvc, NVC_LIBDIR=tools.nvc_libdir(nvc), IVERILOG=tools.find_real("iverilog"))
        r = run([os.path.join(vp.tools.package_root(), "bin", "iverilog-sv2ghdl"), "-o", out, "-g2012",
                 "-s", "tb", res.path], cwd=self.tmp, env=env)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])
        self.assertTrue(os.path.isfile(os.path.join(out, "design.vhd")), r.stdout[-2000:])
        with open(os.path.join(out, "design.vhd")) as fh:
            vhd = fh.read()
        self.assertNotIn("sv2vhdl:deferred", vhd)
        self.assertRegex(vhd, r"pad\s*:\s*inout\s+resolved_logic3d;")
        self.assertRegex(vhd, r"pbus\s*:\s*inout\s+resolved_logic3d_vector\(1 downto 0\)")
        arch = vhd[vhd.index("architecture from_verilog of tb is"):]
        maps = re.findall(r"\n\s*(\w+)\s*:\s*entity work\.inv_sp\w*\s*\n\s*port map \((.*?)\);", arch, re.S)
        self.assertEqual(len(maps), 6, maps)                        # u0, g ug, ua[1:0], p0, p1
        self.assertTrue(all(re.search(r"\by\s*=>", m) for _, m in maps), maps)
        widths = set(re.findall(r"\bq\s*:\s*out\s+logic3d_vector\((\d+) downto 0\)", vhd))
        self.assertTrue({"7", "3"} <= widths, widths)
        self.assertIn("sv_bufif1_vamos_ams_hiz_q", vhd)
        self.assertRegex(vhd, r"\bcode\s*:\s*out\s+logic3d_vector\(5 downto 0\)")
        self.assertRegex(vhd, r"\bvin\s*:\s*in\s+real")
        e = run([nvc, "--std=2040", "-L", tools.nvc_libdir(nvc), "-e", "tb"], cwd=out, env=env)
        self.assertEqual(e.returncode, 0, e.stdout[-2000:])


if __name__ == "__main__":
    unittest.main()
