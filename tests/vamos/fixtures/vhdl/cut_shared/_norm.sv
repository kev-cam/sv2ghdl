`timescale 1ns/1ps
// A wrapper holding a cut cell, instantiated twice (shared parent).  w1.u3 is
// driven by clk2 and hosts its own D2A and A2D; w2.u3 shares both nets with
// u1 (clk in, yb out), so on that path both of its bits are passive.
module tb;
  reg clk = 1'b0;
  reg clk2 = 1'b0;
  wire yb, y3;
  always #10 clk = ~clk;
  always #15 clk2 = ~clk2;
  rc_sp u1 (.a(clk), .y(yb));
  wrap w1 (.a(clk2), .y(y3));
  wrap w2 (.a(clk), .y(yb));
  always @(yb) $display("%0t yb=%b", $time, yb);
  always @(y3) $display("%0t y3=%b", $time, y3);
endmodule

module wrap (input a, output y);
  rc_sp u3 (.a(a), .y(y));
endmodule

`timescale 1ns/1ps
`default_nettype wire
module rc_sp (a, y);
  input a;
  output y;
  bufif1 vamos_ams_hiz_0 (y, 1'b0, 1'b0);
endmodule
