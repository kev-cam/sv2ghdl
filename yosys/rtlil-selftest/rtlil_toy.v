module rtoy(input clk, input rst, input [7:0] d, output [7:0] q, output [7:0] y, output [7:0] y2);
  reg [7:0] a = 8'h00;
  reg [7:0] b = 8'h00;
  reg [7:0] c = 8'h00;
  wire       en = d[0];
  wire [7:0] t_add = d + b;
  wire [7:0] a_next = rst ? 8'h00 : t_add;
  wire [7:0] t_xor = a ^ d;
  wire [7:0] t_addc = c + a;
  wire [7:0] t_and = a & b;
  always @(posedge clk) a <= a_next;
  always @(posedge clk or posedge rst)
    if (rst) b <= 8'h00; else b <= t_xor;
  // enable-gated register: the UNTAKEN branch must HOLD — exercises the
  // switch/case (decision-tree) form of the builder API
  always @(posedge clk)
    if (en) c <= t_addc;
  // small true memory: dynamic write (enable/addr from d) + dynamic read
  reg [7:0] mm [0:3];
  wire       men = d[3];
  wire [1:0] mwa = d[2:1];
  wire [1:0] mra = d[5:4];
  always @(posedge clk)
    if (men) mm[mwa] <= t_add;
  assign y2 = mm[mra];
  assign q = a;
  assign y = t_and ^ c;
endmodule
