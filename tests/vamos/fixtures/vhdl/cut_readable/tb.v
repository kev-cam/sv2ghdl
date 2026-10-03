`timescale 1ns/1ps
// tgt-vhdl's _Readable shadows: (e) cell -> wrapper output -> second cell;
// (a) two cells in one wrapper on one output net, different formal names;
// (b) the same with equal formal names; (c) a wrapper inout port; and a
// bandgap -> ADC pair inside one module whose shared net is exported.
module tb;
  reg clk = 1'b0;
  always #10 clk = ~clk;
  wire ve, va, vb, vc, vref, q, qe;
  wrap_e we (.a(clk), .y(ve));
  sink ue (.a(ve), .q(qe));
  wrap_a wa (.a(clk), .y(va));
  wrap_b wb (.a(clk), .y(vb));
  wrap_c wc (.p(vc));
  bgadc bg (.clk(clk), .vref(vref), .q(q));
  always @(qe or va or vb or vc or q) $display("%0t %b %b %b %b %b", $time, qe, va, vb, vc, q);
endmodule

module wrap_e (input a, output y);
  src u (.a(a), .vo(y));
endmodule

module wrap_a (input a, output y);
  src u1 (.a(a), .vo(y));
  src2 u2 (.a(a), .q(y));
endmodule

module wrap_b (input a, output y);
  src u1 (.a(a), .vo(y));
  src u2 (.a(a), .vo(y));
endmodule

module wrap_c (inout p);
  pad_sp u (.pad(p));
endmodule

module bgadc (input clk, output vref, output q);
  bg u1 (.vref(vref));
  adc u2 (.vin(vref), .clk(clk), .q(q));
endmodule

`timescale 1ns/1ps
`default_nettype wire
module src (a, vo);
  input a;
  output vo;
  bufif1 vamos_ams_hiz_0 (vo, 1'b0, 1'b0);
endmodule
`timescale 1ns/1ps
`default_nettype wire
module src2 (a, q);
  input a;
  output q;
  bufif1 vamos_ams_hiz_0 (q, 1'b0, 1'b0);
endmodule
`timescale 1ns/1ps
`default_nettype wire
module sink (a, q);
  input a;
  output q;
  bufif1 vamos_ams_hiz_0 (q, 1'b0, 1'b0);
endmodule
`timescale 1ns/1ps
`default_nettype wire
module pad_sp (pad);
  inout pad;
  bufif1 vamos_ams_hiz_0 (pad, 1'b0, 1'b0);
endmodule
`timescale 1ns/1ps
`default_nettype wire
module bg (vref);
  output vref;
  bufif1 vamos_ams_hiz_0 (vref, 1'b0, 1'b0);
endmodule
`timescale 1ns/1ps
`default_nettype wire
module adc (vin, clk, q);
  input vin;
  input clk;
  output q;
  bufif1 vamos_ams_hiz_0 (q, 1'b0, 1'b0);
endmodule
