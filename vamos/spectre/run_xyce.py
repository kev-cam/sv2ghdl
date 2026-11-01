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
    warnings are errors.  A Verilog-A `include that PyMS would not find (missing_includes) is an
    error too (AHDL read-in, status 2, Xyce not run): PyMS would build the module without it.
    Then:
      - the parameter check: `<xyce> -norun` on the first deck, and xyce.output_problems and
        xyce.device_summary_problems over its output and over every deck's output; a match is
        status 2, naming the alter or sweep that introduced it (the alters applied since the
        previous deck ran and the deck's own sweeps are the candidates: earlier ones were
        checked by the earlier decks), else the netlist or the analysis; so is a PyMS build of a
        module that failed for good (pyms_failures: Xyce would run it as a device that
        contributes nothing) and an include PyMS skipped that is not a standard header;
      - the decks run one after another in run_dir as engines.xyce_bin() (else
        tools.find_real("Xyce")) vamos_a<k>.cir, with engines.env_for("xyce", None,
        tools.child_env()) and tools.bound_blas_threads, stdin /dev/null, proc.child_setup, the
        output in vamos_a<k>.log (the -norun check's in vamos_a<k>.norun.log), all under one
        proc.Interrupts(stop.HANDLED, stop.FORWARD, stop.FLAGS, group=True), every signal to Xyce's
        whole process group (its PyMS compile included), whose flags are polled while Xyce runs:
        SIGUSR1 prints log.status_line naming the running deck and Xyce's last "Percent complete"
        (during the -norun check, that check; and the file PyMS compiles, when it does), SIGUSR2
        the notice stop.CHECKPOINT (§2.7); each deck runs between its log.analysis_banner
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
        handled signal (not a repeat of one hangup's SIGHUP: stop.classify, TRIAL2EF-01)
        kills Xyce and raises stop.Stop as soon as Xyce has ended, before its
        output is classified (vamos exits 3 without converting), while the grace-period kill
        (proc.INTERRUPT_GRACE) is converted as status 3, the warning naming the kill;
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
    renames reversed, for the master tables.spectre_master_of names), a master's value
    parameter (VALUE_KEY: r, c, l, a gain, gm or rm, a coupling, an area) into
    Instance.value, a source field as the re-resolved Source; a model parameter on the card or
    on every card of a group (by base name); temp/tnom on the Netlist; a variable into
    Netlist.values and its top-level Param's expression, its dependents re-evaluated.
    test_spectre_chain.replay (phase 1b) is the reference.  Phase 2c5 (R2): an instance target
    is set on the instance its path names on the instance path the run takes (taken_instance:
    every if resolved in the state replayed, as the Xyce deck's elaboration resolves it), never
    on an untaken branch's duplicate (find_instance's first match was one: out 0.5 where Spectre
    gives 0.25) nor in the definition an untaken branch instantiates (2c4's find_instances
    changed it, and another instance of it took the alter: out2 0.25 where 0.5); an instance in
    a definition another instance path reaches too is a NoteError (plan.build refuses such a
    target on Xyce, §5.3), so the shared definition is never changed.
apply_alter(nl, act, dependents=None) -> None
    The same, in place.
render_decks(nl, plan, sigmap, hdl=None, notes=None) -> (List[Deck], names)
    The decks of a plan without running them (the replay applied in order); NoteError with
    every deck's errors when one does not render.
place_va(hdl, run_dir) -> List[str]
    Copy the Verilog-A sources into run_dir, each with the files it `includes relative to
    itself (PyMS resolves an include beside the including file, then beside the top file [E75];
    a copy without them compiles with the include skipped and a wrong result, measured), and
    return the names the .hdl lines use: the base name; a relative path from the common
    directory of the source and its includes when an include climbs out of the source's
    directory; under vamos_va<k>/ when two sources need the same name for different files.
    No directory but run_dir holds two modules' files (phase 2c2): PyMS gives a module without
    xyce* attributes those of a .va beside it, so two modules of one library side by side in
    lib/ lost their parameters [E125]; a module that needs a directory gets one of its own
    (its layout under run_dir when that is free, else its own vamos_va<k>/), while base names
    in run_dir have no directory PyMS looks in [E104].
missing_includes(hdl) -> List[(source, including file, name)]
    The `includes PyMS would not find (beside the including file, beside the top file, in
    PYMS_INCLUDE_PATH or in its own directory) and that are not STANDARD_HEADERS; only those
    outside `ifdef blocks, whose conditions vamos does not evaluate.
undefined_macros(hdl) -> List[(source, file, line, macro)]
    macro_problems without its reasons.
macro_problems(hdl) -> List[(source, file, line, macro, why)]
    The macro uses that PyMS would compile wrong, read as PyMS's preprocessor reads the source (PymsScan):
    one that is not defined where it stands (nothing `defines it, its `define sits in a branch
    not taken, an `undef came first, or the `define comes later), and one that PyMS expands with
    another definition than the one in force there (an `undef or a new `define later in the file:
    PyMS expands the macros of a file at its end).  FIX2E4; FIX2E5 for the conditions and undefs.
pyms_default_problems(hdl) -> List[(source, file, line, parameter, why)]
    The parameter declarations whose default PyMS evaluates as 0: a scaled number inside an
    expression (1k * 1), a scale factor it does not read (1a), two parameters in one statement
    (FIX2E5, TRIAL2EH-F1), and every form its evaluation cannot read: a function call, a ternary, a
    digit separator, the power operator, a name that is not a parameter (FIX2E6, _eval_form).
pyms_probe_problems(hdl) -> List[(source, file, line, probe, why)]
    The currents I(...) a module reads in an expression: PyMS builds a contribution that assigns a
    current from voltages and reads no current (a node pair's fails its C++ compile, a named branch's
    is 0; FIX2E6, TRIAL2EI-1).  PymsScan.annotation: an undefined `name(...), PyMS's annotation form
    (BSIM-CMG's `attr), is accepted where PyMS's parser drops it (FIX2E6, S-2E5's regression).
pyms_failures(text) -> List[PymsFailure]
    The PyMS builds in a Xyce output that failed for good: a Python traceback through
    build_vae_so.py (its retry failed too, a step timed out, or the build raised), each with
    the module its library names and the compiler's first error, else the exception's line.
"""

from __future__ import annotations

import ast
import copy
import filecmp
import glob
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
from vamos.names import escape_name, unescape_text
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
# the value parameter spectre.parse moves into Instance.value, per master (plan.VALUE_PARAMS): r, c, l, a controlled
# source's gain, gm or rm, a mutual inductor's coupling, a diode's, bjt's or jfet's area.  Phase 2c2: the replay of an
# alter of a gain wrote params["gain"], which render refuses ("E1: parameter gain not supported on a E element")
VALUE_KEY = {"resistor": "r", "capacitor": "c", "inductor": "l", "vcvs": "gain", "vccs": "gm", "ccvs": "rm",
             "cccs": "gain", "mutual_inductor": "coupling", "diode": "area", "bjt": "area", "jfet": "area"}
POLL = 0.1                      # seconds between two looks at the flags while Xyce runs
STOP_REL = 1e-9                 # a tran's last time equals stop to this relative tolerance (§7.1, §7.2)
ENGINE_LINES = 30               # Xyce messages printed per deck (§8.7: never raw floods; as run_vacask)
VA_SUBDIR = "vamos_va%d"        # a Verilog-A source that cannot sit in run_dir under its base name
PHASE_READIN = "circuit read-in"    # the phase of render's notes (§8.7; as run_vacask)
PHASE_AHDL = "AHDL read-in"         # the phase of the Verilog-A checks before Xyce runs (as run_vacask's compile)
PHASE_SETUP = "initial setup"       # the phase of the -norun check
PHASE_WORDS = {"op": "DC analysis", "dc": "DC analysis", "ac": "AC analysis", "noise": "noise analysis",
               "xf": "XF analysis", "tran": "transient analysis"}
# the standard headers, Verilog-AMS's and Spectre's (the ones openvaf-r 20260616 provides itself): PyMS
# ships constants.vams and does without the others (it knows the disciplines and the constants), so a
# module that includes one runs when PyMS skips it (measured 2026-10-17: discipline.h and constants.h,
# ref19's examples pp.452 and 541, and disciplines.h, b = 1/3 V); any other `include it does not find
# leaves the module wrong, or dead (§4.5 item 13)
STANDARD_HEADERS = ("disciplines.vams", "constants.vams", "discipline.h", "disciplines.h", "constants.h")
# PyMS's own directory, where its preprocessor looks last (utils/PyMS/vae/preprocess.py: its own
# directory, then the installed one; $PYMS_DIR/vae when PYMS_DIR names the PyMS that builds)
PYMS_VAE_DIRS = ("/usr/local/share/xyce/PyMS/vae", "/usr/local/src/xyce/utils/PyMS/vae")
# Xyce's messages that say nothing about the run: PyMS registering a module, its preprocessor
# skipping a standard header it does without (§4.5 item 13); they stay in the deck's log
_QUIET = re.compile(r"\.HDL: compiled and registered |^preprocess: warning: '(?:[^']*/)?(?:disciplines\.vams|"
                    r"constants\.vams|disciplines?\.h|constants\.h)' not found")
# the signals that, sent to Xyce by someone other than vamos, make status 3 (§2.7's table)
OUTSIDE_STOPS: Tuple[int, ...] = tuple(getattr(signal, n) for n in ("SIGKILL", "SIGTERM", "SIGINT", "SIGHUP")
                                       if hasattr(signal, n))

_PERCENT = re.compile(r"Percent complete:\s*([0-9.eE+-]+)\s*%")
_VA_INCLUDE = re.compile(r'^[ \t]*`include[ \t]+"([^"\n]+)"', re.M)
# Xyce's diagnostics (the narration of §8.7): a header line, then its indented continuation lines
_DIAG_HEAD = re.compile(r"^(?:(?:Netlist|Simulation|User|Device|Analysis)\s+)?(?:warning|error|fatal)\b"
                        r"|^Simulation aborted|^\*\*\* Xyce Abort|^Time step too small"
                        r"|^function \S+::\S+:|^preprocess: |^Failed DC sweep steps"
                        # PyMS compiling a module: a failed build it retries without the zero-valued
                        # parameters; when the retry fails too (an undeclared `$abstime', measured
                        # 2026-10-17) the module has no evaluator at all, which pyms_failures reports
                        r"|^build_vae_so: |^BuildError: |^\S+\.(?:cpp|hpp|h):\d+:\d+: error: ", re.I)
_DIAG_ERROR = re.compile(r"error|abort|fatal|too small|maximum number of failures|failed dc sweep|exiting|singular",
                         re.I)
# where Xyce's problem messages name the parameter (§7.2 [E43], E104, E108): its last ':' part is
# compared with an alter's or a sweep's parameter
_PROBLEM_PARAM = (re.compile(r"No model parameter (\S+) found for model"),
                  re.compile(r"Unrecognized parameter (\S+) for device"),
                  re.compile(r"Function or variable (\S+) is not defined"),
                  re.compile(r"Unable to find parameter (\S+)"))
# a PyMS build in Xyce's output (pyms_failures): a Python traceback whose frames run through
# build_vae_so.py, the evaluator library it names (vae_<module>_<hash>.so), the compiler's error
_TRACEBACK = "Traceback (most recent call last):"
_CHAINED = ("During handling of the above exception, another exception occurred:",
            "The above exception was the direct cause of the following exception:")
_PYMS_FRAME = re.compile(r'^\s+File "[^"]*build_vae_so\.py", line \d+')
_PYMS_LIB = re.compile(r"\bvae_(\w+)_[0-9a-f]+\.so\b")
_ERROR_TEXT = re.compile(r"\berror: (.+)$")
_PYMS_SKIPPED = re.compile(r"^preprocess: warning: '([^']+)' not found \(skipped\)", re.M)
_VA_COND = re.compile(r"^[ \t]*`(ifdef|ifndef|endif)\b")


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
            try:
                apply_alter(work, act, plan.dependents)
            except NoteError as exc:                    # R2 (phase 2c5): a target the replay cannot set on its path
                errors.extend(exc.notes)
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
        inst = taken_instance(nl, name, act.origin)          # R2 (phase 2c5): the run's path only
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
    for part in T.split_path(nl, path):                       # a name may hold a '.' (the guide trial of phase 2e)
        inst = next((it for it in ir.flat_items(body) if isinstance(it, ir.Instance) and it.name == part), None)
        if inst is None:
            raise KeyError("no instance %s (%s)" % (path, part))
        if inst.kind == "x" and inst.master in subs:
            body = subs[inst.master].body
    assert inst is not None
    return inst


def taken_instance(nl: ir.Netlist, path: str, origin: str = "") -> ir.Instance:
    """The Instance an IR path names on the instance path the run takes (phase 2c5, R2): every Cond on the way and in
    the final body resolved with the path's values in nl's state, as the Xyce deck's elaboration resolves them
    (tables.path_envs, non-strict, nl's values: the state replayed).  An if and its else may both hold R1, or hold an
    X1 of two subckts: the replay sets the taken one only, so neither an untaken duplicate nor the definition an untaken
    X line instantiates (which another instance may use) takes the alter.  NoteError when the instance lies in a
    definition that more than one instance path reaches, which the alter would change for every path (plan.build
    refuses such a target on Xyce, §5.3), or when the paths do not resolve; KeyError when the path names none."""
    parts = T.split_path(nl, path)                            # a name may hold a '.' (the guide trial of phase 2e)
    parent, leaf = ".".join(parts[:-1]), parts[-1]
    try:
        pes = list(T.path_envs(nl, strict=False, dialect=nl.dialect))
    except (X.EvalError, T.TableError) as exc:
        raise NoteError([error(origin, "the alter of %s: the instance paths do not resolve for the replay: %s"
                               % (path, exc))])
    pe = next((p for p in pes if p.path == parent), None)
    inst = next((it for it in pe.items if it.name == leaf), None) if pe is not None else None
    if pe is None or inst is None:
        raise KeyError("no instance %s on the instance paths" % path)
    if pe.subckt is not None:
        users = [p.path for p in pes if p.subckt is pe.subckt]
        if len(users) > 1:
            raise NoteError([error(origin, "the replay of an alter: on Xyce %s lies in subckt %s, which %d instance paths "
                                           "reach (%s): changing the shared definition would change every path; not "
                                           "supported in v1 (§5.3)" % (path, pe.subckt.name, len(users), ", ".join(users)))])
    return inst


def find_instances(nl: ir.Netlist, path: str) -> List[ir.Instance]:
    """Every Instance an IR path names through the subckt definitions: in each body every instance of that name,
    Cond branches included (phase 2c4); KeyError when there is none.  Phase 2c5: a lookup only; the replay sets the
    run's one (taken_instance), every match having reached the untaken branches' definitions too (R2)."""
    subs = _subckts(nl)
    parts = T.split_path(nl, path)                            # a name may hold a '.' (the guide trial of phase 2e)
    bodies: List[Sequence[object]] = [nl.body]
    for part in parts[:-1]:
        nxt: List[Sequence[object]] = []
        for body in bodies:
            for it in ir.flat_items(body):
                if isinstance(it, ir.Instance) and it.name == part and it.kind == "x" and it.master in subs:
                    b = subs[it.master].body
                    if not any(b is o for o in nxt):
                        nxt.append(b)
        bodies = nxt
    out: List[ir.Instance] = []
    for body in bodies:
        out += [it for it in ir.flat_items(body) if isinstance(it, ir.Instance) and it.name == parts[-1]
                and not any(it is o for o in out)]
    if not out:
        raise KeyError("no instance %s" % path)
    return out


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
    """path and the files it `includes, transitively, that Xyce's PyMS reads relative to a file:
    beside the including file, else beside path itself, the top file (PyMS's first two places
    [E75]); an absolute name, or one only PyMS's own directories hold, is read where it is and needs
    no copy."""
    top = os.path.dirname(os.path.abspath(path))
    seen: List[str] = []
    todo = [os.path.abspath(path)]
    while todo:
        p = todo.pop(0)
        if p in seen:
            continue
        seen.append(p)
        text = _read(p)
        if text is None:
            continue
        for name in _VA_INCLUDE.findall(text):
            if os.path.isabs(name):
                continue
            for d in (os.path.dirname(p), top):
                cand = os.path.normpath(os.path.join(d, name))
                if os.path.isfile(cand):
                    if cand not in seen:
                        todo.append(cand)
                    break
    return seen


def _read(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def standard_header(name: str) -> bool:
    """Whether an `include names one of STANDARD_HEADERS (by its base name)."""
    return posixpath.basename(name.replace("\\", "/")) in STANDARD_HEADERS


def pyms_search_dirs(env: Optional[Dict[str, str]] = None) -> List[str]:
    """Where PyMS's preprocessor looks for an `include after the including file's directory and the
    top file's (utils/PyMS/vae/preprocess.py): each PYMS_INCLUDE_PATH entry and its subdirectories,
    four levels down, then its own directory ($PYMS_DIR/vae, PYMS_VAE_DIRS)."""
    env = os.environ if env is None else env           # type: ignore[assignment]
    out: List[str] = []
    for entry in env.get("PYMS_INCLUDE_PATH", "").split(os.pathsep):
        entry = entry.strip()
        if not entry:
            continue
        out.append(entry)
        if os.path.isdir(entry):
            for root, dirs, _files in os.walk(entry):
                out.extend(os.path.join(root, d) for d in dirs)
                if root[len(entry):].count(os.sep) >= 4:
                    dirs.clear()
    if env.get("PYMS_DIR"):
        out.append(os.path.join(env["PYMS_DIR"], "vae"))
    out.extend(PYMS_VAE_DIRS)
    return out


def _includes(text: str) -> List[Tuple[str, bool]]:
    """The `include names of a Verilog-A text, in order, each with whether it sits in an `ifdef or
    `ifndef block (a line comment hides an include, as from PyMS's preprocessor)."""
    out: List[Tuple[str, bool]] = []
    depth = 0
    for line in text.splitlines():
        m = _VA_COND.match(line)
        if m:
            depth = max(depth - 1, 0) if m.group(1) == "endif" else depth + 1
            continue
        m = _VA_INCLUDE.match(line)
        if m:
            out.append((m.group(1), depth > 0))
    return out


def missing_includes(hdl: Sequence[str], env: Optional[Dict[str, str]] = None) -> List[Tuple[str, str, str]]:
    """(source, the including file, the name), in order, for each `include of the Verilog-A sources and
    of the files they include that PyMS would not find (beside the including file, beside the source,
    then pyms_search_dirs) and that is not a standard header.  PyMS skips such an include and builds
    the module without it: rc 0 and a wrong device, on a cold cache with one warning that the -norun
    check's output alone holds, on a warm one with none (measured 2026-10-17).  Only an include outside
    `ifdef blocks (and not reached through one) is judged: vamos does not evaluate the conditions, and
    PyMS's own warning in Xyce's output covers the others (_Runner.problems).  A source that does not
    exist is left to Xyce."""
    extra = pyms_search_dirs(env)
    out: List[Tuple[str, str, str]] = []
    done: Set[str] = set()
    for h in hdl:
        src = os.path.abspath(h)
        if src in done or not os.path.isfile(src):
            continue
        done.add(src)
        top = os.path.dirname(src)
        seen: Dict[str, bool] = {}                       # a file read -> whether only through an `ifdef
        todo: List[Tuple[str, bool]] = [(src, False)]
        while todo:
            p, cond = todo.pop(0)
            if p in seen and (cond or not seen[p]):
                continue
            seen[p] = cond
            text = _read(p)
            if text is None:
                continue
            for name, inner in _includes(text):
                cands = [os.path.join(os.path.dirname(p), name), os.path.join(top, name)] + \
                    [os.path.join(d, name) for d in extra]
                found = next((os.path.normpath(c) for c in cands if os.path.isfile(c)), None)
                if found is not None:
                    todo.append((found, cond or inner))
                elif not (cond or inner) and not standard_header(name):
                    out.append((src, p, name))
    return out


# the Verilog-AMS compiler directives and predefined macros (a `name that is not a macro use), and the macros PyMS's
# preprocessor defines itself (utils/PyMS/vae/preprocess.py, _Preprocessor.defines)
VA_DIRECTIVES = frozenset(("include", "define", "undef", "ifdef", "ifndef", "elsif", "else", "endif", "timescale",
                           "resetall", "celldefine", "endcelldefine", "default_nettype", "default_discipline",
                           "default_transition", "line", "begin_keywords", "end_keywords", "pragma",
                           "unconnected_drive", "nounconnected_drive", "__FILE__", "__LINE__"))
PYMS_PREDEFINED = ("insideADMS", "__XYCE_COMPACT_MODELING__")
# the macros of the standard headers (constants.vams, Spectre's constants.h), which PyMS knows whether it reads or
# skips the header (STANDARD_HEADERS: a module that uses `M_PI with constants.h included runs right, measured
# 2026-10-17); PyMS's own constants.vams, where it is found, adds its `defines
STANDARD_MACROS = ("CONSTANTS_VAMS", "DISCIPLINES_VAMS", "M_E", "M_LOG2E", "M_LOG10E", "M_LN2", "M_LN10", "M_PI",
                   "M_TWO_PI", "M_PI_2", "M_PI_4", "M_1_PI", "M_2_PI", "M_2_SQRTPI", "M_SQRT2", "M_SQRT1_2", "P_Q",
                   "P_C", "P_K", "P_H", "P_EPS0", "P_U0", "P_CELSIUS0")
_VA_DEFINE = re.compile(r"^[ \t]*`define[ \t]+([A-Za-z_]\w*)", re.M)
_VA_MACRO = re.compile(r"`([A-Za-z_]\w*)")
_VA_SKIP = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"', re.S)


def _va_code(text: str) -> str:
    """A Verilog-A text with its comments and strings blanked, its lines kept (a `name there is no macro use)."""
    return _VA_SKIP.sub(lambda m: "".join(c if c == "\n" else " " for c in m.group(0)), text)


_PP_DIRECTIVE = re.compile(r"`(define|undef|include|ifdef|ifndef|elsif|else|endif)\b(.*)$", re.S)
# a `define as Verilog-A reads it (openvaf-r: a macro takes arguments when a '(' follows its name at once), and as
# PyMS reads it (preprocess.py, _DEFINE_RE: a '(' after blanks too, so `define RV (2000.0) is a macro with the
# argument 2000.0 and no body, which leaves a use `RV as it is; measured on Xyce: V1:p = -1e-12, exit 0)
_PP_DEFINE = re.compile(r"\s*([A-Za-z_]\w*)(\([^)]*\))?[ \t]*(.*)$", re.S)
_PP_DEFINE_PYMS = re.compile(r"\s*(\w+)\s*(\([^)]*\))?\s*(.*)$", re.S)
_PP_NAME = re.compile(r"\s*([A-Za-z_]\w*)")
_PP_INCLUDE = re.compile(r'\s*"([^"\n]+)"')
_PP_MAX_DEPTH = 32                       # include nesting (PyMS's max_include_depth) and macro expansion depth
# PyMS's tokens (utils/PyMS/vae/parser.py, _TOKEN_PATTERNS), comments and strings blanked beforehand (_va_code)
_PP_TOKEN = re.compile(r"(?P<NUMBER>[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?(?:[fpnumkMGT])?)"
                       r"|(?P<IDENT>[A-Za-z_\$`][A-Za-z0-9_\$]*)|(?P<OP>\(\*|\*\)|<\+|[+\-*/%(){},;:=<>!&|~\[\]@^?.])"
                       r"|(?P<WS>\s+)|(?P<OTHER>.)", re.S)
# the scale factors Verilog-A takes after a number, and the ones PyMS's parse_number reads in a default that is the
# number alone (_SCALE: not `a')
VA_SCALES = {"T": 1e12, "G": 1e9, "M": 1e6, "K": 1e3, "k": 1e3, "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12,
             "f": 1e-15, "a": 1e-18}
PYMS_SCALES = frozenset("fpnumkKMGT")


def _define_parts(rx, rest: str) -> Optional[Tuple[str, Optional[Tuple[str, ...]], str]]:
    m = rx.match(rest)
    if m is None:
        return None
    params = tuple(p.strip() for p in m.group(2)[1:-1].split(",") if p.strip()) if m.group(2) is not None else None
    return m.group(1), params, m.group(3).strip()


@dataclass(eq=False)
class _Define:
    """A `define where it stands (file, line): its parameters (None for a macro without) and body as Verilog-A reads
    it, and as PyMS reads it (pparams, pbody: _PP_DEFINE_PYMS)."""
    name: str
    params: Optional[Tuple[str, ...]]
    body: Optional[str]                                  # None: a macro PyMS knows (PYMS_PREDEFINED, STANDARD_MACROS)
    pparams: Optional[Tuple[str, ...]] = None
    pbody: Optional[str] = None
    file: str = ""
    line: int = 0

    def text(self, pyms: bool = False) -> str:
        params, body = (self.pparams, self.pbody) if pyms else (self.params, self.body)
        return "`define %s%s %s" % (self.name, "(%s)" % ", ".join(params) if params is not None else "", body or "")


@dataclass(eq=False)
class _Frame:
    """One file as PyMS's preprocessor reads it (an `include is a frame inside its includer's): its parent, the
    definitions in force at its end, where PyMS expands the macros it holds (preprocess.py, _process)."""
    path: str
    parent: Optional["_Frame"]
    end: Dict[str, _Define] = field(default_factory=dict)


@dataclass
class _Use:
    """A macro use in a line PyMS emits: the frame it stands in, whether a '(' follows it on its line, and its
    expansion as the standard reads it, each macro the expansion reaches with the definition in force at the use."""
    name: str
    file: str
    line: int
    frame: _Frame
    chain: List[Tuple[str, Optional[_Define]]]
    paren: bool = False
    at: int = -1                                         # the index of its line in PymsScan.lines (FIX2E6)
    nth: int = 0                                         # its ordinal among the uses of its name on that line


_DROPPED = "dropped"       # PymsScan.annotation: PyMS's parser drops the annotation, and the module runs right
# the keywords PyMS's parser dispatches on in a module body (parser.py, parse_module), and the tokens a statement ends at
_PYMS_KEYWORDS = frozenset(("inout", "input", "output", "electrical", "parameter", "real", "integer", "analog", "branch",
                            "genvar", "module", "endmodule", "paramset", "endparamset"))
_STATEMENT_ENDS = frozenset((";", "begin", "end", "endmodule", "endcase", "endfunction", "endparamset", "endnature",
                             "enddiscipline"))
_Tok = Tuple[str, str, int, int]                         # (kind, value, offset, line index)


def _group_end(toks: Sequence[_Tok], i: int) -> int:
    """The index after the ')' that closes the group whose '(' follows the token i (an annotation `name(...), as PyMS's
    _consume_inline_attrs reads it: only '(' and ')' count); -1 when it does not close."""
    j = i + 1
    if j >= len(toks) or toks[j][1] != "(":
        return -1
    depth = 0
    while j < len(toks):
        if toks[j][1] == "(":
            depth += 1
        elif toks[j][1] == ")":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return -1


def _after_group(toks: Sequence[_Tok], j: int, open_: str, close: str) -> int:
    """The index after the token that closes the group opened at j (the same opener nested counted); len(toks) when it
    does not close."""
    depth = 0
    while j < len(toks):
        if toks[j][1] == open_:
            depth += 1
        elif toks[j][1] == close:
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return len(toks)


def _tok_text(toks: Sequence[_Tok], a: int, b: int) -> str:
    return " ".join(t[1] for t in toks[max(a, 0):b])


class PymsScan:
    """A model of PyMS's preprocessor (utils/PyMS/vae/preprocess.py) for one Verilog-A source, for vamos's checks before
    Xyce runs (§4.5 item 13): `ifdef/`ifndef/`elsif/`else/`endif evaluated in order with the macros defined at that
    point (PyMS defines PYMS_PREDEFINED itself, and STANDARD_MACROS are known to it), `define and `undef in the
    branches taken, `include read in place (beside the including file, beside the source, then pyms_search_dirs),
    and the expansion PyMS makes: the macros of each file are expanded at its end, with the definitions in force
    there, and what is left at its includer's end (FIX2E5, S-2E4-N2: a `define in an `ifdef branch not taken, and a
    `define then `undef, counted as defined, and the module ran wrong on Xyce, V1:p = -1e-12 with exit 0; measured on
    Xyce: a use followed by an `undef also gave -1e-12, and a use followed by a new `define took the last value,
    where VACASK reads the definition in force at the use).  Comments and strings are blanked first, as for the uses
    (_va_code).  `complete` is False when an `include of a branch taken is not found and is not a standard header
    (missing_includes and PyMS's own warning judge such a source)."""

    def __init__(self, src: str, extra: Sequence[str], known: Set[str]) -> None:
        self.src = os.path.abspath(src)
        self.top = os.path.dirname(self.src)
        self.extra = list(extra)
        self.defs: Dict[str, _Define] = {n: _Define(n, None, None) for n in sorted(known)}
        self.undefs: Dict[str, Tuple[str, int]] = {}     # the last `undef of each name: (file, line)
        self.dead: Dict[str, Tuple[str, int]] = {}       # a `define in a branch not taken: (file, line)
        self.uses: List[_Use] = []
        self.lines: List[Tuple[str, int, str, _Frame]] = []    # the lines PyMS emits, as (file, line, code, frame)
        self.complete = True
        self.root = self.read(self.src, None, [])

    # -- reading
    def read(self, path: str, parent: Optional[_Frame], stack: List[str]) -> _Frame:
        frame = _Frame(path, parent)
        text = _read(path)
        if text is None or path in stack or len(stack) >= _PP_MAX_DEPTH:
            frame.end = dict(self.defs)
            return frame
        lines = _va_code(text).split("\n")                 # comments and strings blanked: what a directive is, the uses
        raws = text.split("\n")                            # as written: a directive's arguments (an include's name)
        cond: List[Tuple[bool, bool]] = []                  # (this branch taken, a branch of the chain taken)
        k = 0
        while k < len(lines):
            line, raw, lineno = lines[k], raws[k] if k < len(raws) else "", k + 1
            k += 1
            while line.rstrip().endswith("\\") and k < len(lines):     # a continued line (PyMS unfolds them)
                line = line.rstrip()[:-1] + " " + lines[k]
                raw = raw.rstrip()[:-1] + " " + (raws[k] if k < len(raws) else "")
                k += 1
            m = _PP_DIRECTIVE.match(line.strip())
            taking = all(t for t, _ in cond)
            if m is not None:
                what = m.group(1)
                rm = _PP_DIRECTIVE.match(_strip_line_comment(raw).strip())
                rest = rm.group(2) if rm is not None and rm.group(1) == what else m.group(2)
                nm = _PP_NAME.match(rest)
                name = nm.group(1) if nm else ""
                if what in ("ifdef", "ifndef"):
                    hit = (name in self.defs) == (what == "ifdef")
                    cond.append((taking and hit, taking and hit))
                elif what == "elsif" and cond:
                    _t, taken = cond[-1]
                    outer = all(t for t, _ in cond[:-1])
                    now = outer and not taken and name in self.defs
                    cond[-1] = (now, taken or now)
                elif what == "else" and cond:
                    _t, taken = cond[-1]
                    outer = all(t for t, _ in cond[:-1])
                    now = outer and not taken
                    cond[-1] = (now, taken or now)
                elif what == "endif" and cond:
                    cond.pop()
                elif what == "define" and not taking:
                    if name:
                        self.dead.setdefault(name, (path, lineno))
                elif what == "define":
                    std = _define_parts(_PP_DEFINE, rest)
                    pym = _define_parts(_PP_DEFINE_PYMS, rest)
                    if std is not None and pym is not None:
                        self.defs[std[0]] = _Define(std[0], std[1], std[2], pym[1], pym[2], path, lineno)
                elif what == "undef" and taking:
                    if name:
                        self.defs.pop(name, None)
                        self.undefs[name] = (path, lineno)
                elif what == "include" and taking:
                    im = _PP_INCLUDE.match(rest)
                    if im is not None:
                        self.include(im.group(1), path, frame, stack + [path])
                continue
            if not taking:
                continue
            self.lines.append((path, lineno, line, frame))
            seen: Dict[str, int] = {}
            for um in _VA_MACRO.finditer(line):
                if um.group(1) not in VA_DIRECTIVES:
                    paren = line[um.end():].lstrip(" \t").startswith("(")
                    nth = seen.get(um.group(1), 0)
                    seen[um.group(1)] = nth + 1
                    self.uses.append(_Use(um.group(1), path, lineno, frame, self.chain(um.group(1)), paren,
                                          len(self.lines) - 1, nth))
        frame.end = dict(self.defs)
        return frame

    def include(self, name: str, path: str, frame: _Frame, stack: List[str]) -> None:
        cands = [os.path.join(os.path.dirname(path), name), os.path.join(self.top, name)] + \
            [os.path.join(d, name) for d in self.extra]
        found = next((os.path.normpath(c) for c in cands if os.path.isfile(c)), None)
        if found is not None:
            self.read(found, frame, stack)
        elif not standard_header(name):
            self.complete = False

    def chain(self, name: str, depth: int = 0) -> List[Tuple[str, Optional[_Define]]]:
        """The macros a use of name reaches as the standard expands it here: name, then those in its body."""
        d = self.defs.get(name)
        out: List[Tuple[str, Optional[_Define]]] = [(name, d)]
        if d is None or not d.body or depth >= _PP_MAX_DEPTH:
            return out
        for inner in _VA_MACRO.findall(d.body):
            if inner not in VA_DIRECTIVES and not (d.params and inner in d.params):
                out.extend(self.chain(inner, depth + 1))
        return out

    # -- PyMS's expansion
    @staticmethod
    def pyms_def(name: str, frame: Optional[_Frame]) -> Optional[_Define]:
        """The definition PyMS expands name with in a line of frame: the one in force at the frame's end, else at its
        includer's end, and so on."""
        while frame is not None:
            d = frame.end.get(name)
            if d is not None:
                return d
            frame = frame.parent
        return None

    def problems(self) -> List[Tuple[str, int, str, str]]:
        """(file, line, macro, why) for each use that PyMS would compile wrong, the first wrong macro of its
        expansion: why is the text after the macro's name in the error."""
        out: List[Tuple[str, int, str, str]] = []
        for use in self.uses:
            for k, (name, std) in enumerate(use.chain):
                pyms = self.pyms_def(name, use.frame)
                if k == 0 and std is None and pyms is None:
                    # FIX2E6 (S-2E5's regression): an undefined `name(...) is PyMS's annotation form (BSIM-CMG's
                    # `attr(...) used without its `define), which its parser drops in a few positions, where the module
                    # runs right, and reads as text elsewhere; VACASK's openvaf-r refuses it as any undefined macro
                    site = self.annotation(use)
                    if site == _DROPPED:
                        break
                    if site is not None:
                        out.append((use.file, use.line, name, self.annotation_why(name, site)))
                        break
                # PyMS leaves a use of a macro it reads with arguments as it is unless a '(' follows (the use's line,
                # or, inside a body, where the standard reads the macro without arguments)
                leaves = pyms is not None and pyms.pparams is not None and (
                    not use.paren if k == 0 else (std is None or std.params is None))
                if std is not None and pyms is not None and not leaves and \
                        (std.params, std.body) == (pyms.pparams, pyms.pbody):
                    continue
                where = "" if k == 0 else "(in the definition of `%s) " % use.name
                out.append((use.file, use.line, name, where + self.why(name, std, pyms, use, leaves)))
                break
        return out

    def why(self, name: str, std: Optional[_Define], pyms: Optional[_Define], use: _Use, leaves: bool = False) -> str:
        at = lambda fl: "%s:%d" % (os.path.basename(fl[0]), fl[1])        # noqa: E731
        undefined = ("Xyce's PyMS compiles a use of an undefined macro without an error, so the module would run "
                     "wrong (a parameter default `RV gave V1:p = -1e-12 with exit status 0); not supported on Xyce in "
                     "v1: `define %s before its use (VACASK's openvaf-r refuses it too: macro '`%s' has not been "
                     "declared)" % (name, name))
        if leaves and std is not None:
            args = tuple(pyms.pparams or ())
            return ("is defined at %s as `%s %s', which Xyce's PyMS reads as a macro with the argument%s %s and no "
                    "body (it takes a '(' after blanks for an argument list), so it leaves this use as it is and the "
                    "module would not build or would run wrong; not supported on Xyce in v1: write the body without "
                    "the parenthesis, or run it on VACASK"
                    % (at((std.file, std.line)), name, std.body or "", "" if len(args) == 1 else "s",
                       ", ".join(args) or "()"))
        if std is None and pyms is not None:
            return ("is used before its `define (%s): VACASK's openvaf-r refuses such a use (macro '`%s' has not been "
                    "declared), while Xyce's PyMS expands it with that later definition; not supported in v1: `define "
                    "%s before its use" % (at((pyms.file, pyms.line)), name, name))
        if std is None:
            if name in self.undefs:
                return "is not defined here: `undef %s at %s comes first, and %s" % (name, at(self.undefs[name]),
                                                                                     undefined)
            if name in self.dead:
                return ("is not defined here: its `define (%s) is in an `ifdef or `ifndef branch that is not taken, "
                        "and %s" % (at(self.dead[name]), undefined))
            return ("is not defined: no `define %s in the Verilog-A source or the files it includes, and Xyce's PyMS "
                    "compiles a use of an undefined macro without an error, so the module would run wrong (a parameter "
                    "default `RV gave V1:p = -1e-12 with exit status 0); not supported on Xyce in v1: `define %s in "
                    "the source (VACASK's openvaf-r refuses it too: macro '`%s' has not been declared)"
                    % (name, name, name))
        if pyms is None:
            und = self.undefs.get(name, ("", 0))
            return ("is undefined by the `undef %s at %s after this use: Xyce's PyMS expands the macros of a file at "
                    "its end, where %s is no longer defined, so it compiles the use without an error and the module "
                    "would run wrong (VACASK reads the `define at %s); not supported on Xyce in v1: drop the `undef, "
                    "or run it on VACASK" % (name, at(und), name, at((std.file, std.line))))
        return ("is defined again at %s after this use: Xyce's PyMS expands every use with the last definition (%s, "
                "where this use reads %s), so the module would run wrong; not supported on Xyce in v1: give %s one "
                "definition, or run it on VACASK" % (at((pyms.file, pyms.line)), pyms.text(True), std.text(), name))

    # -- PyMS's annotation form: an undefined `name(...) (FIX2E6, S-2E5's regression of round 4's rule)
    def expanded(self) -> Tuple[str, List[int], List[Tuple[str, int]]]:
        """The lines PyMS emits, each expanded as PyMS expands it (expand) with its comments and strings blanked, joined
        by newlines: (the text, each line's start offset, each line's (file, line))."""
        got = getattr(self, "_expanded", None)
        if got is None:
            pieces: List[str] = []
            starts: List[int] = []
            where: List[Tuple[str, int]] = []
            pos = 0
            for path, lineno, line, frame in self.lines:
                text = _va_code(self.expand(line, frame))      # a body's strings and comments blanked too
                starts.append(pos)
                where.append((path, lineno))
                pieces.append(text)
                pos += len(text) + 1
            got = self._expanded = ("\n".join(pieces), starts, where)
        return got

    def tokens(self) -> List[_Tok]:
        """PyMS's tokens of the expanded text (_PP_TOKEN, white space left out): (kind, value, offset, line index)."""
        got = getattr(self, "_tokens", None)
        if got is None:
            whole, starts, _where = self.expanded()
            got = self._tokens = [(m.lastgroup or "", m.group(0), m.start(), max(0, _bisect(starts, m.start())))
                                  for m in _PP_TOKEN.finditer(whole) if m.lastgroup != "WS"]
        return got

    def _use_token(self, use: _Use, toks: Sequence[_Tok]) -> int:
        """The index of the token of a use: the nth `name on its line (PyMS's expansion keeps a line's tokens on it)."""
        want = "`" + use.name
        n = 0
        for j, t in enumerate(toks):
            if t[3] == use.at and t[1] == want:
                if n == use.nth:
                    return j
                n += 1
        return -1

    def annotation(self, use: _Use) -> Optional[str]:
        """What PyMS's parser does with a use of an undefined macro that a '(' follows, the annotation form of BSIM-CMG's
        `attr(info="...") (parser.py; measured 2026-10-24, FIX2E6, on the guide's module a1 through 5d2291f's vamos):
        _DROPPED where PyMS drops the annotation and the module runs right (after a module header's port list,
        _skip_inline_attrs; at the end of a parameter statement, after its default and ranges, _consume_inline_attrs; a
        statement of its own, ended by a ';' or, outside the analog block, by its line, _skip_directive; in a port
        direction or a variable declaration, whose names PyMS reads up to the ';', harmless), else the text of what
        PyMS does with it there, for the error (inside an analog block before a statement it swallows the statement:
        a device that conducted nothing with exit status 0; in an expression the C++ compile fails; before a
        parameter's name or its ranges the parser fails; after `electrical' it becomes an internal node); None when
        no '(' follows the use (a plain undefined macro)."""
        toks = self.tokens()
        i = self._use_token(use, toks)
        if i < 0 or i + 1 >= len(toks) or toks[i + 1][1] != "(":
            return None
        name = use.name
        end = _group_end(toks, i)
        if end < 0:
            return "finds no ')' that closes the annotation and reads the rest of the module as its text"
        s = i
        while s > 0 and toks[s - 1][1] not in _STATEMENT_ENDS:
            s -= 1
        if toks[s][1] == "(*":                            # a (* *) attribute group before the statement's keyword
            s = _after_group(toks, s, "(*", "*)")
            if s > i:
                return "reads it inside a (* *) attribute group"
        kw = toks[s][1]
        if s == i:                                        # a statement of its own
            nxt = toks[end] if end < len(toks) else None
            if nxt is None or nxt[1] == ";":
                return _DROPPED
            if self._in_analog(toks, s):
                return ("reads it and the statement after it (%s ...) as one statement and drops both (measured: a "
                        "contribution after `%s(...) in an analog block gave a device that conducted nothing, with exit "
                        "status 0)" % (_tok_text(toks, end, end + 6), name))
            if nxt[3] == toks[end - 1][3]:
                return ("reads its line as a directive and drops the rest of the line (%s)"
                        % _tok_text(toks, end, end + 6))
            later = [t for t in toks[i + 1:end] if t[3] != toks[i][3] and t[1] in _PYMS_KEYWORDS]
            if later:
                return ("reads its first line as a directive and `%s' on a later line of the annotation as the start "
                        "of a declaration" % later[0][1])
            return _DROPPED
        if kw == "module":
            return self._header_site(toks, s, i, end)
        if kw == "parameter":
            return self._parameter_site(toks, s, i, end)
        if kw in ("inout", "input", "output", "real", "integer"):
            return _DROPPED                                # its names read up to the ';': no port, variables unused
        if kw == "electrical":
            return ("reads `%s as an internal node of the module (every name of an electrical declaration that is not "
                    "a port), and the compile of its device shell fails" % name)
        if kw == "analog" or self._in_analog(toks, s):
            return "leaves it in the statement as an operand, and the C++ compile of the module's evaluator fails"
        return "reads it as part of the %s statement" % kw

    @staticmethod
    def _in_analog(toks: Sequence[_Tok], s: int) -> bool:
        """Whether the statement starting at s lies inside an `analog begin ... end' block."""
        a = -1
        for j in range(s - 1, -1, -1):
            if toks[j][1] == "analog" and not (j + 1 < len(toks) and toks[j + 1][1] == "function"):
                a = j
                break
            if toks[j][1] in ("endmodule", "module"):
                break
        if a < 0 or a + 1 >= len(toks) or toks[a + 1][1] != "begin":
            return False
        depth = 0
        for j in range(a + 1, s):
            if toks[j][1] == "begin":
                depth += 1
            elif toks[j][1] == "end":
                depth -= 1
        return depth > 0

    @staticmethod
    def _annotation_tail(toks: Sequence[_Tok], p: int) -> int:
        """The index after the `name(...) and (* *) groups that start at p (PyMS's _consume_inline_attrs)."""
        while p < len(toks):
            if toks[p][1] == "(*":
                p = _after_group(toks, p, "(*", "*)")
            elif toks[p][0] == "IDENT" and toks[p][1].startswith("`") and p + 1 < len(toks) and toks[p + 1][1] == "(":
                q = _group_end(toks, p)
                if q < 0:
                    return len(toks)
                p = q
            else:
                break
        return p

    def _header_site(self, toks: Sequence[_Tok], s: int, i: int, end: int) -> str:
        """`module name [(ports)] <annotations> ;' (parse_module: the annotations after the port list are skipped)."""
        p = s + 2
        if i < p:
            return "reads it as the module's name and what follows as its port list"
        if p < len(toks) and toks[p][1] == "(":
            p = _after_group(toks, p, "(", ")")
        if i < p:
            return "reads it inside the module's port list, as a port"
        p = self._annotation_tail(toks, p)
        if p < len(toks) and toks[p][1] == ";":
            return _DROPPED
        return ("cannot read the module header: after the annotation it expects the ';' and finds `%s' (a traceback: "
                "PyMS compilation failed)" % (toks[p][1] if p < len(toks) else "the end of the file"))

    def _parameter_site(self, toks: Sequence[_Tok], s: int, i: int, end: int) -> str:
        """A `parameter' statement as PyMS's _parse_param reads it: the type, the name, [lo:hi], `= default' up to a ';',
        `from', `exclude', `(*' or a `name( at depth 0 (_collect_param_value), the ranges the same way, then the
        annotations (_consume_inline_attrs), then the ';'."""
        p = s + 1
        if p >= len(toks) or toks[p][0] != "IDENT" or toks[p][1].startswith("`"):
            return "reads it where it expects the parameter's type, and cannot read the statement (a traceback)"
        p += 1
        if p >= len(toks) or toks[p][0] != "IDENT" or toks[p][1].startswith("`"):
            return ("reads it where it expects the parameter's name, and cannot read the statement (a traceback: PyMS "
                    "compilation failed)")
        pname = toks[p][1]
        p += 1
        if p < len(toks) and toks[p][1] == "[":
            while p < len(toks) and toks[p][1] != "]":
                p += 1
            p += 1
        if p < len(toks) and toks[p][1] == "=":
            p = self._collect(toks, p + 1)
            if i < p:
                return "reads it as part of the default of %s, which it then evaluates as 0" % pname
        while p < len(toks) and toks[p][1] in ("from", "exclude"):
            p = self._collect(toks, p + 1)
            if i < p:
                return "reads it as part of a range of %s" % pname
        p = self._annotation_tail(toks, p)
        if p < len(toks) and toks[p][1] == ";":
            return _DROPPED
        return ("cannot read the parameter statement: after the annotation it expects the ';' and finds `%s' (a "
                "traceback: PyMS compilation failed)" % (toks[p][1] if p < len(toks) else "the end of the file"))

    @staticmethod
    def _collect(toks: Sequence[_Tok], p: int) -> int:
        """PyMS's _collect_param_value: the index of the first ';', `from', `exclude', `(*' or `name( at depth 0."""
        depth = 0
        while p < len(toks):
            kind, v = toks[p][0], toks[p][1]
            if depth == 0:
                if v in (";", "from", "exclude", "(*"):
                    break
                if kind == "IDENT" and v.startswith("`") and p + 1 < len(toks) and toks[p + 1][1] == "(":
                    break
            if v in ("(", "["):
                depth += 1
            elif v in (")", "]"):
                depth -= 1
            p += 1
        return p

    # -- a current read in an expression (FIX2E6, TRIAL2EI-1)
    def probe_problems(self) -> List[Tuple[str, int, str, str]]:
        """(file, line, the probe as written, why) for each current probe I(...) that a module reads in an expression, that
        is one not followed by `<+' (a contribution's target).  PyMS builds a contribution that assigns a current from
        voltages, I(a, b) <+ f(V(...)), and reads no current: for a node pair's I(a, b) in an expression the C++ compile
        of its evaluator fails ('p' was not declared in this scope: a loud stop, after the compile), and a named branch's
        I(br) it takes as 0 (ginac_emitter: `I(branch) probe -> 0'; measured 2026-10-24: V(br) <+ r * I(br) gave out =
        1 where VACASK gives 0.5, with exit status 0)."""
        toks = self.tokens()
        whole, _starts, where = self.expanded()
        modules = [(mm.start(), mm.group(1)) for mm in re.finditer(r"(?<![\w$`])module\s+([A-Za-z_][\w$]*)", whole)]
        mstarts = [p for p, _n in modules]
        out: List[Tuple[str, int, str, str]] = []
        for j, t in enumerate(toks):
            if t[0] != "IDENT" or t[1] != "I" or j + 1 >= len(toks) or toks[j + 1][1] != "(":
                continue
            end = _group_end(toks, j)
            if end < 0 or (end < len(toks) and toks[end][1] == "<+"):
                continue
            args = [a.strip() for a in _tok_text(toks, j + 2, end - 1).split(",")]
            probe = "I(%s)" % ", ".join(args)
            km = _bisect(mstarts, t[2])
            mod = modules[km][1] if km >= 0 else "?"
            if len(args) == 1:
                what = ("it takes a named branch's current as 0, so the module would run wrong (measured: `V(br) <+ r * "
                        "I(br)' gave out = 1 where VACASK gives 0.5, with exit status 0)")
            else:
                what = ("the C++ compile of its evaluator fails on a node pair's current (`'p' was not declared in this "
                        "scope'), so Xyce would not run the module (PyMS could not build it)")
            f, line = where[t[3]]
            out.append((f, line, probe, "module %s reads the current %s in an expression: Xyce's PyMS builds a contribution "
                                        "that assigns a current from voltages (I(a, b) <+ f(V(...))) and reads no current: "
                                        "%s; not supported on Xyce in v1: write the contribution the other way round (I(p, n) "
                                        "<+ V(p, n) / r), or run it on VACASK" % (mod, probe, what)))
        return out

    @staticmethod
    def annotation_why(name: str, what: str) -> str:
        return ("is not defined, and Xyce's PyMS leaves an undefined macro as it stands: it drops `%s(...) as an "
                "annotation at the end of a module header or of a parameter statement (BSIM-CMG's `attr) and as a "
                "statement of its own, but here it %s, so the module would run wrong or would not build; not supported "
                "on Xyce in v1: `define %s, or move or drop the annotation (VACASK's openvaf-r refuses an undefined "
                "macro: macro '`%s' has not been declared)" % (name, what, name, name))

    def expand(self, text: str, frame: _Frame, depth: int = 0) -> str:
        """A line as PyMS expands it in frame (preprocess.py, _expand_once): each macro by pyms_def as PyMS reads it,
        one with arguments only where a '(' follows, its arguments substituted as whole words."""
        if "`" not in text or depth >= _PP_MAX_DEPTH:
            return text
        out: List[str] = []
        i = 0
        while i < len(text):
            m = _VA_MACRO.search(text, i)
            if m is None:
                out.append(text[i:])
                break
            out.append(text[i:m.start()])
            i = m.end()
            d = self.pyms_def(m.group(1), frame)
            if d is not None and d.pbody is None:
                out.append(m.group(1))                    # a constant PyMS knows (`M_PI): its name, a value
                continue
            if d is None:
                out.append(m.group(0))
                continue
            body = d.pbody
            if d.pparams is not None:
                j = i
                while j < len(text) and text[j] in " \t":
                    j += 1
                if j >= len(text) or text[j] != "(":
                    out.append(m.group(0))
                    continue
                args, end = _macro_args(text, j)
                if end < 0:
                    out.append(m.group(0))
                    continue
                i = end
                for p, a in zip(d.pparams, args + [""] * (len(d.pparams) - len(args))):
                    body = re.sub(r"\b%s\b" % re.escape(p), lambda _m, a=a: a, body)
            out.append(self.expand(body, frame, depth + 1))
        return "".join(out)

    def default_problems(self) -> List[Tuple[str, int, str, str]]:
        """(file, line, parameter, why) for each `parameter' statement whose default PyMS evaluates as 0 (its
        parse_number reads a scale factor only in a default that is the number alone, and xyce_device_gen's
        _resolve_default takes 0 for anything it cannot read; measured on Xyce: `1k * 1', `2 * 0.5k', `(1k)', `1K *
        1', `1000a * 1e18' and `parameter real r = 1000.0, g = 1.0' each gave a device that conducted nothing, exit 0,
        where `1k', `1K', `+1k', `1000 * 1', `1k from (0:inf)' and a default reading another parameter run right)."""
        whole, starts, where = self.expanded()
        modules = [(mm.start(), mm.group(1)) for mm in re.finditer(r"(?<![\w$`])module\s+([A-Za-z_][\w$]*)", whole)]
        mstarts = [p for p, _n in modules]
        # a module -> its parameters, (type, the default is a number): PyMS's eval namespace, and the constants PyMS knows
        # (`M_PI), numbers to it
        known: _ParamInfo = {n: ("real", True) for n, d in self.defs.items() if d.body is None}
        pinfo: Dict[int, _ParamInfo] = {}
        matches = list(re.finditer(r"(?<![\w$`])parameter\b", whole))
        for m in matches:
            decl = _param_decl(whole, m.end())
            if decl is not None:
                typ, name, default = decl
                pinfo.setdefault(_bisect(mstarts, m.start()), dict(known))[name] = (typ, not default or _numeric_default(default))
        out: List[Tuple[str, int, str, str]] = []
        for m in matches:
            km = _bisect(mstarts, m.start())
            got = _param_problem(whole, m.end(), pinfo.get(km, known))
            if got is None:
                continue
            name, why = got
            k = max(0, _bisect(starts, m.start()))
            mod = " of module %s" % modules[km][1] if km >= 0 else ""
            out.append((where[k][0], where[k][1], name, "parameter %s%s: %s" % (name, mod, why)))
        return out


def _strip_line_comment(line: str) -> str:
    """A line without its trailing // comment, a string's // kept (PyMS's _strip_line_comment)."""
    in_str = False
    i = 0
    while i < len(line):
        c = line[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "/" and line[i + 1:i + 2] == "/":
            return line[:i].rstrip()
        i += 1
    return line.rstrip()


def _bisect(starts: List[int], pos: int) -> int:
    lo, hi = 0, len(starts)
    while lo < hi:
        mid = (lo + hi) // 2
        if starts[mid] <= pos:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1


def _macro_args(text: str, lparen: int) -> Tuple[List[str], int]:
    """The arguments of a macro call whose '(' is text[lparen], split at its top-level commas (PyMS's
    _split_macro_args); (args, the index after its ')'), -1 when it does not close on the line."""
    depth = 0
    args: List[str] = []
    cur: List[str] = []
    for j in range(lparen, len(text)):
        c = text[j]
        if c in "([{":
            depth += 1
            if depth > 1:
                cur.append(c)
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                args.append("".join(cur).strip())
                return args, j + 1
            cur.append(c)
        elif c == "," and depth == 1:
            args.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
    return args, -1


_ParamInfo = Dict[str, Tuple[str, bool]]                  # a module's parameters: name -> (type, its default is a number)


def _param_decl(text: str, start: int) -> Optional[Tuple[str, str, List[Tuple[str, str, int]]]]:
    """The statement after a `parameter' keyword at text[start:], read with PyMS's tokens as its _parse_param reads it:
    (type, name, the default's tokens: a type, a name, an optional [lo:hi], `= default' up to `;', `from', `exclude', `(*'
    or a `name( at depth 0); None for a form PyMS refuses itself (no type or name)."""
    toks: List[Tuple[str, str, int]] = []
    for m in _PP_TOKEN.finditer(text, start):
        kind = m.lastgroup
        if kind == "WS":
            continue
        toks.append((kind, m.group(0), m.start()))
        if m.group(0) == ";" or len(toks) > 4000:
            break
    if len(toks) < 3 or toks[0][1] not in ("real", "integer", "string") or toks[1][0] != "IDENT":
        return None
    typ, name = toks[0][1], toks[1][1]
    i = 2
    if i < len(toks) and toks[i][1] == "[":
        while i < len(toks) and toks[i][1] != "]":
            i += 1
        i += 1
    default: List[Tuple[str, str, int]] = []
    if i >= len(toks) or toks[i][1] != "=":
        return typ, name, default
    i += 1
    depth = 0
    while i < len(toks):
        kind, value, at = toks[i]
        if depth == 0 and (value in (";", "from", "exclude", "(*")):
            break
        if depth == 0 and value.startswith("`") and i + 1 < len(toks) and toks[i + 1][1] == "(":
            break                                         # an `attr(...) annotation (PyMS's _collect_param_value)
        if value in ("(", "["):
            depth += 1
        elif value in (")", "]"):
            depth -= 1
        default.append(toks[i])
        i += 1
    return typ, name, default


def _numeric_default(default: List[Tuple[str, str, int]]) -> bool:
    """Whether PyMS reads a default as a number on Xyce's side (parser.parse_number over the tokens joined by a blank: a
    float, or one ending in a scale factor it knows; `1 K' included, `1 a' not)."""
    joined = " ".join(v for _k, v, _a in default)
    try:
        float(joined)
        return True
    except ValueError:
        pass
    if joined and joined[-1] in PYMS_SCALES:
        try:
            float(joined[:-1])
            return True
        except ValueError:
            pass
    return False


def _param_problem(text: str, start: int, params: Optional[_ParamInfo] = None) -> Optional[Tuple[str, str]]:
    """The statement after a `parameter' keyword at text[start:] (_param_decl): (name, why) when PyMS evaluates the
    default as 0 or otherwise than Verilog-A does, else None.  params: the module's parameters (_ParamInfo), the names a
    default may read (_eval_form)."""
    decl = _param_decl(text, start)
    if decl is None:
        return None
    typ, name, default = decl
    if typ == "string":
        return None                                       # a string: not in the module's arithmetic
    if any(v.startswith("`") for _k, v, _a in default):
        return None                                       # a macro PyMS leaves as written: macro_problems reports it
    if not default:
        return None
    shown = " ".join(text[default[0][2]:default[-1][2] + len(default[-1][1])].split())     # as written, blanks folded
    commas = [k for k, (kind, value, _at) in enumerate(default) if value == ","]
    depth = 0
    top_commas = []
    for k, (kind, value, _at) in enumerate(default):
        if value in ("(", "[", "{"):
            depth += 1
        elif value in (")", "]", "}"):
            depth -= 1
        elif value == "," and depth == 0:
            top_commas.append(k)
    if commas and top_commas:
        names = [name] + [default[k + 1][1] for k in top_commas
                          if k + 1 < len(default) and default[k + 1][0] == "IDENT"]
        return name, ("one statement declares %s: Xyce's PyMS reads only the first of them, and its default as 0, so "
                      "the module would run wrong (measured: parameter real r = 1000.0, g = 1.0 gave a device that "
                      "conducted nothing, with exit status 0); not supported on Xyce in v1: declare each parameter in "
                      "a statement of its own, or run it on VACASK" % " and ".join(names))
    scaled: List[Tuple[str, str]] = []                    # (the scaled number as written, its scale factor)
    for k, (kind, value, at) in enumerate(default):
        if kind != "NUMBER":
            continue
        if value[-1] in VA_SCALES and not value[-1].isdigit():
            scaled.append((value.lstrip("+-"), value[-1]))
        elif k + 1 < len(default) and default[k + 1][0] == "IDENT" and default[k + 1][2] == at + len(value) \
                and default[k + 1][1] in ("K", "a"):
            scaled.append((value.lstrip("+-") + default[k + 1][1], default[k + 1][1]))
    if not scaled:
        return _eval_form(name, default, shown, params or {})
    alone = len(default) == 1 or (len(default) == 2 and default[1][1] in ("K", "a") and default[0][0] == "NUMBER")
    lit, scale = scaled[0]
    if scale not in PYMS_SCALES:
        return name, ("its default %s holds %s, whose scale factor %s (%g) Xyce's PyMS does not read: it evaluates the "
                      "default as 0, so the module would run wrong; not supported on Xyce in v1: write the number with "
                      "an exponent instead, or run it on VACASK" % (shown, lit, scale, VA_SCALES[scale]))
    if alone:
        return None
    return name, ("its default %s holds the scaled number %s inside an expression, which Xyce's PyMS evaluates as 0 "
                  "(it reads a scale factor only in a default that is the number alone), so the module would run wrong "
                  "(a default 1k * 1 gave a device that conducted nothing, with exit status 0); not supported on Xyce "
                  "in v1: write the number with an exponent (1e3 for 1k), or give the scaled number alone, or run it "
                  "on VACASK" % (shown, lit))


_EVAL_RULE = ("it reads a default as a number, as the name of another parameter, or as arithmetic of numbers and parameter "
              "names (+, -, *, /, %, parentheses and a comparison, read as Python reads them)")


def _eval_form(name: str, default: List[Tuple[str, str, int]], shown: str, params: _ParamInfo) -> Optional[Tuple[str, str]]:
    """FIX2E6 (S-2E5's three silent values): a default without a scaled number that PyMS evaluates as 0, or otherwise
    than Verilog-A does.  On Xyce's side PyMS reads a default that is one number (parser.parse_number) or the name of
    another parameter whose default is a number (xyce_device_gen._resolve_default); any other default is 0 there.  Its
    evaluator (ginac_emitter) reads the module's defaults as Python's eval reads the tokens joined by blanks, with the
    other parameters as names and no builtins, then overlays the device's values, which the device shell writes under
    the names in capitals: so for a parameter whose name is written in capitals the device's 0 wins (measured
    2026-10-24: R = 2 * RR, R = (1000.0), G = GG with GG = 2 * 0.5e-3 gave a device that conducted nothing, exit 0,
    where r = 2 * rr, Gg = 2 * 0.5e-3 and g = gg run right), and for any other name the eval's value: arithmetic of
    numbers and parameter names (2 * rr, 1000 * 1, (1000.0), - 1000.0, 1000 % 1001, (1 > 0) * 1000.0 and 1000 | 0
    run right), while a function call (pow, sqrt, exp, abs, min, max, floor, ln, $simparam), the ternary ?:, a digit
    separator (1_000: the tokens `1' and `_000'), the power operator ** (two `*' tokens), !, &&, ||, a two-character
    comparison (>=, ==: two tokens), a based literal ('h3e8) and a name that is not a parameter fail there and leave
    0 (each measured: out = 0, exit 0); and Python's arithmetic differs from Verilog-A's for the division of two
    integers (1 / 1000 is 0 in Verilog-A, 0.001 in Python: g = 1 / 1000 gave out = 0.5 where VACASK gives 0) and for
    the sign of % with a negative operand.  (name, why) for such a default, else None."""
    values = [v for _k, v, _a in default]
    capitals = name == name.upper() and any(c.isalpha() for c in name)
    if _numeric_default(default):
        return None
    joined = " ".join(values)                             # PyMS's _collect_param_value joins the tokens with a blank

    def refuse(form: str) -> Tuple[str, str]:
        return name, ("its default %s holds %s, which Xyce's PyMS evaluates as 0 (%s), so the module would run wrong "
                      "(measured: a default pow(10, 3) gave a device that conducted nothing, with exit status 0); not "
                      "supported on Xyce in v1: write the default as a number, or as such arithmetic, or run it on VACASK"
                      % (shown, form, _EVAL_RULE))

    def refuse_capitals() -> Tuple[str, str]:
        return name, ("its default %s is not a number, and the parameter's name %s is written in capitals: Xyce's PyMS "
                      "registers such a default as 0 on Xyce's side (it reads a default there as a number, or as the name "
                      "of a parameter whose default is a number), and its evaluator takes the device's value, that 0, for a "
                      "parameter named in capitals (measured: `parameter real R = 2 * RR' and `R = (1000.0)' gave a device "
                      "that conducted nothing, with exit status 0, where `r = 2 * rr' runs right); not supported on Xyce in "
                      "v1: write the default as a number, or name the parameter with a lower-case letter, or run it on "
                      "VACASK" % (shown, name))

    if len(values) == 1 and values[0] in params:         # the name of another parameter
        if params[values[0]][1] or not capitals:
            return None
        return refuse_capitals()
    try:
        tree = ast.parse(joined, mode="eval")
    except (SyntaxError, ValueError):
        return refuse(_odd_form(default))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            return refuse("the function call %s(...)" % (getattr(fn, "id", None) or "it holds"))
        if isinstance(node, ast.Name):
            if node.id not in params:
                return refuse("the name %s, which is not a parameter of the module" % node.id)
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                return refuse("the value %r" % (node.value,))
        elif isinstance(node, ast.BinOp):
            if isinstance(node.op, (ast.BitOr, ast.BitAnd, ast.BitXor, ast.LShift, ast.RShift, ast.FloorDiv)):
                if not _int_only(node):
                    return refuse("the operator %s on a value that is not an integer literal" % _OP_TEXT[type(node.op)])
            elif isinstance(node.op, ast.Pow):
                return refuse("the power operator")
            elif not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod)):
                return refuse("the operator %s" % type(node.op).__name__)
        elif isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Invert):
                if not _int_only(node):
                    return refuse("the operator ~ on a value that is not an integer literal")
            elif not isinstance(node.op, (ast.UAdd, ast.USub)):
                return refuse("the operator %s" % type(node.op).__name__)
        elif not isinstance(node, (ast.Expression, ast.Compare, ast.Load) + tuple(
                t for t in (ast.Lt, ast.Gt, ast.LtE, ast.GtE, ast.Eq, ast.NotEq, ast.Add, ast.Sub, ast.Mult, ast.Div,
                            ast.Mod, ast.UAdd, ast.USub, ast.BitOr, ast.BitAnd, ast.BitXor, ast.LShift, ast.RShift,
                            ast.Invert, ast.FloorDiv))):
            return refuse("a %s" % type(node).__name__)
    for node in ast.walk(tree):                           # Python's arithmetic against Verilog-A's
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and _vint(node.left, params) and _vint(node.right, params):
            a, b = node.left, node.right
            exact = isinstance(a, ast.Constant) and isinstance(b, ast.Constant) and b.value != 0 and a.value % b.value == 0
            if not exact:
                return name, ("its default %s holds the integer division %s / %s, which Verilog-A truncates to an integer "
                              "and Xyce's PyMS evaluates as Python does, to a real (measured: `parameter real g = 1 / 1000' "
                              "gave out = 0.5 on Xyce where VACASK gives 0, with exit status 0); not supported on Xyce in "
                              "v1: divide reals (1.0 / 1000), or write the value as a number, or run it on VACASK"
                              % (shown, _src(a, joined), _src(b, joined)))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) and not all(
                isinstance(x, ast.Constant) and not isinstance(x.value, bool) and x.value >= 0 for x in (node.left, node.right)):
            return name, ("its default %s holds the operator %% with an operand that is not a non-negative literal: for a "
                          "negative operand Verilog-A gives the result the sign of the dividend and Python, which Xyce's "
                          "PyMS evaluates it with, the sign of the divisor; not supported on Xyce in v1: write the value as a "
                          "number, or run it on VACASK" % shown)
    if capitals:
        return refuse_capitals()
    return None


def _vint(node: object, params: _ParamInfo) -> bool:
    """Whether an ast subtree is an integer in Verilog-A's typing: an integer literal, an integer parameter, a comparison,
    arithmetic of those."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, int) and not isinstance(node.value, bool)
    if isinstance(node, ast.Name):
        return params.get(node.id, ("real", True))[0] == "integer"
    if isinstance(node, ast.UnaryOp):
        return _vint(node.operand, params)
    if isinstance(node, ast.BinOp):
        return _vint(node.left, params) and _vint(node.right, params)
    return isinstance(node, ast.Compare)


def _src(node: object, joined: str) -> str:
    """An ast node's text in the joined default (Python 3.8+ keeps the columns)."""
    try:
        return joined[node.col_offset:node.end_col_offset]           # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        return "?"


_OP_TEXT = {ast.BitOr: "|", ast.BitAnd: "&", ast.BitXor: "^", ast.LShift: "<<", ast.RShift: ">>", ast.FloorDiv: "//"}


def _int_only(node: object) -> bool:
    """Whether every leaf of an ast subtree is an integer literal (Python's bitwise operators take no float)."""
    for n in ast.walk(node):                       # type: ignore[arg-type]
        if isinstance(n, ast.Name):
            return False
        if isinstance(n, ast.Constant) and not (isinstance(n.value, int) and not isinstance(n.value, bool)):
            return False
    return True


def _odd_form(default: List[Tuple[str, str, int]]) -> str:
    """The form in a default that Python's eval cannot read, named for the message (the tokens as PyMS reads them)."""
    values = [v for _k, v, _a in default]
    for k, (kind, v, at) in enumerate(default):
        nxt = default[k + 1] if k + 1 < len(default) else None
        if v == "?":
            return "the ternary ?:"
        if kind == "NUMBER" and nxt is not None and nxt[0] == "IDENT" and nxt[2] == at + len(v) and nxt[1].startswith("_"):
            return "the digit separator of %s%s (PyMS reads the tokens `%s' and `%s')" % (v, nxt[1], v, nxt[1])
        if v == "*" and nxt is not None and nxt[1] == "*":
            return "the power operator **"
        if kind == "IDENT" and v.startswith("$"):
            return "the system function %s" % v
        if v == "'":
            return "a based literal"
        if v == "!" and not (nxt is not None and nxt[1] == "="):
            return "the operator !"
        if v in ("&", "|", "<", ">", "=", "!") and nxt is not None and nxt[1] in ("&", "|", "="):
            return "the operator %s%s" % (v, nxt[1])
    return "a form Python's eval cannot read (%s)" % " ".join(values)


def _pyms_known(env: Optional[Dict[str, str]]) -> Tuple[List[str], Set[str]]:
    """pyms_search_dirs, and the macros PyMS knows before a source defines any (PYMS_PREDEFINED, STANDARD_MACROS and
    the `defines of its own constants.vams, where it is found)."""
    extra = pyms_search_dirs(env)
    known: Set[str] = set(PYMS_PREDEFINED) | set(STANDARD_MACROS)
    for d in extra:
        text = _read(os.path.join(d, "constants.vams")) if os.path.isdir(d) else None
        if text is not None:
            known.update(_VA_DEFINE.findall(_va_code(text)))
    return extra, known


def pyms_scans(hdl: Sequence[str], env: Optional[Dict[str, str]] = None) -> List[PymsScan]:
    """A PymsScan of each Verilog-A source that exists and whose include tree is complete, in order, once each."""
    extra, known = _pyms_known(env)
    out: List[PymsScan] = []
    done: Set[str] = set()
    for h in hdl:
        src = os.path.abspath(h)
        if src in done or not os.path.isfile(src):
            continue
        done.add(src)
        scan = PymsScan(src, extra, known)
        if scan.complete:
            out.append(scan)
    return out


def macro_problems(hdl: Sequence[str], env: Optional[Dict[str, str]] = None,
                   scans: Optional[List[PymsScan]] = None) -> List[Tuple[str, str, int, str, str]]:
    """(source, file, line, macro, why), in order, for each macro use PyMS would compile wrong (PymsScan.problems):
    FIX2E4, S-2E H5 (PyMS compiles `parameter real r = `RV' without `define RV with no error, and the module ran wrong,
    V1:p = -1e-12 with exit 0, where VACASK's openvaf-r refuses it: `macro `RV has not been declared'); FIX2E5 for
    the conditions, the undefs and PyMS's expansion at the end of a file.  vamos gives Xyce no -va,define (an error
    for a netlist with Verilog-A, flow.engine_notes).  A source that does not exist, and one with an `include that is
    not found (which could define the macro: missing_includes and PyMS's own warning judge those), are left to Xyce."""
    return [(s.src, f, line, name, why) for s in (scans if scans is not None else pyms_scans(hdl, env))
            for f, line, name, why in s.problems()]


def undefined_macros(hdl: Sequence[str], env: Optional[Dict[str, str]] = None) -> List[Tuple[str, str, int, str]]:
    """(source, file, line, macro) of macro_problems."""
    return [(s, f, line, name) for s, f, line, name, _why in macro_problems(hdl, env)]


def pyms_default_problems(hdl: Sequence[str], env: Optional[Dict[str, str]] = None,
                          scans: Optional[List[PymsScan]] = None) -> List[Tuple[str, str, int, str, str]]:
    """(source, file, line, parameter, why), in order, for each parameter declaration whose default Xyce's PyMS
    evaluates as 0 (PymsScan.default_problems; FIX2E5, TRIAL2EH-F1: `parameter real r = 1k * 1' gave a device that
    contributed nothing, with exit 0, where VACASK runs it; FIX2E6: every form PyMS's evaluation cannot read, _eval_form),
    read in the source as PyMS's preprocessor expands it."""
    return [(s.src, f, line, name, why) for s in (scans if scans is not None else pyms_scans(hdl, env))
            for f, line, name, why in s.default_problems()]


def pyms_probe_problems(hdl: Sequence[str], env: Optional[Dict[str, str]] = None,
                        scans: Optional[List[PymsScan]] = None) -> List[Tuple[str, str, int, str, str]]:
    """(source, file, line, the probe, why), in order, for each current probe I(...) a module reads in an expression
    (PymsScan.probe_problems; FIX2E6, TRIAL2EI-1: PyMS reads no current: a node pair's fails its C++ compile, a named
    branch's is 0)."""
    return [(s.src, f, line, probe, why) for s in (scans if scans is not None else pyms_scans(hdl, env))
            for f, line, probe, why in s.probe_problems()]


# -- PyMS's builds in Xyce's output (§4.5 item 13) -------------------------------------------------------

@dataclass
class PymsFailure:
    """A PyMS build that failed for good: the module its evaluator library names ("" when the output
    names none), why (the compiler's first error, else the exception's line), whether that is a
    compiler error, and the output lines that belong to it (left out of the narration)."""
    module: str
    detail: str
    lines: List[str] = field(default_factory=list)
    compiler: bool = False


def pyms_failures(text: str) -> List[PymsFailure]:
    """The PyMS builds in one Xyce output that failed for good.  build_vae_so.py prints a Python
    traceback only when it gives up: its retry without the zero-valued parameters failed too (two
    chained tracebacks), a step timed out (it allows 900 s), or the build raised; Xyce then registers
    the module and runs it with no evaluator, a device that contributes nothing (measured 2026-10-17:
    $abstime, a space in PYMS_VAE_CACHE).  A retry that builds prints no traceback."""
    lines = text.splitlines()
    out: List[PymsFailure] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() != _TRACEBACK:
            i += 1
            continue
        block: List[str] = []
        through = False
        exc: List[str] = []
        while True:                                  # one traceback, then each one chained to it
            block.append(lines[i])
            i += 1
            while i < len(lines) and lines[i][:1].isspace():         # the frames
                through = through or bool(_PYMS_FRAME.match(lines[i]))
                block.append(lines[i])
                i += 1
            start = len(block)
            while i < len(lines) and not _exception_ends(lines[i]) and len(block) - start < 400:
                block.append(lines[i])
                i += 1
            exc = block[start:]
            j = i
            while j < len(lines) and lines[j].strip() in _CHAINED + ("",):
                j += 1
            if j < len(lines) and j > i and lines[j].strip() == _TRACEBACK and \
                    any(ln.strip() in _CHAINED for ln in lines[i:j]):
                block.extend(lines[i:j])
                i = j
                continue
            break
        if not through:
            continue
        m = _PYMS_LIB.search("\n".join(block))
        err = next((_ERROR_TEXT.search(ln) for ln in exc if _ERROR_TEXT.search(ln)), None)
        head = next((ln.strip() for ln in exc if ln.strip()), "the build raised")
        out.append(PymsFailure(m.group(1) if m else "", err.group(1).strip() if err else head,
                               [ln for ln in block if ln.strip()], err is not None))
    return out


def _exception_ends(line: str) -> bool:
    """Where a traceback's exception text ends: the next traceback or a chain line, or a line of Xyce
    (its banner) or of PyMS's other steps."""
    s = line.strip()
    return s == _TRACEBACK or s in _CHAINED or line.startswith(("*****", "build_vae_so: ", "preprocess: "))


def pyms_compiling(pgid: int) -> List[str]:
    """The Verilog-A files that build_vae_so.py processes of a process group compile now (from /proc;
    [] where there is none): what Xyce's PyMS does while a deck or the -norun check seems idle."""
    out: List[str] = []
    for stat in glob.glob("/proc/[0-9]*/stat"):
        try:
            with open(stat) as fh:
                fields = fh.read().rsplit(")", 1)[1].split()
            if int(fields[2]) != pgid:
                continue
            with open(stat[:-len("stat")] + "cmdline", "rb") as fh:
                argv = [a.decode("utf-8", "replace") for a in fh.read().split(b"\0") if a]
        except (OSError, IndexError, ValueError):
            continue
        for k, a in enumerate(argv[:-1]):
            if a.endswith("build_vae_so.py"):
                out.append(argv[k + 1])
                break
    return out


def place_va(hdl: Sequence[str], run_dir: str) -> List[str]:
    """Copy the Verilog-A sources into run_dir (see the module docstring); the .hdl names, in order,
    each source once.  OSError when a copy fails.

    Phase 2c2 (F4): no directory but run_dir itself holds two modules' files.  PyMS builds a module whose
    source carries no xyceModelGroup/xyceLevelNumber attributes with the attributes of the first .va file
    beside it that does (its sibling-directory fallback, utils/PyMS/vae/xyce_device_gen.py, which reads
    os.path.dirname of the .hdl name): two modules of one library sharing ../inc, placed side by side in
    lib/, lost their parameters (rc 1, "Unrecognized parameter", status 2 for a valid deck [E125]).  A base
    name has no directory to look in, so run_dir holds any number of modules [E104]; a module that needs a
    directory (an include that climbs out of its own, or a base name another file holds) sits in one no
    other module's file shares, run_dir/<its layout> when that is free, else its own vamos_va<k>/ with its
    include tree (a vamos_va<k>/ holds one module)."""
    names: List[str] = []
    placed: Dict[str, str] = {}
    users: Dict[str, Set[str]] = {}         # a directory of run_dir ("" itself) -> the module names with files there
    module_dirs: Dict[str, str] = {}        # a directory other than run_dir holding a module's file -> its name
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
            name = posixpath.join(*(([sub] if sub else []) + rels[0].split(os.sep)))
            dirs = [posixpath.dirname(posixpath.join(*(([sub] if sub else []) + r.split(os.sep)))) for r in rels]
            if all(not os.path.exists(d) or (os.path.isfile(d) and filecmp.cmp(f, d, shallow=False))
                   for f, d in zip(files, dests)) and _apart(name, dirs, sub, users, module_dirs):
                break
            k += 1
        for f, d in zip(files, dests):
            if not os.path.exists(d):
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copyfile(f, d)
        for dname in dirs:
            users.setdefault(dname, set()).add(name)
        if dirs[0]:
            module_dirs[dirs[0]] = name
        placed[src] = name
        names.append(name)
    return names


def _apart(name: str, dirs: Sequence[str], sub: str, users: Dict[str, Set[str]], module_dirs: Dict[str, str]) -> bool:
    """place_va's rule: the module `name` (its file's directory dirs[0], its tree's files in dirs, relative to
    run_dir) shares no directory but run_dir with another module's file: its own directory holds no other
    module's file, none of its files lands in another module's directory, and a vamos_va<k>/ (sub) is one
    no other module uses.  The same name is the same module (one file, its identical copy, already placed)."""
    if sub and any(d == sub or d.startswith(sub + "/") for d, who in users.items() if who - {name}):
        return False
    if dirs[0] and users.get(dirs[0], set()) - {name}:
        return False
    return all(module_dirs.get(d, name) == name for d in dirs if d)


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
    grid, a step grid as the .DATA table it writes (a descending one: the plan's points, R-emit-3)."""
    spec = lv.spec
    if spec.mode == "step":
        start, stop_, step = (float(getattr(e, "value", e)) for e in (spec.start, spec.stop, spec.step))
        if step < 0:
            return len(grid_values(spec))
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
    """proc.Interrupts with spectre's signals (stop.py), sent to Xyce's process group (its PyMS compile
    runs in children of Xyce: sh, python3, g++, cc1plus), which also tells a second handled signal
    (`second`, stop.Stop) from the grace-period kill (`expired`, converted as status 3): the two
    set the same `killed` flag."""

    def __init__(self, grace: Optional[float] = None) -> None:
        super().__init__(stop.HANDLED, stop.FORWARD, stop.FLAGS,
                         grace=proc.INTERRUPT_GRACE if grace is None else grace, group=True)
        self.second: Optional[int] = None
        self.expired = False
        stop.forget()                                    # an engine run starts with no stop signal (stop.classify)

    def __enter__(self) -> "Signals":
        super().__enter__()
        self._flags[:0] = stop.take_over_flags(self._saved)        # the flow's, not printed yet (TRIAL2EG-F07)
        return self

    def __exit__(self, *exc) -> None:
        saved = dict(self._saved)
        super().__exit__(*exc)
        stop.give_back_flags(saved, self.take_flags())            # not printed: the flow's poll answers them (FIX2E5)

    def _on_signal(self, signum, frame) -> None:
        # the guide trial of phase 2e (TRIAL2EF-01): a repeat of one hangup's SIGHUP (stop.classify: the kernel's after
        # bash's) is neither passed on again nor a second signal (it raised stop.Stop, and nothing was converted)
        kind = stop.classify(signum, self.signum is not None)
        if kind == "repeat":
            return
        if kind == "second" and self.second is None:
            self.second = signum
        proc.Interrupts._on_signal(self, signum, frame)

    def _on_alarm(self, signum, frame) -> None:
        self.expired = True
        proc.Interrupts._on_alarm(self, signum, frame)


def _second_stop(signum: int, phase: str = PHASE_SETUP) -> stop.Stop:
    """stop.Stop for a second handled signal (§2.7): raised once Xyce has ended, before its output is
    classified, so nothing says that results are written; phase: the stage it came in, which the flow's
    warning names (the deck's analysis; TRIAL2EH-F11)."""
    return stop.Stop(signum, "stopped by a second signal (%s): Xyce was killed and nothing is converted"
                     % proc.signal_name(signum), phase=phase)


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
        self.checking: Optional[Deck] = None          # the deck the -norun check runs on, while it runs
        self.told: Set[str] = set()                   # Xyce messages already printed (once per run)
        self._printed: Optional[Dict[str, str]] = None   # the deck's element names (upper case) -> the netlist's

    # -- messages
    def say(self, n: Note, phase: str) -> None:
        if self.log is not None:
            self.log.message(n, phase)

    def unprint(self, text: str) -> str:
        """Xyce's text with every name the deck printed by the user's escaped-name rule (2026-10-19) as the netlist's:
        an element by the name Xyce gives it (its letter, then the escaped name, in Xyce's upper case: R_R_0741_076
        is R<1>), a node or a card through names.unescape_text (_B_056C is B.C); Xyce's ':' between the components
        stays.  Only Xyce's own text goes through it, never vamos's (whose names are the netlist's already)."""
        if self.nl.dialect != "spectre" or "_" not in text:
            return text
        if self._printed is None:
            va = {k.lower(): v for k, v in self.nl.va_modules.items()}
            # the deck's own names: xyce.element_renames makes each printed name one element's (FIX2E4)
            renamed = xyce.element_renames(self.nl, va)
            self._printed = {}
            for items in [self.nl.body] + [sub.body for sub in T.all_subckts(self.nl)]:
                for it in ir.flat_items(items):
                    if not isinstance(it, ir.Instance):
                        continue
                    odd = escape_name(it.name) != it.name
                    if odd or (xyce.element_letter(it, va), it.name) in renamed:
                        self._printed.setdefault(xyce.printed(it, va, True, renamed).upper(), it.name)
                    if it.kind == "r" and (odd or (xyce.GFORM, it.name) in renamed):
                        gform = renamed.get((xyce.GFORM, it.name), "g" + escape_name(it.name))
                        self._printed.setdefault(gform.upper(), it.name)
        printed = self._printed
        return unescape_text(text, ":", lambda c: printed.get(c.upper()))

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
        if self.include_errors() or self.source_errors():
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
            if intr.second is not None:                 # a second signal while no Xyce ran
                raise _second_stop(intr.second)
        if intr.signum is not None:
            er.rc = 3
        return er

    def include_errors(self) -> bool:
        """The `includes PyMS would not find (missing_includes), each an error in AHDL read-in before
        Xyce runs; True when there is one.  A header that CDS_VLOGA_INCLUDE or -I finds is named with
        its directory: Xyce's compile searches none (§2.8)."""
        missing = missing_includes(self.nl.hdl)
        if not missing:
            return False
        from vamos.spectre.run_vacask import va_search        # §2.8's list, as the VACASK compile gets it
        search = va_search(self.job) if self.job is not None else []
        for src, inc, name in missing:
            text = ("`include \"%s\" is not found beside the file that includes it, where Xyce's PyMS looks for it "
                    "[E75], nor in PYMS_INCLUDE_PATH: PyMS would build the module without it, a wrong device, so "
                    "Xyce does not run (§4.5 item 13)" % name)
            where = next((d for d in search if os.path.isfile(os.path.join(d, name))), None)
            if where is not None:
                text += ("; it is in %s (CDS_VLOGA_INCLUDE or -I), which Xyce's compile does not search (§2.8): "
                         "put it beside %s, or run on VACASK" % (where, os.path.basename(inc)))
            if inc != src:
                text += " (a header of %s)" % src
            self.say(error(inc, text), PHASE_AHDL)
        return True

    def macro_errors(self, scans: Optional[List[PymsScan]] = None) -> bool:
        """The macro uses PyMS would compile wrong (macro_problems), each an error in AHDL read-in before Xyce runs;
        True when there is one (FIX2E4, S-2E H5; FIX2E5: read as PyMS's preprocessor reads the source)."""
        bad = macro_problems(self.nl.hdl, scans=scans)
        for src, inc, lineno, name, why in bad:
            text = "`%s %s" % (name, why)
            if inc != src:
                text += " (a header of %s)" % src
            self.say(error("%s:%d" % (inc, lineno), text), PHASE_AHDL)
        return bool(bad)

    def default_errors(self, scans: Optional[List[PymsScan]] = None) -> bool:
        """The parameter defaults PyMS evaluates as 0 (pyms_default_problems), each an error in AHDL read-in before Xyce
        runs; True when there is one (FIX2E5, TRIAL2EH-F1)."""
        bad = pyms_default_problems(self.nl.hdl, scans=scans)
        for src, inc, lineno, _name, why in bad:
            text = why + (" (a header of %s)" % src if inc != src else "")
            self.say(error("%s:%d" % (inc, lineno), text), PHASE_AHDL)
        return bool(bad)

    def probe_errors(self, scans: Optional[List[PymsScan]] = None) -> bool:
        """The currents a module reads in an expression (pyms_probe_problems), each an error in AHDL read-in before Xyce
        runs; True when there is one (FIX2E6, TRIAL2EI-1)."""
        bad = pyms_probe_problems(self.nl.hdl, scans=scans)
        for src, inc, lineno, _probe, why in bad:
            text = why + (" (a header of %s)" % src if inc != src else "")
            self.say(error("%s:%d" % (inc, lineno), text), PHASE_AHDL)
        return bool(bad)

    def source_errors(self) -> bool:
        """macro_errors, default_errors and probe_errors over one PymsScan of each source; True when there is one."""
        scans = pyms_scans(self.nl.hdl)
        bad = self.macro_errors(scans)
        bad = self.default_errors(scans) or bad
        return self.probe_errors(scans) or bad

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
                proc.kill_group(p)                      # an exception above: never leave Xyce (or its compile) behind
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
                self.say(note("", stop.CHECKPOINT),
                         self.phase(self.running) if self.running is not None else PHASE_SETUP)

    def progress(self) -> str:
        """The SIGUSR1 text: the deck Xyce runs and its last `Percent complete', or the -norun check
        (which on a cold PyMS cache compiles every Verilog-A module, about 15 s for e2e 8 [E125]),
        and the file PyMS compiles when it does."""
        d = self.running
        if d is None:
            if self.checking is not None:
                return "Xyce checks the netlist (-norun on %s)%s" % (self.checking.file, self.compiling())
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
        return text + self.compiling()

    def compiling(self) -> str:
        """", PyMS compiles <file>" while a build_vae_so.py process runs in the running Xyce's process
        group (pyms_compiling); "" otherwise."""
        p = self.intr.proc if self.intr is not None else None
        if p is None or p.poll() is not None or not os.path.isdir("/proc"):
            return ""
        files = pyms_compiling(p.pid)
        return ", PyMS compiles %s" % ", ".join(sorted(set(os.path.basename(f) for f in files))) if files else ""

    def narrate(self, text: str, deck: Deck, skip: Sequence[str] = ()) -> None:
        """Xyce's messages of one deck (§8.7's filtered narration): its warnings as warnings, any other
        message of a deck that did not fail as a warning too, each text once per run (the decks
        share the circuit, so a model warning would repeat in every deck), at most ENGINE_LINES per
        deck; PyMS's registration lines and the problems (`skip`, reported as errors) left out."""
        shown = more = 0
        for raw in diagnostics(text):
            m = self.unprint(raw)
            if m in self.told or _QUIET.search(raw) or any(s in m or m in s for s in skip):
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
        """`<xyce> -norun` on the first deck: 0 when it passes, 2 for a problem, 3 for a signal;
        stop.Stop after a second signal."""
        self.checking = deck
        try:
            rc, text, _ = self.engine(["-norun", deck.file], deck.step.id + ".norun.log", deck)
        finally:
            self.checking = None
        assert self.intr is not None
        if self.intr.second is not None:
            raise _second_stop(self.intr.second)
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
            msgs = [self.unprint(m) for m in ([m for m in diagnostics(text) if _DIAG_ERROR.search(m)]
                                              or diagnostics(text)[-5:])]
            what = ("exit status %d" % rc) if sig is None else ("ended by %s" % proc.signal_name(sig))
            self.say(error("", "Xyce's check of the netlist (-norun on %s, the deck of analysis `%s') failed (%s)%s"
                           % (deck.file, deck.step.name, what, (": " + "; ".join(msgs[:ENGINE_LINES])) if msgs else "")),
                     PHASE_SETUP)
            return 2
        return 0

    def stop_reason(self, sig: Optional[int]) -> str:
        """The signal that stopped Xyce: the one vamos got and passed on (and, after the grace, that
        Xyce was killed), else Xyce's own."""
        assert self.intr is not None
        if self.intr.signum is not None:
            text = proc.signal_name(self.intr.signum)
            if self.intr.expired:
                text += "; Xyce did not stop within %g s and was killed" % self.intr.grace
            return text
        return "%s from outside vamos" % proc.signal_name(sig) if sig is not None else "a signal"

    def problems(self, text: str, deck: Deck) -> List[Note]:
        """output_problems and device_summary_problems over one Xyce output, each named by the
        alter or sweep that introduced it where one matches its parameter; then the PyMS builds that
        failed for good (pyms_failures: Xyce runs such a module as a device that contributes
        nothing) and the includes PyMS skipped that are not standard headers (it built the module
        without them)."""
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
                                 % (label, deck.step.name, deck.file, self.unprint(n.message))))
            else:
                out.append(error(n.origin if n.origin != "Xyce" else "",
                                 "Xyce, the deck of analysis `%s' (%s): %s" % (deck.step.name, deck.file,
                                                                             self.unprint(n.message))))
        where = "Xyce, the deck of analysis `%s' (%s)" % (deck.step.name, deck.file)
        modules = {k.lower(): m for k, m in self.nl.va_modules.items()}
        for f in pyms_failures(text):
            m = modules.get(f.module.lower()) if f.module else None
            if m is not None:
                who = "Verilog-A module %s (%s)" % (f.module, m.path)
            elif f.module:
                who = "Verilog-A module %s" % f.module
            else:
                files = sorted(set(m.path for m in modules.values()))
                who = "a Verilog-A module" + (" of %s" % ", ".join(files) if files else "")
            why = ("the C++ compile of its evaluator failed: %s" % f.detail) if f.compiler else f.detail
            if "$abstime" in f.detail:
                why += "; PyMS has no $abstime [E125], VACASK runs the module"
            cache = self.env.get("PYMS_VAE_CACHE", "")
            if any(c.isspace() for c in cache):
                why += "; PYMS_VAE_CACHE=%s holds a space, which PyMS's build cannot take" % cache
            msg = ("%s: PyMS could not build %s, which Xyce would run as a device that contributes nothing, so the "
                   "run stops (§4.5 item 13): %s" % (where, who, why))
            if msg not in seen:
                seen.add(msg)
                out.append(error("", msg))
        for name in _PYMS_SKIPPED.findall(text):
            msg = ("%s: PyMS did not find the Verilog-A include \"%s\" and built the module without it (its "
                   "preprocessor's warning: '%s' not found (skipped)): it looks for an include beside the file "
                   "that includes it [E75], so the run stops (§4.5 item 13)" % (where, name, name))
            if not standard_header(name) and msg not in seen:
                seen.add(msg)
                out.append(error("", msg))
        return out

    @staticmethod
    def pyms_lines(text: str) -> List[str]:
        """The output lines of the PyMS failures that problems() reports, which the narration leaves out."""
        out = [ln.strip() for f in pyms_failures(text) for ln in f.lines]
        if out:
            out.append("build_vae_so: full build failed")
        return out + ["preprocess: warning: '%s' not found" % name for name in _PYMS_SKIPPED.findall(text)
                      if not standard_header(name)]

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
            if self.intr is not None and self.intr.second is not None:
                raise _second_stop(self.intr.second, self.phase(deck))   # nothing is converted: nothing is classified
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
                if intr.signum is not None:
                    # a Xyce that did not stop for the SIGTERM vamos passed on ran the deck to its end (TRIAL2EH-F11, as
                    # on VACASK: exit status 3 with no word of the signal before)
                    later = any(d is deck for d in self.decks) and self.decks[-1] is not deck
                    tail = ", and the analyses after it are not run" if later else ""
                    self.say(warning("", "vamos got %s and passed it on, but Xyce ran analysis `%s' to its end: its "
                                         "results are written%s" % (proc.signal_name(intr.signum), step.name, tail)),
                             phase)
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
                           % (proc.signal_name(sig), step.name, deck.file, self.unprint(self._errors_of(text)))), phase)
            return 2
        problems = self.problems(text, deck)
        if problems:
            er.status[step.id] = "failed"
            self.narrate(text, deck, [n.message for n in problems] + [self.unprint(x) for x in self.pyms_lines(text)])
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
        errs = [self.unprint(m) for m in diagnostics(text) if _DIAG_ERROR.search(m)]
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
    """The environment of every Xyce run (§7.1, §7.2: E68, E96), OpenBLAS's threads bounded unless the
    user set a thread count (tools.bound_blas_threads), from tools.child_env: the shim directories off PATH
    and the lock-out, as for the version probes (TRIAL2EG-F04: the caller's PATH, VAMOS_REDIRECT ignored)."""
    from vamos.ams import engines
    return tools.bound_blas_threads(engines.env_for(ENGINE, None, tools.child_env()))


def run(nl: ir.Netlist, plan: "RunPlan", sigmap: "SignalMap", run_dir: str, job: "SpectreJob",
        log: "SpectreLog", strict: bool = False) -> EngineResult:
    """Run the plan on Xyce, one deck per analysis (see the module docstring).  strict
    (--vamos-strict, the additive keyword run_vacask takes and flow passes): render's warnings
    are errors, exit status 2 before Xyce runs."""
    return _Runner(nl, plan, sigmap, run_dir, job, log, strict).run()
