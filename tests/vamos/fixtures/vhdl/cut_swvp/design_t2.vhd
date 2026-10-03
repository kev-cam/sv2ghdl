library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library sv2vhdl;
use sv2vhdl.sv_display_pkg.all;
use sv2vhdl.sv_strength_pkg.all;
use sv2vhdl.logic3d_types_pkg.all;
use sv2vhdl.sv_analog_pkg.all;
use sv2vhdl.sv_math_pkg.all;

-- Generated from Verilog module pad_sp (/tmp/vamos_fx/swvp/nvc/_norm.sv:23)
entity pad_sp is
  port (
    pad : inout resolved_logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of pad_sp : entity is "/tmp/vamos_fx/swvp/nvc/_norm.sv:23";
end entity; 

-- Generated from Verilog module pad_sp (/tmp/vamos_fx/swvp/nvc/_norm.sv:23)
architecture from_verilog of pad_sp is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/swvp/nvc/_norm.sv:25
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/swvp/nvc/_norm.sv:25
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => pad,
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

-- Generated from Verilog module pad_sp (/tmp/vamos_fx/swvp/nvc/_norm.sv:23)
entity pad_sp__c3c7 is
  port (
    pad : inout resolved_logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of pad_sp__c3c7 : entity is "/tmp/vamos_fx/swvp/nvc/_norm.sv:23";
end entity; 

-- Generated from Verilog module pad_sp (/tmp/vamos_fx/swvp/nvc/_norm.sv:23)
architecture from_verilog of pad_sp__c3c7 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/swvp/nvc/_norm.sv:25
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/swvp/nvc/_norm.sv:25
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => pad,
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

-- Generated from Verilog module tb (/tmp/vamos_fx/swvp/nvc/_norm.sv:5)
entity tb is
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of tb : entity is "/tmp/vamos_fx/swvp/nvc/_norm.sv:5";
end entity; 

-- Generated from Verilog module tb (/tmp/vamos_fx/swvp/nvc/_norm.sv:5)
architecture from_verilog of tb is
  signal tmp_ivl_2 : logic3d_vector(3 downto 0) := (others => L3D_X);  -- Temporary created at /tmp/vamos_fx/swvp/nvc/_norm.sv:10
  signal dout : logic3d_vector(3 downto 0) := logic3d_vector'(L3D_0, L3D_1, L3D_0, L3D_1);  -- Declared at /tmp/vamos_fx/swvp/nvc/_norm.sv:6
  signal oe : logic3d := L3D_0;  -- Declared at /tmp/vamos_fx/swvp/nvc/_norm.sv:7
  signal pbus : resolved_logic3d_vector(3 downto 0) := (others => L3D_X);  -- Declared at /tmp/vamos_fx/swvp/nvc/_norm.sv:8
  signal qbus : resolved_logic3d_vector(1 downto 0) := (others => L3D_X);  -- Declared at /tmp/vamos_fx/swvp/nvc/_norm.sv:9
  alias SW_ivl_7_b is pbus(2);
  alias SW_ivl_9_b is qbus(1);
  alias SW_ivl_0_b_g0 is pbus(0);
  alias SW_ivl_0_b_g1 is pbus(1);
  
  component pad_sp is
    port (
      pad : inout resolved_logic3d
    );
  end component;
begin
  process (all) is

  begin
    if is_one(oe) then
      pbus <= dout;
    else
      pbus <= tmp_ivl_2;
    end if;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/swvp/nvc/_norm.sv:13
  -- Verilog instance: ring[0].u
  u_g0: entity work.pad_sp__c3c7
    port map (
      pad => SW_ivl_0_b_g0
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/swvp/nvc/_norm.sv:13
  -- Verilog instance: ring[1].u
  u_g1: entity work.pad_sp__c3c7
    port map (
      pad => SW_ivl_0_b_g1
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/swvp/nvc/_norm.sv:15
  -- Verilog instance: u2
  u2: entity work.pad_sp__c3c7
    port map (
      pad => SW_ivl_7_b
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/swvp/nvc/_norm.sv:16
  -- Verilog instance: u9
  u9: entity work.pad_sp__c3c7
    port map (
      pad => SW_ivl_9_b
    );
  process (all) is

  begin
    tmp_ivl_2 <= (others => L3D_Z);
  end process;
  
  -- Generated from always process in tb (/tmp/vamos_fx/swvp/nvc/_norm.sv:17)
  process (qbus, pbus) is
  begin
    sv_display_line("" & sv_tstr((now / (1000 ps)), -9, -12) & " " & sv_bstr(to_std_logic_vector(pbus)) & " " & sv_bstr(to_std_logic_vector(qbus)));
  end process;
  
  -- Generated from initial process in tb (/tmp/vamos_fx/swvp/nvc/_norm.sv:18)
  process is
  begin
    wait for 20000 ps;
    oe := L3D_1;
    wait;
  end process;
end architecture;



