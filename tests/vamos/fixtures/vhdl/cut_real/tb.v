`timescale 1ns/1ps
// real ports of a multi-view cell: a digitally driven real input (RD2A) and
// a real output read digitally (RA2D).
module tb;
  real vin_r;
  real vout_r;
  vamp a1 (.vin(vin_r), .vout(vout_r));
  initial begin
    vin_r = 0.5;
    #10 vin_r = 1.25;
    #10 $display("%f", vout_r);
  end
endmodule

`timescale 1ns/1ps
`default_nettype wire
module vamp (input real vin, output real vout);
endmodule
