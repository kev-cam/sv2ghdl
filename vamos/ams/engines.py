"""Where the analog engines live (docs/VAMOS_AMS_DESIGN.md §6).

Shared by the compile step (OpenVAF compiles, the deck smoke check) and the
run step (backends/cosim.py).  Every location can be overridden from the
environment; nothing here loads a library into the vamos process.

An override that is set is used as given and never replaced by a default: a
VAMOS_OPENVAF or VAMOS_XYCE that names no executable is not "not set".
problems(engine) lists every override of that engine's tools that names
nothing usable ("VAMOS_X=<value> is not an executable", "... is not a
directory"); the AMS compile checks it right after choosing the engine
(ams/flow.py step 2) and stops with those messages, so no later step meets a
bad override.

choose_engine(opts, configured=None) picks the engine (--vamos-analog > VAMOS_ANALOG
> configured > vacask; ValueError for a value that names no engine), env_for(engine,
nvc_libdir=None, base=None) gives the environment a run needs (no bridge directory
without an nvc library directory), and tool_rows(engine, xyce=None) the provenance
header's engine rows; ams/flow.py wraps the first and builds compile_tools on the
third, and the spectre personality calls all three (docs/VAMOS_SPECTRE_DESIGN.md §1,
§10).
"""

from __future__ import annotations

import glob
import os
from typing import Dict, List, Optional, Tuple

ENGINES = ("vacask", "xyce")
XYCE_DEFAULT = "/usr/local/src/xyce-build/src/Xyce"


def _is_exe(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def vacask_home() -> str:
    return os.environ.get("VAMOS_VACASK_HOME", "/opt/build.VACASK/Release")


def vacask_bin() -> str:
    """The standalone VACASK simulator (smoke check)."""
    return os.environ.get("VAMOS_VACASK", os.path.join(vacask_home(), "simulator", "vacask"))


def vacask_cinterface() -> str:
    return os.path.join(vacask_home(), "cinterface", "libvacaskcinterface.so")


def vacask_module_path() -> Optional[str]:
    """SIM_MODULE_PATH for VACASK.  Always explicit: under nvc the C interface
    would resolve its built-in default relative to the nvc executable."""
    env = os.environ.get("VAMOS_VACASK_MODULE_PATH")
    if env:
        return env
    for cand in (os.path.join(vacask_home(), "lib", "vacask", "mod"),
                 os.path.join(vacask_home(), "devices")):
        if os.path.isdir(cand):
            return cand
    return None


def openvaf() -> Optional[str]:
    """openvaf-r: VAMOS_OPENVAF as given when it is set (problems() reports one that
    is not an executable), else the newest /opt/openvaf-r-*/openvaf-r, else
    <VACASK home>/simulator/openvaf-r; None when none exists."""
    env = os.environ.get("VAMOS_OPENVAF")
    if env:
        return env
    cands = sorted(glob.glob("/opt/openvaf-r-*/openvaf-r"))
    if cands:
        return cands[-1]
    alt = os.path.join(vacask_home(), "simulator", "openvaf-r")
    return alt if os.path.isfile(alt) else None


def xyce_libs() -> List[str]:
    env = os.environ.get("VAMOS_XYCE_LIBS")
    if env:
        return [d for d in env.split(os.pathsep) if d]
    return [os.path.expanduser("~/xyce-libs"), "/usr/local/src/xyce-build/utils/XyceCInterface",
            "/usr/local/src/xyce-build/src"]


def xyce_bin() -> Optional[str]:
    """The standalone Xyce binary (smoke check): VAMOS_XYCE as given when it is set
    (never replaced by the default; problems() reports one that is not an
    executable), else XYCE_DEFAULT when it is executable, else None."""
    env = os.environ.get("VAMOS_XYCE")
    if env:
        return env
    return XYCE_DEFAULT if os.access(XYCE_DEFAULT, os.X_OK) else None


def xyce_cinterface() -> Optional[str]:
    for d in xyce_libs():
        p = os.path.join(d, "libxycecinterface.so")
        if os.path.isfile(p):
            return p
    return None


def problems(engine: str) -> List[str]:
    """Every VAMOS_* override of `engine`'s tools that names nothing usable, in a
    fixed order: "<VAR>=<value> is not an executable" for VAMOS_VACASK and
    VAMOS_OPENVAF (VACASK) or VAMOS_XYCE (Xyce); "<VAR>=<value> is not a
    directory" for VAMOS_VACASK_HOME and VAMOS_VACASK_MODULE_PATH, and
    "VAMOS_XYCE_LIBS=<value>: <dir> is not a directory" for each entry of
    VAMOS_XYCE_LIBS.  Unset variables are never problems (their defaults are
    looked up, and reported if missing, where they are used)."""
    out: List[str] = []

    def exe(var: str) -> None:
        val = os.environ.get(var)
        if val and not _is_exe(val):
            out.append("%s=%s is not an executable" % (var, val))

    def directory(var: str) -> None:
        val = os.environ.get(var)
        if val and not os.path.isdir(val):
            out.append("%s=%s is not a directory" % (var, val))

    if engine == "vacask":
        directory("VAMOS_VACASK_HOME")
        exe("VAMOS_VACASK")
        directory("VAMOS_VACASK_MODULE_PATH")
        exe("VAMOS_OPENVAF")
    elif engine == "xyce":
        exe("VAMOS_XYCE")
        libs = os.environ.get("VAMOS_XYCE_LIBS")
        if libs:
            for d in libs.split(os.pathsep):
                if d and not os.path.isdir(d):
                    out.append("VAMOS_XYCE_LIBS=%s: %s is not a directory" % (libs, d))
    else:
        raise ValueError("unknown analog engine %r" % (engine,))
    return out


def bridge_lib(nvc_libdir: str) -> str:
    """libcosim_bridge.so lives in <nvc prefix>/lib, next to the nvc library directory."""
    for d in (nvc_libdir, os.path.dirname(nvc_libdir)):
        p = os.path.join(d, "libcosim_bridge.so")
        if os.path.isfile(p):
            return p
    return os.path.join(os.path.dirname(nvc_libdir), "libcosim_bridge.so")


def env_for(engine: str, nvc_libdir: Optional[str] = None,
            base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Environment additions for running an engine (standalone or under nvc): a copy of
    `base` with the engine's library directories first on LD_LIBRARY_PATH and, for VACASK,
    SIM_MODULE_PATH and SIM_OPENVAF.  nvc_libdir: nvc's library directory, whose bridge
    library's directory comes first (a run under nvc); None puts no bridge directory on the
    path (a standalone engine run; a dummy such as '/nonexistent' would put '/' first)."""
    env = dict(base or {})
    dirs = [os.path.dirname(bridge_lib(nvc_libdir))] if nvc_libdir is not None else []
    if engine == "vacask":
        dirs.append(os.path.dirname(vacask_cinterface()))
        mp = vacask_module_path()
        if mp:
            env["SIM_MODULE_PATH"] = mp
        ov = openvaf()
        if ov:
            env["SIM_OPENVAF"] = ov
    elif engine == "xyce":
        dirs += xyce_libs()
    else:
        raise ValueError("unknown analog engine %r" % (engine,))
    old = os.environ.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = os.pathsep.join(dirs + ([old] if old else []))
    return env


def choose_engine(opts: dict, configured: Optional[str] = None) -> str:
    """The analog engine: --vamos-analog (opts["analog"]) > VAMOS_ANALOG > `configured` (a
    control file's choose, when it names vacask or xyce) > vacask.  ValueError, with
    vcs-ams's text, for an option or variable value that names no engine; ams/flow.choose_engine
    wraps it (AmsError)."""
    for src, val in (("--vamos-analog", opts.get("analog")),
                     ("VAMOS_ANALOG", os.environ.get("VAMOS_ANALOG"))):
        if val and val is not True:
            if val.lower() not in ENGINES:
                raise ValueError("%s=%s: the analog engine must be vacask or xyce" % (src, val))
            return val.lower()
    if configured and configured.lower() in ENGINES:
        return configured.lower()
    return "vacask"


def tool_rows(engine: str, xyce: Optional[str] = None) -> List[Tuple[str, str]]:
    """The provenance header's engine rows, [(tool, path)]: [("VACASK", vacask_bin()),
    ("OpenVAF-r", openvaf())] (the second only when openvaf() finds one), or [("Xyce", xyce)],
    xyce being the path the caller runs; None stands for xyce_bin() or "Xyce", which is what
    ams/flow.compile_tools passes."""
    if engine == "vacask":
        out = [("VACASK", vacask_bin())]
        ov = openvaf()
        if ov:
            out.append(("OpenVAF-r", ov))
        return out
    if engine == "xyce":
        return [("Xyce", xyce if xyce is not None else (xyce_bin() or "Xyce"))]
    raise ValueError("unknown analog engine %r" % (engine,))
