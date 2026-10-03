"""The analog netlist IR (frozen contract, docs/VAMOS_AMS_DESIGN.md §4.2).

spice.parse() produces a Netlist whose dialect questions are all resolved
(duplicates, PARHIER, ordering, ground aliases, source defaults); the emitters
only print.  Geometry stays unscaled: Netlist.options["scale"] carries
.option scale and the emitters apply it exactly once.  Names are lowercased
(per set_sim_case); Netlist.spelling keeps the original spelling for messages
and reports.

Element conventions (§4.2 table):
  r c l      nodes [a, b]; value = R/C/L; master = model or None; params (w l tc1 m ...)
  k          nodes []; value = coupling; ctrl = [l1, l2]
  v i        nodes [p, n]; source = Source
  e g        linear: nodes [p, n, cp, cn]; value = gain
  f h        nodes [p, n]; value = gain; ctrl = [controlling V source]
  b          behavioral (HSPICE E/G vol= cur= value=): nodes [p, n]; expr; expr_kind 'v' | 'i'
  d          nodes [a, c]; master = model; value = area or None
  q          nodes [c, b, e(, s)]; master; value = area or None
  m          nodes [d, g, s, b]; master; params (w l ad as pd ps nrd nrs nf m ...)
  j          nodes [d, g, s]; master; value = area or None
  x          nodes [...]; master = subckt; params = overrides (m included)
  y          Verilog-A device from .hdl: nodes; master = model card or module; params
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

from vamos.netlist.expr_ast import Expr
from vamos.notes import Note

MODEL_KINDS = ("nmos", "pmos", "npn", "pnp", "njf", "pjf", "d", "r", "c", "l")


@dataclass
class ParseOpts:
    dialect: str = "hspice"
    case: str = "lower"              # set_sim_case: lower | upper | sensitive
    parhier_local: bool = False      # --vamos-parhier=local
    synth_step: float = 1e-11        # TSTEP / TSTOP for source defaults when there is no .tran
    synth_stop: float = 3600.0
    search: List[str] = field(default_factory=list)   # extra .option search directories


@dataclass
class Param:
    name: str
    expr: Expr
    origin: str = ""


@dataclass
class Model:
    name: str                      # full card name (binned: "<base>.<k>")
    kind: str                      # one of MODEL_KINDS
    level: Optional[float]
    params: Dict[str, Expr]
    origin: str = ""
    base: Optional[str] = None     # binned cards: the base name instances use
    bin_index: Optional[int] = None


@dataclass
class Source:
    dc: Optional[Expr] = None
    ac: Optional[Tuple[Expr, Expr]] = None          # (magnitude, phase)
    wave: Optional[str] = None                      # pulse | pwl | sin | exp
    args: Dict[str, Expr] = field(default_factory=dict)
    # Resolved, named fields (§4.3.8):
    #   pulse: v1 v2 td tr tf pw [per]     sin: vo va freq td theta phase
    #   exp:   v1 v2 td1 tau1 td2 tau2     pwl: td (points below)
    points: List[Tuple[Expr, Expr]] = field(default_factory=list)   # PWL (t, v)
    code_uri: Optional[str] = None
    # The complete engine URI, built only by ams/deck.py:
    #   "code:libcosim_bridge.so:<vacask_bridge_init|nvc_bridge_init>:<d2a|a2d>:<name>"
    # A Source with code_uri has no dc/ac/wave.


@dataclass
class Instance:
    name: str
    kind: str                      # see the module docstring
    nodes: List[str]
    master: Optional[str] = None
    value: Optional[Expr] = None
    params: Dict[str, Expr] = field(default_factory=dict)
    source: Optional[Source] = None
    expr: Optional[Expr] = None
    expr_kind: Optional[str] = None    # 'i' | 'v'
    ctrl: List[str] = field(default_factory=list)
    origin: str = ""


@dataclass
class Subckt:
    name: str
    ports: List[str]                                         # after the ground pass
    params: List[Param] = field(default_factory=list)       # header + body, merged, sorted
    body: List["Item"] = field(default_factory=list)
    orig_ports: List[str] = field(default_factory=list)     # as written (lowercased), before the ground pass
    gnd_ports: List[int] = field(default_factory=list)      # indices into orig_ports removed by the ground pass
    origin: str = ""


@dataclass
class Analysis:
    kind: str                      # tran | op | dc | ac
    args: Dict[str, object] = field(default_factory=dict)   # tran: step stop start maxstep (floats), uic (bool)
    origin: str = ""


Item = Union[Instance, Model, Param, Subckt]


@dataclass
class Netlist:
    title: str = ""
    body: List[Item] = field(default_factory=list)
    globals: List[str] = field(default_factory=list)
    options: Dict[str, Expr] = field(default_factory=dict)  # .option values kept for the emitters (scale, tnom, ...)
    temp: Optional[float] = None
    tnom: Optional[float] = None
    parhier: str = "global"
    analyses: List[Analysis] = field(default_factory=list)
    probes: List[Tuple[str, str, str]] = field(default_factory=list)   # (analysis, 'v'|'i', target)
    ics: Dict[str, float] = field(default_factory=dict)
    nodesets: Dict[str, float] = field(default_factory=dict)
    hdl: List[str] = field(default_factory=list)            # absolute .va paths
    values: Dict[str, float] = field(default_factory=dict)  # evaluated top-level parameters
    spelling: Dict[str, str] = field(default_factory=dict)  # lowercased name -> original spelling
    notes: List[Note] = field(default_factory=list)

    # -- helpers shared by every consumer -------------------------------------

    def subckts(self) -> Dict[str, Subckt]:
        """Top-level subckt definitions by name (nested definitions stay in their parent's body)."""
        return {s.name: s for s in self.body if isinstance(s, Subckt)}

    def models(self) -> Dict[str, Model]:
        out: Dict[str, Model] = {}
        for it in self.body:
            if isinstance(it, Model):
                out[it.name] = it
            elif isinstance(it, Subckt):
                for m in it.body:
                    if isinstance(m, Model):
                        out.setdefault(m.name, m)
        return out

    def instances(self) -> List[Instance]:
        return [i for i in self.body if isinstance(i, Instance)]

    def tran(self) -> Optional[Analysis]:
        for a in self.analyses:
            if a.kind == "tran":
                return a
        return None

    def scale(self) -> float:
        """.option scale as a float (1.0 when absent); the emitters apply it once."""
        from vamos.netlist.expr_ast import Num
        s = self.options.get("scale")
        return s.value if isinstance(s, Num) else 1.0
