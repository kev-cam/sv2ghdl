"""The spectre job record and the merged settings (docs/VAMOS_SPECTRE_DESIGN.md §2.2, §2.3; the
API is the §10 block).

Phase 0 (§12): SpectreJob (with notes and defaults) and Settings are frozen; settings() is the
signature S5 implements in phase 1 and raises NotImplementedError until then.

SpectreJob implements the job protocol optable.scan uses: note() and unmapped.  Its scalar
fields hold argv's values (None when argv does not set one) and `defaults` the defaults
layer's, by field name (§2.2): argv and the defaults are scanned as two layers and kept apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from vamos.job import Unmapped
from vamos.netlist.ir import Netlist
from vamos.notes import Note


@dataclass
class SpectreJob:
    prog: str                                           # the only positional fields: prog, argv, cwd
    argv: List[str]
    cwd: str
    netlist: Optional[str] = None                       # as given; None = stdin
    raw: Optional[str] = None
    fmt: Optional[str] = None
    outdir: Optional[str] = None
    log_mode: str = "screen"                            # screen (stdout) | both | file
    log_path: Optional[str] = None
    percent: Dict[str, Optional[str]] = field(default_factory=dict)   # +%X / -%X
    cpp: bool = False
    defines: List[str] = field(default_factory=list)
    undefines: List[str] = field(default_factory=list)
    incdirs: List[str] = field(default_factory=list)
    disable_cpp: bool = False
    config: List[str] = field(default_factory=list)      # after the =config / -config rules (§2.3)
    pre_config: List[str] = field(default_factory=list)
    paramdefault: List[str] = field(default_factory=list)
    classes: Dict[str, bool] = field(default_factory=dict)
    maxwarns: Optional[int] = None
    maxnotes: Optional[int] = None
    maxwarnstolog: Optional[int] = None
    maxnotestolog: Optional[int] = None
    errpreset: Optional[str] = None                     # +errpreset=
    aps: Optional[str] = None                           # +aps= / ++aps=
    mts: bool = True                                    # False under -mts
    ahdllibdir: Optional[str] = None
    va_defines: List[str] = field(default_factory=list)
    escchars: bool = False
    action: str = "run"                                 # run | version | subversion | help
    help_topic: Optional[str] = None
    unmapped: List[Unmapped] = field(default_factory=list)   # optable's five dispositions only
    notes: List[Note] = field(default_factory=list)          # scan-time errors (§2.3): exit 2 before the run dir
    defaults: Dict[str, object] = field(default_factory=dict)  # the defaults layer's scalars, by field name; the
    #                                                            scalar fields above hold argv's (None: not given)

    def note(self, option: str, disposition: str, note: str = "") -> None:
        """optable.scan's record of an option with a string disposition (as vamos.job.Job.note)."""
        self.unmapped.append(Unmapped(option, disposition, note))


@dataclass
class Settings:
    """The merged §2.2 layers, after parsing: argv, then the netlist's options, then job.defaults."""
    fmt: str = "psfascii"
    raw: str = ""
    outdir: Optional[str] = None
    maxwarns: Optional[int] = None
    maxnotes: Optional[int] = None
    maxwarnstolog: Optional[int] = None
    maxnotestolog: Optional[int] = None
    errpreset: Optional[str] = None
    aps: Optional[str] = None
    paramdefault: List[Tuple[str, str, str]] = field(default_factory=list)   # (primitive, parameter, value)
    options: Dict[str, object] = field(default_factory=dict)                 # global options after precedence


def settings(job: SpectreJob, nl: Netlist) -> Settings:
    """§2.2: argv, then the netlist's options statements, then job.defaults (S5)."""
    raise NotImplementedError("spectre.job.settings is implemented in phase 1 (S5; design §2.2, §10)")
