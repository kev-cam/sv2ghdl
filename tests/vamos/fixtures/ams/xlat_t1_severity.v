// T1: $info/$warning/$error print vvp's two lines and the run continues;
// $fatal prints them and ends the run with a failure.
`timescale 1ns/1ps
module tb;
  integer k = 7;
  initial begin
    #1 $info("k is %0d", k);
    #1 $warning("careful");
    #1 $error;
    #1 $display("still running");
    #1 $fatal(2, "giving up at k=%0d", k);
    #1 $display("FAILED: after $fatal");
  end
endmodule
