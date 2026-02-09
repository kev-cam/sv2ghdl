-- 3D Logic version of shift_reg
-- 4-bit shift register combinational logic

library work;
use work.logic3d_pkg.all;

entity shift_reg_3d is
    port (
        si     : in logic3d;
        q_out0 : out logic3d;
        q_out1 : out logic3d;
        q_out2 : out logic3d;
        q_out3 : out logic3d
    );
end entity;

architecture rtl of shift_reg_3d is
begin
    -- Direct buffer chain - all in parallel (no delta cycles between stages)
    q_out0 <= l3d_buf(si);
    q_out1 <= l3d_buf(si);  -- Same as q_out0
    q_out2 <= l3d_buf(si);  -- Same as q_out1
    q_out3 <= l3d_buf(si);  -- Same as q_out2
end architecture;
