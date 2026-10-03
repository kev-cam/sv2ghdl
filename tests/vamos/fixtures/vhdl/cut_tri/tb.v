`timescale 1ns/1ps
// tri1 / tri0 nets on cut inputs, with and without another driver.  Until
// translator patch T5 the pull is lost (or becomes a strong constant).
module tb;
  reg a = 1'b0;
  tri1 t1;
  tri0 t0;
  tri1 t1d;
  assign t1d = a;
  cin c1 (.a(t1));
  cin c2 (.a(t0));
  cin c3 (.a(t1d));
endmodule

`timescale 1ns/1ps
`default_nettype wire
module cin (a);
  input a;
endmodule
