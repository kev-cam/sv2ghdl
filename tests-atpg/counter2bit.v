// 2-bit counter combinational logic (increment function)
// Implements count_out = count_in + en
module counter2bit (
    input en,
    input count_in0,
    input count_in1,
    output count_out0,
    output count_out1
);

wire carry0;

// Bit 0: toggles when enabled
xor xor0 (count_out0, count_in0, en);

// Bit 1: toggles when bit 0 is high and enabled
and and1 (carry0, count_in0, en);
xor xor1 (count_out1, count_in1, carry0);

endmodule
