// Simple 2-input NAND gate
module nand_gate (
    input a,
    input b,
    output y
);

nand nand1 (y, a, b);

endmodule
