// AND-OR gate with registered output
module and_or_reg (
    input clk,
    input rst,
    input a,
    input b,
    input c,
    input d,
    output reg y
);

wire and1_out, and2_out, or_out;

// Combinational logic: (a AND b) OR (c AND d)
and and1 (and1_out, a, b);
and and2 (and2_out, c, d);
or  or1  (or_out, and1_out, and2_out);

// Register the output
always @(posedge clk or posedge rst) begin
    if (rst)
        y <= 1'b0;
    else
        y <= or_out;
end

endmodule
