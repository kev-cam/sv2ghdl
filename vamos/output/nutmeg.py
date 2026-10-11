"""The nutascii / nutbin rawfile (docs/VAMOS_SPECTRE_DESIGN.md §8.5; the API is the §10 block).

"In the Nutmeg format, the Spectre simulator writes output data to a raw file" [M UG p.229]: one
rawfile at the results path, no logFile and no parent plots.  One plot per analysis (sweep leaves
included) in execution order; the variables carry the §6.4 names and the types time, frequency,
voltage and current; a noise plot has the columns frequency, out and <inst>:<member> for every
STRUCT member [I, §14 q.23]; a plot's name is the PSF analysis description [I, §14 q.23].

    nutmeg.write("rc.raw", results, binary=True)

The values stream from each result's rows() (§4.6): the point count is written after the data
into a field reserved in the header (as Xyce does), so nothing is held.  The file is what
vamos.netlist.rawfile.read_all reads back (§11), and ngspice loads it (T2).
"""

from __future__ import annotations

import math
import os
import struct
import time
from typing import Iterable, List, Optional, Tuple

from vamos.notes import Note, warning
from vamos.spectre.results import AnalysisResult, Signal

COUNT_WIDTH = 18                 # the blanks reserved for "No. Points:" (Xyce's layout, rawfile.py)
PARENT_KINDS = ("sweep", "montecarlo")
_X_NAME = {"tran": "time", "ac": "freq", "xf": "freq", "noise": "frequency"}
_X_TYPE = {"tran": "time", "ac": "frequency", "xf": "frequency", "noise": "frequency"}
_TYPE_BY_UNITS = {"V": "voltage", "A": "current", "s": "time", "Hz": "frequency"}
_TYPE_BY_PSF = {"V": "voltage", "I": "current"}


def _variables(res: AnalysisResult) -> Tuple[List[Tuple[str, str]], bool]:
    """The plot's variables (name, type) and whether it is complex (§8.5)."""
    out: List[Tuple[str, str]] = []
    cplx = res.atype in ("ac", "xf")
    if res.sweep is not None:
        label, units = res.sweep[0], res.sweep[1]
        out.append((_X_NAME.get(res.atype, label), _X_TYPE.get(res.atype, _TYPE_BY_UNITS.get(units, "notype"))))
    for sig in res.signals:
        if sig.members is not None:
            for m, _ in sig.members:
                out.append(("%s:%s" % (sig.name, m), "notype"))
        else:
            out.append((sig.name, _TYPE_BY_PSF.get(sig.ptype, "notype")))
    return out, cplx


def _flatten(vals: List[object], signals: List[Signal]) -> List[object]:
    out: List[object] = []
    for sig, v in zip(signals, vals):
        if sig.members is not None:
            out.extend(v)  # type: ignore[arg-type]
        else:
            out.append(v)
    return out


def write(path: str, results: Iterable[AnalysisResult], binary: bool,
          notes: Optional[List[Note]] = None, title: str = "", date: Optional[str] = None) -> None:
    """Write every non-parent result as one plot of the rawfile `path` (§8.5), streaming.  Results
    whose status is "failed" have no data and are skipped.  `title` is the plot title (the design
    line); `date` the rawfile date line (time.asctime() when None); `notes` collects warnings."""
    date = date if date is not None else time.asctime()
    tmp = path + ".vamos-tmp"
    with open(tmp, "wb") as fh:
        for res in results:
            if res.atype in PARENT_KINDS or res.status == "failed":
                continue
            variables, cplx = _variables(res)
            head = ("Title: %s\nDate: %s\nPlotname: %s\nFlags: %s\nNo. Variables: %d\nNo. Points: "
                    % (title, date, res.description, "complex" if cplx else "real", len(variables)))
            fh.write(head.encode("latin-1", "replace"))
            count_at = fh.tell()
            fh.write(b" " * COUNT_WIDTH + b"\nVariables:\n")
            for k, (name, typ) in enumerate(variables):
                fh.write(("\t%d\t%s\t%s\n" % (k, name, typ)).encode("latin-1", "replace"))
            fh.write(b"Binary:\n" if binary else b"Values:\n")
            n = _data(fh, res, cplx, binary, notes)
            end = fh.tell()
            fh.seek(count_at)
            fh.write(("%d" % n).ljust(COUNT_WIDTH).encode("ascii"))
            fh.seek(end)
    os.replace(tmp, path)


def _data(fh, res: AnalysisResult, cplx: bool, binary: bool, notes: Optional[List[Note]]) -> int:
    """The points of one plot; returns their number."""
    signals = res.signals
    if res.sweep is None:                                # an operating point: one row, no x
        rows: Iterable[Tuple[Optional[object], List[object]]] = [(None, [s.values[0] for s in signals])]
    else:
        if res.rows is None:
            raise ValueError("%s: a swept result without rows" % res.key)
        rows = res.rows()
    n = 0
    warned: set = set()
    for x, vals in rows:
        flat = _flatten(vals, signals)
        if x is not None:
            flat.insert(0, x)
        for v in flat:
            fv = v.real if isinstance(v, complex) else float(v)  # type: ignore[arg-type]
            if (math.isnan(fv) or math.isinf(fv)) and res.key not in warned:
                warned.add(res.key)
                if notes is not None:
                    notes.append(warning(res.file, "plot `%s' holds a non-finite value, written as is" % res.key))
        if binary:
            if cplx:
                parts = []
                for v in flat:
                    c = complex(v)  # type: ignore[arg-type]
                    parts.extend((c.real, c.imag))
                fh.write(struct.pack("<%dd" % len(parts), *parts))
            else:
                fh.write(struct.pack("<%dd" % len(flat), *[float(v) for v in flat]))  # type: ignore[arg-type]
        else:
            text = ["%d" % n]
            for v in flat:
                if cplx:
                    c = complex(v)  # type: ignore[arg-type]
                    text.append("\t%.15e,%.15e" % (c.real, c.imag))
                else:
                    text.append("\t%.15e" % float(v))  # type: ignore[arg-type]
            fh.write(("\n".join(text) + "\n").encode("ascii"))
        n += 1
    return n


__all__ = ["write"]
