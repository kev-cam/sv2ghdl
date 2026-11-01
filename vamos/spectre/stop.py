"""What spectre/flow.py and the engine runners share about stopping (phase 2; design §2.7, §7, §10).

The runners (spectre/run_vacask.py, spectre/run_xyce.py) return an `EngineResult` whose `rc` is the
Spectre exit status their engine run implies, decided by the failure rules of §7.1 (VACASK) and §7.2
(Xyce) and the signal rules of §2.7:

    0  every planned analysis ran and left its complete output
    1  an analysis or a sweep point failed in the engine (the others ran and were written)
    2  an engine netlist, elaboration or parameter error, a failed control step, an engine crash or an
       engine ended by a signal other than those of status 3
    3  stopped by a signal: INT, TERM, HUP or QUIT to vamos (forwarded to the engine as SIGTERM), or
       SIGKILL, SIGTERM, SIGINT or SIGHUP to the engine from someone other than vamos; the finished
       analyses and the interrupted one are converted from the engine's partial output

`EngineResult.status` says per step id `ok`, `failed` or `interrupted` (a step the engine never reached
is absent).  A second INT, TERM, HUP or QUIT while an engine runs kills it at once (`proc.Interrupts`),
and vamos then exits 3 without converting (§2.7): the runner raises `Stop`, which `flow.run` catches.
The grace-period kill (the engine still running `INTERRUPT_GRACE` seconds after the first signal) is not
a second signal: the run is converted as for status 3.  The runners signal the engine's whole process
group (`proc.Interrupts(..., group=True)`), so a child of the engine, such as Xyce's PyMS compile, never
outlives vamos.

SIGUSR2's note is `CHECKPOINT` wherever it is printed (flow.poll before and after the engine, and the
runners' flags while it runs): one text [M UG p.237].

One hangup is one signal (the guide trial of phase 2e, TRIAL2EF-01).  A terminal that hangs up delivers
SIGHUP twice: an interactive shell passes the SIGHUP it gets on to its foreground job, and the kernel
signals that job's process group again as the shell, the session leader, exits (measured: 0.3 ms apart,
the kernel's with a SIGCONT); a session manager may add one (systemd: SIGTERM, then SIGHUP).  vamos took
the second as the user's second signal and threw every result away.  classify() is asked by every
handler of a stop signal (cli.py's early one, flow.py's, both runners'): a SIGHUP after a stop signal,
and any stop signal at most HANGUP_WINDOW seconds after a SIGHUP, is a "repeat" of the same hangup,
which the handler ignores; a second INT, TERM or QUIT is still the user's second signal, however quick.
forget() starts the record afresh (flow.run's handlers, and each runner's, at their start).
"""

import signal
import time
from typing import Dict, List, Optional, Tuple

# the signals vamos handles and forwards to the engine as SIGTERM (§2.7)
HANDLED: Tuple[int, ...] = tuple(getattr(signal, n) for n in ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT")
                                 if hasattr(signal, n))
# the signals that only set a flag: the SIGUSR1 status line and the SIGUSR2 note (§2.7)
FLAGS: Tuple[int, ...] = tuple(getattr(signal, n) for n in ("SIGUSR1", "SIGUSR2") if hasattr(signal, n))
# what the first handled signal becomes when it is passed on to the engine (§2.7, E81)
FORWARD: int = signal.SIGTERM
# the SIGUSR2 note (§2.7) [M UG p.237], with no origin: the flow and both runners print this text
CHECKPOINT = "checkpoint not supported: SIGUSR2 is ignored and the run continues"
# a stop signal this many seconds or less after a SIGHUP is part of the same hangup (TRIAL2EF-01)
HANGUP_WINDOW = 1.0
_HANGUP = getattr(signal, "SIGHUP", None)
_last_hangup: List[Optional[float]] = [None]      # time.monotonic() of the latest SIGHUP a handler took


def forget() -> None:
    """A run, or an engine run, starts: no SIGHUP is on record (classify)."""
    _last_hangup[0] = None


def take_over_flags(saved: Dict[int, object]) -> List[int]:
    """The SIGUSR1 and SIGUSR2 flags that the handlers a runner's signal block replaced (saved: signal -> the
    handler before it) recorded and nobody printed yet, in order: the flow's, whose handler's owner offers
    take_pending_flags(), between its last poll and the runner's start.  The runner prints them as its own
    (the guide trial TRIAL2EG-F07: a SIGUSR1 sent between the inventory and the engine's start was answered
    after the last analysis, with the status line of the setup)."""
    out: List[int] = []
    seen: List[object] = []
    for sig in FLAGS:
        owner = getattr(saved.get(sig), "__self__", None)
        take = getattr(owner, "take_pending_flags", None)
        if callable(take) and not any(owner is o for o in seen):
            seen.append(owner)
            out.extend(take())
    return out


def give_back_flags(saved: Dict[int, object], flags: List[int]) -> bool:
    """The SIGUSR1 and SIGUSR2 flags a runner's signal block holds and did not print, handed back as it ends to the
    owner of the handlers it replaced (saved, as take_over_flags reads it), which offers return_pending_flags(): the
    flow, whose next poll answers them as it answered them before the take-over (FIX2E5, S-2E4-N1: a VACASK runner
    that failed before VACASK ran, at a Verilog-A compile error, dropped the flow's SIGUSR1 it had taken over, where
    5d2291f's flow answered it after the error).  True when an owner took them."""
    if not flags:
        return True
    for sig in FLAGS:
        owner = getattr(saved.get(sig), "__self__", None)
        give = getattr(owner, "return_pending_flags", None)
        if callable(give):
            give(list(flags))
            return True
    return False


def classify(signum: int, after_first: bool, now: Optional[float] = None) -> str:
    """What a stop signal is, asked by its handler as it arrives (after_first: the handler's run had a stop
    signal already): "repeat" for a SIGHUP after a stop signal (after_first, or a SIGHUP another handler
    took) and for any stop signal at most HANGUP_WINDOW seconds after a SIGHUP, the same hangup, which the
    handler ignores; else "second" after a first one (§2.7: the engine is killed at once, or the file being
    written is dropped), else "first".  Records the time of a SIGHUP."""
    t = time.monotonic() if now is None else now
    last = _last_hangup[0]
    hup = _HANGUP is not None and signum == _HANGUP
    repeat = (hup and (after_first or last is not None)) or (last is not None and t - last <= HANGUP_WINDOW)
    if hup:
        _last_hangup[0] = t
    if repeat:
        return "repeat"
    return "second" if after_first else "first"


class Stop(Exception):
    """A second INT, TERM or QUIT while an engine ran (§2.7; a SIGHUP after a stop signal is a repeat,
    classify): the engine was killed and vamos exits 3 at once, without converting.  Raised by an engine
    runner, caught by flow.run, whose warning names `phase', the stage the runner was in (the analysis it
    ran; FIX2E5, TRIAL2EH-F11: it said `during initial setup' for a second Ctrl-C during a transient), or
    the flow's own when the runner gives none."""

    def __init__(self, signum: int, message: str = "", phase: str = "") -> None:
        super().__init__(message or "stopped by a second signal")
        self.signum = signum
        self.phase = phase
