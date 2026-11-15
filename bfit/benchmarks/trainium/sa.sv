// Weight-stationary systolic MAC array, N x N PEs, int8 x int8 -> int32.
//  - weights shift DOWN each column while load_w=1 (N cycles to fill; inject W[N-1-t][c] at cycle t)
//  - activations flow RIGHT along rows (row k must be fed A[m][k] at cycle m+k: the test bench skews)
//  - partial sums flow DOWN; column c emits C[m][c] = sum_k A[m][k]*W[k][c] at the bottom, cycle m+N+c-1
// State per PE: a_q (8) + w_q (8) + p_q (32) = 48 bits.  N=128 -> 16,384 PEs, 786,432 state bits.
`default_nettype none
module pe #(parameter AW = 8, WW = 8, PW = 32) (
    input  wire                 clk,
    input  wire                 rst,
    input  wire                 load_w,
    input  wire signed [AW-1:0] a_in,
    input  wire signed [WW-1:0] w_in,
    input  wire signed [PW-1:0] p_in,
    output reg  signed [AW-1:0] a_q,
    output reg  signed [WW-1:0] w_q,
    output reg  signed [PW-1:0] p_q
);
    always @(posedge clk) begin
        if (rst) begin
            a_q <= '0; w_q <= '0; p_q <= '0;
        end else begin
            a_q <= a_in;
            if (load_w) w_q <= w_in;
            p_q <= p_in + PW'(a_in * w_q);
        end
    end
endmodule

module sa #(parameter N = 128, AW = 8, WW = 8, PW = 32) (
    input  wire                 clk,
    input  wire                 rst,
    input  wire                 load_w,
    input  wire signed [N*AW-1:0] a_in,    // one activation per row (left edge)
    input  wire signed [N*WW-1:0] w_in,    // one weight per column (top edge)
    output wire signed [N*PW-1:0] p_out    // one partial sum per column (bottom edge)
);
    wire signed [AW-1:0] a_bus [0:N-1][0:N];   // a_bus[r][c] = input to PE(r,c); [r][N] unused tail
    wire signed [WW-1:0] w_bus [0:N][0:N-1];   // w_bus[r][c] = input to PE(r,c) from above
    wire signed [PW-1:0] p_bus [0:N][0:N-1];   // p_bus[r][c] = input to PE(r,c) from above

    genvar r, c;
    generate
        for (c = 0; c < N; c = c + 1) begin : g_top
            assign w_bus[0][c] = w_in[c*WW +: WW];
            assign p_bus[0][c] = '0;
            assign p_out[c*PW +: PW] = p_bus[N][c];
        end
        for (r = 0; r < N; r = r + 1) begin : g_row
            assign a_bus[r][0] = a_in[r*AW +: AW];
            for (c = 0; c < N; c = c + 1) begin : g_col
                pe #(.AW(AW), .WW(WW), .PW(PW)) u_pe (
                    .clk(clk), .rst(rst), .load_w(load_w),
                    .a_in(a_bus[r][c]), .w_in(w_bus[r][c]), .p_in(p_bus[r][c]),
                    .a_q(a_bus[r][c+1]), .w_q(w_bus[r+1][c]), .p_q(p_bus[r+1][c]));
            end
        end
    endgenerate
endmodule
`default_nettype wire
