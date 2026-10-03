library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library sv2vhdl;
use sv2vhdl.sv_display_pkg.all;
use sv2vhdl.sv_strength_pkg.all;
use sv2vhdl.logic3d_types_pkg.all;
use sv2vhdl.sv_analog_pkg.all;
use sv2vhdl.sv_math_pkg.all;

-- Generated from Verilog module cin (/tmp/vamos_fx/prims/nvc/_norm.sv:36)
entity cin is
  port (
    a : in logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of cin : entity is "/tmp/vamos_fx/prims/nvc/_norm.sv:36";
end entity; 

-- Generated from Verilog module cin (/tmp/vamos_fx/prims/nvc/_norm.sv:36)
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

-- Generated from Verilog module sup (/tmp/vamos_fx/prims/nvc/_norm.sv:41)
entity sup is
  port (
    vdd : in logic3d;
    vss : in logic3d;
    vb : in logic3d_vector(1 downto 0)
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of sup : entity is "/tmp/vamos_fx/prims/nvc/_norm.sv:41";
end entity; 

-- Generated from Verilog module sup (/tmp/vamos_fx/prims/nvc/_norm.sv:41)
architecture from_verilog of sup is
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

-- Generated from Verilog module sup (/tmp/vamos_fx/prims/nvc/_norm.sv:41)
entity sup__2a3b is
  port (
    vdd : in logic3d;
    vss : in logic3d;
    vb : in logic3d_vector(1 downto 0)
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of sup__2a3b : entity is "/tmp/vamos_fx/prims/nvc/_norm.sv:41";
end entity; 

-- Generated from Verilog module sup (/tmp/vamos_fx/prims/nvc/_norm.sv:41)
architecture from_verilog of sup__2a3b is
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

-- Generated from Verilog module cin (/tmp/vamos_fx/prims/nvc/_norm.sv:36)
entity cin__4e0c is
  port (
    a : in logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of cin__4e0c : entity is "/tmp/vamos_fx/prims/nvc/_norm.sv:36";
end entity; 

-- Generated from Verilog module cin (/tmp/vamos_fx/prims/nvc/_norm.sv:36)
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

-- Generated from Verilog module tb (/tmp/vamos_fx/prims/nvc/_norm.sv:5)
entity tb is
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of tb : entity is "/tmp/vamos_fx/prims/nvc/_norm.sv:5";
end entity; 

-- Generated from Verilog module tb (/tmp/vamos_fx/prims/nvc/_norm.sv:5)
architecture from_verilog of tb is
  signal a : logic3d := L3D_0;  -- Declared at /tmp/vamos_fx/prims/nvc/_norm.sv:6
  signal b : logic3d := L3D_1;  -- Declared at /tmp/vamos_fx/prims/nvc/_norm.sv:6
  signal en : logic3d := L3D_0;  -- Declared at /tmp/vamos_fx/prims/nvc/_norm.sv:6
  signal n_and : logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/prims/nvc/_norm.sv:7
  signal n_nand : logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/prims/nvc/_norm.sv:7
  signal n_pull : logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/prims/nvc/_norm.sv:7
  signal n_pullsup : logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/prims/nvc/_norm.sv:7
  signal n_tri : logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/prims/nvc/_norm.sv:7
  signal n_undrv : logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/prims/nvc/_norm.sv:7
  signal n_weak : logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/prims/nvc/_norm.sv:7
  signal r : logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/prims/nvc/_norm.sv:6
  signal vbus : logic3d_vector(1 downto 0) := (others => L3D_X);  -- Declared at /tmp/vamos_fx/prims/nvc/_norm.sv:10
  signal vdd : logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/prims/nvc/_norm.sv:8
  signal vss : logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/prims/nvc/_norm.sv:9
  
  component cin is
    port (
      a : in logic3d
    );
  end component;
  
  component sup is
    port (
      vdd : in logic3d;
      vss : in logic3d;
      vb : in logic3d_vector(1 downto 0)
    );
  end component;
begin
  
  -- sv_strength: supply1 supply0
  sv_pullup_ivl_0: entity sv2vhdl.sv_pullup(behavioral)
    port map (
      y => vbus
    );
  
  -- sv_strength: supply1 supply0
  sv_pullup_ivl_1: entity sv2vhdl.sv_pullup(behavioral)
    port map (
      y => vdd
    );
  
  -- sv_strength: supply1 supply0
  sv_pulldown_ivl_2: entity sv2vhdl.sv_pulldown(behavioral)
    port map (
      y => vss
    );
  
  sv_nand_g1_0_0_inst: entity sv2vhdl.sv_nand(behavioral)
    generic map (
      n => 2
    )
    port map (
      y => n_nand,
      a => a & b
    );
  
  -- sv_strength: pull1 pull0
  sv_pullup_ivl_3_0_0_inst: entity sv2vhdl.sv_pullup(behavioral)
    port map (
      y => n_pull
    );
  
  sv_bufif1_g2_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)
    port map (
      y => n_tri,
      data => a,
      ctrl => en
    );
  
  sv_strength_buf_ivl_4: entity sv2vhdl.sv_strength_buf(strength)
    generic map (
      str1 => 2,
      str0 => 2
    )
    port map (
      y => n_weak,
      data => a
    );
  
  sv_and_ivl_7: entity sv2vhdl.sv_and(behavioral)
    generic map (
      n => 2
    )
    port map (
      y => n_and,
      a => a & b
    );
  
  -- sv_strength: supply1 highz0
  sv_pullup_p1_0_0_inst: entity sv2vhdl.sv_pullup(behavioral)
    port map (
      y => n_pullsup
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/prims/nvc/_norm.sv:17
  c1: entity work.cin__4e0c
    port map (
      a => n_nand
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/prims/nvc/_norm.sv:18
  c2: entity work.cin__4e0c
    port map (
      a => n_pull
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/prims/nvc/_norm.sv:19
  c3: entity work.cin__4e0c
    port map (
      a => n_tri
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/prims/nvc/_norm.sv:20
  c4: entity work.cin__4e0c
    port map (
      a => n_weak
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/prims/nvc/_norm.sv:21
  c5: entity work.cin__4e0c
    port map (
      a => n_and
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/prims/nvc/_norm.sv:22
  c6: entity work.cin__4e0c
    port map (
      a => n_pullsup
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/prims/nvc/_norm.sv:23
  c7: entity work.cin__4e0c
    port map (
      a => r
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/prims/nvc/_norm.sv:24
  c8: entity work.cin__4e0c
    port map (
      a => n_undrv
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/prims/nvc/_norm.sv:25
  s1: entity work.sup__2a3b
    port map (
      vb => vbus,
      vdd => vdd,
      vss => vss
    );
  process (all) is

  begin
    n_undrv <= L3D_Z;
  end process;
  
  -- Generated from initial process in tb (/tmp/vamos_fx/prims/nvc/_norm.sv:26)
  process is
  begin
    r := L3D_0;
    wait for 10000 ps;
    r := L3D_1;
    wait for 10000 ps;
    en <= L3D_1;
    wait for 10000 ps;
    a <= L3D_1;
    wait;
  end process;
end architecture;



