// Simple counter for testing sv2ghdl
module counter (
    input clk,
    input rst,
    output reg [7:0] count
);

always @(posedge clk) begin
    if (rst)
        count <= 8'h00;
    else
        count <= count + 1;
end

endmodule
