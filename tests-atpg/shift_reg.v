// 4-bit shift register combinational logic (shift function)
// Implements q_out = {q_in[2:0], si}
module shift_reg (
    input si,
    input q_in0,
    input q_in1,
    input q_in2,
    input q_in3,
    output q_out0,
    output q_out1,
    output q_out2,
    output q_out3
);

// Shift left operation
buf buf0 (q_out0, si);
buf buf1 (q_out1, q_in0);
buf buf2 (q_out2, q_in1);
buf buf3 (q_out3, q_in2);

endmodule
