"""Every generated name (docs/VAMOS_AMS_DESIGN.md §5.6).

The deck, the boundary file, the cut VHDL, ams.json and the IE report all get
their names from here, so the two sides of the co-simulation cannot drift
apart.  Bridge names are matched with an exact, case-sensitive strcmp by
libcosim_bridge; boundary paths are case-insensitive.
"""

from __future__ import annotations

import hashlib
import re
from typing import Dict, Iterable, List, Sequence

from vamos.ams.model import (A2D, BIDIR, D2A, RA2D, RD2A, REAL, AmsPlan, Bridge, D2A_IE, PortRef)
from vamos.netlist.numbers import fmt
from vamos.notes import NoteError, error

MAX_BRIDGE = 200          # registry truncates at 255; keep well clear
HASH_KEEP = 150
MAX_FIELD = 511           # vamos's own limit on a boundary path (nvc allows 4095 since P4)
MAX_LINE = 1000           # vamos's own limit on a boundary line (nvc has none since P4)
BRIDGE_SUFFIX = {"d2a": "__d", "en": "__e", "a2d": "__a"}
_NODE_BAD = re.compile(r"[^a-z0-9_]")
_BRIDGE_BAD = re.compile(r"[^A-Za-z0-9_.\[\]<>]")


class NameAllocator:
    """Collision-free names for one deck, case-insensitively.

    Seed it with every user name the deck already contains (top-level nodes,
    instances, models, subckts) before allocating anything.
    """

    def __init__(self, seed: Iterable[str] = ()):
        self.used = set()
        for n in seed:
            self.used.add(n.lower())

    def reserve(self, name: str) -> None:
        self.used.add(name.lower())

    def free(self, name: str) -> bool:
        return name.lower() not in self.used

    def take(self, want: str) -> str:
        """want, or want_<k> for the smallest k that is free."""
        return self.take_group(want, ("",))[0]

    def take_group(self, base: str, suffixes: Sequence[str]) -> List[str]:
        """A base for which base+s is free for every suffix s; reserves all of them.

        Used for nodes, whose derived names (<n>_d, <n>_e) must not collide
        with a user node either (bit 1 of port a vs a scalar port a_1, <n>_d vs
        a port a_d).
        """
        k = 0
        while True:
            b = base if k == 0 else "%s_%d" % (base, k)
            names = [b + s for s in suffixes]
            if all(self.free(n) for n in names):
                for n in names:
                    self.reserve(n)
                return names
            k += 1


def _strip_top(canonical: str) -> str:
    parts = canonical.split(".", 1)
    return parts[1] if len(parts) == 2 else parts[0]


def node_base(canonical: str) -> str:
    """Deck node base for an analog node: n_ + canonical without its top component."""
    return "n_" + _NODE_BAD.sub("_", _strip_top(canonical).lower())


def node(alloc: NameAllocator, canonical: str) -> str:
    """Allocate the node and its derived <n>_d / <n>_e names together."""
    return alloc.take_group(node_base(canonical), ("", "_d", "_e"))[0]


def inst(alloc: NameAllocator, prefix: str, node_name: str) -> str:
    """Generated element names (prefix includes the '_'):
    vd_ ve_ g_ ia_ rsh_ vp_ (pull source) vs_ (powernet/removed source) nc_."""
    return alloc.take(prefix + node_name)


def xname(alloc: NameAllocator, vpath: str) -> str:
    """Deck X instance for a cut instance."""
    return alloc.take("xv_" + _NODE_BAD.sub("_", _strip_top(vpath).lower()))


def bridge_base(canonical: str) -> str:
    """Registry base name: canonical restricted to [A-Za-z0-9_.[]<>], hashed if long."""
    s = _BRIDGE_BAD.sub("_", canonical)
    if len(s) > MAX_BRIDGE:
        h = hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]
        s = s[:HASH_KEEP] + "_h" + h
    return s


def bridge(base: str, kind: str) -> str:
    return base + BRIDGE_SUFFIX[kind]


def vb_signal(port_index: int, bit: int, kind: str) -> str:
    """Bridge signal inside a clone architecture; kind 'd' | 'e' | 'a'.

    A real port is one slot with bit 0 (vb<k>_0_d / vb<k>_0_a)."""
    assert kind in ("d", "e", "a"), kind
    return "vb%d_%d_%s" % (port_index, bit, kind)


def boundary_path(labels: Sequence[str], signal: str) -> str:
    """Boundary-file path: dot-separated labels below the top, then the signal."""
    return "." + ".".join(list(labels) + [signal])


def path_name(top: str, labels: Sequence[str]) -> str:
    """VHDL 'path_name of a cut instance's clone, as nvc reports it."""
    return ":" + ":".join([top] + list(labels)).lower() + ":"


def clone_entity(variant: str) -> str:
    return variant + "__vams"


def build_bridges(plan: AmsPlan) -> List[Bridge]:
    """Every bridge of the plan, at each node's host bit (§5.7).

    D2A (gated): value on <n>_d, enable on <n>_e.  D2A with d2a.powernet and
    RD2A: an ideal value source on <n> itself, no enable.  A2D / RA2D: a probe
    on <n>.  BIDIR: the gated D2A pair plus the probe.  Raises NoteError on a
    bridge name or boundary field the nvc registry could not hold.
    """
    out: List[Bridge] = []
    bases: Dict[str, str] = {}
    problems = []
    ana = plan.analysis
    for n in plan.nodes:
        if n.role not in (D2A, A2D, BIDIR, RD2A, RA2D) or n.host is None:
            continue
        h: PortRef = n.host
        ci = ana.instances[h.inst]
        cell = ana.cells[ci.cell]
        bit = 0 if cell.ports[h.port].kind == REAL else h.bit
        base = bridge_base(n.canonical)
        if base in bases:
            k = 1
            while "%s_%d" % (base, k) in bases:
                k += 1
            base = "%s_%d" % (base, k)
        bases[base] = n.name
        ie = n.d2a or D2A_IE(0.0, 0.0)
        rise, fall = ie.rise, ie.fall

        def add(kind: str, sig: str, on: str) -> None:
            b = Bridge(kind, bridge(base, kind), boundary_path(ci.labels, vb_signal(h.port, bit, sig)),
                       on, h, rise, fall)
            if len(b.vhdl_path) > MAX_FIELD:
                problems.append(error(ci.vpath, "boundary path of %d characters exceeds %d"
                                      % (len(b.vhdl_path), MAX_FIELD)))
            out.append(b)

        if n.role in (D2A, BIDIR):
            if n.role == D2A and ie.powernet:
                add("d2a", "d", n.name)
            else:
                add("d2a", "d", n.name + "_d")
                add("en", "e", n.name + "_e")
        if n.role == RD2A:
            add("d2a", "d", n.name)
        if n.role in (A2D, BIDIR, RA2D):
            add("a2d", "a", n.name)
    if problems:
        raise NoteError(problems)
    return out


def boundary_line(b: Bridge) -> str:
    """One boundary-file line for a bridge (§5.5); D2A lines carry ramp columns."""
    if b.kind == "a2d":
        line = "A2D %s %s" % (b.vhdl_path, b.name)
    else:
        line = "D2A %s %s rise=%s fall=%s" % (b.vhdl_path, b.name, fmt(b.rise), fmt(b.fall))
    if len(line) > MAX_LINE:
        raise NoteError([error(b.vhdl_path, "boundary line of %d characters exceeds %d"
                               % (len(line), MAX_LINE))])
    return line
