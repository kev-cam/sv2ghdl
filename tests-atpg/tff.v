// T flip-flop combinational logic
// Output is XOR of inputs t and q_in
module tff (
    input t,
    input q_in,
    output y
);

// T flip-flop next state logic: y = t XOR q_in
xor xor1 (y, t, q_in);

endmodule
