"""The cut -> deck contract (frozen, docs/VAMOS_AMS_DESIGN.md §5.7).

Data only.  cut.analyse() builds a CutAnalysis; cut.assign_roles() turns its
nets into AnalogNodes; deck.py fills the levels through rules.py;
names.build_bridges() builds every Bridge; cut.emit() and deck.py both
iterate AmsPlan.bridges.  Nothing downstream rebuilds a name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from vamos.notes import Note

# -- port directions / kinds ---------------------------------------------------

INPUT, OUTPUT, INOUT, AUTO = "input", "output", "inout", "auto"
LOGIC, REAL = "logic", "real"

# -- roles of an analog node (§5.4) ---------------------------------------------

THROUGH = "THROUGH"      # only cut ports: one analog node, no IE
D2A = "D2A"              # digital drives cut inputs (gated, or ideal when d2a.powernet)
A2D = "A2D"              # a cut port drives digital readers
BIDIR = "BIDIR"          # both directions on one node
RD2A = "RD2A"            # real port, digitally driven: ideal source
RA2D = "RA2D"            # real output, digitally read: node voltage deposited
POWERNET = "POWERNET"    # supply0/supply1 net: constant ideal source, no bridge
REMOVED = "REMOVED"      # remove_d2a on a D2A net (dc= source, or left to the analog side)
DISABLED = "DISABLED"    # disable_ie
NONE = "NONE"            # private / undriven node, no IE
ROLES = (THROUGH, D2A, A2D, BIDIR, RD2A, RA2D, POWERNET, REMOVED, DISABLED, NONE)
BRIDGED = (D2A, A2D, BIDIR, RD2A, RA2D)

# Per-bit role codes returned by vams_cut_pkg.vams_role() (§5.5).  Only the
# node's host bit gets the node's role; every other cut-port bit is PASSIVE
# (no bridge processes; driven L3D_Z when its mode is out or inout).
ROLE_PASSIVE = 0
ROLE_CODE = {D2A: 1, A2D: 2, BIDIR: 3, RD2A: 4, RA2D: 5}

# Level keys for vams_cut_pkg.vams_lvl() (§5.5).  Times in seconds.
#   HIV LOV      D2A levels (V)            X2V     x2v code 0..4
#   HITH LOTH    A2D thresholds (V)        XBAND   PAMS hysteresis divisor (0 = none)
#   MIDV_T       midv time (s, <0 = none)  MIDV_L  midv logic: 0, 1, 2 = X, 3 = Z
#   DR DF        delay rise / fall (s)     WF      enable fraction for a weak driver
#   PULL_V       level of a pull moved into the deck (V)
#   PULL_E       enable toward PULL_V when no external strong driver (0 = no pull)
LVL_KEYS = ("HIV", "LOV", "X2V", "HITH", "LOTH", "XBAND", "MIDV_T", "MIDV_L", "DR", "DF",
            "WF", "PULL_V", "PULL_E")

# Driver strength classes (§5.4).
STRONG, WEAK, PULL_UP, PULL_DOWN, SUPPLY1, SUPPLY0, UNKNOWN = (
    "strong", "weak", "pull_up", "pull_down", "supply1", "supply0", "unknown")


# -- interface-element parameters (§3.4; rules.py fills them) -------------------

@dataclass
class D2A_IE:
    hiv: float
    lov: float
    rise: float = 1e-11
    fall: float = 1e-11
    delay_rise: float = 0.0
    delay_fall: float = 0.0
    x2v: int = 0                     # 0 lov, 1 hiv, 2 mid, 3 hold, 4 the level of the inverse of
    #                                  the previous digital input (PAMS p206)
    powernet: bool = False           # ideal source, no series R, no gating
    r_series: float = 500.7          # rmap strength 6 (strong)
    weak_frac: float = 500.7 / 3500.2    # enable for a weak (pull-strength) driver


@dataclass
class A2D_IE:
    loth: float
    hith: float
    xband: Optional[float] = None    # PAMS p189 divisor; shapes the midv window only
    midv_time: Optional[float] = None
    midv_logic: str = "X"            # '0' | '1' | 'X' | 'Z'


@dataclass
class RuleHits:
    """Which control-file selectors matched something (for [MSV-IE-OPT-TNF]).

    Keys are "<kind>#<index in its AmsConfig list>": rule#3, remove_d2a#0,
    disable_ie#1, ref_voltage#0, vdd#3, vss#3, use_spice_inst#2.0,
    port_connect_inst#1.  Whoever matches a selector marks it.
    """
    counts: Dict[str, int] = field(default_factory=dict)

    def mark(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1

    def hit(self, key: str) -> bool:
        return self.counts.get(key, 0) > 0


# -- cells and ports --------------------------------------------------------------

@dataclass
class CutPort:
    index: int                       # position in the shell's port list
    verilog: str                     # Verilog port name, spelled as the shell spells it
    kind: str                        # LOGIC | REAL
    declared: str                    # INPUT | OUTPUT | INOUT | AUTO (as configured)
    shell_dir: str = INOUT           # INPUT | OUTPUT | INOUT: the §5.1 probe result (= declared unless AUTO)
    range_text: Optional[str] = None # "[W-1:0]" as written (multi-view), None for scalars
    msb: Optional[int] = None        # concrete for SPICE-only cells; None if parameter-dependent
    lsb: Optional[int] = None


@dataclass
class PortMap:
    explicit: Dict[str, str] = field(default_factory=dict)   # "p" | "p[i]" -> spice port | "snps_open"
    default: str = "snps_by_name"                            # snps_by_name | snps_by_position | snps_open
    bus_formats: List[str] = field(default_factory=lambda: ["[%d]"])


@dataclass
class CutCell:
    name: str                        # Verilog module spelling (matched exactly against provenance comments)
    view: str                        # 'spice' (SPICE-only, shadow module) | 'multi' (use_spice over a Verilog view)
    subckt: str                      # default subckt (lowercased IR name)
    ports: List[CutPort] = field(default_factory=list)
    portmap: PortMap = field(default_factory=PortMap)        # default for instances no -inst statement covers
    connects: Dict[str, str] = field(default_factory=dict)   # spice port -> deck net | 'snps_open'
    params: Dict[str, str] = field(default_factory=dict)     # multi-view: header parameter defaults (text)
    origin: str = ""


@dataclass
class VariantBind:
    entity: str                      # VHDL entity of the variant
    clone: str                       # <entity>__vams
    vhdl_ports: List[str] = field(default_factory=list)      # by CutPort.index
    ranges: List[Optional[Tuple[int, int]]] = field(default_factory=list)
    # Verilog (left, right) per port, evaluated with this variant's parameters; None = scalar.
    # VHDL ranges are always (w-1 downto 0): VHDL index == bit offset from the right bound.
    vhdl_vector: List[bool] = field(default_factory=list)    # False when VHDL made the port a scalar ([0:0])
    params: Dict[str, str] = field(default_factory=dict)     # from attribute nvc_verilog_params


@dataclass
class CutInstance:
    labels: List[str]                # VHDL labels below the top
    vpath: str                       # Verilog hierarchical path, top included ("tb.dut.g[1].x")
    path_name: str                   # VHDL 'path_name, ":tb:dut:x_i1:"
    cell: str
    variant: str
    subckt: str                      # per path (use_spice -inst c:s)
    portmap: Optional[PortMap] = None   # None: the cell's default
    spice: Dict[Tuple[int, int], Optional[str]] = field(default_factory=dict)
    # (port index, bit offset) -> SPICE port (from portmap.bind_bits), None = open
    params: Dict[str, str] = field(default_factory=dict)
    # every elaborated parameter value of this path's variant, as tgt-vhdl printed it (§5.4);
    # cut.param_overrides picks those that differ from the cell's defaults
    xname: str = ""                  # deck X instance name (deck.py)
    port_nodes: Dict[str, str] = field(default_factory=dict) # spice port -> deck node (deck.py)


@dataclass(frozen=True)
class PortRef:
    inst: int                        # index into CutAnalysis.instances
    port: int                        # CutPort.index
    bit: int                         # offset from the right bound (0 for scalars)


@dataclass
class Driver:
    strength: str                    # STRONG WEAK PULL_UP PULL_DOWN SUPPLY1 SUPPLY0 UNKNOWN
    origin: str                      # "<arch>:<statement label or line>"
    removable: bool = False          # a static pull that can be moved into the deck
    stmt: str = ""                   # vhdl.py statement id (for removal)


@dataclass
class Net:
    key: str                         # unique, stable within one analysis
    aliases: List[str] = field(default_factory=list)   # Verilog-style names (§3.1)
    ports: List[PortRef] = field(default_factory=list) # walk order
    passive: List[PortRef] = field(default_factory=list)  # bits mapped to ground / port_connect'ed SPICE ports
    drivers: List[Driver] = field(default_factory=list)
    readers: int = 0
    variable: bool = False           # the trace passed a reg/logic/bit declaration (VCS would split it)
    origins: List[str] = field(default_factory=list)


@dataclass
class AnalogNode:
    name: str                        # deck node name (names.node); <name>_d / <name>_e are reserved with it
    canonical: str                   # VCS-style IE name
    aliases: List[str]
    net: Optional[str]               # Net.key, None for private nodes
    ports: List[PortRef]
    role: str
    host: Optional[PortRef] = None   # the cut-port bit whose clone hosts the bridges
    d2a: Optional[D2A_IE] = None
    a2d: Optional[A2D_IE] = None
    dc: Optional[float] = None       # POWERNET / REMOVED dc= value
    shunt: bool = False
    pull: Optional[str] = None       # 'up' | 'down': a static pull moved into the deck (BIDIR)
    report: List[str] = field(default_factory=list)  # IE-report comment lines (levels source, trace, direction, ...)


@dataclass
class Bridge:
    kind: str                        # 'd2a' | 'en' | 'a2d'
    name: str                        # registry name, with __d / __e / __a
    vhdl_path: str                   # ".<labels>.vb<k>_<b>_<d|e|a>"
    node: str                        # deck node the source sits on (<n>_d, <n>_e or <n>)
    host: PortRef
    rise: float = 1e-11
    fall: float = 1e-11

    @property
    def direction(self) -> str:
        return "A2D" if self.kind == "a2d" else "D2A"


@dataclass
class CutAnalysis:
    top: str
    cells: Dict[str, CutCell] = field(default_factory=dict)
    instances: List[CutInstance] = field(default_factory=list)   # walk order
    variants: Dict[str, VariantBind] = field(default_factory=dict)
    nets: List[Net] = field(default_factory=list)
    notes: List[Note] = field(default_factory=list)


@dataclass
class AmsPlan:
    analysis: CutAnalysis
    nodes: List[AnalogNode] = field(default_factory=list)
    bridges: List[Bridge] = field(default_factory=list)
    notes: List[Note] = field(default_factory=list)


@dataclass
class CutEmitResult:
    vhdl_path: str                   # ams/cut.vhd
    boundary_path: str               # ams/vamos.boundary
    clones: Dict[str, str] = field(default_factory=dict)    # variant entity -> clone entity
    repointed: List[str] = field(default_factory=list)      # "<arch>:<label>" statements re-pointed
