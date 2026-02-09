-- 3D Logic version of and_or_reg
-- AND-OR gate: y = (a AND b) OR (c AND d)
-- Single expression - no intermediate signals, no delta cycles

library work;
use work.logic3d_types_pkg.all;

entity and_or_reg_3d is
    port (
        a : in logic3d;
        b : in logic3d;
        c : in logic3d;
        d : in logic3d;
        y : out logic3d
    );
end entity;

architecture rtl of and_or_reg_3d is
begin
    -- All in one expression: (a AND b) OR (c AND d)
    y <= l3d_or(l3d_and(a, b), l3d_and(c, d));
end architecture;
