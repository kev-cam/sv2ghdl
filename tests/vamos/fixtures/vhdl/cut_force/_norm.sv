`timescale 1ns/1ps
// force/release on a net that reaches a cut port (an error in v1).
module tb;
  reg a = 1'b0;
  wire n;
  assign n = a;
  cin c1 (.a(n));
  initial begin
    #10 force n = 1'b1;
    #10 release n;
  end
endmodule

`timescale 1ns/1ps
`default_nettype wire
module cin (a);
  input a;
endmodule
