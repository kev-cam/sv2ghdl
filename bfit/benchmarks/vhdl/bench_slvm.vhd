-- std_logic baseline with the SAME logical op sequence as bench_l3dw, at the
-- same wire count (WIRES = NWORDS*8), for an apples-to-apples speed ratio.
library ieee; use ieee.std_logic_1164.all; use ieee.numeric_std.all;
use std.env.stop; use std.textio.all;
entity bench_slvm is
  generic (CYCLES : natural := 200000; NWORDS : natural := 128); end entity;
architecture t of bench_slvm is
  signal clk : std_logic := '0'; signal done : boolean := false;
begin
  clkgen: process begin
    while not done loop clk <= '0'; wait for 5 ns; clk <= '1'; wait for 5 ns; end loop; wait;
  end process;
  main: process
    constant W : natural := NWORDS*8;
    variable a, b, c : std_logic_vector(W-1 downto 0);
    variable acc : unsigned(63 downto 0) := (others => '0');
    variable l : line;
  begin
    for i in a'range loop a(i) := '1' when (i mod 3)=0 else '0';
                          b(i) := '1' when (i mod 5)=0 else '0'; end loop;
    for k in 1 to CYCLES loop
      wait until clk = '1';
      c := a xor b;
      a := (c and b) or (a and not b);
      b := c xor a;
      a := a(W-2 downto 0) & a(W-1);
      acc := acc + unsigned(a(7 downto 0));
    end loop;
    write(l, string'("bench_slvm CHK=")); hwrite(l, std_logic_vector(acc)); writeline(output, l);
    done <= true; stop;
  end process;
end architecture;
