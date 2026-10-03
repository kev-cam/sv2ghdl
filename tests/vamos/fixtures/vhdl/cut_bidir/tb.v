`timescale 1ns/1ps
// A bidirectional SPICE pad: a tri-state Verilog driver with a pull-up on the
// pad net, plus a reader.  The pad cell has a pull-down inside (deck).
module tb;
  reg en = 1'b0, d = 1'b0;
  wire pad;
  assign pad = en ? d : 1'bz;
  pullup (pad);
  pad_sp u1 (.pad(pad));
  always @(pad) $display("%0t pad=%b", $time, pad);
  initial begin
    #20 en = 1'b1;
    #20 d = 1'b1;
    #20 en = 1'b0;
    #20 d = 1'b0;
  end
endmodule

`timescale 1ns/1ps
`default_nettype wire
module pad_sp (pad);
  inout pad;
  bufif1 vamos_ams_hiz_0 (pad, 1'b0, 1'b0);
endmodule
