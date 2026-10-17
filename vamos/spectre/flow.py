"""The spectre run: §1's pipeline from step 1b on, and §2.7's signals and exit status
(docs/VAMOS_SPECTRE_DESIGN.md; the API is the §10 block).

    flow.run(job, opts) -> int        personalities/spectre.main hands it every job (§10)

personalities/spectre.main does step 1 (build_job, the --vamos-* effects) and hands the job here as
soon as this module imports; run() never raises (§2.7) and returns the Spectre exit status:

  help    -h and its forms print the help text and exit 0 (S5's rule: not with a scan-time error)
  1b      personalities.spectre.open_log (the only log opening), the -V/-W line (alone: nothing
          else, exit 0), the provenance header (banner.provenance over engines.tool_rows and the
          cpp row when cpp runs), personalities.spectre.report: the scan-time stop is exit 2 before
          any run directory exists; then a bad engine choice (exit 2)
  2       the results path that argv and the defaults give (§2.5: -raw, else %C:r.raw moved into
          -outdir when it has no /); its stale logFile (psf) or nutmeg file removed; the run
          directory tempfile.mkdtemp(prefix="<%C:r:t>.run.", dir=<its directory>)
  3       the input: the netlist, or stdin copied to <run>/stdin (%C = stdin, origins stdin:<line>)
  4       cpp when args.cpp_runs: cpp.command echoed under --vamos-verbose, cpp.preprocess, then
          cpp.macro_warnings; without cpp, cpp.directive_errors (§2.6)
  5       spectre.parse with SpectreParseOpts (-I, the %-codes, +pre_config/+config, the cpp
          markers and title, -mts, CDS_VLOGA_INCLUDE, -va,define; stdin: top_dir = the cwd)
  6       job.settings, log.limits, the errors settings() appends to job.notes (§2.2)
  7       engines.problems (exit 2); the dispositions that depend on the engine: -va,define noted on
          Xyce (§2.3), and for a netlist with Verilog-A CDS_VLOGA_INCLUDE noted on Xyce and a
          CDS_AHDL_* other than Spectre's documented default a warning (§2.8)
  8       plan.build; its notes, less the duplicate option notes (§14 q.54, below), and its note
          actions in netlist order
  9       signals.resolve, with the readns/readic/read names made absolute against the cwd
  11      the engine: vamos.spectre.run_<engine>.run(<render copy>, plan, sigmap, run_dir, job, log)
          -> EngineResult (stop.py's rc convention; `strict=` too where the runner takes it);
          stop.Stop: exit 3 at once, not converted.  The runner prints what happens while the engine
          runs (§8.7): the analysis banners and times, the engine's messages, the failed and
          interrupted analyses; EngineResult.log is not printed again
  12-13   results.collect (masters: STRUCT type -> master from the IR, the run's notes) into
          psf.write_analysis per analysis and psf.write_logfile, or nutmeg.write; options
          save=nooutput (no data file), dc print=yes (the node voltages in the log) and the
          write=/writefinal= state files (output/statefile.py) handled here, outside collect; a
          result collect could not fill makes the status at least 1; after a non-zero rc a step with
          no status is one the engine never reached (stop.py): not converted, not warned about
  14      the run directory removed after status 0 (unless --vamos-keep), kept after 1, 2 or 3 with
          "run directory kept: <dir>", an empty one always removed; log.finish(status)

What the flow prints itself (§8.7): the -V/-W line, the provenance header, the scan-time messages
(report), "Simulating `<netlist>' ...", the read-in and setup messages, the circuit inventory, the
cpp echo under --vamos-verbose, the conversion's messages, the print=yes node voltages, the
run-directory note and the trailer.

Signals (§2.7).  run() installs handlers in the main thread for INT TERM HUP QUIT (stop.HANDLED),
USR1 USR2 (stop.FLAGS) and TSTP; one that is ignored at start stays ignored.  Before an engine runs
a stop signal ends the run at once with status 3 and the trailer.  While a runner runs, its
proc.Interrupts own the signals (the first is forwarded to the engine; the runner returns rc 3, or
raises stop.Stop for a second one).  During the conversion the first stop signal ends it before
the next file (nutmeg: the next plot), a second one at once (the file being written dropped), with
status 3 and a warning naming the signal; the logFile lists the complete blocks only (an unswept
analysis, or a whole sweep block), so it stays consistent.  USR1 and USR2 only set flags; the main
loop prints the status line or the note "checkpoint not supported".

Every exception is exit status 2 with the "terminated prematurely" trailer: a NoteError's notes, a
ToolError, a ScanError, an engine-choice ValueError and an OSError while setting up the run
directory or writing the results as one error message each; anything else is an internal error, one
line on the screen with the traceback in the run directory, else in the +log/=log file, else in a
vamos-spectre-*.traceback temporary file the line names.

The duplicate option note (§14 q.54): the parser notes `options <key>=<v>: not applied (§3.10)` and
plan.build `options: <key>=<v> has no effect here` for the same key; the parser's message is kept and
plan.build's dropped, for every key of the netlist's options statements that the parser already
reported (its notes and warnings, never an error).
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import os
import re
import shlex
import shutil
import signal
import sys
import tempfile
import threading
import traceback
from datetime import datetime
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from vamos import VERSION, banner, tools
from vamos.ams import engines
from vamos.netlist import plan as nplan
from vamos.netlist import signals as nsignals
from vamos.netlist import spectre as nspectre
from vamos.netlist import tables
from vamos.netlist.expr_ast import Num, Str
from vamos.notes import ERROR, Note, NoteError, error, has_errors, note, strict, warning
from vamos.optable import ScanError, unmapped_notes
from vamos.output import nutmeg, psf, statefile
from vamos.personalities import spectre as personality
from vamos.spectre import args as sargs
from vamos.spectre import cpp as scpp
from vamos.spectre import percent, results, stop
from vamos.spectre.job import Settings, SpectreJob
from vamos.spectre.job import settings as job_settings
from vamos.spectre.log import SpectreLog, si

# Message phases, the 5.1.41 form "Error from <prog> during <phase>." [M UG pp.270, 274]: the
# command line is S5's PHASE_ARGS; the others follow the UG's samples (circuit read-in, initial setup).
PHASE_READ = "circuit read-in"
PHASE_SETUP = "initial setup"
PHASE_INTERNAL = "setup"            # personalities.spectre._internal_error's wording, one for both
STDIN_NAME = "stdin"                # %C and the copy in the run directory (§1, §2.4)
TRACEBACK_NAME = "vamos-spectre.traceback"
STOP_SIGNALS = stop.HANDLED
_SIGUSR1 = getattr(signal, "SIGUSR1", None)

# the analysis words of a phase: "during transient analysis `tr1'" [M UG p.274 samples]
_PHASE_WORDS = {"op": "DC", "dc": "DC", "ac": "AC", "noise": "noise", "xf": "XF", "tran": "transient"}
# the dc family solves an operating point: write=/writefinal= and print=yes apply (§3.11, §8.6)
_STATE_KINDS = ("op", "dc", "tran")
_PARENT_KINDS = psf.PARENT_KINDS
# §2.8 [M ref19 pp.550-551]: Spectre's documented default of each CDS_AHDL_* setting that changes
# results; a value Spectre documents as the default is silent, any other one a warning.  IGNORE_OPPOINT
# has none (YES for compact models, NO for others): every value is a warning.  The flag-like ones name
# the value that changes behaviour (YES for IGNORE_HIDDEN_STATE, NO for AUTOGDEV_SUPPORT).
_AHDL_DEFAULTS: Dict[str, Optional[str]] = {
    "CDS_AHDL_FINISH_MODE": "0", "CDS_AHDL_DDT_SCALE": "1e-17", "CDS_AHDL_IDT_SCALE": "1",
    "CDS_AHDL_IGNORE_HIDDEN_STATE": "NO", "CDS_AHDL_IGNORE_OPPOINT": None, "CDS_AHDL_AUTOGDEV_SUPPORT": "YES",
    "CDS_AHDL_LRM_COMPATIBILITY": "2.3"}
_PARSER_OPTION = re.compile(r"^options (\w+)=")
_PLAN_OPTION = re.compile(r"^options: (?:option )?(\w+)=")
_PARSER_INFO = re.compile(r"^(\S+) info: the info file is not written")
_PLAN_INFO = re.compile(r"^info (\S+): the information file is not written")
_VA_INCLUDE_SPLIT = re.compile(r"[,:;]")


def run(job: SpectreJob, opts: dict) -> int:
    """§1 from step 1b on, with §2.7's signals and exit statuses; never raises."""
    if job.action == "help" and not has_errors(job.notes):
        print(personality.help_text(job.help_topic))
        return 0
    return _Run(job, opts).main()


# =============================================================================================
# Signals (§2.7)
# =============================================================================================

class _Interrupted(BaseException):
    """A stop signal that ends the run (status 3).  A BaseException, as KeyboardInterrupt is, so
    that no `except Exception` of a module the flow calls swallows the user's signal."""

    def __init__(self, signum: int) -> None:
        super().__init__("stopped by %s" % _signal_name(signum))
        self.signum = signum


class _Signals:
    """flow.run's own handlers, outside the engine runs (a runner's proc.Interrupts replaces them
    while its engine runs and restores them afterwards).  The stop signals raise _Interrupted in
    the "now" state; in "deferred" (the conversion) the first one is recorded for the main loop and
    a second one raises; in "quiet" (the trailer) they are recorded only."""

    def __init__(self) -> None:
        self.signum: Optional[int] = None
        self.count = 0
        self.state = "now"
        self._flags: List[int] = []
        self._saved: Dict[int, object] = {}

    def install(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        for sig in STOP_SIGNALS:
            self._install(sig, self._on_stop)
        for sig in stop.FLAGS:
            self._install(sig, self._on_flag)
        tstp = getattr(signal, "SIGTSTP", None)
        if tstp is not None:
            self._install(tstp, self._on_tstp)

    def _install(self, sig: int, handler) -> None:
        try:
            if signal.getsignal(sig) == signal.SIG_IGN:      # nohup, a background job: stays ignored
                return
            self._saved[sig] = signal.signal(sig, handler)
        except (OSError, RuntimeError, ValueError):
            pass

    def restore(self) -> None:
        for sig, handler in self._saved.items():
            try:
                signal.signal(sig, handler if handler is not None else signal.SIG_DFL)
            except (OSError, RuntimeError, ValueError):
                pass
        self._saved = {}

    def take_flags(self) -> List[int]:
        out, self._flags = self._flags, []
        return out

    def _on_stop(self, signum, frame) -> None:
        self.count += 1
        if self.signum is None:
            self.signum = signum
        if self.state == "now" or (self.state == "deferred" and self.count > 1):
            raise _Interrupted(signum)

    def _on_flag(self, signum, frame) -> None:
        self._flags.append(signum)

    def _on_tstp(self, signum, frame) -> None:
        os.kill(os.getpid(), signal.SIGSTOP)             # Ctrl-Z stops vamos (no engine runs here)


def _signal_name(signum: int) -> str:
    try:
        return signal.Signals(signum).name
    except ValueError:
        return "signal %d" % signum


# =============================================================================================
# The run
# =============================================================================================

class _Fail(Exception):
    """Ends the run with these messages and this status (an error the flow names itself)."""

    def __init__(self, notes: Sequence[Note], phase: str, status: int = 2) -> None:
        super().__init__("; ".join(n.message for n in notes))
        self.notes, self.phase, self.status = list(notes), phase, status


class _Jobview:
    """The job protocol optable.unmapped_notes reads, over a slice of job.unmapped."""

    def __init__(self, unmapped) -> None:
        self.unmapped = list(unmapped)


class _Run:
    def __init__(self, job: SpectreJob, opts: dict) -> None:
        self.job, self.opts = job, opts
        self.start = datetime.now()
        self.sig = _Signals()
        self.log: Optional[SpectreLog] = None
        self.run_dir: Optional[str] = None
        self.phase = PHASE_SETUP
        self.quiet = False                               # -V/-W alone: no trailer
        self.finished = False                            # the trailer is out
        self.strict_on = bool(opts.get("strict"))
        self.keep = bool(opts.get("keep"))
        self.verbose = bool(opts.get("verbose")) or bool(os.environ.get("VAMOS_VERBOSE"))
        self.legacy = opts.get("psf_names") == "legacy"
        self.codes: Dict[str, str] = {}
        self.engine = "vacask"
        self._instances: Optional[Dict[str, object]] = None
        self._solution: Optional[List[Tuple[tuple, str]]] = None
        self.progress = ""                               # the SIGUSR1 status line's progress text

    # -- the frame -------------------------------------------------------------------------------

    def main(self) -> int:
        self.sig.install()
        try:
            try:
                return self.guarded()
            except _Interrupted as e:        # a stop signal in the instant before the quiet state
                self.sig.state = "quiet"
                if self.finished or self.quiet:
                    return 3
                self.message(warning("", "the run was stopped by %s (§2.7)" % _signal_name(e.signum)), self.phase)
                return self.end(3)
        finally:
            self.sig.restore()

    def guarded(self) -> int:
        try:
            status = self.steps()
            self.sig.state = "quiet"
        except _Interrupted as e:
            self.sig.state = "quiet"
            self.message(warning("", "the run was stopped by %s (§2.7)" % _signal_name(e.signum)), self.phase)
            status = 3
        except stop.Stop as e:
            self.sig.state = "quiet"
            self.message(warning("", "stopped by a second %s: the engine was killed and nothing is converted "
                                     "(§2.7)" % _signal_name(e.signum)), self.phase)
            status = 3
        except _Fail as f:
            self.sig.state = "quiet"
            self.say(f.notes, f.phase, use_strict=False)
            status = f.status
        except NoteError as e:
            self.sig.state = "quiet"
            self.say(e.notes, self.phase, use_strict=False)
            if not has_errors(e.notes):
                self.message(error("", "the %s stopped" % self.phase), self.phase)
            status = 2
        except (ScanError, tools.ToolError) as e:
            self.sig.state = "quiet"
            self.message(error("", str(e)), self.phase)
            status = 2
        except Exception as e:                                       # noqa: BLE001  (§2.7: never raises)
            self.sig.state = "quiet"
            self.internal(e)
            status = 2
        if self.quiet:
            return status
        return self.end(status)

    def ensure_log(self) -> SpectreLog:
        if self.log is None:
            job = self.job
            screen = dataclasses.replace(job, log_mode="screen", log_path=None,
                                         defaults={k: v for k, v in job.defaults.items()
                                                   if k not in ("log_mode", "log_path")})
            self.log = SpectreLog(screen, banner.Banner(banner.load_profile("spectre"), "spectre"), self.start)
        return self.log

    def message(self, n: Note, phase: str) -> None:
        self.ensure_log().message(n, phase)

    def say(self, notes: Iterable[Note], phase: str, use_strict: bool = True) -> bool:
        """Print notes as Spectre messages (warnings as errors under --vamos-strict before the
        engine runs, §0); True when one is an error."""
        ns = list(notes)
        if use_strict:
            ns = strict(ns, self.strict_on)
        log = self.ensure_log()
        for n in ns:
            log.message(n, phase)
        return has_errors(ns)

    def poll(self) -> None:
        """The flags the handlers set (§2.7): SIGUSR1 prints the status line (with the result being
        written during the conversion), SIGUSR2 the note."""
        for signum in self.sig.take_flags():
            if signum == _SIGUSR1:
                self.ensure_log().status_line(self.progress)
            else:
                self.message(note("", "checkpoint not supported"), self.phase)

    def internal(self, e: BaseException) -> None:
        """§2.7: one line on the screen; the traceback in the run directory, else the log file,
        else a temporary file the line names."""
        text = traceback.format_exc()
        log = self.ensure_log()
        where = None
        if self.run_dir is not None and os.path.isdir(self.run_dir):
            path = os.path.join(self.run_dir, TRACEBACK_NAME)
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)
                where = path
            except OSError:
                where = None
        if where is None and log.path is not None and log.file is not None:
            log.file_only(text)
            where = log.path
        if where is None:
            fh = tempfile.NamedTemporaryFile("w", prefix="vamos-spectre-", suffix=".traceback", delete=False)
            with fh:
                fh.write(text)
            where = fh.name
        log.message(error("", "internal error: %s: %s (the traceback is in %s)" % (type(e).__name__, e, where)),
                    PHASE_INTERNAL)

    def end(self, status: int) -> int:
        """Step 14: the run directory (§1) and the trailer (§8.7)."""
        self.sig.state = "quiet"
        log = self.ensure_log()
        d = self.run_dir
        if d is not None and os.path.isdir(d):
            try:
                if not os.listdir(d):
                    os.rmdir(d)
                elif status == 0 and not self.keep:
                    shutil.rmtree(d)
                else:
                    log.line("run directory kept: %s" % d)
            except OSError as e:
                log.message(warning("", "cannot remove the run directory %s: %s" % (d, e)), PHASE_SETUP)
        self.poll()
        self.finished = True
        log.finish(status)
        return status

    # -- the steps -------------------------------------------------------------------------------

    def steps(self) -> int:
        job, opts = self.job, self.opts
        self.codes = percent.codes(job, self.start)
        self.make_log_dir()
        self.log = personality.open_log(job, opts, self.start)          # step 1b: the one log opening
        log = self.log
        if job.action in ("version", "subversion"):
            log.line(log.version_text(job.action))
            if job.netlist is None and not has_errors(job.notes):
                log.close()
                self.quiet = True
                return 0
        engine, why = self.choose_engine()
        self.provenance(engine)
        status = personality.report(job, opts, log)
        notes_seen, unmapped_seen = len(job.notes), len(job.unmapped)
        if status:
            return status
        if why:
            raise _Fail([error("", why)], PHASE_SETUP)
        self.engine = engine
        log.run_start()
        self.poll()

        # step 2: the run directory, beside the results path argv and the defaults give (§1, §2.5)
        fmt0 = sargs.effective(job, "fmt") or "psfascii"
        raw0 = self.raw_path(None)
        self.remove_stale(raw0, fmt0)
        self.make_run_dir(raw0)

        # step 3: the input
        self.phase = PHASE_READ
        if job.netlist is None:
            path = os.path.join(self.run_dir, STDIN_NAME)               # type: ignore[arg-type]
            self.copy_stdin(path)
            origin, top_name, top_dir = STDIN_NAME, STDIN_NAME, job.cwd
        else:
            path = job.netlist
            origin = os.path.normpath(os.path.join(job.cwd, path))
            top_name = top_dir = None
            if not os.path.isfile(origin):
                raise _Fail([error(job.netlist, "netlist %s not found" % job.netlist)], PHASE_READ)
        text = _read_text(path if os.path.isabs(path) else os.path.join(job.cwd, path))
        self.poll()

        # step 4: cpp (§2.6)
        cpp_ran = sargs.cpp_runs(job)
        title: Optional[str] = None
        parse_path = path
        if cpp_ran:
            if self.verbose:
                log.line(" ".join(shlex.quote(c) for c in scpp.command(path, job, self.run_dir, self.cpp_exe() or "cpp")))
            out, title, cnotes = scpp.preprocess(path, job, self.run_dir)   # NoteError, ToolError
            stop_now = self.say(cnotes, PHASE_READ)
            if text is not None:
                stop_now = self.say(scpp.macro_warnings(text, origin, job.defines), PHASE_READ) or stop_now
            if stop_now:
                return 2
            parse_path = out
        elif text is not None:
            if self.say(scpp.directive_errors(text, origin), PHASE_READ):
                return 2
        self.poll()

        # step 5: the netlist
        popts = nspectre.SpectreParseOpts(
            search=list(job.incdirs), percent=dict(self.codes), pre=list(job.pre_config), post=list(job.config),
            cpp_markers=cpp_ran, title=title, mts=bool(sargs.effective(job, "mts")), va_include=_va_include(),
            va_defines=list(job.va_defines), top_dir=top_dir, top_name=top_name)
        nl = nspectre.parse(parse_path, job.cwd, popts)

        # step 6: the merged settings (§2.2); the netlist's maxwarns/maxnotes govern its own messages
        settings = job_settings(job, nl)
        log.limits(settings)
        late = job.notes[notes_seen:] + unmapped_notes(_Jobview(job.unmapped[unmapped_seen:]))
        stop_now = self.say(nl.notes, PHASE_READ)
        if self.say(late, PHASE_READ) or stop_now or has_errors(job.notes):
            return 2
        self.poll()
        self.inventory(nl)

        # step 7: the engine's tools; the dispositions that depend on the engine (§2.3, §2.8)
        self.phase = PHASE_SETUP
        probs = engines.problems(engine)
        if probs:
            raise _Fail([error("", p) for p in probs], PHASE_SETUP)
        if self.say(self.engine_notes(nl), PHASE_SETUP):
            return 2

        # step 8: the plan (§5)
        try:
            pl = nplan.build(nl, engine, settings)
        except NoteError as e:
            raise _Fail(_without_duplicate_option_notes(nl.notes, e.notes), PHASE_SETUP)
        stop_now = self.say(_without_duplicate_option_notes(nl.notes, pl.notes), PHASE_SETUP)
        actions = [a.args.get("note") for a in pl.actions if a.op == "note"]
        actions = _without_duplicate_info_notes(nl.notes, [n for n in actions if isinstance(n, Note)])
        stop_now = self.say(actions, PHASE_SETUP) or stop_now
        if stop_now:
            return 2
        self.poll()

        # step 9: the signals (§6)
        self.absolute_state_files(pl)
        nl2, sigmap, snotes = nsignals.resolve(nl, pl)
        if self.say(snotes, PHASE_SETUP):
            return 2
        steps = [a.step for a in pl.actions if a.op == "analysis" and a.step is not None]
        if not steps:                                    # plan.build: "nothing to run"
            return 0
        self.poll()

        # step 11: the engine
        raw = self.raw_path(settings.raw if (job.raw is None and "rawfile" in nl.options) else None)
        self.prepare_results(raw, settings.fmt, raw0, fmt0)
        er = self.engine_run(nl2, pl, sigmap)

        # steps 12-13: the results
        return self.convert(nl, nl2, pl, sigmap, er, settings, raw, steps)

    # -- step 1b ---------------------------------------------------------------------------------

    def make_log_dir(self) -> None:
        """A +log/=log file named without a / goes into -outdir (§2.5): create -outdir for it."""
        job = self.job
        mode = sargs.effective(job, "log_mode")
        lp = sargs.effective(job, "log_path")
        outdir = self.outdir()
        if mode in ("both", "file") and lp and outdir:
            p = percent.expand(str(lp), self.codes)
            if not os.path.isabs(p) and "/" not in p and os.sep not in p:
                try:
                    os.makedirs(outdir, exist_ok=True)
                except OSError:
                    pass                                 # open_log reports the log it cannot open

    def choose_engine(self) -> Tuple[str, Optional[str]]:
        """--vamos-analog > VAMOS_ANALOG > vacask (§1 step 7); (engine, the error text or None).  The
        choice is made here because the provenance header names the engine."""
        try:
            return engines.choose_engine(self.opts), None
        except ValueError as e:
            return "", str(e)

    def engine_notes(self, nl) -> List[Note]:
        """§2.3: -va,define is noted on Xyce; §2.8, for a netlist with Verilog-A: CDS_VLOGA_INCLUDE is
        noted on Xyce (its .hdl compile gets no include directories), and a CDS_AHDL_* setting that
        changes results is a warning when it is not Spectre's documented default."""
        out: List[Note] = []
        job = self.job
        if self.engine == "xyce" and job.va_defines:
            out.append(note("", "-va,define %s: not applied on Xyce, whose .hdl compile takes no macro "
                                "definitions (§2.3)" % " ".join(job.va_defines)))
        if not nl.hdl:
            return out
        inc = os.environ.get("CDS_VLOGA_INCLUDE", "")
        if self.engine == "xyce" and inc.strip():
            out.append(note("", "CDS_VLOGA_INCLUDE=%s: not applied on Xyce, whose .hdl compile gets no include "
                                "directories and resolves an `include beside the .va file (§2.8)" % inc))
        for var in sorted(_AHDL_DEFAULTS):
            val = os.environ.get(var)
            if val is None or not val.strip() or _ahdl_default(var, val):
                continue
            out.append(warning("", "%s=%s: not applied; a Verilog-A setting that changes results (§2.8)" % (var, val)))
        return out

    def cpp_exe(self) -> Optional[str]:
        try:
            return tools.find_real("cpp")
        except tools.ToolError:
            return None

    def provenance(self, engine: str) -> None:
        """§1: banner.provenance over vamos, the engine rows and cpp when it runs."""
        used = [("vamos", tools.version_of("vamos", tools.launcher()), tools.launcher())]
        if engine:
            xy = None
            if engine == "xyce":
                xy = engines.xyce_bin()
                if not xy:
                    try:
                        xy = tools.find_real("Xyce")
                    except tools.ToolError:
                        xy = None
            for name, p in engines.tool_rows(engine, xyce=xy):
                used.append((name, tools.version_of(name, p), p))
        if sargs.cpp_runs(self.job):
            cpp = self.cpp_exe()
            if cpp:
                used.append(("cpp", tools.version_of("cpp", cpp), cpp))
        self.ensure_log().line(banner.provenance(used, "spectre"))

    # -- step 2 ----------------------------------------------------------------------------------

    def outdir(self) -> Optional[str]:
        d = sargs.effective(self.job, "outdir")
        if not d:
            return None
        return os.path.normpath(os.path.join(self.job.cwd, percent.expand(str(d), self.codes)))

    def raw_path(self, netlist_raw: Optional[str]) -> str:
        """The results path (§2.5): argv's -raw, else the netlist's options rawfile= (netlist_raw,
        %-expanded by the parser), else the defaults' -raw, else %C:r.raw, which -outdir moves when it
        has no /.  Relative paths resolve against the cwd."""
        job = self.job
        if job.raw is not None:
            p = percent.expand(job.raw, self.codes)
        elif netlist_raw:
            p = netlist_raw
        elif job.defaults.get("raw"):
            p = percent.expand(str(job.defaults["raw"]), self.codes)
        else:
            p = percent.expand("%C:r.raw", self.codes)
            outdir = self.outdir()
            if outdir and "/" not in p and os.sep not in p:
                return os.path.normpath(os.path.join(outdir, p))
        return os.path.normpath(os.path.join(job.cwd, p))

    def nutmeg_file(self, raw: str) -> str:
        """§8.5: the nutmeg file is the results path, or <dir>/%C:r:t.raw when it is a directory."""
        if os.path.isdir(raw):
            return os.path.join(raw, percent.expand("%C:r:t", self.codes) + ".raw")
        return raw

    def remove_stale(self, raw: str, fmt: str) -> None:
        """§1: an earlier run's <raw>/logFile (psf) or nutmeg file is removed before the input is read."""
        target = os.path.join(raw, "logFile") if fmt == "psfascii" else self.nutmeg_file(raw)
        if not os.path.isfile(target):
            return
        try:
            os.remove(target)
        except FileNotFoundError:
            pass
        except OSError as e:
            raise _Fail([error("", "cannot remove the earlier run's %s: %s" % (target, e.strerror or e))], PHASE_SETUP)

    def make_run_dir(self, raw: str) -> None:
        parent = os.path.dirname(raw) or self.job.cwd
        prefix = percent.expand("%C:r:t", self.codes) + ".run."
        try:
            os.makedirs(parent, exist_ok=True)
            self.run_dir = tempfile.mkdtemp(prefix=prefix, dir=parent)
        except OSError as e:
            raise _Fail([error("", "cannot create the run directory in %s: %s" % (parent, e.strerror or e))],
                        PHASE_SETUP)

    # -- step 3 ----------------------------------------------------------------------------------

    def copy_stdin(self, path: str) -> None:
        data = _read_stdin()
        try:
            with open(path, "wb") as fh:
                fh.write(data)
        except OSError as e:
            raise _Fail([error(STDIN_NAME, "cannot copy the standard input to %s: %s" % (path, e.strerror or e))],
                        PHASE_READ)

    # -- step 6 ----------------------------------------------------------------------------------

    def instances(self, nl) -> Dict[str, object]:
        """IR path -> Instance over every instance path, Cond resolved (tables.path_envs)."""
        if self._instances is None:
            out: Dict[str, object] = {}
            try:
                for pe in tables.path_envs(nl, strict=False, dialect="spectre"):
                    for it in pe.items:
                        out.setdefault(("%s.%s" % (pe.path, it.name)) if pe.path else it.name, it)
            except Exception:                                    # noqa: BLE001  (a best-effort index)
                pass
            self._instances = out
        return self._instances

    def solution_entries(self, nl) -> List[Tuple[tuple, str]]:
        """The full solution of §5.4 as (Ref, Spectre name): every net of the flattened circuit (IR
        paths, `.` hierarchy; a subckt port is its caller's net; ground and vamos's own nodes left
        out), then every vsource's current (`<path>:p`), each group in signals.resolve's allpub order."""
        if self._solution is None:
            nets: Dict[str, None] = {}
            currents: Dict[str, None] = {}
            try:
                for pe in tables.path_envs(nl, strict=False, dialect="spectre"):
                    ports = set(pe.subckt.ports) if pe.subckt is not None else set()
                    for it in pe.items:
                        for n in getattr(it, "nodes", ()):
                            if n == "0" or (pe.subckt is not None and n in ports and n not in nl.globals):
                                continue
                            g = n if (pe.subckt is None or n in nl.globals) else "%s.%s" % (pe.path, n)
                            if not any(part.startswith("vamos_") for part in g.split(".")):
                                nets.setdefault(g)
                        if (getattr(it, "kind", "") == "v" and getattr(it, "prim", "") == "vsource"
                                and not it.name.startswith("vamos_")):
                            currents.setdefault(it.name if not pe.path else "%s.%s" % (pe.path, it.name))
            except Exception:                                    # noqa: BLE001  (the saved signals remain)
                nets, currents = {}, {}
            order = lambda s: (s.lower(), s)                      # noqa: E731  (signals' allpub order)
            self._solution = ([(("v", g), g) for g in sorted(nets, key=order)]
                              + [(("i", c), c + ":p") for c in sorted(currents, key=order)])
        return self._solution

    def inventory(self, nl) -> None:
        """§8.7's circuit inventory: the nets, then each primitive's instance count."""
        counts: Dict[str, int] = {}
        nets = set()
        try:
            subckts = _subckt_names(nl)
            for pe in tables.path_envs(nl, strict=False, dialect="spectre"):
                ports = set(pe.subckt.ports) if pe.subckt is not None else set()
                for it in pe.items:
                    for n in it.nodes:
                        if n == "0":
                            continue
                        if pe.subckt is None or n in nl.globals:
                            nets.add(n)
                        elif n not in ports:
                            nets.add("%s.%s" % (pe.path, n))
                    if it.kind == "x" and it.master in subckts:
                        continue
                    key = it.prim or it.master or it.kind
                    counts[key] = counts.get(key, 0) + 1
        except Exception:                                        # noqa: BLE001  (the inventory is cosmetic)
            return
        ordered: Dict[str, int] = {"nodes": len(nets)}
        for k in sorted(counts, key=str.lower):
            ordered[k] = counts[k]
        self.ensure_log().inventory(ordered)

    # -- step 9 ----------------------------------------------------------------------------------

    def absolute_state_files(self, pl) -> None:
        """signals.resolve reads readns/readic/read relative to the process cwd (S2): the names are
        made absolute against the job's cwd first, so the run does not depend on the process cwd."""
        for a in pl.actions:
            st = a.step
            if st is None:
                continue
            for key in ("readns", "readic", "read"):
                v = st.args.get(key)
                if isinstance(v, str) and v and not os.path.isabs(v):
                    st.args[key] = os.path.normpath(os.path.join(self.job.cwd, v))

    # -- step 11 ---------------------------------------------------------------------------------

    def prepare_results(self, raw: str, fmt: str, raw0: str, fmt0: str) -> None:
        """The results path the run writes (an options rawfile=/rawfmt= may have moved it): its stale
        index removed too, and the directory created, so a bad path stops the run before the engine."""
        if (raw, fmt) != (raw0, fmt0):
            self.remove_stale(raw, fmt)
        try:
            if fmt == "psfascii":
                if os.path.exists(raw) and not os.path.isdir(raw):
                    raise _Fail([error("", "the results path %s is a file; the psf results are a directory "
                                           "(§2.5)" % raw)], PHASE_SETUP)
                os.makedirs(raw, exist_ok=True)
            else:
                target = self.nutmeg_file(raw)
                if target != raw:
                    self.message(note("", "%s is a directory: the %s results are written to %s (§8.5)"
                                      % (raw, fmt, target)), PHASE_SETUP)
                os.makedirs(os.path.dirname(target), exist_ok=True)
        except OSError as e:
            raise _Fail([error("", "cannot create the results directory for %s: %s" % (raw, e.strerror or e))],
                        PHASE_SETUP)

    def engine_run(self, nl2, pl, sigmap) -> results.EngineResult:
        """§1 step 11 through vamos.spectre.run_<engine>.run (§10), given signals.resolve's render
        copy.  The runner owns the signals while it runs (proc.Interrupts) and raises stop.Stop for a
        second one (stop.py); it prints the analysis banners, the engine's messages and the failed
        and interrupted analyses (§8.7).  A runner that takes the additive `strict` keyword gets
        --vamos-strict (render's warnings as errors)."""
        mod = importlib.import_module("vamos.spectre.run_%s" % self.engine)
        fn = mod.run
        kw = {}
        try:
            if "strict" in inspect.signature(fn).parameters:
                kw["strict"] = self.strict_on
        except (TypeError, ValueError):
            pass
        log = self.ensure_log()
        er = fn(nl2, pl, sigmap, self.run_dir, self.job, log, **kw)
        # the runner's proc.Interrupts gave the signals back: from here on the conversion rule holds
        # (§2.7).  An engine run that ended with status 3 was stopped by a signal, which counts as the
        # first one: the next stop signal is the second, and ends the conversion at once (§2.7: "or at
        # a second signal, which also makes vamos exit 3 at once without converting")
        self.sig.state = "deferred"
        if isinstance(er, results.EngineResult) and er.rc == 3:
            self.sig.count = max(self.sig.count, 1)
        if not isinstance(er, results.EngineResult):
            raise TypeError("run_%s.run returned %r, not an EngineResult" % (self.engine, er))
        er.files = {k: (v if os.path.isabs(v) else os.path.join(self.run_dir, v))   # type: ignore[arg-type]
                    for k, v in er.files.items()}
        log.analysis = ""                                # the conversion's messages belong to no analysis
        self.poll()
        return er

    # -- steps 12-13 -----------------------------------------------------------------------------

    def convert(self, nl, nl2, pl, sigmap, er: results.EngineResult, settings: Settings, raw: str,
                steps: List[nplan.AnalysisStep]) -> int:
        job = self.job
        self.sig.state = "deferred"
        cnotes: List[Note] = []
        status = er.rc if er.rc in (0, 1, 2, 3) else 2
        if er.rc != 0:
            # stop.py: a step with no status is one the engine never reached (a refused deck, the steps
            # after a crash or an interrupt); it has no output, and the runner said why: no warning
            unreached = {st.id: "failed" for st in steps if st.id not in er.status}
            if unreached:
                er = dataclasses.replace(er, status=dict(er.status, **unreached))
        masters = self.masters(nl2, sigmap)
        prec_text = _opt_text(settings.options.get("precision"))
        head = psf.PsfHead(version=VERSION, date=psf.psf_date(self.start), design=nl.title,
                           precision=psf.precision(prec_text, cnotes, ""))
        index = _StepIndex(steps, self.legacy)
        save_mode = (_opt_text(nl.options.get("save")) or "selected").lower()
        stream = (r for r in results.collect(pl, sigmap, er, "legacy" if self.legacy else "modern",
                                             masters=masters, notes=cnotes)
                  if not _no_output(index.step_of(r), save_mode))
        conv = _Conversion(self, raw, head, cnotes, masters, index, sigmap, nl2)
        conv.er, conv.pl = er, pl
        if settings.fmt == "psfascii":
            conv.psf(stream)
        else:
            conv.nutmeg(stream, settings.fmt == "nutbin", nl.title)
        self.sig.state = "quiet"
        self.progress = ""
        self.phase = PHASE_SETUP
        if self.sig.signum is not None:
            self.message(warning("", "the conversion was stopped by %s: the analyses converted before it are "
                                     "written%s (§2.7)" % (_signal_name(self.sig.signum),
                                                          " and listed in logFile" if settings.fmt == "psfascii"
                                                          else "")), PHASE_SETUP)
        if conv.failed_results and status == 0:
            status = 1
        if any(er.status.get(st.id) == "failed" for st in steps) and status == 0:
            status = 1
        if conv.write_error:
            status = max(status, 2) if status != 3 else 3
        if self.sig.signum is not None:
            status = 3
        return status

    def masters(self, nl2, sigmap) -> Dict[str, str]:
        """§8.4: a noise STRUCT type name -> its instance's master (the primitive, or the Verilog-A
        module), built from the IR per noise instance."""
        out: Dict[str, str] = {}
        insts = self.instances(nl2)
        for entries in sigmap.per_step.values():
            for ref, _name, ptype in entries:
                if ref and ref[0] == "noise" and ptype not in out:
                    it = insts.get(ref[1])
                    if it is not None:
                        master = getattr(it, "prim", "") or getattr(it, "master", None) or getattr(it, "kind", "")
                        if master:
                            out[ptype] = master
        return out

    def state_path(self, name: str) -> str:
        """§2.5: a relative state-file name resolves against the cwd, or -outdir when it has no /."""
        if os.path.isabs(name):
            return name
        outdir = self.outdir()
        if outdir and "/" not in name and os.sep not in name:
            return os.path.join(outdir, name)
        return os.path.normpath(os.path.join(self.job.cwd, name))


class _Conversion:
    """Steps 12-13: the results written in blocks, with what the flow adds outside collect."""

    def __init__(self, run: _Run, raw: str, head: psf.PsfHead, cnotes: List[Note], masters: Dict[str, str],
                 index: "_StepIndex", sigmap, nl2) -> None:
        self.r, self.raw, self.head, self.cnotes, self.masters = run, raw, head, cnotes, masters
        self.index, self.sigmap, self.nl2 = index, sigmap, nl2
        self.er: Optional[results.EngineResult] = None   # the engine run and the plan (the state files)
        self.pl: Optional[nplan.RunPlan] = None
        self.seen = 0
        self.failed_results = False
        self.write_error = False

    def flush(self, phase: str) -> None:
        new = self.cnotes[self.seen:]
        self.seen = len(self.cnotes)
        self.r.say(new, phase, use_strict=False)

    def begin(self, res: results.AnalysisResult) -> None:
        phase = _result_phase(res)
        self.flush(phase)
        self.r.phase = phase
        self.r.progress = "writing the results of `%s'" % psf.analysis_name(res)

    def psf(self, stream: Iterable[results.AnalysisResult]) -> None:
        """One file per result, then the logFile listing the complete blocks.  A first stop signal
        ends the conversion before the next file, a second one at once (its file dropped); a block
        that is not complete is not listed, so every parent lists exactly the leaves that exist
        (§8.2); the files written stay."""
        job, log = self.r.job, self.r.ensure_log()
        listed: List[results.AnalysisResult] = []
        done: List[results.AnalysisResult] = []
        current: Optional[results.AnalysisResult] = None
        prev_top = False
        try:
            for res in stream:
                starts, prev_top = _starts_block(res, prev_top, self.r.legacy)
                if starts:
                    listed.extend(done)
                    done = []
                if self.r.sig.signum is not None:                    # a first signal: no further file
                    break
                self.begin(res)
                current = res
                self.track(res)
                psf.write_analysis(self.raw, res, self.head, escchars=job.escchars, notes=self.cnotes,
                                   masters=self.masters)
                done.append(res)
                current = None
                self.after(res)
            else:
                listed.extend(done)
                done = []
        except _Interrupted:
            self.r.sig.state = "quiet"
            if current is not None:
                _remove(os.path.join(self.raw, current.file + ".vamos-tmp"))
        except OSError as e:
            if current is not None:
                _remove(os.path.join(self.raw, current.file + ".vamos-tmp"))
            self.write_error = True
            self.r.message(error("", "cannot write the results in %s: %s" % (self.raw, e.strerror or e)), PHASE_SETUP)
        self.r.sig.state = "quiet"                       # the logFile is written whatever comes now
        self.flush(PHASE_SETUP)
        try:
            psf.write_logfile(self.raw, listed, self.head, escchars=job.escchars)
        except OSError as e:
            self.write_error = True
            self.r.message(error("", "cannot write %s: %s" % (os.path.join(self.raw, "logFile"), e.strerror or e)),
                           PHASE_SETUP)
        log.analysis = ""

    def nutmeg(self, stream: Iterable[results.AnalysisResult], binary: bool, title: str) -> None:
        path = self.r.nutmeg_file(self.raw)
        written: List[results.AnalysisResult] = []

        def plots() -> Iterator[results.AnalysisResult]:
            for res in stream:
                if self.r.sig.signum is not None:            # a first signal: the file ends here
                    return
                self.begin(res)
                self.track(res)
                written.append(res)
                yield res
                self.after(res, post=False)
        try:
            nutmeg.write(path, plots(), binary, notes=self.cnotes, title=title)
        except _Interrupted:
            self.r.sig.state = "quiet"
            _remove(path + ".vamos-tmp")
            written = []
        except OSError as e:
            _remove(path + ".vamos-tmp")
            self.write_error = True
            self.r.message(error("", "cannot write %s: %s" % (path, e.strerror or e)), PHASE_SETUP)
            written = []
        self.r.sig.state = "quiet"
        self.flush(PHASE_SETUP)
        for res in written:
            self.post(res)

    def track(self, res: results.AnalysisResult) -> None:
        """A data result collect could not fill (a missing or unreadable output, a failed point) makes
        the status at least 1 (§2.7); the runner reported the engine's side of it."""
        if res.atype not in _PARENT_KINDS and res.status == "failed":
            self.failed_results = True

    def after(self, res: results.AnalysisResult, post: bool = True) -> None:
        self.flush(_result_phase(res))
        if post:
            self.post(res)
        self.r.poll()

    # -- what collect leaves to the flow: dc print=yes and the state files (S4's seam)

    def post(self, res: results.AnalysisResult) -> None:
        if res.atype in _PARENT_KINDS or res.status == "failed":
            return
        st = self.index.step_of(res)
        if st is None:
            return
        phase = _result_phase(res)
        if st.kind in ("op", "dc") and _yes(st.args.get("print")):
            self.print_nodes(res, phase)
        for key, which in (("write", 0), ("writefinal", -1)):
            name = st.args.get(key)
            if isinstance(name, str) and name:
                self.state_file(res, st, key, name, which, phase)

    def print_nodes(self, res: results.AnalysisResult, phase: str) -> None:
        """dc print=yes: the node voltages in the log, per point of a dc sweep [M ref19 p.43]."""
        log = self.r.ensure_log()
        nodes = [k for k, s in enumerate(res.signals) if s.ptype == "V" and s.members is None]
        log.line("Node voltages of `%s' (print=yes):" % psf.analysis_name(res))
        try:
            if res.sweep is None:
                for k in nodes:
                    log.line("    %s = %s" % (res.signals[k].name, si(_real(res.signals[k].values[0]), "V")))
                return
            if res.rows is None:
                return
            for x, vals in res.rows():
                log.line("  %s = %s" % (res.sweep[0], si(_real(x), res.sweep[1])))
                for k in nodes:
                    log.line("    %s = %s" % (res.signals[k].name, si(_real(vals[k]), "V")))
        except (OSError, ValueError, IndexError) as e:
            self.r.message(warning("", "print=yes: the node voltages of `%s' cannot be read: %s"
                                   % (psf.analysis_name(res), e)), phase)

    def full_solution(self, st, which: int) -> Optional[Tuple[Dict[str, float], Dict[str, float]]]:
        """§5.4: a write=/writefinal= step saves every node in the engine (VACASK `save default`, Xyce
        V(*) plus the V and L currents): its nets (IR paths, `.` hierarchy) and vsource currents
        (`<path>:p`) at the first (0) or last (-1) point, read from the engine output through the
        engine's own column names (names_for); None when there is no engine output to read."""
        er = self.er
        path = er.files.get(st.id) if er is not None else None
        if not path or not os.path.isfile(path) or path.endswith(".prn"):
            return None
        entries = self.r.solution_entries(self.nl2)
        sm = nsignals.SignalMap(per_step={st.id: [(ref, nm, "V" if ref[0] == "v" else "I") for ref, nm in entries]})
        mod = importlib.import_module("vamos.netlist.%s" % self.r.engine)
        try:
            names = mod.names_for(self.nl2, self.pl, sm).get(st.id, {})
        except (tables.TableError, ValueError):
            return None
        plots = results._raw_plots(path)                  # noqa: SLF001  (the streamed reader of collect)
        if not plots:
            return None
        plot = plots[0]
        cols: List[int] = []
        found: List[Tuple[tuple, str]] = []
        for ref, nm in entries:
            col = names.get(ref)
            if col is None:
                continue
            try:
                cols.append(plot.exact(col))
            except KeyError:
                continue
            found.append((ref, nm))
        nodes: Dict[str, float] = {}
        currents: Dict[str, float] = {}
        if not cols:
            return nodes, currents
        row = None
        for r in plot.rows(cols):
            row = r
            if which == 0:
                break
        if row is None:
            return None
        for (ref, nm), v in zip(found, row):
            (nodes if ref[0] == "v" else currents)[nm] = _real(v)
        return nodes, currents

    def state_file(self, res: results.AnalysisResult, st, key: str, name: str, which: int, phase: str) -> None:
        """§8.6: write= takes the first point and writefinal= the last: nets, then vsource currents."""
        if st.kind not in _STATE_KINDS:
            self.r.message(warning("", "%s %s: %s=%s is not written: only dc and tran solutions are"
                                   % (st.kind, st.name, key, name)), phase)
            return
        if which == -1 and res.status == "interrupted":
            self.r.message(warning("", "%s %s: %s=%s is not written: the analysis was interrupted"
                                   % (st.kind, st.name, key, name)), phase)
            return
        try:
            vals = _point(res, which)
        except (OSError, ValueError) as e:
            self.r.message(warning("", "%s %s: %s=%s is not written: %s" % (st.kind, st.name, key, name, e)), phase)
            return
        if vals is None:
            self.r.message(warning("", "%s %s: %s=%s is not written: the analysis left no point"
                                   % (st.kind, st.name, key, name)), phase)
            return
        # §5.4, §8.6: the full solution the engine saved for this step (every net, every vsource
        # current), then whatever the PSF selection adds that the engine output did not name
        nodes: Dict[str, float] = {}
        currents: Dict[str, float] = {}
        try:
            full = self.full_solution(st, which)
        except (OSError, ValueError, KeyError) as e:
            full = None
            self.r.message(warning("", "%s %s: %s=%s: the full solution cannot be read (%s); the saved signals "
                                   "are written" % (st.kind, st.name, key, name, e)), phase)
        if full is not None:
            nodes.update(full[0])
            currents.update(full[1])
        refs = {sname: ref for ref, sname, _t in self.sigmap.per_step.get(st.id, [])}
        insts = self.r.instances(self.nl2)
        for sig, v in zip(res.signals, vals):
            if sig.members is not None:
                continue
            if sig.ptype == "V":
                nodes.setdefault(sig.name, _real(v))
            elif sig.ptype == "I":
                ref = refs.get(sig.name)
                it = insts.get(ref[1]) if ref and ref[0] == "i" and len(ref) > 1 else None
                if it is not None and getattr(it, "kind", "") == "v" and getattr(it, "prim", "") == "vsource":
                    currents.setdefault(sig.name, _real(v))
        path = self.r.state_path(name)
        circuit = self.r.job.netlist if self.r.job.netlist is not None else STDIN_NAME
        try:
            statefile.write(path, nodes, currents, circuit, st.name, statefile.state_date(datetime.now()))
        except OSError as e:
            self.write_error = True
            self.r.message(error("", "%s %s: cannot write the state file %s: %s"
                                 % (st.kind, st.name, path, e.strerror or e)), phase)


# =============================================================================================
# Helpers
# =============================================================================================

class _StepIndex:
    """The AnalysisStep of a result (collect does not name it): an unswept analysis by its key, a
    sweep leaf by its top sweep's name and the longest step name that ends it (§8.1)."""

    def __init__(self, steps: Sequence[nplan.AnalysisStep], legacy: bool) -> None:
        self.unswept: Dict[str, nplan.AnalysisStep] = {}
        self.swept: List[Tuple[nplan.AnalysisStep, str]] = []
        self.sep = "_" if legacy else "-"
        for st in steps:
            atype = "dc" if st.kind in ("op", "dc") else st.kind
            if st.context:
                self.swept.append((st, atype))
            else:
                self.unswept.setdefault("%s-%s" % (st.name, atype), st)

    def step_of(self, res: results.AnalysisResult) -> Optional[nplan.AnalysisStep]:
        if res.atype in _PARENT_KINDS:
            return None
        if not res.parent and res.key in self.unswept:
            return self.unswept[res.key]
        name = psf.analysis_name(res)
        best: Optional[nplan.AnalysisStep] = None
        for st, atype in self.swept:
            if atype != res.atype:
                continue
            if name.startswith(st.context[0].name + self.sep) and name.endswith("_" + st.name):
                if best is None or len(st.name) > len(best.name):
                    best = st
        return best


def _starts_block(res: results.AnalysisResult, prev_top_parent: bool, legacy: bool) -> Tuple[bool, bool]:
    """(whether res starts a block, whether it is a top-level parent).  A block is the unit the
    logFile lists after a stop (§8.2): an unswept analysis, or a whole sweep block (its parents, then
    its points, as collect yields them); the legacy names have no parents, so there each result is
    one (§8.1)."""
    top_parent = res.atype in _PARENT_KINDS and not res.parent
    unswept = res.atype not in _PARENT_KINDS and not res.parent
    return (legacy or unswept or (top_parent and not prev_top_parent)), top_parent


def _without_duplicate_option_notes(parser_notes: Sequence[Note], plan_notes: Sequence[Note]) -> List[Note]:
    """§14 q.54: plan.build's message about an options-statement key the parser already reported
    (`options <key>=...`) is dropped; the parser's is kept.  Errors are never dropped."""
    keys = set()
    for n in parser_notes:
        m = _PARSER_OPTION.match(n.message)
        if m and n.severity != ERROR:
            keys.add(m.group(1).lower())
    out = []
    for n in plan_notes:
        m = _PLAN_OPTION.match(n.message) if not n.origin else None
        if m and n.severity != ERROR and m.group(1).lower() in keys:
            continue
        out.append(n)
    return out


def _without_duplicate_info_notes(parser_notes: Sequence[Note], plan_notes: Sequence[Note]) -> List[Note]:
    """The q.54 rule for info statements: the parser notes `<name> info: the info file is not written
    (§3.6)` and plan.build's note action `info <name>: the information file is not written (v1)`; the
    parser's is kept, plan.build's dropped for every info statement the parser noted."""
    names = set()
    for n in parser_notes:
        m = _PARSER_INFO.match(n.message)
        if m:
            names.add(m.group(1))
    out = []
    for n in plan_notes:
        m = _PLAN_INFO.match(n.message)
        if m and n.severity != ERROR and m.group(1) in names:
            continue
        out.append(n)
    return out


def _ahdl_default(var: str, value: str) -> bool:
    """Whether a CDS_AHDL_* value is Spectre's documented default (numbers compared as numbers)."""
    default = _AHDL_DEFAULTS.get(var)
    if default is None:
        return False
    v = value.strip()
    if var == "CDS_AHDL_IGNORE_HIDDEN_STATE":
        return v.upper() != "YES"
    if var == "CDS_AHDL_AUTOGDEV_SUPPORT":
        return v.upper() != "NO"
    try:
        return float(v) == float(default)
    except ValueError:
        return v.lower() == default.lower()


def _no_output(step: Optional[nplan.AnalysisStep], global_mode: str) -> bool:
    """save=nooutput (the analysis's, else the options statement's): no data file is written (§6.1;
    signals.resolve gives such a step no signals and notes it).  Noise and xf keep their outputs."""
    if step is None or step.kind in ("noise", "xf"):
        return False
    mode = step.args.get("save")
    mode = mode.lower() if isinstance(mode, str) and mode else global_mode
    return mode == "nooutput"


def _subckt_names(nl) -> set:
    out = set()

    def walk(items) -> None:
        for it in items:
            body = getattr(it, "body", None)
            if body is not None and hasattr(it, "ports"):
                out.add(it.name)
                walk(body)
    walk(nl.body)
    return out


def _analysis_phase(kind: str, name: str) -> str:
    return "%s analysis `%s'" % (_PHASE_WORDS.get(kind, kind), name)


def _result_phase(res: results.AnalysisResult) -> str:
    if res.atype in _PARENT_KINDS:
        return "sweep `%s'" % psf.analysis_name(res)
    return _analysis_phase(res.atype, psf.analysis_name(res))


def _point(res: results.AnalysisResult, which: int) -> Optional[List[object]]:
    """The first (0) or last (-1) point of a result, as values in TRACE order."""
    if res.sweep is None:
        return [s.values[0] if s.values else 0.0 for s in res.signals]
    if res.rows is None:
        return None
    last = None
    for _x, vals in res.rows():
        if which == 0:
            return list(vals)
        last = vals
    return list(last) if last is not None else None


def _real(v: object) -> float:
    if isinstance(v, complex):
        return v.real
    return float(v)  # type: ignore[arg-type]


def _yes(v: object) -> bool:
    return isinstance(v, str) and v.lower() == "yes"


def _opt_text(v: object) -> Optional[str]:
    if isinstance(v, Str):
        return v.text
    if isinstance(v, str):
        return v
    if isinstance(v, Num):
        return repr(v.value)
    return None


def _va_include() -> List[str]:
    """CDS_VLOGA_INCLUDE: directories separated by commas, colons or semicolons [M ref19 p.551]."""
    return [d for d in _VA_INCLUDE_SPLIT.split(os.environ.get("CDS_VLOGA_INCLUDE", "")) if d]


def _read_stdin() -> bytes:
    f = sys.stdin
    if f is None:
        return b""
    try:
        buf = getattr(f, "buffer", None)
        if buf is not None:
            return buf.read()
        data = f.read()
    except (OSError, ValueError):                    # a closed standard input: an empty netlist
        return b""
    return data.encode("utf-8") if isinstance(data, str) else data


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


__all__ = ["run", "PHASE_READ", "PHASE_SETUP", "STDIN_NAME", "TRACEBACK_NAME"]
