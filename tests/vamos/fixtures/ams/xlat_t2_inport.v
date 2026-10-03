// T2 fallback: an inout port on a bit of the parent's *input* port cannot
// be an alias (VHDL cannot drive an `in' port): a one-way copy, with a
// warning, and the design still translates and runs.
`timescale 1ns/1ps
module xlat_rd (inout pad, output i);
  assign i = pad;
endmodule

module xlat_mid (input [1:0] d, output i);
  xlat_rd r (.pad(d[1]), .i(i));
endmodule

module tb;
  reg [1:0] d = 2'b00;
  wire i;
  xlat_mid m (.d(d), .i(i));
  always @(i) $display("%0d i=%b", $time, i);
  initial begin
    #10 d = 2'b10;
    #10 d = 2'b01;
    #10 $finish;
  end
endmodule
