#!/bin/bash
# opt_asserts.sh -- POSITIVE assertions that specific optimizations FIRE.
#
# Every other gate here checks that correctness is PRESERVED.  None of them
# would notice an optimization quietly disappearing: revert the worbits
# peephole and accel-gate stays green while the wide shapes get 2.4x slower;
# stop declining comb-only chunks and nothing fails until a full VeeR run
# diverges at the first clk edge.  Both happened in July 2026, which is why
# this file exists.  (Precedent: l3dcat_run.sh asserts install=1, not just a
# matching checksum -- this generalises that pattern.)
#
# Each check asserts EVIDENCE that the mechanism fired -- a decline note, a
# code shape in the generated C, a cache filename, an instruction budget --
# and, where a kill-switch exists, runs the NEGATIVE CONTROL to prove the
# assertion can fail (GSM_ALLOW_COMB, ACCEL_CC, FRESHCACHE...).  A check that
# cannot fail is decoration.
#
# Budgets are INSTRUCTION counts (stable to ~1% on this box, unlike wall
# clock) with 1.5x headroom over the measured value, so they catch a lost
# 2.4x optimization without flaking on contention.
#
# usage: ./opt_asserts.sh          exit 0 = all fired, 1 = something regressed
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
NVC="${NVC:-/usr/local/src/nvc-build/bin/nvc}"
VLIB="${NVC_LIBDIR:-/usr/local/src/nvc-build/lib}"
GSM="${GEN_STATEMACHINE:-/usr/local/src/sv2ghdl/yosys/gen_statemachine}"
W="${OPT_ASSERT_WORK:-${TMPDIR:-/tmp}/opt-asserts-$$}"
rm -rf "$W"; mkdir -p "$W"
export HOME="$W"    # private accel cache

pass=0; fail=0
ok()  { pass=$((pass+1)); printf "  PASS  %-42s %s\n" "$1" "${2:-}"; }
bad() { fail=$((fail+1)); printf "  FAIL  %-42s %s\n" "$1" "${2:-}"; }

# ---- fixture: one tiny flop design, analysed once ---------------------------
D="$W/d"; mkdir -p "$D"
python3 "$HERE/gen.py" wide "$D" N=8 W=256 CYC=2000 >/dev/null
eval "$(sed 's/=/="/; s/$/"/' "$D/INFO")"
A=(-M 256m -H 256m --std=2008 --work="$D/w" -L "$VLIB")
for f in $order; do $NVC "${A[@]}" -a "$D/$f" >/dev/null 2>&1; done
$NVC "${A[@]}" -e -gCYC=2000 "$tb" >/dev/null 2>&1
AE=(NVC_ACCEL=1 NVC_ACCEL_JIT=1 NVC_ACCEL_FROM_VHDL=1)
YI=$($NVC "${A[@]}" -r "$tb" 2>&1 | grep -oE 'Y=[0-9]+' | tail -1)

# ---- fixture: a comb-only module (the rvoclkhdr shape) ----------------------
cat > "$W/passthru.v" <<'EOF'
module clkpass (input en, input clk, input scan_mode, output l1clk);
  wire se;
  assign l1clk = clk;
  assign se = 1'b0;
endmodule
EOF

echo "== opt_asserts: does each optimization actually fire? =="

# 1. COMB-ONLY DECLINE (the rvoclkhdr/L1CLK fix).  A zero-register chunk must
#    be REFUSED: bridging a pure-comb path adds a delta hop, catastrophic on a
#    clock wire (full-VeeR diverged from the first retirement).
out=$("$GSM" "$W/passthru.v" clkpass "$W/pt.c" 2>&1); rc=$?
if [ $rc -ne 0 ] && printf '%s' "$out" | grep -q 'comb-only'; then
  ok "comb-only chunk declines" "(rc=$rc)"
else bad "comb-only chunk declines" "rc=$rc out=${out:0:60}"; fi

# ...negative control: the kill-switch must re-admit it, proving the check bites
GSM_ALLOW_COMB=1 "$GSM" "$W/passthru.v" clkpass "$W/pt2.c" >/dev/null 2>&1
if [ $? -eq 0 ] && [ -s "$W/pt2.c" ]; then ok "  ...GSM_ALLOW_COMB=1 overrides"
else bad "  ...GSM_ALLOW_COMB=1 overrides"; fi

# 2. REGISTERED CHUNKS STILL GENERATE + INSTALL (the decline must not overreach)
out=$(env "${AE[@]}" $NVC "${A[@]}" -r "$tb" 2>&1)
YA=$(printf '%s' "$out" | grep -oE 'Y=[0-9]+' | tail -1)
inst=$(printf '%s' "$out" | grep -c 'accel installed')
if [ "$inst" -ge 1 ] && [ "$YA" = "$YI" ]; then
  ok "registered chunk installs + matches" "(installs=$inst)"
else bad "registered chunk installs + matches" "inst=$inst YA=$YA YI=$YI"; fi

MC=$(ls "$W"/.cache/nvc/accel/aj_*_????????????????.c 2>/dev/null | grep -vE '_nvc' | head -1)

# 3. WORBITS SINGLE-WORD PEEPHOLE (3370154, 2.4x on wide shapes).  The folded
#    form `d[K] |= ((s[J] >> SB) & MASK) << DB;` must dominate the generated C;
#    a revert would put thousands of worbits() calls back.
if [ -n "$MC" ]; then
  folded=$(grep -cE '\[[0-9]+\] \|= \(\(' "$MC")
  calls=$(grep -cE '\bworbits(_s)?\(' "$MC")
  if [ "$folded" -ge 100 ] && [ "$calls" -le 600 ]; then
    ok "worbits peephole fired" "(folded=$folded calls=$calls)"
  else bad "worbits peephole fired" "folded=$folded calls=$calls (expect >=100 / <=600)"; fi

# 4. WPLACE IS WORD-CHUNKED, not bit-serial (the withdrawn-963x defect: the
#    bit-at-a-time form cost 24k bit-iterations per network pass).
  if grep -q 'word-chunked' "$MC"; then ok "wplace word-chunked prelude present"
  else bad "wplace word-chunked prelude present"; fi
else
  bad "generated C found for shape checks" "no model .c in cache"
fi

# 5. INSTRUCTION BUDGET on the accelerated run -- the end-to-end backstop for
#    3+4: measured 1.02e9 on this design; a lost peephole roughly doubles it.
# perf counters need perf_event_paranoid <= 2 (or CAP_PERFMON); on boxes
# where the kernel refuses (containers often set 3) skip the backstop with
# a loud note instead of failing the landing -- the peepholes themselves
# are still verified structurally by checks 3+4 on the generated C.
if ! perf stat -e instructions true >/dev/null 2>&1; then
  ok "accel instruction budget" "(SKIPPED: perf events unavailable, paranoid=$(cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null || echo '?'))"
else
  ins=$(env "${AE[@]}" perf stat -e instructions $NVC "${A[@]}" -r "$tb" 2>&1 \
        | grep -oE '^[ ]*[0-9,]+[ ]+instructions' | tr -d ' ,' | sed 's/instructions//')
  BUDGET=550000000   # measured 365M; a lost worbits peephole (~2.3x) lands ~840M
  if [ -n "$ins" ] && [ "$ins" -le $BUDGET ]; then
    ok "accel instruction budget" "($ins <= $BUDGET)"
  else bad "accel instruction budget" "ins=${ins:-?} > $BUDGET"; fi
fi

# 6. TWO-TIER CACHE KEY (9496794f6): same logic under two compilers must give
#    TWO .so files and ONE portable .c -- reusing an -O0 binary for an -O3 run
#    invalidated every optimisation-level comparison before this.
rm -rf "$W/.cache/nvc/accel"
env "${AE[@]}" NVC_ACCEL_CC="gcc -O0" $NVC "${A[@]}" -r "$tb" >/dev/null 2>&1
env "${AE[@]}" NVC_ACCEL_CC="gcc -O3" $NVC "${A[@]}" -r "$tb" >/dev/null 2>&1
nso=$(ls "$W"/.cache/nvc/accel/*.so 2>/dev/null | wc -l)
nc=$(ls "$W"/.cache/nvc/accel/aj_*_????????????????.c 2>/dev/null | grep -vcE '_nvc')
if [ "$nso" -eq 2 ] && [ "$nc" -eq 1 ]; then
  ok "two-tier cache key" "(2 .so, 1 portable .c)"
else bad "two-tier cache key" "nso=$nso nc=$nc (want 2/1)"; fi

# 7. WARM CACHE REUSE (04cd9f6): a second identical run must reuse, not resynth.
out=$(env "${AE[@]}" NVC_ACCEL_CC="gcc -O3" $NVC "${A[@]}" -r "$tb" 2>&1)
if printf '%s' "$out" | grep -q 'reusing cached .so'; then ok "warm cache reuse note present"
else bad "warm cache reuse note present"; fi

# 8. SYNTH TIMEOUT (d33618f74): a hung generator must DEGRADE TO A DECLINE with
#    the interpreter's answer, not hang the run (ic_mem span 37 HOURS before).
cat > "$W/sleepy" <<'EOF'
#!/bin/bash
sleep 30
EOF
chmod +x "$W/sleepy"
rm -rf "$W/.cache/nvc/accel"
out=$(env "${AE[@]}" GEN_STATEMACHINE="$W/sleepy" NVC_ACCEL_SYNTH_TIMEOUT=2 \
      timeout 60 $NVC "${A[@]}" -r "$tb" 2>&1); rc=$?
YT=$(printf '%s' "$out" | grep -oE 'Y=[0-9]+' | tail -1)
if [ $rc -eq 0 ] && printf '%s' "$out" | grep -q 'exceeded 2s' && [ "$YT" = "$YI" ]; then
  ok "synth timeout degrades to decline" "(Y matches interp)"
else bad "synth timeout degrades to decline" "rc=$rc Y=$YT note=$(printf '%s' "$out" | grep -c exceeded)"; fi

# 8b. FORK-WORKER TIMEOUT: the same degrade contract for the in-process fork
#     worker (no exec, no /usr/bin/timeout — the child's own alarm() is the
#     deadline).  GSM_TEST_SLEEP hangs the child before it synthesizes; if the
#     alarm cannot kill it this hangs 30s and the checksum/note checks fail.
#     Also proves the fork path actually ran (its launch note), so a missing
#     libgsm.so silently falling back to the CLI cannot fake a pass.
rm -rf "$W/.cache/nvc/accel"
# -u NVC_ACCEL_RTLIL: this check targets the TEXT fork-worker's deadline —
# with the rtlil walker enabled its child times out first (same degrade
# contract, its own notes) and the text fork legitimately never launches.
out=$(env -u NVC_ACCEL_RTLIL "${AE[@]}" GSM_TEST_SLEEP=30 NVC_ACCEL_SYNTH_TIMEOUT=2 \
      timeout 60 $NVC "${A[@]}" -r "$tb" 2>&1); rc=$?
YT=$(printf '%s' "$out" | grep -oE 'Y=[0-9]+' | tail -1)
ffork=$(printf '%s' "$out" | grep -c 'in-process fork')
if [ $rc -eq 0 ] && [ "$ffork" -ge 1 ] \
   && printf '%s' "$out" | grep -q 'exceeded 2s' && [ "$YT" = "$YI" ]; then
  ok "fork-worker timeout degrades to decline" "(alarm killed child, Y matches)"
else bad "fork-worker timeout degrades to decline" "rc=$rc fork=$ffork Y=$YT note=$(printf '%s' "$out" | grep -c exceeded)"; fi

# 9. DERIVED-CLOCK DECLINE (nvc model.c gate).  A chunk whose group-0 clock is
#    comb-derived races that clock's producer at the bridge -- measured on
#    VeeR: ifu's bus beat counter (active_clk) froze and the IFU issued
#    AR addr=0 forever.  A derived-clock DUT must DECLINE with the note; the
#    registered-chunk install in check 2 already proves primary clocks pass.
cat > "$W/dclk.vhd" <<'EOF'
library ieee; use ieee.std_logic_1164.all; use ieee.numeric_std.all;
entity ddsub is
  port (dclk : in std_logic; d : in std_logic_vector(3 downto 0);
        q : out std_logic_vector(3 downto 0));
end entity;
architecture rtl of ddsub is
  signal r : std_logic_vector(3 downto 0) := (others => '0');
begin
  process (dclk) is begin
    if rising_edge(dclk) then r <= d; end if;
  end process;
  q <= r;
end architecture;
library ieee; use ieee.std_logic_1164.all; use ieee.numeric_std.all;
use std.env.stop;
entity ddtop is end entity;
architecture tb of ddtop is
  signal run  : boolean := true;
  signal clk  : std_logic := '0';
  signal en   : std_logic := '1';
  signal dclk : std_logic;
  signal d, q : std_logic_vector(3 downto 0) := (others => '0');
begin
  clk  <= not clk after 5 ns when run else '0';
  dclk <= clk and en;                      -- comb-DERIVED clock
  u : entity work.ddsub port map (dclk, d, q);
  main : process is
    variable chk : natural := 0;
  begin
    for i in 1 to 40 loop
      d <= std_logic_vector(to_unsigned(i mod 16, 4));
      en <= '1' when (i mod 3) /= 0 else '0';
      wait until rising_edge(clk);
      chk := (chk mod 100000) * 3 + to_integer(unsigned(q));
    end loop;
    report "Y=" & integer'image(chk);
    run <= false; wait for 20 ns; stop;
  end process;
end architecture;
EOF
DD="$W/dd"; mkdir -p "$DD"
$NVC -M 256m -H 256m --std=2008 --work="$DD/w" -L "$VLIB" -a "$W/dclk.vhd" >/dev/null 2>&1
$NVC -M 256m -H 256m --std=2008 --work="$DD/w" -L "$VLIB" -e ddtop >/dev/null 2>&1
YDI=$($NVC -M 256m -H 256m --std=2008 --work="$DD/w" -L "$VLIB" -r ddtop 2>&1 | grep -oE 'Y=[0-9]+' | tail -1)
dout=$(env "${AE[@]}" NVC_ACCEL_ONLY=ddsub NVC_ACCEL_MIN_MODULES=1        $NVC -M 256m -H 256m --std=2008 --work="$DD/w" -L "$VLIB" -r ddtop 2>&1)
YDA=$(printf '%s' "$dout" | grep -oE 'Y=[0-9]+' | tail -1)
dnote=$(printf '%s' "$dout" | grep -c 'DERIVED clock')
dinst=$(printf '%s' "$dout" | grep -c 'accel installed')
if [ "$dnote" -ge 1 ] && [ "$dinst" -eq 0 ] && [ "$YDA" = "$YDI" ]; then
  ok "derived-clock chunk declines" "(note present, Y matches)"
else bad "derived-clock chunk declines" "note=$dnote inst=$dinst Y=$YDA/$YDI"; fi

# ---- fixture: cross-boundary flop-to-flop protocol (mechanism 3) ------------
# Two accelerated flop chunks chained on the primary clock plus an INTERP
# consumer flop on a delta-shifted copy (clk2 <= clk).  The delta-late
# consumer is the shape that BITES: same-list consumers registered at elab
# always sample before the rerouted chunk (it re-registers last), so the
# single-hop form can never fail.  Under the shipped defaults (NBA region for
# reg outputs, 2-delta stage for comb-of-edge) the checksum matches interp;
# with NVC_ACCEL_NBA=0 the consumer captures the producer's post-edge value
# one stage early (the collapsed-pipeline checksum) — the negative control
# that proves this assert can fail.
cat > "$W/xb.vhd" <<'VHD'
library ieee; use ieee.std_logic_1164.all;
entity xbsuba is
  port (clk : in std_logic; d : in std_logic_vector(3 downto 0);
        q : out std_logic_vector(3 downto 0));
end entity;
architecture rtl of xbsuba is
  signal r : std_logic_vector(3 downto 0) := (others => '0');
begin
  process (clk) is begin
    if rising_edge(clk) then r <= d; end if;
  end process;
  q <= r;
end architecture;
library ieee; use ieee.std_logic_1164.all;
entity xbsubb is
  port (clk : in std_logic; d : in std_logic_vector(3 downto 0);
        q : out std_logic_vector(3 downto 0));
end entity;
architecture rtl of xbsubb is
  signal r : std_logic_vector(3 downto 0) := (others => '0');
begin
  process (clk) is begin
    if rising_edge(clk) then r <= d; end if;
  end process;
  q <= r;
end architecture;
library ieee; use ieee.std_logic_1164.all; use ieee.numeric_std.all;
use std.env.stop;
entity xbtop2 is end entity;
architecture tb of xbtop2 is
  signal run : boolean := true;
  signal clk, clk2 : std_logic := '0';
  signal d, q1, q2, s3 : std_logic_vector(3 downto 0) := (others => '0');
begin
  clk  <= not clk after 5 ns when run else '0';
  clk2 <= clk;
  ua : entity work.xbsuba port map (clk, d,  q1);
  ub : entity work.xbsubb port map (clk, q1, q2);
  cons : process (clk2) is begin
    if rising_edge(clk2) then s3 <= q2; end if;
  end process;
  main : process is
    variable chk : natural := 0;
  begin
    for i in 1 to 40 loop
      d <= std_logic_vector(to_unsigned(i mod 16, 4));
      wait until rising_edge(clk);
      chk := (chk mod 100000) * 3 + to_integer(unsigned(s3));
    end loop;
    report "Y=" & integer'image(chk);
    run <= false; wait for 20 ns; stop;
  end process;
end architecture;
VHD
XB="$W/xb"; mkdir -p "$XB"
$NVC -M 256m -H 256m --std=2008 --work="$XB/w" -L "$VLIB" -a "$W/xb.vhd" >/dev/null 2>&1
$NVC -M 256m -H 256m --std=2008 --work="$XB/w" -L "$VLIB" -e xbtop2 >/dev/null 2>&1
YXI=$($NVC -M 256m -H 256m --std=2008 --work="$XB/w" -L "$VLIB" -r xbtop2 2>&1 | grep -oE 'Y=[0-9]+' | tail -1)
xout=$(env "${AE[@]}" NVC_ACCEL_ONLY=xbsub NVC_ACCEL_MIN_MODULES=1 \
       $NVC -M 256m -H 256m --std=2008 --work="$XB/w" -L "$VLIB" -r xbtop2 2>&1)
YXA=$(printf '%s' "$xout" | grep -oE 'Y=[0-9]+' | tail -1)
xinst=$(printf '%s' "$xout" | grep -c 'accel installed')
YXN=$(env "${AE[@]}" NVC_ACCEL_ONLY=xbsub NVC_ACCEL_MIN_MODULES=1 NVC_ACCEL_NBA=0 \
      $NVC -M 256m -H 256m --std=2008 --work="$XB/w" -L "$VLIB" -r xbtop2 2>&1 | grep -oE 'Y=[0-9]+' | tail -1)
if [ "$xinst" -eq 2 ] && [ -n "$YXI" ] && [ "$YXA" = "$YXI" ] && [ "$YXN" != "$YXI" ]; then
  ok "cross-boundary flop protocol (NBA default)" "(2 chunks, match; NBA=0 diverges)"
else bad "cross-boundary flop protocol (NBA default)" "inst=$xinst Y=$YXA/$YXI nba0=$YXN"; fi

# ---- fixture: mini-GALS domain merge + negedge state flip -------------------
# Two ICG-gated domains (interp latch glue), identity-buffered clock tree,
# a chained member pair (internal-edge fusion), an interp-NBA response loop,
# burst enables, base-clock monitor.  Asserts the merge fires (one fused
# install), the chained pair fused over an internal wire, and the checksum
# is byte-exact under NVC_ACCEL_MERGE=1.
cat > "$W/gals.vhd" <<'VHD'
-- mini-GALS fixture: every structural feature that broke at VeeR scale,
-- in a seconds-fast design.  Gated clock domains via ICG (latch+AND, kept
-- as TOP-LEVEL processes so they stay interpreted, like VeeR's declined
-- ICGs); identity-buffered clock distribution; cross-domain nets through
-- port hops; burst-boundary enables; a base-clock monitor sampling a
-- domain-A-registered value (the +4-capture shape).
library ieee; use ieee.std_logic_1164.all;
entity gff_a1 is  -- domain A stage 1
  port (clk : in std_logic; d : in std_logic_vector(7 downto 0);
        q : out std_logic_vector(7 downto 0));
end entity;
architecture rtl of gff_a1 is
  signal r : std_logic_vector(7 downto 0) := (others => '0');
begin
  process (clk) is begin
    if rising_edge(clk) then r <= d xor x"A5"; end if;
  end process;
  q <= r;
end architecture;
library ieee; use ieee.std_logic_1164.all;
entity gff_a2 is  -- domain A stage 2 (chained from a1: internal-edge test)
  port (clk : in std_logic; d : in std_logic_vector(7 downto 0);
        q : out std_logic_vector(7 downto 0));
end entity;
architecture rtl of gff_a2 is
  signal r : std_logic_vector(7 downto 0) := (others => '0');
begin
  process (clk) is begin
    if rising_edge(clk) then r <= d(6 downto 0) & d(7); end if;
  end process;
  q <= r;
end architecture;
library ieee; use ieee.std_logic_1164.all;
entity gff_b1 is  -- domain B (separate gated clock)
  port (clk : in std_logic; d : in std_logic_vector(7 downto 0);
        q : out std_logic_vector(7 downto 0));
end entity;
architecture rtl of gff_b1 is
  signal r : std_logic_vector(7 downto 0) := (others => '0');
begin
  process (clk) is begin
    if rising_edge(clk) then r <= d xor x"3C"; end if;
  end process;
  q <= r;
end architecture;
library ieee; use ieee.std_logic_1164.all; use ieee.numeric_std.all;
use std.env.stop;
entity gals_tb is end entity;
architecture tb of gals_tb is
  signal run  : boolean := true;
  signal clk  : std_logic := '0';
  signal clkb : std_logic;                    -- identity clock buffer
  signal en_a, en_b   : std_logic := '1';
  signal enl_a, enl_b : std_logic := '1';     -- ICG latches
  signal gclk_a, gclk_b : std_logic;          -- gated clocks
  signal d0, qa1, qa2, qb1 : std_logic_vector(7 downto 0) := (others => '0');
  signal r_resp, bin : std_logic_vector(7 downto 0) := (others => '0');
  signal cnt : unsigned(7 downto 0) := (others => '0');
begin
  clk  <= not clk after 5 ns when run else '0';
  clkb <= clk;                                -- buffer hop (root-walk test)
  -- ICGs: transparent-low latch + AND (interp glue, like VeeR's declined ones)
  icg_a : process (clkb, en_a) is begin
    if clkb = '0' then enl_a <= en_a; end if;
  end process;
  gclk_a <= clkb and enl_a;
  icg_b : process (clkb, en_b) is begin
    if clkb = '0' then enl_b <= en_b; end if;
  end process;
  gclk_b <= clkb and enl_b;
  -- domain A: two chained stages (a1.q -> a2.d is a port-hop edge)
  ua1 : entity work.gff_a1 port map (gclk_a, d0,  qa1);
  ua2 : entity work.gff_a2 port map (gclk_a, qa1, qa2);
  -- interp-NBA response loop: r_resp is a TB flop (interp NBA) of qa2;
  -- feeding it into domain B makes qb1 sensitive to the ARRIVAL CYCLE of
  -- r_resp — a rim-input capture (chunk reading same-timestep NBA commits)
  -- shifts the checksum (the v9 two-cycles-early VeeR residual, in small)
  resp : process (clk) is begin
    if rising_edge(clk) then r_resp <= qa2; end if;
  end process;
  bin <= r_resp;
  ub1 : entity work.gff_b1 port map (gclk_b, bin, qb1);
  -- stimulus + burst-boundary enables + base-clock monitor (the +4 shape)
  main : process is
    variable chk : natural := 0;
  begin
    for i in 1 to 60 loop
      d0   <= std_logic_vector(to_unsigned(i mod 256, 8));
      en_a <= '1' when (i mod 7) < 5 else '0';   -- bursts with gaps
      en_b <= '1' when (i mod 5) < 3 else '0';
      wait until rising_edge(clk);
      cnt <= cnt + 1;
      -- sample BOTH domains' registered outputs on the BASE clock: any
      -- post-edge capture or missed flip shifts chk (the retire-PC analogue)
      chk := (chk mod 100000) * 5 + to_integer(unsigned(qa2))
             + 2 * to_integer(unsigned(qb1));
    end loop;
    report "Y=" & integer'image(chk);
    run <= false; wait for 20 ns; stop;
  end process;
end architecture;
VHD
GD="$W/gals"; mkdir -p "$GD"
$NVC -M 256m -H 256m --std=2008 --work="$GD/w" -L "$VLIB" -a "$W/gals.vhd" >/dev/null 2>&1
$NVC -M 256m -H 256m --std=2008 --work="$GD/w" -L "$VLIB" -e gals_tb >/dev/null 2>&1
YGI=$($NVC -M 256m -H 256m --std=2008 --work="$GD/w" -L "$VLIB" -r gals_tb 2>&1 | grep -oE 'Y=[0-9]+' | tail -1)
gout=$(env "${AE[@]}" NVC_ACCEL_MIN_MODULES=1 NVC_ACCEL_MERGE=1 \
       $NVC -M 256m -H 256m --std=2008 --work="$GD/w" -L "$VLIB" -r gals_tb 2>&1)
YGA=$(printf '%s' "$gout" | grep -oE 'Y=[0-9]+' | tail -1)
gmrg=$(printf '%s' "$gout" | grep -c 'MERGE ACTIVE')
gedge=$(printf '%s' "$gout" | grep -oE '[0-9]+ internal edges' | head -1 | grep -oE '^[0-9]+')
if [ "$gmrg" -ge 1 ] && [ "${gedge:-0}" -ge 1 ] && [ -n "$YGI" ] && [ "$YGA" = "$YGI" ]; then
  ok "domain merge + negedge flip (GALS)" "(fused, $gedge internal edge, Y matches)"
else bad "domain merge + negedge flip (GALS)" "mrg=$gmrg edge=$gedge Y=$YGA/$YGI"; fi

# ...under NVC_ACCEL_RTLIL the pool group must have been CONSTRUCTED by the
# rtlil merge builder (engagement — a silent text fallback matches Y too)
if [ -n "${NVC_ACCEL_RTLIL:-}" ]; then
  gmeng=$(printf '%s' "$gout" | grep -c 'via rtlil merge builder')
  if [ "$gmeng" -ge 1 ]; then
    ok "  ...GALS pool via rtlil merge builder"
  else bad "  ...GALS pool via rtlil merge builder" "(eng=0)"; fi
fi

# 11. MEM WHOLE-ARRAY SIGNAL ASSIGN must not kill the probe (2026-08-30):
#     vhdl2vlog's mem_scan_cb and the whole-array emission called
#     tree_value() directly on a T_SIGNAL_ASSIGN — a FATAL object lookup,
#     so the merge-collection PROBE killed the simulator on any design
#     whose memory is written whole-array by a signal assign (the
#     NBA-shadow commit `mem <= v_nba_mem` on EH1a walked into it).
#     The probe's contract is DECLINE, never die: this run must complete
#     with the interpreter's answer whether or not anything installs.
cat > "$W/memagg.vhd" <<'VHD'
library ieee; use ieee.std_logic_1164.all; use ieee.numeric_std.all;
entity memagg_dut is
  port (clk : in std_logic; y : out unsigned(7 downto 0));
end entity;
architecture rtl of memagg_dut is
  type mem_t is array (0 to 3) of std_logic_vector(7 downto 0);
  signal m   : mem_t;
  signal cnt : unsigned(7 downto 0) := (others => '0');
  signal acc : unsigned(7 downto 0) := (others => '0');
begin
  process(clk) begin
    if rising_edge(clk) then
      if cnt = 0 then
        m <= (x"11", x"22", x"33", x"44");  -- whole-array SIGNAL assign
      else
        m(to_integer(cnt(1 downto 0))) <= std_logic_vector(cnt);
      end if;
      acc <= acc + unsigned(m(to_integer(cnt(1 downto 0))));
      cnt <= cnt + 1;
    end if;
  end process;
  y <= acc;
end architecture;

library ieee; use ieee.std_logic_1164.all; use ieee.numeric_std.all;
use std.env.stop;
entity memagg_tb is end entity;
architecture sim of memagg_tb is
  signal clk : std_logic := '0';
  signal y   : unsigned(7 downto 0);
  signal run : boolean := true;
begin
  clk <= not clk after 5 ns when run;
  dut: entity work.memagg_dut port map (clk => clk, y => y);
  process begin
    wait for 1500 ns;
    report "Y=" & integer'image(to_integer(y));
    run <= false; wait for 20 ns; stop;
  end process;
end architecture;
VHD
MD="$W/memagg"; mkdir -p "$MD"
$NVC -M 256m -H 256m --std=2008 --work="$MD/w" -L "$VLIB" -a "$W/memagg.vhd" >/dev/null 2>&1
$NVC -M 256m -H 256m --std=2008 --work="$MD/w" -L "$VLIB" -e memagg_tb >/dev/null 2>&1
YMI=$($NVC -M 256m -H 256m --std=2008 --work="$MD/w" -L "$VLIB" -r memagg_tb 2>&1 | grep -oE 'Y=[0-9]+' | tail -1)
mout=$(env "${AE[@]}" NVC_ACCEL_MIN_MODULES=1 NVC_ACCEL_MERGE=1 \
       timeout 60 $NVC -M 256m -H 256m --std=2008 --work="$MD/w" -L "$VLIB" -r memagg_tb 2>&1); mrc=$?
YMA=$(printf '%s' "$mout" | grep -oE 'Y=[0-9]+' | tail -1)
mfat=$(printf '%s' "$mout" | grep -c 'Fatal')
if [ $mrc -eq 0 ] && [ "$mfat" -eq 0 ] && [ -n "$YMI" ] && [ "$YMA" = "$YMI" ]; then
  ok "mem whole-array assign probe survives" "(no Fatal, Y matches)"
else bad "mem whole-array assign probe survives" "rc=$mrc fatal=$mfat Y=$YMA/$YMI"; fi

# 12. DIRECT-RTLIL WALKER (opt-in NVC_ACCEL_RTLIL=1): the wide fixture must
#     synthesize ENTIRELY through the rtlil builder — engagement note present,
#     NO walker decline (a decline silently falls back to the text path and
#     everything still matches, so only the engagement check can tell), and
#     the checksum equal to interp.
rm -rf "$W/.cache/nvc/accel"
rout=$(env "${AE[@]}" NVC_ACCEL_RTLIL=1 \
       timeout 120 $NVC "${A[@]}" -r "$tb" 2>&1); rrc=$?
YR=$(printf '%s' "$rout" | grep -oE 'Y=[0-9]+' | tail -1)
reng=$(printf '%s' "$rout" | grep -c 'via rtlil builder')
rdec=$(printf '%s' "$rout" | grep -c 'vhdl2rtlil.*declined')
if [ $rrc -eq 0 ] && [ "$reng" -ge 1 ] && [ "$rdec" -eq 0 ] && [ "$YR" = "$YI" ]; then
  ok "direct-rtlil walker full coverage (wide)" "(no decline, Y matches)"
else bad "direct-rtlil walker full coverage (wide)" "rc=$rrc eng=$reng dec=$rdec Y=$YR/$YI"; fi

# 13. DIRECT-RTLIL MEMORIES: a true array memory (dynamic write + dynamic
#     read) must synthesize through the walker, and the walker's generated C
#     must TRACE-MATCH the text path's generated C under a driving harness
#     (these clk-only chunks do not install into the sim — a pre-existing
#     quiet stop after the bridge — so the harness diff is the real oracle;
#     the checksum-vs-interp check would pass trivially).
cat > "$W/memrf.vhd" <<'VHD'
library ieee; use ieee.std_logic_1164.all; use ieee.numeric_std.all;
entity memrf_dut is
  port (clk : in std_logic; y : out unsigned(15 downto 0));
end entity;
architecture rtl of memrf_dut is
  type mem_t is array (0 to 7) of std_logic_vector(15 downto 0);
  signal m   : mem_t;
  signal cnt : unsigned(7 downto 0) := (others => '0');
  signal acc : unsigned(15 downto 0) := (others => '0');
begin
  process(clk) begin
    if rising_edge(clk) then
      m(to_integer(cnt(2 downto 0))) <=
        std_logic_vector(cnt) & std_logic_vector(cnt xor x"5A");
      if cnt > 8 then
        acc <= acc + unsigned(m(to_integer(cnt(2 downto 0) xor "001")));
      end if;
      cnt <= cnt + 1;
    end if;
  end process;
  y <= acc;
end architecture;

library ieee; use ieee.std_logic_1164.all; use ieee.numeric_std.all;
use std.env.stop;
entity memrf_tb is end entity;
architecture sim of memrf_tb is
  signal clk : std_logic := '0';
  signal y   : unsigned(15 downto 0);
  signal run : boolean := true;
begin
  clk <= not clk after 5 ns when run;
  dut: entity work.memrf_dut port map (clk => clk, y => y);
  process begin
    wait for 1500 ns;
    report "Y=" & integer'image(to_integer(y));
    run <= false; wait for 20 ns; stop;
  end process;
end architecture;
VHD
cat > "$W/memrf_harness.c" <<'CEOF'
#include <stdio.h>
#include <stdint.h>
#define SM_NO_MAIN
#include MODEL_C
int main(void)
{
   state_t s; inputs_t in; outputs_t o;
   sm_reset(&s);
   for (int cyc = 0; cyc < 100; cyc++) {
      sm_comb(&s, &in, &o);
      printf("%02d y=%04llx\n", cyc, (unsigned long long)o._y);
      sm_clock(&s, &in);
   }
   return 0;
}
CEOF
RD="$W/memrf"; mkdir -p "$RD"
$NVC -M 256m -H 256m --std=2008 --work="$RD/w" -L "$VLIB" -a "$W/memrf.vhd" >/dev/null 2>&1
$NVC -M 256m -H 256m --std=2008 --work="$RD/w" -L "$VLIB" -e memrf_tb >/dev/null 2>&1
rm -rf "$W/.cache/nvc/accel"
mout=$(env "${AE[@]}" NVC_ACCEL_RTLIL=1 NVC_ACCEL_MIN_MODULES=1        timeout 60 $NVC -M 256m -H 256m --std=2008 --work="$RD/w" -L "$VLIB"        -r memrf_tb 2>&1)
meng=$(printf '%s' "$mout" | grep -c 'via rtlil builder')
mdec=$(printf '%s' "$mout" | grep -c 'vhdl2rtlil.*declined')
# -u: this arm must be the TEXT path even when the whole gate runs under
# NVC_ACCEL_RTLIL=1 (the diff needs one .c from EACH backend)
env -u NVC_ACCEL_RTLIL "${AE[@]}" NVC_ACCEL_MIN_MODULES=1 timeout 60 $NVC -M 256m -H 256m     --std=2008 --work="$RD/w" -L "$VLIB" -r memrf_tb >/dev/null 2>&1
RC=$(ls "$W"/.cache/nvc/accel/aj_memrf_dut_*.c 2>/dev/null      | grep -v _bridge | grep -v _nvc | grep -v subtree)
mok=0
if [ "$meng" -ge 1 ] && [ "$mdec" -eq 0 ] && [ $(printf '%s\n' $RC | wc -l) -eq 2 ]; then
  set -- $RC
  gcc -O1 -DMODEL_C="\"$1\"" -o "$RD/h1" "$W/memrf_harness.c" 2>/dev/null && \
  gcc -O1 -DMODEL_C="\"$2\"" -o "$RD/h2" "$W/memrf_harness.c" 2>/dev/null && \
  "$RD/h1" > "$RD/t1.txt" && "$RD/h2" > "$RD/t2.txt" && \
  diff -q "$RD/t1.txt" "$RD/t2.txt" >/dev/null && \
  [ "$(tail -1 "$RD/t1.txt")" != "99 y=0000" ] && mok=1
fi
if [ "$mok" -eq 1 ]; then
  ok "direct-rtlil memories trace-match text" "(100-cycle harness, nonzero)"
else bad "direct-rtlil memories trace-match text" "eng=$meng dec=$mdec ncand=$(printf '%s\n' $RC | wc -l)"; fi

# 14. DIRECT-RTLIL DYNAMIC BIT-WRITE: a non-constant single-bit index write
#     on a vector signal (NOT an array memory — those take the $memwr path)
#     lowers to the masked whole-target compose.  The compose reads the
#     PRE-activation value (the signal, not g0 — reading g0 from module-level
#     cells is a combinational loop yosys would only catch at synth as an
#     unresolvable topological order).  Engagement + no decline + Y matches.
cat > "$W/dynw.vhd" <<'VHD'
library ieee; use ieee.std_logic_1164.all; use ieee.numeric_std.all;
entity dynw_dut is
  port (clk : in std_logic; y : out unsigned(15 downto 0));
end entity;
architecture rtl of dynw_dut is
  signal vec : std_logic_vector(15 downto 0) := (others => '0');
  signal cnt : unsigned(7 downto 0) := (others => '0');
  signal acc : unsigned(15 downto 0) := (others => '0');
begin
  process(clk) begin
    if rising_edge(clk) then
      vec(to_integer(cnt(3 downto 0))) <= cnt(4) xor cnt(0);
      acc <= acc + unsigned(vec);
      cnt <= cnt + 1;
    end if;
  end process;
  y <= acc;
end architecture;

library ieee; use ieee.std_logic_1164.all; use ieee.numeric_std.all;
use std.env.stop;
entity dynw_tb is end entity;
architecture sim of dynw_tb is
  signal clk : std_logic := '0';
  signal y   : unsigned(15 downto 0);
  signal run : boolean := true;
begin
  clk <= not clk after 5 ns when run;
  dut: entity work.dynw_dut port map (clk => clk, y => y);
  process begin
    wait for 1500 ns;
    report "Y=" & integer'image(to_integer(y));
    run <= false; wait for 20 ns; stop;
  end process;
end architecture;
VHD
DD="$W/dynw"; mkdir -p "$DD"
$NVC -M 256m -H 256m --std=2008 --work="$DD/w" -L "$VLIB" -a "$W/dynw.vhd" >/dev/null 2>&1
$NVC -M 256m -H 256m --std=2008 --work="$DD/w" -L "$VLIB" -e dynw_tb >/dev/null 2>&1
YDI=$($NVC -M 256m -H 256m --std=2008 --work="$DD/w" -L "$VLIB" -r dynw_tb 2>&1 | grep -oE 'Y=[0-9]+' | tail -1)
rm -rf "$W/.cache/nvc/accel"
dout=$(env "${AE[@]}" NVC_ACCEL_RTLIL=1 NVC_ACCEL_MIN_MODULES=1 \
       timeout 60 $NVC -M 256m -H 256m --std=2008 --work="$DD/w" -L "$VLIB" -r dynw_tb 2>&1); drc=$?
YDA=$(printf '%s' "$dout" | grep -oE 'Y=[0-9]+' | tail -1)
deng=$(printf '%s' "$dout" | grep -c 'via rtlil builder')
ddec=$(printf '%s' "$dout" | grep -c 'vhdl2rtlil.*declined')
if [ $drc -eq 0 ] && [ "$deng" -ge 1 ] && [ "$ddec" -eq 0 ] && [ -n "$YDI" ] && [ "$YDA" = "$YDI" ]; then
  ok "direct-rtlil dynamic bit-write" "(masked compose, Y matches)"
else bad "direct-rtlil dynamic bit-write" "rc=$drc eng=$deng dec=$ddec Y=$YDA/$YDI"; fi

# 15. NEGATIVE-RESULT CACHE: a clean synth decline (exit 1) writes a marker
#     keyed on the same vhash; the next run must SKIP the fork ("cached
#     decline" note) with identical results.  GSM_ALLOW_COMB flips comb-only
#     declines but is not in the vhash, so it rides in the marker fingerprint:
#     flipping it must IGNORE the marker and admit the chunk (negative
#     control — proves the marker cannot pin a chunk out wrongly).
cat > "$W/dcc.vhd" <<'VHD'
library ieee; use ieee.std_logic_1164.all; use ieee.numeric_std.all;
entity dcc_dut is
  port (a, b : in std_logic_vector(7 downto 0);
        y : out std_logic_vector(7 downto 0));
end entity;
architecture rtl of dcc_dut is
begin
  y <= a xor b;
end architecture;

library ieee; use ieee.std_logic_1164.all; use ieee.numeric_std.all;
use std.env.stop;
entity dcc_tb is end entity;
architecture sim of dcc_tb is
  signal clk : std_logic := '0';
  signal cnt : unsigned(7 downto 0) := (others => '0');
  signal acc : unsigned(15 downto 0) := (others => '0');
  signal a, b, y : std_logic_vector(7 downto 0);
  signal run : boolean := true;
begin
  clk <= not clk after 5 ns when run;
  a <= std_logic_vector(cnt);
  b <= std_logic_vector(cnt(3 downto 0) & cnt(7 downto 4));
  dut: entity work.dcc_dut port map (a => a, b => b, y => y);
  process(clk) begin
    if rising_edge(clk) then
      acc <= acc + unsigned(y);
      cnt <= cnt + 1;
    end if;
  end process;
  process begin
    wait for 1500 ns;
    report "Y=" & integer'image(to_integer(acc));
    run <= false; wait for 20 ns; stop;
  end process;
end architecture;
VHD
DC="$W/dcc"; mkdir -p "$DC"
$NVC -M 256m -H 256m --std=2008 --work="$DC/w" -L "$VLIB" -a "$W/dcc.vhd" >/dev/null 2>&1
$NVC -M 256m -H 256m --std=2008 --work="$DC/w" -L "$VLIB" -e dcc_tb >/dev/null 2>&1
YCI=$($NVC -M 256m -H 256m --std=2008 --work="$DC/w" -L "$VLIB" -r dcc_tb 2>&1 | grep -oE 'Y=[0-9]+' | tail -1)
rm -rf "$W/.cache/nvc/accel"
c1=$(env "${AE[@]}" NVC_ACCEL_MIN_MODULES=1 timeout 60 $NVC -M 256m -H 256m \
     --std=2008 --work="$DC/w" -L "$VLIB" -r dcc_tb 2>&1)
mrk=$(ls "$W"/.cache/nvc/accel/*.decline 2>/dev/null | wc -l)
c2=$(env "${AE[@]}" NVC_ACCEL_MIN_MODULES=1 timeout 60 $NVC -M 256m -H 256m \
     --std=2008 --work="$DC/w" -L "$VLIB" -r dcc_tb 2>&1)
Y2=$(printf '%s' "$c2" | grep -oE 'Y=[0-9]+' | tail -1)
hits=$(printf '%s' "$c2" | grep -c 'cached decline')
if [ "$mrk" -ge 1 ] && [ "$hits" -ge 1 ] && [ -n "$YCI" ] && [ "$Y2" = "$YCI" ]; then
  ok "decline cache skips re-synth" "(markers=$mrk hits=$hits, Y matches)"
else bad "decline cache skips re-synth" "mrk=$mrk hits=$hits Y=$Y2/$YCI"; fi
c3=$(env "${AE[@]}" NVC_ACCEL_MIN_MODULES=1 GSM_ALLOW_COMB=1 timeout 60 $NVC \
     -M 256m -H 256m --std=2008 --work="$DC/w" -L "$VLIB" -r dcc_tb 2>&1)
Y3=$(printf '%s' "$c3" | grep -oE 'Y=[0-9]+' | tail -1)
h3=$(printf '%s' "$c3" | grep -c 'cached decline')
# the contract is "the marker must not suppress the fresh ATTEMPT" — a
# comb chunk still doesn't install in a full nvc run (0-register install
# stall, pre-existing), so assert the re-synth happened, not the install
s3=$(printf '%s' "$c3" | grep -cE "synth 'dcc_dut")
if [ "$h3" -eq 0 ] && [ "$s3" -ge 1 ] && [ "$Y3" = "$YCI" ]; then
  ok "  ...GSM_ALLOW_COMB bypasses the marker" "(re-attempted, Y matches)"
else bad "  ...GSM_ALLOW_COMB bypasses the marker" "hits=$h3 synth=$s3 Y=$Y3/$YCI"; fi

echo "== $pass passed, $fail failed =="
rm -rf "$W"
exit $((fail > 0))
