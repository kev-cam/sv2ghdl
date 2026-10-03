library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library sv2vhdl;
use sv2vhdl.sv_display_pkg.all;
use sv2vhdl.sv_strength_pkg.all;
use sv2vhdl.logic3d_types_pkg.all;
use sv2vhdl.sv_analog_pkg.all;
use sv2vhdl.sv_math_pkg.all;

-- Generated from Verilog module vamp (/tmp/vamos_fx/real/nvc/_norm.sv:17)
entity vamp__b6bb is
  port (
    vin : in real;
    vout : out real
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of vamp__b6bb : entity is "/tmp/vamos_fx/real/nvc/_norm.sv:17";
end entity; 

-- Generated from Verilog module vamp (/tmp/vamos_fx/real/nvc/_norm.sv:17)
architecture from_verilog of vamp__b6bb is
  signal vout_Reg : real := 0.0;
begin
  process (all) is

  begin
    vout <= vout_Reg;
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

-- Generated from Verilog module tb (/tmp/vamos_fx/real/nvc/_norm.sv:4)
entity tb is
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of tb : entity is "/tmp/vamos_fx/real/nvc/_norm.sv:4";
end entity; 

-- Generated from Verilog module tb (/tmp/vamos_fx/real/nvc/_norm.sv:4)
architecture from_verilog of tb is
  signal vin_r : real := 0.0;  -- Declared at /tmp/vamos_fx/real/nvc/_norm.sv:5
  signal vout_r : real := 0.0;  -- Declared at /tmp/vamos_fx/real/nvc/_norm.sv:6
  
  component vamp is
    port (
      vin : in real;
      vout : out real
    );
  end component;
begin
  
  -- Generated from instantiation at /tmp/vamos_fx/real/nvc/_norm.sv:7
  a1: entity work.vamp__b6bb
    port map (
      vin => vin_r,
      vout => vout_r
    );
  
  -- Generated from initial process in tb (/tmp/vamos_fx/real/nvc/_norm.sv:8)
  process is
  begin
    vin_r := 0.5;
    wait for 10000 ps;
    vin_r := 1.25;
    wait for 10000 ps;
    sv_display_line("" & to_string(vout_r, "%f"));
    wait;
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

-- Generated from Verilog module vamp (/tmp/vamos_fx/real/nvc/_norm.sv:17)
entity vamp is
  port (
    vin : in real;
    vout : out real
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of vamp : entity is "/tmp/vamos_fx/real/nvc/_norm.sv:17";
end entity; 

-- Generated from Verilog module vamp (/tmp/vamos_fx/real/nvc/_norm.sv:17)
architecture from_verilog of vamp is
  signal vout_Reg : real := 0.0;
begin
  process (all) is

  begin
    vout <= vout_Reg;
  end process;
end architecture;



