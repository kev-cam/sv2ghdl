library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library sv2vhdl;
use sv2vhdl.sv_display_pkg.all;
use sv2vhdl.sv_strength_pkg.all;
use sv2vhdl.logic3d_types_pkg.all;
use sv2vhdl.sv_analog_pkg.all;
use sv2vhdl.sv_math_pkg.all;

-- Generated from Verilog module cin2 (/tmp/vamos_fx/vec/nvc/_norm.sv:33)
entity cin2 is
  port (
    d : in logic3d_vector(1 downto 0)
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of cin2 : entity is "/tmp/vamos_fx/vec/nvc/_norm.sv:33";
end entity; 

-- Generated from Verilog module cin2 (/tmp/vamos_fx/vec/nvc/_norm.sv:33)
architecture from_verilog of cin2 is
begin
end architecture;


-- This VHDL was converted from Verilog using the
-- Icarus Verilog VHDL Code Generator 13.0 (devel) (6029f3dfb)

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library sv2vhdl;
use sv2vhdl.sv_display_pkg.all;
use sv2vhdl.sv_strength_pkg.all;
use sv2vhdl.logic3d_types_pkg.all;
use sv2vhdl.sv_analog_pkg.all;
use sv2vhdl.sv_math_pkg.all;

-- Generated from Verilog module cout (/tmp/vamos_fx/vec/nvc/_norm.sv:27)
entity cout is
  port (
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of cout : entity is "/tmp/vamos_fx/vec/nvc/_norm.sv:27";
end entity; 

-- Generated from Verilog module cout (/tmp/vamos_fx/vec/nvc/_norm.sv:27)
architecture from_verilog of cout is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/vec/nvc/_norm.sv:29
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/vec/nvc/_norm.sv:29
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => y,
      data => tmp_ivl_0,
      ctrl => tmp_ivl_2
    );
  process (all) is

  begin
    tmp_ivl_0 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_2 <= L3D_0;
  end process;
end architecture;


-- This VHDL was converted from Verilog using the
-- Icarus Verilog VHDL Code Generator 13.0 (devel) (6029f3dfb)

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library sv2vhdl;
use sv2vhdl.sv_display_pkg.all;
use sv2vhdl.sv_strength_pkg.all;
use sv2vhdl.logic3d_types_pkg.all;
use sv2vhdl.sv_analog_pkg.all;
use sv2vhdl.sv_math_pkg.all;

-- Generated from Verilog module cin2 (/tmp/vamos_fx/vec/nvc/_norm.sv:33)
entity cin2__9a93 is
  port (
    d : in logic3d_vector(1 downto 0)
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of cin2__9a93 : entity is "/tmp/vamos_fx/vec/nvc/_norm.sv:33";
end entity; 

-- Generated from Verilog module cin2 (/tmp/vamos_fx/vec/nvc/_norm.sv:33)
architecture from_verilog of cin2__9a93 is
begin
end architecture;

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library sv2vhdl;
use sv2vhdl.sv_display_pkg.all;
use sv2vhdl.sv_strength_pkg.all;
use sv2vhdl.logic3d_types_pkg.all;
use sv2vhdl.sv_analog_pkg.all;
use sv2vhdl.sv_math_pkg.all;

-- Generated from Verilog module cout (/tmp/vamos_fx/vec/nvc/_norm.sv:27)
entity cout__1760 is
  port (
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of cout__1760 : entity is "/tmp/vamos_fx/vec/nvc/_norm.sv:27";
end entity; 

-- Generated from Verilog module cout (/tmp/vamos_fx/vec/nvc/_norm.sv:27)
architecture from_verilog of cout__1760 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/vec/nvc/_norm.sv:29
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/vec/nvc/_norm.sv:29
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => y,
      data => tmp_ivl_0,
      ctrl => tmp_ivl_2
    );
  process (all) is

  begin
    tmp_ivl_0 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_2 <= L3D_0;
  end process;
end architecture;

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library sv2vhdl;
use sv2vhdl.sv_display_pkg.all;
use sv2vhdl.sv_strength_pkg.all;
use sv2vhdl.logic3d_types_pkg.all;
use sv2vhdl.sv_analog_pkg.all;
use sv2vhdl.sv_math_pkg.all;

-- Generated from Verilog module tb (/tmp/vamos_fx/vec/nvc/_norm.sv:5)
entity tb is
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of tb : entity is "/tmp/vamos_fx/vec/nvc/_norm.sv:5";
end entity; 

-- Generated from Verilog module tb (/tmp/vamos_fx/vec/nvc/_norm.sv:5)
architecture from_verilog of tb is
  signal tmp_ivl_12 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/vec/nvc/_norm.sv:18
  signal tmp_ivl_16 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/vec/nvc/_norm.sv:20
  signal tmp_ivl_18 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/vec/nvc/_norm.sv:20
  signal tmp_ivl_32 : logic3d_vector(1 downto 0) := (others => L3D_X);  -- Temporary created at 
  signal arr : logic3d_vector(1 downto 0) := (others => L3D_X);  -- Declared at /tmp/vamos_fx/vec/nvc/_norm.sv:8
  signal clk : logic3d := L3D_0;  -- Declared at /tmp/vamos_fx/vec/nvc/_norm.sv:6
  signal code : logic3d_vector(3 downto 0) := logic3d_vector'(L3D_0, L3D_1, L3D_0, L3D_1);  -- Declared at /tmp/vamos_fx/vec/nvc/_norm.sv:7
  signal gy : logic3d_vector(1 downto 0) := (others => L3D_X);  -- Declared at /tmp/vamos_fx/vec/nvc/_norm.sv:8
  signal mix : logic3d_vector(3 downto 0) := (others => L3D_X);  -- Declared at /tmp/vamos_fx/vec/nvc/_norm.sv:9
  signal two : logic3d_vector(1 downto 0) := (others => L3D_X);  -- Declared at /tmp/vamos_fx/vec/nvc/_norm.sv:8
  signal LPM_d0_ivl_4 : logic3d := L3D_X;
  signal LPM_d1_ivl_4 : logic3d := L3D_X;
  signal LPM_q_ivl_20 : logic3d_vector(1 downto 0) := (others => L3D_X);
  signal LPM_q_ivl_21 : logic3d_vector(1 downto 0) := (others => L3D_X);
  signal LPM_d0_ivl_28 : logic3d := L3D_X;
  signal LPM_d1_ivl_28 : logic3d := L3D_X;
  signal LPM_d0_ivl_29 : logic3d := L3D_X;
  signal LPM_d1_ivl_29 : logic3d := L3D_X;
  signal LPM_d0_ivl_30 : logic3d := L3D_X;
  
  component cout is
    port (
      y : out logic3d
    );
  end component;
  
  component cin2 is
    port (
      d : in logic3d_vector(1 downto 0)
    );
  end component;
begin
  process (all) is

  begin
    arr <= LPM_d1_ivl_4 & LPM_d0_ivl_4;
  end process;
  process (all) is

  begin
    LPM_q_ivl_21 <= code(2 + 1 downto 2);
  end process;
  process (all) is

  begin
    gy <= LPM_d1_ivl_28 & LPM_d0_ivl_28;
  end process;
  process (all) is

  begin
    two <= LPM_d1_ivl_29 & LPM_d0_ivl_29;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/vec/nvc/_norm.sv:11
  ca0: entity work.cout__1760
    port map (
      y => LPM_d0_ivl_4
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/vec/nvc/_norm.sv:11
  ca1: entity work.cout__1760
    port map (
      y => LPM_d1_ivl_4
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/vec/nvc/_norm.sv:14
  u_i0: entity work.cout__1760
    port map (
      y => LPM_d0_ivl_28
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/vec/nvc/_norm.sv:14
  u_i1: entity work.cout__1760
    port map (
      y => LPM_d1_ivl_28
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/vec/nvc/_norm.sv:19
  m0: entity work.cout__1760
    port map (
      y => LPM_d0_ivl_30
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/vec/nvc/_norm.sv:16
  t0: entity work.cout__1760
    port map (
      y => LPM_d0_ivl_29
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/vec/nvc/_norm.sv:17
  t1: entity work.cout__1760
    port map (
      y => LPM_d1_ivl_29
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/vec/nvc/_norm.sv:20
  ub: entity work.cin2__9a93
    port map (
      d => LPM_q_ivl_20
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/vec/nvc/_norm.sv:21
  up: entity work.cin2__9a93
    port map (
      d => LPM_q_ivl_21
    );
  
  -- Generated from always process in tb (/tmp/vamos_fx/vec/nvc/_norm.sv:10)
  process is
    variable v_clk : logic3d;
  begin
    v_clk := clk;
    loop
      wait for 10000 ps;
      v_clk := l3d_not(v_clk);
      clk <= v_clk;
    end loop;
  end process;
  
  -- Generated from always process in tb (/tmp/vamos_fx/vec/nvc/_norm.sv:22)
  process (mix, two, gy, arr) is
  begin
    sv_display_line("" & sv_tstr((now / (1000 ps)), -9, -12) & " " & sv_bstr(to_std_logic_vector(arr)) & " " & sv_bstr(to_std_logic_vector(gy)) & " " & sv_bstr(to_std_logic_vector(two)) & " " & sv_bstr(to_std_logic_vector(mix)));
  end process;
  
  comb_fused_0: process (LPM_d0_ivl_30, clk) is
  begin
    tmp_ivl_12 := clk;
    tmp_ivl_32 := (others => L3D_Z);
    mix := tmp_ivl_12 & tmp_ivl_32 & LPM_d0_ivl_30;
  end process;
  
  comb_fused_1: process (code) is
  begin
    tmp_ivl_16 := code(0);
    tmp_ivl_18 := code(1);
    LPM_q_ivl_20 := tmp_ivl_16 & tmp_ivl_18;
  end process;
end architecture;



