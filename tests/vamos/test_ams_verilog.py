"""vcs-ams Verilog side (docs/VAMOS_AMS_DESIGN.md §1.2-§1.4, §5.1): vamos/ams/verilog_ports.py.

    python3 -m unittest discover -s tests/vamos -p 'test_ams_verilog.py' -v

The pure tests run anywhere; TestStack runs the real iverilog (WSL) and pins the
preprocessor behaviour the module relies on.
"""

import os
import unittest

from vamos_testlib import TempDir, fixture, needs_stack

from vamos.ams import verilog_ports as vp  # noqa: E402
from vamos.ams.model import LOGIC, REAL  # noqa: E402
from vamos.job import Job, Source  # noqa: E402
from vamos.notes import ERROR, NoteError, has_errors  # noqa: E402


def read_fixture(name):
    with open(fixture("ams", name)) as fh:
        return fh.read()


def kinds(text):
    return [(t.kind, t.text) for t in vp.tokenize(text)]


class TestLexer(unittest.TestCase):
    def test_comments_strings_attributes(self):
        toks = kinds('a // b c\n/* module x; */ "s // t" (* keep = 1 *) @(*) @( * )')
        self.assertEqual(toks[0], ("id", "a"))
        self.assertIn(("str", '"s // t"'), toks)
        self.assertIn(("attr", "(* keep = 1 *)"), toks)
        self.assertNotIn(("id", "module"), toks)
        self.assertEqual([t for t in toks if t[0] == "attr"], [("attr", "(* keep = 1 *)")])   # @(*) is not one

    def test_numbers_identifiers_directives(self):
        toks = kinds("4'b1010 8 'h F_f 'h3 1.5e3 '0 \\esc[0] $display `timescale 1 ns / 1 ps\n`resetall x")
        self.assertEqual(toks[:5], [("num", "4'b1010"), ("num", "8 'h F_f"), ("num", "'h3"), ("num", "1.5e3"),
                                    ("num", "'0")])
        self.assertEqual(toks[5], ("esc", "\\esc[0]"))
        self.assertEqual(vp.tokenize("\\esc[0] ")[0].name, "esc[0]")
        self.assertEqual(toks[6], ("sys", "$display"))
        self.assertEqual(toks[7], ("dir", "`timescale 1 ns / 1 ps"))     # runs to the end of the line
        self.assertEqual(toks[8:], [("dir", "`resetall"), ("id", "x")])  # a single word


class TestUnits(unittest.TestCase):
    SRC = """(* top *) (* keep *)
module automatic a (input x); wire s = "endmodule"; // endmodule
endmodule : a
interface ifc; logic q; endinterface
package p; class c; endclass endpackage
extern module e (input z);
macromodule b; a u (.x()); endmodule
primitive udp (o, i); output o; input i; table 0 : 1; 1 : 0; endtable endprimitive
"""

    def test_definitions(self):
        pp = vp.from_text(self.SRC)
        self.assertEqual(sorted(pp.modules), ["a", "b"])
        a = pp.modules["a"][0]
        self.assertEqual(a.start, 0)                                  # leading attributes belong to it
        self.assertEqual(pp.text[a.end - len("endmodule : a"):a.end], "endmodule : a")
        self.assertEqual((a.line, a.end_line), (2, 3))
        self.assertEqual(pp.modules["b"][0].keyword, "macromodule")
        self.assertEqual([k for k, _, _ in vp.units(pp)], ["module", "interface", "package", "macromodule",
                                                          "primitive"])

    def test_blank_keeps_lines(self):
        pp = vp.from_text(self.SRC)
        a = pp.modules["a"][0]
        out = vp.blank(pp.text, [(a.start, a.end)])
        self.assertEqual(out.count("\n"), pp.text.count("\n"))
        self.assertNotIn("endmodule : a", out)
        self.assertEqual(sorted(vp.from_text(out).modules), ["b"])


class TestLineMap(unittest.TestCase):
    def test_line_directives(self):
        # real `iverilog -E -BP<ivlpp -L wrapper>` output: nested includes, three files, a multi-line macro
        raw = read_fixture("verilog_iv_lstream.txt")
        text, origins, names, cmd = vp.strip_line_directives(raw, ["f1.v", "f2.v", "f3.v"])
        self.assertNotIn("`line", text)
        lines = text.split("\n")
        where = {}
        for k, line in enumerate(lines):
            if line.strip():
                fi, ln = origins[k]
                where[line.strip()] = ("%s:%d" % (names[fi], ln), cmd[k])
        self.assertEqual(where["// f1 line 1"], ("f1.v:1", 0))
        self.assertEqual(where["// in b.vh line 1"], ("inc/b.vh:1", 0))     # an include belongs to its includer
        self.assertEqual(where["// in a.vh line 2"], ("inc/a.vh:2", 0))
        self.assertEqual(where["module m1; wire [3:0] x ="], ("f1.v:3", 0))
        self.assertEqual(where["; endmodule"], ("f1.v:3", 0))              # resynced after the macro body
        self.assertEqual(where["module m2; endmodule"], ("f2.v:1", 1))
        self.assertEqual(where["module m3; endmodule"], ("f3.v:4", 2))


class TestTime(unittest.TestCase):
    def test_literals(self):
        self.assertEqual(vp.time_exp("1ns"), -9)
        self.assertEqual(vp.time_exp("100 ps"), -10)
        self.assertIsNone(vp.time_exp("2ns"))
        self.assertEqual([vp.time_text(e) for e in (-15, -11, -3, -2, 0, 2)],
                         ["1fs", "10ps", "1ms", "10ms", "1s", "100s"])
        self.assertEqual(vp.time_seconds("10us"), 1e-5)
        self.assertEqual(vp.parse_timescale("1ns / 10ps"), (-9, -11))
        for bad in ("1ns", "1ns/1us", "3ns/1ps", ""):
            with self.assertRaises(ValueError, msg=bad):
                vp.parse_timescale(bad)

    def test_effective_time_base(self):
        pp = vp.from_text("""`timescale 1ns/1ps
module a; endmodule
`timescale 10us/1us
module b; endmodule
`resetall
module c; endmodule
module d; timeunit 100ps; timeprecision 10fs; endmodule
package p; timeunit 1ms; endpackage
module e; endmodule
""", ams=True)
        got = {d.name: (d.unit, d.precision) for d in pp.definitions()}
        self.assertEqual(got, {"a": ("1ns", "1ps"), "b": ("10us", "1us"), "c": (None, None),
                               "d": ("100ps", "10fs"), "e": (None, None)})   # a package's timeunit is its own
        self.assertEqual(pp.precision, "10fs")
        errs = [n for n in pp.notes if n.severity == ERROR]
        self.assertEqual(len(errs), 2)
        self.assertIn("module c has no time unit", errs[0].message)
        self.assertIn("-override_timescale with a precision <= 1ms", errs[0].message)

    def test_compilation_unit_and_precedence(self):
        pp = vp.from_text("timeunit 1ns; timeprecision 1ps;\nmodule a; endmodule\n"
                          "`timescale 1us/1ns\nmodule b; endmodule\n", ams=True)
        got = {d.name: (d.unit, d.precision) for d in pp.definitions()}
        self.assertEqual(got, {"a": ("1ns", "1ps"), "b": ("1us", "1ns")})    # `timescale before the CU unit
        self.assertFalse(has_errors(pp.notes))

    def test_coarse_precision(self):
        pp = vp.from_text("`timescale 1s/10ms\nmodule slow; endmodule\n`timescale 1s/1ms\nmodule ok; endmodule\n",
                          ams=True)
        msgs = [n.message for n in pp.notes if n.severity == ERROR]
        self.assertEqual(len(msgs), 1)
        self.assertIn("module slow: digital precision 10ms is coarser than 1 ms", msgs[0])
        plain = vp.from_text("`timescale 1s/1s\nmodule m; endmodule\n", ams=False)
        self.assertFalse(plain.notes)                                         # plain vcs mode: no rule
        self.assertEqual(plain.precision, "1s")

    def test_unused_library_module_does_not_count(self):
        text = "`timescale 1ns/1ps\nmodule t; used u (); endmodule\n`resetall\nmodule used; endmodule\n" \
               "module unused; endmodule\n"
        pp = vp.from_text(text, ams=True, lib_lines=[(3, 5)])
        msgs = [n.message for n in pp.notes if n.severity == ERROR]
        self.assertEqual(len(msgs), 1)
        self.assertIn("module used has no time unit", msgs[0])

    def test_override_rewrite(self):
        text = ("`timescale 10us/1us\nmodule a; timeunit 1ms\n  / 1us; timeprecision 1us; endmodule\n"
                "`resetall\nmodule b; endmodule\n`resetall module c; endmodule\n")
        pp = vp.from_text(text)
        new, notes = vp._override(pp, vp.parse_timescale("1ns/1ps"))
        self.assertEqual(new.count("\n"), text.count("\n"))                  # line count kept
        self.assertIn("`timescale 1ns/1ps\n", new)
        self.assertIn("timeunit 1ns/1ps;\n", new)
        self.assertIn("timeprecision 1ps;", new)
        self.assertIn("`resetall `timescale 1ns/1ps\n", new)
        self.assertEqual(len(notes), 1)                                       # code after `resetall
        self.assertIn("code follows `resetall", notes[0].message)
        pp2 = vp.from_text(new, ams=True)
        got = {d.name: (d.unit, d.precision) for d in pp2.definitions()}
        self.assertEqual(got["a"], ("1ns", "1ps"))
        self.assertEqual(got["b"], ("1ns", "1ps"))


class TestApiScan(unittest.TestCase):
    def test_hits_and_non_hits(self):
        pp = vp.from_text("""module t;
  real v;
  initial v = $snps_get_volt("tb.x");      // $snps_in_comment
  initial $display("$snps_in_string $hdl_xmr");
  (* note = "$snps_attr" *) wire w;
  initial $hdl_xmr("tb.a", "tb.b");
  always @(snps_cross(v - 0.5)) $display("x");
endmodule
""")
        notes = vp.api_scan(pp)
        self.assertEqual([(n.origin, n.message.split(":")[0]) for n in notes],
                         [("pp.orig.v:3", "$snps_get_volt"), ("pp.orig.v:6", "$hdl_xmr"),
                          ("pp.orig.v:7", "snps_cross")])
        self.assertTrue(all(n.severity == ERROR for n in notes))


class TestUnsupportedTasks(unittest.TestCase):
    def test_design_vhd_comments(self):
        pp = vp.from_text("module t;\n  initial $foo;\nendmodule\n")
        vhd = ("  null;  -- Unsupported system task $foo omitted here (/d/nvc/_norm.sv:2)\n"
               "  null;  -- Unsupported system task $foo omitted here (/d/nvc/_norm.sv:2)\n"
               "  null;  -- Unsupported system task $bar omitted here (/x/other.v:7)\n")
        notes = vp.unsupported_tasks(vhd, pp)
        self.assertEqual([(n.severity, n.origin, n.message.split(" is ")[0]) for n in notes],
                         [(ERROR, "pp.orig.v:2", "system task $foo"), (ERROR, "/x/other.v:7", "system task $bar")])
        self.assertEqual({n.severity for n in vp.unsupported_tasks(vhd, None, ams=False)}, {"warning"})


class TestDeclarations(unittest.TestCase):
    SRC = """module m (input logic a, output logic y, output reg [3:0] q, inout wire z, input tri1 t1, output r);
  reg r;
  logic l;
  wire logic wl;
  var v;
  bit [1:0] b, c;
  tri0 [2:0] pd;
  integer i;
  function automatic integer f(input integer n); reg loc; f = n; endfunction
  task tk; input x; reg tloc; begin end endtask
  for (genvar g = 0; g < 2; g++) begin : gb
    logic gl;
  end
  always begin : blk
    reg inner;
  end
  assign y = int'(a);
endmodule
"""

    def test_variables_and_special_nets(self):
        pp = vp.from_text(self.SRC)
        got = {(d.name, d.kind, d.port, d.block) for d in pp.variables}
        self.assertEqual(got, {("y", "logic", True, ""), ("q", "reg", True, ""), ("r", "reg", False, ""),
                               ("l", "logic", False, ""), ("v", "logic", False, ""), ("b", "bit", False, ""),
                               ("c", "bit", False, ""), ("i", "integer", False, ""),
                               ("gl", "logic", False, "gb"), ("inner", "reg", False, "blk")})
        self.assertEqual({(d.name, d.kind, d.port) for d in pp.tri_nets}, {("t1", "tri1", True),
                                                                          ("pd", "tri0", False)})
        self.assertTrue(pp.is_variable("m", "y"))
        self.assertFalse(pp.is_variable("m", "a"))          # input logic is a net
        self.assertFalse(pp.is_variable("m", "wl"))         # wire logic is a net
        self.assertFalse(pp.is_variable("m", "inner"))      # a begin-block local
        self.assertEqual(pp.tri_kind("m", "t1"), "tri1")
        d = pp.decl_at(3, "l")
        self.assertEqual((d.module, d.kind), ("m", "logic"))
        self.assertEqual([x.range_text for x in pp.decls("m", "pd")], ["[2:0]"])

    def test_sv_shapes(self):
        pp = vp.from_text("""module s (input pkg::req_t req, output pkg::rsp_t rsp, my_if.mp bus);
  typedef struct packed { logic a; logic [3:0] b; } s_t;
  s_t st;
  logic [1:0] sel;
  assign sel = '{default: 1'b0};
endmodule
""")
        self.assertEqual({d.name for d in pp.variables}, {"sel"})        # struct members are not items
        with self.assertRaises(NoteError) as cm:
            vp.module_header(pp, "s")
        msgs = " | ".join(n.message for n in cm.exception.notes)
        self.assertIn("port req has the user-defined or interface type pkg::req_t", msgs)
        self.assertIn("port bus has the user-defined or interface type my_if.mp", msgs)


class TestInstantiations(unittest.TestCase):
    def test_scan(self):
        pp = vp.from_text("""module t;
  inv_sp #(5) u2 (a, y), u3 (.a(b), .Y());
  dac #(.N(8)) ud [1:0] (.d(), .v(), .q());
  sub #4 s1 (.*);
  nand g1 (y, a, b);
  bufif1 (strong0, weak1) #1 bz (y, a, en);
  initial begin $dumpvars(0, t); x.y.z(1); pkg::f(2); end
  \\esc$mod  \\i0  ( .p() );
endmodule
""")
        got = [(x.module, x.name, x.params, x.named, x.positional, x.array, x.scope) for x in vp.instantiations(pp)]
        self.assertEqual(got, [("inv_sp", "u2", "#(5)", [], 2, None, "t"),
                               ("inv_sp", "u3", "#(5)", ["a", "Y"], 0, None, "t"),
                               ("dac", "ud", "#(.N(8))", ["d", "v", "q"], 0, "[1:0]", "t"),
                               ("sub", "s1", "#4", ["*"], 0, None, "t"),
                               ("esc$mod", "i0", None, ["p"], 0, None, "t")])
        x = vp.instantiations(pp)[2]
        self.assertEqual(pp.text[x.start:x.end], "dac #(.N(8)) ud [1:0] (.d(), .v(), .q())")

    def test_connections(self):
        """Inst.conns and Inst.actual: what each port is connected to, as written (§5.1 probe step 2b)."""
        pp = vp.from_text("""module t;
  sub_sp u1 (.a(x[3:2]), .b( ), .c, (* keep *) .d({p, q}));
  sub_sp u2 (x, , y + 1);
  sub_sp u3 (.a(w), .*);
endmodule
""")
        u1, u2, u3 = vp.instantiations(pp)
        self.assertEqual([(c.port, c.expr) for c in u1.conns], [("a", "x[3:2]"), ("b", ""), ("c", None), ("d", "{p, q}")])
        self.assertEqual((u1.named, u1.positional), (["a", "b", "c", "d"], 0))     # the attribute is not a port
        self.assertEqual([u1.actual(p, k) for k, p in enumerate("abcde")], ["x[3:2]", "", "c", "{p, q}", ""])
        self.assertEqual([(c.port, c.expr) for c in u2.conns], [(None, "x"), (None, ""), (None, "y + 1")])
        self.assertEqual([u2.actual(p, k) for k, p in enumerate("abcd")], ["x", "", "y + 1", ""])
        self.assertEqual((u3.actual("a", 0), u3.actual("b", 1)), ("w", "b"))      # .* connects the same name

    def test_actual_shapes(self):
        self.assertEqual(vp.ident_ref("a"), ("a", False))
        self.assertEqual(vp.ident_ref(" b[3] "), ("b", True))
        self.assertEqual(vp.ident_ref("c[W-1:0][1]"), ("c", True))
        self.assertEqual(vp.ident_ref("\\e.x [0]"), ("e.x", True))
        for e in ("", None, "1'b0", "~a", "a.b", "a[1", "a[1)", "{a, b}", "f(a)", "a + b", "a[1] b"):
            self.assertIsNone(vp.ident_ref(e), e)
        self.assertEqual(vp.concat_operands(" {a, b[1], {c, d}} "), ["a", "b[1]", "{c, d}"])
        for e in ("a", "{2{a}}", "{W{1'b0}}", "{a, }", "{a}+b", "", None):
            self.assertIsNone(vp.concat_operands(e), e)


class TestRoots(unittest.TestCase):
    SRC = """module tb; dut d (); initial $dumpvars(0, tb); endmodule
module dut; inv_sp u (); endmodule
module nand_mv (input a); endmodule
module helper; endmodule
"""

    def test_roots_with_exclusions(self):
        pp = vp.from_text(self.SRC)
        self.assertEqual(vp.find_roots(pp), ["tb", "nand_mv", "helper"])
        self.assertEqual(vp.find_roots(pp, ["NAND_*"]), ["tb", "helper"])        # VCS globs, case-insensitive
        pp = vp.from_text(self.SRC, lib_lines=[(4, 4)])
        self.assertEqual(vp.find_roots(pp, ["nand_mv"]), ["tb"])                # helper only in a -v file
        self.assertEqual(vp.find_top(pp, Job("vcs"), ["nand_mv"]), "tb")

    def test_top_errors(self):
        pp = vp.from_text(self.SRC)
        with self.assertRaises(NoteError) as cm:
            vp.find_top(pp, Job("vcs"), [])
        self.assertIn("several top-level modules: tb (pp.orig.v:1), nand_mv", cm.exception.notes[0].message)
        self.assertIn("give -top", cm.exception.notes[0].message)
        self.assertEqual(vp.find_top(pp, Job("vcs", tops=["helper"]), []), "helper")
        with self.assertRaises(NoteError):
            vp.find_top(pp, Job("vcs", tops=["nosuch"]), [])
        with self.assertRaises(NoteError) as cm:
            vp.find_top(pp, Job("vcs", tops=["tb", "helper"]), [])
        self.assertIn("-top was given 2 times", cm.exception.notes[0].message)
        with self.assertRaises(NoteError) as cm:
            vp.find_top(vp.from_text("module a; a x (); endmodule\n"), Job("vcs"), [])
        self.assertIn("no top-level module", cm.exception.notes[0].message)


class TestHeader(unittest.TestCase):
    def test_ansi_with_parameters(self):
        pp = vp.from_text("""module dac #(parameter N = 4, M = 2, localparam L = N + 1)
  (input [N-1:0] d, b, output real v, output reg signed [M:0] q, inout wreal w, input [3:0] c,
   output [L-1:0] e);
  localparam W = L * 2;
  function integer clog2(input integer x); clog2 = x; endfunction
  function integer unused(input integer x); unused = x; endfunction
  localparam C = clog2(W);
endmodule
""")
        h = vp.module_header(pp, "dac")
        self.assertTrue(h.ansi)
        self.assertEqual(h.param_text, "#(parameter N = 4, M = 2, localparam L = N + 1)")
        got = [(p.name, p.direction, p.kind, p.range_text, p.msb, p.lsb, p.variable) for p in h.ports]
        self.assertEqual(got, [("d", "input", LOGIC, "[N-1:0]", None, None, False),
                               ("b", "input", LOGIC, "[N-1:0]", None, None, False),        # inherited
                               ("v", "output", REAL, None, None, None, False),
                               ("q", "output", LOGIC, "[M:0]", None, None, True),
                               ("w", "inout", REAL, None, None, None, False),
                               ("c", "input", LOGIC, "[3:0]", 3, 0, False),
                               ("e", "output", LOGIC, "[L-1:0]", None, None, False)])
        self.assertEqual([(p.name, p.default, p.local, p.in_header) for p in h.params],
                         [("N", "4", False, True), ("M", "2", False, True), ("L", "N + 1", True, True),
                          ("W", "L * 2", True, False), ("C", "clog2(W)", True, False)])
        self.assertEqual(h.range_params, {"N", "M", "L"})              # L through its default N + 1
        self.assertEqual(h.body_text[0], "localparam W = L * 2;")
        self.assertTrue(h.body_text[1].startswith("function integer clog2"))   # used by C
        self.assertFalse(any("unused" in t for t in h.body_text))
        self.assertIn("wreal", h.ports[4].type_text)

    def test_non_ansi(self):
        pp = vp.from_text("""module adc (vin, code, ready, vref);
  parameter K = 3;
  localparam W = K + 1;
  input vin; real vin;
  output [W-1:0] code;
  output ready;
  reg ready;
  inout vref;
endmodule
""")
        h = vp.module_header(pp, "adc")
        self.assertFalse(h.ansi)
        self.assertEqual([(p.name, p.direction, p.kind, p.range_text, p.variable) for p in h.ports],
                         [("vin", "input", REAL, None, False), ("code", "output", LOGIC, "[W-1:0]", False),
                          ("ready", "output", LOGIC, None, True), ("vref", "inout", LOGIC, None, False)])
        self.assertEqual(h.range_params, {"W", "K"})
        self.assertEqual(h.body_text, ["parameter K = 3;", "localparam W = K + 1;"])
        self.assertEqual(h.defaults(), {"K": "3", "W": "K + 1"})

    def test_refused_headers(self):
        cases = {
            "module c (.a(x), .b(y)); input x; output y; endmodule": "port expressions",
            "module c ({a, b}); input a, b; endmodule": "port expressions",
            "module c (a[3:0]); input [3:0] a; endmodule": "port expressions",
            "module c (my_if.mp bus); endmodule": "interface type",
            "module c (input logic [3:0] a [0:1]); endmodule": "unpacked",
            "module c (input [1:0][3:0] a); endmodule": "more than one packed dimension",
            "module c (a); endmodule": "no direction declaration",
            "module c (input integer n); endmodule": "type integer",
            "module c #(parameter type T = logic) (input T a); endmodule": "type parameters",
            "module c (a, a); inout a; endmodule": "listed more than once",
            "module c (input [7] a); endmodule": "is not [msb:lsb]",
        }
        for src, frag in cases.items():
            with self.assertRaises(NoteError, msg=src) as cm:
                vp.module_header(vp.from_text(src), "c")
            self.assertTrue(any(frag in n.message for n in cm.exception.notes),
                            "%s: %s" % (src, [n.message for n in cm.exception.notes]))
        with self.assertRaises(NoteError) as cm:
            vp.module_header(vp.from_text("module c; endmodule\nmodule c; endmodule\n"), "c")
        self.assertIn("defined 2 times", cm.exception.notes[0].message)

    def test_port_directions_any_module(self):
        """port_directions reads any module's port list (a wrapper, not only a SPICE cell) and never raises."""
        pp = vp.from_text("""module w1 (input a, b, output [1:0] y, inout z);
endmodule
module w2 (a, y);
  input a;
  output y;
endmodule
module w3 (input a, input integer n, output logic [1:0] q [2]);
endmodule
module w4 (.a(x), y);
  input x;
  output y;
endmodule
module w5 (a, b);
  input a;
endmodule
module w6;
endmodule
module w6;
endmodule
""")
        self.assertEqual(vp.port_directions(pp, "w1"),
                         [("a", "input"), ("b", "input"), ("y", "output"), ("z", "inout")])
        self.assertEqual(vp.port_directions(pp, "w2"), [("a", "input"), ("y", "output")])
        with self.assertRaises(NoteError):
            vp.module_header(pp, "w3")                       # refused as a SPICE cell header ...
        self.assertEqual(vp.port_directions(pp, "w3"), [("a", "input"), ("n", "input"), ("q", "output")])
        for name in ("w4", "w5", "w6", "nosuch"):            # a port expression, no direction, two definitions
            self.assertIsNone(vp.port_directions(pp, name), name)
        self.assertEqual(vp.module_header(pp, "w1").ports[2].range_text, "[1:0]")   # the cache still works
        self.assertIs(vp.module_header(pp, "w1"), vp.module_header(pp, "w1"))

    def test_constant_names(self):
        pp = vp.from_text("""module m #(parameter N = 4, M = 2, localparam integer L = N + 1) (input [N-1:0] a);
  parameter [3:0] P = 4'h3, Q = 1;
  localparam R = P + Q;
  genvar g, h;
  for (genvar k = 0; k < 2; k = k + 1) begin : b
    localparam S = k;
  end
  specparam T = 2;
  wire [1:0] w = 2'b01;
  initial #(N) $display(w);
endmodule
""")
        self.assertEqual(vp.constant_names(pp, "m"), {"N", "M", "L", "P", "Q", "R", "g", "h", "k", "S", "T"})
        self.assertEqual(vp.constant_names(pp, "nosuch"), set())

    def test_specparam_copied(self):
        pp = vp.from_text("module c (q);\n  specparam SP = 2;\n  localparam W = SP + 1;\n  output [W-1:0] q;\n"
                          "endmodule\n")
        h = vp.module_header(pp, "c")
        self.assertEqual(h.body_text, ["specparam SP = 2;", "localparam W = SP + 1;"])
        self.assertEqual(h.range_params, {"W", "SP"})

    def test_const_range(self):
        self.assertEqual(vp.const_range("[3:0]"), (3, 0))
        self.assertEqual(vp.const_range("[0:7]"), (0, 7))
        self.assertEqual(vp.const_range("[2*4-1 : (1)]"), (7, 1))
        self.assertIsNone(vp.const_range("[N-1:0]"))
        self.assertIsNone(vp.const_range("[1:0][3:0]"))
        self.assertIsNone(vp.const_range(None))


class TestDiagnostics(unittest.TestCase):
    def test_probe_messages(self):
        diags, other = vp.parse_diags(read_fixture("verilog_iv_probe_inout.txt"))
        self.assertEqual(other, [])                                   # the summary is dropped
        inout = [d for d in diags if d.message.startswith("Inout port expression")]
        self.assertEqual([(d.file, d.line, d.extra) for d in inout],
                         [("tbp.sv", 7, ["Port 1 (a) of inv_sp is connected to r1"]),
                          ("tbp.sv", 7, ["Port 2 (y) of inv_sp is connected to l1"]),
                          ("tbp.sv", 7, ["Port 3 (b) of inv_sp is connected to v['sd2:'sd1]"]),
                          ("tbp.sv", 8, ["Port 3 (b) of inv_sp is connected to {l1, r1}"])])

    def test_other_shapes(self):
        d, _ = vp.parse_diags(read_fixture("verilog_iv_syntax.txt"))
        self.assertEqual([(x.line, x.kind, x.message) for x in d],
                         [(3, "error", "syntax error"), (3, "error", "Invalid module item.")])
        d, other = vp.parse_diags(read_fixture("verilog_iv_noinclude.txt"))
        self.assertEqual([(x.kind, x.message) for x in d], [("", "Include file nope.vh not found")])
        self.assertEqual(other, [])
        d, _ = vp.parse_diags(read_fixture("verilog_iv_undefmacro.txt"))
        self.assertEqual(d[0].kind, "warning")
        self.assertIn("undefined (and assumed null)", d[0].message)
        d, other = vp.parse_diags(read_fixture("verilog_iv_cellset.txt"))
        self.assertEqual([x.message for x in d], ["Unknown module type: inv_sp",
                                                  "Unknown module type: vamos_ams_probe_0"] * 2)
        self.assertEqual(other, [])
        d, _ = vp.parse_diags(read_fixture("verilog_iv_notaport.txt"))      # shells.py hints on this one
        self.assertEqual((d[0].kind, d[0].message), ("error", "port ``b'' is not a port of u."))
        d, _ = vp.parse_diags(read_fixture("verilog_iv_width.txt"))         # passed on when it names a cut cell
        self.assertEqual([(x.kind, x.message, x.extra) for x in d],
                         [("warning", "Port 1 (a) of module c expects 2 bit(s), given 1.",
                           ["Padding 1 high bits of the port."])])


def job_for(tmp, sources, libs=(), **kw):
    return Job("vcs", cwd=tmp, sources=[Source(os.path.join(tmp, s), "sv") for s in sources],
               lib_files=[os.path.join(tmp, f) for f in libs], **kw)


@needs_stack
class TestStack(TempDir):
    """Real iverilog: the -L origin map, masking shapes, the precheck, the time rules."""

    def test_origins_and_masking_shapes(self):
        self.write("inc/cells.vh", "`timescale 1ns/1ps\nmodule dac (input [1:0] d, output real v);\n"
                   "`define OFFSET 3\n`define HAVE_DAC_MODEL\n  always @* v = d * 0.45;\nendmodule\n")
        self.write("tb.v", "`include \"cells.vh\"\nmodule other;\n  initial $display(\"val=%0d\", `OFFSET + 1);\n"
                   "`ifdef HAVE_DAC_MODEL\n  initial $display(\"branch=model\");\n`else\n"
                   "  initial $display(\"branch=fallback\");\n`endif\nendmodule\n"
                   "`ifdef ALT\nmodule alt (output y); assign y = 1; endmodule\n`else\n"
                   "module alt (output y); assign y = 0; endmodule\n`endif\n"
                   "module tb; reg [1:0] d; wire real v; dac u (.d(d), .v(v)); other o (); wire y; alt a (y);\n"
                   "endmodule\n")
        job = job_for(self.tmp, ["tb.v"], incdirs=[os.path.join(self.tmp, "inc")])
        pp = vp.preprocess(job, os.path.join(self.tmp, "ams", "pp.orig.v"))
        self.assertFalse(has_errors(pp.notes), [n.text() for n in pp.notes])
        self.assertEqual(pp.modules["dac"][0].origin, os.path.join("inc", "cells.vh") + ":2")
        self.assertEqual(pp.modules["dac"][0].file, os.path.join(self.tmp, "tb.v"))   # its includer
        self.assertEqual(pp.modules["tb"][0].origin, "tb.v:15")
        self.assertEqual(len(pp.modules["alt"]), 1)                    # `ifdef alternatives: one remains
        self.assertEqual(pp.modules["alt"][0].origin, "tb.v:13")
        # masking happens on this stream: macros and `ifdef are resolved everywhere else already
        d = pp.modules["dac"][0]
        masked = vp.blank(pp.text, [(d.start, d.end)])
        self.assertIn("3 + 1", masked)
        self.assertIn("branch=model", masked)
        self.assertNotIn("branch=fallback", masked)
        self.assertNotIn("module dac", masked)
        self.assertEqual(masked.count("\n"), pp.text.count("\n"))
        with open(pp.path) as fh:
            self.assertEqual(fh.read(), pp.text)
        self.assertNotIn("`line", pp.text)
        self.assertEqual(vp.find_top(pp, job, ["dac"]), "tb")

    def test_v_file_cell_and_roots(self):
        self.write("tb.v", "`timescale 1ns/1ps\nmodule tb; dac u (); endmodule\n")
        self.write("lib.v", "module dac; endmodule\nmodule unused_lib; endmodule\n")
        job = job_for(self.tmp, ["tb.v"], libs=["lib.v"])
        pp = vp.preprocess(job, os.path.join(self.tmp, "pp", "pp.v"))
        self.assertFalse(has_errors(pp.notes))
        self.assertTrue(pp.modules["dac"][0].lib)
        self.assertEqual(pp.modules["dac"][0].origin, "lib.v:1")
        self.assertFalse(pp.modules["tb"][0].lib)
        self.assertEqual(vp.find_roots(pp, ["dac"]), ["tb"])          # unused_lib is -v only

    def test_precheck_reports_user_lines(self):
        self.write("a.v", "`timescale 1ns/1ps\nmodule tb; inv_sp u (); b x (); endmodule\n")
        self.write("b.v", "module b;\n  wire w\n  assign w = 1;\nendmodule\n")
        job = job_for(self.tmp, ["a.v", "b.v"])
        notes = vp.precheck(job, "tb")
        self.assertEqual([(n.severity, n.origin, n.message) for n in notes if n.severity == ERROR][:1],
                         [("error", "b.v:3", "syntax error")])
        self.write("b.v", "module b; endmodule\n")
        self.assertEqual(vp.precheck(job, "tb"), [])                 # inv_sp is a SPICE cell: dropped

    def test_undefined_macro_and_missing_include(self):
        self.write("u.v", "`timescale 1ns/1ps\nmodule u; wire a = `NOPE; endmodule\n")
        pp = vp.preprocess(job_for(self.tmp, ["u.v"]), os.path.join(self.tmp, "ams", "pp.orig.v"))
        errs = [n for n in pp.notes if n.severity == ERROR]
        self.assertEqual([(n.origin, "NOPE" in n.message) for n in errs], [("u.v:2", True)])
        pp = vp.preprocess(job_for(self.tmp, ["u.v"]), os.path.join(self.tmp, "pp", "pp.v"), ams=False)
        self.assertEqual([n.severity for n in pp.notes], ["warning"])
        self.write("i.v", "`include \"nope.vh\"\nmodule i; endmodule\n")
        with self.assertRaises(NoteError) as cm:
            vp.preprocess(job_for(self.tmp, ["i.v"]), os.path.join(self.tmp, "ams", "pp.orig.v"))
        self.assertEqual(cm.exception.notes[0].origin, "i.v:2")      # ivlpp names the line after the `include
        self.assertIn("nope.vh not found", cm.exception.notes[0].message)

    def test_override_timescale(self):
        src = ("module t;\n  const time PH = 5ns;\n  logic clk = 0;\n  initial begin #PH clk = 1; end\n"
               "endmodule\n`timescale 1s/1s\nmodule s; timeunit 1s; timeprecision 1s; endmodule\n")
        self.write("t.sv", src)
        job = job_for(self.tmp, ["t.sv"])
        pp = vp.preprocess(job, os.path.join(self.tmp, "ams", "pp.orig.v"))
        msgs = [n.message for n in pp.notes if n.severity == ERROR]
        self.assertTrue(any("module t has no time unit" in m for m in msgs), msgs)
        self.assertTrue(any("module s: digital precision 1s is coarser than 1 ms" in m for m in msgs), msgs)
        pp = vp.preprocess(job, os.path.join(self.tmp, "ams", "pp.orig.v"), override_timescale="1ns/1ps")
        self.assertFalse(has_errors(pp.notes), [n.text() for n in pp.notes])
        self.assertEqual(pp.precision, "1ps")
        self.assertEqual({(d.name, d.unit, d.precision) for d in pp.definitions()},
                         {("t", "1ns", "1ps"), ("s", "1ns", "1ps")})
        self.assertTrue(pp.text.startswith("`timescale 1ns/1ps\n"))   # the prelude
        self.assertIn("timeunit 1ns; timeprecision 1ps;", pp.text)
        self.assertEqual(pp.modules["t"][0].origin, "t.sv:1")
        with self.assertRaises(NoteError):
            vp.preprocess(job, os.path.join(self.tmp, "ams", "pp.orig.v"), override_timescale="1ns")
        pp = vp.preprocess(job, os.path.join(self.tmp, "ams", "pp.orig.v"), override_timescale="1s/10ms")
        self.assertIn("-override_timescale", [n.origin for n in pp.notes if n.severity == ERROR])

    def test_timescale_prelude(self):
        self.write("t.v", "module t; endmodule\n")
        job = job_for(self.tmp, ["t.v"], timescale="10ns/100ps")
        pp = vp.preprocess(job, os.path.join(self.tmp, "ams", "pp.orig.v"))
        self.assertFalse(pp.notes)
        self.assertEqual((pp.modules["t"][0].unit, pp.precision), ("10ns", "100ps"))
        self.assertEqual(pp.modules["t"][0].origin, "t.v:1")


if __name__ == "__main__":
    unittest.main()
