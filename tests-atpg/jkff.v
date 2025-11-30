// JK flip-flop using gate primitives
module jkff (
    input clk,
    input rst,
    input j,
    input k,
    output reg q
);

wire q_next;
wire set_term, reset_term, hold_term;

// JK logic: q_next = (j AND ~q) OR (~k AND q)
wire not_q, not_k;
not not1 (not_q, q);
not not2 (not_k, k);

and and1 (set_term, j, not_q);
and and2 (hold_term, not_k, q);
or  or1  (q_next, set_term, hold_term);

always @(posedge clk or posedge rst) begin
    if (rst)
        q <= 1'b0;
    else
        q <= q_next;
end

endmodule
