#!/bin/bash
# Ship prebuilt binaries + data to any ssh-reachable GPU host and run a
# command there in one held-open session.  Nothing but the listed files
# leaves this machine; results come back on stdout for you to verify
# against locally-computed checksums (never trust a remote host's output
# without a certification value it could not have guessed).
#
#   ship_run.sh user@host 'cd ~ && chmod +x farm && ./farm 1500 4096' farm data.bin
#
# WSL targets: run the remote command through `wsl -e bash -c '...'` and
# keep THIS session open for the duration — nohup'd WSL jobs die when the
# last wsl.exe session exits.
set -eu
HOST=${1:?user@host}; CMD=${2:?remote command}; shift 2
[ $# -ge 1 ] || { echo "no files to ship" >&2; exit 1; }
scp -o ConnectTimeout=15 "$@" "$HOST":
exec ssh -o ConnectTimeout=15 "$HOST" "$CMD"
