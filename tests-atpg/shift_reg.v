// 4-bit shift register
module shift_reg (
    input clk,
    input rst,
    input si,        // serial input
    output reg [3:0] q
);

always @(posedge clk or posedge rst) begin
    if (rst)
        q <= 4'b0000;
    else
        q <= {q[2:0], si};  // shift left, insert si at LSB
end

endmodule
