`timescale 1ns/1ps
// Declared cut outputs into vector bits: an instance array, a generate loop
// over v[i], two plain instances on v[0]/v[1]; a vector with one digitally
// driven bit; a bus input fed by a bit-select pair and a part-select.
module tb;
  reg clk = 1'b0;
  reg [3:0] code = 4'b0101;
  wire [1:0] arr, gy, two;
  wire [3:0] mix;
  always #10 clk = ~clk;
  cout ca [1:0] (.y(arr));
  genvar i;
  generate for (i = 0; i < 2; i = i + 1) begin : g
    cout u (.y(gy[i]));
  end endgenerate
  cout t0 (.y(two[0]));
  cout t1 (.y(two[1]));
  assign mix[3] = clk;
  cout m0 (.y(mix[0]));
  cin2 ub (.d({code[0], code[1]}));
  cin2 up (.d(code[3:2]));
  always @(arr or gy or two or mix) $display("%0t %b %b %b %b", $time, arr, gy, two, mix);
endmodule

`timescale 1ns/1ps
`default_nettype wire
module cout (y);
  output y;
  bufif1 vamos_ams_hiz_0 (y, 1'b0, 1'b0);
endmodule
`timescale 1ns/1ps
`default_nettype wire
module cin2 (d);
  input [1:0] d;
endmodule
