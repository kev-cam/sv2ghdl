-- 3D Logic version of dff
-- D flip-flop combinational logic (mux controlled by rst)

library work;
use work.logic3d_pkg.all;

entity dff_3d is
    port (
        rst : in logic3d;
        d   : in logic3d;
        y   : out logic3d
    );
end entity;

architecture rtl of dff_3d is
begin
    -- y = d AND (NOT rst)
    -- All computed in single expression (no intermediate signals needed)
    y <= l3d_and(d, l3d_not(rst));
end architecture;
