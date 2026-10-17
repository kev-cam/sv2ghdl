"""Engine results into per-analysis results (docs/VAMOS_SPECTRE_DESIGN.md §8, §4.6; the API is
the §10 block).

Phase 0 (§12): EngineResult, Signal and AnalysisResult are frozen.  Phase 1 (S4) adds collect():
streaming, sweep splitting, the noise and xf transforms.  It needs no IR: the sweep variables,
units and descriptions come from each step's SweepLevels (§5.2, §8.3), the trace types and noise
STRUCT types from the SignalMap (§6.4) and the header values from the step's args and options
(§5.1).  Engine columns are found only through EngineResult.names, by exact name (Raw.exact,
§4.6, §6.4); the x axis is the engine's own (VACASK: the SweepLevel.id column, `time` or
`frequency`; Xyce: the first column of each plot, or a .STEP's value, §7.2).

    for res in collect(plan, sigmap, er, "modern"):      # execution order, sweeps point-major (§8.2)
        psf.write_analysis(rawdir, res, head)
    psf.write_logfile(rawdir, results, head)

Reading is streamed (§4.6): a leaf's `rows()` walks rawfile.iter_rows over its own row range, so
a 10^6-point transient converts under a fixed footprint; only the group boundaries of a swept
VACASK plot (one pass over its x column) and an operating point's single rows are held.
"""

from __future__ import annotations

import itertools
import math
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from vamos.netlist import rawfile
from vamos.netlist.plan import AnalysisStep, RunPlan, SweepLevel
from vamos.netlist.signals import Ref, SignalMap
from vamos.notes import Note, note, warning


@dataclass
class EngineResult:
    rc: int = 0
    status: Dict[str, str] = field(default_factory=dict)        # step id -> ok | failed | interrupted
    files: Dict[str, str] = field(default_factory=dict)         # step id -> engine output file
    failed_points: Dict[str, int] = field(default_factory=dict) # step id -> first failed sweep point (1-based)
    names: Dict[str, Dict[Ref, str]] = field(default_factory=dict)   # from render (§4.5 item 6)
    log: List[str] = field(default_factory=list)


@dataclass
class Signal:
    name: str
    ptype: str
    units: str
    values: list = field(default_factory=list)                  # an op's single values only (swept: rows)
    members: Optional[List[Tuple[str, list]]] = None


@dataclass
class AnalysisResult:
    key: str
    atype: str
    file: str
    parent: str = ""
    tree: str = ""                                               # "", "leafNode" or "sweepNode"
    sweep: Optional[Tuple[str, str, int, List[float]]] = None    # (name, units, grid, values); values for
    #                                                              parents only, a leaf's x comes from rows
    signals: List[Signal] = field(default_factory=list)          # the traces, in TRACE order
    header: Dict[str, object] = field(default_factory=dict)
    description: str = ""
    swept: Dict[str, float] = field(default_factory=dict)
    data_type: str = ""
    status: str = "ok"                                           # ok | failed | interrupted
    rows: Optional[Callable[[], Iterator[Tuple[object, List[object]]]]] = None   # swept: (x, values in
    #                                                              TRACE order), streamed (§4.6)


# -- constants of §8 -------------------------------------------------------------------------------

ANALYSIS_TITLES = {                 # the description's first words (§8.3 [S]; xf: the ref19 chapter title
    "op": "DC Analysis", "dc": "DC Analysis", "ac": "AC Analysis", "noise": "Noise Analysis",
    "xf": "Transfer Function Analysis", "tran": "Transient Analysis",
}
DATA_TYPES = {"dc": "scalar", "ac": "swept_scalar", "tran": "swept_scalar", "xf": "swept_scalar",
              "noise": "swept_struct"}                                           # §8.2
PSF_UNITS = {"V": "V", "I": "A"}                                                 # TRACE units by type (§8.3)
RESISTOR_CONTRIBUTIONS = {"thermal": "rn", "flicker": "fn"}                      # §8.4 [S]
# Spectre's option defaults the header table falls back on when a step's options lack them
# (§3.10: temp and tnom are always 27 unless set; the tolerances are ref19's defaults)
OPTION_DEFAULTS = {"reltol": 1e-3, "vabstol": 1e-6, "iabstol": 1e-12, "temp": 27.0, "tnom": 27.0,
                   "tempeffects": "all", "gmin": 1e-12}
ISTEP = 1e-3                        # every sample's tran header carries "istep" 1e-3 [S]
_SI = {-6: "a", -5: "f", -4: "p", -3: "n", -2: "u", -1: "m", 0: "", 1: "k", 2: "M", 3: "G", 4: "T", 5: "P"}


def si(v: float, units: str) -> str:
    """A description number: %g with an SI prefix and the units (§8.3): 500 ms, 1 MHz, 4.19 MHz, 0 s."""
    v = float(v)
    if v == 0.0 or math.isnan(v) or math.isinf(v):
        text, prefix = "%g" % v, ""
    else:
        k = int(math.floor(math.log10(abs(v)) / 3.0))
        k = max(min(_SI), min(max(_SI), k))
        mant = v / (10.0 ** (3 * k))
        if abs(mant) >= 999.9999999:            # %g rounding carried into the next prefix
            k = min(max(_SI), k + 1)
            mant = v / (10.0 ** (3 * k))
        text, prefix = "%g" % mant, _SI[k]
    unit = prefix + units
    return text + (" " + unit if unit else "")


def grid_values(spec) -> List[float]:
    """The points of an evaluated SweepSpec (§5.2; every Expr field a folded Num after plan.build):
    `values` as given; lin=N: N+1 points; step: start, start+step, ... <= stop; dec=N (an integral
    span): start*10^(k/N), the last exactly stop."""
    def num(e) -> float:
        if e is None:
            raise ValueError("sweep %r: a bound is missing" % (spec,))
        return float(getattr(e, "value", e))
    mode = spec.mode
    if mode == "values" or spec.values:                 # filled values win (a "log" grid plan.build computed)
        return [num(e) for e in spec.values]
    start, stop = num(spec.start), num(spec.stop)
    if mode == "lin":
        n = int(spec.count or 0)
        if n < 1:
            return [start]
        return [start + (stop - start) * k / n for k in range(n)] + [stop]
    if mode == "step":
        step = num(spec.step)
        if step == 0 or (stop - start) * step < 0:
            return [start]
        n = int(math.floor((stop - start) / step + 1e-9))
        return [start + k * step for k in range(n + 1)]
    if mode == "dec":
        n = int(spec.count or 0)
        if n < 1 or start <= 0 or stop <= 0:
            return [start, stop]
        total = int(round(n * math.log10(stop / start)))
        if total < 1:
            return [start]
        return [start * 10.0 ** (k / float(n)) for k in range(total)] + [stop]
    if mode == "log":
        n = int(spec.count or 0)
        if n < 1 or start <= 0 or stop <= 0:
            return [start, stop]
        return [start * (stop / start) ** (k / float(n)) for k in range(n)] + [stop]
    raise ValueError("sweep %r: unknown mode %r" % (spec, mode))


def grid_of(level: Optional[SweepLevel]) -> int:
    """The SWEEP PROP `grid`: 3 for a logarithmic (dec) sweep, 1 otherwise (§8.3 [S])."""
    return 3 if level is not None and level.spec.mode in ("dec", "log") else 1


def _count_width(n: int) -> int:
    """The point index has 3 digits, more for >= 1000 points (§8.1)."""
    return max(3, len(str(max(n - 1, 0))))


# -- engine plots ----------------------------------------------------------------------------------

class _Plot:
    """One engine plot: its variables (a Raw with no points, for Raw.exact) and a row source."""

    def __init__(self, raw: rawfile.Raw, rows: Callable[[Sequence[int]], Iterator[tuple]], n: Optional[int]):
        self.raw, self._rows, self.n = raw, rows, n

    def exact(self, name: str) -> int:
        return self.raw.exact(name)

    def names(self) -> List[str]:
        return self.raw.names()

    def rows(self, cols: Sequence[int]) -> Iterator[tuple]:
        return self._rows(cols)


def _raw_header(path: str, start: int) -> rawfile.Raw:
    """The header of the plot that starts at byte `start`: its variables, flags and plot name, no
    data (the reader of §4.6 is rawfile.iter_rows; this only names the columns for Raw.exact)."""
    raw = rawfile.Raw()
    raw.path = path
    nvars = None
    with open(path, "rb") as fh:
        fh.seek(start)
        while True:
            line = fh.readline()
            if not line:
                raise rawfile.RawError("%s: no Binary: or Values: section" % path)
            text = line.decode("latin-1").rstrip("\r\n")
            if not text.strip():
                continue
            key, _, value = text.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "title":
                raw.title = value
            elif key == "date":
                raw.date = value
            elif key == "plotname":
                raw.plotname = value
            elif key == "flags":
                raw.flags = value.lower().split()
            elif key == "no. variables":
                nvars = int(value)
            elif key == "no. points":
                raw.declared_points = int(value) if value else None
            elif key == "variables":
                if nvars is None:
                    raise rawfile.RawError("%s: Variables: before No. Variables:" % path)
                lines = [value] if value else []
                while len(lines) < nvars:
                    vl = fh.readline().decode("latin-1").rstrip("\r\n")
                    if vl.strip():
                        lines.append(vl)
                for vl in lines:
                    fields = [f.strip() for f in vl.split("\t") if f.strip()] if "\t" in vl.strip() else vl.split()
                    raw.variables.append((fields[1], fields[2] if len(fields) > 2 else ""))
            elif key in ("binary", "values"):
                raw.binary = key == "binary"
                return raw
            else:
                raw.header[key] = value


def _raw_plots(path: str) -> List[_Plot]:
    """Every plot of a rawfile as a _Plot streaming through rawfile.iter_rows."""
    scanned = rawfile.scan(path)
    starts = [0] + [p.next_start for p in scanned[:-1]]
    plots = []
    for k, (start, sp) in enumerate(zip(starts, scanned)):
        raw = _raw_header(path, start)
        plots.append(_Plot(raw, lambda cols, k=k: rawfile.iter_rows(path, k, list(cols)), sp.n))
    return plots


def _prn_plots(path: str) -> List[_Plot]:
    """Every .STEP table of a Xyce .PRINT file (rawfile.read_prn), rows from memory."""
    plots = []
    for raw in rawfile.read_prn(path):
        pts = raw.points
        plots.append(_Plot(raw, lambda cols, pts=pts: (tuple(p[c] for c in cols) for p in pts), len(pts)))
    return plots


# -- the step reader -------------------------------------------------------------------------------

class _Trace:
    """How one PSF trace is computed from a fetched row: `fetch` names the engine columns it needs (by
    index into the plot), `make(vals)` computes its value from those columns' values."""

    def __init__(self, signal: Signal, cols: List[int], make: Callable[[Sequence[object]], object]) -> None:
        self.signal, self.cols, self.make = signal, cols, make


def _sqrt(v: object) -> float:
    x = float(v.real if isinstance(v, complex) else v)  # type: ignore[union-attr]
    return math.sqrt(x) if x >= 0 else float("nan")


class _Step:
    """One analysis step's engine output, decoded (§7.1, §7.2): its plots, the PSF traces built from
    the SignalMap and `names`, the x axis and the sweep groups."""

    def __init__(self, coll: "_Collector", step: AnalysisStep) -> None:
        self.c, self.step = coll, step
        self.engine = coll.plan.engine
        self.kind = step.kind
        self.atype = "dc" if self.kind in ("op", "dc") else self.kind
        self.ctx_values = [grid_values(lv.spec) for lv in step.context]
        self.counts = [len(v) for v in self.ctx_values]
        self.own = step.own
        self.own_values = grid_values(self.own.spec) if self.own is not None else []
        # the x axis: ("freq"|"time"|"param"|None) and its label/units
        if self.kind == "tran":
            self.axis, self.label, self.units = "time", "time", "s"
        elif self.own is not None and self.own.target[0] == "freq":
            self.axis, self.label, self.units = "freq", self.own.label or "freq", self.own.units or "Hz"
        elif self.own is not None:
            self.axis, self.label, self.units = "param", self.own.label, self.own.units
        else:
            self.axis, self.label, self.units = None, "", ""
        # Xyce renders a parameter sweep of ac/noise/xf at a fixed frequency as a .STEP (§7.2)
        self.own_stepped = (self.engine == "xyce" and self.axis == "param" and self.kind in ("ac", "noise", "xf"))
        self.status = coll.er.status.get(step.id, "ok")
        self.path = coll.er.files.get(step.id)
        self.plots: List[_Plot] = []
        self.traces: List[_Trace] = []
        self.cols: List[int] = []
        self.xcol: Optional[int] = None
        self.xpos = -1                                  # the x column's place in a fetched row
        self.groups: List[Tuple[int, int, int]] = []    # (plot index, first row, last row) per group, or
        self.loaded = False                             # (plot, 0, -1) for a whole plot

    # -- loading
    def load(self) -> None:
        if self.loaded:
            return
        self.loaded = True
        if self.status == "failed" and self.step.id not in self.c.er.failed_points:
            return
        if not self.path or not os.path.isfile(self.path):
            if self.status != "failed":
                self.c.warn(self.step.name, "the engine left no output file for analysis `%s'" % self.step.name)
                self.status = "failed"
            return
        try:
            if self.path.endswith(".prn"):
                self.plots = _prn_plots(self.path)
            else:
                self.plots = _raw_plots(self.path)
        except (rawfile.RawError, OSError, ValueError) as e:
            self.c.warn(self.step.name, "the output of analysis `%s' is unreadable: %s" % (self.step.name, e))
            self.status = "failed"
            return
        if not self.plots:
            self.status = "failed"
            return
        self._traces(self.plots[0])
        self._groups()

    def _traces(self, plot: _Plot) -> None:
        """The PSF traces of this step from the SignalMap entries and the name map (§6.4, §8.4)."""
        step, names = self.step, self.c.er.names.get(self.step.id, {})
        entries = self.c.sigmap.per_step.get(step.id, [])
        colmap: Dict[int, int] = {}

        def col(ref: Ref) -> Optional[int]:
            text = names.get(ref)
            if text is None:
                return None
            try:
                k = plot.exact(text)
            except KeyError:
                return None
            if k not in colmap:
                colmap[k] = len(self.cols)
                self.cols.append(k)
            return colmap[k]

        def missing(name: str) -> None:
            self.c.warn(step.name, "signal `%s' of analysis `%s' has no column in the engine output; not written"
                        % (name, step.name))

        var_names = plot.names()
        xyce_total_noted = False
        for ref, name, ptype in entries:
            kind = ref[0]
            if kind in ("v", "i", "tf"):
                k = col(ref)
                if k is None:
                    missing(name)
                    continue
                self.traces.append(_Trace(Signal(name, ptype, PSF_UNITS.get(ptype, ptype)), [k],
                                          lambda v, k=k: v[k]))
            elif kind == "onoise":
                k = col(ref)
                if k is None:
                    missing(name)
                    continue
                self.traces.append(_Trace(Signal(name, ptype, ptype), [k], lambda v, k=k: _sqrt(v[k])))
            elif kind == "gain":
                # §8.4 per engine: the name maps send ('gain',) and ('inoise',) to one column on each
                # engine (VACASK `gain`, the power gain; Xyce INOISE), so the formula follows the engine
                if self.engine != "xyce":                               # VACASK: sqrt(power gain)
                    k = col(ref)
                    if k is None:
                        missing(name)
                        continue
                    self.traces.append(_Trace(Signal(name, ptype, ptype), [k], lambda v, k=k: _sqrt(v[k])))
                    continue
                ko, ki = col(("onoise",)), col(("inoise",)) if ("inoise",) in names else col(ref)
                if ko is None or ki is None:
                    missing(name)
                    continue
                self.traces.append(_Trace(Signal(name, ptype, ptype), [ko, ki],         # Xyce: sqrt(ON/IN)
                                          lambda v, ko=ko, ki=ki: _sqrt(float(v[ko]) / float(v[ki])
                                                                        if float(v[ki]) else float("nan"))))
            elif kind == "inoise":
                if self.engine == "xyce":                               # Xyce: sqrt(INOISE)
                    k = col(ref)
                    if k is None:
                        missing(name)
                        continue
                    self.traces.append(_Trace(Signal(name, ptype, ptype), [k], lambda v, k=k: _sqrt(v[k])))
                    continue
                ko = col(("onoise",))                                   # VACASK: out / gain
                kg = col(("gain",)) if ("gain",) in names else col(ref)
                if ko is None or kg is None:
                    missing(name)
                    continue
                self.traces.append(_Trace(Signal(name, ptype, ptype), [ko, kg],
                                          lambda v, ko=ko, kg=kg: (_sqrt(v[ko]) / _sqrt(v[kg])
                                                                   if float(v[kg]) > 0 else float("nan"))))
            elif kind == "noise":
                k = col(ref)
                if k is None:
                    missing(name)
                    continue
                total_col = names[ref]
                members: List[Tuple[str, int]] = []
                if total_col.endswith(")"):                             # VACASK n(inst) -> n(inst,contrib)
                    prefix = total_col[:-1] + ","
                    for vn in var_names:
                        if vn.startswith(prefix) and vn.endswith(")"):
                            contrib = vn[len(prefix):-1]
                            members.append((contrib, self._col_by_name(plot, vn, colmap)))
                if not members and self.engine == "xyce" and not xyce_total_noted:
                    xyce_total_noted = True
                    self.c.note(step.name, "noise analysis `%s' on Xyce: the per-instance STRUCTs carry only "
                                "`total' (DNO), no contributions" % step.name)
                master = self.c.masters.get(ptype, ptype)
                mnames = [RESISTOR_CONTRIBUTIONS.get(m, m) if master == "resistor" else m for m, _ in members]
                sig = Signal(name, ptype, "", members=[(m, []) for m in mnames] + [("total", [])])
                mcols = [c for _, c in members] + [k]
                self.traces.append(_Trace(sig, mcols, lambda v, mcols=mcols: [v[c] for c in mcols]))
            else:
                missing(name)
        # the x column
        if self.engine == "vacask":
            xname = {"time": "time", "freq": "frequency"}.get(self.axis or "")
            if self.axis == "param":
                xname = self.own.id if self.own is not None else None
            if xname is not None:
                try:
                    self.xcol = plot.exact(xname)
                except KeyError:
                    self.c.warn(step.name, "analysis `%s': the engine output has no %r column" % (step.name, xname))
                    self.status = "failed"
                    return
        elif self.axis is not None and not self.own_stepped:
            self.xcol = 0                                                # Xyce: the first printed column
        if self.xcol is not None:
            self.cols.append(self.xcol)
            self.xpos = len(self.cols) - 1

    def _col_by_name(self, plot: _Plot, name: str, colmap: Dict[int, int]) -> int:
        k = plot.exact(name)
        if k not in colmap:
            colmap[k] = len(self.cols)
            self.cols.append(k)
        return colmap[k]

    def _groups(self) -> None:
        """The sweep groups (§7.1 Reading, §7.2 Output): VACASK one plot, split by the known inner
        counts or the restart of the time/frequency axis; Xyce one plot per .STEP point."""
        if self.status == "failed" and not self.plots:
            return
        if self.engine == "xyce":
            per = len(self.own_values) if self.own_stepped else 1
            n = len(self.plots) // per if per else 0
            self.groups = [(g, 0, -1) for g in range(n)]
            self._short_of_plan()
            return
        plot = self.plots[0]
        if not self.step.context:
            self.groups = [(0, 0, -1)]
            return
        total = plot.n if plot.n is not None else sum(1 for _ in plot.rows([0]))
        if self.axis in ("time", "freq") and self.xcol is not None:
            bounds: List[int] = []
            prev = None
            for i, (x,) in enumerate(plot.rows([self.xcol])):
                xv = x.real if isinstance(x, complex) else x
                if prev is None or xv <= prev:
                    bounds.append(i)
                prev = xv
            bounds.append(total)
            self.groups = [(0, a, b) for a, b in zip(bounds, bounds[1:])]
        else:
            size = len(self.own_values) if self.axis == "param" else 1
            size = max(size, 1)
            full = total // size
            self.groups = [(0, g * size, (g + 1) * size) for g in range(full)]
            if total % size and self.status == "interrupted":
                self.groups.append((0, full * size, total))
        planned = 1
        for n in self.counts:
            planned *= n
        if len(self.groups) > planned:
            self.c.warn(self.step.name, "analysis `%s' left %d sweep groups where %d were planned; the extra "
                        "ones are ignored" % (self.step.name, len(self.groups), planned))
            self.groups = self.groups[:planned]
        self._short_of_plan()

    def _short_of_plan(self) -> None:
        """A step the runner passed (ok) whose output holds fewer sweep groups than planned: its missing
        points are failed leaves (group_status), and the run's status 1 then names them (§0's one rule,
        §2.7: a short output counts, whatever the engine returned; phase 2's integration)."""
        if self.status != "ok":
            return
        planned = 1
        for n in self.counts:
            planned *= n
        if len(self.groups) < planned:
            self.c.warn(self.step.name, "analysis `%s' left %d of the %d sweep points planned; the missing ones "
                        "are not written" % (self.step.name, len(self.groups), planned))

    # -- rows
    def row_source(self, g: int) -> Callable[[], Iterator[tuple]]:
        """The fetched rows of group g, as a callable (each call re-reads from the file)."""
        plot_k, a, b = self.groups[g]
        if self.own_stepped:
            per = len(self.own_values)

            def stepped() -> Iterator[tuple]:
                for j in range(per):
                    for row in itertools.islice(self.plots[plot_k * per + j].rows(self.cols), 0, 1):
                        yield row
            return stepped
        if b < 0:
            return lambda: self.plots[plot_k].rows(self.cols)
        return lambda: itertools.islice(self.plots[plot_k].rows(self.cols), a, b)

    def xy_rows(self, g: int) -> Callable[[], Iterator[Tuple[object, List[object]]]]:
        src = self.row_source(g)
        traces = self.traces
        if self.own_stepped:
            xs = self.own_values

            def it_stepped() -> Iterator[Tuple[object, List[object]]]:
                for x, row in zip(xs, src()):
                    yield x, [t.make(row) for t in traces]
            return it_stepped
        xpos = self.xpos
        keep = self.tran_filter() if self.kind == "tran" else None

        def it() -> Iterator[Tuple[object, List[object]]]:
            for row in src():
                x = row[xpos]
                xv = x.real if isinstance(x, complex) else x
                if keep is not None and not keep(xv):
                    continue
                yield xv, [t.make(row) for t in traces]
        return it

    def tran_filter(self) -> Optional[Callable[[float], bool]]:
        """The tran output filters (§3.11, §6.2): points before `outputstart` are dropped; between
        `skipstart` and `skipstop` only one of every `skipcount` points is kept; a save's
        `time_window=[t1 t2 t3 t4 ...]` keeps the points inside those intervals.  None when no filter
        applies (the engines already honour outputstart natively, §7.1, §7.2)."""
        args = self.step.args
        start = float(args.get("start", 0.0))  # type: ignore[arg-type]
        stop = float(args.get("stop", float("inf")))  # type: ignore[arg-type]
        outputstart = float(args.get("outputstart", start))  # type: ignore[arg-type]
        skipcount = int(float(args.get("skipcount", 1)))  # type: ignore[arg-type]
        skipstart = float(args.get("skipstart", start))  # type: ignore[arg-type]
        skipstop = float(args.get("skipstop", stop))  # type: ignore[arg-type]
        windows: List[Tuple[float, float]] = []
        for spec in self.step.saves or []:
            tw = list(getattr(spec, "time_window", []) or [])
            for a, b in zip(tw[0::2], tw[1::2]):
                windows.append((float(a), float(b)))
        if outputstart <= start and skipcount <= 1 and not windows:
            return None
        state = {"k": 0}

        def keep(t: float) -> bool:
            if t < outputstart:
                return False
            if windows and not any(a <= t <= b for a, b in windows):
                return False
            if skipcount > 1 and skipstart <= t <= skipstop:
                k = state["k"]
                state["k"] = k + 1
                return k % skipcount == 0
            return True
        return keep

    def point_values(self, g: int) -> Optional[List[object]]:
        """An operating point's values (one row per group); None when the row is missing."""
        for row in self.row_source(g)():
            return [t.make(row) for t in self.traces]
        return None

    def group_status(self, g: int) -> str:
        """ok | failed | interrupted for group g (§2.7, §7.1): missing groups and the points from
        EngineResult.failed_points on are failed; the last present group of an interrupted run is it."""
        k = self.c.er.failed_points.get(self.step.id)
        if k is not None and g >= k - 1:
            return "failed"
        if g >= len(self.groups):
            return "failed"
        if self.status == "interrupted" and g == len(self.groups) - 1:
            return "interrupted"
        if self.status == "failed":
            return "failed" if k is None else "ok"
        return "ok"

    def group_index(self, idx: Tuple[int, ...]) -> int:
        g = 0
        for i, n in zip(idx, self.counts):
            g = g * n + i
        return g

    # -- naming (§8.1, §8.2)
    def leaf_name(self, idx: Tuple[int, ...]) -> str:
        sep = "_" if self.c.legacy else "-"
        parts = ["%s%s%0*d" % (lv.name, sep, _count_width(n), i)
                 for lv, i, n in zip(self.step.context, idx, self.counts)]
        return "_".join(parts + [self.step.name])

    def parent_name(self, depth: int, idx: Tuple[int, ...]) -> str:
        parts = ["%s-%0*d" % (lv.name, _count_width(n), i)
                 for lv, i, n in zip(self.step.context[:depth], idx, self.counts)]
        return "_".join(parts + [self.step.context[depth].name, self.step.name])

    def extension(self) -> str:
        if self.kind == "tran":
            return "tran" if self.c.legacy else "tran.tran"
        return self.atype

    # -- the header table and the description (§8.3)
    def header(self, name: str) -> Dict[str, object]:
        step, opt, args = self.step, self.step.options, self.step.args

        def o(key: str) -> object:
            return opt.get(key, OPTION_DEFAULTS.get(key))

        h: Dict[str, object] = {"tolerance.relative": o("reltol")}
        if self.atype == "dc":
            h.update([("reltol", o("reltol")), ("abstol(V)", o("vabstol")), ("abstol(I)", o("iabstol")),
                      ("temp", o("temp")), ("tnom", o("tnom")), ("tempeffects", o("tempeffects")),
                      ("gmindc", opt.get("gmindc", o("gmin")))])
        elif self.atype in ("ac", "xf"):
            first, last = self.span(args)
            h.update([("start", first), ("stop", last), ("operating point producer", name)])
        elif self.atype == "noise":
            h.update([("operating point producer", name), ("output", "pair of nodes"), ("ground", "0"),
                      ("positive output signal", str(args.get("p", args.get("oprobe", "")))),
                      ("negative output signal", str(args.get("n", "0")))])
        elif self.atype == "tran":
            start = float(args.get("start", 0.0))  # type: ignore[arg-type]
            stop = float(args.get("stop", 0.0))  # type: ignore[arg-type]
            h.update([("start", start), ("outputstart", args.get("outputstart", start)), ("stop", stop),
                      ("step", args.get("step", 1e-3 * (stop - start))), ("istep", ISTEP),
                      ("maxstep", args.get("maxstep", (stop - start) / 50.0)),
                      ("ic", args.get("ic", "all")), ("useprevic", _yesno(args.get("useprevic", "no"))),
                      ("skipdc", _yesno(args.get("skipdc", "no"))),
                      ("reltol", args.get("reltol", o("reltol"))), ("abstol(V)", o("vabstol")),
                      ("abstol(I)", o("iabstol")), ("temp", o("temp")), ("tnom", o("tnom")),
                      ("tempeffects", o("tempeffects")), ("errpreset", args.get("errpreset", "moderate")),
                      ("method", args.get("method", "traponly")), ("lteratio", args.get("lteratio", 3.5)),
                      ("relref", args.get("relref", "sigglobal")), ("cmin", args.get("cmin", 0.0)),
                      ("gmin", o("gmin"))])
        return h

    def span(self, args: Mapping[str, object]) -> Tuple[float, float]:
        """The first and last point of the step's own axis (start/stop, else the grid's ends)."""
        if "start" in args and "stop" in args:
            return float(args["start"]), float(args["stop"])  # type: ignore[arg-type]
        if self.own_values:
            return self.own_values[0], self.own_values[-1]
        return 0.0, 0.0

    def description(self, name: str) -> str:
        title = ANALYSIS_TITLES.get(self.kind, "%s Analysis" % self.kind)
        if self.axis is None:
            return "%s `%s'" % (title, name)
        if self.kind == "tran":
            first, last = float(self.step.args.get("start", 0.0)), float(self.step.args.get("stop", 0.0))  # type: ignore[arg-type]
            var = "time"
        else:
            first, last = self.span(self.step.args)
            var = (self.own.desc or self.own.label) if self.own is not None else self.label
        return "%s `%s': %s = (%s -> %s)" % (title, name, var, si(first, self.units), si(last, self.units))


def _yesno(v: object) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v)


# -- the collector ---------------------------------------------------------------------------------

class _Collector:
    def __init__(self, plan: RunPlan, sigmap: SignalMap, er: EngineResult, naming: str,
                 masters: Optional[Mapping[str, str]], notes: Optional[List[Note]]) -> None:
        self.plan, self.sigmap, self.er = plan, sigmap, er
        self.legacy = naming == "legacy"
        self.masters = dict(masters or {})
        self.notes = notes
        self.reltol = float(plan.options.get("reltol", OPTION_DEFAULTS["reltol"]))  # type: ignore[arg-type]

    def warn(self, origin: str, text: str) -> None:
        if self.notes is not None:
            self.notes.append(warning(origin, text))

    def note(self, origin: str, text: str) -> None:
        if self.notes is not None:
            self.notes.append(note(origin, text))

    def steps(self) -> List[AnalysisStep]:
        return [a.step for a in self.plan.actions if a.op == "analysis" and a.step is not None]

    def run(self) -> Iterator[AnalysisResult]:
        steps = self.steps()
        i = 0
        while i < len(steps):
            st = steps[i]
            if not st.context:
                yield self.unswept(_Step(self, st))
                i += 1
                continue
            # the children of one Spectre sweep: plan.build gives each child its own SweepLevel copy
            # (a fresh vamos_w<k> id per child, §5.2), so a block is the run of consecutive steps under
            # the same sweep name, emitted point-major (§8.2)
            top = st.context[0].name
            block = [_Step(self, st)]
            j = i + 1
            while j < len(steps) and steps[j].context and steps[j].context[0].name == top:
                block.append(_Step(self, steps[j]))
                j += 1
            for s in block:
                s.load()
            if not self.legacy:
                for s in block:
                    yield self.parent(s, 0, ())
            for res in self.points(block, 0, ()):
                yield res
            i = j

    def points(self, block: List[_Step], depth: int, idx: Tuple[int, ...]) -> Iterator[AnalysisResult]:
        """Point-major emission (§8.2): per point of the level at `depth`, each step's leaf, or its
        nested parent followed by that nested sweep's points."""
        n = block[0].counts[depth]
        for i in range(n):
            idx2 = idx + (i,)
            k = 0
            while k < len(block):
                s = block[k]
                if len(s.step.context) == depth + 1:
                    yield self.leaf(s, idx2)
                    k += 1
                    continue
                nested = s.step.context[depth + 1].name          # by the sweep's name, as above
                sub = [s]
                k += 1
                while (k < len(block) and len(block[k].step.context) > depth + 1
                       and block[k].step.context[depth + 1].name == nested):
                    sub.append(block[k])
                    k += 1
                if not self.legacy:
                    for t in sub:
                        yield self.parent(t, depth + 1, idx2)
                for res in self.points(sub, depth + 1, idx2):
                    yield res

    # -- results
    def unswept(self, s: _Step) -> AnalysisResult:
        s.load()
        name = s.step.name
        res = AnalysisResult("%s-%s" % (name, s.atype), s.atype, "%s.%s" % (name, s.extension()),
                             header=s.header(name), description=s.description(name),
                             data_type=DATA_TYPES.get(s.atype, "scalar"))
        self.fill(res, s, 0, s.group_status(0) if s.groups else "failed")
        return res

    def parent(self, s: _Step, depth: int, idx: Tuple[int, ...]) -> AnalysisResult:
        level = s.step.context[depth]
        name = s.parent_name(depth, idx)
        values = list(s.ctx_values[depth])
        # a parent lists the points whose leaves it has (psfcheck: children == points): the complete
        # prefix of a sweep that failed or was interrupted part-way
        if depth == len(s.step.context) - 1:
            keep = 0
            for i in range(len(values)):
                g = s.group_index(idx + (i,))
                if s.group_status(g) == "failed":
                    break
                keep = i + 1
            values = values[:keep]
        res = AnalysisResult("%s-sweep" % name, "sweep", "%s.sweep" % name,
                             parent=("%s-sweep" % s.parent_name(depth - 1, idx[:-1])) if depth else "",
                             tree="sweepNode", sweep=(level.label, level.units, grid_of(level), values),
                             header={"tolerance.relative": self.reltol}, description="Sweep parent",
                             swept=({s.step.context[depth - 1].label: s.ctx_values[depth - 1][idx[-1]]}
                                    if depth else {}))
        return res

    def leaf(self, s: _Step, idx: Tuple[int, ...]) -> AnalysisResult:
        name = s.leaf_name(idx)
        g = s.group_index(idx)
        depth = len(s.step.context)
        res = AnalysisResult("%s-%s" % (name, s.atype), s.atype, "%s.%s" % (name, s.extension()),
                             parent="" if self.legacy else "%s-sweep" % s.parent_name(depth - 1, idx[:-1]),
                             tree="" if self.legacy else "leafNode",
                             header=s.header(name), description=s.description(name),
                             swept={} if self.legacy else {s.step.context[-1].label: s.ctx_values[-1][idx[-1]]},
                             data_type=DATA_TYPES.get(s.atype, "scalar"))
        self.fill(res, s, g, s.group_status(g))
        return res

    def fill(self, res: AnalysisResult, s: _Step, g: int, status: str) -> None:
        """The data of group g into res: an operating point's values, or a sweep's rows."""
        res.status = status
        if status == "failed":
            return
        res.signals = [t.signal for t in s.traces]
        if s.axis is None:                                      # an operating point
            vals = s.point_values(g)
            if vals is None:
                self.warn(s.step.name, "analysis `%s' left no operating-point row" % res.key)
                res.status = "failed"
                return
            res.signals = [Signal(t.signal.name, t.signal.ptype, t.signal.units, [v],
                                  members=None if t.signal.members is None
                                  else [(m, [mv]) for (m, _), mv in zip(t.signal.members, v)])  # type: ignore[arg-type]
                           for t, v in zip(s.traces, vals)]
            return
        res.sweep = (s.label, s.units, grid_of(s.own) if s.axis != "time" else 1, [])
        res.rows = s.xy_rows(g)


def collect(plan: RunPlan, sigmap: SignalMap, er: EngineResult, naming: str,
            masters: Optional[Mapping[str, str]] = None,
            notes: Optional[List[Note]] = None) -> Iterator[AnalysisResult]:
    """The AnalysisResults of a run, in execution order with sweeps point-major (§8.2): per sweep
    block its parents, then per point each child's leaf (or nested parent and leaves).  `naming` is
    "modern" or "legacy" (§8.1; legacy writes no parents).  `masters` maps a noise STRUCT type name
    (a model name) to its master, for the STRUCT PROP and the resistor contribution names (§8.4); a
    type not listed is its own master.  `notes` collects the warnings and notes (a missing column,
    an unreadable output); the flow passes its list.  Failed analyses and sweep points are yielded
    with status "failed" and no data, so the writer skips them and the flow can report them."""
    return _Collector(plan, sigmap, er, naming, masters, notes).run()
