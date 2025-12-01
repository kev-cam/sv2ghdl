// JK flip-flop combinational logic using gate primitives
// Next state: (j AND ~q) OR (~k AND q)
module jkff (
    input j,
    input k,
    input q_in,
    output y
);

wire set_term, hold_term;
wire not_q, not_k;

// JK logic: y = (j AND ~q_in) OR (~k AND q_in)
not not1 (not_q, q_in);
not not2 (not_k, k);

and and1 (set_term, j, not_q);
and and2 (hold_term, not_k, q_in);
or  or1  (y, set_term, hold_term);

endmodule
