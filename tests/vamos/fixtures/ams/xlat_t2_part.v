// T2: inout ports connected to part-selects, directly and through a wrapper
// whose own inout bus is part-selected again.
`timescale 1ns/1ps
module xlat_pad2 (inout [1:0] pad, input oe, input [1:0] o, output [1:0] i);
  assign pad = oe ? o : 2'bzz;
  assign i = pad;
endmodule

module xlat_wrap (inout [3:0] w, input oe, input [1:0] o, output [1:0] i);
  xlat_pad2 p (.pad(w[2:1]), .oe(oe), .o(o), .i(i));
endmodule

module tb;
  reg oe = 0, ext = 0;
  reg [1:0] o = 2'b10;
  wire [7:0] pbus;
  wire [1:0] ii, jj;
  assign pbus[1:0] = 2'b10;
  assign pbus[3:2] = ext ? 2'b01 : 2'bzz;
  assign pbus[4] = 1'b1;
  assign pbus[7] = 1'b0;
  xlat_pad2 u (.pad(pbus[3:2]), .oe(oe), .o(o), .i(ii));
  xlat_wrap uw (.w(pbus[7:4]), .oe(oe), .o(~o), .i(jj));
  always @(pbus) $display("%0d pbus=%b", $time, pbus);
  always @(ii) $display("%0d ii=%b", $time, ii);
  always @(jj) $display("%0d jj=%b", $time, jj);
  initial begin
    #10 oe = 1;
    #10 o = 2'b01;
    #10 oe = 0;
    #10 ext = 1;
    #10 $finish;
  end
endmodule
