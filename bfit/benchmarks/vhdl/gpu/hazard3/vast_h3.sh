#!/bin/bash
# vast_h3.sh - rent ONE vast.ai node, ship ONLY the two prebuilt binaries +
# sweep_h3.sh + h3expect.txt (no source, no toolchain), run the sweep in one
# held-open ssh session (gpubuild/vast_run.sh), log, DESTROY the instance,
# then certify from the log here.  bfit GPU-farm kit (PolyForm Noncommercial
# 1.0.0).  Not run by the porter or the reviewer (no rental in that workflow).
#
#   read -rs VAST_API_KEY && export VAST_API_KEY   # typed at a prompt: not echoed, not in
#                                                  # shell history, never on any argv
#   ./vast_h3.sh RTX_4090                          # one card
#   NGPUS=8 MAXDPH=5.00 ./vast_h3.sh RTX_4090      # 8-card node
#   ./vast_h3.sh --reap                            # destroy every instance labelled h3farm-*
# Instead of the env var, the key may sit in vastai's own key file
# (${XDG_CONFIG_HOME:-~/.config}/vastai/vast_api_key, chmod 600); it is then exported
# for gpubuild/vast_run.sh, which insists on the env var.  Never write
# 'VAST_API_KEY=... cmd' (shell history) or wrap it in 'wsl bash -lc "..."' (argv).
# env: NGPUS (1), MAXDPH (dollar/h cap, default 1.0), VERIFIED (verified=true),
#      RUN_TIMEOUT (s the remote sweep may run, default 2400),
#      MAX_SESSION (s from create to forced teardown, default RUN_TIMEOUT + 2100),
#      EXCLUDE (machine ids), VASTAI (CLI; default ~/vastai-venv/bin/vastai,
#      ~/.local/bin/vastai or PATH), MINBS/NLIST/MLIST (passed to the sweep)
# Who destroys the instance: THIS script, on every exit it can see (normal end, error,
# Ctrl-C, TERM, HUP, the MAX_SESSION deadline) - by id, and by its unique label if the
# create reply could not be parsed - before the local certification; it retries and then
# VERIFIES the instance is gone, and says so loudly (exit 6) when it cannot.  SIGKILL, a
# WSL/VM shutdown or host sleep cannot be trapped: after any abnormal end run
# './vast_h3.sh --reap' or 'vastai show instances'.  Worst-case bill per rental:
# MAXDPH x (MAX_SESSION + teardown) = ~76 min at the defaults (~78 if the destroy is retried).
set -u
GPU=${1:?gpu_name e.g. RTX_4090 RTX_3090 H100_SXM L40S, or --reap}
K=$(cd "$(dirname "$0")" && pwd); cd "$K"
V=${VASTAI:-}
if [ -z "$V" ]; then
    for c in "$HOME/vastai-venv/bin/vastai" "$HOME/.local/bin/vastai" "$(command -v vastai 2>/dev/null)"; do
        [ -n "$c" ] && [ -x "$c" ] && { V=$c; break; }
    done
fi
[ -n "$V" ] && [ -x "$V" ] || { echo "vast_h3: no vastai CLI (set VASTAI=)"; exit 1; }
export PATH="$(dirname "$V"):$PATH" VASTAI_NO_UPDATE_CHECK=1      # vast_run.sh calls plain 'vastai'
KF=${XDG_CONFIG_HOME:-$HOME/.config}/vastai/vast_api_key
if [ -z "${VAST_API_KEY:-}" ] && [ -r "$KF" ]; then VAST_API_KEY=$(cat "$KF"); fi
: "${VAST_API_KEY:?set VAST_API_KEY (read -rs VAST_API_KEY; export VAST_API_KEY) or save it in $KF}"
export VAST_API_KEY
LOG=$K/vast_h3.log
say() { echo "$(date +%T) $*" | tee -a "$LOG"; }

ids_by_label() {   # ids of my instances whose label is $1 (or starts with $1 when $2 = prefix)
    "$V" show instances --raw 2>>"$LOG" | python3 -c '
import json, sys
try:
    rows = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for r in rows if isinstance(rows, list) else []:
    l = str(r.get("label") or "")
    if (l.startswith(sys.argv[1]) if sys.argv[2:] == ["prefix"] else l == sys.argv[1]) and r.get("id") is not None:
        print(r["id"])' "$@"
}
gone() {           # 0 only when the API positively reports instance $1 as gone
    "$V" show instance "$1" --raw 2>>"$LOG" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(1)                                  # no answer: not confirmed
sys.exit(0 if d is None or (isinstance(d, dict) and "instances" in d and d["instances"] is None) else 1)'
}
destroy() {        # destroy $1, retrying, until the API reports it gone
    local id=$1 t w
    for t in 1 2 3 4 5 6; do
        "$V" destroy instance "$id" -y --raw >> "$LOG" 2>&1
        for w in 1 2 3 4 5 6; do
            gone "$id" && { say "destroyed $id (verified gone)"; return 0; }
            sleep "${DESTROY_POLL:-5}"
        done
        say "destroy of $id not confirmed (attempt $t)"
    done
    say "!!!!! INSTANCE $id MAY STILL BE RUNNING AND BILLING - destroy it by hand: vastai destroy instance $id (or ./vast_h3.sh --reap)"
    return 1
}

if [ "$GPU" = --reap ]; then
    rc=0; n=0
    for id in $(ids_by_label h3farm- prefix); do say "reaping $id"; n=$((n + 1)); destroy "$id" || rc=6; done
    say "reap: $n instance(s) labelled h3farm-*"; exit $rc
fi

NGPUS=${NGPUS:-1}; TAG=${GPU}x${NGPUS}; MAXDPH=${MAXDPH:-1.0}
export RUN_TIMEOUT=${RUN_TIMEOUT:-2400}; MAX_SESSION=${MAX_SESSION:-$((RUN_TIMEOUT + 2100))}
LABEL=h3farm-$TAG-$(date +%s)-$$
IID=; LABEL_USED=; TORN=; TD_RC=0; RP=; ST=
teardown() {       # idempotent: stop the session, destroy what this run created (by id and by label)
    [ -n "$TORN" ] && return $TD_RC
    TORN=1
    [ -n "$RP" ] && kill "$RP" 2>/dev/null
    [ -n "$LABEL_USED" ] || return 0
    local id ids; ids=$(printf '%s\n' $IID $(ids_by_label "$LABEL") | sort -u)
    [ -n "$ids" ] || say "teardown: no instance with label $LABEL"
    for id in $ids; do destroy "$id" || TD_RC=6; done
    return $TD_RC
}
on_exit() { local rc=$?; trap - EXIT HUP INT TERM; teardown || rc=6; [ -n "$ST" ] && rm -rf "$ST"; exit $rc; }
trap on_exit EXIT; trap 'exit 129' HUP; trap 'exit 130' INT; trap 'exit 143' TERM

python3 check_h3.py --write-expect h3expect.txt || exit 1          # refuses until cert_h3.sh has passed
(cd bin && sha256sum -c --quiet SHA256SUMS) || { echo "bin/ does not match SHA256SUMS"; exit 1; }
OFFER=$("$V" search offers "gpu_name=$GPU num_gpus=$NGPUS rentable=true ${VERIFIED:-verified=true} cuda_vers>=12.4 reliability>0.95 inet_down>50 disk_space>=10" -o 'dph' --raw 2>>"$LOG" | \
  python3 -c "import json,sys; o=json.load(sys.stdin); o=[x for x in o if x.get('dph_total',99)<$MAXDPH and str(x.get('machine_id')) not in '${EXCLUDE:-}'.split(',')]; print(o[0]['id'], o[0]['dph_total'], str(o[0].get('gpu_name')).replace(' ','_'), o[0].get('cuda_max_good'), str(o[0].get('geolocation','?')).replace(' ','_')) if o else print('NONE')")
say "offer: $OFFER"
read -r OID DPH GN CV GEO <<< "$OFFER"
[ -n "${OID:-}" ] && [ "$OID" != NONE ] || { echo "no $TAG offer under \$$MAXDPH/h"; exit 2; }
LABEL_USED=1                                    # from here on teardown also searches by label
CR=$("$V" create instance "$OID" --image nvidia/cuda:12.4.1-runtime-ubuntu22.04 --ssh --direct --disk 10 \
       --label "$LABEL" --cancel-unavail --raw 2>>"$LOG")
T_END=$((SECONDS + MAX_SESSION))
IID=$(printf '%s' "$CR" | python3 -c '
import json, re, sys
s = sys.stdin.read()
try:
    print(int(json.loads(s)["new_contract"]))
except Exception:
    m = re.search(r"\"new_contract\"\s*:\s*(\d+)", s)
    print(m.group(1) if m else "")')
[ -n "$IID" ] || IID=$(ids_by_label "$LABEL" | head -1)     # reply unparsable: find it by its label
[ -n "$IID" ] || { say "create gave no instance (label $LABEL): $CR"; exit 3; }
say "instance $IID ($LABEL) on offer $OID ($GN cuda<=$CV \$${DPH}/h $GEO), teardown by $(date -d "+$MAX_SESSION sec" +%T 2>/dev/null || echo "+${MAX_SESSION}s")"
ST=$(mktemp -d); cp sweep_h3.sh h3expect.txt bin/h3thr_gpu bin/h3split_gpu "$ST"/
SW_ENV="MINBS='${MINBS:-1 3 4}' MLIST='${MLIST:-0 2 4}'"; [ -n "${NLIST:-}" ] && SW_ENV="$SW_ENV NLIST='$NLIST'"
# the session runs in the background so that a signal or the deadline reaches teardown at once
"${VAST_RUN:-/usr/local/src/sv2ghdl/gpubuild/vast_run.sh}" "$IID" "cd /root && echo DPH=$DPH GPU=$TAG && $SW_ENV bash ./sweep_h3.sh" \
    "$ST"/sweep_h3.sh "$ST"/h3expect.txt "$ST"/h3thr_gpu "$ST"/h3split_gpu &
RP=$!
while kill -0 "$RP" 2>/dev/null; do
    [ $SECONDS -ge $T_END ] && { say "MAX_SESSION ${MAX_SESSION}s reached: ending the session"; break; }
    sleep 5
done
teardown; TRC=$?                                # destroy BEFORE the local certification
mkdir -p results; mv -f vast_${IID}.log results/vast_h3_${TAG}_${IID}.log 2>/dev/null
python3 check_h3.py results/vast_h3_${TAG}_${IID}.log; CRC=$?
[ $TRC = 0 ] || exit 6
exit $CRC
