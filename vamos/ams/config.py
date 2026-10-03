"""Control-file data (frozen contract, docs/VAMOS_AMS_DESIGN.md §2.3).

initfile.parse_control() fills an AmsConfig; rules.py, shells.py, cut.py and
flow.py read it.  Raw rule parameter values stay strings here; rules.py
parses numbers, '%' and supply references.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from vamos.notes import Note

TNF = "MSV-IE-OPT-TNF"


@dataclass
class Choose:
    engine: str                          # as written: xa finesim primesim hsim nanosim vacask xyce
    netlists: List[str] = field(default_factory=list)   # as written (resolved by netlist/spice.py)
    cfgs: List[str] = field(default_factory=list)       # every -c / -C XA cfg path, as written
    out_prefix: Optional[str] = None     # -o
    uic: bool = False                    # -skipdc
    dialect: str = "hspice"              # -nspice selects "spice" (parsed as HSPICE in v1, a warning);
    #                                      -spice is FineSim/PrimeSim's SPICE mode (a note, dialect kept)
    options: List[str] = field(default_factory=list)    # anything else (each gets a note or warning)
    origin: str = ""


@dataclass
class UseSpice:
    cells: List[Tuple[str, str]] = field(default_factory=list)   # (verilog cell glob, subckt or '' = same)
    insts: List[str] = field(default_factory=list)               # full Verilog paths, '*' allowed
    port_map: List[Tuple[str, str]] = field(default_factory=list)       # ("p" | "p[i]" | "*", target)
    index_order: List[Tuple[str, str]] = field(default_factory=list)    # ("a" | "*", "dec" | "inc" | "same")
    origin: str = ""


@dataclass
class PortDir:
    cell: str
    dirs: Dict[str, str] = field(default_factory=dict)  # spice port/bus (lower) -> input | output | inout
    origin: str = ""


@dataclass
class PortConnect:
    cell: str
    inst: Optional[str] = None
    conns: List[Tuple[str, str, bool]] = field(default_factory=list)   # (spice port, net | 'snps_open', real)
    origin: str = ""


@dataclass
class IeRule:
    kind: str                            # 'a2d' | 'd2a' | 'map_by_node' (params {'r': raw}, node= only)
    params: Dict[str, str] = field(default_factory=dict)   # key (lower) -> raw value; flags -> ""
    node: Optional[str] = None
    cell: Optional[str] = None
    inst: Optional[str] = None
    port: Optional[str] = None
    library: Optional[str] = None
    origin: str = ""


@dataclass
class AmsConfig:
    choose: Optional[Choose] = None
    use_spice: List[UseSpice] = field(default_factory=list)
    bus_formats: List[str] = field(default_factory=list)         # empty -> ["[%d]"]
    port_dirs: Dict[str, PortDir] = field(default_factory=dict)  # cell (lower) -> PortDir
    port_connects: List[PortConnect] = field(default_factory=list)
    rules: List[IeRule] = field(default_factory=list)            # file order, snps_vcsAD.ini first
    remove_d2a: List[Tuple[str, Optional[float], str]] = field(default_factory=list)  # (node selector, dc, origin)
    disable_ie: List[Tuple[str, str]] = field(default_factory=list)                   # (node selector, origin)
    netlist_lines: List[Tuple[str, str]] = field(default_factory=list)                # (line, origin)
    ref_voltages: List[Tuple[str, Optional[float], str]] = field(default_factory=list)
    # (node, volts, origin): volts None when ie_reference_voltage has no voltage=, NaN
    # (initfile.SKIP) for a skip_node= entry
    severity_overrides: Dict[str, str] = field(default_factory=dict)                  # TNF -> warning|error
    xa: Dict[str, object] = field(default_factory=dict)          # mined XA cfg: probe_v, probe_i, case
    files: List[str] = field(default_factory=list)               # control files read, in order
    notes: List[Note] = field(default_factory=list)

    def formats(self) -> List[str]:
        return self.bus_formats or ["[%d]"]
