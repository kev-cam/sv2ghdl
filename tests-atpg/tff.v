// T flip-flop (toggle flip-flop) using DFF and XOR gate
module tff (
    input clk,
    input rst,
    input t,
    output reg q
);

wire q_next;

// T flip-flop: q_next = t XOR q
xor xor1 (q_next, t, q);

always @(posedge clk or posedge rst) begin
    if (rst)
        q <= 1'b0;
    else
        q <= q_next;
end

endmodule
