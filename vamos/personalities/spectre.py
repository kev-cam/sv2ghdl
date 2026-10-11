"""The spectre personality: Spectre's command line, run on VACASK or Xyce.

Phase 0 of docs/VAMOS_SPECTRE_DESIGN.md (§10, §12): this module's public names with their
frozen signatures.  S5 implements them in phase 1.  Until then `main` reports that the
personality is not implemented and returns Spectre's exit status 2 ("stopped early
because of a Spectre error condition", §2.7); build_job and help_text raise
NotImplementedError.  cli.py imports this module for every personality, so nothing here
imports vamos.spectre or vamos.netlist at import time.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, List, Mapping, Optional

from vamos.optable import Opt, Table

if TYPE_CHECKING:
    from vamos.spectre.job import SpectreJob

# §2.3's rows, one Opt per option and per abbreviation; scanned with option_chars "-+=" (§2.1).
OPTIONS: List[Opt] = []
TABLE: Table = Table(OPTIONS)


def build_job(argv: List[str], env: Mapping[str, str], cwd: str, prog: str) -> SpectreJob:
    """The defaults layer (<prog>_DEFAULTS, §2.2) and argv (after cli.py took the --vamos-*
    options) -> SpectreJob; prog is the invoked name (tools.invoked)."""
    raise NotImplementedError("spectre.build_job is written in phase 1 (S5)")


def main(args: List[str], opts: dict) -> int:
    """cli entry; the invoked name is tools.invoked (§2.1).  Returns the Spectre exit status
    and never raises (§2.7)."""
    print("vamos: error: the spectre personality is not implemented yet "
          "(docs/VAMOS_SPECTRE_DESIGN.md: phase 0 skeleton)", file=sys.stderr)
    return 2


def help_text(topic: Optional[str] = None) -> str:
    """spectre -h [topic]: at most 100 columns, ending with the absolute path of
    docs/VAMOS_GUIDE.md (§2.1)."""
    raise NotImplementedError("spectre.help_text is written in phase 1 (S5)")
