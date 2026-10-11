"""Engine results into per-analysis results (docs/VAMOS_SPECTRE_DESIGN.md §8, §4.6; the API is
the §10 block).

Phase 0 (§12): EngineResult, Signal and AnalysisResult are frozen.  S4 adds collect() in phase
1 (streaming, sweep splitting, the noise and xf transforms): it needs no IR, since the sweep
variables, units and descriptions come from each step's SweepLevels (§5.2, §8.3), the trace
types and noise STRUCT types from the SignalMap (§6.4) and the header values from the step's
args and options (§5.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from vamos.netlist.signals import Ref


@dataclass
class EngineResult:
    rc: int = 0
    status: Dict[str, str] = field(default_factory=dict)        # step id -> ok | failed | interrupted
    files: Dict[str, str] = field(default_factory=dict)         # step id -> engine output file
    failed_points: Dict[str, int] = field(default_factory=dict) # step id -> first failed sweep point (1-based)
    names: Dict[str, Dict[Ref, str]] = field(default_factory=dict)   # from render (§4.5 item 6)
    log: List[str] = field(default_factory=list)


@dataclass
class Signal:
    name: str
    ptype: str
    units: str
    values: list = field(default_factory=list)                  # an op's single values only (swept: rows)
    members: Optional[List[Tuple[str, list]]] = None


@dataclass
class AnalysisResult:
    key: str
    atype: str
    file: str
    parent: str = ""
    tree: str = ""                                               # "", "leafNode" or "sweepNode"
    sweep: Optional[Tuple[str, str, int, List[float]]] = None    # (name, units, grid, values); values for
    #                                                              parents only, a leaf's x comes from rows
    signals: List[Signal] = field(default_factory=list)          # the traces, in TRACE order
    header: Dict[str, object] = field(default_factory=dict)
    description: str = ""
    swept: Dict[str, float] = field(default_factory=dict)
    data_type: str = ""
    status: str = "ok"                                           # ok | failed | interrupted
    rows: Optional[Callable[[], Iterator[Tuple[object, List[object]]]]] = None   # swept: (x, values in
    #                                                              TRACE order), streamed (§4.6)
