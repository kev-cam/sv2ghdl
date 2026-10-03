// T2: a tristate pad whose inout port is connected to a bus bit-select.
// Both directions must work: the pad drives pbus[2], and the pad reads an
// external driver of pbus[2]. Every bus bit has a driver.
`timescale 1ns/1ps
module xlat_pad (inout pad, input oe, input o, output i);
  assign pad = oe ? o : 1'bz;
  assign i = pad;
endmodule

module tb;
  reg oe = 0, o = 1, ext_en = 0;
  wire [3:0] pbus;
  wire ii;
  assign pbus[1:0] = 2'b01;
  assign pbus[3] = 1'b1;
  assign pbus[2] = ext_en ? 1'b0 : 1'bz;
  xlat_pad u2 (.pad(pbus[2]), .oe(oe), .o(o), .i(ii));
  always @(pbus) $display("%0d pbus=%b", $time, pbus);
  always @(ii) $display("%0d ii=%b", $time, ii);
  initial begin
    #10 oe = 1;          // the pad drives 1 onto pbus[2]
    #10 o = 0;           // ... then 0
    #10 oe = 0;          // released: z
    #10 ext_en = 1;      // an external 0 reaches the pad's reader
    #10 oe = 1; o = 1;   // contention: x
    #10 $finish;
  end
endmodule
