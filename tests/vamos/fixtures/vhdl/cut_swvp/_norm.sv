`timescale 1ns/1ps
// inout cut ports on bus bits: a generate pad ring and a plain bit-select.
// Without translator patch T2 these go through one-way SW_ivl_*_b copies
// (the temporaries guard rejects them); the T2 form is an alias.
module tb;
  reg [3:0] dout = 4'b0101;
  reg oe = 1'b0;
  wire [3:0] pbus;
  wire [1:0] qbus;
  assign pbus = oe ? dout : 4'bzzzz;
  genvar g;
  generate for (g = 0; g < 2; g = g + 1) begin : ring
    pad_sp u (.pad(pbus[g]));
  end endgenerate
  pad_sp u2 (.pad(pbus[2]));
  pad_sp u9 (.pad(qbus[1]));
  always @(pbus or qbus) $display("%0t %b %b", $time, pbus, qbus);
  initial #20 oe = 1'b1;
endmodule

`timescale 1ns/1ps
`default_nettype wire
module pad_sp (pad);
  inout pad;
  bufif1 vamos_ams_hiz_0 (pad, 1'b0, 1'b0);
endmodule
