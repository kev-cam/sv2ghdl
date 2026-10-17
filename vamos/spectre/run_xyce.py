"""The Xyce runner of a spectre run: one deck per analysis (docs/VAMOS_SPECTRE_DESIGN.md §1 step 11,
§7.2; the API is the §10 block; the stopping contract is spectre/stop.py).

run(nl, plan, sigmap, run_dir, job, log, strict=False) -> EngineResult
    nl is the render copy signals.resolve returned (its probes inserted), plan the RunPlan for
    "xyce", sigmap the SignalMap.  Every `analysis` action becomes <run_dir>/vamos_a<k>.cir,
    rendered with xyce.render(..., step=, sigmap=, names=) from a copy of the IR with the alters
    up to that point applied (the state replay below; the options in force reach each deck
    through AnalysisStep.options, which plan.build fills).  The Verilog-A sources are copied
    into run_dir and the .hdl lines name them by base name [E104] (below).  All decks are
    rendered before Xyce runs: a deck that does not render is an error (exit status 2) before
    any engine run; render's warnings and notes are printed once each (circuit read-in), and
    under strict (--vamos-strict, the additive keyword run_vacask takes and flow passes) its
    warnings are errors.  Then:
      - the parameter check: `<xyce> -norun` on the first deck, and xyce.output_problems and
        xyce.device_summary_problems over its output and over every deck's output; a match is
        status 2, naming the alter or sweep that introduced it (the alters applied since the
        previous deck ran and the deck's own sweeps are the candidates: earlier ones were
        checked by the earlier decks), else the netlist or the analysis;
      - the decks run one after another in run_dir as engines.xyce_bin() (else
        tools.find_real("Xyce")) vamos_a<k>.cir, with engines.env_for("xyce", None,
        dict(os.environ)), stdin /dev/null, proc.child_setup, the output in vamos_a<k>.log (the
        -norun check's in vamos_a<k>.norun.log), all under one proc.Interrupts(stop.HANDLED,
        stop.FORWARD, stop.FLAGS) whose flags are polled while Xyce runs: SIGUSR1 prints
        log.status_line naming the running deck and Xyce's last "Percent complete", SIGUSR2 the
        notice "checkpoint not supported" (§2.7); each deck runs between its log.analysis_banner
        and its log.analysis_done line (Xyce's time; `dc` for an op, as run_vacask), so
        log.analysis names the running analysis meanwhile (the status line and the per-analysis
        message limits read it), and Xyce's messages of that deck are printed in between: its
        warnings (and any other message of a deck that did not fail) as warnings, each text once
        per run, at most ENGINE_LINES per deck, PyMS's registration lines left out (§8.7's
        filtered narration; the whole output stays in the deck's log);
      - the failure rules of §7.2: a non-zero exit, or a print file that is missing, blank or
        short (a "No. Points:" left blank or not equal to the rows; fewer plots or .PRINT
        tables than the .STEP points planned; an op plot with other than one row; a dc, ac or
        noise plot with other than the planned grid's rows; a tran that does not reach stop or
        whose time restarts inside a plot, which is how a failed .STEP point shows: Xyce goes
        on with the next point, exits 0 and appends it to the failed one) is that analysis
        failed, status 1, and the decks after it still run; the first failed .STEP group gives
        EngineResult.failed_points (1-based) when groups before it are complete; "Netlist
        error" or a parse error in the output (output_problems) is status 2 and stops the run;
        either way the decks that finished are converted (§2.7);
      - signals (§2.7, stop.py): the first INT, TERM, HUP or QUIT is passed to Xyce as SIGTERM;
        the running deck is "interrupted" and its print file is converted from what Xyce left
        (FORMAT=RAW files are readable after a signal [E72]; so is the noise .prn, whose rows
        Xyce writes whole: measured 2026-10-16, §14 q.26; a last line a signal cut would be
        dropped, so that read_prn reads the complete rows), later decks are not run, status 3;
        Xyce ended by SIGKILL, SIGTERM, SIGINT or SIGHUP from outside
        vamos is the same; Xyce ended by any other signal is a crash, status 2; a second
        handled signal kills Xyce and raises stop.Stop (vamos exits 3 without converting),
        while the grace-period kill (proc.INTERRUPT_GRACE) is converted as status 3;
      - xf is refused (plan.build refuses it on Xyce already; this is the guard).
    EngineResult: rc (stop.py's convention), status per step id (ok | failed | interrupted;
    absent for a deck that never ran), files (step id -> the absolute path of the print file,
    vamos_a<k>.raw, or vamos_a<k>.prn for noise, read by rawfile.read_prn), failed_points,
    names (render's, per step: step id -> Ref -> Xyce column) and log: one line per engine run
    (`<deck>: <arguments> -> exit <rc> in <s> s`) and Xyce's messages, one per line prefixed by
    the log they came from; flow does not print it again (everything above is printed here:
    log.message, and log.line for the --vamos-verbose command echo).

replay(nl, act, dependents=None) -> Netlist
    §7.2's state replay of one alter action, on a copy (the keys are §5.1's: Action.args
    target, value and source): an instance parameter in its IR spelling (SPECTRE_MASTERS'
    renames reversed, for the master tables.spectre_master_of names), an r/c/l value into
    Instance.value, a source field as the re-resolved Source; a model parameter on the card or
    on every card of a group (by base name); temp/tnom on the Netlist; a variable into
    Netlist.values and its top-level Param's expression, its dependents re-evaluated.
    test_spectre_chain.replay (phase 1b) is the reference.
apply_alter(nl, act, dependents=None) -> None
    The same, in place.
render_decks(nl, plan, sigmap, hdl=None, notes=None) -> (List[Deck], names)
    The decks of a plan without running them (the replay applied in order); NoteError with
    every deck's errors when one does not render.
place_va(hdl, run_dir) -> List[str]
    Copy the Verilog-A sources into run_dir, each with the files it `includes relative to
    itself (PyMS resolves an include beside the .va file [E75]; a copy without them compiles
    with the include skipped and a wrong result, measured), and return the names the .hdl
    lines use: the base name; a relative path from the common directory of the source and its
    includes when an include climbs out of the source's directory; under vamos_va<k>/ when
    two sources need the same name for different files (relative names with a directory work
    for two modules on PyMS, measured 2026-10-16).
"""

from __future__ import annotations

import copy
import filecmp
import os
import posixpath
import re
import shlex
import shutil
import signal
import subprocess
import time
import types
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Set, Tuple

from vamos import proc, tools
from vamos.netlist import expr as X
from vamos.netlist import ir, rawfile, xyce
from vamos.netlist import tables as T
from vamos.netlist.expr_ast import Num, Str
from vamos.notes import ERROR, Note, NoteError, error, has_errors, note, strict as strict_notes, warning
from vamos.spectre import stop
from vamos.spectre.results import EngineResult, grid_values

if TYPE_CHECKING:                                  # the run plan and the log (§5, §6, §8.7)
    from vamos.netlist.plan import Action, AnalysisStep, RunPlan, SweepLevel
    from vamos.netlist.signals import Ref, SignalMap
    from vamos.spectre.job import SpectreJob
    from vamos.spectre.log import SpectreLog

ENGINE = "xyce"
VALUE_KEY = {"resistor": "r", "capacitor": "c", "inductor": "l"}   # the r/c/l value in Instance.value
POLL = 0.1                      # seconds between two looks at the flags while Xyce runs
STOP_REL = 1e-9                 # a tran's last time equals stop to this relative tolerance (§7.1, §7.2)
ENGINE_LINES = 30               # Xyce messages printed per deck (§8.7: never raw floods; as run_vacask)
VA_SUBDIR = "vamos_va%d"        # a Verilog-A source that cannot sit in run_dir under its base name
PHASE_READIN = "circuit read-in"    # the phase of render's notes (§8.7; as run_vacask)
PHASE_SETUP = "initial setup"       # the phase of the -norun check
PHASE_WORDS = {"op": "DC analysis", "dc": "DC analysis", "ac": "AC analysis", "noise": "noise analysis",
               "xf": "XF analysis", "tran": "transient analysis"}
# Xyce's messages that say nothing about the run: PyMS registering a module, its preprocessor
# skipping a standard header it provides itself (§4.5 item 13); they stay in the deck's log
_QUIET = re.compile(r"\.HDL: compiled and registered |^preprocess: warning: '(?:disciplines|constants)\.vams' "
                    r"not found")
# the signals that, sent to Xyce by someone other than vamos, make status 3 (§2.7's table)
OUTSIDE_STOPS: Tuple[int, ...] = tuple(getattr(signal, n) for n in ("SIGKILL", "SIGTERM", "SIGINT", "SIGHUP")
                                       if hasattr(signal, n))

_PERCENT = re.compile(r"Percent complete:\s*([0-9.eE+-]+)\s*%")
_VA_INCLUDE = re.compile(r'^[ \t]*`include[ \t]+"([^"\n]+)"', re.M)
# Xyce's diagnostics (the narration of §8.7): a header line, then its indented continuation lines
_DIAG_HEAD = re.compile(r"^(?:(?:Netlist|Simulation|User|Device|Analysis)\s+)?(?:warning|error|fatal)\b"
                        r"|^Simulation aborted|^\*\*\* Xyce Abort|^Time step too small"
                        r"|^function \S+::\S+:|^preprocess: |^Failed DC sweep steps"
                        # PyMS compiling a module: a failed build it retries in a reduced form (measured
                        # 2026-10-16: `$abstime' is not declared, the module then runs without it)
                        r"|^build_vae_so: |^BuildError: |^\S+\.(?:cpp|hpp|h):\d+:\d+: error: ", re.I)
_DIAG_ERROR = re.compile(r"error|abort|fatal|too small|maximum number of failures|failed dc sweep|exiting|singular",
                         re.I)
# where Xyce's problem messages name the parameter (§7.2 [E43], E104, E108): its last ':' part is
# compared with an alter's or a sweep's parameter
_PROBLEM_PARAM = (re.compile(r"No model parameter (\S+) found for model"),
                  re.compile(r"Unrecognized parameter (\S+) for device"),
                  re.compile(r"Function or variable (\S+) is not defined"),
                  re.compile(r"Unable to find parameter (\S+)"))


# -- the decks ------------------------------------------------------------------------------------

@dataclass
class Deck:
    """One analysis action's deck (§7.2)."""
    step: "AnalysisStep"
    action: "Action"
    text: str
    alters: List["Action"] = field(default_factory=list)   # the alters applied since the previous deck

    @property
    def file(self) -> str:
        return self.step.id + ".cir"

    @property
    def output(self) -> str:
        """The print file render names (FILE=): .prn for noise (Xyce refuses RAW there [E12])."""
        return self.step.id + (".prn" if self.step.kind == "noise" else ".raw")


def render_decks(nl: ir.Netlist, plan: "RunPlan", sigmap: "SignalMap", hdl: Optional[Sequence[str]] = None,
                 notes: Optional[List[Note]] = None) -> Tuple[List[Deck], Dict[str, Dict["Ref", str]]]:
    """Every analysis action's deck, in plan order, each rendered from the IR with the alters up to
    that point applied (§7.2); names filled per step (step id -> Ref -> Xyce column).  hdl: the
    names the .hdl lines use (place_va's), None keeps nl.hdl.  nl itself is not changed.  Every
    deck is rendered; NoteError with every error (each once) when one does not render; the
    warnings and notes go to `notes` (each once)."""
    work = copy.deepcopy(nl)
    if hdl is not None:
        work.hdl = list(hdl)
    names: Dict[str, Dict["Ref", str]] = {}
    decks: List[Deck] = []
    pending: List["Action"] = []
    errors: List[Note] = []
    others: List[Note] = []
    for act in plan.actions:
        if act.op == "alter":
            apply_alter(work, act, plan.dependents)
            pending.append(act)
            continue
        if act.op != "analysis" or act.step is None:
            continue
        step = act.step
        got: List[Note] = []
        try:
            if step.kind == "xf":
                raise NoteError([error(act.origin, "analysis `%s': xf runs on VACASK only; it is not available on "
                                                   "Xyce (§3.11)" % step.name)])
            text = xyce.render(work, step=step, sigmap=sigmap, names=names, notes=got)
        except NoteError as exc:
            errors.extend(_step_origin(n, step) for n in exc.notes if n.severity == ERROR)
            others.extend(_step_origin(n, step) for n in exc.notes if n.severity != ERROR)
            text = ""
        except (T.TableError, X.PrintError, X.ExprError) as exc:
            errors.append(error(act.origin, "analysis `%s': %s" % (step.name, exc)))
            text = ""
        others.extend(_step_origin(n, step) for n in got)
        decks.append(Deck(step, act, text, pending))
        pending = []
    if errors:
        raise NoteError(_once(errors) + _once(others))
    if notes is not None:
        notes.extend(_once(others))
    return decks, names


def _step_origin(n: Note, step: "AnalysisStep") -> Note:
    """A render note whose origin is the step id: the analysis name for an error, none for the
    others (the option and nodeset notes speak for every deck)."""
    if n.origin != step.id:
        return n
    return Note(n.severity, "analysis `%s'" % step.name if n.severity == ERROR else "", n.message)


def _once(notes: Sequence[Note]) -> List[Note]:
    seen: Set[Tuple[str, str, str]] = set()
    out = []
    for n in notes:
        key = (n.severity, n.origin, n.message) if n.severity == ERROR else (n.severity, "", n.message)
        if key not in seen:
            seen.add(key)
            out.append(n)
    return out


# -- the state replay (§7.2) ----------------------------------------------------------------------

def replay(nl: ir.Netlist, act: "Action", dependents: Optional[Set[str]] = None) -> ir.Netlist:
    """A copy of nl with one alter action applied in IR spellings (§7.2; the keys read are §5.1's:
    target, value, source).  test_spectre_chain.replay is the reference."""
    out = copy.deepcopy(nl)
    apply_alter(out, act, dependents)
    return out


def apply_alter(nl: ir.Netlist, act: "Action", dependents: Optional[Set[str]] = None) -> None:
    """replay, in place.  dependents: RunPlan.dependents (the variables and the top-level
    parameters that read them); None finds a variable's dependents from the expressions."""
    kind, name, param = act.args["target"]
    value = act.args["value"]
    if kind == "instance":
        inst = find_instance(nl, name)
        if "source" in act.args:
            inst.source = copy.deepcopy(act.args["source"])
            return
        master = T.spectre_master_of(inst, _card_of(nl, inst))
        key = ir_param(master or inst.prim, "instance", param)
        if VALUE_KEY.get(master or inst.prim or "") == key:
            inst.value = ir_value(value)
        else:
            inst.params[key] = ir_value(value)
    elif kind == "model":
        for m in nl.models().values():
            if m.name == name or m.base == name:
                m.params[ir_param(m.prim, "card", param)] = ir_value(value)
    elif kind == "option":
        setattr(nl, name, float(value))
    elif kind == "variable":
        _set_variable(nl, name, float(value), dependents)


def find_instance(nl: ir.Netlist, path: str) -> ir.Instance:
    """The Instance an IR path (x1.r1) names, through the subckt definitions (Cond branches too)."""
    subs = _subckts(nl)
    body: Sequence[object] = nl.body
    inst: Optional[ir.Instance] = None
    for part in path.split("."):
        inst = next((it for it in ir.flat_items(body) if isinstance(it, ir.Instance) and it.name == part), None)
        if inst is None:
            raise KeyError("no instance %s (%s)" % (path, part))
        if inst.kind == "x" and inst.master in subs:
            body = subs[inst.master].body
    assert inst is not None
    return inst


def ir_param(prim: Optional[str], section: str, param: str) -> str:
    """The IR spelling of a target's parameter: plan.build applies SPECTRE_MASTERS' renames
    (§5.2), the IR keeps Spectre's names; `section` is "card" or "instance"."""
    row = T.SPECTRE_MASTERS.get(prim or "")
    if row is None:
        return param
    rules = row.params if section == "card" else row.instance
    if any(r.name == param for r in rules):
        return param
    for r in rules:
        if r.action == "rename" and r.value == param:
            return r.name
    return param


def ir_value(v: object):
    """An evaluated Setting (§5.1) as an IR expression."""
    if isinstance(v, list):
        return [Num(float(x)) for x in v]
    if isinstance(v, str):
        return Str(v)
    return Num(float(v))                                      # type: ignore[arg-type]


def _subckts(nl: ir.Netlist) -> Dict[str, ir.Subckt]:
    out: Dict[str, ir.Subckt] = {}

    def collect(items: Sequence[object]) -> None:
        for it in ir.flat_items(items):
            if isinstance(it, ir.Subckt):
                out[it.name] = it
                collect(it.body)
    collect(nl.body)
    return out


def _card_of(nl: ir.Netlist, inst: ir.Instance) -> Optional[ir.Model]:
    """The card a model instance names (a group: its first entry), for its master's rules."""
    if not inst.master or inst.kind not in T.MODEL_KINDS_OF:
        return None
    models = nl.models()
    if inst.master in models:
        return models[inst.master]
    return next((m for m in models.values() if m.base == inst.master), None)


def _set_variable(nl: ir.Netlist, name: str, value: float, dependents: Optional[Set[str]]) -> None:
    """A variable alter: Netlist.values and the top-level Param's expression (which the deck's
    .param prints), then the top-level parameters that read it re-evaluated in Netlist.values."""
    nl.values[name] = value
    top = [p for p in nl.body if isinstance(p, ir.Param)]
    for p in top:
        if p.name == name:
            p.expr = Num(value)
    reads: Set[str] = {name}
    changed = True
    while changed:                                  # the dependents, transitively (plan._dependents)
        changed = False
        for p in top:
            if p.name not in reads and _reads(p.expr) & reads:
                reads.add(p.name)
                changed = True
    if dependents is not None:
        reads &= set(dependents) | {name}
    for p in top:
        if p.name != name and p.name in reads and p.name in nl.values:
            try:
                nl.values[p.name] = X.evaluate(p.expr, nl.values, "spectre")
            except X.EvalError:
                pass


def _reads(e: object) -> Set[str]:
    try:
        return set(X.names(e))                      # type: ignore[arg-type]
    except Exception:  # noqa: BLE001  (not an expression)
        return set()


# -- the Verilog-A sources (§4.5 item 13) --------------------------------------------------------------

def va_closure(path: str) -> List[str]:
    """path and the files it `includes, transitively, where Xyce's PyMS reads them: relative to the
    including file [E75] (an absolute name is read where it is and needs no copy)."""
    seen: List[str] = []
    todo = [os.path.abspath(path)]
    while todo:
        p = todo.pop(0)
        if p in seen:
            continue
        seen.append(p)
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for name in _VA_INCLUDE.findall(text):
            if os.path.isabs(name):
                continue
            cand = os.path.normpath(os.path.join(os.path.dirname(p), name))
            if os.path.isfile(cand) and cand not in seen:
                todo.append(cand)
    return seen


def place_va(hdl: Sequence[str], run_dir: str) -> List[str]:
    """Copy the Verilog-A sources into run_dir (see the module docstring); the .hdl names, in order,
    each source once.  OSError when a copy fails."""
    names: List[str] = []
    placed: Dict[str, str] = {}
    for h in hdl:
        src = os.path.abspath(h)
        if src in placed:
            continue
        if not os.path.isfile(src):
            placed[src] = src                       # Xyce reports it
            names.append(src)
            continue
        files = va_closure(src)
        root = os.path.commonpath([os.path.dirname(f) for f in files])
        rels = [os.path.relpath(f, root) for f in files]
        k = 0
        while True:
            sub = VA_SUBDIR % k if k else ""
            dests = [os.path.join(run_dir, sub, r) for r in rels]
            if all(not os.path.exists(d) or (os.path.isfile(d) and filecmp.cmp(f, d, shallow=False))
                   for f, d in zip(files, dests)):
                break
            k += 1
        for f, d in zip(files, dests):
            if not os.path.exists(d):
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copyfile(f, d)
        parts = ([sub] if sub else []) + rels[0].split(os.sep)
        name = posixpath.join(*parts)
        placed[src] = name
        names.append(name)
    return names


# -- the print files ----------------------------------------------------------------------------------

def planned(step: "AnalysisStep") -> Tuple[int, int, Optional[int]]:
    """(the plots or .PRINT tables the deck writes, the plots per context group, the rows each
    plot holds, None for a tran): one plot per .STEP point (§7.2), rows by the deck's grid."""
    groups = 1
    for lv in step.context:
        groups *= len(grid_values(lv.spec))
    own = step.own
    per = 1
    rows: Optional[int] = None
    if step.kind in ("op", "dc"):
        rows = len(grid_values(own.spec)) if own is not None and own.target[0] != "freq" else 1
    elif step.kind in ("ac", "noise", "xf"):
        if own is not None and own.target[0] == "freq":
            rows = _freq_count(own)
        else:
            rows = 1                                    # a parameter sweep at one frequency: a .STEP
            if own is not None:
                per = len(grid_values(own.spec))
    return groups * per, per, rows


def _freq_count(lv: "SweepLevel") -> int:
    """The frequencies of an .AC/.NOISE grid as xyce's freq_text prints them: DEC and LIN as the
    grid, a step grid as the .DATA table it writes."""
    spec = lv.spec
    if spec.mode == "step":
        start, stop_, step = (float(getattr(e, "value", e)) for e in (spec.start, spec.stop, spec.step))
        n, f = 0, start
        while f <= stop_ * (1 + 1e-12) and step > 0:
            n += 1
            f += step
        return n
    return len(grid_values(spec))


@dataclass
class Check:
    """What a print file holds against the plan: complete, or the first bad plot (None: none
    can be named) and why."""
    complete: bool
    first_bad: Optional[int] = None
    why: str = ""


def check_output(step: "AnalysisStep", path: str) -> Check:
    """§7.2's rule over one deck's print file (see the module docstring)."""
    if not os.path.isfile(path):
        return Check(False, 0, "Xyce wrote no %s" % os.path.basename(path))
    want, _per, rows = planned(step)
    base = os.path.basename(path)
    counts: List[Tuple[Optional[int], int, Optional[float]]] = []      # (declared, rows, last x)
    steps: List[Optional[int]] = []                                    # each plot's .STEP number
    try:
        if path.endswith(".prn"):
            for raw in rawfile.read_prn(path):
                counts.append((len(raw.points), len(raw.points), None))
        else:
            scanned = rawfile.scan(path)
            for p in scanned:
                counts.append((p.declared, p.n, p.last))
            if want > 1:
                steps = _step_numbers(path, scanned, want)
    except (rawfile.RawError, OSError, ValueError) as exc:
        return Check(False, 0, "%s is unreadable: %s" % (base, exc))
    if steps and all(k is not None for k in steps) and steps != list(range(1, len(steps) + 1)):
        # a .STEP point Xyce could not solve leaves no plot and the run goes on (measured on e2e 11:
        # "Step 1 of 3", "Step 3 of 3"): the plot names say which point failed, positions would not
        missing = next(k for k in range(1, want + 1) if k not in steps)
        return Check(False, missing - 1, "%s has no plot for .STEP point %d of %d (Xyce went on past it)"
                     % (base, missing, want))
    stop_t = _tran_stop(step)
    for j, (declared, n, last) in enumerate(counts[:want]):
        what = "plot %d of %s" % (j + 1, base) if want > 1 else base
        if declared is None:
            return Check(False, j, "%s has a blank No. Points (%d rows)" % (what, n))
        if declared != n:
            return Check(False, j, "%s declares %d points and holds %d" % (what, declared, n))
        if rows is not None and n != rows:
            return Check(False, j, "%s holds %d rows where %d were planned" % (what, n, rows))
        if rows is None:
            if n == 0:
                return Check(False, j, "%s holds no time point" % what)
            if stop_t is not None and last is not None and abs(last - stop_t) > STOP_REL * max(abs(stop_t), 1e-30):
                return Check(False, j, "%s ends at t = %g, not at stop = %g" % (what, last, stop_t))
    if len(counts) < want:
        if rows is None and not path.endswith(".prn"):
            for j in range(len(counts)):
                if _restarts(path, j):
                    return Check(False, j, "plot %d of %s restarts its time axis: a failed .STEP point, which "
                                           "Xyce continued past" % (j + 1, base))
        return Check(False, len(counts), "%s holds %d of the %d .STEP points planned" % (base, len(counts), want))
    if len(counts) > want:
        return Check(False, None, "%s holds %d plots where %d were planned" % (base, len(counts), want))
    return Check(True)


_STEP_PLOT = re.compile(r"\bStep (\d+) of (\d+)\b")


def _step_numbers(path: str, scanned: Sequence[rawfile.RawPlot], want: int) -> List[Optional[int]]:
    """Each plot's .STEP number from its Plotname ("Step Analysis: Step 3 of 3 params: ..."); None
    for a plot that names none, or none at all when the plots count other than `want` steps."""
    from vamos.spectre.results import _raw_header           # the header reader collect uses
    out: List[Optional[int]] = []
    starts = [0] + [p.next_start for p in scanned[:-1]]
    for s in starts:
        m = _STEP_PLOT.search(_raw_header(path, s).plotname or "")
        if m is None or int(m.group(2)) != want:
            return []
        out.append(int(m.group(1)))
    return out


def _tran_stop(step: "AnalysisStep") -> Optional[float]:
    if step.kind != "tran":
        return None
    v = step.args.get("stop")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _restarts(path: str, plot: int) -> bool:
    """Whether the time column of a plot decreases somewhere (two .STEP points in one plot)."""
    prev = None
    try:
        for (t,) in rawfile.iter_rows(path, plot, [0]):
            x = t.real if isinstance(t, complex) else t
            if prev is not None and x < prev:
                return True
            prev = x
    except (rawfile.RawError, OSError, IndexError, ValueError):
        return False
    return False


def trim_partial_line(path: str) -> bool:
    """Drop a last line the signal cut (no newline at the end) from a text print file, so that
    rawfile.read_prn reads the complete rows (§14 q.26); True if the file changed."""
    try:
        with open(path, "rb+") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            if size == 0:
                return False
            fh.seek(size - 1)
            if fh.read(1) == b"\n":
                return False
            keep, end = 0, size
            while end > 0:                              # back to the last newline, a chunk at a time
                start = max(0, end - (1 << 16))
                fh.seek(start)
                cut = fh.read(end - start).rfind(b"\n")
                if cut >= 0:
                    keep = start + cut + 1
                    break
                end = start
            fh.truncate(keep)
            return True
    except OSError:
        return False


# -- the engine runs ---------------------------------------------------------------------------------

class Signals(proc.Interrupts):
    """proc.Interrupts with spectre's signals (stop.py), which also tells a second handled signal
    (`second`, stop.Stop) from the grace-period kill (`expired`, converted as status 3): the two
    set the same `killed` flag."""

    def __init__(self, grace: Optional[float] = None) -> None:
        super().__init__(stop.HANDLED, stop.FORWARD, stop.FLAGS,
                         grace=proc.INTERRUPT_GRACE if grace is None else grace)
        self.second: Optional[int] = None
        self.expired = False

    def _on_signal(self, signum, frame) -> None:
        if self.signum is not None and self.second is None:
            self.second = signum
        proc.Interrupts._on_signal(self, signum, frame)

    def _on_alarm(self, signum, frame) -> None:
        self.expired = True
        proc.Interrupts._on_alarm(self, signum, frame)


def ended_by(rc: int) -> Optional[int]:
    """The signal that ended Xyce, from its exit status: -N (killed by N), or 128+N for the stop
    signals as a shell reports them (E72's 130 and 143); None for a plain exit."""
    if rc < 0:
        return -rc
    if rc > 128 and (rc - 128) in OUTSIDE_STOPS + tuple(stop.HANDLED):
        return rc - 128
    return None


def diagnostics(text: str) -> List[str]:
    """Xyce's warnings and errors in its output, one string each (a header line and its indented
    continuation lines), consecutive repeats dropped: the narration of §8.7, without Xyce's
    banner, progress and timing lines."""
    msgs: List[str] = []
    cur: Optional[List[str]] = None

    def close() -> None:
        if cur:
            m = " ".join(cur)
            if not msgs or msgs[-1] != m:
                msgs.append(m)

    for line in text.splitlines():
        s = line.strip()
        if s and _DIAG_HEAD.match(s):
            close()
            cur = [s]
        elif cur is not None and _continues(cur[0], line):
            cur.append(s)
        else:
            close()
            cur = None
    close()
    return msgs


def _continues(head: str, line: str) -> bool:
    """Whether an indented line continues a diagnostic: Xyce wraps a message onto lines that start
    with one space; a header ending in ':' (`function OneStep::rejectStep:`, `Failed DC sweep
    steps:`) takes its indented lines; the solver statistics and the timing table never do."""
    s = line.strip()
    if not s or not line[:1].isspace() or s.startswith(("Number ", "Total ")) or "%)" in s:
        return False
    if head.endswith(":"):
        return True
    return line.startswith(" ") and not line[1:2].isspace()


def _done_label(step: "AnalysisStep") -> types.SimpleNamespace:
    """What log.analysis_banner/analysis_done read for a step: its title and name, and its PSF
    analysis type (`dc` for an op), so the line reads `... for dc analysis `myop' ...` as
    run_vacask's does."""
    from vamos.spectre.log import ANALYSIS_TITLES
    title = ANALYSIS_TITLES.get(step.kind, "%s Analysis" % step.kind)
    return types.SimpleNamespace(description="%s `%s'" % (title, step.name), key=step.name,
                                 atype="dc" if step.kind in ("op", "dc") else step.kind)


class _Runner:
    def __init__(self, nl: ir.Netlist, plan: "RunPlan", sigmap: "SignalMap", run_dir: str, job: "SpectreJob",
                 log: Optional["SpectreLog"], strict: bool = False) -> None:
        self.nl, self.plan, self.sigmap, self.job, self.log = nl, plan, sigmap, job, log
        self.strict = strict
        self.run_dir = os.path.abspath(run_dir)
        self.er = EngineResult()
        self.decks: List[Deck] = []
        self.intr: Optional[Signals] = None
        self.exe = ""
        self.env: Dict[str, str] = {}
        self.names_of: Dict[str, str] = {}            # an alter's origin -> its Spectre name
        self.sweeps_at: Dict[str, str] = {}           # a sweep block's name -> its origin
        for a in ir.flat_analyses(nl.analyses):
            if a.kind == "alter" and a.origin and a.name:
                self.names_of.setdefault(a.origin, a.name)
            elif a.kind == "sweep" and a.name:
                self.sweeps_at.setdefault(a.name, a.origin)
        self.running: Optional[Deck] = None
        self.told: Set[str] = set()                   # Xyce messages already printed (once per run)

    # -- messages
    def say(self, n: Note, phase: str) -> None:
        if self.log is not None:
            self.log.message(n, phase)

    def record(self, text: str) -> None:
        self.er.log.append(text)

    @staticmethod
    def phase(deck: Deck) -> str:
        return "%s `%s'" % (PHASE_WORDS.get(deck.step.kind, "%s analysis" % deck.step.kind), deck.step.name)

    def fail(self, text: str, phase: str) -> EngineResult:
        """A setup error before any engine run: exit status 2."""
        self.say(error("", text), phase)
        self.er.rc = 2
        return self.er

    # -- the run
    def run(self) -> EngineResult:
        er = self.er
        steps = [a.step for a in self.plan.actions if a.op == "analysis" and a.step is not None]
        if not steps:
            return er
        bad = [s for s in steps if s.kind == "xf"]
        if bad:
            for s in bad[1:]:
                self.say(error("", "analysis `%s': xf runs on VACASK only; it is not available on Xyce (§3.11)"
                               % s.name), PHASE_SETUP)
            return self.fail("analysis `%s': xf runs on VACASK only; it is not available on Xyce (§3.11)"
                             % bad[0].name, PHASE_SETUP)
        try:
            self.exe = xyce_exe()
        except tools.ToolError as exc:
            return self.fail(str(exc), PHASE_SETUP)
        if not self.exe:
            return self.fail("Xyce not found (set VAMOS_XYCE, §7.2)", PHASE_SETUP)
        try:
            os.makedirs(self.run_dir, exist_ok=True)
            hdl = place_va(self.nl.hdl, self.run_dir)
        except OSError as exc:
            return self.fail("cannot copy the Verilog-A sources into the run directory: %s" % exc, PHASE_SETUP)
        notes: List[Note] = []
        try:
            self.decks, names = render_decks(self.nl, self.plan, self.sigmap, hdl, notes)
        except NoteError as exc:
            notes = list(exc.notes)
        notes = strict_notes(notes, self.strict)          # --vamos-strict: render's warnings are errors
        for n in notes:
            self.say(n, PHASE_READIN)
        if has_errors(notes):
            er.rc = 2
            return er
        er.names = names
        try:
            for d in self.decks:
                with open(os.path.join(self.run_dir, d.file), "w", encoding="utf-8") as fh:
                    fh.write(d.text)
        except OSError as exc:
            return self.fail("cannot write the Xyce decks: %s" % exc, PHASE_SETUP)
        self.env = engines_env()
        with Signals() as intr:
            self.intr = intr
            try:
                rc = self.norun(self.decks[0])
                if rc == 0:
                    for d in self.decks:
                        if intr.signum is not None:
                            break
                        rc = max(rc, self.run_deck(d))
                        if rc >= 2:
                            break
                er.rc = rc
                self.flags()
            finally:
                self.intr = None
            if intr.second is not None:
                raise stop.Stop(intr.second, "stopped by a second signal (%s): Xyce was killed and nothing is "
                                             "converted" % proc.signal_name(intr.second))
        if intr.signum is not None:
            er.rc = 3
        return er

    def engine(self, args: List[str], logname: str, deck: Deck) -> Tuple[int, str, float]:
        """One Xyce process in run_dir under the run's Signals; (exit status, its output, seconds).
        An exit status of -1000 stands for a Xyce that could not be started."""
        cmd = [self.exe] + args
        if os.environ.get("VAMOS_VERBOSE") and self.log is not None:
            self.log.line("vamos: + (cd %s && %s)" % (shlex.quote(self.run_dir), " ".join(shlex.quote(c) for c in cmd)))
        logpath = os.path.join(self.run_dir, logname)
        assert self.intr is not None
        t0 = time.time()
        with open(logpath, "w", encoding="utf-8", errors="replace") as out:
            try:
                p = subprocess.Popen(cmd, cwd=self.run_dir, env=self.env, stdin=subprocess.DEVNULL, stdout=out,
                                     stderr=subprocess.STDOUT, preexec_fn=proc.child_setup())
            except OSError as exc:
                return -1000, "cannot run %s: %s" % (self.exe, exc), 0.0
            self.intr.attach(p)
            try:
                while True:
                    try:
                        rc = p.wait(timeout=POLL)
                        break
                    except subprocess.TimeoutExpired:
                        self.flags()
            finally:
                if p.poll() is None:                    # an exception above: never leave Xyce behind
                    p.kill()
                    p.wait()
        seconds = time.time() - t0
        try:
            with open(logpath, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            text = ""
        self.record("%s: %s -> exit %d in %.2f s" % (deck.file, " ".join(cmd[1:]), rc, seconds))
        for m in diagnostics(text):
            self.record("%s: %s" % (logname, m))
        return rc, text, seconds

    def flags(self) -> None:
        """The SIGUSR1 status line and the SIGUSR2 note (§2.7), from the main loop."""
        if self.intr is None:
            return
        usr1, usr2 = getattr(signal, "SIGUSR1", None), getattr(signal, "SIGUSR2", None)
        for sig in self.intr.take_flags():
            if sig == usr1 and self.log is not None:
                self.log.status_line(self.progress())
            elif sig == usr2:
                self.say(note("", "checkpoint not supported: SIGUSR2 is ignored and the run continues"),
                         self.phase(self.running) if self.running is not None else PHASE_SETUP)

    def progress(self) -> str:
        d = self.running
        if d is None:
            return "Xyce not running"
        k = self.decks.index(d) + 1
        text = "Xyce deck %s (%d of %d)" % (d.file, k, len(self.decks))
        try:
            with open(os.path.join(self.run_dir, d.step.id + ".log"), "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 8192))
                tail = fh.read().decode("latin-1")
            found = _PERCENT.findall(tail)
            if found:
                text += ", %s %% complete" % found[-1]
        except OSError:
            pass
        return text

    def narrate(self, text: str, deck: Deck, skip: Sequence[str] = ()) -> None:
        """Xyce's messages of one deck (§8.7's filtered narration): its warnings as warnings, any other
        message of a deck that did not fail as a warning too, each text once per run (the decks
        share the circuit, so a model warning would repeat in every deck), at most ENGINE_LINES per
        deck; PyMS's registration lines and the problems (`skip`, reported as errors) left out."""
        shown = more = 0
        for m in diagnostics(text):
            if m in self.told or _QUIET.search(m) or any(s in m or m in s for s in skip):
                continue
            self.told.add(m)
            if shown >= ENGINE_LINES:
                more += 1
                continue
            shown += 1
            self.say(warning("", "Xyce: %s" % m), self.phase(deck))
        if more:
            self.say(note("", "%d more Xyce messages in %s" % (more, os.path.join(self.run_dir, deck.step.id + ".log"))),
                     self.phase(deck))

    # -- the parameter check (§7.2)
    def norun(self, deck: Deck) -> int:
        """`<xyce> -norun` on the first deck: 0 when it passes, 2 for a problem, 3 for a signal."""
        rc, text, _ = self.engine(["-norun", deck.file], deck.step.id + ".norun.log", deck)
        assert self.intr is not None
        if rc == -1000:
            self.say(error("", text), PHASE_SETUP)
            return 2
        sig = ended_by(rc)
        if self.intr.signum is not None or (sig is not None and sig in OUTSIDE_STOPS):
            self.say(warning("", "the Xyce check of the netlist was stopped (%s); no analysis ran"
                             % self.stop_reason(sig)), PHASE_SETUP)
            return 3
        problems = self.problems(text, deck)
        if problems:
            for n in problems:
                self.say(n, PHASE_SETUP)
            return 2
        if rc != 0:
            msgs = [m for m in diagnostics(text) if _DIAG_ERROR.search(m)] or diagnostics(text)[-5:]
            what = ("exit status %d" % rc) if sig is None else ("ended by %s" % proc.signal_name(sig))
            self.say(error("", "Xyce's check of the netlist (-norun on %s, the deck of analysis `%s') failed (%s)%s"
                           % (deck.file, deck.step.name, what, (": " + "; ".join(msgs[:ENGINE_LINES])) if msgs else "")),
                     PHASE_SETUP)
            return 2
        return 0

    def stop_reason(self, sig: Optional[int]) -> str:
        """The signal that stopped Xyce: the one vamos got and passed on, else Xyce's own."""
        assert self.intr is not None
        if self.intr.signum is not None:
            return proc.signal_name(self.intr.signum)
        return "%s from outside vamos" % proc.signal_name(sig) if sig is not None else "a signal"

    def problems(self, text: str, deck: Deck) -> List[Note]:
        """output_problems and device_summary_problems over one Xyce output, each named by the
        alter or sweep that introduced it where one matches its parameter."""
        found = list(xyce.output_problems(text, self.nl)) + list(xyce.device_summary_problems(text, self.nl))
        out = []
        seen: Set[str] = set()
        for n in found:
            if n.message in seen:
                continue
            seen.add(n.message)
            who = self.introducer(n.message, deck)
            if who is not None:
                origin, label = who
                out.append(error(origin, "%s sets a parameter Xyce does not know (the deck of analysis `%s', %s): %s"
                                 % (label, deck.step.name, deck.file, n.message)))
            else:
                out.append(error(n.origin if n.origin != "Xyce" else "",
                                 "Xyce, the deck of analysis `%s' (%s): %s" % (deck.step.name, deck.file, n.message)))
        return out

    def introducer(self, message: str, deck: Deck) -> Optional[Tuple[str, str]]:
        """(origin, label) of the alter or sweep whose parameter a problem message names: the
        deck's own sweep levels, then the alters applied since the previous deck ran (latest
        first; the earlier ones were checked by the earlier decks); None when none matches."""
        named = {m.split(":")[-1].upper() for rx in _PROBLEM_PARAM for m in rx.findall(message)}
        if not named:
            return None

        def hit(param: str) -> bool:
            cands = {param.upper()}
            if param.lower().startswith("instance_"):          # printed without the prefix on Xyce
                cands.add(param[len("instance_"):].upper())
            return bool(cands & named)

        step = deck.step
        for lv in list(step.context) + ([step.own] if step.own is not None else []):
            kind, _name, param = lv.target
            if kind in ("instance", "model") and param and hit(param):
                if lv.name:
                    return self.sweeps_at.get(lv.name, ""), "sweep `%s'" % lv.name
                return deck.action.origin, "the sweep of analysis `%s'" % step.name
        for act in reversed(deck.alters):
            kind, _name, param = act.args["target"]
            if kind in ("instance", "model") and "source" not in act.args and param and hit(param):
                name = self.names_of.get(act.origin, "")
                return act.origin, "alter `%s'" % name if name else "the alter at %s" % act.origin
        return None

    # -- one deck
    def run_deck(self, deck: Deck) -> int:
        """Run one deck between its analysis banner and its time line (§8.7) and apply §7.2's rules;
        the deck's contribution to EngineResult.rc."""
        step = deck.step
        if self.log is not None:
            self.log.analysis_banner(step)               # log.analysis names it meanwhile
        self.running = deck
        try:
            rc, text, seconds = self.engine([deck.file], step.id + ".log", deck)
        finally:
            self.running = None
        try:
            return self.classify(deck, rc, text)
        finally:
            if self.log is not None:
                self.log.analysis_done(_done_label(step), seconds)

    def classify(self, deck: Deck, rc: int, text: str) -> int:
        er, step, intr = self.er, deck.step, self.intr
        assert intr is not None
        out = os.path.join(self.run_dir, deck.output)
        if os.path.isfile(out):
            er.files[step.id] = out
        phase = self.phase(deck)
        if rc == -1000:
            er.status[step.id] = "failed"
            self.say(error("", text), phase)
            return 2
        sig = ended_by(rc)
        if intr.signum is not None or (sig is not None and sig in OUTSIDE_STOPS):
            chk = check_output(step, out)
            if rc == 0 and chk.complete:
                er.status[step.id] = "ok"
                self.narrate(text, deck)
                return 3
            er.status[step.id] = "interrupted"
            if out.endswith(".prn") and os.path.isfile(out) and trim_partial_line(out):
                self.record("%s: the last line, cut by the signal, was dropped (§14 q.26)" % deck.output)
            self.narrate(text, deck)
            at = ""
            if step.kind == "tran" and os.path.isfile(out):
                try:
                    last = [p.last for p in rawfile.scan(out)][-1]
                    if last is not None:
                        from vamos.spectre.log import si
                        at = " at time = %s" % si(last, "s")
                except (rawfile.RawError, OSError, IndexError):
                    pass
            self.say(warning("", "analysis `%s' was interrupted%s (%s); its partial results (%s) are written, and "
                                 "the analyses after it are not run"
                             % (step.name, at, self.stop_reason(sig), deck.output)), phase)
            return 3
        if sig is not None:
            er.status[step.id] = "failed"
            self.say(error("", "Xyce crashed (ended by %s) in analysis `%s' (%s)%s"
                           % (proc.signal_name(sig), step.name, deck.file, self._errors_of(text))), phase)
            return 2
        problems = self.problems(text, deck)
        if problems:
            er.status[step.id] = "failed"
            self.narrate(text, deck, [n.message for n in problems])
            for n in problems:
                self.say(n, phase)
            return 2
        chk = check_output(step, out)
        if rc == 0 and chk.complete:
            er.status[step.id] = "ok"
            self.narrate(text, deck)
            return 0
        er.status[step.id] = "failed"
        _total, per, _rows = planned(step)
        point = ""
        if chk.first_bad is not None and step.context:
            g = chk.first_bad // per
            if g >= 1:
                er.failed_points[step.id] = g + 1
                point = "; the sweep points before point %d are written" % (g + 1)
        reasons = []
        if rc != 0:
            reasons.append("exit status %d" % rc)
        if not chk.complete:
            reasons.append(chk.why)
        errs = [m for m in diagnostics(text) if _DIAG_ERROR.search(m)]
        self.narrate(text, deck, errs)
        self.say(error("", "analysis `%s' failed in Xyce (%s)%s%s"
                       % (step.name, "; ".join(reasons), point, (": " + "; ".join(errs[:ENGINE_LINES])) if errs else "")),
                 phase)
        return 1

    @staticmethod
    def _errors_of(text: str) -> str:
        msgs = [m for m in diagnostics(text) if _DIAG_ERROR.search(m)]
        return (": " + "; ".join(msgs[:ENGINE_LINES])) if msgs else ""


def xyce_exe() -> str:
    """The Xyce vamos runs (§7.2): engines.xyce_bin() (VAMOS_XYCE, else the build tree's launcher
    when it is executable), else tools.find_real("Xyce"); "" when there is none."""
    from vamos.ams import engines
    return engines.xyce_bin() or tools.find_real("Xyce") or ""


def engines_env() -> Dict[str, str]:
    """The environment of every Xyce run (§7.1, §7.2: E68, E96)."""
    from vamos.ams import engines
    return engines.env_for(ENGINE, None, dict(os.environ))


def run(nl: ir.Netlist, plan: "RunPlan", sigmap: "SignalMap", run_dir: str, job: "SpectreJob",
        log: "SpectreLog", strict: bool = False) -> EngineResult:
    """Run the plan on Xyce, one deck per analysis (see the module docstring).  strict
    (--vamos-strict, the additive keyword run_vacask takes and flow passes): render's warnings
    are errors, exit status 2 before Xyce runs."""
    return _Runner(nl, plan, sigmap, run_dir, job, log, strict).run()
