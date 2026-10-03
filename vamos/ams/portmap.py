"""Verilog port bit <-> SPICE subckt port (docs/VAMOS_AMS_DESIGN.md §5.1).

Explicit port_map items first, then the default rule: by name (scalar p <-> p;
bit p[i] <-> p + each bus_format with %d = i, the first that exists), by
position (Verilog bits in declaration order against the ORIGINAL .subckt port
list: before the ground pass, port_connect'ed ports included), or open.
Case-insensitive.  Bits are identified by their offset from the right bound
of the Verilog range (0 for scalars), which is also the VHDL index (tgt-vhdl
declares every vector (w-1 downto 0)) and the bridge-name numbering.

The caller classifies the result: a bit mapped to a ground or port_connect'ed
SPICE port is passive (no node of its own, no IE).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from vamos.ams.model import CutCell, PortMap
from vamos.notes import Note, NoteError, error

OPEN = "snps_open"
Range = Optional[Tuple[int, int]]          # (left, right) as declared; None = scalar


def bits(rng: Range) -> List[int]:
    """Bit indices in declaration order (left bound first)."""
    if rng is None:
        return [0]
    left, right = rng
    step = -1 if left >= right else 1
    return list(range(left, right + step, step))


def offset(bit: int, rng: Range) -> int:
    """Offset of a bit from the right bound (the bridge-name numbering, the VHDL index)."""
    return 0 if rng is None else abs(bit - rng[1])


def bit_of(off: int, rng: Range) -> int:
    """Verilog index of an offset: right + off if left > right, else right - off."""
    if rng is None:
        return 0
    left, right = rng
    return right + off if left >= right else right - off


def bus_name(base: str, fmt: str, i: int) -> str:
    return base + fmt.replace("%d", str(i))


def bind_bits(cell: CutCell, pm: Optional[PortMap], ranges: Sequence[Range],
              orig_ports: Sequence[str]) -> Dict[Tuple[int, int], Optional[str]]:
    """Map every (port index, bit offset) of one variant to a SPICE port or None (open).

    pm: the instance's port map (None: the cell's default).
    ranges: Verilog (left, right) per CutPort.index for this variant.
    orig_ports: the subckt's ports as written (Subckt.orig_ports, lowercased).
    Raises NoteError listing every problem.
    """
    pm = pm or cell.portmap
    allp = [p.lower() for p in orig_ports]
    pset = set(allp)
    explicit = {k.lower(): v.lower() for k, v in pm.explicit.items()}
    fmts = pm.bus_formats or ["[%d]"]
    out: Dict[Tuple[int, int], Optional[str]] = {}
    positional: List[Tuple[int, int]] = []
    problems: List[Note] = []

    def rng_of(k: int) -> Range:
        return ranges[k] if k < len(ranges) else None

    def by_name(base: str, rng: Range, i: int) -> Optional[str]:
        if rng is None:
            return base if base in pset else None
        for f in fmts:
            cand = bus_name(base, f, i)
            if cand in pset:
                return cand
        return None

    for port in cell.ports:
        k = port.index
        rng = rng_of(k)
        name = port.verilog.lower()
        for b in bits(rng):
            off = offset(b, rng)
            key_bit = name if rng is None else "%s[%d]" % (name, b)
            if key_bit in explicit:
                target: Optional[str] = None if explicit[key_bit] == OPEN else explicit[key_bit]
                if target is not None and target not in pset:
                    problems.append(error(cell.origin, "port_map %s => %s: no such port on subckt %s"
                                          % (key_bit, target, cell.subckt)))
                out[(k, off)] = target
                continue
            if rng is not None and name in explicit:
                t = explicit[name]
                if t == OPEN:
                    out[(k, off)] = None
                    continue
                target = by_name(t, rng, b)
                if target is None:
                    problems.append(error(cell.origin, "port_map %s => %s: no SPICE port for bit %d"
                                          % (name, t, b)))
                out[(k, off)] = target
                continue
            if pm.default == OPEN:
                out[(k, off)] = None
            elif pm.default == "snps_by_position":
                positional.append((k, off))
            else:
                target = by_name(name, rng, b)
                if target is None:
                    problems.append(error(cell.origin, "cell %s: Verilog port %s has no SPICE port on "
                                          "subckt %s (add port_map or snps_open)"
                                          % (cell.name, key_bit, cell.subckt)))
                out[(k, off)] = target

    if pm.default == "snps_by_position":
        # Every Verilog bit owns a position, explicitly mapped ones included;
        # the explicit target overrides, the positional port is then unused.
        order = [(p.index, offset(b, rng_of(p.index))) for p in cell.ports for b in bits(rng_of(p.index))]
        if len(order) != len(allp):
            problems.append(error(cell.origin, "cell %s: snps_by_position maps %d Verilog bits onto the "
                                  "%d ports of subckt %s" % (cell.name, len(order), len(allp), cell.subckt)))
        pos = dict(zip(order, allp))
        for key in positional:
            if key in pos:
                out[key] = pos[key]

    seen: Dict[str, Tuple[int, int]] = {}
    for key, sp in sorted(out.items()):
        if sp is None:
            continue
        if sp in seen:
            problems.append(error(cell.origin, "cell %s: SPICE port %s is mapped twice"
                                  % (cell.name, sp)))
        seen[sp] = key
    if problems:
        raise NoteError(problems)
    return out
