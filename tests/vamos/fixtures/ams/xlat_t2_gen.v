// T2: generate-loop pad rings, each pad on its own bus bit; each copy of
// the connection must reach its own bit (no shared temporary). The second
// ring sits in a module whose bus is its own inout port.
`timescale 1ns/1ps
module xlat_pad (inout pad, input oe, input o, output i);
  assign pad = oe ? o : 1'bz;
  assign i = pad;
endmodule

module xlat_ring (inout [1:0] pads, input [1:0] oe, input [1:0] o, output [1:0] i);
  genvar k;
  for (k = 0; k < 2; k = k + 1) begin : r
    xlat_pad p (.pad(pads[k]), .oe(oe[k]), .o(o[k]), .i(i[k]));
  end
endmodule

module tb;
  reg [3:0] oe = 4'b0000;
  reg [3:0] o = 4'b1010;
  reg [3:0] ext = 4'b0000;
  wire [3:0] pbus;
  wire [3:0] ii;
  genvar g;
  for (g = 0; g < 4; g = g + 1) begin : ring
    assign pbus[g] = ext[g] ? 1'b0 : 1'bz;
    xlat_pad u (.pad(pbus[g]), .oe(oe[g]), .o(o[g]), .i(ii[g]));
  end
  wire [1:0] rbus;
  wire [1:0] ri;
  assign rbus = ext[3] ? 2'b01 : 2'bzz;
  xlat_ring rr (.pads(rbus), .oe(oe[1:0]), .o(o[3:2]), .i(ri));
  always @(pbus) $display("%0d pbus=%b", $time, pbus);
  always @(ii) $display("%0d ii=%b", $time, ii);
  always @(rbus) $display("%0d rbus=%b", $time, rbus);
  always @(ri) $display("%0d ri=%b", $time, ri);
  initial begin
    #10 oe = 4'b0101;
    #10 o = 4'b0101;
    #10 oe = 4'b0000;
    #10 ext = 4'b0011;
    #10 oe = 4'b1111;
    #10 ext = 4'b1000;
    #10 oe = 4'b0000;
    #10 $finish;
  end
endmodule
