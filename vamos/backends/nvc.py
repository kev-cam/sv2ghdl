"""sv2ghdl -> nvc backend: analyse, elaborate, run.

Translation and analysis go through bin/iverilog-sv2ghdl, the same pipeline
the iverilog personality and `nvc -a x.v` use (sv-normalize, sv2vhdl-modules,
iverilog's VHDL backend, sv2ghdl.pl fallback).  Its output directory - VHDL,
nvc work library and _metadata - lives inside the job's daidir.

Runs (NvcBackend.stream, shared by the digital simv and the AMS co-simulation):

- nvc's output is read as raw lines, split at "\\n" only (a testbench's "\\r"
  reaches the user unchanged), and every line goes to on_raw before the
  OutputFilter.  nvc runs with NVC_COLORS=never, so its prefixes are plain.
- Testbench output always arrives as an nvc report: the translator sends every
  $display/$write/$monitor/$strobe line, and the message of $error, $warning,
  $info and $fatal, through `report`, which nvc prints as "** <Severity>:
  <time>+<delta>: <text>" ("(init): " during initialisation; REPORT_RE).  Any
  other line is nvc's own, the co-simulation bridge's or an analog engine's.
- nvc's lifetime is tied to vamos's.  nvc runs in a process group of its own,
  with stdin from /dev/null, so signals reach it only through vamos: a terminal's
  Ctrl-C reaches vamos alone, and nvc gets exactly one SIGINT (it takes a second
  one, while the first is pending, as "quit now": exit 1, no end line).  While
  nvc runs, a SIGINT, SIGTERM or SIGHUP to vamos reaches nvc as SIGINT (nvc stops
  the design; a co-simulation's engine still finishes its output); a second one,
  or nvc still running INTERRUPT_GRACE seconds after the first, kills nvc.  A
  Ctrl-Z (SIGTSTP) stops nvc with vamos.  On Linux nvc gets SIGTERM when vamos
  dies (prctl PR_SET_PDEATHSIG), so a killed vamos never leaves nvc running.
  NvcBackend.interrupted holds the signal vamos got: the run is reported as
  interrupted and simv ends with that same signal.
- A nvc killed by a signal is reported as such ("nvc was killed by signal 9
  (SIGKILL)", exit status 128 + N), never as an ordinary failure.
"""

import io
import locale
import os
import re
import signal
import subprocess
import sys
import threading
from typing import Callable, List, Optional, Sequence, Tuple

from vamos import tools
from vamos.job import Job

NVC_SUBDIR = "nvc"

# A translator warning line (bin/iverilog-sv2ghdl under vamos, T4); NvcBackend.analyse keeps
# them for the caller (vamos.ams.verilog_ports.TRANSLATOR_WARNING parses the same lines)
_TRANSLATOR_WARNING = re.compile(r"^iverilog-sv2ghdl: Warning: ")

# Seconds nvc has to end after vamos passed it an interrupt, before it is killed
INTERRUPT_GRACE = 5.0


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
        self.interrupted: Optional[int] = None   # the signal that interrupted the last run
        self.killed = False                      # nvc ignored the interrupt and was killed
        # analyse(): the translator's "iverilog-sv2ghdl: Warning: ..." lines, kept for the
        # caller to report as vamos warnings (verilog_ports.translator_warnings; an error
        # under --vamos-strict) instead of being printed as plain output
        self.translator_lines: List[str] = []

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
        self.translator_lines = []
        rc = self._call(cmd, cwd=job.cwd, keep=_TRANSLATOR_WARNING, kept=self.translator_lines)
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

    def work_spec(self) -> str:
        """--work value naming the translated library by absolute path, so no
        nvc invocation depends on its cwd (NAME:PATH: a ':' in a bare path
        would be misread)."""
        return "work:" + os.path.abspath(os.path.join(self.workdir, "work"))

    def elaborate(self, top: str) -> None:
        std = self._metadata().get("NVC_STD", "2040")
        rc = self._call([self.nvc, "--std=" + std, "--work=" + self.work_spec(),
                         "-L", self.libdir, "-e", top], cwd=self.workdir)
        if rc != 0:
            raise BackendError("elaboration failed (exit %d)" % rc)

    def run_command(self, top: str, plusargs: List[str],
                    run_args: Sequence[str] = ()) -> Tuple[List[str], dict]:
        """The `nvc -r` command line and environment, shared with the AMS
        co-simulation backend.  run_args go after -r (--stop-time,
        --vacask-netlist, --cosim-config); --work and --load are global and
        must come before it."""
        std = self._metadata().get("NVC_STD", "2040")
        cmd = [self.nvc, "--std=" + std, "--work=" + self.work_spec(), "-L", self.libdir]
        extra = {}
        plugins = self.plugins()
        if plugins:
            cmd.append("--load=" + ",".join(plugins))
        if any(os.path.basename(p) == "libresolver.so" for p in plugins):
            extra["SV2VHDL_QUIET"] = "1"
            pydir = self._resolver_pydir()
            if pydir:
                pp = os.environ.get("PYTHONPATH")
                extra["PYTHONPATH"] = pydir + (os.pathsep + pp if pp else "")
        cmd += ["-r"] + list(run_args) + [top] + list(plusargs)
        env = self._env()
        env.update(extra)
        env["NVC_COLORS"] = "never"      # the output filters read nvc's plain prefixes
        return cmd, env

    # The sv2vhdl runtime's VHPI libraries, in load order.  libresolver.so first: the
    # resolver, the plusargs and the design-wide $random generator (sv_random), which
    # libsv_math.so exports too (the first loaded library wins).  libsv_math.so: the
    # sv_math_pkg foreign functions ($sqrt, $ln, $pow, $sin, $hypot, $rtoi, $dist_*, ...);
    # without it any of them stops the run ("foreign function sv_sqrt not found").
    PLUGINS = ("libresolver.so", "libsv_math.so")

    def plugins(self) -> List[str]:
        """The PLUGINS that exist in <libdir>/sv2vhdl, in load order (nvc --load=a,b)."""
        found = []
        for name in self.PLUGINS:
            p = os.path.join(self.libdir, "sv2vhdl", name)
            if os.path.isfile(p):
                found.append(p)
        return found

    def stream(self, cmd: List[str], env: dict, cwd: str,
               out: Callable[[str], None], err: Callable[[str], None],
               on_raw: Optional[Callable[[str], None]] = None) -> Tuple[int, "OutputFilter"]:
        """Run cmd, feeding every raw line to on_raw before the output filter.

        Returns (exit status, the filter); the status is -N when nvc was killed by
        signal N.  self.interrupted / self.killed tell whether vamos was signalled
        meanwhile (see the module docstring).  BackendError if cmd cannot start."""
        if os.environ.get("VAMOS_VERBOSE"):
            self.emit("vamos: + " + " ".join(cmd))
        self.interrupted, self.killed = None, False
        f = OutputFilter(out, err)
        intr = _Interrupts()
        with intr:                               # before nvc starts: no signal goes unseen
            try:
                # stdin: nvc's process group is not the terminal's foreground group, where
                # a read would stop it (SIGTTIN); nothing nvc runs reads stdin
                proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        preexec_fn=_child_setup())
            except OSError as e:
                raise BackendError("cannot run %s: %s" % (cmd[0], e))
            intr.attach(proc)
            assert proc.stdout is not None
            text = io.TextIOWrapper(proc.stdout, encoding=locale.getpreferredencoding(False),
                                    errors="replace", newline="\n")
            try:
                for line in text:
                    if line.endswith("\n"):
                        line = line[:-1]
                    if on_raw is not None:
                        on_raw(line)
                    f.feed(line)
                rc = proc.wait()
            finally:
                if proc.poll() is None:          # an exception above: never leave nvc behind
                    proc.kill()
                    proc.wait()
                text.close()
        self.interrupted, self.killed = intr.signum, intr.killed
        return rc, f

    def run(self, top: str, plusargs: List[str],
            out: Callable[[str], None], err: Callable[[str], None],
            stop_fs: Optional[int] = None) -> Tuple[int, Optional[str]]:
        """Run the elaborated design.  Returns (exit code, the final simulation time for the
        footer, or None when nothing ran).

        The final time is the time of the design's own end ($finish, $stop, a fatal
        report): nvc prints it.  A run bounded by stop_fs (+vcs+finish+N) that the design
        did not end itself ran to that time, which nvc does not print."""
        run_args = ["--stop-time=%dfs" % stop_fs] if stop_fs is not None else []
        cmd, env = self.run_command(top, plusargs, run_args)
        try:
            rc, f = self.stream(cmd, env, self.workdir, out, err)
        except BackendError as e:
            err("vamos: error: %s" % e)
            return 1, None
        if (stop_fs is not None and not f.ended and self.interrupted is None
                and rc >= 0 and remap_exit(rc, f) == 0):
            simtime = fs_text(stop_fs)           # it ran to +vcs+finish+N
        else:
            simtime = footer_time(f.last_time)
        if self.interrupted is not None:
            err("vamos: note: interrupted (%s)%s" % (
                signal_name(self.interrupted),
                "; nvc did not stop within %g s and was killed" % INTERRUPT_GRACE
                if self.killed else ""))
            return 128 + self.interrupted, simtime
        if rc < 0:
            err("vamos: error: nvc was killed by signal %d (%s)" % (-rc, signal_name(-rc)))
            return 128 - rc, simtime
        return remap_exit(rc, f), simtime

    def _resolver_pydir(self) -> Optional[str]:
        cands = [os.path.join(self.libdir, "sv2vhdl")]
        if tools.dev_mode():
            prefix = os.path.dirname(os.path.dirname(os.path.realpath(self.nvc)))
            cands.append(os.path.join(os.path.dirname(prefix), "nvc", "lib", "sv2vhdl"))
        for c in cands:
            if os.path.isfile(os.path.join(c, "sv2vhdl_resolver.py")):
                return c
        return None

    def _call(self, cmd: List[str], cwd: str, keep: Optional["re.Pattern"] = None,
              kept: Optional[List[str]] = None) -> int:
        """Run cmd, printing its output; a line matching keep goes to kept instead."""
        if os.environ.get("VAMOS_VERBOSE"):
            self.emit("vamos: + " + " ".join(cmd))
        with subprocess.Popen(cmd, cwd=cwd, env=self._env(),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True, errors="replace") as proc:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip("\n")
                if keep is not None and kept is not None and keep.match(line):
                    kept.append(line)
                else:
                    self.emit(line)
            return proc.wait()


def remap_exit(rc: int, f: "OutputFilter") -> int:
    """nvc's exit status as VCS would report it.

    nvc exits 1 after a run with severity-error reports ($error, T1), where VCS
    exits 0 unless -exitstatus is given.  Fatal and failure reports, nvc's own
    errors, crashes and signals keep their status.  A translation made without
    tgt-vhdl's sv2vhdl mode (vamos always uses it: $finish is std.env.finish,
    which exits 0) ends a clean $finish with the report "SIMULATION FINISHED"
    of severity failure; only that exact nvc line maps to 0 (finished_marker),
    never testbench text that merely contains the words."""
    if rc > 0 and f.finished_marker:
        return 0
    if rc == 1 and f.errors and not f.fatal and not f.tool_errors:
        return 0
    return rc


# The prefix nvc gives a report: its time and delta ("100ns+0: "), or "(init): "
_STAMP = r"(?:\d+(?:\.\d+)?\s*[fpnum]?s\+\d+|\(init\))"
# A report line: every line of testbench output (see the module docstring)
REPORT_RE = re.compile(r"^\*\* (?:Note|Warning|Error|Failure|Fatal): " + _STAMP + ": ")
_TIME_RE = re.compile(r"^\*\* \w+: (\d+(?:\.\d+)?\s*[fpnum]?s)\+\d+:")
# std.env.finish / std.env.stop ($finish / $stop, T1): a note, and nvc's trace line naming
# the procedure (a testbench line "FINISH called" is traced to SV_DISPLAY_LINE instead)
_ENV_END = re.compile(r"^\*\* Note: \d+(?:\.\d+)?\s*[fpnum]?s\+\d+: (?:FINISH|STOP) called$")
_ENV_TRACE = re.compile(r"^   Procedure (?:FINISH|STOP) ")
_FINISH_MARKER = re.compile(r"^\*\* Failure: " + _STAMP + r": SIMULATION FINISHED$")

# nvc / plugin chatter that is never user output (ported from vvp-sv2ghdl).  The trace nvc
# prints after a report names the process and every subprogram on the way: "   Process
# :tb:_p0 at ...", "   Procedure SV_DISPLAY_LINE [STRING] at ...", and "   Function CHK
# [LOGIC3D_VECTOR return LOGIC3D_VECTOR] at design.vhd:33" for output printed inside a
# Verilog function.  Testbench output never looks like one: every line of it is a report
# (sv_display_line reports each line of a multi-line $display on its own).
_DROP = [re.compile(p) for p in (
    r"^\*\* Note: loading VHPI", r"^   Process ", r"^   Procedure ", r"^   Function .* at .+:\d+$",
    r"^\*\* Debug: ",
    r"^plugin loaded", r"^Python ", r"^loaded ", r"^===.*===", r"^---", r"^region:",
    r"^  instance:", r"^    port ", r"^      drv=", r"^Total ", r"^Nets ", r"^resolver:",
    r"^Root:", r"^Initial ")]


def is_report(line: str) -> bool:
    """True for a line nvc printed for a `report`: testbench output (module docstring)."""
    return REPORT_RE.match(line) is not None


class OutputFilter:
    """Turn nvc report lines back into plain $display output.

    last_time: the time of the last report; ended: the design ended itself
    ($finish, $stop, a fatal or failure report)."""

    def __init__(self, out: Callable[[str], None], err: Callable[[str], None]):
        self.out, self.err = out, err
        self.last_time = "0"
        self.ended = False
        self.finished_marker = False     # the exact SIMULATION FINISHED line (remap_exit)
        self.errors = 0            # severity-error reports ($error): nvc then exits 1, VCS exits 0
        self.tool_errors = 0       # nvc's own "** Error:" lines (no time stamp)
        self.fatal = False         # ** Fatal / ** Failure ($fatal, runtime errors)
        self._env_end = False      # the last line was a FINISH/STOP called note

    def feed(self, line: str) -> None:
        m = _TIME_RE.match(line)
        if m:
            self.last_time = m.group(1).replace(" ", "")
        if self._env_end and _ENV_TRACE.match(line):
            self.ended = True
        self._env_end = _ENV_END.match(line) is not None
        if _FINISH_MARKER.match(line):
            self.finished_marker = self.ended = True
        elif line.startswith("** Error: "):
            if is_report(line):
                self.errors += 1
            else:
                self.tool_errors += 1
        elif line.startswith(("** Fatal: ", "** Failure: ")):
            self.fatal = self.ended = True
        for d in _DROP:
            if d.match(line):
                return
        if line.startswith("** Note: co-simulation"):
            self.out(line)               # co-simulation status lines stay whole
        elif line.startswith("** Note: "):
            self.out(re.sub(r"^\*\* Note: [^:]*: ", "", line))
        elif re.match(r"^\*\* (Warning|Error|Fatal|Failure): ", line):
            self.err(line)
        else:
            self.out(line)


def cpu_time() -> float:
    t = os.times()
    return t.user + t.system + t.children_user + t.children_system


# -- times for the footer ------------------------------------------------------------

_UNITS_FS = (("ms", 10 ** 12), ("us", 10 ** 9), ("ns", 10 ** 6), ("ps", 10 ** 3), ("fs", 1))
_TIME_TEXT = re.compile(r"^\s*(\d+)\s*(ms|us|ns|ps|fs)?\s*$")


def fs_text(fs: int) -> str:
    """A femtosecond count the way nvc prints a time, in its largest exact unit (600ns,
    3257407025fs, 3600000ms); 0 is "0"."""
    if fs == 0:
        return "0"
    for unit, k in _UNITS_FS:
        if fs % k == 0:
            return "%d%s" % (fs // k, unit)
    return "%dfs" % fs


def footer_time(text: str) -> str:
    """A time nvc printed ("25ns", "0ms") in fs_text's form; other text unchanged."""
    m = _TIME_TEXT.match(text)
    if not m:
        return text
    return fs_text(int(m.group(1)) * dict(_UNITS_FS).get(m.group(2) or "fs", 1))


def signal_name(signum: int) -> str:
    try:
        return signal.Signals(signum).name
    except ValueError:
        return "signal %d" % signum


# -- nvc's lifetime --------------------------------------------------------------------

class _Interrupts:
    """While nvc runs: the first SIGINT, SIGTERM or SIGHUP vamos gets is passed to nvc
    as SIGINT and recorded (signum); a second one, or nvc still running INTERRUPT_GRACE
    seconds after the first (SIGALRM), kills nvc (killed).  A SIGTSTP (Ctrl-Z) stops nvc
    with vamos, and nvc continues when vamos does (nvc is in a process group of its own,
    _child_setup).  Handlers are installed only in the main thread, and restored
    afterwards; a signal that is ignored stays ignored."""

    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.signum: Optional[int] = None
        self.killed = False
        self._saved: dict = {}

    def attach(self, proc: subprocess.Popen) -> None:
        """nvc has started; a signal that came while it started is passed on now."""
        self.proc = proc
        if self.signum is not None:
            self._send(signal.SIGINT)
            self._alarm(INTERRUPT_GRACE)

    def __enter__(self) -> "_Interrupts":
        if threading.current_thread() is not threading.main_thread():
            return self
        for name, handler in (("SIGINT", self._on_signal), ("SIGTERM", self._on_signal),
                              ("SIGHUP", self._on_signal), ("SIGTSTP", self._on_stop)):
            sig = getattr(signal, name, None)
            # an ignored signal stays ignored (nohup, a background job without job control)
            if sig is not None and signal.getsignal(sig) != signal.SIG_IGN:
                self._saved[sig] = signal.signal(sig, handler)
        if hasattr(signal, "SIGALRM") and hasattr(signal, "setitimer"):
            self._saved[signal.SIGALRM] = signal.signal(signal.SIGALRM, self._on_alarm)
        return self

    def _alarm(self, seconds: float) -> None:
        alarm = getattr(signal, "SIGALRM", None)
        if alarm is not None and alarm in self._saved:
            signal.setitimer(signal.ITIMER_REAL, seconds)

    def __exit__(self, *exc) -> None:
        self._alarm(0)
        for sig, handler in self._saved.items():
            signal.signal(sig, handler if handler is not None else signal.SIG_DFL)
        self._saved = {}

    def _on_signal(self, signum, frame) -> None:
        if self.signum is None:
            self.signum = signum
            self._send(signal.SIGINT)
            self._alarm(INTERRUPT_GRACE)
        else:
            self._kill()

    def _on_alarm(self, signum, frame) -> None:
        self._kill()

    def _on_stop(self, signum, frame) -> None:
        self._send(signal.SIGSTOP)
        os.kill(os.getpid(), signal.SIGSTOP)        # vamos stops here ...
        self._send(signal.SIGCONT)                  # ... and nvc goes on when it does

    def _kill(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.killed = True
            self._send(signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)

    def _send(self, sig) -> None:
        if self.proc is None:
            return
        try:
            self.proc.send_signal(sig)
        except OSError:
            pass


def _child_setup() -> Optional[Callable[[], None]]:
    """The Popen preexec_fn for nvc.

    nvc gets a process group of its own: a terminal's Ctrl-C (SIGINT to the foreground
    process group) then reaches vamos alone, and nvc gets exactly one SIGINT, from vamos.
    nvc takes a second SIGINT, while the first is pending, as "quit now" (jit_interrupt:
    exit(1) with no end line), and in a co-simulation the first one stays pending.  On
    Linux nvc also gets SIGTERM when vamos dies (prctl PR_SET_PDEATHSIG)."""
    if not hasattr(os, "setpgid"):
        return None
    prctl = None
    if sys.platform.startswith("linux"):
        try:
            import ctypes
            prctl = ctypes.CDLL(None, use_errno=True).prctl
            prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
                              ctypes.c_ulong]
        except (OSError, AttributeError):
            prctl = None
    parent = os.getpid()

    def preexec() -> None:
        try:
            os.setpgid(0, 0)
        except OSError:
            pass
        if prctl is not None:
            prctl(1, int(signal.SIGTERM), 0, 0, 0)      # PR_SET_PDEATHSIG
            if os.getppid() != parent:                  # vamos died before that
                os._exit(1)
    return preexec

