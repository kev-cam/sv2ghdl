// 2:1 multiplexer with 4-bit data
module mux2to1 (
    input [3:0] a,
    input [3:0] b,
    input sel,
    output reg [3:0] out
);

always @(*) begin
    if (sel)
        out = b;
    else
        out = a;
end

endmodule
