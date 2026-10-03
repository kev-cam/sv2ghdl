library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library sv2vhdl;
use sv2vhdl.sv_display_pkg.all;
use sv2vhdl.sv_strength_pkg.all;
use sv2vhdl.logic3d_types_pkg.all;
use sv2vhdl.sv_analog_pkg.all;
use sv2vhdl.sv_math_pkg.all;

-- Generated from Verilog module cin (/tmp/vamos_fx/temps/nvc/_norm.sv:23)
entity cin is
  port (
    a : in logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of cin : entity is "/tmp/vamos_fx/temps/nvc/_norm.sv:23";
end entity; 

-- Generated from Verilog module cin (/tmp/vamos_fx/temps/nvc/_norm.sv:23)
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

-- Generated from Verilog module cin2 (/tmp/vamos_fx/temps/nvc/_norm.sv:28)
entity cin2 is
  port (
    d : in logic3d_vector(1 downto 0)
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of cin2 : entity is "/tmp/vamos_fx/temps/nvc/_norm.sv:28";
end entity; 

-- Generated from Verilog module cin2 (/tmp/vamos_fx/temps/nvc/_norm.sv:28)
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

-- Generated from Verilog module cout (/tmp/vamos_fx/temps/nvc/_norm.sv:33)
entity cout is
  port (
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of cout : entity is "/tmp/vamos_fx/temps/nvc/_norm.sv:33";
end entity; 

-- Generated from Verilog module cout (/tmp/vamos_fx/temps/nvc/_norm.sv:33)
architecture from_verilog of cout is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/temps/nvc/_norm.sv:35
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/temps/nvc/_norm.sv:35
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

-- Generated from Verilog module cout2 (/tmp/vamos_fx/temps/nvc/_norm.sv:39)
entity cout2 is
  port (
    y : out logic3d_vector(1 downto 0)
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of cout2 : entity is "/tmp/vamos_fx/temps/nvc/_norm.sv:39";
end entity; 

-- Generated from Verilog module cout2 (/tmp/vamos_fx/temps/nvc/_norm.sv:39)
architecture from_verilog of cout2 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/temps/nvc/_norm.sv:41
  signal tmp_ivl_10 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/temps/nvc/_norm.sv:42
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/temps/nvc/_norm.sv:41
  signal tmp_ivl_4 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/temps/nvc/_norm.sv:41
  signal tmp_ivl_6 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/temps/nvc/_norm.sv:42
  signal tmp_ivl_8 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/temps/nvc/_norm.sv:42
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
  process (all) is

  begin
    y <= tmp_ivl_0 & tmp_ivl_6;
  end process;
  process (all) is

  begin
    tmp_ivl_10 <= L3D_0;
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

-- Generated from Verilog module cout2 (/tmp/vamos_fx/temps/nvc/_norm.sv:39)
entity cout2__ddf6 is
  port (
    y : out logic3d_vector(1 downto 0)
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of cout2__ddf6 : entity is "/tmp/vamos_fx/temps/nvc/_norm.sv:39";
end entity; 

-- Generated from Verilog module cout2 (/tmp/vamos_fx/temps/nvc/_norm.sv:39)
architecture from_verilog of cout2__ddf6 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/temps/nvc/_norm.sv:41
  signal tmp_ivl_10 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/temps/nvc/_norm.sv:42
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/temps/nvc/_norm.sv:41
  signal tmp_ivl_4 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/temps/nvc/_norm.sv:41
  signal tmp_ivl_6 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/temps/nvc/_norm.sv:42
  signal tmp_ivl_8 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/temps/nvc/_norm.sv:42
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
  process (all) is

  begin
    y <= tmp_ivl_0 & tmp_ivl_6;
  end process;
  process (all) is

  begin
    tmp_ivl_10 <= L3D_0;
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

-- Generated from Verilog module cout (/tmp/vamos_fx/temps/nvc/_norm.sv:33)
entity cout__1760 is
  port (
    y : out logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of cout__1760 : entity is "/tmp/vamos_fx/temps/nvc/_norm.sv:33";
end entity; 

-- Generated from Verilog module cout (/tmp/vamos_fx/temps/nvc/_norm.sv:33)
architecture from_verilog of cout__1760 is
  signal tmp_ivl_0 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/temps/nvc/_norm.sv:35
  signal tmp_ivl_2 : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/temps/nvc/_norm.sv:35
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

-- Generated from Verilog module cin (/tmp/vamos_fx/temps/nvc/_norm.sv:23)
entity cin__4e0c is
  port (
    a : in logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of cin__4e0c : entity is "/tmp/vamos_fx/temps/nvc/_norm.sv:23";
end entity; 

-- Generated from Verilog module cin (/tmp/vamos_fx/temps/nvc/_norm.sv:23)
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

-- Generated from Verilog module cin2 (/tmp/vamos_fx/temps/nvc/_norm.sv:28)
entity cin2__9a93 is
  port (
    d : in logic3d_vector(1 downto 0)
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of cin2__9a93 : entity is "/tmp/vamos_fx/temps/nvc/_norm.sv:28";
end entity; 

-- Generated from Verilog module cin2 (/tmp/vamos_fx/temps/nvc/_norm.sv:28)
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

-- Generated from Verilog module tb (/tmp/vamos_fx/temps/nvc/_norm.sv:5)
entity tb is
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of tb : entity is "/tmp/vamos_fx/temps/nvc/_norm.sv:5";
end entity; 

-- Generated from Verilog module tb (/tmp/vamos_fx/temps/nvc/_norm.sv:5)
architecture from_verilog of tb is
  signal gnd : logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/temps/nvc/_norm.sv:7
  signal v : logic3d_vector(1 downto 0) := (others => L3D_X);  -- Declared at /tmp/vamos_fx/temps/nvc/_norm.sv:9
  signal vdd : logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/temps/nvc/_norm.sv:8
  signal w : logic3d_vector(7 downto 0) := (others => L3D_X);  -- Declared at /tmp/vamos_fx/temps/nvc/_norm.sv:10
  signal x : logic3d := L3D_0;  -- Declared at /tmp/vamos_fx/temps/nvc/_norm.sv:6
  signal LPM_q_ivl_3 : logic3d_vector(1 downto 0) := (others => L3D_X);
  signal LPM_d0_ivl_5 : logic3d := L3D_X;
  signal LPM_q_ivl_6 : logic3d := L3D_X;
  signal LPM_d0_ivl_9 : logic3d_vector(1 downto 0) := (others => L3D_X);
  signal LPM_q_ivl_10 : logic3d := L3D_X;
  
  component cin2 is
    port (
      d : in logic3d_vector(1 downto 0)
    );
  end component;
  
  component cin is
    port (
      a : in logic3d
    );
  end component;
  
  component cout is
    port (
      y : out logic3d
    );
  end component;
  
  component cout2 is
    port (
      y : out logic3d_vector(1 downto 0)
    );
  end component;
begin
  
  -- sv_strength: supply1 supply0
  sv_pulldown_ivl_0: entity sv2vhdl.sv_pulldown(behavioral)
    port map (
      y => gnd
    );
  
  -- sv_strength: supply1 supply0
  sv_pullup_ivl_1: entity sv2vhdl.sv_pullup(behavioral)
    port map (
      y => vdd
    );
  process (all) is

  begin
    LPM_q_ivl_3 <= x & gnd;
  end process;
  process (all) is

  begin
    v(0) <= LPM_d0_ivl_5;
  end process;
  process (all) is

  begin
    LPM_q_ivl_6 <= v(0);
  end process;
  process (all) is

  begin
    w(4 + 1 downto 4) <= LPM_d0_ivl_9;
  end process;
  process (all) is

  begin
    LPM_q_ivl_10 <= w(4);
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/temps/nvc/_norm.sv:12
  c1: entity work.cin2__9a93
    port map (
      d => LPM_q_ivl_3
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/temps/nvc/_norm.sv:13
  c2: entity work.cin__4e0c
    port map (
      a => vdd
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/temps/nvc/_norm.sv:14
  c3: entity work.cout__1760
    port map (
      y => LPM_d0_ivl_5
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/temps/nvc/_norm.sv:15
  c4: entity work.cin__4e0c
    port map (
      a => LPM_q_ivl_6
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/temps/nvc/_norm.sv:16
  c5: entity work.cout2__ddf6
    port map (
      y => LPM_d0_ivl_9
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/temps/nvc/_norm.sv:17
  c6: entity work.cin__4e0c
    port map (
      a => LPM_q_ivl_10
    );
  
  -- Generated from initial process in tb (/tmp/vamos_fx/temps/nvc/_norm.sv:18)
  process is
  begin
    wait for 10000 ps;
    x := L3D_1;
    wait;
  end process;
end architecture;



