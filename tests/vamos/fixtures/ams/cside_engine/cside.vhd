-- C-side co-simulation tests (docs/VAMOS_AMS_DESIGN.md §7 P1-P7, §9 "C-side").
-- Digital halves for run_cside.py.  Every boundary signal is a VHDL real
-- (volts); the deck's D2A source of signal q drives node nin, the A2D probe on
-- node nsense deposits into ain.  Analyse with --std=2008 (no sv2vhdl needed).

-- 20 ns square wave on q, std.env.finish at 120 ns (deck stop 1 us)
entity cs_fin120 is end entity;
architecture tb of cs_fin120 is
   signal q : real := 0.0;
begin
   clk: process begin
      q <= 0.0; wait for 10 ns;
      q <= 1.0; wait for 10 ns;
   end process;
   fin: process begin
      wait for 120 ns;
      std.env.finish;
      wait;
   end process;
end architecture;

-- std.env.finish at an odd time between analog points (123.456 ns)
entity cs_finodd is end entity;
architecture tb of cs_finodd is
   signal q : real := 0.0;
begin
   process begin
      q <= 1.0; wait for 30 ns;
      q <= 0.0; wait for 93.456 ns;
      std.env.finish;
      wait;
   end process;
end architecture;

-- initial $finish: std.env.finish at t=0 (a stop during the t=0 settle)
entity cs_fin0 is end entity;
architecture tb of cs_fin0 is
   signal q : real := 0.0;
begin
   process begin
      q <= 1.0;
      std.env.finish;
      wait;
   end process;
end architecture;

-- $fatal at t=0: a failure report during the t=0 settle (exit status 1)
entity cs_fatal0 is end entity;
architecture tb of cs_fatal0 is
   signal q : real := 0.0;
begin
   process begin
      report "FATAL" severity failure;
      wait;
   end process;
end architecture;

-- A stop triggered by an A2D value: finish as soon as ain > 0.5 (with the
-- deck's sense node at 0.7 V from the operating point on: the first sample)
entity cs_a2dfin is end entity;
architecture tb of cs_a2dfin is
   signal ain : real := 0.0;
   signal q   : real := 0.0;
begin
   process (ain) begin
      if ain > 0.5 then
         report "ain crossed 0.5 at " & time'image(now);
         std.env.finish;
      end if;
   end process;
end architecture;

-- A digital runtime fatal (index out of range) at 50 ns
entity cs_rtfatal is end entity;
architecture tb of cs_rtfatal is
   signal q : real := 0.0;
   type arr_t is array (0 to 3) of integer;
begin
   process
      variable a : arr_t := (others => 0);
      variable i : integer := 2;
   begin
      q <= 1.0; wait for 50 ns;
      i := i + 3;
      a(i) := 1;
      wait;
   end process;
end architecture;

-- std.env.stop at 80 ns
entity cs_stop80 is end entity;
architecture tb of cs_stop80 is
   signal q : real := 0.0;
begin
   process begin
      q <= 1.0; wait for 40 ns;
      q <= 0.0; wait for 40 ns;
      std.env.stop;
      wait;
   end process;
end architecture;

-- A clock that is 1 at t=0 (declared 0.0): the operating point must see 1 V
-- (the t=0 settle), so there is no start-up pulse
entity cs_clk1 is end entity;
architecture tb of cs_clk1 is
   signal q : real := 0.0;
begin
   process begin
      q <= 1.0; wait for 50 ns;
      q <= 0.0; wait for 50 ns;
   end process;
end architecture;

-- One rising edge at 50 ns (ramp tests: rise=1e-11, rise=0)
entity cs_edge is end entity;
architecture tb of cs_edge is
   signal q : real := 0.0;
begin
   process begin
      wait for 50 ns;
      q <= 1.0;
      wait;
   end process;
end architecture;

-- A reversal mid-rise: up at 50 ns (10 ps rise), down at 50.005 ns (1 ns
-- fall): the fall starts from 0.5 V and ends at 51.005 ns
entity cs_reversal is end entity;
architecture tb of cs_reversal is
   signal q : real := 0.0;
begin
   process begin
      wait for 50 ns;
      q <= 1.0;
      wait for 5 ps;
      q <= 0.0;
      wait;
   end process;
end architecture;

-- Real values: 5 MV passes through (P7), real'low means "no value" (0 V)
entity cs_bigreal is end entity;
architecture tb of cs_bigreal is
   signal q  : real := 0.0;
   signal q2 : real;            -- real'low, never assigned
begin
   process begin
      wait for 10 ns;
      q <= 5.0e6;
      wait;
   end process;
end architecture;

-- A2D round trip: threshold ain at 0.5 V, drive the decision back out on dq
entity cs_a2d is end entity;
architecture tb of cs_a2d is
   signal ain : real := 0.0;
   signal dq  : real := 0.0;
begin
   process (ain) begin
      if ain > 0.5 then dq <= 1.0;
      else              dq <= 0.0; end if;
   end process;
end architecture;

-- std.env.finish at 1000000005 fs (1.000000005 us): 10 significant digits,
-- the stop line must print them all (%.9g printed 1.00000001e-06, 5 fs late)
entity cs_fin10 is end entity;
architecture tb of cs_fin10 is
   signal q : real := 0.0;
begin
   process begin
      wait for 100 ns;
      q <= 1.0;
      wait for 900000005 fs;
      std.env.finish;
      wait;
   end process;
end architecture;

-- std.env.finish at 2000000006 ps (2.000000006 ms; %.9g printed 0.00200000001)
entity cs_fin2ms is end entity;
architecture tb of cs_fin2ms is
   signal q : real := 0.0;
begin
   process begin
      wait for 100 ns;
      q <= 1.0;
      wait for 1999900006 ps;
      std.env.finish;
      wait;
   end process;
end architecture;

-- A finish on a slow threshold crossing: ain settles just above 1.65 V
-- (about 1 mV/ns there), so the probe moves less than COSIM_A2D_DV in a step
-- and the digital stops at the candidate time itself, whose femtosecond count
-- is the engine's time rounded (the finish window, cosim.c)
entity cs_slowfin is end entity;
architecture tb of cs_slowfin is
   signal q   : real := 0.0;
   signal ain : real := 0.0;
begin
   process begin
      wait for 1 ns;
      q <= 3.3;
      wait;
   end process;
   process (ain) begin
      if ain > 1.65 and ain'last_value <= 1.65 then
         report "ain crossed 1.65 at " & time'image(now);
         std.env.finish;
      end if;
   end process;
end architecture;

-- A 4 ns real clock on q and no end of its own (the interrupt case)
entity cs_clk4 is end entity;
architecture tb of cs_clk4 is
   signal q : real := 0.0;
begin
   process begin
      q <= 1.0; wait for 2 ns;
      q <= 0.0; wait for 2 ns;
   end process;
end architecture;
