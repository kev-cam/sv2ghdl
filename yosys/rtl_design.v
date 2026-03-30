// Synthesizable RTL design — no testbench, no delays

module lfsr32(input clk, input rst, output reg [31:0] q);
  always @(posedge clk or posedge rst)
    if (rst) q <= 32'hDEADBEEF;
    else     q <= {q[30:0], q[31] ^ q[21] ^ q[1] ^ q[0]};
endmodule

module counter32(input clk, input rst, output reg [31:0] count);
  always @(posedge clk or posedge rst)
    if (rst) count <= 0;
    else     count <= count + 1;
endmodule

module shift_reg #(parameter WIDTH=64) (
  input clk, input rst, input din,
  output reg [WIDTH-1:0] q
);
  always @(posedge clk or posedge rst)
    if (rst) q <= 0;
    else     q <= {q[WIDTH-2:0], din};
endmodule

module alu8(
  input [7:0] a, b,
  input [2:0] op,
  output reg [7:0] result,
  output reg carry
);
  always @(*) begin
    carry = 0;
    case (op)
      3'b000: {carry, result} = a + b;
      3'b001: {carry, result} = a - b;
      3'b010: result = a & b;
      3'b011: result = a | b;
      3'b100: result = a ^ b;
      3'b101: result = ~a;
      3'b110: result = a << b[2:0];
      3'b111: result = a >> b[2:0];
    endcase
  end
endmodule

module rtl_top(input clk, input rst,
               output [31:0] lfsr_out, output [31:0] count_out,
               output [63:0] shift_out, output [7:0] alu_result,
               output alu_carry);

  lfsr32    u_lfsr(.clk(clk), .rst(rst), .q(lfsr_out));
  counter32 u_cnt (.clk(clk), .rst(rst), .count(count_out));
  shift_reg u_sr  (.clk(clk), .rst(rst), .din(lfsr_out[0]), .q(shift_out));
  alu8      u_alu (.a(lfsr_out[7:0]), .b(count_out[7:0]),
                   .op(count_out[10:8]), .result(alu_result), .carry(alu_carry));
endmodule
