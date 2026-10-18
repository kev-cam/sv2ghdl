"""PSF ASCII data files and the logFile (docs/VAMOS_SPECTRE_DESIGN.md §8.2, §8.3, §8.4; the API is
the §10 block).

Phase 0 (§12) froze the format constants and PsfHead; phase 1 (S4) adds the rest:

    psf_string(s, escchars)          the one PSF string encoder (§8.3), quotes included
    psf_date(t)                      §8.2's "date" from fixed English tables, never strftime [E86]
    TYPES                            the fixed 23.1 TYPE lists per family, verbatim from the samples (§8.3)
    write_analysis(dir, res, head)   one data file: HEADER TYPE [SWEEP TRACE] VALUE END
    write_logfile(dir, results, head)   the logFile (§8.2), written once at the end

Every value written here comes from an AnalysisResult (vamos.spectre.results): the signal names
are the SignalMap's Spectre names (§6.4), never an engine column name; the writers read no IR.
Streaming: a swept file is written point by point from `res.rows()` (§4.6), nothing is held.

The format fields are constants here, not banner keys, so a banner profile (`none` included)
never changes them: readers may key on them (§8.2).  "version" is always vamos's.

Families of TYPES (§8.3 names no keys; these are S4's, recorded in the phase-1 report):
    "op"       myop.dc            an operating point: LogicV V I MMF Wb Temp Pwr U V/Sec I/Sec 1/Sec HOD Q
    "swept"    mydc.dc, mytran.tran.tran   "sweep" + the "op" list (a dc sweep, a tran)
    "complex"  myac.ac            "sweep" + the COMPLEX variants (ac, xf), without LogicV
    "parent"   mysweep_ac1.sweep  "sweep" + the FLOAT list without LogicV (sweep and Monte Carlo parents)
    "noise"    noiva.noise (15.1) "sweep", "V/sqrt(Hz)", "V^2/Hz"; the STRUCTs and the gain/in types
                                  are added per file (§8.4)
All from psf-parser's tests/data/ascii (Spectre 23.1.0.242) except "noise", whose only sample is
psf_utils' 15.1 noiva.noise (its fixed entries carry no reals, so the layout is version independent).

Notes (a non-finite value, a rejected `precision`, a STRUCT type suffixed for its member set) are
appended to the `notes` list the caller passes (vamos.notes.Note); the flow prints them through
SpectreLog.  With `notes=None` nothing is recorded, so the flow always passes its list.

Progress (D3): `write_analysis(..., progress=f)` calls f(rows written so far) at least every
PROGRESS_ROWS rows (sooner for wide rows: every PROGRESS_VALUES values) and once after the last row
(an operating point: f(1), a parent: f(its points)); an exception f raises ends the write at once,
leaving `<file>.vamos-tmp` for the caller to remove (the flow does, §2.7).
"""

from __future__ import annotations

import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from vamos.notes import Note, note, warning
from vamos.spectre.results import AnalysisResult, Signal

# §8.2: data-format fields that readers may key on, so they keep Spectre's values
SIMULATOR = "spectre"
LOG_GENERATOR = "drlLog rev. 1.0"
SIM_MODE = "Spectre"
SIGNAL_NAME_TYPE = "spectre"

PSF_VERSION = "1.00"                 # the "PSFversion" header of every file [S]
DEFAULT_PRECISION = "%.15e"          # VALUE reals [S 23.1.0.242]
HEADER_PRECISION = "%.15e"           # header and TYPE PROP reals [S 23.1.0.242]
ISTEP = 1e-3                         # the tran header's "istep", 1e-3 in every sample [S]
PROGRESS_ROWS = 4096                 # `progress` is called at least every this many rows (D3) ...
PROGRESS_VALUES = 65536              # ... and every this many values of wide rows (well under 0.5 s of writing)

# `options precision`: a C conversion for VALUE reals (§8.3); `#` is added for g/G so a `.` or an
# exponent always appears (psf-parser rejects an integer token where FLOAT is declared [E83])
_PRECISION = re.compile(r"^%[-+ #0]*\d*(\.\d+)?[eEgG]$")

_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")          # datetime.weekday(): Monday = 0
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_SAFE = re.compile(r"[A-Za-z0-9_.:!]")

# §8.3's xVecSorted per analysis kind
XVEC_SORTED: Dict[str, str] = {"ac": "ascending", "noise": "ascending", "xf": "ascending",
                               "tran": "ascending", "montecarlo": "ascending", "dc": "unsorted",
                               "sweep": "unknown", "info": "unknown"}
# §8.2's logFile data_type per analysis kind
DATA_TYPE: Dict[str, str] = {"dc": "scalar", "ac": "swept_scalar", "tran": "swept_scalar",
                             "xf": "swept_scalar", "noise": "swept_struct", "info": "struct"}
PARENT_KINDS = ("sweep", "montecarlo")

# -- the fixed TYPE lists, verbatim from the samples (§8.3) ----------------------------------------

_SWEEP = '"sweep" FLOAT DOUBLE PROP(\n"key" "sweep"\n)'
_LOGICV = '"LogicV" FLOAT DOUBLE PROP(\n"key" "node"\n)'
_FLOAT_TYPES = [
    '"V" FLOAT DOUBLE PROP(\n"units" "V"\n"key" "node"\n"tolerance" 1.000000000000000e-06\n)',
    '"I" FLOAT DOUBLE PROP(\n"units" "A"\n"key" "branch"\n"tolerance" 1.000000000000000e-12\n)',
    '"MMF" FLOAT DOUBLE PROP(\n"units" "A*turn"\n"key" "node"\n"tolerance" 1.000000000000000e-12\n)',
    '"Wb" FLOAT DOUBLE PROP(\n"units" "Wb"\n"key" "node"\n"tolerance" 1.000000000000000e-09\n)',
    '"Temp" FLOAT DOUBLE PROP(\n"units" "C"\n"key" "node"\n"tolerance" 1.000000000000000e-04\n)',
    '"Pwr" FLOAT DOUBLE PROP(\n"units" "W"\n"key" "node"\n"tolerance" 1.000000000000000e-09\n)',
    '"U" FLOAT DOUBLE PROP(\n"units" ""\n"key" "node"\n"tolerance" 1.000000000000000e-06\n)',
    '"V/Sec" FLOAT DOUBLE PROP(\n"units" "V/s"\n"key" "node"\n"tolerance" 9.999999999999999e-05\n)',
    '"I/Sec" FLOAT DOUBLE PROP(\n"units" "A/s"\n"key" "node"\n"tolerance" 1.000000000000000e-06\n)',
    '"1/Sec" FLOAT DOUBLE PROP(\n"units" "1/s"\n"key" "node"\n"tolerance" 9.999999999999999e-05\n)',
    '"HOD" FLOAT DOUBLE PROP(\n"units" ""\n"key" "node"\n"tolerance" 1.000000000000000e-02\n)',
    '"Q" FLOAT DOUBLE PROP(\n"units" "Coul"\n"key" "node"\n"tolerance" 1.000000000000000e-15\n)',
]
_COMPLEX_TYPES = [
    '"V" COMPLEX DOUBLE PROP(\n"units" "V"\n"key" "node"\n"tolerance" 1.000000000000000e-06\n)',
    '"I" COMPLEX DOUBLE PROP(\n"units" "A"\n"key" "branch"\n"tolerance" 1.000000000000000e-12\n)',
    '"MMF" COMPLEX DOUBLE PROP(\n"units" "A*turn"\n"key" "node"\n"tolerance" 1.000000000000000e-12\n)',
    '"Wb" COMPLEX DOUBLE PROP(\n"units" "Wb"\n"key" "node"\n"tolerance" 1.000000000000000e-09\n)',
    '"Temp" COMPLEX DOUBLE PROP(\n"units" "C"\n"key" "node"\n"tolerance" 1.000000000000000e-04\n)',
    '"Pwr" COMPLEX DOUBLE PROP(\n"units" "W"\n"key" "node"\n"tolerance" 1.000000000000000e-09\n)',
    '"U" COMPLEX DOUBLE PROP(\n"units" ""\n"key" "node"\n"tolerance" 1.000000000000000e-06\n)',
    '"V/Sec" COMPLEX DOUBLE PROP(\n"units" "V/s"\n"key" "node"\n"tolerance" 9.999999999999999e-05\n)',
    '"I/Sec" COMPLEX DOUBLE PROP(\n"units" "A/s"\n"key" "node"\n"tolerance" 1.000000000000000e-06\n)',
    '"1/Sec" COMPLEX DOUBLE PROP(\n"units" "1/s"\n"key" "node"\n"tolerance" 9.999999999999999e-05\n)',
    '"HOD" COMPLEX DOUBLE PROP(\n"units" ""\n"key" "node"\n"tolerance" 1.000000000000000e-02\n)',
    '"Q" COMPLEX DOUBLE PROP(\n"units" "Coul"\n"key" "node"\n"tolerance" 1.000000000000000e-15\n)',
]
_NOISE_TYPES = [
    _SWEEP,
    '"V/sqrt(Hz)" FLOAT DOUBLE PROP(\n"units" "V/sqrt(Hz)"\n"key" "noise"\n)',
    '"V^2/Hz" FLOAT DOUBLE PROP(\n"units" "V^2/Hz"\n"key" "noise"\n)',
]

TYPES: Dict[str, List[str]] = {
    "op": [_LOGICV] + _FLOAT_TYPES,                 # myop.dc [S 23.1]
    "swept": [_SWEEP, _LOGICV] + _FLOAT_TYPES,      # mydc.dc, mytran.tran.tran [S 23.1]
    "complex": [_SWEEP] + _COMPLEX_TYPES,           # myac.ac [S 23.1]
    "parent": [_SWEEP] + _FLOAT_TYPES,              # mysweep_ac1.sweep, mymonte_dc2.montecarlo [S 23.1]
    "noise": _NOISE_TYPES,                          # noiva.noise [S 15.1], the fixed entries
}

# the types a noise file adds when it carries gain or an input-referred trace (§8.4, §6.4; [I, §14
# q.48] for their shape, which follows "V/sqrt(Hz)"'s)
NOISE_EXTRA_TYPES: Dict[str, str] = {
    "A/sqrt(Hz)": '"A/sqrt(Hz)" FLOAT DOUBLE PROP(\n"units" "A/sqrt(Hz)"\n"key" "noise"\n)',
    "V/V": '"V/V" FLOAT DOUBLE PROP(\n"units" "V/V"\n"key" "noise"\n)',
    "V/A": '"V/A" FLOAT DOUBLE PROP(\n"units" "V/A"\n"key" "noise"\n)',
}
# the types an xf file adds for its transfer-function traces (§6.4: "V/V" for a vsource, "V/A" for an
# isource), which the complex family does not declare; no Spectre xf sample exists, so their shape is
# the complex family's "V" entry without a tolerance, as NOISE_EXTRA_TYPES follow "V/sqrt(Hz)"'s
XF_EXTRA_TYPES: Dict[str, str] = {
    "V/V": '"V/V" COMPLEX DOUBLE PROP(\n"units" "V/V"\n"key" "node"\n)',
    "V/A": '"V/A" COMPLEX DOUBLE PROP(\n"units" "V/A"\n"key" "node"\n)',
}
FIXED_TYPE_NAMES = frozenset(["sweep", "LogicV", "V", "I", "MMF", "Wb", "Temp", "Pwr", "U", "V/Sec",
                              "I/Sec", "1/Sec", "HOD", "Q", "V/sqrt(Hz)", "V^2/Hz", "A/sqrt(Hz)",
                              "V/V", "V/A"])
STRUCT_MEMBER_UNITS = "V^2/Hz"      # every noise STRUCT member carries PROP("units" "V^2/Hz") [S]


@dataclass
class PsfHead:
    """Phase 0: the header values every data file and the logFile carry (§8.2, §8.3)."""
    version: str = ""
    date: str = ""
    design: str = ""
    simulator: str = SIMULATOR
    psfversion: str = "1.4.0"
    precision: str = "%.15e"                            # VALUE reals (options precision, §8.3)
    prop_precision: str = "%#g"                         # logFile PROP reals


# -- strings, dates, numbers -----------------------------------------------------------------------

def psf_string(s: str, escchars: bool = False) -> str:
    """The one PSF string encoder (§8.3): the quoted string with a backslash before `"` and `\\`;
    under +escchars (True) also before every character outside [A-Za-z0-9_.:!].  Applied to the
    unescaped name or text only (never to a name escaped beforehand, which would double the
    backslash before `<`, §14 q.12)."""
    out = ['"']
    for c in s:
        if c == '"' or c == "\\" or (escchars and not _SAFE.match(c)):
            out.append("\\")
        out.append(c)
    out.append('"')
    return "".join(out)


def psf_date(t: datetime) -> str:
    """§8.2's "date": fixed English tables, never strftime: "%d:%02d:%02d %s, %s %s %d, %d" with the
    hour on a 12-hour clock unpadded, AM/PM, Sun..Sat, Jan..Dec, the day unpadded, the year
    (1:07:28 PM, Tue Feb 2, 2021 [S 19.1 joop-banaan.dc])."""
    hour = t.hour % 12 or 12
    ampm = "AM" if t.hour < 12 else "PM"
    return "%d:%02d:%02d %s, %s %s %d, %d" % (hour, t.minute, t.second, ampm, _DAYS[t.weekday()],
                                             _MONTHS[t.month - 1], t.day, t.year)


def precision(text: Optional[str], notes: Optional[List[Note]] = None, origin: str = "") -> str:
    """The VALUE real format from `options precision="<C conversion>"` (§8.3): accepted when it
    matches %[-+ #0]*\\d*(\\.\\d+)?[eEgG], with `#` added for g/G; anything else (None included)
    gives %.15e, a rejected text with a warning."""
    if text is None or text == "":
        return DEFAULT_PRECISION
    if not _PRECISION.match(text):
        if notes is not None:
            notes.append(warning(origin, "options precision=%r is not a %%[-+ #0]*[width][.digits]e|E|g|G "
                                         "conversion; %s is used" % (text, DEFAULT_PRECISION)))
        return DEFAULT_PRECISION
    if text[-1] in "gG" and "#" not in text:
        return "%#" + text[1:]
    return text


def _real(v: float, fmt: str) -> str:
    """A FLOAT token: `fmt` for a finite value; nan, inf or -inf otherwise (§8.3)."""
    v = float(v)
    if math.isnan(v):
        return "nan"
    if math.isinf(v):
        return "inf" if v > 0 else "-inf"
    return fmt % v


def _header_value(v: object) -> str:
    """A HEADER scalar by its Python type: strings quoted, numbers as %.15e reals (every header
    real is a FLOAT in the samples [S 23.1.0.242]); a bool is written as yes/no."""
    if isinstance(v, bool):
        return psf_string("yes" if v else "no")
    if isinstance(v, (int, float)):
        return _real(float(v), HEADER_PRECISION)
    return psf_string(str(v))


def progress_period(width: int) -> int:
    """The rows between two `progress` calls for rows of `width` values (D3): PROGRESS_ROWS, fewer
    for wide rows, so that a call comes at least every PROGRESS_VALUES values written."""
    return max(1, min(PROGRESS_ROWS, PROGRESS_VALUES // max(1, width)))


# -- data files (§8.3) -----------------------------------------------------------------------------

class _Writer:
    """Writes one data file; keeps the per-signal non-finite warnings to one each."""

    def __init__(self, fh, res: AnalysisResult, head: PsfHead, escchars: bool,
                 notes: Optional[List[Note]], masters: Optional[Dict[str, str]],
                 progress: Optional[Callable[[int], object]] = None) -> None:
        self.fh, self.res, self.head, self.esc = fh, res, head, escchars
        self.notes, self.masters, self.progress = notes, masters or {}, progress
        self.fmt = precision(head.precision, notes, res.file)
        self.warned: set = set()
        self.tnames: List[str] = [sig.ptype for sig in res.signals]   # each trace's TYPE name (types())

    def s(self, text: str) -> str:
        return psf_string(text, self.esc)

    def line(self, text: str) -> None:
        self.fh.write(text + "\n")

    def real(self, v: object, signal: str) -> str:
        fv = float(v)  # type: ignore[arg-type]
        if (math.isnan(fv) or math.isinf(fv)) and signal not in self.warned:
            self.warned.add(signal)
            if self.notes is not None:
                self.notes.append(warning(self.res.file, "signal %r has a non-finite value (%s), written as is"
                                          % (signal, _real(fv, self.fmt))))
        return _real(fv, self.fmt)

    def value(self, v: object, sig: Signal) -> str:
        """One VALUE token for a trace: a real, `(re im)` for a complex, or a STRUCT body."""
        if sig.members is not None:
            vals = list(v) if isinstance(v, (list, tuple)) else [v]
            if len(vals) != len(sig.members):
                raise ValueError("%s: STRUCT %r has %d members but a row gives %d values"
                                 % (self.res.file, sig.name, len(sig.members), len(vals)))
            return "(\n" + "".join(self.real(x, sig.name) + "\n" for x in vals) + ")"
        if isinstance(v, complex):
            return "(%s %s)" % (self.real(v.real, sig.name), self.real(v.imag, sig.name))
        return self.real(v, sig.name)

    # -- sections
    def header(self) -> None:
        res, head = self.res, self.head
        self.line("HEADER")
        for k, v in (("PSFversion", PSF_VERSION), ("simulator", head.simulator), ("version", head.version),
                     ("date", head.date), ("design", head.design), ("analysis type", res.atype),
                     ("analysis name", analysis_name(res)), ("analysis description", res.description),
                     ("xVecSorted", XVEC_SORTED.get(res.atype, "unknown"))):
            self.line("%s %s" % (psf_string(k), self.s(str(v)) if k == "design" else psf_string(str(v))))
        items = list(res.header.items())
        if not any(k == "tolerance.relative" for k, _ in items):
            items.insert(0, ("tolerance.relative", 1e-3))
        for k, v in items:
            self.line("%s %s" % (psf_string(k), _header_value(v)))

    def types(self) -> None:
        res = self.res
        self.line("TYPE")
        family = type_family(res)
        for entry in TYPES[family]:
            self.line(entry)
        if family == "complex":
            declared_c = set(_type_name(e) for e in TYPES[family])
            for sig in res.signals:
                if sig.members is None and sig.ptype not in declared_c:
                    extra = XF_EXTRA_TYPES.get(sig.ptype)
                    if extra is None:
                        raise ValueError("%s: trace %r has type %r, which the %s file does not declare"
                                         % (res.file, sig.name, sig.ptype, res.atype))
                    self.line(extra)
                    declared_c.add(sig.ptype)
        if family == "noise":
            declared = set(["sweep", "V/sqrt(Hz)", "V^2/Hz"])
            structs: Dict[str, Tuple[str, Tuple[str, ...]]] = {}     # STRUCT type -> (first trace, members)
            variants: Dict[Tuple[str, Tuple[str, ...]], str] = {}    # (type, members) -> the declared name
            taken = set(s.ptype for s in res.signals) | FIXED_TYPE_NAMES
            for i, sig in enumerate(res.signals):
                if sig.members is not None and sig.ptype in FIXED_TYPE_NAMES:
                    raise ValueError("%s: STRUCT type %r of trace %r collides with a fixed type name; "
                                     "signals.resolve suffixes such names (design §8.4)"
                                     % (res.file, sig.ptype, sig.name))
                if sig.members is None:
                    if sig.ptype in declared:
                        if sig.ptype in structs:
                            raise ValueError("%s: type %r is both a STRUCT and a scalar type" % (res.file, sig.ptype))
                        continue
                    extra = NOISE_EXTRA_TYPES.get(sig.ptype)
                    if extra is None:
                        raise ValueError("%s: trace %r has type %r, which is neither a noise type nor a STRUCT"
                                         % (res.file, sig.name, sig.ptype))
                    self.line(extra)
                    declared.add(sig.ptype)
                    continue
                mset = tuple(m for m, _ in sig.members)
                if (sig.ptype, mset) in variants:
                    self.tnames[i] = variants[(sig.ptype, mset)]
                    continue
                if sig.ptype in declared and sig.ptype not in structs:
                    raise ValueError("%s: type %r is both a STRUCT and a scalar type" % (res.file, sig.ptype))
                name = sig.ptype
                if name in structs:                 # each distinct member set is a type of its own (§8.4)
                    k = 2
                    while "%s_%d" % (sig.ptype, k) in taken | declared:
                        k += 1
                    name = "%s_%d" % (sig.ptype, k)
                    self.struct_note(sig, mset, name, *structs[sig.ptype])
                master = self.masters.get(sig.ptype, sig.ptype)
                body = "".join('%s FLOAT DOUBLE PROP(\n"units" %s\n)\n'
                               % (self.s(m), psf_string(STRUCT_MEMBER_UNITS)) for m in mset)
                self.line('%s STRUCT(\n%s) PROP(\n"key" "inst"\n"master" %s\n)'
                          % (psf_string(name), body, self.s(master)))
                structs[name] = (sig.name, mset)
                variants[(sig.ptype, mset)] = name
                declared.add(name)
                self.tnames[i] = name

    def struct_note(self, sig: Signal, mset: Tuple[str, ...], name: str, first: str,
                    first_set: Tuple[str, ...]) -> None:
        """One note per run (the same text is not repeated for a sweep's next leaf) for a STRUCT type
        written under a suffixed name because its member set differs from the first one's (R-emit-5)."""
        if self.notes is None:
            return
        text = ("noise STRUCT type `%s' of `%s' has the members (%s), not those of `%s' (%s); written as `%s' "
                "(§8.4)" % (sig.ptype, sig.name, " ".join(mset), first, " ".join(first_set), name))
        if not any(n.message == text for n in self.notes):
            self.notes.append(note(self.res.file, text))

    def sweep(self) -> None:
        res = self.res
        if res.sweep is None:
            return
        name, units, grid, _ = res.sweep
        self.line("SWEEP")
        self.line('%s "sweep" PROP(\n"sweep_direction" 0\n"units" %s\n"plot" 0\n"grid" %d\n)'
                  % (self.s(name), self.s(units), int(grid)))

    def trace(self) -> None:
        """TRACE: each name (escaped under +escchars) and its type, referenced exactly as TYPE
        declares it: a type name is not a signal name and is never +escchars-escaped, so a reader
        that matches the reference against the declaration literally resolves it."""
        res = self.res
        if res.sweep is None or res.atype in PARENT_KINDS:
            return
        self.line("TRACE")
        for sig, tname in zip(res.signals, self.tnames):
            self.line("%s %s%s" % (self.s(sig.name), psf_string(tname), _current_prop(sig)))

    def values(self) -> None:
        res = self.res
        self.line("VALUE")
        if res.sweep is None:                                   # an operating point
            for sig, tname in zip(res.signals, self.tnames):
                if len(sig.values) != 1:
                    raise ValueError("%s: operating-point signal %r carries %d values, not one"
                                     % (res.file, sig.name, len(sig.values)))
                self.line("%s %s %s%s" % (self.s(sig.name), psf_string(tname),
                                          self.value(sig.values[0], sig), _current_prop(sig)))
            self.done(1)
            return
        sname = self.s(res.sweep[0])
        if res.atype in PARENT_KINDS:
            for x in res.sweep[3]:
                self.line("%s %s" % (sname, self.real(x, res.sweep[0])))
            self.done(len(res.sweep[3]))
            return
        if res.rows is None:
            raise ValueError("%s: a swept result without rows" % res.file)
        sigs = res.signals
        progress, n = self.progress, 0
        period = progress_period(1 + sum(1 if s.members is None else len(s.members) + 2 for s in sigs))
        for x, vals in res.rows():
            if len(vals) != len(sigs):
                raise ValueError("%s: a row carries %d values for %d traces" % (res.file, len(vals), len(sigs)))
            self.line("%s %s" % (sname, self.real(x, res.sweep[0])))
            for sig, v in zip(sigs, vals):
                self.line("%s %s" % (self.s(sig.name), self.value(v, sig)))
            n += 1
            if progress is not None and n % period == 0:
                progress(n)
        if n == 0 or n % period:
            self.done(n)

    def done(self, n: int) -> None:
        """The `progress` call after the last row (D3)."""
        if self.progress is not None:
            self.progress(n)

    def write(self) -> None:
        self.header()
        self.types()
        self.sweep()
        self.trace()
        self.values()
        self.line("END")


def _type_name(entry: str) -> str:
    """The name a TYPE entry declares (its first quoted string)."""
    return entry.split('"')[1]


def _current_prop(sig: Signal) -> str:
    """A current trace or value carries PROP("units" "A") [S 19.1]."""
    return ' PROP(\n"units" "A"\n)' if sig.ptype == "I" else ""


def analysis_name(res: AnalysisResult) -> str:
    """The "analysis name": the key without its `-<type>` suffix (§8.2's key rule)."""
    suffix = "-" + res.atype
    return res.key[:-len(suffix)] if res.key.endswith(suffix) else res.key


def type_family(res: AnalysisResult) -> str:
    """The TYPES family a result's file carries (the module docstring)."""
    if res.atype in PARENT_KINDS:
        return "parent"
    if res.atype in ("ac", "xf"):
        return "complex"
    if res.atype == "noise":
        return "noise"
    return "op" if res.sweep is None else "swept"


def write_analysis(dirpath: str, res: AnalysisResult, head: PsfHead, escchars: bool = False,
                   notes: Optional[List[Note]] = None, masters: Optional[Dict[str, str]] = None,
                   progress: Optional[Callable[[int], object]] = None) -> None:
    """Write `<dirpath>/<res.file>` (§8.3): the header table for its kind (res.header, built by
    results.collect), the fixed TYPE list of its family plus the noise STRUCTs, SWEEP, TRACE and the
    VALUEs streamed from res.rows() (an operating point from Signal.values).  A result whose status
    is "failed" has no data and is not written (§2.7: the analyses that finished are converted).
    `masters` maps a noise STRUCT type name to the master it is written with (PROP "master", §8.4);
    a type absent from it is its own master.  STRUCT traces of one type name with different member
    sets get one type each, the later ones `<type>_<k>` with a note (§8.4).  `notes` collects the
    warnings and notes (non-finite values, a rejected precision, a suffixed type).  `progress` (D3):
    called with the rows written so far at least every PROGRESS_ROWS rows and after the last row; an
    exception it raises ends the write at once, `<file>.vamos-tmp` left for the caller to remove and
    the file itself untouched."""
    if res.status == "failed":
        return
    os.makedirs(dirpath, exist_ok=True)
    path = os.path.join(dirpath, res.file)
    tmp = path + ".vamos-tmp"
    with open(tmp, "w", encoding="latin-1", newline="\n") as fh:
        _Writer(fh, res, head, escchars, notes, masters, progress).write()
    os.replace(tmp, path)


# -- the logFile (§8.2) ----------------------------------------------------------------------------

LOG_STRUCT = ('"analysisInst" STRUCT(\n"analysisType" STRING *\n"dataFile" STRING *\n"format" STRING *\n'
              '"parent" STRING *\n"sweepVariable" ARRAY ( * ) STRING *\n"description" STRING *\n)')


def write_logfile(dirpath: str, results: Iterable[AnalysisResult], head: PsfHead,
                  escchars: bool = False, stamp: Optional[str] = None) -> None:
    """Write `<dirpath>/logFile` (§8.2) listing `results` in the given order (execution order,
    point-major: results.collect yields them so); results whose status is "failed" are left out,
    since logFile lists the analyses that finished (and after status 3 the interrupted one).  Always
    PSF ASCII.  `stamp` replaces time.asctime() for the "Log Time Stamp" (tests)."""
    os.makedirs(dirpath, exist_ok=True)
    path = os.path.join(dirpath, "logFile")
    tmp = path + ".vamos-tmp"
    with open(tmp, "w", encoding="latin-1", newline="\n") as fh:
        w = fh.write
        w("HEADER\n")
        for k, v in (("PSFversion", PSF_VERSION), ("Log Generator", LOG_GENERATOR),
                     ("Log Time Stamp", stamp if stamp is not None else time.asctime()),
                     ("psfversion", head.psfversion), ("simulator", head.simulator), ("version", head.version),
                     ("date", head.date)):
            w("%s %s\n" % (psf_string(k), psf_string(v)))
        w("%s %s\n" % (psf_string("design"), psf_string(head.design, escchars)))
        for k, v in (("signalNameType", SIGNAL_NAME_TYPE), ("simMode", SIM_MODE)):
            w("%s %s\n" % (psf_string(k), psf_string(v)))
        w('"measdgt" 0\n"ingold" 2\n"sst2usecolon" 0\n')
        w("TYPE\n%s\nVALUE\n" % LOG_STRUCT)
        for res in results:
            if res.status == "failed":
                continue
            sv = "(%s)" % psf_string(res.sweep[0], escchars) if res.sweep is not None else "()"
            w('%s "analysisInst" (\n%s\n%s\n"PSF"\n%s\n%s\n%s\n)' % (
                psf_string(res.key, escchars), psf_string(res.atype), psf_string(res.file, escchars),
                psf_string(res.parent, escchars), sv, psf_string(res.description, escchars)))
            props = _log_props(res, head, escchars)
            if props is None:                   # a Monte Carlo parent: no PROP, no blank line [S]
                w("\n")
                continue
            w(" PROP(\n")
            for k, v in props:
                w("%s %s\n" % (psf_string(k, escchars), v))
            w(")\n\n")
        w("END\n")
    os.replace(tmp, path)


def _log_props(res: AnalysisResult, head: PsfHead, escchars: bool) -> Optional[List[tuple]]:
    """A logFile entry's PROP lines (§8.2): parents `sweep_tree_type sweepNode` (Monte Carlo parents
    none); leaves `data_type`, `sweep_tree_type leafNode`, the swept value; top-level analyses
    `data_type` alone.  The swept values are `%#g` (head.prop_precision)."""
    if res.atype == "montecarlo":
        return None
    out: List[tuple] = []
    if res.atype in PARENT_KINDS:
        out.append(("sweep_tree_type", psf_string("sweepNode")))
    else:
        out.append(("data_type", psf_string(res.data_type or DATA_TYPE.get(res.atype, "scalar"))))
        if res.parent:
            out.append(("sweep_tree_type", psf_string("leafNode")))
    for k, v in res.swept.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append((k, _real(float(v), head.prop_precision)))
        else:
            out.append((k, psf_string(str(v), escchars)))
    return out


__all__ = ["SIMULATOR", "LOG_GENERATOR", "SIM_MODE", "SIGNAL_NAME_TYPE", "PSF_VERSION", "TYPES",
           "NOISE_EXTRA_TYPES", "XF_EXTRA_TYPES", "FIXED_TYPE_NAMES", "XVEC_SORTED", "DATA_TYPE",
           "PARENT_KINDS", "PROGRESS_ROWS", "PROGRESS_VALUES", "PsfHead", "psf_string", "psf_date", "precision",
           "progress_period", "analysis_name", "type_family", "write_analysis", "write_logfile"]
