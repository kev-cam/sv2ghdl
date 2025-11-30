// Simple 2-input NOR gate
module nor_gate (
    input a,
    input b,
    output y
);

nor nor1 (y, a, b);

endmodule
