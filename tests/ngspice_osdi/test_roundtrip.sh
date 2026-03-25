#!/bin/bash
# Round-trip test: Verilog-AMS → iverilog → VHDL → NVC → .va → OpenVAF → .osdi
set -e

DIR=$(dirname "$0")
NVC=/usr/local/src/nvc/build/bin/nvc
IVL=/usr/local/src/iverilog/driver/iverilog
OPENVAF=/usr/local/src/OpenVAF/openvaf
LIBDIR=/usr/local/src/nvc/build/lib
WORKDIR=/tmp/roundtrip_test

rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"

# Step 1: iverilog → VHDL (requires -g verilog-ams for discipline parsing)
echo "=== Step 1: iverilog → VHDL ==="
$IVL -B/tmp/ivl -g verilog-ams -tvhdl -psv2vhdl=1 \
    -o "$WORKDIR/myresistor.vhd" "$DIR/test_resistor.va"
echo "--- VHDL output ---"
cat "$WORKDIR/myresistor.vhd"

# Step 2: NVC analyze + elaborate + run (writes myresistor.va via destructor)
echo ""
echo "=== Step 2: NVC analyze + elaborate ==="
cd "$WORKDIR"
$NVC --std=2040 -L "$LIBDIR" -a myresistor.vhd
$NVC --std=2040 -L "$LIBDIR" --load="$LIBDIR/sv2vhdl/libsv_analog.so" \
    -e myresistor -r myresistor 2>&1 || true
echo "--- Reconstructed .va ---"
cat "$WORKDIR/myresistor.va"

# Step 3: Compile reconstructed .va with OpenVAF
echo ""
echo "=== Step 3: OpenVAF compile ==="
$OPENVAF "$WORKDIR/myresistor.va" -o "$WORKDIR/myresistor_roundtrip.osdi"
echo "OpenVAF compiled successfully"

echo ""
echo "PASS: round-trip pipeline complete"
