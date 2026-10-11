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
"""

import os
import re
import sys
from typing import Callable, Dict, List, Optional

from vamos import VERSION, banner, tools
from vamos.optable import (check_vamos_opts, strict_message, vamos_option_effects,
                           vamos_options_help)
from vamos.personalities import passthrough, simv, spectre, vcs

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

# `spectre` with a version suffix (spectre231, spectre-23.1, spectre_23) dispatches to the
# spectre personality (docs/VAMOS_SPECTRE_DESIGN.md §2.1).
_SPECTRE_VERSIONED = re.compile(r"^spectre[-_.]?[0-9][0-9A-Za-z._-]*$")


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


def spectre_names() -> List[str]:
    """The alias names of the spectre personality: VAMOS_SPECTRE_NAMES, colon-separated
    (`specsim` is the manual's own example)."""
    return [n for n in os.environ.get("VAMOS_SPECTRE_NAMES", "").split(":") if n]


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
