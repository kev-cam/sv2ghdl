"""Entry point: pick the personality, apply the lock-out, dispatch.

The personality is the invoked name (a `vcs` symlink to vamos) or, when run
as `vamos`, a leading -<personality> argument.  vamos's own options are all
--vamos-*, so they cannot collide with a vendor option, and are removed
before the personality sees the command line.
"""

import os
import sys
from typing import Callable, Dict, List

from vamos import VERSION, banner, tools
from vamos.personalities import passthrough, simv, vcs

PERSONALITIES: Dict[str, Callable[[List[str], dict], int]] = {
    "vcs": vcs.main,
    "simv": simv.main,
    "nvc": passthrough.make("nvc"),
}

USAGE = """usage: vamos -<personality> [tool arguments...]
       <personality> [tool arguments...]      (via a symlink named after the tool)

personalities: %s

vamos options (accepted by every personality):
  --vamos-banner=<name|path|none>   banner profile (default: the personality's)
  --vamos-strict                    fail on unsupported or unknown options
  --vamos-verbose                   show the commands vamos runs
  --vamos-licenses                  list the tools vamos runs and their licences
  --vamos-version                   print the vamos version
""" % ", ".join(sorted(PERSONALITIES))


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


def main(argv: List[str]) -> int:
    argv0 = os.environ.pop("VAMOS_ARGV0", "") or os.path.basename(sys.argv[0] or "vamos")
    opts, args = split_vamos_opts(argv)

    if opts.get("version"):
        print("vamos %s" % VERSION)
        return 0
    if opts.get("licenses"):
        print(banner.license_report())
        return 0
    if opts.get("verbose"):
        os.environ["VAMOS_VERBOSE"] = "1"

    if argv0 in PERSONALITIES:
        personality = argv0
    else:
        if not args or not args[0].startswith("-") or args[0][1:] not in PERSONALITIES:
            if args and args[0] in ("-h", "-help", "--help"):
                print(USAGE)
                return 0
            print(USAGE, file=sys.stderr)
            return 2
        personality, args = args[0][1:], args[1:]

    try:
        real = tools.check_lockout(personality)
    except tools.LockoutError as e:
        print("vamos: error: %s" % e, file=sys.stderr)
        return 2
    if real:
        tools.exec_real(real, args, personality)

    tools.current = personality
    return PERSONALITIES[personality](args, opts)
