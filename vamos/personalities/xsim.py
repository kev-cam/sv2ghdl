"""xvlog / xvhdl / xelab / xsim personalities: the Vivado simulator's command lines on nvc.

Vivado's flow has four programs and a directory:

  xvlog [--sv] [-d M[=V]] [-i DIR] [--work LIB] files...   analyse Verilog/SV into a library
  xvhdl [--2008] [--work LIB] files...                     analyse VHDL into a library
  xelab [-s SNAP] [-L LIB]... [--debug ...] [-R] [lib.]top...   elaborate a snapshot
  xsim SNAP (-R | -t script.tcl) [--testplusarg N=V]...   run the snapshot

The analysis steps record what they were given: xsim.dir/<lib>/vamos.lib.json lists the
library's sources with their defines, include directories and language, in analysis order,
the way xvlog keeps compiled units under xsim.dir/<lib>/.  xelab collects the sources of the
libraries it searches (work, plus every -L), builds a vamos Job and compiles it the way the
vcs personality does (sv2ghdl translation, nvc analysis and elaboration) into the snapshot
directory xsim.dir/<snapshot>/, where vamos.job.json is the record ./simv would read.  xsim
runs that record through the simv runtime: --testplusarg becomes a plusarg, --log the run
log, a -t Tcl batch file's `run` commands the stop time.  Both --long and -long spellings
of every option are accepted, as the Vivado tools do.

Not covered (noted or warned, as the option table says): DPI (--sv_lib/--sv_root), coverage,
--generic_top parameter overrides, SDF and timing options, the interactive Tcl shell (xsim
without -R/-t), waveform databases (--wdb; use $dumpvars), and the Xilinx simulation
libraries (unisims_ver, secureip, xpm, ...), which exist for Vivado's own simulator only.
Mixed VHDL + Verilog elaboration is not supported yet: a snapshot is all Verilog/SV (through
sv2ghdl) or all VHDL (nvc directly).

--vamos-engine=verilator (or VAMOS_ENGINE=verilator) at xelab builds the snapshot with Verilator
instead (backends/verilator.py); xsim then runs that binary.  The engine is recorded in the
snapshot, so xsim needs no option.
"""

import dataclasses
import json
import os
import re
from typing import Dict, List, Optional, Tuple

from vamos import VERSION, argscan, banner, tools
from vamos.backends.nvc import BackendError, NvcBackend, cpu_time
from vamos.backends.verilator import VerilatorBackend, engine_of
from vamos.console import Console
from vamos.job import IGNORED, INAPPLICABLE, NOTED, UNKNOWN, UNSUPPORTED, Job, Source
from vamos.optable import (Opt, ScanError, Table, report_unmapped, scan, strict_failures,
                           strict_message, vamos_option_effects)
from vamos.personalities import simv as simv_mod
from vamos.personalities import vcs as vcs_mod

XSIM_DIR = "xsim.dir"
LIB_FILE = "vamos.lib.json"
LIB_SCHEMA = 1
XILINX_LIBS = ("unisim", "unisims_ver", "unimacro", "unimacro_ver", "unifast", "unifast_ver",
               "secureip", "simprim", "simprims_ver", "xpm", "xilinx_vip", "axi_vip_v1_1_0")
VERILOG_EXTS = {".v": "verilog", ".vh": "verilog", ".vp": "verilog",
                ".sv": "sv", ".svh": "sv", ".svp": "sv"}
VHDL_EXTS = {".vhd": "vhdl", ".vhdl": "vhdl", ".vho": "vhdl"}


# -- shared helpers -------------------------------------------------------------------------

def _both(name: str) -> Tuple[str, ...]:
    """Vivado accepts --long and -long; single-letter options stay as given."""
    if name.startswith("--"):
        return (name, name[1:])
    return (name,)


def _opts(table: List[Tuple]) -> List[Opt]:
    """(names, arity, act[, note]) rows into Opts, expanded for both spellings."""
    out = []
    for row in table:
        names, arity, act = row[0], row[1], row[2]
        note = row[3] if len(row) > 3 else ""
        for n in (names if isinstance(names, tuple) else (names,)):
            for spelling in _both(n):
                out.append(Opt(spelling, arity, act, note))
    return out


def _set(attr):
    def act(job, val):
        setattr(job, attr, val)
    return act


def _flag(attr):
    def act(job, _):
        setattr(job, attr, True)
    return act


def _define(job, val):
    name, eq, value = val.partition("=")
    job.defines[name] = value if eq else None


def _incdir(job, val):
    job.incdirs.append(os.path.normpath(os.path.join(job.cwd, val)))


def _work(job, val):
    """--work LIB[=DIR]: the library analysed into (the directory, if given, is where
    Vivado would keep it; vamos keeps every library under xsim.dir)."""
    name, eq, where = val.partition("=")
    job.library = name
    if eq and where:
        job.note("--work " + val, NOTED, "libraries live under %s/; the directory is not used"
                 % XSIM_DIR)


def _lib(job, val):
    name = val.partition("=")[0]
    job.libs.append(name)


def _xsim_dir(job, val):
    job.xsim_dir = os.path.normpath(os.path.join(job.cwd, val))


def _help(job, _):
    job.help_requested = True


def _version(job, _):
    job.version_requested = True


def _unknown(job, tok, args, i) -> int:
    job.note(tok, UNKNOWN)
    return 1


def _lang_of(path: str, sv: bool) -> Optional[str]:
    ext = os.path.splitext(path)[1].lower()
    if ext in VHDL_EXTS:
        return "vhdl"
    if ext in VERILOG_EXTS:
        return "sv" if sv or VERILOG_EXTS[ext] == "sv" else "verilog"
    return None


def _expand(args: List[str], cwd: str) -> List[str]:
    """Vivado's --file/-file/-f option files, through argscan (which knows -f/-F/-file)."""
    mapped = ["-f" if a in ("--file", "--f") else a for a in args]
    return argscan.expand_option_files(mapped, cwd)


def _prj_lines(path: str, cwd: str) -> List[Tuple[str, str, List[str]]]:
    """A Vivado simulator project file: `<lang> <lib> <files...>` per line, lang in
    verilog | sv | vhdl | vhdl2008 | vhdl2019; # comments."""
    out = []
    with open(path) as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 3:
                raise ScanError("%s: a project line is '<lang> <lib> <files...>': %s" % (path, raw.strip()))
            lang, lib, files = parts[0].lower(), parts[1], parts[2:]
            out.append((lang, lib, [os.path.normpath(os.path.join(cwd, f)) for f in files]))
    return out


def _lib_dir(xsim_dir: str, lib: str) -> str:
    return os.path.join(xsim_dir, lib)


def _read_lib(xsim_dir: str, lib: str) -> List[dict]:
    try:
        with open(os.path.join(_lib_dir(xsim_dir, lib), LIB_FILE)) as fh:
            d = json.load(fh)
    except FileNotFoundError:
        return []
    if d.get("schema", 1) > LIB_SCHEMA:
        raise BackendError("%s/%s was written by a newer vamos; analyse again" % (lib, LIB_FILE))
    return d.get("sources", [])


def _write_lib(xsim_dir: str, lib: str, sources: List[dict]) -> None:
    d = _lib_dir(xsim_dir, lib)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, LIB_FILE)
    tmp = path + ".vamos-tmp"
    with open(tmp, "w") as fh:
        json.dump({"schema": LIB_SCHEMA, "library": lib, "sources": sources}, fh, indent=2)
    os.replace(tmp, path)


def _record(xsim_dir: str, lib: str, new: List[dict]) -> int:
    """Add analysed sources to a library record; a file analysed again replaces its
    earlier entry (keeping the earlier position, as a recompiled unit keeps its place)."""
    have = _read_lib(xsim_dir, lib)
    index = {s["path"]: i for i, s in enumerate(have)}
    for s in new:
        if s["path"] in index:
            have[index[s["path"]]] = s
        else:
            index[s["path"]] = len(have)
            have.append(s)
    _write_lib(xsim_dir, lib, have)
    return len(have)


def _note_vamos_options(job, opts: dict, as_personality: str, run: bool = False) -> None:
    for opt, why in vamos_option_effects(opts, as_personality, ams=False, run=run):
        job.note(opt, INAPPLICABLE, why)


def _finish(con: Console, job, opts: dict, prof_name: str) -> int:
    """Report the options vamos did not act on; --vamos-strict makes them an error."""
    report_unmapped(job, con.err)
    if opts.get("strict"):
        bad = strict_failures(job)
        if bad:
            con.err("vamos: error: " + strict_message(bad))
            return 1
    return 0


class _AnJob(Job):
    """A Job plus what an analysis step needs: the library, --sv, the project file."""
    def __init__(self, personality: str, args: List[str], cwd: str):
        super().__init__(personality=personality, argv=list(args), cwd=cwd)
        self.library = "work"
        self.sv = False
        self.vhdl_std = "2008"
        self.prj: List[str] = []
        self.nolog = False
        self.xsim_dir = os.path.join(cwd, XSIM_DIR)
        self.libs: List[str] = []
        self.help_requested = False
        self.version_requested = False
        self.files: List[str] = []       # positional sources, in order


# -- xvlog / xvhdl ----------------------------------------------------------------------------

_ANALYSE_COMMON = [
    (("-h", "--help"), "flag", _help),
    ("--version", "flag", _version),
    ("--log", "next", _set("log")),
    ("--nolog", "flag", _flag("nolog")),
    (("-v", "--verbose"), "next", IGNORED),
    ("--incr", "flag", IGNORED),
    ("--relax", "flag", IGNORED),
    ("--initfile", "next", NOTED, "simulator init files are not read"),
    (("-L", "--lib"), "next", _lib),            # recorded; libraries are searched at xelab
    ("--work", "next", _work),
    ("--prj", "next", lambda job, val: job.prj.append(os.path.join(job.cwd, val))),
]
XVLOG_OPTIONS = _opts(_ANALYSE_COMMON + [
    (("-i", "--include"), "next", _incdir),
    (("-d", "--define"), "next", _define),
    ("--sv", "flag", _flag("sv")),
    ("--sourcelibdir", "next", lambda job, val: job.lib_dirs.append(os.path.join(job.cwd, val))),
    ("--sourcelibext", "next", lambda job, val: job.lib_exts.append(val)),
    ("--sourcelibfile", "next", lambda job, val: job.lib_files.append(os.path.join(job.cwd, val))),
    ("--noname_unnamed_generate", "flag", IGNORED),
    ("--uvm_version", "next", NOTED, "UVM is not available on nvc (docs/TODO-uvm-translator.md)"),
])
XVHDL_OPTIONS = _opts(_ANALYSE_COMMON + [
    ("--2008", "flag", lambda job, _: setattr(job, "vhdl_std", "2008")),
    ("--2019", "flag", lambda job, _: setattr(job, "vhdl_std", "2019")),
    ("--93", "flag", lambda job, _: setattr(job, "vhdl_std", "1993")),
])
XVLOG_TABLE = Table(XVLOG_OPTIONS)
XVHDL_TABLE = Table(XVHDL_OPTIONS)


def _positional(job, tok):
    job.files.append(tok)


def _analyse_help(tool: str) -> str:
    opts = XVLOG_OPTIONS if tool == "xvlog" else XVHDL_OPTIONS
    names = sorted({o.name for o in opts if o.name.startswith("--") or len(o.name) == 2})
    return ("Usage: %s [options] files...\n"
            "Analyses the files into a library under %s/ for a later xelab (vamos, on sv2ghdl + nvc).\n"
            "Options accepted (--long or -long): %s\n"
            "The user guide: %s" % (tool, XSIM_DIR, " ".join(names), tools.guide_path()))


def _analyse(tool: str, args: List[str], opts: dict) -> int:
    con = Console()
    cwd = os.getcwd()
    job = _AnJob(tool, args, cwd)
    table = XVLOG_TABLE if tool == "xvlog" else XVHDL_TABLE
    try:
        scan(table, _expand(args, cwd), job, _positional, _unknown)
    except (argscan.ArgError, ScanError) as e:
        con.err("vamos: error: %s" % e)
        return 1
    if job.help_requested:
        con.out(_analyse_help(tool))
        return 0
    if job.version_requested:
        con.out("vamos %s (%s personality)" % (VERSION, tool))
        return 0
    _note_vamos_options(job, opts, "vcs")
    if not job.nolog:
        con.open_log(job.log or os.path.join(cwd, tool + ".log"))
    try:
        prof = banner.load_profile("xsim", opts.get("banner"))
    except ValueError as e:
        con.err("vamos: error: %s" % e)
        return 1
    ban = banner.Banner(prof, tool)
    t = ban.text("analyse_start")
    if t is not None:
        con.out(t)

    # the sources: positional files (this step's language), then project-file lines
    per_lib: Dict[str, List[dict]] = {}
    bad = []

    def add(lib: str, path: str, lang: str, defines: dict, incdirs: List[str], std: str):
        p = os.path.normpath(os.path.join(cwd, path))
        if not os.path.isfile(p):
            bad.append(p)
            return
        per_lib.setdefault(lib, []).append({"path": p, "lang": lang, "defines": dict(defines),
                                            "incdirs": list(incdirs), "std": std,
                                            "lib_files": list(job.lib_files),
                                            "lib_dirs": list(job.lib_dirs),
                                            "lib_exts": list(job.lib_exts)})

    for f in job.files:
        lang = _lang_of(f, job.sv)
        if tool == "xvlog":
            if lang == "vhdl":
                con.err("vamos: error: %s: a VHDL file is analysed with xvhdl" % f)
                return 1
            lang = lang or ("sv" if job.sv else "verilog")
            add(job.library, f, lang, job.defines, job.incdirs, lang)
        else:
            if lang in ("verilog", "sv"):
                con.err("vamos: error: %s: a Verilog file is analysed with xvlog" % f)
                return 1
            add(job.library, f, "vhdl", {}, [], "vhdl" + job.vhdl_std)
    try:
        for prj in job.prj:
            for lang, lib, files in _prj_lines(prj, cwd):
                if lang in ("verilog", "sv"):
                    for f in files:
                        add(lib, f, lang, job.defines, job.incdirs, lang)
                elif lang.startswith("vhdl"):
                    for f in files:
                        add(lib, f, "vhdl", {}, [], lang if lang != "vhdl" else "vhdl2008")
                else:
                    con.err("vamos: error: %s: unknown language '%s'" % (prj, lang))
                    return 1
    except (OSError, ScanError) as e:
        con.err("vamos: error: %s" % e)
        return 1
    for p in bad:
        con.err("vamos: error: source file '%s' cannot be opened" % p)
    if bad:
        return 1
    if not per_lib:
        con.err("vamos: error: no source files given")
        return 1
    rc = _finish(con, job, opts, "xsim")
    if rc:
        return rc
    try:
        for lib, srcs in per_lib.items():
            n = _record(job.xsim_dir, lib, srcs)
            for s in srcs:
                con.out("INFO: [vamos] analysing %s file \"%s\" into library %s"
                        % ("SystemVerilog" if s["lang"] == "sv" else "Verilog" if s["lang"] == "verilog"
                           else "VHDL", os.path.relpath(s["path"], cwd), lib))
            con.out("INFO: [vamos] library %s: %d source file(s) recorded in %s/%s/%s; the design is "
                    "compiled when xelab runs" % (lib, n, XSIM_DIR, lib, LIB_FILE))
    except (OSError, BackendError) as e:
        con.err("vamos: error: %s" % e)
        return 1
    con.close()
    return 0


def main_xvlog(args: List[str], opts: dict) -> int:
    return _analyse("xvlog", args, opts)


def main_xvhdl(args: List[str], opts: dict) -> int:
    return _analyse("xvhdl", args, opts)


# -- xelab --------------------------------------------------------------------------------------

class _ElabJob(_AnJob):
    def __init__(self, args, cwd):
        super().__init__("xelab", args, cwd)
        self.snapshot: Optional[str] = None
        self.timescale = "1ns/1ps"        # xelab's default for Verilog modules without a `timescale
        self.direct: List[Tuple[str, str]] = []     # (lang, path) from --vlog/--svlog/--vhdl*
        self.run = False
        self.debug: Optional[str] = None


def _direct(lang):
    def act(job, val):
        job.direct.append((lang, os.path.normpath(os.path.join(job.cwd, val))))
    return act


def _debug_opt(job, val):
    job.debug = val
    if val not in ("off", "typical", "all", "wave"):
        job.note("--debug " + val, UNKNOWN, "off | typical | all | wave")
    elif val != "off":
        job.note("--debug " + val, NOTED, "nvc runs with full visibility anyway; waves need "
                 "$dumpvars in the design (--wdb is not produced)")


def _run_opt(job, _):
    job.run = True


def _run_interactive(job, _):
    job.run = True
    job.note("-r", NOTED, "there is no interactive Tcl shell: the run goes to completion, as -R")


XELAB_OPTIONS = _opts([
    (("-h", "--help"), "flag", _help),
    ("--version", "flag", _version),
    ("--log", "next", _set("log")),
    ("--nolog", "flag", _flag("nolog")),
    (("-v", "--verbose"), "next", IGNORED),
    (("-s", "--snapshot"), "next", _set("snapshot")),
    (("-L", "--lib"), "next", _lib),
    (("-R", "--runall"), "flag", _run_opt),
    (("-r", "--run"), "flag", _run_interactive),
    ("--debug", "next", _debug_opt),
    ("--timescale", "next", _set("timescale")),
    (("-d", "--define"), "next", _define),
    (("-i", "--include"), "next", _incdir),
    ("--prj", "next", lambda job, val: job.prj.append(os.path.join(job.cwd, val))),
    ("--vlog", "next", _direct("verilog")),
    ("--svlog", "next", _direct("sv")),
    ("--vhdl", "next", _direct("vhdl")),
    ("--vhdl2008", "next", _direct("vhdl")),
    ("--vhdl2019", "next", _direct("vhdl")),
    ("--incr", "flag", IGNORED),
    ("--relax", "flag", IGNORED),
    (("-a", "--standalone"), "flag", IGNORED),
    ("--mt", "next", IGNORED),
    ("--stats", "flag", IGNORED),
    (("--O0", "--O1", "--O2", "--O3"), "flag", IGNORED),
    (("-O0", "-O1", "-O2", "-O3"), "flag", IGNORED),
    ("--maxarraysize", "next", IGNORED),
    ("--maxdesigndepth", "next", IGNORED),
    ("--driver_display_limit", "next", IGNORED),
    ("--noname_unnamed_generate", "flag", IGNORED),
    ("--dup_entity_as_module", "flag", IGNORED),
    ("--suppress_localparam_override_error", "flag", IGNORED),
    ("--transform_timing_checkers", "flag", IGNORED),
    ("--rangecheck", "flag", NOTED, "nvc checks ranges always"),
    ("--initfile", "next", NOTED, "simulator init files are not read"),
    ("--override_timeunit", "flag", NOTED, "not applied"),
    ("--override_timeprecision", "flag", NOTED, "not applied"),
    ("--timeprecision_vhdl", "next", NOTED, "nvc keeps the design's precision"),
    ("--uvm_version", "next", NOTED, "UVM is not available on nvc (docs/TODO-uvm-translator.md)"),
    ("--generic_top", "next", UNSUPPORTED, "top-level parameter/generic overrides"),
    ("--ignore_assertions", "flag", NOTED, "assertions are not controlled yet"),
    ("--report_assertion_pass", "flag", NOTED, "assertions are not controlled yet"),
    ("--ignore_coverage", "flag", IGNORED),
    (("--cov_db_dir", "--cov_db_name", "--cc_type", "--ccExclusionFile"), "next", UNSUPPORTED, "coverage"),
    (("--cc", "--cc_celldefines", "--cc_libs"), "flag", UNSUPPORTED, "coverage"),
    (("--sv_lib", "--sv_root", "--sv_liblist", "--sc_lib", "--sc_root", "--dpiheader",
      "--dpi_stacksize"), "next", UNSUPPORTED, "DPI/SystemC (planned via nvc --load)"),
    ("--dpi_absolute", "flag", UNSUPPORTED, "DPI (planned via nvc --load)"),
    (("--nospecify", "--notimingchecks"), "flag", NOTED, "specify blocks and timing checks are not modelled"),
    (("--maxdelay", "--mindelay", "--typdelay", "--nosdfinterconnectdelays", "--sdfnoerror",
      "--sdfnowarn", "--transport_int_delays"), "flag", NOTED, "SDF/delay selection is not modelled"),
    (("--sdfmax", "--sdfmin", "--sdftyp", "--sdfroot", "--pulse_int_e", "--pulse_int_r", "--pulse_e",
      "--pulse_r", "--pulse_e_style"), "next", NOTED, "SDF/pulse options are not modelled"),
    (("--sourcelibdir", "-sourcelibdir"), "next", lambda job, val: job.lib_dirs.append(os.path.join(job.cwd, val))),
    (("--sourcelibext", "-sourcelibext"), "next", lambda job, val: job.lib_exts.append(val)),
    (("--sourcelibfile", "-sourcelibfile"), "next", lambda job, val: job.lib_files.append(os.path.join(job.cwd, val))),
])
XELAB_TABLE = Table(XELAB_OPTIONS)

_TOP_RE = re.compile(r"^(?:(\w+)\.)?(\w+)(?:\((\w+)\))?$")


def _tops(job: _ElabJob, con: Console) -> Optional[List[str]]:
    tops = []
    for tok in job.files:
        m = _TOP_RE.match(tok)
        if not m:
            con.err("vamos: error: '%s' is not a top-level unit ([lib.]name[(arch)])" % tok)
            return None
        lib, name, arch = m.groups()
        if name == "glbl" and lib in (None, "work"):
            job.note("glbl", NOTED, "Vivado's glbl module (GSR/GTS) has no role on nvc; dropped")
            continue
        if arch:
            job.note(tok, NOTED, "the architecture is chosen by nvc (the last analysed)")
        tops.append(name)
    return tops


def xelab_help() -> str:
    names = sorted({o.name for o in XELAB_OPTIONS if o.name.startswith("--") or len(o.name) == 2})
    return ("Usage: xelab [options] [lib.]top...\n"
            "Elaborates the tops from the libraries analysed by xvlog/xvhdl (work and every -L) into\n"
            "%s/<snapshot>/, through sv2ghdl + nvc (vamos); -R runs it, else `xsim <snapshot> -R`.\n"
            "Options accepted (--long or -long): %s\n"
            "The user guide: %s" % (XSIM_DIR, " ".join(names), tools.guide_path()))


def _gather(job: _ElabJob, con: Console) -> Tuple[List[dict], List[str]]:
    """The sources xelab elaborates: this call's --vlog/--svlog/--vhdl* files and project
    lines, then the libraries searched (work first, then each -L, once each), in analysis
    order.  Returns (sources, the libraries searched)."""
    srcs: List[dict] = []
    for lang, p in job.direct:
        srcs.append({"path": p, "lang": lang, "defines": dict(job.defines), "incdirs": list(job.incdirs),
                     "std": lang if lang != "vhdl" else "vhdl2008", "lib_files": [], "lib_dirs": [], "lib_exts": []})
    for prj in job.prj:
        for lang, lib, files in _prj_lines(prj, job.cwd):
            for f in files:
                srcs.append({"path": f, "lang": "vhdl" if lang.startswith("vhdl") else lang,
                             "defines": dict(job.defines), "incdirs": list(job.incdirs),
                             "std": lang if lang != "vhdl" else "vhdl2008", "lib_files": [], "lib_dirs": [], "lib_exts": []})
    libs = []
    for lib in [job.library] + job.libs:
        if lib in libs:
            continue
        libs.append(lib)
    for lib in libs:
        if lib in XILINX_LIBS:
            job.note("-L " + lib, NOTED, "a Xilinx simulation library of Vivado's own simulator; "
                     "not available on nvc")
            continue
        found = _read_lib(job.xsim_dir, lib)
        if not found and lib != job.library:
            job.note("-L " + lib, NOTED, "no sources were analysed into library %s" % lib)
        srcs += found
    return srcs, libs


def _vhdl_only(job: Job, be: NvcBackend, srcs: List[dict], con: Console) -> List[str]:
    """All-VHDL snapshot: nvc analyses and elaborates the files directly."""
    std = {"vhdl2008": "2008", "vhdl2019": "2019", "vhdl1993": "1993"}.get(srcs[-1]["std"], "2008")
    os.makedirs(be.workdir, exist_ok=True)
    rc = be._call([be.nvc, "--std=" + std, "--work=" + be.work_spec(), "-L", be.libdir, "-a"]
                  + [s["path"] for s in srcs], cwd=job.cwd)
    if rc != 0:
        raise BackendError("VHDL analysis failed (exit %d)" % rc)
    with open(os.path.join(be.workdir, "_metadata"), "w") as fh:
        fh.write('NVC_STD="%s"\nTOP_ENTITY="%s"\n' % (std, job.tops[0]))
    rc = be._call([be.nvc, "--std=" + std, "--work=" + be.work_spec(), "-L", be.libdir, "-e", job.tops[0]],
                  cwd=be.workdir)
    if rc != 0:
        raise BackendError("elaboration failed (exit %d)" % rc)
    return [job.tops[0]]


def main_xelab(args: List[str], opts: dict) -> int:
    con = Console()
    cwd = os.getcwd()
    job = _ElabJob(args, cwd)
    try:
        scan(XELAB_TABLE, _expand(args, cwd), job, _positional, _unknown)
    except (argscan.ArgError, ScanError) as e:
        con.err("vamos: error: %s" % e)
        return 1
    if job.help_requested:
        con.out(xelab_help())
        return 0
    if job.version_requested:
        con.out("vamos %s (xelab personality)" % VERSION)
        return 0
    _note_vamos_options(job, opts, "vcs", run=job.run)
    if not job.nolog:
        con.open_log(job.log or os.path.join(cwd, "xelab.log"))
    try:
        prof = banner.load_profile("xsim", opts.get("banner"))
    except ValueError as e:
        con.err("vamos: error: %s" % e)
        return 1
    ban = banner.Banner(prof, "xelab")
    t = ban.text("compile_start")
    if t is not None:
        con.out(t)

    tops = _tops(job, con)
    if tops is None:
        return 1
    if not tops:
        con.err("vamos: error: no top-level unit given")
        return 1
    try:
        srcs, libs = _gather(job, con)
    except (OSError, ScanError, BackendError) as e:
        con.err("vamos: error: %s" % e)
        return 1
    if not srcs:
        con.err("vamos: error: nothing to elaborate: no sources were analysed into %s (xvlog/xvhdl "
                "first, or give --vlog/--svlog/--vhdl files)" % ", ".join(libs))
        return 1
    missing = [s["path"] for s in srcs if not os.path.isfile(s["path"])]
    for m in missing:
        con.err("vamos: error: source file '%s' cannot be opened (analysed earlier, gone now)" % m)
    if missing:
        return 1
    try:
        engine = engine_of(opts)
    except BackendError as e:
        con.err("vamos: error: %s" % e)
        return 1
    langs = {s["lang"] for s in srcs}
    vhdl_only = langs == {"vhdl"}
    if "vhdl" in langs and not vhdl_only and engine == "nvc":
        con.err("vamos: error: mixed VHDL and Verilog elaboration is not supported yet by the xelab "
                "personality (an all-Verilog or all-VHDL snapshot is)")
        return 1

    snap = job.snapshot or "%s.%s" % (job.library, tops[0])
    daidir = os.path.join(job.xsim_dir, snap)
    defines: Dict[str, Optional[str]] = {}
    incdirs: List[str] = []
    lib_files: List[str] = []
    lib_dirs: List[str] = []
    lib_exts: List[str] = []
    for s in srcs:
        defines.update(s.get("defines", {}))
        for d in s.get("incdirs", []):
            if d not in incdirs:
                incdirs.append(d)
        for k, acc in (("lib_files", lib_files), ("lib_dirs", lib_dirs), ("lib_exts", lib_exts)):
            for x in s.get(k, []):
                if x not in acc:
                    acc.append(x)
    compiled = Job(personality="xelab", argv=list(args), cwd=cwd,
                   sources=[Source(s["path"], s["lang"]) for s in srcs],
                   lib_files=lib_files, lib_dirs=lib_dirs, lib_exts=lib_exts,
                   incdirs=incdirs, defines=defines, timescale=job.timescale,
                   std="sv" if "sv" in langs else "verilog", tops=tops,
                   exe=os.path.join(daidir, "xsimk"), daidir=daidir, log=job.log, run=job.run,
                   unmapped=list(job.unmapped), engine=engine)
    try:
        be = VerilatorBackend(compiled, con.out) if engine == "verilator" else NvcBackend(compiled, con.out)
        used = [("vamos", tools.version_of("vamos", tools.launcher()), tools.launcher())]
        used += [(n, tools.version_of(n, p), p) for n, p in be.compile_tools()]
    except (BackendError, tools.ToolError) as e:
        con.err("vamos: error: %s" % e)
        return 1
    con.out(banner.provenance(used, "xelab"))
    rc = _finish(con, compiled, opts, "xsim")
    if rc:
        return rc
    try:
        os.makedirs(daidir, exist_ok=True)
        for p in (os.path.join(daidir, vcs_mod.JOB_FILE),):
            try:
                os.remove(p)
            except OSError:
                pass
    except OSError as e:
        con.err("vamos: error: cannot prepare %s: %s" % (daidir, e))
        return 1
    c0 = cpu_time()
    try:
        if engine == "verilator":
            c1 = be.build(tops[0])
            tops_done = [tops[0]]
        elif vhdl_only:
            tops_done = _vhdl_only(compiled, be, srcs, con)
            c1 = cpu_time()
        else:
            tops_done, c1 = vcs_mod.plain_compile(compiled, be, con, opts)
    except (BackendError, tools.ToolError) as e:
        con.err("vamos: error: %s" % e)
        t = ban.text("compile_failed")
        if t is not None:
            con.out(t)
        return 1
    c2 = cpu_time()
    vcs_mod._write_atomic(os.path.join(daidir, vcs_mod.JOB_FILE), compiled.to_json())
    simv_mod.write_tools(daidir, used)
    t = ban.text("compile_tops", tops_lines="\n".join("       " + x for x in tops_done))
    if t is not None:
        con.out(t)
    t = ban.text("compile_done", exe=os.path.relpath(daidir, cwd), cpu_compile=c1 - c0, cpu_elab=c2 - c1)
    if t is not None:
        con.out(t)
    if job.run:
        if engine == "verilator":
            return run_verilator(compiled, [], con, opts)
        con.close()
        run_args = ["-l", job.log, "--vamos-append-log"] if job.log else []
        return simv_mod.run_daidir(daidir, run_args, opts)
    con.close()
    return 0


def run_verilator(compiled: Job, plusargs: List[str], con: Console, opts: dict) -> int:
    """Run a snapshot built by the Verilator engine: the provenance header, the binary's
    output, a footer with the CPU time (Verilator prints the end time itself)."""
    try:
        be = VerilatorBackend(compiled, con.out)
    except BackendError as e:
        con.err("vamos: error: %s" % e)
        return 1
    used = [("vamos", tools.version_of("vamos", tools.launcher()), tools.launcher())]
    used += [(n, tools.version_of(n, p), p) for n, p in be.run_tools()]
    con.out(banner.provenance(used, "xsim"))
    c0 = cpu_time()
    try:
        rc = be.run(plusargs, con.out, con.err)
    except BackendError as e:
        con.err("vamos: error: %s" % e)
        con.close()
        return 1
    con.out("vamos: verilator run ended with status %d; CPU time %.3f seconds" % (rc, cpu_time() - c0))
    con.close()
    return rc


# -- xsim ---------------------------------------------------------------------------------------

class _RunJob(_AnJob):
    def __init__(self, args, cwd):
        super().__init__("xsim", args, cwd)
        self.runall = False
        self.tcl: List[str] = []
        self.plusargs: List[str] = []
        self.seed: Optional[str] = None
        self.snapshot: Optional[str] = None


def _testplusarg(job, val):
    job.plusargs.append("+" + val)


XSIM_OPTIONS = _opts([
    (("-h", "--help"), "flag", _help),
    ("--version", "flag", _version),
    ("--log", "next", _set("log")),
    ("--nolog", "flag", _flag("nolog")),
    (("-R", "--runall"), "flag", _flag("runall")),
    (("-t", "--tclbatch"), "next", lambda job, val: job.tcl.append(os.path.join(job.cwd, val))),
    ("--testplusarg", "next", _testplusarg),
    ("--sv_seed", "next", _set("seed")),
    ("--xsimdir", "next", _xsim_dir),
    (("-g", "--gui"), "flag", NOTED, "no GUI; waves need $dumpvars in the design"),
    ("--wdb", "next", NOTED, "no waveform database is written ($dumpvars writes a VCD)"),
    ("--view", "next", NOTED, "no GUI"),
    ("--onfinish", "next", IGNORED),
    ("--onerror", "next", IGNORED),
    ("--maxdeltaid", "next", IGNORED),
    ("--maxlogsize", "next", IGNORED),
    ("--stats", "flag", IGNORED),
    ("--protoinst", "next", IGNORED),
    ("--ieeewarnings", "flag", IGNORED),
    ("--nosignalhandlers", "flag", IGNORED),
    ("--autoloadwcfg", "flag", IGNORED),
    ("--clear_child_on_exit", "flag", IGNORED),
    ("--disable_circular_dependency_check", "flag", IGNORED),
    ("--disable_multi_driver_resolution_scheduling", "flag", IGNORED),
    ("--temp", "next", IGNORED),
    (("--tl", "--tp"), "flag", IGNORED),
    (("--downgrade_error2info", "--downgrade_error2warning", "--downgrade_fatal2info",
      "--downgrade_fatal2warning"), "flag", NOTED, "severity downgrades are not applied"),
    ("--downgrade_severity", "next", NOTED, "severity downgrades are not applied"),
    (("--cov_db_dir", "--cov_db_name"), "next", UNSUPPORTED, "coverage"),
    ("--ignore_assertions", "flag", NOTED, "assertions are not controlled yet"),
    ("--ignore_coverage", "flag", IGNORED),
    ("--ignore_feature", "next", IGNORED),
])
XSIM_TABLE = Table(XSIM_OPTIONS)

_UNITS = {"fs": 1, "ps": 10 ** 3, "ns": 10 ** 6, "us": 10 ** 9, "ms": 10 ** 12, "s": 10 ** 15}
_RUN_RE = re.compile(r"^run(?:\s+(?:-all|all)|\s+(\d+(?:\.\d+)?)\s*(fs|ps|ns|us|ms|s)?)?\s*;?$")


def tcl_batch(lines: List[str], job) -> Tuple[bool, Optional[int]]:
    """The subset of a -t batch file xsim honours: `run all` (run to completion), `run N unit`
    (summed, in femtoseconds), `quit`/`exit`.  Wave logging, puts/set and anything else is
    noted and skipped; a `run` with no unit uses the default (ns)."""
    run_all = False
    total_fs = 0
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = _RUN_RE.match(line)
        if m:
            if m.group(1) is None:
                run_all = True
            else:
                total_fs += int(float(m.group(1)) * _UNITS[m.group(2) or "ns"])
            continue
        word = line.split()[0]
        if word in ("quit", "exit"):
            break
        if word in ("log_wave", "open_vcd", "log_vcd", "close_vcd", "add_wave", "open_wave_config",
                    "save_wave_config", "add_force", "remove_forces", "restart", "current_scope",
                    "report_values", "get_value", "set_value"):
            job.note("-t: " + word, NOTED, "Tcl simulation commands are not supported; skipped")
        elif word in ("set", "puts", "source", "if", "proc", "foreach", "while"):
            job.note("-t: " + word, NOTED, "Tcl scripting is not interpreted; skipped")
        else:
            job.note("-t: " + word, UNKNOWN, "unknown Tcl command skipped")
    return run_all, (total_fs if not run_all else None)


def xsim_help() -> str:
    names = sorted({o.name for o in XSIM_OPTIONS if o.name.startswith("--") or len(o.name) == 2})
    return ("Usage: xsim <snapshot> (-R | -t script.tcl) [options]\n"
            "Runs a snapshot xelab built under %s/ on nvc (vamos).  -R runs to completion; a -t Tcl\n"
            "batch file's `run all` / `run N ns` set how far; --testplusarg N=V is a plusarg.\n"
            "Options accepted (--long or -long): %s\n"
            "The user guide: %s" % (XSIM_DIR, " ".join(names), tools.guide_path()))


def main_xsim(args: List[str], opts: dict) -> int:
    con = Console()
    cwd = os.getcwd()
    job = _RunJob(args, cwd)
    try:
        scan(XSIM_TABLE, _expand(args, cwd), job, _positional, _unknown)
    except (argscan.ArgError, ScanError) as e:
        con.err("vamos: error: %s" % e)
        return 1
    if job.help_requested:
        con.out(xsim_help())
        return 0
    if job.version_requested:
        con.out("vamos %s (xsim personality)" % VERSION)
        return 0
    _note_vamos_options(job, opts, "simv")
    if len(job.files) != 1:
        con.err("vamos: error: xsim takes one snapshot name%s" % (": " + " ".join(job.files) if job.files else ""))
        return 1
    snap = job.files[0]
    daidir = os.path.join(job.xsim_dir, snap)
    if not os.path.isfile(os.path.join(daidir, vcs_mod.JOB_FILE)):
        con.err("vamos: error: snapshot '%s' was not built (no %s/%s/%s; run xelab first)"
                % (snap, os.path.relpath(job.xsim_dir, cwd), snap, vcs_mod.JOB_FILE))
        return 1
    try:
        with open(os.path.join(daidir, vcs_mod.JOB_FILE)) as fh:
            compiled = Job.from_json(fh.read())
    except (OSError, ValueError, TypeError) as e:
        con.err("vamos: error: %s/%s: %s; run xelab again" % (daidir, vcs_mod.JOB_FILE, e))
        return 1
    finish: Optional[str] = None
    if job.tcl:
        lines: List[str] = []
        for p in job.tcl:
            try:
                with open(p) as fh:
                    lines += fh.read().splitlines()
            except OSError as e:
                con.err("vamos: error: %s" % e)
                return 1
        run_all, fs = tcl_batch(lines, job)
        if not run_all:
            if not fs:
                job.note("-t", NOTED, "the batch file runs no simulation time; nothing is simulated")
                report_unmapped(job, con.err)
                return 0
            finish = "%dfs" % fs
    elif not job.runall:
        con.err("vamos: error: xsim's interactive Tcl shell is not provided: run with -R, or give a "
                "-t batch file whose `run` commands set the simulation time")
        return 1
    run_args = list(job.plusargs)
    if compiled.engine == "verilator":
        if finish:
            job.note("-t: run %s" % finish, UNSUPPORTED, "a Verilator binary has no stop time; the run goes "
                     "to the design's own end")
        if job.seed is not None:
            job.note("--sv_seed " + job.seed, NOTED, "passed to the Verilator binary as +ntb_random_seed")
            run_args.append("+ntb_random_seed=" + job.seed)
        if not job.nolog:
            con.open_log(job.log or os.path.join(cwd, "xsim.log"))
        report_unmapped(job, con.err)
        if opts.get("strict") and strict_failures(job):
            con.err("vamos: error: " + strict_message(strict_failures(job)))
            return 1
        compiled.daidir = daidir
        return run_verilator(compiled, run_args, con, opts)
    if job.seed is not None:
        run_args.append("+ntb_random_seed=" + job.seed)
    if finish:
        run_args.append("+vcs+finish+" + finish)
    if not job.nolog:
        run_args += ["-l", job.log or os.path.join(cwd, "xsim.log")]
    report_unmapped(job, con.err)
    if opts.get("strict"):
        bad = strict_failures(job)
        if bad:
            con.err("vamos: error: " + strict_message(bad))
            return 1
    return simv_mod.run_daidir(daidir, run_args, opts)
