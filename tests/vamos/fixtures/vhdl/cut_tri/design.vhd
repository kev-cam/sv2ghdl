library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library sv2vhdl;
use sv2vhdl.sv_display_pkg.all;
use sv2vhdl.sv_strength_pkg.all;
use sv2vhdl.logic3d_types_pkg.all;
use sv2vhdl.sv_analog_pkg.all;
use sv2vhdl.sv_math_pkg.all;

-- Generated from Verilog module cin (/tmp/vamos_fx/tri/nvc/_norm.sv:17)
entity cin is
  port (
    a : in logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of cin : entity is "/tmp/vamos_fx/tri/nvc/_norm.sv:17";
end entity; 

-- Generated from Verilog module cin (/tmp/vamos_fx/tri/nvc/_norm.sv:17)
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

-- Generated from Verilog module cin (/tmp/vamos_fx/tri/nvc/_norm.sv:17)
entity cin__4e0c is
  port (
    a : in logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of cin__4e0c : entity is "/tmp/vamos_fx/tri/nvc/_norm.sv:17";
end entity; 

-- Generated from Verilog module cin (/tmp/vamos_fx/tri/nvc/_norm.sv:17)
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

-- Generated from Verilog module tb (/tmp/vamos_fx/tri/nvc/_norm.sv:4)
entity tb is
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of tb : entity is "/tmp/vamos_fx/tri/nvc/_norm.sv:4";
end entity; 

-- Generated from Verilog module tb (/tmp/vamos_fx/tri/nvc/_norm.sv:4)
architecture from_verilog of tb is
  signal a : logic3d := L3D_0;  -- Declared at /tmp/vamos_fx/tri/nvc/_norm.sv:5
  signal t0 : logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/tri/nvc/_norm.sv:7
  signal t1 : logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/tri/nvc/_norm.sv:6
  signal t1d : logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/tri/nvc/_norm.sv:8
  
  component cin is
    port (
      a : in logic3d
    );
  end component;
begin
  process (all) is

  begin
    t1d <= a;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/tri/nvc/_norm.sv:10
  c1: entity work.cin__4e0c
    port map (
      a => t1
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/tri/nvc/_norm.sv:11
  c2: entity work.cin__4e0c
    port map (
      a => t0
    );
  
  -- Generated from instantiation at /tmp/vamos_fx/tri/nvc/_norm.sv:12
  c3: entity work.cin__4e0c
    port map (
      a => t1d
    );
  process (all) is

  begin
    t0 <= L3D_0;
  end process;
  process (all) is

  begin
    t1 <= L3D_1;
  end process;
end architecture;



