"""Shared helpers for the vamos tests (standard library only).

Each test module imports from here instead of from another test module, so
agents and suites can work on separate files.  Fixtures live in
tests/vamos/fixtures/{netlist,vhdl,ams}.
"""

import glob
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from vamos import tools  # noqa: E402

LAUNCHER = os.path.join(ROOT, "bin", "vamos")
FIXTURES = os.path.join(ROOT, "tests", "vamos", "fixtures")

LINUX = sys.platform.startswith("linux")


def fixture(*parts: str) -> str:
    return os.path.join(FIXTURES, *parts)


class TempDir(unittest.TestCase):
    """A scratch directory per test, with the environment and tool caches restored."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vamos-test-")
        self._env = dict(os.environ)
        tools._scrub_cache.clear()
        tools._real_cache.clear()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        reap(self.tmp)
        if os.environ.get("VAMOS_TEST_KEEP"):
            sys.stderr.write("kept %s\n" % self.tmp)
        else:
            shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, text):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)
        return p

    def read(self, rel):
        with open(os.path.join(self.tmp, rel), errors="replace") as fh:
            return fh.read()


def procs_under(directory: str) -> list:
    """Processes whose working directory is `directory` or below it (from /proc; [] where
    there is none).  This process is never one of them."""
    if not os.path.isdir("/proc"):
        return []
    root = os.path.realpath(directory)
    found = []
    for p in glob.glob("/proc/[0-9]*"):
        try:
            cwd = os.readlink(os.path.join(p, "cwd"))
            pid = int(os.path.basename(p))
        except (OSError, ValueError):
            continue
        if cwd.endswith(" (deleted)"):
            cwd = cwd[:-len(" (deleted)")]
        if pid != os.getpid() and (cwd == root or cwd.startswith(root + os.sep)):
            found.append(pid)
    return found


def reap(directory: str) -> list:
    """Kill every process still running in a test's scratch directory, and say so.

    A test that fails part-way (a subtest that never collects its ./simv) would otherwise
    leave the run behind: simv, and nvc in its own process group, which a busy co-simulation
    keeps running for the deck's whole stop time.  Returns the pids killed."""
    import signal
    pids = procs_under(directory)
    for pid in pids:
        try:
            with open("/proc/%d/cmdline" % pid, "rb") as fh:
                cmd = fh.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except OSError:
            cmd = "?"
        try:
            os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        except OSError:
            continue
        sys.stderr.write("vamos tests: killed process %d left running in %s: %s\n"
                         % (pid, directory, cmd[:200]))
    return pids


# -- what is installed (the stack is Linux ELF: WSL here) -----------------------

def have_stack() -> bool:
    """nvc + iverilog, the digital stack."""
    if not LINUX:
        return False
    os.environ.setdefault("VAMOS_LAUNCHER", os.path.realpath(LAUNCHER))
    return bool(tools.find_real("nvc")) and bool(tools.find_real("iverilog"))


def vacask_home() -> str:
    return os.environ.get("VAMOS_VACASK_HOME", "/opt/build.VACASK/Release")


def vacask_bin() -> str:
    return os.path.join(vacask_home(), "simulator", "vacask")


def openvaf_bin() -> str:
    env = os.environ.get("VAMOS_OPENVAF")
    if env:
        return env
    cands = sorted(glob.glob("/opt/openvaf-r-*/openvaf-r"))
    return cands[-1] if cands else ""


def have_vacask() -> bool:
    return LINUX and os.access(vacask_bin(), os.X_OK)


def xyce_bin() -> str:
    for c in (os.environ.get("VAMOS_XYCE", ""), "/usr/local/src/xyce-build/src/Xyce"):
        if c and os.access(c, os.X_OK):
            return c
    return ""


def have_xyce() -> bool:
    return LINUX and bool(xyce_bin())


def xyce_env() -> dict:
    env = dict(os.environ)
    libs = os.environ.get("VAMOS_XYCE_LIBS", os.path.expanduser("~/xyce-libs") +
                          ":/usr/local/src/xyce-build/utils/XyceCInterface:/usr/local/src/xyce-build/src")
    env["LD_LIBRARY_PATH"] = libs + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    return env


def run(cmd, cwd=None, env=None, timeout=600):
    """Run a command, return CompletedProcess with merged text output."""
    return subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          universal_newlines=True, errors="replace", timeout=timeout)


needs_stack = unittest.skipUnless(have_stack(), "needs nvc + iverilog (Linux/WSL)")
needs_vacask = unittest.skipUnless(have_vacask(), "needs VACASK (Linux/WSL)")
needs_xyce = unittest.skipUnless(have_xyce(), "needs Xyce (Linux/WSL)")
