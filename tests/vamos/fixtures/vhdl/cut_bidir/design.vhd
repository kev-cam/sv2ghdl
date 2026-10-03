library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library sv2vhdl;
use sv2vhdl.sv_display_pkg.all;
use sv2vhdl.sv_strength_pkg.all;
use sv2vhdl.logic3d_types_pkg.all;
use sv2vhdl.sv_analog_pkg.all;
use sv2vhdl.sv_math_pkg.all;

-- Generated from Verilog module pad_sp (/tmp/vamos_fx/bidir/nvc/_norm.sv:21)
entity pad_sp is
  port (
    pad : inout resolved_logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of pad_sp : entity is "/tmp/vamos_fx/bidir/nvc/_norm.sv:21";
end entity; 

-- Generated from Verilog module pad_sp (/tmp/vamos_fx/bidir/nvc/_norm.sv:21)
architecture from_verilog of pad_sp is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/bidir/nvc/_norm.sv:23
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/bidir/nvc/_norm.sv:23
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

-- Generated from Verilog module pad_sp (/tmp/vamos_fx/bidir/nvc/_norm.sv:21)
entity pad_sp__c3c7 is
  port (
    pad : inout resolved_logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of pad_sp__c3c7 : entity is "/tmp/vamos_fx/bidir/nvc/_norm.sv:21";
end entity; 

-- Generated from Verilog module pad_sp (/tmp/vamos_fx/bidir/nvc/_norm.sv:21)
architecture from_verilog of pad_sp__c3c7 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/bidir/nvc/_norm.sv:23
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/bidir/nvc/_norm.sv:23
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

-- Generated from Verilog module tb (/tmp/vamos_fx/bidir/nvc/_norm.sv:4)
entity tb is
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of tb : entity is "/tmp/vamos_fx/bidir/nvc/_norm.sv:4";
end entity; 

-- Generated from Verilog module tb (/tmp/vamos_fx/bidir/nvc/_norm.sv:4)
architecture from_verilog of tb is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/bidir/nvc/_norm.sv:7
  signal d : logic3d := L3D_0;  -- Declared at /tmp/vamos_fx/bidir/nvc/_norm.sv:5
  signal en : logic3d := L3D_0;  -- Declared at /tmp/vamos_fx/bidir/nvc/_norm.sv:5
  signal pad : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/bidir/nvc/_norm.sv:6
  
  component pad_sp is
    port (
      pad : inout resolved_logic3d
    );
  end component;
begin
  
  -- sv_strength: pull1 pull0
  sv_pullup_ivl_4_0_0_inst: entity sv2vhdl.sv_pullup(behavioral)
    port map (
      y => pad
    );
  process (all) is

  begin
    if is_one(en) then
      pad <= d;
    else
      pad <= tmp_ivl_0;
    end if;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/bidir/nvc/_norm.sv:9
  u1: entity work.pad_sp__c3c7
    port map (
      pad => pad
    );
  process (all) is

  begin
    tmp_ivl_0 <= L3D_Z;
  end process;
  
  -- Generated from always process in tb (/tmp/vamos_fx/bidir/nvc/_norm.sv:10)
  process (pad) is
  begin
    sv_display_line("" & sv_tstr((now / (1000 ps)), -9, -12) & " pad=" & sv_bstr(to_std_logic_vector(pad)));
  end process;
  
  -- Generated from initial process in tb (/tmp/vamos_fx/bidir/nvc/_norm.sv:11)
  process is
  begin
    wait for 20000 ps;
    en := L3D_1;
    wait for 20000 ps;
    d <= L3D_1;
    wait for 20000 ps;
    en := L3D_0;
    wait for 20000 ps;
    d <= L3D_0;
    wait;
  end process;
end architecture;



