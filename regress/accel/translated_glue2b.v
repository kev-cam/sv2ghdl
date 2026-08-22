// glue2b — reconstruction of the lost #68 "glue2" fixture class.
// Shape: an ACCEL-admitted registered chunk (bchunk) whose boundary
// input `pkt` is produced by the INTERP side at a DEEP delta of the
// same instant (gated flop -> comb buffer chain), later than the
// chunk's armed capture (~+3).  This is the wb_pkt
// capture-before-input family from the EH1a whole-core merge
// (aj_merged_12 m4_din = ifu_bus_rvalid -> BUS_RSP_VLD_FF.din).
//
// Recipe (after translation to VHDL via the standard ladder):
//   1. interp golden:   nvc -r (no accel)               -> q trace
//   2. accel + census:  NVC_ACCEL_MERGE=1 NVC_ACCEL_ASSERT_ORDER=3
//      expect: "CENSUS capture-before-producer chunk BCHUNK ...
//               captured at delta A, input pin 'PKT' produced at delta B>A"
//   3. fix:             NVC_ACCEL_CAPTURE_DELTA=<B> (global) or the
//      census-file per-chunk route -> byte-exact vs interp.
// Keep aprod INTERP (inadmissible via the $display in its always block).

module clockhdr (input CK, input EN, input SE, output Q);
  reg en_ff;
  always @(CK or EN or SE) if (!CK) en_ff = EN | SE;
  assign Q = CK & en_ff;
endmodule

// Interp-side producer: gated flop then a comb delay ladder so `pkt`
// commits several deltas after the root edge
module aprod (input clk, input rst_l, input [7:0] din, input en,
              output [7:0] pkt);
  wire gclk;
  clockhdr hdr (.CK(clk), .EN(en), .SE(1'b0), .Q(gclk));
  reg [7:0] stage;
  always @(posedge gclk or negedge rst_l) begin
    if (!rst_l) stage <= 8'h00;
    else begin
      stage <= din;
      if (din == 8'hFE) $display("aprod-marker");  // keeps aprod interp
    end
  end
  // +1 delta per hop
  wire [7:0] h1 = stage ^ 8'h00;
  wire [7:0] h2 = h1 | 8'h00;
  wire [7:0] h3 = h2 & 8'hFF;
  wire [7:0] h4 = h3 ^ 8'h00;
  wire [7:0] h5 = h4 | 8'h00;
  wire [7:0] h6 = h5 & 8'hFF;
  assign pkt = h6;
endmodule

// Accel-candidate consumer: plain registered chunk on the root clock
module bchunk (input clk, input rst_l, input [7:0] pkt,
               output reg [7:0] q);
  reg [7:0] acc;
  always @(posedge clk or negedge rst_l) begin
    if (!rst_l) begin acc <= 8'h00; q <= 8'h00; end
    else begin
      acc <= acc + pkt;
      q   <= acc ^ {pkt[3:0], pkt[7:4]};
    end
  end
endmodule

module top;
  reg clk = 0, rst_l = 0, en = 0;
  reg [7:0] din = 0;
  wire [7:0] pkt, q;

  aprod  a (.clk(clk), .rst_l(rst_l), .din(din), .en(en), .pkt(pkt));
  bchunk b (.clk(clk), .rst_l(rst_l), .pkt(pkt), .q(q));

  always #5 clk = ~clk;
  integer i;
  initial begin
    #12 rst_l = 1; en = 1;
    for (i = 0; i < 64; i = i + 1) begin
      din = i * 7 + 3;
      @(posedge clk);
      #1 $display("t=%0t q=%h pkt=%h", $time, q, pkt);
    end
    $finish;
  end
endmodule
