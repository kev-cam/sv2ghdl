"""The VACASK runner of the spectre personality: one deck, one run (docs/VAMOS_SPECTRE_DESIGN.md
§7.1, §1 steps 10-11, §2.5, §2.7; the API is the §10 block, the stop rules vamos/spectre/stop.py).

run(nl, plan, sigmap, run_dir, job, log, strict=False) -> EngineResult
    Step 10: every Verilog-A file of nl.hdl is compiled by engines.openvaf() (environment
    engines.env_for("vacask", None, os.environ) with tools.bound_blas_threads: OPENBLAS_NUM_THREADS=1
    unless the user set a thread count) into <ahdllibdir>/<stem>_<sha1>.osdi, written to
    a temporary file and moved with os.replace (concurrent runs share the directory), and reused
    while its sha1 holds; openvaf-r gets the include directories the module scan read
    (CDS_VLOGA_INCLUDE's, then -I's) and -va,define's macros (§2.5, §2.8).  Step 11:
    vacask.render(nl, osdi=<those files>, plan=plan, sigmap=sigmap, names=<the map it fills>) is
    written to <run_dir>/vamos.sim and run as `engines.vacask_bin() vamos.sim` in run_dir, stdin
    /dev/null, its own process group and PR_SET_PDEATHSIG (proc.child_setup), under
    proc.Interrupts(stop.HANDLED, stop.FORWARD, stop.FLAGS, grace=INTERRUPT_GRACE, group=True): every
    signal vamos sends a child goes to its whole process group, and a child left running when the
    runner ends (an exception) is killed with it (proc.kill_group).  VACASK's
    output goes through a pseudo-terminal where one opens (its stdout is then line-buffered, so the
    analyses are reported as they run; a pipe would hold every line until VACASK exits) and is kept
    whole in <run_dir>/vacask.log.  render's errors, a missing openvaf-r, a failed compile and a
    VACASK that cannot start print through `log` and give rc 2; render's warnings and notes print
    through `log` (strict: each warning an error, rc 2, VACASK not run).  A second INT, TERM, HUP or
    QUIT while a child runs kills it and raises stop.Stop.

What is printed (§8.7; every line through `log`): the analysis banner when VACASK starts an
analysis (`Running analysis 'vamos_aK'.`), that analysis's engine messages, then its "Total time
required" line with VACASK's elapsed time.  VACASK's own banner, its `Simulating:` line and its
running/elapsed lines are not printed.  A failed analysis is one error message naming it: VACASK's
lines with vamos_a<k>, vamos_w<k> and vamos_v_<p> given their Spectre names (at most ENGINE_LINES
of them) and the reason vamos found; a failed sweep point adds the warning of §7.1 (which points
are written).  An engine warning (`Warning, ...`) is a warning, any other line (a Verilog-A
$strobe) a plain line, at most ENGINE_LINES per analysis; the rest stays in vacask.log, which the
message names.  SIGUSR1 prints log.status_line with the running analysis and its progress, read
from the end of its rawfile (VACASK prints none into a pipe or an unsized terminal); SIGUSR2 the
note stop.CHECKPOINT.  Under VAMOS_VERBOSE (--vamos-verbose) each command is echoed.

The EngineResult (stop.py): rc 0, 1, 2 or 3; status per AnalysisStep id (absent for a step
VACASK never reached); files: the absolute vamos_a<k>.raw of every step whose rawfile exists;
failed_points: the first failed point (1-based) of a swept step that failed part-way; names:
render's map; log: VACASK's message lines (at most LOG_LINES).  An analysis succeeded only if its
rawfile exists, its `No. Points:` is filled and equals the rows rawfile.scan counts, it holds the
planned groups (the product of the points VACASK runs for its sweep statements; for a time axis,
its restarts; for a frequency axis, groups of its grid's points, R-emit-4) and, for a tran, every
group ends at `stop` (relative REL_STOP); otherwise it failed, whatever VACASK printed or returned
(§7.1, E27, E28, E66).  A
failed swept analysis keeps its complete leading groups: failed_points is one past them (counted in
the enclosing sweep blocks' points for a step inside them, else in the step's own dc or
fixed-frequency sweep points), never past the point `Sweep aborted @ k/n (v)[, k/n (v)...].`
names.  A non-zero VACASK exit is rc 2 with the engine's message; the analyses that finished
before it keep their status.  After the first INT, TERM, HUP or QUIT, VACASK gets SIGTERM; once it
stops, the analysis it was in is repaired with rawfile.fix_points and is `interrupted` (`failed`
when it left no complete row), the later ones are absent, and rc is 3; so it is after the
grace-period kill and when VACASK is ended by SIGKILL, SIGTERM, SIGINT or SIGHUP from outside;
any other signal that ends VACASK is rc 2 (§2.7).

Helpers, public for the tests and the flow: vacask_count(spec) (the points VACASK runs for a
sweep), sweep_levels(step), check_step(step, path) (the §7.1 rules on one rawfile), ahdl_dir(job,
env) (§2.5's directory), vloga_include(env) and va_search(job, env) (§2.8's include list),
va_files(src, search) and osdi_path(...) (the cache key).
"""

from __future__ import annotations

import errno
import hashlib
import math
import os
import re
import selectors
import shlex
import signal
import struct
import subprocess
import time
import types
import uuid
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from vamos import proc, tools
from vamos.ams import engines
from vamos.netlist import rawfile, vacask
from vamos.netlist.ir import Netlist
from vamos.netlist.plan import AnalysisStep, RunPlan, SweepLevel
from vamos.netlist.signals import SignalMap
from vamos.notes import ERROR, Note, NoteError, error, note, strict as strict_notes, warning
from vamos.spectre import percent, stop
from vamos.spectre.args import effective
from vamos.spectre.job import SpectreJob
from vamos.spectre.log import ANALYSIS_TITLES, SpectreLog, si
from vamos.spectre.results import EngineResult

DECK = "vamos.sim"                  # the deck in the run directory (§7.1)
ENGINE_LOG = "vacask.log"           # VACASK's whole output, kept in the run directory
ENGINE_LINES = 30                   # engine lines printed per message (§8.7: never raw floods)
LOG_LINES = 2000                    # VACASK's message lines kept in EngineResult.log
INTERRUPT_GRACE = proc.INTERRUPT_GRACE   # read at each run, so a test can shorten it
POLL = 0.1                          # seconds between the main loop's looks at the flags (§2.7)
REL_STOP = 1e-9                     # a tran group ends at stop within this relative tolerance (§7.1)
OSDI_KEY = b"vamos-spectre-osdi 1"  # part of every .osdi's sha1: changes when the key's recipe does

PHASE_READIN = "circuit read-in"
PHASE_AHDL = "AHDL read-in"
PHASE_SETUP = "initial setup"
PHASE_RUN = "the run"
_KIND_PHASE = {"op": "DC analysis", "dc": "DC analysis", "ac": "AC analysis", "noise": "noise analysis",
               "xf": "XF analysis", "tran": "transient analysis"}

# the signals that, ending VACASK from outside vamos, give status 3; any other gives 2 (§2.7)
_STOPPING = tuple(getattr(signal, n) for n in ("SIGKILL", "SIGTERM", "SIGINT", "SIGHUP") if hasattr(signal, n))

_RUNNING = re.compile(r"^Running analysis '([^']*)'\.\s*$")
_ELAPSED = re.compile(r"^\s*Elapsed time:\s*(\S+)\s*$")
_SIMULATING = re.compile(r"^Simulating:")
_BANNER = re.compile(r"^(This is vacask\b|\(c\)|https?://|\s*OpenMP\b|Available CPUs:|CPUs used:|OpenBLAS CPUs:)")
_ABORTED = re.compile(r"^Analysis '([^']*)' aborted\.\s*$")
_SWEEP_ABORTED = re.compile(r"^Sweep aborted @ (.*)\.\s*$")
_POSITION = re.compile(r"(\d+)/(\d+)\s*\(")
_BIND = re.compile(r"^Failed to bind (?:analysis outputs|sweep parameters)\.\s*$")
# an engine warning in the narration: VACASK's own, and the messages of an OSDI device (lib/osdicallback.cpp:
# `OSDI(warn) M8: Source conductance reset ...`; err and fatal of an analysis that did not fail too, as
# run_xyce narrates the messages of a deck that did not fail), so they are counted (§8.7; phase 2)
_WARNING = re.compile(r"^\s*(?:warning\b|OSDI\((?:warn|err|fatal)\))", re.I)
_QUOTED_ID = re.compile(r"'(vamos_[aw]\d+|vamos_v_[A-Za-z0-9_$]+)'")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_VLOGA_SPLIT = re.compile(r"[,:;]")


def _terminator(line: str) -> bool:
    """A line VACASK prints last for a failed analysis (§7.1, E6, E27, E28)."""
    return bool(_ABORTED.match(line) or _SWEEP_ABORTED.match(line) or _BIND.match(line))


# -- signals ---------------------------------------------------------------------------------------

class _Interrupts(proc.Interrupts):
    """proc.Interrupts with stop.py's signals, sent to the child's process group, which also records
    a second handled signal: the grace-period kill and a second signal both set `killed`, and only
    the second one stops the run without converting (stop.Stop)."""

    def __init__(self) -> None:
        super().__init__(stop.HANDLED, stop.FORWARD, stop.FLAGS, grace=INTERRUPT_GRACE, group=True)
        self.second: Optional[int] = None

    def _on_signal(self, signum, frame) -> None:
        if self.signum is not None and self.second is None:
            self.second = signum
        super()._on_signal(signum, frame)


class _Stopped(Exception):
    """The first handled signal came before VACASK ran (during the Verilog-A compile)."""


class _Fail(Exception):
    """A setup error before the engine runs: rc 2, with the notes to print."""

    def __init__(self, notes: List[Note], phase: str) -> None:
        super().__init__("; ".join(n.message for n in notes))
        self.notes, self.phase = notes, phase


# -- the grids VACASK runs (VACASK lib/answeep.cpp) --------------------------------------------------

def _num(e) -> float:
    if e is None:
        raise ValueError("a sweep bound is missing")
    return float(getattr(e, "value", e))


def vacask_count(spec) -> Optional[int]:
    """The points VACASK runs for an evaluated SweepSpec printed as vacask.render prints it (§5.2):
    `values=[...]` for values and log; else from/to and step= (VACASK's stepped sweep:
    n = ceil((to-from)/step), plus one when from+n*step does not pass `to`; a step grid whose VACASK
    count would differ from the plan's is printed as values=[...] with the plan's points, so it has
    those, vacask.step_values), mode="lin" (points+1) or mode="dec"
    (ceil(|points*log(to/from)/log(10)|)+1).  None when the spec cannot be read."""
    try:
        mode = spec.mode
        if mode in ("values", "log"):
            return len(spec.values)
        start, stop_ = _num(spec.start), _num(spec.stop)
        if mode == "step":
            pts = vacask.step_values(spec)
            if pts is not None:
                return len(pts)
            return vacask.vacask_step_count(start, stop_, _num(spec.step))
        if mode == "lin":
            return int(spec.count) + 1 if spec.count is not None else None
        if mode == "dec":
            if spec.count is None or start <= 0 or stop_ <= 0:
                return None
            return int(math.ceil(abs(int(spec.count) * math.log(stop_ / start) / math.log(10.0)))) + 1
    except (TypeError, ValueError, AttributeError, ZeroDivisionError, OverflowError):
        return None
    return None


def sweep_levels(step: AnalysisStep) -> List[SweepLevel]:
    """The levels vacask.render prints as sweep statements, outermost first: the context, then the
    step's own level unless it is a frequency axis (§7.1)."""
    levels = list(step.context)
    if step.own is not None and step.own.target[0] != "freq":
        levels.append(step.own)
    return levels


def _axis(step: AnalysisStep) -> Optional[str]:
    """The rawfile column a step's own output runs along: `time` for a tran, `frequency` for an
    ac, noise or xf over a frequency grid; None when each sweep point is one row."""
    if step.kind == "tran":
        return "time"
    if step.kind in ("ac", "noise", "xf") and step.own is not None and step.own.target[0] == "freq":
        return "frequency"
    return None


def _product(counts: Iterable[Optional[int]]) -> Optional[int]:
    out = 1
    for c in counts:
        if c is None:
            return None
        out *= c
    return out


# -- the rawfile checks of §7.1 ----------------------------------------------------------------------

class _Head:
    """A rawfile header as far as the checks and the status line need it."""

    def __init__(self) -> None:
        self.names: List[str] = []
        self.binary = False
        self.complex = False
        self.data_start = -1


def _header(path: str) -> Optional[_Head]:
    """The variables and layout of a rawfile's first plot; None when the header is not (yet)
    complete or not a rawfile header."""
    head = _Head()
    nvars = None
    try:
        with open(path, "rb") as fh:
            while True:
                line = fh.readline(1 << 16)
                if not line.endswith(b"\n"):
                    return None
                text = line.decode("latin-1").rstrip("\r\n")
                key, _, value = text.partition(":")
                key = key.strip().lower()
                if key == "flags":
                    head.complex = "complex" in value.lower().split()
                elif key == "no. variables":
                    nvars = int(value.strip())
                elif key == "variables":
                    if nvars is None:
                        return None
                    lines = [value] if value.strip() else []
                    while len(lines) < nvars:
                        vl = fh.readline(1 << 16)
                        if not vl.endswith(b"\n"):
                            return None
                        if vl.strip():
                            lines.append(vl.decode("latin-1").rstrip("\r\n"))
                    for vl in lines:
                        fields = [f.strip() for f in (vl.split("\t") if "\t" in vl.strip() else vl.split())
                                  if f.strip()]
                        head.names.append(fields[1] if len(fields) > 1 else "")
                elif key in ("binary", "values"):
                    head.binary = key == "binary"
                    head.data_start = fh.tell()
                    return head if nvars is not None and len(head.names) == nvars else None
    except (OSError, ValueError):
        return None


class StepCheck:
    """The §7.1 rules applied to one step's rawfile (check_step)."""

    def __init__(self) -> None:
        self.exists = False
        self.declared: Optional[int] = None     # the header's No. Points (None: blank)
        self.n = 0                               # the rows rawfile.scan counts
        self.groups = 0                          # the groups found (rows, time restarts, frequency grids)
        self.planned: Optional[int] = None       # the groups the plan asks for (None: not known)
        self.done = 0                            # complete leading units a failed swept step keeps
        self.last: Optional[float] = None        # the last axis value (a tran's last time)
        self.complete = False
        self.reason = ""                         # why the step failed ("" when complete)


def check_step(step: AnalysisStep, path: str) -> StepCheck:
    """§7.1's success rules for one analysis step: the rawfile exists; its `No. Points:` is filled
    and equals the rows read (rawfile.scan); it holds the planned groups (the product of
    vacask_count over the sweep statements; for a time axis, its restarts; for a frequency axis,
    groups of its grid's vacask_count points, the last one complete); for a tran, every group ends
    at args["stop"] within REL_STOP.  `done` counts the complete leading units a failed swept step
    keeps: groups of the enclosing sweep blocks when it is inside one, else the points of its own
    sweep."""
    c = StepCheck()
    if not os.path.isfile(path):
        c.reason = "VACASK wrote no rawfile for it"
        return c
    c.exists = True
    try:
        plot = rawfile.scan(path)[0]
    except (rawfile.RawError, OSError, ValueError, IndexError) as e:
        c.reason = "its rawfile is unreadable (%s)" % e
        return c
    c.declared, c.n = plot.declared, plot.n
    levels = sweep_levels(step)
    ctx = [vacask_count(lv.spec) for lv in step.context]
    own_sweep = step.own is not None and step.own.target[0] != "freq"
    own_n = vacask_count(step.own.spec) if own_sweep and step.own is not None else 1
    axis = _axis(step)
    stop_ = None
    if step.kind == "tran":
        try:
            stop_ = float(step.args.get("stop"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            stop_ = None
    ends_ok = True
    per: Optional[int] = None                      # a frequency axis's points per group (below)
    short_rows = 0
    if axis is None:                               # one row per point of every sweep statement
        c.planned = _product(ctx + [own_n])
        c.groups = c.n
        if step.context and own_sweep:
            c.done = c.n // own_n if own_n else 0
        else:
            c.done = c.n
    elif not levels:                               # one plain tran, ac, noise or xf
        c.planned = 1
        c.groups = 1 if c.n else 0
        c.last = plot.last
        c.done = c.groups
        if stop_ is not None and c.n:
            ends_ok = abs((plot.last or 0.0) - stop_) <= REL_STOP * abs(stop_)
    else:                                          # a time or frequency axis under sweep statements
        c.planned = _product(ctx)
        head = _header(path)
        if head is None or axis not in head.names:
            c.reason = "its rawfile has no %s column" % axis
            return c
        k = (len(levels) if len(head.names) > len(levels) and head.names[len(levels)] == axis
             else head.names.index(axis))
        groups: List[List[float]] = []             # [rows, last x] per group
        prev = None
        # a frequency axis is grouped by its grid's point count, as results._groups splits it: render prints the
        # plan's grid, which need not increase (start = stop, values=[1M 1k 10k], a descending lin or dec;
        # R-emit-4); a time axis is split at its restarts
        per = vacask_count(step.own.spec) if axis == "frequency" and step.own is not None else None
        try:
            for (x,) in rawfile.iter_rows(path, 0, [k]):
                xv = x.real if isinstance(x, complex) else float(x)
                if ((not groups or groups[-1][0] >= per) if per else (prev is None or xv <= prev)):
                    groups.append([0, xv])
                groups[-1][0] += 1
                groups[-1][1] = xv
                prev = xv
        except (rawfile.RawError, OSError, ValueError, IndexError) as e:
            c.reason = "its rawfile is unreadable (%s)" % e
            return c
        c.groups = len(groups)
        c.last = groups[-1][1] if groups else None
        if stop_ is not None:
            good = [abs(last - stop_) <= REL_STOP * abs(stop_) for _rows, last in groups]
            ends_ok = all(good)
        else:
            want = vacask_count(step.own.spec) if step.own is not None else None
            good = [want is None or rows == want for rows, _last in groups]
            if want is None and groups and c.declared != c.n:
                good[-1] = False                   # a failed file's last group may be cut short
            if per:
                ends_ok = all(good)                # a last group short of the grid's points is cut short
                short_rows = int(groups[-1][0]) if groups else 0
        done = 0
        for g in good:
            if not g:
                break
            done += 1
        c.done = done
    if c.declared is None:
        c.reason = "its rawfile's No. Points: is blank (%d rows written)" % c.n
    elif c.declared != c.n:
        c.reason = "its rawfile declares %d points and holds %d" % (c.declared, c.n)
    elif c.planned is not None and c.groups != c.planned:
        c.reason = "its rawfile holds %d of the %d planned %s" % (c.groups, c.planned,
                                                                  "sweep groups" if levels else "plots")
    elif not ends_ok and stop_ is None:
        c.reason = ("a group of its rawfile holds %d of the %d points of its frequency grid"
                    % (short_rows, per or 0))
    elif not ends_ok:
        c.reason = ("a group of its rawfile ends at time = %s, before stop = %s"
                    % (si(c.last or 0.0, "s"), si(stop_ or 0.0, "s")))
    else:
        c.complete = True
    return c


def _sweep_positions(lines: Sequence[str]) -> Optional[List[Tuple[int, int]]]:
    """(k, n) for every level in the last `Sweep aborted @ k/n (v), k/n (v).` line, outermost
    first (VACASK's ParameterSweeper::progress); None when there is none."""
    for line in reversed(lines):
        m = _SWEEP_ABORTED.match(line)
        if m:
            return [(int(a), int(b)) for a, b in _POSITION.findall(m.group(1))]
    return None


def _flat(positions: Sequence[Tuple[int, int]]) -> int:
    """The 1-based flattened index of a point given per level as (k, n), outermost first."""
    idx = 0
    for k, n in positions:
        idx = idx * n + (k - 1)
    return idx + 1


# -- the Verilog-A cache (§1 step 10, §2.5, §2.8) ------------------------------------------------

def _from_cwd(job: SpectreJob, path: str) -> str:
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(job.cwd, path))


def ahdl_dir(job: SpectreJob, env: Optional[Mapping[str, str]] = None) -> str:
    """Where compiled Verilog-A is kept (§2.5): -ahdllibdir; else $CDS_AHDLCMI_SIMDB_DIR, which
    must exist and be writable [M ref19 p.542]; else %C:r:t.ahdlSimDB under -outdir when one is
    given; else %C:r:t.ahdlSimDB in the cwd, Spectre's default [M ref19 p.542].  Relative paths
    resolve against the job's cwd.  _Fail for a CDS_AHDLCMI_SIMDB_DIR that is missing or not
    writable; the directory is not created here."""
    env = os.environ if env is None else env
    given = effective(job, "ahdllibdir")
    if given:
        return _from_cwd(job, str(given))
    simdb = env.get("CDS_AHDLCMI_SIMDB_DIR")
    if simdb:
        d = _from_cwd(job, simdb)
        if not os.path.isdir(d) or not os.access(d, os.W_OK | os.X_OK):
            raise _Fail([error("", "CDS_AHDLCMI_SIMDB_DIR=%s: %s, where compiled Verilog-A is kept, %s"
                                   % (simdb, d, "is not writable" if os.path.isdir(d) else "does not exist"))],
                        PHASE_AHDL)
        return d
    name = percent.expand("%C:r:t", percent.codes(job, datetime.now())) + ".ahdlSimDB"
    outdir = effective(job, "outdir")
    return os.path.join(_from_cwd(job, str(outdir)) if outdir else job.cwd, name)


def vloga_include(env: Optional[Mapping[str, str]] = None) -> List[str]:
    """CDS_VLOGA_INCLUDE's directories, separated by commas, colons or semicolons [M ref19 p.551]."""
    env = os.environ if env is None else env
    return [d for d in (p.strip() for p in _VLOGA_SPLIT.split(env.get("CDS_VLOGA_INCLUDE", ""))) if d]


def va_search(job: SpectreJob, env: Optional[Mapping[str, str]] = None) -> List[str]:
    """The include directories of the Verilog-A module scan (§3.1, §2.8): CDS_VLOGA_INCLUDE's, then
    -I's, each against the cwd.  openvaf-r gets the same list."""
    out: List[str] = []
    for d in vloga_include(env) + list(job.incdirs):
        p = _from_cwd(job, d)
        if p not in out:
            out.append(p)
    return out


_VA_INCLUDE = re.compile(rb'^[ \t]*`include[ \t]+"([^"]+)"', re.M)
_VA_DEPTH = 32


def va_files(src: str, search: Sequence[str]) -> List[str]:
    """The Verilog-A source and every file its `include`s reach (beside the including file, then
    the search directories), each once.  Unlike the module scan (spice.va_modules, which skips
    every *.vams header) it follows all of them, so a user's own header is part of the cache key;
    an include that is not found is left to openvaf-r to report."""
    out: List[str] = []

    def walk(path: str, depth: int) -> None:
        path = os.path.abspath(path)
        if path in out or depth > _VA_DEPTH:
            return
        out.append(path)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            return
        for m in _VA_INCLUDE.finditer(data):
            name = m.group(1).decode("utf-8", "replace")
            cands = [name] if os.path.isabs(name) else \
                [os.path.join(os.path.dirname(path), name)] + [os.path.join(d, name) for d in search]
            for c in cands:
                if os.path.isfile(c):
                    walk(c, depth + 1)
                    break
    walk(src, 0)
    return out


def osdi_path(src: str, libdir: str, openvaf: str, search: Sequence[str], defines: Sequence[str]) -> str:
    """<libdir>/<stem>_<sha1>.osdi for a Verilog-A file: the sha1 covers openvaf-r (path, size,
    mtime), the include directories, the macros, and the path and bytes of the source and of every
    file its `include`s reach (va_files), so a change compiles anew and an unchanged source is
    reused."""
    h = hashlib.sha1(OSDI_KEY + b"\0")
    try:
        st = os.stat(openvaf)
        h.update(("%s\0%d\0%d\0" % (openvaf, st.st_size, int(st.st_mtime))).encode("utf-8", "replace"))
    except OSError:
        h.update(openvaf.encode("utf-8", "replace") + b"\0")
    for d in search:
        h.update(b"I" + d.encode("utf-8", "replace") + b"\0")
    for m in defines:
        h.update(b"D" + m.encode("utf-8", "replace") + b"\0")
    for f in va_files(src, search):
        h.update(b"F" + f.encode("utf-8", "replace") + b"\0")
        try:
            with open(f, "rb") as fh:
                h.update(fh.read())
        except OSError:
            h.update(b"(unreadable)")
        h.update(b"\0")
    stem = os.path.splitext(os.path.basename(src))[0]
    return os.path.join(libdir, "%s_%s.osdi" % (stem, h.hexdigest()))


# -- the child processes -----------------------------------------------------------------------------

def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _spawn(cmd: List[str], cwd: str, env: Dict[str, str]) -> Tuple[subprocess.Popen, int, Callable[[], None]]:
    """Start VACASK with stdin /dev/null, its own process group and PR_SET_PDEATHSIG, its stdout
    and stderr on a pseudo-terminal (VACASK's streams are then line-buffered; with no window size
    its progress bar stays off) or, where none opens, a pipe.  Returns (the process, the fd to
    read, a closer)."""
    master = slave = None
    if hasattr(os, "openpty"):
        try:
            master, slave = os.openpty()
        except OSError:
            master = slave = None
    if master is not None and slave is not None:
        try:
            import termios
            attrs = termios.tcgetattr(slave)
            attrs[1] &= ~termios.OPOST                      # "\n" stays "\n"
            termios.tcsetattr(slave, termios.TCSANOW, attrs)
        except (ImportError, OSError, AttributeError):
            pass
        try:
            p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=slave, stderr=slave,
                                 preexec_fn=proc.child_setup())
        except OSError:
            os.close(master)
            os.close(slave)
            raise
        os.close(slave)
        fd = master

        def close_pty() -> None:
            try:
                os.close(fd)
            except OSError:
                pass
        return p, fd, close_pty
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, preexec_fn=proc.child_setup())
    assert p.stdout is not None
    out = p.stdout

    def close_pipe() -> None:
        try:
            out.close()
        except OSError:
            pass
    return p, out.fileno(), close_pipe


def _done_label(step: AnalysisStep):
    """What log.analysis_done names: the step's name and its PSF analysis type (`dc` for an op)."""
    title = ANALYSIS_TITLES.get(step.kind, "%s Analysis" % step.kind)
    return types.SimpleNamespace(description="%s `%s'" % (title, step.name), key=step.name,
                                 atype="dc" if step.kind in ("op", "dc") else step.kind)


class _Block:
    """The engine lines of one analysis, from its `Running analysis` line to the next one."""

    def __init__(self, name: str, step: Optional[AnalysisStep]) -> None:
        self.name, self.step = name, step
        self.lines: List[str] = []
        self.dropped = 0
        self.elapsed: Optional[float] = None
        self.t0 = time.time()
        self.closed = False

    def add(self, line: str) -> None:
        if len(self.lines) < 4 * ENGINE_LINES + 200 or _terminator(line):
            self.lines.append(line)
        else:
            self.dropped += 1


# -- one run ---------------------------------------------------------------------------------------

class _Run:
    """The state of one run(): the steps, the result, VACASK's narration."""

    def __init__(self, nl: Netlist, plan: RunPlan, sigmap: SignalMap, run_dir: str, job: SpectreJob,
                 log: SpectreLog, strict: bool) -> None:
        self.nl, self.plan, self.sigmap, self.job, self.log, self.strict = nl, plan, sigmap, job, log, strict
        self.run_dir = os.path.abspath(run_dir)
        self.er = EngineResult()
        self.steps: List[AnalysisStep] = [a.step for a in plan.actions if a.op == "analysis" and a.step is not None]
        self.by_id: Dict[str, AnalysisStep] = {s.id: s for s in self.steps}
        self.levels: Dict[str, Tuple[SweepLevel, AnalysisStep]] = {}
        for s in self.steps:
            for lv in list(s.context) + ([s.own] if s.own is not None else []):
                self.levels.setdefault(lv.id, (lv, s))
        self.env = tools.bound_blas_threads(engines.env_for("vacask", None, dict(os.environ)))
        self.intr: Optional[_Interrupts] = None
        self.checks: Dict[str, StepCheck] = {}
        self.reasons: Dict[str, str] = {}
        self.classified: Dict[str, str] = {}
        self.stage = "banner"                     # banner -> setup (`Simulating:`) -> run (an analysis started)
        self.pre: List[str] = []                  # engine lines before the first analysis (no banner)
        self.blocks: List[_Block] = []
        self.current: Optional[_Block] = None
        self.kept = 0                             # VACASK's message lines seen (EngineResult.log keeps LOG_LINES)
        self.compiling = ""                       # the Verilog-A file openvaf-r compiles now
        self.child = ""                           # the child the first signal reached: openvaf-r or VACASK
        self.fd: Optional[int] = None             # VACASK's output while it runs (pump)
        self.sel: Optional[selectors.BaseSelector] = None
        self.buf = b""
        self.eof = False
        self.keep = None                          # the vacask.log being written

    # -- messages
    def translate(self, text: str) -> str:
        """VACASK's quoted vamos_a<k>, vamos_w<k> and vamos_v_<p> as Spectre names (§7.1)."""
        def sub(m) -> str:
            ident = m.group(1)
            if ident.startswith("vamos_v_"):
                return "`%s'" % ident[len("vamos_v_"):]
            if ident in self.by_id:
                return "`%s'" % self.by_id[ident].name
            if ident in self.levels:
                lv, st = self.levels[ident]
                return "`%s'" % (lv.name or st.name)
            return m.group(0)
        return _QUOTED_ID.sub(sub, text)

    @staticmethod
    def phase_of(step: AnalysisStep) -> str:
        return "%s `%s'" % (_KIND_PHASE.get(step.kind, "%s analysis" % step.kind), step.name)

    def where(self) -> str:
        return os.path.join(self.run_dir, ENGINE_LOG)

    def message(self, severity: str, lines: Sequence[str], phase: str, extra: Sequence[str] = (),
                dropped: int = 0) -> None:
        """One Spectre message from engine lines (translated, at most ENGINE_LINES) and vamos lines."""
        shown = [self.translate(x.rstrip()) for x in lines if x.strip()]
        more = max(len(shown) - ENGINE_LINES, 0) + dropped
        shown = shown[:ENGINE_LINES]
        if more:
            shown.append("... %d more engine lines in %s" % (more, self.where()))
        shown += list(extra)
        if shown:
            self.log.message(Note(severity, "", "\n".join(shown)), phase)

    def narrate(self, lines: Sequence[str], phase: str, dropped: int = 0) -> None:
        """The lines of an analysis that did not fail: engine warnings as warnings, the rest as plain
        lines, at most ENGINE_LINES of each."""
        plain = warns = 0
        for x in lines:
            if not x.strip():
                continue
            if _WARNING.match(x):
                warns += 1
                if warns <= ENGINE_LINES:
                    self.log.message(warning("", self.translate(x.strip())), phase)
                continue
            plain += 1
            if plain <= ENGINE_LINES:
                self.log.line(self.translate(x.rstrip()))
        more = max(plain - ENGINE_LINES, 0) + max(warns - ENGINE_LINES, 0) + dropped
        if more:
            self.log.line("... %d more engine lines in %s" % (more, self.where()))

    def echo(self, cmd: Sequence[str], cwd: str) -> None:
        if os.environ.get("VAMOS_VERBOSE"):
            self.log.line("vamos: + cd %s && %s" % (shlex.quote(cwd), " ".join(shlex.quote(c) for c in cmd)))

    def stop_reason(self, signum: Optional[int]) -> str:
        assert self.intr is not None
        if self.intr.signum is not None:
            text = "vamos got %s" % proc.signal_name(self.intr.signum)
            if self.child:
                text += " and stopped %s" % self.child
                if self.intr.killed:
                    text += "; %s did not stop within %g s and was killed" % (self.child, self.intr.grace)
            return text
        if signum is not None:
            return "VACASK was ended by %s from outside vamos" % proc.signal_name(signum)
        return "stopped"

    # -- step 10: the Verilog-A files
    def compile_va(self) -> List[str]:
        if not self.nl.hdl:
            return []
        ov = engines.openvaf()
        if not ov:
            raise _Fail([error("openvaf-r", "cannot find openvaf-r, which compiles the Verilog-A files for VACASK "
                                            "(set VAMOS_OPENVAF)")], PHASE_AHDL)
        libdir = ahdl_dir(self.job)
        try:
            os.makedirs(libdir, exist_ok=True)
        except OSError as e:
            raise _Fail([error("", "cannot create %s, where compiled Verilog-A is kept: %s"
                                   % (libdir, e.strerror or e))], PHASE_AHDL)
        search = va_search(self.job)
        defines = list(self.job.va_defines)
        out: List[str] = []
        for src in self.nl.hdl:
            target = osdi_path(src, libdir, ov, search, defines)
            if '"' in target:
                raise _Fail([error(src, "%s holds a double quote, which a VACASK load line cannot name"
                                        % target)], PHASE_AHDL)
            if not os.path.isfile(target):
                self.compile_one(ov, src, target, search, defines)
            out.append(target)
        return out

    def compile_one(self, ov: str, src: str, target: str, search: Sequence[str], defines: Sequence[str]) -> None:
        """openvaf-r src into a temporary file beside target, then os.replace (§2.5)."""
        assert self.intr is not None
        tmp = os.path.join(os.path.dirname(target), ".%s.%d.%s.part" % (os.path.basename(target), os.getpid(),
                                                                       uuid.uuid4().hex[:8]))
        cmd = [ov, src, "-o", tmp]
        for d in search:
            cmd += ["-I", d]
        for m in defines:
            cmd += ["-D", m]
        cwd = os.path.dirname(src) or "."
        self.echo(cmd, cwd)
        if self.intr.signum is not None:
            raise _Stopped()
        try:
            p = subprocess.Popen(cmd, cwd=cwd, env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, preexec_fn=proc.child_setup())
        except OSError as e:
            via = " (VAMOS_OPENVAF)" if os.environ.get("VAMOS_OPENVAF") == ov else ""
            raise _Fail([error("openvaf-r", "cannot run openvaf-r %s%s: %s" % (ov, via, e.strerror or e))],
                        PHASE_AHDL)
        self.intr.attach(p)
        self.child = "openvaf-r"
        self.compiling = os.path.basename(src)
        out = b""
        try:
            while True:
                try:
                    data, _ = p.communicate(timeout=POLL)
                    out += data or b""
                    break
                except subprocess.TimeoutExpired:
                    self.flags()
                    if self.intr.second is not None:
                        break
        finally:
            proc.kill_group(p)                           # left running only by an exception or a second signal
            self.compiling = ""
            if self.intr.signum is None:
                self.child = ""
        if self.intr.signum is not None:
            _remove(tmp)
            if self.intr.second is not None:
                raise stop.Stop(self.intr.second)
            raise _Stopped()
        text = _ANSI.sub("", out.decode("utf-8", "replace"))
        if p.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
            _remove(tmp)
            lines = [x.rstrip() for x in text.splitlines() if x.strip()]
            more = max(len(lines) - 4 * ENGINE_LINES, 0)
            body = lines[:4 * ENGINE_LINES] + (["... %d more lines" % more] if more else [])
            raise _Fail([error(src, "openvaf-r failed (exit status %s):\n%s"
                               % (p.returncode, "\n".join(body) or "(no output)"))], PHASE_AHDL)
        os.replace(tmp, target)

    # -- step 11: the deck
    def render(self, osdi: Sequence[str]) -> str:
        notes: List[Note] = []
        names: Dict = {}
        try:
            text = vacask.render(self.nl, osdi=list(osdi), notes=notes, plan=self.plan, sigmap=self.sigmap,
                                 names=names)
        except NoteError as e:
            raise _Fail(list(e.notes), PHASE_READIN)
        notes = strict_notes(notes, self.strict)
        for n in notes:
            self.log.message(n, PHASE_READIN)
        if any(n.severity == ERROR for n in notes):
            raise _Fail([], PHASE_READIN)
        self.er.names = names
        return text

    # -- the flags: SIGUSR1 and SIGUSR2 (§2.7)
    def flags(self) -> None:
        assert self.intr is not None
        sigs = self.intr.take_flags()
        if sigs:
            for _ in range(20):                            # the lines in flight first: the latest state
                if not self.read_ready(0.05):
                    break
        for sig in sigs:
            if sig == getattr(signal, "SIGUSR1", None):
                self.log.status_line(self.progress())
            elif sig == getattr(signal, "SIGUSR2", None):
                blk = self.current
                phase = self.phase_of(blk.step) if blk is not None and blk.step is not None else PHASE_RUN
                self.log.message(note("", stop.CHECKPOINT), phase)

    def progress(self) -> str:
        """The running analysis's progress, from the end of its rawfile."""
        if self.compiling:
            return "compiling the Verilog-A file %s" % self.compiling
        blk = self.current
        if blk is None or blk.step is None:
            return ""
        step = blk.step
        try:
            path = os.path.join(self.run_dir, step.id + ".raw")
            head = _header(path)
            if head is None or not head.binary or not head.names:
                return ""
            size = 16 if head.complex else 8
            width = len(head.names) * size
            n = max(os.path.getsize(path) - head.data_start, 0) // width
            if n <= 0:
                return "no point written yet"
            if step.kind == "tran" and "time" in head.names:
                with open(path, "rb") as fh:
                    fh.seek(head.data_start + (n - 1) * width + head.names.index("time") * size)
                    t = struct.unpack("<d", fh.read(8))[0]
                stop_ = step.args.get("stop")
                if isinstance(stop_, (int, float)) and float(stop_) > 0:
                    return "time = %s of %s (%.0f %%), %d points written" % (si(t, "s"), si(float(stop_), "s"),
                                                                             100.0 * t / float(stop_), n)
                return "time = %s, %d points written" % (si(t, "s"), n)
            return "%d points written" % n
        except (OSError, ValueError, IndexError, struct.error):
            return ""

    # -- the narration
    def feed(self, line: str) -> None:
        line = line.rstrip("\r\n")
        if "\r" in line:                                   # in-place updates: the last one counts
            line = line.rsplit("\r", 1)[-1]
        m = _RUNNING.match(line)
        if m:
            self.open_block(m.group(1))
            return
        if self.stage == "banner":
            if _SIMULATING.match(line):
                self.stage = "setup"
                return
            if _BANNER.match(line):
                return
        if not line.strip():
            return
        if self.current is not None:
            m = _ELAPSED.match(line)
            if m:
                try:
                    self.current.elapsed = float(m.group(1))
                except ValueError:
                    pass
                return
        if self.kept < LOG_LINES:
            self.er.log.append(line)
        elif self.kept == LOG_LINES:
            self.er.log.append("... (the rest is in %s)" % self.where())
        self.kept += 1
        if self.current is None:
            self.pre.append(line)
        else:
            self.current.add(line)

    def open_block(self, name: str) -> None:
        if self.current is not None:
            self.close_block(self.current, "next")
        if self.stage != "run":
            self.stage = "run"
            lines, self.pre = self.pre, []
            self.narrate(lines, PHASE_SETUP)
        step = self.by_id.get(name)
        self.current = _Block(name, step)
        self.blocks.append(self.current)
        if step is not None:
            self.log.analysis_banner(step)

    def close_block(self, blk: _Block, how: str, signum: Optional[int] = None,
                    rc: Optional[int] = None) -> List[str]:
        """Classify the block's step once VACASK is past it or has ended, and print its messages and
        its done line.  how: next (the next analysis started), exit (VACASK exited 0), error (a
        non-zero exit, or a signal of rc 2), signal (rc 3).  Returns the lines after the step's own
        that belong to a fatal error (a non-zero exit after the analysis)."""
        if blk.closed:
            return []
        blk.closed = True
        lines = list(blk.lines)
        fatal: List[str] = []
        if how == "error" and rc is not None and rc > 0:
            last = max([i for i, x in enumerate(lines) if _terminator(x)] or [-1])
            lines, fatal = lines[:last + 1], lines[last + 1:]
        step = blk.step
        if step is None:
            self.narrate(lines, PHASE_RUN, blk.dropped)
            return fatal
        status = self.classify(step, lines, how)
        phase = self.phase_of(step)
        if status == "failed":
            reason = self.reasons.get(step.id, "")
            extra = ["vamos: analysis `%s' failed: %s" % (step.name, reason)] if reason else \
                ["vamos: analysis `%s' failed" % step.name]
            self.message(ERROR, lines, phase, extra, blk.dropped)
            fp = self.er.failed_points.get(step.id)
            if fp is not None:
                self.sweep_warning(step, fp, phase)
        else:
            self.narrate(lines, phase, blk.dropped)
            if status == "interrupted":
                self.log.message(warning("", self.interrupted_text(step, signum)), phase)
        self.log.analysis_done(_done_label(step), blk.elapsed if blk.elapsed is not None else time.time() - blk.t0)
        return fatal

    def sweep_warning(self, step: AnalysisStep, fp: int, phase: str) -> None:
        levels = list(step.context) if step.context else sweep_levels(step)
        total = _product(vacask_count(lv.spec) for lv in levels)
        names = ", ".join("`%s'" % (lv.name or step.name) for lv in levels)
        of = " of %d" % total if total else ""
        if fp > 1:
            kept = "point 1" if fp == 2 else "points 1-%d" % (fp - 1)
            text = ("sweep %s of analysis `%s' failed at point %d%s: the results of %s are written, the later "
                    "points have none" % (names, step.name, fp, of, kept))
        else:
            text = "sweep %s of analysis `%s' failed at its first point%s: no point has results" % (
                names, step.name, of)
        self.log.message(warning("", text), phase)

    def interrupted_text(self, step: AnalysisStep, signum: Optional[int]) -> str:
        chk = self.checks.get(step.id)
        at = " at time = %s" % si(chk.last, "s") if chk is not None and step.kind == "tran" and chk.last is not None else ""
        return "analysis `%s' was interrupted%s (%s); its partial results are written" % (
            step.name, at, self.stop_reason(signum))

    def classify(self, step: AnalysisStep, lines: Sequence[str], how: str) -> str:
        """The step's status by §7.1's rules on its rawfile; after a signal the incomplete rawfile of
        the analysis VACASK was in is repaired (rawfile.fix_points) and `interrupted`."""
        path = os.path.join(self.run_dir, step.id + ".raw")
        chk = check_step(step, path)
        self.checks[step.id] = chk
        if chk.exists:
            self.er.files[step.id] = path
        if chk.complete:
            status = "ok"
        elif how == "signal" and not any(_terminator(x) for x in lines):
            status = "failed"
            if chk.exists:
                try:
                    rawfile.fix_points(path)
                except (rawfile.RawError, OSError, ValueError):
                    pass
                fixed = check_step(step, path)
                fixed.reason = chk.reason
                self.checks[step.id] = fixed
                if fixed.n > 0:
                    status = "interrupted"
                else:
                    self.reasons[step.id] = "it was interrupted before it wrote a complete point"
            else:
                self.reasons[step.id] = "it was interrupted before it wrote any output"
        else:
            status = "failed"
            self.reasons[step.id] = chk.reason
            if chk.exists and sweep_levels(step):
                done = chk.done
                pos = _sweep_positions(lines)
                if pos:
                    if step.context and len(pos) >= len(step.context):
                        done = min(done, _flat(pos[:len(step.context)]) - 1)
                    elif not step.context:
                        done = min(done, pos[0][0] - 1)
                self.er.failed_points[step.id] = max(done, 0) + 1
        self.er.status[step.id] = status
        self.classified[step.id] = status
        return status

    # -- VACASK
    def engine(self, text: str) -> None:
        assert self.intr is not None
        os.makedirs(self.run_dir, exist_ok=True)
        for st in self.steps:                              # a reused run directory: no stale results
            _remove(os.path.join(self.run_dir, st.id + ".raw"))
        with open(os.path.join(self.run_dir, DECK), "w", encoding="utf-8") as fh:
            fh.write(text)
        cmd = [engines.vacask_bin(), DECK]
        self.echo(cmd, self.run_dir)
        if self.intr.signum is not None:
            raise _Stopped()
        try:
            p, fd, closer = _spawn(cmd, self.run_dir, self.env)
        except OSError as e:
            raise _Fail([error("", "cannot run VACASK %s: %s" % (cmd[0], e.strerror or e))], PHASE_SETUP)
        self.intr.attach(p)
        self.child = "VACASK"
        try:
            rc = self.pump(p, fd)
        finally:
            proc.kill_group(p)                           # left running only by an exception
            closer()
        if self.intr.second is not None:
            raise stop.Stop(self.intr.second)
        self.finish(rc)

    def read_ready(self, timeout: float) -> bool:
        """Read what VACASK has written within `timeout` seconds and feed its complete lines to the
        narration; False when nothing came (or the output ended)."""
        if self.eof or self.fd is None or self.sel is None:
            return False
        if not self.sel.select(timeout):
            return False
        try:
            chunk = os.read(self.fd, 65536)
        except OSError as e:
            if e.errno not in (errno.EIO, errno.EBADF):
                raise
            chunk = b""                                     # a pty whose child has closed it
        if not chunk:
            self.eof = True
            return False
        if self.keep is not None:
            self.keep.write(chunk)
        self.buf += chunk
        while b"\n" in self.buf:
            raw, self.buf = self.buf.split(b"\n", 1)
            self.feed(raw.decode("utf-8", "replace"))
        return True

    def pump(self, p: subprocess.Popen, fd: int) -> int:
        """VACASK's output, line by line into the narration, until it ends and VACASK exits; the
        flags every POLL seconds; a second signal ends the loop at once."""
        assert self.intr is not None
        self.fd, self.buf, self.eof = fd, b"", False
        self.sel = selectors.DefaultSelector()
        self.sel.register(fd, selectors.EVENT_READ)
        with open(os.path.join(self.run_dir, ENGINE_LOG), "wb") as keep:
            self.keep = keep
            try:
                while True:
                    if not self.eof:
                        self.read_ready(POLL)
                    else:
                        try:
                            p.wait(timeout=POLL)
                        except subprocess.TimeoutExpired:
                            pass
                    self.flags()
                    if self.intr.second is not None:
                        proc.kill_group(p)               # the handler's SIGKILL came first: this reaps it
                        return p.wait()
                    if self.eof and p.poll() is not None:
                        break
                if self.buf.strip():
                    self.feed(self.buf.decode("utf-8", "replace"))
            finally:
                self.keep, self.fd = None, None
                self.sel.close()
                self.sel = None
        return p.wait()

    def finish(self, rc: int) -> None:
        """The exit status (§2.7, stop.py) and the end of the narration."""
        assert self.intr is not None
        sig = -rc if rc < 0 else None
        if self.intr.signum is not None or (sig is not None and sig in _STOPPING):
            how, self.er.rc = "signal", 3
        elif rc != 0:
            how, self.er.rc = "error", 2
        else:
            how = "exit"
        fatal: List[str] = []
        if self.current is not None:
            fatal = self.close_block(self.current, how, signum=sig, rc=rc)
            self.current = None
        started = self.stage == "run"
        if not started and how != "error":
            lines, self.pre = self.pre, []
            self.narrate(lines, PHASE_SETUP)
        if how == "exit":
            for st in self.steps:                          # VACASK ran every analysis: each one has a status
                if st.id not in self.classified and self.classify(st, [], how) == "failed":
                    self.message(ERROR, [], self.phase_of(st),
                                 ["vamos: analysis `%s' failed: %s" % (st.name, self.reasons.get(st.id, ""))])
            self.er.rc = 1 if any(v == "failed" for v in self.er.status.values()) else 0
            return
        if how == "error":
            deck = os.path.join(self.run_dir, DECK)
            if not started:
                lines, self.pre = self.pre, []
                phase = PHASE_SETUP if self.stage == "setup" else PHASE_READIN
                why = ("VACASK was ended by %s before any analysis ran" % proc.signal_name(sig) if sig is not None
                       else "VACASK stopped with exit status %d before any analysis ran" % rc)
                self.message(ERROR, lines, phase, ["vamos: %s; the deck is %s" % (why, deck)])
                return
            last = next((b.step for b in reversed(self.blocks) if b.step is not None), None)
            if sig is not None:
                when = ""
                if last is not None:
                    when = " %s analysis `%s'" % ("after" if self.er.status.get(last.id) == "ok" else "during",
                                                  last.name)
                self.log.message(error("", "VACASK was ended by %s%s; the deck is %s"
                                       % (proc.signal_name(sig), when, deck)),
                                 self.phase_of(last) if last else PHASE_RUN)
            else:
                phase = "control statements after analysis `%s'" % last.name if last else PHASE_RUN
                self.message(ERROR, fatal, phase, ["vamos: VACASK stopped with exit status %d; the deck is %s"
                                                   % (rc, deck)])
            return
        if not any(v == "interrupted" for v in self.er.status.values()):
            nxt = [s for s in self.steps if s.id not in self.classified]
            where = " before analysis `%s'" % nxt[0].name if nxt else ""
            self.log.message(warning("", "the run was stopped%s (%s)" % (where, self.stop_reason(sig))), PHASE_RUN)


def run(nl: Netlist, plan: RunPlan, sigmap: SignalMap, run_dir: str, job: SpectreJob, log: SpectreLog,
        strict: bool = False) -> EngineResult:
    """Run the plan on VACASK (see the module docstring): the Verilog-A compile (§1 step 10), the
    deck <run_dir>/vamos.sim (step 11) and one VACASK run, under §2.7's signal handling.  Returns
    an EngineResult by stop.py's conventions; raises stop.Stop after a second INT, TERM, HUP or
    QUIT while a child runs (the child killed first).  strict: render's warnings are errors
    (--vamos-strict)."""
    if plan.engine != "vacask":
        raise ValueError("run_vacask.run: the plan was built for %s" % plan.engine)
    r = _Run(nl, plan, sigmap, run_dir, job, log, strict)
    with _Interrupts() as intr:
        r.intr = intr
        try:
            r.engine(r.render(r.compile_va()))
        except _Fail as f:
            for n in f.notes:
                log.message(n, f.phase)
            r.er.rc = 2
        except _Stopped:
            r.er.rc = 3
            log.message(warning("", "the run was stopped before VACASK ran (%s)" % r.stop_reason(None)), PHASE_SETUP)
    return r.er
