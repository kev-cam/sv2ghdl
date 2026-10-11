"""Child processes under signals: the engine-lifetime machinery vamos runs its simulators with
(docs/VAMOS_SPECTRE_DESIGN.md §2.7, §10).  Moved here from backends/nvc.py, which imports it
back; the spectre flow is its second user.

Interrupts(handled, forward, flags=(), grace=INTERRUPT_GRACE)
    A context manager for the time a child runs.  The first signal in `handled` that
    vamos gets is passed to the child as `forward` and recorded (signum); a second one,
    or the child still running `grace` seconds after the first (SIGALRM), kills it
    (killed).  A signal in `flags` only sets a flag, collected in order by take_flags(),
    and is never forwarded.  SIGTSTP (Ctrl-Z) stops the child with vamos, and the child
    goes on when vamos does (SIGCONT), which needs the child in a process group of its
    own (child_setup).  Handlers are installed only in the main thread, and restored on
    exit; a signal that is ignored stays ignored (nohup, a background job without job
    control).  attach(proc) names the child; a signal that came before it is passed on
    then.
    nvc: Interrupts((SIGINT, SIGTERM, SIGHUP), SIGINT, grace=nvc.INTERRUPT_GRACE);
    spectre: ((SIGINT, SIGTERM, SIGHUP, SIGQUIT), SIGTERM, flags=(SIGUSR1, SIGUSR2)).
child_setup() -> the Popen preexec_fn: the child's own process group, and on Linux
    SIGTERM when vamos dies (prctl PR_SET_PDEATHSIG); None where setpgid is missing.
signal_name(signum) -> "SIGINT", or "signal 42" for a number with no name.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from typing import Callable, List, Optional, Sequence

# Seconds a child has to end after vamos passed it the forwarded signal, before it is
# killed: the default for callers that pass none (backends/nvc.py keeps its own
# INTERRUPT_GRACE and passes it at each construction)
INTERRUPT_GRACE = 5.0


class Interrupts:
    """While a child runs: the first signal in `handled` vamos gets is passed to the child
    as `forward` and recorded (signum); a second one, or the child still running `grace`
    seconds after the first (SIGALRM), kills it (killed).  A signal in `flags` sets a flag
    for take_flags() and is never forwarded.  A SIGTSTP (Ctrl-Z) stops the child with
    vamos, and the child continues when vamos does (the child is in a process group of
    its own, child_setup).  Handlers are installed only in the main thread, and restored
    afterwards; a signal that is ignored stays ignored."""

    def __init__(self, handled: Sequence[int], forward: int, flags: Sequence[int] = (),
                 grace: float = INTERRUPT_GRACE) -> None:
        self.handled = tuple(handled)
        self.forward = forward
        self.flags = tuple(flags)
        self.grace = grace
        self.proc: Optional[subprocess.Popen] = None
        self.signum: Optional[int] = None        # the first handled signal, None if none came
        self.killed = False                      # the child outlived the grace or a second signal
        self._flags: List[int] = []
        self._saved: dict = {}

    def attach(self, proc: subprocess.Popen) -> None:
        """The child has started; a signal that came while it started is passed on now."""
        self.proc = proc
        if self.signum is not None:
            self._send(self.forward)
            self._alarm(self.grace)

    def take_flags(self) -> List[int]:
        """The flag signals that came since the last call, in order (the handlers only
        record them: printing from a handler is not safe)."""
        out, self._flags = self._flags, []
        return out

    def __enter__(self) -> "Interrupts":
        if threading.current_thread() is not threading.main_thread():
            return self
        stop = getattr(signal, "SIGTSTP", None)
        for sig in self.handled:
            if sig != stop:                      # TSTP is the stop handler, below, always
                self._install(sig, self._on_signal)
        for sig in self.flags:
            self._install(sig, self._on_flag)
        if stop is not None:
            self._install(stop, self._on_stop)
        if hasattr(signal, "SIGALRM") and hasattr(signal, "setitimer"):
            self._saved[signal.SIGALRM] = signal.signal(signal.SIGALRM, self._on_alarm)
        return self

    def _install(self, sig: int, handler) -> None:
        # an ignored signal stays ignored (nohup, a background job without job control)
        if sig in self._saved or signal.getsignal(sig) == signal.SIG_IGN:
            return
        self._saved[sig] = signal.signal(sig, handler)

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
            self._send(self.forward)
            self._alarm(self.grace)
        else:
            self._kill()

    def _on_flag(self, signum, frame) -> None:
        self._flags.append(signum)

    def _on_alarm(self, signum, frame) -> None:
        self._kill()

    def _on_stop(self, signum, frame) -> None:
        self._send(signal.SIGSTOP)
        os.kill(os.getpid(), signal.SIGSTOP)        # vamos stops here ...
        self._send(signal.SIGCONT)                  # ... and the child goes on when it does

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


def child_setup() -> Optional[Callable[[], None]]:
    """The Popen preexec_fn for a simulator vamos runs.

    The child gets a process group of its own: a terminal's Ctrl-C (SIGINT to the foreground
    process group) then reaches vamos alone, and the child gets exactly one signal, from
    vamos (nvc takes a second SIGINT, while the first is pending, as "quit now": exit(1)
    with no end line; a co-simulation ends at once on a second one, with the interrupted
    end line and exit status 130, before the engine finishes its output).  On Linux the
    child also gets SIGTERM when vamos dies (prctl PR_SET_PDEATHSIG), so a killed vamos
    never leaves a simulator running."""
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


def signal_name(signum: int) -> str:
    try:
        return signal.Signals(signum).name
    except ValueError:
        return "signal %d" % signum
