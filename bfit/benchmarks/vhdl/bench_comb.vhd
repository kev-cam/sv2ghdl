-- bench_comb: datapath/eval-bound RTL micro-benchmark (portable VHDL-2008).
-- Each rising edge mixes four 32-bit LCG lanes (32x32 multiply truncated to 32 +
-- xor/shift) and folds them into a 64-bit accumulator. Heavier per-cycle compute
-- than bench_seq but using only 32-bit multiplies (mcode handles 64x64->128 very
-- poorly), so it stays a fair cross-engine datapath benchmark. Runs on nvc & ghdl.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.stop;
use std.textio.all;

entity bench_comb is
  generic (CYCLES : natural := 3000000);
end entity;

architecture tb of bench_comb is
  signal clk  : std_logic := '0';
  signal done : boolean   := false;

  -- one 32-bit LCG + xorshift mixing step (32x32 multiply truncated to 32)
  function mix (x : unsigned(31 downto 0)) return unsigned is
    constant A : unsigned(31 downto 0) := x"9E3779B1";  -- LCG multiplier
    variable p : unsigned(63 downto 0);
    variable r : unsigned(31 downto 0);
  begin
    p := x * A;                          -- 32x32 -> 64
    r := p(31 downto 0);
    r := r xor (r srl 15);
    return r;
  end function;
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
    variable s0, s1, s2, s3 : unsigned(31 downto 0);
    variable acc            : unsigned(63 downto 0) := (others => '0');
    variable l              : line;
  begin
    s0 := x"01234567";
    s1 := x"89ABCDEF";
    s2 := x"11111111";
    s3 := x"A5A5A5A5";
    for i in 1 to CYCLES loop
      wait until rising_edge(clk);
      s0  := mix(s0 + acc(31 downto 0));
      s1  := mix(s1 xor s0);
      s2  := mix(s2 + s1);
      s3  := mix(s3 xor s2);
      acc := acc + (s0 & s1) + (s2 & s3);
    end loop;
    write(l, string'("bench_comb CHK="));
    hwrite(l, std_logic_vector(acc));
    writeline(output, l);
    done <= true;
    stop;
  end process;
end architecture;
