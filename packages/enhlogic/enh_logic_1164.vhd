-- Enhanced Logic Package (based on IEEE std_logic_1164)
-- Defines enh_logic type and enh_logic_vector type with resolution functions
--
-- This package is modeled after IEEE.std_logic_1164 but uses the name
-- enh_logic instead of std_logic throughout.

package enh_logic_1164 is

    -------------------------------------------------------------------
    -- logic state type (unresolved)
    -------------------------------------------------------------------
    type enh_ulogic is ( 'U',  -- Uninitialized
                         'X',  -- Forcing  Unknown
                         '0',  -- Forcing  0
                         '1',  -- Forcing  1
                         'Z',  -- High Impedance
                         'W',  -- Weak     Unknown
                         'L',  -- Weak     0
                         'H',  -- Weak     1
                         '-'   -- Don't care
                       );

    -------------------------------------------------------------------
    -- unconstrained array of enh_ulogic for use with the resolution function
    -------------------------------------------------------------------
    type enh_ulogic_vector is array ( natural range <> ) of enh_ulogic;

    -------------------------------------------------------------------
    -- resolution function
    -------------------------------------------------------------------
    function resolved ( s : enh_ulogic_vector ) return enh_ulogic;

    -------------------------------------------------------------------
    -- logic state type (resolved)
    -------------------------------------------------------------------
    subtype enh_logic is resolved enh_ulogic;

    -------------------------------------------------------------------
    -- unconstrained array of enh_logic for use in declaring signal arrays
    -------------------------------------------------------------------
    type enh_logic_vector is array ( natural range <> ) of enh_logic;

    -------------------------------------------------------------------
    -- common subtypes
    -------------------------------------------------------------------
    subtype ENH_X01     is resolved enh_ulogic range 'X' to '1';
    subtype ENH_X01Z    is resolved enh_ulogic range 'X' to 'Z';
    subtype ENH_UX01    is resolved enh_ulogic range 'U' to '1';
    subtype ENH_UX01Z   is resolved enh_ulogic range 'U' to 'Z';

    -------------------------------------------------------------------
    -- logical operators
    -------------------------------------------------------------------
    function "and"  ( l : enh_ulogic; r : enh_ulogic ) return enh_ulogic;
    function "nand" ( l : enh_ulogic; r : enh_ulogic ) return enh_ulogic;
    function "or"   ( l : enh_ulogic; r : enh_ulogic ) return enh_ulogic;
    function "nor"  ( l : enh_ulogic; r : enh_ulogic ) return enh_ulogic;
    function "xor"  ( l : enh_ulogic; r : enh_ulogic ) return enh_ulogic;
    function "xnor" ( l : enh_ulogic; r : enh_ulogic ) return enh_ulogic;
    function "not"  ( l : enh_ulogic ) return enh_ulogic;

    -------------------------------------------------------------------
    -- vectorized overloading for logical operators
    -------------------------------------------------------------------
    function "and"  ( l, r : enh_logic_vector ) return enh_logic_vector;
    function "nand" ( l, r : enh_logic_vector ) return enh_logic_vector;
    function "or"   ( l, r : enh_logic_vector ) return enh_logic_vector;
    function "nor"  ( l, r : enh_logic_vector ) return enh_logic_vector;
    function "xor"  ( l, r : enh_logic_vector ) return enh_logic_vector;
    function "xnor" ( l, r : enh_logic_vector ) return enh_logic_vector;
    function "not"  ( l : enh_logic_vector ) return enh_logic_vector;

    function "and"  ( l : enh_logic_vector; r : enh_logic ) return enh_logic_vector;
    function "and"  ( l : enh_logic; r : enh_logic_vector ) return enh_logic_vector;

    function "nand" ( l : enh_logic_vector; r : enh_logic ) return enh_logic_vector;
    function "nand" ( l : enh_logic; r : enh_logic_vector ) return enh_logic_vector;

    function "or"   ( l : enh_logic_vector; r : enh_logic ) return enh_logic_vector;
    function "or"   ( l : enh_logic; r : enh_logic_vector ) return enh_logic_vector;

    function "nor"  ( l : enh_logic_vector; r : enh_logic ) return enh_logic_vector;
    function "nor"  ( l : enh_logic; r : enh_logic_vector ) return enh_logic_vector;

    function "xor"  ( l : enh_logic_vector; r : enh_logic ) return enh_logic_vector;
    function "xor"  ( l : enh_logic; r : enh_logic_vector ) return enh_logic_vector;

    function "xnor" ( l : enh_logic_vector; r : enh_logic ) return enh_logic_vector;
    function "xnor" ( l : enh_logic; r : enh_logic_vector ) return enh_logic_vector;

    -------------------------------------------------------------------
    -- conversion functions
    -------------------------------------------------------------------
    function To_bit       ( s : enh_ulogic;        xmap : bit := '0') return bit;
    function To_bitvector ( s : enh_logic_vector;  xmap : bit := '0') return bit_vector;

    function To_EnhULogic ( b : bit               ) return enh_ulogic;
    function To_EnhLogicVector ( b : bit_vector   ) return enh_logic_vector;
    function To_EnhLogicVector ( s : enh_ulogic_vector ) return enh_logic_vector;
    function To_EnhULogicVector ( s : enh_logic_vector ) return enh_ulogic_vector;

    -------------------------------------------------------------------
    -- strength strippers and type convertors
    -------------------------------------------------------------------
    function To_X01  ( s : enh_logic_vector ) return enh_logic_vector;
    function To_X01  ( s : enh_ulogic_vector ) return enh_ulogic_vector;
    function To_X01  ( s : enh_ulogic ) return ENH_X01;
    function To_X01  ( b : bit_vector ) return enh_logic_vector;
    function To_X01  ( b : bit ) return ENH_X01;

    function To_X01Z ( s : enh_logic_vector ) return enh_logic_vector;
    function To_X01Z ( s : enh_ulogic_vector ) return enh_ulogic_vector;
    function To_X01Z ( s : enh_ulogic ) return ENH_X01Z;
    function To_X01Z ( b : bit_vector ) return enh_logic_vector;
    function To_X01Z ( b : bit ) return ENH_X01Z;

    function To_UX01 ( s : enh_logic_vector ) return enh_logic_vector;
    function To_UX01 ( s : enh_ulogic_vector ) return enh_ulogic_vector;
    function To_UX01 ( s : enh_ulogic ) return ENH_UX01;
    function To_UX01 ( b : bit_vector ) return enh_logic_vector;
    function To_UX01 ( b : bit ) return ENH_UX01;

    -------------------------------------------------------------------
    -- edge detection
    -------------------------------------------------------------------
    function rising_edge  ( signal s : enh_ulogic ) return boolean;
    function falling_edge ( signal s : enh_ulogic ) return boolean;

    -------------------------------------------------------------------
    -- test functions
    -------------------------------------------------------------------
    function Is_X ( s : enh_ulogic_vector ) return boolean;
    function Is_X ( s : enh_logic_vector  ) return boolean;
    function Is_X ( s : enh_ulogic        ) return boolean;

    -------------------------------------------------------------------
    -- string conversion
    -------------------------------------------------------------------
    function to_string ( value : enh_logic_vector ) return string;
    function to_string ( value : enh_ulogic ) return string;

end package enh_logic_1164;
