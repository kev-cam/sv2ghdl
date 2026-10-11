#!/bin/bash
# cst/regen.sh: build spectre_dump on NetlistParse.rs at the pinned commit and dump the vamos-authored
# Spectre fixtures (docs/VAMOS_SPECTRE_DESIGN.md §11 T0 "Statements and the CST oracle": "cst/regen.sh
# (WSL, cargo) rebuilds spectre_dump at the pinned commit and dumps any new vamos fixture").
#
# The NetlistParse.rs corpus dumps in cst/expected/ are never regenerated: they are the upstream files,
# byte for byte (../README).  This script writes
#   cadnip/<deck>.cst   the dump of cadnip/<deck>.scs WITHOUT its title line (the T3b harness strips
#                       line 1 and feeds the body to VACASK; T0 checks that the dump tiles that body)
#   lang/<name>.cst     the dump of lang/<name>.scs, whole (the vamos-authored inputs of phase 1), once
#                       lang/ exists
#
# Usage (WSL; cargo on the PATH, e.g. `. ~/.cargo/env`):
#   tests/vamos/fixtures/spectre/cst/regen.sh [--check] [file.scs ...]
#     --check       compare instead of writing: exit 1 when a committed .cst differs from a fresh dump
#     file.scs ...  only these files (default: every cadnip/*.scs and lang/*.scs)
#   NPRS=<dir>      a NetlistParse.rs checkout to use; it must be at $NPRS_COMMIT (the script checks
#                   out that commit only when the checkout is elsewhere).  Default: a clone of
#                   $NPRS_URL under $WORK
#   WORK=<dir>      where the clone and the spectre_dump crate are built
#                   (default ${TMPDIR:-/tmp}/vamos-spectre-dump)
set -eu
NPRS_URL=https://github.com/NyanCAD/NetlistParse.rs
NPRS_COMMIT=d565fd3e359893fbc4376bb9c7b5608ef786e6bb
HERE=$(cd "$(dirname "$0")" && pwd)            # .../fixtures/spectre/cst
TOP=$(dirname "$HERE")                         # .../fixtures/spectre
WORK=${WORK:-${TMPDIR:-/tmp}/vamos-spectre-dump}
NPRS=${NPRS:-$WORK/NetlistParse.rs}
check=0
files=()
for a in "$@"; do
  case $a in
    --check) check=1 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) files+=("$a") ;;
  esac
done

mkdir -p "$WORK"
if [ ! -d "$NPRS/.git" ]; then
  echo "cloning $NPRS_URL into $NPRS"
  git clone -q "$NPRS_URL" "$NPRS"
fi
if [ "$(git -C "$NPRS" rev-parse HEAD)" != "$NPRS_COMMIT" ]; then
  git -C "$NPRS" fetch -q origin "$NPRS_COMMIT" || true
  git -C "$NPRS" -c advice.detachedHead=false checkout -q "$NPRS_COMMIT"
fi
if [ "$(git -C "$NPRS" rev-parse HEAD)" != "$NPRS_COMMIT" ]; then
  echo "regen.sh: $NPRS is not at $NPRS_COMMIT" >&2
  exit 2
fi

# The dumper: a five-line program over the netlist-syntax crate (the design sessions' spectre_dump).
CRATE=$WORK/spectre_dump
mkdir -p "$CRATE/src"
cat > "$CRATE/Cargo.toml" <<EOF
[package]
name = "spectre_dump"
version = "0.1.0"
edition = "2021"
[dependencies]
netlist-syntax = { path = "$NPRS/crates/netlist-syntax" }
[workspace]
EOF
cat > "$CRATE/src/main.rs" <<'EOF'
fn main() {
    let path = std::env::args().nth(1).expect("usage: spectre_dump file.scs");
    let src = std::fs::read_to_string(&path).unwrap();
    let tree = netlist_syntax::parse_spectre(&src);
    print!("{}", netlist_syntax::dump::dump(&tree));
}
EOF
(cd "$CRATE" && cargo build -q --release)
DUMP=$CRATE/target/release/spectre_dump
echo "spectre_dump built on NetlistParse.rs $(git -C "$NPRS" rev-parse --short HEAD): $DUMP"

if [ ${#files[@]} -eq 0 ]; then
  for f in "$TOP"/cadnip/*.scs; do files+=("$f"); done
  if [ -d "$TOP/lang" ]; then
    for f in "$TOP"/lang/*.scs; do [ -e "$f" ] && files+=("$f"); done
  fi
fi

rc=0
for f in "${files[@]}"; do
  out=${f%.scs}.cst
  case $f in
    */cadnip/*) tail -n +2 "$f" > "$WORK/body.scs"; src=$WORK/body.scs ;;   # line 1 is the title (T3b)
    *) src=$f ;;
  esac
  if [ $check = 1 ]; then
    if "$DUMP" "$src" | cmp -s - "$out"; then
      echo "same     $out"
    else
      echo "DIFFERS  $out"
      rc=1
    fi
  else
    "$DUMP" "$src" > "$out"
    echo "wrote    $out ($(wc -l < "$out") lines)"
  fi
done
exit $rc
