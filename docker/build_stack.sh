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

# Mode selects which slice of the stack to build:
#   full    - everything (digital + analog)   [default]
#   digital - just the digital simulation tools (iverilog/nvc/ghdl/yosys/sv2ghdl)
#   analog  - just Xyce (and Trilinos, its dependency)
MODE=${1:-full}
case "$MODE" in
    full|digital|analog) ;;
    -h|--help)
        echo "Usage: $0 [full|digital|analog]"
        echo "  full    everything (default)"
        echo "  digital iverilog, nvc, ghdl, yosys, sv2ghdl wrappers"
        echo "  analog  Xyce (+ Trilinos)"
        exit 0 ;;
    *)
        echo "Usage: $0 [full|digital|analog]" >&2
        exit 1 ;;
esac
BUILD_DIGITAL=0
BUILD_ANALOG=0
[[ $MODE = full || $MODE = digital ]] && BUILD_DIGITAL=1
[[ $MODE = full || $MODE = analog  ]] && BUILD_ANALOG=1

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

# smak first — its bundled cmake wrapper provides a modern cmake (≥3.23)
# needed by nvc, Trilinos, and Xyce.
if [[ -L "$PREFIX/bin/cmake" && -x "$PREFIX/bin/smak" ]]; then
    echo "===== smak (already installed, skipping) ====="
else
    echo "===== smak ====="
    clone_or_update https://github.com/kev-cam/smak.git smak
    if [[ -x "$SRC/smak/smak-install" ]]; then
        "$SRC/smak/smak-install" "$PREFIX"
    fi
fi

if [[ $BUILD_DIGITAL = 1 ]]; then
    if [[ -x "$PREFIX/bin/iverilog" ]]; then
        echo "===== iverilog (already built, skipping) ====="
    else
        echo "===== iverilog ====="
        clone_or_update https://github.com/kev-cam/iverilog.git iverilog
        ( cd "$SRC/iverilog" && sh autoconf.sh && ./configure --prefix="$PREFIX" \
          && make -j$JOBS && make install )
    fi

    if [[ -x "$PREFIX/bin/nvc" ]]; then
        echo "===== nvc (already built, skipping) ====="
    else
        echo "===== nvc ====="
        clone_or_update https://github.com/kev-cam/nvc.git nvc
        ( cd "$SRC/nvc" && ./autogen.sh && mkdir -p build && cd build \
          && CFLAGS="-g -O2 -fPIC -ftls-model=global-dynamic" \
             ../configure --prefix="$PREFIX" \
          && make -j$JOBS && make install )
    fi

    if [[ -x "$PREFIX/bin/ghdl" ]]; then
        echo "===== ghdl (already built, skipping) ====="
    else
        echo "===== ghdl ====="
        clone_or_update https://github.com/kev-cam/ghdl.git ghdl
        ( cd "$SRC/ghdl" && ./configure --prefix="$PREFIX" && make -j$JOBS && make install )
    fi

    if [[ -x "$PREFIX/bin/yosys" ]]; then
        echo "===== yosys (already built, skipping) ====="
    else
        echo "===== yosys ====="
        clone_or_update https://github.com/YosysHQ/yosys.git yosys
        ( cd "$SRC/yosys" && make config-gcc \
          && make -j$JOBS PREFIX="$PREFIX" && make install PREFIX="$PREFIX" )
    fi

    # Always re-copy wrappers and helpers (cheap)
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
fi

# Trilinos + Xyce: built when mode is 'full' or 'analog'. Adds ~30-90 min
# and several GB.
if [[ $BUILD_ANALOG = 1 ]]; then
    MAKE_CMD="smak -j$JOBS"

    if [[ -d "$PREFIX/lib/cmake/Trilinos" ]]; then
        echo "===== Trilinos (already built, skipping) ====="
    else
        echo "===== Trilinos 14.4 ====="
        if [[ ! -d "$SRC/Trilinos" ]]; then
            git clone --depth=1 --branch trilinos-release-14-4-branch \
                https://github.com/trilinos/Trilinos.git "$SRC/Trilinos"
        fi

        # Xyce ships the trilinos-base.cmake config we use to seed the build.
        clone_or_update https://github.com/kev-cam/xyce.git xyce

        # Tpetra ETI is disabled because smak's interp doesn't yet evaluate
        # TriBITS' tribits_eti_generate_macros (empty TPETRA_ETI_MANGLING
        # _TYPEDEFS breaks ETI .cpp compiles). Stokhos's Tpetra ETI paths
        # are gated on TpetraCore_ENABLE_EXPLICIT_INSTANTIATION so they
        # also skip. Stokhos_ENABLE_Amesos2 is OFF because its ENSEMBLE ETI
        # path isn't gated on TpetraCore ETI and would still try to build.
        # Kokkos_ENABLE_SERIAL/Tpetra_INST_SERIAL pinned ON because TriBITS'
        # KOKKOS_HAS_TRILINOS PARENT_SCOPE propagation isn't fully modeled.
        # Stokhos PCE+Ensemble headers ship cleanly under this config — Xyce
        # needs Stokhos_Sacado.hpp from the PCE path.
        mkdir -p "$SRC/trilinos-build"
        ( cd "$SRC/trilinos-build" \
          && cmake \
               -C "$SRC/xyce/cmake/trilinos/trilinos-base.cmake" \
               -D CMAKE_INSTALL_PREFIX="$PREFIX" \
               -D BUILD_SHARED_LIBS=ON \
               -D AMD_INCLUDE_DIRS=/usr/include/suitesparse \
               -D Kokkos_ENABLE_SERIAL=ON \
               -D Tpetra_INST_SERIAL=ON \
               -D Tpetra_ENABLE_EXPLICIT_INSTANTIATION=OFF \
               -D Stokhos_ENABLE_Amesos2=OFF \
               "$SRC/Trilinos" \
          && $MAKE_CMD && bash install.sh )
    fi

    if [[ -x "$PREFIX/bin/Xyce" ]]; then
        echo "===== Xyce (already built, skipping) ====="
    else
        echo "===== Xyce ====="
        # Ensure xyce source is present (may have been cloned for Trilinos above)
        clone_or_update https://github.com/kev-cam/xyce.git xyce

        mkdir -p "$SRC/xyce-build"
        ( cd "$SRC/xyce-build" \
          && cmake \
               -D CMAKE_INSTALL_PREFIX="$PREFIX" \
               -D Trilinos_ROOT="$PREFIX" \
               -D BUILD_SHARED_LIBS=ON \
               "$SRC/xyce" \
          && $MAKE_CMD && bash install.sh )
    fi
fi

echo "===== done. exported tree: $PREFIX ====="
ls "$PREFIX/bin" | head
