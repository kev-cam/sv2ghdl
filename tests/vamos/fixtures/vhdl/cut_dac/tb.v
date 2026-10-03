`timescale 1ns/1ps
// A parameterised multi-view cell (flash) used with N=4 and N=8 (two
// variants, generate-loop markers on a [N-1:0] output), and a SPICE-only cell
// split into two variants by a tied net (wire t; assign t = 1'b1).
module tb;
  reg clk = 1'b0;
  wire [3:0] q4;
  wire [7:0] q8;
  wire t, pw, p1, p2;
  assign t = 1'b1;
  assign pw = clk;
  always #10 clk = ~clk;
  flash #(.N(4)) f4 (.clk(clk), .q(q4));
  flash #(.N(8)) f8 (.clk(clk), .q(q8));
  pio_sp u1 (.pad(t), .y(p1));
  pio_sp u2 (.pad(pw), .y(p2));
  always @(q4 or q8 or p1 or p2) $display("%0t %b %b %b %b", $time, q4, q8, p1, p2);
endmodule

`timescale 1ns/1ps
`default_nettype wire
module flash #(parameter N = 4) (input clk, output [N-1:0] q);
  genvar vamos_ams_g_q; for (vamos_ams_g_q = ((N-1) < (0) ? (N-1) : (0)); vamos_ams_g_q <= ((N-1) > (0) ? (N-1) : (0)); vamos_ams_g_q = vamos_ams_g_q + 1) begin : vamos_ams_hiz_q bufif1 vamos_ams_hiz (q[vamos_ams_g_q], 1'b0, 1'b0); end
endmodule
`timescale 1ns/1ps
`default_nettype wire
module pio_sp (pad, y);
  inout pad;
  output y;
  bufif1 vamos_ams_hiz_0 (pad, 1'b0, 1'b0);
  bufif1 vamos_ams_hiz_1 (y, 1'b0, 1'b0);
endmodule
