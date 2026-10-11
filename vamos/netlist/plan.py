"""The run plan (docs/VAMOS_SPECTRE_DESIGN.md §5; the API is the §10 block).

Phase 0 (§12): the dataclasses are frozen; build() is the signature S2 implements in phase 1
and raises NotImplementedError until then.

plan.build walks Netlist.analyses in netlist order and produces an ordered list of Actions
(§5.1).  The plan speaks IR names only ('.' paths, IR card names, IR parameter names); render
translates them (§4.5 item 5).  Values are evaluated: a float, a str for an enumeration or a
file name, a List[float] for a vector (Setting).  Every Target is a 3-tuple.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, Union

from vamos.netlist.ir import Netlist, SaveSpec, SweepSpec
from vamos.notes import Note
from vamos.spectre.job import Settings

# Always three: ("instance", "x1.r1", "r") | ("model", "nch", "vth0") | ("option", "temp" | "tnom", "")
# | ("variable", "rval", "") | ("freq", "", "")   (§5.1)
Target = Tuple[str, str, str]
# An evaluated value: a number, an enumeration or file name, a vector (§5.1)
Setting = Union[float, str, List[float]]


@dataclass
class SweepLevel:
    """One sweep a step runs under (§5.2)."""
    name: str                   # the Spectre sweep's name (an enclosing sweep block); "" for the step's own sweep
    id: str                     # vamos_w<k>: the VACASK sweep statement and its rawfile column (§7.1)
    target: Target
    spec: SweepSpec             # evaluated, engine-ready: mode lin | step | values, or dec for an integral span.
    #                             Invariant after plan.build: every Expr field of it (start, stop, step, each
    #                             element of values) is a folded Num; S3 may rely on it.  S2's tests pin it
    continuation: int = 1       # VACASK continuation (§5.2)
    label: str = ""             # the PSF SWEEP variable: the parameter in a leaf ("dc" for a source, "temp", "r",
    #                             "<param>", "freq"), "<dev>:<param>" in a parent ("R1:r") (§8.3)
    desc: str = ""              # the variable as descriptions write it: "Vin:dc", "temp", "<param>", "<model>:<param>"
    units: str = ""             # its PSF units: "V", "Ohm", "C", "", "Hz" ... (§8.3)


@dataclass
class AnalysisStep:
    id: str                       # vamos_a<k>
    name: str                     # Spectre name (child name inside sweeps)
    kind: str                     # op dc ac noise xf tran
    context: List[SweepLevel] = field(default_factory=list)   # enclosing Spectre sweeps, outermost first
    own: Optional[SweepLevel] = None   # a dc target; an ac/noise/xf parameter at a fixed freq, or their frequency axis
    args: Dict[str, Setting] = field(default_factory=dict)    # §3.11's names, evaluated; tran: errpreset applied (§5.1)
    options: Dict[str, Setting] = field(default_factory=dict) # the global options in force at this step (§5.1)
    saves: Optional[List[SaveSpec]] = None
    full_solution: bool = False   # write=/writefinal=: save every node (§5.4)
    noise_input: Optional[str] = None   # IR name of the input source; vamos_nin<k> when the netlist gives none (§5.6)
    stores: Optional[str] = None  # prevoppoint/useprevic stored-solution names
    uses: Optional[str] = None


@dataclass
class Action:
    """One step of the plan (§5.1).  `args` holds the keys of §5.1's table, per `op`:

      op        args
      options   {<option key>: Setting} for each option that changes: §3.10's keys with an engine
                meaning, as Spectre spells them (temp tnom gmin reltol vabstol iabstol chargeabstol).
                The first action holds the whole global set (argv + options + defaults and
                +paramdefault, §2.2); each `set` statement gives one more
      var       {<top-level parameter>: float}: the plan variables (§4.5 item 4) at their nominal
                values; one action, before the first analysis
      alter     {"target": Target, "value": float or str}, plus "source": Source when the target is a
                field of an independent source: the source re-resolved by spectre.resolve_source with
                the new value (§5.3).  An alter of temp or tnom is an alter with an ("option", ...)
                target, not an options action
      source    {"path": <IR path of the source>, "type": "dc" or the waveform type, "source": Source}:
                a VACASK source-state switch (§5.3)
      analysis  {}; the step is Action.step
      note      {"note": Note}: reported by flow in netlist order (an info statement, a skipped
                construct); render prints nothing

    Names are IR names; render translates them (§4.5 item 5).  Render (S3), the results (S4) and
    phase 2's Xyce state replay read exactly these keys.
    """
    op: str                       # options | var | alter | source | analysis | note
    args: Dict[str, object] = field(default_factory=dict)     # the keys of §5.1's table, per op (docstring)
    step: Optional[AnalysisStep] = None
    origin: str = ""


@dataclass
class RunPlan:
    actions: List[Action] = field(default_factory=list)
    variables: Dict[str, float] = field(default_factory=dict)
    overridden: Dict[str, Set[str]] = field(default_factory=dict)  # subckt -> parameters sub= targets make overridable
    options: Dict[str, object] = field(default_factory=dict)       # the global set, as the first options action has it
    notes: List[Note] = field(default_factory=list)
    dependents: Set[str] = field(default_factory=set)   # filled by build: variables and every top-level Param that
    #                                                     reads one (§4.1); signals.resolve only reads it
    engine: str = "vacask"


def build(nl: Netlist, engine: str, settings: Settings) -> RunPlan:
    """The ordered plan of a Spectre netlist for one engine (§5): per-path and per-state evaluation
    on tables.path_envs, the sweep grids, alters and source states, the options actions, dependents."""
    raise NotImplementedError("plan.build is implemented in phase 1 (S2; design §5, §10)")
