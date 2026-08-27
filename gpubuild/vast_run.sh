#!/bin/bash
# Binary-only vast.ai session: poll a created instance to running, attach
# your ssh key, scp PREBUILT binaries (seconds — instance auth can decay
# before a remote toolchain build would finish), run one command in a
# held-open session, log everything.  The API key is used ONLY locally to
# talk to the vast.ai API; it is never copied to the instance.
#
#   export VAST_API_KEY=...            # required, never hardcode it
#   vastai create instance <offer-id> \
#       --image nvidia/cuda:12.4.1-runtime-ubuntu22.04 --ssh --direct --disk 12
#   vast_run.sh <instance-id> 'cd /root && chmod +x farm && ./farm 2000 16384' farm data.bin
#
# Remember: `vastai destroy instance <id>` when done — it bills until then.
set -eu
: "${VAST_API_KEY:?set VAST_API_KEY in the environment}"
export VAST_API_KEY
IID=${1:?instance id}; CMD=${2:?remote command}; shift 2
[ $# -ge 1 ] || { echo "no files to ship" >&2; exit 1; }
OUT=vast_${IID}.log
for i in $(seq 80); do
    read -r ST H P <<< $(vastai show instance "$IID" --raw 2>/dev/null | \
        python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('actual_status'), d.get('ssh_host'), d.get('ssh_port'))")
    [ "$ST" = running ] && break
    sleep 15
done
echo "$(date +%T) status=$ST $H:$P" | tee -a "$OUT"
[ "$ST" = running ] || exit 2
vastai attach ssh "$IID" "$(cat ~/.ssh/id_rsa.pub)" >/dev/null 2>&1 || true
for t in $(seq 30); do
    if scp -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -P "$P" \
        "$@" "root@$H:/root/" >> "$OUT" 2>&1; then
        echo "$(date +%T) binaries shipped (attempt $t)" | tee -a "$OUT"
        timeout "${RUN_TIMEOUT:-3000}" ssh -o ConnectTimeout=10 -p "$P" \
            "root@$H" "$CMD" 2>&1 | tee -a "$OUT"
        echo "$(date +%T) RUN-EXIT=$?" | tee -a "$OUT"
        exit 0
    fi
    sleep 10
done
echo "$(date +%T) ssh window never opened" | tee -a "$OUT"
exit 3
