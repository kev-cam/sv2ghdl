#!/bin/bash
# For each ITC design: gen TB, run our-nvc plain (CHK), run with --accel to
# get the whole-scope Verilog + gsm model in a private cache dir.
set -u
S=/usr/local/src/sv2ghdl/bfit/benchmarks/vhdl/gpu
NVC=/usr/local/src/nvc/build/bin/nvc; L=/usr/local/src/nvc/build/lib
GEN=/usr/local/src/sv2ghdl/bfit/benchmarks/vhdl/gen_tb.py
ITC=/home/claude/I99T/i99t
export NVC_GSM_LIB=/usr/local/src/sv2ghdl/yosys/libgsm.so
export NVC_ACCEL_CACHE_DIR=$S/accel; mkdir -p $NVC_ACCEL_CACHE_DIR
for row in "b01 3000000" "b06 2000000" "b12 3000000" "b14 1000000" "b17 1000000" "b22 1000000"; do
  read -r n cyc <<<"$row"
  d=$S/work/$n; rm -rf $d; mkdir -p $d
  python3 $GEN $ITC/$n/$n.vhd $n > $d/${n}_tb.vhd || { echo "$n: gen_tb FAILED"; continue; }
  $NVC -L $L --work=$d/w --std=2040 -a $ITC/$n/$n.vhd $d/${n}_tb.vhd > $d/a.log 2>&1 || { echo "$n: analyse FAILED"; tail -3 $d/a.log; continue; }
  $NVC -L $L --work=$d/w --std=2040 -e -gCYCLES=$cyc ${n}_tb > $d/e.log 2>&1 || { echo "$n: elab FAILED"; tail -3 $d/e.log; continue; }
  t0=$(date +%s.%N); $NVC -L $L --work=$d/w --std=2040 -r ${n}_tb > $d/r.log 2>&1; t1=$(date +%s.%N)
  chk=$(grep -oE 'CHK=[0-9A-Fa-f]+' $d/r.log | head -1)
  echo "$n plain: $(awk "BEGIN{printf \"%.3f\", $t1-$t0}")s $chk"
  t0=$(date +%s.%N)
  NVC_ACCEL=1 NVC_ACCEL_JIT=1 NVC_ACCEL_FROM_VHDL=1 NVC_ACCEL_CC="gcc -O2" NVC_ACCEL_SYNTH_TIMEOUT=120 \
    $NVC -L $L --work=$d/w --std=2040 -r ${n}_tb > $d/ra.log 2>&1; t1=$(date +%s.%N)
  achk=$(grep -oE 'CHK=[0-9A-Fa-f]+' $d/ra.log | head -1)
  echo "$n accel: $(awk "BEGIN{printf \"%.3f\", $t1-$t0}")s $achk $(grep -oE 'accel-jit:[^\n]{0,80}' $d/ra.log | head -2 | tr '\n' '|')"
done
ls -la $NVC_ACCEL_CACHE_DIR | grep -v bridge | head -40
