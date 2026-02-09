-- 3D Logic version of xor_gate

library work;
use work.logic3d_pkg.all;

entity xor_gate_3d is
    port (
        a : in logic3d;
        b : in logic3d;
        y : out logic3d
    );
end entity;

architecture rtl of xor_gate_3d is
begin
    y <= l3d_xor(a, b);
end architecture;
