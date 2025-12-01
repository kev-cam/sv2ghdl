// AND-OR gate combinational logic
// Output: (a AND b) OR (c AND d)
module and_or_reg (
    input a,
    input b,
    input c,
    input d,
    output y
);

wire and1_out, and2_out;

// Combinational logic: (a AND b) OR (c AND d)
and and1 (and1_out, a, b);
and and2 (and2_out, c, d);
or  or1  (y, and1_out, and2_out);

endmodule
