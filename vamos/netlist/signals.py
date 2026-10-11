"""Signals and names (docs/VAMOS_SPECTRE_DESIGN.md §6; the API is the §10 block).

Phase 0 (§12): Ref, SignalMap and the psf type values are frozen; resolve() is the signature S2
implements in phase 1 and raises NotImplementedError until then.

signals.resolve lists, per step, the requested signals as (Ref, Spectre name, psf type) in PSF
order (§6.4); render records each Ref's engine column in `names` (§4.5 item 6), the only
source of engine columns.  It returns a copy.deepcopy of the netlist with the probes inserted
and Netlist.values reduced per engine from RunPlan.dependents (§4.1), never the input IR.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

from vamos.netlist.ir import Netlist
from vamos.netlist.plan import RunPlan
from vamos.notes import Note

# ('v', node path) | ('i', element path) | ('noise', instance path) | ('onoise',) | ('inoise',)
# | ('gain',) | ('tf', source path); IR names, '.' hierarchy (§6.4)
Ref = Tuple[str, ...]

# The psf type of a signal (§6.4, §8.3, §8.4): the PSF TRACE type name.  Besides these fixed names,
# a per-instance noise Ref carries the instance's STRUCT type name (its model, else its master, a
# collision suffixed, §8.4).
PSF_V = "V"                    # a node voltage
PSF_I = "I"                    # a current
PSF_V_NOISE = "V/sqrt(Hz)"     # noise out; noise in with a vsource input
PSF_A_NOISE = "A/sqrt(Hz)"     # noise in with an isource input
PSF_GAIN_VV = "V/V"            # gain, by the input's kind
PSF_GAIN_VA = "V/A"
PSF_TYPES = (PSF_V, PSF_I, PSF_V_NOISE, PSF_A_NOISE, PSF_GAIN_VV, PSF_GAIN_VA)


@dataclass
class SignalMap:
    per_step: Dict[str, List[Tuple[Ref, str, str]]] = field(default_factory=dict)   # step id -> (Ref, Spectre
    #                                                                                  name, psf type), PSF order
    allpub: Set[str] = field(default_factory=set)       # step ids printed as save default / V(*) (§6.4)


def resolve(nl: Netlist, plan: RunPlan) -> Tuple[Netlist, SignalMap, List[Note]]:
    """The render copy (a copy.deepcopy of nl with the probes, Netlist.values reduced per engine from
    plan.dependents, §4.1), the SignalMap and the notes (§6)."""
    raise NotImplementedError("signals.resolve is implemented in phase 1 (S2; design §6, §10)")
