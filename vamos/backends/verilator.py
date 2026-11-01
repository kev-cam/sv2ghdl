"""The Verilator engine: `--vamos-engine=verilator` (or VAMOS_ENGINE=verilator) runs a vendor
command line's design on Verilator instead of sv2ghdl + nvc.

Verilator is a compiler, so the two stages are one: `verilator --binary --timing -O3` builds
<daidir>/verilator/V<top> from the Job's Verilog/SystemVerilog sources, defines, include and
library options and timescale; a run executes that binary with the plusargs.  What the engine
cannot do is said at the point of use: VHDL sources (Verilator reads none), several top-level
modules (one --top-module), a stop time given at run time (+vcs+finish+N, xsim's `run N ns`:
a Verilator binary has no such option; the design ends at its own $finish), and nvc-only
runtime features (the AMS co-simulation, the VCD request options).  The real verilator is
found as every tool is (VAMOS_VERILATOR, then PATH, then the dev build area).
"""

import os
import subprocess
from typing import Callable, List, Optional, Tuple

from vamos import tools
from vamos.backends.nvc import BackendError, cpu_time
from vamos.job import Job

ENGINES = ("nvc", "verilator")
WORK_SUBDIR = "verilator"


def engine_of(opts: dict) -> str:
    """The engine an invocation asked for: --vamos-engine, else VAMOS_ENGINE, else nvc.
    BackendError for a value that is not an engine (the option's value is checked by
    check_vamos_opts; the variable is checked here)."""
    val = opts.get("engine") or os.environ.get("VAMOS_ENGINE", "") or "nvc"
    val = str(val).lower()
    if val not in ENGINES:
        raise BackendError("VAMOS_ENGINE=%s: the engines are %s" % (val, ", ".join(ENGINES)))
    return val


class VerilatorBackend:
    def __init__(self, job: Job, emit: Callable[[str], None]):
        self.job = job
        self.emit = emit
        v = tools.find_real("verilator")
        if not v:
            raise BackendError("cannot find verilator (set VAMOS_VERILATOR, or put verilator on PATH)")
        self.verilator = os.path.abspath(v)
        self.workdir = os.path.join(job.daidir, WORK_SUBDIR)

    def compile_tools(self) -> List[Tuple[str, str]]:
        return [("verilator", self.verilator)]

    def run_tools(self) -> List[Tuple[str, str]]:
        return [("verilator", self.verilator)]

    def exe(self, top: Optional[str] = None) -> str:
        return os.path.join(self.workdir, "vamos_sim")

    def check(self) -> Optional[str]:
        """Why this Job cannot run on Verilator, or None."""
        job = self.job
        if any(s.lang == "vhdl" for s in job.sources):
            return "the Verilator engine reads no VHDL (use the nvc engine)"
        if len(job.tops) > 1:
            return ("the Verilator engine builds one top-level module (--top-module); %d were "
                    "given: %s" % (len(job.tops), ", ".join(job.tops)))
        return None

    def command(self, top: str) -> List[str]:
        job = self.job
        cmd = [self.verilator, "--binary", "--timing", "-O3", "-Wno-fatal",
               "-j", str(max(1, min(8, os.cpu_count() or 1))),
               "--Mdir", self.workdir, "-o", "vamos_sim"]
        if top:                                  # else verilator picks the top itself
            cmd += ["--top-module", top]
        if job.timescale:
            cmd += ["--timescale", job.timescale]
        for k, v in job.defines.items():
            cmd.append("-D%s=%s" % (k, v) if v is not None else "-D" + k)
        for d in job.incdirs:
            cmd.append("-I" + d)
        for d in job.lib_dirs:
            cmd += ["-y", d]
        for f in job.lib_files:
            cmd += ["-v", f]
        if job.lib_exts:
            cmd.append("+libext" + "".join("+" + e for e in job.lib_exts))
        cmd += [s.path for s in job.sources]
        return cmd

    def build(self, top: Optional[str]) -> float:
        """Build V<top>; returns the cpu time when the build finished.  BackendError on
        failure, after the compiler's output has been printed."""
        why = self.check()
        if why:
            raise BackendError(why)
        os.makedirs(self.workdir, exist_ok=True)
        rc = self._call(self.command(top), cwd=self.job.cwd)
        if rc != 0:
            raise BackendError("verilator failed (exit %d)" % rc)
        if not os.access(self.exe(), os.X_OK):
            raise BackendError("verilator produced no %s" % self.exe())
        return cpu_time()

    def run(self, plusargs: List[str], out: Callable[[str], None],
            err: Callable[[str], None]) -> int:
        """Run V<top> with the plusargs, in the current directory (relative file names in the
        testbench resolve there, as under the vendor tool).  Returns the exit status."""
        exe = self.exe()
        if not os.access(exe, os.X_OK):
            raise BackendError("%s is missing; compile again" % exe)
        cmd = [exe] + list(plusargs)
        if os.environ.get("VAMOS_VERBOSE"):
            out("vamos: + " + " ".join(cmd))
        try:
            with subprocess.Popen(cmd, cwd=os.getcwd(), env=tools.child_env(), stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  universal_newlines=True, errors="replace") as proc:
                assert proc.stdout is not None
                for line in proc.stdout:
                    out(line.rstrip("\n"))
                return proc.wait()
        except OSError as e:
            raise BackendError("cannot run %s: %s" % (exe, e))

    def _call(self, cmd: List[str], cwd: str) -> int:
        if os.environ.get("VAMOS_VERBOSE"):
            self.emit("vamos: + " + " ".join(cmd))
        try:
            with subprocess.Popen(cmd, cwd=cwd, env=tools.child_env(), stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  universal_newlines=True, errors="replace") as proc:
                assert proc.stdout is not None
                for line in proc.stdout:
                    self.emit(line.rstrip("\n"))
                return proc.wait()
        except OSError as e:
            raise BackendError("cannot run %s: %s" % (cmd[0], e))
