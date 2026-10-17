"""Shared helpers for the vamos spectre tests (docs/VAMOS_SPECTRE_DESIGN.md §11): the gates, the
fixture paths and the SpectreCase base class.  Standard library only, Python 3.9, both legs.

Gates (`unittest.skipUnless` decorators; every skip prints its reason).  They read the environment
and nothing else, except where §11 names a path:

  needs_vacask, needs_xyce   vamos_testlib's (VAMOS_VACASK_HOME; VAMOS_XYCE and its default)
  needs_engines              an analog engine without nvc (ams_e2e_lib.needs_ams also wants nvc and
                             iverilog)
  needs_cpp                  the C preprocessor, as vamos finds it (tools.find_real: VAMOS_CPP, then
                             the PATH), else WSL's /usr/bin/cpp (§11); Cygwin has none
  needs_psf_parser           Python >= 3.10 and the psf_parser package: VAMOS_PSF_PARSER names the
                             directory that holds it, default tests/vamos/third_party/psf_parser/src
                             (the vendored copy, §11)
  needs_psf_utils            VAMOS_PSF_UTILS (a psf_utils checkout) and VAMOS_PSF_UTILS_DEPS (the
                             directories of its dependencies, os.pathsep separated: ply 3.10, inform,
                             arrow, six, quantiphy); numpy comes from the system; nothing is vendored
  needs_ngspice              ngspice as vamos finds it (VAMOS_NGSPICE, then the PATH)
  needs_cadnip               VAMOS_CADNIP_VACASK (a VACASK built with -DCADNIP_PARSERS=ON, recipe in
                             fixtures/spectre/cadnip/BUILD) and SIM_MODULE_PATH (its device directory)
  needs_pyms                 an Xyce: both launchers here carry PyMS and, under engines.env_for, load
                             the same library (§11 Gates, §13 E96), so no binary has to be selected
  needs_cmc                  VAMOS_CMC=1 and VAMOS_CMC_EXAMPLES (the CMC decks, read in place)
  needs_vacask_src           VAMOS_VACASK_SRC (VACASK's source tree; no default, §11)
  needs_vacask_rawread       needs_vacask_src plus numpy (VACASK's python/rawfile.py)
  needs_openvaf              VACASK and engines.openvaf() (phase 2: the copy test_spectre_engine_facts
                             defines, here for the e2e decks that list it)
  needs_flow                 vamos/spectre/flow.py exists (phase 2): without it personalities.spectre
                             stops every run with exit status 2

`GATES` maps each name to (predicate, reason), so an expect.json can list gates by name.

    class TestRc(SpectreCase):
        def test_rc(self):
            for engine in self.engines():
                with self.subTest(engine=engine):
                    d = self.case("rc_" + engine, {"rc.scs": DECK})
                    r = self.run_spectre(d, "rc.scs", engine=engine)
                    psf = psfcheck.check(os.path.join(d, "rc.raw", "tr.tran.tran"))

The T2 tier (§11.2, phase 2): an e2e deck directory fixtures/spectre/e2e/<nn>_<slug>/ with an
expect.json is checked by `SpectreCase.check_case(case_dir)`: per engine, a copy of the directory,
`vamos -spectre <argv> --vamos-analog=<engine>` in it (run_spectre), then `check_expect`, the
vendored psf-parser over every PSF file (needs_psf_parser), the cross-engine comparison and the
`also` decks.  The expect.json keys (one object; every key but argv and status optional):

  argv          the deck file relative to the deck directory, then the options (["deck.scs",
                "-format", "nutascii"]); a run that reads the netlist from stdin starts with an
                option (or is []) and names the file in `stdin`
  engines       the engines the base expectation applies to, a subset of ["vacask", "xyce"]
  status        the exit status
  files         the exact set of files in the results directory (§8.1's names, logFile included
                for the psf formats); for nutascii/nutbin, the results file alone ([<name>.raw]),
                and no logFile may exist beside it (§8.5); [] matches an absent or empty directory;
                left out (or null): not checked
  values        [{file, signal, at, value, rel, abs, row, points}], read with psfcheck's reader:
                  at     the sweep value (an exact grid point, else linear interpolation along an
                         ascending sweep); null for an operating point, a one-point file, a row or
                         a whole column
                  row    an index into the file's points (-1 the last)
                  value  a number; [re, im] for a complex value; a dict member -> number for a
                         STRUCT (only the members given are compared); a list for the whole
                         column (the sweep variable of a parent: "rval": [1000.0, 2000.0]); null:
                         the signal must be absent from the file
                  signal a trace or operating-point name, or the file's sweep variable itself
                  rel, abs  |got - want| <= abs + rel*|want| (for a complex value each part
                         against the magnitude); neither given: exact
                  points the file's point count (an operating point has 1)
  cross_engine  {rel}: every value entry read on both engines of `engines` agrees within rel
                (and the entry's abs)
  messages      [[severity, substring]]: a Spectre message of that severity (error, warning,
                note) whose text holds the substring (whitespace-normalized), on the screen or
                in the +log/=log file
  vacask, xyce  per-engine overrides: merged over the base for an engine in `engines` (one whose
                status 2 or 3 stops a run the base expects to end 0 or 1 restates files and values;
                files null: not checked); for an engine only an override names (e2e 5's xyce), the
                whole expectation, with the base's argv, env, stdin and gates
  also          a second deck (or a list of them): {deck | argv, status, ...} with the keys above,
                each run in its own copy of the directory; it takes `engines` and `gates` from the
                main expectation unless it gives its own engines (gates add up)
  gates         gate names of GATES: the case is skipped with the gate's reason when one fails
  env           extra environment for the run (strings); spectre_DEFAULTS, SPECTRE_DEFAULTS,
                VAMOS_BANNER and VAMOS_SPECTRE_NAMES are never inherited, only given here
  stdin         a file in the deck directory fed to vamos's stdin (otherwise /dev/null)
  real          {dir, logfile, omit}: compare_results_dir of the results directory against a real
                Spectre run (e2e 28): dir and logfile relative to the deck directory, else to
                fixtures/spectre; omit a list of logFile keys or "RC_OMIT_KEYS"

The results path is found as §2.5 gives it, from argv and the defaults variable: -raw (or -r),
else %C:r.raw moved into -outdir when it has no "/"; %C, its colon modifiers and %% are expanded
(another percent code there is an authoring error).  The log file: the last +log/=log (or +l/=l),
an argv -log cancelling the defaults', under -outdir when its name has no "/".  An `options
rawfile=` or `rawfmt=` in the netlist is not seen: such a deck needs its own test module
(test_spectre_e2e_<nn>.py), as does every check expect.json cannot say.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import unittest
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from vamos_testlib import (FIXTURES, LAUNCHER, LINUX, ROOT, TempDir, fixture, have_vacask,  # noqa: F401
                           have_xyce, needs_vacask, needs_xyce, reap, run, vacask_bin, xyce_bin,
                           xyce_env)
from vamos import tools  # noqa: E402

import psfcheck  # noqa: E402  (tests/vamos: the strict PSF reader, stdlib only)

SHIMS = os.path.join(ROOT, "shims")
SPECTRE_FIXTURES = os.path.join(FIXTURES, "spectre")
THIRD_PARTY = os.path.join(ROOT, "tests", "vamos", "third_party")
DESIGN_DOC = os.path.join(ROOT, "docs", "VAMOS_SPECTRE_DESIGN.md")
TIMEOUT = 1200

_ToolError = getattr(tools, "ToolError", Exception)


def spectre_fixture(*parts: str) -> str:
    return os.path.join(SPECTRE_FIXTURES, *parts)


# -- engines -----------------------------------------------------------------------------------------

def engines_available() -> List[str]:
    """The analog engines installed, in vamos's order (vacask first)."""
    return [e for e, ok in (("vacask", have_vacask()), ("xyce", have_xyce())) if ok]


def have_engines() -> bool:
    return bool(engines_available())


def have_pyms() -> bool:
    """Every Xyce here registers `.hdl` modules through PyMS (§13 E96), so this is `have_xyce`."""
    return have_xyce()


# -- external tools ----------------------------------------------------------------------------------

def _find(name: str) -> str:
    """A tool as vamos finds it (VAMOS_<NAME>, then the scrubbed PATH); "" when there is none."""
    try:
        return tools.find_real(name) or ""
    except _ToolError:
        return ""


def cpp_bin() -> str:
    found = _find("cpp")
    if found:
        return found
    return "/usr/bin/cpp" if LINUX and os.access("/usr/bin/cpp", os.X_OK) else ""


def have_cpp() -> bool:
    return LINUX and bool(cpp_bin())


def ngspice_bin() -> str:
    return _find("ngspice")


def have_ngspice() -> bool:
    return LINUX and bool(ngspice_bin())


# -- PSF consumers -----------------------------------------------------------------------------------

def psf_parser_dir() -> str:
    """The directory holding the `psf_parser` package (VAMOS_PSF_PARSER, else the vendored copy)."""
    return os.environ.get("VAMOS_PSF_PARSER") or os.path.join(THIRD_PARTY, "psf_parser", "src")


def have_psf_parser() -> bool:
    return sys.version_info >= (3, 10) and os.path.isdir(os.path.join(psf_parser_dir(), "psf_parser"))


def import_psf_parser():
    """Import psf_parser from `psf_parser_dir()` (put first on sys.path) and return the module."""
    d = psf_parser_dir()
    if d not in sys.path:
        sys.path.insert(0, d)
    import psf_parser  # noqa: E402  (vendored, MIT; Python >= 3.10)
    return psf_parser


def psf_utils_paths() -> List[str]:
    """sys.path entries for psf_utils: its dependencies (VAMOS_PSF_UTILS_DEPS), then the checkout."""
    deps = [p for p in os.environ.get("VAMOS_PSF_UTILS_DEPS", "").split(os.pathsep) if p]
    home = os.environ.get("VAMOS_PSF_UTILS", "")
    return deps + ([home] if home else [])


def have_psf_utils() -> bool:
    home, deps = os.environ.get("VAMOS_PSF_UTILS"), os.environ.get("VAMOS_PSF_UTILS_DEPS")
    return bool(home and deps) and all(os.path.isdir(p) for p in psf_utils_paths())


def have_numpy() -> bool:
    try:
        import numpy  # noqa: F401
    except Exception:  # noqa: BLE001  (an ImportError, or a broken installation)
        return False
    return True


# -- optional sources and oracles --------------------------------------------------------------------

def cadnip_bin() -> str:
    return os.environ.get("VAMOS_CADNIP_VACASK", "")


def have_cadnip() -> bool:
    mods = os.environ.get("SIM_MODULE_PATH", "")
    return (LINUX and bool(cadnip_bin()) and os.access(cadnip_bin(), os.X_OK)
            and bool(mods) and os.path.isdir(mods))


def cmc_examples() -> str:
    return os.environ.get("VAMOS_CMC_EXAMPLES", "")


def have_cmc() -> bool:
    return os.environ.get("VAMOS_CMC") == "1" and os.path.isdir(cmc_examples())


def vacask_src() -> str:
    return os.environ.get("VAMOS_VACASK_SRC", "")


def have_vacask_src() -> bool:
    return bool(vacask_src()) and os.path.isdir(vacask_src())


def have_vacask_rawread() -> bool:
    return (have_vacask_src() and os.path.isfile(os.path.join(vacask_src(), "python", "rawfile.py"))
            and have_numpy())


needs_engines = unittest.skipUnless(have_engines(), "needs an analog engine, VACASK or Xyce (Linux/WSL)")
needs_cpp = unittest.skipUnless(have_cpp(), "needs the C preprocessor (VAMOS_CPP, the PATH or /usr/bin/cpp; WSL)")
needs_psf_parser = unittest.skipUnless(have_psf_parser(),
                                       "needs Python >= 3.10 and psf_parser (VAMOS_PSF_PARSER or the vendored copy)")
needs_psf_utils = unittest.skipUnless(have_psf_utils(), "needs VAMOS_PSF_UTILS and VAMOS_PSF_UTILS_DEPS")
needs_ngspice = unittest.skipUnless(have_ngspice(), "needs ngspice (VAMOS_NGSPICE or the PATH; Linux/WSL)")
needs_cadnip = unittest.skipUnless(have_cadnip(), "needs VAMOS_CADNIP_VACASK and SIM_MODULE_PATH (Linux/WSL)")
needs_pyms = unittest.skipUnless(have_pyms(), "needs Xyce with PyMS (Linux/WSL)")
needs_cmc = unittest.skipUnless(have_cmc(), "needs VAMOS_CMC=1 and VAMOS_CMC_EXAMPLES")
needs_vacask_src = unittest.skipUnless(have_vacask_src(), "needs VAMOS_VACASK_SRC (VACASK's source tree)")
needs_vacask_rawread = unittest.skipUnless(have_vacask_rawread(),
                                           "needs VAMOS_VACASK_SRC with python/rawfile.py, and numpy")


# -- phase 2's gates ---------------------------------------------------------------------------------

def have_openvaf() -> bool:
    """VACASK with an openvaf-r (`engines.openvaf()`): the decks that compile Verilog-A on VACASK."""
    if not have_vacask():
        return False
    from vamos.ams import engines  # noqa: E402  (imported only where VACASK exists)
    return bool(engines.openvaf())


def have_flow() -> bool:
    """vamos/spectre/flow.py exists (phase 2, §12): personalities.spectre.main hands it the job."""
    try:
        return importlib.util.find_spec("vamos.spectre.flow") is not None
    except (ImportError, ValueError):
        return False


FLOW_REASON = "needs vamos/spectre/flow.py (phase 2): without it every spectre run exits 2"
needs_openvaf = unittest.skipUnless(have_openvaf(), "needs openvaf-r (Linux/WSL)")
needs_flow = unittest.skipUnless(have_flow(), FLOW_REASON)

# every gate by name, (predicate, reason): the reasons are the decorators' own (TestGates checks it)
GATES: Dict[str, Tuple[Callable[[], bool], str]] = {
    "needs_vacask": (have_vacask, "needs VACASK (Linux/WSL)"),
    "needs_xyce": (have_xyce, "needs Xyce (Linux/WSL)"),
    "needs_engines": (have_engines, "needs an analog engine, VACASK or Xyce (Linux/WSL)"),
    "needs_cpp": (have_cpp, "needs the C preprocessor (VAMOS_CPP, the PATH or /usr/bin/cpp; WSL)"),
    "needs_psf_parser": (have_psf_parser,
                         "needs Python >= 3.10 and psf_parser (VAMOS_PSF_PARSER or the vendored copy)"),
    "needs_psf_utils": (have_psf_utils, "needs VAMOS_PSF_UTILS and VAMOS_PSF_UTILS_DEPS"),
    "needs_ngspice": (have_ngspice, "needs ngspice (VAMOS_NGSPICE or the PATH; Linux/WSL)"),
    "needs_cadnip": (have_cadnip, "needs VAMOS_CADNIP_VACASK and SIM_MODULE_PATH (Linux/WSL)"),
    "needs_pyms": (have_pyms, "needs Xyce with PyMS (Linux/WSL)"),
    "needs_cmc": (have_cmc, "needs VAMOS_CMC=1 and VAMOS_CMC_EXAMPLES"),
    "needs_vacask_src": (have_vacask_src, "needs VAMOS_VACASK_SRC (VACASK's source tree)"),
    "needs_vacask_rawread": (have_vacask_rawread, "needs VAMOS_VACASK_SRC with python/rawfile.py, and numpy"),
    "needs_openvaf": (have_openvaf, "needs openvaf-r (Linux/WSL)"),
    "needs_flow": (have_flow, FLOW_REASON),
}
ENGINE_GATES = {"vacask": "needs_vacask", "xyce": "needs_xyce"}


def gate_problems(names: Sequence[str]) -> List[str]:
    """The reasons of the gates among `names` that fail; ValueError for a name GATES lacks."""
    out = []
    for n in names:
        if n not in GATES:
            raise ValueError("unknown gate %r (spectre_testlib.GATES: %s)" % (n, ", ".join(sorted(GATES))))
        pred, why = GATES[n]
        if not pred():
            out.append(why)
    return out


# -- expect.json (§11 T2) ----------------------------------------------------------------------------

ENGINE_NAMES = ("vacask", "xyce")
EXPECT_KEYS = frozenset(["argv", "engines", "status", "files", "values", "cross_engine", "messages", "also",
                         "gates", "env", "stdin", "real", "deck"]) | frozenset(ENGINE_NAMES)
OVERRIDE_KEYS = EXPECT_KEYS - frozenset(["engines", "also", "deck"]) - frozenset(ENGINE_NAMES)
VALUE_KEYS = frozenset(["file", "signal", "at", "value", "rel", "abs", "row", "points"])
REAL_KEYS = frozenset(["dir", "logfile", "omit"])
SEVERITIES = {"error": "error", "warning": "warning", "note": "note", "notice": "note"}
DEFAULTS_VARS = ("spectre_DEFAULTS", "SPECTRE_DEFAULTS")         # <prog>_DEFAULTS, <PROG>_DEFAULTS (§2.2)
UNSET_VARS = DEFAULTS_VARS + ("VAMOS_BANNER", "VAMOS_SPECTRE_NAMES")
NUTMEG_FORMATS = ("nutascii", "nutbin")
PSF_DATA_EXTS = (".dc", ".ac", ".noise", ".xf", ".tran", ".sweep", ".montecarlo", ".info")


def _is_num(x: object) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def is_psf_data(name: str) -> bool:
    """A PSF data file of a results directory by its §8.1 name (logFile is the index)."""
    return name != "logFile" and name.endswith(PSF_DATA_EXTS)


def _value_problems(entry: object, where: str) -> List[str]:
    if not isinstance(entry, dict):
        return ["%s: a value entry is an object, got %r" % (where, entry)]
    out = []
    extra = sorted(set(entry) - VALUE_KEYS)
    if extra:
        out.append("%s: unknown keys %s (known: %s)" % (where, extra, ", ".join(sorted(VALUE_KEYS))))
    for k in ("file", "signal"):
        if not isinstance(entry.get(k), str) or not entry.get(k):
            out.append("%s: %s must be a non-empty string" % (where, k))
    if "value" not in entry:
        out.append("%s: no value (null: the signal is absent)" % where)
    for k in ("rel", "abs"):
        if k in entry and not (_is_num(entry[k]) and entry[k] >= 0):
            out.append("%s: %s must be a number >= 0" % (where, k))
    if entry.get("at") is not None and not _is_num(entry["at"]):
        out.append("%s: at must be a number or null" % where)
    for k in ("row", "points"):
        if k in entry and not isinstance(entry[k], int):
            out.append("%s: %s must be an integer" % (where, k))
    if entry.get("at") is not None and "row" in entry:
        out.append("%s: give at or row, not both" % where)
    return out


def expect_problems(expect: object, where: str = "expect.json", top: bool = True) -> List[str]:
    """The authoring errors of an expectation (unknown keys, wrong types); [] when it is well formed.
    `top`: the main object (engines may be given, also allowed) rather than an override."""
    if not isinstance(expect, dict):
        return ["%s: an object is expected" % where]
    out = []
    known = EXPECT_KEYS if top else OVERRIDE_KEYS
    extra = sorted(set(expect) - known)
    if extra:
        out.append("%s: unknown keys %s" % (where, extra))
    argv = expect.get("argv")
    if top and argv is None and "deck" not in expect:
        out.append("%s: no argv" % where)
    if argv is not None and not (isinstance(argv, list) and all(isinstance(a, str) for a in argv)):
        out.append("%s: argv must be a list of strings" % where)
    if "deck" in expect and not isinstance(expect["deck"], str):
        out.append("%s: deck must be a file name" % where)
    if top and "status" not in expect and not any(isinstance(expect.get(e), dict) and "status" in expect[e]
                                                  for e in ENGINE_NAMES):
        out.append("%s: no status" % where)
    if "status" in expect and expect["status"] not in (0, 1, 2, 3):
        out.append("%s: status must be 0, 1, 2 or 3" % where)
    eng = expect.get("engines")
    if eng is not None and not (isinstance(eng, list) and all(e in ENGINE_NAMES for e in eng)):
        out.append("%s: engines must be a subset of %s" % (where, list(ENGINE_NAMES)))
    files = expect.get("files")
    if files is not None and not (isinstance(files, list) and all(isinstance(f, str) and f for f in files)):
        out.append("%s: files must be a list of file names" % where)
    vals = expect.get("values", [])
    if not isinstance(vals, list):
        out.append("%s: values must be a list" % where)
    else:
        for k, v in enumerate(vals):
            out += _value_problems(v, "%s values[%d]" % (where, k))
    msgs = expect.get("messages", [])
    if not isinstance(msgs, list):
        out.append("%s: messages must be a list" % where)
    else:
        for k, m in enumerate(msgs):
            if not (isinstance(m, list) and len(m) == 2 and m[0] in SEVERITIES and isinstance(m[1], str) and m[1]):
                out.append("%s messages[%d]: [severity, substring] with severity one of %s, got %r"
                           % (where, k, sorted(SEVERITIES), m))
    ce = expect.get("cross_engine")
    if ce is not None and not (isinstance(ce, dict) and set(ce) <= {"rel"} and _is_num(ce.get("rel", 0))):
        out.append("%s: cross_engine must be {rel: number}" % where)
    gates = expect.get("gates", [])
    if not (isinstance(gates, list) and all(isinstance(g, str) for g in gates)):
        out.append("%s: gates must be a list of gate names" % where)
    else:
        for g in gates:
            if g not in GATES:
                out.append("%s: unknown gate %r (GATES: %s)" % (where, g, ", ".join(sorted(GATES))))
    env = expect.get("env")
    if env is not None and not (isinstance(env, dict) and all(isinstance(k, str) and isinstance(v, str)
                                                              for k, v in env.items())):
        out.append("%s: env must map names to strings" % where)
    if "stdin" in expect and not (isinstance(expect["stdin"], str) and expect["stdin"]):
        out.append("%s: stdin must be a file name" % where)
    real = expect.get("real")
    if real is not None:
        if not (isinstance(real, dict) and isinstance(real.get("dir"), str) and set(real) <= REAL_KEYS):
            out.append("%s: real must be {dir, logfile, omit}" % where)
        elif not (real.get("omit") in (None, "RC_OMIT_KEYS") or isinstance(real.get("omit"), list)):
            out.append("%s: real.omit must be a list of logFile keys or \"RC_OMIT_KEYS\"" % where)
    for e in ENGINE_NAMES:
        if e in expect:
            if not top:
                out.append("%s: a per-engine override cannot hold another (%s)" % (where, e))
            else:
                out += expect_problems(expect[e], "%s %s" % (where, e), top=False)
                over = expect[e]
                if (isinstance(over, dict) and e in (expect.get("engines") or []) and over.get("status") in (2, 3)
                        and expect.get("status") in (0, 1)):
                    for k in ("files", "values"):
                        if expect.get(k) and k not in over:
                            out.append("%s %s: status %d, but the base's %s (a status-%d run's) are merged under it: "
                                       "give %s in the override (files null: not checked), or leave %s out of "
                                       "engines (e2e 5's form)" % (where, e, over["status"], k, expect["status"], k, e))
    if "also" in expect:
        if not top:
            out.append("%s: an override cannot hold also" % where)
        else:
            items = expect["also"] if isinstance(expect["also"], list) else [expect["also"]]
            for k, it in enumerate(items):
                w = "%s also[%d]" % (where, k)
                if not isinstance(it, dict):
                    out.append("%s: an object is expected" % w)
                    continue
                if "also" in it:
                    out.append("%s: also inside also" % w)
                if "argv" not in it and "deck" not in it:
                    out.append("%s: deck or argv is required" % w)
                out += expect_problems({k2: v for k2, v in it.items() if k2 != "also"}, w)
    return out


def run_engines(expect: dict) -> List[str]:
    """The engines a case runs on: those of `engines` and those an override names (vamos's order)."""
    base = expect.get("engines") or []
    return [e for e in ENGINE_NAMES if e in base or isinstance(expect.get(e), dict)]


def expectation_for(expect: dict, engine: str) -> Optional[dict]:
    """The expectation of one engine: the base with the engine's override merged over it, for an
    engine in `engines`; for an engine only an override names, the override with the base's argv,
    env, stdin and gates; None for an engine the case does not run."""
    over = expect.get(engine)
    if engine in (expect.get("engines") or []):
        out = {k: v for k, v in expect.items() if k not in ENGINE_NAMES and k != "also"}
        if isinstance(over, dict):
            out.update(over)
    elif isinstance(over, dict):
        out = {k: expect[k] for k in ("argv", "env", "stdin", "gates", "deck") if k in expect}
        out.update(over)
    else:
        return None
    if "argv" not in out and "deck" in out:
        out["argv"] = [out["deck"]]
    return out


def also_items(expect: dict) -> List[dict]:
    """The second decks of a case: each with its argv ([deck] when it gives a deck only), the main
    expectation's engines unless it gives its own, and the gates of both."""
    also = expect.get("also")
    if also is None:
        return []
    out = []
    for it in (also if isinstance(also, list) else [also]):
        it = dict(it)
        if "argv" not in it and "deck" in it:
            it["argv"] = [it["deck"]]
        it.setdefault("engines", list(expect.get("engines") or []))
        gates = list(expect.get("gates") or []) + [g for g in it.get("gates") or [] if g not in (expect.get("gates") or [])]
        if gates:
            it["gates"] = gates
        out.append(it)
    return out


def value_key(entry: dict) -> str:
    """The identity of a value entry across engines: file, signal, at and row."""
    return json.dumps([entry.get("file"), entry.get("signal"), entry.get("at"), entry.get("row")])


# -- where a run's output goes (§2.5) ----------------------------------------------------------------

_RAW_OPTS = ("-raw", "-r")
_OUTDIR_OPTS = ("-outdir",)
_FORMAT_OPTS = ("-format", "-f")
_LOG_ON = ("+log", "+l", "=log", "=l")
_LOG_OFF = ("-log", "-l")


def defaults_tokens(env: Dict[str, str]) -> List[str]:
    """The defaults layer of a `spectre` run (§2.2): the first of spectre_DEFAULTS and
    SPECTRE_DEFAULTS that exists, shlex-split (an empty one: no defaults)."""
    for var in DEFAULTS_VARS:
        if var in env:
            return shlex.split(env[var])
    return []


def _modify(value: str, mod: str) -> str:
    i = value.rfind("/")
    head, tail = (value[:i + 1], value[i + 1:]) if i >= 0 else ("", value)
    if mod == "r":
        j = tail.rfind(".")
        return head + tail[:j] if j >= 0 else value
    if mod == "e":
        j = tail.rfind(".")
        return tail[j + 1:] if j >= 0 else ""
    if mod == "h":
        return head[:-1] if head else "."
    return tail                                                         # "t"


def expand_percent(text: str, netlist: Optional[str]) -> str:
    """%C (the netlist as given, `stdin` without one) with its colon modifiers :r :e :h :t ::, %S
    (`spectre`) and %% (§2.4) [M UG pp.264-268]; ValueError for any other code."""
    codes = {"C": netlist if netlist is not None else "stdin", "S": "spectre"}
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c != "%" or i + 1 >= n:
            out.append(c)
            i += 1
            continue
        x = text[i + 1]
        if x == "%":
            out.append("%")
            i += 2
            continue
        if not x.isalpha():
            out.append(c)
            i += 1
            continue
        if x not in codes:
            raise ValueError("the T2 harness cannot predict %%%s in %r (give the path without it)" % (x, text))
        v = codes[x]
        i += 2
        while i + 1 < n and text[i] == ":" and text[i + 1] in "reht:":
            mod = text[i + 1]
            i += 2
            if mod == ":":
                v += ":"
                break
            v = _modify(v, mod)
        out.append(v)
    return "".join(out)


class Located:
    """Where a run writes: the netlist as given (None: stdin), the results path (absolute), the
    format, the log file (absolute; None for the screen only) and +escchars."""

    def __init__(self, netlist: Optional[str], results: str, fmt: str, log: Optional[str], escchars: bool) -> None:
        self.netlist, self.results, self.fmt, self.log, self.escchars = netlist, results, fmt, log, escchars

    @property
    def nutmeg(self) -> bool:
        return self.fmt in NUTMEG_FORMATS

    def __repr__(self) -> str:
        return "Located(%r, %r, %r, %r, %r)" % (self.netlist, self.results, self.fmt, self.log, self.escchars)


def locate(argv: Sequence[str], env: Dict[str, str], cwd: str) -> Located:
    """The results path, format and log file of `vamos -spectre <argv>` in cwd (§2.5), with the
    defaults variable of env applied first (argv wins).  The netlist is argv[0] unless that is an
    option (the expect.json convention); every other token is scanned for the options that move
    the output, so a value is never mistaken for one."""
    argv = list(argv)
    netlist = argv[0] if argv and argv[0][:1] not in ("-", "+", "=") else None
    layers = [defaults_tokens(env), argv[1:] if netlist is not None else argv]
    raw = outdir = log = None
    fmt = "psfascii"
    escchars = False
    for toks in layers:
        i = 0
        while i < len(toks):
            t = toks[i]
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if t in _RAW_OPTS + _OUTDIR_OPTS + _FORMAT_OPTS + _LOG_ON and nxt is not None:
                if t in _RAW_OPTS:
                    raw = nxt
                elif t in _OUTDIR_OPTS:
                    outdir = nxt
                elif t in _FORMAT_OPTS:
                    fmt = nxt if nxt in NUTMEG_FORMATS else "psfascii"
                else:
                    log = nxt
                i += 2
                continue
            if t in _LOG_OFF:
                log = None
            elif t == "+escchars":
                escchars = True
            i += 1
    out_dir = expand_percent(outdir, netlist) if outdir else None
    root = _modify(netlist if netlist is not None else "stdin", "r")
    if raw:
        path = expand_percent(raw, netlist)
    else:
        path = root + ".raw"
        if out_dir and "/" not in path:
            path = os.path.join(out_dir, path)
    path = os.path.normpath(os.path.join(cwd, path))
    if fmt in NUTMEG_FORMATS and os.path.isdir(path):
        path = os.path.join(path, _modify(root, "t") + ".raw")           # §8.5: <dir>/%C:r:t.raw
    log_path = None
    if log:
        p = expand_percent(log, netlist)
        if not os.path.isabs(p):
            base = os.path.join(cwd, out_dir) if out_dir and "/" not in p else cwd
            p = os.path.join(base, p)
        log_path = os.path.normpath(p)
    return Located(netlist, path, fmt, log_path, escchars)


# -- the screen and the log (§8.7) -------------------------------------------------------------------

_MSG_HEAD = re.compile(r"^(Error|Warning|Notice) from (\S+) during (.*)\.$")
_MSG_SEVERITY = {"Error": "error", "Warning": "warning", "Notice": "note"}
TRAILER_OK = re.compile(r"^(\S+) completes with (\d+) errors, (\d+) warnings, and (\d+) notices$")
TRAILER_FATAL = re.compile(r"^(\S+) terminated prematurely due to fatal error\.$")


def parse_messages(text: str) -> List[Tuple[str, str]]:
    """The Spectre messages of a screen or log text (§8.7, the 5.1.41 form): (severity, text), the
    severity error, warning or note, the text the indented lines after the header joined by
    newlines."""
    out: List[List[Any]] = []
    cur: Optional[List[Any]] = None
    for line in (text or "").splitlines():
        m = _MSG_HEAD.match(line)
        if m:
            cur = [_MSG_SEVERITY[m.group(1)], []]
            out.append(cur)
        elif cur is not None and line.startswith("    "):
            cur[1].append(line[4:])
        else:
            cur = None
    return [(sev, "\n".join(body)) for sev, body in out]


def _norm(s: str) -> str:
    return " ".join(s.split())


def find_message(messages: Sequence[Tuple[str, str]], severity: str, substring: str) -> bool:
    sev = SEVERITIES.get(severity, severity)
    want = _norm(substring)
    return any(s == sev and want in _norm(body) for s, body in messages)


def trailer(text: str) -> str:
    """The last non-empty line of a screen or log text (the trailer, §8.7)."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


# -- values ------------------------------------------------------------------------------------------

def _interp(xs: Sequence[float], ys: Sequence[Any], x: float) -> Any:
    """ys at x: the value of an exact grid point (relative 1e-12), else linear interpolation along an
    ascending sweep (STRUCT values member by member)."""
    for i, xi in enumerate(xs):
        if xi == x or (x != 0 and abs(xi - x) <= 1e-12 * abs(x)):
            return ys[i]
    if any(b < a for a, b in zip(xs, xs[1:])):
        raise ValueError("%r is not a point of the sweep, which is not ascending" % x)
    if not xs or x < xs[0] or x > xs[-1]:
        raise ValueError("%r is outside the sweep (%s)" % (x, "%r..%r" % (xs[0], xs[-1]) if xs else "no points"))
    for i in range(len(xs) - 1):
        a, b = xs[i], xs[i + 1]
        if a <= x <= b and b > a:
            t = (x - a) / (b - a)
            y0, y1 = ys[i], ys[i + 1]
            if isinstance(y0, dict):
                return {m: y0[m] + (y1[m] - y0[m]) * t for m in y0}
            return y0 + (y1 - y0) * t
    raise ValueError("%r is not inside the sweep" % x)


def read_entry(psf: psfcheck.PsfFile, entry: dict) -> Any:
    """The value a value entry names in a checked PSF file (the expect.json rules above); ValueError
    with the reason when the file does not hold it."""
    sig = entry["signal"]
    at, want = entry.get("at"), entry.get("value")
    if not psf.swept:
        if at is not None:
            raise ValueError("at=%r for an operating-point file" % (at,))
        if "row" in entry and entry["row"] not in (0, -1):
            raise ValueError("row %r of an operating point (it has one)" % (entry["row"],))
        if sig not in psf.values:
            raise ValueError("no value %r (values: %s)" % (sig, ", ".join(psf.values)))
        return psf.values[sig]
    if sig == psf.sweep.name:
        col = psf.sweep_values()
    elif sig in psf.columns:
        col = psf.columns[sig]
    else:
        raise ValueError("no signal %r (sweep %s, traces: %s)" % (sig, psf.sweep.name, ", ".join(psf.names())))
    if "row" in entry:
        try:
            return col[entry["row"]]
        except IndexError:
            raise ValueError("row %d of %d points" % (entry["row"], len(col)))
    if at is not None:
        return _interp(psf.sweep_values(), col, float(at))
    if isinstance(want, list) and (all(isinstance(w, (list, dict)) for w in want)
                                   or (all(_is_num(w) for w in want) and all(_is_num(c) for c in col))):
        return list(col)
    if len(col) == 1:
        return col[0]
    raise ValueError("%d points: give at or row, or a list of the %d values" % (len(col), len(col)))


def compare_value(got: Any, want: Any, rel: float = 0.0, abs_: float = 0.0) -> Optional[str]:
    """None when got matches want (|got - want| <= abs + rel*|want|; a complex [re, im] each part
    against the magnitude; a STRUCT dict member by member; a list element by element), else why."""
    if isinstance(want, dict):
        if not isinstance(got, dict):
            return "a STRUCT {%s} is expected, got %r" % (", ".join(want), got)
        for m, w in want.items():
            if m not in got:
                return "no STRUCT member %r (members: %s)" % (m, ", ".join(got))
            p = compare_value(got[m], w, rel, abs_)
            if p:
                return "member %s: %s" % (m, p)
        return None
    if isinstance(want, list):
        if not isinstance(got, list):
            if len(want) == 2 and all(_is_num(w) for w in want) and (_is_num(got) or isinstance(got, complex)):
                g = complex(got)
                tol = abs_ + rel * math.hypot(want[0], want[1])
                if abs(g.real - want[0]) > tol or abs(g.imag - want[1]) > tol:
                    return "got (%r, %r), want (%r, %r) within %g" % (g.real, g.imag, want[0], want[1], tol)
                return None
            return "a list of %d values is expected, got %r" % (len(want), got)
        if len(got) != len(want):
            return "%d values, want %d: %r" % (len(got), len(want), got[:20])
        for i, (g, w) in enumerate(zip(got, want)):
            p = compare_value(g, w, rel, abs_)
            if p:
                return "[%d]: %s" % (i, p)
        return None
    if _is_num(want):
        if isinstance(got, complex):
            return "a complex value %r: give [re, im]" % (got,)
        if not _is_num(got):
            return "got %r, want the number %r" % (got, want)
        if not math.isfinite(got) or abs(got - want) > abs_ + rel * abs(want):
            return "got %r, want %r (rel %g, abs %g)" % (got, want, rel, abs_)
        return None
    return "the expected value %r is none of a number, [re, im], a list or a STRUCT dict" % (want,)


def cross_compare(a: Any, b: Any, rel: float, abs_: float = 0.0) -> Optional[str]:
    """None when two engines' values agree: |a - b| <= abs + rel*max(|a|, |b|), part by part."""
    if isinstance(a, dict) and isinstance(b, dict):
        for m in a:
            if m in b:
                p = cross_compare(a[m], b[m], rel, abs_)
                if p:
                    return "member %s: %s" % (m, p)
        return None
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return "%d values against %d" % (len(a), len(b))
        for i, (x, y) in enumerate(zip(a, b)):
            p = cross_compare(x, y, rel, abs_)
            if p:
                return "[%d]: %s" % (i, p)
        return None
    try:
        x, y = complex(a), complex(b)
    except (TypeError, ValueError):
        return "%r against %r" % (a, b)
    if abs(x - y) > abs_ + rel * max(abs(x), abs(y)):
        return "%r against %r (rel %g, abs %g)" % (a, b, rel, abs_)
    return None


class ExpectRun:
    """One checked run: its directory, process, Located, the values read (value_key -> value), the
    PSF files checked (name -> psfcheck.PsfFile), the messages and the log text."""

    def __init__(self, d: str, proc: Any, loc: Located) -> None:
        self.dir, self.proc, self.loc = d, proc, loc
        self.values: Dict[str, Any] = {}
        self.files: Dict[str, psfcheck.PsfFile] = {}
        self.messages: List[Tuple[str, str]] = []
        self.log_text = ""


def cross_engine_problems(runs: Dict[str, ExpectRun], expect: dict) -> List[str]:
    """Every value entry read on two or more engines agrees within cross_engine.rel (§11 T2)."""
    rel = (expect.get("cross_engine") or {}).get("rel")
    engines = [e for e in ENGINE_NAMES if e in runs]
    if rel is None or len(engines) < 2:
        return []
    out = []
    first = engines[0]
    for entry in expect.get("values") or []:
        if entry.get("value") is None:
            continue
        key = value_key(entry)
        for other in engines[1:]:
            a, b = runs[first].values.get(key), runs[other].values.get(key)
            if a is None or b is None:
                continue
            p = cross_compare(a, b, float(rel), float(entry.get("abs", 0.0)))
            if p:
                out.append("%s %s%s: %s %s" % (entry["file"], entry["signal"],
                                                "" if entry.get("at") is None else " at %r" % entry["at"],
                                                "%s against %s" % (first, other), p))
    return out


# -- e2e 28's structural comparison with a real Spectre run (moved from test_spectre_outputs, S4) ----

RC_OMIT_KEYS = ("myinfo_Oppoint-info", "myinfo_Models-info", "myinfo_all-info", "dc2-dc", "tran1-tran",
                "mymonte_dc2-montecarlo", "mymonte_tran1-montecarlo") + tuple(
    "mymonte-%03d_%s" % (k, c) for k in (1, 2, 3) for c in ("dc2-dc", "tran1-tran"))   # §11.2 e2e 28: info, montecarlo


def compare_results_dir(got: str, want: str, omit_keys: Sequence[str] = (), header_ignore: Sequence[str] = ("version", "date"),
                        rel: float = 1e-9, want_logfile: Optional[str] = None) -> List[str]:
    """e2e 28's structural assertions (§11.2): the set of files and their names; logFile's keys,
    parents, PROPs and order; each file's header keys and values (except `header_ignore`), TYPE
    list, TRACE names and order, SWEEP variable, units and grid, and description.  Values are not
    compared.  Entries of `want`'s logFile whose keys are in `omit_keys` are left out with their
    files.  `want_logfile` names `want`'s logFile when it is not `<want>/logFile` (psf-parser keeps
    its 23.1 logFile under binary/).  Returns the differences, one line each; [] when the structures
    agree."""
    diffs: List[str] = []
    wlog = psfcheck.check_logfile(want_logfile or os.path.join(want, "logFile"), datadir=want)
    glog = psfcheck.check_logfile(os.path.join(got, "logFile"))
    wentries = [e for e in wlog.entries if e.key not in omit_keys]
    wfiles = sorted(e.data_file for e in wentries)
    gfiles = sorted(e.data_file for e in glog.entries)
    if wfiles != gfiles:
        diffs.append("files: missing %s, extra %s" % (sorted(set(wfiles) - set(gfiles)), sorted(set(gfiles) - set(wfiles))))
    present = sorted(f for f in os.listdir(got) if f != "logFile")
    if present != gfiles:
        diffs.append("logFile and directory disagree: %s vs %s" % (gfiles, present))
    if [e.key for e in wentries] != glog.keys():
        diffs.append("logFile order: want %s, got %s" % ([e.key for e in wentries], glog.keys()))
    for we in wentries:
        ge = glog.by_key.get(we.key)
        if ge is None:
            continue
        for attr in ("analysis_type", "data_file", "parent", "sweep_variable", "description"):
            if getattr(we, attr) != getattr(ge, attr):
                diffs.append("logFile %s %s: want %r, got %r" % (we.key, attr, getattr(we, attr), getattr(ge, attr)))
        if list(we.props) != list(ge.props):
            diffs.append("logFile %s PROP keys: want %s, got %s" % (we.key, list(we.props), list(ge.props)))
        else:
            for k, wv in we.props.items():
                gv = ge.props[k]
                if isinstance(wv, (int, float)) and isinstance(gv, (int, float)):
                    if not math.isclose(float(wv), float(gv), rel_tol=1e-5):
                        diffs.append("logFile %s PROP %s: want %r, got %r" % (we.key, k, wv, gv))
                elif wv != gv:
                    diffs.append("logFile %s PROP %s: want %r, got %r" % (we.key, k, wv, gv))
        if we.data_file not in gfiles:
            continue
        wf = psfcheck.check(os.path.join(want, we.data_file))
        gf = psfcheck.check(os.path.join(got, ge.data_file))
        if list(wf.header) != list(gf.header):
            diffs.append("%s header keys: want %s, got %s" % (we.data_file, list(wf.header), list(gf.header)))
        for k, wv in wf.header.items():
            if k in header_ignore or k not in gf.header:
                continue
            gv = gf.header[k]
            if isinstance(wv, float) and isinstance(gv, float):
                if not math.isclose(wv, gv, rel_tol=rel, abs_tol=0.0):
                    diffs.append("%s header %s: want %r, got %r" % (we.data_file, k, wv, gv))
            elif wv != gv:
                diffs.append("%s header %s: want %r, got %r" % (we.data_file, k, wv, gv))
        wt = [(n, repr(t), t.props) for n, t in wf.types.items()]
        gt = [(n, repr(t), t.props) for n, t in gf.types.items()]
        if wt != gt:
            diffs.append("%s TYPE: want %s, got %s" % (we.data_file, [n for n, _, _ in wt], [n for n, _, _ in gt]))
        if [(t.name, t.type) for t in wf.traces] != [(t.name, t.type) for t in gf.traces]:
            diffs.append("%s TRACE: want %s, got %s" % (we.data_file, [(t.name, t.type) for t in wf.traces],
                                                       [(t.name, t.type) for t in gf.traces]))
        ws = (wf.sweep.name, wf.sweep.props.get("units"), wf.sweep.props.get("grid")) if wf.sweep else None
        gs = (gf.sweep.name, gf.sweep.props.get("units"), gf.sweep.props.get("grid")) if gf.sweep else None
        if ws != gs:
            diffs.append("%s SWEEP: want %s, got %s" % (we.data_file, ws, gs))
        if not wf.swept and list(wf.values) != list(gf.values):
            diffs.append("%s VALUE names: want %s, got %s" % (we.data_file, list(wf.values), list(gf.values)))
    return diffs


def _fixture_path(name: str, *bases: str) -> str:
    """A path an expect.json names: absolute, else under the first of `bases` (the deck directory
    in the fixtures, then its copy) where it exists, else under fixtures/spectre, else under
    tests/vamos/fixtures; the first candidate when none exists."""
    if os.path.isabs(name):
        return name
    cands = [os.path.normpath(os.path.join(b, name)) for b in tuple(b for b in bases if b) + (SPECTRE_FIXTURES, FIXTURES)]
    for c in cands:
        if os.path.exists(c):
            return c
    return cands[0]


def _decode(name: str) -> str:
    """psf-parser returns the escapes raw [E83]: backslash-x is x."""
    return re.sub(r"\\(.)", r"\1", name)


# -- the end-to-end base class -------------------------------------------------------------------------

class SpectreCase(TempDir):
    """A scratch directory per test plus the `vamos -spectre` runner (§11 T2).

    Phase 0 laid down the pieces §11 fixes: the engine loop, the isolated scratch directory and
    PYMS_CACHE, the launcher command and the expect.json loader.  Phase 2 adds the comparison of a
    run with its expect.json (`check_expect`), the psf-parser consumer check (`check_psf_parser`)
    and the whole T2 case (`check_case`: the engines, the cross-engine comparison, the also decks).
    """

    def engines(self) -> List[str]:
        return engines_available()

    def case(self, name: str, files: Dict[str, str]) -> str:
        """Create <tmp>/<name>/ with the given files (relative paths, sub-directories allowed)."""
        d = os.path.join(self.tmp, name)
        for rel, text in files.items():
            p = os.path.join(d, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write(text)
        os.makedirs(d, exist_ok=True)
        return d

    def child_env(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """The run's environment: the shims first on the PATH, no inherited VAMOS_ANALOG, and the
        temporary files and the PyMS cache isolated under this test's scratch directory."""
        env = dict(os.environ)
        env["PATH"] = SHIMS + os.pathsep + env.get("PATH", "")
        env.pop("VAMOS_ANALOG", None)
        for var, sub in (("TMPDIR", "tmp"), ("PYMS_CACHE", "pyms_cache")):
            d = os.path.join(self.tmp, sub)
            os.makedirs(d, exist_ok=True)
            env[var] = d
        if extra:
            env.update(extra)
        return env

    def run_cmd(self, cmd: List[str], cwd: str, env: Optional[Dict[str, str]] = None,
                timeout: int = TIMEOUT, stdin: Optional[str] = None,
                unset: Sequence[str] = ()) -> subprocess.CompletedProcess:
        """Run cmd in cwd under child_env(env) less the variables in `unset`; `stdin` names a file
        fed to the command's standard input (None: inherited, as before phase 2)."""
        e = self.child_env(env)
        for var in unset:
            e.pop(var, None)
        if stdin is None:
            return subprocess.run(cmd, cwd=cwd, env=e, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, universal_newlines=True, errors="replace",
                                  timeout=timeout)
        with open(stdin, "rb") as fh:
            return subprocess.run(cmd, cwd=cwd, env=e, stdin=fh, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, universal_newlines=True, errors="replace",
                                  timeout=timeout)

    def run_spectre(self, d: str, *args: str, engine: Optional[str] = None,
                    expect_rc: Optional[int] = 0, env: Optional[Dict[str, str]] = None,
                    timeout: int = TIMEOUT, stdin: Optional[str] = None,
                    unset: Sequence[str] = ()) -> subprocess.CompletedProcess:
        """Run `vamos -spectre <args>` in d; asserts the exit status unless expect_rc is None."""
        cmd = [LAUNCHER, "-spectre"] + list(args)
        if engine:
            cmd.append("--vamos-analog=" + engine)
        r = self.run_cmd(cmd, d, env, timeout, stdin=stdin, unset=unset)
        if expect_rc is not None:
            self.assertEqual(r.returncode, expect_rc, "%s\n%s" % (" ".join(cmd), r.stdout))
        return r

    @staticmethod
    def load_expect(path: str) -> dict:
        """An expect.json: {argv, engines, status, files, values: [{file, signal, at, value, rel,
        abs}], cross_engine: {rel}, messages: [[severity, substring]]} (§11); the module docstring
        lists every key."""
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    # -- phase 2: the T2 tier (§11.2) ---------------------------------------------------------------

    def copy_case(self, case_dir: str, name: str) -> str:
        """A copy of a deck directory under this test's scratch directory, <tmp>/<name>/."""
        d = os.path.join(self.tmp, name)
        shutil.copytree(case_dir, d)
        return d

    def case_env(self, exp: dict) -> Tuple[Dict[str, str], List[str]]:
        """The extra environment of an expectation and the variables a run must not inherit."""
        extra = dict(exp.get("env") or {})
        return extra, [v for v in UNSET_VARS if v not in extra]

    def run_expect(self, d: str, expect: dict, engine: str, timeout: int = TIMEOUT) -> subprocess.CompletedProcess:
        """`vamos -spectre <argv> --vamos-analog=<engine>` in d for the engine's expectation: its env,
        its stdin file (else /dev/null)."""
        exp = expectation_for(expect, engine)
        if exp is None:
            self.fail("the expectation does not run on %s" % engine)
        extra, unset = self.case_env(exp)
        stdin = os.path.join(d, exp["stdin"]) if exp.get("stdin") else os.devnull
        return self.run_spectre(d, *exp["argv"], engine=engine, expect_rc=None, env=extra, timeout=timeout,
                                stdin=stdin, unset=unset)

    def locate(self, d: str, exp: dict) -> Located:
        extra, unset = self.case_env(exp)
        env = dict(os.environ)
        for v in unset:
            env.pop(v, None)
        env.update(extra)
        return locate(exp["argv"], env, d)

    def check_expect(self, d: str, expect: dict, engine: str, proc: Any = None,
                     case_dir: Optional[str] = None) -> ExpectRun:
        """Check one run in the deck directory copy d against the engine's expectation (the module
        docstring's keys): the exit status, the messages, the results directory's exact file set,
        psfcheck over every PSF file and the logFile against the data files, the values, and `real`
        (its relative paths against case_dir, the deck directory d was copied from, then d).
        With proc None the run is made here (run_expect); proc is anything with returncode and
        stdout (the synthetic tests pass their own).  Fails with every problem found at once;
        returns the ExpectRun."""
        bad = expect_problems(expect)
        if bad:
            self.fail("expect.json is malformed:\n  " + "\n  ".join(bad))
        exp = expectation_for(expect, engine)
        if exp is None:
            self.fail("the expectation does not run on %s" % engine)
        if proc is None:
            proc = self.run_expect(d, expect, engine)
        loc = self.locate(d, exp)
        r = ExpectRun(d, proc, loc)
        problems: List[str] = []
        if proc.returncode != exp.get("status"):
            problems.append("exit status %r, expected %r" % (proc.returncode, exp.get("status")))
        screen = proc.stdout or ""
        if loc.log and os.path.isfile(loc.log):
            with open(loc.log, encoding="utf-8", errors="replace") as fh:
                r.log_text = fh.read()
        r.messages = parse_messages(screen) + parse_messages(r.log_text)
        for sev, sub in exp.get("messages") or []:
            if not find_message(r.messages, sev, sub):
                problems.append("no %s message holding %r (messages: %s)" % (
                    SEVERITIES.get(sev, sev), sub, "; ".join("%s: %s" % (s, _norm(b)[:120]) for s, b in r.messages)
                    or "none"))
        problems += self._file_problems(exp, loc)
        if not loc.nutmeg:
            r.files = self._psf_files(loc, problems)
        elif exp.get("values"):
            problems.append("values are read from PSF files; %s writes %s (check it in a deck test)"
                            % (loc.fmt, loc.results))
        for k, entry in enumerate(exp.get("values") or []):
            where = "values[%d] %s %s" % (k, entry.get("file"), entry.get("signal"))
            psf = r.files.get(entry["file"])
            if psf is None:
                problems.append("%s: no PSF file %s in %s" % (where, entry["file"], loc.results))
                continue
            why, got = self._value_problem(psf, entry)
            if why:
                problems.append("%s: %s" % (where, why))
            elif entry.get("value") is not None:
                r.values[value_key(entry)] = got
        real = exp.get("real")
        if real:
            problems += self._real_problems(real, loc, case_dir, d)
        if problems:
            self.fail(self._report(engine, exp, proc, problems))
        return r

    @staticmethod
    def _value_problem(psf: psfcheck.PsfFile, entry: dict) -> Tuple[Optional[str], Any]:
        """(None, the value read) when the entry holds, else (the reason, None); an entry whose
        value is null checks that the signal is absent."""
        want = entry.get("value")
        if want is None:
            sig = entry["signal"]
            present = ((sig in psf.columns or (psf.sweep is not None and sig == psf.sweep.name)) if psf.swept
                       else sig in psf.values)
            return ("%s is in the file, expected absent" % sig if present else None), None
        if "points" in entry:
            n = psf.points if psf.swept else 1
            if n != entry["points"]:
                return "%d points, expected %d" % (n, entry["points"]), None
        try:
            got = read_entry(psf, entry)
        except ValueError as e:
            return str(e), None
        why = compare_value(got, want, float(entry.get("rel", 0.0)), float(entry.get("abs", 0.0)))
        return (why, None) if why else (None, got)

    @staticmethod
    def _file_problems(exp: dict, loc: Located) -> List[str]:
        files = exp.get("files")
        if files is None:
            return []
        want = sorted(files)
        res = loc.results
        if loc.nutmeg:
            out = []
            if os.path.isdir(res):
                return ["the %s results path %s is a directory" % (loc.fmt, res)]
            got = [os.path.basename(res)] if os.path.isfile(res) else []
            if got != want:
                out.append("results file %s: got %s, expected %s" % (res, got, want))
            beside = os.path.join(os.path.dirname(res), "logFile")
            if os.path.exists(beside):
                out.append("a %s run wrote %s (§8.5: no logFile)" % (loc.fmt, beside))
            return out
        if os.path.isfile(res):
            return ["the results path %s is a file, a psf results directory is expected" % res]
        got = sorted(os.listdir(res)) if os.path.isdir(res) else []
        if got != want:
            return ["results directory %s: missing %s, unexpected %s" % (
                res, sorted(set(want) - set(got)), sorted(set(got) - set(want)))]
        return []

    @staticmethod
    def _psf_files(loc: Located, problems: List[str]) -> Dict[str, psfcheck.PsfFile]:
        """psfcheck over every PSF data file and the logFile of the results directory; the logFile
        must list exactly the data files the directory holds (§8.2)."""
        res = loc.results
        if not os.path.isdir(res):
            return {}
        names = sorted(os.listdir(res))
        data = [n for n in names if is_psf_data(n)]
        out: Dict[str, psfcheck.PsfFile] = {}
        for n in data:
            try:
                out[n] = psfcheck.check(os.path.join(res, n), escchars=loc.escchars)
            except (psfcheck.PsfError, OSError, KeyError, IndexError, ValueError) as e:
                problems.append("psfcheck rejects %s: %s" % (n, e))
        if "logFile" in names:
            try:
                log = psfcheck.check_logfile(os.path.join(res, "logFile"), escchars=loc.escchars)
            except (psfcheck.PsfError, OSError, KeyError, IndexError, ValueError) as e:
                problems.append("psfcheck rejects logFile: %s" % e)
            else:
                listed = sorted(e.data_file for e in log.entries)
                if listed != data:
                    problems.append("logFile lists %s, the directory holds the data files %s" % (listed, data))
        elif data:
            problems.append("data files %s but no logFile" % data)
        return out

    @staticmethod
    def _real_problems(real: dict, loc: Located, *bases: Optional[str]) -> List[str]:
        want = _fixture_path(real["dir"], *[b for b in bases if b])
        want_log = _fixture_path(real["logfile"], *[b for b in bases if b]) if real.get("logfile") else None
        omit = real.get("omit")
        omit_keys = RC_OMIT_KEYS if omit == "RC_OMIT_KEYS" else tuple(omit or ())
        where = "real (%s)" % real["dir"]
        if not os.path.isdir(want):
            return ["%s: no such directory under the deck directory or the fixtures (%s)" % (where, want)]
        try:
            diffs = compare_results_dir(loc.results, want, omit_keys=omit_keys, want_logfile=want_log)
        except (psfcheck.PsfError, OSError) as e:
            return ["%s: %s" % (where, e)]
        return ["%s: %s" % (where, x) for x in diffs]

    @staticmethod
    def _report(engine: str, exp: dict, proc: Any, problems: List[str]) -> str:
        out = (proc.stdout or "").splitlines()
        tail = out[-60:]
        return "vamos -spectre %s on %s:\n  - %s\n--- output%s ---\n%s" % (
            " ".join(exp.get("argv") or []), engine, "\n  - ".join(problems),
            " (last %d of %d lines)" % (len(tail), len(out)) if len(out) > len(tail) else "", "\n".join(tail))

    def check_psf_parser(self, results: str, escchars: bool = False) -> None:
        """The vendored psf-parser parses every PSF file of a results directory (§11 T2) and finds
        the traces (decoded, [E83]) and point counts psfcheck finds; needs_psf_parser."""
        pp = import_psf_parser()
        problems = []
        for n in sorted(os.listdir(results)):
            if n != "logFile" and not is_psf_data(n):
                continue
            path = os.path.join(results, n)
            try:
                parsed = pp.PsfFile(path)
            except Exception as e:  # noqa: BLE001  (a third-party reader: any failure is the finding)
                problems.append("%s: psf-parser: %s: %s" % (n, type(e).__name__, e))
                continue
            if n == "logFile":
                want = len(psfcheck.check_logfile(path, escchars=escchars).entries)
                if len(parsed.values) != want:
                    problems.append("logFile: psf-parser reads %d entries, psfcheck %d" % (len(parsed.values), want))
                continue
            ours = psfcheck.check(path, escchars=escchars)
            if ours.swept:
                theirs = {_decode(t.name): t for t in parsed.traces}
                for t in ours.traces:
                    if t.name not in theirs:
                        problems.append("%s: psf-parser has no trace %r (it has %s)" % (n, t.name, sorted(theirs)))
                    elif len(theirs[t.name].data) != ours.points:
                        problems.append("%s: psf-parser reads %d points of %r, psfcheck %d"
                                        % (n, len(theirs[t.name].data), t.name, ours.points))
            else:
                theirs = set(_decode(v.name) for v in parsed.values)
                missing = [k for k in ours.values if k not in theirs]
                if missing:
                    problems.append("%s: psf-parser has no value %s" % (n, missing))
        if problems:
            self.fail("psf-parser on %s:\n  - %s" % (results, "\n  - ".join(problems)))

    def check_case(self, case_dir: str, expect: Optional[dict] = None) -> Dict[str, Dict[str, ExpectRun]]:
        """The T2 check of one deck directory (§11.2): the expect.json's form, its gates, then per
        engine (a subTest each) a copy of the directory, the run and check_expect, the psf-parser
        check (its own subTest, a reported skip without psf-parser), the cross-engine comparison,
        and the same for every `also` deck.  Returns {"main"|"also<k>": {engine: ExpectRun}}."""
        if expect is None:
            expect = self.load_expect(os.path.join(case_dir, "expect.json"))
        bad = expect_problems(expect, os.path.join(os.path.basename(case_dir), "expect.json"))
        if bad:
            self.fail("expect.json is malformed:\n  " + "\n  ".join(bad))
        why = gate_problems(expect.get("gates") or ())
        if why:
            self.skipTest("; ".join(why))
        if not have_flow():
            self.skipTest(FLOW_REASON)
        engines = run_engines(expect) + [e for it in also_items(expect) for e in run_engines(it)]
        if not any(GATES[ENGINE_GATES[e]][0]() for e in engines):
            self.skipTest("; ".join(sorted(set(GATES[ENGINE_GATES[e]][1] for e in engines)))
                          or "the expect.json names no engine")
        out = {"main": self._check_deck(case_dir, expect, "main")}
        for k, item in enumerate(also_items(expect), 1):
            out["also%d" % k] = self._check_deck(case_dir, item, "also%d" % k)
        return out

    def _check_deck(self, case_dir: str, expect: dict, label: str) -> Dict[str, ExpectRun]:
        runs: Dict[str, ExpectRun] = {}
        name = os.path.basename(os.path.normpath(case_dir))
        why = gate_problems(expect.get("gates") or ())
        for engine in run_engines(expect):
            r: Optional[ExpectRun] = None
            with self.subTest(deck=label, engine=engine):
                pred, reason = GATES[ENGINE_GATES[engine]]
                if not pred():
                    self.skipTest(reason)
                if why:
                    self.skipTest("; ".join(why))
                d = self.copy_case(case_dir, "%s_%s_%s" % (name, label, engine))
                proc = self.run_expect(d, expect, engine)
                r = self.check_expect(d, expect, engine, proc=proc, case_dir=case_dir)
                if engine in (expect.get("engines") or []):
                    runs[engine] = r
            if r is not None and not r.loc.nutmeg and os.path.isdir(r.loc.results):
                with self.subTest(deck=label, engine=engine, check="psf-parser"):
                    if not have_psf_parser():
                        self.skipTest(GATES["needs_psf_parser"][1])
                    self.check_psf_parser(r.loc.results, r.loc.escchars)
        if (expect.get("cross_engine") or {}).get("rel") is not None and len(runs) > 1:
            with self.subTest(deck=label, check="cross_engine"):
                problems = cross_engine_problems(runs, expect)
                if problems:
                    self.fail("%s %s: the engines disagree:\n  - %s" % (name, label, "\n  - ".join(problems)))
        return runs

    def copy_fixture(self, d: str, *parts: str) -> str:
        """Copy a spectre fixture file into the case directory d; returns the copy's path."""
        src = spectre_fixture(*parts)
        dst = os.path.join(d, os.path.basename(src))
        shutil.copyfile(src, dst)
        return dst
