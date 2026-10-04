#!/usr/bin/env python3
"""Xyce-side tests of the co-simulation engine patches (docs/VAMOS_AMS_DESIGN.md §7 P1, P5, ABI).

The nvc side, and both engines end to end, are covered by the nvc-side runner
(../cside_engine/run_cside.py), which goes with the patches in nvc,
libcosim_bridge and VACASK; this one goes with the Xyce patches.  It covers
what the Xyce patches do on their own, and what only Xyce has:

- standalone Xyce (no nvc) with stepstub.cpp, a code: URI library that answers
  the candidate-step protocol the way nvc's stepper does: the transient loop's
  finish (accept the step, end the transient there, finish the output), the
  answer combined over several libraries, the meaning of other answers, and
  BindCB's handling of an init function that returns no callback (P5);
- the ABI handshake: libxycecinterface.so exports xyce_cosim_abi() = 2; the
  interface loaded with a Xyce library that predates the patches answers 1,
  and nvc refuses it, as it refuses the pre-patch interface;
- through nvc: the rawfile after a finish (its No. Points: filled) and after a
  pause (left blank, as §6 expects), Xyce's own finish line, and the stock
  Xyce demos identical to the pre-patch Xyce.

    python3 run_cside_xyce.py [--xyce BIN] [--xyce-libs D1:D2:D3] [--nvc-build DIR]
                              [--old-xyce-lib LIB] [--old-xyce-ci LIB] [-k SUBSTR]...
                              [--keep] [--scratch DIR] [--timeout S] [--list]

Linux (WSL) only: Xyce and nvc are Linux binaries, and the stub library and
the ABI probe are built with c++/cc.  Each case runs in its own directory under
a scratch root (kept with --keep).  The exit status is 0 when no selected case
fails; a case whose tool or input is missing is skipped with the reason.

--old-xyce-lib / --old-xyce-ci name the libxyce.so and libxycecinterface.so of
a Xyce without the patches, for the cases that compare with it or check that
it is refused; they default to $VAMOS_CSIDE_XYCE_OLD_LIB / _OLD_CI, else to
copies of the unpatched libraries saved in the Xyce build tree before the Xyce
patches were built into it (src/libxyce.so.pre-e2,
utils/XyceCInterface/libxycecinterface.so.pre-e2, as those copies are named on
disk), when present ("" = none).
Other defaults: $VAMOS_XYCE, $VAMOS_XYCE_LIBS (as in vamos_testlib), $NVCB.
The unittest wrapper is tests/vamos/test_cside_xyce.py.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
NVC_RUNNER = os.path.join(os.path.dirname(HERE), "cside_engine", "run_cside.py")
XYCE_BUILD = "/usr/local/src/xyce-build"
OLD_LIB_DEFAULT = os.path.join(XYCE_BUILD, "src", "libxyce.so.pre-e2")
OLD_CI_DEFAULT = os.path.join(XYCE_BUILD, "utils", "XyceCInterface", "libxycecinterface.so.pre-e2")
DEMOS = ("min", "a2d", "glitch")

# Xyce's line when a co-simulation finish ends the transient (N_ANP_Transient.C)
FINISH_RE = re.compile(r"^Co-simulation finish at (\S+) s\.  Exiting transient loop$", re.M)


class Failure(Exception):
    """A check failed."""


class Skip(Exception):
    """The case cannot run here (missing tool, library or input)."""


def _load_nvc_runner():
    """The nvc-side runner as a module (its Env/Ctx run nvc), or None when it is
    missing."""
    if not os.path.isfile(NVC_RUNNER):
        return None
    saved, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec = importlib.util.spec_from_file_location("vamos_run_cside_for_xyce", NVC_RUNNER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = saved
    return mod


NVC_SIDE = _load_nvc_runner()
FAILURES = (Failure,) + ((NVC_SIDE.Failure,) if NVC_SIDE else ())
SKIPS = (Skip,) + ((NVC_SIDE.Skip,) if NVC_SIDE else ())


# -- rawfiles and stub logs ---------------------------------------------------------

class Raw(object):
    """A real SPICE rawfile: the variables, the rows, and the No. Points: field as
    written (blank when Xyce ended paused)."""

    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as fh:
            data = fh.read()
        pos, binary = data.find(b"Binary:\n"), True
        if pos < 0:
            pos, binary = data.find(b"Values:\n"), False
        if pos < 0:
            raise Failure("%s: no Binary:/Values: section" % path)
        header = data[:pos].decode("latin-1").splitlines()
        nvars, self.points_field, self.names = 0, None, []  # type: int, Optional[str], List[str]
        for i, line in enumerate(header):
            key, _, val = line.partition(":")
            key = key.strip().lower()
            if key == "flags" and "complex" in val.lower():
                raise Failure("%s: complex data not supported" % path)
            if key == "no. variables":
                nvars = int(val)
            elif key == "no. points":
                self.points_field = val.strip()
            elif key == "variables":
                self.names = [v.split()[1].lower() for v in header[i + 1:i + 1 + nvars]]
        if nvars <= 0 or len(self.names) != nvars:
            raise Failure("%s: bad variable list" % path)
        body = data[pos + 8:]
        if binary:
            n = len(body) // (8 * nvars)
            vals = struct.unpack("<%dd" % (n * nvars), body[:n * nvars * 8])
            self.rows = [list(vals[p * nvars:(p + 1) * nvars]) for p in range(n)]
        else:
            toks, self.rows, k = body.decode("latin-1").split(), [], 0
            while k + 1 + nvars <= len(toks):
                self.rows.append([float(x.split(",")[0]) for x in toks[k + 1:k + 1 + nvars]])
                k += 1 + nvars

    def col(self, name: str) -> List[float]:
        k = name.lower()
        if k not in self.names:
            raise Failure("%s has no column %r (has %s)" % (self.path, name, ", ".join(self.names)))
        i = self.names.index(k)
        return [r[i] for r in self.rows]

    def times(self) -> List[float]:
        return self.col("time")

    def tlast(self) -> float:
        if not self.rows:
            raise Failure("%s holds no points" % self.path)
        return self.times()[-1]

    def at(self, name: str, t: float) -> float:
        """The value at t, linear between points."""
        ts, vs = self.times(), self.col(name)
        if not ts:
            raise Failure("%s holds no points" % self.path)
        if t <= ts[0]:
            return vs[0]
        for i in range(1, len(ts)):
            if ts[i] >= t:
                if ts[i] == ts[i - 1]:
                    return vs[i]
                return vs[i - 1] + (vs[i] - vs[i - 1]) * (t - ts[i - 1]) / (ts[i] - ts[i - 1])
        return vs[-1]

    def expect_points_filled(self) -> "Raw":
        """The No. Points: field holds the number of points (the output was finished)."""
        if self.points_field != str(len(self.rows)):
            raise Failure("%s: No. Points: is %r, the file holds %d points (output not finished?)"
                          % (self.path, self.points_field, len(self.rows)))
        return self

    def expect_tlast(self, t: float, after: float = 0.0) -> "Raw":
        got = self.tlast()
        tol = 1e-9 * abs(t) + 1e-21
        if not (abs(got - t) <= tol or t < got <= t + after):
            raise Failure("%s ends at %.12g s, expected %.12g s%s"
                          % (self.path, got, t, " (+ up to %.3g s)" % after if after else ""))
        return self


def read_log(path: str) -> List[Tuple[float, int, float]]:
    """A stepstub log: (t, answer, tEvt) per candidate step Xyce offered."""
    if not os.path.isfile(path):
        return []
    out = []
    with open(path) as fh:
        for ln in fh:
            f = ln.split()
            if len(f) == 3:
                out.append((float(f[0]), int(f[1]), float(f[2])))
    return out


def rc_deck(title: str, stubs: Sequence[Tuple[str, str]] = (), extra: Sequence[str] = (),
            stop: str = "1u") -> str:
    """An RC driven by a pulse (R 1k, C 100p: tau 100 ns), plus a 0 A stub source on
    nout per (tag, library)."""
    lines = ["* cside_xyce: " + title,
             "V1 nin 0 PULSE(0 1 10n 1n 1n 40n 100n)",
             "R1 nin nout 1k",
             "C1 nout 0 100p"]
    for tag, lib in stubs:
        lines.append('I%s nout 0 PWL FILE "code:%s:stub_init:a2d:%s"' % (tag, lib, tag.lower()))
    lines += list(extra)
    lines += [".tran 1n %s" % stop, ".print tran format=raw file=out.raw V(nin) V(nout)", ".end"]
    return "\n".join(lines) + "\n"


def rc_value(t: float) -> float:
    """V(nout) of rc_deck during the first pulse (input edge at 10..11 ns, taken at
    10.5 ns; good to ~1e-4)."""
    import math
    return 0.0 if t < 10.5e-9 else 1.0 - math.exp(-(t - 10.5e-9) / 100e-9)


# -- environment ---------------------------------------------------------------------

def _symbols(lib: str, kinds: str = "T") -> Optional[List[str]]:
    """Dynamic symbols of a library whose nm type letter is in kinds (T: defined
    code, w/v: weak undefined), or None when nm is unavailable."""
    try:
        p = subprocess.run(["nm", "-D", lib], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           universal_newlines=True)
    except OSError:
        return None
    if p.returncode != 0:
        return None
    out = []
    for ln in p.stdout.splitlines():
        f = ln.split()
        if len(f) >= 2 and f[-2] in kinds:
            out.append(f[-1])
    return out


def default_xyce_libs() -> List[str]:
    env = os.environ.get("VAMOS_XYCE_LIBS")
    if env:
        return [d for d in env.split(os.pathsep) if d]
    return [os.path.expanduser("~/xyce-libs"), os.path.join(XYCE_BUILD, "utils", "XyceCInterface"),
            os.path.join(XYCE_BUILD, "src")]


def default_xyce() -> str:
    for c in (os.environ.get("VAMOS_XYCE", ""), os.path.join(XYCE_BUILD, "src", "Xyce")):
        if c and os.access(c, os.X_OK):
            return c
    return ""


def _old_default(var: str, path: str) -> Optional[str]:
    v = os.environ.get(var)
    if v is not None:
        return v or None
    return path if os.path.isfile(path) else None


class Env(object):
    """Where Xyce and nvc are, the pre-patch libraries, and the built helpers."""

    def __init__(self, xyce: str, xyce_libs: Sequence[str], nvcb: str, scratch: str,
                 old_lib: Optional[str] = None, old_ci: Optional[str] = None):
        self.xyce = xyce
        self.xyce_libs = [d for d in xyce_libs if d]
        self.nvcb = nvcb
        self.scratch = scratch
        self.old_lib = old_lib
        self.old_ci = old_ci
        self._built = {}   # type: Dict[str, str]
        self._nvc_envs = {}   # type: Dict[str, object]
        self._ref = None   # type: Optional[Raw]

    # -- availability --

    def suite_problem(self) -> Optional[str]:
        if not sys.platform.startswith("linux"):
            return "needs Linux (WSL): Xyce and nvc are Linux binaries"
        if not self.xyce or not os.access(self.xyce, os.X_OK):
            return "no Xyce binary (set VAMOS_XYCE or --xyce)"
        for cc in ("cc", "c++"):
            if shutil.which(cc) is None:
                return "no %s for the stub library and the ABI probe" % cc
        return None

    def cinterface(self) -> Optional[str]:
        for d in self.xyce_libs:
            p = os.path.join(d, "libxycecinterface.so")
            if os.path.isfile(p):
                return p
        return None

    def lib(self) -> Optional[str]:
        for d in self.xyce_libs:
            p = os.path.join(d, "libxyce.so")
            if os.path.isfile(p):
                return p
        return None

    def ld_path(self, first: Sequence[str] = ()) -> str:
        old = os.environ.get("LD_LIBRARY_PATH")
        return ":".join(list(first) + self.xyce_libs + ([old] if old else []))

    def need_old(self, which: str) -> str:
        """The directory holding the pre-patch libxyce.so ('lib') or
        libxycecinterface.so ('ci') under its real name; Skip when not given."""
        src = self.old_lib if which == "lib" else self.old_ci
        name = "libxyce.so" if which == "lib" else "libxycecinterface.so"
        if not src:
            raise Skip("no pre-patch %s (--old-xyce-%s)" % (name, which))
        if not os.path.isfile(src):
            raise Skip("missing pre-patch %s %s" % (name, src))
        key = "old_" + which
        if key not in self._built:
            d = os.path.join(self.scratch, "old", which)
            os.makedirs(d, exist_ok=True)
            dst = os.path.join(d, name)
            if not os.path.lexists(dst):
                os.symlink(os.path.abspath(src), dst)
            self._built[key] = d
        return self._built[key]

    # -- builds --

    def _run(self, cmd: List[str], what: str) -> None:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, errors="replace")
        if p.returncode != 0:
            raise Failure("%s failed (rc %d): %s\n%s" % (what, p.returncode, " ".join(cmd),
                                                          p.stdout[-3000:]))

    def stub(self, tag: str) -> str:
        """libstepstub_<tag>.so (built once); its absolute path."""
        key = "stub_" + tag
        if key not in self._built:
            d = os.path.join(self.scratch, "stubs")
            os.makedirs(d, exist_ok=True)
            out = os.path.join(d, "libstepstub_%s.so" % tag)
            self._run(["c++", "-O2", "-shared", "-fPIC", "-DSTUB_TAG=%s" % tag, "-o", out,
                       os.path.join(HERE, "stepstub.cpp")], "c++ stepstub.cpp")
            self._built[key] = out
        return self._built[key]

    def probe(self) -> str:
        if "probe" not in self._built:
            d = os.path.join(self.scratch, "stubs")
            os.makedirs(d, exist_ok=True)
            out = os.path.join(d, "abi_probe")
            self._run(["cc", "-O2", "-o", out, os.path.join(HERE, "abi_probe.c"), "-ldl"],
                      "cc abi_probe.c")
            self._built["probe"] = out
        return self._built["probe"]

    def abi_value(self, ci: str, first: Sequence[str] = ()) -> str:
        """What abi_probe prints for this interface (first: library dirs ahead)."""
        env = dict(os.environ)
        env["LD_LIBRARY_PATH"] = self.ld_path(first)
        p = subprocess.run([self.probe(), ci], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, errors="replace",
                           timeout=120)
        return p.stdout.strip()

    # -- the nvc-side runner, for the nvc cases --

    def nvc_env(self, old: bool = False):
        """The nvc-side runner's Env on this Xyce, or (old=True) on the pre-patch Xyce
        through that runner's xyce_abi_shim (which supplies the xyce_cosim_abi it
        lacks)."""
        if NVC_SIDE is None:
            raise Skip("needs the nvc-side runner %s" % NVC_RUNNER)
        key = "old" if old else "new"
        if key not in self._nvc_envs:
            libs = list(self.xyce_libs)
            if old:
                libs = [self.need_old("ci"), self.need_old("lib")] + libs
            env = NVC_SIDE.Env(self.nvcb, os.environ.get("VCB", "/opt/build.VACASK/Release"), libs,
                               os.path.join(self.scratch, "nvc_" + key), xyce_shim=old)
            why = env.suite_problem() or env.engine_problem("xyce")
            if why:
                raise Skip(why)
            self._nvc_envs[key] = env
        return self._nvc_envs[key]


# -- one standalone Xyce run ---------------------------------------------------------

class Run(object):
    """A standalone Xyce run and the checks on it."""

    def __init__(self, ctx: "Ctx", rc: int, out: str, cmd: List[str]):
        self.ctx = ctx
        self.rc = rc
        self.out = out
        self.cmd = cmd

    def fail(self, msg: str) -> None:
        raise Failure("%s\n  command: %s (in %s)\n  rc %d; output tail:\n%s"
                      % (msg, " ".join(self.cmd), self.ctx.rundir, self.rc, self.out[-2500:]))

    def expect_rc(self, rc) -> "Run":
        if rc == "nonzero":
            ok = self.rc > 0
        else:
            ok = self.rc == rc
        if not ok:
            self.fail("exit status %d, expected %s" % (self.rc, rc))
        return self

    def expect(self, *patterns: str) -> "Run":
        for p in patterns:
            if not re.search(p, self.out, re.M):
                self.fail("no line matching %r" % p)
        return self

    def expect_text(self, *patterns: str) -> "Run":
        """Like expect, on the output with every run of white space made one blank
        (Xyce wraps its messages)."""
        flat = " ".join(self.out.split())
        for p in patterns:
            if not re.search(p, flat):
                self.fail("no text matching %r" % p)
        return self

    def forbid(self, *patterns: str) -> "Run":
        for p in patterns:
            m = re.search(p, self.out, re.M)
            if m:
                self.fail("unexpected line matching %r: %r" % (p, m.group(0)))
        return self

    def finish_time(self) -> Optional[float]:
        """The time of Xyce's co-simulation finish line (None: no such line);
        more than one line fails."""
        ms = FINISH_RE.findall(self.out)
        if len(ms) > 1:
            self.fail("%d co-simulation finish lines" % len(ms))
        return float(ms[0]) if ms else None

    def raw(self) -> Raw:
        p = self.ctx.path("out.raw")
        if not os.path.isfile(p):
            self.fail("no rawfile %s" % p)
        return Raw(p)


class Ctx(object):
    """One case: its run directory and helpers."""

    def __init__(self, env: Env, rundir: str, timeout: float):
        self.env = env
        self.rundir = rundir
        self.timeout = timeout

    def path(self, name: str) -> str:
        return os.path.join(self.rundir, name)

    def log(self, tag: str) -> List[Tuple[float, int, float]]:
        return read_log(self.path("stub_%s.log" % tag))

    def xyce(self, deck: str, stub_env: Optional[Dict[str, str]] = None,
             first: Sequence[str] = (), name: str = "deck.cir") -> Run:
        """Run Xyce standalone on this deck text in the run directory; stub_env sets
        the stubs' STEPSTUB_<TAG>_* variables (each tag's LOG goes to stub_<tag>.log)."""
        p = self.path(name)
        with open(p, "w") as fh:
            fh.write(deck)
        env = dict(os.environ)
        env["LD_LIBRARY_PATH"] = self.env.ld_path(first)
        for k in list(env):
            if k.startswith("STEPSTUB_"):
                del env[k]
        for k, v in (stub_env or {}).items():
            env[k] = v
            m = re.match(r"^STEPSTUB_([A-Za-z0-9]+)_MODE$", k)
            if m:
                env["STEPSTUB_%s_LOG" % m.group(1)] = self.path("stub_%s.log" % m.group(1))
        cmd = [self.env.xyce, p]
        try:
            r = subprocess.run(cmd, cwd=self.rundir, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, universal_newlines=True, errors="replace",
                               timeout=self.timeout)
            rc, out = r.returncode, r.stdout
        except subprocess.TimeoutExpired as e:
            out = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode("latin-1")
            raise Failure("hung: killed after %.0f s\n  command: %s\n%s"
                          % (self.timeout, " ".join(cmd), out[-2000:]))
        with open(self.path("xyce.log"), "w") as fh:
            fh.write(out)
        return Run(self, rc, out, cmd)

    def reference(self) -> Raw:
        """rc_deck without stubs on the current Xyce (one run per Env)."""
        if self.env._ref is None:
            d = os.path.join(self.env.scratch, "reference")
            os.makedirs(d, exist_ok=True)
            r = Ctx(self.env, d, self.timeout).xyce(rc_deck("reference"))
            r.expect_rc(0)
            self.env._ref = r.raw()
        return self.env._ref

    def nvc_ctx(self, old: bool = False):
        """A Ctx of the nvc-side runner on Xyce in a subdirectory of this case's
        directory."""
        env = self.env.nvc_env(old)
        d = self.path("old" if old else "new")
        os.makedirs(d, exist_ok=True)
        return NVC_SIDE.Ctx(env, "xyce", d, self.timeout)


# -- the cases ---------------------------------------------------------------------------

class CaseSpec(object):
    def __init__(self, name: str, fn: Callable[[Ctx], None], doc: str):
        self.name = name
        self.fn = fn
        self.doc = doc


CASES = []  # type: List[CaseSpec]


def case(name: str):
    def deco(fn):
        CASES.append(CaseSpec(name, fn, " ".join((fn.__doc__ or "").split())))
        return fn
    return deco


def _near(what: str, got: float, want: float, tol: float) -> None:
    if abs(got - want) > tol:
        raise Failure("%s is %.9g, expected %.9g +- %.3g" % (what, got, want, tol))


def _runner_fail(res, msg: str) -> None:
    """Fail on a Result of the nvc-side runner (its public cmd/rc/out)."""
    raise Failure("%s\n  command: %s\n  rc %d; output tail:\n%s"
                  % (msg, " ".join(res.cmd), res.rc, res.out[-2500:]))


def _finish_at(r: Run, t: float) -> None:
    got = r.finish_time()
    if got is None:
        r.fail("no 'Co-simulation finish at' line")
    if abs(got - t) > 1e-5 * abs(t) + 1e-21:   # printed with 6 digits
        r.fail("Xyce finished at %.6g s, expected %.6g s" % (got, t))


@case("stub_finish_mid")
def _stub_finish_mid(t):
    """A digital stop at 123.456 ns (stub in 'stop' mode): the step past it is
    vetoed to it, the step that lands on it is answered finish; Xyce accepts that
    step and ends the transient there with rc 0, offers no further step, prints
    its finish line and finishes the rawfile (No. Points: filled); the values up
    to the stop match the run without the stub."""
    tf = 123.456e-9
    r = t.xyce(rc_deck("finish at 123.456 ns", [("A", t.env.stub("A"))]),
               {"STEPSTUB_A_MODE": "stop", "STEPSTUB_A_STOP": repr(tf)})
    r.expect_rc(0)
    _finish_at(r, tf)
    raw = r.raw().expect_points_filled().expect_tlast(tf)
    log = t.log("A")
    vetoes = [x for x in log if x[1] == 1]
    if len(vetoes) != 1 or abs(vetoes[0][2] - tf) > 1e-21:
        r.fail("expected one veto to %.9g s, the stub answered %r" % (tf, vetoes))
    if not log or log[-1][1] != 2 or abs(log[-1][0] - tf) > 1e-9 * tf:
        r.fail("the last step offered is %r, expected the finish at %.9g s" % (log[-1:], tf))
    if any(x[1] == 2 for x in log[:-1]):
        r.fail("Xyce offered further steps after a finish: %r" % [x for x in log if x[1] == 2])
    ref = t.reference()
    for at in (51e-9, 100e-9):
        _near("V(nout) at %.3g s" % at, raw.at("v(nout)", at), ref.at("v(nout)", at), 1e-3)
    _near("V(nout) at the stop", raw.col("v(nout)")[-1], ref.at("v(nout)", tf), 1e-3)


@case("stub_finish_first_step")
def _stub_finish_first(t):
    """A digital stopped at t=0 answers finish to the very first step: the run
    ends there (rc 0) with two points, t=0 and that step, and the rawfile
    finished."""
    r = t.xyce(rc_deck("finish on the first step", [("A", t.env.stub("A"))]),
               {"STEPSTUB_A_MODE": "stop", "STEPSTUB_A_STOP": "0"})
    r.expect_rc(0)
    log = t.log("A")
    if len(log) != 1 or log[0][1] != 2:
        r.fail("expected one offer answered finish, the stub logged %r" % log)
    _finish_at(r, log[0][0])
    raw = r.raw().expect_points_filled().expect_tlast(log[0][0])
    if len(raw.rows) != 2 or raw.times()[0] != 0.0:
        r.fail("expected the points t=0 and %.9g s, the rawfile has %r" % (log[0][0], raw.times()))


@case("stub_veto_beats_finish")
def _stub_veto_beats_finish(t):
    """Two libraries: A answers finish to every step (stopped at 0), B vetoes the
    first step to its midpoint.  The veto wins (the step is redone to B's time),
    then A's finish ends the run there; both libraries see every offer."""
    r = t.xyce(rc_deck("veto and finish from two libraries",
                       [("A", t.env.stub("A")), ("B", t.env.stub("B"))]),
               {"STEPSTUB_A_MODE": "stop", "STEPSTUB_A_STOP": "0", "STEPSTUB_B_MODE": "vetofirst"})
    r.expect_rc(0)
    la, lb = t.log("A"), t.log("B")
    if len(lb) != 2 or lb[0][1] != 1 or lb[1][1] != 0:
        r.fail("library B answered %r, expected a veto then an accept" % lb)
    te = lb[0][2]
    if len(la) != 2 or [x[1] for x in la] != [2, 2] or [x[0] for x in la] != [x[0] for x in lb]:
        r.fail("library A answered %r, expected finish to the same two offers as B %r" % (la, lb))
    if abs(lb[1][0] - te) > 1e-9 * te:
        r.fail("the redone step ends at %.12g s, B vetoed to %.12g s" % (lb[1][0], te))
    _finish_at(r, te)
    r.raw().expect_points_filled().expect_tlast(te)


@case("stub_other_answers")
def _stub_other_answers(t):
    """Answers other than 0/1/2 keep their old meaning: 3 with tEvt >= 0 is a veto
    (the step lands on tEvt = 47.123 ns), 1 with tEvt < 0 is an accept; nothing is
    read as a finish and the run reaches the deck stop."""
    at = 47.123e-9
    r = t.xyce(rc_deck("answers 3 and 1/-1", [("A", t.env.stub("A"))]),
               {"STEPSTUB_A_MODE": "odd", "STEPSTUB_A_AT": repr(at)})
    r.expect_rc(0)
    if r.finish_time() is not None:
        r.fail("an answer other than 2 was read as a finish")
    raw = r.raw().expect_points_filled().expect_tlast(1e-6)
    if min(abs(x - at) for x in raw.times()) > 1e-9 * at:
        r.fail("no point at the vetoed time %.9g s" % at)
    log = t.log("A")
    odd = [i for i, x in enumerate(log) if x[1] == 3]
    if len(odd) != 1 or odd[0] + 1 >= len(log) or abs(log[odd[0] + 1][0] - at) > 1e-9 * at:
        r.fail("expected one answer 3, then an offer at %.9g s; the stub logged %r"
               % (at, log[max(0, (odd or [0])[0] - 1):(odd or [0])[0] + 3]))
    if any(x[1] != 1 for x in log[odd[0] + 1:]):
        r.fail("the stub answered other than 1 after its veto")


@case("bindcb_null_callback")
def _bindcb_null(t):
    """P5: an init function that returns no callback is a fatal input error that
    names the URI ('returned no callback') and fails the source ('Failed to
    connect URI'): a clean non-zero exit, never a call through NULL."""
    lib = t.env.stub("A")
    r = t.xyce(rc_deck("init returns NULL",
                       extra=['V2 nx 0 PWL FILE "code:%s:null_init:d2a:x"' % lib, "R2 nx 0 1k"]))
    r.expect_rc("nonzero")
    r.expect_text(r"Netlist error: code: URI function null_init\(\) in \S*libstepstub_A\.so "
                  r"returned no callback for 'd2a:x': the source is not connected",
                  r"Failed to connect URI V2", r"\*\*\* Xyce Abort \*\*\*")
    r.forbid(r"Segmentation", r"Caught signal", r"SIGSEGV", r"core dumped")


@case("bindcb_missing_function")
def _bindcb_missing(t):
    """An init function the library does not export: the source fails to
    connect, a clean non-zero exit (the path BindCB already had)."""
    lib = t.env.stub("A")
    r = t.xyce(rc_deck("no such init function",
                       extra=['V2 nx 0 PWL FILE "code:%s:no_such_init:d2a:x"' % lib, "R2 nx 0 1k"]))
    r.expect_rc("nonzero").expect(r"Failed to connect URI")
    r.forbid(r"Segmentation", r"Caught signal", r"SIGSEGV", r"core dumped")


@case("plain_deck_unchanged")
def _plain(t):
    """A deck without co-simulation sources runs as before: the RC response is
    right, the rawfile finished, and (with the pre-patch libxyce.so) every point
    is identical to the pre-patch Xyce."""
    r = t.xyce(rc_deck("plain"))
    r.expect_rc(0)
    if r.finish_time() is not None:
        r.fail("a co-simulation finish without any co-simulation source")
    raw = r.raw().expect_points_filled().expect_tlast(1e-6)
    for at in (30e-9, 51e-9):
        _near("V(nout) at %.3g s" % at, raw.at("v(nout)", at), rc_value(at), 2e-3)
    if not t.env.old_lib:
        return
    old_dir = t.env.need_old("lib")
    os.makedirs(t.path("old"), exist_ok=True)
    ro = Ctx(t.env, t.path("old"), t.timeout).xyce(rc_deck("plain"), first=[old_dir])
    ro.expect_rc(0)
    old = ro.raw()
    if len(old.rows) != len(raw.rows):
        r.fail("%d points, the pre-patch Xyce gives %d" % (len(raw.rows), len(old.rows)))
    for a, b in zip(raw.rows, old.rows):
        if any(abs(x - y) > 1e-15 * max(1.0, abs(y)) for x, y in zip(a, b)):
            r.fail("point %r differs from the pre-patch Xyce's %r" % (a, b))


@case("abi_symbols")
def _abi_symbols(t):
    """libxycecinterface.so exports xyce_cosim_abi and references the library's
    xyce_lib_cosim_abi weakly; libxyce.so exports xyce_lib_cosim_abi; the
    interface answers 2 (read as nvc reads it)."""
    ci, lib = t.env.cinterface(), t.env.lib()
    if not ci or not lib:
        raise Skip("no libxycecinterface.so / libxyce.so in %s" % ":".join(t.env.xyce_libs))
    defined, weak = _symbols(ci, "T"), _symbols(ci, "wv")
    if defined is None:
        raise Skip("no nm")
    if "xyce_cosim_abi" not in defined:
        raise Failure("%s does not export xyce_cosim_abi" % ci)
    if "xyce_lib_cosim_abi" not in (weak or []):
        raise Failure("%s does not reference xyce_lib_cosim_abi weakly (nm: %s)"
                      % (ci, "defined" if "xyce_lib_cosim_abi" in defined else "absent or strong"))
    if "xyce_lib_cosim_abi" not in (_symbols(lib, "T") or []):
        raise Failure("%s does not export xyce_lib_cosim_abi" % lib)
    got = t.env.abi_value(ci)
    if got != "xyce_cosim_abi=2":
        raise Failure("abi_probe %s says %r, expected xyce_cosim_abi=2" % (ci, got))


@case("abi_old_xyce_lib")
def _abi_old_lib(t):
    """The patched interface loaded with the pre-patch libxyce.so answers 1, and
    nvc refuses it before anything runs ('has xyce_cosim_abi() = 1, need 2')."""
    old_dir = t.env.need_old("lib")
    ci = t.env.cinterface()
    if not ci:
        raise Skip("no libxycecinterface.so")
    got = t.env.abi_value(ci, first=[old_dir])
    if got != "xyce_cosim_abi=1":
        raise Failure("abi_probe with the pre-patch libxyce.so says %r, expected xyce_cosim_abi=1"
                      % got)
    c = t.nvc_ctx()
    res = c.cosim("cs_edge", c.deck("rc"), c.fixture("q.boundary"), first=[old_dir])
    res.expect_rc(1).end_line(None)
    res.expect(r"^\*\* Fatal: co-simulation ABI mismatch: \S*libxycecinterface\.so has "
               r"xyce_cosim_abi\(\) = 1, need 2 or later; rebuild Xyce with the vamos cosim patches")


@case("abi_old_interface")
def _abi_old_ci(t):
    """nvc refuses the pre-patch libxycecinterface.so ('does not export
    xyce_cosim_abi()') before anything runs."""
    old_dir = t.env.need_old("ci")
    c = t.nvc_ctx()
    res = c.cosim("cs_edge", c.deck("rc"), c.fixture("q.boundary"), first=[old_dir])
    res.expect_rc(1).end_line(None)
    res.expect(r"^\*\* Fatal: co-simulation ABI mismatch: \S*libxycecinterface\.so does not "
               r"export xyce_cosim_abi\(\)")


@case("nvc_finish_rawfile")
def _nvc_finish_raw(t):
    """Through nvc, std.env.finish at 120 ns with the deck stop at 1 us: nvc's
    end line, Xyce's finish line, and a finished rawfile (No. Points: = the
    points written) ending at the stop (+ at most one step)."""
    c = t.nvc_ctx()
    res = c.cosim("cs_fin120", c.deck("rc"), c.fixture("q.boundary"))
    res.expect_rc(0).end_line(r"^\*\* Note: co-simulation finished: digital stop at 1\.2e-07 s$")
    m = FINISH_RE.findall(res.out)
    if len(m) != 1:
        _runner_fail(res, "expected one Xyce 'Co-simulation finish at' line, got %d" % len(m))
    tf = float(m[0])
    if not 1.2e-7 * (1 - 1e-5) <= tf <= 1.2e-7 + 2e-9:
        _runner_fail(res, "Xyce finished at %.6g s, expected 1.2e-07 s (+ up to 2 ns)" % tf)
    raw = Raw(c.path("xyce_tran.raw")).expect_points_filled()
    raw.expect_tlast(1.2e-7, after=2e-9)


@case("nvc_pause_rawfile")
def _nvc_pause_raw(t):
    """Through nvc, --stop-time before the deck stop: Xyce ends paused, so its
    rawfile reaches the stop time with No. Points: left blank (what §6's
    rawfile.fix_points repairs) or, if a later Xyce fills it, correct."""
    c = t.nvc_ctx()
    res = c.cosim("cs_edge", c.deck("rc"), c.fixture("q.boundary"), stop_time="100ns")
    res.expect_rc(0).end_line(r"analog end at 1e-07 s$")
    if FINISH_RE.search(res.out):
        _runner_fail(res, "a co-simulation finish at a pause")
    raw = Raw(c.path("xyce_tran.raw")).expect_tlast(1e-7)
    if raw.points_field not in ("", str(len(raw.rows))):
        _runner_fail(res, "No. Points: is %r with %d points" % (raw.points_field, len(raw.rows)))


@case("nvc_demos_vs_old")
def _nvc_demos(t):
    """The stock Xyce co-simulation demos (min, a2d, glitch) give exactly the same
    output on the patched Xyce as on the pre-patch Xyce (run through
    ../cside_engine's xyce_abi_shim): the patches change nothing until the digital
    stops."""
    if NVC_SIDE is None:
        raise Skip("needs the nvc-side runner %s" % NVC_RUNNER)
    t.env.need_old("lib")
    t.env.need_old("ci")
    for name in DEMOS:
        vhd = os.path.join(NVC_SIDE.VACASK_DEMO, "cosim_%s.vhd" % name)
        if not os.path.isfile(vhd):
            raise Skip("missing demo %s" % vhd)
        waves = []
        for old in (False, True):
            c = t.nvc_ctx(old)   # "new" / "old" subdirectories
            deck = c.fixture(name + ".cir", NVC_SIDE.XYCE_DEMO)
            bnd = c.fixture(name + ".boundary", NVC_SIDE.XYCE_DEMO)
            # the loader's trace shows which libxyce.so the old run really used
            res = c.cosim("cosim_" + name, deck, bnd, stop_time="200ns", vhd=[vhd], std="2040",
                          extra_env={"LD_DEBUG": "files"} if old else None)
            res.expect_rc(0).end_line(r"analog end at 2e-07 s$")
            if old and not re.search(r"calling init: %s/libxyce\.so$" % re.escape(t.env.need_old("lib")),
                                     res.out, re.M):
                _runner_fail(res, "the pre-patch run did not load %s/libxyce.so" % t.env.need_old("lib"))
            prn = c.path(name + ".cir.prn")
            if not os.path.isfile(prn):
                _runner_fail(res, "no %s" % prn)
            waves.append(NVC_SIDE.read_prn(prn))
        new, old = waves
        if new.names != old.names or len(new.rows) != len(old.rows):
            raise Failure("demo %s: %d points %s, the pre-patch Xyce gives %d points %s"
                          % (name, len(new.rows), new.names, len(old.rows), old.names))
        for a, b in zip(new.rows, old.rows):
            if any(abs(x - y) > 1e-12 * max(1.0, abs(y)) for x, y in zip(a, b)):
                raise Failure("demo %s: point %r differs from the pre-patch Xyce's %r" % (name, a, b))


# -- driver ------------------------------------------------------------------------------

def select(names: Sequence[str]) -> List[CaseSpec]:
    if not names:
        return list(CASES)
    return [c for c in CASES if any(n in c.name for n in names)]


def run_one(env: Env, spec: CaseSpec, keep_root: str, timeout: float = 120.0
            ) -> Tuple[str, str, float]:
    """('PASS'|'FAIL'|'SKIP', detail, seconds) for one case."""
    why = env.suite_problem()
    if why:
        return "SKIP", why, 0.0
    rundir = os.path.join(keep_root, spec.name)
    shutil.rmtree(rundir, ignore_errors=True)
    os.makedirs(rundir)
    t0 = time.time()
    try:
        spec.fn(Ctx(env, rundir, timeout))
    except SKIPS as e:
        return "SKIP", str(e), time.time() - t0
    except FAILURES as e:
        return "FAIL", str(e), time.time() - t0
    return "PASS", "", time.time() - t0


def make_env(scratch: str, xyce: Optional[str] = None, xyce_libs: Optional[Sequence[str]] = None,
             nvcb: Optional[str] = None, old_lib: Optional[str] = None,
             old_ci: Optional[str] = None) -> Env:
    """An Env with the documented defaults for whatever is not given."""
    return Env(xyce or default_xyce(), list(xyce_libs) if xyce_libs else default_xyce_libs(),
               nvcb or os.environ.get("NVCB", "/usr/local/src/nvc-build"), scratch,
               old_lib if old_lib is not None else _old_default("VAMOS_CSIDE_XYCE_OLD_LIB",
                                                                OLD_LIB_DEFAULT),
               old_ci if old_ci is not None else _old_default("VAMOS_CSIDE_XYCE_OLD_CI",
                                                              OLD_CI_DEFAULT))


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--xyce", default=None, help="the Xyce binary")
    ap.add_argument("--xyce-libs", default=None, help="Xyce library directories, ':'-separated")
    ap.add_argument("--nvc-build", default=None)
    ap.add_argument("--old-xyce-lib", default=None, help="a pre-patch libxyce.so ('' = none)")
    ap.add_argument("--old-xyce-ci", default=None, help="a pre-patch libxycecinterface.so ('' = none)")
    ap.add_argument("-k", action="append", default=[], help="run the cases whose name contains this")
    ap.add_argument("--keep", action="store_true", help="keep the scratch directory")
    ap.add_argument("--scratch", help="scratch root (default: a new temporary directory)")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)

    cases = select(a.k)
    if a.list:
        for c in cases:
            print("%-26s %s" % (c.name, c.doc))
        return 0
    scratch = a.scratch or tempfile.mkdtemp(prefix="cside-xyce-")
    os.makedirs(scratch, exist_ok=True)
    env = make_env(scratch, a.xyce, a.xyce_libs.split(":") if a.xyce_libs else None, a.nvc_build,
                   a.old_xyce_lib, a.old_xyce_ci)
    why = env.suite_problem()
    if why:
        print("SKIP all: %s" % why)
        return 0
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for c in cases:
        st, detail, secs = run_one(env, c, os.path.join(scratch, "runs"), a.timeout)
        counts[st] += 1
        print("%s %-26s %6.2f s%s" % (st, c.name, secs,
                                      ("  " + detail.replace("\n", "\n    ")) if detail else ""))
        sys.stdout.flush()
    print("%d passed, %d failed, %d skipped  (scratch %s%s)"
          % (counts["PASS"], counts["FAIL"], counts["SKIP"], scratch, "" if a.keep else ", removed"))
    if not a.keep:
        shutil.rmtree(scratch, ignore_errors=True)
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
