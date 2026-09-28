"""vcs personality: the two-step flow `vcs [opts] files...` then `./simv`.

Compiles through the nvc backend into <exe>.daidir/, writes the ./simv stub,
and with -R runs it straight away.  Options come from the VCS User Guide's
compile-time option index; ones not implemented yet are accepted and reported
(see optable dispositions), never a usage error.
"""

import os
import shlex
from typing import List

from vamos import argscan, banner, tools
from vamos.backends.nvc import BackendError, NvcBackend, cpu_time
from vamos.console import Console
from vamos.job import IGNORED, NOTED, UNKNOWN, UNSUPPORTED, Job, Source
from vamos.optable import Opt, ScanError, Table, report_unmapped, scan, strict_failures
from vamos.personalities import simv as simv_mod

VERILOG_EXTS = {".v": "verilog", ".vh": "verilog", ".vlib": "verilog",
                ".sv": "sv", ".svh": "sv", ".svp": "sv"}
VHDL_EXTS = {".vhd", ".vhdl", ".vho"}
C_EXTS = {".c", ".cc", ".cpp", ".cxx", ".o", ".a", ".so"}


def _set(attr):
    def act(job: Job, val):
        setattr(job, attr, val)
    return act


def _extend(attr):
    def act(job: Job, val):
        getattr(job, attr).extend(val)
    return act


def _abs(job: Job, p: str) -> str:
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(job.cwd, p))


def _incdir(job: Job, val):
    job.incdirs.extend(_abs(job, d) for d in val)


def _define(job: Job, val):
    for d in val:
        k, eq, v = d.partition("=")
        job.defines[k] = v if eq else None


def _libfile(job: Job, val):
    job.lib_files.append(_abs(job, val))


def _libdir(job: Job, val):
    job.lib_dirs.append(_abs(job, val))
    job.note("-y " + val, NOTED, "library directories are recorded but modules are "
             "not yet pulled from them; list the files or use -v")


def _top(job: Job, val):
    job.tops.append(val)


def _sverilog(job: Job, _):
    job.std = "sv"


def _run_now(job: Job, _):
    job.run = True


def _log(job: Job, val):
    job.log = _abs(job, val)


def _mdir(job: Job, val):
    job.mdir = val


def _debug(name):
    def act(job: Job, val):
        job.debug.append(name + (val or ""))
        job.note(name + (val or ""), NOTED,
                 "waves/debug database not produced yet (planned: FST via nvc)")
    return act


def _ntb_opts(job: Job, val):
    if "uvm" in val:
        job.note("-ntb_opts " + val, UNSUPPORTED, "UVM needs SystemVerilog class support "
                 "in sv2ghdl; tracked separately")
    else:
        job.note("-ntb_opts " + val, NOTED)


def _cm(job: Job, val):
    job.coverage.append(val)
    job.note("-cm " + val, UNSUPPORTED, "coverage mapping onto nvc --cover is planned")


OPTIONS = [
    # outputs and flow
    Opt("-o", "next", _set("exe")),
    Opt("-R", "flag", _run_now),
    Opt("-l", "next", _log),
    Opt("-Mdir", "eq", _mdir),
    Opt("-top", "next", _top),
    # sources and libraries
    Opt("+incdir+", "plus", _incdir),
    Opt("+define+", "plus", _define),
    Opt("-v", "next", _libfile),
    Opt("-y", "next", _libdir),
    Opt("+libext+", "plus", _extend("lib_exts")),
    Opt("-sverilog", "flag", _sverilog),
    Opt("+v2k", "flag", IGNORED),
    Opt("+systemverilogext+", "plus", NOTED, "extension list recorded; .sv/.svh are SV already"),
    Opt("+verilog2001ext+", "plus", IGNORED),
    Opt("+verilog1995ext+", "plus", IGNORED),
    Opt("-timescale", "eq", _set("timescale")),
    Opt("-override_timescale", "eq", NOTED, "`timescale in the sources is used"),
    Opt("-pvalue+", "prefix", UNSUPPORTED, "parameter overrides"),
    Opt("-parameters", "next", UNSUPPORTED, "parameter override file"),
    # debug / waves / GUI
    Opt("-debug_access", "prefix", _debug("-debug_access")),
    Opt("-debug_region", "prefix", IGNORED),
    Opt("-debug_all", "flag", _debug("-debug_all")),
    Opt("-debug_pp", "flag", _debug("-debug_pp")),
    Opt("-debug", "flag", _debug("-debug")),
    Opt("-kdb", "prefix", NOTED, "Verdi KDB is not produced"),
    Opt("+vcs+vcdpluson", "flag", _debug("+vcs+vcdpluson")),
    Opt("-gui", "prefix", NOTED, "no GUI; waves will be written for an external viewer"),
    Opt("-verdi", "flag", NOTED, "Verdi is not available"),
    Opt("-ucli", "flag", NOTED, "UCLI scripting arrives in a later phase"),
    Opt("-lca", "flag", IGNORED),
    # coverage / assertions / verification
    Opt("-cm", "next", _cm),
    Opt("-cm_dir", "next", UNSUPPORTED),
    Opt("-cm_name", "next", UNSUPPORTED),
    Opt("-cm_hier", "next", UNSUPPORTED),
    Opt("-cm_tgl", "next", UNSUPPORTED),
    Opt("-assert", "next", NOTED, "assertion control options not mapped yet"),
    Opt("-ntb_opts", "next", _ntb_opts),
    Opt("-xprop", "prefix", UNSUPPORTED, "nvc X semantics differ; see docs"),
    # timing
    Opt("+notimingcheck", "flag", NOTED, "timing checks are not modelled"),
    Opt("+nospecify", "flag", NOTED, "specify blocks are not modelled"),
    Opt("+delay_mode_zero", "flag", NOTED),
    Opt("+delay_mode_unit", "flag", NOTED),
    Opt("+delay_mode_path", "flag", NOTED),
    Opt("+delay_mode_distributed", "flag", NOTED),
    Opt("+maxdelays", "flag", NOTED),
    Opt("+mindelays", "flag", NOTED),
    Opt("+typdelays", "flag", NOTED),
    # foreign code
    Opt("-P", "next", UNSUPPORTED, "PLI/VPI tables (planned via nvc --load)"),
    Opt("-load", "next", UNSUPPORTED, "VPI libraries (planned via nvc --load)"),
    Opt("-CFLAGS", "next", UNSUPPORTED, "DPI-C"),
    Opt("-LDFLAGS", "next", UNSUPPORTED, "DPI-C"),
    Opt("-cpp", "next", UNSUPPORTED, "DPI-C"),
    Opt("-cc", "next", UNSUPPORTED, "DPI-C"),
    # environment, licensing, build speed, messages: meaningless here
    Opt("-full64", "flag", IGNORED),
    Opt("-licqueue", "flag", IGNORED),
    Opt("-licwait", "next", IGNORED),
    Opt("+vcs+lic+wait", "flag", IGNORED),
    Opt("+vcs+lic+vcsi", "flag", IGNORED),
    Opt("-ID", "flag", IGNORED),
    Opt("-id", "flag", IGNORED),
    Opt("-notice", "flag", IGNORED),
    Opt("-q", "flag", IGNORED),
    Opt("-V", "flag", IGNORED),
    Opt("-nc", "flag", IGNORED),
    Opt("-j", "prefix", IGNORED),
    Opt("-fastcomp", "prefix", IGNORED),
    Opt("-partcomp", "prefix", IGNORED),
    Opt("-pcmakeprof", "flag", IGNORED),
    Opt("+lint=", "prefix", IGNORED),
    Opt("+warn=", "prefix", IGNORED),
    Opt("-error=", "prefix", IGNORED),
    Opt("-suppress=", "prefix", IGNORED),
    Opt("+error+", "prefix", IGNORED),
    Opt("-ignore", "next", IGNORED),
    Opt("+vpi", "flag", IGNORED),
    Opt("+acc", "prefix", IGNORED),
    Opt("+cli", "prefix", IGNORED),
    Opt("-picarchive", "flag", IGNORED),
    Opt("-reportstats", "flag", IGNORED),
    Opt("-diag", "next", IGNORED),
]

TABLE = Table(OPTIONS)

HELP = """usage: vcs [options] source-files...      (vamos vcs personality)

Compiles Verilog/SystemVerilog through sv2ghdl + nvc into ./simv.
Common options: -o <exe>  -R  -l <log>  -top <mod>  -f/-F <file>
                +incdir+<dir>  +define+<m>[=<v>]  -v <file>  -sverilog
vamos options:  --vamos-banner=<name|path|none>  --vamos-strict
                --vamos-verbose  --vamos-licenses
Unimplemented VCS options are accepted and reported, not rejected.
See docs/VAMOS_PLAN.md."""


def _positional(job: Job, tok: str) -> None:
    path = _abs(job, tok)
    ext = os.path.splitext(tok)[1].lower()
    if ext in VHDL_EXTS:
        job.note(tok, UNSUPPORTED, "VHDL sources go through vhdlan in the three-step "
                 "flow (phase 2)")
        return
    if ext in C_EXTS:
        job.note(tok, UNSUPPORTED, "C/C++ sources for DPI/PLI")
        return
    lang = VERILOG_EXTS.get(ext, "verilog")
    job.sources.append(Source(path, "sv" if job.std == "sv" and lang == "verilog" else lang))


def _unknown(job: Job, tok: str, args: List[str], i: int) -> int:
    if tok.startswith("+"):
        # VCS warns about and ignores unknown compile-time plusargs.  With -R
        # they reach the simulation, as they do under VCS.
        job.plusargs.append(tok)
        job.note(tok, IGNORED)
        return 1
    job.note(tok, UNKNOWN)
    return 1


def build_job(args: List[str], cwd: str) -> Job:
    job = Job(personality="vcs", argv=list(args), cwd=cwd)
    expanded = argscan.expand_option_files(args, cwd)
    scan(TABLE, expanded, job, _positional, _unknown)
    job.exe = _abs(job, job.exe)
    job.daidir = job.exe + ".daidir"
    return job


def main(args: List[str], opts: dict) -> int:
    con = Console()
    if any(a in ("-h", "-help", "--help") for a in args):
        con.out(HELP)
        return 0
    cwd = os.getcwd()
    try:
        job = build_job(args, cwd)
    except (argscan.ArgError, ScanError) as e:
        con.err("vamos: error: %s" % e)
        return 1
    if job.log:
        con.open_log(job.log)
    try:
        prof = banner.load_profile("vcs", opts.get("banner"))
    except ValueError as e:
        con.err("vamos: error: %s" % e)
        return 1
    ban = banner.Banner(prof, "vcs")

    missing = [s.path for s in job.sources + [Source(p, "verilog") for p in job.lib_files]
               if not os.path.isfile(s.path)]
    if missing:
        for m in missing:
            con.err("vamos: error: source file '%s' cannot be opened" % m)
        return 1
    if not job.sources:
        con.err("vamos: error: no source files given")
        return 1

    try:
        be = NvcBackend(job, con.out)
    except BackendError as e:
        con.err("vamos: error: %s" % e)
        return 1

    t = ban.text("compile_start")
    if t is not None:
        con.out(t)
    used = [("vamos", tools.version_of("vamos", tools.launcher()), tools.launcher())]
    used += [(n, tools.version_of(n, p), p) for n, p in be.compile_tools()]
    con.out(banner.provenance(used, "vcs"))
    report_unmapped(job, con.err)
    if opts.get("strict"):
        bad = strict_failures(job)
        if bad:
            con.err("vamos: error: --vamos-strict: %d unsupported/unknown option(s): %s"
                    % (len(bad), " ".join(bad)))
            return 1

    os.makedirs(job.daidir, exist_ok=True)
    c0 = cpu_time()
    try:
        top = be.analyse()
        c1 = cpu_time()
        be.elaborate(top)
    except BackendError as e:
        con.err("vamos: error: %s" % e)
        t = ban.text("compile_failed")
        if t is not None:
            con.out(t)
        return 1
    c2 = cpu_time()
    if not job.tops:
        job.tops = [top]
    deferred = be.deferred_modules()
    if top in deferred:
        con.err("vamos: warning: sv2ghdl could not translate top module '%s' (left as an "
                "empty deferred stub) - the simulation will do nothing. Run with "
                "--vamos-verbose and see %s/nvc/_mods.vhd" % (top, job.daidir))
    elif deferred and os.environ.get("VAMOS_VERBOSE"):
        con.err("vamos: note: deferred modules: " + " ".join(deferred))

    with open(os.path.join(job.daidir, "vamos.job.json"), "w") as fh:
        fh.write(job.to_json())
    simv_mod.write_tools(job.daidir, used)
    write_stub(job)

    t = ban.text("compile_tops", tops_lines="\n".join("       " + x for x in job.tops))
    if t is not None:
        con.out(t)
    t = ban.text("compile_done", exe=os.path.basename(job.exe),
                 cpu_compile=c1 - c0, cpu_elab=c2 - c1)
    if t is not None:
        con.out(t)

    if job.run:
        con.close()
        run_args = list(job.plusargs)
        if job.log:
            run_args += ["-l", job.log, "--vamos-append-log"]
        return simv_mod.run_daidir(job.daidir, run_args, opts)
    con.close()
    return 0


def write_stub(job: Job) -> None:
    """./simv: a small script that re-enters vamos's simv personality."""
    body = ("#!/bin/sh\n"
            "# Generated by vamos (vcs personality). Runs the design compiled into\n"
            "# %s; accepts simv runtime options.\n"
            "exec %s -simv --vamos-daidir=%s \"$@\"\n"
            % (os.path.basename(job.daidir), shlex.quote(tools.launcher()),
               shlex.quote(job.daidir)))
    tmp = job.exe + ".vamos-tmp"
    with open(tmp, "w") as fh:
        fh.write(body)
    os.chmod(tmp, 0o755)
    os.replace(tmp, job.exe)
