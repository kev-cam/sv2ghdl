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
#   verify  - optional extra: clone Nuitka source for verification work
# -mpi (anywhere on the line) builds the distributed-parallel Trilinos + Xyce
# instead of the serial stack; the mode (full/digital/analog/verify) is the
# positional arg.
MPI=0
MODE=
for arg in "$@"; do
    case "$arg" in
        -mpi) MPI=1 ;;
        full|digital|analog|verify) MODE="$arg" ;;
        -h|--help)
            echo "Usage: $0 [full|digital|analog|verify] [-mpi]"
            echo "  full    everything (default)"
            echo "  digital iverilog, nvc, ghdl, yosys, sv2ghdl wrappers"
            echo "  analog  Xyce (+ Trilinos)"
            echo "  verify  clone Nuitka source (install step deferred)"
            echo "  -mpi    build distributed-parallel (MPI) Trilinos + Xyce"
            echo "          (Xyce_PARALLEL_MPI: large-circuit / cloud scale-out)."
            echo "          Needs an MPI toolchain (mpicc/mpicxx)."
            echo ""
            echo "Env:"
            echo "  RUN_TESTS=1         after analog/full, run a Xyce_Regression subset"
            echo "  TEST_LABEL=...      ctest label to run (default: vpwl, ~26 tests)"
            echo "  XYCE_VARIANT=ours|stock  which Xyce to test (default: ours)"
            echo "  BUILD_STOCK_XYCE=1  also build upstream Sandia Xyce against our Trilinos"
            echo "  STOCK_XYCE_PREFIX=  install prefix for stock Xyce (default /opt/xyce-stock)"
            exit 0 ;;
        *)
            echo "Usage: $0 [full|digital|analog|verify] [-mpi]" >&2
            exit 1 ;;
    esac
done
MODE=${MODE:-full}
BUILD_DIGITAL=0
BUILD_ANALOG=0
BUILD_VERIFY=0
[[ $MODE = full || $MODE = digital ]] && BUILD_DIGITAL=1
[[ $MODE = full || $MODE = analog  ]] && BUILD_ANALOG=1
[[ $MODE = verify ]]                  && BUILD_VERIFY=1

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
        # ENABLE_LIBYOSYS=1 builds libyosys.so so sv2ghdl/yosys/gen_statemachine
        # can link against it. ENABLE_PYOSYS=0 avoids a Python build dep
        # not used by the wrappers.
        ( cd "$SRC/yosys" && make config-gcc \
          && echo 'ENABLE_LIBYOSYS := 1' >> Makefile.conf \
          && make -j$JOBS PREFIX="$PREFIX" \
          && make install PREFIX="$PREFIX" )
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

    # -mpi: build the distributed-parallel stack. We pass the deltas from
    # xyce/cmake/trilinos/trilinos-MPI-base.cmake (which is just trilinos-base
    # + these) as -D flags on top of the base cache file, so we don't rely on a
    # cache-file include(). mpicc/mpicxx make the Xyce link pull -lmpi in
    # automatically; build-stats is off because smak's cmake interpreter doesn't
    # emit the build_stat_*_wrapper.sh that feature points the compiler at.
    TRI_MPI_ARGS=()
    XYCE_MPI_ARGS=()
    if [[ $MPI = 1 ]]; then
        echo "===== MPI build: distributed Trilinos + Xyce (Xyce_PARALLEL_MPI) ====="
        TRI_MPI_ARGS=(
            -D TPL_ENABLE_MPI=ON
            -D Trilinos_ENABLE_Zoltan=ON
            -D Trilinos_ENABLE_Isorropia=ON
            -D Trilinos_ENABLE_BUILD_STATS=OFF
            -D CMAKE_C_COMPILER=mpicc
            -D CMAKE_CXX_COMPILER=mpicxx
        )
        XYCE_MPI_ARGS=(
            -D CMAKE_C_COMPILER=mpicc
            -D CMAKE_CXX_COMPILER=mpicxx
        )
        # GCC 15+ tightened -Wtemplate-body vs Trilinos 14.4's KokkosKernels ETI;
        # soften it on newer hosts. The AMS image base (Debian Trixie / GCC 14)
        # needs nothing.
        case "$(g++ -dumpversion)" in 1[5-9]*|[2-9][0-9]*)
            TRI_MPI_ARGS+=(-D CMAKE_CXX_FLAGS=-Wno-template-body) ;;
        esac
    fi

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

        mkdir -p "$SRC/trilinos-build"
        ( cd "$SRC/trilinos-build" \
          && cmake \
               -C "$SRC/xyce/cmake/trilinos/trilinos-base.cmake" \
               "${TRI_MPI_ARGS[@]}" \
               -D CMAKE_INSTALL_PREFIX="$PREFIX" \
               -D BUILD_SHARED_LIBS=ON \
               -D AMD_INCLUDE_DIRS=/usr/include/suitesparse \
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
               "${XYCE_MPI_ARGS[@]}" \
               -D CMAKE_INSTALL_PREFIX="$PREFIX" \
               -D Trilinos_ROOT="$PREFIX" \
               -D BUILD_SHARED_LIBS=ON \
               "$SRC/xyce" \
          && $MAKE_CMD && bash install.sh )
    fi

    # Optional: also build the upstream Sandia Xyce alongside, sharing
    # our Trilinos install. Useful for paired regression-test runs to
    # disambiguate fork-specific failures vs. general Xyce/Trilinos
    # issues. Off by default; set BUILD_STOCK_XYCE=1 to enable.
    # Lands under STOCK_XYCE_PREFIX (default /opt/xyce-stock) so it
    # doesn't collide with our Xyce at $PREFIX/bin/Xyce.
    if [[ "${BUILD_STOCK_XYCE:-0}" = 1 ]]; then
        STOCK_XYCE_PREFIX=${STOCK_XYCE_PREFIX:-/opt/xyce-stock}
        if [[ -x "$STOCK_XYCE_PREFIX/bin/Xyce" ]]; then
            echo "===== stock Xyce (already built, skipping) ====="
        else
            echo "===== stock Xyce (upstream Sandia, prefix $STOCK_XYCE_PREFIX) ====="
            if [[ ! -d "$SRC/Xyce-stock" ]]; then
                git clone --depth=1 \
                    https://github.com/Xyce/Xyce.git "$SRC/Xyce-stock"
            fi
            mkdir -p "$SRC/Xyce-stock-build"
            ( cd "$SRC/Xyce-stock-build" \
              && cmake \
                   -D CMAKE_INSTALL_PREFIX="$STOCK_XYCE_PREFIX" \
                   -D Trilinos_ROOT="$PREFIX" \
                   -D BUILD_SHARED_LIBS=ON \
                   "$SRC/Xyce-stock" \
              && $MAKE_CMD && bash install.sh )
        fi
    fi
fi

# Nuitka: optional, used for verification work. Source-only for now —
# install step is intentionally deferred until the install method is decided.
if [[ $BUILD_VERIFY = 1 ]]; then
    echo "===== Nuitka (source only) ====="
    clone_or_update https://github.com/Nuitka/Nuitka.git Nuitka
fi

# Optional: run a short Xyce regression subset to validate the Xyce build.
# Off by default (full nightly run is hours); set RUN_TESTS=1 to enable.
# Override the label with TEST_LABEL (e.g. nightly, vpwl, BREAK).
# Pick which Xyce to test with XYCE_VARIANT=ours|stock (default ours).
if [[ "${RUN_TESTS:-0}" = 1 && $BUILD_ANALOG = 1 ]]; then
    XYCE_VARIANT=${XYCE_VARIANT:-ours}
    case "$XYCE_VARIANT" in
        ours)  test_prefix="$PREFIX"; test_dir="$SRC/xyce-test" ;;
        stock) test_prefix="${STOCK_XYCE_PREFIX:-/opt/xyce-stock}"
               test_dir="$SRC/xyce-stock-test" ;;
        *)     echo "Unknown XYCE_VARIANT=$XYCE_VARIANT (use ours|stock)" >&2
               exit 1 ;;
    esac

    if [[ -x "$test_prefix/bin/Xyce" ]]; then
        echo "===== Xyce_Regression: $XYCE_VARIANT (label: ${TEST_LABEL:-vpwl}) ====="
        if [[ ! -d "$SRC/Xyce_Regression" ]]; then
            git clone --depth=1 \
                https://github.com/Xyce/Xyce_Regression.git "$SRC/Xyce_Regression"
        fi
        # PyMS plugin compile path needs xyce_device_gen.py + the install
        # tree's headers/libXyceLib.so. Only relevant when XYCE_VARIANT=ours.
        if [[ "$XYCE_VARIANT" = ours ]]; then
            export PYMS_DIR="$SRC/xyce/utils/PyMS"
        fi
        export Xyce_DIR="$test_prefix/bin"
        mkdir -p "$test_dir"
        ( cd "$test_dir" \
          && cmake "$SRC/Xyce_Regression" \
          && ctest -L "${TEST_LABEL:-vpwl}" -j "$JOBS" --output-on-failure \
          || echo "WARN: regression tests reported failures (continuing)" )
    else
        echo "===== Xyce_Regression (skipped: $test_prefix/bin/Xyce missing) ====="
    fi
fi

echo "===== done. exported tree: $PREFIX ====="
ls "$PREFIX/bin" | head
