// SPDX-License-Identifier: Apache-2.0
//
// tb_hazard3.v - testbench for the portable hazard3 mandelbrot variants.
//
// The clock loop follows main_verilator.cpp of the upstream test case
//   https://github.com/verijit/verilator-hazard3-mandelbrot-testbench
//   commit 9e768306a3cb03b9894aee609545fe3ed829d6be, file main_verilator.cpp
// (Apache License, Version 2.0; LICENSES/Apache-2.0.txt at the root of this
// repository), which per cycle sets clock = 0, evaluates, sets clock = 1,
// evaluates, and counts the cycle. CHANGED: this is a Verilog testbench, not a
// C++ harness: no hierarchical references and no C++, so every simulator runs
// it as is. Instead of polling 'finished' and reading the image out of the
// RAM, it prints every word the SoC's TOHOST snooper captures, in a fixed
// format, and ends at the firmware's DONE marker.
//
// Output (the regression harness compares the TOHOST lines with the golden):
//   TOHOST <8 hex digits>                     one line per captured word
//   HAZARD3 DONE cycles=<n> words=<n>         at the DONE marker, then $finish
//   HAZARD3 FAIL cycle cap <n> reached ...    if DONE has not come by CYCLE_CAP
//
// Defines: CYCLE_CAP (cycles before giving up; default 50000000). The SoC
// (soc_portable.v from gen_soc.py) needs SIM defined.

`timescale 1ns/1ps

`ifndef CYCLE_CAP
`define CYCLE_CAP 50000000
`endif

module tb;
  reg clock;
  wire [31:0] keep_alive;
  wire finished;
  wire tohost_valid;
  wire [31:0] tohost_data;
  integer cycles;
  integer words;

  soc dut (
    .clock(clock),
    .keep_alive(keep_alive),
    .finished(finished),
    .tohost_valid(tohost_valid),
    .tohost_data(tohost_data)
  );

  // Two phases per cycle, as main_verilator.cpp: clock low, then high.
  initial begin
    cycles = 0;
    words = 0;
    clock = 1'b0;
    forever begin
      #5 clock = 1'b1;
      #5 clock = 1'b0;
    end
  end

  // cycles = number of rising edges so far (main_verilator.cpp's 'cycle'
  // after that many loop iterations).
  always @(posedge clock) begin
    cycles = cycles + 1;
    if (tohost_valid) begin
      words = words + 1;
      $display("TOHOST %h", tohost_data);
      if (tohost_data == 32'hffffffff) begin
        $display("HAZARD3 DONE cycles=%0d words=%0d", cycles, words);
        $finish;
      end
    end
    if (cycles >= `CYCLE_CAP) begin
      $display("HAZARD3 FAIL cycle cap %0d reached after %0d TOHOST words (no DONE marker)",
               cycles, words);
      $finish;
    end
  end
endmodule
