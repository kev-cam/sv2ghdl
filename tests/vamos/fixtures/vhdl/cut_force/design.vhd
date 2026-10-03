library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library sv2vhdl;
use sv2vhdl.sv_display_pkg.all;
use sv2vhdl.sv_strength_pkg.all;
use sv2vhdl.logic3d_types_pkg.all;
use sv2vhdl.sv_analog_pkg.all;
use sv2vhdl.sv_math_pkg.all;

-- Generated from Verilog module cin (/tmp/vamos_fx/force/nvc/_norm.sv:16)
entity cin is
  port (
    a : in logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of cin : entity is "/tmp/vamos_fx/force/nvc/_norm.sv:16";
end entity; 

-- Generated from Verilog module cin (/tmp/vamos_fx/force/nvc/_norm.sv:16)
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

-- Generated from Verilog module cin (/tmp/vamos_fx/force/nvc/_norm.sv:16)
entity cin__4e0c is
  port (
    a : in logic3d
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of cin__4e0c : entity is "/tmp/vamos_fx/force/nvc/_norm.sv:16";
end entity; 

-- Generated from Verilog module cin (/tmp/vamos_fx/force/nvc/_norm.sv:16)
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

-- Generated from Verilog module tb (/tmp/vamos_fx/force/nvc/_norm.sv:3)
entity tb is
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of tb : entity is "/tmp/vamos_fx/force/nvc/_norm.sv:3";
end entity; 

-- Generated from Verilog module tb (/tmp/vamos_fx/force/nvc/_norm.sv:3)
architecture from_verilog of tb is
  signal a : logic3d := L3D_0;  -- Declared at /tmp/vamos_fx/force/nvc/_norm.sv:4
  signal n : logic3d := L3D_X;  -- Declared at /tmp/vamos_fx/force/nvc/_norm.sv:5
  
  component cin is
    port (
      a : in logic3d
    );
  end component;
begin
  process (all) is

  begin
    n <= a;
  end process;
  
  -- Generated from instantiation at /tmp/vamos_fx/force/nvc/_norm.sv:7
  c1: entity work.cin__4e0c
    port map (
      a => n
    );
  
  -- Generated from initial process in tb (/tmp/vamos_fx/force/nvc/_norm.sv:8)
  process is
  begin
    wait for 10000 ps;
    n <= force L3D_1;
    wait for 10000 ps;
    n <= release;
    wait;
  end process;
end architecture;



