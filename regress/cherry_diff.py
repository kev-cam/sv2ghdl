#!/usr/bin/env python3
"""cherry_diff.py <repo-name> <sha> [geometry]

Review one commit: open it in meld AND show a pop-up with Accept / Skip options.
 - Accept  -> cherry-pick the commit onto the candidate branch (cherry/<user>,
              accumulating); conflicts go through cherry_resolve.py.
 - Skip    -> just close.
Standalone process (its own root window) so the pop-up surfaces reliably.
"""
import os
import signal
import subprocess
import sys
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cherry_picker as cp   # noqa: E402

if len(sys.argv) < 3:
    sys.exit(1)
repo, sha = sys.argv[1], sys.argv[2]
geom = sys.argv[3] if len(sys.argv) > 3 else None
rd = cp.repo_dir(repo)
base = cp.default_base(repo)
branch = cp.candidate_branch(repo)
subj = cp.git(repo, "log", "-1", "--date=short", "--format=%h %ad %s", sha, check=False).stdout.strip()

root = tk.Tk()
root.title("Review commit")
root.update_idletasks()
root.geometry(geom or f"620x220+30+{max(20, root.winfo_screenheight() - 300)}")
root.attributes("-topmost", True)
root.lift()
root.focus_force()

msg = tk.Label(root, justify="left", padx=14, pady=12, font=("sans", 11),
               text=f"Review → {branch} (base {base}):\n  {subj}\n\nmeld is opening (slow)…")
msg.pack(fill="both", expand=True)

# show the commit's diff in meld
diff = subprocess.Popen(
    ["git", "-C", rd, "difftool", "--no-prompt", "--tool=meld", f"{sha}~1", sha],
    start_new_session=True)


def kill(p):
    if p and p.poll() is None:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except Exception:
            pass


def resolve_cb(_r, _files):
    # anchor the resolver near this (visible) review window
    root.update_idletasks()
    g = f"720x320+{max(0, root.winfo_x())}+{max(0, root.winfo_y() + 30)}"
    rc = subprocess.call([sys.executable, os.path.join(HERE, "cherry_resolve.py"), rd, g])
    return {0: True, 1: False, 2: "stop"}.get(rc, False)


def accept():
    for b in btns:
        b.config(state="disabled")
    msg.config(text=f"cherry-picking {sha[:9]} onto {branch}…")
    root.update_idletasks()
    try:
        st = cp.cherry_pick(repo, [sha], branch, base, reset=False, conflict_cb=resolve_cb)
        s = st.get(sha, "?")
    except SystemExit as e:
        s = f"error: {e}"
    verdict = {"clean": "✓ accepted (clean)", "edited": "✎ accepted (with edits)",
               "conflict": "⚠ conflict — not applied"}.get(s, s)
    msg.config(text=f"{subj}\n\n{verdict}\n(branch {branch})")
    for b in btns:
        b.config(state="normal")


bf = tk.Frame(root); bf.pack(pady=6)
btns = [tk.Button(bf, text="Accept", command=accept),
        tk.Button(bf, text="Skip / Close", command=root.destroy)]
for b in btns:
    b.pack(side="left", padx=6)
root.protocol("WM_DELETE_WINDOW", root.destroy)

root.mainloop()
kill(diff)
