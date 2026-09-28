"""sv2ghdl -> nvc backend: analyse, elaborate, run.

Translation and analysis go through bin/iverilog-sv2ghdl, the same pipeline
the iverilog personality and `nvc -a x.v` use (sv-normalize, sv2vhdl-modules,
iverilog's VHDL backend, sv2ghdl.pl fallback).  Its output directory - VHDL,
nvc work library and _metadata - lives inside the job's daidir.
"""

import os
import re
import subprocess
from typing import Callable, List, Optional, Tuple

from vamos import tools
from vamos.job import Job

NVC_SUBDIR = "nvc"


class BackendError(Exception):
    pass


class NvcBackend:
    def __init__(self, job: Job, emit: Callable[[str], None]):
        self.job = job
        self.emit = emit
        self.nvc = tools.find_real("nvc")
        if not self.nvc:
            raise BackendError("cannot find nvc (set VAMOS_NVC, or put nvc on PATH)")
        self.libdir = tools.nvc_libdir(self.nvc)
        self.workdir = os.path.join(job.daidir, NVC_SUBDIR)

    # -- tools used, for the provenance header -------------------------------

    def compile_tools(self) -> List[Tuple[str, str]]:
        """[(tool, path)] this backend runs at compile time."""
        used = [("sv2ghdl", self._translator())]
        iv = tools.find_real("iverilog")
        if iv:
            used.append(("iverilog", iv))
        used.append(("nvc", self.nvc))
        return used

    def run_tools(self) -> List[Tuple[str, str]]:
        return [("nvc", self.nvc)]

    def _translator(self) -> str:
        path = os.path.join(tools.bindir(), "iverilog-sv2ghdl")
        if not os.path.isfile(path):
            raise BackendError("cannot find iverilog-sv2ghdl next to vamos (%s)" % path)
        return path

    def _env(self) -> dict:
        extra = {"NVC": self.nvc, "NVC_LIBDIR": self.libdir}
        iv = tools.find_real("iverilog")
        if iv:
            extra["IVERILOG"] = iv
        return tools.child_env(extra)

    def _metadata(self) -> dict:
        md = {}
        try:
            with open(os.path.join(self.workdir, "_metadata")) as fh:
                for line in fh:
                    m = re.match(r'\s*(\w+)="?([^"\n]*)"?', line)
                    if m:
                        md[m.group(1)] = m.group(2)
        except OSError:
            pass
        return md

    # -- stages ---------------------------------------------------------------

    def analyse(self) -> str:
        """Translate + analyse all sources. Returns the top entity name."""
        job = self.job
        cmd = [self._translator(), "-o", self.workdir, "-g2012"]
        if job.tops:
            cmd += ["-s", job.tops[0]]
        for d in job.incdirs:
            cmd.append("-I" + d)
        for k, v in job.defines.items():
            cmd.append("-D%s=%s" % (k, v) if v is not None else "-D" + k)
        if job.timescale:
            # VCS -timescale applies to files with no `timescale of their own:
            # a directive ahead of the first source does exactly that.
            os.makedirs(job.daidir, exist_ok=True)
            ts = os.path.join(job.daidir, "vamos_timescale.v")
            with open(ts, "w") as fh:
                fh.write("`timescale %s\n" % job.timescale)
            cmd.append(ts)
        cmd += [s.path for s in job.sources]
        cmd += job.lib_files
        rc = self._call(cmd, cwd=job.cwd)
        if rc != 0:
            raise BackendError("translation/analysis failed (exit %d)" % rc)
        top = self._metadata().get("TOP_ENTITY")
        if not top:
            raise BackendError("could not determine the top-level module")
        return top

    def deferred_modules(self) -> List[str]:
        """Modules sv2ghdl left as empty 'deferred' stubs in design.vhd.

        A deferred top module simulates as nothing at all and still exits 0,
        so the caller must say so loudly.
        """
        found = []
        try:
            with open(os.path.join(self.workdir, "design.vhd"), errors="replace") as fh:
                for line in fh:
                    m = re.search(r"sv2vhdl:deferred source=\S+ module=(\w+)", line)
                    if m:
                        found.append(m.group(1))
        except OSError:
            pass
        return found

    def elaborate(self, top: str) -> None:
        std = self._metadata().get("NVC_STD", "2040")
        rc = self._call([self.nvc, "--std=" + std, "-L", self.libdir, "-e", top],
                        cwd=self.workdir)
        if rc != 0:
            raise BackendError("elaboration failed (exit %d)" % rc)

    def run(self, top: str, plusargs: List[str],
            out: Callable[[str], None], err: Callable[[str], None]) -> Tuple[int, str]:
        """Run the elaborated design. Returns (exit code, last sim time)."""
        std = self._metadata().get("NVC_STD", "2040")
        cmd = [self.nvc, "--std=" + std, "-L", self.libdir]
        extra = {}
        resolver = os.path.join(self.libdir, "sv2vhdl", "libresolver.so")
        if os.path.isfile(resolver):
            cmd.append("--load=" + resolver)
            extra["SV2VHDL_QUIET"] = "1"
            pydir = self._resolver_pydir()
            if pydir:
                pp = os.environ.get("PYTHONPATH")
                extra["PYTHONPATH"] = pydir + (os.pathsep + pp if pp else "")
        cmd += ["-r", top] + list(plusargs)
        env = self._env()
        env.update(extra)
        if os.environ.get("VAMOS_VERBOSE"):
            self.emit("vamos: + " + " ".join(cmd))
        proc = subprocess.Popen(cmd, cwd=self.workdir, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                universal_newlines=True, errors="replace")
        f = OutputFilter(out, err)
        assert proc.stdout is not None
        for line in proc.stdout:
            f.feed(line.rstrip("\n"))
        rc = proc.wait()
        # iverilog's VHDL backend ends with `report "SIMULATION FINISHED"
        # severity failure`, so nvc exits non-zero on a clean finish.
        if rc != 0 and f.finished_marker:
            rc = 0
        return rc, f.last_time

    def _resolver_pydir(self) -> Optional[str]:
        cands = [os.path.join(self.libdir, "sv2vhdl")]
        if tools.dev_mode():
            prefix = os.path.dirname(os.path.dirname(os.path.realpath(self.nvc)))
            cands.append(os.path.join(os.path.dirname(prefix), "nvc", "lib", "sv2vhdl"))
        for c in cands:
            if os.path.isfile(os.path.join(c, "sv2vhdl_resolver.py")):
                return c
        return None

    def _call(self, cmd: List[str], cwd: str) -> int:
        if os.environ.get("VAMOS_VERBOSE"):
            self.emit("vamos: + " + " ".join(cmd))
        proc = subprocess.Popen(cmd, cwd=cwd, env=self._env(),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                universal_newlines=True, errors="replace")
        assert proc.stdout is not None
        for line in proc.stdout:
            self.emit(line.rstrip("\n"))
        return proc.wait()


_TIME_RE = re.compile(r"^\*\* \w+: (\d+(?:\.\d+)?\s*[fpnum]?s)\+\d+:")

# nvc / plugin chatter that is never user output (ported from vvp-sv2ghdl).
_DROP = [re.compile(p) for p in (
    r"^\*\* Note: loading VHPI", r"^   Process ", r"^   Procedure ", r"^\*\* Debug: ",
    r"^plugin loaded", r"^Python ", r"^loaded ", r"^===.*===", r"^---", r"^region:",
    r"^  instance:", r"^    port ", r"^      drv=", r"^Total ", r"^Nets ", r"^resolver:",
    r"^Root:", r"^Initial ")]


class OutputFilter:
    """Turn nvc report lines back into plain $display output."""

    def __init__(self, out: Callable[[str], None], err: Callable[[str], None]):
        self.out, self.err = out, err
        self.last_time = "0"
        self.finished_marker = False

    def feed(self, line: str) -> None:
        m = _TIME_RE.match(line)
        if m:
            self.last_time = m.group(1).replace(" ", "")
        if "SIMULATION FINISHED" in line:
            self.finished_marker = True
        for d in _DROP:
            if d.match(line):
                return
        if line.startswith("** Note: "):
            self.out(re.sub(r"^\*\* Note: [^:]*: ", "", line))
        elif re.match(r"^\*\* (Warning|Error|Fatal|Failure): ", line):
            self.err(line)
        else:
            self.out(line)


def cpu_time() -> float:
    t = os.times()
    return t.user + t.system + t.children_user + t.children_system

