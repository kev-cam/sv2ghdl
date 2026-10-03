// T1: $stop ends a batch run (std.env.stop), after flushing pending $write text.
`timescale 1ns/1ps
module tb;
  initial begin
    #5 $write("pending ");
    $stop;
    $display("FAILED: after $stop");
  end
  initial #20 $display("FAILED: still running");
endmodule
