"""simv personality: the runtime side of the vcs two-step flow.

The generated ./simv stub runs `vamos -simv --vamos-daidir=<dir> args...`.
Every +plusarg reaches the simulation ($test$plusargs / $value$plusargs);
simv's own options are handled from the table below.
"""

import json
import os
from typing import List, Tuple

from vamos import banner, tools
from vamos.backends.nvc import BackendError, NvcBackend, cpu_time
from vamos.console import Console
from vamos.job import IGNORED, NOTED, UNKNOWN, UNSUPPORTED, Job
from vamos.optable import Opt, Table, report_unmapped, scan, strict_failures


def _plusarg(job: Job, tok):
    job.plusargs.append(tok)


def _seed(job: Job, val):
    job.seed = val
    job.plusargs.append("+ntb_random_seed=" + val)


def _run_log(job: Job, val):
    job.run_log = val if os.path.isabs(val) else os.path.join(job.cwd, val)


OPTIONS = [
    Opt("-l", "next", _run_log),
    Opt("+ntb_random_seed", "eq", _seed),
    Opt("+vcs+lic+wait", "flag", IGNORED),
    Opt("-licqueue", "flag", IGNORED),
    Opt("-q", "flag", IGNORED),
    Opt("-k", "next", IGNORED),
    Opt("+vcs+finish+", "prefix", UNSUPPORTED, "simulation time limit"),
    Opt("+vcs+stop+", "prefix", UNSUPPORTED),
    Opt("-ucli", "flag", UNSUPPORTED, "UCLI scripting arrives in a later phase"),
    Opt("-do", "next", UNSUPPORTED, "UCLI scripts arrive in a later phase"),
    Opt("-gui", "prefix", NOTED, "no GUI"),
    Opt("-verdi", "flag", NOTED, "Verdi is not available"),
    Opt("+fsdbfile+", "prefix", NOTED, "FSDB is not produced"),
    Opt("+vpdfile+", "prefix", NOTED, "VPD is not produced"),
    Opt("-assert", "next", NOTED),
    Opt("-cm", "next", UNSUPPORTED),
    Opt("-cm_dir", "next", UNSUPPORTED),
    Opt("-cm_name", "next", UNSUPPORTED),
    Opt("+notimingcheck", "flag", NOTED),
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


def run_daidir(daidir: str, args: List[str], opts: dict) -> int:
    con = Console()
    try:
        with open(os.path.join(daidir, "vamos.job.json")) as fh:
            compiled = Job.from_json(fh.read())
    except (OSError, ValueError) as e:
        con.err("vamos: error: %s is not a vamos simulation directory (%s)" % (daidir, e))
        return 1

    append = "--vamos-append-log" in args
    args = [a for a in args if a != "--vamos-append-log"]
    rt = Job(personality="simv", argv=list(args), cwd=os.getcwd(), daidir=daidir)
    scan(TABLE, args, rt, _positional, _unknown)
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
    con.out(banner.provenance(used, "simv"))
    report_unmapped(rt, con.err)
    if opts.get("strict") and strict_failures(rt):
        con.err("vamos: error: --vamos-strict: " + " ".join(strict_failures(rt)))
        return 1

    c0 = cpu_time()
    rc, simtime = be.run(compiled.tops[0], rt.plusargs, con.out, con.err)
    t = ban.text("run_footer", simtime=simtime, cpu=cpu_time() - c0,
                 status="ok" if rc == 0 else "exit %d" % rc)
    if t is not None:
        con.out(t)
    con.close()
    return rc


def main(args: List[str], opts: dict) -> int:
    daidir = opts.get("daidir")
    if not daidir:
        Console().err("vamos: error: -simv needs --vamos-daidir=<dir> "
                      "(run the generated ./simv instead)")
        return 1
    return run_daidir(daidir, args, opts)
