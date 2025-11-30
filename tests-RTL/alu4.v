// Simple 4-bit ALU
// op=00: ADD, op=01: SUB, op=10: AND, op=11: OR
module alu4 (
    input [3:0] a,
    input [3:0] b,
    input [1:0] op,
    output reg [3:0] result
);

always @(*) begin
    case (op)
        2'b00: result = a + b;
        2'b01: result = a - b;
        2'b10: result = a & b;
        2'b11: result = a | b;
    endcase
end

endmodule
