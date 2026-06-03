#!/usr/bin/env python3
"""cherry_resolve.py <repo_dir> [geometry]

One window managing all the conflicted files of the current (mid-cherry-pick)
state: a row per file with its own meld button and a live marker
(· untouched / ✓ resolved / ⚠ still conflicted), and Accept / Skip commit /
No & done at the bottom. Open each file in meld, resolve, then Accept.

Standalone (own root window) so it surfaces reliably. Exit code:
    0 = accept   1 = skip this commit   2 = No & done (stop the rest)
"""
import os
import signal
import subprocess
import sys
import tkinter as tk

if len(sys.argv) < 2:
    sys.exit(1)
repo = sys.argv[1]
geom = sys.argv[2] if len(sys.argv) > 2 else None


def git(*a):
    return subprocess.run(["git", "-C", repo, *a], text=True, capture_output=True)


def unmerged():
    return set(git("diff", "--name-only", "--diff-filter=U").stdout.split())


files = sorted(unmerged())
N = len(files)
if N == 0:
    sys.exit(0)

root = tk.Tk()
root.title(f"Resolve conflict — {N} file(s)")
root.update_idletasks()
h = min(150 + 28 * N, 560)
# caller passes a geometry anchored to its (visible) window; default to the
# top-left region — NOT screen bottom, which is off-screen on a tall virtual desktop
root.geometry(geom or f"720x{h}+30+120")
root.attributes("-topmost", True)
root.lift()
root.focus_force()

tk.Label(root, anchor="w", padx=12, pady=6,
         text=f"{N} conflicted file(s): per file use meld, or accept (take incoming) / "
              "reject (keep ours). Then Apply.").pack(fill="x")

procs = []
rows = {}


def kill_all():
    for p in procs:
        if p.poll() is None:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except Exception:
                pass


def refresh_marks():
    um = unmerged()
    for f, mk in rows.items():
        if f in um:
            mk.config(text="⚠", fg="#b00020")
        else:
            mk.config(text="✓", fg="#1b9e3e")


def open_meld(f):
    p = subprocess.Popen(
        ["git", "-C", repo, "mergetool", "--no-prompt", "--tool=meld", "--", f],
        start_new_session=True)
    procs.append(p)

    def poll():
        if p.poll() is not None:
            refresh_marks()
            return
        root.after(300, poll)
    poll()


def take(f, side):
    # accept = take the incoming commit's version (--theirs);
    # reject = keep our/base version (--ours). Then stage it (resolved).
    git("checkout", f"--{side}", "--", f)
    git("add", "--", f)
    if side == "theirs":
        rows[f].config(text="✓", fg="#1b9e3e")   # accepted incoming
    else:
        rows[f].config(text="↩", fg="#1565c0")   # rejected -> kept ours


frm = tk.Frame(root)
frm.pack(fill="both", expand=True, padx=12)
for f in files:
    row = tk.Frame(frm); row.pack(fill="x", pady=1)
    mk = tk.Label(row, text="·", width=2, fg="#888")
    mk.pack(side="left")
    tk.Button(row, text="meld", command=lambda f=f: open_meld(f)).pack(side="left", padx=2)
    tk.Button(row, text="accept", command=lambda f=f: take(f, "theirs")).pack(side="left", padx=2)
    tk.Button(row, text="reject", command=lambda f=f: take(f, "ours")).pack(side="left", padx=2)
    tk.Label(row, text=f, anchor="w", font=("monospace", 10)).pack(side="left", fill="x", expand=True)
    rows[f] = mk

st = {"rc": 1}


def finish(rc):
    st["rc"] = rc
    kill_all()
    try:
        root.destroy()
    except Exception:
        pass


bf = tk.Frame(root); bf.pack(pady=8)
tk.Button(bf, text="Apply", command=lambda: finish(0)).pack(side="left", padx=6)
tk.Button(bf, text="Skip commit", command=lambda: finish(1)).pack(side="left", padx=6)
tk.Button(bf, text="No & done", command=lambda: finish(2)).pack(side="left", padx=6)
root.protocol("WM_DELETE_WINDOW", lambda: finish(1))

root.mainloop()
sys.exit(st["rc"])
