// T5: tri1/tri0 nets pull with and without another driver: undriven,
// a tri-state assign, a partly driven vector, a child's bufif output and an
// unconnected tri1 input port.
`timescale 1ns/1ps
module xlat_od (input en, input d, output y);
  bufif1 b (y, d, en);
endmodule

module xlat_rd (input tri1 a, output y);
  assign y = a;
endmodule

module tb;
  tri1 t1;
  tri0 t0;
  reg en1 = 0, d1 = 0;
  tri1 t1d;
  assign t1d = en1 ? d1 : 1'bz;
  reg en0 = 0, d0 = 1;
  tri0 t0d;
  assign t0d = en0 ? d0 : 1'bz;
  reg en2 = 0;
  tri1 [3:0] v1;
  assign v1[1:0] = en2 ? 2'b00 : 2'bzz;
  reg en3 = 0, d3 = 0;
  tri1 t1c;
  xlat_od u (.en(en3), .d(d3), .y(t1c));
  wire r1 = t1d;
  wire ry;
  xlat_rd ur (.a(), .y(ry));
  always @(t1 or t0 or t1d or t0d or v1 or t1c or r1 or ry)
    $display("%0d t1=%b t0=%b t1d=%b t0d=%b v1=%b t1c=%b r1=%b ry=%b",
             $time, t1, t0, t1d, t0d, v1, t1c, r1, ry);
  initial begin
    #10 en1 = 1;
    #10 en0 = 1;
    #10 en2 = 1;
    #10 en3 = 1;
    #10 d1 = 1; d0 = 0; d3 = 1;
    #10 en1 = 0; en0 = 0; en2 = 0; en3 = 0;
    #10 $finish;
  end
endmodule
