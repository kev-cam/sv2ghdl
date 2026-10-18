"""Entry point: pick the personality, apply the lock-out, dispatch.

The personality is the invoked name (a `vcs` symlink to vamos) or, when run
as `vamos`, a leading -<personality> argument.  The spectre personality also
answers to `spectre` plus a version suffix (spectre231, spectre-23.1) and to
the names listed in VAMOS_SPECTRE_NAMES (docs/VAMOS_SPECTRE_DESIGN.md §2.1);
the name vamos was invoked as is kept in tools.invoked.  vamos's own options
are all --vamos-*, so they cannot collide with a vendor option, and are
removed before the personality sees the command line.  vamos owns that
namespace: an unknown --vamos-* key, a value given to a flag or a missing
value is a usage error (exit 2, optable.check_vamos_opts), and an option that
has no effect where it was given is a warning (an error under --vamos-strict).

A spectre invocation (the names above, or `vamos -spectre`) is prepared before
anything else is imported, since the imports take a noticeable time (phase 2c;
docs/VAMOS_SPECTRE_DESIGN.md §2.7): a standard descriptor that is closed (`>&-`)
gets /dev/null, so that no file vamos opens lands on fd 1, and INT, TERM, HUP,
QUIT, USR1 and USR2 get a handler that records them (an inherited SIG_IGN stays).
A signal while vamos starts is then neither a traceback nor death by the signal:
spectre/flow.py takes over what was recorded when it installs its own handlers,
and puts this handler back when it is done.  A second stop signal before that
ends vamos at once with status 3.  When the personality has returned, USR1 and
USR2 are ignored: the interpreter's teardown sets the handlers it runs back to
SIG_DFL, and a late status request would kill a finished run.
"""

import os
import re
import signal
import sys
from typing import Callable, Dict, List, Optional

# `spectre` with a version suffix (spectre231, spectre-23.1, spectre_23) dispatches to the
# spectre personality (docs/VAMOS_SPECTRE_DESIGN.md §2.1).
_SPECTRE_VERSIONED = re.compile(r"^spectre[-_.]?[0-9][0-9A-Za-z._-]*$")
# the invoked names of the other personalities (PERSONALITIES below)
_OTHER_NAMES = ("vcs", "vcs-ams", "simv", "nvc")


def spectre_names() -> List[str]:
    """The alias names of the spectre personality: VAMOS_SPECTRE_NAMES, colon-separated
    (`specsim` is the manual's own example)."""
    return [n for n in os.environ.get("VAMOS_SPECTRE_NAMES", "").split(":") if n]


class _EarlySignals:
    """The handler of a spectre invocation's start (phase 2c: R-tests-2, R-run-6): it records the
    signals for spectre/flow.py, whose handlers take them over (take_early_signals) and put this
    one back when the run is done.  A second stop signal before the first was taken over (a start
    that hangs) ends vamos at once with status 3 (§2.7: a second signal is not waited on)."""

    def __init__(self) -> None:
        names = ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT")
        self.stops = tuple(getattr(signal, n) for n in names if hasattr(signal, n))
        self.flags = tuple(getattr(signal, n) for n in ("SIGUSR1", "SIGUSR2") if hasattr(signal, n))
        self.received: List[int] = []
        self.installed: List[int] = []

    def __call__(self, signum, frame) -> None:
        self.received.append(signum)
        if signum in self.stops and sum(1 for s in self.received if s in self.stops) > 1:
            os._exit(3)

    def install(self) -> None:
        for sig in self.stops + self.flags:
            try:
                if signal.getsignal(sig) == signal.SIG_IGN:      # nohup, a background job: stays ignored
                    continue
                signal.signal(sig, self)
            except (OSError, RuntimeError, ValueError):          # not the main thread
                continue
            self.installed.append(sig)

    def take_early_signals(self) -> List[int]:
        """The signals received since the last call, in order (spectre/flow.py takes them over)."""
        out, self.received = self.received, []
        return out

    def before_exec(self) -> None:
        """The real tool is about to replace vamos (tools.exec_real): a stop signal received while
        vamos started is delivered again at its default action, as if no handler had been there."""
        stops = [s for s in self.received if s in self.stops]
        for sig in self.installed:
            try:
                signal.signal(sig, signal.SIG_DFL)
            except (OSError, RuntimeError, ValueError):
                pass
        if stops:
            os.kill(os.getpid(), stops[0])

    def finish(self) -> None:
        """The personality has returned: USR1 and USR2 are ignored from here on (the interpreter's
        teardown would set this handler back to SIG_DFL, and a late status request kill vamos)."""
        for sig in self.flags:
            if sig in self.installed:
                try:
                    signal.signal(sig, signal.SIG_IGN)
                except (OSError, RuntimeError, ValueError):
                    pass


def _spectre_invocation(argv: List[str]) -> bool:
    """Whether main will run the spectre personality, read from the command line as main reads it:
    the invoked name (VAMOS_ARGV0, else argv[0]'s basename), else a leading -spectre after the
    --vamos-* options."""
    name = os.environ.get("VAMOS_ARGV0", "") or os.path.basename((argv[0] if argv else "") or "vamos")
    if name in _OTHER_NAMES:
        return False
    if name == "spectre" or _SPECTRE_VERSIONED.match(name) or name in spectre_names():
        return True
    if name.startswith("spectre"):                        # a Cadence program vamos does not provide
        return False
    rest = [a for a in argv[1:] if not a.startswith("--vamos-")]
    return rest[:1] == ["-spectre"]


def _standard_fds() -> None:
    """D1 (phase 2c): a standard descriptor closed at start (`>&-`) gets /dev/null before vamos opens
    any file, so that no file vamos opens (the +log file, a deck) and no child's stdout lands there."""
    for fd in (0, 1, 2):
        try:
            os.fstat(fd)
            continue
        except OSError:
            pass
        try:
            null = os.open(os.devnull, os.O_RDWR)
        except OSError:
            continue
        try:
            if null == fd:
                os.set_inheritable(fd, True)
            else:
                os.dup2(null, fd)
                os.close(null)
        except OSError:
            pass


_EARLY: Optional[_EarlySignals] = None
if _spectre_invocation(getattr(sys, "argv", None) or []):
    _standard_fds()
    _EARLY = _EarlySignals()
    _EARLY.install()

from vamos import VERSION, banner, tools  # noqa: E402  (after the spectre start, above)
from vamos.optable import (check_vamos_opts, strict_message, vamos_option_effects,  # noqa: E402
                           vamos_options_help)
from vamos.personalities import passthrough, simv, spectre, vcs  # noqa: E402

PERSONALITIES: Dict[str, Callable[[List[str], dict], int]] = {
    "vcs": vcs.main,
    "vcs-ams": vcs.main_ams,
    "simv": simv.main,
    "nvc": passthrough.make("nvc"),
    "spectre": spectre.main,
}

_ROLES = {
    "vcs": "VCS two-step flow: vcs [options] files..., then ./simv",
    "vcs-ams": "vcs with -ad implied: Verilog with SPICE cells (VACASK, or Xyce)",
    "simv": "the runtime the generated ./simv runs",
    "nvc": "hands its command line to the real nvc unchanged",
    "spectre": "the Spectre command line: spectre [options] [netlist], run on VACASK (or Xyce)",
}


def usage() -> str:
    return ("usage: vamos -<personality> [tool arguments...]\n"
            "       <personality> [tool arguments...]      (via a symlink named after the tool)\n"
            "\npersonalities:\n%s\n"
            "\nvamos options (--vamos-<key>[=<value>], anywhere on the command line; the vendor\n"
            "tool never sees them; [..] says where an option has an effect):\n%s\n"
            "\nThe user guide: %s"
            % ("\n".join("  %-8s  %s" % (p, _ROLES.get(p, "")) for p in sorted(PERSONALITIES)),
               vamos_options_help(), tools.guide_path()))


# Kept for callers that print the usage text directly.
USAGE = usage()


def split_vamos_opts(args: List[str]):
    opts: dict = {}
    rest: List[str] = []
    for a in args:
        if not a.startswith("--vamos-"):
            rest.append(a)
            continue
        key, eq, val = a[len("--vamos-"):].partition("=")
        opts[key.replace("-", "_")] = val if eq else True
    return opts, rest


def personality_of(name: str) -> Optional[str]:
    """The personality the invoked name `name` dispatches to, or None.

    A PERSONALITIES key dispatches to itself; `spectre` plus a version suffix
    (spectre231, spectre-23.1) and a name listed in VAMOS_SPECTRE_NAMES dispatch to
    "spectre".  Any other spectre* name (spectrespp, spectre_encrypt, ...) is a Cadence
    program vamos does not provide: None here, and a usage error in main.
    """
    if name in PERSONALITIES:
        return name
    if _SPECTRE_VERSIONED.match(name) or name in spectre_names():
        return "spectre"
    return None


def main(argv: List[str]) -> int:
    argv0 = os.environ.pop("VAMOS_ARGV0", "") or os.path.basename(sys.argv[0] or "vamos")
    opts, args = split_vamos_opts(argv)

    bad = check_vamos_opts(opts)
    if bad:
        for b in bad:
            print("vamos: error: %s" % b, file=sys.stderr)
        return 2
    if opts.get("version"):
        print("vamos %s" % VERSION)
        return 0
    if opts.get("licenses"):
        print(banner.license_report())
        return 0
    if opts.get("verbose"):
        os.environ["VAMOS_VERBOSE"] = "1"

    personality = personality_of(argv0)
    if personality:
        invoked = argv0
    else:
        if argv0.startswith("spectre"):
            print("vamos: error: '%s' is a Cadence program vamos does not provide (vamos provides "
                  "spectre, and the alias names listed in VAMOS_SPECTRE_NAMES)" % argv0,
                  file=sys.stderr)
            print(usage(), file=sys.stderr)
            return 2
        if not args or not args[0].startswith("-") or args[0][1:] not in PERSONALITIES:
            if args and args[0] in ("-h", "-help", "--help"):
                print(usage())
                return 0
            print(usage(), file=sys.stderr)
            return 2
        personality, args = args[0][1:], args[1:]
        invoked = personality

    try:
        real = tools.check_lockout(personality)
        if real:
            if _EARLY is not None:
                _EARLY.before_exec()
            tools.exec_real(real, args, personality)
    except tools.LockoutError as e:
        print("vamos: error: %s" % e, file=sys.stderr)
        return 2
    except tools.ToolError as e:
        print("vamos: error: %s" % e, file=sys.stderr)
        return 1

    tools.current = personality
    tools.invoked = invoked
    if personality not in ("vcs", "vcs-ams", "spectre"):  # these report the options themselves
        effects = vamos_option_effects(opts, personality)
        for opt, why in effects:
            print("vamos: warning: %s has no effect: %s" % (opt, why), file=sys.stderr)
        if effects and opts.get("strict"):
            print("vamos: error: " + strict_message([o for o, _ in effects]), file=sys.stderr)
            return 1
    if personality == "simv" and opts.get("append_log"):
        args = args + ["--vamos-append-log"]          # run_daidir reads it from its arguments
    try:
        return PERSONALITIES[personality](args, opts)
    except tools.ToolError as e:
        print("vamos: error: %s" % e, file=sys.stderr)
        return 1
    finally:
        if _EARLY is not None and personality == "spectre":
            _EARLY.finish()
