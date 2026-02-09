-- 3D Logic ATPG Testbench
-- Tests gates with Atalanta-generated patterns plus X propagation

library work;
use work.logic3d_pkg.all;

entity atpg_3d_tb is
end entity;

architecture test of atpg_3d_tb is

    -- AND gate signals
    signal and_a, and_b, and_y : logic3d;

    -- XOR gate signals
    signal xor_a, xor_b, xor_y : logic3d;

    -- DFF signals
    signal dff_rst, dff_d, dff_y : logic3d;

begin

    -- Instantiate gates
    and_gate: entity work.and_gate_3d
        port map (a => and_a, b => and_b, y => and_y);

    xor_gate: entity work.xor_gate_3d
        port map (a => xor_a, b => xor_b, y => xor_y);

    dff: entity work.dff_3d
        port map (rst => dff_rst, d => dff_d, y => dff_y);

    test_proc: process
        variable errors : natural := 0;
    begin
        report "=== 3D Logic ATPG Tests ===";
        report "";

        -- ============ AND Gate Tests ============
        report "AND Gate Tests:";

        -- ATPG Pattern 1: 01 -> 0
        and_a <= L3D_0; and_b <= L3D_1;
        wait for 1 ns;
        report "  01 -> " & to_char(and_y);
        if and_y /= L3D_0 then errors := errors + 1; end if;

        -- ATPG Pattern 2: 10 -> 0
        and_a <= L3D_1; and_b <= L3D_0;
        wait for 1 ns;
        report "  10 -> " & to_char(and_y);
        if and_y /= L3D_0 then errors := errors + 1; end if;

        -- ATPG Pattern 3: 11 -> 1
        and_a <= L3D_1; and_b <= L3D_1;
        wait for 1 ns;
        report "  11 -> " & to_char(and_y);
        if and_y /= L3D_1 then errors := errors + 1; end if;

        -- X propagation test: X1 -> X
        and_a <= L3D_X; and_b <= L3D_1;
        wait for 1 ns;
        report "  X1 -> " & to_char(and_y) & " (X propagation)";
        if and_y /= L3D_X then errors := errors + 1; end if;

        -- X masking test: 0X -> 0 (0 dominates)
        and_a <= L3D_0; and_b <= L3D_X;
        wait for 1 ns;
        report "  0X -> " & to_char(and_y) & " (0 dominates)";
        if and_y /= L3D_0 then errors := errors + 1; end if;

        report "";

        -- ============ XOR Gate Tests ============
        report "XOR Gate Tests:";

        -- 00 -> 0
        xor_a <= L3D_0; xor_b <= L3D_0;
        wait for 1 ns;
        report "  00 -> " & to_char(xor_y);
        if xor_y /= L3D_0 then errors := errors + 1; end if;

        -- 01 -> 1
        xor_a <= L3D_0; xor_b <= L3D_1;
        wait for 1 ns;
        report "  01 -> " & to_char(xor_y);
        if xor_y /= L3D_1 then errors := errors + 1; end if;

        -- 10 -> 1
        xor_a <= L3D_1; xor_b <= L3D_0;
        wait for 1 ns;
        report "  10 -> " & to_char(xor_y);
        if xor_y /= L3D_1 then errors := errors + 1; end if;

        -- 11 -> 0
        xor_a <= L3D_1; xor_b <= L3D_1;
        wait for 1 ns;
        report "  11 -> " & to_char(xor_y);
        if xor_y /= L3D_0 then errors := errors + 1; end if;

        -- X propagation: X always propagates in XOR
        xor_a <= L3D_X; xor_b <= L3D_0;
        wait for 1 ns;
        report "  X0 -> " & to_char(xor_y) & " (X propagates)";
        if xor_y /= L3D_X then errors := errors + 1; end if;

        report "";

        -- ============ DFF Tests ============
        report "DFF Tests (y = d AND NOT rst):";

        -- ATPG Pattern 1: rst=1, d=1 -> 0
        dff_rst <= L3D_1; dff_d <= L3D_1;
        wait for 1 ns;
        report "  rst=1 d=1 -> " & to_char(dff_y);
        if dff_y /= L3D_0 then errors := errors + 1; end if;

        -- ATPG Pattern 2: rst=0, d=1 -> 1
        dff_rst <= L3D_0; dff_d <= L3D_1;
        wait for 1 ns;
        report "  rst=0 d=1 -> " & to_char(dff_y);
        if dff_y /= L3D_1 then errors := errors + 1; end if;

        -- ATPG Pattern 3: rst=0, d=0 -> 0
        dff_rst <= L3D_0; dff_d <= L3D_0;
        wait for 1 ns;
        report "  rst=0 d=0 -> " & to_char(dff_y);
        if dff_y /= L3D_0 then errors := errors + 1; end if;

        -- X on rst: rst=X, d=1 -> X
        dff_rst <= L3D_X; dff_d <= L3D_1;
        wait for 1 ns;
        report "  rst=X d=1 -> " & to_char(dff_y) & " (X propagates)";
        if dff_y /= L3D_X then errors := errors + 1; end if;

        -- X masked by rst=1: rst=1, d=X -> 0
        dff_rst <= L3D_1; dff_d <= L3D_X;
        wait for 1 ns;
        report "  rst=1 d=X -> " & to_char(dff_y) & " (rst=1 masks)";
        if dff_y /= L3D_0 then errors := errors + 1; end if;

        report "";
        report "=== Summary ===";
        if errors = 0 then
            report "PASSED: All 3D logic ATPG tests passed";
        else
            report "FAILED: " & integer'image(errors) & " errors";
        end if;

        wait;
    end process;

end architecture;
