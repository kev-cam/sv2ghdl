// D flip-flop combinational logic (mux controlled by rst)
// When rst=1, output is 0; otherwise output follows d
module dff (
    input rst,
    input d,
    output y
);

wire not_rst, d_gated;

not not1 (not_rst, rst);
and and1 (d_gated, d, not_rst);
buf buf1 (y, d_gated);

endmodule
