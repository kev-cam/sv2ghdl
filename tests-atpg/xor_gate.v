// Simple 2-input XOR gate
module xor_gate (
    input a,
    input b,
    output y
);

xor xor1 (y, a, b);

endmodule
