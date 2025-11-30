// 2-bit counter using gate primitives and flip-flops
module counter2bit (
    input clk,
    input rst,
    input en,
    output reg [1:0] count
);

wire [1:0] count_next;
wire count0_toggle;

// count[0] toggles when enabled
and and1 (count0_toggle, en, 1'b1);
xor xor0 (count_next[0], count[0], count0_toggle);

// count[1] toggles when count[0] is 1 and enabled
wire toggle1;
and and2 (toggle1, count[0], en);
xor xor1 (count_next[1], count[1], toggle1);

always @(posedge clk or posedge rst) begin
    if (rst)
        count <= 2'b00;
    else
        count <= count_next;
end

endmodule
