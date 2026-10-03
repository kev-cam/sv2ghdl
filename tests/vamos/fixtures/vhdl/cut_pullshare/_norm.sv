`timescale 1ns/1ps
// A wrapper with a pullup on a net that reaches a cut port, instantiated
// twice: on w1 the net is bidirectional (the pull must move into the deck),
// on w2 it is a plain D2A (the pull stays digital).  One architecture cannot
// do both: an error in v1.
module tb;
  reg en = 1'b0;
  wire p1, p2;
  assign p1 = en ? 1'b0 : 1'bz;
  always @(p1) $display("%0t p1=%b", $time, p1);
  pw w1 (.p(p1));
  pw w2 (.p(p2));
endmodule

module pw (inout p);
  pullup (p);
  pio u (.pad(p));
endmodule

`timescale 1ns/1ps
`default_nettype wire
module pio (pad);
  inout pad;
  bufif1 vamos_ams_hiz_0 (pad, 1'b0, 1'b0);
endmodule
