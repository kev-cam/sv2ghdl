-- 3D Logic Type Package
-- Compact representation using three boolean fields:
--   value    : logic value (false=0, true=1)
--   strength : drive strength (false=weak, true=strong)
--   uncertain: value known (false=known, true=unknown)
--
-- Maps to 8 states covering essential logic values:
--   (0, strong, known)   -> '0' forcing zero
--   (1, strong, known)   -> '1' forcing one
--   (0, weak,   known)   -> 'L' weak zero
--   (1, weak,   known)   -> 'H' weak one
--   (*, strong, unknown) -> 'X' forcing unknown
--   (*, weak,   unknown) -> 'Z' high impedance (when not driven)
--                        -> 'W' weak unknown (when conflicting weak drivers)

library ieee;
use ieee.std_logic_1164.all;

package logic_3d is

    -------------------------------------------------------------------
    -- 3D logic record type (unresolved)
    -------------------------------------------------------------------
    type logic_3d_t is record
        value    : boolean;   -- false=0, true=1
        strength : boolean;   -- false=weak, true=strong
        uncertain: boolean;   -- false=known, true=unknown
    end record logic_3d_t;

    -------------------------------------------------------------------
    -- Constants for common values
    -------------------------------------------------------------------
    constant L3D_0 : logic_3d_t := (value => false, strength => true,  uncertain => false);  -- '0'
    constant L3D_1 : logic_3d_t := (value => true,  strength => true,  uncertain => false);  -- '1'
    constant L3D_X : logic_3d_t := (value => false, strength => true,  uncertain => true);   -- 'X'
    constant L3D_Z : logic_3d_t := (value => false, strength => false, uncertain => true);   -- 'Z'
    constant L3D_L : logic_3d_t := (value => false, strength => false, uncertain => false);  -- 'L'
    constant L3D_H : logic_3d_t := (value => true,  strength => false, uncertain => false);  -- 'H'
    constant L3D_W : logic_3d_t := (value => true,  strength => false, uncertain => true);   -- 'W' (weak unknown)
    constant L3D_U : logic_3d_t := (value => false, strength => true,  uncertain => true);   -- 'U' (same as X for now)

    -------------------------------------------------------------------
    -- Array type for resolution
    -------------------------------------------------------------------
    type logic_3d_vector is array (natural range <>) of logic_3d_t;

    -------------------------------------------------------------------
    -- Resolution function
    -------------------------------------------------------------------
    function resolved (s : logic_3d_vector) return logic_3d_t;

    -------------------------------------------------------------------
    -- Resolved subtype
    -------------------------------------------------------------------
    subtype logic_3d_r is resolved logic_3d_t;

    -------------------------------------------------------------------
    -- Conversion to/from std_logic
    -------------------------------------------------------------------
    function to_std_logic (l : logic_3d_t) return std_logic;
    function to_logic_3d  (s : std_logic)  return logic_3d_t;

    -------------------------------------------------------------------
    -- Logical operators
    -------------------------------------------------------------------
    function "not"  (l : logic_3d_t) return logic_3d_t;
    function "and"  (l, r : logic_3d_t) return logic_3d_t;
    function "nand" (l, r : logic_3d_t) return logic_3d_t;
    function "or"   (l, r : logic_3d_t) return logic_3d_t;
    function "nor"  (l, r : logic_3d_t) return logic_3d_t;
    function "xor"  (l, r : logic_3d_t) return logic_3d_t;
    function "xnor" (l, r : logic_3d_t) return logic_3d_t;

    -------------------------------------------------------------------
    -- Utility functions
    -------------------------------------------------------------------
    function is_known   (l : logic_3d_t) return boolean;
    function is_high_z  (l : logic_3d_t) return boolean;
    function is_strong  (l : logic_3d_t) return boolean;
    function to_bit     (l : logic_3d_t) return bit;
    function to_string  (l : logic_3d_t) return string;

end package logic_3d;

package body logic_3d is

    -------------------------------------------------------------------
    -- Resolution function
    -- Combines multiple drivers according to strength rules
    -------------------------------------------------------------------
    function resolved (s : logic_3d_vector) return logic_3d_t is
        variable result : logic_3d_t := L3D_Z;  -- default high-Z
        variable has_strong : boolean := false;
        variable strong_val : boolean := false;
        variable has_weak   : boolean := false;
        variable weak_val   : boolean := false;
        variable strong_conflict : boolean := false;
        variable weak_conflict   : boolean := false;
    begin
        if s'length = 0 then
            return L3D_Z;
        end if;

        -- Scan all drivers
        for i in s'range loop
            if not s(i).uncertain then
                -- Known value driver
                if s(i).strength then
                    -- Strong driver
                    if has_strong then
                        if strong_val /= s(i).value then
                            strong_conflict := true;
                        end if;
                    else
                        has_strong := true;
                        strong_val := s(i).value;
                    end if;
                else
                    -- Weak driver
                    if has_weak then
                        if weak_val /= s(i).value then
                            weak_conflict := true;
                        end if;
                    else
                        has_weak := true;
                        weak_val := s(i).value;
                    end if;
                end if;
            elsif s(i).strength then
                -- Strong unknown (X) - propagates
                strong_conflict := true;
                has_strong := true;
            end if;
            -- Weak unknown (Z/W) doesn't contribute unless nothing else
        end loop;

        -- Determine result based on what we found
        if strong_conflict then
            return L3D_X;  -- Strong conflict -> X
        elsif has_strong then
            return (value => strong_val, strength => true, uncertain => false);
        elsif weak_conflict then
            return L3D_W;  -- Weak conflict -> W
        elsif has_weak then
            return (value => weak_val, strength => false, uncertain => false);
        else
            return L3D_Z;  -- No drivers -> Z
        end if;
    end function resolved;

    -------------------------------------------------------------------
    -- Conversion to std_logic
    -------------------------------------------------------------------
    function to_std_logic (l : logic_3d_t) return std_logic is
    begin
        if l.uncertain then
            if l.strength then
                return 'X';  -- strong unknown
            else
                return 'Z';  -- weak unknown (high-Z)
            end if;
        else
            if l.strength then
                if l.value then return '1'; else return '0'; end if;
            else
                if l.value then return 'H'; else return 'L'; end if;
            end if;
        end if;
    end function to_std_logic;

    -------------------------------------------------------------------
    -- Conversion from std_logic
    -------------------------------------------------------------------
    function to_logic_3d (s : std_logic) return logic_3d_t is
    begin
        case s is
            when '0' => return L3D_0;
            when '1' => return L3D_1;
            when 'L' => return L3D_L;
            when 'H' => return L3D_H;
            when 'Z' => return L3D_Z;
            when 'W' => return L3D_W;
            when 'X' => return L3D_X;
            when 'U' => return L3D_U;
            when '-' => return L3D_X;  -- don't care treated as X
        end case;
    end function to_logic_3d;

    -------------------------------------------------------------------
    -- NOT operator
    -------------------------------------------------------------------
    function "not" (l : logic_3d_t) return logic_3d_t is
    begin
        if l.uncertain then
            return l;  -- not X = X, not Z = Z
        else
            return (value => not l.value, strength => l.strength, uncertain => false);
        end if;
    end function "not";

    -------------------------------------------------------------------
    -- AND operator
    -------------------------------------------------------------------
    function "and" (l, r : logic_3d_t) return logic_3d_t is
    begin
        -- 0 AND anything = 0 (strong 0 dominates)
        if (not l.uncertain and not l.value and l.strength) or
           (not r.uncertain and not r.value and r.strength) then
            return L3D_0;
        end if;
        -- If either is unknown, result is unknown
        if l.uncertain or r.uncertain then
            return L3D_X;
        end if;
        -- Both known: result is AND of values, weaker strength
        return (value    => l.value and r.value,
                strength => l.strength and r.strength,
                uncertain => false);
    end function "and";

    -------------------------------------------------------------------
    -- NAND operator
    -------------------------------------------------------------------
    function "nand" (l, r : logic_3d_t) return logic_3d_t is
    begin
        return not (l and r);
    end function "nand";

    -------------------------------------------------------------------
    -- OR operator
    -------------------------------------------------------------------
    function "or" (l, r : logic_3d_t) return logic_3d_t is
    begin
        -- 1 OR anything = 1 (strong 1 dominates)
        if (not l.uncertain and l.value and l.strength) or
           (not r.uncertain and r.value and r.strength) then
            return L3D_1;
        end if;
        -- If either is unknown, result is unknown
        if l.uncertain or r.uncertain then
            return L3D_X;
        end if;
        -- Both known: result is OR of values, weaker strength
        return (value    => l.value or r.value,
                strength => l.strength and r.strength,
                uncertain => false);
    end function "or";

    -------------------------------------------------------------------
    -- NOR operator
    -------------------------------------------------------------------
    function "nor" (l, r : logic_3d_t) return logic_3d_t is
    begin
        return not (l or r);
    end function "nor";

    -------------------------------------------------------------------
    -- XOR operator
    -------------------------------------------------------------------
    function "xor" (l, r : logic_3d_t) return logic_3d_t is
    begin
        if l.uncertain or r.uncertain then
            return L3D_X;
        end if;
        return (value    => l.value xor r.value,
                strength => l.strength and r.strength,
                uncertain => false);
    end function "xor";

    -------------------------------------------------------------------
    -- XNOR operator
    -------------------------------------------------------------------
    function "xnor" (l, r : logic_3d_t) return logic_3d_t is
    begin
        return not (l xor r);
    end function "xnor";

    -------------------------------------------------------------------
    -- Utility: is the value known?
    -------------------------------------------------------------------
    function is_known (l : logic_3d_t) return boolean is
    begin
        return not l.uncertain;
    end function is_known;

    -------------------------------------------------------------------
    -- Utility: is it high-Z?
    -------------------------------------------------------------------
    function is_high_z (l : logic_3d_t) return boolean is
    begin
        return l.uncertain and not l.strength;
    end function is_high_z;

    -------------------------------------------------------------------
    -- Utility: is it strong drive?
    -------------------------------------------------------------------
    function is_strong (l : logic_3d_t) return boolean is
    begin
        return l.strength;
    end function is_strong;

    -------------------------------------------------------------------
    -- Convert to bit (unknown -> '0')
    -------------------------------------------------------------------
    function to_bit (l : logic_3d_t) return bit is
    begin
        if l.value and not l.uncertain then
            return '1';
        else
            return '0';
        end if;
    end function to_bit;

    -------------------------------------------------------------------
    -- String representation
    -------------------------------------------------------------------
    function to_string (l : logic_3d_t) return string is
        variable c : character;
        variable s : std_logic;
    begin
        s := to_std_logic(l);
        case s is
            when '0' => c := '0';
            when '1' => c := '1';
            when 'X' => c := 'X';
            when 'Z' => c := 'Z';
            when 'L' => c := 'L';
            when 'H' => c := 'H';
            when 'W' => c := 'W';
            when 'U' => c := 'U';
            when '-' => c := '-';
        end case;
        return (1 => c);
    end function to_string;

end package body logic_3d;
