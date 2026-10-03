library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library sv2vhdl;
use sv2vhdl.sv_display_pkg.all;
use sv2vhdl.sv_strength_pkg.all;
use sv2vhdl.logic3d_types_pkg.all;
use sv2vhdl.sv_analog_pkg.all;
use sv2vhdl.sv_math_pkg.all;

-- Generated from Verilog module pio (/tmp/vamos_fx/pullshare/nvc/_norm.sv:22)
entity pio is
  port (
    pad : inout resolved_logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of pio : entity is "/tmp/vamos_fx/pullshare/nvc/_norm.sv:22";
end entity; 

-- Generated from Verilog module pio (/tmp/vamos_fx/pullshare/nvc/_norm.sv:22)
architecture from_verilog of pio is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/pullshare/nvc/_norm.sv:24
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/pullshare/nvc/_norm.sv:24
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

-- Generated from Verilog module pio (/tmp/vamos_fx/pullshare/nvc/_norm.sv:22)
entity pio__c3c7 is
  port (
    pad : inout resolved_logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of pio__c3c7 : entity is "/tmp/vamos_fx/pullshare/nvc/_norm.sv:22";
end entity; 

-- Generated from Verilog module pio (/tmp/vamos_fx/pullshare/nvc/_norm.sv:22)
architecture from_verilog of pio__c3c7 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/pullshare/nvc/_norm.sv:24
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/pullshare/nvc/_norm.sv:24
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

-- Generated from Verilog module pw (/tmp/vamos_fx/pullshare/nvc/_norm.sv:15)
entity pw is
  port (
    p : inout resolved_logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of pw : entity is "/tmp/vamos_fx/pullshare/nvc/_norm.sv:15";
end entity; 

-- Generated from Verilog module pw (/tmp/vamos_fx/pullshare/nvc/_norm.sv:15)
architecture from_verilog of pw is
  
  component pio is
    port (
      pad : inout resolved_logic3d
    );
  end component;
begin
  
  -- sv_strength: pull1 pull0
  sv_pullup_ivl_0_0_0_inst: entity sv2vhdl.sv_pullup(behavioral)
    port map (
      y => p
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/pullshare/nvc/_norm.sv:17
  u: entity work.pio__c3c7
    port map (
      pad => p
    );
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

-- Generated from Verilog module pw (/tmp/vamos_fx/pullshare/nvc/_norm.sv:15)
entity pw__ff9e is
  port (
    p : inout resolved_logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of pw__ff9e : entity is "/tmp/vamos_fx/pullshare/nvc/_norm.sv:15";
end entity; 

-- Generated from Verilog module pw (/tmp/vamos_fx/pullshare/nvc/_norm.sv:15)
architecture from_verilog of pw__ff9e is
  
  component pio is
    port (
      pad : inout resolved_logic3d
    );
  end component;
begin
  
  -- sv_strength: pull1 pull0
  sv_pullup_ivl_0_0_0_inst: entity sv2vhdl.sv_pullup(behavioral)
    port map (
      y => p
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/pullshare/nvc/_norm.sv:17
  u: entity work.pio__c3c7
    port map (
      pad => p
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

-- Generated from Verilog module tb (/tmp/vamos_fx/pullshare/nvc/_norm.sv:6)
entity tb is
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of tb : entity is "/tmp/vamos_fx/pullshare/nvc/_norm.sv:6";
end entity; 

-- Generated from Verilog module tb (/tmp/vamos_fx/pullshare/nvc/_norm.sv:6)
architecture from_verilog of tb is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/pullshare/nvc/_norm.sv:9
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/pullshare/nvc/_norm.sv:9
  signal en : logic3d := L3D_0;  -- Declared at /tmp/vamos_fx/pullshare/nvc/_norm.sv:7
  signal p1 : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/pullshare/nvc/_norm.sv:8
  signal p2 : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/pullshare/nvc/_norm.sv:8
  
  component pw is
    port (
      p : inout resolved_logic3d
    );
  end component;
begin
  process (all) is

  begin
    if is_one(en) then
      p1 <= tmp_ivl_0;
    else
      p1 <= tmp_ivl_2;
    end if;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/pullshare/nvc/_norm.sv:11
  w1: entity work.pw__ff9e
    port map (
      p => p1
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/pullshare/nvc/_norm.sv:12
  w2: entity work.pw__ff9e
    port map (
      p => p2
    );
  process (all) is

  begin
    tmp_ivl_0 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_2 <= L3D_Z;
  end process;
  
  -- Generated from always process in tb (/tmp/vamos_fx/pullshare/nvc/_norm.sv:10)
  process (p1) is
  begin
    sv_display_line("" & sv_tstr((now / (1000 ps)), -9, -12) & " p1=" & sv_bstr(to_std_logic_vector(p1)));
  end process;
end architecture;



