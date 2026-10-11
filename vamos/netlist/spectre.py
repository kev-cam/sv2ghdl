"""Spectre netlists into the IR (docs/VAMOS_SPECTRE_DESIGN.md §3; the API is the §10 block).

Phase 0 (§12): the contracts only.  SpectreParseOpts and Stmt are frozen; statements(),
parse() and resolve_source() are the signatures S1 implements in phase 1 and raise
NotImplementedError until then.

The statement layer, statements(), is the CST oracle's unit (§3.2, §11 T0): it reads text
without file access and without lowering.  parse() is built on it: two phases over every
file in both languages (§3.1, §4.3), the SPICE regions read by spice.parse_fragment, every
reference resolved to an IR name, Netlist.dialect = "spectre".  resolve_source() applies
§3.8.1's table once for the parser and again for alters (§5.3).
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import List, Mapping, Optional, Sequence, Tuple

from vamos.netlist import expr
from vamos.netlist.ir import Netlist, Source, Value
from vamos.notes import Note


@dataclass
class SpectreParseOpts:
    search: List[str] = field(default_factory=list)          # the -I directories
    percent: Mapping[str, str] = field(default_factory=dict) # %-codes for quoted strings (§2.4)
    pre: List[str] = field(default_factory=list)             # +pre_config fragment files
    post: List[str] = field(default_factory=list)            # +config fragment files
    cpp_markers: bool = False   # the input is cpp output: origins, include directory and language per
    #                             '# <line> "<file>" [flags]' marker, resolved against the cwd (§2.6)
    title: Optional[str] = None # line 1 of the original file when cpp ran
    mts: bool = True            # False under -mts: options inside subckts are global (§3.6)
    va_include: List[str] = field(default_factory=list)      # CDS_VLOGA_INCLUDE (§2.8)
    va_defines: List[str] = field(default_factory=list)      # -va,define, for va_modules' preprocessor (§3.1)
    top_dir: Optional[str] = None   # the directory the top file's relative includes resolve against
    #                                 (None: the file's own; the cwd for a stdin copy, §1)
    top_name: Optional[str] = None  # the name the top file's origins carry ("stdin"; None: its path)


@dataclass
class Stmt:
    """Phase 0: the statement layer, the CST oracle's unit (§3.2, §11 T0)."""
    kind: str                   # instance model parameters subckt if analysis save ic nodeset include ...
    name: str = ""
    nodes: List[str] = field(default_factory=list)
    master: str = ""            # instance master, model master or analysis keyword
    params: List[Tuple[str, str]] = field(default_factory=list)       # (name, value text as written)
    children: List["Stmt"] = field(default_factory=list)              # subckt, sweep, montecarlo, group body
    branches: List[Tuple[str, List["Stmt"]]] = field(default_factory=list)   # if: (condition text, body)
    origin: str = ""
    span: Tuple[int, int] = (0, 0)                                    # character offsets in the text


def statements(text: str, origin: str, title: bool = True) -> List[Stmt]:
    """The statements of one Spectre-language text: no file access, no lowering; NoteError on a
    syntax error (§3.2).  title: line 1 is the title line (False for an included file)."""
    raise NotImplementedError("spectre.statements is implemented in phase 1 (S1; design §3.2, §10)")


def parse(path: str, cwd: str, opts: SpectreParseOpts) -> Netlist:
    """A Spectre netlist, with every file it includes in both languages, into one Netlist with
    dialect "spectre" (§3, §4.3); built on statements(); NoteError with every note on error."""
    raise NotImplementedError("spectre.parse is implemented in phase 1 (S1; design §3, §10)")


def resolve_source(params: Mapping[str, Value], type: str, tran_stops: Sequence[float],
                   notes: List[Note]) -> Source:
    """§3.8.1's table: the parameters written on a vsource/isource (Spectre names) and its type into
    a Source with every HSPICE field resolved; tran_stops are the stop times of every tran
    (ir.flat_analyses) for the pulse edge default; plan.py reuses it for alters (§5.3)."""
    raise NotImplementedError("spectre.resolve_source is implemented in phase 1 (S1; design §3.8.1, §10)")


number = functools.partial(expr.number, dialect="spectre")   # §3.3 (expr.number's dialect keyword: §4.2)
