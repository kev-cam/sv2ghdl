-- 3D Logic version of and_gate
-- Converted from Verilog gate-level netlist

library work;
use work.logic3d_types_pkg.all;

entity and_gate_3d is
    port (
        a : in logic3d;
        b : in logic3d;
        y : out logic3d
    );
end entity;

architecture rtl of and_gate_3d is
begin
    y <= l3d_and(a, b);
end architecture;
