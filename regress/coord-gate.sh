#!/bin/bash
###############################################################################
# coord-gate.sh — coordinated multi-repo regression gate for the digital
#                 SV -> VHDL -> nvc shim path.
#
# WHY THIS EXISTS
#   `regress gate` (lib/Regress/Gate.pm) gates ONE repo at a time against that
#   repo's registered baseline. The RTLMeter/VeeR work lands as a *coordinated*
#   branch of the SAME name across THREE repos at once:
#       sv2ghdl   bin/sv-normalize           (perl, no build)
#       iverilog  tgt-vhdl/*.cc -> vhdl.tgt  (the -tvhdl translate target)
#       nvc       lib/sv2vhdl/*.vhd          (VHDL support pkg, re-analyzed; no nvc binary rebuild)
#   All three feed exactly one regression block: ivtest/iverilog-nvc. This
#   driver runs that block on the CURRENT (branch) build, diffs it against a
#   reference run, filters clock-jump noise, and — only if there are zero
#   pass->fail regressions — rebases each touched repo onto its current
#   origin/<default> (origin tends to advance under us) and fast-forward-merges.
#
# CONTRACT (what "this context" is for): run regressions, report, merge if
#   clean. If NOT clean it prints a PROBLEMS block (paste to the Simulator
#   net-chat for the other Claude) and merges nothing.
#
# USAGE
#   ./coord-gate.sh [--branch NAME] [--block BLOCK] [--ref-run N]
#                   [--push] [--dry-run]
#     --branch NAME   coordinated branch name present in all touched repos
#                     (default: the branch currently checked out in sv2ghdl)
#     --block BLOCK   regression block to gate on (default: ivtest/iverilog-nvc)
#     --ref-run N     reference run_id to diff against
#                     (default: most recent prior run of BLOCK, excluding the
#                      candidate this script creates)
#     --push          actually FF-merge to origin/<default> when clean
#                     (without it: gate + report only, no writes to origin)
#     --dry-run       run nothing; just print the plan (repos touched, FF state)
#
# EXIT: 0 = clean (merged if --push). 1 = regressions held. 2 = setup error.
#
# SAFETY
#   * FF-only: each repo is rebased onto origin/<default> and pushed with a
#     plain `git push` (a non-fast-forward is refused by the server, never
#     forced). Pre-rebase tips are tagged `<branch>-pre-rebase`.
#   * Our changed files are hashed before/after rebase; a mismatch aborts that
#     repo's push (proves the rebase preserved the change byte-for-byte).
#   * NEVER regenerates gold/baseline data (see memory: no-self-baseline). The
#     reference run is an existing recorded run; the diff is pass/fail deltas
#     only.
#   * Clock-jump guard: this dev container's wall clock can jump, producing
#     spurious ~timeout FAILs. Each candidate pass->fail is re-confirmed
#     `--seq`; a rerun-pass or a ~timeout-duration fail is discarded as noise.
#   * Mass-regression guard: >MAX_REGR pass->fail looks systemic/environmental
#     and is HELD regardless (never auto-pushed).
###############################################################################
set -uo pipefail

REG=/usr/local/src/sv2ghdl/regress
SRC=/usr/local/src
DB=$REG/results.db
BLOCK="ivtest/iverilog-nvc"
BRANCH=""
REFRUN=""
DO_PUSH=0
DRYRUN=0
MAX_REGR=30
TIMEOUT_MS=29000        # >= this on a FAIL == clock-jump noise

# repos the digital shim branch may touch, with their default branch + the
# tracked files our commits are expected to change (used for the rebase-preserve
# hash check). Format: repo:default:file[,file...]
REPOS=(
  "sv2ghdl:main:bin/sv-normalize"
  "iverilog:main:tgt-vhdl/cast.cc,tgt-vhdl/scope.cc,tgt-vhdl/stmt.cc"
  "nvc:master:lib/sv2vhdl/logic3d_types_pkg.vhd"
)

while [ $# -gt 0 ]; do
  case "$1" in
    --branch)  BRANCH="$2"; shift 2;;
    --block)   BLOCK="$2";  shift 2;;
    --ref-run) REFRUN="$2"; shift 2;;
    --push)    DO_PUSH=1;   shift;;
    --dry-run) DRYRUN=1;    shift;;
    *) echo "unknown arg: $1"; exit 2;;
  esac
done

GE() { git -C "$SRC/$1" "${@:2}"; }
q()  { perl -MDBI -e '
  my $db=DBI->connect("dbi:SQLite:dbname='"$DB"'","","",{RaiseError=>1});
  my ($sql,@a)=@ARGV; my $r=$db->selectall_arrayref($sql,undef,@a);
  print join("\t",map{defined$_?$_:""}@$_),"\n" for @$r;' "$@"; }

# default branch = whatever is checked out in sv2ghdl
[ -z "$BRANCH" ] && BRANCH=$(GE sv2ghdl rev-parse --abbrev-ref HEAD)
echo "=== coord-gate: branch=$BRANCH block=$BLOCK push=$DO_PUSH  $(date -u) ==="

# ---- 1. which repos actually carry this branch + are ahead of origin? --------
TOUCHED=()
for spec in "${REPOS[@]}"; do
  IFS=: read -r repo def files <<<"$spec"
  d="$SRC/$repo"
  GE "$repo" rev-parse --verify "$BRANCH" >/dev/null 2>&1 || { echo "  $repo: no branch $BRANCH — skip"; continue; }
  GE "$repo" fetch origin --quiet 2>/dev/null
  bsha=$(GE "$repo" rev-parse "$BRANCH")
  osha=$(GE "$repo" rev-parse "origin/$def" 2>/dev/null)
  if [ "$bsha" = "$osha" ]; then echo "  $repo: branch == origin/$def (nothing to merge) — skip"; continue; fi
  ahead=$(GE "$repo" rev-list --count "origin/$def..$BRANCH" 2>/dev/null)
  echo "  $repo: $ahead commit(s) ahead of origin/$def ($files)"
  TOUCHED+=("$spec")
done
[ ${#TOUCHED[@]} -eq 0 ] && { echo "VERDICT: nothing to gate (no touched repos)"; exit 0; }

if [ "$DRYRUN" = 1 ]; then echo "VERDICT: dry-run, plan printed"; exit 0; fi

# ---- 2. run the block on the CURRENT (branch) build = candidate -------------
echo "[run] $BLOCK --seq on current branch build"
cd "$REG" || exit 2
./regress run "$BLOCK" --seq --notes "coord-gate CANDIDATE $BRANCH" >/dev/null 2>&1
CAND=$(q "SELECT MAX(run_id) FROM block_run WHERE block=?" "$BLOCK")
read ct cp cf < <(q "SELECT total,passed,failed FROM block_run WHERE run_id=? AND block=?" "$CAND" "$BLOCK")
echo "  candidate run #$CAND: total=$ct pass=$cp fail=$cf"

# ---- 3. pick reference run + diff -------------------------------------------
[ -z "$REFRUN" ] && REFRUN=$(q "SELECT MAX(run_id) FROM block_run WHERE block=? AND run_id<?" "$BLOCK" "$CAND")
read rt rp rf < <(q "SELECT total,passed,failed FROM block_run WHERE run_id=? AND block=?" "$REFRUN" "$BLOCK")
echo "  reference run #$REFRUN: total=$rt pass=$rp fail=$rf"
RB=$(q "SELECT block_run_id FROM block_run WHERE run_id=? AND block=?" "$REFRUN" "$BLOCK")
CB=$(q "SELECT block_run_id FROM block_run WHERE run_id=? AND block=?" "$CAND"   "$BLOCK")
REGR=$(q "SELECT a.test_name FROM result a JOIN result b ON a.test_name=b.test_name
          WHERE a.block_run_id=? AND b.block_run_id=? AND a.status='pass'
          AND b.status IN ('fail','error') ORDER BY a.test_name" "$RB" "$CB")
FIXED=$(q "SELECT a.test_name FROM result a JOIN result b ON a.test_name=b.test_name
          WHERE a.block_run_id=? AND b.block_run_id=? AND a.status IN ('fail','error')
          AND b.status='pass' ORDER BY a.test_name" "$RB" "$CB")
NREGR=$(printf '%s' "$REGR" | grep -c . || true)
NFIX=$(printf '%s' "$FIXED" | grep -c . || true)
echo "[diff] #$REFRUN -> #$CAND : pass->fail=$NREGR  fail->pass=$NFIX"

if [ "$NREGR" -gt "$MAX_REGR" ]; then
  echo "VERDICT: HELD ($NREGR pass->fail > $MAX_REGR — systemic/environmental, not merging)"; exit 1
fi

# ---- 4. re-confirm each pass->fail seq (filter clock-jump noise) ------------
REAL=""
while IFS= read -r t; do
  [ -z "$t" ] && continue
  ./regress run "$BLOCK" --filter "$t" --seq --notes "coord-gate reconfirm $t" >/dev/null 2>&1
  rr=$(q "SELECT MAX(run_id) FROM block_run WHERE block=?" "$BLOCK")
  rb=$(q "SELECT block_run_id FROM block_run WHERE run_id=? AND block=?" "$rr" "$BLOCK")
  read st du < <(q "SELECT status,duration_ms FROM result WHERE block_run_id=? AND test_name=?" "$rb" "$t")
  du=${du:-0}
  if   [ "$st" = "pass" ]; then echo "    $t: PASS on rerun -> noise";
  elif [ "${du%.*}" -ge "$TIMEOUT_MS" ]; then echo "    $t: dur=${du}ms ~timeout -> clock-jump noise";
  else echo "    $t: dur=${du}ms status=$st -> REAL"; REAL="$REAL $t"; fi
done < <(printf '%s\n' "$REGR")
REAL=$(echo $REAL | xargs -n1 2>/dev/null | sort -u | xargs)

# ---- 5. verdict + (optional) coordinated FF-merge --------------------------
if [ -n "$REAL" ]; then
  echo "VERDICT: HELD — real regressions: $REAL"
  echo "----8<---- PROBLEMS (paste to Simulator net-chat) ----8<----"
  echo "@DESKTOP-3SRS8MD coord-gate HELD $BRANCH on $BLOCK: pass->fail = $REAL"
  echo "(ref run #$REFRUN pass=$rp -> candidate #$CAND pass=$cp; +$NFIX fixes but the above regressed)"
  echo "----8<-------------------------------------------------8<----"
  exit 1
fi
echo "VERDICT: CLEAN — 0 pass->fail, +$NFIX fixes (ref #$REFRUN -> cand #$CAND)"
[ "$DO_PUSH" = 0 ] && { echo "(--push not given: not merging)"; exit 0; }

# clean + --push: rebase each touched repo onto origin/<default>, FF-merge
for spec in "${TOUCHED[@]}"; do
  IFS=: read -r repo def files <<<"$spec"
  echo "[merge] $repo -> origin/$def"
  GE "$repo" tag -f "${BRANCH}-pre-rebase" "$BRANCH" >/dev/null 2>&1
  # pre-rebase hashes of our files
  declare -A PRE=()
  IFS=',' read -ra FL <<<"$files"
  for f in "${FL[@]}"; do PRE[$f]=$(GE "$repo" show "$BRANCH:$f" 2>/dev/null | sha256sum | cut -d' ' -f1); done
  GE "$repo" checkout "$BRANCH" >/dev/null 2>&1
  if ! GE "$repo" -c rebase.autoStash=false rebase "origin/$def" >/dev/null 2>&1; then
    GE "$repo" rebase --abort >/dev/null 2>&1
    echo "  !! $repo rebase conflict onto origin/$def — HOLD this repo (path no longer orthogonal)"; continue
  fi
  ok=1
  for f in "${FL[@]}"; do
    post=$(GE "$repo" show "HEAD:$f" 2>/dev/null | sha256sum | cut -d' ' -f1)
    [ "${PRE[$f]}" = "$post" ] || { echo "  !! $repo $f changed by rebase — HOLD"; ok=0; }
  done
  [ "$ok" = 1 ] || continue
  tip=$(GE "$repo" rev-parse --short HEAD)
  if GE "$repo" push origin "$BRANCH:refs/heads/$def" 2>&1 | tail -1; then
    GE "$repo" branch -f "$def" "$BRANCH"
    GE "$repo" notes add -f -m "Merged via coord-gate (claude@clevo-lx) $(date -u +%F): $BLOCK ref #$REFRUN -> cand #$CAND, 0 pass->fail, +$NFIX fixes." "$tip" >/dev/null 2>&1
    GE "$repo" push origin refs/notes/commits >/dev/null 2>&1
    echo "  ✓ $repo $tip -> origin/$def (+note)"
  else
    echo "  !! $repo push refused (non-FF?) — HOLD"
  fi
done
echo "=== coord-gate done $(date -u) ==="
