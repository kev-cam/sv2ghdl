#!/usr/bin/env bash
# Host-side helper: build the stack inside a running sv2ghdl container and
# rsync the result onto the host.
#
# Usage:
#     docker/export.sh                       # build + export to /tmp/sv2ghdl-export
#     docker/export.sh /custom/path
#     SV2GHDL_PORT=2222 docker/export.sh     # override SSH port
#
# Prerequisites (docker or podman — both work, no code changes needed):
#     docker build -t sv2ghdl-base -f docker/Dockerfile .
#     docker run -d --name sv2ghdl -p 2222:22 -p 8080:80 sv2ghdl-base
#  -or-
#     podman build -t sv2ghdl-base -f docker/Dockerfile .
#     podman run -d --name sv2ghdl -p 2222:22 -p 8080:80 sv2ghdl-base
#
# Visit http://localhost:8080 for the in-container instructions page.
# This script itself only uses ssh + rsync, so it's runtime-agnostic.

set -euo pipefail

DEST="${1:-/tmp/sv2ghdl-export}"
PORT="${SV2GHDL_PORT:-2222}"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p $PORT"

mkdir -p "$DEST"

echo "==> Triggering build inside container (this takes a while)"
ssh $SSH_OPTS root@localhost /opt/sv2ghdl/docker/build_stack.sh

echo "==> Rsyncing /opt/sv2ghdl-stack/usr/ -> $DEST"
rsync -av -e "ssh $SSH_OPTS" \
    root@localhost:/opt/sv2ghdl-stack/usr/ "$DEST/"

echo
echo "Done. To install on the host:"
echo "    sudo rsync -a $DEST/ /usr/local/"
