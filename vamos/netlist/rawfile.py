"""SPICE rawfiles: read VACASK and Xyce output, repair the point count (docs/VAMOS_AMS_DESIGN.md §4.6, §6).

A rawfile is one or more plots, each a text header

    Title: ...            Date: ...            Plotname: Transient Analysis
    Flags: real           (or complex; "padded"/"unpadded"/"double" accepted)
    No. Variables: 5
    No. Points: 60        (Xyce writes 18 blanks and fills them in at the end)
    Variables:
            0       time    notype        (index, name, type; tab separated)
            ...
    Binary:               (little-endian doubles; complex values are re, im pairs)
or  Values:               (ASCII: per point an index, then one value per
                           variable; complex values are "re,im")

API
---
read(path) -> Raw               the first plot
read_all(path) -> List[Raw]     every plot
    The point count is taken from the data, never trusted from the header:
    a blank or wrong "No. Points:" (Xyce leaves it blank when it ends paused,
    e.g. under a co-simulation --stop-time) and a truncated last point are
    tolerated.  Raises RawError for a file that is not a rawfile, or one in
    a layout vamos does not read (LTspice's float32 "forward" data).

Raw: title, date, plotname, flags (lowercased words), variables [(name,
    type)], points (list of tuples, one value per variable; complex numbers
    when the flags say complex), declared_points (the header's count, None
    when blank or unreadable), binary, path, header (other header lines, by
    lowercased key).
    complex                  True for a complex plot
    names()                  the variable names
    index(name)              column number; name matched case-insensitively
                             as written ("V(OUT)"), or as v(n) / V(N) / n for
                             a node voltage and i(x) for a branch current,
                             across both engines' spellings: Xyce "V(X1:N)",
                             "I(V1)", "I(BEB)" for the behavioral element eb
                             (the Xyce emitter's B prefix); VACASK "x1:n",
                             "v1:flow(br)", "r1.i" (a resistor's current);
                             HSPICE's '.' hierarchy matches ':'.  KeyError
                             when nothing, or more than one variable, matches.
    column(name)             that column as a list; also v(a,b), computed as
                             v(a) - v(b) when the file has no such column
    time()                   the time scale (RawError if the plot has none)
    last_time()              the last time point, or None for no points
    at(name, t)              the value at time t, linearly interpolated
    crossings(name, level, direction=0)
                             interpolated times where the column crosses level
                             (+1 rising, -1 falling, 0 both)

fix_points(path) -> bool
    Rewrite every blank or wrong "No. Points:" field from the data length: in
    place when the number fits the field (Xyce reserves 18 characters), else
    by rewriting the file (written beside it, then os.replace).  True if the
    file changed.
"""

from __future__ import annotations

import os
import re
import sys
from array import array
from typing import Dict, List, Optional, Tuple, Union

Value = Union[float, complex]

_LAYOUT_FLAGS = frozenset(("real", "complex", "padded", "unpadded", "double"))
_PLOT_START = re.compile(rb"[\r\n]*(?:Title|Plotname|Date):", re.I)
_HEADER_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z. ]*):(.*)$")
_ACCESS = re.compile(r"^([vi])\((.*)\)$")
_TOKEN = re.compile(rb"\S+")


class RawError(ValueError):
    pass


class Raw:
    """One plot of a rawfile (see the module docstring)."""

    def __init__(self) -> None:
        self.path = ""
        self.title = ""
        self.date = ""
        self.plotname = ""
        self.flags: List[str] = []
        self.variables: List[Tuple[str, str]] = []
        self.points: List[tuple] = []
        self.declared_points: Optional[int] = None
        self.binary = True
        self.header: Dict[str, str] = {}

    def __repr__(self) -> str:
        return "<Raw %r %s: %d variables, %d points>" % (self.path, self.plotname,
                                                        len(self.variables), len(self.points))

    @property
    def complex(self) -> bool:
        return "complex" in self.flags

    def names(self) -> List[str]:
        return [n for n, _ in self.variables]

    def index(self, name: str) -> int:
        low = name.strip().lower()
        exact = [k for k, (n, _) in enumerate(self.variables) if n.lower() == low]
        if len(exact) == 1:
            return exact[0]
        want = _key(low, "")
        hits = [k for k, (n, t) in enumerate(self.variables) if want in _keys(n.lower(), t)]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise KeyError("%s: no variable %r in plot %r (variables: %s)"
                           % (self.path, name, self.plotname, ", ".join(self.names())))
        raise KeyError("%s: %r matches several variables: %s"
                       % (self.path, name, ", ".join(self.variables[k][0] for k in hits)))

    def column(self, name: str) -> List[Value]:
        try:
            k = self.index(name)
        except KeyError:
            m = _ACCESS.match(name.strip().lower())
            parts = m.group(2).split(",") if (m and m.group(1) == "v") else []
            if len(parts) != 2:
                raise
            a, b = (p.strip() for p in parts)
            ca = self.column("v(%s)" % a)
            cb = [0.0] * len(ca) if b == "0" else self.column("v(%s)" % b)
            return [x - y for x, y in zip(ca, cb)]
        return [p[k] for p in self.points]

    def time(self) -> List[float]:
        if not self.variables or not (self.variables[0][0].lower() == "time" or
                                      self.variables[0][1].lower() == "time"):
            raise RawError("%s: plot %r has no time scale" % (self.path, self.plotname))
        return [p[0].real if self.complex else p[0] for p in self.points]

    def last_time(self) -> Optional[float]:
        t = self.time()
        return t[-1] if t else None

    def at(self, name: str, t: float) -> float:
        """The column's value at time t, linearly interpolated (RawError outside the run)."""
        ts, ys = self.time(), self.column(name)
        if not ts or t < ts[0] or t > ts[-1]:
            raise RawError("%s: t=%r is outside the run (%s)" % (
                self.path, t, "no points" if not ts else "%r..%r" % (ts[0], ts[-1])))
        lo, hi = 0, len(ts) - 1
        while hi - lo > 1:                                  # last point with ts[lo] <= t
            mid = (lo + hi) // 2
            if ts[mid] <= t:
                lo = mid
            else:
                hi = mid
        if ts[hi] <= t:
            return ys[hi]
        if ts[hi] == ts[lo]:
            return ys[lo]
        return ys[lo] + (ys[hi] - ys[lo]) * (t - ts[lo]) / (ts[hi] - ts[lo])

    def crossings(self, name: str, level: float, direction: int = 0) -> List[float]:
        """Times at which the column crosses level, interpolated; direction +1 rising only,
        -1 falling only, 0 both.  A point exactly at the level counts once."""
        ts, ys = self.time(), self.column(name)
        out: List[float] = []
        for k in range(1, len(ts)):
            a, b = ys[k - 1] - level, ys[k] - level
            rising, falling = a < 0 <= b, a > 0 >= b
            if (rising and direction >= 0) or (falling and direction <= 0):
                out.append(ts[k - 1] + (ts[k] - ts[k - 1]) * a / (a - b))
        return out


def _key(name: str, typ: str) -> Tuple[str, str]:
    """(kind, node) of a variable or a query: ('v', 'x1:n'), ('i', 'v1'), ('', 'time')."""
    m = _ACCESS.match(name)
    if m:
        kind, inner = m.group(1), m.group(2).replace(" ", "")
    elif name.endswith(":flow(br)"):                         # VACASK branch current
        kind, inner = "i", name[:-len(":flow(br)")]
    elif name in ("time", "frequency") or typ.lower() in ("time", "frequency"):
        return ("", name)
    else:
        kind, inner = "v", name
    return (kind, inner.replace(".", ":"))


def _keys(name: str, typ: str) -> List[Tuple[str, str]]:
    """The keys a variable answers to: its own, plus the element current it may stand for.

    VACASK saves a device output as <instance>.<output> (vamos saves a resistor's current
    as p(r1, i): "r1.i", "x1:rx.i"); it types every variable notype, so "x.i" answers both
    i(x) and a node named x.i.  The Xyce emitter prints a behavioral E/G element (and the
    gated conductance) as B<name> (xyce.printed), so "I(X1:BEB)" also answers i(x1.eb).
    """
    out = [_key(name, typ)]
    if name.endswith(".i") and len(name) > 2:
        out.append(("i", name[:-2].replace(".", ":")))
    kind, inner = out[0]
    if kind == "i":
        head, _, last = inner.rpartition(":")
        if last.startswith("b") and len(last) > 1:
            out.append(("i", (head + ":" if head else "") + last[1:]))
    return out


# -- parsing -------------------------------------------------------------------

class _Plot:
    """A parsed plot and where its parts are in the file (fix_points edits them)."""

    def __init__(self) -> None:
        self.raw = Raw()
        self.points_field: Optional[Tuple[int, int, bool]] = None   # value span, space before it
        self.nvars_line_end = -1
        self.data_start = 0
        self.next_start = -1                                    # the next plot's header, or -1


def _parse(data: bytes, path: str) -> List[_Plot]:
    plots: List[_Plot] = []
    pos = 0
    while True:
        plot = _parse_plot(data, pos, path)
        plots.append(plot)
        if plot.next_start < 0:
            return plots
        pos = plot.next_start


def _parse_plot(data: bytes, pos: int, path: str) -> _Plot:
    plot = _Plot()
    raw = plot.raw
    raw.path = path
    nvars = None
    marker = None
    while pos < len(data):
        eol = data.find(b"\n", pos)
        if eol < 0:
            eol = len(data)
        line = data[pos:eol].decode("latin-1").rstrip("\r")
        start, pos = pos, eol + 1
        if not line.strip():
            continue
        m = _HEADER_LINE.match(line)
        if not m:
            raise RawError("%s: not a rawfile header line: %r" % (path, line[:80]))
        key, value = m.group(1).strip().lower(), m.group(2).strip()
        if key == "title":
            raw.title = value
        elif key == "date":
            raw.date = value
        elif key == "plotname":
            raw.plotname = value
        elif key == "flags":
            raw.flags = value.lower().split()
            odd = [f for f in raw.flags if f not in _LAYOUT_FLAGS]
            if odd:
                raise RawError("%s: unsupported rawfile flags %s" % (path, " ".join(odd)))
        elif key == "no. variables":
            try:
                nvars = int(value)
            except ValueError:
                raise RawError("%s: bad No. Variables: %r" % (path, value))
            plot.nvars_line_end = pos
        elif key == "no. points":
            colon = start + line.index(":")
            a = colon + 1
            spaced = data[a:a + 1] == b" "
            if spaced:
                a += 1
            b = eol - 1 if data[eol - 1:eol] == b"\r" else eol
            plot.points_field = (a, max(a, b), spaced)
            try:
                raw.declared_points = int(value) if value else None
            except ValueError:
                raw.declared_points = None
        elif key == "variables":
            if nvars is None:
                raise RawError("%s: Variables: before No. Variables:" % path)
            pos = _variables(data, pos, nvars, value, raw, path)
        elif key in ("binary", "values"):
            marker = key
            break
        else:
            raw.header[key] = value
    if marker is None:
        raise RawError("%s: no Binary: or Values: section" % path)
    if nvars is None or nvars < 1 or len(raw.variables) != nvars:
        raise RawError("%s: the variable list does not match No. Variables:" % path)
    raw.binary = marker == "binary"
    plot.data_start = pos
    if raw.binary:
        _binary(data, plot, nvars)
    else:
        _ascii(data, plot, nvars)
    return plot


def _variables(data: bytes, pos: int, nvars: int, first: str, raw: Raw, path: str) -> int:
    lines = [first] if first else []
    while len(lines) < nvars:
        eol = data.find(b"\n", pos)
        if eol < 0:
            raise RawError("%s: the variable list ends early" % path)
        line = data[pos:eol].decode("latin-1").rstrip("\r")
        pos = eol + 1
        if line.strip():
            lines.append(line)
    for k, line in enumerate(lines):
        if "\t" in line.strip():
            fields = [f.strip() for f in line.split("\t") if f.strip()]
        else:
            fields = line.split()
        if len(fields) < 2 or not fields[0].isdigit() or int(fields[0]) != k:
            raise RawError("%s: bad variable line %r" % (path, line))
        raw.variables.append((fields[1], fields[2] if len(fields) > 2 else ""))
    return pos


def _binary(data: bytes, plot: _Plot, nvars: int) -> None:
    raw = plot.raw
    width = 2 if raw.complex else 1
    row = nvars * width * 8
    avail = len(data) - plot.data_start
    n = raw.declared_points
    if n is not None and 0 <= n and n * row <= avail:
        end = plot.data_start + n * row
        if end < len(data) and data[end:].strip():
            if _PLOT_START.match(data, end):
                plot.next_start = end                       # another plot follows
            else:
                n = None                                    # the count is wrong
    else:
        n = None
    if n is None:
        n = avail // row
    vals = array("d")
    vals.frombytes(data[plot.data_start:plot.data_start + n * row])
    if sys.byteorder != "little":
        vals.byteswap()
    if width == 2:
        it = iter(vals)
        flat: list = [complex(re_, im) for re_, im in zip(it, it)]
    else:
        flat = vals.tolist()
    raw.points = [tuple(flat[i:i + nvars]) for i in range(0, len(flat), nvars)]


def _ascii(data: bytes, plot: _Plot, nvars: int) -> None:
    raw = plot.raw
    points = []
    toks = _TOKEN.finditer(data, plot.data_start)
    for tok in toks:
        if not tok.group(0).isdigit():
            if _PLOT_START.match(data, tok.start()):
                plot.next_start = tok.start()
                break
            raise RawError("%s: expected a point index at byte %d, got %r"
                           % (raw.path, tok.start(), tok.group(0)[:40]))
        vals = []
        for t in toks:
            s = t.group(0).decode("latin-1")
            while s.endswith(","):                          # "re, im"
                t = next(toks, None)
                if t is None:
                    break
                s += t.group(0).decode("latin-1")
            try:
                vals.append(_value(s, raw.complex))
            except ValueError:
                raise RawError("%s: bad value %r in point %d" % (raw.path, s, len(points)))
            if len(vals) == nvars:
                break
        if len(vals) < nvars:
            break                                           # a truncated last point
        points.append(tuple(vals))
    raw.points = points


def _value(s: str, cplx: bool) -> Value:
    if cplx:
        re_, _, im = s.partition(",")
        return complex(float(re_), float(im) if im else 0.0)
    return float(s)


def read_all(path: str) -> List[Raw]:
    """Every plot of a rawfile."""
    with open(path, "rb") as fh:
        data = fh.read()
    if not data.strip():
        raise RawError("%s: empty rawfile" % path)
    return [p.raw for p in _parse(data, path)]


def read(path: str) -> Raw:
    """The first plot of a rawfile (see the module docstring)."""
    return read_all(path)[0]


# -- repair ---------------------------------------------------------------------

def fix_points(path: str) -> bool:
    """Make every plot's "No. Points:" agree with its data; True if the file changed."""
    with open(path, "rb") as fh:
        data = fh.read()
    if not data.strip():
        raise RawError("%s: empty rawfile" % path)
    edits: List[Tuple[int, int, bytes]] = []                # (start, end, replacement)
    for plot in _parse(data, path):
        n = len(plot.raw.points)
        if plot.points_field is None:                       # no such line: add one
            edits.append((plot.nvars_line_end, plot.nvars_line_end, b"No. Points: %d\n" % n))
            continue
        if plot.raw.declared_points == n:
            continue
        a, b, spaced = plot.points_field
        digits = b"%d" % n
        if len(digits) <= b - a:
            edits.append((a, b, digits.ljust(b - a)))
        else:
            edits.append((a, b, digits if spaced else b" " + digits))
    if not edits:
        return False
    if all(len(rep) == e - s for s, e, rep in edits):
        with open(path, "r+b") as fh:
            for s, _, rep in edits:
                fh.seek(s)
                fh.write(rep)
        return True
    out = bytearray()
    prev = 0
    for s, e, rep in sorted(edits):
        out += data[prev:s]
        out += rep
        prev = e
    out += data[prev:]
    tmp = path + ".vamos-fix"
    with open(tmp, "wb") as fh:
        fh.write(bytes(out))
    os.replace(tmp, path)
    return True
