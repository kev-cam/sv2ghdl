"""Port connections tgt-vhdl used to lose or mistranslate, on the real stack (the
vcs personality's translator, bin/iverilog-sv2ghdl, run under nvc and compared
with vvp like test_translator_tgt.py):

  * TB-01: the core's port buffer of an input port driven inside its module, for
    an instance array (one buffer for the whole array, in element [0]; a shared
    or a split actual) and for an actual of another width (the pad, signed pad or
    prune between the buffer and the port) -- each port is associated, with the
    buffer and those nodes drawn in the parent (PB_/PBT_ signals);
  * TB-02: the pull of a tri1/tri0 input fed from a variable, a constant z, an
    expression, a concatenation, or a bit-select (T2 alias): released, the port
    reads its pull;
  * TB-03: an inout instance array on a part-select (of a wire, of the module's
    own inout port) is aliased to the vector both ways; a tran on a bit-select
    says it is connected one way only;
  * TB-04/TB-11: real arithmetic in continuous assignments and real port
    expressions (r + 0.2, -r, r / 2.0, r * 2.0, code * 0.1, sel ? r1 : r2);
  * TB-05/TB-12: an inout port on a concatenation (associated part by part), and
    escaped instance names (valid VHDL labels);
  * TB-06: a wrapper's input port passed to a child's driven inout port;
  * TB-07/TB-08/TB-09: real parameters: printed exactly, kept apart in variant
    names, and several instances of a module with one;
  * TB-10: an undriven `wire real' reads 0.0.

    python3 -m unittest discover -s tests/vamos -p 'test_translator_ports.py' -v

Linux (WSL) only: the stack is Linux ELF.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from typing import Dict, List

from vamos_testlib import ROOT, needs_stack

import test_translator_tgt as tt

BIN = os.path.join(ROOT, "bin")


class Case(tt.Translated):
    """tt.Translated with helpers for the port maps and the iverilog log."""

    def port_maps(self) -> Dict[str, Dict[str, str]]:
        """{instance label: {formal: actual}} of every user-module instance."""
        out = {}
        for m in re.finditer(r"^\s*(\w+): entity work\.\w+\n\s*port map \((.*?)\n\s*\);",
                             self.vhdl, re.M | re.S):
            assoc = {}
            for item in m.group(2).split(","):
                if "=>" in item:
                    f, a = item.split("=>", 1)
                    assoc[f.strip()] = a.strip()
            out[m.group(1)] = assoc
        return out

    def log(self) -> str:
        with open(os.path.join(self.outdir, "iverilog.log"), errors="replace") as fh:
            return fh.read()

    def top_arch(self) -> str:
        m = re.search(r"^architecture from_verilog of tb is\n(.*?)^end architecture;",
                      self.vhdl, re.M | re.S)
        self.assertTrue(m, self.vhdl)
        return m.group(1)


# ------------------------------------------------------------------- TB-01

@needs_stack
class TestArrayBroadcast(Case):
    """An instance array whose elements pull their input up, on one variable: the
    core's one port buffer (in element [0]) feeds every element's port."""

    SOURCE = """\
`timescale 1ns/1ps
module slave(input scl, output seen);
  pullup(scl);
  assign seen = scl;
endmodule
module tb;
  reg scl_r;
  wire [3:0] seen;
  slave s[3:0] (.scl(scl_r), .seen(seen));
  initial begin
    scl_r = 1'b0;
    #10 $display("%0t seen=%b", $time, seen);
    scl_r = 1'bz;
    #10 $display("%0t seen=%b", $time, seen);
    scl_r = 1'b0;
    #10 $display("%0t seen=%b", $time, seen);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_settled_like_vvp()

    def test_every_element_associated(self):
        maps = self.port_maps()
        self.assertEqual(sorted(maps), ["s0", "s1", "s2", "s3"])
        for label, assoc in maps.items():
            self.assertIn("scl", assoc, (label, assoc))
            self.assertRegex(assoc["scl"], r"^PB_s\d_scl$")
        # one net for all of them, as the core joins them: one copy of the variable
        self.assertEqual(len(set(a["scl"] for a in maps.values())), 1)
        self.assertEqual(self.lines(r"^\s*PB_s\d_scl <= scl_r;"), ["PB_s0_scl <= scl_r;"])


@needs_stack
class TestArraySplit(Case):
    """A vector variable split across an instance array: each element gets its bit."""

    SOURCE = """\
`timescale 1ns/1ps
module slave(input scl, output seen);
  pullup(scl);
  assign seen = scl;
endmodule
module tb;
  reg [3:0] v;
  wire [3:0] seen;
  slave s[3:0] (.scl(v), .seen(seen));
  initial begin
    v = 4'b0000;
    #10 $display("%0t seen=%b", $time, seen);
    v = 4'bz0z0;
    #10 $display("%0t seen=%b", $time, seen);
    v = 4'b0101;
    #10 $display("%0t seen=%b", $time, seen);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_settled_like_vvp()

    def test_parts_drawn_in_the_parent(self):
        maps = self.port_maps()
        self.assertEqual(sorted(maps), ["s0", "s1", "s2", "s3"])
        arch = self.top_arch()
        for k in range(4):
            self.assertEqual(maps["s%d" % k]["scl"], "PB_s%d_scl" % k)
            self.assertRegex(arch, r"PB_s%d_scl (<=|:=) v\(%d\);" % (k, k))


@needs_stack
class TestWidthMismatch(Case):
    """Actuals of another width on inputs driven inside: unsized constants on
    tri1/tri0 ports (pruned), a narrower variable (padded with zeros) and a signed
    one (sign-extended) under a pull, a wider variable (pruned), and the Quartus
    style `input wren; tri0 wren;' with unsized tie-offs."""

    SOURCE = """\
`timescale 1ns/1ps
module m(input tri1 [1:0] en, input tri0 rst, output [2:0] y);
  assign y = {rst, en};
endmodule
module p(input [1:0] a, output [1:0] y);
  pullup(a[1]);
  assign y = a;
endmodule
module d(input [3:0] a, output [3:0] y);
  pulldown(a[3]);
  assign y = a;
endmodule
module s(input signed [3:0] a, output [3:0] y);
  pullup(a[3]);
  assign y = a;
endmodule
module ram1 (address, wren, aclr, q);
  input [1:0] address;
  input wren;
  input aclr;
  output [3:0] q;
  tri0 wren;
  tri0 aclr;
  assign q = {aclr, wren, address};
endmodule
module tb;
  reg r1;
  reg [1:0] r2;
  reg signed [1:0] rs;
  reg [5:0] r6;
  wire [2:0] y1, y2;
  wire [1:0] yp, yw;
  wire [3:0] yd, ys, q1;
  m u1 (.en(1), .rst(0), .y(y1));
  m u2 (.en(2'b01), .rst(1'b0), .y(y2));
  p up (.a(r1), .y(yp));
  p uw (.a(r6), .y(yw));
  d ud (.a(r2), .y(yd));
  s us (.a(rs), .y(ys));
  ram1 uq (.address(r2), .wren(1), .aclr(0), .q(q1));
  initial begin
    r1 = 1'b0; r2 = 2'b11; rs = -2'sd1; r6 = 6'b101010;
    #10 $display("%0t a=%b %b %b %b %b %b %b", $time, y1, y2, yp, yw, yd, ys, q1);
    r1 = 1'b1; r2 = 2'b10; rs = 2'sd1; r6 = 6'b010101;
    #10 $display("%0t a=%b %b %b %b %b %b %b", $time, y1, y2, yp, yw, yd, ys, q1);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_settled_like_vvp()

    def test_every_input_associated(self):
        maps = self.port_maps()
        for label, formals in (("u1", ("en", "rst")), ("u2", ("en", "rst")), ("up", ("a",)),
                               ("uw", ("a",)), ("ud", ("a",)), ("us", ("a",)),
                               ("uq", ("address", "wren", "aclr"))):
            for f in formals:
                self.assertIn(f, maps.get(label, {}), (label, f, maps.get(label)))

    def test_pad_and_prune_in_the_parent(self):
        arch = self.top_arch()
        self.assertRegex(arch, r"PB_up_a (<=|:=) \(?L3D_0 & r1\)?;")              # zero pad
        # (the core's buffer temporary is unsigned, so a signed actual is
        # zero-padded too -- in vvp as here)
        self.assertRegex(arch, r"PB_us_a (<=|:=) \(?logic3d_vector'\(L3D_0, L3D_0\) & rs\)?;")
        self.assertRegex(arch, r"PB_u1_en (<=|:=) tmp_ivl_\d+\(0 \+ 1 downto 0\);")  # prune
        self.assertRegex(arch, r"PB_uw_a (<=|:=) r6\(0 \+ 1 downto 0\);")
        # nothing of them is left in the modules (no dangling LO_ buffer copy)
        self.assertEqual(self.lines(r"^\s*tmp_ivl_\d+ <= LO_ivl_\d+;"), [])


@needs_stack
class TestUntranslatedNeverSilent(Case):
    """Ports that are really left open stay open, quietly (no false alarm), and
    the per-instance safety net says nothing for them."""

    SOURCE = """\
`timescale 1ns/1ps
module m(input a, input [1:0] b, output y);
  pullup(a);
  assign y = a & b[0];
endmodule
module tb;
  wire y1, y2;
  m u1 (.a(), .b(), .y(y1));
  m u2 (.y(y2));
  initial #10 $display("%0t y=%b %b", $time, y1, y2);
endmodule
"""

    def test_like_vvp(self):
        self.assert_settled_like_vvp()

    def test_no_error(self):
        self.assertNotIn("VHDL conversion error", self.log())


@needs_stack
class TestUntranslatedShapeIsAnError(unittest.TestCase):
    """A port buffer the translation cannot draw -- a real actual cast into a vector
    input that is pulled inside (the cast sits in the parent, after the buffer) --
    stops the translation with an error naming the instance and the line, instead of
    leaving the port silently undriven."""

    SOURCE = """\
`timescale 1ns/1ps
module m(input [3:0] a, output [3:0] y);
  pullup(a[3]);
  assign y = a;
endmodule
module tb;
  real r = 3.0;
  wire [3:0] y;
  m u (.a(r), .y(y));
  initial #10 $display("%0t y=%b", $time, y);
endmodule
"""

    def test_error(self):
        tmp = tempfile.mkdtemp(prefix="vamos-xlat-err-")
        try:
            src = os.path.join(tmp, "src.v")
            with open(src, "w") as fh:
                fh.write(self.SOURCE)
            r = subprocess.run([os.path.join(BIN, "iverilog-sv2ghdl"), "-o", "vsim", "-g2012",
                                src], cwd=tmp, env=tt._stack_env(), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, universal_newlines=True,
                               errors="replace", timeout=600)
            with open(os.path.join(tmp, "vsim", "iverilog.log"), errors="replace") as fh:
                log = fh.read()
            self.assertRegex(log, r"VHDL conversion error: \S+:9: an input port connection of "
                                  r"instance tb\.u is not translated")
            self.assertIn("module tb was not translated", r.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------- TB-02

@needs_stack
class TestTriInputsReleased(Case):
    """tri1/tri0 inputs fed from a variable, a constant z, a tri-state expression, a
    vector variable, a concatenation, an `input x; tri1 x;' port, a chain, and a
    bit-select of a tri-state bus (a T2 alias): released, each reads its pull."""

    SOURCE = """\
`timescale 1ns/1ps
module m(input tri1 a, output y);
  assign y = a;
endmodule
module n(input tri0 b, output z);
  assign z = b;
endmodule
module mv(input tri1 [3:0] a, output [3:0] y);
  assign y = a;
endmodule
module nv(input tri0 [1:0] b, output [1:0] z);
  assign z = b;
endmodule
module mf(clken, aclr, q);
  input clken;
  input aclr;
  output [1:0] q;
  tri1 clken;
  tri0 aclr;
  assign q = {clken, aclr};
endmodule
module leaf(input a, output y);
  assign y = a;
endmodule
module mid(input tri1 a, output y);
  leaf l (.a(a), .y(y));
endmodule
module tb;
  reg r, s, en, d, ce, ac, rc;
  reg [3:0] r4;
  reg [1:0] bus_en;
  wire [1:0] bus;
  assign bus = bus_en[0] ? 2'b00 : 2'bzz;
  wire y1, z1, y2, z2, y3, y5, y6;
  wire [3:0] y4;
  wire [1:0] z3, q;
  m u1 (.a(r), .y(y1));
  n u2 (.b(s), .z(z1));
  m u3 (.a(1'bz), .y(y2));
  n u4 (.b(en ? ~d : 1'bz), .z(z2));
  mv u5 (.a(r4), .y(y4));
  nv u6 (.b({r, s}), .z(z3));
  mf u7 (.clken(ce), .aclr(ac), .q(q));
  mid u8 (.a(rc), .y(y5));
  m u9 (.a(bus[0]), .y(y6));
  initial begin
    r = 1'bz; s = 1'bz; en = 0; d = 0; r4 = 4'bzz0z; ce = 1'bz; ac = 1'bz; rc = 1'bz;
    bus_en = 2'b00;
    #10 $display("%0t t=%b%b %b%b %b %b %b %b %b", $time, y1, z1, y2, z2, y4, z3, q, y5, y6);
    r = 0; s = 1; en = 1; r4 = 4'b0000; ce = 0; ac = 1; rc = 0; bus_en = 2'b01;
    #10 $display("%0t t=%b%b %b%b %b %b %b %b %b", $time, y1, z1, y2, z2, y4, z3, q, y5, y6);
    r = 1'bz; s = 1'bz; en = 0; r4 = 4'bzzzz; ce = 1'bz; ac = 1'bz; rc = 1'bz; bus_en = 2'b00;
    #10 $display("%0t t=%b%b %b%b %b %b %b %b %b", $time, y1, z1, y2, z2, y4, z3, q, y5, y6);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_settled_like_vvp()

    def test_pulls_on_the_port_buffers(self):
        pulls = re.findall(r"^\s*(sv_tri[01]_\w+): entity sv2vhdl\.(sv_pullup|sv_pulldown)",
                           self.top_arch(), re.M)
        names = dict(pulls)
        for lbl, ent in (("sv_tri1_PB_u1_a", "sv_pullup"), ("sv_tri0_PB_u2_b", "sv_pulldown"),
                         ("sv_tri1_PB_u5_a_b3", "sv_pullup"), ("sv_tri0_PB_u6_b_b1", "sv_pulldown"),
                         ("sv_tri1_PB_u7_clken", "sv_pullup"), ("sv_tri0_PB_u7_aclr", "sv_pulldown")):
            self.assertEqual(names.get(lbl), ent, (lbl, pulls))
        # the bit-select's T2 alias carries its pull too
        self.assertTrue(any(lbl.startswith("sv_tri1_SW") for lbl in names), pulls)

    def test_no_unplaced_pull_of_an_instance(self):
        self.assertNotRegex(self.log(), r"Warning: tri[01] net tb\.u\d\.")


# ------------------------------------------------------------------- TB-03

@needs_stack
class TestT2ArrayOnPartSelect(Case):
    """Inout pad arrays on a part-select of a wire, of the module's own inout port, and
    an IOBUF array on an inout bus: aliases of the vector itself, so the pads drive it."""

    SOURCE = """\
`timescale 1ns/1ps
module pad(inout p, input oe, input o);
  assign p = oe ? o : 1'bz;
endmodule
module ring(inout [3:0] b, input [1:0] oe, input [1:0] o);
  pad pa[1:0] (.p(b[2:1]), .oe(oe), .o(o));
endmodule
module iobuf(inout IO, input I, input T, output O);
  assign IO = T ? 1'bz : I;
  assign O = IO;
endmodule
module io8(inout [7:0] gpio, input [3:0] d, input [3:0] t, output [3:0] q);
  iobuf iob[3:0] (.IO(gpio[3:0]), .I(d), .T(t), .O(q));
endmodule
module tb;
  wire [3:0] bus, rb;
  wire [7:0] gpio;
  wire [3:0] q;
  reg [1:0] oe, o;
  reg [3:0] d, t;
  pad pa[1:0] (.p(bus[2:1]), .oe(oe), .o(o));
  ring r (.b(rb), .oe(oe), .o(o));
  io8 u (.gpio(gpio), .d(d), .t(t), .q(q));
  initial begin
    oe = 2'b11; o = 2'b10; d = 4'b1001; t = 4'b0000;
    #10 $display("%0t v=%b %b %b %b", $time, bus, rb, gpio, q);
    o = 2'b01; t = 4'b1100;
    #10 $display("%0t v=%b %b %b %b", $time, bus, rb, gpio, q);
    oe = 2'b00; t = 4'b1111;
    #10 $display("%0t v=%b %b %b %b", $time, bus, rb, gpio, q);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_settled_like_vvp()

    def test_aliases_of_the_vector(self):
        targets = sorted(a.split(" is ")[1].split(";")[0]
                         for a in self.lines(r"^\s*alias SW\w*_b is "))
        for want in ("bus_sig(1)", "bus_sig(2)", "b(1)", "b(2)",
                     "gpio(0)", "gpio(1)", "gpio(2)", "gpio(3)"):
            self.assertIn(want, targets)
        self.assertEqual([t for t in targets if t.startswith("tmp_")], [])
        self.assertNotIn("connected one way only", self.log())


@needs_stack
class TestT2TranOnBitSelect(Case):
    """A tran on a bit-select still joins a one-way copy: it says so."""

    SOURCE = """\
`timescale 1ns/1ps
module tb;
  wire [3:0] bus;
  wire w;
  reg en;
  assign bus = en ? 4'b1010 : 4'bzzzz;
  tran t1 (bus[2], w);
  initial begin
    en = 1;
    #10 $display("%0t bus=%b", $time, bus);
  end
endmodule
"""

    def test_warns(self):
        self.assertRegex(self.log(), r"Warning: bus_sig\(2\) at \S+:\d+ is connected one way only")


# ------------------------------------------------------------- TB-04 / TB-11

@needs_stack
class TestRealArithmetic(Case):
    """Real continuous assignments and real port expressions, into a real port."""

    SOURCE = """\
`timescale 1ns/1ps
module amp(input real vin, output real vout);
  assign vout = vin * 2.0;
endmodule
module tb;
  real r1 = 0.1, r2 = 0.5;
  reg [3:0] code = 4'd3;
  reg sel = 1'b0;
  wire real a, b, c, d, e, f, rv;
  wire real o1, o2, o3, o4, o5, o6, o7;
  assign a = r1 + 0.2;
  assign b = r1 - 0.05;
  assign c = r1 * 2.0;
  assign d = r1 / 2.0;
  assign e = -r1;
  assign f = r1 + r2;
  assign rv = r1 + 0.2;
  amp u1 (.vin(r1 + 0.2), .vout(o1));
  amp u2 (.vin(-r1), .vout(o2));
  amp u3 (.vin(r1 / 2.0), .vout(o3));
  amp u4 (.vin(r1 * 2.0), .vout(o4));
  amp u5 (.vin(rv), .vout(o5));
  amp u6 (.vin(code * 0.1), .vout(o6));
  amp u7 (.vin(sel ? r1 : r2), .vout(o7));
  initial begin
    #10 $display("%0t c=%f %f %f %f %f %f", $time, a, b, c, d, e, f);
    $display("%0t p=%f %f %f %f %f %f %f", $time, o1, o2, o3, o4, o5, o6, o7);
    r1 = 0.25; code = 4'd7; sel = 1'b1;
    #10 $display("%0t c=%f %f %f %f %f %f", $time, a, b, c, d, e, f);
    $display("%0t p=%f %f %f %f %f %f %f", $time, o1, o2, o3, o4, o5, o6, o7);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_settled_like_vvp()

    def test_values(self):
        out = self.nvc().stdout
        self.assertIn("20000 p=0.900000 -0.500000 0.250000 1.000000 0.900000 1.400000 0.500000",
                      out)

    def test_real_operations(self):
        # real operands into a real result, never through logic3d
        self.assertEqual(self.lines(r"real_to_l3d1\(\w+\) [-+*/] real_to_l3d1"), [])
        self.assertTrue(self.lines(r"(<=|:=) r1 \+ tmp_ivl_\d+;"), self.vhdl)
        self.assertTrue(self.lines(r"l3d_to_real\(code\)"), self.vhdl)


# ------------------------------------------------------------- TB-05 / TB-12

@needs_stack
class TestInoutConcatenation(Case):
    """An inout bus port on a concatenation of wires and a part-select: associated
    part by part, both ways (the module drives the operands, they drive it)."""

    SOURCE = """\
`timescale 1ns/1ps
module quadio(inout [3:0] y, input en, input [3:0] d, output [3:0] rd);
  assign y = en ? d : 4'bzzzz;
  assign rd = y;
endmodule
module tb;
  wire p, q, r, s, w, v;
  wire [3:0] bus, rd1, rd2;
  reg en = 1'b0;
  reg [3:0] d = 4'b1010;
  reg pe = 1'b1;
  assign p = pe ? 1'b0 : 1'bz;
  assign s = pe ? 1'b1 : 1'bz;
  assign bus = pe ? 4'b0110 : 4'bzzzz;
  assign w = pe ? 1'b1 : 1'bz;
  quadio u1 (.y({p, q, r, s}), .en(en), .d(d), .rd(rd1));
  quadio u2 (.y({bus[2:1], w, v}), .en(en), .d(d), .rd(rd2));
  initial begin
    #10 $display("%0t c=%b%b%b%b %b %b %b%b %b", $time, p, q, r, s, rd1, bus, w, v, rd2);
    pe = 1'b0; en = 1'b1;
    #10 $display("%0t c=%b%b%b%b %b %b %b%b %b", $time, p, q, r, s, rd1, bus, w, v, rd2);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_settled_like_vvp()

    def test_individual_association(self):
        maps = self.port_maps()
        self.assertEqual({f: a for f, a in maps["u1"].items() if f.startswith("y")},
                         {"y(3)": "p", "y(2)": "q", "y(1)": "r", "y(0)": "s"})
        self.assertEqual({f: a for f, a in maps["u2"].items() if f.startswith("y")},
                         {"y(3 downto 2)": "bus_sig(1 + 1 downto 1)", "y(1)": "w", "y(0)": "v"})
        self.assertEqual(self.lines(r"<= SW\w*_a\("), [])


@needs_stack
class TestEscapedInstanceNames(Case):
    """Escaped instance names give valid VHDL labels; the T3 comment keeps the name."""

    SOURCE = """\
`timescale 1ns/1ps
module leaf(input a, output y);
  assign y = ~a;
endmodule
module tb;
  reg a = 1'b0;
  wire y1, y2, y3, y4;
  leaf \\a+b (.a(a), .y(y1));
  leaf \\x.y (.a(a), .y(y2));
  leaf \\9lives (.a(a), .y(y3));
  leaf plain (.a(a), .y(y4));
  initial begin
    #10 a = 1'b1;
    #10 $display("%0t y=%b%b%b%b", $time, y1, y2, y3, y4);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_settled_like_vvp()

    def test_labels(self):
        self.assertEqual(sorted(self.port_maps()), ["a_b", "inst_9lives", "plain", "x_y"])
        paths = sorted(re.findall(r"-- Verilog instance: (\S+)", self.vhdl))
        self.assertEqual(paths, ["9lives", "a+b", "plain", "x.y"])


# ------------------------------------------------------------------- TB-06

@needs_stack
class TestWrapperInputOnDrivenInout(Case):
    """A wrapper's input port passed straight to a child's driven inout port (a pad
    ring, plain and in a generate loop, behind one or two input levels, and a
    read-only inout chain), fed from a net, an expression and a constant."""

    SOURCE = """\
`timescale 1ns/1ps
module pad(inout p, input oe, input o, output i);
  assign p = oe ? o : 1'bz;
  assign i = p;
endmodule
module ring(inout [1:0] pads, input [1:0] oe, input [1:0] o, output [1:0] i);
  pad p0 (.p(pads[0]), .oe(oe[0]), .o(o[0]), .i(i[0]));
  pad p1 (.p(pads[1]), .oe(oe[1]), .o(o[1]), .i(i[1]));
endmodule
module gring(inout [1:0] pads, input [1:0] oe, input [1:0] o, output [1:0] i);
  genvar k;
  generate for (k = 0; k < 2; k = k + 1) begin : g
    pad pp (.p(pads[k]), .oe(oe[k]), .o(o[k]), .i(i[k]));
  end endgenerate
endmodule
module wrap(input [1:0] p, input [1:0] oe, input [1:0] o, output [1:0] i);
  ring r (.pads(p), .oe(oe), .o(o), .i(i));
endmodule
module gwrap(input [1:0] p, input [1:0] oe, input [1:0] o, output [1:0] i);
  gring r (.pads(p), .oe(oe), .o(o), .i(i));
endmodule
module wrap2(input [1:0] p, input [1:0] oe, input [1:0] o, output [1:0] i);
  wrap w (.p(p), .oe(oe), .o(o), .i(i));
endmodule
module leafr(inout m, output i);
  assign i = m;
endmodule
module midr(inout m, output i);
  leafr l (.m(m), .i(i));
endmodule
module wrapr(input p, output i);
  midr m (.m(p), .i(i));
endmodule
module tb;
  reg [1:0] pr, oe, o;
  reg rr;
  wire [1:0] pw, pg, p2, i1, i2, i3, i4, i5;
  wire ir;
  assign pw = pr;
  assign pg = pr;
  assign p2 = pr;
  wrap w1 (.p(pw), .oe(oe), .o(o), .i(i1));
  gwrap w2 (.p(pg), .oe(oe), .o(o), .i(i2));
  wrap2 w3 (.p(p2), .oe(oe), .o(o), .i(i3));
  wrap w4 (.p(pr ^ 2'b00), .oe(oe), .o(o), .i(i4));
  wrap w5 (.p(2'b10), .oe(oe), .o(o), .i(i5));
  wrapr w6 (.p(rr), .i(ir));
  initial begin
    pr = 2'b10; oe = 2'b00; o = 2'b00; rr = 1'b1;
    #10 $display("%0t v=%b %b %b %b %b %b %b %b %b", $time, i1, pw, i2, pg, i3, p2, i4, i5, ir);
    pr = 2'b01; rr = 1'b0;
    #10 $display("%0t v=%b %b %b %b %b %b %b %b %b", $time, i1, pw, i2, pg, i3, p2, i4, i5, ir);
    oe = 2'b01; o = 2'b00;
    #10 $display("%0t v=%b %b %b %b %b %b %b %b %b", $time, i1, pw, i2, pg, i3, p2, i4, i5, ir);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_settled_like_vvp()

    def test_wrapper_inputs_are_inout(self):
        for ent in ("wrap", "gwrap", "wrap2", "wrapr"):
            m = re.search(r"^entity %s\w* is\n\s*port \(\n\s*p : (\w+) (\w+)" % ent,
                          self.vhdl, re.M)
            self.assertTrue(m, ent)
            self.assertEqual(m.group(1), "inout", ent)
            self.assertTrue(m.group(2).startswith("resolved_"), ent)

    def test_one_way_copies(self):
        # an expression or a constant reaches the inout port through a copy
        arch = self.top_arch()
        self.assertRegex(arch, r"PB_w4_p <= ")
        self.assertRegex(arch, r"PB_w5_p <= ")


@needs_stack
class TestVariableOnDrivenInput(Case):
    """A variable on a wrapper input that a child drives through an inout port: a
    variable is never coerced to inout, so the drive stays one way (the child still
    sees the variable, and its own drive)."""

    SOURCE = """\
`timescale 1ns/1ps
module pad(inout p, input oe, input o, output i);
  assign p = oe ? o : 1'bz;
  assign i = p;
endmodule
module ring(inout [1:0] pads, input [1:0] oe, input [1:0] o, output [1:0] i);
  pad p0 (.p(pads[0]), .oe(oe[0]), .o(o[0]), .i(i[0]));
  pad p1 (.p(pads[1]), .oe(oe[1]), .o(o[1]), .i(i[1]));
endmodule
module wrap(input [1:0] p, input [1:0] oe, input [1:0] o, output [1:0] i);
  ring r (.pads(p), .oe(oe), .o(o), .i(i));
endmodule
module tb;
  reg [1:0] pr, oe, o;
  wire [1:0] i;
  wrap w (.p(pr), .oe(oe), .o(o), .i(i));
  initial begin
    pr = 2'b10; oe = 2'b00; o = 2'b00;
    #10 $display("%0t i=%b", $time, i);
    pr = 2'b01;
    #10 $display("%0t i=%b", $time, i);
    oe = 2'b01; o = 2'b00;
    #10 $display("%0t i=%b", $time, i);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_settled_like_vvp()

    def test_copy_not_join(self):
        self.assertEqual(self.port_maps()["w"]["p"], "PB_w_p")
        self.assertTrue(self.lines(r"^\s*PB_w_p <= pr;"), self.vhdl)


# ------------------------------------------------------- TB-07 / TB-08 / TB-09 / TB-10

@needs_stack
class TestRealParameters(Case):
    """Several instances of a module with a real parameter (one variant each, no
    assertion), printed exactly (0.2500001 is not 0.25), and an undriven real net."""

    SOURCE = """\
`timescale 1ns/1ps
module g #(parameter real GAIN = 0.25) (input [3:0] a, output real o);
  assign o = GAIN * 2.0;
endmodule
module tb;
  reg [3:0] a = 4'd3;
  wire real w1, w2, w3, w4, nd;
  g #(.GAIN(0.5)) u1 (.a(a), .o(w1));
  g #(.GAIN(0.9)) u2 (.a(a), .o(w2));
  g u3 (.a(a), .o(w3));
  g #(.GAIN(0.2500001)) u4 (.a(a), .o(w4));
  initial #10 $display("%0t w=%f %f %f %.9f %f", $time, w1, w2, w3, w4, nd);
endmodule
"""

    def test_like_vvp(self):
        self.assert_settled_like_vvp()

    def test_printed_exactly(self):
        gains = sorted(set(re.findall(r"^--   GAIN = (\S+)$", self.vhdl, re.M)))
        self.assertEqual(gains, ["0.25", "0.2500001", "0.5", "0.9"])

    def test_undriven_real_net(self):
        self.assertEqual(self.lines(r"^\s*nd <= L3D_Z"), [])


@needs_stack
class TestVariantParameterLines(Case):
    """A real parameter the body does not use (a multi-view cell's Verilog view) under
    a wrapper that overrides it: the variant the wrapper instantiates in design.vhd
    shows the wrapper's value, not another run's (sv-rename-variants)."""

    SOURCE = """\
`timescale 1ns/1ps
module mv #(parameter real GAIN = 0.25) (input a, output y);
  assign y = a;
endmodule
module a_wrap #(parameter real G = 0.25) (input a, output y);
  mv #(.GAIN(G)) u (.a(a), .y(y));
endmodule
module tb;
  reg a = 1'b0;
  wire y1, y2;
  a_wrap #(.G(0.9)) w (.a(a), .y(y1));
  mv z2 (.a(a), .y(y2));
  initial #10 $display("%0t y=%b%b", $time, y1, y2);
endmodule
"""
    # (w sorts before z2: in tb's run the 0.9 variant is the first one, named mv,
    # like the 0.25 one of a_wrap's own run -- the same name and body)

    def entity_gain(self, ent: str) -> str:
        m = re.search(r"^--   GAIN = (\S+)\n(?:--.*\n)*entity %s is" % re.escape(ent),
                      self.vhdl, re.M)
        self.assertTrue(m, ent)
        return m.group(1)

    def test_wrapper_variant_keeps_its_value(self):
        arch = self.top_arch()
        wvar = re.search(r"^\s*w: entity work\.(\w+)", arch, re.M).group(1)
        m = re.search(r"^architecture from_verilog of %s is\n(.*?)^end architecture;"
                      % re.escape(wvar), self.vhdl, re.M | re.S)
        self.assertTrue(m, wvar)
        inner = re.search(r"^\s*u: entity work\.(\w+)", m.group(1), re.M).group(1)
        self.assertEqual(self.entity_gain(inner), "0.9")
        z2var = re.search(r"^\s*z2: entity work\.(\w+)", arch, re.M).group(1)
        self.assertEqual(self.entity_gain(z2var), "0.25")
        self.assertNotEqual(inner, z2var)

    def test_metadata_has_the_stage(self):
        with open(os.path.join(self.outdir, "_metadata")) as fh:
            md = fh.read()
        self.assertIn("SV2VHDL_MODULES=1", md)
        self.assertIn('TOP_ENTITY="tb"', md)


@needs_stack
class TestWarningsSurfacedUnderVamos(unittest.TestCase):
    """Under vamos (VAMOS_STACK set), iverilog-sv2ghdl repeats the top run's
    "connected one way only" / "not translated" warnings on stderr; without it
    (the ivtest harness, which compares output with gold files) it does not."""

    SOURCE = TestT2TranOnBitSelect.SOURCE

    def translate(self, extra: Dict[str, str]) -> subprocess.CompletedProcess:
        tmp = tempfile.mkdtemp(prefix="vamos-xlat-warn-")
        try:
            src = os.path.join(tmp, "src.v")
            with open(src, "w") as fh:
                fh.write(self.SOURCE)
            env = tt._stack_env()
            env.pop("VAMOS_STACK", None)
            env.update(extra)
            return subprocess.run([os.path.join(BIN, "iverilog-sv2ghdl"), "-o", "vsim",
                                   "-g2012", src], cwd=tmp, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, universal_newlines=True,
                                  errors="replace", timeout=600)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_under_vamos(self):
        r = self.translate({"VAMOS_STACK": "vcs"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stderr, r"iverilog-sv2ghdl: Warning: bus_sig\(2\) at \S+ is "
                                   r"connected one way only")

    def test_quiet_otherwise(self):
        r = self.translate({})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("connected one way only", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
