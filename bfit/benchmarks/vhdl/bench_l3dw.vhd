-- Speed + self-consistency benchmark for the packed 3D-logic word intrinsics.
-- Operates ONLY on l3dw ops (and/or/xor/not) so the hot path is the l3dw
-- intrinsic; inputs are built and the checksum extracted with scalar integer
-- math (no ieee 8-bit-unsigned vector ops, which have an unrelated SSE
-- overread on sub-16-bit vectors). WIRES = 8*NWORDS.
--
-- Correctness is validated by running with NVC_JIT_INTRINSICS on vs off: the
-- checksum must be identical (intrinsic == VHDL body).
library ieee; use ieee.std_logic_1164.all; use ieee.numeric_std.all;
library sv2vhdl; use sv2vhdl.logic3dw_pkg.all;
use std.env.stop; use std.textio.all;
entity bench_l3dw is
  generic (CYCLES : natural := 200000; NWORDS : natural := 128); end entity;
architecture t of bench_l3dw is
  signal clk : std_logic := '0'; signal done : boolean := false;
begin
  clkgen: process begin
    while not done loop clk <= '0'; wait for 5 ns; clk <= '1'; wait for 5 ns; end loop; wait;
  end process;
  main: process
    variable a, b, c : l3dw_vector(NWORDS-1 downto 0);
    variable acc : unsigned(63 downto 0) := (others => '0');
    variable l : line;
    constant DRV : integer := 255*256;                 -- driven plane
  begin
    for i in a'range loop
      a(i) := l3dw((((i*37+3) mod 256)) + DRV);         -- value byte varies
      b(i) := l3dw((((i*91+7) mod 256)) + DRV);
    end loop;
    for k in 1 to CYCLES loop
      wait until clk = '1';
      c := l3dw_xor(a, b);
      a := l3dw_or(l3dw_and(c, b), l3dw_and(a, l3dw_not(b)));
      b := l3dw_xor(c, a);
      a := a(NWORDS-2 downto 0) & a(NWORDS-1);          -- word rotate
      acc := acc + to_unsigned(integer(a(0)) mod 256, 64);   -- fold value byte
    end loop;
    write(l, string'("bench_l3dw CHK=")); hwrite(l, std_logic_vector(acc)); writeline(output, l);
    done <= true; stop;
  end process;
end architecture;
