// T2: alias offsets on a vector with a non-zero LSB and on an ascending range
// (VHDL vectors are (w-1 downto 0): the alias index is the bit offset)
`timescale 1ns/1ps
module xlat_padv (inout pad, input oe, input o, output i);
  assign pad = oe ? o : 1'bz;
  assign i = pad;
endmodule

module tb;
  reg oe = 0, o = 1;
  wire [7:4] hi;      // pad on hi[5] -> offset 1
  wire [0:3] asc;     // pad on asc[0] -> the MSB, offset 3
  wire i1, i2;
  assign hi[7:6] = 2'b00;
  assign hi[4] = 1'b0;
  assign asc[1:3] = 3'b000;
  xlat_padv u1 (.pad(hi[5]), .oe(oe), .o(o), .i(i1));
  xlat_padv u2 (.pad(asc[0]), .oe(oe), .o(o), .i(i2));
  always @(hi) $display("%0d hi=%b", $time, hi);
  always @(asc) $display("%0d asc=%b", $time, asc);
  initial begin
    #10 oe = 1;
    #10 o = 0;
    #10 $finish;
  end
endmodule
