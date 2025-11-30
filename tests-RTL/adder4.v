// 4-bit adder with carry
module adder4 (
    input [3:0] a,
    input [3:0] b,
    input cin,
    output reg [3:0] sum,
    output reg cout
);

always @(*) begin
    {cout, sum} = a + b + cin;
end

endmodule
