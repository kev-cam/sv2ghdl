#!/bin/bash
# build_h3.sh - everything up to the CPU farm for both Hazard3 workloads.
# bfit GPU-farm kit (PolyForm Noncommercial 1.0.0).  Runs under Linux/WSL.
#
#   build_h3.sh [thr] [split]        (default: both)
# Per workload, in $H3_WORK/<wl>/ (default ~/gf_hz3/work):
#   1. firmware (clang-19/lld-19, upstream sw/Makefile flags, linked at 0)
#      thr:   UNCHANGED sw/mandelbrot.c, SIZE_LOG2=4 MAX_ITERS=8, with
#             tests/hazard3_mandelbrot/fw/rv32_tohost.c = the committed
#             mandel16_i8 variant (image checked byte-identical)
#      split: gen_tile_fw.py(sw/mandelbrot.c) with upstream sw/rv32.c and
#             fw/tile_rt.h, 1024x1024 MAX_ITERS=256 (upstream defaults)
#   2. goldens: thr = the committed golden.tohost (folded by h3chk.py) and
#      ref_cycles 95038; split = fw/tile_native.h for m = 0, 2, 4 (per-tile
#      CHK, AGG, and the union image, whose md5 must be upstream's)
#   3. gen_farm_soc.py -> soc_farm.v (RAM depths: thr i128/d512, split i128/d16)
#   4. gen_statemachine (GSM_U32=1) -> model.c ; strip_model.py ; mem_prep.py
#      -> model_m.c ; gpubuild/make_device_model.sh -> model_m_dev.c
#   5. CPU farm (g++ -O2 -fopenmp) and the Verilator twin (tb_h3twin.cpp)
# Facts that the GPU run is certified against go to expect/<wl>.txt.
set -u
K=$(cd "$(dirname "$0")" && pwd)
G=$(dirname "$K")
H=${HAZARD3_SUITE_DIR:-/usr/local/src/sv2ghdl/tests/hazard3_mandelbrot}
U=${HAZARD3_MANDELBROT_DIR:-$HOME/verilator-hazard3-mandelbrot-testbench}
W=${H3_WORK:-$HOME/gf_hz3/work}
GSM=${GSM:-$HOME/gf_hz3/bin/gen_statemachine}
CLANG=${CLANG:-clang-19}; LLD=${LLD:-$(command -v ld.lld-19 || command -v ld.lld)}; OBJDUMP=${OBJDUMP:-llvm-objdump-19}
VERILATOR=${VERILATOR:-verilator}
CF="-fPIC -ffreestanding -nostdlib -O2 -fno-unroll-loops -fno-inline-functions -target riscv32-unknown-unknown -march=rv32imac"
UP_MD5=693d2391e979a114a82af00b3e64e54c          # upstream output.ppm (variants.json bench.ppm_md5)
mkdir -p $K/expect $K/results
die() { echo "build_h3: $*" >&2; exit 1; }
[ -f $U/soc.tmpl.v ] || die "no upstream checkout at $U (tests/hazard3_mandelbrot/setup.sh makes one)"
. $H/UPSTREAM
[ "$(git -C $U rev-parse HEAD)" = "$UPSTREAM_COMMIT" ] || die "$U is not at the pinned $UPSTREAM_COMMIT"
[ "$(git -C $U/Hazard3 rev-parse HEAD)" = "$HAZARD3_COMMIT" ] || die "$U/Hazard3 is not at the pinned $HAZARD3_COMMIT"
if [ ! -x "$GSM" ]; then          # build gen_statemachine from the sv2ghdl source (needs a yosys build tree)
    YD=${YOSYS_DIR:-$HOME/toolchain/src/yosys}; YB=${YOSYS_BUILD:-$HOME/toolchain/src/yosys-build}
    mkdir -p $(dirname $GSM)
    g++ -std=c++20 -O2 -I$YD -I$YB -I$YB/share/include -D_YOSYS_ -DYOSYS_ENABLE_READLINE=0 -DYOSYS_ENABLE_TCL=0 \
        -DYOSYS_ENABLE_ABC -DYOSYS_ENABLE_GLOB -DYOSYS_ENABLE_ZLIB -fPIC -o $GSM \
        /usr/local/src/sv2ghdl/yosys/gen_statemachine.cpp -L$YB -lyosys -Wl,-rpath,$YB || die "cannot build gen_statemachine"
fi
F=$(awk -v U=$U '/^HAZARD3_FILES :=/ {on=1; next} on && NF == 0 {exit} on {sub(/\\$/,""); if ($1!="") print U "/" $1}' $U/Makefile)
sha() { sha256sum "$1" | cut -c1-16; }

no_data_sections() {   # the d_ram preload is dropped: assert there is nothing to preload
    $OBJDUMP -h "$1" | awk 'NR>5 && $2 ~ /^\./ {print $2}' | grep -vxE '\.text|\.comment|\.riscv\.attributes|\.symtab|\.shstrtab|\.strtab' \
        && die "$1 has data sections; use gen_farm_soc.py --d-preload elf" || true
}

model() {   # $1 = workload dir: soc_farm.v -> model_m.c, model_m_dev.c, farm_cpu, Vtwin
    local d=$1
    ( cd $d
      for f in $U/Hazard3/hdl/*.vh; do ln -sf $f .; done          # `include search: the cwd
      t0=$(date +%s)
      GSM_U32=1 $GSM $d/soc_farm.v $F soc $d/model.c > gsm.log 2>&1 || die "gen_statemachine failed ($d/gsm.log)"
      echo "  gsm: $(grep -o 'Generated .*' gsm.log | head -1 | sed 's/.*: //') ($(( $(date +%s)-t0 ))s)"
      python3 $G/strip_model.py model.c > model_s.c 2> strip.log
      python3 $K/mem_prep.py model_s.c > model_m.c 2> mem_prep.log || die "mem_prep failed"
      sed 's/^/  /' mem_prep.log
      /usr/local/src/sv2ghdl/gpubuild/make_device_model.sh model_m.c > /dev/null
      g++ -O2 -fopenmp -x c++ -DFARM_CPU -DMODEL_C="\"$d/model_m.c\"" -o farm_cpu $K/h3farm.cu 2> cc_cpu.log || die "g++ failed ($d/cc_cpu.log)"
      rm -rf obj_twin
      $VERILATOR --cc --exe --build -j 8 -O3 --x-assign 0 --x-initial 0 -Wno-fatal -Wno-lint -Wno-style \
          --top-module soc -I$U/Hazard3/hdl -I$U/Hazard3 --Mdir obj_twin -o Vtwin \
          -MAKEFLAGS OPT_FAST=-O3 -CFLAGS -march=native soc_farm.v $F $K/tb_h3twin.cpp > verilator.log 2>&1 || die "verilator failed ($d/verilator.log)"
      echo "  cpu farm + Verilator twin built" ) || exit 1
}

do_thr() {
    local d=$W/thr; mkdir -p $d; cd $d
    echo "== thr: every instance renders mandel16_i8 (16x16, MAX_ITERS 8)"
    $CLANG $CF --ld-path=$LLD -Wl,--image-base=0 -DSIZE_LOG2=4 -DMAX_ITERS=8 --include $H/fw/rv32_tohost.c \
        -o thr.elf $U/sw/mandelbrot.c || die "clang"
    no_data_sections thr.elf
    python3 $H/elf2hex.py --images thr.elf dense_i.hex dense_d.hex > /dev/null
    cmp -s dense_i.hex $H/variants/mandel16_i8/i_ram.hex || die "thr.elf is not the committed mandel16_i8 image"
    cmp -s dense_d.hex $H/variants/mandel16_i8/d_ram.hex || die "thr.elf data image differs from mandel16_i8"
    python3 $K/gen_farm_soc.py --template $U/soc.tmpl.v --elf thr.elf --i-depth 128 --d-depth 512 -o soc_farm.v || die "gen_farm_soc"
    local chk; chk=$(python3 $K/h3chk.py $H/variants/mandel16_i8/golden.tohost | grep -oE 'CHK=[0-9A-F]+' | cut -d= -f2)
    local ref; ref=$(python3 -c "import json; print([v for v in json.load(open('$H/variants.json'))['variants'] if v['name']=='mandel16_i8'][0]['ref_cycles'])")
    model $d
    { echo "# thr: every instance renders tests/hazard3_mandelbrot variant mandel16_i8 (generated by build_h3.sh)"
      echo "# CHK0 = h3chk.py fold of tests/hazard3_mandelbrot/variants/mandel16_i8/golden.tohost (native gcc golden)"
      echo "# DONE0 = that variant's ref_cycles (Verilator and Icarus agree on it, tests/hazard3_mandelbrot)"
      echo "workload=thr CHK0=$chk DONE0=$ref words=260"
      echo "provenance thr.elf=$(sha thr.elf) i_ram=$(sha soc_farm_i_ram.hex) model_m=$(sha model_m.c) mandelbrot.c=$(sha $U/sw/mandelbrot.c) rv32_tohost.c=$(sha $H/fw/rv32_tohost.c) soc.tmpl.v=$(sha $U/soc.tmpl.v) gsm_src=$(sha /usr/local/src/sv2ghdl/yosys/gen_statemachine.cpp)"
    } > $K/expect/thr.txt
    cat $K/expect/thr.txt | grep -v '^#'
}

do_split() {
    local d=$W/split; mkdir -p $d; cd $d
    echo "== split: upstream 1024x1024 MAX_ITERS=256, one tile per instance"
    python3 $K/gen_tile_fw.py $U/sw/mandelbrot.c > tile_mandelbrot.c || die "gen_tile_fw"
    $CLANG $CF --ld-path=$LLD -Wl,--image-base=0 -ffunction-sections -Wl,--gc-sections -DSIZE_LOG2=10 -DMAX_ITERS=256 \
        --include $U/sw/rv32.c --include $K/fw/tile_rt.h -o tile.elf tile_mandelbrot.c || die "clang"
    no_data_sections tile.elf
    $OBJDUMP -d tile.elf > tile.asm
    gcc -O2 -DSIZE_LOG2=10 -DMAX_ITERS=256 -include $K/fw/tile_native.h -o tile_native tile_mandelbrot.c 2> native_cc.log || die "native gcc"
    { echo "# split: upstream 1024x1024 MAX_ITERS=256 rendered as NT = 2^(20-m) tiles of 2^m pixels (fw/tile_geom.h)"
      echo "# CHK0/AGG = fw/tile_native.h (the same generated mandelbrot_tile() compiled natively by gcc -O2):"
      echo "#   per-tile FNV-1a-64 over (tile word, pixels, DONE) and FNV-1a-64 over the CHK vector in tile order"
      echo "# md5 = the union of the native tiles written as upstream's output.ppm; must be $UP_MD5"
    } > $K/expect/split.txt
    for m in 0 2 4; do
        local line; line=$(./tile_native $m native_m$m.ppm native_m${m}_tiles.txt) || die "tile_native m=$m"
        local md5; md5=$(md5sum native_m$m.ppm | cut -d' ' -f1)
        [ "$md5" = "$UP_MD5" ] || die "native union image m=$m md5 $md5 != upstream $UP_MD5"
        echo "workload=split m=$m NT=$(echo $line | grep -oE 'NT=[0-9]+' | cut -d= -f2) CHK0=$(echo $line | grep -oE 'CHK0=[0-9A-F]+' | cut -d= -f2) AGG=$(echo $line | grep -oE 'AGG=[0-9A-F]+' | cut -d= -f2) words=$(( (1<<m) + 2 )) md5=$md5" >> $K/expect/split.txt
    done
    python3 $K/gen_farm_soc.py --template $U/soc.tmpl.v --elf tile.elf --i-depth 128 --d-depth 16 -o soc_farm.v || die "gen_farm_soc"
    model $d
    echo "provenance tile.elf=$(sha tile.elf) i_ram=$(sha soc_farm_i_ram.hex) model_m=$(sha model_m.c) mandelbrot.c=$(sha $U/sw/mandelbrot.c) rv32.c=$(sha $U/sw/rv32.c) soc.tmpl.v=$(sha $U/soc.tmpl.v) gsm_src=$(sha /usr/local/src/sv2ghdl/yosys/gen_statemachine.cpp)" >> $K/expect/split.txt
    grep -v '^#' $K/expect/split.txt
}

for wl in ${@:-thr split}; do do_$wl; done
echo BUILD-H3-DONE
