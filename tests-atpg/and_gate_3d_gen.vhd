-- Auto-generated from and_gate.v
-- Mode: logic3d
-- sv2ghdl version 0.1.0

library work;
use work.logic3d_pkg.all;

-- Simple 2-input AND gate
entity and_gate is
  port (
    a : in logic3d;
    b : in logic3d;
    y : inout logic3d
  );

end entity;

architecture rtl of and_gate is
begin
  y <= l3d_and(a, b);

end architecture;
