library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library sv2vhdl;
use sv2vhdl.sv_display_pkg.all;
use sv2vhdl.sv_strength_pkg.all;
use sv2vhdl.logic3d_types_pkg.all;
use sv2vhdl.sv_analog_pkg.all;
use sv2vhdl.sv_math_pkg.all;

-- Generated from Verilog module flash (/tmp/vamos_fx/dac/nvc/_norm.sv:22)
--   N = 4
entity flash is
  port (
    clk : in logic3d;
    q : out logic3d_vector(3 downto 0)
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of flash : entity is "/tmp/vamos_fx/dac/nvc/_norm.sv:22";
  attribute nvc_verilog_params : string;
  attribute nvc_verilog_params of flash : entity is "N=4";
end entity; 

-- Generated from Verilog module flash (/tmp/vamos_fx/dac/nvc/_norm.sv:22)
--   N = 4
architecture from_verilog of flash is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_4 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_6 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_0_vamos_ams_g_q0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_2_vamos_ams_g_q0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_0_vamos_ams_g_q1 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_2_vamos_ams_g_q1 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_0_vamos_ams_g_q2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_2_vamos_ams_g_q2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_0_vamos_ams_g_q3 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_2_vamos_ams_g_q3 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
begin
  process (all) is

  begin
    q <= tmp_ivl_6 & tmp_ivl_4 & tmp_ivl_2 & tmp_ivl_0;
  end process;
  
  sv_bufif1_vamos_ams_hiz_q_0_vamos_ams_hiz_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_0,
      data => tmp_ivl_0_vamos_ams_g_q0,
      ctrl => tmp_ivl_2_vamos_ams_g_q0
    );
  
  sv_bufif1_vamos_ams_hiz_q_1_vamos_ams_hiz_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_2,
      data => tmp_ivl_0_vamos_ams_g_q1,
      ctrl => tmp_ivl_2_vamos_ams_g_q1
    );
  
  sv_bufif1_vamos_ams_hiz_q_2_vamos_ams_hiz_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_4,
      data => tmp_ivl_0_vamos_ams_g_q2,
      ctrl => tmp_ivl_2_vamos_ams_g_q2
    );
  
  sv_bufif1_vamos_ams_hiz_q_3_vamos_ams_hiz_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_6,
      data => tmp_ivl_0_vamos_ams_g_q3,
      ctrl => tmp_ivl_2_vamos_ams_g_q3
    );
  process (all) is

  begin
    tmp_ivl_0_vamos_ams_g_q0 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_2_vamos_ams_g_q0 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_0_vamos_ams_g_q1 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_2_vamos_ams_g_q1 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_0_vamos_ams_g_q2 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_2_vamos_ams_g_q2 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_0_vamos_ams_g_q3 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_2_vamos_ams_g_q3 <= L3D_0;
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

-- Generated from Verilog module pio_sp (/tmp/vamos_fx/dac/nvc/_norm.sv:27)
entity pio_sp is
  port (
    pad : inout resolved_logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of pio_sp : entity is "/tmp/vamos_fx/dac/nvc/_norm.sv:27";
end entity; 

-- Generated from Verilog module pio_sp (/tmp/vamos_fx/dac/nvc/_norm.sv:27)
architecture from_verilog of pio_sp is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:30
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:30
  signal tmp_ivl_4 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:31
  signal tmp_ivl_6 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:31
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => pad,
      data => tmp_ivl_0,
      ctrl => tmp_ivl_2
    );
  
  sv_bufif1_vamos_ams_hiz_1_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => y,
      data => tmp_ivl_4,
      ctrl => tmp_ivl_6
    );
  process (all) is

  begin
    tmp_ivl_0 <= L3D_0;
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

-- Generated from Verilog module pio_sp (/tmp/vamos_fx/dac/nvc/_norm.sv:27)
entity pio_sp1__01a1 is
  port (
    pad : inout resolved_logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of pio_sp1__01a1 : entity is "/tmp/vamos_fx/dac/nvc/_norm.sv:27";
end entity; 

-- Generated from Verilog module pio_sp (/tmp/vamos_fx/dac/nvc/_norm.sv:27)
architecture from_verilog of pio_sp1__01a1 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:30
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:30
  signal tmp_ivl_4 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:31
  signal tmp_ivl_6 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:31
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => pad,
      data => tmp_ivl_0,
      ctrl => tmp_ivl_2
    );
  
  sv_bufif1_vamos_ams_hiz_1_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => y,
      data => tmp_ivl_4,
      ctrl => tmp_ivl_6
    );
  process (all) is

  begin
    tmp_ivl_0 <= L3D_0;
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

-- Generated from Verilog module pio_sp (/tmp/vamos_fx/dac/nvc/_norm.sv:27)
entity pio_sp__efe1 is
  port (
    pad : inout resolved_logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of pio_sp__efe1 : entity is "/tmp/vamos_fx/dac/nvc/_norm.sv:27";
end entity; 

-- Generated from Verilog module pio_sp (/tmp/vamos_fx/dac/nvc/_norm.sv:27)
architecture from_verilog of pio_sp__efe1 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:30
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:30
  signal tmp_ivl_4 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:31
  signal tmp_ivl_6 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:31
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => pad,
      data => tmp_ivl_0,
      ctrl => tmp_ivl_2
    );
  
  sv_bufif1_vamos_ams_hiz_1_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => y,
      data => tmp_ivl_4,
      ctrl => tmp_ivl_6
    );
  process (all) is

  begin
    tmp_ivl_0 <= L3D_0;
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
    pad <= L3D_1;
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

-- Generated from Verilog module flash (/tmp/vamos_fx/dac/nvc/_norm.sv:22)
--   N = 8
entity flash1__8c78 is
  port (
    clk : in logic3d;
    q : out logic3d_vector(7 downto 0)
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of flash1__8c78 : entity is "/tmp/vamos_fx/dac/nvc/_norm.sv:22";
  attribute nvc_verilog_params : string;
  attribute nvc_verilog_params of flash1__8c78 : entity is "N=8";
end entity; 

-- Generated from Verilog module flash (/tmp/vamos_fx/dac/nvc/_norm.sv:22)
--   N = 8
architecture from_verilog of flash1__8c78 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_10 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_12 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_14 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_4 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_6 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_8 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_0_vamos_ams_g_q0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_2_vamos_ams_g_q0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_0_vamos_ams_g_q1 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_2_vamos_ams_g_q1 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_0_vamos_ams_g_q2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_2_vamos_ams_g_q2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_0_vamos_ams_g_q3 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_2_vamos_ams_g_q3 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_0_vamos_ams_g_q4 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_2_vamos_ams_g_q4 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_0_vamos_ams_g_q5 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_2_vamos_ams_g_q5 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_0_vamos_ams_g_q6 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_2_vamos_ams_g_q6 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_0_vamos_ams_g_q7 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_2_vamos_ams_g_q7 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
begin
  process (all) is

  begin
    q <= tmp_ivl_14 & tmp_ivl_12 & tmp_ivl_10 & tmp_ivl_8 & tmp_ivl_6 & tmp_ivl_4 & tmp_ivl_2 & tmp_ivl_0;
  end process;
  
  sv_bufif1_vamos_ams_hiz_q_0_vamos_ams_hiz_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_0,
      data => tmp_ivl_0_vamos_ams_g_q0,
      ctrl => tmp_ivl_2_vamos_ams_g_q0
    );
  
  sv_bufif1_vamos_ams_hiz_q_1_vamos_ams_hiz_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_2,
      data => tmp_ivl_0_vamos_ams_g_q1,
      ctrl => tmp_ivl_2_vamos_ams_g_q1
    );
  
  sv_bufif1_vamos_ams_hiz_q_2_vamos_ams_hiz_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_4,
      data => tmp_ivl_0_vamos_ams_g_q2,
      ctrl => tmp_ivl_2_vamos_ams_g_q2
    );
  
  sv_bufif1_vamos_ams_hiz_q_3_vamos_ams_hiz_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_6,
      data => tmp_ivl_0_vamos_ams_g_q3,
      ctrl => tmp_ivl_2_vamos_ams_g_q3
    );
  
  sv_bufif1_vamos_ams_hiz_q_4_vamos_ams_hiz_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_8,
      data => tmp_ivl_0_vamos_ams_g_q4,
      ctrl => tmp_ivl_2_vamos_ams_g_q4
    );
  
  sv_bufif1_vamos_ams_hiz_q_5_vamos_ams_hiz_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_10,
      data => tmp_ivl_0_vamos_ams_g_q5,
      ctrl => tmp_ivl_2_vamos_ams_g_q5
    );
  
  sv_bufif1_vamos_ams_hiz_q_6_vamos_ams_hiz_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_12,
      data => tmp_ivl_0_vamos_ams_g_q6,
      ctrl => tmp_ivl_2_vamos_ams_g_q6
    );
  
  sv_bufif1_vamos_ams_hiz_q_7_vamos_ams_hiz_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_14,
      data => tmp_ivl_0_vamos_ams_g_q7,
      ctrl => tmp_ivl_2_vamos_ams_g_q7
    );
  process (all) is

  begin
    tmp_ivl_0_vamos_ams_g_q0 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_2_vamos_ams_g_q0 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_0_vamos_ams_g_q1 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_2_vamos_ams_g_q1 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_0_vamos_ams_g_q2 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_2_vamos_ams_g_q2 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_0_vamos_ams_g_q3 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_2_vamos_ams_g_q3 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_0_vamos_ams_g_q4 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_2_vamos_ams_g_q4 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_0_vamos_ams_g_q5 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_2_vamos_ams_g_q5 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_0_vamos_ams_g_q6 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_2_vamos_ams_g_q6 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_0_vamos_ams_g_q7 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_2_vamos_ams_g_q7 <= L3D_0;
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

-- Generated from Verilog module flash (/tmp/vamos_fx/dac/nvc/_norm.sv:22)
--   N = 4
entity flash__24a6 is
  port (
    clk : in logic3d;
    q : out logic3d_vector(3 downto 0)
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of flash__24a6 : entity is "/tmp/vamos_fx/dac/nvc/_norm.sv:22";
  attribute nvc_verilog_params : string;
  attribute nvc_verilog_params of flash__24a6 : entity is "N=4";
end entity; 

-- Generated from Verilog module flash (/tmp/vamos_fx/dac/nvc/_norm.sv:22)
--   N = 4
architecture from_verilog of flash__24a6 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_4 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_6 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_0_vamos_ams_g_q0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_2_vamos_ams_g_q0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_0_vamos_ams_g_q1 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_2_vamos_ams_g_q1 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_0_vamos_ams_g_q2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_2_vamos_ams_g_q2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_0_vamos_ams_g_q3 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
  signal tmp_ivl_2_vamos_ams_g_q3 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/dac/nvc/_norm.sv:23
begin
  process (all) is

  begin
    q <= tmp_ivl_6 & tmp_ivl_4 & tmp_ivl_2 & tmp_ivl_0;
  end process;
  
  sv_bufif1_vamos_ams_hiz_q_0_vamos_ams_hiz_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_0,
      data => tmp_ivl_0_vamos_ams_g_q0,
      ctrl => tmp_ivl_2_vamos_ams_g_q0
    );
  
  sv_bufif1_vamos_ams_hiz_q_1_vamos_ams_hiz_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_2,
      data => tmp_ivl_0_vamos_ams_g_q1,
      ctrl => tmp_ivl_2_vamos_ams_g_q1
    );
  
  sv_bufif1_vamos_ams_hiz_q_2_vamos_ams_hiz_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_4,
      data => tmp_ivl_0_vamos_ams_g_q2,
      ctrl => tmp_ivl_2_vamos_ams_g_q2
    );
  
  sv_bufif1_vamos_ams_hiz_q_3_vamos_ams_hiz_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_6,
      data => tmp_ivl_0_vamos_ams_g_q3,
      ctrl => tmp_ivl_2_vamos_ams_g_q3
    );
  process (all) is

  begin
    tmp_ivl_0_vamos_ams_g_q0 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_2_vamos_ams_g_q0 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_0_vamos_ams_g_q1 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_2_vamos_ams_g_q1 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_0_vamos_ams_g_q2 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_2_vamos_ams_g_q2 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_0_vamos_ams_g_q3 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_2_vamos_ams_g_q3 <= L3D_0;
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

-- Generated from Verilog module tb (/tmp/vamos_fx/dac/nvc/_norm.sv:5)
entity tb is
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of tb : entity is "/tmp/vamos_fx/dac/nvc/_norm.sv:5";
end entity; 

-- Generated from Verilog module tb (/tmp/vamos_fx/dac/nvc/_norm.sv:5)
architecture from_verilog of tb is
  signal clk : logic3d := L3D_0;  -- Declared at /tmp/vamos_fx/dac/nvc/_norm.sv:6
  signal p1 : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/dac/nvc/_norm.sv:9
  signal p2 : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/dac/nvc/_norm.sv:9
  signal pw : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/dac/nvc/_norm.sv:9
  signal q4 : resolved_logic3d_vector(3 downto 0) := (others => L3D_X);  -- Declared at /tmp/vamos_fx/dac/nvc/_norm.sv:7
  signal q8 : resolved_logic3d_vector(7 downto 0) := (others => L3D_X);  -- Declared at /tmp/vamos_fx/dac/nvc/_norm.sv:8
  signal t : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/dac/nvc/_norm.sv:9
  
  component flash is
    port (
      clk : in logic3d;
      q : out logic3d_vector(3 downto 0)
    );
  end component;
  
  component flash1 is
    port (
      clk : in logic3d;
      q : out logic3d_vector(7 downto 0)
    );
  end component;
  
  component pio_sp is
    port (
      pad : inout resolved_logic3d;
      y : out logic3d
    );
  end component;
  
  component pio_sp1 is
    port (
      pad : inout resolved_logic3d;
      y : out logic3d
    );
  end component;
begin
  process (all) is

  begin
    pw <= clk;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/dac/nvc/_norm.sv:13
  f4: entity work.flash__24a6
    port map (
      clk => clk,
      q => q4
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/dac/nvc/_norm.sv:14
  f8: entity work.flash1__8c78
    port map (
      clk => clk,
      q => q8
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/dac/nvc/_norm.sv:15
  u1: entity work.pio_sp__efe1
    port map (
      pad => t,
      y => p1
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/dac/nvc/_norm.sv:16
  u2: entity work.pio_sp1__01a1
    port map (
      pad => pw,
      y => p2
    );
  
  -- Generated from always process in tb (/tmp/vamos_fx/dac/nvc/_norm.sv:12)
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
  
  -- Generated from always process in tb (/tmp/vamos_fx/dac/nvc/_norm.sv:17)
  process (p2, p1, q8, q4) is
  begin
    sv_display_line("" & sv_tstr((now / (1000 ps)), -9, -12) & " " & sv_bstr(to_std_logic_vector(q4)) & " " & sv_bstr(to_std_logic_vector(q8)) & " " & sv_bstr(to_std_logic_vector(p1)) & " " & sv_bstr(to_std_logic_vector(p2)));
  end process;
end architecture;



