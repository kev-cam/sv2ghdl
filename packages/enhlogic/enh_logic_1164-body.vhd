-- Enhanced Logic Package Body (based on IEEE std_logic_1164)
-- Implementation of enh_logic package functions

package body enh_logic_1164 is

    -------------------------------------------------------------------
    -- local types
    -------------------------------------------------------------------
    type enh_table is array(enh_ulogic, enh_ulogic) of enh_ulogic;

    -------------------------------------------------------------------
    -- resolution function table
    -------------------------------------------------------------------
    constant resolution_table : enh_table := (
    --      ---------------------------------------------------------
    --      |  U    X    0    1    Z    W    L    H    -        |   |
    --      ---------------------------------------------------------
            ( 'U', 'U', 'U', 'U', 'U', 'U', 'U', 'U', 'U' ),  -- | U |
            ( 'U', 'X', 'X', 'X', 'X', 'X', 'X', 'X', 'X' ),  -- | X |
            ( 'U', 'X', '0', 'X', '0', '0', '0', '0', 'X' ),  -- | 0 |
            ( 'U', 'X', 'X', '1', '1', '1', '1', '1', 'X' ),  -- | 1 |
            ( 'U', 'X', '0', '1', 'Z', 'W', 'L', 'H', 'X' ),  -- | Z |
            ( 'U', 'X', '0', '1', 'W', 'W', 'W', 'W', 'X' ),  -- | W |
            ( 'U', 'X', '0', '1', 'L', 'W', 'L', 'W', 'X' ),  -- | L |
            ( 'U', 'X', '0', '1', 'H', 'W', 'W', 'H', 'X' ),  -- | H |
            ( 'U', 'X', 'X', 'X', 'X', 'X', 'X', 'X', 'X' )   -- | - |
    );

    -------------------------------------------------------------------
    -- resolution function
    -------------------------------------------------------------------
    function resolved ( s : enh_ulogic_vector ) return enh_ulogic is
        variable result : enh_ulogic := 'Z';
    begin
        if s'length = 0 then
            return 'Z';
        else
            for i in s'range loop
                result := resolution_table(result, s(i));
            end loop;
        end if;
        return result;
    end function resolved;

    -------------------------------------------------------------------
    -- tables for logical operations
    -------------------------------------------------------------------
    constant and_table : enh_table := (
    --      ---------------------------------------------------------
    --      |  U    X    0    1    Z    W    L    H    -        |   |
    --      ---------------------------------------------------------
            ( 'U', 'U', '0', 'U', 'U', 'U', '0', 'U', 'U' ),  -- | U |
            ( 'U', 'X', '0', 'X', 'X', 'X', '0', 'X', 'X' ),  -- | X |
            ( '0', '0', '0', '0', '0', '0', '0', '0', '0' ),  -- | 0 |
            ( 'U', 'X', '0', '1', 'X', 'X', '0', '1', 'X' ),  -- | 1 |
            ( 'U', 'X', '0', 'X', 'X', 'X', '0', 'X', 'X' ),  -- | Z |
            ( 'U', 'X', '0', 'X', 'X', 'X', '0', 'X', 'X' ),  -- | W |
            ( '0', '0', '0', '0', '0', '0', '0', '0', '0' ),  -- | L |
            ( 'U', 'X', '0', '1', 'X', 'X', '0', '1', 'X' ),  -- | H |
            ( 'U', 'X', '0', 'X', 'X', 'X', '0', 'X', 'X' )   -- | - |
    );

    constant or_table : enh_table := (
    --      ---------------------------------------------------------
    --      |  U    X    0    1    Z    W    L    H    -        |   |
    --      ---------------------------------------------------------
            ( 'U', 'U', 'U', '1', 'U', 'U', 'U', '1', 'U' ),  -- | U |
            ( 'U', 'X', 'X', '1', 'X', 'X', 'X', '1', 'X' ),  -- | X |
            ( 'U', 'X', '0', '1', 'X', 'X', '0', '1', 'X' ),  -- | 0 |
            ( '1', '1', '1', '1', '1', '1', '1', '1', '1' ),  -- | 1 |
            ( 'U', 'X', 'X', '1', 'X', 'X', 'X', '1', 'X' ),  -- | Z |
            ( 'U', 'X', 'X', '1', 'X', 'X', 'X', '1', 'X' ),  -- | W |
            ( 'U', 'X', '0', '1', 'X', 'X', '0', '1', 'X' ),  -- | L |
            ( '1', '1', '1', '1', '1', '1', '1', '1', '1' ),  -- | H |
            ( 'U', 'X', 'X', '1', 'X', 'X', 'X', '1', 'X' )   -- | - |
    );

    constant xor_table : enh_table := (
    --      ---------------------------------------------------------
    --      |  U    X    0    1    Z    W    L    H    -        |   |
    --      ---------------------------------------------------------
            ( 'U', 'U', 'U', 'U', 'U', 'U', 'U', 'U', 'U' ),  -- | U |
            ( 'U', 'X', 'X', 'X', 'X', 'X', 'X', 'X', 'X' ),  -- | X |
            ( 'U', 'X', '0', '1', 'X', 'X', '0', '1', 'X' ),  -- | 0 |
            ( 'U', 'X', '1', '0', 'X', 'X', '1', '0', 'X' ),  -- | 1 |
            ( 'U', 'X', 'X', 'X', 'X', 'X', 'X', 'X', 'X' ),  -- | Z |
            ( 'U', 'X', 'X', 'X', 'X', 'X', 'X', 'X', 'X' ),  -- | W |
            ( 'U', 'X', '0', '1', 'X', 'X', '0', '1', 'X' ),  -- | L |
            ( 'U', 'X', '1', '0', 'X', 'X', '1', '0', 'X' ),  -- | H |
            ( 'U', 'X', 'X', 'X', 'X', 'X', 'X', 'X', 'X' )   -- | - |
    );

    constant not_table : array(enh_ulogic) of enh_ulogic := (
            'U', 'X', '1', '0', 'X', 'X', '1', '0', 'X'
    );

    -------------------------------------------------------------------
    -- logical operators
    -------------------------------------------------------------------
    function "and" ( l : enh_ulogic; r : enh_ulogic ) return enh_ulogic is
    begin
        return and_table(l, r);
    end function "and";

    function "nand" ( l : enh_ulogic; r : enh_ulogic ) return enh_ulogic is
    begin
        return not_table(and_table(l, r));
    end function "nand";

    function "or" ( l : enh_ulogic; r : enh_ulogic ) return enh_ulogic is
    begin
        return or_table(l, r);
    end function "or";

    function "nor" ( l : enh_ulogic; r : enh_ulogic ) return enh_ulogic is
    begin
        return not_table(or_table(l, r));
    end function "nor";

    function "xor" ( l : enh_ulogic; r : enh_ulogic ) return enh_ulogic is
    begin
        return xor_table(l, r);
    end function "xor";

    function "xnor" ( l : enh_ulogic; r : enh_ulogic ) return enh_ulogic is
    begin
        return not_table(xor_table(l, r));
    end function "xnor";

    function "not" ( l : enh_ulogic ) return enh_ulogic is
    begin
        return not_table(l);
    end function "not";

    -------------------------------------------------------------------
    -- vectorized logical operators
    -------------------------------------------------------------------
    function "and" ( l, r : enh_logic_vector ) return enh_logic_vector is
        alias lv : enh_logic_vector ( 1 to l'length ) is l;
        alias rv : enh_logic_vector ( 1 to r'length ) is r;
        variable result : enh_logic_vector ( 1 to l'length );
    begin
        if l'length /= r'length then
            assert false
            report "arguments of overloaded 'and' operator are not of the same length"
            severity failure;
        else
            for i in result'range loop
                result(i) := and_table(lv(i), rv(i));
            end loop;
        end if;
        return result;
    end function "and";

    function "nand" ( l, r : enh_logic_vector ) return enh_logic_vector is
        alias lv : enh_logic_vector ( 1 to l'length ) is l;
        alias rv : enh_logic_vector ( 1 to r'length ) is r;
        variable result : enh_logic_vector ( 1 to l'length );
    begin
        if l'length /= r'length then
            assert false
            report "arguments of overloaded 'nand' operator are not of the same length"
            severity failure;
        else
            for i in result'range loop
                result(i) := not_table(and_table(lv(i), rv(i)));
            end loop;
        end if;
        return result;
    end function "nand";

    function "or" ( l, r : enh_logic_vector ) return enh_logic_vector is
        alias lv : enh_logic_vector ( 1 to l'length ) is l;
        alias rv : enh_logic_vector ( 1 to r'length ) is r;
        variable result : enh_logic_vector ( 1 to l'length );
    begin
        if l'length /= r'length then
            assert false
            report "arguments of overloaded 'or' operator are not of the same length"
            severity failure;
        else
            for i in result'range loop
                result(i) := or_table(lv(i), rv(i));
            end loop;
        end if;
        return result;
    end function "or";

    function "nor" ( l, r : enh_logic_vector ) return enh_logic_vector is
        alias lv : enh_logic_vector ( 1 to l'length ) is l;
        alias rv : enh_logic_vector ( 1 to r'length ) is r;
        variable result : enh_logic_vector ( 1 to l'length );
    begin
        if l'length /= r'length then
            assert false
            report "arguments of overloaded 'nor' operator are not of the same length"
            severity failure;
        else
            for i in result'range loop
                result(i) := not_table(or_table(lv(i), rv(i)));
            end loop;
        end if;
        return result;
    end function "nor";

    function "xor" ( l, r : enh_logic_vector ) return enh_logic_vector is
        alias lv : enh_logic_vector ( 1 to l'length ) is l;
        alias rv : enh_logic_vector ( 1 to r'length ) is r;
        variable result : enh_logic_vector ( 1 to l'length );
    begin
        if l'length /= r'length then
            assert false
            report "arguments of overloaded 'xor' operator are not of the same length"
            severity failure;
        else
            for i in result'range loop
                result(i) := xor_table(lv(i), rv(i));
            end loop;
        end if;
        return result;
    end function "xor";

    function "xnor" ( l, r : enh_logic_vector ) return enh_logic_vector is
        alias lv : enh_logic_vector ( 1 to l'length ) is l;
        alias rv : enh_logic_vector ( 1 to r'length ) is r;
        variable result : enh_logic_vector ( 1 to l'length );
    begin
        if l'length /= r'length then
            assert false
            report "arguments of overloaded 'xnor' operator are not of the same length"
            severity failure;
        else
            for i in result'range loop
                result(i) := not_table(xor_table(lv(i), rv(i)));
            end loop;
        end if;
        return result;
    end function "xnor";

    function "not" ( l : enh_logic_vector ) return enh_logic_vector is
        alias lv : enh_logic_vector ( 1 to l'length ) is l;
        variable result : enh_logic_vector ( 1 to l'length );
    begin
        for i in result'range loop
            result(i) := not_table(lv(i));
        end loop;
        return result;
    end function "not";

    -------------------------------------------------------------------
    -- mixed vector/scalar logical operators
    -------------------------------------------------------------------
    function "and" ( l : enh_logic_vector; r : enh_logic ) return enh_logic_vector is
        alias lv : enh_logic_vector ( 1 to l'length ) is l;
        variable result : enh_logic_vector ( 1 to l'length );
    begin
        for i in result'range loop
            result(i) := and_table(lv(i), r);
        end loop;
        return result;
    end function "and";

    function "and" ( l : enh_logic; r : enh_logic_vector ) return enh_logic_vector is
        alias rv : enh_logic_vector ( 1 to r'length ) is r;
        variable result : enh_logic_vector ( 1 to r'length );
    begin
        for i in result'range loop
            result(i) := and_table(l, rv(i));
        end loop;
        return result;
    end function "and";

    function "nand" ( l : enh_logic_vector; r : enh_logic ) return enh_logic_vector is
        alias lv : enh_logic_vector ( 1 to l'length ) is l;
        variable result : enh_logic_vector ( 1 to l'length );
    begin
        for i in result'range loop
            result(i) := not_table(and_table(lv(i), r));
        end loop;
        return result;
    end function "nand";

    function "nand" ( l : enh_logic; r : enh_logic_vector ) return enh_logic_vector is
        alias rv : enh_logic_vector ( 1 to r'length ) is r;
        variable result : enh_logic_vector ( 1 to r'length );
    begin
        for i in result'range loop
            result(i) := not_table(and_table(l, rv(i)));
        end loop;
        return result;
    end function "nand";

    function "or" ( l : enh_logic_vector; r : enh_logic ) return enh_logic_vector is
        alias lv : enh_logic_vector ( 1 to l'length ) is l;
        variable result : enh_logic_vector ( 1 to l'length );
    begin
        for i in result'range loop
            result(i) := or_table(lv(i), r);
        end loop;
        return result;
    end function "or";

    function "or" ( l : enh_logic; r : enh_logic_vector ) return enh_logic_vector is
        alias rv : enh_logic_vector ( 1 to r'length ) is r;
        variable result : enh_logic_vector ( 1 to r'length );
    begin
        for i in result'range loop
            result(i) := or_table(l, rv(i));
        end loop;
        return result;
    end function "or";

    function "nor" ( l : enh_logic_vector; r : enh_logic ) return enh_logic_vector is
        alias lv : enh_logic_vector ( 1 to l'length ) is l;
        variable result : enh_logic_vector ( 1 to l'length );
    begin
        for i in result'range loop
            result(i) := not_table(or_table(lv(i), r));
        end loop;
        return result;
    end function "nor";

    function "nor" ( l : enh_logic; r : enh_logic_vector ) return enh_logic_vector is
        alias rv : enh_logic_vector ( 1 to r'length ) is r;
        variable result : enh_logic_vector ( 1 to r'length );
    begin
        for i in result'range loop
            result(i) := not_table(or_table(l, rv(i)));
        end loop;
        return result;
    end function "nor";

    function "xor" ( l : enh_logic_vector; r : enh_logic ) return enh_logic_vector is
        alias lv : enh_logic_vector ( 1 to l'length ) is l;
        variable result : enh_logic_vector ( 1 to l'length );
    begin
        for i in result'range loop
            result(i) := xor_table(lv(i), r);
        end loop;
        return result;
    end function "xor";

    function "xor" ( l : enh_logic; r : enh_logic_vector ) return enh_logic_vector is
        alias rv : enh_logic_vector ( 1 to r'length ) is r;
        variable result : enh_logic_vector ( 1 to r'length );
    begin
        for i in result'range loop
            result(i) := xor_table(l, rv(i));
        end loop;
        return result;
    end function "xor";

    function "xnor" ( l : enh_logic_vector; r : enh_logic ) return enh_logic_vector is
        alias lv : enh_logic_vector ( 1 to l'length ) is l;
        variable result : enh_logic_vector ( 1 to l'length );
    begin
        for i in result'range loop
            result(i) := not_table(xor_table(lv(i), r));
        end loop;
        return result;
    end function "xnor";

    function "xnor" ( l : enh_logic; r : enh_logic_vector ) return enh_logic_vector is
        alias rv : enh_logic_vector ( 1 to r'length ) is r;
        variable result : enh_logic_vector ( 1 to r'length );
    begin
        for i in result'range loop
            result(i) := not_table(xor_table(l, rv(i)));
        end loop;
        return result;
    end function "xnor";

    -------------------------------------------------------------------
    -- conversion functions
    -------------------------------------------------------------------
    constant to_bit_table : array(enh_ulogic) of bit := (
            '0', '0', '0', '1', '0', '0', '0', '1', '0'
    );

    function To_bit ( s : enh_ulogic; xmap : bit := '0' ) return bit is
    begin
        if s = '1' or s = 'H' then
            return '1';
        elsif s = '0' or s = 'L' then
            return '0';
        else
            return xmap;
        end if;
    end function To_bit;

    function To_bitvector ( s : enh_logic_vector; xmap : bit := '0' ) return bit_vector is
        alias sv : enh_logic_vector ( 1 to s'length ) is s;
        variable result : bit_vector ( 1 to s'length );
    begin
        for i in result'range loop
            result(i) := To_bit(sv(i), xmap);
        end loop;
        return result;
    end function To_bitvector;

    function To_EnhULogic ( b : bit ) return enh_ulogic is
    begin
        case b is
            when '0' => return '0';
            when '1' => return '1';
        end case;
    end function To_EnhULogic;

    function To_EnhLogicVector ( b : bit_vector ) return enh_logic_vector is
        alias bv : bit_vector ( 1 to b'length ) is b;
        variable result : enh_logic_vector ( 1 to b'length );
    begin
        for i in result'range loop
            result(i) := To_EnhULogic(bv(i));
        end loop;
        return result;
    end function To_EnhLogicVector;

    function To_EnhLogicVector ( s : enh_ulogic_vector ) return enh_logic_vector is
        alias sv : enh_ulogic_vector ( 1 to s'length ) is s;
        variable result : enh_logic_vector ( 1 to s'length );
    begin
        for i in result'range loop
            result(i) := s(i);
        end loop;
        return result;
    end function To_EnhLogicVector;

    function To_EnhULogicVector ( s : enh_logic_vector ) return enh_ulogic_vector is
        alias sv : enh_logic_vector ( 1 to s'length ) is s;
        variable result : enh_ulogic_vector ( 1 to s'length );
    begin
        for i in result'range loop
            result(i) := s(i);
        end loop;
        return result;
    end function To_EnhULogicVector;

    -------------------------------------------------------------------
    -- strength strippers
    -------------------------------------------------------------------
    constant to_x01_table : array(enh_ulogic) of ENH_X01 := (
            'X', 'X', '0', '1', 'X', 'X', '0', '1', 'X'
    );

    function To_X01 ( s : enh_logic_vector ) return enh_logic_vector is
        alias sv : enh_logic_vector ( 1 to s'length ) is s;
        variable result : enh_logic_vector ( 1 to s'length );
    begin
        for i in result'range loop
            result(i) := to_x01_table(sv(i));
        end loop;
        return result;
    end function To_X01;

    function To_X01 ( s : enh_ulogic_vector ) return enh_ulogic_vector is
        alias sv : enh_ulogic_vector ( 1 to s'length ) is s;
        variable result : enh_ulogic_vector ( 1 to s'length );
    begin
        for i in result'range loop
            result(i) := to_x01_table(sv(i));
        end loop;
        return result;
    end function To_X01;

    function To_X01 ( s : enh_ulogic ) return ENH_X01 is
    begin
        return to_x01_table(s);
    end function To_X01;

    function To_X01 ( b : bit_vector ) return enh_logic_vector is
    begin
        return To_X01(To_EnhLogicVector(b));
    end function To_X01;

    function To_X01 ( b : bit ) return ENH_X01 is
    begin
        return To_X01(To_EnhULogic(b));
    end function To_X01;

    constant to_x01z_table : array(enh_ulogic) of ENH_X01Z := (
            'X', 'X', '0', '1', 'Z', 'X', '0', '1', 'X'
    );

    function To_X01Z ( s : enh_logic_vector ) return enh_logic_vector is
        alias sv : enh_logic_vector ( 1 to s'length ) is s;
        variable result : enh_logic_vector ( 1 to s'length );
    begin
        for i in result'range loop
            result(i) := to_x01z_table(sv(i));
        end loop;
        return result;
    end function To_X01Z;

    function To_X01Z ( s : enh_ulogic_vector ) return enh_ulogic_vector is
        alias sv : enh_ulogic_vector ( 1 to s'length ) is s;
        variable result : enh_ulogic_vector ( 1 to s'length );
    begin
        for i in result'range loop
            result(i) := to_x01z_table(sv(i));
        end loop;
        return result;
    end function To_X01Z;

    function To_X01Z ( s : enh_ulogic ) return ENH_X01Z is
    begin
        return to_x01z_table(s);
    end function To_X01Z;

    function To_X01Z ( b : bit_vector ) return enh_logic_vector is
    begin
        return To_X01Z(To_EnhLogicVector(b));
    end function To_X01Z;

    function To_X01Z ( b : bit ) return ENH_X01Z is
    begin
        return To_X01Z(To_EnhULogic(b));
    end function To_X01Z;

    constant to_ux01_table : array(enh_ulogic) of ENH_UX01 := (
            'U', 'X', '0', '1', 'X', 'X', '0', '1', 'X'
    );

    function To_UX01 ( s : enh_logic_vector ) return enh_logic_vector is
        alias sv : enh_logic_vector ( 1 to s'length ) is s;
        variable result : enh_logic_vector ( 1 to s'length );
    begin
        for i in result'range loop
            result(i) := to_ux01_table(sv(i));
        end loop;
        return result;
    end function To_UX01;

    function To_UX01 ( s : enh_ulogic_vector ) return enh_ulogic_vector is
        alias sv : enh_ulogic_vector ( 1 to s'length ) is s;
        variable result : enh_ulogic_vector ( 1 to s'length );
    begin
        for i in result'range loop
            result(i) := to_ux01_table(sv(i));
        end loop;
        return result;
    end function To_UX01;

    function To_UX01 ( s : enh_ulogic ) return ENH_UX01 is
    begin
        return to_ux01_table(s);
    end function To_UX01;

    function To_UX01 ( b : bit_vector ) return enh_logic_vector is
    begin
        return To_UX01(To_EnhLogicVector(b));
    end function To_UX01;

    function To_UX01 ( b : bit ) return ENH_UX01 is
    begin
        return To_UX01(To_EnhULogic(b));
    end function To_UX01;

    -------------------------------------------------------------------
    -- edge detection
    -------------------------------------------------------------------
    function rising_edge ( signal s : enh_ulogic ) return boolean is
    begin
        return (s'event and (To_X01(s) = '1') and (To_X01(s'last_value) = '0'));
    end function rising_edge;

    function falling_edge ( signal s : enh_ulogic ) return boolean is
    begin
        return (s'event and (To_X01(s) = '0') and (To_X01(s'last_value) = '1'));
    end function falling_edge;

    -------------------------------------------------------------------
    -- test functions
    -------------------------------------------------------------------
    function Is_X ( s : enh_ulogic_vector ) return boolean is
    begin
        for i in s'range loop
            case s(i) is
                when 'U' | 'X' | 'Z' | 'W' | '-' => return true;
                when others => null;
            end case;
        end loop;
        return false;
    end function Is_X;

    function Is_X ( s : enh_logic_vector ) return boolean is
    begin
        for i in s'range loop
            case s(i) is
                when 'U' | 'X' | 'Z' | 'W' | '-' => return true;
                when others => null;
            end case;
        end loop;
        return false;
    end function Is_X;

    function Is_X ( s : enh_ulogic ) return boolean is
    begin
        case s is
            when 'U' | 'X' | 'Z' | 'W' | '-' => return true;
            when others => return false;
        end case;
    end function Is_X;

    -------------------------------------------------------------------
    -- string conversion
    -------------------------------------------------------------------
    type char_indexed_by_enh is array (enh_ulogic) of character;
    constant enh_to_char : char_indexed_by_enh := "UX01ZWLH-";

    function to_string ( value : enh_logic_vector ) return string is
        alias v : enh_logic_vector(1 to value'length) is value;
        variable result : string(1 to value'length);
    begin
        for i in result'range loop
            result(i) := enh_to_char(v(i));
        end loop;
        return result;
    end function to_string;

    function to_string ( value : enh_ulogic ) return string is
        variable result : string(1 to 1);
    begin
        result(1) := enh_to_char(value);
        return result;
    end function to_string;

end package body enh_logic_1164;
