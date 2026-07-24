-- Current logic3d (int32 per WIRE) with the SAME op sequence as bench_l3dw,
-- to measure the 3D-logic-internal speedup of the packed word.
library ieee; use ieee.std_logic_1164.all; use ieee.numeric_std.all;
library sv2vhdl; use sv2vhdl.logic3d_types_pkg.all;
use std.env.stop; use std.textio.all;
entity bench_l3d is
  generic (CYCLES : natural := 200000; NWORDS : natural := 128); end entity;
architecture t of bench_l3d is
  signal clk : std_logic := '0'; signal done : boolean := false;
begin
  clkgen: process begin
    while not done loop clk <= '0'; wait for 5 ns; clk <= '1'; wait for 5 ns; end loop; wait;
  end process;
  main: process
    constant W : natural := NWORDS*8;                  -- same wire count
    variable a, b, c : logic3d_vector(W-1 downto 0);
    variable acc : unsigned(63 downto 0) := (others => '0');
    variable l : line;
  begin
    for i in a'range loop a(i) := L3D_1 when (i mod 3)=0 else L3D_0;
                          b(i) := L3D_1 when (i mod 5)=0 else L3D_0; end loop;
    for k in 1 to CYCLES loop
      wait until clk = '1';
      c := l3d_xor(a, b);
      a := l3d_or(l3d_and(c, b), l3d_and(a, l3d_not(b)));
      b := l3d_xor(c, a);
      a := a(W-2 downto 0) & a(W-1);
      acc := acc + to_unsigned(a(0) mod 8, 64);
    end loop;
    write(l, string'("bench_l3d CHK=")); hwrite(l, std_logic_vector(acc)); writeline(output, l);
    done <= true; stop;
  end process;
end architecture;
