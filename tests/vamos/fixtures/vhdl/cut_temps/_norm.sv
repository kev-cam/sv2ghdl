`timescale 1ns/1ps
// Port temporaries: a concatenation with a supply0 bit, a supply1 actual, a
// cell output into v[0] read by a second cell, and a cell output on w[5:4]
// read by a cell on w[4].
module tb;
  reg x = 1'b0;
  supply0 gnd;
  supply1 vdd;
  wire [1:0] v;
  wire [7:0] w;
  wire q;
  cin2 c1 (.d({x, gnd}));
  cin c2 (.a(vdd));
  cout c3 (.y(v[0]));
  cin c4 (.a(v[0]));
  cout2 c5 (.y(w[5:4]));
  cin c6 (.a(w[4]));
  initial #10 x = 1'b1;
endmodule

`timescale 1ns/1ps
`default_nettype wire
module cin (a);
  input a;
endmodule
`timescale 1ns/1ps
`default_nettype wire
module cin2 (d);
  input [1:0] d;
endmodule
`timescale 1ns/1ps
`default_nettype wire
module cout (y);
  output y;
  bufif1 vamos_ams_hiz_0 (y, 1'b0, 1'b0);
endmodule
`timescale 1ns/1ps
`default_nettype wire
module cout2 (y);
  output [1:0] y;
  bufif1 vamos_ams_hiz_0 (y[1], 1'b0, 1'b0);
  bufif1 vamos_ams_hiz_1 (y[0], 1'b0, 1'b0);
endmodule
