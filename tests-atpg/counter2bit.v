// 2-bit counter - simple behavioral version
module counter2bit (
    input clk,
    input rst,
    input en,
    output reg [1:0] count
);

always @(posedge clk or posedge rst) begin
    if (rst)
        count <= 2'b00;
    else if (en)
        count <= count + 1;
end

endmodule
