// T3: Verilog instance names through generate loops, an if-generate, an
// unnamed generate block, nested loops and an instance array.
`timescale 1ns/1ps
module xlat_leaf (input a, output y);
  assign y = a;
endmodule

module xlat_mid (input [1:0] a, output [1:0] y);
  genvar i;
  for (i = 0; i < 2; i = i + 1) begin : g
    xlat_leaf xb (.a(a[i]), .y(y[i]));
  end
endmodule

module tb;
  reg [1:0] a = 2'b01;
  wire [1:0] y, y2, y3;
  wire y4;
  xlat_mid m (.a(a), .y(y));
  xlat_leaf ua [1:0] (.a(a), .y(y2));
  generate if (1) begin : gi
    xlat_leaf ui (.a(a[0]), .y(y3[0]));
  end endgenerate
  generate if (1)
    xlat_leaf un (.a(a[1]), .y(y3[1]));
  endgenerate
  genvar j, k;
  for (j = 0; j < 1; j = j + 1) begin : outer
    for (k = 1; k < 2; k = k + 1) begin : inner
      xlat_leaf deep (.a(a[0]), .y(y4));
    end
  end
  initial #1 $display("%0d y=%b y2=%b y3=%b y4=%b", $time, y, y2, y3, y4);
endmodule
