library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library sv2vhdl;
use sv2vhdl.sv_display_pkg.all;
use sv2vhdl.sv_strength_pkg.all;
use sv2vhdl.logic3d_types_pkg.all;
use sv2vhdl.sv_analog_pkg.all;
use sv2vhdl.sv_math_pkg.all;

-- Generated from Verilog module cin (/tmp/vamos_fx/portbuf/nvc/_norm.sv:54)
entity cin is
  port (
    a : in logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of cin : entity is "/tmp/vamos_fx/portbuf/nvc/_norm.sv:54";
end entity; 

-- Generated from Verilog module cin (/tmp/vamos_fx/portbuf/nvc/_norm.sv:54)
architecture from_verilog of cin is
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

-- Generated from Verilog module srci (/tmp/vamos_fx/portbuf/nvc/_norm.sv:59)
entity srci is
  port (
    a : inout resolved_logic3d;
    vo : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of srci : entity is "/tmp/vamos_fx/portbuf/nvc/_norm.sv:59";
end entity; 

-- Generated from Verilog module srci (/tmp/vamos_fx/portbuf/nvc/_norm.sv:59)
architecture from_verilog of srci is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:62
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:62
  signal tmp_ivl_4 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:63
  signal tmp_ivl_6 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:63
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => a,
      data => tmp_ivl_0,
      ctrl => tmp_ivl_2
    );
  
  sv_bufif1_vamos_ams_hiz_1_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => vo,
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

-- Generated from Verilog module srco (/tmp/vamos_fx/portbuf/nvc/_norm.sv:77)
entity srco is
  port (
    a : out logic3d;
    vo : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of srco : entity is "/tmp/vamos_fx/portbuf/nvc/_norm.sv:77";
end entity; 

-- Generated from Verilog module srco (/tmp/vamos_fx/portbuf/nvc/_norm.sv:77)
architecture from_verilog of srco is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:80
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:80
  signal tmp_ivl_4 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:81
  signal tmp_ivl_6 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:81
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => a,
      data => tmp_ivl_0,
      ctrl => tmp_ivl_2
    );
  
  sv_bufif1_vamos_ams_hiz_1_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => vo,
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

-- Generated from Verilog module srcv (/tmp/vamos_fx/portbuf/nvc/_norm.sv:67)
entity srcv is
  port (
    a : inout resolved_logic3d_vector(1 downto 0);
    vo : out logic3d_vector(1 downto 0)
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of srcv : entity is "/tmp/vamos_fx/portbuf/nvc/_norm.sv:67";
end entity; 

-- Generated from Verilog module srcv (/tmp/vamos_fx/portbuf/nvc/_norm.sv:67)
architecture from_verilog of srcv is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:70
  signal tmp_ivl_10 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:71
  signal tmp_ivl_12 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:72
  signal tmp_ivl_14 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:72
  signal tmp_ivl_16 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:72
  signal tmp_ivl_18 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:73
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:70
  signal tmp_ivl_20 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:73
  signal tmp_ivl_22 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:73
  signal tmp_ivl_4 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:70
  signal tmp_ivl_6 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:71
  signal tmp_ivl_8 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:71
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_0,
      data => tmp_ivl_2,
      ctrl => tmp_ivl_4
    );
  
  sv_bufif1_vamos_ams_hiz_1_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_6,
      data => tmp_ivl_8,
      ctrl => tmp_ivl_10
    );
  
  sv_bufif1_vamos_ams_hiz_2_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_12,
      data => tmp_ivl_14,
      ctrl => tmp_ivl_16
    );
  
  sv_bufif1_vamos_ams_hiz_3_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_18,
      data => tmp_ivl_20,
      ctrl => tmp_ivl_22
    );
  process (all) is

  begin
    a <= tmp_ivl_0 & tmp_ivl_6;
  end process;
  process (all) is

  begin
    vo <= tmp_ivl_12 & tmp_ivl_18;
  end process;
  process (all) is

  begin
    tmp_ivl_10 <= L3D_0;
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
    tmp_ivl_2 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_20 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_22 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_4 <= L3D_0;
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

-- Generated from Verilog module srcv (/tmp/vamos_fx/portbuf/nvc/_norm.sv:67)
entity srcv__2da9 is
  port (
    a : inout resolved_logic3d_vector(1 downto 0);
    vo : out logic3d_vector(1 downto 0)
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of srcv__2da9 : entity is "/tmp/vamos_fx/portbuf/nvc/_norm.sv:67";
end entity; 

-- Generated from Verilog module srcv (/tmp/vamos_fx/portbuf/nvc/_norm.sv:67)
architecture from_verilog of srcv__2da9 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:70
  signal tmp_ivl_10 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:71
  signal tmp_ivl_12 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:72
  signal tmp_ivl_14 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:72
  signal tmp_ivl_16 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:72
  signal tmp_ivl_18 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:73
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:70
  signal tmp_ivl_20 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:73
  signal tmp_ivl_22 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:73
  signal tmp_ivl_4 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:70
  signal tmp_ivl_6 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:71
  signal tmp_ivl_8 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:71
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_0,
      data => tmp_ivl_2,
      ctrl => tmp_ivl_4
    );
  
  sv_bufif1_vamos_ams_hiz_1_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_6,
      data => tmp_ivl_8,
      ctrl => tmp_ivl_10
    );
  
  sv_bufif1_vamos_ams_hiz_2_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_12,
      data => tmp_ivl_14,
      ctrl => tmp_ivl_16
    );
  
  sv_bufif1_vamos_ams_hiz_3_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_18,
      data => tmp_ivl_20,
      ctrl => tmp_ivl_22
    );
  process (all) is

  begin
    a(1) <= tmp_ivl_0;
  end process;
  process (all) is

  begin
    a(0) <= tmp_ivl_6;
  end process;
  process (all) is

  begin
    vo <= tmp_ivl_12 & tmp_ivl_18;
  end process;
  process (all) is

  begin
    tmp_ivl_10 <= L3D_0;
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
    tmp_ivl_2 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_20 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_22 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_4 <= L3D_0;
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

-- Generated from Verilog module wrap_v (/tmp/vamos_fx/portbuf/nvc/_norm.sv:33)
entity wrap_v__e26a is
  port (
    a : inout resolved_logic3d_vector(1 downto 0);
    y : out logic3d_vector(1 downto 0)
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of wrap_v__e26a : entity is "/tmp/vamos_fx/portbuf/nvc/_norm.sv:33";
end entity; 

-- Generated from Verilog module wrap_v (/tmp/vamos_fx/portbuf/nvc/_norm.sv:33)
architecture from_verilog of wrap_v__e26a is
  
  component srcv is
    port (
      a : inout resolved_logic3d_vector(1 downto 0);
      vo : out logic3d_vector(1 downto 0)
    );
  end component;
  signal vo_Readable : logic3d_vector(1 downto 0) := (others => L3D_X);  -- Needed to connect outputs
begin
  process (all) is

  begin
    y <= vo_Readable;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/portbuf/nvc/_norm.sv:34
  -- Verilog instance: u
  u: entity work.srcv__2da9
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

-- Generated from Verilog module srci (/tmp/vamos_fx/portbuf/nvc/_norm.sv:59)
entity srci__e24b is
  port (
    a : inout resolved_logic3d;
    vo : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of srci__e24b : entity is "/tmp/vamos_fx/portbuf/nvc/_norm.sv:59";
end entity; 

-- Generated from Verilog module srci (/tmp/vamos_fx/portbuf/nvc/_norm.sv:59)
architecture from_verilog of srci__e24b is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:62
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:62
  signal tmp_ivl_4 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:63
  signal tmp_ivl_6 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:63
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => a,
      data => tmp_ivl_0,
      ctrl => tmp_ivl_2
    );
  
  sv_bufif1_vamos_ams_hiz_1_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => vo,
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

-- Generated from Verilog module wrap_r (/tmp/vamos_fx/portbuf/nvc/_norm.sv:37)
entity wrap_r__502b is
  port (
    a : inout resolved_logic3d;
    y : out logic3d;
    q : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of wrap_r__502b : entity is "/tmp/vamos_fx/portbuf/nvc/_norm.sv:37";
end entity; 

-- Generated from Verilog module wrap_r (/tmp/vamos_fx/portbuf/nvc/_norm.sv:37)
architecture from_verilog of wrap_r__502b is
  
  component srci is
    port (
      a : inout resolved_logic3d;
      vo : out logic3d
    );
  end component;
  signal vo_Readable : logic3d := L3D_X;  -- Needed to connect outputs
begin
  process (all) is

  begin
    q <= l3d_not(a);
  end process;
  process (all) is

  begin
    y <= vo_Readable;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/portbuf/nvc/_norm.sv:38
  -- Verilog instance: u
  u: entity work.srci__e24b
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

-- Generated from Verilog module srco (/tmp/vamos_fx/portbuf/nvc/_norm.sv:77)
entity srco__78f7 is
  port (
    a : out logic3d;
    vo : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of srco__78f7 : entity is "/tmp/vamos_fx/portbuf/nvc/_norm.sv:77";
end entity; 

-- Generated from Verilog module srco (/tmp/vamos_fx/portbuf/nvc/_norm.sv:77)
architecture from_verilog of srco__78f7 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:80
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:80
  signal tmp_ivl_4 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:81
  signal tmp_ivl_6 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:81
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => a,
      data => tmp_ivl_0,
      ctrl => tmp_ivl_2
    );
  
  sv_bufif1_vamos_ams_hiz_1_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => vo,
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

-- Generated from Verilog module wrap_o (/tmp/vamos_fx/portbuf/nvc/_norm.sv:42)
entity wrap_o__6972 is
  port (
    a : inout resolved_logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of wrap_o__6972 : entity is "/tmp/vamos_fx/portbuf/nvc/_norm.sv:42";
end entity; 

-- Generated from Verilog module wrap_o (/tmp/vamos_fx/portbuf/nvc/_norm.sv:42)
architecture from_verilog of wrap_o__6972 is
  
  component srco is
    port (
      a : out logic3d;
      vo : out logic3d
    );
  end component;
  signal vo_Readable : logic3d := L3D_X;  -- Needed to connect outputs
begin
  process (all) is

  begin
    y <= vo_Readable;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/portbuf/nvc/_norm.sv:43
  -- Verilog instance: u
  u: entity work.srco__78f7
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

-- Generated from Verilog module wrap_d (/tmp/vamos_fx/portbuf/nvc/_norm.sv:46)
entity wrap_d__5c0d is
  port (
    a : inout resolved_logic3d;
    en : in logic3d;
    y : out logic3d;
    q : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of wrap_d__5c0d : entity is "/tmp/vamos_fx/portbuf/nvc/_norm.sv:46";
end entity; 

-- Generated from Verilog module wrap_d (/tmp/vamos_fx/portbuf/nvc/_norm.sv:46)
architecture from_verilog of wrap_d__5c0d is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:48
  
  component srci is
    port (
      a : inout resolved_logic3d;
      vo : out logic3d
    );
  end component;
  signal vo_Readable : logic3d := L3D_X;  -- Needed to connect outputs
begin
  
  sv_bufif1_b_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => a,
      data => tmp_ivl_0,
      ctrl => en
    );
  process (all) is

  begin
    q <= a;
  end process;
  process (all) is

  begin
    y <= vo_Readable;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/portbuf/nvc/_norm.sv:47
  -- Verilog instance: u
  u: entity work.srci__e24b
    port map (
      a => a,
      vo => vo_Readable
    );
  process (all) is

  begin
    tmp_ivl_0 <= L3D_0;
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

-- Generated from Verilog module wrap_e (/tmp/vamos_fx/portbuf/nvc/_norm.sv:29)
entity wrap_e__8507 is
  port (
    a : inout resolved_logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of wrap_e__8507 : entity is "/tmp/vamos_fx/portbuf/nvc/_norm.sv:29";
end entity; 

-- Generated from Verilog module wrap_e (/tmp/vamos_fx/portbuf/nvc/_norm.sv:29)
architecture from_verilog of wrap_e__8507 is
  
  component srci is
    port (
      a : inout resolved_logic3d;
      vo : out logic3d
    );
  end component;
  signal vo_Readable : logic3d := L3D_X;  -- Needed to connect outputs
begin
  process (all) is

  begin
    y <= vo_Readable;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/portbuf/nvc/_norm.sv:30
  -- Verilog instance: u
  u: entity work.srci__e24b
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

-- Generated from Verilog module cin (/tmp/vamos_fx/portbuf/nvc/_norm.sv:54)
entity cin__4e0c is
  port (
    a : in logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of cin__4e0c : entity is "/tmp/vamos_fx/portbuf/nvc/_norm.sv:54";
end entity; 

-- Generated from Verilog module cin (/tmp/vamos_fx/portbuf/nvc/_norm.sv:54)
architecture from_verilog of cin__4e0c is
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

-- Generated from Verilog module tb (/tmp/vamos_fx/portbuf/nvc/_norm.sv:10)
entity tb is
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of tb : entity is "/tmp/vamos_fx/portbuf/nvc/_norm.sv:10";
end entity; 

-- Generated from Verilog module tb (/tmp/vamos_fx/portbuf/nvc/_norm.sv:10)
architecture from_verilog of tb is
  signal tmp_ivl_1 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:19
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:20
  signal clk : logic3d := L3D_0;  -- Declared at /tmp/vamos_fx/portbuf/nvc/_norm.sv:11
  signal qd : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/portbuf/nvc/_norm.sv:15
  signal qr : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/portbuf/nvc/_norm.sv:15
  signal rv : logic3d_vector(1 downto 0) := logic3d_vector'(L3D_0, L3D_1);  -- Declared at /tmp/vamos_fx/portbuf/nvc/_norm.sv:12
  signal vb : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/portbuf/nvc/_norm.sv:15
  signal vd : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/portbuf/nvc/_norm.sv:15
  signal ve : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/portbuf/nvc/_norm.sv:15
  signal vo : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/portbuf/nvc/_norm.sv:15
  signal vr : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/portbuf/nvc/_norm.sv:15
  signal vv : resolved_logic3d_vector(1 downto 0) := (others => L3D_X);  -- Declared at /tmp/vamos_fx/portbuf/nvc/_norm.sv:16
  signal vx : resolved_logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/portbuf/nvc/_norm.sv:15
  signal LPM_q_ivl_4 : logic3d := L3D_X;
  
  component cin is
    port (
      a : in logic3d
    );
  end component;
  
  component wrap_e is
    port (
      a : inout resolved_logic3d;
      y : out logic3d
    );
  end component;
  signal PB_wb_a : resolved_logic3d := L3D_X;  -- Port buffer of input a of instance wb (its net is also driven inside)
  
  component wrap_d is
    port (
      a : inout resolved_logic3d;
      en : in logic3d;
      y : out logic3d;
      q : out logic3d
    );
  end component;
  signal PB_wd_a : resolved_logic3d := L3D_X;  -- Port buffer of input a of instance wd (its net is also driven inside)
  signal PB_we_a : resolved_logic3d := L3D_X;  -- Port buffer of input a of instance we (its net is also driven inside)
  
  component wrap_o is
    port (
      a : inout resolved_logic3d;
      y : out logic3d
    );
  end component;
  signal PB_wo_a : resolved_logic3d := L3D_X;  -- Port buffer of input a of instance wo (its net is also driven inside)
  
  component wrap_r is
    port (
      a : inout resolved_logic3d;
      y : out logic3d;
      q : out logic3d
    );
  end component;
  signal PB_wr_a : resolved_logic3d := L3D_X;  -- Port buffer of input a of instance wr (its net is also driven inside)
  
  component wrap_v is
    port (
      a : inout resolved_logic3d_vector(1 downto 0);
      y : out logic3d_vector(1 downto 0)
    );
  end component;
  signal PB_wv_a : resolved_logic3d_vector(1 downto 0) := (others => L3D_X);  -- Port buffer of input a of instance wv (its net is also driven inside)
  signal PB_wx_a : resolved_logic3d := L3D_X;  -- Port buffer of input a of instance wx (its net is also driven inside)
  
  function Verilog_Time_Field(S : string; W : natural; Z : Boolean) return string is
    variable F : integer := S'low;
  begin
    while F < S'high and S(F) = ' ' loop
      F := F + 1;
    end loop;
    if S'high - F + 1 >= W then
      return S(F to S'high);
    elsif Z then
      return (1 to W - (S'high - F + 1) => '0') & S(F to S'high);
    else
      return (1 to W - (S'high - F + 1) => ' ') & S(F to S'high);
    end if;
  end function;
begin
  process (all) is

  begin
    tmp_ivl_2 <= l3d_not(clk);
  end process;
  process (all) is

  begin
    tmp_ivl_1 <= rv(1);
  end process;
  process (all) is

  begin
    LPM_q_ivl_4 <= rv(0);
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/portbuf/nvc/_norm.sv:17
  -- Verilog instance: c1
  c1: entity work.cin__4e0c
    port map (
      a => clk
    );
  process (all) is

  begin
    PB_wb_a <= tmp_ivl_1;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/portbuf/nvc/_norm.sv:19
  -- Verilog instance: wb
  wb: entity work.wrap_e__8507
    port map (
      a => PB_wb_a,
      y => vb
    );
  process (all) is

  begin
    PB_wd_a <= clk;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/portbuf/nvc/_norm.sv:24
  -- Verilog instance: wd
  wd: entity work.wrap_d__5c0d
    port map (
      a => PB_wd_a,
      en => LPM_q_ivl_4,
      q => qd,
      y => vd
    );
  process (all) is

  begin
    PB_we_a <= clk;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/portbuf/nvc/_norm.sv:18
  -- Verilog instance: we
  we: entity work.wrap_e__8507
    port map (
      a => PB_we_a,
      y => ve
    );
  process (all) is

  begin
    PB_wo_a <= clk;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/portbuf/nvc/_norm.sv:23
  -- Verilog instance: wo
  wo: entity work.wrap_o__6972
    port map (
      a => PB_wo_a,
      y => vo
    );
  process (all) is

  begin
    PB_wr_a <= clk;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/portbuf/nvc/_norm.sv:22
  -- Verilog instance: wr
  wr: entity work.wrap_r__502b
    port map (
      a => PB_wr_a,
      q => qr,
      y => vr
    );
  process (all) is

  begin
    PB_wv_a <= rv;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/portbuf/nvc/_norm.sv:21
  -- Verilog instance: wv
  wv: entity work.wrap_v__e26a
    port map (
      a => PB_wv_a,
      y => vv
    );
  process (all) is

  begin
    PB_wx_a <= tmp_ivl_2;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/portbuf/nvc/_norm.sv:20
  -- Verilog instance: wx
  wx: entity work.wrap_e__8507
    port map (
      a => PB_wx_a,
      y => vx
    );
  
  -- Generated from always process in tb (/tmp/vamos_fx/portbuf/nvc/_norm.sv:13)
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
  
  -- Generated from always process in tb (/tmp/vamos_fx/portbuf/nvc/_norm.sv:14)
  process is
    variable v_rv : logic3d_vector(1 downto 0);
  begin
    v_rv := rv;
    loop
      wait for 20000 ps;
      v_rv := v_rv + logic3d_vector'(L3D_0, L3D_1);
      rv <= v_rv;
    end loop;
  end process;
  
  -- Generated from always process in tb (/tmp/vamos_fx/portbuf/nvc/_norm.sv:25)
  process (qd, vd, vo, qr, vr, vv, vx, vb, ve) is
  begin
    sv_display_line("" & Verilog_Time_Field(sv_tstr((now / (1000 ps)), -9, -12), 0, False) & " " & sv_bstr(to_std_logic_vector(ve)) & " " & sv_bstr(to_std_logic_vector(vb)) & " " & sv_bstr(to_std_logic_vector(vx)) & " " & sv_bstr(to_std_logic_vector(vv)) & " " & sv_bstr(to_std_logic_vector(vr)) & " " & sv_bstr(to_std_logic_vector(qr)) & " " & sv_bstr(to_std_logic_vector(vo)) & " " & sv_bstr(to_std_logic_vector(vd)) & " " & sv_bstr(to_std_logic_vector(qd)));
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

-- Generated from Verilog module wrap_d (/tmp/vamos_fx/portbuf/nvc/_norm.sv:46)
entity wrap_d is
  port (
    a : inout resolved_logic3d;
    en : in logic3d;
    y : out logic3d;
    q : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of wrap_d : entity is "/tmp/vamos_fx/portbuf/nvc/_norm.sv:46";
end entity; 

-- Generated from Verilog module wrap_d (/tmp/vamos_fx/portbuf/nvc/_norm.sv:46)
architecture from_verilog of wrap_d is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:48
  
  component srci is
    port (
      a : inout resolved_logic3d;
      vo : out logic3d
    );
  end component;
  signal vo_Readable : logic3d := L3D_X;  -- Needed to connect outputs
begin
  
  sv_bufif1_b_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => a,
      data => tmp_ivl_0,
      ctrl => en
    );
  process (all) is

  begin
    q <= a;
  end process;
  process (all) is

  begin
    y <= vo_Readable;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/portbuf/nvc/_norm.sv:47
  -- Verilog instance: u
  u: entity work.srci__e24b
    port map (
      a => a,
      vo => vo_Readable
    );
  process (all) is

  begin
    tmp_ivl_0 <= L3D_0;
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

-- Generated from Verilog module wrap_e (/tmp/vamos_fx/portbuf/nvc/_norm.sv:29)
entity wrap_e is
  port (
    a : inout resolved_logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of wrap_e : entity is "/tmp/vamos_fx/portbuf/nvc/_norm.sv:29";
end entity; 

-- Generated from Verilog module wrap_e (/tmp/vamos_fx/portbuf/nvc/_norm.sv:29)
architecture from_verilog of wrap_e is
  
  component srci is
    port (
      a : inout resolved_logic3d;
      vo : out logic3d
    );
  end component;
  signal vo_Readable : logic3d := L3D_X;  -- Needed to connect outputs
begin
  process (all) is

  begin
    y <= vo_Readable;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/portbuf/nvc/_norm.sv:30
  -- Verilog instance: u
  u: entity work.srci__e24b
    port map (
      a => a,
      vo => vo_Readable
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

-- Generated from Verilog module wrap_o (/tmp/vamos_fx/portbuf/nvc/_norm.sv:42)
entity wrap_o is
  port (
    a : inout resolved_logic3d;
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of wrap_o : entity is "/tmp/vamos_fx/portbuf/nvc/_norm.sv:42";
end entity; 

-- Generated from Verilog module wrap_o (/tmp/vamos_fx/portbuf/nvc/_norm.sv:42)
architecture from_verilog of wrap_o is
  
  component srco is
    port (
      a : out logic3d;
      vo : out logic3d
    );
  end component;
  signal vo_Readable : logic3d := L3D_X;  -- Needed to connect outputs
begin
  process (all) is

  begin
    y <= vo_Readable;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/portbuf/nvc/_norm.sv:43
  -- Verilog instance: u
  u: entity work.srco__78f7
    port map (
      a => a,
      vo => vo_Readable
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

-- Generated from Verilog module wrap_r (/tmp/vamos_fx/portbuf/nvc/_norm.sv:37)
entity wrap_r is
  port (
    a : inout resolved_logic3d;
    y : out logic3d;
    q : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of wrap_r : entity is "/tmp/vamos_fx/portbuf/nvc/_norm.sv:37";
end entity; 

-- Generated from Verilog module wrap_r (/tmp/vamos_fx/portbuf/nvc/_norm.sv:37)
architecture from_verilog of wrap_r is
  
  component srci is
    port (
      a : inout resolved_logic3d;
      vo : out logic3d
    );
  end component;
  signal vo_Readable : logic3d := L3D_X;  -- Needed to connect outputs
begin
  process (all) is

  begin
    q <= l3d_not(a);
  end process;
  process (all) is

  begin
    y <= vo_Readable;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/portbuf/nvc/_norm.sv:38
  -- Verilog instance: u
  u: entity work.srci__e24b
    port map (
      a => a,
      vo => vo_Readable
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

-- Generated from Verilog module srcv (/tmp/vamos_fx/portbuf/nvc/_norm.sv:67)
entity srcv__6e11 is
  port (
    a : inout resolved_logic3d_vector(1 downto 0);
    vo : out logic3d_vector(1 downto 0)
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of srcv__6e11 : entity is "/tmp/vamos_fx/portbuf/nvc/_norm.sv:67";
end entity; 

-- Generated from Verilog module srcv (/tmp/vamos_fx/portbuf/nvc/_norm.sv:67)
architecture from_verilog of srcv__6e11 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:70
  signal tmp_ivl_10 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:71
  signal tmp_ivl_12 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:72
  signal tmp_ivl_14 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:72
  signal tmp_ivl_16 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:72
  signal tmp_ivl_18 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:73
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:70
  signal tmp_ivl_20 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:73
  signal tmp_ivl_22 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:73
  signal tmp_ivl_4 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:70
  signal tmp_ivl_6 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:71
  signal tmp_ivl_8 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/portbuf/nvc/_norm.sv:71
begin
  
  sv_bufif1_vamos_ams_hiz_0_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_0,
      data => tmp_ivl_2,
      ctrl => tmp_ivl_4
    );
  
  sv_bufif1_vamos_ams_hiz_1_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_6,
      data => tmp_ivl_8,
      ctrl => tmp_ivl_10
    );
  
  sv_bufif1_vamos_ams_hiz_2_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_12,
      data => tmp_ivl_14,
      ctrl => tmp_ivl_16
    );
  
  sv_bufif1_vamos_ams_hiz_3_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => tmp_ivl_18,
      data => tmp_ivl_20,
      ctrl => tmp_ivl_22
    );
  process (all) is

  begin
    a <= tmp_ivl_0 & tmp_ivl_6;
  end process;
  process (all) is

  begin
    vo <= tmp_ivl_12 & tmp_ivl_18;
  end process;
  process (all) is

  begin
    tmp_ivl_10 <= L3D_0;
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
    tmp_ivl_2 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_20 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_22 <= L3D_0;
  end process;
  process (all) is

  begin
    tmp_ivl_4 <= L3D_0;
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

-- Generated from Verilog module wrap_v (/tmp/vamos_fx/portbuf/nvc/_norm.sv:33)
entity wrap_v is
  port (
    a : inout resolved_logic3d_vector(1 downto 0);
    y : out logic3d_vector(1 downto 0)
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of wrap_v : entity is "/tmp/vamos_fx/portbuf/nvc/_norm.sv:33";
end entity; 

-- Generated from Verilog module wrap_v (/tmp/vamos_fx/portbuf/nvc/_norm.sv:33)
architecture from_verilog of wrap_v is
  
  component srcv is
    port (
      a : inout resolved_logic3d_vector(1 downto 0);
      vo : out logic3d_vector(1 downto 0)
    );
  end component;
  signal vo_Readable : logic3d_vector(1 downto 0) := (others => L3D_X);  -- Needed to connect outputs
begin
  process (all) is

  begin
    y <= vo_Readable;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/portbuf/nvc/_norm.sv:34
  -- Verilog instance: u
  u: entity work.srcv__6e11
    port map (
      a => a,
      vo => vo_Readable
    );
end architecture;



