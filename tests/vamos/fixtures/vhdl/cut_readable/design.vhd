library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library sv2vhdl;
use sv2vhdl.sv_display_pkg.all;
use sv2vhdl.sv_strength_pkg.all;
use sv2vhdl.logic3d_types_pkg.all;
use sv2vhdl.sv_analog_pkg.all;
use sv2vhdl.sv_math_pkg.all;

-- Generated from Verilog module adc (/tmp/vamos_fx/readable/nvc/_norm.sv:77)
entity adc is
  port (
    vin : in logic3d;
    clk : in logic3d;
    q : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of adc : entity is "/tmp/vamos_fx/readable/nvc/_norm.sv:77";
end entity; 

-- Generated from Verilog module adc (/tmp/vamos_fx/readable/nvc/_norm.sv:77)
architecture from_verilog of adc is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:81
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:81
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => q,
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

-- Generated from Verilog module bg (/tmp/vamos_fx/readable/nvc/_norm.sv:71)
entity bg is
  port (
    vref : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of bg : entity is "/tmp/vamos_fx/readable/nvc/_norm.sv:71";
end entity; 

-- Generated from Verilog module bg (/tmp/vamos_fx/readable/nvc/_norm.sv:71)
architecture from_verilog of bg is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:73
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:73
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => vref,
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

-- Generated from Verilog module adc (/tmp/vamos_fx/readable/nvc/_norm.sv:77)
entity adc__aa94 is
  port (
    vin : in logic3d;
    clk : in logic3d;
    q : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of adc__aa94 : entity is "/tmp/vamos_fx/readable/nvc/_norm.sv:77";
end entity; 

-- Generated from Verilog module adc (/tmp/vamos_fx/readable/nvc/_norm.sv:77)
architecture from_verilog of adc__aa94 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:81
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:81
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => q,
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

-- Generated from Verilog module bg (/tmp/vamos_fx/readable/nvc/_norm.sv:71)
entity bg__b4b2 is
  port (
    vref : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of bg__b4b2 : entity is "/tmp/vamos_fx/readable/nvc/_norm.sv:71";
end entity; 

-- Generated from Verilog module bg (/tmp/vamos_fx/readable/nvc/_norm.sv:71)
architecture from_verilog of bg__b4b2 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:73
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:73
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => vref,
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

-- Generated from Verilog module bgadc (/tmp/vamos_fx/readable/nvc/_norm.sv:37)
entity bgadc is
  port (
    clk : in logic3d;
    vref : out logic3d;
    q : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of bgadc : entity is "/tmp/vamos_fx/readable/nvc/_norm.sv:37";
end entity; 

-- Generated from Verilog module bgadc (/tmp/vamos_fx/readable/nvc/_norm.sv:37)
architecture from_verilog of bgadc is
  
  component bg is
    port (
      vref : out logic3d
    );
  end component;
  signal vref_Readable : logic3d := L3D_X;  -- Needed to connect outputs
  
  component adc is
    port (
      vin : in logic3d;
      clk : in logic3d;
      q : out logic3d
    );
  end component;
  signal q_Readable : logic3d := L3D_X;  -- Needed to connect outputs
  signal vin_Readable : logic3d := L3D_X;  -- Needed to connect outputs
begin
  
  -- Generated from instantiation at /tmp/vamos_fx/readable/nvc/_norm.sv:38
  u1: entity work.bg__b4b2
    port map (
      vref => vref_Readable
    );
  process (all) is

  begin
    q <= q_Readable;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/readable/nvc/_norm.sv:39
  u2: entity work.adc__aa94
    port map (
      clk => clk,
      q => q_Readable,
      vin => vin_Readable
    );
  
  comb_fused_0: process (vref_Readable) is
  begin
    vref := vref_Readable;
    vin_Readable := vref;
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

-- Generated from Verilog module pad_sp (/tmp/vamos_fx/readable/nvc/_norm.sv:65)
entity pad_sp is
  port (
    pad : inout resolved_logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of pad_sp : entity is "/tmp/vamos_fx/readable/nvc/_norm.sv:65";
end entity; 

-- Generated from Verilog module pad_sp (/tmp/vamos_fx/readable/nvc/_norm.sv:65)
architecture from_verilog of pad_sp is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:67
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:67
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

-- Generated from Verilog module sink (/tmp/vamos_fx/readable/nvc/_norm.sv:58)
entity sink is
  port (
    a : in logic3d;
    q : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of sink : entity is "/tmp/vamos_fx/readable/nvc/_norm.sv:58";
end entity; 

-- Generated from Verilog module sink (/tmp/vamos_fx/readable/nvc/_norm.sv:58)
architecture from_verilog of sink is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:61
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:61
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => q,
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

-- Generated from Verilog module src (/tmp/vamos_fx/readable/nvc/_norm.sv:44)
entity src is
  port (
    a : in logic3d;
    vo : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of src : entity is "/tmp/vamos_fx/readable/nvc/_norm.sv:44";
end entity; 

-- Generated from Verilog module src (/tmp/vamos_fx/readable/nvc/_norm.sv:44)
architecture from_verilog of src is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:47
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:47
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => vo,
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

-- Generated from Verilog module src2 (/tmp/vamos_fx/readable/nvc/_norm.sv:51)
entity src2 is
  port (
    a : in logic3d;
    q : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of src2 : entity is "/tmp/vamos_fx/readable/nvc/_norm.sv:51";
end entity; 

-- Generated from Verilog module src2 (/tmp/vamos_fx/readable/nvc/_norm.sv:51)
architecture from_verilog of src2 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:54
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:54
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => q,
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

-- Generated from Verilog module src (/tmp/vamos_fx/readable/nvc/_norm.sv:44)
entity src__a4e7 is
  port (
    a : in logic3d;
    vo : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of src__a4e7 : entity is "/tmp/vamos_fx/readable/nvc/_norm.sv:44";
end entity; 

-- Generated from Verilog module src (/tmp/vamos_fx/readable/nvc/_norm.sv:44)
architecture from_verilog of src__a4e7 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:47
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:47
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => vo,
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

-- Generated from Verilog module wrap_e (/tmp/vamos_fx/readable/nvc/_norm.sv:19)
entity wrap_e__66ef is
  port (
    a : in logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of wrap_e__66ef : entity is "/tmp/vamos_fx/readable/nvc/_norm.sv:19";
end entity; 

-- Generated from Verilog module wrap_e (/tmp/vamos_fx/readable/nvc/_norm.sv:19)
architecture from_verilog of wrap_e__66ef is
  
  component src is
    port (
      a : in logic3d;
      vo : out logic3d
    );
  end component;
  signal vo_Readable : logic3d := L3D_X;  -- Needed to connect outputs
begin
  process (all) is

  begin
    y <= vo_Readable;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/readable/nvc/_norm.sv:20
  u: entity work.src__a4e7
    port map (
      a => a,
      vo => vo_Readable
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

-- Generated from Verilog module pad_sp (/tmp/vamos_fx/readable/nvc/_norm.sv:65)
entity pad_sp__c3c7 is
  port (
    pad : inout resolved_logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of pad_sp__c3c7 : entity is "/tmp/vamos_fx/readable/nvc/_norm.sv:65";
end entity; 

-- Generated from Verilog module pad_sp (/tmp/vamos_fx/readable/nvc/_norm.sv:65)
architecture from_verilog of pad_sp__c3c7 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:67
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:67
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

-- Generated from Verilog module wrap_c (/tmp/vamos_fx/readable/nvc/_norm.sv:33)
entity wrap_c__c0ef is
  port (
    p : inout resolved_logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of wrap_c__c0ef : entity is "/tmp/vamos_fx/readable/nvc/_norm.sv:33";
end entity; 

-- Generated from Verilog module wrap_c (/tmp/vamos_fx/readable/nvc/_norm.sv:33)
architecture from_verilog of wrap_c__c0ef is
  
  component pad_sp is
    port (
      pad : inout resolved_logic3d
    );
  end component;
begin
  
  -- Generated from instantiation at /tmp/vamos_fx/readable/nvc/_norm.sv:34
  u: entity work.pad_sp__c3c7
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

-- Generated from Verilog module wrap_b (/tmp/vamos_fx/readable/nvc/_norm.sv:28)
entity wrap_b__76da is
  port (
    a : in logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of wrap_b__76da : entity is "/tmp/vamos_fx/readable/nvc/_norm.sv:28";
end entity; 

-- Generated from Verilog module wrap_b (/tmp/vamos_fx/readable/nvc/_norm.sv:28)
architecture from_verilog of wrap_b__76da is
  
  component src is
    port (
      a : in logic3d;
      vo : out logic3d
    );
  end component;
  signal vo_Readable : logic3d := L3D_X;  -- Needed to connect outputs
begin
  process (all) is

  begin
    y <= vo_Readable;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/readable/nvc/_norm.sv:29
  u1: entity work.src__a4e7
    port map (
      a => a,
      vo => vo_Readable
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/readable/nvc/_norm.sv:30
  u2: entity work.src__a4e7
    port map (
      a => a,
      vo => y
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

-- Generated from Verilog module src2 (/tmp/vamos_fx/readable/nvc/_norm.sv:51)
entity src2__0fa0 is
  port (
    a : in logic3d;
    q : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of src2__0fa0 : entity is "/tmp/vamos_fx/readable/nvc/_norm.sv:51";
end entity; 

-- Generated from Verilog module src2 (/tmp/vamos_fx/readable/nvc/_norm.sv:51)
architecture from_verilog of src2__0fa0 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:54
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:54
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => q,
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

-- Generated from Verilog module wrap_a (/tmp/vamos_fx/readable/nvc/_norm.sv:23)
entity wrap_a__a8b4 is
  port (
    a : in logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of wrap_a__a8b4 : entity is "/tmp/vamos_fx/readable/nvc/_norm.sv:23";
end entity; 

-- Generated from Verilog module wrap_a (/tmp/vamos_fx/readable/nvc/_norm.sv:23)
architecture from_verilog of wrap_a__a8b4 is
  
  component src is
    port (
      a : in logic3d;
      vo : out logic3d
    );
  end component;
  signal vo_Readable : logic3d := L3D_X;  -- Needed to connect outputs
  
  component src2 is
    port (
      a : in logic3d;
      q : out logic3d
    );
  end component;
  signal q_Readable : logic3d := L3D_X;  -- Needed to connect outputs
begin
  process (all) is

  begin
    y <= vo_Readable;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/readable/nvc/_norm.sv:24
  u1: entity work.src__a4e7
    port map (
      a => a,
      vo => vo_Readable
    );
  process (all) is

  begin
    y <= q_Readable;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/readable/nvc/_norm.sv:25
  u2: entity work.src2__0fa0
    port map (
      a => a,
      q => q_Readable
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

-- Generated from Verilog module sink (/tmp/vamos_fx/readable/nvc/_norm.sv:58)
entity sink__0fa0 is
  port (
    a : in logic3d;
    q : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of sink__0fa0 : entity is "/tmp/vamos_fx/readable/nvc/_norm.sv:58";
end entity; 

-- Generated from Verilog module sink (/tmp/vamos_fx/readable/nvc/_norm.sv:58)
architecture from_verilog of sink__0fa0 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:61
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/readable/nvc/_norm.sv:61
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => q,
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

-- Generated from Verilog module bgadc (/tmp/vamos_fx/readable/nvc/_norm.sv:37)
entity bgadc__64fc is
  port (
    clk : in logic3d;
    vref : out logic3d;
    q : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of bgadc__64fc : entity is "/tmp/vamos_fx/readable/nvc/_norm.sv:37";
end entity; 

-- Generated from Verilog module bgadc (/tmp/vamos_fx/readable/nvc/_norm.sv:37)
architecture from_verilog of bgadc__64fc is
  
  component bg is
    port (
      vref : out logic3d
    );
  end component;
  signal vref_Readable : logic3d := L3D_X;  -- Needed to connect outputs
  
  component adc is
    port (
      vin : in logic3d;
      clk : in logic3d;
      q : out logic3d
    );
  end component;
  signal q_Readable : logic3d := L3D_X;  -- Needed to connect outputs
  signal vin_Readable : logic3d := L3D_X;  -- Needed to connect outputs
begin
  
  -- Generated from instantiation at /tmp/vamos_fx/readable/nvc/_norm.sv:38
  u1: entity work.bg__b4b2
    port map (
      vref => vref_Readable
    );
  process (all) is

  begin
    q <= q_Readable;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/readable/nvc/_norm.sv:39
  u2: entity work.adc__aa94
    port map (
      clk => clk,
      q => q_Readable,
      vin => vin_Readable
    );
  
  comb_fused_0: process (vref_Readable) is
  begin
    vref := vref_Readable;
    vin_Readable := vref;
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

-- Generated from Verilog module tb (/tmp/vamos_fx/readable/nvc/_norm.sv:6)
entity tb is
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of tb : entity is "/tmp/vamos_fx/readable/nvc/_norm.sv:6";
end entity; 

-- Generated from Verilog module tb (/tmp/vamos_fx/readable/nvc/_norm.sv:6)
architecture from_verilog of tb is
  signal clk : logic3d := L3D_0;  -- Declared at /tmp/vamos_fx/readable/nvc/_norm.sv:7
  signal q : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/readable/nvc/_norm.sv:9
  signal qe : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/readable/nvc/_norm.sv:9
  signal va : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/readable/nvc/_norm.sv:9
  signal vb : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/readable/nvc/_norm.sv:9
  signal vc : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/readable/nvc/_norm.sv:9
  signal ve : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/readable/nvc/_norm.sv:9
  signal vref : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/readable/nvc/_norm.sv:9
  
  component bgadc is
    port (
      clk : in logic3d;
      vref : out logic3d;
      q : out logic3d
    );
  end component;
  
  component sink is
    port (
      a : in logic3d;
      q : out logic3d
    );
  end component;
  
  component wrap_a is
    port (
      a : in logic3d;
      y : out logic3d
    );
  end component;
  
  component wrap_b is
    port (
      a : in logic3d;
      y : out logic3d
    );
  end component;
  
  component wrap_c is
    port (
      p : inout resolved_logic3d
    );
  end component;
  
  component wrap_e is
    port (
      a : in logic3d;
      y : out logic3d
    );
  end component;
begin
  
  -- Generated from instantiation at /tmp/vamos_fx/readable/nvc/_norm.sv:15
  bg_inst: entity work.bgadc__64fc
    port map (
      clk => clk,
      q => q,
      vref => vref
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/readable/nvc/_norm.sv:11
  ue: entity work.sink__0fa0
    port map (
      a => ve,
      q => qe
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/readable/nvc/_norm.sv:12
  wa: entity work.wrap_a__a8b4
    port map (
      a => clk,
      y => va
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/readable/nvc/_norm.sv:13
  wb: entity work.wrap_b__76da
    port map (
      a => clk,
      y => vb
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/readable/nvc/_norm.sv:14
  wc: entity work.wrap_c__c0ef
    port map (
      p => vc
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/readable/nvc/_norm.sv:10
  we: entity work.wrap_e__66ef
    port map (
      a => clk,
      y => ve
    );
  
  -- Generated from always process in tb (/tmp/vamos_fx/readable/nvc/_norm.sv:8)
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
  
  -- Generated from always process in tb (/tmp/vamos_fx/readable/nvc/_norm.sv:16)
  process (q, vc, vb, va, qe) is
  begin
    sv_display_line("" & sv_tstr((now / (1000 ps)), -9, -12) & " " & sv_bstr(to_std_logic_vector(qe)) & " " & sv_bstr(to_std_logic_vector(va)) & " " & sv_bstr(to_std_logic_vector(vb)) & " " & sv_bstr(to_std_logic_vector(vc)) & " " & sv_bstr(to_std_logic_vector(q)));
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

-- Generated from Verilog module wrap_a (/tmp/vamos_fx/readable/nvc/_norm.sv:23)
entity wrap_a is
  port (
    a : in logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of wrap_a : entity is "/tmp/vamos_fx/readable/nvc/_norm.sv:23";
end entity; 

-- Generated from Verilog module wrap_a (/tmp/vamos_fx/readable/nvc/_norm.sv:23)
architecture from_verilog of wrap_a is
  
  component src is
    port (
      a : in logic3d;
      vo : out logic3d
    );
  end component;
  signal vo_Readable : logic3d := L3D_X;  -- Needed to connect outputs
  
  component src2 is
    port (
      a : in logic3d;
      q : out logic3d
    );
  end component;
  signal q_Readable : logic3d := L3D_X;  -- Needed to connect outputs
begin
  process (all) is

  begin
    y <= vo_Readable;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/readable/nvc/_norm.sv:24
  u1: entity work.src__a4e7
    port map (
      a => a,
      vo => vo_Readable
    );
  process (all) is

  begin
    y <= q_Readable;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/readable/nvc/_norm.sv:25
  u2: entity work.src2__0fa0
    port map (
      a => a,
      q => q_Readable
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

-- Generated from Verilog module wrap_b (/tmp/vamos_fx/readable/nvc/_norm.sv:28)
entity wrap_b is
  port (
    a : in logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of wrap_b : entity is "/tmp/vamos_fx/readable/nvc/_norm.sv:28";
end entity; 

-- Generated from Verilog module wrap_b (/tmp/vamos_fx/readable/nvc/_norm.sv:28)
architecture from_verilog of wrap_b is
  
  component src is
    port (
      a : in logic3d;
      vo : out logic3d
    );
  end component;
  signal vo_Readable : logic3d := L3D_X;  -- Needed to connect outputs
begin
  process (all) is

  begin
    y <= vo_Readable;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/readable/nvc/_norm.sv:29
  u1: entity work.src__a4e7
    port map (
      a => a,
      vo => vo_Readable
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/readable/nvc/_norm.sv:30
  u2: entity work.src__a4e7
    port map (
      a => a,
      vo => y
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

-- Generated from Verilog module wrap_c (/tmp/vamos_fx/readable/nvc/_norm.sv:33)
entity wrap_c is
  port (
    p : inout resolved_logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of wrap_c : entity is "/tmp/vamos_fx/readable/nvc/_norm.sv:33";
end entity; 

-- Generated from Verilog module wrap_c (/tmp/vamos_fx/readable/nvc/_norm.sv:33)
architecture from_verilog of wrap_c is
  
  component pad_sp is
    port (
      pad : inout resolved_logic3d
    );
  end component;
begin
  
  -- Generated from instantiation at /tmp/vamos_fx/readable/nvc/_norm.sv:34
  u: entity work.pad_sp__c3c7
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

-- Generated from Verilog module wrap_e (/tmp/vamos_fx/readable/nvc/_norm.sv:19)
entity wrap_e is
  port (
    a : in logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of wrap_e : entity is "/tmp/vamos_fx/readable/nvc/_norm.sv:19";
end entity; 

-- Generated from Verilog module wrap_e (/tmp/vamos_fx/readable/nvc/_norm.sv:19)
architecture from_verilog of wrap_e is
  
  component src is
    port (
      a : in logic3d;
      vo : out logic3d
    );
  end component;
  signal vo_Readable : logic3d := L3D_X;  -- Needed to connect outputs
begin
  process (all) is

  begin
    y <= vo_Readable;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/readable/nvc/_norm.sv:20
  u: entity work.src__a4e7
    port map (
      a => a,
      vo => vo_Readable
    );
end architecture;



