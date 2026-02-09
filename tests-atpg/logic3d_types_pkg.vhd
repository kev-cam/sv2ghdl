-- 3D Logic Type Package
-- Provides the enum representation for single-bit signals
-- Vector versions would use ieee.numeric_std.unsigned for bitwise ops

package logic3d_types_pkg is

    ---------------------------------------------------------------------------
    -- Enum version for single-bit signals (3-bit encoding)
    ---------------------------------------------------------------------------
    subtype logic3d is natural range 0 to 7;

    -- Encoding: bit2=uncertain, bit1=strength, bit0=value
    -- 000 = invalid (treat as Z)
    -- 001 = invalid (treat as Z)
    -- 010 = strong 0 (L3D_0)
    -- 011 = strong 1 (L3D_1)
    -- 100 = high-Z (L3D_Z)
    -- 101 = invalid (treat as Z)
    -- 110 = unknown/conflict (L3D_X)
    -- 111 = invalid (treat as X)

    constant L3D_0 : logic3d := 2;  -- 010: strong 0
    constant L3D_1 : logic3d := 3;  -- 011: strong 1
    constant L3D_Z : logic3d := 4;  -- 100: high-Z
    constant L3D_X : logic3d := 6;  -- 110: unknown

    ---------------------------------------------------------------------------
    -- Lookup tables (8x8 for 2-input, 8 for 1-input)
    ---------------------------------------------------------------------------
    type lut1_t is array (0 to 7) of logic3d;
    type lut2_t is array (0 to 7, 0 to 7) of logic3d;

    constant NOT_LUT : lut1_t := (4, 4, 3, 2, 4, 4, 6, 6);

    constant AND_LUT : lut2_t := (
        0 => (4, 4, 2, 2, 4, 4, 6, 6),
        1 => (4, 4, 2, 2, 4, 4, 6, 6),
        2 => (2, 2, 2, 2, 2, 2, 2, 2),  -- 0 & x = 0
        3 => (2, 2, 2, 3, 4, 4, 6, 6),
        4 => (4, 4, 2, 4, 4, 4, 6, 6),
        5 => (4, 4, 2, 4, 4, 4, 6, 6),
        6 => (6, 6, 2, 6, 6, 6, 6, 6),
        7 => (6, 6, 2, 6, 6, 6, 6, 6)
    );

    constant OR_LUT : lut2_t := (
        0 => (4, 4, 4, 3, 4, 4, 6, 6),
        1 => (4, 4, 4, 3, 4, 4, 6, 6),
        2 => (4, 4, 2, 3, 4, 4, 6, 6),
        3 => (3, 3, 3, 3, 3, 3, 3, 3),  -- 1 | x = 1
        4 => (4, 4, 4, 3, 4, 4, 6, 6),
        5 => (4, 4, 4, 3, 4, 4, 6, 6),
        6 => (6, 6, 6, 3, 6, 6, 6, 6),
        7 => (6, 6, 6, 3, 6, 6, 6, 6)
    );

    constant XOR_LUT : lut2_t := (
        0 => (4, 4, 4, 4, 4, 4, 6, 6),
        1 => (4, 4, 4, 4, 4, 4, 6, 6),
        2 => (4, 4, 2, 3, 4, 4, 6, 6),
        3 => (4, 4, 3, 2, 4, 4, 6, 6),
        4 => (4, 4, 4, 4, 4, 4, 6, 6),
        5 => (4, 4, 4, 4, 4, 4, 6, 6),
        6 => (6, 6, 6, 6, 6, 6, 6, 6),
        7 => (6, 6, 6, 6, 6, 6, 6, 6)
    );

    constant NAND_LUT : lut2_t := (
        0 => (4, 4, 3, 3, 4, 4, 6, 6),
        1 => (4, 4, 3, 3, 4, 4, 6, 6),
        2 => (3, 3, 3, 3, 3, 3, 3, 3),
        3 => (3, 3, 3, 2, 4, 4, 6, 6),
        4 => (4, 4, 3, 4, 4, 4, 6, 6),
        5 => (4, 4, 3, 4, 4, 4, 6, 6),
        6 => (6, 6, 3, 6, 6, 6, 6, 6),
        7 => (6, 6, 3, 6, 6, 6, 6, 6)
    );

    constant NOR_LUT : lut2_t := (
        0 => (4, 4, 4, 2, 4, 4, 6, 6),
        1 => (4, 4, 4, 2, 4, 4, 6, 6),
        2 => (4, 4, 3, 2, 4, 4, 6, 6),
        3 => (2, 2, 2, 2, 2, 2, 2, 2),
        4 => (4, 4, 4, 2, 4, 4, 6, 6),
        5 => (4, 4, 4, 2, 4, 4, 6, 6),
        6 => (6, 6, 6, 2, 6, 6, 6, 6),
        7 => (6, 6, 6, 2, 6, 6, 6, 6)
    );

    constant XNOR_LUT : lut2_t := (
        0 => (4, 4, 4, 4, 4, 4, 6, 6),
        1 => (4, 4, 4, 4, 4, 4, 6, 6),
        2 => (4, 4, 3, 2, 4, 4, 6, 6),  -- 0 xnor 0=1, 0 xnor 1=0
        3 => (4, 4, 2, 3, 4, 4, 6, 6),  -- 1 xnor 0=0, 1 xnor 1=1
        4 => (4, 4, 4, 4, 4, 4, 6, 6),
        5 => (4, 4, 4, 4, 4, 4, 6, 6),
        6 => (6, 6, 6, 6, 6, 6, 6, 6),
        7 => (6, 6, 6, 6, 6, 6, 6, 6)
    );

    ---------------------------------------------------------------------------
    -- Gate functions
    ---------------------------------------------------------------------------
    function l3d_not(a : logic3d) return logic3d;
    function l3d_and(a, b : logic3d) return logic3d;
    function l3d_or(a, b : logic3d) return logic3d;
    function l3d_xor(a, b : logic3d) return logic3d;
    function l3d_nand(a, b : logic3d) return logic3d;
    function l3d_nor(a, b : logic3d) return logic3d;
    function l3d_xnor(a, b : logic3d) return logic3d;
    function l3d_buf(a : logic3d) return logic3d;

    -- Multi-input (chained lookups, no delta cycles)
    function l3d_and3(a, b, c : logic3d) return logic3d;
    function l3d_and4(a, b, c, d : logic3d) return logic3d;
    function l3d_or3(a, b, c : logic3d) return logic3d;
    function l3d_or4(a, b, c, d : logic3d) return logic3d;
    function l3d_xor3(a, b, c : logic3d) return logic3d;
    function l3d_nand3(a, b, c : logic3d) return logic3d;
    function l3d_nor3(a, b, c : logic3d) return logic3d;

    ---------------------------------------------------------------------------
    -- Utilities
    ---------------------------------------------------------------------------
    function to_char(a : logic3d) return character;
    function is_one(a : logic3d) return boolean;
    function is_zero(a : logic3d) return boolean;
    function is_x(a : logic3d) return boolean;
    function is_z(a : logic3d) return boolean;
    function is_strong(a : logic3d) return boolean;

end package;

package body logic3d_types_pkg is

    ---------------------------------------------------------------------------
    -- Gate implementations (single table lookup)
    ---------------------------------------------------------------------------
    function l3d_not(a : logic3d) return logic3d is
    begin
        return NOT_LUT(a);
    end function;

    function l3d_and(a, b : logic3d) return logic3d is
    begin
        return AND_LUT(a, b);
    end function;

    function l3d_or(a, b : logic3d) return logic3d is
    begin
        return OR_LUT(a, b);
    end function;

    function l3d_xor(a, b : logic3d) return logic3d is
    begin
        return XOR_LUT(a, b);
    end function;

    function l3d_nand(a, b : logic3d) return logic3d is
    begin
        return NAND_LUT(a, b);
    end function;

    function l3d_nor(a, b : logic3d) return logic3d is
    begin
        return NOR_LUT(a, b);
    end function;

    function l3d_xnor(a, b : logic3d) return logic3d is
    begin
        return XNOR_LUT(a, b);
    end function;

    function l3d_buf(a : logic3d) return logic3d is
    begin
        return a;
    end function;

    ---------------------------------------------------------------------------
    -- Multi-input gates (chained, single expression, no delta cycles)
    ---------------------------------------------------------------------------
    function l3d_and3(a, b, c : logic3d) return logic3d is
    begin
        return AND_LUT(AND_LUT(a, b), c);
    end function;

    function l3d_and4(a, b, c, d : logic3d) return logic3d is
    begin
        return AND_LUT(AND_LUT(AND_LUT(a, b), c), d);
    end function;

    function l3d_or3(a, b, c : logic3d) return logic3d is
    begin
        return OR_LUT(OR_LUT(a, b), c);
    end function;

    function l3d_or4(a, b, c, d : logic3d) return logic3d is
    begin
        return OR_LUT(OR_LUT(OR_LUT(a, b), c), d);
    end function;

    function l3d_xor3(a, b, c : logic3d) return logic3d is
    begin
        return XOR_LUT(XOR_LUT(a, b), c);
    end function;

    function l3d_nand3(a, b, c : logic3d) return logic3d is
    begin
        return NOT_LUT(AND_LUT(AND_LUT(a, b), c));
    end function;

    function l3d_nor3(a, b, c : logic3d) return logic3d is
    begin
        return NOT_LUT(OR_LUT(OR_LUT(a, b), c));
    end function;

    ---------------------------------------------------------------------------
    -- Utilities
    ---------------------------------------------------------------------------
    function to_char(a : logic3d) return character is
    begin
        case a is
            when L3D_0 => return '0';
            when L3D_1 => return '1';
            when L3D_Z => return 'Z';
            when L3D_X => return 'X';
            when others => return '?';
        end case;
    end function;

    function is_one(a : logic3d) return boolean is
    begin
        return a = L3D_1;
    end function;

    function is_zero(a : logic3d) return boolean is
    begin
        return a = L3D_0;
    end function;

    function is_x(a : logic3d) return boolean is
    begin
        return a = L3D_X;
    end function;

    function is_z(a : logic3d) return boolean is
    begin
        return a = L3D_Z;
    end function;

    function is_strong(a : logic3d) return boolean is
    begin
        return a = L3D_0 or a = L3D_1;
    end function;

end package body;
