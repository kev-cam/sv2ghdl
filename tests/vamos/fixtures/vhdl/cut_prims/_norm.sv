`timescale 1ns/1ps
// Digital drivers of cut inputs: primitives (nand, pullup, bufif1), a weak
// assign, an and-assign, initial-block deposits, supply nets (scalar and
// vector), a pullup with supply1 strength, and an undriven wire.
module tb;
  reg a = 1'b0, b = 1'b1, en = 1'b0, r;
  wire n_nand, n_pull, n_tri, n_weak, n_and, n_pullsup, n_undrv;
  supply1 vdd;
  supply0 vss;
  supply1 [1:0] vbus;
  nand g1 (n_nand, a, b);
  pullup (n_pull);
  bufif1 g2 (n_tri, a, en);
  assign (weak1, weak0) n_weak = a;
  assign n_and = a & b;
  pullup (supply1) p1 (n_pullsup);
  cin c1 (.a(n_nand));
  cin c2 (.a(n_pull));
  cin c3 (.a(n_tri));
  cin c4 (.a(n_weak));
  cin c5 (.a(n_and));
  cin c6 (.a(n_pullsup));
  cin c7 (.a(r));
  cin c8 (.a(n_undrv));
  sup s1 (.vdd(vdd), .vss(vss), .vb(vbus));
  initial begin
    r = 1'b0;
    #10 r = 1'b1;
    #10 en = 1'b1;
    #10 a = 1'b1;
  end
endmodule

`timescale 1ns/1ps
`default_nettype wire
module cin (a);
  input a;
endmodule
`timescale 1ns/1ps
`default_nettype wire
module sup (vdd, vss, vb);
  input vdd;
  input vss;
  input [1:0] vb;
endmodule
