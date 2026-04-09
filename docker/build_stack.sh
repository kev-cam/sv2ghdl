#!/usr/bin/env bash
# Build the sv2ghdl simulation stack from clean upstream sources.
# Run inside the sv2ghdl-base container. Result lands in /opt/sv2ghdl-stack/usr.
set -euo pipefail

PREFIX=/opt/sv2ghdl-stack/usr
SRC=/opt/sv2ghdl-stack/src
mkdir -p "$PREFIX/bin" "$PREFIX/lib" "$SRC"
export PATH="$PREFIX/bin:$PATH"
JOBS=$(nproc)

clone_or_update() {
    local url=$1 dir=$2
    if [[ -d "$SRC/$dir/.git" ]]; then
        git -C "$SRC/$dir" fetch --depth=1 origin && git -C "$SRC/$dir" reset --hard origin/HEAD
    else
        git clone --depth=1 "$url" "$SRC/$dir"
    fi
}

echo "===== iverilog ====="
clone_or_update https://github.com/kev-cam/iverilog.git iverilog
( cd "$SRC/iverilog" && sh autoconf.sh && ./configure --prefix="$PREFIX" \
  && make -j$JOBS && make install )

echo "===== nvc ====="
clone_or_update https://github.com/kev-cam/nvc.git nvc
( cd "$SRC/nvc" && ./autogen.sh && mkdir -p build && cd build \
  && CFLAGS="-g -O2 -fPIC -ftls-model=global-dynamic" \
     ../configure --prefix="$PREFIX" \
  && make -j$JOBS && make install )

echo "===== ghdl ====="
clone_or_update https://github.com/kev-cam/ghdl.git ghdl
( cd "$SRC/ghdl" && ./configure --prefix="$PREFIX" && make -j$JOBS && make install )

echo "===== yosys ====="
clone_or_update https://github.com/YosysHQ/yosys.git yosys
( cd "$SRC/yosys" && make config-gcc \
  && make -j$JOBS PREFIX="$PREFIX" && make install PREFIX="$PREFIX" )

echo "===== sv2ghdl wrappers + sv2vhdl library ====="
cp /opt/sv2ghdl/bin/* "$PREFIX/bin/"
mkdir -p "$PREFIX/lib/nvc"
if [[ -d /opt/sv2ghdl/packages/sv2vhdl ]]; then
    cp -r /opt/sv2ghdl/packages/sv2vhdl "$PREFIX/lib/nvc/"
    ( cd /opt/sv2ghdl/packages/sv2vhdl \
      && "$PREFIX/bin/nvc" --std=2040 --work="$PREFIX/lib/nvc/sv2vhdl" -a *.vhd )
fi

# Yosys-linked helpers
if [[ -f /opt/sv2ghdl/yosys/gen_statemachine.cpp ]]; then
    ( cd /opt/sv2ghdl && YOSYS_DIR="$SRC/yosys" make yosys/gen_statemachine yosys/cover_solve || true )
    cp /opt/sv2ghdl/yosys/gen_statemachine "$PREFIX/bin/" 2>/dev/null || true
    cp /opt/sv2ghdl/yosys/cover_solve      "$PREFIX/bin/" 2>/dev/null || true
fi

echo "===== done. exported tree: $PREFIX ====="
ls "$PREFIX/bin" | head
