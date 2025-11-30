// Simple 2-input NAND gate
module nand_gate (
    input a,
    input b,
    output y
);

assign y = ~(a & b);

endmodule
