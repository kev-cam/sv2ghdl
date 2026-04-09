#!/usr/bin/env bash
# Build the sv2ghdl simulation stack from clean upstream sources.
#
# By design this builds as a regular user. If invoked as root (typical inside
# a container), it creates the user `claude` (group `dev`) and re-execs itself
# as that user. The build then lives under ~claude — never under /opt or any
# system directory — which keeps file ownership sane and matches how a normal
# developer would do it on their own machine.
set -euo pipefail

BUILD_USER=${BUILD_USER:-claude}
BUILD_GROUP=${BUILD_GROUP:-dev}

if [[ $EUID -eq 0 ]]; then
    if ! getent group "$BUILD_GROUP" >/dev/null; then
        groupadd "$BUILD_GROUP"
    fi
    if ! id -u "$BUILD_USER" >/dev/null 2>&1; then
        useradd -m -g "$BUILD_GROUP" -s /bin/bash "$BUILD_USER"
    fi
    echo "==> re-executing as $BUILD_USER:$BUILD_GROUP"
    # Forward any user-set overrides; otherwise let the child pick its defaults
    # under ~claude.
    exec runuser -u "$BUILD_USER" -- bash "$0" "$@"
fi

# --- now running as the regular build user ----------------------------------
HOME_DIR=$(getent passwd "$(id -un)" | cut -d: -f6)
PREFIX=${PREFIX:-$HOME_DIR/sv2ghdl-stack/usr}
SRC=${SRC:-$HOME_DIR/sv2ghdl-stack/src}
SV2GHDL_DIR=${SV2GHDL_DIR:-$HOME_DIR/sv2ghdl}
SV2GHDL_REPO=${SV2GHDL_REPO:-https://github.com/kev-cam/sv2ghdl.git}
mkdir -p "$PREFIX/bin" "$PREFIX/lib" "$SRC"
export PATH="$PREFIX/bin:$PATH"
JOBS=$(nproc)

# Self-bootstrap: if the sv2ghdl source tree isn't present (e.g. running this
# script via curl|bash on a bare WSL), clone it into the user's home.
if [[ ! -d "$SV2GHDL_DIR" ]]; then
    git clone --depth=1 "$SV2GHDL_REPO" "$SV2GHDL_DIR"
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
# ENABLE_LIBYOSYS=1 builds libyosys.so so gen_statemachine/cover_solve can link.
( cd "$SRC/yosys" && make config-gcc \
  && make -j$JOBS PREFIX="$PREFIX" ENABLE_LIBYOSYS=1 \
  && make install PREFIX="$PREFIX" ENABLE_LIBYOSYS=1 )

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
