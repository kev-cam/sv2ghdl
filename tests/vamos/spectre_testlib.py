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

    class TestRc(SpectreCase):
        def test_rc(self):
            for engine in self.engines():
                with self.subTest(engine=engine):
                    d = self.case("rc_" + engine, {"rc.scs": DECK})
                    r = self.run_spectre(d, "rc.scs", engine=engine)
                    psf = psfcheck.check(os.path.join(d, "rc.raw", "tr.tran.tran"))
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from typing import Dict, List, Optional

from vamos_testlib import (FIXTURES, LAUNCHER, LINUX, ROOT, TempDir, fixture, have_vacask,  # noqa: F401
                           have_xyce, needs_vacask, needs_xyce, reap, run, vacask_bin, xyce_bin,
                           xyce_env)
from vamos import tools  # noqa: E402

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


# -- the end-to-end base class -------------------------------------------------------------------------

class SpectreCase(TempDir):
    """A scratch directory per test plus the `vamos -spectre` runner (§11 T2).

    Phase 0 lays down the pieces §11 fixes: the engine loop, the isolated scratch directory and
    PYMS_CACHE, the launcher command and the expect.json loader.  The comparison of a run with its
    expect.json (`check_expect`) is phase 2's (§12) and raises NotImplementedError until then.
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
                timeout: int = TIMEOUT) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, cwd=cwd, env=self.child_env(env), stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, universal_newlines=True, errors="replace",
                              timeout=timeout)

    def run_spectre(self, d: str, *args: str, engine: Optional[str] = None,
                    expect_rc: Optional[int] = 0, env: Optional[Dict[str, str]] = None,
                    timeout: int = TIMEOUT) -> subprocess.CompletedProcess:
        """Run `vamos -spectre <args>` in d; asserts the exit status unless expect_rc is None."""
        cmd = [LAUNCHER, "-spectre"] + list(args)
        if engine:
            cmd.append("--vamos-analog=" + engine)
        r = self.run_cmd(cmd, d, env, timeout)
        if expect_rc is not None:
            self.assertEqual(r.returncode, expect_rc, "%s\n%s" % (" ".join(cmd), r.stdout))
        return r

    @staticmethod
    def load_expect(path: str) -> dict:
        """An expect.json: {argv, engines, status, files, values: [{file, signal, at, value, rel,
        abs}], cross_engine: {rel}, messages: [[severity, substring]]} (§11)."""
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def check_expect(self, d: str, expect: dict, engine: str) -> None:
        raise NotImplementedError("SpectreCase.check_expect is phase 2's (VAMOS_SPECTRE_DESIGN.md §12)")

    def copy_fixture(self, d: str, *parts: str) -> str:
        """Copy a spectre fixture file into the case directory d; returns the copy's path."""
        src = spectre_fixture(*parts)
        dst = os.path.join(d, os.path.basename(src))
        shutil.copyfile(src, dst)
        return dst
