-- 3D Logic AND-OR gate testbench
-- Tests (a AND b) OR (c AND d) with X propagation/masking

library work;
use work.logic3d_types_pkg.all;

entity and_or_3d_tb is
end entity;

architecture test of and_or_3d_tb is
    signal a, b, c, d, y : logic3d;
begin

    DUT: entity work.and_or_reg_3d
        port map (a => a, b => b, c => c, d => d, y => y);

    test_proc: process
        variable errors : natural := 0;
    begin
        report "=== AND-OR Gate 3D Logic Tests ===";
        report "y = (a AND b) OR (c AND d)";
        report "";

        -- Basic truth table tests
        report "Basic tests:";

        -- 0000 -> 0
        a <= L3D_0; b <= L3D_0; c <= L3D_0; d <= L3D_0;
        wait for 1 ns;
        report "  0000 -> " & to_char(y);
        if y /= L3D_0 then errors := errors + 1; end if;

        -- 1100 -> 1 (a AND b = 1)
        a <= L3D_1; b <= L3D_1; c <= L3D_0; d <= L3D_0;
        wait for 1 ns;
        report "  1100 -> " & to_char(y);
        if y /= L3D_1 then errors := errors + 1; end if;

        -- 0011 -> 1 (c AND d = 1)
        a <= L3D_0; b <= L3D_0; c <= L3D_1; d <= L3D_1;
        wait for 1 ns;
        report "  0011 -> " & to_char(y);
        if y /= L3D_1 then errors := errors + 1; end if;

        -- 1111 -> 1
        a <= L3D_1; b <= L3D_1; c <= L3D_1; d <= L3D_1;
        wait for 1 ns;
        report "  1111 -> " & to_char(y);
        if y /= L3D_1 then errors := errors + 1; end if;

        report "";
        report "X propagation tests:";

        -- X on 'a' when b=1: X AND 1 = X, but if c AND d = 1, result is 1
        a <= L3D_X; b <= L3D_1; c <= L3D_1; d <= L3D_1;
        wait for 1 ns;
        report "  X111 -> " & to_char(y) & " (1 dominates OR)";
        if y /= L3D_1 then errors := errors + 1; end if;

        -- X on 'a' when b=1, c AND d = 0: X OR 0 = X
        a <= L3D_X; b <= L3D_1; c <= L3D_0; d <= L3D_0;
        wait for 1 ns;
        report "  X100 -> " & to_char(y) & " (X propagates)";
        if y /= L3D_X then errors := errors + 1; end if;

        report "";
        report "X masking tests:";

        -- X on 'a' when b=0: 0 AND X = 0
        a <= L3D_X; b <= L3D_0; c <= L3D_0; d <= L3D_0;
        wait for 1 ns;
        report "  X000 -> " & to_char(y) & " (0 masks AND)";
        if y /= L3D_0 then errors := errors + 1; end if;

        -- X on 'c' when d=0, a AND b = 0
        a <= L3D_0; b <= L3D_0; c <= L3D_X; d <= L3D_0;
        wait for 1 ns;
        report "  00X0 -> " & to_char(y) & " (0 masks AND)";
        if y /= L3D_0 then errors := errors + 1; end if;

        -- X on both branches masked by 0s
        a <= L3D_X; b <= L3D_0; c <= L3D_X; d <= L3D_0;
        wait for 1 ns;
        report "  X0X0 -> " & to_char(y) & " (0s mask both)";
        if y /= L3D_0 then errors := errors + 1; end if;

        report "";
        if errors = 0 then
            report "PASSED: All AND-OR 3D logic tests passed";
        else
            report "FAILED: " & integer'image(errors) & " errors";
        end if;

        wait;
    end process;

end architecture;
