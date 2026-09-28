"""Personalities that share a real tool's name (nvc, ...).

Phase 0: hand the command line to the real tool unchanged (vamos extensions
come later).  The real tool is found with find_real(), which skips vamos
shims, and runs with a scrubbed PATH and the lock-out stack, so an `nvc`
symlink to vamos placed first on PATH cannot recurse.
"""

import sys
from typing import List

from vamos import tools


def make(name: str):
    def main(args: List[str], opts: dict) -> int:
        real = tools.find_real(name)
        if real is None:
            print("vamos: error: no real '%s' found (PATH has only vamos shims); "
                  "set VAMOS_%s" % (name, name.upper()), file=sys.stderr)
            return 127
        tools.exec_real(real, args, name)
        return 127  # not reached
    return main
