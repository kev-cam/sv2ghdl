"""Finding and running the real tools: PATH scrubbing and re-entry lock-out.

PATH scrubbing (docs/VAMOS_PLAN.md 4a): any process vamos starts gets a PATH
without vamos shim directories, so a real tool (or its own sub-calls) cannot
land back in vamos.  find_real() never returns vamos itself.

Lock-out: vamos exports VAMOS_STACK (the personalities currently active,
outermost first).  Entering a personality that is already on the stack means a
real tool called its own name and hit a shim; vamos then execs the real tool
instead of dispatching again.  More than MAX_DEPTH nested vamos entries is a
hard error that shows the chain.
"""

import os
import re
import shutil
import subprocess
from typing import Dict, List, Optional, Tuple

MAX_DEPTH = 4

# Where sibling tools are in a source checkout (dev mode) - last resort only.
DEV_TOOL_PATHS = {
    "nvc": ["/usr/local/src/nvc-build/bin/nvc"],
    "iverilog": ["/usr/local/src/iverilog/_install/bin/iverilog"],
}


def launcher() -> str:
    """Real path of bin/vamos, as recorded by the launcher script."""
    env = os.environ.get("VAMOS_LAUNCHER")
    if env:
        return env
    return os.path.join(package_root(), "bin", "vamos")


def package_root() -> str:
    """sv2ghdl checkout (dev) or PREFIX/lib (installed)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bindir() -> str:
    return os.path.dirname(launcher())


def dev_mode() -> bool:
    if os.environ.get("VAMOS_DEV") == "1":
        return True
    root = package_root()
    return os.path.exists(os.path.join(root, "sv2ghdl.pl"))


def is_vamos(path: str) -> bool:
    """True if path is (a link to) a vamos launcher."""
    try:
        real = os.path.realpath(path)
    except OSError:
        return False
    return real == os.path.realpath(launcher()) or os.path.basename(real) == "vamos"


# Names a vamos shim can have.  Shim detection probes only these, never lists
# a directory: WSL puts many /mnt/c directories with thousands of files on
# PATH, and every stat there crosses the slow Windows file bridge.
SHIM_NAMES = ("vcs", "vlogan", "vhdlan", "vcs-ams", "xrun", "irun", "ncverilog",
              "vlog", "vcom", "vsim", "vlib", "vmap", "nvc", "ghdl", "iverilog",
              "vvp", "verilator")


def _is_shim_dir(d: str, redirect: List[str]) -> bool:
    """A directory of vamos shims: holds links to vamos but not vamos itself.

    A mixed directory such as PREFIX/bin (vamos plus real tools) is kept; a
    re-entry through it is caught by the lock-out instead.
    """
    if not d:
        return False
    if os.path.realpath(d) in redirect:
        return True
    v = os.path.join(d, "vamos")
    if os.path.lexists(v) and not os.path.islink(v):
        return False              # the launcher's own dir
    for n in SHIM_NAMES:
        p = os.path.join(d, n)
        if os.path.islink(p) and is_vamos(p):
            return True
    return False


def _redirect_dirs() -> List[str]:
    val = os.environ.get("VAMOS_REDIRECT", "")
    return [os.path.realpath(d) for d in val.split(os.pathsep) if d]


_scrub_cache: Dict[Tuple[str, str], str] = {}


def scrubbed_path(path: Optional[str] = None) -> str:
    path = os.environ.get("PATH", "") if path is None else path
    key = (path, os.environ.get("VAMOS_REDIRECT", ""))
    if key not in _scrub_cache:
        redirect = _redirect_dirs()
        keep = [d for d in path.split(os.pathsep) if not _is_shim_dir(d, redirect)]
        _scrub_cache[key] = os.pathsep.join(keep)
    return _scrub_cache[key]


def find_real(name: str) -> Optional[str]:
    """Locate the real `name`, never a vamos shim.

    Order: $VAMOS_<NAME> override, vamos's own bin dir, scrubbed PATH,
    dev-mode build areas.
    """
    override = os.environ.get("VAMOS_" + re.sub(r"\W", "_", name).upper())
    if override:
        return override
    key = (name, os.environ.get("PATH", ""))
    if key not in _real_cache:
        _real_cache[key] = _search_real(name)
    return _real_cache[key]


_real_cache: Dict[Tuple[str, str], Optional[str]] = {}


def _search_real(name: str) -> Optional[str]:
    cands = [os.path.join(bindir(), name)]
    for d in scrubbed_path().split(os.pathsep):
        if d:
            cands.append(os.path.join(d, name))
    if dev_mode():
        cands.extend(DEV_TOOL_PATHS.get(name, []))
    for c in cands:
        if os.path.isfile(c) and os.access(c, os.X_OK) and not is_vamos(c):
            return c
    return None


def stack() -> List[str]:
    return [p for p in os.environ.get("VAMOS_STACK", "").split(",") if p]


# The personality this process is running as (set by cli.main).
current = "vamos"


def child_env(extra: Optional[Dict[str, str]] = None,
              personality: Optional[str] = None) -> Dict[str, str]:
    """Environment for any process vamos starts: scrubbed PATH + lock-out stack."""
    env = dict(os.environ)
    env["PATH"] = scrubbed_path()
    env["VAMOS_STACK"] = ",".join(stack() + [personality or current])
    env["VAMOS_DEPTH"] = str(len(stack()) + 1)
    for k in ("VAMOS_ARGV0",):
        env.pop(k, None)
    if extra:
        env.update(extra)
    return env


class LockoutError(Exception):
    pass


def check_lockout(personality: str) -> Optional[str]:
    """Called on entry.  Returns a real tool to exec instead, or None.

    Raises LockoutError when the chain is too deep or recursion has no exit.
    """
    st = stack()
    if len(st) >= MAX_DEPTH:
        raise LockoutError("vamos re-entered %d times (%s -> %s); refusing to recurse"
                           % (len(st), " -> ".join(st), personality))
    if personality in st:
        real = find_real(personality)
        if real is None:
            raise LockoutError("'%s' was called from inside vamos %s and no real "
                               "'%s' exists to hand it to"
                               % (personality, " -> ".join(st), personality))
        return real
    return None


def exec_real(path: str, args: List[str], personality: str) -> None:
    env = child_env(personality=personality)
    os.execve(path, [path] + list(args), env)


def nvc_libdir(nvc: str) -> str:
    """nvc's library dir: build tree (bin/../lib) or installed (lib/nvc)."""
    if os.environ.get("NVC_LIBDIR"):
        return os.environ["NVC_LIBDIR"]
    prefix = os.path.dirname(os.path.dirname(os.path.realpath(nvc)))
    if os.path.isdir(os.path.join(prefix, "lib", "sv2vhdl")):
        return os.path.join(prefix, "lib")
    return os.path.join(prefix, "lib", "nvc")


def version_of(name: str, path: str) -> str:
    """One-line version string for the provenance header."""
    if name == "sv2ghdl" or name == "vamos":
        root = package_root()
        if os.path.isdir(os.path.join(root, ".git")) and shutil.which("git"):
            try:
                r = subprocess.run(["git", "-C", root, "rev-parse", "--short", "HEAD"],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   universal_newlines=True, timeout=10)
                if r.returncode == 0 and r.stdout.strip():
                    return "git-" + r.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                pass
        if name == "vamos":
            from vamos import VERSION
            return VERSION
        return "installed"
    flag = {"iverilog": "-V"}.get(name, "--version")
    try:
        r = subprocess.run([path, flag], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=20,
                           env=child_env())
    except (OSError, subprocess.SubprocessError):
        return "?"
    first = (r.stdout or "").strip().splitlines()[:1]
    if not first:
        return "?"
    m = re.search(r"(\d+\.\d+[\w.\-]*)", first[0])
    return m.group(1) if m else first[0][:40]
