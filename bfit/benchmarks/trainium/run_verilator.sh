#!/bin/bash
# Build + run the systolic-array Verilator model for one N.  Usage: run_verilator.sh N [M] [threads]
# Output dir /opt/trn/sa/obj_N<N>[_t<threads>]; result line appended to /opt/trn/sa/results.txt
set -u
N=${1:-16}; M=${2:-4096}; T=${3:-1}
cd /opt/trn/sa || exit 1
OBJ=obj_N${N}; [ "$T" != 1 ] && OBJ=${OBJ}_t${T}
THR=""; [ "$T" != 1 ] && THR="--threads $T"
START=$(date +%s)
verilator --cc --exe --build -j 16 -O3 --x-assign fast --x-initial fast --noassert \
    -Wno-fatal -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC $THR \
    -GN=$N -CFLAGS "-O2 -DSA_N=$N" --Mdir "$OBJ" --top-module sa sa.sv tb_sa.cpp > "$OBJ.build.log" 2>&1
rc=$?
BUILD=$(( $(date +%s) - START ))
if [ $rc -ne 0 ] || [ ! -x "$OBJ/Vsa" ]; then
    echo "N=$N threads=$T BUILD FAILED rc=$rc (${BUILD}s), see $OBJ.build.log"; tail -5 "$OBJ.build.log"; exit 1
fi
echo "N=$N threads=$T built in ${BUILD}s"
RES=$("$OBJ/Vsa" "$M" 1)
echo "$RES"
echo "$(date -Is) threads=$T build=${BUILD}s $RES" >> results.txt
