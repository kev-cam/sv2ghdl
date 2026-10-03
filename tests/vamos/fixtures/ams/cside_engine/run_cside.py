#!/usr/bin/env python3
"""C-side tests of the co-simulation engine patches (docs/VAMOS_AMS_DESIGN.md §7, §9).

Runs nvc's analog-master co-simulation (``nvc -r --vacask-netlist=|--xyce-netlist=
--cosim-config=``) on small decks and checks, per case and engine, the exit
status, the end-of-run line and the analog waveform: the finish protocol and its
ABI handshake (P1), the t=0 settle (P2), per-boundary ramps (P3, late in a run
too, and ramps too short for the engine), the 8192-entry registry and the
boundary parser (P4), failing callbacks for unknown names (P5), the run loop and
Xyce failure reporting (P6) and the real-value clamp (P7); §6's --stop-time as
vamos writes it, whose femtosecond count is 2^32 or more for any stop from
4.294967296 us (the stop_time_* cases); end-line times printed exactly (the
finish_*_digits cases) and the finish window (finish_a2d_at_candidate); and an
interrupt, which is never a clean stop (the interrupt_* cases).

    python3 run_cside.py [--nvc-build DIR] [--vacask-build DIR] [--xyce-libs D1:D2]
                         [--engines vacask,xyce] [--xyce-shim] [-k SUBSTR]...
                         [--old-bridge LIB] [--old-vacask LIB] [--keep] [--list]

Linux (WSL) only: nvc, VACASK and Xyce are Linux binaries and the stub
libraries are built with cc.  Each case runs in its own directory under a
scratch root (kept with --keep).  The exit status is 0 when no selected case
fails; a case whose engine or input is missing is skipped with the reason.

--xyce-shim runs the Xyce cases through xyce_abi_shim.c, which adds
xyce_cosim_abi() to a Xyce C interface that predates the vamos cosim patches;
such an engine cannot end its transient at a digital stop, so the cases that
need the finish protocol are skipped on it.

Defaults come from the environment: NVCB (/usr/local/src/nvc-build), VCB
(/opt/build.VACASK/Release), VAMOS_XYCE_LIBS (~/xyce-libs,
/usr/local/src/xyce-build/utils/XyceCInterface, /usr/local/src/xyce-build/src).
The unittest wrapper is tests/vamos/test_cside_engine.py.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINES = ("vacask", "xyce")
VACASK_DEMO = "/usr/local/src/VACASK/demo/cosim"
XYCE_DEMO = "/usr/local/src/xyce/utils/test_simetrix_cosim"

# The end-of-run lines nvc prints (docs §6/§7); a run prints exactly one of
# them unless it fails before the engine runs.
END_RE = re.compile(r"^\*\* (?:Note|Error): (?:co-simulation finished: (?:digital stop at \S+ s"
                    r"(?: \(before the first analog step\))?|analog end at \S+ s)"
                    r"|(?:VACASK|Xyce) transient failed at \S+ s"
                    r"|co-simulation stalled at \S+ s"
                    r"|co-simulation interrupted at \S+ s)$")

# The exit status of an interrupted co-simulation (src/cosim.c, 128 + SIGINT)
EXIT_INTERRUPTED = 130


def fs_text(fs: int) -> str:
    """A femtosecond count as nvc's end lines print it (src/cosim.c fs_text): the time
    in seconds, exactly, in the style of %g -- what %.15g prints whenever that is exact,
    with as many digits as the count needs otherwise."""
    if fs == 0:
        return "0"
    sign, u = ("-", -fs) if fs < 0 else ("", fs)
    digits = str(u)
    nsig = len(digits.rstrip("0")) or 1
    x = len(digits) - 1 - 15
    prec = max(nsig, 15)
    if x < -4 or x >= prec:
        mant = digits[0] + ("." + digits[1:nsig] if nsig > 1 else "")
        return "%s%se%s%02d" % (sign, mant, "-" if x < 0 else "+", abs(x))
    if x >= 0:
        frac = digits[x + 1:nsig]
        return sign + digits[:x + 1] + ("." + frac if frac else "")
    return sign + "0." + "0" * (-x - 1) + digits[:nsig]


class Failure(Exception):
    """A check failed."""


class Skip(Exception):
    """The case cannot run here (missing engine, input or option)."""


# -- waveforms -------------------------------------------------------------------

class Wave(object):
    """Columns of a SPICE rawfile or a Xyce .prn file, looked up case-insensitively
    as ``n``, ``v(n)`` or the name as written."""

    def __init__(self, path: str, names: List[str], rows: List[List[float]]):
        self.path = path
        self.names = names
        self.rows = rows
        self._key = {}  # type: Dict[str, int]
        for i, n in enumerate(names):
            k = n.lower()
            self._key.setdefault(k, i)
            m = re.match(r"^v\((.*)\)$", k)
            self._key.setdefault(m.group(1) if m else "v(%s)" % k, i)

    def index(self, name: str) -> int:
        k = name.lower()
        if k not in self._key:
            raise Failure("%s has no column %r (has %s)" % (self.path, name, ", ".join(self.names)))
        return self._key[k]

    def col(self, name: str) -> List[float]:
        i = self.index(name)
        return [r[i] for r in self.rows]

    def times(self) -> List[float]:
        return self.col("time")

    def tlast(self) -> float:
        ts = self.times()
        if not ts:
            raise Failure("%s holds no points" % self.path)
        return ts[-1]

    def at(self, name: str, t: float) -> float:
        """The value at t, linear between points (the first point at or after t
        that is not a repeated time; the last value after the end)."""
        ts, vs = self.times(), self.col(name)
        if not ts:
            raise Failure("%s holds no points" % self.path)
        if t <= ts[0]:
            return vs[0]
        for i in range(1, len(ts)):
            if ts[i] >= t:
                t0, t1 = ts[i - 1], ts[i]
                if t1 == t0:
                    return vs[i]
                return vs[i - 1] + (vs[i] - vs[i - 1]) * (t - t0) / (t1 - t0)
        return vs[-1]


def read_raw(path: str) -> Wave:
    """A real SPICE rawfile, binary or ASCII; a blank No. Points: (Xyce paused)
    is counted from the data."""
    with open(path, "rb") as fh:
        data = fh.read()
    pos, binary = data.find(b"Binary:\n"), True
    if pos < 0:
        pos, binary = data.find(b"Values:\n"), False
    if pos < 0:
        raise Failure("%s: no Binary:/Values: section" % path)
    header = data[:pos].decode("latin-1").splitlines()
    nvars, npts, names = 0, -1, []  # type: int, int, List[str]
    for i, line in enumerate(header):
        key, _, val = line.partition(":")
        key = key.strip().lower()
        if key == "flags" and "complex" in val.lower():
            raise Failure("%s: complex data not supported" % path)
        if key == "no. variables":
            nvars = int(val)
        elif key == "no. points":
            npts = int(val) if val.strip() else -1
        elif key == "variables":
            for v in header[i + 1:i + 1 + nvars]:
                names.append(v.split()[1])
    if nvars <= 0 or len(names) != nvars:
        raise Failure("%s: bad variable list" % path)
    body = data[pos + 8:]
    rows = []  # type: List[List[float]]
    if binary:
        n = len(body) // (8 * nvars)
        if 0 <= npts < n:
            n = npts
        vals = struct.unpack("<%dd" % (n * nvars), body[:n * nvars * 8])
        rows = [list(vals[p * nvars:(p + 1) * nvars]) for p in range(n)]
    else:
        toks = body.decode("latin-1").split()
        k = 0
        while k + 1 + nvars <= len(toks) and (npts < 0 or len(rows) < npts):
            # a point index, then one value per variable
            rows.append([float(x.split(",")[0]) for x in toks[k + 1:k + 1 + nvars]])
            k += 1 + nvars
    return Wave(path, names, rows)


def read_prn(path: str) -> Wave:
    """A Xyce .prn file (Index TIME V(...) ... columns)."""
    with open(path) as fh:
        lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    if not lines:
        raise Failure("%s is empty" % path)
    names = lines[0].split()[1:]
    rows = []
    for ln in lines[1:]:
        if ln.startswith("End"):
            break
        rows.append([float(x) for x in ln.split()[1:]])
    return Wave(path, names, rows)


# -- environment -------------------------------------------------------------------

def _defined_symbols(lib: str) -> Optional[List[str]]:
    """The dynamic symbols a library defines, or None when nm is unavailable."""
    try:
        p = subprocess.run(["nm", "-D", "--defined-only", lib], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, universal_newlines=True)
    except OSError:
        return None
    if p.returncode != 0:
        return None
    return [ln.split()[-1] for ln in p.stdout.splitlines() if ln.split()]


class Env(object):
    """Where the tools are, plus the compiled work libraries and stubs."""

    def __init__(self, nvcb: str, vcb: str, xyce_libs: Sequence[str], scratch: str,
                 xyce_shim: bool = False, old_bridge: Optional[str] = None,
                 old_vacask: Optional[str] = None):
        self.nvcb = nvcb
        self.nvc = os.path.join(nvcb, "bin", "nvc")
        self.libdir = os.path.join(nvcb, "lib")
        self.vcb = vcb
        self.xyce_libs = [d for d in xyce_libs if d]
        self.scratch = scratch
        self.xyce_shim = xyce_shim
        self.old_bridge = old_bridge
        self.old_vacask = old_vacask
        self._works = {}   # type: Dict[Tuple[str, Tuple[str, ...]], str]
        self._elab = set()
        self._stubs = {}   # type: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], str]
        self._engine_ok = {}  # type: Dict[str, Optional[str]]

    # -- availability --

    def suite_problem(self) -> Optional[str]:
        if not sys.platform.startswith("linux"):
            return "needs Linux (WSL): nvc and the engines are Linux binaries"
        if not os.access(self.nvc, os.X_OK):
            return "no nvc at %s" % self.nvc
        if shutil.which("cc") is None:
            return "no C compiler (cc) for the stub libraries"
        bridge = os.path.join(self.libdir, "libcosim_bridge.so")
        if not os.path.isfile(bridge):
            return "no %s" % bridge
        syms = _defined_symbols(bridge)
        if syms is not None and "cosim_bridge_abi" not in syms:
            return "%s lacks cosim_bridge_abi (build it from nvc src/cosim_bridge.cpp)" % bridge
        return None

    def vacask_cinterface(self) -> str:
        return os.path.join(self.vcb, "cinterface", "libvacaskcinterface.so")

    def xyce_cinterface(self) -> Optional[str]:
        for d in self.xyce_libs:
            p = os.path.join(d, "libxycecinterface.so")
            if os.path.isfile(p):
                return p
        return None

    def engine_problem(self, engine: str) -> Optional[str]:
        if engine in self._engine_ok:
            return self._engine_ok[engine]
        why = None  # type: Optional[str]
        if engine == "vacask":
            lib = self.vacask_cinterface()
            if not os.path.isfile(lib):
                why = "no VACASK C interface at %s" % lib
            elif not os.path.isdir(os.path.join(self.vcb, "devices")):
                why = "no VACASK devices directory in %s" % self.vcb
            else:
                syms = _defined_symbols(lib)
                if syms is not None and "vacask_cosim_abi" not in syms:
                    why = "%s lacks vacask_cosim_abi (rebuild VACASK with the cosim patches)" % lib
        else:
            lib = self.xyce_cinterface()
            if lib is None:
                why = "no libxycecinterface.so in %s" % ":".join(self.xyce_libs)
            elif not self.xyce_shim:
                syms = _defined_symbols(lib)
                if syms is not None and "xyce_cosim_abi" not in syms:
                    why = ("%s lacks xyce_cosim_abi (rebuild Xyce with the vamos cosim patches, "
                           "or test the nvc side with --xyce-shim)" % lib)
        self._engine_ok[engine] = why
        return why

    # -- builds --

    def _run(self, cmd: List[str], cwd: Optional[str] = None, env: Optional[dict] = None,
             what: str = "") -> str:
        p = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, errors="replace")
        if p.returncode != 0:
            raise Failure("%s failed (rc %d): %s\n%s" % (what or cmd[0], p.returncode, " ".join(cmd),
                                                          p.stdout[-3000:]))
        return p.stdout

    def work(self, vhd: Sequence[str], std: str = "2008") -> str:
        """A work library with these VHDL files analysed (cached)."""
        key = (std, tuple(vhd))
        if key not in self._works:
            wdir = os.path.join(self.scratch, "lib%d" % len(self._works), "work")
            os.makedirs(os.path.dirname(wdir), exist_ok=True)
            self._run([self.nvc, "--std=" + std, "--work=work:" + wdir, "-L", self.libdir, "-a"]
                      + list(vhd), what="nvc -a")
            self._works[key] = wdir
        return self._works[key]

    def elaborate(self, work: str, top: str, std: str = "2008") -> None:
        if (work, top) not in self._elab:
            self._run([self.nvc, "--std=" + std, "--work=work:" + work, "-L", self.libdir, "-e",
                       top], what="nvc -e " + top)
            self._elab.add((work, top))

    def stub(self, kind: str, **defs: str) -> str:
        """Build a stub library (bridge, vacask, xyce or xyce_shim) once; returns
        the directory holding it, to put first on LD_LIBRARY_PATH."""
        key = (kind, tuple(sorted(defs.items())))
        if key not in self._stubs:
            src, out, flags = {
                "bridge": ("stub_bridge.c", "libcosim_bridge.so", []),
                "vacask": ("stub_engine.c", "libvacaskcinterface.so", []),
                "xyce": ("stub_engine.c", "libxycecinterface.so", ["-DSTUB_XYCE"]),
                "xyce_shim": ("xyce_abi_shim.c", "libxycecinterface.so", []),
            }[kind]
            d = os.path.join(self.scratch, "stubs", "%s%d" % (kind, len(self._stubs)))
            os.makedirs(d, exist_ok=True)
            cmd = ["cc", "-O2", "-shared", "-fPIC", "-o", os.path.join(d, out),
                   os.path.join(HERE, src)] + flags + ["-D%s=%s" % kv for kv in sorted(defs.items())]
            self._run(cmd + ["-ldl"], what="cc " + src)
            self._stubs[key] = d
        return self._stubs[key]

    def run_env(self, engine: str, first: Sequence[str] = ()) -> Dict[str, str]:
        """The environment of an nvc co-simulation run on this engine."""
        env = dict(os.environ)
        dirs = list(first) + [self.libdir]
        if engine == "vacask":
            dirs.append(os.path.join(self.vcb, "cinterface"))
            env["SIM_MODULE_PATH"] = os.path.join(self.vcb, "devices")
        else:
            if self.xyce_shim:
                dirs.append(self.stub("xyce_shim"))
                env["XYCE_SHIM_REAL"] = self.xyce_cinterface() or ""
            dirs += self.xyce_libs
        old = env.get("LD_LIBRARY_PATH")
        env["LD_LIBRARY_PATH"] = ":".join(dirs + ([old] if old else []))
        for k in ("COSIM_TRACE", "VACASK_COSIM_TRACE"):
            env.pop(k, None)
        return env


# -- one run --------------------------------------------------------------------------

class Result(object):
    """An nvc co-simulation run and the checks on it."""

    def __init__(self, ctx: "Ctx", rc: int, out: str, wall: float, cmd: List[str]):
        self.ctx = ctx
        self.rc = rc
        self.out = out
        self.wall = wall
        self.cmd = cmd

    def _fail(self, msg: str) -> None:
        tail = "\n".join(ln for ln in self.out.splitlines()
                         if not ln.lstrip().startswith("[") and ln.strip())[-2500:]
        raise Failure("%s\n  command: %s\n  rc %d, %.2f s; output tail:\n%s"
                      % (msg, " ".join(self.cmd), self.rc, self.wall, tail))

    def expect_rc(self, rc) -> "Result":
        ok = self.rc != 0 if rc == "nonzero" else self.rc == rc
        if not ok:
            self._fail("exit status %d, expected %s" % (self.rc, rc))
        return self

    def expect(self, *patterns: str) -> "Result":
        for p in patterns:
            if not re.search(p, self.out, re.M):
                self._fail("no line matching %r" % p)
        return self

    def forbid(self, *patterns: str) -> "Result":
        for p in patterns:
            m = re.search(p, self.out, re.M)
            if m:
                self._fail("unexpected line matching %r: %r" % (p, m.group(0)))
        return self

    def end_line(self, pattern: Optional[str]) -> "Result":
        """Exactly one end-of-run line, matching pattern (None: no end line)."""
        ends = [ln for ln in self.out.splitlines() if END_RE.match(ln)]
        if pattern is None:
            if ends:
                self._fail("unexpected end-of-run line %r" % ends[0])
            return self
        if len(ends) != 1:
            self._fail("%d end-of-run lines, expected one matching %r" % (len(ends), pattern))
        if not re.search(pattern, ends[0]):
            self._fail("end-of-run line %r does not match %r" % (ends[0], pattern))
        return self

    def wall_below(self, seconds: float) -> "Result":
        if self.wall > seconds:
            self._fail("took %.1f s, expected under %.0f s" % (self.wall, seconds))
        return self

    def wave(self) -> Wave:
        d = self.ctx.rundir
        if self.ctx.engine == "vacask":
            p = os.path.join(d, "tran1.raw")
            if os.path.isfile(p):
                return read_raw(p)
        else:
            p = os.path.join(d, "xyce_tran.raw")
            if os.path.isfile(p):
                return read_raw(p)
            prns = sorted(f for f in os.listdir(d) if f.endswith(".prn"))
            if prns:
                return read_prn(os.path.join(d, prns[0]))
        self._fail("no analog output in %s" % d)
        raise AssertionError  # not reached

    def near(self, node: str, t: float, v: float, tol: float) -> "Result":
        got = self.wave().at(node, t)
        if abs(got - v) > tol:
            self._fail("%s at %.6g s is %.6g, expected %.6g +- %.3g" % (node, t, got, v, tol))
        return self

    def tlast(self, t: float, after: float = 0.0) -> "Result":
        """The analog output ends at t (within 1e-9 relative or 2 fs, whichever is larger:
        an engine finishing at a digital stop may end up to 1 fs + 1e-14 relative, plus
        0.5 fs of rounding to the digital's clock, before it -- the finish window of
        src/cosim.c), or at most `after` later."""
        got = self.wave().tlast()
        tol = max(1e-9 * abs(t), 2e-15)
        if not (abs(got - t) <= tol or t < got <= t + after):
            self._fail("analog output ends at %.12g s, expected %.12g s%s"
                       % (got, t, " (+ up to %.3g s)" % after if after else ""))
        return self


class Ctx(object):
    """One case on one engine: its run directory and helpers."""

    def __init__(self, env: Env, engine: str, rundir: str, timeout: float):
        self.env = env
        self.engine = engine
        self.rundir = rundir
        self.timeout = timeout

    def path(self, name: str) -> str:
        return os.path.join(self.rundir, name)

    def write(self, name: str, text: str) -> str:
        p = self.path(name)
        with open(p, "w") as fh:
            fh.write(text)
        return p

    def fixture(self, name: str, src_dir: str = HERE) -> str:
        """Copy an input into the run directory (Xyce writes .prn next to its deck)."""
        src = os.path.join(src_dir, name)
        if not os.path.isfile(src):
            raise Skip("missing input %s" % src)
        dst = self.path(os.path.basename(name))
        shutil.copyfile(src, dst)
        return dst

    def deck(self, base: str) -> str:
        return self.fixture(base + (".sim" if self.engine == "vacask" else ".cir"))

    def cosim(self, top: str, deck: str, boundary: Optional[str], stop_time: Optional[str] = None,
              vhd: Optional[Sequence[str]] = None, std: str = "2008", first: Sequence[str] = (),
              extra_env: Optional[Dict[str, str]] = None, timeout: Optional[float] = None) -> Result:
        work = self.env.work(list(vhd) if vhd else [os.path.join(HERE, "cside.vhd")], std)
        self.env.elaborate(work, top, std)
        cmd = [self.env.nvc, "--std=" + std, "--work=work:" + work, "-L", self.env.libdir, "-r"]
        if stop_time:
            cmd.append("--stop-time=" + stop_time)
        cmd.append(("--vacask-netlist=" if self.engine == "vacask" else "--xyce-netlist=") + deck)
        if boundary:
            cmd.append("--cosim-config=" + boundary)
        cmd.append(top)
        env = self.env.run_env(self.engine, first)
        env.update(extra_env or {})
        t0 = time.time()
        try:
            p = subprocess.run(cmd, cwd=self.rundir, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, universal_newlines=True, errors="replace",
                               timeout=timeout or self.timeout)
            rc, out = p.returncode, p.stdout
        except subprocess.TimeoutExpired as e:
            out = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode("latin-1")
            raise Failure("hung: killed after %.0f s\n  command: %s\n%s"
                          % (timeout or self.timeout, " ".join(cmd), out[-2000:]))
        with open(self.path("nvc.log"), "w") as fh:
            fh.write(out)
        return Result(self, rc, out, time.time() - t0, cmd)

    def cosim_interrupt(self, top: str, deck: str, boundary: str, after: float,
                        vhd: Optional[Sequence[str]] = None, std: str = "2008",
                        timeout: Optional[float] = None) -> Result:
        """Run like cosim() and send nvc SIGINT `after` seconds after its "starting
        co-simulation" note (its interrupt handler is installed by then), as a Ctrl-C or a
        batch system's kill -INT would."""
        work = self.env.work(list(vhd) if vhd else [os.path.join(HERE, "cside.vhd")], std)
        self.env.elaborate(work, top, std)
        cmd = [self.env.nvc, "--std=" + std, "--work=work:" + work, "-L", self.env.libdir, "-r",
               ("--vacask-netlist=" if self.engine == "vacask" else "--xyce-netlist=") + deck,
               "--cosim-config=" + boundary, top]
        log = self.path("nvc.log")
        t0 = time.time()
        with open(log, "w") as fh:
            p = subprocess.Popen(cmd, cwd=self.rundir, env=self.env.run_env(self.engine),
                                 stdout=fh, stderr=subprocess.STDOUT)

        def text() -> str:
            with open(log, errors="replace") as fh:
                return fh.read()

        started = False
        while time.time() < t0 + 60 and p.poll() is None:
            if "starting co-simulation" in text():
                started = True
                break
            time.sleep(0.05)
        sent = False
        if started:
            time.sleep(after)
            if p.poll() is None:
                p.send_signal(signal.SIGINT)
                sent = True
        try:
            rc = p.wait(timeout=timeout or self.timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
            raise Failure("hung after SIGINT: killed after %.0f s\n  command: %s\n%s"
                          % (timeout or self.timeout, " ".join(cmd), text()[-2000:]))
        r = Result(self, rc, text(), time.time() - t0, cmd)
        if not sent:
            r._fail("the run %s before the interrupt was due" % ("ended" if started else
                                                                 "never started the co-simulation"))
        return r


# -- the cases ---------------------------------------------------------------------------

class CaseSpec(object):
    def __init__(self, name: str, fn: Callable[[Ctx], None], engines: Sequence[str],
                 needs_finish: bool, doc: str):
        self.name = name
        self.fn = fn
        self.engines = tuple(engines)
        self.needs_finish = needs_finish
        self.doc = doc


CASES = []  # type: List[CaseSpec]


def case(name: str, engines: Sequence[str] = ENGINES, needs_finish: bool = False):
    """Register a case; engines it runs on; needs_finish: the engine must end its
    transient at a digital stop (not possible through --xyce-shim)."""
    def deco(fn):
        CASES.append(CaseSpec(name, fn, engines, needs_finish, " ".join((fn.__doc__ or "").split())))
        return fn
    return deco


def _demo(t: Ctx, name: str) -> Result:
    """Run a stock demo (VACASK demo/cosim, Xyce utils/test_simetrix_cosim) unchanged."""
    vhd = os.path.join(VACASK_DEMO, "cosim_%s.vhd" % name)
    if not os.path.isfile(vhd):
        raise Skip("missing demo %s" % vhd)
    if t.engine == "vacask":
        deck = t.fixture(name + ".sim", VACASK_DEMO)
        bnd = t.fixture(name + ".boundary", VACASK_DEMO)
    else:
        deck = t.fixture(name + ".cir", XYCE_DEMO)
        bnd = t.fixture(name + ".boundary", XYCE_DEMO)
    return t.cosim("cosim_" + name, deck, bnd, stop_time="200ns", vhd=[vhd], std="2040")


@case("demo_min")
def _demo_min(t):
    """The stock min demo unchanged: a digital square wave drives an RC."""
    r = _demo(t, "min").expect_rc(0).end_line(r"analog end at 2e-07 s$")
    for at, v in ((99e-9, 0.3844), (149e-9, 0.2421), (199e-9, 0.5313)):
        r.near("nout", at, v, 0.003)


@case("demo_a2d")
def _demo_a2d(t):
    """The stock a2d demo unchanged: analog step -> A2D -> threshold -> D2A."""
    r = _demo(t, "a2d").expect_rc(0).end_line(r"analog end at 2e-07 s$")
    for at, v in ((99.4e-9, 0.0), (100e-9, 0.4998), (100.5e-9, 0.9998), (101e-9, 1.0)):
        r.near("nout", at, v, 0.006)


@case("demo_glitch")
def _demo_glitch(t):
    """The stock glitch demo unchanged: a D2A change mid-ramp reverses continuously."""
    r = _demo(t, "glitch").expect_rc(0).end_line(r"analog end at 2e-07 s$")
    for at, v in ((50.25e-9, 0.25), (50.5e-9, 0.5), (50.75e-9, 0.375), (51e-9, 0.25), (51.5e-9, 0.0)):
        r.near("nin", at, v, 0.005)


@case("demo_a2d_bcmp_fails", engines=("xyce",))
def _demo_a2d_bcmp(t):
    """The stock Xyce a2d demo plus a comparator that makes the transient fail at
    150 ns: 'Xyce transient failed at', non-zero exit (P6)."""
    vhd = os.path.join(VACASK_DEMO, "cosim_a2d.vhd")
    src = os.path.join(XYCE_DEMO, "a2d.cir")
    if not os.path.isfile(vhd) or not os.path.isfile(src):
        raise Skip("missing demo %s or %s" % (vhd, src))
    with open(src) as fh:
        text = fh.read()
    text = re.sub(r"(?im)^\.end\s*$", "Bcmp out 0 V={IF(TIME < 150n, 0, IF(V(cap) > 0.5, 0, 1))}\n"
                  "R1 out cap 1k\nC1 cap 0 1p\n.end\n", text)
    deck = t.write("a2d.cir", text)
    bnd = t.fixture("a2d.boundary", XYCE_DEMO)
    r = t.cosim("cosim_a2d", deck, bnd, stop_time="200ns", vhd=[vhd], std="2040")
    r.expect_rc("nonzero").end_line(r"^\*\* Error: Xyce transient failed at 1\.5e-07 s$")


@case("finish_120ns", needs_finish=True)
def _finish_120(t):
    """std.env.finish at 120 ns with the deck stop at 1 us: the run ends there
    promptly, rc 0, with the digital's stop time."""
    r = t.cosim("cs_fin120", t.deck("rc"), t.fixture("q.boundary"))
    r.expect_rc(0).end_line(r"^\*\* Note: co-simulation finished: digital stop at 1\.2e-07 s$")
    r.wall_below(30).tlast(1.2e-7, after=0.0 if t.engine == "vacask" else 2e-9)


@case("finish_odd_time", needs_finish=True)
def _finish_odd(t):
    """A finish between analog points (123.456 ns): the analog lands on it."""
    r = t.cosim("cs_finodd", t.deck("rc"), t.fixture("q.boundary"))
    r.expect_rc(0).end_line(r"digital stop at 1\.23456e-07 s$")
    r.tlast(1.23456e-7, after=0.0 if t.engine == "vacask" else 2e-9)


@case("finish_t0")
def _finish_t0(t):
    """An initial $finish (std.env.finish at t=0, during the t=0 settle): rc 0."""
    r = t.cosim("cs_fin0", t.deck("rc"), t.fixture("q.boundary"))
    r.expect_rc(0).expect(r"FINISH called")
    r.end_line(r"^\*\* Note: co-simulation finished: digital stop at 0 s \(before the first analog step\)$")
    r.wall_below(30)
    if t.engine == "vacask":
        r.tlast(0.0)


@case("fatal_t0")
def _fatal_t0(t):
    """A $fatal at t=0 (a failure report during the t=0 settle): rc 1."""
    r = t.cosim("cs_fatal0", t.deck("rc"), t.fixture("q.boundary"))
    r.expect_rc(1).expect(r"FATAL")
    r.end_line(r"^\*\* Note: co-simulation finished: digital stop at 0 s \(before the first analog step\)$")
    r.wall_below(30)


@case("finish_first_a2d_sample", needs_finish=True)
def _a2d_first(t):
    """A $finish triggered by the first A2D sample (the operating point, 0.7 V):
    never a veto to the step start; ends promptly, rc 0."""
    r = t.cosim("cs_a2dfin", t.deck("sense07"), t.fixture("ain.boundary"))
    r.expect_rc(0).expect(r"ain crossed 0\.5 at 0 fs")
    r.end_line(r"^\*\* Note: co-simulation finished: digital stop at 0 s( \(before the first analog step\))?$")
    r.wall_below(30)


@case("finish_a2d_threshold", needs_finish=True)
def _a2d_thr(t):
    """A $finish triggered by an A2D threshold crossing mid-run (~99.5 ns): the
    analog ends at the digital's stop time."""
    r = t.cosim("cs_a2dfin", t.deck("senseramp"), t.fixture("ain.boundary"))
    r.expect_rc(0).end_line(r"digital stop at 9\.95\d*e-08 s$")
    m = re.search(r"digital stop at (\S+) s", r.out)
    r.tlast(float(m.group(1)), after=0.0 if t.engine == "vacask" else 2e-9)


@case("runtime_fatal", needs_finish=True)
def _rt_fatal(t):
    """A digital runtime fatal (index out of range at 50 ns) exits non-zero
    without hanging."""
    r = t.cosim("cs_rtfatal", t.deck("rc"), t.fixture("q.boundary"))
    r.expect_rc("nonzero").expect(r"index 5 outside of")
    r.end_line(r"digital stop at 5e-08 s$").wall_below(30)


@case("env_stop", needs_finish=True)
def _env_stop(t):
    """std.env.stop ($stop) ends the run like a finish."""
    r = t.cosim("cs_stop80", t.deck("rc"), t.fixture("q.boundary"))
    r.expect_rc(0).expect(r"STOP called").end_line(r"digital stop at 8e-08 s$")
    r.tlast(8e-8, after=0.0 if t.engine == "vacask" else 2e-9)


@case("deck_end")
def _deck_end(t):
    """No stop: the run ends at the deck stop with 'analog end', never 'stalled'."""
    r = t.cosim("cs_edge", t.deck("rc"), t.fixture("q.boundary"))
    r.expect_rc(0).end_line(r"^\*\* Note: co-simulation finished: analog end at 1e-06 s$")
    r.forbid(r"stalled").tlast(1e-6)


@case("stop_time_before_deck_end")
def _stop_time(t):
    """--stop-time before the deck stop: 'analog end' at the stop time."""
    r = t.cosim("cs_edge", t.deck("rc"), t.fixture("q.boundary"), stop_time="100ns")
    r.expect_rc(0).end_line(r"^\*\* Note: co-simulation finished: analog end at 1e-07 s$")
    r.tlast(1e-7)


# --stop-time as vamos writes it (§6: ceil(stop * 1e15) + 1 fs, or N x precision for
# +vcs+finish+N, 3600 s without a .tran): femtosecond counts of 2^32 and more.

def _rc_deck_stop(t: Ctx, stop: str) -> str:
    """The rc deck with its transient stop changed from 1u to `stop` (e.g. "6u")."""
    src = t.deck("rc")
    with open(src) as fh:
        text = fh.read()
    old = "stop=1u" if t.engine == "vacask" else ".tran 1n 1u"
    if old not in text:
        raise Failure("%s has no %r to change" % (src, old))
    text = text.replace(old, old[:-2] + stop).replace("(deck stop 1 us)", "(deck stop %s)" % stop)
    return t.write("rc_%s%s" % (stop, os.path.splitext(src)[1]), text)


def _wrapped_stop(r: Result, stop_fs: int) -> None:
    """Fail with the diagnosis when nvc's 'starting co-simulation (stop_time=...)' note
    shows the femtosecond count modulo 2^32 instead of the count."""
    wrapped = stop_fs % (1 << 32)
    m = re.search(r"starting co-simulation \(stop_time=(\S+) s\)", r.out)
    if m and m.group(1) == "%.3g" % (wrapped / 1e15) and wrapped != stop_fs:
        r._fail("nvc read --stop-time=%dfs as %d fs, the count modulo 2^32 (src/nvc.c "
                "parse_time() reads it with sscanf(\"%%u\") into an unsigned int)"
                % (stop_fs, wrapped))


def _stop_fs(t: Ctx, deck: str, stop_fs: int, end: float) -> Result:
    """Run cs_edge with --stop-time=<stop_fs>fs: nvc must take the whole 64-bit count (its
    'starting co-simulation (stop_time=...)' note), end with 'analog end at <end> s' and
    the analog output must reach <end>."""
    r = t.cosim("cs_edge", deck, t.fixture("q.boundary"), stop_time="%dfs" % stop_fs)
    _wrapped_stop(r, stop_fs)
    r.expect(r"^\*\* Note: starting co-simulation \(stop_time=%s s\)$"
             % re.escape("%.3g" % (stop_fs / 1e15)))
    r.expect_rc(0).end_line(r"^\*\* Note: co-simulation finished: analog end at %s s$"
                            % re.escape("%.9g" % end))
    return r.forbid(r"stalled").tlast(end)


@case("stop_time_beyond_32bit_fs")
def _stop_time_32(t):
    """A 6 us deck with --stop-time=6000000001fs (vamos's ceil(stop*1e15)+1 fs; above
    2^32 fs = 4.294967296 us): 'analog end' at the deck stop, not at the count modulo
    2^32 (1.705 us)."""
    _stop_fs(t, _rc_deck_stop(t, "6u"), 6000000001, 6e-6)


@case("stop_time_paused_beyond_32bit_fs")
def _stop_time_32_paused(t):
    """A 6 us deck with --stop-time=4500000000fs (+vcs+finish+4500000 at 1 ps): 'analog
    end' at 4.5 us, before the deck stop (not at 0.205 us)."""
    _stop_fs(t, _rc_deck_stop(t, "6u"), 4500000000, 4.5e-6)


@case("stop_time_3600s_default")
def _stop_time_3600(t):
    """The 1 us deck with --stop-time=3600000000000000001fs (vamos's stop without a
    .tran): the run ends at the deck stop (not at 0.661 us)."""
    _stop_fs(t, t.deck("rc"), 3600000000000000001, 1e-6)


@case("stop_time_beyond_time_high")
def _stop_time_high(t):
    """--stop-time=9223372036854775808fs, one past TIME'HIGH (and 0 modulo 2^32): an
    error naming the value before anything runs, or a run to the deck stop if nvc
    clamps it to TIME'HIGH; never a run that ends early."""
    stop_fs = 9223372036854775808
    r = t.cosim("cs_edge", t.deck("rc"), t.fixture("q.boundary"), stop_time="%dfs" % stop_fs)
    if r.rc != 0 and not any(END_RE.match(ln) for ln in r.out.splitlines()):
        r.expect(r"^\*\* Fatal: .*%dfs" % stop_fs)
        return
    _wrapped_stop(r, stop_fs)
    r.expect_rc(0).end_line(r"^\*\* Note: co-simulation finished: analog end at 1e-06 s$")
    r.tlast(1e-6)


@case("transient_failed")
def _tran_failed(t):
    """A mid-transient analog failure: '<engine> transient failed at', non-zero
    exit (VACASK: a negative-resistance node kicked at 100 ns; Xyce: the a2d
    round trip plus a comparator that fails at 150 ns)."""
    if t.engine == "vacask":
        r = t.cosim("cs_edge", t.fixture("fail.sim"), t.fixture("q.boundary"))
        r.expect_rc("nonzero").end_line(r"^\*\* Error: VACASK transient failed at 1\.\d+e-07 s$")
    else:
        r = t.cosim("cs_a2d", t.fixture("a2dfail.cir"), t.fixture("a2d.boundary"))
        r.expect_rc("nonzero").end_line(r"^\*\* Error: Xyce transient failed at 1\.5e-07 s$")


@case("loop_stalled")
def _loop_stalled(t):
    """The run loop on an engine that returns without progress: 'co-simulation
    stalled at', non-zero exit (stub engine)."""
    lib = t.env.stub(t.engine, STUB_ABI="2")
    r = t.cosim("cs_edge", t.deck("rc"), t.fixture("q.boundary"), first=[lib],
                extra_env={"STUB_MODE": "stall"})
    r.expect_rc("nonzero").end_line(r"^\*\* Error: co-simulation stalled at 0 s$")


@case("loop_failed")
def _loop_failed(t):
    """The run loop on an engine whose simulateUntil fails before completion:
    '<engine> transient failed at 5e-08 s' (stub engine; Xyce's bool result)."""
    lib = t.env.stub(t.engine, STUB_ABI="2")
    r = t.cosim("cs_edge", t.deck("rc"), t.fixture("q.boundary"), first=[lib],
                extra_env={"STUB_MODE": "fail"})
    eng = "VACASK" if t.engine == "vacask" else "Xyce"
    r.expect_rc("nonzero").end_line(r"^\*\* Error: %s transient failed at 5e-08 s$" % eng)


@case("loop_complete")
def _loop_complete(t):
    """The run loop on an engine that completes early: 'analog end at 3e-08 s'
    (stub engine; Xyce's bool result read correctly)."""
    lib = t.env.stub(t.engine, STUB_ABI="2")
    r = t.cosim("cs_edge", t.deck("rc"), t.fixture("q.boundary"), first=[lib],
                extra_env={"STUB_MODE": "complete"})
    r.expect_rc(0).end_line(r"^\*\* Note: co-simulation finished: analog end at 3e-08 s$")


@case("abi_bridge_missing", engines=("vacask",))
def _abi_bridge_missing(t):
    """New nvc with an old (unpatched) libcosim_bridge.so: a clean ABI failure
    before anything runs."""
    lib = t.env.stub("bridge")
    r = t.cosim("cs_edge", t.deck("rc"), t.fixture("q.boundary"), first=[lib])
    r.expect_rc(1).end_line(None)
    r.expect(r"^\*\* Fatal: co-simulation ABI mismatch: \S*libcosim_bridge\.so does not export "
             r"cosim_bridge_abi\(\)")


@case("abi_bridge_version", engines=("vacask",))
def _abi_bridge_version(t):
    """A bridge reporting ABI 1: a clean ABI failure."""
    lib = t.env.stub("bridge", STUB_ABI="1")
    r = t.cosim("cs_edge", t.deck("rc"), t.fixture("q.boundary"), first=[lib])
    r.expect_rc(1).end_line(None)
    r.expect(r"co-simulation ABI mismatch: \S*libcosim_bridge\.so has cosim_bridge_abi\(\) = 1, need 2 or later")


@case("abi_engine_missing")
def _abi_engine_missing(t):
    """New nvc with an engine C interface that predates the finish protocol:
    a clean ABI failure (an old engine would read a finish as accept)."""
    lib = t.env.stub(t.engine)
    r = t.cosim("cs_edge", t.deck("rc"), t.fixture("q.boundary"), first=[lib])
    r.expect_rc(1).end_line(None)
    r.expect(r"^\*\* Fatal: co-simulation ABI mismatch: \S*lib%scinterface\.so does not export "
             r"%s_cosim_abi\(\)" % (t.engine, t.engine))


@case("abi_real_old_bridge", engines=("vacask",))
def _abi_real_old_bridge(t):
    """The unpatched libcosim_bridge.so itself (--old-bridge): a clean ABI failure."""
    if not t.env.old_bridge:
        raise Skip("no --old-bridge given")
    d = t.path("oldbridge")
    os.makedirs(d, exist_ok=True)
    shutil.copyfile(t.env.old_bridge, os.path.join(d, "libcosim_bridge.so"))
    r = t.cosim("cs_edge", t.deck("rc"), t.fixture("q.boundary"), first=[d])
    r.expect_rc(1).end_line(None).expect(r"co-simulation ABI mismatch: .*cosim_bridge_abi")


@case("abi_real_old_vacask", engines=("vacask",))
def _abi_real_old_vacask(t):
    """The unpatched libvacaskcinterface.so itself (--old-vacask): a clean ABI failure."""
    if not t.env.old_vacask:
        raise Skip("no --old-vacask given")
    d = t.path("oldvacask")
    os.makedirs(d, exist_ok=True)
    shutil.copyfile(t.env.old_vacask, os.path.join(d, "libvacaskcinterface.so"))
    r = t.cosim("cs_edge", t.deck("rc"), t.fixture("q.boundary"), first=[d])
    r.expect_rc(1).end_line(None).expect(r"co-simulation ABI mismatch: .*vacask_cosim_abi")


@case("t0_settle_no_startup_pulse")
def _clk1(t):
    """A clock that is 1 at t=0 (declared 0): the operating point sees 1 V and
    there is no start-up pulse (P2)."""
    r = t.cosim("cs_clk1", t.deck("rc"), t.fixture("q.boundary"), stop_time="100ns")
    r.expect_rc(0).end_line(r"analog end at 1e-07 s$")
    w = r.wave()
    for at in (0.0, 0.5e-9, 1e-9, 25e-9, 49e-9):
        r.near("nin", at, 1.0, 1e-6)
    lo = min(v for tt, v in zip(w.times(), w.col("nin")) if tt <= 49e-9)
    if lo < 1.0 - 1e-6:
        r._fail("nin dips to %.6g before 49 ns" % lo)
    r.near("nin", 50.5e-9, 0.5, 0.01)


@case("ramp_rise_10ps")
def _ramp_10ps(t):
    """rise=1e-11 gives a 10 ps edge (P3)."""
    r = t.cosim("cs_edge", t.deck("rc"), t.fixture("q_r11.boundary"), stop_time="100ns")
    r.expect_rc(0).end_line(r"analog end at 1e-07 s$")
    r.near("nin", 50e-9, 0.0, 1e-6).near("nin", 50.005e-9, 0.5, 0.02).near("nin", 50.01e-9, 1.0, 1e-6)


@case("ramp_default_1ns")
def _ramp_1ns(t):
    """No ramp columns: the 1 ns default."""
    r = t.cosim("cs_edge", t.deck("rc"), t.fixture("q.boundary"), stop_time="100ns")
    r.expect_rc(0).near("nin", 50e-9, 0.0, 1e-6).near("nin", 50.5e-9, 0.5, 0.02).near("nin", 51e-9, 1.0, 1e-6)


@case("ramp_rise0_clamped")
def _ramp_0(t):
    """rise=0 fall=0: clamped to 1 fs with a warning; the run completes."""
    r = t.cosim("cs_edge", t.deck("rc"), t.fixture("q_r0.boundary"), stop_time="100ns")
    r.expect_rc(0).end_line(r"analog end at 1e-07 s$")
    r.expect(r"^\*\* Warning: \S*q_r0\.boundary:2: rise=0 is below 1 fs: clamped to 1e-15 s$",
             r"^\*\* Warning: \S*q_r0\.boundary:2: fall=0 is below 1 fs: clamped to 1e-15 s$")
    r.near("nin", 50e-9, 0.0, 1e-6).near("nin", 50.5e-9, 1.0, 1e-6)


@case("ramp_reversal_mid_rise")
def _ramp_rev(t):
    """rise=1e-11 fall=1e-9 and a reversal mid-rise (at 0.5 V): the fall takes
    the full 1 ns from the reversal."""
    r = t.cosim("cs_reversal", t.deck("rc"), t.fixture("q_rev.boundary"), stop_time="100ns")
    r.expect_rc(0).end_line(r"analog end at 1e-07 s$")
    r.near("nin", 50.005e-9, 0.5, 0.02).near("nin", 50.505e-9, 0.25, 0.02)
    r.near("nin", 51.005e-9, 0.0, 1e-6).near("nin", 52e-9, 0.0, 1e-6)


@case("real_passthrough")
def _real(t):
    """Finite real values pass through unclamped (5 MV); real'low is 0 V (P7)."""
    r = t.cosim("cs_bigreal", t.deck("rc2"), t.fixture("q2.boundary"))
    r.expect_rc(0).end_line(r"analog end at 1e-07 s$")
    r.near("nin", 50e-9, 5.0e6, 1e-3).near("nin2", 50e-9, 0.0, 1e-12)


def _many_vhdl(n: int, assign: bool) -> str:
    sigs = "\n".join("   signal s%d : real := 0.0;" % k for k in range(n))
    body = "\n".join("      s%d <= %s;" % (k, repr(k * 0.001)) for k in range(n)) if assign else ""
    return ("entity cs_many is end entity;\narchitecture tb of cs_many is\n%s\nbegin\n"
            "   process begin\n      wait for 10 ns;\n%s\n      wait;\n   end process;\nend architecture;\n"
            % (sigs, body))


@case("registry_300")
def _registry_300(t):
    """300 boundary signals (beyond the old 256-entry registry) all bind and drive."""
    n = 300
    vhd = t.write("many.vhd", _many_vhdl(n, True))
    bnd = t.write("many.boundary", "".join("D2A .s%d n%d\n" % (k, k) for k in range(n)))
    if t.engine == "vacask":
        deck = t.write("many.sim", "C-side: 300 D2A sources\n\nload \"resistor.osdi\"\n\nmodel vsrc vsource\n"
                       "model r resistor\n\n" + "".join(
                           "v%d (n%d 0) vsrc type=\"pwl\" file=\"code:libcosim_bridge.so:vacask_bridge_init:"
                           "d2a:n%d\"\nr%d (n%d 0) r r=1k\n" % (k, k, k, k, k) for k in range(n))
                       + "\ncontrol\n  analysis tran1 tran step=1n stop=50n\nendc\n")
    else:
        deck = t.write("many.cir", "* C-side: 300 D2A sources\n" + "".join(
            "V%d n%d 0 PWL FILE \"code:libcosim_bridge.so:nvc_bridge_init:d2a:n%d\"\nR%d n%d 0 1k\n"
            % (k, k, k, k, k) for k in range(n))
            + ".tran 1n 50n\n.print tran format=raw file=xyce_tran.raw " +
            " ".join("V(n%d)" % k for k in (0, 1, 255, 256, 299)) + "\n.end\n")
    r = t.cosim("cs_many", deck, bnd, vhd=[vhd])
    r.expect_rc(0).end_line(r"analog end at 5e-08 s$").forbid(r"registry full", r"FAILED")
    for k in (0, 1, 255, 256, 299):
        r.near("n%d" % k, 40e-9, k * 0.001, 1e-9)


@case("registry_full", engines=("vacask",))
def _registry_full(t):
    """8193 boundary signals: a hard 'registry full' error before the engine
    starts (the registry holds 8192)."""
    n = 8193
    vhd = t.write("full.vhd", _many_vhdl(n, False))
    bnd = t.write("full.boundary", "".join("D2A .s%d n%d\n" % (k, k) for k in range(n)))
    r = t.cosim("cs_many", t.deck("rc"), bnd, vhd=[vhd], timeout=600)
    r.expect_rc(1).end_line(None)
    r.expect(r"D2A: \.s8192 <-> 'n8192' FAILED \(registry full: at most 8192 boundary signals\)",
             r"^\*\* Fatal: failed to register the boundary signals")
    r.forbid(r"s8191 <-> 'n8191' FAILED")


@case("boundary_malformed", engines=("vacask",))
def _malformed(t):
    """Malformed boundary lines are errors naming file:line; the run stops
    before the engine starts (P3/P4)."""
    bad = [
        "D2A .q",                                  # 2: missing field
        "X2Y .q nin",                              # 3: bad direction
        "D2A .q nin rise=nan",                     # 4: NaN
        "D2A .q nin fall=-1e-9",                   # 5: negative
        "D2A .q nin slew=1e-9",                    # 6: unknown column
        "D2A .q nin 5",                            # 7: not key=value
        "D2A .q nin rise=1e-9 rise=2e-9",          # 8: repeated
        "D2A .q nin rise=1e-9xyz",                 # 9: junk
        "D2A .q " + "n" * 256,                     # 10: name too long
        "D2A ." + "p" * 4095 + " nin",             # 11: path too long (4096)
        "D2A .q nin rise=inf",                     # 12: infinite
    ]
    bnd = t.write("bad.boundary", "# malformed lines\n" + "\n".join(bad) + "\n")
    r = t.cosim("cs_edge", t.deck("rc"), bnd)
    r.expect_rc(1).end_line(None)
    r.expect(r"bad\.boundary:2: malformed boundary line: expected D2A\|A2D",
             r"bad\.boundary:3: unknown direction 'X2Y'",
             r"bad\.boundary:4: malformed boundary line: rise=nan is not a time",
             r"bad\.boundary:5: malformed boundary line: fall=-1e-9 is not a time",
             r"bad\.boundary:6: malformed boundary line: unknown column 'slew'",
             r"bad\.boundary:7: malformed boundary line: unexpected field '5'",
             r"bad\.boundary:8: malformed boundary line: rise= given twice",
             r"bad\.boundary:9: malformed boundary line: rise=1e-9xyz is not a time",
             r"bad\.boundary:10: malformed boundary line: bridge name of 256 characters exceeds 255",
             r"bad\.boundary:11: malformed boundary line: nvc path of 4096 characters exceeds 4095",
             r"bad\.boundary:12: malformed boundary line: rise=inf is not a time",
             r"11 errors in boundary config",
             r"^\*\* Fatal: failed to parse boundary config")


@case("boundary_long_line", engines=("vacask",))
def _long_line(t):
    """A valid line longer than the old 1024-byte buffer (a long trailing
    comment) parses as one line."""
    bnd = t.write("long.boundary", "D2A .q nin rise=1e-11 fall=1e-11 # " + "x" * 3000 + "\n")
    r = t.cosim("cs_edge", t.deck("rc"), bnd, stop_time="100ns")
    r.expect_rc(0).end_line(r"analog end at 1e-07 s$").forbid(r"malformed")
    r.near("nin", 50.005e-9, 0.5, 0.02)


@case("missing_bridge_name")
def _missing_name(t):
    """A deck name missing from the boundary file fails cleanly, no segfault (P5)."""
    r = t.cosim("cs_edge", t.deck("rc_nope"), t.fixture("q.boundary"))
    r.expect_rc("nonzero").expect(r"\[cosim_bridge\] signal 'nope' not registered")
    r.forbid(r"Caught signal", r"SEGV", r"Segmentation").end_line(None)
    if t.engine == "vacask":
        r.expect(r"^\*\* Fatal: VACASK initialize failed")


@case("direction_mismatch", engines=("vacask",))
def _dir_mismatch(t):
    """A deck D2A bound to a name the boundary file declares A2D fails cleanly."""
    bnd = t.write("dir.boundary", "A2D .q nin\n")
    r = t.cosim("cs_edge", t.deck("rc"), bnd)
    r.expect_rc("nonzero").end_line(None)
    r.expect(r"signal 'nin' not registered as D2A \(the boundary file has it as A2D\)")


@case("duplicate_bridge_name", engines=("vacask",))
def _dup(t):
    """Two boundary lines with one bridge name: an error naming the line."""
    bnd = t.write("dup.boundary", "D2A .q nin\nD2A .q nin\n")
    r = t.cosim("cs_edge", t.deck("rc"), bnd)
    r.expect_rc(1).end_line(None).expect(r"dup\.boundary:2: duplicate bridge name 'nin'")


# -- end-line times: the digital's femtosecond count, printed exactly (§6/§7) ------------

def _end_time(r: Result) -> Tuple[str, float]:
    """The time text of the one end line, and its value."""
    ends = [ln for ln in r.out.splitlines() if END_RE.match(ln)]
    if len(ends) != 1:
        r._fail("%d end-of-run lines, expected one" % len(ends))
    m = re.search(r" at (\S+) s", ends[0])
    return m.group(1), float(m.group(1))


@case("finish_10_digits", needs_finish=True)
def _finish_10_digits(t):
    """std.env.finish at 1000000005 fs: the stop line prints all 10 digits
    (1.000000005e-06; %.9g printed 1.00000001e-06, 5 fs after the analog's end, and
    vamos failed the run as "analog output ends early")."""
    r = t.cosim("cs_fin10", _rc_deck_stop(t, "2u"), t.fixture("q.boundary"))
    r.expect_rc(0).end_line(r"^\*\* Note: co-simulation finished: digital stop at "
                            r"1\.000000005e-06 s$")
    r.tlast(1.000000005e-6, after=0.0 if t.engine == "vacask" else 2e-9)


@case("finish_ms_10_digits", needs_finish=True)
def _finish_ms_10_digits(t):
    """std.env.finish at 2000000006 ps (deck stop 3 ms): "digital stop at 0.002000000006
    s" (%.9g printed 0.00200000001)."""
    r = t.cosim("cs_fin2ms", _rc_deck_stop(t, "3m"), t.fixture("q.boundary"), timeout=300)
    r.expect_rc(0).end_line(r"^\*\* Note: co-simulation finished: digital stop at "
                            r"0\.002000000006 s$")
    r.tlast(2.000000006e-3, after=0.0 if t.engine == "vacask" else 2e-9)


def _slow_deck(t: Ctx) -> str:
    """D2A q -> 96k/103k divider + 1 pF -> A2D ain (settling just above 1.65 V, about
    1 mV/ns at the crossing), plus a sine branch so that the step times are irregular."""
    if t.engine == "vacask":
        return t.write("slow.sim", """C-side: slow threshold crossing

load "resistor.osdi"
load "capacitor.osdi"

model vsrc vsource
model isrc isource
model r resistor
model c capacitor

v_d2a (nin 0) vsrc type="pwl" file="code:libcosim_bridge.so:vacask_bridge_init:d2a:nin"
r1 (nin mid) r r=96k
r2 (mid 0) r r=103k
c1 (mid 0) c c=1p
i_a2d (mid 0) isrc type="pwl" file="code:libcosim_bridge.so:vacask_bridge_init:a2d:ain"
vs (s 0) vsrc type="sine" sinedc=0 ampl=1 freq=37.7e6
rs (s n2) r r=1k
cs (n2 0) c c=1p

control
  analysis tran1 tran step=1n stop=3u
endc
""")
    return t.write("slow.cir", """* C-side: slow threshold crossing
V_d2a nin 0 PWL FILE "code:libcosim_bridge.so:nvc_bridge_init:d2a:nin"
R1 nin mid 96k
R2 mid 0 103k
C1 mid 0 1p
I_a2d mid 0 PWL FILE "code:libcosim_bridge.so:nvc_bridge_init:a2d:ain"
VS s 0 SIN(0 1 37.7e6)
RS s n2 1k
CS n2 0 1p
.tran 1n 3u
.print tran format=raw file=xyce_tran.raw V(nin) V(mid)
.end
""")


@case("finish_a2d_at_candidate", needs_finish=True)
def _finish_a2d_candidate(t):
    """A finish on a slow A2D crossing (the probe moves less than COSIM_A2D_DV in a
    step, so the digital stops at the candidate time itself): the stop line is the
    digital's own femtosecond time, exactly, and the analog ends within the finish window
    (2 fs) of it."""
    bnd = t.write("slow.boundary", "D2A .q nin\nA2D .ain ain\n")
    r = t.cosim("cs_slowfin", _slow_deck(t), bnd)
    r.expect_rc(0).expect(r"ain crossed 1\.65 at \d+ fs")
    fs = int(re.search(r"ain crossed 1\.65 at (\d+) fs", r.out).group(1))
    r.end_line(r"^\*\* Note: co-simulation finished: digital stop at %s s$"
               % re.escape(fs_text(fs)))
    r.tlast(fs / 1e15, after=0.0 if t.engine == "vacask" else 2e-9)


# -- P3 on both engines: ramps late in a run --------------------------------------------
#
# D2A q -> 1k/1k divider -> A2D ain; q goes 0 -> hi at the edge and back to 0 100 ns
# later; the digital reports when ain crosses 0.25 V, ideally at edge + ramp * 0.25/(hi/2)
# up and edge + ramp * (1 - 0.25/(hi/2)) down.  The first analog step after a change was
# sized before the source saw it, and on Xyce it used to stride past a short ramp: the
# change was spread over the whole step (an A2D crossing 4.4 ps late at 1.5 ms, 7.8 ns at
# 1.5 s) or the run failed ("time step too small").

def _ramp_vhdl(edge: str, hi: float) -> str:
    return ("entity cs_late is end entity;\n"
            "architecture tb of cs_late is\n"
            "   constant tedge : time := %s;\n"
            "   signal q   : real := 0.0;\n"
            "   signal ain : real := 0.0;\n"
            "begin\n"
            "   process begin\n"
            "      wait for tedge;\n"
            "      q <= %r;\n"
            "      wait for 100 ns;\n"
            "      q <= 0.0;\n"
            "      wait;\n"
            "   end process;\n"
            "   process (ain) begin\n"
            "      if ain > 0.25 and ain'last_value <= 0.25 then\n"
            "         report \"rise crossing = edge + \" & time'image(now - tedge);\n"
            "      end if;\n"
            "      if ain < 0.25 and ain'last_value >= 0.25 then\n"
            "         report \"fall crossing = edge + \" & time'image(now - tedge - 100 ns);\n"
            "      end if;\n"
            "   end process;\n"
            "end architecture;\n" % (edge, hi))


def _ramp_run(t: Ctx, name: str, edge: str, hi: float, ramp: float, xyce_tran: str,
              vacask_tran: str) -> Result:
    vhd = t.write(name + ".vhd", _ramp_vhdl(edge, hi))
    bnd = t.write(name + ".boundary", "D2A .q nin rise=%r fall=%r\nA2D .ain ain\n" % (ramp, ramp))
    if t.engine == "vacask":
        deck = t.write(name + ".sim", """C-side: D2A q -> 1k/1k divider -> A2D ain

load "resistor.osdi"
model vsrc vsource
model isrc isource
model r resistor

v_d2a (nin 0) vsrc type="pwl" file="code:libcosim_bridge.so:vacask_bridge_init:d2a:nin"
r1 (nin nmid) r r=1k
r2 (nmid 0) r r=1k
i_a2d (nmid 0) isrc type="pwl" file="code:libcosim_bridge.so:vacask_bridge_init:a2d:ain"

control
  analysis tran1 tran %s
endc
""" % vacask_tran)
    else:
        deck = t.write(name + ".cir", """* C-side: D2A q -> 1k/1k divider -> A2D ain
V_d2a nin 0 PWL FILE "code:libcosim_bridge.so:nvc_bridge_init:d2a:nin"
R1 nin nmid 1k
R2 nmid 0 1k
I_a2d nmid 0 PWL FILE "code:libcosim_bridge.so:nvc_bridge_init:a2d:ain"
%s
.print tran format=raw file=xyce_tran.raw V(nin) V(nmid)
.end
""" % xyce_tran)
    return t.cosim("cs_late", deck, bnd, vhd=[vhd])


def _crossings(r: Result, hi: float, ramp: float, tol: float) -> None:
    """Both 0.25 V crossings within tol of where the ramps put them."""
    for edge, want in (("rise", ramp * 0.25 / (hi / 2)), ("fall", ramp * (1 - 0.25 / (hi / 2)))):
        m = re.search(r"%s crossing = edge \+ (\d+) fs" % edge, r.out)
        if not m:
            r._fail("no %s crossing reported" % edge)
        got = int(m.group(1)) * 1e-15
        if abs(got - want) > tol:
            r._fail("%s crossing at edge + %.4g s, expected edge + %.4g s +- %.2g s (the D2A ramp "
                    "is not honoured)" % (edge, got, want, tol))


@case("ramp_late_10ps")
def _ramp_late_10ps(t):
    """10 ps ramps at 1.5 ms with no maximum step: both A2D crossings at edge + 5 ps
    (+- 1 ps; on Xyce the first step after each change used to stride past the ramp, 4.4
    ps late) and the waveform follows the ramp."""
    r = _ramp_run(t, "late", "1.5 ms", 1.0, 1e-11, ".tran 1n 2m", "step=1n stop=2m")
    r.expect_rc(0).end_line(r"analog end at 0\.002 s$")
    _crossings(r, 1.0, 1e-11, 1e-12)
    r.near("nin", 1.5e-3 - 1e-12, 0.0, 1e-6).near("nin", 1.5e-3 + 5e-12, 0.5, 0.02)
    r.near("nin", 1.5e-3 + 1e-11, 1.0, 1e-6)


@case("ramp_late_coarse_1v8")
def _ramp_late_coarse(t):
    """1.8 V, 10 ps ramps at 1.5 ms with the maximum step vamos derives for .tran 100n 2m
    (500 ns): crossings at edge + 2.78 ps and edge + 7.22 ps (+- 1 ps; Xyce's fall used to
    land at 17.9 ps)."""
    r = _ramp_run(t, "coarse", "1.5 ms", 1.8, 1e-11, ".tran 100n 2m 0 500n",
                  "step=100n stop=2m maxstep=500n")
    r.expect_rc(0).end_line(r"analog end at 0\.002 s$")
    _crossings(r, 1.8, 1e-11, 1e-12)


@case("ramp_1p5s_10ps")
def _ramp_1p5s(t):
    """10 ps ramps at 1.5 s with a 5 ms maximum step (.tran 1m 2 0 5m): the run ends
    normally at 2 s (on Xyce the skipped falling ramp used to end it with "time step too
    small") and both crossings are at edge + 5 ps (+- 1 ps)."""
    r = _ramp_run(t, "sec", "1.5 sec", 1.0, 1e-11, ".tran 1m 2 0 5m", "step=1m stop=2 maxstep=5m")
    r.expect_rc(0).end_line(r"analog end at 2 s$").forbid(r"(?i)too small")
    _crossings(r, 1.0, 1e-11, 1e-12)


@case("ramp_below_resolution")
def _ramp_subres(t):
    """1 fs ramps at 1.5 s, shorter than either engine resolves there (1e-13 x t): a
    warning naming the signal, and the ramps take 150 fs (crossings at edge + 75 fs) --
    never a whole analog step (VACASK spread them over 100 ns, Xyce over 15.6 ns or failed
    with "time step too small")."""
    r = _ramp_run(t, "subres", "1.5 sec", 1.0, 1e-15, ".tran 1n 2", "step=1n stop=2")
    r.expect_rc(0).end_line(r"analog end at 2 s$").forbid(r"(?i)too small")
    r.expect(r"^\[cosim_bridge\] warning: D2A 'nin': a 1e-15 s ramp at 1\.5 s is shorter than "
             r"%s resolves at that time; it takes 1\.5e-13 s" % ("VACASK" if t.engine == "vacask"
                                                                 else "Xyce"))
    if len(re.findall(r"warning: D2A 'nin'", r.out)) != 1:
        r._fail("the short-ramp warning is not reported once")
    _crossings(r, 1.0, 1.5e-13, 1e-14)


# -- interrupts: never a clean stop -------------------------------------------------------

@case("interrupt_stub_init")
def _interrupt_init(t):
    """SIGINT during engine initialisation (stub engine): "co-simulation interrupted at 0
    s", exit status 130, never "digital stop"."""
    lib = t.env.stub(t.engine, STUB_ABI="2")
    r = t.cosim("cs_edge", t.deck("rc"), t.fixture("q.boundary"), first=[lib],
                extra_env={"STUB_MODE": "sigint_init"})
    r.expect_rc(EXIT_INTERRUPTED).end_line(r"^\*\* Error: co-simulation interrupted at 0 s$")
    r.forbid(r"digital stop")


@case("interrupt_stub_run")
def _interrupt_run(t):
    """SIGINT while the engine runs (stub engine, between digital processes):
    "co-simulation interrupted at 0 s", exit status 130 (the digital recorded no status)."""
    lib = t.env.stub(t.engine, STUB_ABI="2")
    r = t.cosim("cs_edge", t.deck("rc"), t.fixture("q.boundary"), first=[lib],
                extra_env={"STUB_MODE": "sigint_run"})
    r.expect_rc(EXIT_INTERRUPTED).end_line(r"^\*\* Error: co-simulation interrupted at 0 s$")
    r.forbid(r"digital stop")


@case("interrupt_running", needs_finish=True)
def _interrupt_real(t):
    """SIGINT to nvc 1 s into a long run (a 4 ns clock, deck stop 2 ms): the engine still
    ends its transient (a readable rawfile), but the run is reported as interrupted --
    "co-simulation interrupted at <t> s", exit status 130 -- never as a digital stop."""
    r = t.cosim_interrupt("cs_clk4", _rc_deck_stop(t, "2m"), t.fixture("q.boundary"), 1.0)
    r.expect_rc(EXIT_INTERRUPTED).end_line(r"^\*\* Error: co-simulation interrupted at \S+ s$")
    r.forbid(r"digital stop", r"analog end")
    text, at = _end_time(r)
    if not 0.0 < at < 2e-3:
        r._fail("interrupted at %s s, expected inside the run" % text)
    if r.wave().tlast() <= 0.0:
        r._fail("the engine's output holds no transient")


# -- driver ------------------------------------------------------------------------------

def select(names: Sequence[str]) -> List[CaseSpec]:
    if not names:
        return list(CASES)
    return [c for c in CASES if any(n in c.name for n in names)]


def run_one(env: Env, spec: CaseSpec, engine: str, keep_root: str,
            timeout: float = 120.0) -> Tuple[str, str, float]:
    """('PASS'|'FAIL'|'SKIP', detail, seconds) for one case on one engine."""
    why = env.engine_problem(engine)
    if why:
        return "SKIP", why, 0.0
    if spec.needs_finish and engine == "xyce" and env.xyce_shim:
        return "SKIP", "needs the finish protocol (not through --xyce-shim)", 0.0
    rundir = os.path.join(keep_root, "%s.%s" % (spec.name, engine))
    shutil.rmtree(rundir, ignore_errors=True)
    os.makedirs(rundir)
    t0 = time.time()
    try:
        spec.fn(Ctx(env, engine, rundir, timeout))
    except Skip as e:
        return "SKIP", str(e), time.time() - t0
    except Failure as e:
        return "FAIL", str(e), time.time() - t0
    return "PASS", "", time.time() - t0


def default_xyce_libs() -> List[str]:
    env = os.environ.get("VAMOS_XYCE_LIBS")
    if env:
        return [d for d in env.split(os.pathsep) if d]
    return [os.path.expanduser("~/xyce-libs"), "/usr/local/src/xyce-build/utils/XyceCInterface",
            "/usr/local/src/xyce-build/src"]


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--nvc-build", default=os.environ.get("NVCB", "/usr/local/src/nvc-build"))
    ap.add_argument("--vacask-build", default=os.environ.get("VCB", "/opt/build.VACASK/Release"))
    ap.add_argument("--xyce-libs", default=":".join(default_xyce_libs()))
    ap.add_argument("--engines", default=",".join(ENGINES))
    ap.add_argument("--xyce-shim", action="store_true")
    ap.add_argument("--old-bridge")
    ap.add_argument("--old-vacask")
    ap.add_argument("-k", action="append", default=[], help="run the cases whose name contains this")
    ap.add_argument("--keep", action="store_true", help="keep the scratch directory")
    ap.add_argument("--scratch", help="scratch root (default: a new temporary directory)")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)

    cases = select(a.k)
    if a.list:
        for c in cases:
            print("%-28s %-12s %s" % (c.name, ",".join(c.engines), c.doc))
        return 0
    engines = [e for e in a.engines.split(",") if e]
    scratch = a.scratch or tempfile.mkdtemp(prefix="cside-")
    os.makedirs(scratch, exist_ok=True)
    env = Env(a.nvc_build, a.vacask_build, a.xyce_libs.split(":"), scratch, a.xyce_shim,
              a.old_bridge, a.old_vacask)
    why = env.suite_problem()
    if why:
        print("SKIP all: %s" % why)
        return 0
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for c in cases:
        for e in engines:
            if e not in c.engines:
                continue
            st, detail, secs = run_one(env, c, e, os.path.join(scratch, "runs"), a.timeout)
            counts[st] += 1
            print("%s %-28s %-7s %5.2f s%s" % (st, c.name, e, secs,
                                               ("  " + detail.replace("\n", "\n    ")) if detail else ""))
            sys.stdout.flush()
    print("%d passed, %d failed, %d skipped  (scratch %s%s)"
          % (counts["PASS"], counts["FAIL"], counts["SKIP"], scratch, "" if a.keep else ", removed"))
    if not a.keep:
        shutil.rmtree(scratch, ignore_errors=True)
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
