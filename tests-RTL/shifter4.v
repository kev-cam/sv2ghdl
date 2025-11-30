// 4-bit shift register with parallel load
module shifter4 (
    input clk,
    input rst,
    input load,
    input [3:0] din,
    input sin,
    output reg [3:0] q
);

always @(posedge clk or posedge rst) begin
    if (rst)
        q <= 4'b0000;
    else if (load)
        q <= din;
    else
        q <= {q[2:0], sin};
end

endmodule
