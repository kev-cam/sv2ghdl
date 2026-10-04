"""vcs personality: the two-step flow `vcs [opts] files...` then `./simv`.

Compiles through the nvc backend into <exe>.daidir/, writes the ./simv stub,
and with -R runs it straight away.  Options come from the VCS User Guide's
compile-time option index; ones not implemented yet are accepted and reported
(see optable dispositions), never a usage error.

A compile first makes ./simv a stub that refuses to run and removes
<daidir>/vamos.job.json (VCS disables the old simv the same way when it starts
relinking): a compile that fails or is interrupted never leaves a half-rebuilt
daidir runnable.  A first compile (no ./simv before it) that fails removes the
stub again, so it leaves no ./simv, as VCS does.  The job record and the real
stub are written last, only when the compile succeeds.  The real stub runs the
daidir next to itself, as VCS's simv does, so a moved or copied simv + daidir
pair runs its own copy.

A plain (digital) compile (docs/VAMOS_AMS_DESIGN.md §1.2-§1.4, §8):
  preprocess  iverilog -E of the sources and the -v files into <daidir>/pp/pp.v
              (verilog_ports.preprocess: the precision for +vcs+finish+N, the
              -override_timescale rewrite); a failure is a compile error
  libraries   VCS's -v rule: a library module is used only when no source file
              defines it, and the first -v file that defines it wins; every other
              copy is blanked in pp.v (lines kept)
  tops        -top (repeatable, -top a+b+ too), checked against the modules
              defined; else every module nothing instantiates
              (verilog_ports.find_roots), as VCS elaborates each of them
  translate   pp.v through iverilog-sv2ghdl (NvcBackend.analyse); a top left as a
              deferred stub is an error quoting iverilog's reason from
              <daidir>/nvc/iverilog.log; tgt-vhdl's "Unsupported system task /
              function" comments are warnings at the user's file:line, except
              the VCD tasks': $dumpfile/$dumpvars (and +vcs+dumpvars) are
              recorded in job.dump for ./simv's waves (dump_request), and the
              others get notes
  elaborate   the top; with several tops a generated entity (vamos_tops) that
              instantiates each (inputs tied to Z, outputs open), so %m and
              hierarchical names stay as VCS prints them.  job.tops[0] is the
              entity nvc elaborates and ./simv runs; with a wrapper, job.tops is
              [wrapper, top1, top2, ...]
--vamos-strict makes every warning of a plain compile an error too.
"""

import dataclasses
import os
import re
import shlex
import subprocess
from typing import List, Optional, Sequence, Tuple

from vamos import argscan, banner, tools
from vamos.backends.nvc import DEFAULT_DUMPFILE, BackendError, NvcBackend, cpu_time
from vamos.console import Console
from vamos.job import IGNORED, INAPPLICABLE, NOTED, UNKNOWN, UNSUPPORTED, Job, Source
from vamos.optable import (Opt, ScanError, Table, report_unmapped, scan, strict_failures,
                           strict_message, vamos_option_effects, vamos_option_text,
                           vamos_options_help)
from vamos.personalities import simv as simv_mod

VERILOG_EXTS = {".v": "verilog", ".vh": "verilog", ".vlib": "verilog",
                ".sv": "sv", ".svh": "sv", ".svp": "sv"}
VHDL_EXTS = {".vhd", ".vhdl", ".vho"}
C_EXTS = {".c", ".cc", ".cpp", ".cxx", ".o", ".a", ".so"}

WRAPPER = "vamos_tops"        # the entity over several top-level modules
JOB_FILE = "vamos.job.json"


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
    # VCS: "-top a -top b" or "-top a+b+" for several top-level modules.
    job.tops.extend(t for t in val.split("+") if t)


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
                 "no debug database or VPD/FSDB waves are produced; $dumpvars writes a VCD")
    return act


def _dumpvars_all(job: Job, _):
    # +vcs+dumpvars: "a substitute for entering the $dumpvars system task, without
    # arguments, in your Verilog code" (a compile-time option; dump_request)
    setattr(job, "dumpvars_all", True)


def _ntb_opts(job: Job, val):
    if "uvm" in val:
        job.note("-ntb_opts " + val, UNSUPPORTED, "UVM needs SystemVerilog class support "
                 "in sv2ghdl; tracked separately")
    else:
        job.note("-ntb_opts " + val, NOTED)


def _cm(job: Job, val):
    job.coverage.append(val)
    job.note("-cm " + val, UNSUPPORTED, "coverage mapping onto nvc --cover is planned")


def _ams(job: Job, val):
    # -ad / +ad read ./vcsAD.init; -ad=<file> / +ad=<file> name the control file.
    job.ams_control = _abs(job, val) if val else ""


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
    Opt("-override_timescale", "eq", _set("override_timescale")),
    Opt("-pvalue+", "prefix", UNSUPPORTED, "parameter overrides"),
    Opt("-parameters", "next", UNSUPPORTED, "parameter override file"),
    # runtime options compiled into simv
    Opt("+plusarg_save", "flag", UNSUPPORTED, "runtime options are not compiled into simv: give them "
        "to ./simv"),
    Opt("+plusarg_ignore", "flag", NOTED, "runtime options are never compiled into simv"),
    # debug / waves / GUI
    Opt("-debug_access", "prefix", _debug("-debug_access")),
    Opt("-debug_region", "prefix", IGNORED),
    Opt("-debug_all", "flag", _debug("-debug_all")),
    Opt("-debug_pp", "flag", _debug("-debug_pp")),
    Opt("-debug", "flag", _debug("-debug")),
    Opt("-kdb", "prefix", NOTED, "Verdi KDB is not produced"),
    Opt("+vcs+vcdpluson", "flag", _debug("+vcs+vcdpluson")),
    Opt("+vcs+dumpvars", "flag", _dumpvars_all),
    Opt("-gui", "prefix", NOTED, "no GUI ($dumpvars writes a VCD for a wave viewer)"),
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
    # AMS (docs/VAMOS_AMS_DESIGN.md §8).  -ad is never a prefix: it would
    # swallow -adopt and -ad_*.
    Opt("-ad", "flag", _ams),
    Opt("-ad", "eq", _ams),
    Opt("+ad", "flag", _ams),
    Opt("+ad=", "prefix", _ams),
    Opt("-ams", "flag", UNSUPPORTED, "the Verilog-AMS flow is not in vcs-ams v1"),
    Opt("-ams_discipline", "next", NOTED),
    Opt("-ams_dresolution", "flag", NOTED),
    Opt("-ams_iereport", "flag", NOTED, "the interface-element report is always written"),
    Opt("-ad_iereport", "flag", NOTED, "the interface-element report is always written"),
    Opt("-realport", "flag", NOTED),
    Opt("-adopt", "next", NOTED),
    Opt("-xlrm", "next", NOTED),
    Opt("-wreal", "next", NOTED),
    Opt("+verilogamsext+", "plus", NOTED),
    Opt("-sysc", "eq", UNSUPPORTED, "SystemC"),
    Opt("+msvsdf", "flag", NOTED),
    Opt("+msvsdfext", "flag", NOTED),
    Opt("+bidir+", "prefix", NOTED),
    Opt("+print+bidir+warn", "flag", NOTED),
]

TABLE = Table(OPTIONS)

_HELP_HEAD = {
    "vcs": "usage: vcs [options] source-files...          (vamos vcs personality)\n\n"
           "Compiles Verilog/SystemVerilog through sv2ghdl + nvc into ./simv; run ./simv\n"
           "afterwards, or add -R.  -ad[=<file>] or +ad[=<file>] makes it an AMS compile, as\n"
           "vcs-ams.",
    "vcs-ams": "usage: vcs-ams [options] source-files...      (vamos vcs-ams personality)\n\n"
               "vcs with -ad implied: compiles a Verilog design whose SPICE cells the control\n"
               "file names (./vcsAD.init, or -ad=<file>) into ./simv.  nvc runs the digital\n"
               "side; the analog side runs on VACASK (the default) or Xyce (--vamos-analog=xyce).",
}

_HELP_OPTIONS = """Options vamos acts on:
  -o <exe>  -R  -l <log>  -top <mod>[+<mod>...]  -f/-F/-file <file>  -v <file>
  +incdir+<dir>  +define+<m>[=<v>]  -sverilog  -timescale=<u>/<p>
  -override_timescale=<u>/<p>  -ad[=<file>]  +ad[=<file>]
Every other VCS option is accepted: it is ignored, or gets a note or a warning
(--vamos-strict makes the warnings errors); none is a usage error."""


def help_text(personality: str = "vcs") -> str:
    return ("%s\n\n%s\n\nvamos options (--vamos-<key>[=<value>]; [..] says where an option has an "
            "effect):\n%s\n\nRuntime options and +plusargs go to ./simv.\nThe user guide: %s"
            % (_HELP_HEAD.get(personality, _HELP_HEAD["vcs"]), _HELP_OPTIONS,
               vamos_options_help(personality), tools.guide_path()))


HELP = help_text("vcs")


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
    if ext in (".va", ".vams"):
        job.note(tok, UNSUPPORTED, "Verilog-A/AMS sources are the Verilog-AMS flow (not in "
                 "vcs-ams v1); Verilog-A goes into the SPICE netlist with .hdl")
        return
    lang = VERILOG_EXTS.get(ext, "verilog")
    job.sources.append(Source(path, "sv" if job.std == "sv" and lang == "verilog" else lang))


def _unknown(job: Job, tok: str, args: List[str], i: int) -> int:
    if tok.startswith("--vamos-"):
        # cli takes --vamos-* off the command line; this one came from an option file.
        job.note(tok, UNSUPPORTED, "vamos options are read from the command line only, not "
                 "from option files")
        return 1
    if tok.startswith("+"):
        # An unknown compile-time plusarg reaches the simulation of a -R run, as
        # under VCS; without -R build_job notes it.
        job.plusargs.append(tok)
        return 1
    job.note(tok, UNKNOWN)
    return 1


def build_job(args: List[str], cwd: str, personality: str = "vcs") -> Job:
    job = Job(personality=personality, argv=list(args), cwd=cwd)
    expanded = argscan.expand_option_files(args, cwd)
    scan(TABLE, expanded, job, _positional, _unknown)
    if personality == "vcs-ams" and job.ams_control is None:
        job.ams_control = ""             # vcs-ams: -ad is implied (./vcsAD.init)
    if not job.run:
        for p in job.plusargs:
            job.note(p, NOTED, "a plusarg given to vcs reaches only a -R run, as under VCS; "
                     "give it to ./simv")
    job.exe = _abs(job, job.exe)
    job.daidir = job.exe + ".daidir"
    return job


# -- the plain (digital) compile ---------------------------------------------------------------

def _flush(con: Console, notes, strict_on: bool, failure: str) -> None:
    """Print notes (warnings as errors under --vamos-strict); BackendError(failure) on an error."""
    from vamos.notes import has_errors, strict
    ns = strict(list(notes), strict_on)
    for n in ns:
        con.err("vamos: " + n.text())
    if has_errors(ns):
        raise BackendError(failure)


def library_rule(pp) -> Tuple[str, List[str]]:
    """VCS's -v rule on the preprocessed stream (verilog_ports.library_rule, which the AMS
    flow applies too): a module defined in a -v library file is used only when no source
    file defines it, and among -v files the first that defines it wins.  Returns pp's text
    with every other library copy blanked (line numbers kept) and the names of the modules
    that had one.  Two definitions in source files are left for iverilog to report."""
    from vamos.ams import verilog_ports
    return verilog_ports.library_rule(pp)


def plain_tops(job: Job, pp) -> List[str]:
    """The top-level modules of a plain compile: the -top modules, each checked against the
    modules defined, else every module nothing instantiates (modules only in -v files
    excluded), as VCS elaborates them.  Raises BackendError naming what is available."""
    import difflib
    from vamos.ams import verilog_ports
    roots = verilog_ports.find_roots(pp)
    if job.tops:
        tops: List[str] = []
        for t in job.tops:
            if t not in tops:
                tops.append(t)
        bad = [t for t in tops if t not in pp.modules]
        if bad:
            msgs = []
            for t in bad:
                near = difflib.get_close_matches(t, list(pp.modules), 1)
                msgs.append("-top %s: no module %s in the Verilog sources%s"
                            % (t, t, " (did you mean %s?)" % near[0] if near else ""))
            raise BackendError("; ".join(msgs) + "; the top-level modules are: %s"
                               % (", ".join(roots) or "none (every module is instantiated)"))
        return tops
    if not roots:
        raise BackendError("no top-level module: every module is instantiated, or only in a -v "
                           "library file; give -top <module>")
    return roots


_LOC = re.compile(r"(?:[^\s:()]*/)?(?:_norm\.sv|_pp\.v):(\d+)")


def deferred_reason(log_path: str, module: str, pp=None) -> str:
    """What iverilog said when sv2vhdl-modules could not translate `module`: its section of
    iverilog.log, with nvc/_norm.sv and _pp.v lines shown at the user's file:line (pp)."""
    try:
        with open(log_path, errors="replace") as fh:
            text = fh.read()
    except OSError:
        return "no iverilog.log"
    head = "=== sv2vhdl-modules: iverilog -tvhdl -s %s: " % module
    status, lines, on = "", [], False
    for ln in text.splitlines():
        if ln.startswith(head):
            on, status, lines = True, ln[len(head):].strip(), []
            continue
        if ln.startswith("=== "):
            on = False
            continue
        if on and ln.strip() and not re.match(r"^\s*error: Code generation had \d+ error", ln):
            lines.append(ln.strip())
    if pp is not None:
        lines = [_LOC.sub(lambda m: pp.origin(int(m.group(1))), ln) for ln in lines]
    if not lines:
        return status or "iverilog gave no reason"
    return "; ".join(lines[:3]) + ("; ..." if len(lines) > 3 else "")


_PROVENANCE = re.compile(r"^--\s*Generated from Verilog module (\S+) \(")
_ENTITY_DECL = re.compile(r"^\s*entity\s+(\w+)\s+is\b", re.I)


def top_entity(vhd: str, module: str) -> str:
    """The VHDL entity tgt-vhdl made of top-level module `module`: tgt-vhdl renames a module
    whose name is a VHDL reserved word (pipe -> pipe_module__50dc, loop, view, ...), and the
    "-- Generated from Verilog module <m> (...)" comment right above the entity keeps the
    Verilog name (the comment lines between them are the entity's own).  The last such
    entity: a translation run writes its root last.  `module` itself when none is found."""
    found, cur = module, None
    for line in vhd.splitlines():
        s = line.strip()
        m = _PROVENANCE.match(s)
        if m:
            cur = m.group(1)
            continue
        if s.startswith("--"):
            continue
        e = _ENTITY_DECL.match(line)
        if e and cur == module:
            found = e.group(1)
        cur = None
    return found


def _entity_ports(vhd: str, name: str) -> Optional[List[Tuple[str, str, str, bool]]]:
    """(port, mode, type, has a default) of entity `name` in VHDL text; None: no such entity."""
    m = re.search(r"(?im)^\s*entity\s+%s\s+is\b" % re.escape(name), vhd)
    if not m:
        return None
    rest = vhd[m.end():]
    e = re.search(r"(?im)^\s*end\b", rest)
    body = re.sub(r"--[^\n]*", "", rest[:e.start()] if e else rest)
    p = re.search(r"(?i)\bport\s*\(", body)
    if not p:
        return []
    depth, i = 1, p.end()
    while i < len(body) and depth:
        depth += {"(": 1, ")": -1}.get(body[i], 0)
        i += 1
    items, depth, cur = [], 0, ""
    for ch in body[p.end():i - 1]:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if ch == ";" and depth == 0:
            items.append(cur)
            cur = ""
        else:
            cur += ch
    items.append(cur)
    out = []
    for it in items:
        names, colon, decl = it.partition(":")
        if not colon:
            continue
        mm = re.match(r"(?is)\s*(in|out|inout|buffer|linkage)\b(.*)$", decl)
        mode, typ = (mm.group(1).lower(), mm.group(2)) if mm else ("in", decl)
        typ, _, default = typ.partition(":=")
        for n in names.split(","):
            if n.strip():
                out.append((n.strip(), mode, " ".join(typ.split()), bool(default.strip())))
    return out


def _tie_off(typ: str) -> Optional[str]:
    """An actual for an input port of a top that VCS leaves undriven: Z, or 0 for a number."""
    base = re.sub(r"\s*\(.*$", "", typ.strip().lower(), flags=re.S)
    return {"logic3d": "L3D_Z", "resolved_logic3d": "L3D_Z",
            "logic3d_vector": "(others => L3D_Z)", "resolved_logic3d_vector": "(others => L3D_Z)",
            "std_logic": "'Z'", "std_ulogic": "'Z'",
            "std_logic_vector": "(others => 'Z')", "std_ulogic_vector": "(others => 'Z')",
            "real": "0.0", "integer": "0", "natural": "0"}.get(base)


def undriven_top_inputs(pp, vhd: str, top: str) -> list:
    """The one top-level module's logic input ports, as a warning: nvc elaborates the top
    alone, so an input it cannot associate takes its type's first value (0), where VCS leaves
    a top-level input undriven (z).  Said, not modelled: several tops go through
    wrapper_vhdl, which ties inputs to Z."""
    from vamos.notes import warning
    ports = _entity_ports(vhd, top_entity(vhd, top)) or []
    logic = [p for p, mode, typ, has_default in ports
             if mode == "in" and not has_default and "Z" in (_tie_off(typ) or "")]
    if not logic:
        return []
    defs = pp.modules.get(top, [])
    return [warning(defs[0].origin if defs else "",
                    "top-level module %s has input port%s %s that nothing drives: %s 0 in this "
                    "simulation, where VCS leaves %s undriven (z)"
                    % (top, "" if len(logic) == 1 else "s", ", ".join(logic),
                       "it reads" if len(logic) == 1 else "they read",
                       "it" if len(logic) == 1 else "them"))]


def wrapper_vhdl(name: str, tops: Sequence[str], vhd: str) -> str:
    """VHDL for one entity that instantiates every top-level module: nvc elaborates one
    top, VCS every top-level module.  Each top's own translation (sv2vhdl-modules runs each
    module as its own root) keeps its %m strings and hierarchical names rooted at it.  An
    input port is tied to Z (an undriven top-level input), outputs and inouts are open.
    Raises BackendError for a top with no entity or an input it cannot tie off."""
    stmts = []
    for k, t in enumerate(tops, 1):
        ent = top_entity(vhd, t)
        ports = _entity_ports(vhd, ent)
        if ports is None:
            raise BackendError("the translation (design.vhd) has no entity for top module %s" % t)
        assoc = []
        for p, mode, typ, has_default in ports:
            if mode != "in" or has_default:
                continue
            v = _tie_off(typ)
            if v is None:
                raise BackendError("top module %s: input port %s (%s) cannot be left unconnected "
                                   "beside other top-level modules; give -top" % (t, p, typ))
            assoc.append("%s => %s" % (p, v))
        stmt = "  top%d: entity work.%s" % (k, ent)
        if assoc:
            stmt += "\n    port map (%s)" % ", ".join(assoc)
        stmts.append(stmt + ";  -- top-level module %s" % t)
    return ("-- Generated by vamos: VCS elaborates every top-level module (%s); nvc elaborates\n"
            "-- one entity, so this one instantiates each of them.  Inputs are tied to Z,\n"
            "-- outputs and inouts are open.\n"
            "library sv2vhdl;\nuse sv2vhdl.logic3d_types_pkg.all;\n\n"
            "entity %s is\nend entity;\n\narchitecture vamos of %s is\nbegin\n%s\nend architecture;\n"
            % (", ".join(tops), name, name, "\n".join(stmts)))


def _wrapper_name(pp, vhd: str) -> str:
    taken = {m.lower() for m in pp.modules} | {x.lower() for x in re.findall(r"(?im)^\s*entity\s+(\w+)", vhd)}
    name, k = WRAPPER, 0
    while name.lower() in taken:
        k += 1
        name = "%s_%d" % (WRAPPER, k)
    return name


def _elaborate_wrapper(be: NvcBackend, con: Console, name: str, text: str) -> None:
    path = os.path.join(be.workdir, name + ".vhd")
    with open(path, "w") as fh:
        fh.write(text)
    std = be._metadata().get("NVC_STD", "2040")
    cmd = [be.nvc, "--std=" + std, "--work=" + be.work_spec(), "-L", be.libdir, "-a", path]
    if os.environ.get("VAMOS_VERBOSE"):
        con.out("vamos: + " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=be.workdir, env=be._env(), stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, universal_newlines=True, errors="replace")
    if r.returncode != 0:
        for ln in r.stdout.splitlines():
            con.err(ln)
        raise BackendError("nvc could not analyse %s, the entity over the top-level modules" % path)
    be.elaborate(name)


def plain_compile(job: Job, be: NvcBackend, con: Console, opts: dict) -> Tuple[List[str], float]:
    """A plain (digital) compile, steps in the module docstring.  Returns (the top-level
    modules, cpu time after translation); sets job.tops (what ./simv runs) and
    job.precision.  Raises BackendError after printing what went wrong."""
    from vamos.ams import layout, verilog_ports
    from vamos.notes import NoteError
    strict_on = bool(opts.get("strict"))
    try:
        pp = verilog_ports.preprocess(job, layout.plain_pp(job.daidir),
                                      override_timescale=job.override_timescale, ams=False)
    except NoteError as e:
        _flush(con, e.notes, strict_on, "preprocessing failed")
        raise BackendError("preprocessing failed")
    _flush(con, pp.notes, strict_on, "preprocessing failed")
    job.precision = pp.precision
    # the run's messages name the user's file:line (backends/nvc.py source_relocator)
    from vamos.backends.nvc import write_source_lines
    write_source_lines(job.daidir, pp)
    text, _ = library_rule(pp)
    tops = plain_tops(job, pp)
    with open(pp.path, "w") as fh:
        fh.write(text)

    job2 = dataclasses.replace(job, sources=[Source(os.path.abspath(pp.path), "sv")], lib_files=[],
                               defines={}, incdirs=[], timescale=None, override_timescale=None,
                               tops=[tops[0]])
    be2 = NvcBackend(job2, con.out)
    from vamos.notes import strict as strict_notes
    try:
        be2.analyse()
    except BackendError:
        for n in strict_notes(verilog_ports.translator_warnings(be2.translator_lines, pp),
                              strict_on):
            con.err("vamos: " + n.text())
        raise
    c1 = cpu_time()

    log = os.path.join(be.workdir, "iverilog.log")
    deferred = set(be.deferred_modules())
    bad = [t for t in tops if t in deferred]
    hint = ("; it is a top-level module because nothing instantiates it (VCS elaborates every "
            "such module): give -top to choose the tops") if len(tops) > 1 and not job.tops else ""
    for t in bad:
        con.err("vamos: error: sv2ghdl could not translate top module '%s': %s (see %s)%s"
                % (t, deferred_reason(log, t, pp), log, hint))
    if bad:
        for n in strict_notes(verilog_ports.translator_warnings(be2.translator_lines, pp),
                              strict_on):
            con.err("vamos: " + n.text())
        raise BackendError("compile failed at the translation")
    others = sorted(deferred - set(tops))
    if others and os.environ.get("VAMOS_VERBOSE"):
        con.err("vamos: note: modules sv2ghdl could not translate as their own top (deferred "
                "stubs in design.vhd): " + " ".join(others))
    from vamos.notes import warning
    notes = []
    design = os.path.join(be.workdir, "design.vhd")
    if os.path.isfile(design):
        files = [design]
    else:
        files = sorted(os.path.join(be.workdir, f) for f in os.listdir(be.workdir)
                       if f.endswith(".vhd") and not f.startswith("_"))     # not _mods.vhd
        notes.append(warning("", "the translation fell back to translating module by module "
                             "(iverilog-sv2ghdl's last resort, which may use the older sv2ghdl.pl "
                             "translator); see %s" % log))
    vhd = ""
    for f in files:
        with open(f, errors="replace") as fh:
            vhd += fh.read() + "\n"
    notes += verilog_ports.translator_warnings(be2.translator_lines, pp)
    # the design's timing that is not simulated (specify path delays and timing checks,
    # $sdf_annotate): VCS applies it unless +nospecify / +notimingcheck say not to
    given = {u.option for u in job.unmapped}
    notes += verilog_ports.timing_omissions(pp, "+nospecify" in given, "+notimingcheck" in given,
                                            text=text)
    if len(tops) == 1:
        notes += undriven_top_inputs(pp, vhd, tops[0])
    wrapper = _wrapper_name(pp, vhd) if len(tops) > 1 else None
    # $dumpfile/$dumpvars and the other VCD tasks are vamos's (job.dump, ./simv's waves):
    # their notes replace the "not translated" warnings
    job.dump, dump_notes = dump_request(vhd, pp, tops, wrapper,
                                        every=bool(getattr(job, "dumpvars_all", False)))
    notes += [n for n in verilog_ports.unsupported_tasks(vhd, pp, ams=False)
              if not _is_dump_task_note(n)]
    notes += dump_notes
    _flush(con, notes, strict_on, "compile failed at the translation")

    if wrapper is None:
        # the entity of the top (top_entity: a reserved-word module name is renamed;
        # `module pipe' elaborated WORK.PIPE, which does not exist)
        job.tops = [top_entity(vhd, tops[0])]
        be.elaborate(job.tops[0])
    else:
        _elaborate_wrapper(be, con, wrapper, wrapper_vhdl(wrapper, tops, vhd))
        job.tops = [wrapper] + list(tops)
    return tops, c1


# -- waves: $dumpfile, $dumpvars and the other VCD tasks ---------------------------------------
#
# tgt-vhdl leaves every VCD task out of the translation with its located comment ("Unsupported
# system task $dumpvars omitted here (<file>:<line>)"), which stays.  vamos reads the calls
# there (only what the translation kept: a generate branch not taken has none), takes their
# arguments and the $test$plusargs tests they run under from pp, and records them in job.dump;
# ./simv runs nvc with the waveform options (simv.wave_request, backends/nvc.Waves).  nvc
# writes the VCD from time 0 for the whole run, with four-state values, modules as module
# scopes, and none of the translator's own nets.  Names are the translation's: a VHDL
# reserved word gets "_sig" (bus -> bus_sig), and an output reg q has its q_reg beside q.

# What vamos does with each VCD task (the extended-VCD $dumpports family is not here: those
# keep the translator's "not translated" warning)
_DUMP_TASKS = {
    "$dumpfile": "",
    "$dumpvars": "",
    "$dumpoff": "is not honoured: the VCD records the whole run",
    "$dumpon": "is not honoured: the VCD records the whole run",
    "$dumpall": "has no effect: the VCD records every change",
    "$dumpflush": "has no effect: the VCD is written when the run ends",
    "$dumplimit": "is not honoured: the VCD has no size limit",
}
_DUMP_CALL = re.compile(r"Unsupported system task (\$dump\w+) omitted here \((.*):(\d+)\)")
_DUMP_NOTE = re.compile(r"^system task (\$\w+) is not translated")
_PP_FILES = ("_pp.v", "_norm.sv", "pp.v", "pp.orig.v")


def dump_covers(dump: Optional[dict], names: Sequence[str]) -> bool:
    """Whether the dump record (dump_request's job.dump) records any of the Verilog
    hierarchical names (tb.seen, tb.u1.q[3]): a call with no scopes dumps the whole design;
    a scope [levels, path] the signal at path, or those of instance path and, for levels n
    > 0, the n-1 levels of instances below it (every level for 0).  A call's plusarg tests
    are taken as passed: the run may make it."""
    if not dump:
        return False
    for c in dump.get("calls", ()):
        scopes = c.get("scopes") or []
        if not scopes:
            return True
        for levels, path in scopes:
            for name in names:
                n = re.sub(r"\[[^\]]*\]$", "", name)
                if n == path:
                    return True
                scope = n.rpartition(".")[0]
                if scope == path or scope.startswith(path + "."):
                    depth = scope.count(".") - path.count(".")
                    if levels == 0 or depth < levels:
                        return True
    return False


def _is_dump_task_note(n) -> bool:
    m = _DUMP_NOTE.match(n.message)
    return bool(m) and m.group(1) in _DUMP_TASKS


# The statements whose system task calls _Conditions knows the conditions of
_PROCESSES = ("initial", "always", "always_ff", "always_comb", "always_latch", "final")
_BLOCK_ENDS = ("end", "join", "join_any", "join_none")


class _Conditions:
    """The conditions under which each system task call in a module's processes runs: a
    structural walk of every initial/always/final statement (begin/fork blocks, if/else,
    case, loops, delays, event and wait controls).  at: token index of the call ->
    [condition, ...], outermost first; a condition is ("plusarg", name, present, var) for
    an `if` on $test$plusargs("name") (present False: its else branch, or `!`), or on
    $value$plusargs("name%s", var); else ("other", what).  A call in a task or function
    runs when it is called: ("other", "task t").  The walk never fails: text it cannot read
    as a statement is skipped to the next ';'."""

    def __init__(self, toks, lo: int, hi: int):
        self.t, self.hi = toks, hi
        self.at: dict = {}
        i = lo
        while i < hi:
            x = toks[i]
            if x.kind == "id" and x.text in _PROCESSES:
                try:
                    i = self.stmt(i + 1, [])
                except RecursionError:          # nested deeper than Python's stack: the
                    i += 1                      # calls in it keep no conditions
            elif x.kind == "id" and x.text in ("task", "function"):
                i = self.subprogram(i)
            else:
                i += 1

    def text(self, a: int, b: int) -> str:
        s = " ".join(t.text for t in self.t[a:b])
        return s if len(s) <= 60 else s[:57] + "..."

    def close(self, i: int) -> int:
        """The index of the bracket closing toks[i] (or hi - 1)."""
        from vamos.ams import verilog_ports
        return min(verilog_ports._match(self.t, i), self.hi - 1)

    def subprogram(self, i: int) -> int:
        kw = self.t[i].text
        end = "end" + kw
        j, name = i + 1, "?"
        while j < self.hi and self.t[j].text != end:
            if name == "?" and self.t[j].text in ("(", ";") and self.t[j - 1].kind in ("id", "esc"):
                name = self.t[j - 1].name
            if self.t[j].kind == "sys":
                self.at[j] = [("other", "%s %s" % (kw, name))]
            j += 1
        return j + 1

    def simple(self, i: int) -> int:
        """Past the ';' ending the simple statement at i (depth 0)."""
        depth, j = 0, i
        while j < self.hi:
            t = self.t[j]
            if t.kind == "op":
                if t.text in ("(", "[", "{"):
                    depth += 1
                elif t.text in (")", "]", "}"):
                    depth -= 1
                elif t.text == ";" and depth <= 0:
                    return j + 1
            elif depth <= 0 and t.kind == "id" and t.text in _BLOCK_ENDS + ("endcase",):
                return j                # a missing ';': never run past the block's end
            j += 1
        return j

    def cond(self, a: int, b: int):
        """The condition of `if (toks[a:b])`."""
        t = self.t
        while b - a >= 2 and t[a].text == "(" and self.close(a) == b - 1:
            a, b = a + 1, b - 1          # ( cond )
        present = True
        if a < b and t[a].text == "!":
            present, a = False, a + 1
            while b - a >= 2 and t[a].text == "(" and self.close(a) == b - 1:
                a, b = a + 1, b - 1
        if (b - a == 4 and t[a].text == "$test$plusargs" and t[a + 1].text == "("
                and t[a + 2].kind == "str" and t[a + 3].text == ")"):
            return ("plusarg", _unquote(t[a + 2].text), present, None)
        if (b - a == 6 and t[a].text == "$value$plusargs" and t[a + 1].text == "("
                and t[a + 2].kind == "str" and t[a + 3].text == ","
                and t[a + 4].kind in ("id", "esc") and t[a + 5].text == ")"):
            name = _unquote(t[a + 2].text).split("%", 1)[0]
            return ("plusarg", name, present, t[a + 4].name if present else None)
        return ("other", "if (%s)" % self.text(a, b) if present else
                "if (!%s)" % self.text(a, b))

    @staticmethod
    def negate(c):
        if c[0] == "plusarg":
            return ("plusarg", c[1], not c[2], None)
        return ("other", "the else branch of " + c[1])

    def stmt(self, i: int, conds: list) -> int:
        """Past the statement at i, recording the system task calls in it."""
        t, hi = self.t, self.hi
        if i >= hi:
            return hi
        x = t[i]
        w = x.text
        if x.kind == "attr" or (x.kind == "id" and w in ("unique", "unique0", "priority")):
            return self.stmt(i + 1, conds)
        if x.kind == "op" and w == ";":
            return i + 1
        if x.kind == "id" and w in ("begin", "fork"):
            j = i + 1
            if j + 1 < hi and t[j].text == ":":
                j += 2                                  # begin : label
            while j < hi and not (t[j].kind == "id" and t[j].text in _BLOCK_ENDS):
                k = self.stmt(j, conds)
                j = max(k, j + 1)
            j += 1
            if j + 1 < hi and t[j].text == ":":
                j += 2                                  # end : label
            return j
        if x.kind == "id" and w == "if" and i + 1 < hi and t[i + 1].text == "(":
            while True:                     # an else-if chain in this frame, however long
                c = self.close(i + 1)
                cond = self.cond(i + 2, c)
                j = self.stmt(c + 1, conds + [cond])
                if not (j < hi and t[j].text == "else"):
                    return j
                conds = conds + [self.negate(cond)]
                if j + 2 < hi and t[j + 1].text == "if" and t[j + 2].text == "(":
                    i = j + 1
                    continue
                return self.stmt(j + 1, conds)
        if x.kind == "id" and w in ("case", "casex", "casez", "randcase"):
            j = i + 1
            if w != "randcase" and j < hi and t[j].text == "(":
                j = self.close(j) + 1
            if j < hi and t[j].text == "inside":
                j += 1
            inner = conds + [("other", "a %s statement" % w)]
            from vamos.ams import verilog_ports
            while j < hi and t[j].text != "endcase":
                if t[j].text == "default":
                    j += 1
                    if j < hi and t[j].text == ":":
                        j += 1
                else:
                    k = verilog_ports._find(t, j, hi, ":")
                    if k < 0:
                        return self.simple(j)
                    j = k + 1
                j = max(self.stmt(j, inner), j + 1)
            return j + 1
        if x.kind == "id" and w in ("for", "while", "repeat", "foreach") and i + 1 < hi \
                and t[i + 1].text == "(":
            return self.stmt(self.close(i + 1) + 1, conds + [("other", "a %s loop" % w)])
        if x.kind == "id" and w == "forever":
            return self.stmt(i + 1, conds)              # its body runs
        if x.kind == "id" and w == "do":
            j = self.stmt(i + 1, conds)                 # its body runs once at least
            if j < hi and t[j].text == "while":
                j = self.simple(j)
            return j
        if x.kind == "op" and w in ("#", "@", "##"):
            j = i + 1                                   # a delay or event control
            if j < hi and t[j].text == "(":
                j = self.close(j) + 1
            elif j < hi:
                j += 1                                  # #10, #d, @clk, @*
                if j < hi and t[j].kind == "id" and t[j].start == t[j - 1].end \
                        and t[j].text in ("s", "ms", "us", "ns", "ps", "fs", "step"):
                    j += 1                              # #10ns
            return self.stmt(j, conds)
        if x.kind == "id" and w == "wait" and i + 1 < hi and t[i + 1].text == "(":
            return self.stmt(self.close(i + 1) + 1, conds)
        if x.kind in ("id", "esc") and i + 2 < hi and t[i + 1].text == ":" \
                and not (x.kind == "id" and w in verilog_keywords()):
            return self.stmt(i + 2, conds)              # label : statement
        if x.kind == "sys":
            self.at[i] = conds
        return self.simple(i)


def verilog_keywords():
    from vamos.ams import verilog_ports
    return verilog_ports.KEYWORDS


class _DumpCall:
    """One VCD task call: the task, its pp line, the user's file:line, the module it is in
    (None if unknown), its arguments as token lists, whether pp had the call (found), and
    the conditions it runs under (_Conditions)."""

    def __init__(self, task: str, line: int, origin: str, module: Optional[str], args: list,
                 found: bool, conds: list):
        self.task, self.line, self.origin, self.module = task, line, origin, module
        self.args, self.found, self.conds = args, found, conds


def _dump_calls(vhd: str, pp) -> List[_DumpCall]:
    """Every VCD task call the translation kept a comment for, once each (a module
    translated twice repeats its comments), in source order."""
    from vamos.ams import verilog_ports
    seen = set()
    out: List[_DumpCall] = []
    if pp is None or not _DUMP_CALL.search(vhd):
        return out
    toks = pp.toks()
    walked: dict = {}                       # module definition start -> _Conditions
    for m in _DUMP_CALL.finditer(vhd):
        task, f, ln = m.group(1), m.group(2), int(m.group(3))
        if task not in _DUMP_TASKS or os.path.basename(f) not in _PP_FILES or (task, ln) in seen:
            continue
        seen.add((task, ln))
        args, at = [], -1
        for i, t in enumerate(toks):
            if t.kind == "sys" and t.text == task and pp.line_of(t.start) == ln:
                at = i
                break
        if at >= 0 and at + 1 < len(toks) and toks[at + 1].text == "(":
            close = verilog_ports._match(toks, at + 1)
            args = [toks[a:b] for a, b in verilog_ports._split(toks, at + 2, close)]
            if len(args) == 1 and not args[0]:
                args = []                                   # $dumpvars()
        mod = pp.module_at(toks[at].start) if at >= 0 else None
        conds: list = []
        if mod is not None:
            if mod.start not in walked:
                walked[mod.start] = _Conditions(toks, mod.tok_hdr + 1, mod.tok_end)
            conds = walked[mod.start].at.get(at, [])
        out.append(_DumpCall(task, ln, pp.origin(ln), mod.name if mod is not None else None,
                             args, at >= 0, conds))
    out.sort(key=lambda c: c.line)
    return out


def _instance_paths(pp, tops: Sequence[str]) -> dict:
    """module -> the Verilog hierarchical names of its instances under the tops, from the
    structural scan.  An instance in an instance array has no plain name, so a module
    instantiated only so maps to None; one nothing instantiates is not in the map.  (A name
    can still be wrong for an instance in a generate block, which the translation flattens
    into its module: nvc then warns that the dump scope names nothing.)"""
    from vamos.ams import verilog_ports
    children: dict = {}
    arrayed = set()
    for i in verilog_ports.instantiations(pp):
        if i.array is None:
            children.setdefault(i.scope, []).append((i.name, i.module))
        else:
            arrayed.add((i.scope, i.module))
    out: dict = {}

    def walk(mod: str, path: str, depth: int) -> None:
        if out.get(mod) is None:
            out[mod] = []
        out[mod].append(path)
        if depth < 64:
            for name, child in children.get(mod, []):
                if child in pp.modules:
                    walk(child, path + "." + name, depth + 1)
            for scope, child in arrayed:
                if scope == mod and child not in out:
                    out[child] = None
    for t in tops:
        walk(t, t, 0)
    return out


def _when(conds) -> str:
    """The plusarg tests of a dump record as words: " when ./simv gets +x and not +y"."""
    if not conds:
        return ""
    return " when ./simv gets " + " and ".join(("+%s" if present else "no +%s") % name
                                              for name, present in conds)


def dump_request(vhd: str, pp, tops: Sequence[str], wrapper: Optional[str],
                 every: bool = False) -> Tuple[Optional[dict], list]:
    """The design's VCD tasks (their translator comments in vhd, their arguments and
    conditions in pp) as the dump record for ./simv (job.dump; None when no $dumpvars
    counts) and the notes that say what vamos does with each.  tops are the top-level
    modules; wrapper, the entity over several of them, whose instances top1, top2, ... are
    the tops in nvc's path names.  every: +vcs+dumpvars, a $dumpvars with no arguments."""
    from vamos.notes import note
    notes = []
    files: List[dict] = []
    calls: List[dict] = []
    paths = None

    def nvc_path(name: str) -> str:
        """A Verilog hierarchical name as nvc names it (tops under the wrapper's topN)."""
        head, _, rest = name.partition(".")
        if wrapper is not None and head in tops:
            return "%s.top%d%s" % (wrapper, list(tops).index(head) + 1, "." + rest if rest else "")
        return name

    def tests(c: _DumpCall) -> Tuple[list, dict]:
        """The call's plusarg tests ([name, present]) and $value$plusargs variables; a note
        for each condition ./simv cannot evaluate (the call then counts as made)."""
        out, vars_, others = [], {}, []
        for k in c.conds:
            if k[0] == "plusarg":
                out.append([k[1], k[2]])
                if k[3]:
                    vars_[k[3]] = k[1]
            else:
                others.append(k[1])
        if others:                             # the innermost one, which says the most
            notes.append(note(c.origin, "%s: the call is inside %s%s, which vamos cannot "
                              "evaluate before the run: ./simv acts as if it runs"
                              % (c.task, others[-1], " (and %d more condition%s)"
                                 % (len(others) - 1, "s" if len(others) > 2 else "")
                                 if len(others) > 1 else "")))
        return out, vars_

    if every:
        calls.append({"scopes": [], "origin": "+vcs+dumpvars", "if": []})
    for c in _dump_calls(vhd, pp):
        if not c.found:
            notes.append(note(c.origin, "%s: vamos could not read the call's arguments, so it is "
                              "taken as one with none" % c.task))
        if c.task not in ("$dumpfile", "$dumpvars"):
            notes.append(note(c.origin, "%s %s" % (c.task, _DUMP_TASKS[c.task])))
            continue
        if c.module is not None and c.module not in tops:
            if paths is None:
                paths = _instance_paths(pp, tops)
            if c.module not in paths:
                continue                       # in a module nothing instantiates: never runs
        cond, vars_ = tests(c)
        if c.task == "$dumpfile":
            a = c.args
            if not a:
                files.append({"name": DEFAULT_DUMPFILE, "origin": c.origin, "if": cond})
            elif len(a) == 1 and len(a[0]) == 1 and a[0][0].kind == "str":
                files.append({"name": _unquote(a[0][0].text), "origin": c.origin, "if": cond})
            elif len(a) == 1 and len(a[0]) == 1 and a[0][0].kind in ("id", "esc") \
                    and a[0][0].name in vars_:
                # if ($value$plusargs("vcd=%s", f)) $dumpfile(f): the plusarg's value
                files.append({"plusarg": vars_[a[0][0].name], "origin": c.origin, "if": cond})
            else:
                notes.append(note(c.origin, "$dumpfile: the file name is not a string literal, "
                                  "which vamos cannot evaluate; the call is ignored"))
            continue
        levels, scopes, whole = 0, [], False
        if c.args:
            lv = c.args[0]
            m = re.match(r"^(?:\d*\s*'[dD]\s*)?(\d+)$", lv[0].text) \
                if len(lv) == 1 and lv[0].kind == "num" else None
            if m:
                levels = int(m.group(1))
            else:
                notes.append(note(c.origin, "$dumpvars: the level count is not a number vamos "
                                  "can read; every level is dumped"))
        names = []
        for a in c.args[1:]:
            if a and a[0].kind in ("id", "esc") and len(a) % 2 == 1 and all(
                    t.kind in ("id", "esc") if k % 2 == 0 else t.text == "."
                    for k, t in enumerate(a)):
                names.append(".".join(t.name for t in a[::2]))
            else:
                notes.append(note(c.origin, "$dumpvars: an argument that is not a module "
                                  "instance or a variable name is ignored"))
        if not names:
            if levels == 0:
                whole = True                   # $dumpvars: every variable of the design
            else:
                scopes += [(levels, t) for t in tops]
        for n in names:
            if n.split(".")[0] in tops or c.module is None:
                scopes.append((levels, n))
                continue
            if paths is None:
                paths = _instance_paths(pp, tops)
            here = paths.get(c.module)
            if here is None:
                notes.append(note(c.origin, "$dumpvars: %s is relative to %s, whose instances "
                                  "are in an instance array, which vamos cannot name; every "
                                  "level of the design is dumped" % (n, c.module)))
                whole = True
                continue
            for p in here:                     # a name in a module: in each of its instances
                scopes.append((levels, p + "." + n))
        calls.append({"scopes": [] if whole else [[n, nvc_path(p)] for n, p in scopes],
                      "origin": c.origin, "if": cond, "_shown": [] if whole else scopes})
    if not calls:
        if files:
            notes.append(note(files[0]["origin"], "$dumpfile without $dumpvars: no waves are "
                              "written (dumping starts with $dumpvars)"))
        return None, notes
    def fname(f: dict) -> str:
        return f.get("name") or "the value of +%s" % f["plusarg"]

    def counts(f: dict, guard: list) -> bool:
        """f's tests are among guard's: f counts whenever a call under guard does."""
        return {tuple(x) for x in f["if"]} <= {tuple(x) for x in guard}

    same = all(c["if"] == calls[0]["if"] for c in calls)
    shown: List[str] = []
    mixed = len(calls) > 1 and any(c["origin"] == "+vcs+dumpvars" for c in calls)
    for c in calls:
        sc = c.pop("_shown", [])
        what = "the whole design" if not c["scopes"] else ", ".join(
            "%s (%s)" % (p, "every level" if n == 0 else "%d level%s" % (n, "s" if n > 1 else ""))
            for n, p in sc)
        if mixed and c["origin"] == "+vcs+dumpvars":
            # the note is placed at the design's $dumpvars: say which part is the option's
            what += " (+vcs+dumpvars)"
        what += "" if same else _when(c["if"])
        if what not in shown:
            shown.append(what)
    guard = calls[0]["if"] if same else []
    named = next((f for f in files if counts(f, guard)), None)
    if named is not None:
        to = fname(named)
    elif files:
        to = "the $dumpfile name ./simv's plusargs select, else %s" % DEFAULT_DUMPFILE
    else:
        to = "%s (./simv +vcs+dumpfile+<file> names another)" % DEFAULT_DUMPFILE
    later = [f for f in files if counts(f, guard) and f is not named and fname(f) != to]
    if named is not None and later:
        notes.append(note(later[0]["origin"], "$dumpfile: the waves go to %s, the first "
                          "$dumpfile's file" % to))
    first = next((c["origin"] for c in calls if c["origin"] != "+vcs+dumpvars"), "")
    notes.append(note(first, "%s: ./simv writes a VCD of %s to %s%s, from time 0 for the whole "
                      "run" % ("$dumpvars" if first else "+vcs+dumpvars", "; ".join(shown), to,
                               _when(guard))))
    return {"calls": calls, "files": files}, notes


def _unquote(s: str) -> str:
    """A Verilog string literal's text (simple escapes)."""
    body = s[1:-1] if len(s) >= 2 and s[0] == s[-1] == '"' else s
    return re.sub(r'\\(["\\])', r"\1", body)


# -- the personality ---------------------------------------------------------------------------

def main_ams(args: List[str], opts: dict) -> int:
    """The vcs-ams personality: vcs with AMS forced on."""
    return main(args, opts, personality="vcs-ams")


def _note_vamos_options(job: Job, opts: dict, ams: bool) -> None:
    """--vamos-* options with no effect on this compile go into job.unmapped (a warning)."""
    for opt, why in vamos_option_effects(opts, "vcs", ams=ams, run=job.run):
        job.note(opt, INAPPLICABLE, why)


def _write_atomic(path: str, text: str) -> None:
    tmp = path + ".vamos-tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)


def display_exe(job: Job) -> str:
    """The executable for the "is up to date" line: relative to the current directory."""
    try:
        return os.path.relpath(job.exe, job.cwd or os.getcwd())
    except ValueError:                  # another drive (Windows)
        return job.exe


def invalidate(job: Job) -> None:
    """Before the compile touches the daidir: ./simv becomes a stub that refuses to run, and
    the previous compile's job record and AMS interface-element report go.  A compile that
    fails or is interrupted therefore never leaves a half-rebuilt daidir runnable."""
    from vamos.ams import layout
    write_stub(job, failed=True)
    for p in (os.path.join(job.daidir, JOB_FILE), layout.ie_report(job.exe)):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


def main(args: List[str], opts: dict, personality: str = "vcs") -> int:
    con = Console()
    if any(a in ("-h", "-help", "--help") for a in args):
        con.out(help_text(personality))
        return 0
    cwd = os.getcwd()
    try:
        job = build_job(args, cwd, personality)
    except (argscan.ArgError, ScanError) as e:
        con.err("vamos: error: %s" % e)
        return 1
    ams = job.ams_control is not None
    _note_vamos_options(job, opts, ams)
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
    if os.path.isdir(job.exe):
        con.err("vamos: error: -o %s: that is a directory" % job.exe)
        return 1
    if ams and len(job.tops) > 1:
        con.err("vamos: error: -top: an AMS design has one top module; -top was given %d times (%s)"
                % (len(job.tops), ", ".join(job.tops)))
        return 1

    # The banner comes first, as VCS prints its own before any message: every tool lookup
    # below (VAMOS_NVC, VAMOS_IVERILOG, the AMS engines) then reports after it alike.
    t = ban.text("compile_start")
    if t is not None:
        con.out(t)
    try:
        be = NvcBackend(job, con.out)
        used = [("vamos", tools.version_of("vamos", tools.launcher()), tools.launcher())]
        used += [(n, tools.version_of(n, p), p) for n, p in be.compile_tools()]
        if ams:
            from vamos.ams import flow     # imported only for AMS compiles
            used += [(n, tools.version_of(n, p), p) for n, p in flow.compile_tools(job, opts)]
    except (BackendError, tools.ToolError) as e:
        con.err("vamos: error: %s" % e)
        return 1
    con.out(banner.provenance(used, "vcs"))
    report_unmapped(job, con.err)
    if opts.get("strict"):
        bad = strict_failures(job)
        if bad:
            con.err("vamos: error: " + strict_message(bad))
            return 1

    had_exe = os.path.lexists(job.exe)
    try:
        os.makedirs(job.daidir, exist_ok=True)
        invalidate(job)
    except OSError as e:
        con.err("vamos: error: cannot prepare %s: %s" % (job.daidir, e))
        return 1
    c0 = cpu_time()
    try:
        if ams:
            # docs/VAMOS_AMS_DESIGN.md §1: control file, netlist, shells,
            # translation, cut, deck, elaboration; fills job.ams.
            # (the design's $dumpfile/$dumpvars and +vcs+dumpvars: job.dump, set by
            # flow.compile with the plain compile's dump_request)
            top, c1 = flow.compile(job, be, con, opts)
            if not job.tops:
                job.tops = [top]
            tops = [top]
            _check_ams_options(job, opts, con)
        else:
            tops, c1 = plain_compile(job, be, con, opts)
    except (BackendError, tools.ToolError) as e:
        con.err("vamos: error: %s" % e)
        if not had_exe:                 # a first compile that fails leaves no ./simv (as VCS)
            try:
                os.remove(job.exe)
            except OSError:
                pass
        t = ban.text("compile_failed")
        if t is not None:
            con.out(t)
        return 1
    c2 = cpu_time()

    _write_atomic(os.path.join(job.daidir, JOB_FILE), job.to_json())
    simv_mod.write_tools(job.daidir, used)
    write_stub(job)

    t = ban.text("compile_tops", tops_lines="\n".join("       " + x for x in tops))
    if t is not None:
        con.out(t)
    t = ban.text("compile_done", exe=display_exe(job), cpu_compile=c1 - c0, cpu_elab=c2 - c1)
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


def _check_ams_options(job: Job, opts: dict, con: Console) -> None:
    """After an AMS compile: --vamos-analog-stop sets only a synthesized analysis; beside a
    .tran it has no effect, which is a warning (an error under --vamos-strict)."""
    if opts.get("analog_stop") is None or not job.ams or job.ams.get("stop_synthesized"):
        return
    opt = vamos_option_text("analog_stop", opts["analog_stop"])
    why = "the netlist's .tran sets the analog stop time (%g s)" % job.ams.get("stop")
    job.note(opt, INAPPLICABLE, why)
    con.err("vamos: warning: %s has no effect: %s" % (opt, why))
    if opts.get("strict"):
        raise BackendError(strict_message([opt]))


def write_stub(job: Job, failed: bool = False) -> None:
    """./simv: a small script that re-enters vamos's simv personality.

    It runs the daidir next to itself, as VCS's simv does: a moved or copied simv +
    simv.daidir pair runs its own copy, and a symlink to simv alone falls back to the
    daidir beside the link's target.  failed: the stub a compile writes when it starts,
    which refuses to run until that compile succeeds and writes the real one."""
    base = os.path.basename(job.daidir)
    qbase = shlex.quote(base)
    shown = base.replace("\n", "?")
    if failed:
        msg = ("the last compile into %s failed or was interrupted, so there is nothing to run; "
               "compile again" % shown)
        body = ("#!/bin/sh\n"
                "# Generated by vamos (vcs personality) when a compile into %s started;\n"
                "# that compile replaces it with the real script when it succeeds.\n"
                "echo \"vamos: error: $0: \"%s >&2\n"
                "exit 1\n" % (shown, shlex.quote(msg)))
    else:
        body = ("#!/bin/sh\n"
                "# Generated by vamos (vcs personality). Runs the design compiled into %s,\n"
                "# found next to this script as VCS's simv finds its daidir (move or copy the\n"
                "# two together); accepts simv runtime options.\n"
                "case $0 in */*) d=${0%%/*}; d=${d:-/} ;; *) d=. ;; esac\n"
                "[ -d \"$d\"/%s ] || { r=$(readlink -f \"$0\" 2>/dev/null) && d=${r%%/*}; }\n"
                "d=$(cd \"$d\" 2>/dev/null && pwd) || d=.\n"
                "exec %s -simv --vamos-daidir=\"$d\"/%s \"$@\"\n"
                % (shown, qbase, shlex.quote(tools.launcher()), qbase))
    tmp = job.exe + ".vamos-tmp"
    with open(tmp, "w") as fh:
        fh.write(body)
    os.chmod(tmp, 0o755)
    os.replace(tmp, job.exe)
