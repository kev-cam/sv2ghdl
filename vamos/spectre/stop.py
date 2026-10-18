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
"""

import signal
from typing import Tuple

# the signals vamos handles and forwards to the engine as SIGTERM (§2.7)
HANDLED: Tuple[int, ...] = tuple(getattr(signal, n) for n in ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT")
                                 if hasattr(signal, n))
# the signals that only set a flag: the SIGUSR1 status line and the SIGUSR2 note (§2.7)
FLAGS: Tuple[int, ...] = tuple(getattr(signal, n) for n in ("SIGUSR1", "SIGUSR2") if hasattr(signal, n))
# what the first handled signal becomes when it is passed on to the engine (§2.7, E81)
FORWARD: int = signal.SIGTERM
# the SIGUSR2 note (§2.7) [M UG p.237], with no origin: the flow and both runners print this text
CHECKPOINT = "checkpoint not supported: SIGUSR2 is ignored and the run continues"


class Stop(Exception):
    """A second INT, TERM, HUP or QUIT while an engine ran (§2.7): the engine was killed and vamos
    exits 3 at once, without converting.  Raised by an engine runner, caught by flow.run."""

    def __init__(self, signum: int, message: str = "") -> None:
        super().__init__(message or "stopped by a second signal")
        self.signum = signum
