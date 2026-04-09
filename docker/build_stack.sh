#!/usr/bin/env bash
# Build the sv2ghdl simulation stack from clean upstream sources.
# Run inside the sv2ghdl-base container. Result lands in /opt/sv2ghdl-stack/usr.
set -euo pipefail

PREFIX=${PREFIX:-/opt/sv2ghdl-stack/usr}
SRC=${SRC:-/opt/sv2ghdl-stack/src}
SV2GHDL_DIR=${SV2GHDL_DIR:-/opt/sv2ghdl}
SV2GHDL_REPO=${SV2GHDL_REPO:-https://github.com/kev-cam/sv2ghdl.git}
mkdir -p "$PREFIX/bin" "$PREFIX/lib" "$SRC"
export PATH="$PREFIX/bin:$PATH"
JOBS=$(nproc)

# Self-bootstrap: if the sv2ghdl source tree isn't present (e.g. running this
# script via curl|bash on a bare WSL), clone it first.
if [[ ! -d "$SV2GHDL_DIR" ]]; then
    sudo mkdir -p "$(dirname "$SV2GHDL_DIR")" 2>/dev/null || mkdir -p "$(dirname "$SV2GHDL_DIR")"
    git clone --depth=1 "$SV2GHDL_REPO" "$SV2GHDL_DIR" 2>/dev/null \
        || sudo git clone --depth=1 "$SV2GHDL_REPO" "$SV2GHDL_DIR"
fi

clone_or_update() {
    local url=$1 dir=$2
    if [[ -d "$SRC/$dir/.git" ]]; then
        git -C "$SRC/$dir" fetch --depth=1 origin \
            && git -C "$SRC/$dir" reset --hard origin/HEAD \
            && git -C "$SRC/$dir" submodule update --init --recursive --depth=1
    else
        git clone --depth=1 --recurse-submodules --shallow-submodules "$url" "$SRC/$dir"
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
cp "$SV2GHDL_DIR"/bin/* "$PREFIX/bin/"
mkdir -p "$PREFIX/lib/nvc"
if [[ -d "$SV2GHDL_DIR"/packages/sv2vhdl ]]; then
    cp -r "$SV2GHDL_DIR"/packages/sv2vhdl "$PREFIX/lib/nvc/"
    ( cd "$SV2GHDL_DIR"/packages/sv2vhdl \
      && "$PREFIX/bin/nvc" --std=2040 --work="$PREFIX/lib/nvc/sv2vhdl" -a *.vhd )
fi

# Yosys-linked helpers
if [[ -f "$SV2GHDL_DIR"/yosys/gen_statemachine.cpp ]]; then
    ( cd "$SV2GHDL_DIR" && YOSYS_DIR="$SRC/yosys" make yosys/gen_statemachine yosys/cover_solve || true )
    cp "$SV2GHDL_DIR"/yosys/gen_statemachine "$PREFIX/bin/" 2>/dev/null || true
    cp "$SV2GHDL_DIR"/yosys/cover_solve      "$PREFIX/bin/" 2>/dev/null || true
fi

echo "===== smak (parallel make; nvc/Trilinos builds will use it soon) ====="
clone_or_update https://github.com/kev-cam/smak.git smak
# smak is pure Perl — no compile step, just install the entry-point.
if [[ -f "$SRC/smak/smak" ]]; then
    install -m 0755 "$SRC/smak/smak" "$PREFIX/bin/smak"
fi
if [[ -f "$SRC/smak/Smak.pm" ]]; then
    mkdir -p "$PREFIX/share/perl5"
    cp "$SRC/smak/Smak.pm" "$PREFIX/share/perl5/"
fi

# Trilinos + Xyce: skipped by default — adds ~30-90 minutes and several GB.
# Set BUILD_XYCE=1 to enable. Uses smak if available, else plain make -j.
if [[ "${BUILD_XYCE:-0}" = 1 ]]; then
    MAKE_CMD="make -j$JOBS"
    command -v smak >/dev/null 2>&1 && MAKE_CMD="smak -j$JOBS"

    echo "===== Trilinos 14.4 ====="
    if [[ ! -d "$SRC/Trilinos" ]]; then
        git clone --depth=1 --branch trilinos-release-14-4-branch \
            https://github.com/trilinos/Trilinos.git "$SRC/Trilinos"
    fi

    # Xyce ships the trilinos-base.cmake config we use to seed the build.
    clone_or_update https://github.com/kev-cam/xyce.git xyce

    mkdir -p "$SRC/trilinos-build"
    ( cd "$SRC/trilinos-build" \
      && cmake \
           -C "$SRC/xyce/cmake/trilinos/trilinos-base.cmake" \
           -D CMAKE_INSTALL_PREFIX="$PREFIX" \
           -D BUILD_SHARED_LIBS=ON \
           -D AMD_INCLUDE_DIRS=/usr/include/suitesparse \
           "$SRC/Trilinos" \
      && $MAKE_CMD install )

    echo "===== Xyce ====="
    mkdir -p "$SRC/xyce-build"
    ( cd "$SRC/xyce-build" \
      && cmake \
           -D CMAKE_INSTALL_PREFIX="$PREFIX" \
           -D Trilinos_ROOT="$PREFIX" \
           -D BUILD_SHARED_LIBS=ON \
           "$SRC/xyce" \
      && $MAKE_CMD install )
fi

echo "===== done. exported tree: $PREFIX ====="
ls "$PREFIX/bin" | head
