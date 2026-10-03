library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library sv2vhdl;
use sv2vhdl.sv_display_pkg.all;
use sv2vhdl.sv_strength_pkg.all;
use sv2vhdl.logic3d_types_pkg.all;
use sv2vhdl.sv_analog_pkg.all;
use sv2vhdl.sv_math_pkg.all;

-- Generated from Verilog module _cell (/tmp/vamos_fx/ports/nvc/_norm.sv:94)
entity module_cell__f8f0 is
  port (
    a : in logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of module_cell__f8f0 : entity is "/tmp/vamos_fx/ports/nvc/_norm.sv:94";
end entity; 

-- Generated from Verilog module _cell (/tmp/vamos_fx/ports/nvc/_norm.sv:94)
architecture from_verilog of module_cell__f8f0 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:97
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:97
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

-- Generated from Verilog module block (/tmp/vamos_fx/ports/nvc/_norm.sv:66)
entity block_module__f8f0 is
  port (
    a : in logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of block_module__f8f0 : entity is "/tmp/vamos_fx/ports/nvc/_norm.sv:66";
end entity; 

-- Generated from Verilog module block (/tmp/vamos_fx/ports/nvc/_norm.sv:66)
architecture from_verilog of block_module__f8f0 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:69
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:69
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

-- Generated from Verilog module buffer (/tmp/vamos_fx/ports/nvc/_norm.sv:59)
entity buffer_module__f8f0 is
  port (
    a : in logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of buffer_module__f8f0 : entity is "/tmp/vamos_fx/ports/nvc/_norm.sv:59";
end entity; 

-- Generated from Verilog module buffer (/tmp/vamos_fx/ports/nvc/_norm.sv:59)
architecture from_verilog of buffer_module__f8f0 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:62
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:62
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

-- Generated from Verilog module cell_ (/tmp/vamos_fx/ports/nvc/_norm.sv:87)
entity cell_module__f8f0 is
  port (
    a : in logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of cell_module__f8f0 : entity is "/tmp/vamos_fx/ports/nvc/_norm.sv:87";
end entity; 

-- Generated from Verilog module cell_ (/tmp/vamos_fx/ports/nvc/_norm.sv:87)
architecture from_verilog of cell_module__f8f0 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:90
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:90
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

-- Generated from Verilog module inv (/tmp/vamos_fx/ports/nvc/_norm.sv:28)
entity inv is
  port (
    a : in logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of inv : entity is "/tmp/vamos_fx/ports/nvc/_norm.sv:28";
end entity; 

-- Generated from Verilog module inv (/tmp/vamos_fx/ports/nvc/_norm.sv:28)
architecture from_verilog of inv is
begin
  process (all) is

  begin
    y <= l3d_not(a);
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

-- Generated from Verilog module my__cell (/tmp/vamos_fx/ports/nvc/_norm.sv:80)
entity my_cell__f8f0 is
  port (
    a : in logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of my_cell__f8f0 : entity is "/tmp/vamos_fx/ports/nvc/_norm.sv:80";
end entity; 

-- Generated from Verilog module my__cell (/tmp/vamos_fx/ports/nvc/_norm.sv:80)
architecture from_verilog of my_cell__f8f0 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:83
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:83
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

-- Generated from Verilog module register (/tmp/vamos_fx/ports/nvc/_norm.sv:73)
entity register_module__f8f0 is
  port (
    a : in logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of register_module__f8f0 : entity is "/tmp/vamos_fx/ports/nvc/_norm.sv:73";
end entity; 

-- Generated from Verilog module register (/tmp/vamos_fx/ports/nvc/_norm.sv:73)
architecture from_verilog of register_module__f8f0 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:76
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:76
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

-- Generated from Verilog module rw (/tmp/vamos_fx/ports/nvc/_norm.sv:34)
entity rw is
  port (
    in_sig : in logic3d;
    out_sig : out logic3d;
    signal_sig : in logic3d;
    bus_sig : in logic3d;
    open_sig : out logic3d;
    sig_a : in logic3d;
    b_sig : inout resolved_logic3d;
    c_d : out logic3d;
    inv : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of rw : entity is "/tmp/vamos_fx/ports/nvc/_norm.sv:34";
end entity; 

-- Generated from Verilog module rw (/tmp/vamos_fx/ports/nvc/_norm.sv:34)
architecture from_verilog of rw is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:44
  signal tmp_ivl_10 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:46
  signal tmp_ivl_12 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:47
  signal tmp_ivl_14 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:47
  signal tmp_ivl_16 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:48
  signal tmp_ivl_18 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:48
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:44
  signal tmp_ivl_4 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:45
  signal tmp_ivl_6 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:45
  signal tmp_ivl_8 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:46
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => out_sig,
      data => tmp_ivl_0,
      ctrl => tmp_ivl_2
    );
  
  sv_bufif1_vamos_ams_hiz_1_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => open_sig,
      data => tmp_ivl_4,
      ctrl => tmp_ivl_6
    );
  
  sv_bufif1_vamos_ams_hiz_2_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => b_sig,
      data => tmp_ivl_8,
      ctrl => tmp_ivl_10
    );
  
  sv_bufif1_vamos_ams_hiz_3_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => c_d,
      data => tmp_ivl_12,
      ctrl => tmp_ivl_14
    );
  
  sv_bufif1_vamos_ams_hiz_4_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => inv,
      data => tmp_ivl_16,
      ctrl => tmp_ivl_18
    );
  process (all) is

  begin
    tmp_ivl_0 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_10 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_12 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_14 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_16 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_18 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_2 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_4 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_6 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_8 <= L3D_0;
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

-- Generated from Verilog module rw2 (/tmp/vamos_fx/ports/nvc/_norm.sv:52)
entity rw2 is
  port (
    OUT_sig : out logic3d;
    In_sig : in logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of rw2 : entity is "/tmp/vamos_fx/ports/nvc/_norm.sv:52";
end entity; 

-- Generated from Verilog module rw2 (/tmp/vamos_fx/ports/nvc/_norm.sv:52)
architecture from_verilog of rw2 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:55
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:55
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => OUT_sig,
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

-- Generated from Verilog module rw2 (/tmp/vamos_fx/ports/nvc/_norm.sv:52)
entity rw2__7ed3 is
  port (
    OUT_sig : out logic3d;
    In_sig : in logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of rw2__7ed3 : entity is "/tmp/vamos_fx/ports/nvc/_norm.sv:52";
end entity; 

-- Generated from Verilog module rw2 (/tmp/vamos_fx/ports/nvc/_norm.sv:52)
architecture from_verilog of rw2__7ed3 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:55
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:55
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => OUT_sig,
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

-- Generated from Verilog module rw (/tmp/vamos_fx/ports/nvc/_norm.sv:34)
entity rw__c86a is
  port (
    in_sig : in logic3d;
    out_sig : out logic3d;
    signal_sig : in logic3d;
    bus_sig : in logic3d;
    open_sig : out logic3d;
    sig_a : in logic3d;
    b_sig : inout resolved_logic3d;
    c_d : out logic3d;
    inv_sig : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of rw__c86a : entity is "/tmp/vamos_fx/ports/nvc/_norm.sv:34";
end entity; 

-- Generated from Verilog module rw (/tmp/vamos_fx/ports/nvc/_norm.sv:34)
architecture from_verilog of rw__c86a is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:44
  signal tmp_ivl_10 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:46
  signal tmp_ivl_12 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:47
  signal tmp_ivl_14 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:47
  signal tmp_ivl_16 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:48
  signal tmp_ivl_18 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:48
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:44
  signal tmp_ivl_4 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:45
  signal tmp_ivl_6 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:45
  signal tmp_ivl_8 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/ports/nvc/_norm.sv:46
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => out_sig,
      data => tmp_ivl_0,
      ctrl => tmp_ivl_2
    );
  
  sv_bufif1_vamos_ams_hiz_1_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => open_sig,
      data => tmp_ivl_4,
      ctrl => tmp_ivl_6
    );
  
  sv_bufif1_vamos_ams_hiz_2_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => b_sig,
      data => tmp_ivl_8,
      ctrl => tmp_ivl_10
    );
  
  sv_bufif1_vamos_ams_hiz_3_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => c_d,
      data => tmp_ivl_12,
      ctrl => tmp_ivl_14
    );
  
  sv_bufif1_vamos_ams_hiz_4_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => inv_sig,
      data => tmp_ivl_16,
      ctrl => tmp_ivl_18
    );
  process (all) is

  begin
    tmp_ivl_0 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_10 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_12 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_14 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_16 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_18 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_2 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_4 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_6 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_8 <= L3D_0;
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

-- Generated from Verilog module inv (/tmp/vamos_fx/ports/nvc/_norm.sv:28)
entity inv__7342 is
  port (
    a : in logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of inv__7342 : entity is "/tmp/vamos_fx/ports/nvc/_norm.sv:28";
end entity; 

-- Generated from Verilog module inv (/tmp/vamos_fx/ports/nvc/_norm.sv:28)
architecture from_verilog of inv__7342 is
begin
  process (all) is

  begin
    y <= l3d_not(a);
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

-- Generated from Verilog module tb (/tmp/vamos_fx/ports/nvc/_norm.sv:5)
entity tb is
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of tb : entity is "/tmp/vamos_fx/ports/nvc/_norm.sv:5";
end entity; 

-- Generated from Verilog module tb (/tmp/vamos_fx/ports/nvc/_norm.sv:5)
architecture from_verilog of tb is
  signal a_bus : logic3d := L3D_0;  -- Declared at /tmp/vamos_fx/ports/nvc/_norm.sv:6
  signal a_in : logic3d := L3D_0;  -- Declared at /tmp/vamos_fx/ports/nvc/_norm.sv:6
  signal a_signal : logic3d := L3D_1;  -- Declared at /tmp/vamos_fx/ports/nvc/_norm.sv:6
  signal a_ua : logic3d := L3D_1;  -- Declared at /tmp/vamos_fx/ports/nvc/_norm.sv:6
  signal w_OUT : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/ports/nvc/_norm.sv:7
  signal w_b : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/ports/nvc/_norm.sv:8
  signal w_cd : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/ports/nvc/_norm.sv:7
  signal w_inv : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/ports/nvc/_norm.sv:7
  signal w_open : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/ports/nvc/_norm.sv:7
  signal w_out_1 : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/ports/nvc/_norm.sv:7
  signal y_blk : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/ports/nvc/_norm.sv:9
  signal y_buf : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/ports/nvc/_norm.sv:9
  signal y_c1 : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/ports/nvc/_norm.sv:9
  signal y_c2 : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/ports/nvc/_norm.sv:9
  signal y_inv : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/ports/nvc/_norm.sv:9
  signal y_my : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/ports/nvc/_norm.sv:9
  signal y_reg : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/ports/nvc/_norm.sv:9
  
  component inv is
    port (
      a : in logic3d;
      y : out logic3d
    );
  end component;
  
  component rw is
    port (
      in_sig : in logic3d;
      out_sig : out logic3d;
      signal_sig : in logic3d;
      bus_sig : in logic3d;
      open_sig : out logic3d;
      sig_a : in logic3d;
      b_sig : inout resolved_logic3d;
      c_d : out logic3d;
      inv_sig : out logic3d
    );
  end component;
  
  component rw2 is
    port (
      OUT_sig : out logic3d;
      In_sig : in logic3d
    );
  end component;
  
  component buffer_module is
    port (
      a : in logic3d;
      y : out logic3d
    );
  end component;
  
  component block_module is
    port (
      a : in logic3d;
      y : out logic3d
    );
  end component;
  
  component cell_module is
    port (
      a : in logic3d;
      y : out logic3d
    );
  end component;
  
  component module_cell is
    port (
      a : in logic3d;
      y : out logic3d
    );
  end component;
  
  component my_cell is
    port (
      a : in logic3d;
      y : out logic3d
    );
  end component;
  
  component register_module is
    port (
      a : in logic3d;
      y : out logic3d
    );
  end component;
begin
  
  -- Generated from instantiation at /tmp/vamos_fx/ports/nvc/_norm.sv:13
  i1: entity work.inv__7342
    port map (
      a => a_in,
      y => y_inv
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/ports/nvc/_norm.sv:10
  u1: entity work.rw__c86a
    port map (
      sig_a => a_ua,
      b_sig => w_b,
      bus_sig => a_bus,
      c_d => w_cd,
      in_sig => a_in,
      inv_sig => w_inv,
      open_sig => w_open,
      out_sig => w_out_1,
      signal_sig => a_signal
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/ports/nvc/_norm.sv:12
  u2: entity work.rw2__7ed3
    port map (
      In_sig => a_in,
      OUT_sig => w_OUT
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/ports/nvc/_norm.sv:14
  ub: entity work.buffer_module__f8f0
    port map (
      a => a_in,
      y => y_buf
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/ports/nvc/_norm.sv:15
  ubl: entity work.block_module__f8f0
    port map (
      a => a_signal,
      y => y_blk
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/ports/nvc/_norm.sv:18
  uc1: entity work.cell_module__f8f0
    port map (
      a => a_in,
      y => y_c1
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/ports/nvc/_norm.sv:19
  uc2: entity work.module_cell__f8f0
    port map (
      a => a_in,
      y => y_c2
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/ports/nvc/_norm.sv:17
  um: entity work.my_cell__f8f0
    port map (
      a => a_ua,
      y => y_my
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/ports/nvc/_norm.sv:16
  ure: entity work.register_module__f8f0
    port map (
      a => a_bus,
      y => y_reg
    );
  
  -- Generated from initial process in tb (/tmp/vamos_fx/ports/nvc/_norm.sv:20)
  process is
  begin
    wait for 5000 ps;
    a_in := L3D_1;
    wait for 5000 ps;
    sv_display_line("" & sv_bstr(to_std_logic_vector(w_out_1)) & " " & sv_bstr(to_std_logic_vector(w_OUT)) & " " & sv_bstr(to_std_logic_vector(w_open)) & " " & sv_bstr(to_std_logic_vector(w_cd)) & " " & sv_bstr(to_std_logic_vector(w_inv)) & " " & sv_bstr(to_std_logic_vector(w_b)) & " " & sv_bstr(to_std_logic_vector(y_buf)) & " " & sv_bstr(to_std_logic_vector(y_blk)) & " " & sv_bstr(to_std_logic_vector(y_reg)) & " " & sv_bstr(to_std_logic_vector(y_my)) & " " & sv_bstr(to_std_logic_vector(y_c1)) & " " & sv_bstr(to_std_logic_vector(y_c2)));
    wait for 5000 ps;
    sv_write_flush;
    std.env.finish;
    wait;
  end process;
end architecture;



