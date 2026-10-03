`timescale 1ns/1ps
// Reserved-word and odd port names, a port named after another module, and
// cut cells whose names tgt-vhdl renames (buffer, block, register, my__cell,
// cell_, _cell).
module tb;
  reg a_in = 1'b0, a_signal = 1'b1, a_bus = 1'b0, a_ua = 1'b1;
  wire w_out, w_OUT, w_open, w_cd, w_inv;
  wire w_b;
  wire y_buf, y_blk, y_reg, y_my, y_c1, y_c2, y_inv;
  rw u1 (.in(a_in), .out(w_out), .signal(a_signal), .bus(a_bus),
         .open(w_open), ._a(a_ua), .b_(w_b), .c__d(w_cd), .inv(w_inv));
  rw2 u2 (.OUT(w_OUT), .In(a_in));
  inv i1 (.a(a_in), .y(y_inv));
  buffer ub (.a(a_in), .y(y_buf));
  block ubl (.a(a_signal), .y(y_blk));
  register ure (.a(a_bus), .y(y_reg));
  my__cell um (.a(a_ua), .y(y_my));
  cell_ uc1 (.a(a_in), .y(y_c1));
  _cell uc2 (.a(a_in), .y(y_c2));
  initial begin
    #5 a_in = 1'b1;
    #5 $display("%b %b %b %b %b %b %b %b %b %b %b %b", w_out, w_OUT, w_open, w_cd, w_inv, w_b,
                y_buf, y_blk, y_reg, y_my, y_c1, y_c2);
    #5 $finish;
  end
endmodule

module inv (input a, output y);
  assign y = ~a;
endmodule

`timescale 1ns/1ps
`default_nettype wire
module rw (in, out, signal, bus, open, _a, b_, c__d, inv);
  input in;
  output out;
  input signal;
  input bus;
  output open;
  input _a;
  inout b_;
  output c__d;
  output inv;
  bufif1 vamos_ams_hiz_0 (out, 1'b0, 1'b0);
  bufif1 vamos_ams_hiz_1 (open, 1'b0, 1'b0);
  bufif1 vamos_ams_hiz_2 (b_, 1'b0, 1'b0);
  bufif1 vamos_ams_hiz_3 (c__d, 1'b0, 1'b0);
  bufif1 vamos_ams_hiz_4 (inv, 1'b0, 1'b0);
endmodule
`timescale 1ns/1ps
`default_nettype wire
module rw2 (OUT, In);
  output OUT;
  input In;
  bufif1 vamos_ams_hiz_0 (OUT, 1'b0, 1'b0);
endmodule
`timescale 1ns/1ps
`default_nettype wire
module buffer (a, y);
  input a;
  output y;
  bufif1 vamos_ams_hiz_0 (y, 1'b0, 1'b0);
endmodule
`timescale 1ns/1ps
`default_nettype wire
module block (a, y);
  input a;
  output y;
  bufif1 vamos_ams_hiz_0 (y, 1'b0, 1'b0);
endmodule
`timescale 1ns/1ps
`default_nettype wire
module register (a, y);
  input a;
  output y;
  bufif1 vamos_ams_hiz_0 (y, 1'b0, 1'b0);
endmodule
`timescale 1ns/1ps
`default_nettype wire
module my__cell (a, y);
  input a;
  output y;
  bufif1 vamos_ams_hiz_0 (y, 1'b0, 1'b0);
endmodule
`timescale 1ns/1ps
`default_nettype wire
module cell_ (a, y);
  input a;
  output y;
  bufif1 vamos_ams_hiz_0 (y, 1'b0, 1'b0);
endmodule
`timescale 1ns/1ps
`default_nettype wire
module _cell (a, y);
  input a;
  output y;
  bufif1 vamos_ams_hiz_0 (y, 1'b0, 1'b0);
endmodule
