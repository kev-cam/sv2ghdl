"""SPICE numbers (docs/VAMOS_AMS_DESIGN.md §4.1).

parse_number() reads HSPICE-style literals: an optional sign, a mantissa, an
optional exponent, then an optional scale suffix (case-insensitive)

    T 1e12   G 1e9   MEG/X 1e6   K 1e3   MIL 25.4e-6   M 1e-3
    U 1e-6   N 1e-9  P 1e-12     F 1e-15 A 1e-18

and any trailing unit letters, which are ignored ("500mV", "10ns", "1.2v").
Emitters always print floats (repr), so VACASK's "1M" (mega) and integer
division traps never arise.
"""

from __future__ import annotations

import re

_NUM = re.compile(r"""^\s*([+-]?(?:\d+\.?\d*|\.\d+))   # mantissa
                      (?:[eE]([+-]?\d+))?                # exponent
                      ([A-Za-z_]*)\s*$""", re.X)

# Longest first: MEG and MIL must win over M.  Decimal exponents, so the
# result is correctly rounded ("0.22u" == 2.2e-07 exactly as "2.2e-7" reads,
# as ngspice's INPevaluate does); only MIL is a product.
_SCALES = (("meg", 6), ("mil", None), ("t", 12), ("g", 9), ("x", 6), ("k", 3),
           ("m", -3), ("u", -6), ("n", -9), ("p", -12), ("f", -15), ("a", -18))


def parse_number(s: str) -> float:
    """Value of a SPICE number literal; ValueError if s is not one."""
    m = _NUM.match(s)
    if not m:
        raise ValueError("not a number: %r" % (s,))
    mant, exp, tail = m.group(1), int(m.group(2) or 0), m.group(3).lower()
    for name, scale in _SCALES:
        if tail.startswith(name):
            if scale is None:
                return float("%se%d" % (mant, exp)) * 25.4e-6
            return float("%se%d" % (mant, exp + scale))
    return float("%se%d" % (mant, exp))   # no scale: unit letters only ("v", "s", "ohm", "hz")


def is_number(s: str) -> bool:
    try:
        parse_number(s)
    except ValueError:
        return False
    return True


def fmt(x: float) -> str:
    """The one float spelling every emitter uses (round-trips exactly)."""
    if x != x or x in (float("inf"), float("-inf")):
        raise ValueError("cannot print %r into a netlist" % (x,))
    r = repr(float(x))
    return "0.0" if r == "-0.0" else r
