"""The spectre personality: Spectre's command line, run on VACASK or Xyce.

docs/VAMOS_SPECTRE_DESIGN.md §2 (the command line), §8.7 (the log) and the §10 block.  cli.py
dispatches here for the invoked names `spectre`, `spectre<version>` and the VAMOS_SPECTRE_NAMES
aliases, and for `vamos -spectre`; the invoked name is tools.invoked (§2.1): it is %S, the
<prog> of <prog>_DEFAULTS and the <prog> of the log trailer.

build_job scans two layers into one SpectreJob (§2.2): the defaults variable (the options ref5
marks with a dagger; the others are noted and ignored there, and so is a --vamos-* token), then
argv.  List options accumulate, defaults first; scalar settings hold argv's values and
SpectreJob.defaults the defaults layer's, by field name (spectre.args.effective reads across
them).  Every row of §2.3 is an Opt below: mapped options have an action, the others a
disposition (ignored, noted, unsupported); an *error* row appends notes.error to
SpectreJob.notes, as do a second netlist and a bad -format value, and the run stops with exit
status 2 before the run directory is created.  An option the table does not know is a warning
(exit 2 under --vamos-strict), never a usage error.

main never raises (§2.7) and nothing goes to stderr (§8.7): every line goes through SpectreLog.
Phase 1 (§12): the run itself, spectre/flow.py, is phase 2's; until it exists main performs
steps 1 and 1b of §1 (the job, the log, the scan-time messages) and stops with exit status 2
and the trailer, saying so.  With flow.py present main hands the job to flow.run, which reuses
open_log and report below.

cli.py imports this module for every personality, so nothing here imports vamos.spectre or
vamos.netlist at import time.
"""

from __future__ import annotations

import dataclasses
import os
import tempfile
import textwrap
import traceback
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Dict, List, Mapping, Optional

from vamos import banner, tools
from vamos.job import IGNORED, INAPPLICABLE, NOTED, UNKNOWN, UNSUPPORTED
from vamos.notes import error, has_errors
from vamos.optable import (Opt, ScanError, Table, scan, strict_failures, strict_message,
                           unmapped_notes, vamos_option_effects, vamos_options_help)

if TYPE_CHECKING:
    from vamos.spectre.job import SpectreJob
    from vamos.spectre.log import SpectreLog

OPTION_CHARS = "-+="
PHASE_ARGS = "command-line processing"

# -format values (§2.3): written as given, mapped to psfascii with a note, or an error.
FORMATS = ("psfascii", "nutascii", "nutbin")
PSF_ALIASES = ("psfbin", "psfbinf", "psfxl", "sst2", "fsdb", "fsdb5", "wdf", "wsfbin", "wsfascii", "awb", "tr0ascii")


# -- actions -------------------------------------------------------------------------------------

def _given(job, attr: str) -> None:
    given = getattr(job, "given", None)
    if given is not None:
        given.add(attr)


def _set(attr: str) -> Callable:
    def act(job, val):
        setattr(job, attr, val)
        _given(job, attr)
    return act


def _append(attr: str, prefix: str = "") -> Callable:
    def act(job, val):
        getattr(job, attr).append(prefix + (val or ""))
    return act


def _flag(attr: str, value: bool) -> Callable:
    def act(job, val):
        setattr(job, attr, value)
        _given(job, attr)
    return act


def _version(which: str) -> Callable:
    def act(job, val):
        job.action = which
        _given(job, "action")
    return act


def _class(name: str, on: bool) -> Callable:
    def act(job, val):
        job.classes[name] = on
    return act


def _int(attr: str, option: str) -> Callable:
    def act(job, val):
        try:
            n = int(val, 10)
        except ValueError:
            job.notes.append(error("%s %s" % (option, val), "the value must be an integer"))
            return
        setattr(job, attr, n)
        _given(job, attr)
    return act


def _error(option: str, why: str) -> Callable:
    def act(job, val):
        shown = option if val in (None, "") else "%s %s" % (option, val)
        job.notes.append(error(shown, why))
    return act


def _format(job, val) -> None:
    v = val.lower()
    if v in FORMATS:
        job.fmt = v
    elif v in PSF_ALIASES:
        job.fmt = "psfascii"
        job.note("-format %s" % val, NOTED, "psfascii is written")
    elif v == "uwi":
        job.notes.append(error("-format %s" % val, "the user-defined output format is not supported"))
        return
    else:
        job.notes.append(error("-format %s" % val, "unknown output format (vamos writes psfascii, nutascii "
                                                   "and nutbin; the other psf formats are written as psfascii)"))
        return
    _given(job, "fmt")


def _log(mode: str) -> Callable:
    def act(job, val):
        job.log_mode = mode
        job.log_path = val
        _given(job, "log_mode")
        _given(job, "log_path")
    return act


def _cpp_list(attr: str, option: str) -> Callable:
    def act(job, val):
        if not val:
            job.notes.append(error(option, "needs its value written against the option (%s<x>)" % option))
            return
        getattr(job, attr).append(val)
    return act


def _interactive(option: str) -> Callable:
    def act(job, val):
        shown = option if val in (None, "") else "%s=%s" % (option, val)
        job.notes.append(error(shown, "the interactive mode is not supported (vamos runs the netlist and returns)"))
    return act


# §2.3, one Opt per option and per abbreviation; scanned with option_chars "-+=" (§2.1).  The help
# options and +%X/-%X are taken by spectre.args.prescan before the scan.
OPTIONS: List[Opt] = [
    Opt("-V", "flag", _version("version")),
    Opt("-W", "flag", _version("subversion")),
    Opt("-cmiversion", "flag", NOTED),
    Opt("-cmiconfig", "next", NOTED),
    Opt("-raw", "next", _set("raw")), Opt("-r", "next", _set("raw")),
    Opt("-format", "next", _format), Opt("-f", "next", _format),
    Opt("-outdir", "next", _set("outdir")),
    Opt("+rtsf", "flag", IGNORED),
    Opt("-uwifmt", "next", UNSUPPORTED), Opt("-uwilib", "next", UNSUPPORTED),
    Opt("+log", "next", _log("both")), Opt("+l", "next", _log("both")),
    Opt("=log", "next", _log("file")), Opt("=l", "next", _log("file")),
    Opt("-log", "flag", _log("screen")), Opt("-l", "flag", _log("screen")),
    Opt("-cols", "next", IGNORED), Opt("-c", "next", IGNORED), Opt("-colslog", "next", IGNORED),
    Opt("+error", "flag", _class("error", True)), Opt("-error", "flag", _class("error", False)),
    Opt("+warn", "flag", _class("warn", True)), Opt("-warn", "flag", _class("warn", False)),
    Opt("+note", "flag", _class("note", True)), Opt("-note", "flag", _class("note", False)),
    Opt("+info", "flag", _class("info", True)), Opt("-info", "flag", _class("info", False)),
    Opt("+debug", "flag", _class("debug", True)), Opt("-debug", "flag", _class("debug", False)),
    Opt("-maxwarns", "next", _int("maxwarns", "-maxwarns")), Opt("-maxw", "next", _int("maxwarns", "-maxw")),
    Opt("-maxnotes", "next", _int("maxnotes", "-maxnotes")), Opt("-maxn", "next", _int("maxnotes", "-maxn")),
    Opt("-maxwarnstolog", "next", _int("maxwarnstolog", "-maxwarnstolog")),
    Opt("-maxwtl", "next", _int("maxwarnstolog", "-maxwtl")),
    Opt("-maxnotestolog", "next", _int("maxnotestolog", "-maxnotestolog")),
    Opt("-maxntl", "next", _int("maxnotestolog", "-maxntl")),
    Opt("+varedefnerror", "flag", NOTED),
    Opt("-E", "flag", _flag("cpp", True)),
    Opt("-D", "prefix", _cpp_list("defines", "-D")),
    Opt("-U", "prefix", _cpp_list("undefines", "-U")),
    Opt("-I", "prefix", _cpp_list("incdirs", "-I")),
    Opt("-disableCPP", "flag", _flag("disable_cpp", True)),
    Opt("+config", "next", _append("config", "+")), Opt("=config", "next", _append("config", "=")),
    Opt("-config", "flag", _append("config", "-")),
    Opt("+pre_config", "next", _append("pre_config", "+")), Opt("-pre_config", "next", _append("pre_config", "-")),
    Opt("+top", "next", _error("+top", "exposing a subcircuit's contents at the top level is not supported")),
    Opt("+param", "next", NOTED, "soft parameter limits are not checked"),
    Opt("+p", "next", NOTED, "soft parameter limits are not checked"),
    Opt("-param", "flag", IGNORED), Opt("-p", "flag", IGNORED),
    Opt("+paramdefault", "next", _append("paramdefault", "+")),
    Opt("=paramdefault", "next", _append("paramdefault", "=")),
    Opt("-paramdefault", "flag", _append("paramdefault", "-")),
    Opt("+errpreset", "eq", _set("errpreset")),
    Opt("+aps", "flag", IGNORED), Opt("++aps", "flag", IGNORED),
    Opt("+aps", "eq", _set("aps")), Opt("++aps", "eq", _set("aps")),
    Opt("+preset", "eq", NOTED, "the Spectre X accuracy preset is not reproduced; vamos uses the netlist's settings"),
    Opt("+postlpreset", "eq", NOTED, "the Spectre X parasitic preset is not reproduced"),
    Opt("-preset_override", "flag", NOTED, "vamos always uses the netlist's solver options"),
    Opt("-preset_override", "eq", NOTED, "vamos always uses the netlist's solver options"),
    Opt("-mts", "flag", _flag("mts", False)),
    Opt("+xps", "flag", IGNORED), Opt("+xps", "eq", IGNORED), Opt("+ms", "flag", IGNORED),
    Opt("+xdp", "flag", IGNORED), Opt("+xdp", "eq", IGNORED),
    Opt("+speed", "eq", IGNORED), Opt("+query", "eq", IGNORED), Opt("+lite", "flag", IGNORED),
    Opt("-hpc", "flag", IGNORED), Opt("+dcopt", "flag", IGNORED), Opt("+dcopt", "eq", IGNORED),
    Opt("+disk_check", "flag", IGNORED), Opt("+disk_check", "eq", IGNORED),
    Opt("-clearcache", "flag", IGNORED), Opt("-cc", "flag", IGNORED),
    Opt("+hosts", "next", IGNORED),
    Opt("+multithread", "flag", IGNORED), Opt("+multithread", "eq", IGNORED),
    Opt("+mt", "flag", IGNORED), Opt("+mt", "eq", IGNORED),
    Opt("-multithread", "flag", IGNORED), Opt("-mt", "flag", IGNORED),
    Opt("-64", "flag", IGNORED), Opt("-32", "flag", IGNORED),
    Opt("+mtmode", "next", IGNORED), Opt("-processor", "next", IGNORED), Opt("-proc", "next", IGNORED),
    Opt("+diagnose", "flag", NOTED), Opt("+transteps", "flag", NOTED), Opt("+diagnose_fpe", "flag", NOTED),
    Opt("+detect_negcap", "flag", NOTED), Opt("+fix_bad_pivot", "flag", NOTED),
    Opt("+diagnose_minstep", "eq", NOTED), Opt("+diagnose_top", "eq", NOTED),
    Opt("+checkpoint", "flag", NOTED, "no checkpoint file is written"),
    Opt("+cp", "flag", NOTED, "no checkpoint file is written"),
    Opt("+savestate", "flag", NOTED, "no savestate file is written"),
    Opt("+ss", "flag", NOTED, "no savestate file is written"),
    Opt("-checkpoint", "flag", IGNORED), Opt("-cp", "flag", IGNORED),
    Opt("-savestate", "flag", IGNORED), Opt("-ss", "flag", IGNORED),
    Opt("-recover", "flag", IGNORED), Opt("-rec", "flag", IGNORED),
    Opt("+recover", "flag", UNSUPPORTED, "the run starts from scratch"),
    Opt("+recover", "eq", UNSUPPORTED, "the run starts from scratch"),
    Opt("+rec", "flag", UNSUPPORTED, "the run starts from scratch"),
    Opt("+rec", "eq", UNSUPPORTED, "the run starts from scratch"),
    Opt("+interactive", "flag", _interactive("+interactive")), Opt("+interactive", "eq", _interactive("+interactive")),
    Opt("+inter", "flag", _interactive("+inter")), Opt("+inter", "eq", _interactive("+inter")),
    Opt("-interactive", "flag", IGNORED), Opt("-inter", "flag", IGNORED),
    Opt("+mpssession", "eq", UNSUPPORTED), Opt("+mpshost", "eq", UNSUPPORTED),
    Opt("-slave", "next", UNSUPPORTED), Opt("-slvhost", "next", UNSUPPORTED),
    Opt("-mdlcontrol", "flag", IGNORED), Opt("-mdl", "flag", IGNORED),
    Opt("+mdlcontrol", "flag", UNSUPPORTED, "MDL measurements are not run"),
    Opt("+mdlcontrole", "flag", UNSUPPORTED, "MDL measurements are not run"),
    Opt("=mdlcontrol", "next", UNSUPPORTED, "MDL measurements are not run"),
    Opt("=mdl", "next", UNSUPPORTED, "MDL measurements are not run"),
    Opt("=mdlcontrole", "next", UNSUPPORTED, "MDL measurements are not run"),
    Opt("=mdle", "next", UNSUPPORTED, "MDL measurements are not run"),
    Opt("-checklimitfile", "next", NOTED), Opt("-cl", "next", NOTED),
    Opt("+dochecklimit", "flag", IGNORED), Opt("+docl", "flag", IGNORED),
    Opt("-dochecklimit", "flag", IGNORED), Opt("-docl", "flag", IGNORED), Opt("-dynchecks", "flag", IGNORED),
    Opt("+lqtimeout", "next", IGNORED), Opt("+lqt", "next", IGNORED),
    Opt("+lqsleep", "next", IGNORED), Opt("+lqs", "next", IGNORED),
    Opt("+lmode", "next", IGNORED), Opt("+lorder", "next", IGNORED), Opt("-alias", "next", IGNORED),
    Opt("+lqmmtoken", "flag", IGNORED), Opt("+lsuspend", "flag", IGNORED), Opt("+lsusp", "flag", IGNORED),
    Opt("+liclog", "flag", IGNORED),
    Opt("-ahdllibdir", "next", _set("ahdllibdir")),
    Opt("-va,define", "next", _append("va_defines")),
    Opt("-ahdlcom", "next", IGNORED), Opt("-ac", "next", IGNORED),
    Opt("-ahdllint_log", "next", IGNORED), Opt("-rf_ahdl_functionality", "next", IGNORED),
    Opt("-ahdllint", "flag", IGNORED), Opt("-ahdllint", "eq", IGNORED),
    Opt("-ahdllint_maxwarn", "flag", IGNORED), Opt("-ahdllint_maxwarn", "eq", IGNORED),
    Opt("-ahdllint_summary_maxentries", "flag", IGNORED), Opt("-ahdllint_summary_maxentries", "eq", IGNORED),
    Opt("-ahdllint_warn_id", "eq", IGNORED), Opt("-ahdlshipdbdir", "eq", IGNORED), Opt("-ahdlshipdbmode", "eq", IGNORED),
    Opt("-ahdlsourceramp", "flag", NOTED),
    Opt("+sensdata", "next", NOTED),
    Opt("+spp", "flag", IGNORED), Opt("-spp", "flag", IGNORED), Opt("-sppbin", "next", IGNORED),
    Opt("-env", "next", IGNORED),
    Opt("+escchars", "flag", _flag("escchars", True)),
    Opt("+logstatus", "flag", IGNORED),
]

TABLE: Table = Table(OPTIONS)

# The options with an action, for the help text's "Options vamos acts on" (one spelling each).
_ACTED = ("  -raw/-r <dir>  -format/-f <fmt>  -outdir <dir>  +log/+l <file>  =log/=l <file>  -log/-l\n"
          "  -E  -D<x>[=<y>]  -U<x>  -I<dir>  -disableCPP  +config/=config <file>  -config\n"
          "  +pre_config/-pre_config <file>  +paramdefault/=paramdefault <file>  -paramdefault\n"
          "  +errpreset=<p>  +aps=<p>  ++aps=<p>  -mts  -ahdllibdir <dir>  -va,define <m>[=<v>]  +escchars\n"
          "  +error/-error  +warn/-warn  +note/-note  +info/-info  +debug/-debug\n"
          "  -maxwarns/-maxw <n>  -maxnotes/-maxn <n>  -maxwarnstolog/-maxwtl <n>  -maxnotestolog/-maxntl <n>\n"
          "  +%X <string>  -%X  -V  -W  -help/-h [<topic>]  -helpsort/-hs  -helpfull/-hf  -helpsortfull/-hsf")


# -- the layers ----------------------------------------------------------------------------------

_LAYER = None


def _layer_class():
    """A SpectreJob that also records the scalar fields its layer set (`given`)."""
    global _LAYER
    if _LAYER is None:
        from vamos.spectre.job import SpectreJob

        class Layer(SpectreJob):
            """One layer's scratch job: a SpectreJob plus the scalar fields the layer set."""

            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                self.given = set()

        _LAYER = Layer
    return _LAYER


def _normalize(events: List[str]) -> List[str]:
    """The +/=/- rules of §2.3 within one layer, for +config/=config/-config,
    +pre_config/-pre_config and +paramdefault/=paramdefault/-paramdefault: `-` drops every `+`
    and every preceding `=`; a surviving `=` drops every `+` and appends its file."""
    plus = [e[1:] for e in events if e[:1] == "+"]
    if any(e[:1] == "-" for e in events):
        plus = []
        last = max(i for i, e in enumerate(events) if e[:1] == "-")
        eqs = [e[1:] for e in events[last + 1:] if e[:1] == "="]
    else:
        eqs = [e[1:] for e in events if e[:1] == "="]
    return eqs or plus


def _scan_layer(tokens: List[str], prog: str, cwd: str, var: Optional[str]):
    """One layer -> a Layer (a SpectreJob with `given`): the defaults variable `var`, or argv
    (var None).  The defaults layer loses the options Spectre does not read from it and every
    --vamos-* token (both recorded); a non-option token there is an error."""
    from vamos.spectre import args as sargs

    layer = _layer_class()(prog, list(tokens), cwd)
    if var is not None:
        tokens = sargs.not_read(tokens, layer, var)
        tokens = sargs.without_vamos_options(tokens, layer, var)
    rest = sargs.prescan(tokens, layer)
    if layer.action != "run":
        layer.given.update(("action", "help_topic"))

    def positional(job, tok):
        if var is not None:
            job.notes.append(error(tok, "a non-option token in %s: the netlist is given on the command line only"
                                   % var))
        elif job.netlist is None:
            job.netlist = tok
        else:
            job.notes.append(error(tok, "a second input file: spectre reads one netlist, the first "
                                        "non-option token"))

    def unknown(job, tok, a, i):
        job.note(tok, UNKNOWN)
        return 1

    scan(TABLE, rest, layer, positional, unknown, option_chars=OPTION_CHARS)
    layer.config = _normalize(layer.config)
    layer.pre_config = _normalize(layer.pre_config)
    layer.paramdefault = _normalize(layer.paramdefault)
    return layer


def _merge(job, layer, defaults: bool) -> None:
    """A layer into the job (§2.2): lists accumulate (defaults first), dicts merge (argv wins per
    key), scalars go to the job's fields (argv) or to job.defaults by field name (the defaults
    layer); a boolean the defaults layer set is applied to the job as well, since -E,
    -disableCPP, +escchars and -mts only ever set one value.  An explicit argv -log drops the
    defaults' log entries, so `-log` on the command line beats a `+log f` in the variable."""
    for f in dataclasses.fields(type(job)):
        name = f.name
        if name in ("prog", "argv", "cwd", "unmapped", "notes", "defaults"):
            continue
        val = getattr(layer, name)
        if isinstance(val, list):
            getattr(job, name).extend(val)
        elif isinstance(val, dict):
            getattr(job, name).update(val)
        elif name == "netlist":
            if val is not None:
                job.netlist = val
        elif name in layer.given:
            if defaults:
                job.defaults[name] = val
                if isinstance(val, bool):
                    setattr(job, name, val)
            else:
                setattr(job, name, val)
                if name == "log_mode":
                    job.defaults.pop("log_mode", None)
                    job.defaults.pop("log_path", None)
    job.unmapped.extend(layer.unmapped)
    job.notes.extend(layer.notes)


def build_job(argv: List[str], env: Mapping[str, str], cwd: str, prog: str) -> SpectreJob:
    """The defaults layer (<prog>_DEFAULTS, §2.2) and argv (after cli.py took the --vamos-*
    options) -> SpectreJob; prog is the invoked name (tools.invoked).  optable.ScanError for an
    option that needs a value and has none, or an unbalanced quote in the variable."""
    from vamos.spectre import args as sargs
    from vamos.spectre.job import SpectreJob

    job = SpectreJob(prog, list(argv), cwd)
    var, tokens = sargs.defaults_tokens(env, prog)
    if var is not None:
        _merge(job, _scan_layer(tokens, prog, cwd, var), defaults=True)
    _merge(job, _scan_layer(list(argv), prog, cwd, None), defaults=False)
    return job


# -- the run -------------------------------------------------------------------------------------

def _prog() -> str:
    """The invoked name (§2.1); `spectre` when main is called outside cli.main."""
    name = tools.invoked
    return name if name and name != "vamos" else "spectre"


def note_vamos_options(job, opts: dict) -> None:
    """--vamos-* options with no effect under spectre go into job.unmapped as INAPPLICABLE (§10;
    cli.py skips its own effects check for spectre), so they reach the log and the strict check."""
    for opt, why in vamos_option_effects(opts, "spectre"):
        job.note(opt, INAPPLICABLE, why)


def open_log(job, opts: dict, start: datetime) -> SpectreLog:
    """Step 1b (§1): the banner profile and the log.  A bad --vamos-banner is an error note and
    the shipped layout is used; a log file that cannot be opened is an error note and the log
    goes to the screen (both stop the run with exit status 2 after the messages, §2.7)."""
    from vamos.spectre.log import SpectreLog

    try:
        prof = banner.load_profile("spectre", opts.get("banner"))
    except ValueError as e:
        job.notes.append(error("", str(e)))
        prof = banner.load_profile("spectre")
    ban = banner.Banner(prof, "spectre")
    try:
        return SpectreLog(job, ban, start)
    except OSError as e:
        job.notes.append(error("", "cannot open the log file: %s" % e))
        screen = dataclasses.replace(job, log_mode="screen", log_path=None,
                                     defaults={k: v for k, v in job.defaults.items()
                                               if k not in ("log_mode", "log_path")})
        return SpectreLog(screen, ban, start)


def report(job, opts: dict, log: SpectreLog) -> int:
    """The scan-time messages (§2.3): job.notes and the unmapped-option report as Spectre
    messages, then the --vamos-strict check; 2 when the run must stop, else 0."""
    errors = has_errors(job.notes)
    for n in job.notes:
        log.message(n, PHASE_ARGS)
    for n in unmapped_notes(job):
        log.message(n, PHASE_ARGS)
    if opts.get("strict"):
        bad = strict_failures(job)
        if bad:
            log.message(error("", strict_message(bad)), PHASE_ARGS)
            errors = True
    return 2 if errors else 0


def _flow():
    """spectre/flow.py when it exists (phase 2), else None."""
    try:
        from vamos.spectre import flow
    except ImportError:
        return None
    return flow


def main(args: List[str], opts: dict) -> int:
    """cli entry; the invoked name is tools.invoked (§2.1).  Returns the Spectre exit status
    and never raises (§2.7)."""
    from vamos.spectre.job import SpectreJob

    start = datetime.now()
    prog = _prog()
    cwd = os.getcwd()
    job = None
    log = None
    try:
        try:
            job = build_job(args, os.environ, cwd, prog)
        except ScanError as e:
            job = SpectreJob(prog, list(args), cwd)
            job.notes.append(error("", str(e)))
        note_vamos_options(job, opts)
        flow = _flow()
        if flow is not None:
            return flow.run(job, opts)
        # Phase 1 (§12): steps 1 and 1b of §1, then the stop.
        if job.action == "help" and not has_errors(job.notes):
            print(help_text(job.help_topic))
            return 0
        log = open_log(job, opts, start)
        if job.action in ("version", "subversion"):
            log.line(log.version_text(job.action))
            if job.netlist is None and not has_errors(job.notes):
                log.close()
                return 0
        status = report(job, opts, log)
        if status:
            log.finish(status)
            return status
        log.message(error("", "the spectre run is not implemented yet (docs/VAMOS_SPECTRE_DESIGN.md §12: "
                              "phase 2 writes spectre/flow.py); the command line was accepted"), "setup")
        log.finish(2)
        return 2
    except Exception as e:                                   # noqa: BLE001  (§2.7: never raises)
        return _internal_error(e, job, start, prog, args, cwd, log)


def _internal_error(e: Exception, job, start: datetime, prog: str, args: List[str], cwd: str, log) -> int:
    """§2.7: an internal error is one line on the screen and exit status 2 with the trailer; the
    traceback goes to the log file when there is one, else to a file named in the line."""
    from vamos.spectre.job import SpectreJob
    from vamos.spectre.log import SpectreLog

    text = traceback.format_exc()
    try:
        if log is None:
            if job is None:
                job = SpectreJob(prog, list(args), cwd)
            screen = dataclasses.replace(job, log_mode="screen", log_path=None, defaults={})
            log = SpectreLog(screen, banner.Banner(banner.load_profile("spectre"), "spectre"), start)
        where = log.path
        if where is None:
            fh = tempfile.NamedTemporaryFile("w", prefix="vamos-spectre-", suffix=".traceback", delete=False)
            with fh:
                fh.write(text)
            where = fh.name
        else:
            log.file_only(text)
        log.message(error("", "internal error: %s: %s (the traceback is in %s)" % (type(e).__name__, e, where)),
                    "setup")
        log.finish(2)
    except Exception:                                        # noqa: BLE001  (the last resort stays silent)
        pass
    return 2


# -- help ----------------------------------------------------------------------------------------

_HELP_HEAD = """\
usage: spectre [options] [netlist]              (vamos spectre personality)

Runs a Spectre netlist (Spectre or SPICE mode) on VACASK, the default, or Xyce
(--vamos-analog=xyce), and writes what Spectre writes: the results directory (%%C:r.raw, with
logFile and one PSF ASCII file per analysis, or one nutmeg file), the +log file with Spectre's
trailer, the state files, and Spectre's exit status (0 completed, 1 an analysis failed, 2 a
Spectre error, 3 stopped by a signal).  With no netlist the input is standard input (%%C is
stdin).  The name vamos was invoked as (spectre, spectre<version>, or an alias listed in
VAMOS_SPECTRE_NAMES) is %%S and names the defaults variable: <prog>_DEFAULTS, else
<PROG>_DEFAULTS, else SPECTRE_DEFAULTS.

Options vamos acts on:
%s
Errors (exit 2): +top, +interactive, -format uwi and any unknown -format value; a second netlist.
Every other Spectre option is accepted: it is ignored, or gets a note or a warning
(--vamos-strict makes the warnings errors); none is a usage error.  -h <topic> prints the
dispositions of a primitive (resistor, capacitor, inductor, vsource, isource, iprobe, vcvs, vccs,
ccvs, cccs, mutual_inductor, diode, bjt, jfet, mos1, mos2, mos3, bsim3v3, bsim4) or an analysis
(dc, ac, noise, xf, tran, sweep); -h all prints every one."""

_HELP_TAIL = ("vamos options (--vamos-<key>[=<value>]; [..] says where an option has an effect):\n%s\n\n"
              "The user guide: %s")

# §3.11, §0: the analyses' parameter dispositions, for `spectre -h <analysis>`.
ANALYSIS_HELP: Dict[str, str] = {
    "dc": "dc: the operating point, or a sweep (start stop center span step lin dec log values valuesfile) of "
          "dev=, mod=, sub=, param= or temp (param=temp). Mapped: readns, write/writefinal (state files), "
          "useprevic (VACASK; a warning on Xyce), save/nestlvl, print=yes (the values go to the log), "
          "restart and swpuseprevic (VACASK sweep continuation), maxiters (VACASK op_itl). Noted: oppoint "
          "other than no, homotopy, newton, maxsteps, swp1stpointic. Errors: force/readforce other than "
          "none, hysteresis=yes. Ignored: check, annotate, title, emir*.",
    "ac": "ac: as dc, plus freq (the fixed frequency of a parameter sweep) and prevoppoint=yes (VACASK; a "
          "warning on Xyce). Errors: skipdc=yes, perturbation other than linear (with out1, out2, "
          "contriblist, rf*, flin_out, fim_out, maxharm_nonlin).",
    "noise": "noise: as ac. Output: the node pair, or oprobe= a two-terminal component (its terminal pair; an "
             "infinite resistor included) or a controlled source with oportv=1 or absent (its output "
             "port); another port number, or a current output through a vsource or iprobe, is an error. "
             "Input: iprobe= a vsource or isource (a port is an error), iportv 1 or absent; without an "
             "input vamos adds one and writes no in/gain. Noted: separatenoise.",
    "xf": "xf: as ac; output the node pair or probe= (two-terminal, an infinite resistor included; a current "
          "probe is an error); stimuli=sources (nodes_and_terminals is an error). VACASK only: an error "
          "with --vamos-analog=xyce.",
    "tran": "tran: stop (required). Mapped: outputstart, step, maxstep, ic, skipdc, readic (and read), "
            "readns, write/writefinal, useprevic (VACASK; a warning on Xyce), method, errpreset, relref, "
            "lteratio (the tolerances on VACASK only), maxiters (VACASK tran_itl), transres (the pulse "
            "edge default), restart, skipstart/skipstop/skipcount (output filtering). Noted: minstep, "
            "ckptperiod, saveperiod, saveclock, savetime, savefile, recover. Warnings: cmin other than 0, "
            "strobe*, infonames/infotimes/infotime_pair, acnames/actimes/actime_pair (not run). Errors: "
            "start other than 0, tpoints, readtime, linearic=yes, rampup*, maxstepratio, reltolratio, "
            "circuitage other than 0, noisefmax above 0, param, paramset, param_vec, param_file, sub. "
            "Ignored: istep, pstep, oscfreq, annotate*, title, progress_*, compression, comp*, complvl, "
            "flush*, fastbreak, d2a*, fastcross, lteminstep, ltethstep, vref*, iref*, emir*, autostop, "
            "dcmaxiters.",
    "sweep": "sweep: the sweep parameters of dc plus sub; nested sweeps and several children. Errors: "
             "paramset, faults*. Ignored: distribute, numprocesses, savedatainseparatedir, annotate, title.",
    "montecarlo": "montecarlo: not in v1 (an error at plan time); statistics blocks are parsed and the "
                  "netlist runs at nominal values.",
}

# §0: constructs that are errors in v1, for the "not in v1" answer of `spectre -h <topic>`.
NOT_IN_V1 = ("altergroup paramset acmatch dcmatch sp stb pz pss pac pnoise pxf pstb psp qpss qpac qpnoise qpxf "
             "qpsp hb hbac hbnoise hbsp hbstb hbxf envlp tdr thermal reliability stress loadpull lf cosim uti "
             "sens bsource port nport tline mtline switch relay transformer core winding delay a2d d2a fourier "
             "intcap nodcap quantity node pvcvs svcvs zvcvs pvccs svccs zvccs pccvs sccvs zccvs pcccs scccs "
             "zcccs bsimcmg psp psp103 hisim bsimsoi b3soipd btasoi ekv vbic bjt50x hbt mos9xx mos11xxx juncap "
             "tom2 tom3 hvmos gaas misnan").split()
NOTED_IN_V1 = {"info": "parsed; the .info file is not written (a note)",
               "check": "parsed; not evaluated (a note)", "checklimit": "parsed; not evaluated (a note)",
               "assert": "parsed; not evaluated (a note)", "shell": "parsed; the command is not run (a warning)"}

_ACTIONS = (("pass", "mapped"), ("fold", "folded at parse time"), ("rename", "renamed"),
            ("default", "a default is written"), ("strip", "stripped (no target; a note where it matters)"),
            ("error", "an error"))


def _wrap(text: str, indent: str = "  ") -> str:
    return textwrap.fill(text, width=100, initial_indent=indent, subsequent_indent=indent, break_on_hyphens=False,
                         break_long_words=False)


def _master_help(name: str, row) -> str:
    lines = ["%s (Spectre master, IR element kind `%s'%s)" % (
        name, row.element, "; terminals %s" % " ".join(row.terminals) if row.terminals else "")]
    for label, rules in (("card parameters", row.params), ("instance parameters", row.instance)):
        if not rules:
            continue
        lines.append("  %s:" % label)
        for action, title in _ACTIONS:
            names = []
            for r in rules:
                if r.action != action:
                    continue
                item = r.name
                if action == "rename":
                    item = "%s->%s" % (r.name, r.value)
                elif action == "default":
                    item = "%s=%s" % (r.name, r.value)
                if r.match and action in ("strip", "error"):
                    item += "(%s)" % ",".join(r.match)
                if item not in names:
                    names.append(item)
            if names:
                lines.append(_wrap("%s: %s" % (title, " ".join(names)), "    "))
    return "\n".join(lines)


def _topic_help(topic: str) -> str:
    from vamos.netlist import tables

    key = topic.lower()
    masters = tables.SPECTRE_MASTERS
    if key == "all":
        parts = [_master_help(m, masters[m]) for m in sorted(masters)]
        parts += [_wrap(ANALYSIS_HELP[a], "") for a in ("dc", "ac", "noise", "xf", "tran", "sweep", "montecarlo")]
        return "\n\n".join(parts)
    if key in masters:
        return _master_help(key, masters[key])
    if key in ANALYSIS_HELP:
        return _wrap(ANALYSIS_HELP[key], "")
    if key in NOTED_IN_V1:
        return "%s: %s." % (key, NOTED_IN_V1[key])
    if key in NOT_IN_V1:
        return _wrap("%s: not in vamos's spectre v1; a netlist that uses it is an error (exit status 2) "
                     "(docs/VAMOS_SPECTRE_DESIGN.md §0)." % key, "")
    return _wrap("note: no help for `%s': the topics are the masters %s, the analyses %s, and `all'."
                 % (topic, " ".join(sorted(masters)), " ".join(ANALYSIS_HELP)), "")


def help_text(topic: Optional[str] = None) -> str:
    """spectre -h [topic]: at most 100 columns, ending with the absolute path of
    docs/VAMOS_GUIDE.md (§2.1).  With a topic, that primitive's or analysis's dispositions."""
    head = _HELP_HEAD % _ACTED
    if topic is not None:
        head = "%s\n\n%s" % (head.split("\n\n", 1)[0], _topic_help(topic))
    return "%s\n\n%s" % (head, _HELP_TAIL % (vamos_options_help("spectre"), tools.guide_path()))
