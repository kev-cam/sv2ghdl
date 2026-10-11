"""psfcheck: a strict reader and checker for Spectre PSF ASCII files (stdlib only, Python 3.9).

Written from docs/VAMOS_SPECTRE_DESIGN.md §8 and the real Spectre samples under
tests/vamos/fixtures/spectre/psf/real (§11 T0 "psfcheck").  It never imports vamos/output/psf.py,
so it stays an independent oracle for the writer that module becomes: every PSF file vamos writes
is checked here on both legs, and the end-to-end tests read their values through this reader.

    psf = check("results/mytran.tran.tran")        # raises PsfError("<file>:<line>: <what>")
    psf.names()                                    # trace names in TRACE order
    psf.column("out")                              # one value per sweep point
    psf.at("out", 5e-3)                            # linearly interpolated along the sweep
    op = check("results/myop.dc"); op.value("in")  # an operating-point value
    log = check_logfile("results/logFile")         # the analysis tree, checked against the data files

Rules (§8.2, §8.3; [S] = the real samples):
  * sections HEADER [TYPE] [SWEEP] [TRACE] [VALUE] END in this order (a section keyword that comes
    too late is "section X after Y") and nothing after END; VALUE may be left out only when there is
    neither SWEEP nor TRACE (an `info` file with nothing to say,
    `myinfo_Models.info` [S]);
  * strings stay on one line and carry only §8.3's escapes: \\" and \\\\, plus, under +escchars,
    a backslash before any character outside [A-Za-z0-9_.:!]; they are decoded;
  * a FLOAT value carries a `.` or an exponent (or is nan, inf, -inf), COMPLEX is `(re im)`, STRUCT
    and ARRAY values have the declared arity; INT values are integers;
  * every type a SWEEP, TRACE or VALUE entry names is declared in TYPE; names are unique per section;
  * in a swept file every point is the sweep value followed by each trace exactly once, in TRACE
    order, and the last point is complete; `xVecSorted` `ascending` means a non-decreasing sweep;
  * the header carries §8.3's keys for its analysis kind (dc, ac, xf, noise, tran, sweep and Monte
    Carlo parents), the right `xVecSorted` for that kind, and a `date` in §8.2's layout;
  * SWEEP PROPs `sweep_direction`, `plot` and `grid` are integers, `grid` 1 or 3 [S];
  * `logFile`: §8.2's header and the `analysisInst` STRUCT; unique keys of the form `<name>-<type>`;
    every `dataFile` exists (next to the logFile, or in `datadir`) and describes itself the same way
    (analysis type, description, sweep variable); every `parent` is an entry of type sweep or
    montecarlo; a parent's PROP is `sweep_tree_type sweepNode` (Monte Carlo parents carry none); a
    leaf's PROP is `data_type`, `sweep_tree_type leafNode`, then the parent's sweep variable with
    the parent's value at the leaf's index (`-NNN`, 0-based for sweeps, 1-based for Monte Carlo
    [S]); the number of children of a parent equals its number of points.

Run as a script, `python3 psfcheck.py [--escchars] [--datadir DIR] FILE...` prints PASS/FAIL per
file and exits 1 if any file failed.
"""
from __future__ import annotations

import math
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["PsfError", "PsfFile", "LogFile", "LogEntry", "TypeDesc", "Trace", "read", "check",
           "check_logfile", "main"]

_TOK = re.compile(r'''
    (?P<str>"(?:\\.|[^"\\\n])*")
  | (?P<flt>[+-]?(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?|[+-]?\d+[eE][+-]?\d+|[+-]?(?:nan|inf)\b)
  | (?P<int>[+-]?\d+)
  | (?P<kw>[A-Z][A-Z0-9]*)
  | (?P<lp>\() | (?P<rp>\)) | (?P<star>\*)
  | (?P<nl>\n) | (?P<ws>[ \t\r]+)
  | (?P<bad>.)''', re.X)
_SAFE = re.compile(r"[A-Za-z0-9_.:!]")
SECTIONS = ("HEADER", "TYPE", "SWEEP", "TRACE", "VALUE", "END")      # §8.3: the order, TYPE..TRACE optional
_DATE = re.compile(r"^(1[0-2]|[1-9]):[0-5]\d:[0-5]\d [AP]M, (Sun|Mon|Tue|Wed|Thu|Fri|Sat) "
                   r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) ([12]\d|3[01]|[1-9]), \d{4}$")
_ASCTIME = re.compile(r"^(Sun|Mon|Tue|Wed|Thu|Fri|Sat) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
                      r"[ 0-3]\d [0-2]\d:[0-5]\d:[0-5]\d \d{4}$")

# -- §8.3's header table -------------------------------------------------------------------------

COMMON_KEYS = ("PSFversion", "simulator", "version", "date", "design", "analysis type",
               "analysis name", "analysis description", "xVecSorted", "tolerance.relative")
KIND_KEYS: Dict[str, Tuple[str, ...]] = {
    "dc": ("reltol", "abstol(V)", "abstol(I)", "temp", "tnom", "tempeffects", "gmindc"),
    "ac": ("start", "stop", "operating point producer"),
    "xf": ("start", "stop", "operating point producer"),
    "noise": ("operating point producer", "output", "ground", "positive output signal",
              "negative output signal"),
    "tran": ("start", "outputstart", "stop", "step", "istep", "maxstep", "ic", "useprevic", "skipdc",
             "reltol", "abstol(V)", "abstol(I)", "temp", "tnom", "tempeffects", "errpreset", "method",
             "lteratio", "relref", "cmin", "gmin"),
    "sweep": (),
    "montecarlo": (),
}
# keys a real file may add to the table: `rabsshort` when set (19.1 writes it in dc and tran files [S])
OPTIONAL_KEYS: Dict[str, Tuple[str, ...]] = {"dc": ("rabsshort",), "tran": ("rabsshort",)}
XVEC_SORTED: Dict[str, str] = {"ac": "ascending", "noise": "ascending", "xf": "ascending",
                               "tran": "ascending", "montecarlo": "ascending", "dc": "unsorted",
                               "sweep": "unknown", "info": "unknown"}
STRING_KEYS = frozenset(["PSFversion", "simulator", "version", "date", "design", "analysis type",
                         "analysis name", "analysis description", "xVecSorted", "tempeffects", "ic",
                         "useprevic", "skipdc", "errpreset", "method", "relref",
                         "operating point producer", "output", "ground", "positive output signal",
                         "negative output signal"])
FLOAT_KEYS = frozenset(["tolerance.relative", "reltol", "abstol(V)", "abstol(I)", "temp", "tnom",
                        "gmindc", "start", "outputstart", "stop", "step", "istep", "maxstep", "lteratio",
                        "cmin", "gmin", "rabsshort"])
LOG_KEYS = ("PSFversion", "Log Generator", "Log Time Stamp", "simulator", "version", "date", "design",
            "signalNameType", "simMode", "measdgt", "ingold", "sst2usecolon")
LOG_OPTIONAL_KEYS = ("psfversion",)            # 23.1 writes it, 15.1 does not [S]
LOG_INT_KEYS = frozenset(["measdgt", "ingold", "sst2usecolon"])
DATA_TYPE: Dict[str, str] = {"dc": "scalar", "ac": "swept_scalar", "tran": "swept_scalar",
                             "xf": "swept_scalar", "noise": "swept_struct", "info": "struct"}
LOG_STRUCT = (("analysisType", "STRING"), ("dataFile", "STRING"), ("format", "STRING"),
              ("parent", "STRING"), ("sweepVariable", ("ARRAY", "STRING")), ("description", "STRING"))
PARENT_KINDS = ("sweep", "montecarlo")
PROP_REL_TOL = 1e-5                            # logFile PROP reals are %#g, 6 significant digits


class PsfError(ValueError):
    """A malformed or rule-breaking file: the message is `<file>:<line>: <what>`."""


class TypeDesc:
    """A TYPE entry: kind FLOAT | COMPLEX | INT | STRING | ARRAY | STRUCT, `elem` for ARRAY, `members`
    [(name, TypeDesc, props)] for STRUCT, and the entry's own PROPs."""

    def __init__(self, kind: str, elem: Optional["TypeDesc"] = None,
                 members: Optional[List[Tuple[str, "TypeDesc", Dict[str, Any]]]] = None) -> None:
        self.kind = kind
        self.elem = elem
        self.members = members or []
        self.props: Dict[str, Any] = {}

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.kind == other and self.kind not in ("ARRAY", "STRUCT")
        if isinstance(other, tuple) and len(other) == 2 and other[0] == "ARRAY":
            return self.kind == "ARRAY" and self.elem == other[1]
        return (isinstance(other, TypeDesc) and self.kind == other.kind and self.elem == other.elem
                and [(n, t) for n, t, _ in self.members] == [(n, t) for n, t, _ in other.members])

    def __repr__(self) -> str:
        if self.kind == "ARRAY":
            return "ARRAY(%r)" % (self.elem,)
        if self.kind == "STRUCT":
            return "STRUCT(%s)" % ", ".join("%s %r" % (n, t) for n, t, _ in self.members)
        return self.kind


class Trace:
    """A SWEEP or TRACE entry: name, the type name it refers to, its PROPs."""

    def __init__(self, name: str, type_name: str, props: Dict[str, Any]) -> None:
        self.name, self.type, self.props = name, type_name, props

    def __repr__(self) -> str:
        return "Trace(%r, %r)" % (self.name, self.type)


class PsfFile:
    """One parsed PSF ASCII file.

    header: the HEADER pairs in order; types: {name: TypeDesc}; sweep: the SWEEP entry or None;
    traces: the TRACE entries in order; points: the number of sweep points.  Swept values are in
    `columns` ({name: [value per point]}, the sweep variable included); operating-point values in
    `values` ({name: value}) with their types and PROPs in `value_types` and `value_props`.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self.header: Dict[str, Any] = {}
        self.types: Dict[str, TypeDesc] = {}
        self.sweep: Optional[Trace] = None
        self.traces: List[Trace] = []
        self.points = 0
        self.columns: Dict[str, List[Any]] = {}
        self.values: Dict[str, Any] = {}
        self.value_types: Dict[str, str] = {}
        self.value_props: Dict[str, Dict[str, Any]] = {}
        self.has_value_section = False

    @property
    def kind(self) -> str:
        """The `analysis type` header (`logFile` for a log file)."""
        if "analysis type" not in self.header and "Log Generator" in self.header:
            return "logFile"
        return str(self.header.get("analysis type", ""))

    @property
    def swept(self) -> bool:
        return self.sweep is not None

    def names(self) -> List[str]:
        """The trace names in TRACE order (a swept file) or the value names (an operating point)."""
        if self.swept:
            return [t.name for t in self.traces]
        return list(self.values)

    def sweep_values(self) -> List[Any]:
        if not self.swept:
            raise PsfError("%s: not a swept file" % self.path)
        return self.columns[self.sweep.name]

    def column(self, name: str) -> List[Any]:
        """The values of a trace (or of the sweep variable), one per point."""
        if not self.swept:
            raise PsfError("%s: not a swept file" % self.path)
        if name not in self.columns:
            raise PsfError("%s: no trace %r (traces: %s)" % (self.path, name, ", ".join(self.names())))
        return self.columns[name]

    def value(self, name: str) -> Any:
        """An operating-point value."""
        if self.swept:
            raise PsfError("%s: a swept file has no scalar values" % self.path)
        if name not in self.values:
            raise PsfError("%s: no value %r (values: %s)" % (self.path, name, ", ".join(self.values)))
        return self.values[name]

    def at(self, name: str, x: float) -> Any:
        """The trace's value at sweep value x, linearly interpolated (PsfError outside the sweep)."""
        xs, ys = self.sweep_values(), self.column(name)
        if not xs or x < xs[0] or x > xs[-1]:
            raise PsfError("%s: %r is outside the sweep (%s)" % (
                self.path, x, "no points" if not xs else "%r..%r" % (xs[0], xs[-1])))
        lo, hi = 0, len(xs) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if xs[mid] <= x:
                lo = mid
            else:
                hi = mid
        if xs[hi] <= x:
            return ys[hi]
        if xs[hi] == xs[lo]:
            return ys[lo]
        return ys[lo] + (ys[hi] - ys[lo]) * (x - xs[lo]) / (xs[hi] - xs[lo])


class LogEntry:
    """One `analysisInst` of a logFile."""

    def __init__(self, key: str, fields: Dict[str, Any], props: Dict[str, Any]) -> None:
        self.key = key
        self.analysis_type: str = fields["analysisType"]
        self.data_file: str = fields["dataFile"]
        self.format: str = fields["format"]
        self.parent: str = fields["parent"]
        self.sweep_variable: List[str] = list(fields["sweepVariable"])
        self.description: str = fields["description"]
        self.props = props

    @property
    def is_parent(self) -> bool:
        return self.analysis_type in PARENT_KINDS

    def __repr__(self) -> str:
        return "LogEntry(%r, %r, %r)" % (self.key, self.analysis_type, self.data_file)


class LogFile:
    """A checked logFile: `entries` in file order, `by_key`, and `children` {parent key: [entries]}."""

    def __init__(self, path: str, psf: PsfFile) -> None:
        self.path = path
        self.psf = psf
        self.header = psf.header
        self.entries: List[LogEntry] = []
        self.by_key: Dict[str, LogEntry] = {}
        self.children: Dict[str, List[LogEntry]] = {}

    def keys(self) -> List[str]:
        return [e.key for e in self.entries]


# -- the parser ------------------------------------------------------------------------------------

class _Parser:
    def __init__(self, text: str, path: str, escchars: bool) -> None:
        self.toks: List[Tuple[str, str, int]] = []
        line = 1
        for m in _TOK.finditer(text):
            k = m.lastgroup
            if k == "nl":
                line += 1
            elif k == "ws":
                continue
            elif k == "bad":
                raise PsfError("%s:%d: unexpected character %r" % (path, line, m.group()))
            else:
                self.toks.append((k, m.group(), line))
        self.i, self.path, self.esc = 0, path, escchars

    # -- tokens
    def err(self, what: str) -> PsfError:
        if self.toks:
            line = self.toks[min(self.i, len(self.toks) - 1)][2]
        else:
            line = 0
        return PsfError("%s:%d: %s" % (self.path, line, what))

    def peek(self, kind: Optional[str] = None, value: Optional[str] = None) -> bool:
        if self.i >= len(self.toks):
            return False
        tk, tv, _ = self.toks[self.i]
        return (kind is None or tk == kind) and (value is None or tv == value)

    def take(self, kind: str, value: Optional[str] = None) -> str:
        if not self.peek(kind, value):
            got = self.toks[self.i][1] if self.i < len(self.toks) else "end of file"
            raise self.err("expected %s, got %r" % (value or kind, got))
        self.i += 1
        return self.toks[self.i - 1][1]

    def at_end(self) -> bool:
        return self.i >= len(self.toks)

    def section_order(self, last: str) -> None:
        """After section `last`, a section keyword at or before it in SECTIONS is out of order."""
        if self.peek("kw"):
            kw = self.toks[self.i][1]
            if kw in SECTIONS and SECTIONS.index(kw) <= SECTIONS.index(last):
                raise self.err("section %s after %s: the order is HEADER [TYPE] [SWEEP] [TRACE] VALUE END"
                               % (kw, last))

    def string(self) -> str:
        raw = self.take("str")[1:-1]
        out: List[str] = []
        j = 0
        while j < len(raw):
            c = raw[j]
            if c == "\\":
                n = raw[j + 1]                  # the regex guarantees a character follows
                if n in '"\\' or (self.esc and not _SAFE.match(n)):
                    out.append(n)
                    j += 2
                    continue
                raise self.err("escape \\%s is outside the rules (\\\" and \\\\%s)"
                               % (n, "; +escchars: any character outside [A-Za-z0-9_.:!]"
                                  if self.esc else ""))
            out.append(c)
            j += 1
        return "".join(out)

    def scalar(self) -> Any:
        """A header or PROP value: string, FLOAT or INT by its own spelling."""
        if self.peek("str"):
            return self.string()
        if self.peek("flt"):
            return float(self.take("flt"))
        return int(self.take("int"))

    def props(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if not self.peek("kw", "PROP"):
            return out
        self.take("kw", "PROP")
        self.take("lp")
        while not self.peek("rp"):
            k = self.string()
            if k in out:
                raise self.err("duplicate PROP %r" % k)
            out[k] = self.scalar()
        self.take("rp")
        return out

    # -- TYPE
    def typedesc(self) -> TypeDesc:
        kw = self.take("kw")
        if kw in ("FLOAT", "COMPLEX"):
            if self.peek("kw", "SINGLE"):
                raise self.err("%s SINGLE is not a vamos layout (DOUBLE)" % kw)
            self.take("kw", "DOUBLE")
            return TypeDesc(kw)
        if kw == "INT":
            if not (self.peek("kw", "BYTE") or self.peek("kw", "LONG")):
                raise self.err("INT needs BYTE or LONG")
            self.take("kw")
            return TypeDesc("INT")
        if kw == "STRING":
            self.take("star")
            return TypeDesc("STRING")
        if kw == "ARRAY":
            self.take("lp")
            self.take("star")
            self.take("rp")
            return TypeDesc("ARRAY", elem=self.typedesc())
        if kw == "STRUCT":
            self.take("lp")
            members: List[Tuple[str, TypeDesc, Dict[str, Any]]] = []
            seen = set()
            while not self.peek("rp"):
                name = self.string()
                if name in seen:
                    raise self.err("STRUCT member %r listed twice" % name)
                seen.add(name)
                desc = self.typedesc()
                members.append((name, desc, self.props()))
            self.take("rp")
            return TypeDesc("STRUCT", members=members)
        raise self.err("unknown type keyword %s" % kw)

    # -- VALUE
    def value(self, desc: TypeDesc) -> Any:
        if desc.kind == "FLOAT":
            if self.peek("int"):
                raise self.err("integer %r where FLOAT is declared (a FLOAT carries `.` or an exponent)"
                               % self.toks[self.i][1])
            return float(self.take("flt"))
        if desc.kind == "INT":
            if self.peek("flt"):
                raise self.err("real %r where INT is declared" % self.toks[self.i][1])
            return int(self.take("int"))
        if desc.kind == "STRING":
            return self.string()
        if desc.kind == "COMPLEX":
            self.take("lp")
            re_ = self.value(TypeDesc("FLOAT"))
            im = self.value(TypeDesc("FLOAT"))
            self.take("rp")
            return complex(re_, im)
        self.take("lp")
        if desc.kind == "ARRAY":
            items: List[Any] = []
            while not self.peek("rp"):
                items.append(self.value(desc.elem))      # type: ignore[arg-type]
            self.take("rp")
            return items
        vals: Dict[str, Any] = {}
        for name, sub, _ in desc.members:
            if self.peek("rp"):
                raise self.err("STRUCT value ends before member %r" % name)
            vals[name] = self.value(sub)
        if not self.peek("rp"):
            raise self.err("STRUCT value has more than %d members" % len(desc.members))
        self.take("rp")
        return vals


def read(path: str, escchars: bool = False) -> PsfFile:
    """Parse a PSF ASCII file strictly (the grammar and arity rules); content rules are `check`'s."""
    with open(path, encoding="latin-1") as fh:
        text = fh.read()
    p = _Parser(text, path, escchars)
    psf = PsfFile(path)
    p.take("kw", "HEADER")
    while p.peek("str"):
        k = p.string()
        if k in psf.header:
            raise p.err("duplicate header key %r" % k)
        psf.header[k] = p.scalar()
    if not psf.header:
        raise p.err("empty HEADER")
    p.section_order("HEADER")
    if p.peek("kw", "TYPE"):
        p.take("kw", "TYPE")
        while p.peek("str"):
            name = p.string()
            if name in psf.types:
                raise p.err("type %r declared twice" % name)
            desc = p.typedesc()
            desc.props = p.props()
            psf.types[name] = desc
        p.section_order("TYPE")

    def typeref() -> str:
        t = p.string()
        if t not in psf.types:
            raise p.err("type %r is not declared in TYPE" % t)
        return t

    if p.peek("kw", "SWEEP"):
        p.take("kw", "SWEEP")
        name = p.string()
        psf.sweep = Trace(name, typeref(), p.props())
        if p.peek("str"):
            raise p.err("more than one SWEEP variable")
        p.section_order("SWEEP")
    if p.peek("kw", "TRACE"):
        p.take("kw", "TRACE")
        seen = set()
        while p.peek("str"):
            name = p.string()
            if p.peek("kw", "GROUP"):
                raise p.err("GROUP traces are not a vamos layout")
            if name in seen or (psf.sweep is not None and name == psf.sweep.name):
                raise p.err("trace %r listed twice" % name)
            seen.add(name)
            psf.traces.append(Trace(name, typeref(), p.props()))
        p.section_order("TRACE")
    if p.peek("kw", "VALUE"):
        psf.has_value_section = True
        p.take("kw", "VALUE")
        if psf.sweep is not None:
            sname, stype = psf.sweep.name, psf.types[psf.sweep.type]
            cols: Dict[str, List[Any]] = {sname: []}
            for t in psf.traces:
                cols[t.name] = []
            while p.peek("str"):
                got = p.string()
                if got != sname:
                    raise p.err("point %d starts with %r, expected the sweep %r"
                                % (psf.points + 1, got, sname))
                cols[sname].append(p.value(stype))
                for t in psf.traces:
                    got2 = p.string() if p.peek("str") else None
                    if got2 != t.name:
                        raise p.err("point %d: expected trace %r, got %r" % (psf.points + 1, t.name, got2))
                    cols[t.name].append(p.value(psf.types[t.type]))
                psf.points += 1
            psf.columns = cols
        else:
            if psf.traces:
                raise p.err("TRACE without SWEEP")
            while p.peek("str"):
                name = p.string()
                if name in psf.values:
                    raise p.err("value %r listed twice" % name)
                tname = typeref()
                psf.values[name] = p.value(psf.types[tname])
                psf.value_types[name] = tname
                psf.value_props[name] = p.props()
        p.section_order("VALUE")
    elif psf.sweep is not None or psf.traces:
        raise p.err("expected VALUE after SWEEP/TRACE")
    p.take("kw", "END")
    if not p.at_end():
        raise p.err("text after END")
    return psf


# -- the content rules -----------------------------------------------------------------------------

def _rule(path: str, what: str) -> PsfError:
    return PsfError("%s: %s" % (path, what))


def _check_header_types(psf: PsfFile) -> None:
    for k, v in psf.header.items():
        if k in STRING_KEYS and not isinstance(v, str):
            raise _rule(psf.path, "header %r must be a string, got %r" % (k, v))
        if k in FLOAT_KEYS and not isinstance(v, float):
            raise _rule(psf.path, "header %r must be a FLOAT (`.` or an exponent), got %r" % (k, v))


def _check_date(path: str, key: str, value: Any) -> None:
    if not isinstance(value, str) or not _DATE.match(value):
        raise _rule(path, "header %r %r is not `h:mm:ss AM, Day Mon d, yyyy` (unpadded hour and day, "
                          "English names, §8.2)" % (key, value))


def _check_data_file(psf: PsfFile) -> None:
    hdr = psf.header
    missing = [k for k in COMMON_KEYS if k not in hdr]
    if missing:
        raise _rule(psf.path, "header lacks %s" % ", ".join(repr(k) for k in missing))
    _check_header_types(psf)
    _check_date(psf.path, "date", hdr["date"])
    kind = psf.kind
    if kind in KIND_KEYS:
        want = tuple(COMMON_KEYS) + KIND_KEYS[kind]
        allowed = set(want) | set(OPTIONAL_KEYS.get(kind, ()))
        missing = [k for k in want if k not in hdr]
        if missing:
            raise _rule(psf.path, "a %s header lacks %s" % (kind, ", ".join(repr(k) for k in missing)))
        extra = [k for k in hdr if k not in allowed]
        if extra and not (kind == "noise" and hdr.get("output") != "pair of nodes"):
            raise _rule(psf.path, "a %s header has keys outside §8.3's table: %s"
                        % (kind, ", ".join(repr(k) for k in extra)))
    xv = hdr["xVecSorted"]
    if xv not in ("ascending", "unsorted", "unknown"):
        raise _rule(psf.path, "xVecSorted %r is not ascending, unsorted or unknown" % (xv,))
    if kind in XVEC_SORTED and xv != XVEC_SORTED[kind]:
        raise _rule(psf.path, "a %s file has xVecSorted %r, expected %r" % (kind, xv, XVEC_SORTED[kind]))
    if kind in PARENT_KINDS and psf.traces:
        raise _rule(psf.path, "a %s parent carries traces" % kind)
    if psf.sweep is not None:
        sp = psf.sweep.props
        for k in ("sweep_direction", "plot", "grid"):
            if k in sp and not isinstance(sp[k], int):
                raise _rule(psf.path, "SWEEP PROP %r must be an integer, got %r" % (k, sp[k]))
        if "grid" in sp and sp["grid"] not in (1, 3):
            raise _rule(psf.path, "SWEEP PROP grid %r is neither 1 (linear) nor 3 (log)" % (sp["grid"],))
        if xv == "ascending":
            xs = psf.columns[psf.sweep.name]
            for a, b in zip(xs, xs[1:]):
                if b < a:
                    raise _rule(psf.path, "xVecSorted is ascending but the sweep decreases (%r after %r)"
                                % (b, a))


def check(path: str, escchars: bool = False, datadir: Optional[str] = None) -> Any:
    """Read the file and apply every rule; a `logFile` is checked as `check_logfile` does."""
    psf = read(path, escchars)
    if psf.kind == "logFile":
        return _check_log(psf, datadir, escchars)
    _check_data_file(psf)
    return psf


def check_logfile(path: str, datadir: Optional[str] = None, escchars: bool = False) -> LogFile:
    """Check a logFile and the results directory it describes (`datadir`, default: its own)."""
    psf = read(path, escchars)
    if psf.kind != "logFile":
        raise _rule(path, "not a logFile (no `Log Generator` header)")
    return _check_log(psf, datadir, escchars)


_LEAF_INDEX = re.compile(r"^(\d+)(?:_|$)")


def _leaf_index(parent: LogEntry, child_key: str, path: str) -> int:
    """The `-NNN` index of a child of `parent` from its key: `<sweep>-NNN_<rest>-<type>`."""
    stem = parent.key[:-(len(parent.analysis_type) + 1)]        # "<sweep>_<child>"
    sweep_name = stem.rsplit("_", 1)[0]
    cstem = child_key[:-(len(child_key.rsplit("-", 1)[1]) + 1)]
    prefix = sweep_name + "-"
    if not cstem.startswith(prefix):
        raise _rule(path, "logFile entry %r does not continue its parent's name %r" % (child_key, sweep_name))
    m = _LEAF_INDEX.match(cstem[len(prefix):])
    if not m:
        raise _rule(path, "logFile entry %r carries no point index after %r" % (child_key, prefix))
    idx = int(m.group(1))
    return idx - 1 if parent.analysis_type == "montecarlo" else idx     # Monte Carlo leaves are 1-based [S]


def _check_log(psf: PsfFile, datadir: Optional[str], escchars: bool) -> LogFile:
    path = psf.path
    hdr = psf.header
    missing = [k for k in LOG_KEYS if k not in hdr]
    if missing:
        raise _rule(path, "logFile header lacks %s" % ", ".join(repr(k) for k in missing))
    extra = [k for k in hdr if k not in LOG_KEYS and k not in LOG_OPTIONAL_KEYS]
    if extra:
        raise _rule(path, "logFile header has keys outside §8.2's layout: %s" % ", ".join(repr(k) for k in extra))
    for k, v in hdr.items():
        if k in LOG_INT_KEYS:
            if not isinstance(v, int):
                raise _rule(path, "logFile header %r must be an integer, got %r" % (k, v))
        elif not isinstance(v, str):
            raise _rule(path, "logFile header %r must be a string, got %r" % (k, v))
    _check_date(path, "date", hdr["date"])
    if not _ASCTIME.match(hdr["Log Time Stamp"]):
        raise _rule(path, "logFile `Log Time Stamp` %r is not asctime() layout" % (hdr["Log Time Stamp"],))
    if list(psf.types) != ["analysisInst"]:
        raise _rule(path, "logFile TYPE must declare exactly `analysisInst`, got %s" % list(psf.types))
    desc = psf.types["analysisInst"]
    if desc.kind != "STRUCT" or [(n, t) for n, t, _ in desc.members] != list(LOG_STRUCT):
        raise _rule(path, "logFile `analysisInst` is not §8.2's STRUCT: %r" % (desc,))
    if psf.sweep is not None or psf.traces:
        raise _rule(path, "logFile carries SWEEP or TRACE")
    log = LogFile(path, psf)
    for key, fields in psf.values.items():
        if psf.value_types[key] != "analysisInst":
            raise _rule(path, "logFile entry %r is not an analysisInst" % key)
        e = LogEntry(key, fields, psf.value_props[key])
        if not key.endswith("-" + e.analysis_type) or len(key) <= len(e.analysis_type) + 1:
            raise _rule(path, "logFile key %r is not `<name>-%s`" % (key, e.analysis_type))
        if not e.data_file:
            raise _rule(path, "logFile entry %r names no dataFile" % key)
        if e.format != "PSF":
            raise _rule(path, "logFile entry %r has format %r, expected PSF" % (key, e.format))
        log.entries.append(e)
        log.by_key[key] = e
    seen_files: Dict[str, str] = {}
    for e in log.entries:
        if e.data_file in seen_files:
            raise _rule(path, "logFile entries %r and %r name the same dataFile %r"
                        % (seen_files[e.data_file], e.key, e.data_file))
        seen_files[e.data_file] = e.key
    ddir = datadir if datadir is not None else os.path.dirname(os.path.abspath(path))
    files: Dict[str, PsfFile] = {}
    for e in log.entries:
        fpath = os.path.join(ddir, e.data_file)
        if not os.path.isfile(fpath):
            raise _rule(path, "logFile entry %r names a dataFile that does not exist: %s" % (e.key, fpath))
        f = check(fpath, escchars)
        if not isinstance(f, PsfFile):
            raise _rule(path, "logFile entry %r names a logFile as its dataFile" % e.key)
        files[e.key] = f
        if f.header.get("analysis type") != e.analysis_type:
            raise _rule(path, "logFile entry %r says analysisType %r but %s says %r"
                        % (e.key, e.analysis_type, e.data_file, f.header.get("analysis type")))
        if f.header.get("analysis description") != e.description:
            raise _rule(path, "logFile entry %r describes itself as %r but %s says %r"
                        % (e.key, e.description, e.data_file, f.header.get("analysis description")))
        want_sv = [f.sweep.name] if f.sweep is not None else []
        if e.sweep_variable != want_sv:
            raise _rule(path, "logFile entry %r has sweepVariable %r but %s sweeps %r"
                        % (e.key, e.sweep_variable, e.data_file, want_sv))
    for e in log.entries:
        if e.parent:
            if e.parent not in log.by_key:
                raise _rule(path, "logFile entry %r names a parent that does not exist: %r" % (e.key, e.parent))
            if not log.by_key[e.parent].is_parent:
                raise _rule(path, "logFile entry %r has parent %r, which is a %r, not a sweep or Monte Carlo parent"
                            % (e.key, e.parent, log.by_key[e.parent].analysis_type))
            log.children.setdefault(e.parent, []).append(e)
    for e in log.entries:
        _check_entry_props(path, log, e, files)
    for e in log.entries:
        if e.is_parent:
            n = files[e.key].points
            kids = log.children.get(e.key, [])
            if len(kids) != n:
                raise _rule(path, "parent %r has %d points but %d children" % (e.key, n, len(kids)))
    return log


def _check_entry_props(path: str, log: LogFile, e: LogEntry, files: Dict[str, PsfFile]) -> None:
    props = list(e.props.items())
    if e.analysis_type == "montecarlo":
        if props:
            raise _rule(path, "Monte Carlo parent %r carries a PROP" % e.key)
        return
    if e.analysis_type in DATA_TYPE:
        if e.props.get("data_type") != DATA_TYPE[e.analysis_type]:
            raise _rule(path, "logFile entry %r (%s) has data_type %r, expected %r"
                        % (e.key, e.analysis_type, e.props.get("data_type"), DATA_TYPE[e.analysis_type]))
    if e.is_parent:
        if "data_type" in e.props:
            raise _rule(path, "sweep parent %r carries a data_type" % e.key)
        if not props or props[0] != ("sweep_tree_type", "sweepNode"):
            raise _rule(path, "sweep parent %r must start its PROP with `sweep_tree_type sweepNode`" % e.key)
        rest = props[1:]
    else:
        if not props or props[0][0] != "data_type":
            raise _rule(path, "logFile entry %r must start its PROP with data_type" % e.key)
        rest = props[1:]
        if e.parent:
            if not rest or rest[0] != ("sweep_tree_type", "leafNode"):
                raise _rule(path, "leaf %r must carry `sweep_tree_type leafNode` after data_type" % e.key)
            rest = rest[1:]
        elif rest:
            raise _rule(path, "logFile entry %r carries PROPs beyond data_type: %r" % (e.key, rest))
    if e.parent:
        parent = log.by_key[e.parent]
        pfile = files[parent.key]
        pvar = pfile.sweep.name if pfile.sweep is not None else None
        if len(rest) != 1 or rest[0][0] != pvar:
            raise _rule(path, "entry %r must end its PROP with its parent's sweep variable %r, got %r"
                        % (e.key, pvar, rest))
        idx = _leaf_index(parent, e.key, path)
        pvals = pfile.sweep_values()
        if not 0 <= idx < len(pvals):
            raise _rule(path, "entry %r has point index %d but its parent %r has %d points"
                        % (e.key, idx, parent.key, len(pvals)))
        want, got = pvals[idx], rest[0][1]
        if not isinstance(got, (int, float)) or not math.isclose(float(got), float(want),
                                                                  rel_tol=PROP_REL_TOL, abs_tol=0.0):
            raise _rule(path, "entry %r carries %s = %r but its parent's value at index %d is %r"
                        % (e.key, pvar, got, idx, want))
    elif rest and not e.is_parent:
        raise _rule(path, "logFile entry %r carries PROPs beyond data_type: %r" % (e.key, rest))
    elif rest and e.is_parent:
        raise _rule(path, "top-level sweep parent %r carries PROPs beyond sweep_tree_type: %r" % (e.key, rest))


# -- command line ----------------------------------------------------------------------------------

def main(argv: List[str]) -> int:
    esc = False
    datadir: Optional[str] = None
    files: List[str] = []
    it = iter(argv)
    for a in it:
        if a == "--escchars":
            esc = True
        elif a == "--datadir":
            datadir = next(it, None)
        else:
            files.append(a)
    bad = 0
    for f in files:
        try:
            r = check(f, escchars=esc, datadir=datadir)
        except PsfError as e:
            bad += 1
            print("FAIL %s" % e)
            continue
        if isinstance(r, LogFile):
            print("PASS %s: logFile, %d entries, %d parents" % (
                os.path.basename(f), len(r.entries), sum(1 for e in r.entries if e.is_parent)))
        else:
            print("PASS %s: %s, %d types, sweep=%s, %d traces, %d points, %d values" % (
                os.path.basename(f), r.kind, len(r.types), r.sweep.name if r.sweep else None,
                len(r.traces), r.points, len(r.values)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
