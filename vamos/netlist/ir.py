"""The analog netlist IR (frozen contract, docs/VAMOS_AMS_DESIGN.md §4.2;
Spectre extensions: docs/VAMOS_SPECTRE_DESIGN.md §4.1 and the §10 block, phase 0).

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

Spectre extensions (VAMOS_SPECTRE_DESIGN.md §4.1; every one additive: each new
field is appended after its class's last field with a default, so today's
positional call sites bind as before, and the new classes are defined above
Item).  spectre.parse() sets Netlist.dialect = "spectre"; spice.parse() leaves
"hspice" for both of its dialects.  IR copies are made with copy.deepcopy, never
dataclasses.replace (§4.1).  The IR objects are mutable and unhashable (walks key
them by id()); the expr_ast classes are frozen and hashable.

Spectre analyses (§3.11, §4.1 item 1): Analysis.kind is one of
  op dc ac noise xf tran sweep montecarlo alter altergroup info set
Analysis.sweep carries the analysis's own swept variable as a SweepSpec (§4.1
item 2: start stop center span step lin dec log values valuesfile dev mod sub
param, kept as written; center/span become start/stop, a valuesfile is read into
values).  Analysis.args holds the other parameters under §3.11's names, lower
case as Spectre spells them: numbers as Expr (folded to Num when constant),
enumerations and file names as Str, vectors as lists of Expr.  The §3.11 names:
  dc     readns write writefinal useprevic save nestlvl print oppoint force
         readforce restart swpuseprevic homotopy newton maxsteps swp1stpointic
         maxiters hysteresis check annotate title emir*
  ac     the dc names, plus freq prevoppoint skipdc perturbation out1 out2
         contriblist rf* flin_out fim_out maxharm_nonlin
  noise  the ac names, plus oprobe oportv iprobe iportv separatenoise; the
         output node pair is Analysis.nodes
  xf     the ac names, plus probe stimuli; the output node pair is Analysis.nodes
  tran   stop start outputstart step maxstep minstep istep pstep tpoints
         readtime ic skipdc readic read readns write writefinal useprevic
         linearic rampup* oscfreq cmin method errpreset relref lteratio
         maxstepratio reltolratio maxiters transres restart skipstart skipstop
         skipcount strobe* infonames infotimes infotime_pair acnames actimes
         actime_pair ckptperiod saveperiod saveclock savetime savefile recover
         circuitage noisefmax param paramset param_vec param_file sub annotate*
         title progress_* compression comp* complvl flush* fastbreak d2a*
         fastcross lteminstep ltethstep vref* iref* emir* autostop dcmaxiters
  sweep  the dc sweep names plus sub, paramset faults* distribute numprocesses
         savedatainseparatedir annotate title
The HSPICE path keeps today's floats: tran: step stop start maxstep, uic (bool).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Sequence, Tuple, Union

from vamos.netlist.expr_ast import Expr
from vamos.notes import Note

MODEL_KINDS = ("nmos", "pmos", "npn", "pnp", "njf", "pjf", "d", "r", "c", "l")

# A Spectre parameter value (VAMOS_SPECTRE_DESIGN.md §4.1 item 11): numbers as Expr, vectors as
# lists of Expr; enumerations and file names are Str.
Value = Union[Expr, List[Expr]]


@dataclass
class ParseOpts:
    dialect: str = "hspice"          # hspice | spice; "spectre-spice" is accepted by spice.parse_fragment
    #                                  only, never by spice.parse (VAMOS_SPECTRE_DESIGN.md §4.1 item 10)
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
    # -- Spectre (VAMOS_SPECTRE_DESIGN.md §4.1 items 6, 12) --
    bin_rule: Optional[str] = None # "spectre": exact bounds, group order and total w
    prim: str = ""                 # the Spectre master (mos1 bsim3v3 diode bjt ...); "" on the HSPICE path


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
    # -- Spectre (VAMOS_SPECTRE_DESIGN.md §3.8.1, §4.1 item 11): every parameter written on the
    # vsource/isource, in Spectre names, whatever its type --
    spectre: Dict[str, Value] = field(default_factory=dict)


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
    # -- Spectre (VAMOS_SPECTRE_DESIGN.md §4.1 items 7, 15) --
    prim: str = ""                 # the Spectre primitive master (vsource resistor ... or a VA module)
    folded: List[str] = field(default_factory=list)   # instance parameters that fed a value computed at
    #                                                   parse time (a resistor's w/l ...): never swept/altered


@dataclass
class Subckt:
    name: str
    ports: List[str]                                         # after the ground pass
    params: List[Param] = field(default_factory=list)       # header + body, merged, sorted
    body: List["Item"] = field(default_factory=list)
    orig_ports: List[str] = field(default_factory=list)     # as written (lowercased), before the ground pass
    gnd_ports: List[int] = field(default_factory=list)      # indices into orig_ports removed by the ground pass
    origin: str = ""
    # folded port name -> this definition's header spelling (Netlist.spelling keeps one
    # spelling per folded name for the whole netlist, the first definition's)
    spelling: Dict[str, str] = field(default_factory=dict)
    # -- Spectre (VAMOS_SPECTRE_DESIGN.md §4.1 item 5) --
    inline: bool = False


@dataclass
class Analysis:
    kind: str                      # tran | op | dc | ac; Spectre adds noise xf sweep montecarlo alter
    #                                altergroup info set (module docstring)
    args: Dict[str, object] = field(default_factory=dict)   # tran: step stop start maxstep (floats), uic (bool)
    origin: str = ""
    # -- Spectre (VAMOS_SPECTRE_DESIGN.md §4.1 item 1) --
    name: str = ""                 # the analysis instance name (SPICE mode: opBegin, timeSweep ...)
    sweep: Optional[SweepSpec] = None                 # the analysis's own swept variable
    nodes: List[str] = field(default_factory=list)   # the output node pair of noise or xf
    children: List["Analysis"] = field(default_factory=list)   # the body of a sweep or montecarlo, in order
    spice: bool = False            # written in SPICE syntax (decides the output naming)


# -- Spectre classes (VAMOS_SPECTRE_DESIGN.md §10; defined above Item) ---------------------------

@dataclass
class VaModule:
    """One Verilog-A module (§3.1, §4.5 items 12-13): spice.va_modules fills Netlist.va_modules
    with it (§4.1 item 16)."""
    name: str
    path: str                                     # the file that declares it, possibly an `included one
    params: Optional[List[str]] = None            # declared parameter names, as written, from va_modules'
    #                                               preprocessor (§3.1); None: unknown (passed unchecked)
    attrs: Dict[str, str] = field(default_factory=dict)   # xyceModelGroup xyceLevelNumber xyceTypeVariable
    #                                                       xycePTypeValue


@dataclass
class SweepSpec:
    """A swept variable (§4.1 item 2, §5.2); names are IR names, resolved by the parser."""
    target: str                                   # dev | mod | sub | param | freq (temperature: param "temp")
    name: str = ""                                # IR name of the instance, card or subckt instance
    param: str = ""
    mode: str = ""                                # lin | dec | log | step | values
    start: Optional[Expr] = None
    stop: Optional[Expr] = None
    step: Optional[Expr] = None
    count: Optional[int] = None
    values: List[Expr] = field(default_factory=list)
    origin: str = ""


@dataclass
class SaveSpec:
    """A Spectre save statement (§4.1 item 3; ref19 p.517); HSPICE probes stay in Netlist.probes."""
    items: List[str]                              # raw tokens, as written
    depth: Optional[int] = None
    sigtype: str = "node"
    devtype: Optional[str] = None
    subckt: Optional[str] = None
    exclude: List[str] = field(default_factory=list)
    probelvl: Optional[int] = None
    time_window: List[float] = field(default_factory=list)   # t1 t2 t3 t4 ...: interval pairs
    ports: bool = False
    filter: str = "none"                          # none | rc
    origin: str = ""


@dataclass
class ParamTest:
    """A paramtest statement (§4.1 item 4); the emitters print nothing for it."""
    name: str
    tests: List[Tuple[str, Expr]] = field(default_factory=list)   # (printif | warnif | errorif, condition)
    message: str = ""
    severity: str = ""
    origin: str = ""


@dataclass
class Cond:
    """An if / else if / else statement (§4.1 item 4); a model, parameter or subckt cannot be in a
    branch, so BranchItem is Instance, Cond or ParamTest."""
    branches: List[Tuple[Expr, List["BranchItem"]]]
    default: List["BranchItem"] = field(default_factory=list)      # an else-if chain nests a Cond here
    origin: str = ""


@dataclass
class Vary:
    """One vary line of a statistics block (§4.1 item 8; ref19 pp.176-179)."""
    param: str
    dist: str = "gauss"                           # gauss | lnorm | unif
    std: Optional[Expr] = None
    n: Optional[Expr] = None
    percent: bool = False
    origin: str = ""


@dataclass
class Correlate:
    """One correlate line of a statistics block (§4.1 item 8)."""
    params: List[str]
    devs: List[str] = field(default_factory=list) # may hold * patterns
    cc: Optional[Expr] = None
    origin: str = ""


@dataclass
class StatBlock:
    """One process, mismatch or block-level part of a statistics block (§4.1 item 8; parse-only in v1)."""
    kind: str                                     # process | mismatch | statistics
    varies: List[Vary] = field(default_factory=list)
    correlates: List[Correlate] = field(default_factory=list)
    truncate: Optional[Expr] = None
    origin: str = ""


Item = Union[Instance, Model, Param, Subckt, Cond, ParamTest]
BranchItem = Union[Instance, Cond, ParamTest]
ITEM_TYPES = (Instance, Model, Param, Subckt, Cond, ParamTest)   # what a walk checks before it raises


def flat_items(items: Sequence[Item]) -> Iterator[Item]:
    """Each item in order; for a Cond the Cond itself, then the items of each branch and of the
    default, recursively (§4.1 item 4).  Never enters a Subckt body: walks open a Scope per subckt."""
    for it in items:
        yield it
        if isinstance(it, Cond):
            for _cond, body in it.branches:
                yield from flat_items(body)
            yield from flat_items(it.default)


def flat_analyses(analyses: Sequence[Analysis]) -> Iterator[Analysis]:
    """Each analysis in order, followed by the children of a sweep or montecarlo block,
    recursively (§4.1 item 1): Netlist.tran() sees only the top level."""
    for a in analyses:
        yield a
        yield from flat_analyses(a.children)


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
    # Spectre values are expressions (§4.1 item 14); the HSPICE path stores floats
    ics: Dict[str, Union[float, Expr]] = field(default_factory=dict)
    nodesets: Dict[str, Union[float, Expr]] = field(default_factory=dict)
    hdl: List[str] = field(default_factory=list)            # absolute .va paths
    values: Dict[str, float] = field(default_factory=dict)  # evaluated top-level parameters
    spelling: Dict[str, str] = field(default_factory=dict)  # lowercased name -> original spelling
    notes: List[Note] = field(default_factory=list)
    # -- Spectre (VAMOS_SPECTRE_DESIGN.md §4.1 items 3, 8, 9, 13, 16) --
    dialect: str = "hspice"        # "spectre" only from spectre.parse; the emitters branch on it
    saves: List[SaveSpec] = field(default_factory=list)
    statistics: List[StatBlock] = field(default_factory=list)
    # top-level subckts left out because vamos cannot simulate them, with the reason; a real field so
    # that a copy keeps it (spice.py stores and reads it; spice.left_out(nl) returns it)
    left_out: Dict[str, Note] = field(default_factory=dict)
    va_modules: Dict[str, VaModule] = field(default_factory=dict)   # every module of hdl, by lower-cased name

    # -- helpers shared by every consumer -------------------------------------

    def subckts(self) -> Dict[str, Subckt]:
        """Top-level subckt definitions by name (nested definitions stay in their parent's body)."""
        return {s.name: s for s in self.body if isinstance(s, Subckt)}

    def models(self) -> Dict[str, Model]:
        """Cards by name, top level and subckt bodies; does not descend into a Cond (a model cannot
        be in a branch, §4.1 item 4)."""
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
        """Top-level instances; does not descend into a Cond (use flat_items for those)."""
        return [i for i in self.body if isinstance(i, Instance)]

    def tran(self) -> Optional[Analysis]:
        """The first top-level tran only: one inside a sweep block is missed (Spectre code uses
        flat_analyses)."""
        for a in self.analyses:
            if a.kind == "tran":
                return a
        return None

    def scale(self) -> float:
        """.option scale as a float (1.0 when absent); the emitters apply it once."""
        from vamos.netlist.expr_ast import Num
        s = self.options.get("scale")
        return s.value if isinstance(s, Num) else 1.0
