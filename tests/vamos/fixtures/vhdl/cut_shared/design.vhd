library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library sv2vhdl;
use sv2vhdl.sv_display_pkg.all;
use sv2vhdl.sv_strength_pkg.all;
use sv2vhdl.logic3d_types_pkg.all;
use sv2vhdl.sv_analog_pkg.all;
use sv2vhdl.sv_math_pkg.all;

-- Generated from Verilog module rc_sp (/tmp/vamos_fx/shared/nvc/_norm.sv:24)
entity rc_sp is
  port (
    a : in logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of rc_sp : entity is "/tmp/vamos_fx/shared/nvc/_norm.sv:24";
end entity; 

-- Generated from Verilog module rc_sp (/tmp/vamos_fx/shared/nvc/_norm.sv:24)
architecture from_verilog of rc_sp is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/shared/nvc/_norm.sv:27
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/shared/nvc/_norm.sv:27
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

-- Generated from Verilog module rc_sp (/tmp/vamos_fx/shared/nvc/_norm.sv:24)
entity rc_sp__f8f0 is
  port (
    a : in logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of rc_sp__f8f0 : entity is "/tmp/vamos_fx/shared/nvc/_norm.sv:24";
end entity; 

-- Generated from Verilog module rc_sp (/tmp/vamos_fx/shared/nvc/_norm.sv:24)
architecture from_verilog of rc_sp__f8f0 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/shared/nvc/_norm.sv:27
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/shared/nvc/_norm.sv:27
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

-- Generated from Verilog module wrap (/tmp/vamos_fx/shared/nvc/_norm.sv:18)
entity wrap__7aff is
  port (
    a : in logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of wrap__7aff : entity is "/tmp/vamos_fx/shared/nvc/_norm.sv:18";
end entity; 

-- Generated from Verilog module wrap (/tmp/vamos_fx/shared/nvc/_norm.sv:18)
architecture from_verilog of wrap__7aff is
  
  component rc_sp is
    port (
      a : in logic3d;
      y : out logic3d
    );
  end component;
  signal y_Readable : logic3d := L3D_X;  -- Needed to connect outputs
begin
  process (all) is

  begin
    y <= y_Readable;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/shared/nvc/_norm.sv:19
  u3: entity work.rc_sp__f8f0
    port map (
      a => a,
      y => y_Readable
    );
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

-- Generated from Verilog module tb (/tmp/vamos_fx/shared/nvc/_norm.sv:5)
entity tb is
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of tb : entity is "/tmp/vamos_fx/shared/nvc/_norm.sv:5";
end entity; 

-- Generated from Verilog module tb (/tmp/vamos_fx/shared/nvc/_norm.sv:5)
architecture from_verilog of tb is
  signal clk : logic3d := L3D_0;  -- Declared at /tmp/vamos_fx/shared/nvc/_norm.sv:6
  signal clk2 : logic3d := L3D_0;  -- Declared at /tmp/vamos_fx/shared/nvc/_norm.sv:7
  signal y3 : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/shared/nvc/_norm.sv:8
  signal yb : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/shared/nvc/_norm.sv:8
  
  component rc_sp is
    port (
      a : in logic3d;
      y : out logic3d
    );
  end component;
  
  component wrap is
    port (
      a : in logic3d;
      y : out logic3d
    );
  end component;
begin
  
  -- Generated from instantiation at /tmp/vamos_fx/shared/nvc/_norm.sv:11
  u1: entity work.rc_sp__f8f0
    port map (
      a => clk,
      y => yb
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/shared/nvc/_norm.sv:12
  w1: entity work.wrap__7aff
    port map (
      a => clk2,
      y => y3
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/shared/nvc/_norm.sv:13
  w2: entity work.wrap__7aff
    port map (
      a => clk,
      y => yb
    );
  
  -- Generated from always process in tb (/tmp/vamos_fx/shared/nvc/_norm.sv:9)
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
  
  -- Generated from always process in tb (/tmp/vamos_fx/shared/nvc/_norm.sv:10)
  process is
    variable v_clk2 : logic3d;
  begin
    v_clk2 := clk2;
    loop
      wait for 15000 ps;
      v_clk2 := l3d_not(v_clk2);
      clk2 <= v_clk2;
    end loop;
  end process;
  
  -- Generated from always process in tb (/tmp/vamos_fx/shared/nvc/_norm.sv:14)
  process (yb) is
  begin
    sv_display_line("" & sv_tstr((now / (1000 ps)), -9, -12) & " yb=" & sv_bstr(to_std_logic_vector(yb)));
  end process;
  
  -- Generated from always process in tb (/tmp/vamos_fx/shared/nvc/_norm.sv:15)
  process (y3) is
  begin
    sv_display_line("" & sv_tstr((now / (1000 ps)), -9, -12) & " y3=" & sv_bstr(to_std_logic_vector(y3)));
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

-- Generated from Verilog module wrap (/tmp/vamos_fx/shared/nvc/_norm.sv:18)
entity wrap is
  port (
    a : in logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of wrap : entity is "/tmp/vamos_fx/shared/nvc/_norm.sv:18";
end entity; 

-- Generated from Verilog module wrap (/tmp/vamos_fx/shared/nvc/_norm.sv:18)
architecture from_verilog of wrap is
  
  component rc_sp is
    port (
      a : in logic3d;
      y : out logic3d
    );
  end component;
  signal y_Readable : logic3d := L3D_X;  -- Needed to connect outputs
begin
  process (all) is

  begin
    y <= y_Readable;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/shared/nvc/_norm.sv:19
  u3: entity work.rc_sp__f8f0
    port map (
      a => a,
      y => y_Readable
    );
end architecture;



