"""simv personality: the runtime side of the vcs two-step flow.

The generated ./simv stub runs `vamos -simv --vamos-daidir=<dir> args...`.
Every +plusarg reaches the simulation ($test$plusargs / $value$plusargs);
simv's own options are handled from the table below.  -h / -help print the
runtime options vamos knows and exit 0 without simulating.

+vcs+finish+<time> ends the run at an absolute time (VCS User Guide, "Options
for Specifying When Simulation Stops"): N (units of the simulation precision),
N<unit> (fs, ps, ns, us, ms, s: +vcs+finish+9001us), or <low>+<high>, VCS's
two-argument form for times of 2^32 units and more (high * 2^32 + low units).
A value in none of these forms is a warning ("unknown option ... ignored (not
a time value)") and fails --vamos-strict.

A run that a signal interrupted (Ctrl-C, SIGTERM, SIGHUP; backends/nvc.py)
prints its footer, then ends vamos with that same signal, so a calling shell
or script sees an interrupted command, not an exit status.
"""

import json
import os
import re
import signal
import sys
from fractions import Fraction
from typing import List, Optional, Tuple

from vamos import banner, tools
from vamos.backends.nvc import BackendError, NvcBackend, cpu_time
from vamos.console import Console
from vamos.job import IGNORED, NOTED, UNKNOWN, UNSUPPORTED, Job, JobVersionError
from vamos.optable import Opt, ScanError, Table, report_unmapped, scan, strict_failures


def _plusarg(job: Job, tok):
    job.plusargs.append(tok)


def _seed(job: Job, val):
    job.seed = val
    job.plusargs.append("+ntb_random_seed=" + val)


def _run_log(job: Job, val):
    job.run_log = val if os.path.isabs(val) else os.path.join(job.cwd, val)


def _finish(job: Job, val):
    # +vcs+finish+<time>: applied at run time, where the compiled job's precision is
    # known (finish_fs); a value in no accepted form is an unknown option.
    if _finish_form(val) is None:
        job.note("+vcs+finish+" + val, UNKNOWN, "not a time value: N, N<unit> such as 9001us, "
                 "or <low>+<high>")
        return
    job.finish = val


def _help(job: Job, val):
    setattr(job, "help_requested", True)


OPTIONS = [
    Opt("-l", "next", _run_log),
    Opt("+ntb_random_seed", "eq", _seed),
    Opt("+vcs+lic+wait", "flag", IGNORED),
    Opt("-licqueue", "flag", IGNORED),
    Opt("-q", "flag", IGNORED),
    Opt("-k", "next", IGNORED),
    Opt("+vcs+finish+", "prefix", _finish),
    Opt("-h", "flag", _help),
    Opt("-help", "flag", _help),
    Opt("--help", "flag", _help),
    Opt("-ad_runopt", "eq", NOTED, "analog run options are not passed to the engine"),
    Opt("+vcs+stop+", "prefix", UNSUPPORTED),
    Opt("-ucli", "flag", UNSUPPORTED, "UCLI scripting arrives in a later phase"),
    Opt("-do", "next", UNSUPPORTED, "UCLI scripts arrive in a later phase"),
    Opt("-gui", "prefix", NOTED, "no GUI"),
    Opt("-verdi", "flag", NOTED, "Verdi is not available"),
    Opt("+fsdbfile+", "prefix", NOTED, "FSDB is not produced"),
    Opt("+vpdfile+", "prefix", NOTED, "VPD is not produced"),
    Opt("-assert", "next", NOTED, "assertion run-time controls are not mapped yet "
        "(vamos prints no assertion summary, so there is none to suppress)"),
    Opt("-cm", "next", UNSUPPORTED),
    Opt("-cm_dir", "next", UNSUPPORTED),
    Opt("-cm_name", "next", UNSUPPORTED),
    Opt("+notimingcheck", "flag", NOTED, "timing checks are not modelled, so there are "
        "none to disable"),
]
TABLE = Table(OPTIONS)


def _positional(job: Job, tok: str) -> None:
    job.note(tok, UNKNOWN, "simv takes no positional arguments")


def _unknown(job: Job, tok: str, args: List[str], i: int) -> int:
    if tok.startswith("+"):
        job.plusargs.append(tok)
    else:
        job.note(tok, UNKNOWN)
    return 1


def write_tools(daidir: str, used: List[Tuple[str, str, str]]) -> None:
    with open(os.path.join(daidir, "vamos.tools.json"), "w") as fh:
        json.dump([{"tool": t, "version": v, "path": p} for t, v, p in used], fh, indent=2)


def _read_tools(daidir: str) -> dict:
    try:
        with open(os.path.join(daidir, "vamos.tools.json")) as fh:
            return {d["tool"]: d for d in json.load(fh)}
    except (OSError, ValueError, KeyError):
        return {}


_UNIT_FS = {"s": 10 ** 15, "ms": 10 ** 12, "us": 10 ** 9, "ns": 10 ** 6, "ps": 10 ** 3, "fs": 1}
# nvc's clock ends at TIME'HIGH (2^63 - 1 fs, about 9223 s)
_TIME_HIGH = 2 ** 63 - 1


def precision_fs(p: Optional[str]) -> Optional[int]:
    """'1ps' / '100 fs' / '10ns' -> femtoseconds; None if unknown."""
    m = re.match(r"^\s*(1|10|100)\s*(s|ms|us|ns|ps|fs)\s*$", p or "")
    return int(m.group(1)) * _UNIT_FS[m.group(2)] if m else None


def _finish_form(val: str):
    """The +vcs+finish+ value parsed: ("ticks", n), ("abs", Fraction seconds-in-fs), or None."""
    m = re.match(r"^(\d+)$", val)
    if m:
        return ("ticks", int(m.group(1)))
    m = re.match(r"^(\d+)\+(\d+)$", val)          # VCS: <low 32 bits>+<high 32 bits>
    if m:
        return ("ticks", (int(m.group(2)) << 32) + int(m.group(1)))
    m = re.match(r"^(\d+(?:\.\d*)?|\.\d+)(fs|ps|ns|us|ms|s)$", val, re.I)
    if m:
        return ("abs", Fraction(m.group(1)) * _UNIT_FS[m.group(2).lower()])
    return None


def finish_fs(rt: Job, compiled: Job, con: Console) -> Optional[int]:
    """+vcs+finish+<time> as the nvc --stop-time in femtoseconds; None when not given or
    unusable (recorded on rt as an unsupported option, so --vamos-strict fails on it).

    N counts units of the compiled precision.  tgt-vhdl compresses the time base when the
    precision is 1 ms or coarser: one tick of the precision is then translated as 1 ms,
    so a tick is min(precision, 1 ms) of nvc time, and an absolute time is converted to
    ticks of the precision first (+vcs+finish+6s at a 1 s precision is 6 ticks)."""
    if rt.finish is None:
        return None
    opt = "+vcs+finish+" + rt.finish
    form = _finish_form(rt.finish)
    unit = precision_fs(compiled.precision)
    if form is None or unit is None:
        rt.note(opt, UNSUPPORTED, "not a time value" if form is None else
                "the compile recorded no time precision; recompile")
        return None
    tick = min(unit, 10 ** 12)
    kind, n = form
    fs = n * tick if kind == "ticks" else int(n * tick / unit)
    if fs > _TIME_HIGH:
        rt.note(opt, UNSUPPORTED, "%d fs is beyond the simulator's clock (TIME'HIGH, %.6g s); "
                "the run is not bounded" % (fs, _TIME_HIGH / 1e15))
        return None
    return fs


def usage(daidir: Optional[str] = None) -> str:
    """simv's help: the runtime options vamos acts on, notes or rejects."""
    rows = {"mapped": [], NOTED: [], IGNORED: [], UNSUPPORTED: []}
    for o in OPTIONS:
        if o.act is _help:
            continue
        name = o.name + {"next": " <arg>", "eq": "=<v>", "prefix": "..."}.get(o.arity, "")
        rows["mapped" if callable(o.act) else o.act].append((name, o.note))

    def block(title, items):
        if not items:
            return []
        return [title] + ["  %-22s %s" % (n, note) if note else "  " + n for n, note in items]

    guide = os.path.join(tools.package_root(), "docs", "VAMOS_GUIDE.md")
    lines = ["usage: ./simv [runtime options] [+plusargs]",
             "",
             "Runs the design vcs compiled%s." % (" into " + daidir if daidir else ""),
             "Every +plusarg reaches the simulation ($test$plusargs, $value$plusargs)."]
    lines += [""] + block("Runtime options vamos acts on:", [
        ("-l <file>", "copy the run's output to <file>"),
        ("+ntb_random_seed=<n>", "the seed, passed on as a plusarg"),
        ("+vcs+finish+<time>", "end the run at <time>: N units of the precision,\n"
                               "%25sN<unit> (+vcs+finish+9001us), or <low>+<high> (VCS's\n"
                               "%25sform for 2^32 units and more)" % ("", "")),
        ("-h, -help", "this help")])
    lines += [""] + block("Accepted with a note (no effect):", rows[NOTED])
    lines += [""] + block("Accepted and ignored:", rows[IGNORED])
    lines += [""] + block("Not supported yet (a warning; an error with --vamos-strict):",
                          rows[UNSUPPORTED])
    lines += ["",
              "vamos options: --vamos-keep (keep an AMS run's directory), --vamos-strict,",
              "  --vamos-verbose, --vamos-banner=<name|path|none>, --vamos-append-log (with -l:",
              "  append to the log, as vcs -R does)",
              "",
              "More: %s" % (guide if os.path.isfile(guide) else
                            "docs/VAMOS_GUIDE.md in the sv2ghdl sources")]
    return "\n".join(lines)


def _end_with_signal(signum: int) -> int:
    """End vamos with the signal that interrupted the run, as an interrupted command does
    (a shell then stops a script or loop running it); 128 + N if that fails."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
    try:
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
    except (OSError, ValueError):
        pass
    return 128 + signum


def run_daidir(daidir: str, args: List[str], opts: dict) -> int:
    con = Console()
    append = "--vamos-append-log" in args
    args = [a for a in args if a != "--vamos-append-log"]
    rt = Job(personality="simv", argv=list(args), cwd=os.getcwd(), daidir=daidir)
    try:
        scan(TABLE, args, rt, _positional, _unknown)
    except ScanError as e:
        con.err("vamos: error: %s" % e)
        return 1
    if getattr(rt, "help_requested", False):
        print(usage(daidir))
        return 0

    try:
        with open(os.path.join(daidir, "vamos.job.json")) as fh:
            compiled = Job.from_json(fh.read())
    except JobVersionError as e:
        con.err("vamos: error: %s: %s" % (daidir, e))
        return 1
    except TypeError as e:
        con.err("vamos: error: %s was written by an incompatible vamos (%s); recompile"
                % (daidir, e))
        return 1
    except FileNotFoundError as e:
        if os.path.isdir(daidir):
            # a compile removes the job record first and writes it last (vcs.invalidate)
            con.err("vamos: error: %s holds no finished compile (the last compile failed or was "
                    "interrupted, or the directory is incomplete); compile again" % daidir)
        else:
            con.err("vamos: error: %s is not a vamos simulation directory (%s)" % (daidir, e))
        return 1
    except (OSError, ValueError) as e:
        con.err("vamos: error: %s is not a vamos simulation directory (%s)" % (daidir, e))
        return 1

    if rt.run_log:
        con.open_log(rt.run_log, append=append)

    try:
        prof = banner.load_profile("vcs", opts.get("banner"))
    except ValueError as e:
        con.err("vamos: error: %s" % e)
        return 1
    ban = banner.Banner(prof, "vcs")

    compiled.daidir = daidir
    try:
        be = NvcBackend(compiled, con.out)
    except BackendError as e:
        con.err("vamos: error: %s" % e)
        return 1

    known = _read_tools(daidir)
    used = [("vamos", tools.version_of("vamos", tools.launcher()), tools.launcher())]
    for n, p in be.run_tools():
        v = known.get(n, {}).get("version") if known.get(n, {}).get("path") == p else None
        used.append((n, v or tools.version_of(n, p), p))
    if compiled.ams:          # the analog engine the compile chose (recorded with its version)
        eng = ("VACASK", "OpenVAF-r") if compiled.ams.get("engine") == "vacask" else ("Xyce",)
        used += [(n, known[n].get("version") or "?", known[n].get("path") or "?")
                 for n in eng if n in known]
    con.out(banner.provenance(used, "simv"))
    stop_fs = finish_fs(rt, compiled, con)
    report_unmapped(rt, con.err)
    bad = strict_failures(rt)
    if opts.get("strict") and bad:
        con.err("vamos: error: --vamos-strict: %d unsupported/unknown option(s): %s"
                % (len(bad), " ".join(bad)))
        return 1

    c0 = cpu_time()
    try:
        if compiled.ams:
            from vamos.backends import cosim      # AMS jobs: nvc + the analog engine
            rc, simtime = cosim.run(be, compiled, rt, con, opts, stop_fs)
        else:
            rc, simtime = be.run(compiled.tops[0], rt.plusargs, con.out, con.err, stop_fs)
    except KeyboardInterrupt:                 # a Ctrl-C outside the run itself
        con.err("vamos: note: interrupted (SIGINT)")
        con.close()
        return _end_with_signal(signal.SIGINT)
    if simtime is not None:                   # None: nothing was simulated
        t = ban.text("run_footer", simtime=simtime, cpu=cpu_time() - c0,
                     status="ok" if rc == 0 else "exit %d" % rc)
        if t is not None:
            con.out(t)
    con.close()
    if be.interrupted is not None:
        return _end_with_signal(be.interrupted)
    return rc


def main(args: List[str], opts: dict) -> int:
    daidir = opts.get("daidir")
    if not daidir:
        Console().err("vamos: error: -simv needs --vamos-daidir=<dir> "
                      "(run the generated ./simv instead)")
        return 1
    return run_daidir(daidir, args, opts)
