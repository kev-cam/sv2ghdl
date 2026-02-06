-- SystemVerilog Built-in Primitives Library
-- IEEE 1800-2017 Section 28: Gate-level and switch-level modeling
--
-- This file contains entity declarations and stub architectures for all
-- 26 SystemVerilog built-in gate and switch primitives.
--
-- Categories:
--   28.4  N-input gates:    and, nand, or, nor, xor, xnor
--   28.5  N-output gates:   buf, not
--   28.6  Three-state:      bufif0, bufif1, notif0, notif1
--   28.7  MOS switches:     nmos, pmos, rnmos, rpmos
--   28.8  Bidirectional:    tran, tranif0, tranif1, rtran, rtranif0, rtranif1
--   28.9  CMOS switches:    cmos, rcmos
--   28.10 Pull gates:       pullup, pulldown

library ieee;
use ieee.std_logic_1164.all;

library work;
use work.logic_3d.all;

-- ==========================================================================
-- PACKAGE: Component declarations for use in instantiation
-- ==========================================================================
package sv_primitives is

    ---------------------------------------------------------------------------
    -- 28.4 MULTI-INPUT GATES
    ---------------------------------------------------------------------------
    component sv_and is
        generic (n : positive := 2);
        port (
            y : out logic_3d_r;
            a : in  logic_3d_vector(0 to n-1)
        );
    end component;

    component sv_nand is
        generic (n : positive := 2);
        port (
            y : out logic_3d_r;
            a : in  logic_3d_vector(0 to n-1)
        );
    end component;

    component sv_or is
        generic (n : positive := 2);
        port (
            y : out logic_3d_r;
            a : in  logic_3d_vector(0 to n-1)
        );
    end component;

    component sv_nor is
        generic (n : positive := 2);
        port (
            y : out logic_3d_r;
            a : in  logic_3d_vector(0 to n-1)
        );
    end component;

    component sv_xor is
        generic (n : positive := 2);
        port (
            y : out logic_3d_r;
            a : in  logic_3d_vector(0 to n-1)
        );
    end component;

    component sv_xnor is
        generic (n : positive := 2);
        port (
            y : out logic_3d_r;
            a : in  logic_3d_vector(0 to n-1)
        );
    end component;

    ---------------------------------------------------------------------------
    -- 28.5 MULTI-OUTPUT GATES
    ---------------------------------------------------------------------------
    component sv_buf is
        generic (n : positive := 1);
        port (
            y : out logic_3d_vector(0 to n-1);
            a : in  logic_3d_t
        );
    end component;

    component sv_not is
        generic (n : positive := 1);
        port (
            y : out logic_3d_vector(0 to n-1);
            a : in  logic_3d_t
        );
    end component;

    ---------------------------------------------------------------------------
    -- 28.6 THREE-STATE GATES
    ---------------------------------------------------------------------------
    component sv_bufif0 is
        port (
            y    : out logic_3d_r;
            data : in  logic_3d_t;
            ctrl : in  logic_3d_t
        );
    end component;

    component sv_bufif1 is
        port (
            y    : out logic_3d_r;
            data : in  logic_3d_t;
            ctrl : in  logic_3d_t
        );
    end component;

    component sv_notif0 is
        port (
            y    : out logic_3d_r;
            data : in  logic_3d_t;
            ctrl : in  logic_3d_t
        );
    end component;

    component sv_notif1 is
        port (
            y    : out logic_3d_r;
            data : in  logic_3d_t;
            ctrl : in  logic_3d_t
        );
    end component;

    ---------------------------------------------------------------------------
    -- 28.7 MOS SWITCHES
    ---------------------------------------------------------------------------
    component sv_nmos is
        port (
            y    : out logic_3d_r;
            data : in  logic_3d_t;
            gate : in  logic_3d_t
        );
    end component;

    component sv_pmos is
        port (
            y    : out logic_3d_r;
            data : in  logic_3d_t;
            gate : in  logic_3d_t
        );
    end component;

    component sv_rnmos is
        port (
            y    : out logic_3d_r;
            data : in  logic_3d_t;
            gate : in  logic_3d_t
        );
    end component;

    component sv_rpmos is
        port (
            y    : out logic_3d_r;
            data : in  logic_3d_t;
            gate : in  logic_3d_t
        );
    end component;

    ---------------------------------------------------------------------------
    -- 28.9 CMOS SWITCHES
    ---------------------------------------------------------------------------
    component sv_cmos is
        port (
            y     : out logic_3d_r;
            data  : in  logic_3d_t;
            ngate : in  logic_3d_t;
            pgate : in  logic_3d_t
        );
    end component;

    component sv_rcmos is
        port (
            y     : out logic_3d_r;
            data  : in  logic_3d_t;
            ngate : in  logic_3d_t;
            pgate : in  logic_3d_t
        );
    end component;

    ---------------------------------------------------------------------------
    -- 28.8 BIDIRECTIONAL SWITCHES
    ---------------------------------------------------------------------------
    component sv_tran is
        port (
            a : inout logic_3d_r;
            b : inout logic_3d_r
        );
    end component;

    component sv_tranif0 is
        port (
            a    : inout logic_3d_r;
            b    : inout logic_3d_r;
            ctrl : in    logic_3d_t
        );
    end component;

    component sv_tranif1 is
        port (
            a    : inout logic_3d_r;
            b    : inout logic_3d_r;
            ctrl : in    logic_3d_t
        );
    end component;

    component sv_rtran is
        port (
            a : inout logic_3d_r;
            b : inout logic_3d_r
        );
    end component;

    component sv_rtranif0 is
        port (
            a    : inout logic_3d_r;
            b    : inout logic_3d_r;
            ctrl : in    logic_3d_t
        );
    end component;

    component sv_rtranif1 is
        port (
            a    : inout logic_3d_r;
            b    : inout logic_3d_r;
            ctrl : in    logic_3d_t
        );
    end component;

    ---------------------------------------------------------------------------
    -- 28.10 PULL GATES
    ---------------------------------------------------------------------------
    component sv_pullup is
        port (
            y : out logic_3d_r
        );
    end component;

    component sv_pulldown is
        port (
            y : out logic_3d_r
        );
    end component;

end package sv_primitives;

-- ==========================================================================
-- ENTITY DECLARATIONS AND ARCHITECTURES
-- ==========================================================================

---------------------------------------------------------------------------
-- 28.4 N-INPUT GATES
-- N inputs, 1 output; first terminal is output
---------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- AND Gate: N-input AND
entity sv_and is
    generic (n : positive := 2);
    port (
        y : out logic_3d_r;
        a : in  logic_3d_vector(0 to n-1)
    );
end entity sv_and;

architecture behavioral of sv_and is
begin
    process (a)
        variable result : logic_3d_t := L3D_1;
    begin
        result := L3D_1;
        for i in a'range loop
            result := result and a(i);
        end loop;
        y <= result;
    end process;
end architecture behavioral;

---------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- NAND Gate: N-input NAND
entity sv_nand is
    generic (n : positive := 2);
    port (
        y : out logic_3d_r;
        a : in  logic_3d_vector(0 to n-1)
    );
end entity sv_nand;

architecture behavioral of sv_nand is
begin
    process (a)
        variable result : logic_3d_t := L3D_1;
    begin
        result := L3D_1;
        for i in a'range loop
            result := result and a(i);
        end loop;
        y <= not result;
    end process;
end architecture behavioral;

---------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- OR Gate: N-input OR
entity sv_or is
    generic (n : positive := 2);
    port (
        y : out logic_3d_r;
        a : in  logic_3d_vector(0 to n-1)
    );
end entity sv_or;

architecture behavioral of sv_or is
begin
    process (a)
        variable result : logic_3d_t := L3D_0;
    begin
        result := L3D_0;
        for i in a'range loop
            result := result or a(i);
        end loop;
        y <= result;
    end process;
end architecture behavioral;

---------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- NOR Gate: N-input NOR
entity sv_nor is
    generic (n : positive := 2);
    port (
        y : out logic_3d_r;
        a : in  logic_3d_vector(0 to n-1)
    );
end entity sv_nor;

architecture behavioral of sv_nor is
begin
    process (a)
        variable result : logic_3d_t := L3D_0;
    begin
        result := L3D_0;
        for i in a'range loop
            result := result or a(i);
        end loop;
        y <= not result;
    end process;
end architecture behavioral;

---------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- XOR Gate: N-input XOR (odd parity)
entity sv_xor is
    generic (n : positive := 2);
    port (
        y : out logic_3d_r;
        a : in  logic_3d_vector(0 to n-1)
    );
end entity sv_xor;

architecture behavioral of sv_xor is
begin
    process (a)
        variable result : logic_3d_t := L3D_0;
    begin
        result := L3D_0;
        for i in a'range loop
            result := result xor a(i);
        end loop;
        y <= result;
    end process;
end architecture behavioral;

---------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- XNOR Gate: N-input XNOR (even parity)
entity sv_xnor is
    generic (n : positive := 2);
    port (
        y : out logic_3d_r;
        a : in  logic_3d_vector(0 to n-1)
    );
end entity sv_xnor;

architecture behavioral of sv_xnor is
begin
    process (a)
        variable result : logic_3d_t := L3D_0;
    begin
        result := L3D_0;
        for i in a'range loop
            result := result xor a(i);
        end loop;
        y <= not result;
    end process;
end architecture behavioral;

---------------------------------------------------------------------------
-- 28.5 N-OUTPUT GATES
-- 1 input, N outputs; last terminal is input
---------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- BUF: Buffer with N outputs
entity sv_buf is
    generic (n : positive := 1);
    port (
        y : out logic_3d_vector(0 to n-1);
        a : in  logic_3d_t
    );
end entity sv_buf;

architecture behavioral of sv_buf is
begin
    process (a)
        variable out_val : logic_3d_t;
    begin
        -- Buffer: pass through with strong drive
        if a.uncertain then
            out_val := L3D_X;
        elsif a.value then
            out_val := L3D_1;
        else
            out_val := L3D_0;
        end if;
        for i in y'range loop
            y(i) <= out_val;
        end loop;
    end process;
end architecture behavioral;

---------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- NOT: Inverter with N outputs
entity sv_not is
    generic (n : positive := 1);
    port (
        y : out logic_3d_vector(0 to n-1);
        a : in  logic_3d_t
    );
end entity sv_not;

architecture behavioral of sv_not is
begin
    process (a)
        variable out_val : logic_3d_t;
    begin
        -- Inverter: invert with strong drive
        if a.uncertain then
            out_val := L3D_X;
        elsif a.value then
            out_val := L3D_0;
        else
            out_val := L3D_1;
        end if;
        for i in y'range loop
            y(i) <= out_val;
        end loop;
    end process;
end architecture behavioral;

---------------------------------------------------------------------------
-- 28.6 THREE-STATE GATES
-- data input, control input, output
---------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- BUFIF0: Three-state buffer, active-low control
entity sv_bufif0 is
    port (
        y    : out logic_3d_r;
        data : in  logic_3d_t;
        ctrl : in  logic_3d_t
    );
end entity sv_bufif0;

architecture behavioral of sv_bufif0 is
begin
    process (data, ctrl)
    begin
        if not ctrl.uncertain and not ctrl.value then
            -- ctrl = 0: enabled, pass data through as strong
            if data.uncertain then
                y <= L3D_X;
            elsif data.value then
                y <= L3D_1;
            else
                y <= L3D_0;
            end if;
        elsif not ctrl.uncertain and ctrl.value then
            -- ctrl = 1: disabled, high-Z
            y <= L3D_Z;
        else
            -- ctrl = X/Z: output L or H depending on data
            if data.uncertain then
                y <= L3D_X;
            elsif data.value then
                y <= L3D_H;  -- might be 1 or Z
            else
                y <= L3D_L;  -- might be 0 or Z
            end if;
        end if;
    end process;
end architecture behavioral;

---------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- BUFIF1: Three-state buffer, active-high control
entity sv_bufif1 is
    port (
        y    : out logic_3d_r;
        data : in  logic_3d_t;
        ctrl : in  logic_3d_t
    );
end entity sv_bufif1;

architecture behavioral of sv_bufif1 is
begin
    process (data, ctrl)
    begin
        if not ctrl.uncertain and ctrl.value then
            -- ctrl = 1: enabled, pass data through as strong
            if data.uncertain then
                y <= L3D_X;
            elsif data.value then
                y <= L3D_1;
            else
                y <= L3D_0;
            end if;
        elsif not ctrl.uncertain and not ctrl.value then
            -- ctrl = 0: disabled, high-Z
            y <= L3D_Z;
        else
            -- ctrl = X/Z: output L or H depending on data
            if data.uncertain then
                y <= L3D_X;
            elsif data.value then
                y <= L3D_H;
            else
                y <= L3D_L;
            end if;
        end if;
    end process;
end architecture behavioral;

---------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- NOTIF0: Three-state inverter, active-low control
entity sv_notif0 is
    port (
        y    : out logic_3d_r;
        data : in  logic_3d_t;
        ctrl : in  logic_3d_t
    );
end entity sv_notif0;

architecture behavioral of sv_notif0 is
begin
    process (data, ctrl)
    begin
        if not ctrl.uncertain and not ctrl.value then
            -- ctrl = 0: enabled, pass inverted data as strong
            if data.uncertain then
                y <= L3D_X;
            elsif data.value then
                y <= L3D_0;  -- NOT 1 = 0
            else
                y <= L3D_1;  -- NOT 0 = 1
            end if;
        elsif not ctrl.uncertain and ctrl.value then
            -- ctrl = 1: disabled, high-Z
            y <= L3D_Z;
        else
            -- ctrl = X/Z: output L or H depending on inverted data
            if data.uncertain then
                y <= L3D_X;
            elsif data.value then
                y <= L3D_L;  -- NOT 1 might be 0 or Z
            else
                y <= L3D_H;  -- NOT 0 might be 1 or Z
            end if;
        end if;
    end process;
end architecture behavioral;

---------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- NOTIF1: Three-state inverter, active-high control
entity sv_notif1 is
    port (
        y    : out logic_3d_r;
        data : in  logic_3d_t;
        ctrl : in  logic_3d_t
    );
end entity sv_notif1;

architecture behavioral of sv_notif1 is
begin
    process (data, ctrl)
    begin
        if not ctrl.uncertain and ctrl.value then
            -- ctrl = 1: enabled, pass inverted data as strong
            if data.uncertain then
                y <= L3D_X;
            elsif data.value then
                y <= L3D_0;
            else
                y <= L3D_1;
            end if;
        elsif not ctrl.uncertain and not ctrl.value then
            -- ctrl = 0: disabled, high-Z
            y <= L3D_Z;
        else
            -- ctrl = X/Z: output L or H depending on inverted data
            if data.uncertain then
                y <= L3D_X;
            elsif data.value then
                y <= L3D_L;
            else
                y <= L3D_H;
            end if;
        end if;
    end process;
end architecture behavioral;

---------------------------------------------------------------------------
-- 28.7 MOS SWITCHES
-- Unidirectional pass transistors
---------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- NMOS: N-channel MOS, conducts when gate=1
entity sv_nmos is
    port (
        y    : out logic_3d_r;
        data : in  logic_3d_t;
        gate : in  logic_3d_t
    );
end entity sv_nmos;

architecture behavioral of sv_nmos is
begin
    -- NMOS conducts when gate=1, passes data through
    process (data, gate)
    begin
        if not gate.uncertain and gate.value then
            -- gate = 1: conducting, pass data
            if data.uncertain then
                y <= L3D_X;
            elsif data.value then
                y <= L3D_1;
            else
                y <= L3D_0;
            end if;
        elsif not gate.uncertain and not gate.value then
            -- gate = 0: not conducting
            y <= L3D_Z;
        else
            -- gate = X/Z: weak pass-through
            if data.uncertain then
                y <= L3D_X;
            elsif data.value then
                y <= L3D_H;
            else
                y <= L3D_L;
            end if;
        end if;
    end process;
end architecture behavioral;

---------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- PMOS: P-channel MOS, conducts when gate=0
entity sv_pmos is
    port (
        y    : out logic_3d_r;
        data : in  logic_3d_t;
        gate : in  logic_3d_t
    );
end entity sv_pmos;

architecture behavioral of sv_pmos is
begin
    -- PMOS conducts when gate=0, passes data through
    process (data, gate)
    begin
        if not gate.uncertain and not gate.value then
            -- gate = 0: conducting, pass data
            if data.uncertain then
                y <= L3D_X;
            elsif data.value then
                y <= L3D_1;
            else
                y <= L3D_0;
            end if;
        elsif not gate.uncertain and gate.value then
            -- gate = 1: not conducting
            y <= L3D_Z;
        else
            -- gate = X/Z: weak pass-through
            if data.uncertain then
                y <= L3D_X;
            elsif data.value then
                y <= L3D_H;
            else
                y <= L3D_L;
            end if;
        end if;
    end process;
end architecture behavioral;

---------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- RNMOS: Resistive N-channel MOS, reduces strength
entity sv_rnmos is
    port (
        y    : out logic_3d_r;
        data : in  logic_3d_t;
        gate : in  logic_3d_t
    );
end entity sv_rnmos;

architecture behavioral of sv_rnmos is
begin
    -- Resistive NMOS: conducts when gate=1, reduces strength
    process (data, gate)
    begin
        if not gate.uncertain and gate.value then
            -- gate = 1: conducting with reduced strength
            if data.uncertain then
                y <= L3D_W;  -- weak unknown
            elsif data.value then
                y <= L3D_H;  -- weak 1
            else
                y <= L3D_L;  -- weak 0
            end if;
        elsif not gate.uncertain and not gate.value then
            -- gate = 0: not conducting
            y <= L3D_Z;
        else
            -- gate = X/Z: weak pass-through (already weak)
            if data.uncertain then
                y <= L3D_W;
            elsif data.value then
                y <= L3D_H;
            else
                y <= L3D_L;
            end if;
        end if;
    end process;
end architecture behavioral;

---------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- RPMOS: Resistive P-channel MOS, reduces strength
entity sv_rpmos is
    port (
        y    : out logic_3d_r;
        data : in  logic_3d_t;
        gate : in  logic_3d_t
    );
end entity sv_rpmos;

architecture behavioral of sv_rpmos is
begin
    -- Resistive PMOS: conducts when gate=0, reduces strength
    process (data, gate)
    begin
        if not gate.uncertain and not gate.value then
            -- gate = 0: conducting with reduced strength
            if data.uncertain then
                y <= L3D_W;
            elsif data.value then
                y <= L3D_H;
            else
                y <= L3D_L;
            end if;
        elsif not gate.uncertain and gate.value then
            -- gate = 1: not conducting
            y <= L3D_Z;
        else
            -- gate = X/Z: weak pass-through
            if data.uncertain then
                y <= L3D_W;
            elsif data.value then
                y <= L3D_H;
            else
                y <= L3D_L;
            end if;
        end if;
    end process;
end architecture behavioral;

---------------------------------------------------------------------------
-- 28.9 CMOS SWITCHES
-- Complementary MOS (nmos + pmos in parallel)
---------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- CMOS: Complementary MOS switch
entity sv_cmos is
    port (
        y     : out logic_3d_r;
        data  : in  logic_3d_t;
        ngate : in  logic_3d_t;
        pgate : in  logic_3d_t
    );
end entity sv_cmos;

architecture behavioral of sv_cmos is
    -- CMOS = NMOS + PMOS in parallel
    -- nmos conducts when ngate=1, pmos conducts when pgate=0
    -- Typical usage: ngate and pgate are complementary (ngate = NOT pgate)
begin
    process (data, ngate, pgate)
        variable nmos_on : boolean;
        variable pmos_on : boolean;
        variable nmos_uncertain : boolean;
        variable pmos_uncertain : boolean;
    begin
        nmos_on := not ngate.uncertain and ngate.value;           -- ngate = 1
        pmos_on := not pgate.uncertain and not pgate.value;       -- pgate = 0
        nmos_uncertain := ngate.uncertain;
        pmos_uncertain := pgate.uncertain;

        if nmos_on or pmos_on then
            -- At least one device conducting: pass data through
            if data.uncertain then
                y <= L3D_X;
            elsif data.value then
                y <= L3D_1;
            else
                y <= L3D_0;
            end if;
        elsif nmos_uncertain or pmos_uncertain then
            -- Uncertain control: weak pass-through
            if data.uncertain then
                y <= L3D_X;
            elsif data.value then
                y <= L3D_H;
            else
                y <= L3D_L;
            end if;
        else
            -- Both devices off: high-Z
            y <= L3D_Z;
        end if;
    end process;
end architecture behavioral;

---------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- RCMOS: Resistive CMOS switch, reduces strength
entity sv_rcmos is
    port (
        y     : out logic_3d_r;
        data  : in  logic_3d_t;
        ngate : in  logic_3d_t;
        pgate : in  logic_3d_t
    );
end entity sv_rcmos;

architecture behavioral of sv_rcmos is
    -- Resistive CMOS: same as CMOS but reduces strength
begin
    process (data, ngate, pgate)
        variable nmos_on : boolean;
        variable pmos_on : boolean;
        variable nmos_uncertain : boolean;
        variable pmos_uncertain : boolean;
    begin
        nmos_on := not ngate.uncertain and ngate.value;
        pmos_on := not pgate.uncertain and not pgate.value;
        nmos_uncertain := ngate.uncertain;
        pmos_uncertain := pgate.uncertain;

        if nmos_on or pmos_on or nmos_uncertain or pmos_uncertain then
            -- Conducting (or uncertain): pass data with reduced strength
            if data.uncertain then
                y <= L3D_W;
            elsif data.value then
                y <= L3D_H;
            else
                y <= L3D_L;
            end if;
        else
            -- Both devices off: high-Z
            y <= L3D_Z;
        end if;
    end process;
end architecture behavioral;

---------------------------------------------------------------------------
-- 28.8 BIDIRECTIONAL PASS SWITCHES
-- Signal flows both directions; require 'others/'driver for proper modeling
---------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- TRAN: Always-on bidirectional switch
entity sv_tran is
    port (
        a : inout logic_3d_r;
        b : inout logic_3d_r
    );
end entity sv_tran;

architecture stub of sv_tran is
    -- TRAN: Always-on bidirectional switch
    -- STUB: Requires 'others and 'driver NVC extensions for proper implementation
    -- Intended implementation:
    --   process (a'others, b'others)
    --   begin
    --       a'driver := b'others;
    --       b'driver := a'others;
    --   end process;
begin
    -- Cannot implement without 'others/'driver extensions
end architecture stub;

---------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- TRANIF0: Bidirectional switch, conducts when ctrl=0
entity sv_tranif0 is
    port (
        a    : inout logic_3d_r;
        b    : inout logic_3d_r;
        ctrl : in    logic_3d_t
    );
end entity sv_tranif0;

architecture stub of sv_tranif0 is
    -- TRANIF0: Bidirectional switch, conducts when ctrl=0
    -- STUB: Requires 'others and 'driver NVC extensions
begin
end architecture stub;

---------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- TRANIF1: Bidirectional switch, conducts when ctrl=1
entity sv_tranif1 is
    port (
        a    : inout logic_3d_r;
        b    : inout logic_3d_r;
        ctrl : in    logic_3d_t
    );
end entity sv_tranif1;

architecture stub of sv_tranif1 is
    -- TRANIF1: Bidirectional switch, conducts when ctrl=1
    -- STUB: Requires 'others and 'driver NVC extensions
begin
end architecture stub;

---------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- RTRAN: Resistive always-on bidirectional switch
entity sv_rtran is
    port (
        a : inout logic_3d_r;
        b : inout logic_3d_r
    );
end entity sv_rtran;

architecture stub of sv_rtran is
    -- RTRAN: Resistive always-on bidirectional switch (reduces strength)
    -- STUB: Requires 'others and 'driver NVC extensions
begin
end architecture stub;

---------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- RTRANIF0: Resistive bidirectional switch, conducts when ctrl=0
entity sv_rtranif0 is
    port (
        a    : inout logic_3d_r;
        b    : inout logic_3d_r;
        ctrl : in    logic_3d_t
    );
end entity sv_rtranif0;

architecture stub of sv_rtranif0 is
    -- RTRANIF0: Resistive bidirectional switch, conducts when ctrl=0
    -- STUB: Requires 'others and 'driver NVC extensions
begin
end architecture stub;

---------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- RTRANIF1: Resistive bidirectional switch, conducts when ctrl=1
entity sv_rtranif1 is
    port (
        a    : inout logic_3d_r;
        b    : inout logic_3d_r;
        ctrl : in    logic_3d_t
    );
end entity sv_rtranif1;

architecture stub of sv_rtranif1 is
    -- RTRANIF1: Resistive bidirectional switch, conducts when ctrl=1
    -- STUB: Requires 'others and 'driver NVC extensions
begin
end architecture stub;

---------------------------------------------------------------------------
-- 28.10 PULL GATES
-- Provide weak constant drivers
---------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- PULLUP: Drives weak 1 (H)
entity sv_pullup is
    port (
        y : out logic_3d_r
    );
end entity sv_pullup;

architecture behavioral of sv_pullup is
    -- PULLUP: Constant weak 1 (H) driver
begin
    y <= L3D_H;
end architecture behavioral;

---------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
library work;
use work.logic_3d.all;

-- PULLDOWN: Drives weak 0 (L)
entity sv_pulldown is
    port (
        y : out logic_3d_r
    );
end entity sv_pulldown;

architecture behavioral of sv_pulldown is
    -- PULLDOWN: Constant weak 0 (L) driver
begin
    y <= L3D_L;
end architecture behavioral;
