`timescale 1ns/1ps
// Port buffers (test_ams_cut.py TestPortBuffers, test_ams_vhdl.py): the iverilog
// core buffers an input port whose net a cut cell's inout or output port (its shell
// marker) or a digital driver also drives inside; tgt-vhdl draws the buffer as a
// resolved PB_<label>_<port> signal of the parent, `PB_.. <= <actual>`, associated
// with the port.  Fed from: a variable (we, beside the variable's own SPICE input
// c1), a bit of a vector variable (wb), an expression (wx), a vector variable on a
// vector port (wv); a wrapper that also reads the port (wr), an output cut port
// (wo), a digital driver inside the wrapper (wd).
module tb;
  reg clk = 1'b0;
  reg [1:0] rv = 2'b01;
  always #10 clk = ~clk;
  always #20 rv = rv + 2'b01;
  wire ve, vb, vx, vr, qr, vo, vd, qd;
  wire [1:0] vv;
  cin c1 (.a(clk));
  wrap_e we (.a(clk), .y(ve));
  wrap_e wb (.a(rv[1]), .y(vb));
  wrap_e wx (.a(~clk), .y(vx));
  wrap_v wv (.a(rv), .y(vv));
  wrap_r wr (.a(clk), .y(vr), .q(qr));
  wrap_o wo (.a(clk), .y(vo));
  wrap_d wd (.a(clk), .en(rv[0]), .y(vd), .q(qd));
  always @(ve or vb or vx or vv or vr or qr or vo or vd or qd)
    $display("%0t %b %b %b %b %b %b %b %b %b", $time, ve, vb, vx, vv, vr, qr, vo, vd, qd);
endmodule

module wrap_e (input a, output y);
  srci u (.a(a), .vo(y));
endmodule

module wrap_v (input [1:0] a, output [1:0] y);
  srcv u (.a(a), .vo(y));
endmodule

module wrap_r (input a, output y, output q);
  srci u (.a(a), .vo(y));
  assign q = ~a;
endmodule

module wrap_o (input a, output y);
  srco u (.a(a), .vo(y));
endmodule

module wrap_d (input a, input en, output y, output q);
  srci u (.a(a), .vo(y));
  bufif1 b (a, 1'b0, en);
  assign q = a;
endmodule

`timescale 1ns/1ps
`default_nettype wire
module cin (a);
  input a;
endmodule
`timescale 1ns/1ps
`default_nettype wire
module srci (a, vo);
  inout a;
  output vo;
  bufif1 vamos_ams_hiz_0 (a, 1'b0, 1'b0);
  bufif1 vamos_ams_hiz_1 (vo, 1'b0, 1'b0);
endmodule
`timescale 1ns/1ps
`default_nettype wire
module srcv (a, vo);
  inout [1:0] a;
  output [1:0] vo;
  bufif1 vamos_ams_hiz_0 (a[1], 1'b0, 1'b0);
  bufif1 vamos_ams_hiz_1 (a[0], 1'b0, 1'b0);
  bufif1 vamos_ams_hiz_2 (vo[1], 1'b0, 1'b0);
  bufif1 vamos_ams_hiz_3 (vo[0], 1'b0, 1'b0);
endmodule
`timescale 1ns/1ps
`default_nettype wire
module srco (a, vo);
  output a;
  output vo;
  bufif1 vamos_ams_hiz_0 (a, 1'b0, 1'b0);
  bufif1 vamos_ams_hiz_1 (vo, 1'b0, 1'b0);
endmodule
