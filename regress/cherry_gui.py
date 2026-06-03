"""cherry_gui.py — optional Tk front-end for cherry_picker.py.

Loaded by `cherry_picker.py --gui`. Pick a source (upstream / a remote branch
like origin/v1.14-branch / a contributor fork), multi-select commits, view
side-by-side diffs in meld, then Accept (no test) or Cherry-pick + Gate. Each
commit shows an indicator: ✓ accepted clean (green), ✎ accepted with edits
(blue), ⚠ conflict not applied (red), ✗ skipped (grey).

On a conflict, meld (git mergetool) and an Accept/Skip/"No & done" dialog open
together: answering kills meld; meld exiting dismisses the dialog (= Accept);
"No & done" skips this commit and stops processing the rest.

All Tk work happens on the main thread; the pick runs in a worker thread that
talks to the UI only through a thread-safe queue (Tcl is not thread-safe).
Needs python3-tk; conflict editing uses meld/$MERGE_TOOL.
"""
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import scrolledtext, messagebox

import cherry_picker as cp

HERE = os.path.dirname(os.path.abspath(__file__))
RESOLVER = os.path.join(HERE, "cherry_resolve.py")

MARK = {"clean": "✓", "edited": "✎", "conflict": "⚠", "skipped": "✗"}
COLOR = {"clean": "#1b9e3e",     # accepted clean  -> green
         "edited": "#1565c0",    # accepted w/edits -> blue
         "conflict": "#b00020",  # conflict, not applied -> red
         "skipped": "#888888"}   # not processed -> grey
ACCEPTED = ("clean", "edited")


def _source_list(repo, current):
    srcs = ["upstream"]
    srcs += [s for s in cp.load_config() if s not in srcs]
    try:
        refs = cp.git(repo, "for-each-ref", "--format=%(refname:short)",
                      "refs/remotes", check=False).stdout.split()
    except Exception:
        refs = []
    for r in refs:
        if "/" in r and not r.endswith("/HEAD") and r not in srcs:
            srcs.append(r)
    if current and current not in srcs:
        srcs.insert(1, current)
    return srcs


def _main_branch(base):
    return base.split("/", 1)[1] if base.startswith("origin/") else base


def run(args, cands, fetched):
    root = tk.Tk()
    root.title(f"cherry_picker — {args.repo}")
    root.geometry("1060x700")
    ui = queue.Queue()
    state = {"cands": cands, "base": args.base, "status": {},
             "branch": cp.candidate_branch(args.repo, args.branch),
             "procs": [], "branch_started": False}

    def spawn(cmd):
        """Popen a child (meld/mergetool) in its own session and track it so we
        can kill the whole group on Quit."""
        p = subprocess.Popen(cmd, start_new_session=True)
        state["procs"].append(p)
        return p

    def kill_proc(p):
        if p and p.poll() is None:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except Exception:
                pass

    def cleanup_and_quit():
        for p in state["procs"]:
            kill_proc(p)
        root.destroy()

    top = tk.Frame(root); top.pack(fill="x", padx=8, pady=4)
    tk.Label(top, text="source:").pack(side="left")
    src_var = tk.StringVar(value=(args.source or "upstream"))
    tk.OptionMenu(top, src_var, *_source_list(args.repo, args.source)).pack(side="left", padx=4)
    tk.Label(top, text=f"  base: {args.base}  →  branch: {state['branch']}").pack(side="left")
    status = tk.Label(top, text="", anchor="e"); status.pack(side="right")

    frame = tk.Frame(root); frame.pack(fill="both", expand=True, padx=8)
    sb = tk.Scrollbar(frame); sb.pack(side="right", fill="y")
    lb = tk.Listbox(frame, selectmode="extended", yscrollcommand=sb.set, font=("monospace", 10))
    lb.pack(side="left", fill="both", expand=True); sb.config(command=lb.yview)

    push_var = tk.IntVar(value=1 if args.push else 0)
    out = scrolledtext.ScrolledText(root, height=10, font=("monospace", 9))
    out.pack(fill="both", expand=False, padx=8, pady=4)

    # ---- helpers callable from any thread (enqueue) ----
    def log(m):
        ui.put(("log", m))

    # ---- main-thread UI ops ----
    def populate():
        lb.delete(0, "end")
        accepted = 0
        for i, (sha, date, subj, author) in enumerate(state["cands"]):
            st = state["status"].get(sha)
            lb.insert("end", f"{MARK.get(st, '·')} {sha[:9]}  {date}  {subj[:66]:<66}  ({author})")
            if st:
                lb.itemconfig(i, foreground=COLOR[st], selectforeground=COLOR[st])
                accepted += st in ACCEPTED
        status.config(text=f"{len(state['cands'])} candidate(s) · {accepted} accepted")

    def refresh(*_):
        status.config(text=f"fetching {src_var.get()} ..."); root.update_idletasks()
        try:
            c, _ = cp.fetch_candidates(args.repo, src_var.get(), args.base, args.limit,
                                       fetch=not getattr(args, "no_fetch", False))
            state["cands"] = c
            populate()
        except SystemExit as e:
            status.config(text=f"error: {e}")
    src_var.trace_add("write", refresh)

    def _popup(title, w=540, h=170):
        # place near the lower-left of the MAIN window so it lands on the same
        # screen/workspace the user is actually looking at (absolute bottom-left
        # may be on another monitor).
        win = tk.Toplevel(root); win.title(title)
        root.update_idletasks()
        x = max(0, root.winfo_x() + 30)
        y = max(0, root.winfo_y() + root.winfo_height() - h - 10)
        win.geometry(f"{w}x{h}+{x}+{y}")
        win.transient(root); win.lift(); win.attributes("-topmost", True)
        return win

    def show_diff():
        # delegate to the standalone review pop-up (own process — surfaces
        # reliably), anchored near the main window. It shows the diff in meld
        # with Accept/Skip options that cherry-pick onto cherry/<user>.
        sel = lb.curselection()
        if not sel:
            return
        sha = state["cands"][sel[0]][0]
        root.update_idletasks()
        x = max(0, root.winfo_x() + 30)
        y = max(0, root.winfo_y() + root.winfo_height() - 280)
        spawn([sys.executable, os.path.join(HERE, "cherry_diff.py"),
               args.repo, sha, f"620x220+{x}+{y}"])

    # build the coupled meld + Accept/Skip/No&done dialog (main thread, from pump)
    def build_conflict(repo, files, res, ev):
        rd = cp.repo_dir(repo)
        try:
            proc = spawn(["git", "-C", rd, "mergetool", "--no-prompt", "--tool=meld"])
        except Exception:
            proc = None
        started = time.time()
        dlg = tk.Toplevel(root); dlg.title("Resolve conflict")
        tk.Label(dlg, justify="left", padx=12, pady=10,
                 text="Conflict in:\n  " + "\n  ".join(files)
                      + "\n\nmeld is open. Accept the resolution, Skip this commit, "
                        "or 'No & done' to skip the rest too.").pack()

        def finish(outcome):
            if ev.is_set():
                return
            res["ok"] = outcome
            kill_proc(proc)
            try:
                dlg.destroy()
            except Exception:
                pass
            ev.set()

        bf = tk.Frame(dlg); bf.pack(pady=8)
        tk.Button(bf, text="Accept", command=lambda: finish(True)).pack(side="left", padx=5)
        tk.Button(bf, text="Skip", command=lambda: finish(False)).pack(side="left", padx=5)
        tk.Button(bf, text="No & done", command=lambda: finish("stop")).pack(side="left", padx=5)
        dlg.protocol("WM_DELETE_WINDOW", lambda: finish(False))
        # place bottom-left and force it to surface on the :0 WM
        dlg.update_idletasks()
        sh = dlg.winfo_screenheight()
        dlg.geometry(f"480x210+20+{max(20, sh - 270)}")
        dlg.transient(root); dlg.deiconify(); dlg.lift()
        dlg.attributes("-topmost", True); dlg.focus_force()
        def _grab():
            try:
                dlg.grab_set()
            except Exception:
                pass
        dlg.after(50, _grab)

        def poll():
            if ev.is_set():
                return
            # meld exiting counts as Accept — but only if it actually ran a
            # moment (an instant exit means it failed to launch; keep the dialog
            # so the user can still decide manually instead of it vanishing).
            if proc is not None and proc.poll() is not None and (time.time() - started) > 1.0:
                finish(True)
                return
            dlg.after(300, poll)
        poll()

    # called from the worker thread; runs the standalone resolver (its own Tk
    # process with the file-X-of-N pop-up + meld), blocks until it exits.
    def conflict_resolve(repo, files):
        p = subprocess.Popen([sys.executable, RESOLVER, cp.repo_dir(repo)],
                             start_new_session=True)
        state["procs"].append(p)
        p.wait()
        return {0: True, 1: False, 2: "stop"}.get(p.returncode, False)

    # ---- the pick worker ----
    def _act(gate):
        shas = [state["cands"][i][0] for i in lb.curselection()]
        if not shas:
            messagebox.showwarning("cherry_picker", "select one or more commits first")
            return
        for b in btns:
            b.config(state="disabled")
        do_push = bool(push_var.get())

        def worker():
            try:
                br, base = state["branch"], state["base"]
                log(f"{'gate' if gate else 'accept'}: cherry-picking {len(shas)} onto {br} (base {base}) ...")
                conf_files = []
                def cb(r, f):
                    conf_files.extend(f)
                    return conflict_resolve(r, f)
                # accumulate: reset the branch only on the first pick of the
                # session, then append so accepted commits build up for "Done".
                reset = not state["branch_started"]
                st = cp.cherry_pick(args.repo, shas, br, base,
                                    fetch=not getattr(args, "no_fetch", False),
                                    conflict_cb=cb, reset=reset)
                state["branch_started"] = True
                state["status"].update(st)
                ui.put(("populate",))
                applied = [s for s in shas if st.get(s) in ACCEPTED]
                ed = sum(1 for s in shas if st.get(s) == "edited")
                conf = sum(1 for s in shas if st.get(s) == "conflict")
                skip = sum(1 for s in shas if st.get(s) == "skipped")
                log(f"  accepted {len(applied)} ({len(applied)-ed} clean, {ed} with edits)"
                    + (f", {conf} conflict ⚠" if conf else "")
                    + (f", {skip} skipped ✗" if skip else ""))
                if conf_files:
                    log(f"    files with conflicts: {', '.join(sorted(set(conf_files)))}")
                if not applied:
                    return
                if gate:
                    log(f"  gating {br} (push if clean={'yes' if do_push else 'no'}) ...")
                    rc = cp.run_gate(args.repo, br, do_push)
                    log(f"  gate exit {rc} — details on the dashboard (/gates)")
                elif do_push:
                    mb = _main_branch(base)
                    log(f"  accepting WITHOUT test → FF origin/{mb} ...")
                    r = cp.git(args.repo, "push", "origin", f"{br}:refs/heads/{mb}", check=False)
                    log("  pushed." if r.returncode == 0
                        else f"  push refused (non-FF?): {r.stderr.strip()[:200]}")
                else:
                    log(f"  accepted on {br} (no test, no push)")
            except SystemExit as e:
                log(f"  error: {e}")
            finally:
                ui.put(("buttons", "normal"))

        threading.Thread(target=worker, daemon=True).start()

    # Done: fast-forward origin/main with the accumulated accepted commits
    def _done():
        br, base = state["branch"], state["base"]
        mb = _main_branch(base)
        if not cp.branch_exists(args.repo, br):
            messagebox.showinfo("cherry_picker", "Nothing accepted yet."); return
        n = cp.git(args.repo, "rev-list", "--count", f"{base}..{br}", check=False).stdout.strip()
        if n in ("", "0"):
            messagebox.showinfo("cherry_picker", "No accepted commits to merge."); return
        if not messagebox.askyesno("Done",
                f"Fast-forward origin/{mb} with {n} accepted commit(s) from {br}?"):
            return
        for b in btns:
            b.config(state="disabled")

        def worker():
            try:
                log(f"merging {n} accepted commit(s) → FF origin/{mb} ...")
                r = cp.git(args.repo, "push", "origin", f"{br}:refs/heads/{mb}", check=False)
                log(f"  merged — origin/{mb} fast-forwarded." if r.returncode == 0
                    else f"  push refused (non-FF? main moved): {r.stderr.strip()[:200]}")
            finally:
                ui.put(("buttons", "normal"))
        threading.Thread(target=worker, daemon=True).start()

    bar = tk.Frame(root); bar.pack(fill="x", padx=8, pady=6)
    btns = [
        tk.Button(bar, text="Refresh", command=refresh),
        tk.Button(bar, text="Show diff (meld)", command=show_diff),
        tk.Button(bar, text="Accept (no test)", command=lambda: _act(False)),
        tk.Button(bar, text="Cherry-pick + Gate", command=lambda: _act(True)),
        tk.Button(bar, text="Done (merge accepted)", command=_done),
    ]
    for b in btns[:4]:
        b.pack(side="left", padx=4)
    tk.Checkbutton(bar, text="FF origin/main (gate: if clean)", variable=push_var).pack(side="left", padx=12)
    tk.Button(bar, text="Quit", command=cleanup_and_quit).pack(side="right")
    btns[4].pack(side="right", padx=4)   # Done, left of Quit
    root.protocol("WM_DELETE_WINDOW", cleanup_and_quit)
    tk.Label(root, text="✓ accepted clean   ✎ with edits   ⚠ conflict   ✗ skipped",
             anchor="w", fg="#888").pack(fill="x", padx=10)

    # ---- main-thread pump: drains UI requests from the worker ----
    def pump():
        try:
            while True:
                req = ui.get_nowait()
                kind = req[0]
                if kind == "log":
                    out.insert("end", req[1] + "\n"); out.see("end")
                elif kind == "populate":
                    populate(); lb.selection_clear(0, "end")
                elif kind == "buttons":
                    for b in btns:
                        b.config(state=req[1])
                elif kind == "conflict":
                    build_conflict(*req[1:])
        except queue.Empty:
            pass
        root.after(100, pump)

    populate()
    root.after(100, pump)
    root.mainloop()
    return 0
