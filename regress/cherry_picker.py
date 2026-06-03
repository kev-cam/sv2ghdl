#!/usr/bin/env python3
"""cherry_picker.py — bring changes from other people's repos into our fork,
gated by the regression harness.

Sources of candidate commits (commits reachable from the source but not from
our origin/main):
  * upstream (default)   — the project this fork tracks
                           (iverilog -> steveicarus/iverilog, available locally
                            as iverilog-steve; nvc -> nickg/nvc)
  * a name in cherry_pick.conf   (lines: "name  repo  url-or-path  [ref]")
  * --from URL[#ref]     — any git repo/path on the fly
  * pr:N                 — a kev-cam PR head (needs gh)

You pick the commits (GUI when python3-tk is present, else an interactive/CLI
text picker), cherry-pick them onto a candidate branch off origin/main
(resolving conflicts in meld/kompare/$MERGE_TOOL), gate the result through
./regress, and — if clean vs the repo's main baseline — fast-forward origin/main.

Examples:
  cherry_picker.py --repo iverilog --list                 # upstream commits we don't have
  cherry_picker.py --repo iverilog --from URL#branch --list
  cherry_picker.py --repo iverilog --pick a1b2c3,d4e5f6 --gate --push
  cherry_picker.py --repo iverilog --gui                  # pick interactively
"""
import argparse
import os
import shutil
import subprocess
import sys

SRC_ROOT = "/usr/local/src"
HERE = os.path.dirname(os.path.abspath(__file__))
REGRESS = os.path.join(HERE, "regress")
CONFIG = os.path.join(HERE, "cherry_pick.conf")

# built-in upstream per repo; prefer a local clone (no network) when present
UPSTREAM = {
    "iverilog": {"local": f"{SRC_ROOT}/iverilog-steve",
                 "url": "https://github.com/steveicarus/iverilog.git", "ref": "HEAD"},
    "nvc":      {"url": "https://github.com/nickg/nvc.git", "ref": "HEAD"},
}
DIFFTOOLS = ["meld", "kompare", "kdiff3", "xxdiff", "vimdiff"]


def repo_dir(repo):
    return os.path.join(SRC_ROOT, repo)


def git(repo, *args, check=True, capture=True):
    r = subprocess.run(["git", "-C", repo_dir(repo), *args],
                       text=True, capture_output=capture)
    if check and r.returncode != 0:
        sys.stderr.write(r.stderr or f"git {' '.join(args)} failed\n")
        raise SystemExit(r.returncode)
    return r


def load_config():
    srcs = {}
    if os.path.exists(CONFIG):
        for line in open(CONFIG):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            f = line.split()
            if len(f) >= 3:
                srcs[f[0]] = {"repo": f[1], "url": f[2], "ref": f[3] if len(f) > 3 else "HEAD"}
    return srcs


def resolve_source(repo, source):
    """Return (fetch_from, ref) — fetch_from is a URL or local path."""
    if source in (None, "upstream"):
        u = UPSTREAM.get(repo) or {}
        loc = u.get("local")
        frm = loc if loc and os.path.isdir(loc) else u.get("url")
        if not frm:
            raise SystemExit(f"no upstream known for {repo}")
        return frm, u.get("ref", "HEAD")
    if source.startswith("pr:"):
        return _pr_source(repo, source[3:])
    cfg = load_config().get(source)
    if cfg:
        return cfg["url"], cfg.get("ref", "HEAD")
    # a configured git remote name (origin, a contributor fork, ...)
    if source in git_remotes(repo):
        return source, "HEAD"
    # treat as URL[#ref]
    if "#" in source:
        url, ref = source.split("#", 1)
        return url, ref
    return source, "HEAD"


def git_remotes(repo):
    try:
        return git(repo, "remote", check=False).stdout.split()
    except Exception:
        return []


def default_base(repo):
    """The repo's main branch as a remote-tracking ref (nvc uses master, not
    main). Prefer origin/HEAD, else whichever of origin/main|master exists."""
    r = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    for b in ("origin/main", "origin/master"):
        if git(repo, "rev-parse", "--verify", "--quiet", b, check=False).returncode == 0:
            return b
    return "origin/main"


def rev_exists(repo, ref):
    return git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}",
               check=False).stdout.strip()


def _pr_source(repo, num):
    if not shutil.which("gh"):
        raise SystemExit("pr: source needs the gh CLI (not installed)")
    r = subprocess.run(["gh", "pr", "view", num, "--repo", f"kev-cam/{repo}",
                        "--json", "headRefName,headRepositoryUrl"],
                       text=True, capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f"gh pr view {num}: {r.stderr}")
    import json
    j = json.loads(r.stdout)
    return j["headRepositoryUrl"], j["headRefName"]


def fetch_candidates(repo, source, base="origin/main", limit=50, fetch=True):
    """Fetch the source and return [(sha, subject, author), ...] of commits
    reachable from it but not from base, newest first (capped at limit).

    Refreshes the base (origin) first so a behind / un-pulled local checkout
    doesn't skew the candidate list or the cherry-pick base."""
    if fetch and base.startswith("origin/"):
        r = git(repo, "fetch", "--quiet", "origin", check=False)
        if r.returncode != 0:
            sys.stderr.write(f"  warning: could not fetch origin; using cached {base} (may be stale)\n")
    src = source or "upstream"
    direct = "" if src == "upstream" else rev_exists(repo, src)
    if direct:
        fetched = direct                       # source is an existing ref (origin/<branch>, sha, tag)
    else:
        frm, ref = resolve_source(repo, src)
        git(repo, "fetch", "--quiet", frm, ref)
        fetched = git(repo, "rev-parse", "FETCH_HEAD").stdout.strip()
    behind = git(repo, "rev-list", "--count", f"HEAD..{base}", check=False).stdout.strip()
    if behind and behind not in ("", "0"):
        print(f"  note: local checkout is {behind} commit(s) behind {base}; "
              f"cherry-picks are based on {base}, so picks land on the latest main.")
    log = git(repo, "log", "--no-merges", f"--max-count={limit}", "--date=short",
              "--pretty=%H%x1f%ad%x1f%s%x1f%an", f"{base}..{fetched}").stdout
    out = []
    for line in log.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            out.append(tuple(parts))      # (sha, date, subject, author)
    return out, fetched


def candidate_branch(repo, name=None):
    return name or f"cherry/{os.environ.get('USER', 'pick')}"


def branch_exists(repo, branch):
    return bool(git(repo, "rev-parse", "--verify", "--quiet",
                    f"refs/heads/{branch}", check=False).stdout.strip())


def cherry_pick(repo, shas, branch, base="origin/main", fetch=True, conflict_cb=None, reset=True):
    """Cherry-pick shas (in order) onto `branch`. With reset=True (default) the
    branch is (re)created at base; with reset=False the picks are appended to an
    existing branch so accepts accumulate across calls.
    Returns {sha: status}, status in clean|edited|conflict.

    On conflict, conflict_cb(repo, files) is called; if it returns truthy the
    files are staged and the pick continues (-> 'edited'), otherwise the pick is
    aborted (-> 'conflict'). The default cb resolves interactively (meld +
    prompt) for CLI use; the GUI passes a non-interactive cb so conflicts are
    just marked, not forced into a difftool."""
    cb = conflict_cb or _resolve_conflicts
    if fetch and base.startswith("origin/"):
        git(repo, "fetch", "--quiet", "origin", check=False)
    # clear any leftover in-progress operation so we always start from a clean
    # base (otherwise `checkout -B` fails with "resolve your current index first")
    for op in (("cherry-pick", "--abort"), ("merge", "--abort"), ("am", "--abort")):
        git(repo, *op, check=False)
    # -f so a dirty build-area tree (regenerated work libs, etc.) can't block us
    if reset or not branch_exists(repo, branch):
        git(repo, "checkout", "-f", "-B", branch, base)   # fresh from base
    else:
        git(repo, "checkout", "-f", branch)               # append to existing
    status = {}
    shas = list(shas)
    for i, sha in enumerate(shas):
        r = git(repo, "cherry-pick", "-x", sha, check=False)
        if r.returncode == 0:
            status[sha] = "clean"
            continue
        conflicted = git(repo, "diff", "--name-only", "--diff-filter=U").stdout.split()
        decision = cb(repo, conflicted) if conflicted else False
        if decision == "stop":
            # "No & done": skip this one and stop processing the rest
            git(repo, "cherry-pick", "--abort", check=False)
            status[sha] = "conflict"
            for rest in shas[i + 1:]:
                status[rest] = "skipped"
            break
        if decision:
            git(repo, "add", "-A")
            c = git(repo, "-c", "core.editor=true", "cherry-pick", "--continue", check=False)
            status[sha] = "edited" if c.returncode == 0 else "conflict"
            if c.returncode != 0:
                git(repo, "cherry-pick", "--abort", check=False)
        else:
            git(repo, "cherry-pick", "--abort", check=False)
            status[sha] = "conflict"
    return status


def _difftool():
    for t in [os.environ.get("MERGE_TOOL"), os.environ.get("DIFFTOOL")] + DIFFTOOLS:
        if t and shutil.which(t):
            return t
    return None


def _resolve_conflicts(repo, files):
    tool = _difftool()
    print(f"  conflict in: {', '.join(files)}")
    if not tool:
        print("  no difftool (meld/kompare/...) found — resolve manually in another shell,")
        return _ask("  then [c]ontinue once resolved, or [s]kip this commit? ").strip().lower().startswith("c")
    for f in files:
        subprocess.run([tool, os.path.join(repo_dir(repo), f)])
    return _ask("  resolved? [c]ontinue / [s]kip: ").strip().lower().startswith("c")


def run_gate(repo, branch, push):
    cmd = [REGRESS, "gate", "--repo", repo, "--ref", branch]
    if push:
        cmd.append("--push")
    print(f"==> gating: {' '.join(cmd)}")
    return subprocess.call(cmd)


# ---------------------------------------------------------------- CLI / GUI
def print_candidates(cands):
    if not cands:
        print("  (no candidate commits — source is not ahead of origin/main)")
        return
    for i, (sha, date, subj, author) in enumerate(cands):
        print(f"  [{i:2}] {sha[:9]}  {date}  {subj[:64]:<64}  ({author})")


def _ask(prompt):
    try:
        return input(prompt)
    except EOFError:
        return ""


def interactive_pick(cands):
    print_candidates(cands)
    sel = _ask("\npick commits (indices/shas, comma/space separated; blank=cancel): ").strip()
    if not sel:
        return []
    out = []
    for tok in sel.replace(",", " ").split():
        if tok.isdigit() and int(tok) < len(cands):
            out.append(cands[int(tok)][0])
        else:
            out.append(tok)
    return out


def main(argv):
    ap = argparse.ArgumentParser(description="cherry-pick others' changes, gated by regress")
    ap.add_argument("--repo", choices=["iverilog", "nvc"], required=True)
    ap.add_argument("--source", help="upstream (default) | config-name | pr:N")
    ap.add_argument("--from", dest="frm", help="git URL[#ref] to pull from")
    ap.add_argument("--base", default=None, help="comparison/cherry-pick base (default: repo's origin HEAD)")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--branch", help="candidate branch name")
    ap.add_argument("--pick", help="comma/space separated shas/indices to cherry-pick")
    ap.add_argument("--list", action="store_true", help="just list candidates")
    ap.add_argument("--no-fetch", dest="no_fetch", action="store_true",
                    help="don't refresh origin first (offline; base may be stale)")
    ap.add_argument("--gate", action="store_true", help="gate the candidate branch")
    ap.add_argument("--push", action="store_true", help="FF origin/main if the gate is clean")
    ap.add_argument("--gui", action="store_true", help="pick commits in a GUI")
    a = ap.parse_args(argv)

    a.base = a.base or default_base(a.repo)
    source = a.frm or a.source
    cands, fetched = fetch_candidates(a.repo, source, a.base, a.limit, fetch=not a.no_fetch)
    print(f"{len(cands)} candidate commit(s) from "
          f"{source or 'upstream'} not in {a.base} (showing <= {a.limit}):")

    if a.list:
        print_candidates(cands)
        return 0

    if a.gui:
        try:
            import cherry_gui  # optional Tk front-end (sibling module)
            return cherry_gui.run(a, cands, fetched)
        except Exception as e:
            print(f"GUI unavailable ({e}); falling back to text picker. "
                  f"For the GUI install python3-tk (+ meld for conflicts).")

    shas = []
    if a.pick:
        for tok in a.pick.replace(",", " ").split():
            shas.append(cands[int(tok)][0] if tok.isdigit() and int(tok) < len(cands) else tok)
    else:
        shas = interactive_pick(cands)
    if not shas:
        print("nothing picked.")
        return 0

    branch = candidate_branch(a.repo, a.branch)
    status = cherry_pick(a.repo, shas, branch, a.base, fetch=not a.no_fetch)
    clean = [s for s, st in status.items() if st == "clean"]
    edited = [s for s, st in status.items() if st == "edited"]
    conflict = [s for s, st in status.items() if st == "conflict"]
    applied = clean + edited
    print(f"accepted {len(applied)} onto {branch} "
          f"({len(clean)} clean, {len(edited)} with edits)"
          + (f"; {len(conflict)} conflict, not applied ({', '.join(s[:9] for s in conflict)})" if conflict else ""))
    if not applied:
        return 1
    if a.gate:
        return run_gate(a.repo, branch, a.push)
    print(f"candidate ready on branch {branch}; gate it with: "
          f"{os.path.basename(REGRESS)} gate --repo {a.repo} --ref {branch}"
          + (" --push" if a.push else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
