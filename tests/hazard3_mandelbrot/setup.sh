#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
#
# setup.sh [DEST] - fetch the upstream test case of the hazard3 suite.
#
# Clones verilator-hazard3-mandelbrot-testbench at the commit pinned in
# UPSTREAM (next to this script) with its Hazard3 submodule (and none of
# Hazard3's own submodules), as upstream's README does:
#     git clone --depth 1 <UPSTREAM_URL> DEST
#     git -C DEST submodule update --init --depth 1 Hazard3
# then checks that both are at the pinned commits.
#
# DEST: the argument; else $HAZARD3_MANDELBROT_DIR; else
# <src root>/verilator-hazard3-mandelbrot-testbench when <src root>
# ($SV2GHDL_SRC_ROOT, default /usr/local/src) is writable; else
# $HOME/verilator-hazard3-mandelbrot-testbench. regress's Hazard3 adapter
# (Regress::Tools::hazard3_mandelbrot_dir) looks in the same places, and runs
# this script when none of them holds a checkout.
#
# The clone is made in DEST.tmp.<pid> and renamed to DEST only when complete,
# so an interrupted or concurrent setup never leaves a half-made DEST.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
. "$here/UPSTREAM"

dest=${1:-${HAZARD3_MANDELBROT_DIR:-}}
if [ -z "$dest" ]; then
    root=${SV2GHDL_SRC_ROOT:-/usr/local/src}
    if [ -d "$root" ] && [ -w "$root" ]; then
        dest=$root/$UPSTREAM_DIRNAME
    else
        dest=$HOME/$UPSTREAM_DIRNAME
    fi
fi

check_pins() {
    d=$1
    head=$(git -C "$d" rev-parse HEAD)
    if [ "$head" != "$UPSTREAM_COMMIT" ]; then
        echo "setup.sh: $d is at $head, not the pinned $UPSTREAM_COMMIT" >&2
        return 1
    fi
    h3=$(git -C "$d/Hazard3" rev-parse HEAD 2>/dev/null || echo none)
    if [ "$h3" != "$HAZARD3_COMMIT" ]; then
        echo "setup.sh: $d/Hazard3 is at $h3, not the pinned $HAZARD3_COMMIT" >&2
        return 1
    fi
    return 0
}

if [ -e "$dest" ]; then
    if [ -f "$dest/soc.tmpl.v" ] && check_pins "$dest"; then
        echo "setup.sh: $dest is ready (upstream $UPSTREAM_COMMIT, Hazard3 $HAZARD3_COMMIT)"
        exit 0
    fi
    echo "setup.sh: $dest exists but is not the pinned checkout; move it away or pass another DEST" >&2
    exit 1
fi

mkdir -p "$(dirname "$dest")"
tmp=$dest.tmp.$$
trap 'rm -rf "$tmp"' EXIT INT TERM

git clone --depth 1 "$UPSTREAM_URL" "$tmp"
if [ "$(git -C "$tmp" rev-parse HEAD)" != "$UPSTREAM_COMMIT" ]; then
    # upstream has moved on: fetch the pinned commit itself
    git -C "$tmp" fetch --depth 1 origin "$UPSTREAM_COMMIT"
    git -C "$tmp" checkout -q --detach "$UPSTREAM_COMMIT"
fi
git -C "$tmp" submodule update --init --depth 1 Hazard3
check_pins "$tmp"

if ! mv -T "$tmp" "$dest" 2>/dev/null; then
    # another setup finished first
    if [ -f "$dest/soc.tmpl.v" ] && check_pins "$dest"; then
        echo "setup.sh: $dest was set up concurrently; using it"
        exit 0
    fi
    echo "setup.sh: could not move $tmp to $dest" >&2
    exit 1
fi
trap - EXIT INT TERM
echo "setup.sh: $dest is ready (upstream $UPSTREAM_COMMIT, Hazard3 $HAZARD3_COMMIT)"
