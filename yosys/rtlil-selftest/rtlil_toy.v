module rtoy(input clk, input rst, input [7:0] d, output [7:0] q, output [7:0] y);
  reg [7:0] a = 8'h00;
  reg [7:0] b = 8'h00;
  wire [7:0] t_add = d + b;
  wire [7:0] a_next = rst ? 8'h00 : t_add;
  wire [7:0] t_xor = a ^ d;
  always @(posedge clk) a <= a_next;
  always @(posedge clk or posedge rst)
    if (rst) b <= 8'h00; else b <= t_xor;
  assign q = a;
  assign y = a & b;
endmodule
