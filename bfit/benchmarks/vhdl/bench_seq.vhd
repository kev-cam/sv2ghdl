-- bench_seq: event/clock-bound RTL micro-benchmark (portable VHDL-2008).
-- One synchronous process steps a 32-bit maximal-length LFSR and accumulates it
-- into a 64-bit sum for CYCLES rising edges, then prints a checksum and stops.
-- Light per-cycle compute + many clock edges => stresses the event scheduler.
-- Pure ieee.std_logic_1164 / numeric_std + std.env/textio: runs on nvc and ghdl.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.stop;
use std.textio.all;

entity bench_seq is
  generic (CYCLES : natural := 3000000);
end entity;

architecture tb of bench_seq is
  signal clk  : std_logic := '0';
  signal done : boolean   := false;
begin
  clkgen : process
  begin
    while not done loop
      clk <= '0'; wait for 5 ns;
      clk <= '1'; wait for 5 ns;
    end loop;
    wait;
  end process;

  main : process
    variable lfsr : unsigned(31 downto 0) := to_unsigned(1, 32);
    variable acc  : unsigned(63 downto 0) := (others => '0');
    variable fb   : std_logic;
    variable l    : line;
  begin
    for i in 1 to CYCLES loop
      wait until rising_edge(clk);
      -- 32-bit maximal LFSR, taps 32,22,2,1
      fb   := lfsr(31) xor lfsr(21) xor lfsr(1) xor lfsr(0);
      lfsr := lfsr(30 downto 0) & fb;
      acc  := acc + resize(lfsr, 64);
    end loop;
    write(l, string'("bench_seq CHK="));
    hwrite(l, std_logic_vector(acc));
    writeline(output, l);
    done <= true;
    stop;
  end process;
end architecture;
