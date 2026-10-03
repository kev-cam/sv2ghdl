// vamos e2e 7 (docs/VAMOS_AMS_DESIGN.md §9): a small testbench in x-heep's directory
// layout.  It instantiates the SPICE-only cell ams_adc_1b the way
// hw/ip_examples/ams/rtl/ams.sv does: instance ams_adc_1b_i, sel from a register-file
// struct member, out into another one (x-heep's ams.core leaves dummy_adc.sv out under
// ams_sim, so the cell has no Verilog view).  sel walks the mux through the ladder taps
// 0.24 V (00), 0.48 V (01) and 0.72 V (10) while adc.sp's 1 MHz sine runs; every change
// of out is printed with its exact time ($realtime with %f, in ns).
`timescale 1ns/1ps
module tb_ams_adc;
  typedef struct packed { logic [1:0] q; } ams_reg2hw_sel_reg_t;
  typedef struct packed { ams_reg2hw_sel_reg_t sel; } ams_reg2hw_t;
  typedef struct packed { logic d; logic de; } ams_hw2reg_get_reg_t;
  typedef struct packed { ams_hw2reg_get_reg_t get; } ams_hw2reg_t;

  ams_reg2hw_t reg2hw;
  ams_hw2reg_t hw2reg;

  assign hw2reg.get.de = 1;

  ams_adc_1b ams_adc_1b_i (
      .sel(reg2hw.sel.q),
      .out(hw2reg.get.d)
  );

  always @(hw2reg.get.d) $display("%f out=%b", $realtime, hw2reg.get.d);

  initial begin
    reg2hw.sel.q = 2'b00;
    #1300 reg2hw.sel.q = 2'b01;
    #1300 reg2hw.sel.q = 2'b10;
    #1300 $finish;
  end
endmodule
