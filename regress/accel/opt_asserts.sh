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
ins=$(env "${AE[@]}" perf stat -e instructions $NVC "${A[@]}" -r "$tb" 2>&1 \
      | grep -oE '^[ ]*[0-9,]+[ ]+instructions' | tr -d ' ,' | sed 's/instructions//')
BUDGET=550000000   # measured 365M; a lost worbits peephole (~2.3x) lands ~840M
if [ -n "$ins" ] && [ "$ins" -le $BUDGET ]; then
  ok "accel instruction budget" "($ins <= $BUDGET)"
else bad "accel instruction budget" "ins=${ins:-?} > $BUDGET"; fi

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

echo "== $pass passed, $fail failed =="
rm -rf "$W"
exit $((fail > 0))
