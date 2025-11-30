// Simple 2-input NOR gate
module nor_gate (
    input a,
    input b,
    output y
);

assign y = ~(a | b);

endmodule
