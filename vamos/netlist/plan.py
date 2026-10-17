"""The run plan (docs/VAMOS_SPECTRE_DESIGN.md §5; the API is the §10 block).

Phase 0 (§12) froze the dataclasses; phase 1 (S2) implements build().

plan.build walks Netlist.analyses in netlist order and produces an ordered list of Actions
(§5.1).  The plan speaks IR names only ('.' paths, IR card names, IR parameter names); render
translates them (§4.5 item 5).  Values are evaluated: a float, a str for an enumeration or a
file name, a List[float] for a vector (Setting).  Every Target is a 3-tuple.

What build does (§5, in this order):
  - the global options: Spectre's defaults, the netlist's `options`, the merged settings
    (argv wins) and `+paramdefault`'s `options` lines, with §3.10's dispositions; the first
    action holds the engine keys (temp tnom gmin reltol vabstol iabstol chargeabstol);
  - the plan variables (every top-level parameter a sweep or alter targets) and, transitively,
    RunPlan.dependents (§4.1); one `var` action before the first analysis;
  - per analysis, in netlist order: `set` -> an options action; `alter` -> an alter action with
    §5.2's target rules (renames per SPECTRE_MASTERS, the folded/stripped refusal, the Xyce
    per-path refusal, source fields re-resolved by spectre.resolve_source); `info` -> a note
    action; `sweep` blocks expanded outermost first (one SweepLevel copy per child, with §5.2's
    continuation); op/dc/ac/noise/xf/tran -> an AnalysisStep whose args hold §3.11's names
    evaluated (tran: errpreset applied, §5.4), with the grids of §5.2 folded to engine-ready
    SweepSpecs, the noise input of §5.6, prevoppoint/useprevic stores, the source-state
    switches of §5.3 around every tran on VACASK;
  - the evaluation of every reachable expression on every instance path (tables.path_envs,
    strict), for every parameter state the plan sets (the nominal values, each sweep point,
    each alter), with the ParamTests and the condition/bin rule of §3.7 and §4.3's SPICE bin
    comparison;
  - the run-dependent card rules (ParamRule.warn: analyses=..., temp!=tnom), the sources'
    run-dependent errors (§3.8.1), device ic= on C/L, and the ic/nodeset values (§3.10).

Errors are collected with every other note and raised together as a NoteError at the end;
warnings and notes come back in RunPlan.notes.  DIALECT names the expression dialect of every
evaluation ("spectre", §3.5); S2's unit tests run the same code under "hspice" with numeric
inputs, where the two dialects agree.
"""

from __future__ import annotations

import itertools
import math
from contextlib import contextmanager
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Set, Tuple, Union

from vamos.netlist import expr as X
from vamos.netlist import tables as T
from vamos.netlist.expr_ast import Binary, Expr, Name, Num, Str
from vamos.netlist.ir import (Analysis, Cond, Instance, Model, Netlist, Param, ParamTest, SaveSpec, Source,
                              Subckt, SweepSpec, flat_analyses)
from vamos.notes import ERROR, NOTE, WARNING, Note, NoteError
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
    spec: SweepSpec             # evaluated, engine-ready: mode lin | step | values, dec for an integral span, or
    #                             log with `values` filled (every logarithmic grid vamos computes: log=N, a
    #                             non-integral dec, the default log grid; render prints the values, the PSF
    #                             grid is 3, §8.3).  Invariant after plan.build: every Expr field of it (start,
    #                             stop, step, each element of values) is a folded Num; S3 may rely on it.
    #                             S2's tests pin it
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


# -- the data build() reads ------------------------------------------------------------------------

# The expression dialect of every evaluation (§3.5): the Spectre evaluator.  S2's unit tests set it
# to "hspice" with numeric inputs, where the dialects agree (the Spectre evaluator is S1's).
DIALECT = "spectre"

# §3.10's keys with an engine meaning, as Spectre spells them (§5.1); the options actions carry them
ENGINE_OPTIONS = ("temp", "tnom", "gmin", "reltol", "vabstol", "iabstol", "chargeabstol")
# Spectre's documented defaults [M ref19 p.187], as §7.1's control block prints them; chargeabstol
# (1e-21 C) reaches an action only when set
OPTION_DEFAULTS: Dict[str, float] = {"temp": 27.0, "tnom": 27.0, "reltol": 1e-3, "vabstol": 1e-6,
                                     "iabstol": 1e-12, "gmin": 1e-12}
OUTPUT_OPTIONS = ("rawfmt", "rawfile", "precision")          # §2.2, §2.5, §8.3: output settings
SAVE_OPTIONS = ("save", "nestlvl", "currents", "subcktprobelvl", "pwr", "useprobes", "useterms",
                "saveahdlvars", "subcktiprobes", "savefilter", "saveports", "wfdebug")    # §6.1 (signals.py)
LIMIT_OPTIONS = ("maxwarns", "maxnotes")
# element reduction (§3.10): the default is silent, any other value a warning; None: no silent value
REDUCTION_DEFAULTS: Dict[str, Optional[float]] = {
    "rabsshort": 0.0, "rcut": math.inf, "ccut": math.inf, "cgnd": math.inf, "cmax": math.inf,
    "cclamp": 0.0, "lshort": 0.0, "kcut": 0.0, "dcut": None, "qcut": None}
NOTED_OPTIONS = ("homotopy newton limit maxdeltav gmin_start gmin_converge try_fast_op dcmaxiters pivrel "
                 "pivabs rforce residualtol digits cols checklimitdest sensfile psfversion topcheck compatible "
                 "approx icversion").split()
IGNORED_OPTIONS = ("multithread",)

# errpreset (§5.4): reltol factor, relref, method, maxstep divisor (T/n), lteratio
ERRPRESETS: Dict[str, Tuple[float, str, str, float, float]] = {
    "liberal": (10.0, "sigglobal", "trapgear2", 50.0, 3.5),
    "moderate": (1.0, "sigglobal", "traponly", 50.0, 3.5),
    "conservative": (0.1, "alllocal", "gear2only", 100.0, 10.0),
}
RELTOL_CAP = 0.01
DEFAULT_ERRPRESET = "moderate"
DEFAULT_GRID_COUNT = 50          # no step parameter: lin=50 or log=50 (§5.2)
DEC_TOLERANCE = 1e-9             # relative tolerance of the dec interval count (§5.2)

STEP_KINDS = ("op", "dc", "ac", "noise", "xf", "tran")
FREQ_KINDS = ("ac", "noise", "xf")
# §5.3: order-dependent constructs inside a sweep block
SWEEP_FORBIDDEN = ("prevoppoint", "useprevic", "readns", "readic", "read", "write", "writefinal")
# IR wave -> the Spectre type name a source action carries (§5.3)
SPECTRE_TYPE = {"pulse": "pulse", "pwl": "pwl", "sin": "sine", "exp": "exp"}
SOURCE_V_FIELDS = ("dc", "val0", "val1", "ampl", "sinedc")     # §8.3 units: V on a vsource, A on an isource

_MISSING = object()


# -- small helpers ---------------------------------------------------------------------------------

def _txt(e: object) -> str:
    """An expression for a message, in the dialect's spelling when it prints."""
    if isinstance(e, list):
        return "[%s]" % " ".join(_txt(x) for x in e)
    try:
        return X.to_text(e, DIALECT)                   # type: ignore[arg-type]
    except Exception:
        try:
            return X.to_text(e)                        # type: ignore[arg-type]
        except Exception:
            return repr(e)


def _reads_temp(e: Expr) -> bool:
    return any(isinstance(n, Name) and n.name in ("temper", "$tnom") for n in X.walk(e))


def _tmap(temp: float, tnom: float) -> Dict[str, Expr]:
    return {"temper": Num(temp), "$tnom": Num(tnom)}


def _ev(e: Expr, env: Mapping[str, float], temp: float, tnom: float) -> float:
    """The Spectre value of e on a path (§3.5): temp and tnom put in by expr.substitute."""
    if _reads_temp(e):
        e = X.substitute(e, _tmap(temp, tnom))
    return X.evaluate(e, env, dialect=DIALECT)


def _names(v: object) -> Set[str]:
    """The parameter names an IR value reads (an Expr, a vector of them; nothing else reads any)."""
    out: Set[str] = set()
    for e in _exprs(v):
        try:
            out |= X.names(e)
        except Exception:
            pass
    return out


def _exprs(v: object) -> List[Expr]:
    """The expressions of one IR value (an Expr, a vector, a Str: none; a Python number: none)."""
    if isinstance(v, list):
        out: List[Expr] = []
        for x in v:
            out.extend(_exprs(x))
        return out
    if isinstance(v, Str) or v is None or isinstance(v, (bool, int, float, str)):
        return []
    return [v]                                         # type: ignore[list-item]


def is_infinite_resistor(it: Instance) -> bool:
    """§3.8: a resistor whose r is infinite (spectre.py's Num(inf): no r, rsh or l) is not printed but
    stays in the instance table; its value is a marker, not an expression to evaluate."""
    v = it.value
    return it.kind == "r" and isinstance(v, Num) and math.isinf(v.value) and v.value > 0


def _item_exprs(it: Instance) -> List[Tuple[str, Expr]]:
    """Every expression of an instance with the name it is written under (tables.item_exprs widened
    to vectors and Source.spectre)."""
    out: List[Tuple[str, Expr]] = []
    if it.value is not None and not is_infinite_resistor(it):
        out.extend(("value", e) for e in _exprs(it.value))
    if it.expr is not None:
        out.extend(("expr", e) for e in _exprs(it.expr))
    for k, v in it.params.items():
        out.extend((k, e) for e in _exprs(v))
    s = it.source
    if s is not None:
        if s.dc is not None:
            out.extend(("dc", e) for e in _exprs(s.dc))
        if s.ac is not None:
            out.extend(("mag", e) for e in _exprs(s.ac[0]))
            out.extend(("phase", e) for e in _exprs(s.ac[1]))
        for k, v in s.args.items():
            out.extend((k, e) for e in _exprs(v))
        for t, v in s.points:
            out.extend(("wave", e) for e in _exprs(t) + _exprs(v))
        for k, v in s.spectre.items():
            out.extend((k, e) for e in _exprs(v))
    return out


def _text_of(v: object) -> Optional[str]:
    """The text of an enumeration value (a Str or a str); None for a number."""
    if isinstance(v, Str):
        return v.text
    if isinstance(v, str):
        return v
    return None


def _number_text(text: str) -> Optional[float]:
    """A Spectre number or constant expression written as text ("1", "1/3", "7.02e-4", "1e-5")."""
    try:
        return float(text)
    except ValueError:
        pass
    for dialect in (DIALECT, "hspice"):
        try:
            return X.number(text, dialect=dialect)
        except NotImplementedError:
            continue
        except ValueError:
            break
    for dialect in (DIALECT, "hspice"):
        try:
            return X.evaluate(X.parse(text, dialect=dialect), {}, dialect=dialect)
        except NotImplementedError:
            continue
        except (X.ExprError, X.EvalError, ValueError):
            return None
    return None


def _close(a: float, b: float) -> bool:
    return a == b or abs(a - b) <= 1e-9 * max(abs(a), abs(b))


def _fmt(v: object) -> str:
    if isinstance(v, float):
        return repr(v) if not v.is_integer() or abs(v) >= 1e15 else "%d" % int(v)
    return str(v)


# -- rules of SPECTRE_MASTERS (§3.9, §10) ----------------------------------------------------------

def _when_holds(when: str, params: Mapping[str, object]) -> bool:
    for tok in filter(None, (t.strip() for t in when.split(","))):
        how, _, p = tok.partition(":")
        if how == "absent" and p in params:
            return False
        if how == "given" and p not in params:
            return False
    return True


def _match_holds(match: Sequence[str], value: object, env: Mapping[str, float], temp: float, tnom: float) -> bool:
    """§10's reading: an enumeration value, "absent", "0" or "nonzero", or a Spectre number compared
    numerically; () matches any value."""
    if not match:
        return True
    if value is _MISSING:
        return "absent" in match
    text = _text_of(value)
    if text is not None:
        return any(m.lower() == text.lower() for m in match)
    try:
        exprs = _exprs(value)
        num = _ev(exprs[0], env, temp, tnom) if exprs else float(value)   # type: ignore[arg-type]
    except (X.EvalError, TypeError, ValueError, IndexError):
        return False
    for m in match:
        if m == "absent":
            continue
        if m == "nonzero":
            if num != 0:
                return True
            continue
        mv = _number_text(m)
        if mv is not None and _close(mv, num):
            return True
    return False


def rule_for(rules: Sequence[T.ParamRule], name: str, params: Mapping[str, object],
             env: Optional[Mapping[str, float]] = None, temp: float = 27.0, tnom: float = 27.0
             ) -> Optional[T.ParamRule]:
    """The first rule for `name` whose `when` and `match` hold on the card or instance as written
    (§10); None when no rule of that name applies."""
    value = params.get(name, _MISSING)
    for r in rules:
        if r.name != name:
            continue
        if _when_holds(r.when, params) and _match_holds(r.match, value, env or {}, temp, tnom):
            return r
    return None


def _reason(rule: T.ParamRule) -> str:
    """The rule's reason: the cite text after the page reference."""
    _, sep, rest = rule.cite.partition(";")
    return rest.strip() if sep else rule.cite


# -- temp and tnom in the IR (§3.5) -----------------------------------------------------------------

@dataclass
class TempSites:
    """Where an IR reads temp or tnom through the names tables.path_envs evaluates itself: subckt
    parameters, X-line overrides and conditions.  plan.build and signals.resolve substitute the run's
    temperatures there (expr.substitute) around every path_envs walk and restore the IR after."""
    params: List[Param] = field(default_factory=list)                 # subckt Params, header and body
    xparams: List[Tuple[Instance, str]] = field(default_factory=list)  # (X instance, override name)
    conds: List[Tuple[Cond, int]] = field(default_factory=list)       # (Cond, branch index)

    def __bool__(self) -> bool:
        return bool(self.params or self.xparams or self.conds)


def temp_sites(nl: Netlist) -> TempSites:
    sites = TempSites()
    seen: Set[int] = set()

    def scan(items: Sequence[object]) -> None:
        for it in items:
            if isinstance(it, Cond):
                for i, (c, body) in enumerate(it.branches):
                    if _reads_temp(c):
                        sites.conds.append((it, i))
                    scan(body)
                scan(it.default)
            elif isinstance(it, Instance) and it.kind == "x":
                for k, v in it.params.items():
                    if any(_reads_temp(e) for e in _exprs(v)):
                        sites.xparams.append((it, k))
            elif isinstance(it, Subckt):
                for p in list(it.params) + [p for p in it.body if isinstance(p, Param)]:
                    if id(p) not in seen and _reads_temp(p.expr):
                        seen.add(id(p))
                        sites.params.append(p)
                scan(it.body)
    scan(nl.body)
    return sites


@contextmanager
def substituted(sites: TempSites, temp: float, tnom: float) -> Iterator[None]:
    """The IR with temp and tnom put into the sites, restored afterwards."""
    tm = _tmap(temp, tnom)
    saved: List[Tuple[str, Any, Any]] = []
    try:
        for p in sites.params:
            saved.append(("p", p, p.expr))
            p.expr = X.substitute(p.expr, tm)
        for inst, k in sites.xparams:
            old = inst.params[k]
            saved.append(("x", (inst, k), old))
            inst.params[k] = ([X.substitute(e, tm) for e in old] if isinstance(old, list)
                              else X.substitute(old, tm))
        for cond, i in sites.conds:
            c, body = cond.branches[i]
            saved.append(("c", (cond, i), (c, body)))
            cond.branches[i] = (X.substitute(c, tm), body)
        yield
    finally:
        for kind, where, old in reversed(saved):
            if kind == "p":
                where.expr = old
            elif kind == "x":
                where[0].params[where[1]] = old
            else:
                where[0].branches[where[1]] = old


# -- the evaluation state ---------------------------------------------------------------------------

@dataclass
class _State:
    """One parameter state the plan sets (§3.5, §5.1): the top-level overrides (variables), the
    temperatures, and the instance and model parameter values of dev=/sub=/mod= targets."""
    top: Dict[str, float] = field(default_factory=dict)
    temp: float = 27.0
    tnom: float = 27.0
    inst: Dict[Tuple[int, str], Tuple[Instance, str, float]] = field(default_factory=dict)
    model: Dict[Tuple[int, str], Tuple[Model, str, float]] = field(default_factory=dict)

    def key(self) -> Tuple:
        return (tuple(sorted(self.top.items())), self.temp, self.tnom,
                tuple(sorted((k, v[2]) for k, v in self.inst.items())),
                tuple(sorted((k, v[2]) for k, v in self.model.items())))

    def copy(self) -> "_State":
        return _State(dict(self.top), self.temp, self.tnom, dict(self.inst), dict(self.model))


@dataclass
class _Inst:
    """An instance on one path (nominal values), as the targets and probes name it."""
    path: str                     # the IR path ("x1.r1")
    inst: Instance
    env: T.PathEnv                # the path it sits on (its body's values and scope)


class _Ctx:
    def __init__(self, nl: Netlist, engine: str, settings: Settings):
        self.nl, self.engine, self.settings = nl, engine, settings
        self.notes: List[Note] = []
        self._seen: Set[Tuple[str, str, str]] = set()
        self.plan = RunPlan(engine=engine)
        self.n_a = self.n_w = self.n_s = 0
        self.current: Dict[str, Setting] = {}          # the engine options in force
        self.base = _State()                           # the parameter state alters have set
        self.steps: List[AnalysisStep] = []
        self.checked: Set[Tuple] = set()
        self.nominal_sig: Dict[str, Tuple] = {}
        self.nominal_bin: Dict[Tuple[str, str], str] = {}
        self.bin_warned: Set[Tuple[str, str]] = set()
        self.cards_used: Dict[int, Tuple[Model, str, Mapping[str, float]]] = {}   # id(card) -> (card, a path
        #                                                                             using it, that path's values)
        self.card_evaluated: Set[Tuple[int, int]] = set()
        self.tests_fired: Set[Tuple] = set()
        self.source_state: Dict[str, Tuple[Optional[str], bool, Source]] = {}   # path -> (wave, dc given, Source)
        self.tran_stops: List[float] = []
        self.insts: Dict[str, _Inst] = {}
        self.paths: List[T.PathEnv] = []
        self.path_counts: Dict[str, int] = {}
        self.top_params: List[Param] = []
        self.sites = TempSites()                                # where path_envs would meet temp/tnom
        self.reads_temp = False
        self.dependents: Set[str] = set()
        self.variables: Dict[str, float] = {}

    # -- notes
    def _add(self, sev: str, origin: str, msg: str) -> None:
        key = (sev, origin, msg)
        if key not in self._seen:
            self._seen.add(key)
            self.notes.append(Note(sev, origin, msg))

    def err(self, origin: str, msg: str) -> None:
        self._add(ERROR, origin, msg)

    def warn(self, origin: str, msg: str) -> None:
        self._add(WARNING, origin, msg)

    def note(self, origin: str, msg: str) -> None:
        self._add(NOTE, origin, msg)

    @property
    def errors(self) -> bool:
        return any(n.severity == ERROR for n in self.notes)

    def new_a(self) -> str:
        self.n_a += 1
        return "vamos_a%d" % self.n_a

    def new_w(self) -> str:
        self.n_w += 1
        return "vamos_w%d" % self.n_w

    def new_s(self) -> str:
        self.n_s += 1
        return "vamos_s%d" % self.n_s

    # -- the netlist at nominal values
    def scan(self) -> None:
        """What build needs before the first elaboration: the top-level parameters and the temp sites."""
        self.top_params = [p for p in self.nl.body if isinstance(p, Param)]
        self.sites = temp_sites(self.nl)
        self.reads_temp = bool(self.sites) or any(_reads_temp(p.expr) for p in self.top_params)

    def elaborate(self) -> None:
        """The instance paths at nominal values (the caller substitutes the temperatures around it)."""
        nl = self.nl
        try:
            self.paths = list(T.path_envs(nl, dialect=DIALECT))
        except (X.EvalError, T.TableError) as exc:
            self.err("", "the netlist cannot be elaborated at its nominal values: %s" % exc)
            self.paths = []
        for pe in self.paths:
            for it in pe.items:
                path = it.name if not pe.path else pe.path + "." + it.name
                self.insts.setdefault(path, _Inst(path, it, pe))
        self.path_counts = {s.name: 0 for s in T.all_subckts(nl)}
        for pe in self.paths:
            if pe.subckt is not None:
                self.path_counts[pe.subckt.name] = self.path_counts.get(pe.subckt.name, 0) + 1
        self.reads_temp = self.reads_temp or any(_reads_temp(e) for pe in self.paths for it in pe.items
                                                 for _, e in _item_exprs(it))
        for a in flat_analyses(nl.analyses):
            if a.kind == "tran" and "stop" in a.args:
                try:
                    self.tran_stops.append(_ev(_exprs(a.args["stop"])[0], nl.values, 27.0, 27.0))
                except (X.EvalError, IndexError):
                    pass
        for ent in self.insts.values():
            it = ent.inst
            if it.kind in ("v", "i") and it.source is not None and it.prim != "iprobe":
                src = it.source
                self.source_state[ent.path] = (src.wave, src.dc is not None, src)

    def body_of(self, pe: T.PathEnv) -> List[object]:
        return pe.subckt.body if pe.subckt is not None else self.nl.body

    def paths_reaching(self, name: str) -> List[str]:
        return [pe.path for pe in self.paths if pe.subckt is not None and pe.subckt.name == name]


# -- evaluation on every path, for one state --------------------------------------------------------

@contextmanager
def _applied(ctx: _Ctx, st: _State) -> Iterator[None]:
    """The IR with the state's instance and model values and temperatures put in, restored after."""
    saved: List[Tuple[Dict[str, Any], str, Any]] = []
    try:
        for inst, p, v in st.inst.values():
            saved.append((inst.params, p, inst.params.get(p, _MISSING)))
            inst.params[p] = Num(v)
        for model, p, v in st.model.values():
            saved.append((model.params, p, model.params.get(p, _MISSING)))
            model.params[p] = Num(v)
        with substituted(ctx.sites, st.temp, st.tnom):
            yield
    finally:
        for holder, key, old in reversed(saved):
            if old is _MISSING:
                holder.pop(key, None)
            else:
                holder[key] = old


def _top_values(ctx: _Ctx, st: _State, where: str) -> Dict[str, float]:
    """The top-level parameter values in a state: Netlist.values, the variables' values, and every
    dependent or temperature-reading parameter re-evaluated (§3.5)."""
    vals: Dict[str, float] = dict(ctx.nl.values)
    vals.update(st.top)
    todo = [p for p in ctx.top_params
            if p.name not in st.top and (p.name in ctx.dependents or _reads_temp(p.expr) or p.name not in vals)]
    for _ in range(len(todo) + 1):
        remaining: List[Tuple[Param, X.EvalError]] = []
        progress = False
        for p in todo:
            try:
                vals[p.name] = _ev(p.expr, vals, st.temp, st.tnom)
                progress = True
            except X.EvalError as exc:
                remaining.append((p, exc))
        todo = [p for p, _ in remaining]
        if not todo or not progress:
            break
    for p, exc in remaining:                          # type: ignore[possibly-undefined]
        ctx.err(p.origin, "%s: parameter %s=%s does not evaluate: %s" % (where, p.name, _txt(p.expr), exc))
    return vals


def _walk(ctx: _Ctx, items: Sequence[object], pe: T.PathEnv, st: _State, where: str,
          insts: List[Instance], tests: List[ParamTest], sig: List[int]) -> None:
    """The items of one body on one path in a state: each Cond resolved (the branch index goes to
    sig), the ParamTests of the taken branches collected (the rule tables.path_envs applies, §4.4)."""
    for it in items:
        if isinstance(it, Instance):
            insts.append(it)
        elif isinstance(it, Cond):
            taken: Optional[Sequence[object]] = None
            for i, (c, body) in enumerate(it.branches):
                try:
                    v = _ev(c, pe.env, st.temp, st.tnom)
                except X.EvalError as exc:
                    ctx.err(it.origin, "%s: condition %s is not a number on path %s: %s"
                            % (where, _txt(c), pe.path or "top level", exc))
                    v = 0.0
                if v != 0:
                    taken, sig_i = body, i
                    break
            else:
                taken, sig_i = it.default, -1
            sig.append(sig_i)
            _walk(ctx, taken, pe, st, where, insts, tests, sig)
        elif isinstance(it, ParamTest):
            tests.append(it)


def _bin_rule_bins(bins: Sequence[Model]) -> List[Model]:
    if bins and bins[0].bin_rule == "spectre":
        return sorted(bins, key=lambda m: (m.bin_index is None, m.bin_index or 0, m.name))
    return list(bins)


def _check_bin(ctx: _Ctx, it: Instance, bins: Sequence[Model], pe: T.PathEnv, st: _State, where: str) -> None:
    """§3.7: the bin a model-group instance selects, at every state; §4.3: the HSPICE rule's card
    beside the Spectre rule's where the group carries HSPICE bounds."""
    env = pe.env
    bins = _bin_rule_bins(bins)
    master = bins[0].prim
    geom = dict(T.SPECTRE_MASTERS[master].geometry) if master in T.SPECTRE_MASTERS else {}
    vals: Dict[str, float] = {}
    for k in ("l", "w", "nf"):
        e = it.params.get(k)
        if e is None:
            if k == "nf":
                vals[k] = 1.0
            elif k in geom:
                vals[k] = float(geom[k])
            else:
                ctx.err(it.origin, "%s: instance %s on path %s has no %s for its model group %s"
                        % (where, it.name, pe.path or "top level", k, it.master))
                return
        else:
            try:
                vals[k] = _ev(_exprs(e)[0], env, st.temp, st.tnom)
            except (X.EvalError, IndexError) as exc:
                ctx.err(it.origin, "%s: instance %s on path %s: %s=%s does not evaluate: %s"
                        % (where, it.name, pe.path or "top level", k, _txt(e), exc))
                return
    s = ctx.nl.scale()
    path = pe.path or "top level"
    try:
        if bins[0].bin_rule == "spectre":
            bounds = [(b, T.bin_bounds_spectre(b, env)) for b in bins]
            chosen = T.select_bin_spectre(bounds, vals["l"], vals["w"], s)
            if all(all(k in b.params for k in T.BIN_KEYS) for b in bins):   # §4.3: HSPICE-style x.N bins
                hs = T.select_bin([(b, T.bin_bounds(b, env)) for b in bins], vals["l"], vals["w"], vals["nf"], s)
                if hs is not chosen and (path, it.name) not in ctx.bin_warned:
                    ctx.bin_warned.add((path, it.name))
                    ctx.warn(it.origin, "instance %s on path %s: HSPICE's bin rule selects %s where Spectre's "
                             "selects %s (nf=%s, total w); the Spectre rule's card is used [§4.3, §14 q.8]"
                             % (it.name, path, hs.name if hs else "no card", chosen.name if chosen else "no card",
                                _fmt(vals["nf"])))
        else:
            chosen = T.select_bin([(b, T.bin_bounds(b, env)) for b in bins], vals["l"], vals["w"], vals["nf"], s)
    except T.TableError as exc:
        ctx.err(it.origin, "%s: instance %s on path %s: %s" % (where, it.name, path, exc))
        return
    if chosen is None:
        ctx.err(it.origin, "%s: instance %s on path %s selects no card of model group %s (l=%s w=%s nf=%s)"
                % (where, it.name, path, it.master, _fmt(vals["l"]), _fmt(vals["w"]), _fmt(vals["nf"])))
        return
    key = (path, it.name)
    if key not in ctx.nominal_bin:
        ctx.nominal_bin[key] = chosen.name
    elif ctx.nominal_bin[key] != chosen.name:
        ctx.err(it.origin, "%s changes the bin instance %s on path %s selects (%s, nominal %s): vamos "
                "refuses a sweep or alter that changes a model-group bin (§3.7, §14 q.13)"
                % (where, it.name, path, chosen.name, ctx.nominal_bin[key]))


def _check_instance(ctx: _Ctx, it: Instance, pe: T.PathEnv, st: _State, where: str) -> None:
    path = pe.path or "top level"
    for k, e in _item_exprs(it):
        try:
            _ev(e, pe.env, st.temp, st.tnom)
        except X.EvalError as exc:
            ctx.err(it.origin, "%s: %s=%s of instance %s on path %s does not evaluate: %s"
                    % (where, k, _txt(e), it.name, path, exc))
    if it.kind in T.MODEL_KINDS_OF:
        card, bins = pe.scope.card_of(it)
        for c in ([card] if card is not None else []) + list(bins):
            ctx.cards_used.setdefault(id(c), (c, path, dict(pe.env)))
            ekey = (id(c), id(pe.env))
            if ekey not in ctx.card_evaluated or st.top or st.model:
                ctx.card_evaluated.add(ekey)
                for k, v in c.params.items():
                    for e in _exprs(v):
                        try:
                            _ev(e, pe.env, st.temp, st.tnom)
                        except X.EvalError as exc:
                            ctx.err(c.origin, "%s: %s=%s of model %s (used by %s on path %s) does not "
                                    "evaluate: %s" % (where, k, _txt(e), c.name, it.name, path, exc))
        if bins:
            _check_bin(ctx, it, bins, pe, st, where)


_TEST_SEVERITY = {"errorif": ERROR, "warnif": WARNING, "printif": NOTE}
_PT_SEVERITY = {"debug": NOTE, "status": NOTE, "warning": WARNING, "error": ERROR, "fatal": ERROR}


def _paramtest(ctx: _Ctx, pt: ParamTest, pe: T.PathEnv, st: _State, where: str) -> None:
    """§3.6: a paramtest evaluated on one path in one state; several tests act if any passes."""
    path = pe.path or "top level"
    fired: List[Optional[str]] = []
    for kind, cond in pt.tests:
        try:
            v = _ev(cond, pe.env, st.temp, st.tnom)
        except X.EvalError as exc:
            ctx.err(pt.origin, "%s: paramtest %s: %s=%s does not evaluate on path %s: %s"
                    % (where, pt.name, kind, _txt(cond), path, exc))
            continue
        if v != 0:
            fired.append(kind)
    if not pt.tests:
        fired.append(None)
    for kind in fired:
        if kind is not None:
            sev = _TEST_SEVERITY.get(kind, NOTE)
        else:
            sev = _PT_SEVERITY.get(pt.severity.lower(), NOTE)
        text = pt.message or ("paramtest %s" % pt.name)
        if kind is None and pt.severity.lower() == "fatal":
            text += " (fatal)"
        elif kind is None and pt.severity.lower() == "error":
            text += " (the analysis fails)"
        key = (id(pt), path, sev, text)
        if key in ctx.tests_fired:
            continue
        ctx.tests_fired.add(key)
        ctx._add(sev, pt.origin, "paramtest %s on path %s (%s): %s" % (pt.name, path, where, text))


def _check_state(ctx: _Ctx, st: _State, where: str) -> None:
    """Every reachable expression on every instance path in one state (§3.5), the ParamTests and
    the condition/bin rule (§3.7)."""
    key = st.key()
    if key in ctx.checked:
        return
    ctx.checked.add(key)
    with _applied(ctx, st):
        top = _top_values(ctx, st, where)
        try:
            paths = list(T.path_envs(ctx.nl, values=top, strict=True, dialect=DIALECT))
        except X.EvalError as exc:
            ctx.err("", "%s: %s" % (where, exc))
            return
        except T.TableError as exc:
            ctx.err("", "%s: %s" % (where, exc))
            return
        for pe in paths:
            insts: List[Instance] = []
            tests: List[ParamTest] = []
            sig: List[int] = []
            _walk(ctx, ctx.body_of(pe), pe, st, where, insts, tests, sig)
            sigt = tuple(sig)
            if pe.path not in ctx.nominal_sig:
                ctx.nominal_sig[pe.path] = sigt
            elif ctx.nominal_sig[pe.path] != sigt:
                ctx.err("", "%s changes a structural condition on path %s: vamos refuses a sweep or alter that "
                        "changes an if branch, as Spectre does (§3.7, §14 q.13)" % (where, pe.path or "top level"))
            for it in insts:
                _check_instance(ctx, it, pe, st, where)
            for pt in tests:
                _paramtest(ctx, pt, pe, st, where)


# -- values, grids and levels (§5.2) ---------------------------------------------------------------

def _setting(ctx: _Ctx, v: object, env: Mapping[str, float], st: _State, origin: str, what: str) -> Optional[Setting]:
    """An IR value evaluated to a Setting; None (with an error) when it does not evaluate."""
    if isinstance(v, Str):
        return v.text
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, list):
        out: List[float] = []
        for x in v:
            r = _setting(ctx, x, env, st, origin, what)
            if r is None:
                return None
            if isinstance(r, list):
                out.extend(r)
            elif isinstance(r, str):
                ctx.err(origin, "%s: %s is not a number" % (what, r))
                return None
            else:
                out.append(r)
        return out
    try:
        return float(_ev(v, env, st.temp, st.tnom))   # type: ignore[arg-type]
    except X.EvalError as exc:
        ctx.err(origin, "%s=%s does not evaluate: %s" % (what, _txt(v), exc))
        return None


def _check_dependents(ctx: _Ctx, v: object, origin: str, what: str) -> bool:
    """§3.11, §3.10: a value that reads a plan variable or one of its dependents is an error in v1."""
    hit = _names(v) & ctx.dependents
    if hit:
        ctx.err(origin, "%s=%s reads %s, which a sweep or alter changes: not supported in v1"
                % (what, _txt(v), ", ".join(sorted(hit))))
        return False
    return True


def _num(ctx: _Ctx, e: Optional[Expr], env: Mapping[str, float], st: _State, origin: str, what: str
         ) -> Optional[float]:
    if e is None:
        return None
    r = _setting(ctx, e, env, st, origin, what)
    if isinstance(r, float):
        return r
    if r is not None:
        ctx.err(origin, "%s=%s is not a number" % (what, _txt(e)))
    return None


def grid(ctx: _Ctx, spec: SweepSpec, env: Mapping[str, float], st: _State, origin: str, what: str
         ) -> Optional[SweepSpec]:
    """§5.2: the sweep evaluated and engine-ready: mode lin | step | values, or dec for an integral
    span; every Expr field a folded Num.  None (with an error) when it cannot be built."""
    start = _num(ctx, spec.start, env, st, origin, what + " start")
    stop = _num(ctx, spec.stop, env, st, origin, what + " stop")
    step = _num(ctx, spec.step, env, st, origin, what + " step")
    values: List[float] = []
    for v in spec.values:
        x = _num(ctx, v, env, st, origin, what + " values")
        if x is None:
            return None
        values.append(x)
    mode = spec.mode
    count = spec.count

    def out(mode: str, vals: Optional[List[float]] = None, count: Optional[int] = None) -> SweepSpec:
        return SweepSpec(spec.target, spec.name, spec.param, mode,
                         None if start is None else Num(start), None if stop is None else Num(stop),
                         None if step is None else Num(step), count,
                         [Num(v) for v in (vals or [])], spec.origin)

    if mode == "values" or (mode == "" and values):
        if not values:
            ctx.err(origin, "%s: values=[] is empty" % what)
            return None
        return out("values", values)
    if start is None or stop is None:
        if spec.start is None or spec.stop is None:
            ctx.err(origin, "%s needs start and stop (or values)" % what)
        return None
    if mode == "step":
        if step is None:
            if spec.step is None:
                ctx.err(origin, "%s: step is missing" % what)
            return None
        if step == 0 or (stop - start) * step < 0:
            ctx.err(origin, "%s: step=%s never reaches stop=%s from start=%s" % (what, _fmt(step), _fmt(stop), _fmt(start)))
            return None
        return out("step")
    if mode == "lin":
        if not count or count < 1:
            ctx.err(origin, "%s: lin needs a positive step count" % what)
            return None
        return out("lin", None, count)
    if mode in ("dec", "log"):
        if start <= 0 or stop <= 0:
            ctx.err(origin, "%s: a logarithmic sweep needs start > 0 and stop > 0 (start=%s stop=%s)"
                    % (what, _fmt(start), _fmt(stop)))
            return None
        if not count or count < 1:
            ctx.err(origin, "%s: %s needs a positive count" % (what, mode))
            return None
        if mode == "log":
            n = count
            vals = [start * (stop / start) ** (k / n) for k in range(n)] + [stop]
            return out("log", vals)
        span = count * math.log10(stop / start)
        if abs(span - round(span)) <= DEC_TOLERANCE * max(1.0, abs(span)):
            return out("dec", None, count)
        n = int(math.floor(abs(span) + DEC_TOLERANCE))
        sign = 1.0 if span >= 0 else -1.0
        vals = [start * 10.0 ** (sign * k / count) for k in range(n)] + [stop]
        return out("log", vals)
    if mode == "":
        if start <= 0 or stop <= 0 or stop / start < 10:                   # linear [I, §14 q.11]
            return out("lin", None, DEFAULT_GRID_COUNT)
        if center_span(spec):
            ctx.err(origin, "%s: center/span without a step parameter: stop/start = %s selects a logarithmic "
                    "grid, which center/span cannot describe (§5.2, §14 q.11); give step= or lin="
                    % (what, _fmt(stop / start)))
            return None
        n = DEFAULT_GRID_COUNT
        vals = [start * (stop / start) ** (k / n) for k in range(n)] + [stop]
        return out("log", vals)
    ctx.err(origin, "%s: unknown sweep mode %r" % (what, mode))
    return None


def center_span(spec: SweepSpec) -> bool:
    """A center/span sweep without a step parameter: the parser keeps start = c - s/2 and stop = c + s/2
    unfolded (§5.2), so that the ratio rule's logarithmic choice can be refused here."""
    a, b = spec.start, spec.stop
    return (isinstance(a, Binary) and isinstance(b, Binary) and a.op == "-" and b.op == "+"
            and a.left == b.left and a.right == b.right and isinstance(a.right, Binary)
            and a.right.op == "/" and a.right.right == Num(2.0))


def grid_points(spec: SweepSpec) -> List[float]:
    """The points of an engine-ready SweepSpec (plan.build's invariant: every field a Num)."""
    def v(e: Optional[Expr]) -> float:
        assert isinstance(e, Num)
        return float(e.value)
    if spec.mode in ("values", "log"):                  # log: the values plan.build computed (§5.2, §8.3)
        return [v(e) for e in spec.values]
    start, stop = v(spec.start), v(spec.stop)
    if spec.mode == "lin":
        n = spec.count or 1
        return [start + k * (stop - start) / n for k in range(n)] + [stop]
    if spec.mode == "step":
        step = v(spec.step)
        out: List[float] = []
        k = 0
        while True:
            x = start + k * step
            if (step > 0 and x > stop + 1e-9 * abs(step)) or (step < 0 and x < stop - 1e-9 * abs(step)):
                break
            out.append(x)
            k += 1
        return out
    if spec.mode == "dec":
        n = spec.count or 1
        span = n * math.log10(stop / start)
        m = int(round(abs(span)))
        sign = 1.0 if span >= 0 else -1.0
        return [start * 10.0 ** (sign * k / n) for k in range(m)] + [stop]
    raise ValueError("not an engine-ready sweep: mode %r" % spec.mode)


def _units_of(inst: Instance, p: str) -> str:
    """§8.3's units of a swept instance parameter."""
    if inst.kind == "v" and inst.prim in ("vsource", "") and p in SOURCE_V_FIELDS:
        return "V"
    if inst.kind == "i" and p in SOURCE_V_FIELDS:
        return "A"
    if inst.kind == "r" and p == "r":
        return "Ohm"
    if inst.kind == "c" and p == "c":
        return "F"
    if inst.kind == "l" and p == "l":
        return "H"
    if inst.kind != "x" and p in ("w", "l"):
        return "m"
    return ""


def _labels(ctx: _Ctx, target: Target, parent: bool) -> Tuple[str, str, str]:
    """(label, desc, units) of a level (§5.2, §8.3)."""
    kind, name, p = target
    if kind == "freq":
        return "freq", "freq", "Hz"
    if kind == "option":
        return name, name, "C"
    if kind == "variable":
        return name, name, ""
    full = "%s:%s" % (name, p)
    if kind == "model":
        return full, full, ""
    ent = ctx.insts.get(name)
    units = _units_of(ent.inst, p) if ent is not None else ""
    return (full if parent else p), full, units


# -- targets (§5.2, §5.3) ---------------------------------------------------------------------------

@dataclass
class _Resolved:
    target: Target
    inst: Optional[_Inst] = None          # dev=/sub=
    cards: List[Model] = field(default_factory=list)   # mod=
    source_field: bool = False
    group: bool = False
    subckt: Optional[Subckt] = None        # sub=


def _source_fields(prim: str) -> Set[str]:
    row = T.SPECTRE_MASTERS.get(prim)
    return {r.name for r in row.instance} if row is not None else set()


_VALUE_KEY = {"resistor": "r", "capacitor": "c", "inductor": "l"}


def _bound_expr(it: Instance, param: str) -> Optional[Expr]:
    """The expression an instance parameter is bound to: Instance.value for a resistor's r, a capacitor's
    c and an inductor's l (spectre.rcl_value moves them there), else the params entry."""
    if _VALUE_KEY.get(it.prim or "") == param:
        return it.value
    v = it.params.get(param)
    return v if isinstance(v, (Num, Name, Binary)) or (v is not None and not isinstance(v, (Str, list))) else None


def _vacask_bound(ctx: _Ctx, ent: _Inst, param: str, origin: str, what: str) -> bool:
    """VACASK cannot sweep an instance parameter bound to an expression that reads a parameter
    ("parameter 'r' of instance 'X2:R1' is bound to an expression. Failed to bind sweep parameters.",
    rc 0, no rawfile), while an alter of the same parameter works (alter instance("X2:R1") r=2000
    moved the node) [E, phase 1b]; Xyce sweeps it natively (.DC X2:R1:R, §7.2)."""
    if ctx.engine != "vacask":
        return True
    e = _bound_expr(ent.inst, param)
    names = _names(e) - {"temper", "$tnom"} if e is not None else set()
    if not names:
        return True
    ctx.err(origin, "%s: on VACASK %s of %s is bound to the expression %s, which reads %s: VACASK cannot sweep "
            "such a parameter ('is bound to an expression'); sweep what it reads instead (sub= for a "
            "subckt parameter, param= for a netlist parameter) [E, phase 1b]"
            % (what, param, ent.path, _txt(e), ", ".join(sorted(names))))
    return False


def _multipath(ctx: _Ctx, ent: _Inst, origin: str, what: str) -> bool:
    """§5.3: on Xyce a target inside a subckt body that more than one instance path reaches."""
    if ctx.engine != "xyce" or ent.env.subckt is None:
        return True
    paths = ctx.paths_reaching(ent.env.subckt.name)
    if len(paths) > 1:
        ctx.err(origin, "%s: on Xyce %s lies in subckt %s, which %d instance paths reach (%s): changing the "
                "shared definition would change every path; not supported in v1 (§5.3)"
                % (what, ent.path, ent.env.subckt.name, len(paths), ", ".join(paths)))
        return False
    return True


def _resolve_target(ctx: _Ctx, kind: str, name: str, param: str, origin: str, what: str,
                    sweep: bool) -> Optional[_Resolved]:
    nl = ctx.nl
    if kind == "freq":
        return _Resolved(("freq", "", ""))
    if kind == "param":
        if param in ("temp", "tnom"):
            if param == "tnom" and ctx.engine == "xyce" and sweep:
                ctx.err(origin, "%s: a sweep of tnom is an error on Xyce, which has no tnom expression variable (§3.5)"
                        % what)
                return None
            return _Resolved(("option", param, ""))
        if not any(p.name == param for p in ctx.top_params):
            ctx.err(origin, "%s: %s is not a top-level parameter (param= sweeps and alters take a netlist "
                    "parameter, temp or tnom)" % (what, param))
            return None
        return _Resolved(("variable", param, ""))
    if kind == "dev":
        ent = ctx.insts.get(name)
        if ent is None:
            ctx.err(origin, "%s: no instance %s on any path (dev=)" % (what, name))
            return None
        it = ent.inst
        if it.kind in ("v", "i") and it.prim != "iprobe" and param != "m":
            fields = _source_fields(it.prim or ("vsource" if it.kind == "v" else "isource"))
            if fields and param not in fields:
                ctx.err(origin, "%s: %s is not a parameter of a %s (§3.8.1)" % (what, param, it.prim or it.kind))
                return None
            if not _multipath(ctx, ent, origin, what):
                return None
            return _Resolved(("instance", ent.path, param), ent, source_field=True)
        if param in it.folded:
            ctx.err(origin, "%s: %s of %s fed a value vamos computed at parse time (Instance.folded): a sweep or "
                    "alter of it would change nothing the engines print; not supported in v1 (§5.2)"
                    % (what, param, ent.path))
            return None
        p2 = param
        row = T.SPECTRE_MASTERS.get(it.prim) if it.kind not in ("x", "y") else None
        if row is not None:
            rule = rule_for(row.instance, param, it.params, ent.env.env)
            if rule is None:
                ctx.err(origin, "%s: %s is not an instance parameter of %s (master %s)" % (what, param, ent.path, it.prim))
                return None
            if rule.action in ("fold", "strip"):
                ctx.err(origin, "%s: %s of %s is %s by vamos (%s): a sweep or alter of it would change nothing "
                        "the engines print; not supported in v1 (§5.2)"
                        % (what, param, ent.path, "folded" if rule.action == "fold" else "stripped", _reason(rule)))
                return None
            if rule.action == "rename":
                p2 = rule.value
        if not sweep and not _multipath(ctx, ent, origin, what):
            return None
        if sweep and not _vacask_bound(ctx, ent, param, origin, what):
            return None
        return _Resolved(("instance", ent.path, p2), ent)
    if kind == "mod":
        models = nl.models()
        cards: List[Model] = []
        group = False
        if name in models:
            cards = [models[name]]
        else:
            cards = [m for m in models.values() if m.base == name]
            group = bool(cards)
        if not cards:
            ctx.err(origin, "%s: no model %s (mod=)" % (what, name))
            return None
        if group and sweep:
            ctx.err(origin, "%s: %s is a model group; a VACASK sweep names one model, so a mod= sweep of a group "
                    "is an error in v1 (§5.2)" % (what, name))
            return None
        p2 = param
        for card in cards:
            row = T.SPECTRE_MASTERS.get(card.prim)
            if row is None:
                continue
            rule = rule_for(row.params, param, card.params, nl.values)
            if rule is None:
                ctx.err(origin, "%s: %s is not a parameter of model %s (master %s)" % (what, param, card.name, card.prim))
                return None
            if rule.action in ("fold", "strip"):
                ctx.err(origin, "%s: %s of model %s is %s by vamos (%s): a sweep or alter of it would change "
                        "nothing the engines print; not supported in v1 (§5.2)"
                        % (what, param, card.name, "folded" if rule.action == "fold" else "stripped", _reason(rule)))
                return None
            if rule.action == "rename":
                p2 = rule.value
        return _Resolved(("model", name, p2), cards=cards, group=group)
    if kind == "sub":
        ent = ctx.insts.get(name)
        if ent is None or ent.inst.kind != "x":
            ctx.err(origin, "%s: %s is not a subckt instance on any path (sub=)" % (what, name))
            return None
        sub = ent.env.scope.find_subckt(ent.inst.master or "")
        if sub is None:
            ctx.err(origin, "%s: subckt %s of %s is not defined" % (what, ent.inst.master, name))
            return None
        names = {p.name for p in sub.params} | {p.name for p in sub.body if isinstance(p, Param)}
        if param not in names:
            ctx.err(origin, "%s: %s is not a parameter of subckt %s (instance %s)" % (what, param, sub.name, name))
            return None
        if not _multipath(ctx, ent, origin, what):
            return None
        ctx.plan.overridden.setdefault(sub.name, set()).add(param)
        return _Resolved(("instance", ent.path, param), ent, subckt=sub)
    ctx.err(origin, "%s: unknown target kind %r" % (what, kind))
    return None


def _affects(ctx: _Ctx, r: _Resolved, param: str) -> bool:
    """Whether a value of this target can change what the plan checks: branches, bins, expressions."""
    kind = r.target[0]
    if kind == "variable":
        return True
    if kind == "option":
        return ctx.reads_temp
    if kind == "model":
        return param in T.BIN_KEYS
    if kind == "instance":
        if r.source_field:
            return False
        if r.subckt is not None:
            return True
        return r.inst is not None and r.inst.inst.kind == "m" and param in ("w", "l", "nf")
    return False


def _apply(st: _State, r: _Resolved, param: str, value: float) -> _State:
    """The state with one target set to a value."""
    kind, name, _ = r.target
    st = st.copy()
    if kind == "variable":
        st.top[name] = value
    elif kind == "option":
        if name == "temp":
            st.temp = value
        else:
            st.tnom = value
    elif kind == "model":
        for c in r.cards:
            st.model[(id(c), param)] = (c, param, value)
    elif kind == "instance" and r.inst is not None and not r.source_field:
        st.inst[(id(r.inst.inst), param)] = (r.inst.inst, param, value)
    return st


# -- options (§3.10) ---------------------------------------------------------------------------------

def _opt_value(ctx: _Ctx, key: str, v: object) -> Optional[Setting]:
    env = ctx.nl.values
    return _setting(ctx, v, env, ctx.base, "", "option %s" % key)


def _option_dispositions(ctx: _Ctx, items: Mapping[str, object], origin: str, where: str
                         ) -> Dict[str, Setting]:
    """§3.10's table over a set of option assignments; returns the engine keys that are set."""
    out: Dict[str, Setting] = {}
    for key, raw in items.items():
        v = _opt_value(ctx, key, raw)
        if v is None:
            continue
        if key in ENGINE_OPTIONS:
            if not isinstance(v, float):
                ctx.err(origin, "%s: %s=%s is not a number" % (where, key, v))
                continue
            out[key] = v
        elif key == "scale":
            if v != 1.0:
                ctx.warn(origin, "%s: scale=%s is applied by the IR's scale rule (HSPICE's); Spectre's own scaling "
                         "may differ [I, §14 q.43]" % (where, _fmt(v)))
        elif key == "scalem":
            if v != 1.0:
                ctx.err(origin, "%s: scalem=%s: model scaling is not supported" % (where, _fmt(v)))
        elif key in OUTPUT_OPTIONS or key in SAVE_OPTIONS or key in LIMIT_OPTIONS:
            continue
        elif key == "tempeffects":
            if str(v).lower() != "all":
                ctx.err(origin, "%s: tempeffects=%s: only tempeffects=all is supported" % (where, v))
        elif key in REDUCTION_DEFAULTS:
            default = REDUCTION_DEFAULTS[key]
            if default is None or not isinstance(v, float) or v != default:
                ctx.warn(origin, "%s: %s=%s is not applied: element reduction is not supported" % (where, key, _fmt(v)))
        elif key in NOTED_OPTIONS:
            ctx.note(origin, "%s: %s=%s has no effect here" % (where, key, _fmt(v)))
        elif key in IGNORED_OPTIONS:
            continue
        else:
            ctx.warn(origin, "%s: option %s=%s is not applied" % (where, key, _fmt(v)))
    return out


def _global_options(ctx: _Ctx) -> Dict[str, Setting]:
    """The whole global set of the first options action (§2.2, §3.10, §5.1)."""
    nl, settings = ctx.nl, ctx.settings
    eng: Dict[str, Setting] = dict(OPTION_DEFAULTS)
    for key, v in (("temp", nl.temp), ("tnom", nl.tnom)):
        if v is not None:
            s = _setting(ctx, v, nl.values, ctx.base, "", key)
            if isinstance(s, float):
                eng[key] = s
    merged: Dict[str, object] = {}
    for prim, param, value in settings.paramdefault:
        if prim == "options" and param not in nl.options and param not in settings.options:
            merged[param] = _paramdefault_value(value)
    merged.update(nl.options)
    merged.update(settings.options)
    eng.update(_option_dispositions(ctx, merged, "", "options"))
    return eng


def _paramdefault_value(text: str) -> object:
    n = _number_text(text)
    return Str(text) if n is None else Num(n)


# -- the analyses ---------------------------------------------------------------------------------------

def _step_env(ctx: _Ctx) -> Dict[str, float]:
    return _top_values(ctx, ctx.base, "the current parameter state")


def _enum(args: Mapping[str, Setting], key: str, default: str = "") -> str:
    v = args.get(key)
    return v.lower() if isinstance(v, str) else default


def _set_statement(ctx: _Ctx, a: Analysis, in_sweep: bool) -> None:
    if in_sweep:
        ctx.err(a.origin, "set %s inside a sweep block: its effect would depend on the point order (§5.3)" % a.name)
        return
    changed = _option_dispositions(ctx, a.args, a.origin, "set %s" % a.name)
    for key in a.args:
        if key in SAVE_OPTIONS or key in OUTPUT_OPTIONS:
            ctx.warn(a.origin, "set %s: %s between analyses is not applied; the netlist's options statement decides "
                     "it for the whole run" % (a.name, key))
    for key, v in changed.items():
        ctx.current[key] = v
        if key == "temp":
            ctx.base.temp = float(v)                        # type: ignore[arg-type]
        elif key == "tnom":
            ctx.base.tnom = float(v)                        # type: ignore[arg-type]
    ctx.plan.actions.append(Action("options", dict(changed), None, a.origin))
    if changed:
        _check_state(ctx, ctx.base, "set %s" % a.name)


def _alter(ctx: _Ctx, a: Analysis, in_sweep: bool) -> None:
    if in_sweep:
        ctx.err(a.origin, "alter %s inside a sweep block: its effect would depend on the point order (§5.3)" % a.name)
        return
    what = "alter %s" % a.name
    args = a.args
    kind = next((k for k in ("dev", "mod", "sub") if k in args), "param")
    name = _text_of(args.get(kind)) or "" if kind != "param" else ""
    param = _text_of(args.get("param")) or ""
    if not param:
        ctx.err(a.origin, "%s: param= is missing" % what)
        return
    if "value" not in args:
        ctx.err(a.origin, "%s: value= is missing" % what)
        return
    r = _resolve_target(ctx, kind, name, param, a.origin, what, sweep=False)
    if r is None:
        return
    env = _step_env(ctx)
    value = _setting(ctx, args["value"], env, ctx.base, a.origin, what + " value")
    if value is None:
        return
    if not _check_dependents(ctx, args["value"], a.origin, what + " value"):
        return
    act: Dict[str, object] = {"target": r.target, "value": value}
    tk, tname, tparam = r.target
    if r.source_field and r.inst is not None:
        src = r.inst.inst.source
        wave, dc_given, cur = ctx.source_state.get(r.inst.path, (src.wave if src else None, bool(src and src.dc is not None),
                                                                  src or Source()))
        params: Dict[str, object] = dict(cur.spectre)
        if isinstance(value, list):
            params[param] = [Num(v) for v in value]
        elif isinstance(value, str):
            params[param] = Str(value)
        else:
            params[param] = Num(value)
        type_text = _text_of(params.get("type")) or ("dc" if wave is None else SPECTRE_TYPE.get(wave, wave))
        if param == "type":
            type_text = str(value)
        from vamos.netlist import spectre as SP
        notes: List[Note] = []
        new = SP.resolve_source(params, type_text, ctx.tran_stops, notes)
        ctx.notes.extend(notes)
        new_wave = new.wave
        new_given = new.dc is not None
        ctx.source_state[r.inst.path] = (new_wave, new_given, new)
        act["source"] = new
    elif tk == "option":
        if not isinstance(value, float):
            ctx.err(a.origin, "%s: %s=%s is not a number" % (what, param, value))
            return
        ctx.current[tname] = value
        ctx.base = _apply(ctx.base, r, param, value)
    elif tk == "variable":
        if not isinstance(value, float):
            ctx.err(a.origin, "%s: %s=%s is not a number" % (what, param, value))
            return
        ctx.base = _apply(ctx.base, r, param, value)
    elif isinstance(value, float) and _affects(ctx, r, param):
        ctx.base = _apply(ctx.base, r, param, value)
    ctx.plan.actions.append(Action("alter", act, None, a.origin))
    _check_state(ctx, ctx.base, what)


def _level(ctx: _Ctx, spec: SweepSpec, name: str, origin: str, what: str, parent: bool,
           continuation: int) -> Optional[Tuple[SweepLevel, _Resolved]]:
    r = _resolve_target(ctx, spec.target, spec.name, spec.param, origin, what, sweep=True)
    if r is None:
        return None
    env = _step_env(ctx)
    for e in [spec.start, spec.stop, spec.step] + list(spec.values):
        if e is not None and not _check_dependents(ctx, e, origin, what):
            return None
    g = grid(ctx, spec, env, ctx.base, origin, what)
    if g is None:
        return None
    label, desc, units = _labels(ctx, r.target, parent)
    return SweepLevel(name, "", r.target, g, continuation, label, desc, units), r


def _sweep_block(ctx: _Ctx, a: Analysis, context: List[Tuple[SweepLevel, _Resolved]]) -> None:
    what = "sweep %s" % a.name
    if a.sweep is None:
        ctx.err(a.origin, "%s: no swept variable" % what)
        return
    lvl = _level(ctx, a.sweep, a.name, a.origin, what, parent=True, continuation=0)
    for key in a.args:
        if key in ("paramset",) or key.startswith("faults"):
            ctx.err(a.origin, "%s: %s is not supported" % (what, key))
    if lvl is None:
        return
    for child in a.children:
        _analysis(ctx, child, context + [lvl], in_sweep=True)


def _analysis(ctx: _Ctx, a: Analysis, context: List[Tuple[SweepLevel, _Resolved]], in_sweep: bool) -> None:
    kind = a.kind
    if kind == "set":
        _set_statement(ctx, a, in_sweep)
    elif kind == "alter":
        _alter(ctx, a, in_sweep)
    elif kind == "info":
        n = Note(NOTE, a.origin, "info %s: the information file is not written (v1)" % a.name)
        ctx.plan.actions.append(Action("note", {"note": n}, None, a.origin))
    elif kind == "montecarlo":
        ctx.err(a.origin, "montecarlo %s is not supported in v1 (§3.12; phase 3)" % a.name)
    elif kind == "altergroup":
        ctx.err(a.origin, "altergroup %s is not supported in v1 (§3.6)" % a.name)
    elif kind == "sweep":
        _sweep_block(ctx, a, context)
    elif kind in STEP_KINDS:
        _step(ctx, a, context, in_sweep)
    else:
        ctx.err(a.origin, "analysis %s: unknown kind %r" % (a.name, kind))


def _args(ctx: _Ctx, a: Analysis, env: Mapping[str, float], what: str) -> Dict[str, Setting]:
    args: Dict[str, Setting] = {}
    for key, raw in a.args.items():
        if not _check_dependents(ctx, raw, a.origin, "%s %s" % (what, key)):
            continue
        v = _setting(ctx, raw, env, ctx.base, a.origin, "%s %s" % (what, key))
        if v is not None:
            args[key] = v
    for prim, param, value in ctx.settings.paramdefault:
        if prim == a.kind and param not in args and param not in a.args:
            v = _setting(ctx, _paramdefault_value(value), env, ctx.base, a.origin, "%s %s (+paramdefault)" % (what, param))
            if v is not None:
                args[param] = v
    return args


def _common_dispositions(ctx: _Ctx, a: Analysis, step: AnalysisStep, args: Dict[str, Setting], in_sweep: bool) -> None:
    """The dc family's dispositions (§3.11), shared by every step kind that solves an operating point."""
    what = "%s %s" % (a.kind, a.name)
    o = a.origin
    if in_sweep:
        for key in SWEEP_FORBIDDEN:
            if key in args and not (key in ("prevoppoint", "useprevic") and _enum(args, key) == "no"):
                ctx.err(o, "%s: %s inside a sweep block depends on the point order; not supported (§5.3)" % (what, key))
    if "write" in args or "writefinal" in args:
        step.full_solution = True
    if _enum(args, "oppoint", "no") != "no":
        ctx.note(o, "%s: oppoint=%s: operating-point information is not written" % (what, args["oppoint"]))
    for key in ("force", "readforce"):
        if key in args and _enum(args, key, "none") != "none":
            ctx.err(o, "%s: %s=%s is not supported" % (what, key, args[key]))
    for key in ("homotopy", "newton", "maxsteps", "swp1stpointic"):
        if key in args:
            ctx.note(o, "%s: %s=%s has no effect here" % (what, key, _fmt(args[key])))
    if _enum(args, "hysteresis") == "yes":
        ctx.err(o, "%s: hysteresis=yes is not supported" % what)
    if "useprevic" in args and _enum(args, "useprevic") != "no":
        _use_previous(ctx, step, o, what, "useprevic=%s" % args["useprevic"])
    if a.kind in FREQ_KINDS:
        if _enum(args, "prevoppoint") == "yes":
            _use_previous(ctx, step, o, what, "prevoppoint=yes")
        if _enum(args, "skipdc") == "yes":
            ctx.err(o, "%s: skipdc=yes is not supported for %s" % (what, a.kind))
        if _enum(args, "perturbation", "linear") != "linear":
            ctx.err(o, "%s: perturbation=%s is not supported" % (what, args["perturbation"]))
        for key in list(args):
            if key in ("out1", "out2", "contriblist", "flin_out", "fim_out", "maxharm_nonlin") or key.startswith("rf"):
                ctx.err(o, "%s: %s belongs to a nonlinear perturbation analysis; not supported" % (what, key))


def _use_previous(ctx: _Ctx, step: AnalysisStep, origin: str, what: str, which: str) -> None:
    """§5.4: prevoppoint / useprevic on VACASK store the previous analysis's solution; on Xyce a warning."""
    if ctx.engine == "xyce":
        ctx.warn(origin, "%s: %s: Xyce recomputes the operating point (§0)" % (what, which))
        return
    prev = ctx.steps[-1] if ctx.steps else None
    if prev is None:
        ctx.warn(origin, "%s: %s: no previous analysis, a fresh operating point is used" % (what, which))
        return
    if prev.stores is None:
        prev.stores = ctx.new_s()
    step.uses = prev.stores


def _tran_dispositions(ctx: _Ctx, a: Analysis, step: AnalysisStep, args: Dict[str, Setting]) -> None:
    what = "tran %s" % a.name
    o = a.origin
    if "stop" not in args:
        ctx.err(o, "%s: stop is required" % what)
        return
    stop = args["stop"]
    if not isinstance(stop, float):
        ctx.err(o, "%s: stop=%s is not a number" % (what, stop))
        return
    start = args.get("start", 0.0)
    if isinstance(start, float) and start != 0.0:
        ctx.err(o, "%s: start=%s: only start=0 is supported (§14 q.24)" % (what, _fmt(start)))
        return
    if "step" not in args:                      # Spectre's default step = 0.001 (stop - start) [ref19]; §7.1 and
        args["step"] = (stop - float(start)) / 1000.0     # §7.2 print it (5e-06 for stop=5m), Xyce's .TRAN needs it
    if "minstep" in args:
        ctx.note(o, "%s: minstep=%s has no effect here" % (what, _fmt(args["minstep"])))
    for key in ("tpoints", "readtime"):
        if key in args:
            ctx.err(o, "%s: %s is not supported" % (what, key))
    if _enum(args, "linearic") == "yes":
        ctx.err(o, "%s: linearic=yes is not supported" % what)
    for key in list(args):
        if key.startswith("rampup"):
            ctx.err(o, "%s: %s is not supported" % (what, key))
        elif key.startswith("strobe"):
            ctx.warn(o, "%s: %s is not applied: strobed output is not supported" % (what, key))
        elif key in ("infonames", "infotimes", "infotime_pair", "acnames", "actimes", "actime_pair"):
            ctx.warn(o, "%s: %s: the embedded analyses are not run" % (what, key))
        elif key in ("ckptperiod", "saveperiod", "saveclock", "savetime", "savefile", "recover"):
            ctx.note(o, "%s: %s has no effect here (no checkpoints)" % (what, key))
        elif key in ("maxstepratio", "reltolratio"):
            ctx.err(o, "%s: %s defines a custom errpreset; not supported" % (what, key))
        elif key in ("param", "paramset", "param_vec", "param_file", "sub"):
            ctx.err(o, "%s: %s (a transient parameter sweep) is not supported" % (what, key))
    cmin = args.get("cmin", 0.0)
    if isinstance(cmin, float) and cmin != 0.0:
        ctx.warn(o, "%s: cmin=%s is not applied" % (what, _fmt(cmin)))
    age = args.get("circuitage", 0.0)
    if isinstance(age, float) and age != 0.0:
        ctx.err(o, "%s: circuitage=%s is not supported" % (what, _fmt(age)))
    nf = args.get("noisefmax", 0.0)
    if isinstance(nf, float) and nf > 0:
        ctx.err(o, "%s: noisefmax=%s: transient noise is not supported in v1" % (what, _fmt(nf)))
    # §5.4 errpreset
    preset = (ctx.settings.aps or _text_of(args.get("errpreset")) or ctx.settings.errpreset or DEFAULT_ERRPRESET).lower()
    if preset not in ERRPRESETS:
        ctx.err(o, "%s: errpreset=%s is not one of %s" % (what, preset, ", ".join(ERRPRESETS)))
        return
    factor, relref, method, div, lteratio = ERRPRESETS[preset]
    base_reltol = ctx.current.get("reltol", OPTION_DEFAULTS["reltol"])
    args["errpreset"] = preset
    args["reltol"] = min(float(base_reltol) * factor, RELTOL_CAP)      # type: ignore[arg-type]
    args.setdefault("relref", relref)
    args.setdefault("method", method)
    args.setdefault("lteratio", lteratio)
    bound = (stop - float(start)) / div                               # type: ignore[arg-type]
    user = args.get("maxstep")
    args["maxstep"] = min(user, bound) if isinstance(user, float) else bound
    m = _enum(args, "method")
    if ctx.engine == "vacask" and m in ("trapgear2", "trapeuler"):
        ctx.note(o, "%s: method=%s runs as VACASK's trap (§5.4)" % (what, m))
    if ctx.engine == "xyce":
        ctx.note(o, "%s: Xyce takes the method (%s) only; reltol=%s relref=%s lteratio=%s are Spectre's "
                 "settings, not Xyce's (§5.4)" % (what, m, _fmt(args["reltol"]), args["relref"], _fmt(args["lteratio"])))
    ic = _enum(args, "ic", "all")
    if ic not in ("all", "node", "dev", "dc"):
        ctx.err(o, "%s: ic=%s is not one of all, node, dev, dc" % (what, ic))


def _two_terminal(ent: _Inst) -> bool:
    return ent.inst.kind in ("r", "c", "l", "i", "d") and len(ent.inst.nodes) == 2


def _node_path(ctx: _Ctx, at: str, local: str) -> str:
    """The IR path of a net named `local` in the body instantiated at path `at` ("" = top): a port
    follows the X line's binding upwards, a global or ground stays, anything else is prefixed."""
    if local == "0" or local in ctx.nl.globals or at == "":
        return local
    xent = ctx.insts.get(at)
    sub = None
    if xent is not None:
        sub = next((s for s in T.all_subckts(ctx.nl) if s.name == xent.inst.master), None)
    if xent is not None and sub is not None and local in sub.ports:
        i = sub.ports.index(local)
        if i < len(xent.inst.nodes):
            return _node_path(ctx, at.rpartition(".")[0], xent.inst.nodes[i])
    return at + "." + local


def _output_pair(ctx: _Ctx, a: Analysis, args: Dict[str, Setting], ent: Optional[_Inst]) -> None:
    """AnalysisStep.args["p"], ["n"] (§3.11 `p n`; §5.1 the step's settings; §8.3's header pair): the
    analysis's node pair as IR node paths, n = ground when one node is given, or the two terminals of
    oprobe=/probe= (the output port of a controlled source)."""
    if ent is not None:
        if len(ent.inst.nodes) < 2:
            return
        at = ent.path.rpartition(".")[0]
        args["p"] = _node_path(ctx, at, ent.inst.nodes[0])
        args["n"] = _node_path(ctx, at, ent.inst.nodes[1])
    elif a.nodes:
        args["p"] = a.nodes[0]
        args["n"] = a.nodes[1] if len(a.nodes) > 1 else "0"


def _noise_dispositions(ctx: _Ctx, a: Analysis, step: AnalysisStep, args: Dict[str, Setting]) -> None:
    what = "noise %s" % a.name
    o = a.origin
    oprobe = _text_of(args.get("oprobe"))
    if oprobe:
        ent = ctx.insts.get(oprobe)
        if ent is None:
            ctx.err(o, "%s: oprobe=%s names no instance" % (what, oprobe))
        elif ent.inst.kind == "v":
            ctx.err(o, "%s: oprobe=%s is a %s: a current output is not supported" % (what, oprobe, ent.inst.prim or "vsource"))
        elif ent.inst.kind in ("e", "g", "h", "f"):
            port = args.get("oportv", 1.0)
            if port != 1.0:
                ctx.err(o, "%s: oportv=%s: only the output port (1) of a controlled source is supported" % (what, _fmt(port)))
            else:
                _output_pair(ctx, a, args, ent)
        elif not _two_terminal(ent):
            ctx.err(o, "%s: oprobe=%s is not a two-terminal component or a controlled source" % (what, oprobe))
        else:
            _output_pair(ctx, a, args, ent)
    elif len(a.nodes) not in (1, 2):
        ctx.err(o, "%s: an output node pair (p n) or oprobe= is required" % what)
    else:
        _output_pair(ctx, a, args, None)
    iprobe = _text_of(args.get("iprobe"))
    if iprobe:
        ent = ctx.insts.get(iprobe)
        if ent is None:
            ctx.err(o, "%s: iprobe=%s names no instance" % (what, iprobe))
        elif ent.inst.prim == "port" or ent.inst.kind not in ("v", "i") or ent.inst.prim == "iprobe":
            ctx.err(o, "%s: iprobe=%s must be a vsource or isource (a %s is not supported)"
                    % (what, iprobe, ent.inst.prim or ent.inst.kind))
        elif _multipath(ctx, ent, o, what):     # §5.3: on Xyce the AC 1 0 goes into the subckt body, one path only
            step.noise_input = ent.path
        iport = args.get("iportv", 1.0)
        if iport != 1.0:
            ctx.err(o, "%s: iportv=%s: only port 1 is supported" % (what, _fmt(iport)))
    else:
        step.noise_input = "vamos_nin%d" % ctx.n_a                   # §5.6
    if "separatenoise" in args:
        ctx.note(o, "%s: separatenoise=%s has no effect here" % (what, args["separatenoise"]))


def _xf_dispositions(ctx: _Ctx, a: Analysis, step: AnalysisStep, args: Dict[str, Setting]) -> None:
    what = "xf %s" % a.name
    o = a.origin
    if ctx.engine == "xyce":
        ctx.err(o, "%s: xf is not available on Xyce (§3.11); use --vamos-analog=vacask" % what)
    probe = _text_of(args.get("probe"))
    if probe:
        ent = ctx.insts.get(probe)
        if ent is None:
            ctx.err(o, "%s: probe=%s names no instance" % (what, probe))
        elif ent.inst.kind == "v":
            ctx.err(o, "%s: probe=%s is a current probe; xf takes a two-terminal voltage output" % (what, probe))
        elif not _two_terminal(ent):
            ctx.err(o, "%s: probe=%s is not a two-terminal component" % (what, probe))
        else:
            _output_pair(ctx, a, args, ent)
    elif len(a.nodes) not in (1, 2):
        ctx.err(o, "%s: an output node pair (p n) or probe= is required" % what)
    else:
        _output_pair(ctx, a, args, None)
    if _enum(args, "stimuli", "sources") != "sources":
        ctx.err(o, "%s: stimuli=%s is not supported (sources only)" % (what, args["stimuli"]))


def _step(ctx: _Ctx, a: Analysis, context: List[Tuple[SweepLevel, _Resolved]], in_sweep: bool) -> None:
    what = "%s %s" % (a.kind, a.name)
    env = _step_env(ctx)
    args = _args(ctx, a, env, what)
    step = AnalysisStep(ctx.new_a(), a.name, a.kind)
    restart_no = _enum(args, "restart") == "no"
    levels: List[Tuple[SweepLevel, _Resolved]] = []
    for lvl, r in context:
        levels.append((dc_replace(lvl, id=ctx.new_w(), continuation=1 if restart_no else 0), r))
    step.context = [lvl for lvl, _ in levels]
    own: Optional[Tuple[SweepLevel, _Resolved]] = None
    if a.kind == "dc":
        if a.sweep is not None:
            cont = 0 if _enum(args, "swpuseprevic") == "no" else 1
            own = _level(ctx, a.sweep, "", a.origin, what, parent=False, continuation=cont)
    elif a.kind in FREQ_KINDS:
        if a.sweep is None:
            ctx.err(a.origin, "%s: a frequency sweep (start/stop/values) or a parameter sweep with freq= is required" % what)
        else:
            own = _level(ctx, a.sweep, "", a.origin, what, parent=False, continuation=1)
            if a.sweep.target != "freq" and "freq" not in args:
                ctx.err(a.origin, "%s: a parameter sweep of an %s needs freq=, the fixed frequency" % (what, a.kind))
    elif a.sweep is not None:
        ctx.err(a.origin, "%s: a %s has no swept variable" % (what, a.kind))
    if own is not None:
        step.own = own[0]
        own[0].id = ctx.new_w()
        levels.append(own)
    _common_dispositions(ctx, a, step, args, in_sweep)
    if a.kind == "tran":
        _tran_dispositions(ctx, a, step, args)
    elif a.kind == "noise":
        _noise_dispositions(ctx, a, step, args)
    elif a.kind == "xf":
        _xf_dispositions(ctx, a, step, args)
    step.args = args
    step.options = dict(ctx.current)
    # §5.3 source states on VACASK: around a tran, and around an analysis sweeping a derived dc
    before: List[Action] = []
    after: List[Action] = []
    if ctx.engine == "vacask":
        if a.kind == "tran":
            for path, (wave, given, src) in sorted(ctx.source_state.items()):
                if wave is not None and given:
                    before.append(Action("source", {"path": path, "type": SPECTRE_TYPE.get(wave, wave), "source": src}, None, a.origin))
                    after.append(Action("source", {"path": path, "type": "dc", "source": src}, None, a.origin))
        for lvl, r in levels:
            if r.source_field and r.inst is not None and lvl.target[2] == "dc":
                wave, given, src = ctx.source_state.get(r.inst.path, (None, True, Source()))
                if wave is not None and not given:
                    before.append(Action("source", {"path": r.inst.path, "type": "dc", "source": src}, None, a.origin))
                    after.append(Action("source", {"path": r.inst.path, "type": SPECTRE_TYPE.get(wave, wave), "source": src}, None, a.origin))
    ctx.plan.actions.extend(before)
    ctx.plan.actions.append(Action("analysis", {}, step, a.origin))
    ctx.plan.actions.extend(after)
    ctx.steps.append(step)
    _check_step_states(ctx, step, levels, what)


def _check_step_states(ctx: _Ctx, step: AnalysisStep, levels: List[Tuple[SweepLevel, _Resolved]], what: str) -> None:
    """§3.5, §3.7: the nominal state of the step, then every sweep point of the levels whose target
    can change a branch, a bin or an expression."""
    _check_state(ctx, ctx.base, what)
    axes: List[List[Tuple[_Resolved, str, str, float]]] = []
    for lvl, r in levels:
        param = lvl.target[2] if lvl.target[0] != "option" else lvl.target[1]
        if not _affects(ctx, r, param):
            continue
        axes.append([(r, param, lvl.desc, v) for v in grid_points(lvl.spec)])
    if not axes:
        return
    for combo in itertools.product(*axes):
        st = ctx.base
        for r, param, desc, v in combo:
            st = _apply(st, r, param, v)
        where = "%s at %s" % (what, ", ".join("%s=%s" % (desc, _fmt(v)) for _, _, desc, v in combo))
        _check_state(ctx, st, where)


# -- the run-dependent rules (§3.8.1, §3.9) -------------------------------------------------------------

_ANALYSES_TEXT = {"ac,noise,xf,tran": "an ac, noise, xf or tran analysis runs",
                  "noise": "a noise analysis runs"}


def _reaches_absent(rule: T.ParamRule, name: str) -> bool:
    """Whether a row applies to a parameter the card or instance does not give (tables.rule_for's
    reading of §10): a default row, a match "absent", or a `when` naming absent:<name>."""
    return rule.action == "default" or "absent" in rule.match or ("absent:%s" % name) in rule.when.split(",")


def _card_tnom(ctx: _Ctx, card: Model, step: AnalysisStep) -> float:
    e = card.params.get("tnom")
    if e is not None:
        try:
            return _ev(_exprs(e)[0], ctx.nl.values, float(step.options.get("temp", 27.0)),   # type: ignore[arg-type]
                       float(step.options.get("tnom", 27.0)))                              # type: ignore[arg-type]
        except (X.EvalError, IndexError):
            pass
    return float(step.options.get("tnom", 27.0))                                         # type: ignore[arg-type]


def _run_dependent_rules(ctx: _Ctx) -> None:
    kinds_run = {s.kind for s in ctx.steps}
    rows: List[Tuple[str, Mapping[str, object], Sequence[T.ParamRule], str, Mapping[str, float], Optional[Model]]] = []
    for card, path, env in ctx.cards_used.values():
        row = T.SPECTRE_MASTERS.get(card.prim)
        if row is not None:
            rows.append(("model %s (%s)" % (card.name, card.prim), card.params, row.params, card.origin, env, card))
    for ent in ctx.insts.values():
        it = ent.inst
        row = T.SPECTRE_MASTERS.get(it.prim) if it.kind not in ("x", "y") else None
        if row is not None:
            rows.append(("instance %s (%s)" % (ent.path, it.prim), it.params, row.instance, it.origin, ent.env.env, None))
    for label, params, rules, origin, env, card in rows:
        for name in sorted({r.name for r in rules if r.warn and r.warn != "card"}):
            rule = rule_for(rules, name, params, env)
            if rule is None or not rule.warn or rule.warn == "card":
                continue
            given = params.get(name, _MISSING)
            if given is _MISSING and not _reaches_absent(rule, name):
                # §10's grammar (tables.rule_for): a row reaches an absent parameter only through match
                # "absent", a `when` on it, or a default row; the noise-only rows warn about a value the
                # card gives, not about one it leaves at Spectre's default (phase 2's integration)
                continue
            shown = "%s absent" % name if given is _MISSING else "%s=%s" % (name, _txt(given))
            if rule.warn.startswith("analyses="):
                kinds = set(rule.warn[len("analyses="):].split(","))
                hit = sorted(kinds & kinds_run)
                if hit:
                    ctx.warn(origin, "%s: %s: %s; %s (%s)" % (label, shown, _reason(rule),
                                                             _ANALYSES_TEXT.get(rule.warn[len("analyses="):], "it runs"),
                                                             ", ".join(hit)))
            elif rule.warn == "temp!=tnom" and card is not None:
                bad = [(s.name, float(s.options.get("temp", 27.0)), _card_tnom(ctx, card, s))   # type: ignore[arg-type]
                       for s in ctx.steps]
                bad = [(n, t, tn) for n, t, tn in bad if t != tn]
                if bad:
                    n, t, tn = bad[0]
                    ctx.warn(origin, "%s: %s: %s; %s runs at temp=%s where the card's tnom is %s (an approximation, §0)"
                             % (label, shown, _reason(rule), n, _fmt(t), _fmt(tn)))
    # §3.8.1: the sources' run-dependent errors
    for ent in ctx.insts.values():
        it = ent.inst
        if it.kind in ("v", "i") and it.source is not None:
            sp = it.source.spectre
            if "xf" in kinds_run and "xfmag" in sp:
                try:
                    v = _ev(_exprs(sp["xfmag"])[0], ent.env.env, 27.0, 27.0)
                except (X.EvalError, IndexError):
                    v = None
                if v != 1.0:
                    ctx.err(it.origin, "source %s: xfmag=%s while an xf runs: not supported (§3.8.1)"
                            % (ent.path, _txt(sp["xfmag"])))
            if "noise" in kinds_run:
                for key in ("noisefile", "noisevec"):
                    if key in sp:
                        ctx.err(it.origin, "source %s: %s while a noise analysis runs: not supported (§3.8.1)" % (ent.path, key))
    # device ic= on C and L (§0): an error unless every tran uses ic=dc or ic=node
    trans = [s for s in ctx.steps if s.kind == "tran"]
    if any(_enum(s.args, "ic", "all") in ("all", "dev") for s in trans):
        for ent in ctx.insts.values():
            it = ent.inst
            if it.kind in ("c", "l") and "ic" in it.params:
                ctx.err(it.origin, "%s: device ic= on a %s is not supported unless every tran uses ic=dc or ic=node (§0)"
                        % (ent.path, "capacitor" if it.kind == "c" else "inductor"))


def _ic_values(ctx: _Ctx) -> None:
    """§3.10: ic and nodeset values evaluated with Netlist.values; one reading a variable is an error."""
    env = _step_env(ctx)
    for what, table in (("ic", ctx.nl.ics), ("nodeset", ctx.nl.nodesets)):
        for node, v in table.items():
            if isinstance(v, (int, float)):
                continue
            if not _check_dependents(ctx, v, "", "%s %s" % (what, node)):
                continue
            _setting(ctx, v, env, ctx.base, "", "%s %s" % (what, node))


def _variables(ctx: _Ctx) -> Dict[str, float]:
    """Every top-level parameter a sweep or alter targets, at its nominal value (§4.5 item 4)."""
    out: Dict[str, float] = {}
    top = {p.name: p for p in ctx.top_params}
    for a in flat_analyses(ctx.nl.analyses):
        name: Optional[str] = None
        if a.kind in ("dc", "sweep", "ac", "noise", "xf") and a.sweep is not None and a.sweep.target == "param":
            name = a.sweep.param
        elif a.kind == "alter" and not any(k in a.args for k in ("dev", "mod", "sub")):
            name = _text_of(a.args.get("param"))
        if name and name not in ("temp", "tnom") and name in top and name not in out:
            v = ctx.nl.values.get(name)
            if v is None:
                try:
                    v = _ev(top[name].expr, ctx.nl.values, 27.0, 27.0)
                except X.EvalError as exc:
                    ctx.err(top[name].origin, "parameter %s does not evaluate: %s" % (name, exc))
                    continue
            out[name] = float(v)
    return out


def _dependents(ctx: _Ctx, variables: Mapping[str, float]) -> Set[str]:
    deps = set(variables)
    changed = True
    while changed:
        changed = False
        for p in ctx.top_params:
            if p.name not in deps and X.names(p.expr) & deps:
                deps.add(p.name)
                changed = True
    return deps


def build(nl: Netlist, engine: str, settings: Settings) -> RunPlan:
    """The ordered plan of a Spectre netlist for one engine (§5): per-path and per-state evaluation
    on tables.path_envs, the sweep grids, alters and source states, the options actions, dependents.
    NoteError with every note when a construct is an error."""
    if engine not in T.ENGINES:
        raise ValueError("engine must be one of %s, not %r" % (T.ENGINES, engine))
    ctx = _Ctx(nl, engine, settings)
    plan = ctx.plan
    ctx.scan()
    gopts = _global_options(ctx)
    ctx.current = dict(gopts)
    plan.options = dict(gopts)
    plan.actions.append(Action("options", dict(gopts), None, ""))
    ctx.base = _State({}, float(gopts["temp"]), float(gopts["tnom"]))    # type: ignore[arg-type]
    with substituted(ctx.sites, ctx.base.temp, ctx.base.tnom):
        ctx.elaborate()
    ctx.variables = _variables(ctx)
    ctx.dependents = _dependents(ctx, ctx.variables)
    plan.variables = dict(ctx.variables)
    plan.dependents = set(ctx.dependents)
    if ctx.variables:
        plan.actions.append(Action("var", dict(ctx.variables), None, ""))
    if not nl.analyses:
        ctx.note("", "the netlist has no analyses: nothing to run")
    for a in nl.analyses:
        _analysis(ctx, a, [], in_sweep=False)
    if not ctx.steps and nl.analyses:
        _check_state(ctx, ctx.base, "the netlist")
    _run_dependent_rules(ctx)
    _ic_values(ctx)
    if ctx.errors:
        raise NoteError(ctx.notes)
    plan.notes = list(ctx.notes)
    return plan
