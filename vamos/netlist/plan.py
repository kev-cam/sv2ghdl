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
  - the run-dependent card rules (ParamRule.warn: analyses=..., temp!=tnom over every temperature
    a step runs a card at), the sources' run-dependent errors (§3.8.1), device ic= on C/L, and the
    ic/nodeset values (§3.10);
  - phase 2c: AnalysisStep.user_options (D2), the state files an earlier analysis writes (D9: a
    readic=/read= of one an error, a readns= of one a warning and not read), a model-group instance
    without w or l an error (D4);
  - phase 2c2: a non-constant r that is 0 in a state an analysis runs in, or that an alter sets, an
    error naming the resistor, the sweep or alter and the point (F1); the dev= targets a resistor's
    printed form decides: a short's parameters, a sweep of isnoisy (F2, R-plan-19); E102's
    bound-expression check for every master's value parameter and the sources' fields (F3);
  - phase 2c3: an alter of a parameter the netlist binds to an expression reading a name, then a change
    of what it reads, an error on both engines at the first point where the two readings of Spectre
    differ, and on VACASK any change of a variable, temp or tnom (or of the holding subckt instance's
    parameters) after its elaboration before the alter (G1, _Bound); F1's check of a dev= sweep or
    alter of a resistor's r without a walk per point, and of a walked state from the walk's own values
    (G2, _State.rset); a constant expression equal to 0 is the short (G3, constant_zero);
  - phase 2c4: on VACASK a sweep or alter target bound to an expression prints through a switchable hook
    (vacask_hooked, vacask.hook_key), which VACASK's re-evaluation keeps: G1's VACASK-only rule (b) and E102's
    refusal of a dev= sweep stay for what the hook cannot print, with E151 (temp or tnom other than 27 at VACASK's
    first elaboration, an alter before the first analysis) as rule (b)'s trigger there (H1); rule (a) also for an
    alter of a netlist parameter defined by an expression and a sub= alter of a subckt default the X line leaves (H2);
  - phase 2c6: G1 default-deny (K1): an alter of every value type is a bound, compared as its type fits (a pwl's wave
    elementwise; a word or a vector G1 cannot compare refused at the first change), with every spelling of its target (a
    card's renamed alias, a Verilog-A parameter's case) and a source field's bound default (a sine's sinedc, its dc);
    on VACASK every field a source statement prints as a constant where the instance line derives it from fields that
    read a name is a bound of rule (b) (_bound_printed); rule (a) for a sweep of a bound target around a sweep of what
    it reads (_sweep_bounds); E102 for a source field VACASK's instance line derives (sinedc from dc=vv); a sweep of an
    exp's delay refused (spectre.resolve_source folds it into td1 and td2);
  - phase 2c7: §3.7's nominal structure (the if branches and model-group bins) recorded from the netlist's own state
    before any control statement (L2, _record_nominal), so an alter that changes a branch or a bin before the first
    analysis is refused, naming the condition and the branches; the state a run's statement executes in, for the
    renderers (L1, run_values, run_paths); the source constraints of §3.8.1 and of VACASK's setup in every state an
    engine sets a source up in: a pulse's period no longer than rise+fall+width, a negative rise, pwl times that do not
    increase, an exp's td2 no later than its td1 on both engines in a tran, a sine's freq not above 0 and a pwl's first
    time point before 0 on VACASK, and every one on VACASK where it sets the waveform up outside a tran (a derived dc)
    or at its first elaboration (L4, _check_sources); a hooked sweep's first point as its hook's value (L3, _bound_step);
  - phase 2c8: the rule of every source field whose constant vamos refuses, in every state on both engines with the
    constant's words (M1: a negative fall, a negative width, a pwl time point before 0, a single pwl point; M3: an exp
    time constant of 0; M4: a negative freq, while a freq of 0 stays VACASK's alone), wherever the engines use the
    waveform (a tran, a derived dc), a state whose violating fields are constants refused in every analysis as the
    render refuses the constant, and with no analysis at all (_constant_sources); a sine of constant freq 0 in
    tables.constant_sine's form in the source states, the alters and the sweep points (M2: its given dc for the
    DC-type solves, the constant in a tran, its fields swept and altered as a sine's), a sweep of its damp away from 0
    refused, a swept freq of 0 its own 1e-30; §3.7's conditions in the netlist's spelling (_netlist_text);
    edge_default, a swept rise or fall of 0 as transres for the emitters;
  - phase 2c9: one predicate for the value an engine is given at a swept source field's point (swept_value and
    swept_points, which replace 2c8's edge_points and constant_sine_points: a rise or fall of 0 of a pulse is
    transres, a freq of 0 of the constant sine its 1e-30), which every consumer reads in the source state of the
    statement being printed (plan.build's states, VACASK's deck from the alter and source actions, Xyce's replayed
    deck; 2c8's VACASK deck read the instance line: N1); a swept dc moves a sinedc that defaults to it
    (sinedc_follows_dc, N3); tau2's own clause (N5); VACASK's setup before a sweep's first point, in the values in
    force then, for the sources the sweeps reach (_presweep_source, N5).

Errors are collected with every other note and raised together as a NoteError at the end;
warnings and notes come back in RunPlan.notes.  DIALECT names the expression dialect of every
evaluation ("spectre", §3.5); S2's unit tests run the same code under "hspice" with numeric
inputs, where the two dialects agree.
"""

from __future__ import annotations

import itertools
import math
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from typing import Any, Collection, Dict, Iterator, List, Mapping, Optional, Sequence, Set, Tuple, Union

from vamos.netlist import expr as X
from vamos.netlist import tables as T
from vamos.netlist.expr_ast import Binary, Expr, Name, Num, Str, Unary
from vamos.netlist.ir import (Analysis, Cond, Instance, Model, Netlist, Param, ParamTest, SaveSpec, Source,
                              Subckt, SweepSpec, flat_analyses, flat_items)
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
    user_options: Set[str] = field(default_factory=set)   # D2 (phase 2c): the option keys the netlist (options, set,
    #                               alter), +paramdefault or the command line set before this step, and a tran's own
    #                               reltol relref lteratio errpreset method keys; never a Spectre default


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
# D2: job.settings writes these into Settings.options whatever the run set (the resolved output settings), so
# they count as set only when the netlist's options give them
SETTINGS_WRITTEN = ("rawfmt", "rawfile", "maxwarns", "maxnotes")
# D2: a tran's own tolerance and method keys (§5.4), the ones the Xyce notes name
TRAN_TOLERANCE_KEYS = ("reltol", "relref", "lteratio", "errpreset", "method")
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
# tran skipdc [M ref19 p.417, pp.428-429]: v1 runs no and yes (§0); ac, noise and xf take no and yes [M ref19 p.44]
TRAN_SKIPDC = ("no", "yes", "useprevic", "waveless", "rampup", "autodc", "sigrampup", "dcrampup")
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


def _netlist_text(e: object) -> str:
    """Phase 2c8 (M4): a condition as the netlist spells it, as near as the IR keeps it: temp and tnom by their Spectre
    names (the parse reads them as temper and $tnom) and each number in its shortest form, without a trailing .0
    (`temp > 40', `sel > 0'; 2c7 printed `temper > 40.0', `sel > 0.0').  A scale factor (1u) prints as its value."""
    def spell(n: Expr) -> Expr:
        if isinstance(n, Name) and n.name in ("temper", "$tnom"):
            return Name("temp" if n.name == "temper" else "tnom")
        if isinstance(n, Num):
            text = repr(abs(float(n.value)))
            text = text[:-2] if text.endswith(".0") else text
            return Name(text) if n.value >= 0 else Unary("-", Name(text))
        return n
    try:
        return _txt(X.transform(e, spell))                 # type: ignore[arg-type]
    except Exception:  # noqa: BLE001  (not an expression)
        return _txt(e)


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
    (§10); None when no rule of that name applies.  A binning name of bsim3v3 or bsim4 without a row of
    its own takes its P0 parameter's rule (tables.binning_rule, ref5 p.186, p.209; phase 2c), given or
    not, as an unconditional row does here (a mod= target need not be on the card)."""
    value = params.get(name, _MISSING)
    for r in rules:
        if r.name != name:
            continue
        if _when_holds(r.when, params) and _match_holds(r.match, value, env or {}, temp, tnom):
            return r
    return T.binning_rule(rules, name)


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


def run_values(nl: Netlist, variables: Mapping[str, float], temp: float, tnom: float,
               dependents: Collection[str] = ()) -> Dict[str, float]:
    """L1 (phase 2c7): the top-level parameter values of one state of a run, as plan.build's walk computes them
    (_top_values): nl's values with the plan variables' values in force (variables), and every other top-level parameter
    that is a dependent (RunPlan.dependents), reads temp or tnom, or is missing from nl's values evaluated again, in
    netlist order to a fixpoint (a VACASK render copy leaves the variables and their dependents out of its values,
    signals.resolve).  EvalError naming the parameter when one does not evaluate."""
    vals: Dict[str, float] = dict(nl.values)
    vals.update(variables)
    params = [p for p in nl.body if isinstance(p, Param)]
    todo = [p for p in params if p.name not in variables
            and (p.name in dependents or _reads_temp(p.expr) or p.name not in vals)]
    remaining: List[Tuple[Param, X.EvalError]] = []
    for _ in range(len(todo) + 1):
        remaining = []
        progress = False
        for p in todo:
            try:
                vals[p.name] = _ev(p.expr, vals, temp, tnom)
                progress = True
            except X.EvalError as exc:
                remaining.append((p, exc))
        todo = [p for p, _ in remaining]
        if not todo or not progress:
            break
    if remaining:
        p, exc = remaining[0]
        raise X.EvalError("parameter %s=%s does not evaluate: %s" % (p.name, _txt(p.expr), exc))
    return vals


def run_paths(nl: Netlist, values: Mapping[str, float], temp: float, tnom: float,
              xvalues: Sequence[Tuple[Instance, str, float]] = (), sites: Optional[TempSites] = None
              ) -> List[T.PathEnv]:
    """L1 (phase 2c7): the instance paths a run takes in one state, as plan.build's walk takes them (_check_paths): the
    top-level values (run_values), temp and tnom put into the IR's temp sites (substituted; sites: temp_sites(nl), once
    per netlist), the X-line values the state's sub= alters set (xvalues: the X instance, its parameter, the value), and
    tables.path_envs strict, so a condition decides its branch at those values; the IR is restored.  EvalError or
    TableError when they do not resolve."""
    saved: List[Tuple[Dict[str, Any], str, Any]] = []
    try:
        for inst, p, v in xvalues:
            saved.append((inst.params, p, inst.params.get(p, _MISSING)))
            inst.params[p] = Num(v)
        with substituted(sites if sites is not None else temp_sites(nl), temp, tnom):
            return list(T.path_envs(nl, values=values, strict=True, dialect=DIALECT))
    finally:
        for holder, key, old in reversed(saved):
            if old is _MISSING:
                holder.pop(key, None)
            else:
                holder[key] = old


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
    paths: Dict[Tuple[int, str], str] = field(default_factory=dict)   # the IR path a dev= value of inst names
    rset: Dict[str, float] = field(default_factory=dict)  # G2 (phase 2c3): resistor path -> the r a dev= sweep or alter
    #                                                       sets there (F1); no expression reads it, so no walk does
    # phase 2c5 (R1): the VACASK variables of the switchable hooks, target -> (flag, value) as the deck's var statements
    # set them ((0, 0) when absent; vacask.prints_hook); no walk reads them, VACASK re-evaluates its bindings on them
    hooks: Dict[Tuple[str, str, str], Tuple[float, float]] = field(default_factory=dict)

    def walk_key(self) -> Tuple:
        """What a walk of the instance paths reads (_check_paths): everything but rset."""
        return (tuple(sorted(self.top.items())), self.temp, self.tnom,
                tuple(sorted((k, v[2], self.paths.get(k, "")) for k, v in self.inst.items())),
                tuple(sorted((k, v[2]) for k, v in self.model.items())))

    def key(self) -> Tuple:
        return self.walk_key() + (tuple(sorted(self.rset.items())),)

    def copy(self) -> "_State":
        return _State(dict(self.top), self.temp, self.tnom, dict(self.inst), dict(self.model), dict(self.paths),
                      dict(self.rset), dict(self.hooks))

    def hook(self, target: Tuple[str, str, str]) -> Tuple[float, float]:
        return self.hooks.get(target, _HOOK_OFF)


_HOOK_OFF = (0.0, 0.0)


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
        # phase 2c
        self.user_keys: Set[str] = set()                        # D2: the option keys the run set so far
        self.cwd = ""                                           # D9: where relative state-file names resolve (build)
        self.outdir: Optional[str] = None                       # D9: the resolved -outdir (§2.5)
        self.state_writes: Dict[str, Tuple[str, str, str]] = {}  # D9: path -> (step, key, name) written so far
        self.card_users: Dict[int, Dict[str, Instance]] = {}    # R-plan-6: id(card) -> {instance path: instance}
        self.trise_now: Dict[str, float] = {}                   # R-plan-6: instance path -> trise an alter set
        self.step_trise: Dict[str, Dict[str, float]] = {}       # R-plan-6: step id -> trise_now at the step
        self.nowl_reported: Set[Tuple[str, str]] = set()        # D4: (path, instance) without w or l reported
        # phase 2c2
        self.zeros: Dict[Tuple, List[Tuple[str, str, str]]] = {}  # F1: walk key -> the resistances 0 in it (G2: its
        #                                                           rset is applied by _zeros_of)
        self.zero_reported: Set[str] = set()                    # F1: the resistor paths reported
        self.history: List[Tuple[_State, str]] = []             # F1: (base state, the alter or set that made it)
        # phase 2c3
        self.walk_order: Dict[str, int] = {}                    # G2: instance path -> its place in a walk
        self.bound: List[_Bound] = []                           # G1: the alters of bound parameters still in force
        self.elab: Optional[Tuple[_State, int]] = None          # G1: VACASK's last elaboration: (its state, the
        #                                                         number of marks then)
        self.marks: List[Tuple[_State, str]] = []               # G1: (base state, the alter or set that made it)
        self.scope_envs: Dict[Tuple, Optional[Dict[str, float]]] = {}   # G1: (walk key, path) -> its values
        # phase 2c5
        self.hook_printed: Dict[Target, bool] = {}              # R1: target -> a VACASK deck prints it through a hook
        self.ctl_reported: Set[Tuple[str, str]] = set()         # R4: (source path, field) refused in a control statement
        self.resolutions: Dict[str, List] = {}                  # vacask.path_resolutions' cache (IR path -> resolutions)
        # phase 2c7
        self.cond_texts: Dict[int, List[str]] = {}              # L2: id(Cond) -> its conditions as written (scan)
        self.nominal_conds: Dict[str, List[Cond]] = {}          # L2: path -> the Conds of its nominal walk, in sig order
        self.nominal_where: Dict[str, str] = {}                 # L2: path -> where its nominal structure was recorded
        self.src_alter: Dict[str, str] = {}                     # L4: source path -> its last source-field alter
        self.src_reported: Set[Tuple[str, str]] = set()         # L4: (source path, constraint) reported
        self.src_cache: Dict[Tuple, List[Tuple[str, str]]] = {}  # L4: an evaluated source's violations
        self.src_reads: Dict[int, Tuple[str, ...]] = {}         # L4: id(a plan's Source) -> what its waveform reads
        # phase 2c8
        self.netlist_src: Dict[int, Source] = {}                # M2: id(instance) -> the Source its instance line prints
        self.csine_reported: Set[Tuple[str, Target]] = set()    # M2: (sweep, target) of a constant sine refused
        # phase 2c9
        self.src_presweep: Set[Tuple[str, str]] = set()         # N5: (source path, constraint) refused at a pre-sweep setup

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
        """What build needs before the first elaboration: the top-level parameters and the temp sites; phase 2c7 (L2):
        each structural condition's text as written (a walk substitutes temp and tnom into it)."""
        self.top_params = [p for p in self.nl.body if isinstance(p, Param)]
        self.sites = temp_sites(self.nl)
        self.reads_temp = bool(self.sites) or any(_reads_temp(p.expr) for p in self.top_params)
        self.cond_texts = {}

        def conds(items: Sequence[object]) -> None:
            for it in items:
                if isinstance(it, Cond):
                    self.cond_texts[id(it)] = [_netlist_text(c) for c, _body in it.branches]
                    for _c, body in it.branches:
                        conds(body)
                    conds(it.default)
                elif isinstance(it, Subckt):
                    conds(it.body)
        conds(self.nl.body)

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
        self.walk_order = {path: k for k, path in enumerate(self.insts)}
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
                src = _netlist_source(self, ent)          # phase 2c8 (M2): as signals.resolve's render copy prints it
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


def _top_values(ctx: _Ctx, st: _State, where: str, quiet: bool = False) -> Dict[str, float]:
    """The top-level parameter values in a state: Netlist.values, the variables' values, and every
    dependent or temperature-reading parameter re-evaluated (§3.5).  quiet: a parameter that does not
    evaluate is left out, not reported (G1's evaluations; the walk of the state reports it)."""
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
        if quiet:
            vals.pop(p.name, None)
            continue
        ctx.err(p.origin, "%s: parameter %s=%s does not evaluate: %s" % (where, p.name, _txt(p.expr), exc))
    return vals


def _walk(ctx: _Ctx, items: Sequence[object], pe: T.PathEnv, st: _State, where: str,
          insts: List[Instance], tests: List[ParamTest], sig: List[int], conds: Optional[List[Cond]] = None,
          quiet: bool = False) -> bool:
    """The items of one body on one path in a state: each Cond resolved (the branch index goes to
    sig), the ParamTests of the taken branches collected (the rule tables.path_envs applies, §4.4).
    Phase 2c7 (L2): conds receives each Cond in sig's order (the messages of §3.7's rule name it); quiet: a
    condition that does not evaluate stops the walk, unreported (False), as _record_nominal walks."""
    for it in items:
        if isinstance(it, Instance):
            insts.append(it)
        elif isinstance(it, Cond):
            taken: Optional[Sequence[object]] = None
            for i, (c, body) in enumerate(it.branches):
                try:
                    v = _ev(c, pe.env, st.temp, st.tnom)
                except X.EvalError as exc:
                    if quiet:
                        return False
                    ctx.err(it.origin, "%s: condition %s is not a number on path %s: %s"
                            % (where, _txt(c), pe.path or "top level", exc))
                    v = 0.0
                if v != 0:
                    taken, sig_i = body, i
                    break
            else:
                taken, sig_i = it.default, -1
            sig.append(sig_i)
            if conds is not None:
                conds.append(it)
            if not _walk(ctx, taken, pe, st, where, insts, tests, sig, conds, quiet):
                return False
        elif isinstance(it, ParamTest):
            tests.append(it)
    return True


NOMINAL_WHERE = "the netlist's own values"


def _record_nominal(ctx: _Ctx) -> None:
    """L2 (phase 2c7): §3.7's nominal structure is the netlist's own state, before any control statement: the branch each
    structural condition takes on every instance path and the card each model-group instance selects, at the netlist's
    values and the global options' temperatures (the state Spectre elaborates the netlist in, VACASK's first elaboration
    and Xyce's nominal IR).  2c6 took the first state a statement walked, so an alter of what a condition reads before
    the first analysis was the nominal one: it was not refused, and an alter of a source in that if took its source
    state from the branch the netlist takes, not the one the run takes (S-2c6's H05h: in 2 where 1, status 0 on both
    engines).  Quiet: what does not evaluate here is reported by the walks of the states (_check_paths), and a path
    whose structure is not recorded here takes its first walk's, as before.  A structure walk only (no instance
    expression is evaluated), non-strict: a parameter that does not evaluate is hidden on its path, and a condition
    reading it leaves that path to its first walk."""
    st = ctx.base
    try:
        with _applied(ctx, st):
            top = _top_values(ctx, st, "", quiet=True)
            paths = list(T.path_envs(ctx.nl, values=top, strict=False, dialect=DIALECT))
            for pe in paths:
                insts: List[Instance] = []
                sig: List[int] = []
                conds: List[Cond] = []
                if not _walk(ctx, ctx.body_of(pe), pe, st, "", insts, [], sig, conds, quiet=True):
                    continue
                if pe.path not in ctx.nominal_sig:
                    ctx.nominal_sig[pe.path] = tuple(sig)
                    ctx.nominal_conds[pe.path] = conds
                    ctx.nominal_where[pe.path] = NOMINAL_WHERE
                for it in insts:
                    if it.kind in T.MODEL_KINDS_OF:
                        _card, bins = pe.scope.card_of(it)
                        if bins:
                            _check_bin(ctx, it, bins, pe, st, "", quiet=True)
    except (X.EvalError, T.TableError):
        return


def _branch_text(ctx: _Ctx, c: Cond, i: int) -> str:
    """How §3.7's message names the branch index i of a Cond takes (_walk's sig: -1 the default)."""
    if i == -1:
        return "its else branch" if c.default else "no branch (it has no else)"
    if i == 0:
        return "its if branch"
    return "its branch %d (%s)" % (i + 1, _cond_text(ctx, c, i))


def _cond_text(ctx: _Ctx, c: Cond, i: int = 0) -> str:
    """A condition as the netlist writes it (_Ctx.scan's, before a walk substitutes temp and tnom into it)."""
    texts = ctx.cond_texts.get(id(c))
    return texts[i] if texts is not None and i < len(texts) else _netlist_text(c.branches[i][0])


def _structure_message(ctx: _Ctx, where: str, path: str, sig: Tuple[int, ...], conds: Sequence[Cond]
                       ) -> Tuple[str, str]:
    """(origin, message) of §3.7's refusal of a state whose structure differs from the nominal one on a path: the walks
    are equal up to the first branch that differs, so that Cond is the one at its index in both (phase 2c7, L2: the
    message names the condition, the branch the state takes and the nominal one, and where that was recorded)."""
    nom = ctx.nominal_sig[path]
    k = next((i for i, (a, b) in enumerate(zip(sig, nom)) if a != b), None)
    tail = "vamos refuses a sweep or alter that changes an if branch, as Spectre does (§3.7, §14 q.13)"
    if k is None or k >= len(conds) or not conds[k].branches:
        return "", "%s changes a structural condition on path %s: %s" % (where, path or "top level", tail)
    c = conds[k]
    at = ctx.nominal_where.get(path, NOMINAL_WHERE)
    return c.origin, ("%s changes a structural condition on path %s: the condition %s takes %s, where it takes %s %s: %s"
                      % (where, path or "top level", _cond_text(ctx, c), _branch_text(ctx, c, sig[k]),
                         _branch_text(ctx, c, nom[k]), "at " + at if at == NOMINAL_WHERE else "in " + at, tail))


def _bin_rule_bins(bins: Sequence[Model]) -> List[Model]:
    if bins and bins[0].bin_rule == "spectre":
        return sorted(bins, key=lambda m: (m.bin_index is None, m.bin_index or 0, m.name))
    return list(bins)


def _check_bin(ctx: _Ctx, it: Instance, bins: Sequence[Model], pe: T.PathEnv, st: _State, where: str,
               quiet: bool = False) -> None:
    """§3.7: the bin a model-group instance selects, at every state; §4.3: the HSPICE rule's card
    beside the Spectre rule's where the group carries HSPICE bounds.  D4 (phase 2c): an instance of a
    Spectre model group selects only with both w and l on its line [M ref5 p.203].  quiet (phase 2c7, L2): only the
    nominal card is recorded (_record_nominal), nothing reported."""
    env = pe.env
    bins = _bin_rule_bins(bins)
    master = bins[0].prim
    geom = dict(T.SPECTRE_MASTERS[master].geometry) if master in T.SPECTRE_MASTERS else {}
    vals: Dict[str, float] = {}
    if bins[0].bin_rule == "spectre":
        missing = [k for k in ("w", "l") if it.params.get(k) is None]
        if missing:
            key = (pe.path, it.name)
            if key not in ctx.nowl_reported and not quiet:
                ctx.nowl_reported.add(key)
                ctx.err(it.origin, "instance %s on path %s of model group %s has no %s: automatic model selection "
                        "needs both w and l on the instance line [M ref5 p.203] (§3.9)"
                        % (it.name, pe.path or "top level", it.master, " or ".join(missing)))
            return
    for k in ("l", "w", "nf"):
        e = it.params.get(k)
        if e is None:
            if k == "nf":
                vals[k] = 1.0
            elif k in geom:
                vals[k] = float(geom[k])
            else:
                if not quiet:
                    ctx.err(it.origin, "%s: instance %s on path %s has no %s for its model group %s"
                            % (where, it.name, pe.path or "top level", k, it.master))
                return
        else:
            try:
                vals[k] = _ev(_exprs(e)[0], env, st.temp, st.tnom)
            except (X.EvalError, IndexError) as exc:
                if not quiet:
                    ctx.err(it.origin, "%s: instance %s on path %s: %s=%s does not evaluate: %s"
                            % (where, it.name, pe.path or "top level", k, _txt(e), exc))
                return
    s = ctx.nl.scale()
    path = pe.path or "top level"
    try:
        if bins[0].bin_rule == "spectre":
            bounds = [(b, T.bin_bounds_spectre(b, env)) for b in bins]
            chosen = T.select_bin_spectre(bounds, vals["l"], vals["w"], s)
            if all(all(k in b.params for k in T.BIN_KEYS) for b in bins) and not quiet:   # §4.3: HSPICE-style x.N bins
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
        if not quiet:
            ctx.err(it.origin, "%s: instance %s on path %s: %s" % (where, it.name, path, exc))
        return
    if chosen is None:
        if not quiet:
            ctx.err(it.origin, "%s: instance %s on path %s selects no card of model group %s (l=%s w=%s nf=%s)"
                    % (where, it.name, path, it.master, _fmt(vals["l"]), _fmt(vals["w"]), _fmt(vals["nf"])))
        return
    key = (path, it.name)
    if key not in ctx.nominal_bin:
        ctx.nominal_bin[key] = chosen.name
    elif quiet:
        return
    elif ctx.nominal_bin[key] != chosen.name:
        ctx.err(it.origin, "%s changes the bin instance %s on path %s selects (%s, nominal %s): vamos "
                "refuses a sweep or alter that changes a model-group bin (§3.7, §14 q.13)"
                % (where, it.name, path, chosen.name, ctx.nominal_bin[key]))


def _check_instance(ctx: _Ctx, it: Instance, pe: T.PathEnv, st: _State, where: str) -> Optional[float]:
    """Every expression of an instance on one path in one state (§3.5) and its cards; returns the value of its
    Instance.value (a resistor's r, which F1's zero check reads: G2, phase 2c3), None when it has none or it does
    not evaluate."""
    path = pe.path or "top level"
    value: Optional[float] = None
    for k, e in _item_exprs(it):
        try:
            v = _ev(e, pe.env, st.temp, st.tnom)
            if k == "value" and value is None:
                value = v
        except X.EvalError as exc:
            ctx.err(it.origin, "%s: %s=%s of instance %s on path %s does not evaluate: %s"
                    % (where, k, _txt(e), it.name, path, exc))
    if it.kind in T.MODEL_KINDS_OF:
        card, bins = pe.scope.card_of(it)
        for c in ([card] if card is not None else []) + list(bins):
            ctx.cards_used.setdefault(id(c), (c, path, dict(pe.env)))
            ctx.card_users.setdefault(id(c), {}).setdefault(it.name if not pe.path else pe.path + "." + it.name, it)
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
    return value


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


_ZERO_TEXT = ("resistor %s on path %s: %s, and vamos prints a short only for the constant r=0: a non-constant r that "
              "evaluates to 0 is an error in v1 (§3.8, §4.5 item 10; VACASK's resistor fails with NaN at r=0, and Xyce's "
              "swept to 0 becomes an open circuit)")


def _zero_expr(it: Instance, pe: T.PathEnv) -> Optional[Tuple[str, str, str]]:
    """F1 (phase 2c2): (the resistor's path, its origin, the finding) for a resistor whose r a walk evaluated to 0
    on this path (_check_paths passes it only then, G2) and that is not the short the emitters print (is_short:
    the constant 0, phase 2c3's G3 a constant expression equal to it) or an infinite resistor.  Spectre makes such
    a resistor a short (the resistance form it chooses below thresh [M ref5 p.632]); VACASK's resistor gives NaN
    at r = 0 and Xyce's turns into an open circuit (a sweep through 0 gave out = 0 where the short gives 1 V, exit
    0, measured), so vamos refuses it."""
    if isinstance(it.value, Num) or is_short(it) or is_infinite_resistor(it):
        return None
    path = it.name if not pe.path else pe.path + "." + it.name
    return (path, it.origin, _ZERO_TEXT % (it.name, pe.path or "top level", "r=%s evaluates to 0" % _txt(it.value)))


def _zero_set(ctx: _Ctx, path: str) -> Optional[Tuple[str, str, str]]:
    """F1: the finding for the resistor at path whose r a dev= sweep or alter sets to 0 (_State.rset), None for a
    short, an infinite resistor or a path that holds no resistor."""
    ent = ctx.insts.get(path)
    if ent is None or ent.inst.kind != "r" or is_short(ent.inst) or is_infinite_resistor(ent.inst):
        return None
    return (path, ent.inst.origin, _ZERO_TEXT % (ent.inst.name, path.rpartition(".")[0] or "top level",
                                                 "its r, set by a sweep or alter, is 0"))


def _zeros_of(ctx: _Ctx, st: _State) -> List[Tuple[str, str, str]]:
    """F1: the resistances that are 0 in a state: the walk's findings for its walk key (_check_state walked it)
    less the resistors whose r the state sets, plus those it sets to 0, in walk order (G2, phase 2c3: a dev=
    value of r changes that resistor alone, so it is applied here instead of in a walk)."""
    walk = ctx.zeros.get(st.walk_key(), [])
    if not st.rset:
        return walk
    out = [z for z in walk if z[0] not in st.rset]
    for path, v in st.rset.items():
        if v == 0.0:
            z = _zero_set(ctx, path)
            if z is not None:
                out.append(z)
    out.sort(key=lambda z: ctx.walk_order.get(z[0], len(ctx.walk_order)))
    return out


def _walk_state(ctx: _Ctx, st: _State, where: str) -> None:
    """The walk of one state (_check_paths), once per walk key."""
    wk = st.walk_key()
    if wk not in ctx.checked:
        ctx.checked.add(wk)
        ctx.zeros[wk] = []
        _check_paths(ctx, st, where, ctx.zeros[wk])


def _check_state(ctx: _Ctx, st: _State, where: str, runs: bool = False, sweeps: str = "") -> None:
    """Every reachable expression on every instance path in one state (§3.5), the ParamTests and
    the condition/bin rule (§3.7).  runs (phase 2c2, F1): an analysis runs in this state; the resistances
    found 0 in it (_zeros_of) are then errors, each resistor once, naming `where`, the sweeps the
    state is a point of (`sweeps`) and the alter or set statement that made the resistance 0 (_zero_cause)."""
    _walk_state(ctx, st, where)
    if runs:
        _report_zeros(ctx, _zeros_of(ctx, st), where, sweeps)


def _report_zeros(ctx: _Ctx, zeros: Sequence[Tuple[str, str, str]], where: str, sweeps: str) -> None:
    for path, origin, text in zeros:
        if path in ctx.zero_reported:
            continue
        ctx.zero_reported.add(path)
        parts = [sweeps] if sweeps else []
        cause = _zero_cause(ctx, path)
        if cause:
            parts.append("after " + cause)
        ctx.err(origin, "%s%s: %s" % (where, " (%s)" % "; ".join(parts) if parts else "", text))


def _zero_cause(ctx: _Ctx, path: str) -> str:
    """The alter or set statement since whose state the resistor at path has been 0 in the base state (the
    states the alters and sets made, ctx.history), "" when it is 0 at the netlist's values or not in the base."""
    cause = ""
    for st, where in reversed(ctx.history):
        if not any(z[0] == path for z in _zeros_of(ctx, st)):
            break
        cause = where
    return cause


def _check_paths(ctx: _Ctx, st: _State, where: str, zeros: List[Tuple[str, str, str]]) -> None:
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
            conds: List[Cond] = []
            _walk(ctx, ctx.body_of(pe), pe, st, where, insts, tests, sig, conds)
            sigt = tuple(sig)
            if pe.path not in ctx.nominal_sig:
                ctx.nominal_sig[pe.path] = sigt                # a path the netlist's own state does not reach
                ctx.nominal_conds[pe.path] = conds
                ctx.nominal_where[pe.path] = where
            elif ctx.nominal_sig[pe.path] != sigt:
                origin, msg = _structure_message(ctx, where, pe.path, sigt, conds)
                ctx.err(origin, msg)
            for it in insts:
                v = _check_instance(ctx, it, pe, st, where)
                if v == 0.0 and it.kind == "r":          # F1: the value the walk computed (G2: not evaluated again)
                    z = _zero_expr(it, pe)
                    if z is not None:
                        zeros.append(z)
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
    if spec.start is None:
        start = 0.0                       # Spectre's default start [M ref19 pp.43, 64, 182, 406, 439; UG p.67]
    if start is None or stop is None:
        if spec.stop is None:
            ctx.err(origin, "%s needs stop (or values)" % what)
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


def _labels(ctx: _Ctx, target: Target, parent: bool, param: str = "") -> Tuple[str, str, str]:
    """(label, desc, units) of a level (§5.2, §8.3).  param is the parameter as the netlist wrote it: the
    label and the description name it, never the engine spelling a rename gives the target (phase 2c)."""
    kind, name, p = target
    if kind == "freq":
        return "freq", "freq", "Hz"
    if kind == "option":
        return name, name, "C"
    if kind == "variable":
        return name, name, ""
    p = param or p
    full = "%s:%s" % (name, p)
    if kind == "model":
        return (full if parent else p), full, ""
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


# The value parameter spectre.parse moves into Instance.value, per master (spectre.rcl_value and the masters
# pass): the resistor's r, the capacitor's c, the inductor's l, a controlled source's gain, gm or rm, a mutual
# inductor's coupling, a diode's, bjt's or jfet's area.  A dev= target names it; the IR holds it there.  Phase 2c2
# (F3): E102's bound-expression check read r, c and l only, so `vcvs gain=g` swept with dev= failed at VACASK's run
# (exit 1, 'bound to an expression') instead of with status 2 here.
VALUE_PARAMS: Dict[str, str] = {"resistor": "r", "capacitor": "c", "inductor": "l", "vcvs": "gain", "vccs": "gm",
                                "ccvs": "rm", "cccs": "gain", "mutual_inductor": "coupling", "diode": "area",
                                "bjt": "area", "jfet": "area"}
_VALUE_KEY = VALUE_PARAMS


def _written_binding(it: Instance, param: str, tparam: str = "") -> object:
    """What an instance writes for a sweep or alter target, whatever its type (an Expr, a vector, a word), or None:
    Instance.value for the master's value parameter (VALUE_PARAMS), a source field as written (Source.spectre, Spectre's
    names), a Verilog-A parameter whatever its case, else the parameter under the name the alter or sweep writes (param),
    the target's engine spelling (tparam) or a name a master's rename gives it (vacask._target_expr's reading).  Phase 2c6
    (K1): 2c5 looked under param only, so a Verilog-A instance writing R=rv for an alter of r was never G1's."""
    tparam = tparam or param
    if VALUE_PARAMS.get(it.prim or "") == param:
        return it.value
    if it.kind in ("v", "i") and it.source is not None and param in it.source.spectre:
        return it.source.spectre[param]
    if it.kind == "y":
        names = {param.lower(), tparam.lower()}
        return next((v for k, v in it.params.items() if k.lower() in names), None)
    for name in (param, tparam):
        if name in it.params:
            return it.params[name]
    row = T.SPECTRE_MASTERS.get(it.prim or "") if it.kind != "x" else None
    for rr in (row.instance if row is not None else ()):
        if rr.action == "rename" and rr.value in (tparam, "instance_" + tparam) and rr.name in it.params:
            return it.params[rr.name]
    return None


def _bound_expr(it: Instance, param: str, tparam: str = "") -> Optional[Expr]:
    """The expression an instance parameter is bound to (_written_binding), None for a word, a vector or nothing."""
    v = _written_binding(it, param, tparam)
    return v if v is not None and not isinstance(v, (Str, list)) else None   # type: ignore[return-value]


# Phase 2c6 (K1): a waveform field a Source holds through another field's expression when the netlist leaves it out: a
# sine's sinedc, whose default is the dc (ref5 p.685; spectre.resolve_source's vo is the dc's Expr itself)
_SOURCE_DEFAULTS: Dict[str, Dict[str, Tuple[str, str]]] = {"sin": {"sinedc": ("vo", "dc")}}


def _source_default(src: Optional[Source], param: str) -> Tuple[object, str]:
    """(the expression, the field it is written as) of a source field the netlist does not write whose default reads
    another field (_SOURCE_DEFAULTS); (None, "") for every other field."""
    if src is None or param in src.spectre:
        return None, ""
    hit = _SOURCE_DEFAULTS.get(src.wave or "", {}).get(param)
    if hit is None:
        return None, ""
    return src.args.get(hit[0]), hit[1]


def _card_binding(ctx: _Ctx, card: Model, param: str, tparam: str) -> object:
    """What a card writes for a mod= target, or None (vacask._card_expr's reading, and the alter's own spelling): a
    Verilog-A card's parameter whatever its case, else the parameter under the alter's name, the target's engine spelling
    or a name a master's rename gives it.  Phase 2c6 (K1): bsim4's vtho is a rename of vth0 and both are rows (so are the
    diode's pb and vj): a card writing one spelling and an alter naming the other was never G1's."""
    from vamos.netlist import vacask as VK
    if VK.is_va_card(ctx.nl, card):
        names = {param.lower(), tparam.lower()}
        return next((v for k, v in card.params.items() if k.lower() in names), None)
    for name in (param, tparam):
        if name in card.params:
            return card.params[name]
    row = T.SPECTRE_MASTERS.get(card.prim or "")
    return next((card.params[rr.name] for rr in (row.params if row is not None else ())
                 if rr.action == "rename" and rr.value == tparam and rr.name in card.params), None)


def _noisy_binding(it: Instance) -> Optional[Expr]:
    """Phase 2c5 (R1): a resistor's isnoisy written as a word that is none of no, false, yes, true or a number names a
    parameter (spectre.py keeps a bare identifier there as a Str; vacask prints noisy=nflag, which VACASK re-evaluates
    as it re-evaluates every binding, §4.5 item 7): the parameter it reads, else None."""
    v = it.params.get("isnoisy") if it.kind == "r" else None
    if not isinstance(v, Str):
        return None
    word = v.text.strip()
    if word.lower() in ("no", "false", "yes", "true") or _number_text(word.lower()) is not None:
        return None
    return Name(word)


def vacask_hooked(nl: Netlist, target: Target, cache: Optional[Dict[str, List]] = None) -> bool:
    """H1 (phase 2c4): the VACASK deck prints this sweep or alter target through a switchable hook when the netlist
    binds it to an expression, (vamos_af<k> ? vamos_av<k> : expr): VACASK's re-evaluation of its bindings at each
    elaboration keeps the alter (G1's rule (b) and E151 do not apply) and a sweep sets vamos_av<k> (E102's refusal does
    not apply).  Every card parameter, X-line binding and instance parameter vacask.hookable accepts (vacask.hook_key on
    each resolution of the path); not isnoisy, the m of a kind whose multiplier is no device parameter, a behavioral
    source, a source field the parser derives another place from (a pulse's rise, an exp's delay).  cache (phase 2c5):
    vacask.path_resolutions' (plan.build's, _Ctx.resolutions)."""
    from vamos.netlist import vacask as VK
    return VK.hookable(nl, target, cache)


def _hook_printed(ctx: _Ctx, target: Target) -> bool:
    """R1 (phase 2c5): the VACASK deck prints this alter or sweep target through a switchable hook (vacask.prints_hook),
    so its alter is a var statement and its sweep a variable sweep with var statements around it: changes of VACASK's
    variables, after which VACASK re-evaluates every binding (G1's rule (b) counts them; 2c4 did not, and an earlier
    alter of a pulse's rise, of an isnoisy expression or of a model group lacking the parameter was lost).  False on
    Xyce."""
    return ctx.engine == "vacask" and _prints_hook_anywhere(ctx, target)


def _prints_hook_anywhere(ctx: _Ctx, target: Target) -> bool:
    """vacask.prints_hook whatever the engine (_hook_printed on VACASK; rule (a)'s message names what vamos's VACASK
    deck does, R4), once per target."""
    hit = ctx.hook_printed.get(target)
    if hit is None:
        from vamos.netlist import vacask as VK
        hit = ctx.hook_printed[target] = VK.prints_hook(ctx.nl, target, ctx.dependents, ctx.resolutions)
    return hit


def _control_source_check(ctx: _Ctx, origin: str, what: str, statement: str, path: str, src: Source) -> None:
    """R4 (phase 2c5): on VACASK a control-block statement about the source at path (statement: its "alter" or its
    "switch" around an analysis) leaves out every field that reads a name, temp or tnom, which the control block cannot
    read (`Variable or constant 'rt' not defined', exit 1, before); an error for a field it can neither print nor leave
    out (vacask.control_source_problems: a field derived from an altered field that the instance line does not print
    through a hook, which would keep its old value), each field once."""
    if ctx.engine != "vacask":
        return
    ent = ctx.insts.get(path)
    if ent is None:
        return
    from vamos.netlist import vacask as VK
    hooked = {k for k in src.spectre if _hook_printed(ctx, ("instance", path, k))}
    for key, e in VK.control_source_problems(src, _netlist_source(ctx, ent), hooked):
        if (path, key) not in ctx.ctl_reported:
            ctx.ctl_reported.add((path, key))
            ctx.err(origin, VK.control_source_message(what, statement, path, key, e))


def _hook_label(target: Target) -> str:
    """How a rule-(b) message names a hooked target whose VACASK variables changed (R1)."""
    kind, name, param = target
    what = name if kind == "variable" else "%s of %s%s" % (param, "model " if kind == "model" else "", name)
    return "%s, which the VACASK deck sets through a variable" % what


def _vacask_bound(ctx: _Ctx, ent: _Inst, param: str, origin: str, what: str, key: Optional[str] = None) -> bool:
    """VACASK cannot sweep an instance parameter bound to an expression that reads a parameter
    ("parameter 'r' of instance 'X2:R1' is bound to an expression. Failed to bind sweep parameters.",
    rc 0, no rawfile), while an alter of the same parameter works (alter instance("X2:R1") r=2000
    moved the node) [E, phase 1b]; Xyce sweeps it natively (.DC X2:R1:R, §7.2).  Phase 2c2 (F3): every
    master's value parameter (VALUE_PARAMS: a vcvs gain=g, a vccs gm, a ccvs rm, a cccs gain, a mutual
    inductor's coupling, a diode's area) and a source field (dc=vv) fail the same way, measured on VACASK
    0.3.4-91: exit 1, "Failed to bind sweep parameters".  Phase 2c4 (H1): the deck sweeps the switchable hook of
    such a parameter instead (vacask_hooked; key: the target's parameter, the engine spelling), so only what the
    hook cannot print stays refused.  Phase 2c6 (K1): so does a source field whose parameter on the VACASK instance
    line is derived from a bound one (a sine's sinedc, which defaults to dc=vv, printed sinedc=vv: exit 1 before)."""
    if ctx.engine != "vacask":
        return True
    if vacask_hooked(ctx.nl, ("instance", ent.path, key or param), ctx.resolutions):
        return True
    e = _bound_expr(ent.inst, param, key or param)
    names = _names(e) - {"temper", "$tnom"} if e is not None else set()
    if names:
        ctx.err(origin, "%s: on VACASK %s of %s is bound to the expression %s, which reads %s: VACASK cannot sweep "
                "such a parameter ('is bound to an expression'); sweep what it reads instead (sub= for a "
                "subckt parameter, param= for a netlist parameter) [E, phase 1b]"
                % (what, param, ent.path, _txt(e), ", ".join(sorted(names))))
        return False
    src = _netlist_source(ctx, ent) if ent.inst.kind in ("v", "i") and param != "m" and ent.inst.source else None
    if src is not None:
        # phase 2c6 (K1): VACASK sweeps the parameter its instance line prints, which may derive from another field (a
        # sine's sinedc from dc=vv, an exp's delay from td1=td): `parameter 'sinedc' of instance 'V1' is bound to an
        # expression', exit 1, before
        from vamos.netlist import vacask as VK
        key2 = VK.SOURCE_FIELDS.get(param, param)
        pe = (VK.printed_items(src) or {}).get(key2)
        if pe is not None and any(VK.reads_something(x) for x in _flat(pe)):
            ctx.err(origin, "%s: on VACASK %s of %s is printed on its instance line as %s=%s, which reads %s: VACASK "
                    "cannot sweep such a parameter ('is bound to an expression'); sweep what it reads instead (sub= for a "
                    "subckt parameter, param= for a netlist parameter) [E, phase 1b]"
                    % (what, param, ent.path, key2, _txt(_flat(pe) if isinstance(pe, list) else pe),
                       ", ".join(_reads_of(pe))))
            return False
    return True


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
        if it.kind == "x" and param != "m":
            # phase 2c (R-plan-3): a subckt instance's parameter is sub='s target, with its rules (the parameter
            # check, plan.overridden, the per-point condition and bin rule of §3.7)
            return _subckt_target(ctx, ent, name, param, origin, what)
        if it.kind == "y":
            va = ctx.nl.va_modules.get((it.prim or it.master or "").lower())
            if va is not None and not _va_declares(va, param):
                ctx.err(origin, "%s: %s is not a parameter of Verilog-A module %s (declared: %s)"
                        % (what, param, va.name, " ".join(va.params or []) or "none"))
                return None
        if it.kind in ("v", "i") and it.prim != "iprobe" and param != "m":
            fields = _source_fields(it.prim or ("vsource" if it.kind == "v" else "isource"))
            if fields and param not in fields:
                ctx.err(origin, "%s: %s is not a parameter of a %s (§3.8.1)" % (what, param, it.prim or it.kind))
                return None
            if not _multipath(ctx, ent, origin, what):
                return None
            wave_now = ctx.source_state.get(ent.path, (it.source.wave if it.source else None,))[0]
            if sweep and param == "delay" and wave_now == "exp":
                # phase 2c6 (K1): spectre.resolve_source folds an exp's delay into its td1 and td2, so VACASK's delay is the
                # absolute td1 and Xyce's deck holds no delay: a sweep set VACASK's td1 to the swept value and nothing on
                # Xyce (both wrong, status 0)
                ctx.err(origin, "%s: the delay of the exp source %s is folded into its td1 and td2 (§3.8.1), so a sweep "
                        "of it would set VACASK's absolute td1 and nothing in Xyce's deck: not supported in v1 (write "
                        "delay=<a netlist parameter> and sweep that with param=)" % (what, ent.path))
                return None
            if sweep and not _vacask_bound(ctx, ent, param, origin, what):
                return None                                 # phase 2c2 (F3): dc=vv swept with dev= on VACASK
            return _Resolved(("instance", ent.path, param), ent, source_field=True)
        if it.kind == "r" and not _resistor_target(ctx, ent, param, origin, what, sweep):
            return None
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
        if sweep and not _vacask_bound(ctx, ent, param, origin, what, key=p2):
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
                va = nl.va_modules.get((card.prim or "").lower())          # a Verilog-A card (R-plan-21)
                if va is not None and not _va_declares(va, param):
                    ctx.err(origin, "%s: %s is not a parameter of Verilog-A module %s (declared: %s)"
                            % (what, param, va.name, " ".join(va.params or []) or "none"))
                    return None
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
        return _subckt_target(ctx, ent, name, param, origin, what)
    ctx.err(origin, "%s: unknown target kind %r" % (what, kind))
    return None


def _subckt_target(ctx: _Ctx, ent: _Inst, name: str, param: str, origin: str, what: str) -> Optional[_Resolved]:
    """sub= (and dev= on a subckt instance): a parameter the subckt declares, one path on Xyce (§5.3),
    made overridable (RunPlan.overridden) and checked per point (_affects)."""
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


def constant_zero(v: object) -> bool:
    """G3 (phase 2c3): the constant 0, or an expression that reads no parameter, temp or tnom and evaluates to
    exactly 0 (r=1k-1k, r=2*0, r=-(1k-1k)).  Spectre makes such a resistor a short and Xyce had printed it as one
    ({1000-1000}: out = 1 V, R1:1 = 1 mA, phase 2c2's skeptic); F1 had refused it on both engines.  An expression
    that reads a name (r=0*rv) is not constant: F1's rule."""
    if isinstance(v, Num):
        return v.value == 0.0
    if v is None or isinstance(v, (Str, list, str, bool, int, float)):
        return False
    try:
        if X.names(v):                                    # type: ignore[arg-type]
            return False
        return X.evaluate(v, {}, dialect=DIALECT) == 0.0   # type: ignore[arg-type]  (temp/tnom: not a constant)
    except (X.EvalError, X.ExprError, TypeError, ValueError, ArithmeticError):
        return False


def is_short(it: Instance) -> bool:
    """§3.8, §4.5 item 10: a resistor whose r is the constant 0 prints as a short (VACASK a 0 V vsource, Xyce
    R=0); the emitters' test (vacask.rcl, xyce.rcl, signals.current), which plan.build's rules share.  Phase 2c3
    (G3): a constant expression equal to 0 (constant_zero) is the same short; signals.resolve folds it to the
    constant 0 in the render copy, so the emitters print the r=0 form."""
    return it.kind == "r" and constant_zero(it.value)


def noise_switch_off(value: object) -> Optional[bool]:
    """An alter's value of a resistor's isnoisy (§4.5 item 7, R-emit-6): True when it turns the noise off (the
    words no and false, any case, or the number 0), False when on (yes, true, another number), None when it
    is neither a word of the four nor a number."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return float(value) == 0.0
    text = _text_of(value)
    if text is None:
        return None
    low = text.strip().lower()
    if low in ("no", "false"):
        return True
    if low in ("yes", "true"):
        return False
    num = _number_text(low)
    return None if num is None else num == 0.0


def _resistor_target(ctx: _Ctx, ent: _Inst, param: str, origin: str, what: str, sweep: bool) -> bool:
    """Phase 2c2 (F2, R-plan-19): the dev= targets of a resistor that its printed form decides.
      - A resistor printed as a short (is_short) is a 0 V vsource on VACASK, which has none of the resistor's
        parameters ("Parameter 'r' not found"; its $mfactor would divide the current the short reports), and
        R=0 on Xyce, which Xyce makes a short when it reads the netlist (a .DC R1:R from 1k left b at 1 V,
        measured): a sweep of any of its parameters is an error in v1, and so is an alter on VACASK; Xyce's
        state replay renders the altered value into the next deck (an alter of r to 1k gave b = 0.5).
      - A sweep of isnoisy would switch the element per point (VACASK's noisy, Xyce's noise-free G form, §4.5
        item 7): an error in v1; an alter of it is mapped (vacask.alter_lines' noisy=, Xyce's state replay,
        signals' per-step noise lists and probes) and takes the four words or a number."""
    it = ent.inst
    if is_short(it) and (sweep or ctx.engine == "vacask"):
        ctx.err(origin, "%s: %s prints as a short (its r is the constant 0, §3.8, §4.5 item 10: a 0 V source on "
                "VACASK, R=0 on Xyce); %s of its %s is not supported in v1%s"
                % (what, ent.path, "a sweep" if sweep else "an alter", param,
                   "" if sweep else " on VACASK (Xyce runs it)"))
        return False
    if param == "isnoisy" and sweep:
        ctx.err(origin, "%s: a sweep of the isnoisy of %s is not supported in v1: the switch chooses the "
                "resistor's form (VACASK's noisy, Xyce's noise-free G form, §4.5 item 7) per deck, not per "
                "point; alter it between analyses instead" % (what, ent.path))
        return False
    return True


def _va_declares(va: Any, param: str) -> bool:
    """§3.9's rule for a Verilog-A instance parameter (spectre.va_params): declared, compared case-insensitively,
    or m; anything when the module's parameter list is unknown (VaModule.params None, §3.1)."""
    if va.params is None or param == "m":
        return True
    return param.lower() in {p.lower() for p in va.params}


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
        if r.inst is not None and r.inst.inst.kind == "r" and param == "r":
            return True                                    # F1 (phase 2c2): a resistance a sweep or alter sets
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
        if _sets_r(r, param):
            st.rset[r.inst.path] = value                  # G2: that resistor's r alone; F1 checks it (_zeros_of)
        else:
            st.inst[(id(r.inst.inst), param)] = (r.inst.inst, param, value)
            st.paths[(id(r.inst.inst), param)] = r.inst.path
    return st


def _sets_r(r: _Resolved, param: str) -> bool:
    """A dev= target of a resistor's r (G2, phase 2c3): its values change nothing a walk reads (no expression reads
    an instance parameter), only that resistor's F1 check."""
    return (r.target[0] == "instance" and r.inst is not None and r.subckt is None and not r.source_field
            and r.inst.inst.kind == "r" and param == "r")


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
    ctx.user_keys |= set(a.args)                           # D2
    for key in a.args:
        if key in SAVE_OPTIONS or key in OUTPUT_OPTIONS:
            ctx.warn(a.origin, "set %s: %s between analyses is not applied; the netlist's options statement decides "
                     "it for the whole run" % (a.name, key))
    before = ctx.base.key()
    ctx.base = ctx.base.copy()                              # the history keeps the states (F1, G1)
    for key, v in changed.items():
        ctx.current[key] = v
        if key == "temp":
            ctx.base.temp = float(v)                        # type: ignore[arg-type]
        elif key == "tnom":
            ctx.base.tnom = float(v)                        # type: ignore[arg-type]
    ctx.plan.actions.append(Action("options", dict(changed), None, a.origin))
    if ctx.base.key() != before:
        ctx.history.append((ctx.base, "set %s" % a.name))      # F1 (phase 2c2): _zero_cause
        _bound_base_change(ctx, "set %s" % a.name)              # G1 (phase 2c3)
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
    if tk == "instance" and r.inst is not None and r.inst.inst.kind == "r" and tparam == "isnoisy" \
            and noise_switch_off(value) is None:
        ctx.err(a.origin, "%s: isnoisy=%s of %s is not yes, no, true, false or a number (§4.5 item 7)"
                % (what, _fmt(value), r.inst.path))
        return
    # R1 (phase 2c5): the VACASK deck prints the alter of a hooked target as var statements (a source's alter instance
    # statement follows), which elaborate nothing; VACASK elaborates at its first alter instance/model (G1), after them
    hooked = isinstance(value, float) and _hook_printed(ctx, r.target)
    if tk in ("instance", "model") and not hooked:
        if ctx.elab is None:                               # G1: VACASK elaborates at its first alter instance/model
            ctx.elab = (ctx.base, len(ctx.marks))
            if ctx.engine == "vacask":                     # L4 (phase 2c7): and sets every source up, before the alter
                _sources_at(ctx, ctx.base, {}, "", "%s, VACASK's first elaboration" % what, "", elab=True)
    if tk in ("instance", "model", "variable"):
        ctx.bound = [b for b in ctx.bound if b.target != r.target]    # G1: this alter replaces an earlier one's value
    before = ctx.base.key()
    prev_states = dict(ctx.source_state)
    if r.source_field and r.inst is not None:
        src = r.inst.inst.source
        wave, dc_given, cur = ctx.source_state.get(r.inst.path, (src.wave if src else None, bool(src and src.dc is not None),
                                                                  src or Source()))
        ctx.src_alter[r.inst.path] = what                  # L4 (phase 2c7): the cause a constraint's message names
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
        notes: List[Note] = []
        new = _resolved_source(params, type_text, ctx.tran_stops, notes)     # phase 2c8 (M2): a given dc kept
        ctx.notes.extend(notes)
        new_wave = new.wave
        new_given = new.dc is not None
        ctx.source_state[r.inst.path] = (new_wave, new_given, new)
        act["source"] = new
        _control_source_check(ctx, a.origin, what, "alter", r.inst.path, new)     # R4 (phase 2c5)
    elif tk == "option":
        if not isinstance(value, float):
            ctx.err(a.origin, "%s: %s=%s is not a number" % (what, param, value))
            return
        ctx.current[tname] = value
        ctx.user_keys.add(tname)                           # D2
        ctx.base = _apply(ctx.base, r, param, value)
    elif tk == "variable":
        if not isinstance(value, float):
            ctx.err(a.origin, "%s: %s=%s is not a number" % (what, param, value))
            return
        ctx.base = _apply(ctx.base, r, param, value)
    elif isinstance(value, float) and (_affects(ctx, r, param) or _holder_m(r, param)):
        ctx.base = _apply(ctx.base, r, param, value)
    if tk == "instance" and r.inst is not None and isinstance(value, float) and _is_trise(r.inst, tparam):
        ctx.trise_now[r.inst.path] = value                 # R-plan-6: the device temperature from here on
    ctx.plan.actions.append(Action("alter", act, None, a.origin))
    hook_changed = False
    if hooked and ctx.base.hook(r.target) != (1.0, value):
        ctx.base = ctx.base.copy()                   # R1 (phase 2c5): the hook's var statement sets its flag and value
        ctx.base.hooks[r.target] = (1.0, float(value))   # type: ignore[arg-type]
        hook_changed = True
    if ctx.base.key() != before:
        ctx.history.append((ctx.base, what))         # F1 (phase 2c2): an analysis after it names it (_zero_cause)
    if ctx.base.key() != before or hook_changed:
        _bound_base_change(ctx, what)                # G1 (phase 2c3): what the alters in force read
    if hooked and r.source_field and ctx.elab is None:
        ctx.elab = (ctx.base, len(ctx.marks))        # R1: the source's alter instance, after its var, elaborates first
        if ctx.engine == "vacask" and r.inst is not None:
            # L4 (phase 2c7): the instance line then holds the source's previous fields and the hooked one's new value
            _sources_at(ctx, ctx.base, {r.inst.path: {param: float(value)}}, "",                # type: ignore[arg-type]
                        "%s, VACASK's first elaboration" % what, "", elab=True, states=prev_states)
    if tk in ("instance", "model", "variable"):
        # G1, and H2 for a netlist parameter (phase 2c4); phase 2c6 (K1): an alter of every value type, compared as it fits
        # (a vector elementwise, an isnoisy switch on and off: R1, phase 2c5) or refused at the first change (_cmp_of)
        v = value
        if tk == "instance" and tparam == "isnoisy" and noise_switch_off(value) is not None:
            v = 0.0 if noise_switch_off(value) else 1.0
        _bound_alter(ctx, a, r, param, v)
        if r.source_field and r.inst is not None:
            _bound_printed(ctx, a, r, act["source"])     # type: ignore[arg-type]  (K1: what VACASK's statement prints)
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
    label, desc, units = _labels(ctx, r.target, parent, spec.param)
    return SweepLevel(name, "", r.target, g, continuation, label, desc, units), r


def _constant_sine_sweeps(ctx: _Ctx, levels: List[Tuple[SweepLevel, _Resolved]], what: str, origin: str) -> None:
    """Phase 2c8 (M2): a sine whose freq is the constant 0 runs in tables.constant_sine's form, the sine itself at
    CONSTANT_SINE_FREQ, so a dev= sweep of its sinedc, ampl, sinephase or delay reaches the field on both engines (and of
    its freq, a real sine at a point other than 0 and the constant at a point of 0, printed as its own
    CONSTANT_SINE_FREQ: swept_points, _src_override); 2c7 printed the folded dc, every point at the nominal
    constant, status 0.  A point of its damp other than 0 makes it what the constant's rule refuses
    (spectre.resolve_source: 'a sine with freq=0 and damp != 0 is not a constant'), refused here in every analysis on
    both engines, once per sweep, unless a level sweeps its freq away from 0 at every point (a damped sine)."""
    for lvl, r in levels:
        if not (r.source_field and r.inst is not None and r.target[2] == "damp"):
            continue
        name = "sweep %s" % lvl.name if lvl.name else what
        if (name, r.target) in ctx.csine_reported:
            continue
        if not T.is_constant_sine(ctx.source_state.get(r.inst.path, (None, None, None))[2]):
            continue
        freqs = [grid_points(l2.spec) for l2, r2 in levels if r2.source_field and r2.inst is not None
                 and r2.inst.path == r.inst.path and r2.target[2] == "freq"]
        if freqs and not any(v == 0 for pts in freqs for v in pts):
            continue
        bad = next((v for v in grid_points(lvl.spec) if v != 0), None)
        if bad is not None:
            ctx.csine_reported.add((name, r.target))
            ctx.err(origin, "%s at %s=%s: the sine of %s has freq=0 and damp=%s: a sine with freq=0 and damp != 0 is not "
                    "a constant: give the frequency (§3.8.1)" % (name, lvl.desc, _fmt(bad), r.inst.path, _fmt(bad)))


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
        if "skipdc" in args and _enum(args, "skipdc", "?") not in ("no", "yes"):
            ctx.err(o, "%s: skipdc=%s is not one of no, yes [M ref19 p.44]" % (what, _fmt(args["skipdc"])))
        elif _enum(args, "skipdc") == "yes":
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
    if "skipdc" in args:                               # ref19 p.417; v1 runs no and yes (§0; phase 2c, R-plan-2)
        sk = _enum(args, "skipdc", "?")
        if sk not in TRAN_SKIPDC:
            ctx.err(o, "%s: skipdc=%s is not one of %s [M ref19 p.417]" % (what, _fmt(args["skipdc"]),
                                                                         ", ".join(TRAN_SKIPDC)))
        elif sk not in ("no", "yes"):
            ctx.err(o, "%s: skipdc=%s is not supported in v1, only no and yes are (§0; ref19 pp.428-429)" % (what, sk))
        else:
            args["skipdc"] = sk                       # the emitters read "yes"
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
    if ctx.engine == "xyce" and step.user_options & set(TRAN_TOLERANCE_KEYS):
        # D2: only for a tran whose tolerances or method the run set (a run that sets none prints none)
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
        args["p"] = _net_of(ctx, a.nodes[0])
        args["n"] = _net_of(ctx, a.nodes[1]) if len(a.nodes) > 1 else "0"


def _net_of(ctx: _Ctx, name: str) -> str:
    """An analysis's output node as its net: a port of a subckt instance (X1.y) follows the X line's binding,
    as oprobe's terminals do (phase 2c, R-plan-10); any other name stays as written."""
    at, sep, local = name.rpartition(".")
    xent = ctx.insts.get(at) if sep else None
    if xent is None or xent.inst.kind != "x":
        return name
    return _node_path(ctx, at, local)


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


def _user_options(ctx: _Ctx, a: Analysis, args: Mapping[str, Setting]) -> Set[str]:
    """D2: the option keys the run set before this step (ctx.user_keys), and a tran's own tolerance and
    method keys (TRAN_TOLERANCE_KEYS) the netlist or +paramdefault gave, errpreset from +errpreset/+aps."""
    out = set(ctx.user_keys)
    if a.kind == "tran":
        out |= {k for k in TRAN_TOLERANCE_KEYS if k in args}
        if ctx.settings.aps or ctx.settings.errpreset:
            out.add("errpreset")
    return out


def _state_write_path(ctx: _Ctx, name: str) -> str:
    """D9: where the flow writes a write=/writefinal= file (flow.state_path, §2.5): an absolute name as
    it is, a name without / into -outdir, else against the cwd."""
    if os.path.isabs(name):
        return os.path.normpath(name)
    if ctx.outdir and "/" not in name and os.sep not in name:
        return os.path.normpath(os.path.join(ctx.outdir, name))
    return os.path.normpath(os.path.join(ctx.cwd, name))


def _state_read_path(ctx: _Ctx, name: str) -> str:
    """D9: where the flow reads a readns=/readic=/read= file: against the cwd (§14 q.3)."""
    return os.path.normpath(name if os.path.isabs(name) else os.path.join(ctx.cwd, name))


def _state_files(ctx: _Ctx, what: str, origin: str, args: Dict[str, Setting]) -> None:
    """D9 (phase 2c): vamos reads every state file before the run, so a file an earlier analysis of the run
    writes would be a previous run's, or none: a readic=/read= of it is an error naming both analyses, a
    readns= of it a warning, and it is not read (the netlist's nodesets, as without readns=).  The
    analysis's own write comes after its reads: readns=x write=x on one analysis is §5.4's idiom."""
    for key in ("readic", "read", "readns"):
        f = args.get(key)
        if not isinstance(f, str) or not f:
            continue
        hit = ctx.state_writes.get(_state_read_path(ctx, f))
        if hit is None:
            continue
        wstep, wkey, wname = hit
        if key == "readns":
            ctx.warn(origin, "%s: readns=%s is the file %s writes earlier in this run (%s=%s): vamos reads state "
                     "files before the run, so it is not read and the netlist's nodesets apply (§5.4; chaining "
                     "the solution is phase 3)" % (what, f, wstep, wkey, wname))
            del args[key]
        else:
            ctx.err(origin, "%s: %s=%s is the file %s writes earlier in this run (%s=%s): vamos reads state "
                    "files before the run, so it would read a previous run's file or none; not supported in v1 "
                    "(§0, §5.4)" % (what, key, f, wstep, wkey, wname))
    for key in ("write", "writefinal"):
        f = args.get(key)
        if isinstance(f, str) and f:
            ctx.state_writes[_state_write_path(ctx, f)] = (what, key, f)


def _step(ctx: _Ctx, a: Analysis, context: List[Tuple[SweepLevel, _Resolved]], in_sweep: bool) -> None:
    what = "%s %s" % (a.kind, a.name)
    env = _step_env(ctx)
    args = _args(ctx, a, env, what)
    step = AnalysisStep(ctx.new_a(), a.name, a.kind)
    step.user_options = _user_options(ctx, a, args)
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
            # restart=yes, their default, restarts the DC solution from scratch when a condition changes [M ref19
            # p.45, p.184, p.441]: a parameter sweep has continuation 0 as a sweep block has (§5.2; phase 2c)
            cont = 1 if a.sweep.target == "freq" or restart_no else 0
            own = _level(ctx, a.sweep, "", a.origin, what, parent=False, continuation=cont)
            if a.sweep.target != "freq" and "freq" not in args:
                ctx.err(a.origin, "%s: a parameter sweep of an %s needs freq=, the fixed frequency" % (what, a.kind))
    elif a.sweep is not None:
        ctx.err(a.origin, "%s: a %s has no swept variable" % (what, a.kind))
    if own is not None:
        step.own = own[0]
        own[0].id = ctx.new_w()
        levels.append(own)
    _common_dispositions(ctx, a, step, args, in_sweep)
    if not in_sweep:                                     # inside a sweep block a state file is an error already
        _state_files(ctx, what, a.origin, args)
    if a.kind == "tran":
        _tran_dispositions(ctx, a, step, args)
    elif a.kind == "noise":
        _noise_dispositions(ctx, a, step, args)
    elif a.kind == "xf":
        _xf_dispositions(ctx, a, step, args)
    step.args = args
    step.options = dict(ctx.current)
    ctx.step_trise[step.id] = dict(ctx.trise_now)
    # §5.3 source states on VACASK: around a tran, and around an analysis sweeping a derived dc; the waveform
    # acts in tran (§3.8.1), so a tran is never bracketed by the dc state (phase 2c, R-emit-2)
    before: List[Action] = []
    after: List[Action] = []
    if a.kind == "tran":
        for lvl, r in levels:
            if r.source_field and r.inst is not None and lvl.target[2] == "dc":
                wave, _given, now = ctx.source_state.get(r.inst.path, (None, True, Source()))
                # phase 2c9 (N3): a sine whose sinedc defaults to dc follows the swept dc in the tran (unless the step
                # sweeps its sinedc too, which then is given at every point)
                follows = sinedc_follows_dc(now) and not any(l2.target == ("instance", r.inst.path, "sinedc")
                                                             for l2, _r2 in levels)
                if wave is not None and not follows:
                    ctx.note(a.origin, "%s: the swept dc of %s changes nothing in the transient: its %s waveform acts "
                             "in tran (§3.8.1)" % (what, r.inst.path, SPECTRE_TYPE.get(wave, wave)))
    if ctx.engine == "vacask":
        if a.kind == "tran":
            for path, (wave, given, src) in sorted(ctx.source_state.items()):
                if wave is not None and given:
                    before.append(Action("source", {"path": path, "type": SPECTRE_TYPE.get(wave, wave), "source": src}, None, a.origin))
                    after.append(Action("source", {"path": path, "type": "dc", "source": src}, None, a.origin))
        else:
            for lvl, r in levels:
                if r.source_field and r.inst is not None and lvl.target[2] == "dc":
                    wave, given, src = ctx.source_state.get(r.inst.path, (None, True, Source()))
                    if wave is not None and not given:
                        before.append(Action("source", {"path": r.inst.path, "type": "dc", "source": src}, None, a.origin))
                        after.append(Action("source", {"path": r.inst.path, "type": SPECTRE_TYPE.get(wave, wave), "source": src}, None, a.origin))
        for sw in before:                                # R4 (phase 2c5): what the switches print
            _control_source_check(ctx, a.origin, what, "switch", sw.args["path"], sw.args["source"])   # type: ignore[arg-type]
    ctx.plan.actions.extend(before)
    ctx.plan.actions.append(Action("analysis", {}, step, a.origin))
    ctx.plan.actions.extend(after)
    ctx.steps.append(step)
    _check_step_states(ctx, step, levels, what)
    _constant_sine_sweeps(ctx, levels, what, a.origin)   # M2 (phase 2c8)
    _check_sources(ctx, step, levels, what)              # L4 (phase 2c7)
    _bound_step(ctx, levels, what, a.origin)             # G1 (phase 2c3)


def _check_step_states(ctx: _Ctx, step: AnalysisStep, levels: List[Tuple[SweepLevel, _Resolved]], what: str) -> None:
    """§3.5, §3.7: the nominal state of the step, then every sweep point of the levels whose target
    can change a branch, a bin or an expression.  F1 (phase 2c2): the states the step runs in (its sweep
    points, else the nominal one) report the resistances that are 0 there.  G2 (phase 2c3): a level that sets
    a resistor's r is not walked per point (_check_r_levels)."""
    axes: List[List[Tuple[_Resolved, str, str, float]]] = []
    named: List[str] = []
    for lvl, r in levels:
        param = lvl.target[2] if lvl.target[0] != "option" else lvl.target[1]
        if not _affects(ctx, r, param):
            continue
        axes.append([(r, param, lvl.desc, v) for v in grid_points(lvl.spec)])
        if lvl.name:
            named.append("sweep %s" % lvl.name)
    _check_state(ctx, ctx.base, what, runs=not axes)
    if not axes:
        return
    sweeps = ", ".join(named)
    walked = [i for i, ax in enumerate(axes) if not _sets_r(ax[0][0], ax[0][1])]
    if len(walked) == len(axes):
        for combo in itertools.product(*axes):
            st = ctx.base
            for r, param, desc, v in combo:
                st = _apply(st, r, param, v)
            where = "%s at %s" % (what, ", ".join("%s=%s" % (desc, _fmt(v)) for _, _, desc, v in combo))
            _check_state(ctx, st, where, runs=True, sweeps=sweeps)
        return
    _check_r_levels(ctx, axes, walked, what, sweeps)


def _check_r_levels(ctx: _Ctx, axes: List[List[Tuple[_Resolved, str, str, float]]], walked: List[int], what: str,
                    sweeps: str) -> None:
    """G2 (phase 2c3): the points of a step some of whose levels set a resistor's r (_sets_r).  Such a value changes
    that resistor alone, so only the combinations of the other levels are walked; the F1 errors are those of the
    product of every level (2c2's walk of each point), each reported at its first point in product order: a walk's
    finding where no level sets that resistor (those levels at their first point), and a level's first point 0 of
    the resistor it sets (the last such level wins, as _apply does)."""
    last: Dict[str, int] = {}
    for i, ax in enumerate(axes):
        if i not in walked:
            last[ax[0][0].inst.path] = i                  # type: ignore[union-attr]
    first: Dict[str, Tuple[Tuple[int, ...], int, Tuple[str, str, str]]] = {}   # path -> (point, order, finding)

    def keep(idx: List[int], z: Tuple[str, str, str]) -> None:
        cand = (tuple(idx), ctx.walk_order.get(z[0], len(ctx.walk_order)), z)
        old = first.get(z[0])
        if old is None or cand[:2] < old[:2]:
            first[z[0]] = cand

    for widx in itertools.product(*[range(len(axes[i])) for i in walked]):
        idx = [0] * len(axes)
        st = ctx.base
        for i, j in zip(walked, widx):
            idx[i] = j
            r, param, _desc, v = axes[i][j]
            st = _apply(st, r, param, v)
        lead = st                                          # the point with the r levels at their first values
        for i in last.values():
            r, param, _desc, v = axes[i][0]
            lead = _apply(lead, r, param, v)
        _walk_state(ctx, lead, "%s at %s" % (what, ", ".join("%s=%s" % (axes[i][idx[i]][2], _fmt(axes[i][idx[i]][3]))
                                                              for i in range(len(axes)))))
        for z in _zeros_of(ctx, st):
            if z[0] not in last:
                keep(idx, z)
        for path, i in last.items():
            j = next((k for k, p in enumerate(axes[i]) if p[3] == 0.0), None)
            if j is not None:
                z = _zero_set(ctx, path)
                if z is not None:
                    keep(idx[:i] + [j] + idx[i + 1:], z)
    for idx_t, _order, z in sorted(first.values(), key=lambda c: c[:2]):
        where = "%s at %s" % (what, ", ".join("%s=%s" % (axes[i][j][2], _fmt(axes[i][j][3])) for i, j in enumerate(idx_t)))
        _report_zeros(ctx, [z], where, sweeps)


# -- G1 (phase 2c3): an alter of a parameter the netlist binds to an expression ------------------------------------

@dataclass
class _Bound:
    """An alter in force of an instance, subckt-instance, source-field or card parameter that the netlist binds to
    an expression reading a parameter, temp or tnom (r=rv, gain=g, dc=vv, m=mm, X1 rr=rv, a card's is=isv).

    Spectre's reading is not settled: ref19 p.54 ("changes the value of any modifiable component or netlist
    parameter for any analyses that follow"), p.55 (reelaborate=yes "triggers a related expression evaluation
    throughout the circuit"), p.222 (reelaborateonalter) and UG pp.190-191 ("The modifications apply to all analyses
    that follow the alter statement ... until you request another parameter modification") do not say whether
    the alter replaces the binding or a later change of what it reads re-evaluates it.  The engines differ
    (measured, X-follow2 m01-m27): Xyce's state replay prints the altered constant in every later deck;
    VACASK re-propagates every parameter its netlist binds to an expression at its next elaboration (an analysis or
    a sweep point) after a change of a variable, temp or tnom, and inside a subckt instance after a change of that
    instance's parameters (lib/cirparams.cpp's force, hierdevice.cpp's propagateParameters), whether or not the
    expression reads what changed.  So plan.build refuses, at the first analysis point where the readings or the
    engines differ: a change of what the expression reads after the alter on both engines (G1), and on VACASK any
    such change since its elaboration before the alter (Xyce, and Spectre under either reading, keep the alter).
    Phase 2c4: the VACASK deck prints a target vacask_hooked accepts through a switchable hook, which every
    re-evaluation keeps, so the second rule (with E151's first elaboration among its triggers) is left to the rest
    (H1); the first also applies to a netlist parameter defined by an expression and to a subckt default the X line
    leaves (H2), which both engines keep.
    Phase 2c6 (K1), default-deny: every alter of a target whose nominal value is bound to an expression, whatever the
    value's type, is a bound; its comparison fits the type (cmp: a vector elementwise, a pwl's wave; an isnoisy switch on
    and off) or, where vamos cannot compare (a word for an expression), every change refuses it ("opaque").  A field the
    netlist leaves to a bound default is one (a sine's sinedc and its dc, form "derived"), and so is, on VACASK, a field
    the control block's statement prints as a constant where the instance line derives it from fields it reads (item:
    rule (b) only, _bound_printed)."""
    alter: str                         # the alter statement's name
    origin: str
    target: Target
    param: str                         # as the alter names it
    obj: str                           # "R1", "X1.R1", "X1", "model dmod"; "" for a netlist parameter (H2)
    expr: Any                          # an Expr; a list of them for a vector (a pwl's wave; its printed (time, value) pairs)
    reads: Tuple[str, ...]             # the names it reads, temp and tnom as such
    value: Any                         # the alter's value: a float, a list of floats for a vector, else what it was ("opaque")
    scope: str                         # the instance path whose values evaluate it ("" at the top level)
    inputs0: Optional[Tuple] = None    # what it reads, at the alter
    q0: Optional[Tuple] = None         # what VACASK re-elaborates on, at its last elaboration before the alter
    changed: Optional[Tuple[str, str]] = None    # (the alter, set or sweep, the names) that changed what it reads
    vchanged: Optional[Tuple[str, str]] = None   # the same for what VACASK re-elaborates on
    # phase 2c4: VACASK keeps the alter whatever it re-elaborates on (H1's switchable hook, vacask_hooked; H2's netlist
    # parameter and subckt default, which VACASK keeps itself), so only rule (a) applies; the binding's form: "binding"
    # (G1's), "parameter" (H2, parameters rw=2*rv) or "default" (H2, a subckt default rr=q*1k the X line leaves)
    vacask_keeps: bool = False
    form: str = "binding"
    holder: str = ""                   # "default": the subckt whose default it is
    hooked: bool = False               # phase 2c5 (R4): vamos's VACASK deck prints the target through a hook (either
    #                                    engine's plan: rule (a)'s message names what that deck does)
    switch: bool = False               # phase 2c5 (R1): a resistor's isnoisy, compared on and off (0, 1 for the alter's
    #                                    no and yes; any other number is on)
    # phase 2c6 (K1): the comparison ("number", "switch", "list", "opaque"); a printed field's VACASK key (_bound_printed;
    # rule (b) only); the source fields' Expr objects its expression holds that the instance line prints through a hook,
    # with their targets (VACASK reads each as its hook's value while the hook's flag is set); the source state the
    # statement printed and its type, which give the field's intended value while a sweep sets one of those hooks
    cmp: str = "number"
    item: str = ""
    hooks: Tuple[Tuple[object, Target], ...] = ()
    src: Optional[Source] = None
    src_type: str = ""


def _flat(v: object) -> List[object]:
    """A value's leaves: the elements of a vector, a pwl's printed (time, value) pairs flattened; [v] otherwise."""
    if isinstance(v, (list, tuple)):
        out: List[object] = []
        for x in v:
            out.extend(_flat(x))
        return out
    return [v]


def _reads_of(e: object) -> Tuple[str, ...]:
    """The names an expression reads (every element of a vector's), temp (temper) and tnom ($tnom) included as such."""
    if isinstance(e, (list, tuple)):
        found: Set[str] = set()
        for x in _flat(e):
            found |= set(_reads_of(x))
        return tuple(sorted(found))
    try:
        out = set(X.names(e))
        walk = list(X.walk(e))
    except Exception:  # noqa: BLE001  (not an expression)
        return ()
    for n in walk:
        if isinstance(n, Name) and n.name == "temper":
            out.add("temp")
        elif isinstance(n, Name) and n.name == "$tnom":
            out.add("tnom")
    return tuple(sorted(out))


def _card_scope(ctx: _Ctx, card: Model) -> str:
    """The instance path whose values a card's expressions read: "" for a top-level card, else the first path
    whose subckt defines it."""
    if any(m is card for m in flat_items(ctx.nl.body)):
        return ""
    for pe in ctx.paths:
        if pe.subckt is not None and any(m is card for m in flat_items(pe.subckt.body)):
            return pe.path
    return ""


def _scope_env(ctx: _Ctx, st: _State, scope: str) -> Optional[Dict[str, float]]:
    """The parameter values on an instance path in a state (the top-level values for ""), None when they do not
    evaluate; cached per walk key."""
    key = (st.walk_key(), scope)
    if key in ctx.scope_envs:
        return ctx.scope_envs[key]
    env: Optional[Dict[str, float]] = None
    try:
        if not scope:
            env = _top_values(ctx, st, "", quiet=True)
        else:
            with _applied(ctx, st):
                top = _top_values(ctx, st, "", quiet=True)
                for pe in T.path_envs(ctx.nl, values=top, strict=False, dialect=DIALECT):
                    if pe.path == scope:
                        env = dict(pe.env)
                        break
    except (X.EvalError, T.TableError):
        env = None
    ctx.scope_envs[key] = env
    return env


def _bound_inputs(ctx: _Ctx, b: _Bound, st: _State) -> Optional[Tuple]:
    env = _scope_env(ctx, st, b.scope)
    if env is None:
        return None
    return tuple(st.temp if n == "temp" else st.tnom if n == "tnom" else env.get(n) for n in b.reads)


def _input_diff(ctx: _Ctx, b: _Bound, st: _State) -> str:
    """The names the bound expression reads whose values differ in st from those at the alter, "" for none."""
    now = _bound_inputs(ctx, b, st)
    if now is None or b.inputs0 is None:
        return ""
    return ", ".join(n for n, x, y in zip(b.reads, b.inputs0, now) if x != y)


def _holder_m(r: _Resolved, param: str) -> bool:
    """A target of a subckt instance's m: no walk reads it (tables.path_envs leaves m out of the X line's values),
    but VACASK propagates the instance's body again when its $mfactor changes (G1, q_holder_m_alter)."""
    return r.target[0] == "instance" and r.inst is not None and r.inst.inst.kind == "x" and param == "m"


def _holders(scope: str) -> List[str]:
    """The subckt instance paths that hold an instance path's body: "X0.X1" -> ["X0", "X0.X1"]."""
    parts = scope.split(".") if scope else []
    return [".".join(parts[:k]) for k in range(1, len(parts) + 1)]


def _vacask_sig(ctx: _Ctx, b: _Bound, st: _State) -> Tuple:
    """What VACASK re-elaborates such a binding on (G1): the variables' values, temp, tnom, and inside a subckt
    instance the values on its path and the m of every instance that holds it."""
    held: Tuple = ()
    mult: List[Tuple[str, Optional[float]]] = []
    if b.scope:
        env = _scope_env(ctx, st, b.scope)
        held = tuple(sorted(env.items())) if env is not None else ("?",)
        for p in _holders(b.scope):
            ent = ctx.insts.get(p)
            if ent is None or ent.inst.kind != "x":
                continue
            hit = st.inst.get((id(ent.inst), "m"))
            if hit is not None and st.paths.get((id(ent.inst), "m"), p) == p:
                mult.append((p, hit[2]))
                continue
            e = ent.inst.params.get("m")
            try:
                mult.append((p, 1.0 if e is None else _ev(_exprs(e)[0], ent.env.env, st.temp, st.tnom)))
            except (X.EvalError, IndexError):
                mult.append((p, None))
    return (tuple((v, st.top.get(v, nominal)) for v, nominal in sorted(ctx.variables.items())), st.temp, st.tnom,
            held, tuple(mult), tuple(sorted(st.hooks.items())) if st.hooks else ())   # R1 (phase 2c5): the hooks' vars


def _vacask_diff(ctx: _Ctx, b: _Bound, st: _State) -> str:
    """What changed for VACASK between its elaboration before the alter and st, "" for nothing."""
    q0, q1 = b.q0, _vacask_sig(ctx, b, st)
    if q0 is None or q0 == q1:
        return ""
    h0, h1 = dict(q0[5]), dict(q1[5])
    hooks = [t for t in sorted(set(h0) | set(h1)) if h0.get(t, _HOOK_OFF) != h1.get(t, _HOOK_OFF)]
    # a hooked variable's alter changes its value and its hook: named once, by the hook (phase 2c6; `changes rw, rw, ...')
    names = [v for (v, x), (_, y) in zip(q0[0], q1[0]) if x != y and ("variable", v, "") not in hooks]
    names += [n for n, x, y in (("temp", q0[1], q1[1]), ("tnom", q0[2], q1[2])) if x != y]
    names += [_hook_label(t) for t in hooks]
    m0, m1 = dict(q0[4]), dict(q1[4])
    mult = ["%s:m" % p for p in _holders(b.scope) if m0.get(p) != m1.get(p)]
    if not names and not mult and q0[3] != q1[3]:
        d0, d1 = dict(q0[3]) if q0[3] != ("?",) else {}, dict(q1[3]) if q1[3] != ("?",) else {}
        names = ["%s:%s" % (b.scope, k) for k in sorted(set(d0) | set(d1)) if d0.get(k) != d1.get(k)]
    return ", ".join(names + mult) or b.scope


def _bound_now(ctx: _Ctx, b: _Bound, st: _State) -> Any:
    """The value of a bound's expression in a state, as the engine re-evaluating it gets it: a float, a list of floats for
    a vector, None when it does not evaluate (the walk of the state reports that).  A printed field reads each source
    field it holds through a hook as the hook's value while the hook's flag is set (b.hooks, phase 2c6)."""
    env = _scope_env(ctx, st, b.scope)
    if env is None:
        return None
    e = b.expr
    if b.hooks:
        swap = {id(obj): Num(st.hook(t)[1]) for obj, t in b.hooks if st.hook(t)[0] == 1.0}
        if swap:
            from vamos.netlist import vacask as VK
            e = VK.swap(e, swap)
    try:
        if isinstance(e, (list, tuple)):
            return [_ev(x, env, st.temp, st.tnom) for x in _flat(e)]     # type: ignore[arg-type]
        return _ev(e, env, st.temp, st.tnom)
    except X.EvalError:
        return None


def _printed_want(ctx: _Ctx, b: _Bound, st: _State) -> Any:
    """Phase 2c6 (K1): the value a printed field (_bound_printed) is meant to have in st: the statement's constant, or,
    while a hooked sweep sets a field it derives from (st's hook value differs from the source state's), that field
    re-derived from the source state with the swept value (spectre.resolve_source, as the alter derived it): the sweep
    of an altered mag=mm sets the line's mag through its hook, which is the sweep's value, not the alter's.  None when
    it does not resolve."""
    over: Dict[str, object] = {}
    for _obj, t in b.hooks:
        flag, v = st.hook(t)
        cur = b.src.spectre.get(t[2]) if b.src is not None else None
        if flag == 1.0 and not (isinstance(cur, Num) and _close(cur.value, v)):
            over[t[2]] = Num(v)
    if not over:
        return b.value
    if b.src is None:
        return None
    from vamos.netlist import vacask as VK
    params: Dict[str, object] = dict(b.src.spectre)
    params.update(over)
    try:
        item = (VK.printed_items(_resolved_source(params, b.src_type, ctx.tran_stops, [])) or {}).get(b.item)
        if item is None:
            return None
        vals = [float(X.evaluate(x, {}, dialect=DIALECT)) for x in _flat(item)]     # type: ignore[arg-type]
    except (X.EvalError, X.ExprError, TypeError, ValueError, ArithmeticError):
        return None
    return vals if isinstance(item, list) else vals[0]


def _bound_differs(ctx: _Ctx, b: _Bound, st: _State) -> bool:
    """Phase 2c6 (K1): whether the bound's expression in st gives another value than the alter set, in its comparison: a
    number within _close, an isnoisy switch on or off, a vector element by element (another length differs); "opaque"
    (vamos cannot compare: a word for an expression, a scalar for a vector) differs always, so the first change refuses.
    A printed field compares with the value it is meant to have there (_printed_want)."""
    if b.cmp == "opaque":
        return True
    v = _bound_now(ctx, b, st)
    if v is None:
        return False
    want = _printed_want(ctx, b, st) if b.item else b.value
    if want is None:
        return True                                    # a printed field whose meant value vamos cannot derive
    if b.cmp == "list":
        return len(v) != len(want) or any(not _close(x, y) for x, y in zip(v, want))
    if b.cmp == "switch":
        return (v != 0.0) != (want != 0.0)
    return not _close(v, want)


def _cmp_of(e: object, value: object, switch: bool) -> str:
    """Phase 2c6 (K1): the comparison that fits an alter's value and the binding's type (_Bound.cmp)."""
    if isinstance(e, list):
        return "list" if isinstance(value, list) and all(isinstance(x, float) for x in value) else "opaque"
    if not isinstance(value, float):
        return "opaque"
    return "switch" if switch else "number"


def _bindings_of(ctx: _Ctx, r: _Resolved, param: str) -> List[Tuple[str, object, str, str, str]]:
    """Phase 2c6 (K1): the bindings of a sweep or alter target's nominal value, whatever their type: (what a message names,
    the Expr or vector, the instance path whose values evaluate it, the form, the holder).  An instance's or source's
    (_written_binding, every spelling; isnoisy=nflag, a word naming a parameter, R1 of phase 2c5; a source field the netlist
    leaves to a bound default, form "derived", the holder naming the field), a sub= target's subckt default the X line
    leaves (H2, form "default", the holder the subckt), each card's of a mod= target (_card_binding), a netlist parameter's
    (H2, form "parameter").  A word is no binding (an enumeration; VACASK prints it as a string)."""
    tk, tname, tparam = r.target
    out: List[Tuple[str, object, str, str, str]] = []
    if tk == "instance" and r.inst is not None:
        it = r.inst.inst
        e = _written_binding(it, param, tparam)
        form, holder = "binding", ""
        if e is None and r.source_field:
            e, holder = _source_default(it.source, param)
            form = "derived" if e is not None else form
        if param == "isnoisy" and (e is None or isinstance(e, Str)):
            e, form, holder = _noisy_binding(it), "binding", ""
        if e is not None and not isinstance(e, Str):
            out.append((r.inst.path, e, r.inst.path.rpartition(".")[0], form, holder))
        elif e is None and r.subckt is not None:           # H2: the subckt's default, which the X line leaves
            d = next((p.expr for p in list(r.subckt.params) + [p for p in r.subckt.body if isinstance(p, Param)]
                      if p.name == param), None)
            if d is not None and not isinstance(d, Str):
                out.append((r.inst.path, d, r.inst.path, "default", r.subckt.name))
    elif tk == "model":
        for card in r.cards:
            e = _card_binding(ctx, card, param, tparam)
            if e is not None and not isinstance(e, Str):
                out.append(("model %s" % tname, e, _card_scope(ctx, card), "binding", ""))
    elif tk == "variable":                                 # H2: a netlist parameter defined by an expression
        p = next((p for p in ctx.top_params if p.name == tname), None)
        if p is not None and not isinstance(p.expr, Str):
            out.append(("", p.expr, "", "parameter", ""))
    return out


def _bound_alter(ctx: _Ctx, a: Analysis, r: _Resolved, param: str, value: object) -> None:
    """G1: track an alter of a parameter the netlist binds to an expression that reads a name.  Phase 2c4: H2 tracks
    the same for a netlist parameter defined by an expression (parameters rw=2*rv; alter param=rw) and for a sub= target
    whose X line leaves it at its subckt default when that is an expression (rr=q*1k), rule (a) only (VACASK and Xyce
    keep both alters); H1 retires rule (b) for the targets VACASK prints through a switchable hook (vacask_hooked), and
    E151 (an alter before the first analysis with temp or tnom other than 27 at VACASK's first elaboration) is rule (b)'s
    trigger for the rest.  Phase 2c6 (K1), default-deny: every value type (2c5 tracked numbers only, so a pwl's wave was
    lost on VACASK after any change), every binding _bindings_of finds, each compared as _cmp_of says."""
    tk = r.target[0]
    for obj, e, scope, form, holder in _bindings_of(ctx, r, param):
        reads = _reads_of(e)
        if not reads:
            continue                                       # a constant: VACASK propagates expressions only (m11)
        b = _Bound(a.name, a.origin, r.target, param, obj, e, reads, value, scope, form=form, holder=holder)
        # vamos's VACASK deck prints such a binding through a hook where vacask.hookable accepts the target (it reads a
        # name); on Xyce b.hooked is found for rule (a)'s message only (_bound_runs; R4, phase 2c5)
        b.hooked = form in ("binding", "derived") and ctx.engine == "vacask" and vacask_hooked(ctx.nl, r.target,
                                                                                               ctx.resolutions)
        b.switch = tk == "instance" and param == "isnoisy"
        b.cmp = _cmp_of(e, value, b.switch)
        b.vacask_keeps = form not in ("binding", "derived") or b.hooked
        b.inputs0 = _bound_inputs(ctx, b, ctx.base)
        if b.inputs0 is None:
            continue                                       # it does not evaluate here: the walk reports it
        _vacask_track(ctx, b)
        ctx.bound.append(b)


def _vacask_track(ctx: _Ctx, b: _Bound) -> None:
    """Rule (b)'s start for a new bound (VACASK's, phase 2c5: not traced on Xyce): what VACASK re-elaborates on at its
    last elaboration before the alter, and a change since then that no elaboration has seen yet (m16, m19), or E151's."""
    if b.vacask_keeps or ctx.engine != "vacask":
        return
    est, mark = ctx.elab if ctx.elab is not None else (ctx.base, len(ctx.marks))
    b.q0 = _vacask_sig(ctx, b, est)
    for st, label in ctx.marks[mark:]:
        d = _vacask_diff(ctx, b, st)
        if d:
            b.vchanged = (label, d)
            break
    hot = [n for n in ("temp", "tnom") if getattr(est, n) != 27.0]
    if b.vchanged is None and not ctx.steps and hot:
        # E151 (2c3 skeptic): VACASK's first elaboration (this or an earlier alter instance/model, no analysis yet) leaves
        # ParametrizationAffectingOptionsChanged set when temp or tnom differ from its defaults, 27 (Circuit::elaborate
        # clears VariablesChanged only), so the first analysis propagates every binding
        b.vchanged = ("the %s in force at VACASK's first elaboration"
                      % " and ".join("%s=%s" % (n, _fmt(getattr(est, n))) for n in hot), ", ".join(hot))


def _bound_printed(ctx: _Ctx, a: Analysis, r: _Resolved, new: Source) -> None:
    """Phase 2c6 (K1): on VACASK a source alter is one control-block statement that prints every field of the source's
    new state reading no name, a field reading one left out (R4, phase 2c5), and VACASK keeps such a constant only until
    it re-evaluates the field's binding on the instance line (rule (b)'s changes).  G1's bound of the altered field covers
    a field the line binds to the alters' own expressions; every other field the statement prints as a constant where
    the line's binding reads a name is a bound of rule (b) here: a field the line derives through another field's hook
    (an exp whose td2 is hooked and whose constant td1 is altered prints td2=1.5e-06, where the line's
    (vamos_af1 ? vamos_av1 : t2) - 1e-06 gives 1e-06 after a re-evaluation), from a field not altered, or by an
    expression vamos cannot trace to the written fields.  Compared as VACASK re-evaluates it (b.hooks); the source's
    next statement replaces these bounds."""
    if ctx.engine != "vacask" or r.inst is None or r.inst.inst.source is None:
        return
    from vamos.netlist import vacask as VK
    path, base = r.inst.path, _netlist_source(ctx, r.inst)             # phase 2c8 (M2): the instance line's Source
    ctx.bound = [b for b in ctx.bound if not (b.item and b.target[1] == path)]
    was, now = VK.printed_items(base), VK.printed_items(new)
    if not was or not now:
        return
    written = [(k, o) for k, v in base.spectre.items() for o in _flat(v) if VK.reads_something(o)]
    cover = {id(o) for b in ctx.bound if b.target[:2] == ("instance", path) and not b.item and not b.vacask_keeps
             for o in _flat(b.expr)}
    for key, c in now.items():
        if any(VK.reads_something(x) for x in _flat(c)):
            continue                                   # left out of the statement, the instance's binding keeps it (R4)
        e = was.get(key)
        if e is None or not any(VK.reads_something(x) for x in _flat(e)):
            continue                                   # a constant or nothing on the instance line: VACASK keeps it
        deps = [(k, o) for k, o in written if any(VK.holds(x, o) for x in _flat(e))]
        rest = VK.swap(e, {id(o): Num(0.0) for _k, o in deps})
        if deps and not any(VK.reads_something(x) for x in _flat(rest)) and all(id(o) in cover for _k, o in deps):
            continue                                   # the altered fields' own bounds compare it
        try:
            values = [float(X.evaluate(x, {}, dialect=DIALECT)) for x in _flat(c)]    # type: ignore[arg-type]
        except (X.EvalError, X.ExprError, TypeError, ValueError, ArithmeticError):
            values = []
        b = _Bound(a.name, a.origin, r.target, r.target[2], path, e, _reads_of(e),
                   values if isinstance(c, list) else (values[0] if values else None), path.rpartition(".")[0],
                   item=key)
        b.cmp = ("list" if isinstance(c, list) else "number") if values else "opaque"
        b.hooks = tuple((o, ("instance", path, k)) for k, o in deps if _hook_printed(ctx, ("instance", path, k)))
        b.src = new
        b.src_type = _text_of(new.spectre.get("type")) or ("dc" if new.wave is None else SPECTRE_TYPE.get(new.wave, new.wave))
        b.inputs0 = _bound_inputs(ctx, b, ctx.base)
        if b.inputs0 is None:
            continue
        _vacask_track(ctx, b)
        ctx.bound.append(b)


def _bound_base_change(ctx: _Ctx, label: str) -> None:
    """G1: an alter or set statement changed the base state: what the alters in force read, and what VACASK
    re-elaborates on."""
    for b in ctx.bound:
        if b.changed is None and not b.item:                # a printed field (phase 2c6): rule (b) only
            d = _input_diff(ctx, b, ctx.base)
            if d:
                b.changed = (label, d)
        if b.vchanged is None and not b.vacask_keeps and ctx.engine == "vacask":
            d = _vacask_diff(ctx, b, ctx.base)
            if d:
                b.vchanged = (label, d)
    ctx.marks.append((ctx.base, label))


def _bound_step(ctx: _Ctx, levels: List[Tuple[SweepLevel, _Resolved]], what: str, origin: str) -> None:
    """G1: an analysis runs; it is VACASK's latest elaboration afterwards (a sweep restores its values).  R1 (phase
    2c5): the deck sets the flag of a hooked level that is off before the nest and resets it after (vacask's
    analysis_lines), a change of a VACASK variable after that elaboration, which the next analysis elaborates.  Phase 2c6
    (K1): rule (a) for the step's sweeps of a bound target (_sweep_bounds)."""
    if len(levels) > 1:
        _sweep_bounds(ctx, levels, what, origin)
    if ctx.bound:
        _bound_runs(ctx, levels, what)
    reset = [(lvl, r) for lvl, r in levels if _hook_printed(ctx, r.target) and ctx.base.hook(r.target)[0] == 0.0]
    if not reset:
        ctx.elab = (ctx.base, len(ctx.marks))
        return
    est = ctx.base.copy()
    for lvl, r in reset:
        # the sweep restored the value, not the flag; L3 (phase 2c7): the deck sets an instance's or a card's hook value to
        # the sweep's first point with its flag (vacask.analysis_lines), which the restore gives back
        first = grid_points(lvl.spec)[0] if r.target[0] in ("instance", "model") else ctx.base.hook(r.target)[1]
        est.hooks[r.target] = (1.0, first)
    ctx.elab = (est, len(ctx.marks))
    lvl = reset[0][0]                                    # the reset: a change after that elaboration (_bound_alter)
    ctx.marks.append((ctx.base, "sweep %s" % lvl.name if lvl.name else "%s (its sweep of %s)" % (what, lvl.desc)))


def _level_param(lvl: SweepLevel) -> str:
    return lvl.target[2] if lvl.target[0] != "option" else lvl.target[1]


def _point(ctx: _Ctx, st: _State, lvl: SweepLevel, r: _Resolved, v: float, hooks: bool) -> _State:
    """st at one point of a level; hooks (R1, phase 2c5): with the VACASK variables of its hook as the deck sweeps them."""
    st = _apply(st, r, _level_param(lvl), v)
    if hooks and _hook_printed(ctx, r.target):
        st.hooks[r.target] = (1.0, v)                    # _apply's copy
    return st


def _runs(ctx: _Ctx, levels: List[Tuple[SweepLevel, _Resolved]], what: str, hooks: bool
          ) -> List[Tuple[_State, str, List[Tuple[SweepLevel, _Resolved, float]]]]:
    """The points an analysis runs at, of the levels that change a name an expression can read (a variable, temp, tnom,
    a subckt instance's parameter) and with hooks (R1, phase 2c5) of the levels VACASK sweeps through a hook's variable,
    in VACASK's order: (the state, where, the point)."""
    rel = [(lvl, r) for lvl, r in levels if r.target[0] in ("variable", "option")
           or (r.target[0] == "instance" and r.inst is not None and r.inst.inst.kind == "x")
           or (hooks and _hook_printed(ctx, r.target))]
    runs: List[Tuple[_State, str, List[Tuple[SweepLevel, _Resolved, float]]]] = []
    if rel:
        axes = [[(lvl, r, v) for v in grid_points(lvl.spec)] for lvl, r in rel]
        for combo in itertools.product(*axes):
            st = ctx.base
            for lvl, r, v in combo:
                st = _point(ctx, st, lvl, r, v, hooks)
            runs.append((st, "%s at %s" % (what, ", ".join("%s=%s" % (lvl.desc, _fmt(v)) for lvl, _r, v in combo)),
                         list(combo)))
    else:
        runs.append((ctx.base, what, []))
    return runs


def _bound_runs(ctx: _Ctx, levels: List[Tuple[SweepLevel, _Resolved]], what: str) -> None:
    """Each alter in force is refused at the first point the analysis runs at (_runs) at or after a change where the
    expression's value differs from the alter's (_bound_message); rule (b) also sees the hooks' VACASK variables a
    hooked level sweeps (R1, phase 2c5)."""
    runs_by_rule = {"changed": _runs(ctx, levels, what, False)}
    if ctx.engine == "vacask" and any(_hook_printed(ctx, r.target) for _lvl, r in levels):
        runs_by_rule["vacask"] = _runs(ctx, levels, what, True)
    else:
        runs_by_rule["vacask"] = runs_by_rule["changed"]

    def changer(b: _Bound, combo: List[Tuple[SweepLevel, _Resolved, float]], diff: Any, hooks: bool) -> str:
        for lvl, r, v in combo:
            if diff(ctx, b, _point(ctx, ctx.base, lvl, r, v, hooks)):
                return "sweep %s" % lvl.name if lvl.name else "%s (its sweep of %s)" % (what, lvl.desc)
        lvl = combo[0][0] if combo else None
        return what if lvl is None else ("sweep %s" % lvl.name if lvl.name else "%s (its sweep of %s)" % (what, lvl.desc))

    for b in list(ctx.bound):
        for rule, attr, diff in (("changed", "changed", _input_diff), ("vacask", "vchanged", _vacask_diff)):
            if rule == "changed" and b.item:
                continue                                 # a printed field (phase 2c6): VACASK's rule (b) only
            if rule == "vacask" and (ctx.engine != "vacask" or b.vacask_keeps):
                break
            runs = runs_by_rule[rule]
            start = 0 if getattr(b, attr) is not None else None
            if start is None:
                for k, (st, _where, combo) in enumerate(runs):
                    d = diff(ctx, b, st)
                    if d:
                        start = k
                        setattr(b, attr, (changer(b, combo, diff, rule == "vacask"), d))
                        break
            if start is None:
                continue
            hit = next((w for st, w, _c in runs[start:] if _bound_differs(ctx, b, st)), None)
            if hit is not None:
                if rule == "changed" and b.form in ("binding", "derived") and ctx.engine != "vacask":
                    b.hooked = vacask_hooked(ctx.nl, b.target, ctx.resolutions)   # R4: what vamos's VACASK deck does
                ctx.err(b.origin, _bound_message(b, rule, hit))
                ctx.bound.remove(b)
                break


def _btxt(e: object) -> str:
    """A bound's expression for a message: a vector (a pwl's written wave, its printed (time, value) pairs) flattened."""
    return _txt(_flat(e) if isinstance(e, (list, tuple)) else e)


def _setting_text(v: object) -> str:
    """An alter's value for a message: a number as _fmt prints it, a vector of them in brackets, a word as it is."""
    if isinstance(v, list):
        return "[%s]" % " ".join(_fmt(x) for x in v)
    return _fmt(v)


def _binding_clause(b: _Bound) -> str:
    """How a G1 message names a bound's binding (phase 2c6: a field's bound default in its own words)."""
    if b.form == "default":
        return "which subckt %s binds to the default %s" % (b.holder, _btxt(b.expr))
    if b.form == "derived":
        return "whose default, the %s, the netlist binds to the expression %s" % (b.holder, _btxt(b.expr))
    return "which the netlist binds to the expression %s" % _btxt(b.expr)


def _bound_message(b: _Bound, rule: str, where: str) -> str:
    if b.item:                                         # phase 2c6 (K1): a field VACASK's statement prints (_bound_printed)
        head = ("%s: on VACASK, alter %s set %s of %s, so the control block's statement of %s prints %s=%s, which its "
                "instance line binds to the expression %s (it reads %s)"
                % (where, b.alter, b.param, b.obj, b.obj, b.item, _setting_text(b.value), _btxt(b.expr),
                   ", ".join(b.reads)))
    else:
        what = "%s of %s" % (b.param, b.obj) if b.obj else b.param
        head = "%s: %salter %s set %s, %s (it reads %s)" % (
            where, "on VACASK, " if rule == "vacask" else "", b.alter, what, _binding_clause(b), ", ".join(b.reads))
    if rule == "changed":
        label, names = b.changed or ("", "")
        # G1: VACASK's own reading re-evaluates the binding; vamos's VACASK deck keeps the alter of a target it prints
        # through a hook (H1; phase 2c5, R4: the message said `VACASK re-evaluates it' there too); H2's netlist
        # parameter and subckt default keep the alter on both engines
        engines = ("VACASK and Xyce keep the alter" if b.form not in ("binding", "derived") or b.hooked
                   else "VACASK re-evaluates it, Xyce keeps the alter")
        return head + (", and %s changes %s after the alter: whether Spectre keeps the altered value or re-evaluates the "
                       "expression is not known (%s): not supported in v1 (§5.3; alter what the expression reads "
                       "instead)" % (label, names, engines))
    label, names = b.vchanged or ("", "")
    holder = " or of a parameter of the subckt instance %s that holds it" % b.scope if b.scope else ""
    lost = ("so the alter's value is lost (Xyce keeps it, as Spectre does while nothing the expression reads changes)"
            if b.cmp != "opaque" else
            "so the alter's value can be lost (vamos cannot compare the alter's value %s with it; Xyce keeps it)"
            % _setting_text(b.value))
    return head + ("; %s changes %s, and VACASK re-evaluates such a binding at the next analysis after a change of a "
                   "netlist parameter, temp or tnom%s, %s: not supported on VACASK in v1 (Xyce runs it)"
                   % (label, names, holder, lost))


def _sweep_bounds(ctx: _Ctx, levels: List[Tuple[SweepLevel, _Resolved]], what: str, origin: str) -> None:
    """Phase 2c6 (K1): G1's rule (a) for a sweep.  A sweep that sets a target whose nominal value is bound to an
    expression reading a name (_bindings_of), around a sweep that changes a name the expression reads, asks rule (a)'s
    question (does Spectre keep the swept value or re-evaluate the expression?), and the engines answer it differently:
    VACASK keeps the swept value (its hook), Xyce's .STEP of the target under a .DC of what it reads re-evaluated the
    expression at some points (an X-line rr=rv swept 1k, 3k around rv 1k, 2k gave out 0.5, 0.333 at the first point where
    VACASK gave 0.5, 0.5; a source's dc=vv the same; a resistor reading temp under a temp sweep at every point), status
    0 on both.  Refused on both engines at the first point where the expression's value differs from the swept one; a
    sweep of a name inside a sweep of the target sets the target at each point, which leaves no doubt."""
    rel = [k for k, (_lvl, r) in enumerate(levels) if r.target[0] in ("variable", "option")
           or (r.target[0] == "instance" and r.inst is not None and r.inst.inst.kind == "x")]
    for i, (lvl, r) in enumerate(levels):
        if not any(k > i for k in rel) or r.target[0] not in ("instance", "model", "variable"):
            continue
        param = r.target[1] if r.target[0] == "variable" else lvl.desc.rpartition(":")[2]
        for obj, e, scope, form, holder in _bindings_of(ctx, r, param):
            reads = _reads_of(e)
            if not reads or isinstance(e, list):
                continue
            probe = _Bound(lvl.name or what, origin, r.target, param, obj, e, reads, 0.0, scope, form=form, holder=holder)
            idx = sorted(set(rel) | {i})
            axes = [[(k, v) for v in grid_points(levels[k][0].spec)] for k in idx]
            for combo in itertools.product(*axes):
                s0 = s = ctx.base
                for k, v in combo:
                    lk, rk = levels[k]
                    s = _apply(s, rk, _level_param(lk), v)
                    if k <= i:
                        s0 = _apply(s0, rk, _level_param(lk), v)
                    if k == i:
                        probe.value = v
                i0, i1 = _bound_inputs(ctx, probe, s0), _bound_inputs(ctx, probe, s)
                if i0 is None or i1 is None or i0 == i1 or not _bound_differs(ctx, probe, s):
                    continue
                inner = next((levels[k][0] for k, v in combo if k > i
                              and _bound_inputs(ctx, probe, _apply(s0, levels[k][1], _level_param(levels[k][0]), v)) != i0),
                             levels[[k for k in idx if k > i][0]][0])
                at = "%s at %s" % (what, ", ".join("%s=%s" % (levels[k][0].desc, _fmt(v)) for k, v in combo))
                names = ", ".join(n for n, x, y in zip(reads, i0, i1) if x != y)
                obj_what = "%s of %s" % (param, obj) if obj else param
                ctx.err(origin, "%s: sweep %s sets %s, %s (it reads %s), and %s changes %s inside it: whether Spectre "
                        "keeps the swept value or re-evaluates the expression is not known (VACASK keeps it, Xyce "
                        "re-evaluates the expression at some points): not supported in v1 (§5.3; sweep what the "
                        "expression reads instead)"
                        % (at, lvl.name, obj_what, _binding_clause(probe), ", ".join(reads),
                           "sweep %s" % inner.name if inner.name else "%s (its sweep of %s)" % (what, inner.desc), names))
                return


# -- phase 2c7 (L4): the source constraints in every state an engine sets a source up in -------------------------------

# (on both engines, the rule's words up to its citation's end; VACASK's setup message) per constraint.  §3.8.1 states the
# pulse period and the pwl times (spectre.resolve_source checked them for constant fields only), the sine of freq 0
# and damp != 0; a negative rise and an exp's td2 no later than its td1 are refused on both engines too (Xyce would run
# them, a step for the rise and a fall before the rise for the exp, where Spectre's reading is not known); the rest
# VACASK alone refuses (lib/devvisrc.cpp's sourceSetup): a sine's freq not above 0 (Spectre's freq=0 is a constant,
# §3.8.1), a pwl's first time point before 0 or a single point.  Spectre's manual states no pulse constraint beyond
# the fields' meanings (ref5 pp.682-686: rise and fall the transitions, width the duration of val1, period the
# waveform's); §3.8.1's period rule is vamos's reading of them, and VACASK's own.  VACASK sets a source up at its first
# elaboration and at every analysis and sweep point, with the waveform's type in a tran and, outside one, where its dc
# is derived (a given dc prints type="dc", §5.3).
#
# Phase 2c8 (M1, M3, M4): every field whose constant value vamos refuses (spectre.resolve_source at the parse or the
# alter, tables.wave at the render) is refused in every state on both engines, with the constant's rule: a negative
# fall (S-2c7's N07, N10, N12, N15, S0290: VACASK stayed at val1, Xyce ended the plateau early, status 0 on both), a
# negative freq (VACASK's alone before, `(Xyce runs it)', where Xyce refused the constant at render and ran a sweep to
# it), a pwl time point before 0 and a single pwl point (VACASK's alone before); so are a negative width (vamos ran the
# constant, and the engines made different waveforms of it) and an exp time constant of 0 (VACASK stopped with NaN,
# Xyce made a step; ref5 p.685 gives no exp formula that defines it).  A state is refused wherever the engines use its
# waveform: in a tran, and in an analysis whose dc is derived, the waveform's value at t = 0, which both take from it
# (VACASK sets it up, Xyce's DC is tables.wave_t0's; P04's period, VACASK's alone in 2c7, while Xyce ran it); a given
# dc uses no waveform outside a tran.  A sine's freq of 0 stays VACASK's alone: Spectre's is a constant, which Xyce's
# SIN runs.  Per constraint: (on both engines; the rule's words up to its citation's end; VACASK's setup message, or
# for a rule VACASK does not check at its setup what the engines would do; VACASK checks it at its setup, so VACASK
# refuses it at its first elaboration too, and a VACASK-only rule wherever it sets the waveform up; the constant is
# refused whatever runs, at the parse, the alter or the render (the new rules here: by plan.build), so a state whose
# violating fields are constants is refused on both engines in every analysis it runs in, with this rule's words).
_SRC_RULES: Dict[str, Tuple[bool, str, str, bool, bool]] = {
    "period": (True, "an explicit period no longer than rise+fall+width is an error (§3.8.1",
               "Period of pulse transient must be greater than rise+fall+width", True, True),
    "period0": (True, "an explicit period no longer than rise+fall+width is an error (§3.8.1",
                "VACASK would run a period of 0 or less as a single pulse", False, True),
    "rise": (True, "a negative rise is an error (§3.8.1", "Rise time of pulse transient must be grater than 0", True, True),
    "fall": (True, "a negative fall is an error (§3.8.1", "VACASK would stay at val1, as it reads a fall at or below 0 as "
                                                          "none, and Xyce would end val1 early", False, True),
    "width": (True, "a negative width is an error in v1 (Spectre's reading is not known",
              "VACASK and Xyce would make different waveforms of it", False, True),
    "damp": (True, "a sine with freq=0 and damp != 0 is not a constant: give the frequency (§3.8.1",
             "Frequency of sinusoidal transient must be greater than 0", True, True),
    "freq": (True, "a sine with a negative freq is an error in v1, as the constant is (Spectre's reading is not known",
             "Frequency of sinusoidal transient must be greater than 0", True, True),
    "freq0": (False, "", "Frequency of sinusoidal transient must be greater than 0", True, False),
    "td2": (True, "an exp whose td2 is not later than its td1 is an error in v1 (Spectre's reading is not known: Xyce would "
                  "start the fall before the rise", "Parameter td2 of exponential transient must be greater than 0", True,
            True),
    "tau1": (True, "an exp time constant of 0 is an error in v1 (ref5 p.685 gives no exp formula that defines it",
             "VACASK would divide by it at the exp's start and stop with 'NaN found in vector', and Xyce would make a "
             "step or, for some start times, stop with 'Time step too small'",
             False, True),
    # phase 2c9 (N5): tau2's own clause, measured (VACASK 0.3.4-91: `NaN found in vector' at delay + td2; Xyce: `Time
    # step too small near step number ...', exit 1); 2c8 gave it tau1's
    "tau2": (True, "an exp time constant of 0 is an error in v1 (ref5 p.685 gives no exp formula that defines it",
             "VACASK would divide by it at the fall's start, delay + td2, and stop with 'NaN found in vector', and Xyce "
             "would stop with 'Time step too small'", False, True),
    "times": (True, "pwl time points must increase (§3.8.1", "Pwl scale is not strictly monotonic", True, True),
    "first": (True, "a pwl time point before 0 is an error, as the constant is (§3.8.1", "First timepoint must be >=0",
              True, True),
    "points": (True, "a pwl needs two or more time points, as the constant does (§3.8.1",
               "Pwl waveform needs at least two points", True, True),
}
_SRC_NOTE = " (it sets the waveform up in this analysis too: its dc is the waveform's value at t = 0)"
# the Source fields each constraint reads (a pwl's: its time points), for the constants the render refuses
_SRC_RULE_ARGS: Dict[str, Tuple[str, ...]] = {
    "period": ("per", "tr", "tf", "pw"), "period0": ("per", "tr", "tf", "pw"), "rise": ("tr",), "fall": ("tf",),
    "width": ("pw",), "damp": ("freq", "theta"), "freq": ("freq",), "freq0": ("freq",), "td2": ("td1", "td2"),
    "tau1": ("tau1",), "tau2": ("tau2",), "times": (), "first": (), "points": ()}


def edge_default(nl: Netlist, values: Optional[Mapping[str, float]] = None) -> float:
    """Phase 2c8: Spectre's default pulse edge, transres = 1e-9 x the shortest tran stop (§3.8.1, the stops _Ctx.elaborate
    and spectre.resolve_source read, at the netlist's values; values: more of them, a render copy's plan variables),
    which a rise or a fall of 0 is in every value that reaches an engine: the emitters print it for a dev= sweep's point
    of 0 (the raw 0 before: VACASK read a fall of 0 as none, the pulse staying at val1, and stopped at a rise of 0;
    Xyce made a step)."""
    from vamos.netlist import spectre as SP
    env: Dict[str, float] = dict(nl.values)
    env.update(values or {})
    stops: List[float] = []
    for a in flat_analyses(nl.analyses):
        if a.kind == "tran" and "stop" in a.args:
            try:
                stops.append(_ev(_exprs(a.args["stop"])[0], env, 27.0, 27.0))
            except (X.EvalError, IndexError):
                pass
    return SP._TRANSRES * (min(stops) if stops else 1.0)


def swept_value(param: str, src: Optional[Source], v: float, transres: float) -> float:
    """Phase 2c9 (N1): the one predicate of the value an engine is given for a dev= sweep point v of the Spectre field
    `param` of an independent source whose state, at the statement being printed, is src (the Source the alters so far
    made, plan.build's source state; VACASK's deck tracks it from the alter and source actions, Xyce's replays it): a
    rise or a fall of 0 of a pulse is Spectre's default edge, transres (edge_default; §3.8.1: an alter or sweep value of
    0 becomes transres; VACASK read a fall of 0 as none and stopped at a rise of 0), and a freq of 0 of the sine whose
    freq is the constant 0 (tables.constant_sine) the constant's own CONSTANT_SINE_FREQ (VACASK stops at a sine of freq
    0); any other value, field or state is v.  2c8 decided it for VACASK's deck from the instance line, so a sine an
    alter made the constant sine printed its freq of 0 raw and VACASK stopped (S-2c8's P873, Q05)."""
    if v == 0.0 and src is not None:
        if param in ("rise", "fall") and src.wave == "pulse":
            return transres
        if param == "freq" and T.is_constant_sine(src):
            return T.CONSTANT_SINE_FREQ
    return v


def swept_points(param: str, src: Optional[Source], spec: SweepSpec, transres: float) -> Optional[List[float]]:
    """Phase 2c9 (N1): a dev= sweep of a source field as the engines run it, each point through swept_value in the
    source state src; None when no point changes (the grid prints as it is).  The results keep the plan's own points
    (results.collect writes a substituted point back as the deck's 0, N2)."""
    pts = grid_points(spec)
    out = [swept_value(param, src, v, transres) for v in pts]
    return out if any(a != b for a, b in zip(out, pts)) else None


def _netlist_source(ctx: _Ctx, ent: _Inst) -> Source:
    """Phase 2c8 (M2): the Source a source's instance line prints, the render copy's (signals.resolve): a sine whose freq
    is the constant 0 in tables.constant_sine's form (its given dc, the sine itself at 1e-30 Hz, its constant), the
    IR's Source otherwise; one object per instance (the source states and the L4 caches key on its identity)."""
    got = ctx.netlist_src.get(id(ent.inst))
    if got is None:
        src = ent.inst.source or Source()
        got = ctx.netlist_src[id(ent.inst)] = T.constant_sine(src) or src
    return got


def _resolved_source(params: Mapping[str, object], type_text: str, tran_stops: Sequence[float],
                     notes: List[Note]) -> Source:
    """spectre.resolve_source, a sine of constant freq 0 in tables.constant_sine's form (phase 2c8, M2: an alter's or a
    sweep point's source keeps its given dc, as the netlist's does)."""
    from vamos.netlist import spectre as SP
    src = SP.resolve_source(params, type_text, tran_stops, notes)       # type: ignore[arg-type]
    return T.constant_sine(src) or src


def _src_ents(ctx: _Ctx) -> List[_Inst]:
    """The independent sources on the instance paths (not an iprobe, not a bridge's code source)."""
    got = getattr(ctx, "_src_list", None)
    if got is None:
        got = [ent for ent in ctx.insts.values() if ent.inst.kind in ("v", "i") and ent.inst.source is not None
               and ent.inst.prim != "iprobe" and ent.inst.source.code_uri is None]
        ctx._src_list = got                              # type: ignore[attr-defined]
    return got


def _src_state(ctx: _Ctx, ent: _Inst, over: Optional[Mapping[str, float]] = None,
               states: Optional[Mapping[str, Tuple[Optional[str], bool, Source]]] = None
               ) -> Tuple[Optional[str], bool, Source]:
    """(its waveform, its dc given, the Source) of a source in the plan's source state, with the fields a sweep point
    sets (over: Spectre field -> value) re-resolved as an alter re-resolves them (spectre.resolve_source)."""
    src0 = ent.inst.source or Source()
    wave, given, src = (states or ctx.source_state).get(ent.path, (src0.wave, src0.dc is not None, src0))
    if over:
        src = _src_override(ctx, wave, src, over)
        given = src.dc is not None
    return wave, given, src


# L4: the Source field a sweep point sets, per Spectre field (spectre.resolve_source's table, §3.8.1): the waveform keeps its
# type at every point, as the engines' sweeps keep it (a sine swept to freq=0 stays a sine on VACASK and stops it)
_SWEPT_ARG: Dict[str, Dict[str, str]] = {
    "pulse": {"val0": "v1", "val1": "v2", "delay": "td", "rise": "tr", "fall": "tf", "width": "pw", "period": "per"},
    "sin": {"sinedc": "vo", "ampl": "va", "freq": "freq", "delay": "td", "damp": "theta", "sinephase": "phase"},
    "exp": {"val0": "v1", "val1": "v2", "td1": "td1", "tau1": "tau1", "td2": "td2", "tau2": "tau2"},
    "pwl": {"delay": "td"},
}


def _transres(ctx: _Ctx) -> float:
    """Spectre's default pulse edge, 1e-9 x the shortest tran stop (spectre.resolve_source's; edge_default's value)."""
    from vamos.netlist import spectre as SP
    return SP._TRANSRES * (min(ctx.tran_stops) if ctx.tran_stops else 1.0)


def sinedc_follows_dc(src: Optional[Source]) -> bool:
    """Phase 2c9 (N3): src is a sine whose sinedc the netlist leaves to its default, the dc (ref5 p.684; §3.8.1's one
    field whose default is another field: spectre.resolve_source's vo is the dc), so that a sweep of its dc moves its
    sinedc too, as an alter of dc does (resolve_source re-resolves it).  A SPICE-mode SIN writes its VO as sinedc."""
    return src is not None and src.wave == "sin" and "sinedc" not in src.spectre


def _src_override(ctx: _Ctx, wave: Optional[str], src: Source, over: Mapping[str, float]) -> Source:
    """L4 (phase 2c7): src at a sweep point that sets some of its fields (Spectre field -> value): a copy whose resolved
    field takes the value (a rise or fall of 0 the edge default, an exp's td1 or td2 after its delay), Source.spectre
    too; a field outside _SWEPT_ARG re-resolves the source (spectre.resolve_source), as an alter does.  Phase 2c9: the
    value is the engines' (swept_value: N1); a swept dc moves a sinedc that defaults to it (sinedc_follows_dc: N3)."""
    table = _SWEPT_ARG.get(wave or "", {})
    out = Source(dc=src.dc, ac=src.ac, wave=src.wave, args=dict(src.args), points=list(src.points),
                 code_uri=src.code_uri, spectre=dict(src.spectre))
    rest: Dict[str, float] = {}
    transres = _transres(ctx)
    for k, v in over.items():
        out.spectre[k] = Num(v)
        if k == "dc":
            out.dc = Num(v)
            if sinedc_follows_dc(src) and "sinedc" not in over:
                out.args["vo"] = Num(v)                         # phase 2c9 (N3): sinedc defaults to dc
        elif k in ("mag", "phase"):
            ac = list(out.ac or (Num(0.0), Num(0.0)))            # ref5 p.685: mag defaults to 0
            ac[0 if k == "mag" else 1] = Num(v)
            out.ac = (ac[0], ac[1])
        elif k in table:
            arg = table[k]
            dl = src.spectre.get("delay") if wave == "exp" and arg in ("td1", "td2") else None
            sv = swept_value(k, src, v, transres)               # phase 2c9 (N1): the state's own substitution
            if sv != v:
                out.args[arg] = Num(sv)
            elif dl is None or isinstance(dl, (Str, list)):
                out.args[arg] = Num(v)
            elif isinstance(dl, Num):
                out.args[arg] = Num(dl.value + v)              # spectre.resolve_source's delay + td1, delay + td2
            else:
                out.args[arg] = Binary("+", dl, Num(v))
        else:
            rest[k] = v
    if rest:
        params: Dict[str, object] = dict(out.spectre)
        type_text = _text_of(params.get("type")) or ("dc" if wave is None else SPECTRE_TYPE.get(wave, wave))
        out = _resolved_source(params, type_text, ctx.tran_stops, [])
    return out


def _src_reads(src: Source, ctx: Optional[_Ctx] = None) -> Tuple[str, ...]:
    """The names a source's waveform reads (temp and tnom as such); cached on ctx by the Source's identity (the plan's
    Sources, which its actions or the IR keep alive)."""
    if ctx is not None and id(src) in ctx.src_reads:
        return ctx.src_reads[id(src)]
    exprs: List[object] = list(src.args.values()) + [x for pt in src.points for x in pt]
    if src.wave == "exp":
        exprs += [src.spectre.get("td1"), src.spectre.get("td2")]
    got = _reads_of([e for e in exprs if e is not None])
    if ctx is not None:
        ctx.src_reads[id(src)] = got
    return got


def _src_check(wave: str, src: Source, env: Mapping[str, float], temp: float, tnom: float, consts_checked: bool
               ) -> Optional[List[Tuple[str, str, bool]]]:
    """L4: the constraints an evaluated source's waveform breaks, [(key of _SRC_RULES, what it has, whether the fields it
    reads are constants in src)]; None when a field does not evaluate (the walk of the state reports that).
    consts_checked: spectre.resolve_source made src from constant fields, so it refused their pulse period and pwl
    times itself (at the parse or the alter).  A constraint reads numbers only: a field an alter set to a word
    (value="2u") leaves the constraints that read it unchecked (the engines report that value, an open wrong-type
    case), and a pwl without points is resolve_source's own error.  Phase 2c8: a negative fall or width, an exp time
    constant of 0 (M1, M3); a sine's freq below 0 apart from its 0 (M4: the constant's rule on both engines, VACASK's
    alone for the 0); no negative zero in a message (a stretch of -1 printed `-0 s')."""
    def g(v: float) -> str:
        return "%.12g" % (v + 0.0)                     # -0.0 + 0.0 is 0.0

    def const(key: str) -> bool:
        if wave == "pwl":
            return all(isinstance(t, Num) for t, _v in src.points)
        return all(isinstance(src.args.get(k), Num) for k in _SRC_RULE_ARGS[key] if k in src.args)

    out: List[Tuple[str, str, bool]] = []

    def add(key: str, detail: str) -> None:
        out.append((key, detail, const(key)))

    need = {"pulse": ("tr", "tf", "pw", "per"), "sin": ("freq", "theta"),
            "exp": ("td1", "td2", "tau1", "tau2")}.get(wave, ())
    try:
        a = {k: _ev(e, env, temp, tnom) for k, e in src.args.items()
             if k in need and e is not None and not isinstance(e, (Str, list))}
        if wave == "pulse":
            tr, tf, per = a.get("tr"), a.get("tf"), a.get("per")
            pw = a.get("pw") if "pw" in src.args else 1e30
            if tr is not None and tr < 0:
                add("rise", "rise=%s s" % g(tr))
            if tf is not None and tf < 0:
                add("fall", "fall=%s s" % g(tf))
            if pw is not None and pw < 0:
                add("width", "width=%s s" % g(pw))
            consts = all(isinstance(src.args.get(k), Num) for k in ("per", "tr", "tf", "pw"))
            if None not in (per, tr, tf, pw) and not (consts and consts_checked) \
                    and per <= (tr + tf + pw) * (1 + 1e-12):                                    # type: ignore[operator]
                add("period" if per > 0 else "period0", "period=%s s, not longer than rise+fall+width = %s s"   # type: ignore[operator]
                    % (g(per), g(tr + tf + pw)))                                              # type: ignore[operator]
        elif wave == "sin":
            freq = a.get("freq")
            theta = a.get("theta") if "theta" in src.args else 0.0
            if freq is not None and freq == 0 and theta is not None and theta != 0:
                add("damp", "freq=0 and damp=%s" % g(theta))
            elif freq is not None and freq < 0 and theta is not None:
                add("freq", "freq=%s Hz" % g(freq))
            elif freq is not None and freq == 0 and theta is not None:
                add("freq0", "freq=%s Hz" % g(freq))
        elif wave == "exp":
            td1, td2 = a.get("td1"), a.get("td2")
            if td1 is not None and td2 is not None and td2 - td1 <= 0:
                s1, s2 = src.spectre.get("td1", Num(0.0)), src.spectre.get("td2")
                d1 = _ev(s1, env, temp, tnom) if s1 is not None and not isinstance(s1, (Str, list)) else td1
                d2 = _ev(s2, env, temp, tnom) if s2 is not None and not isinstance(s2, (Str, list)) else td2
                add("td2", "td2=%s s, not later than its td1=%s s" % (g(d2), g(d1)))
            for key in ("tau1", "tau2"):
                tau = a.get(key)
                if tau is not None and tau == 0:
                    add(key, "%s=0 s" % key)
        elif wave == "pwl":
            times = [_ev(t, env, temp, tnom) for t, _v in src.points]
            if len(times) == 1:
                add("points", "only one time point")
            bad = next((k for k in range(1, len(times)) if times[k] <= times[k - 1]), None)
            if bad is not None and not (consts_checked and all(isinstance(t, Num) for t, _v in src.points)):
                add("times", "time points that do not increase (%s s, then %s s)" % (g(times[bad - 1]), g(times[bad])))
            if times and times[0] < 0:
                add("first", "its first time point at %s s" % g(times[0]))
    except (X.EvalError, TypeError, AttributeError, ValueError, ArithmeticError):
        return None
    return out


def _src_violations(ctx: _Ctx, ent: _Inst, st: _State, wave: str, src: Source, consts_checked: bool, origin: Tuple,
                    reads: Tuple[str, ...]) -> Optional[List[Tuple[str, str, bool]]]:
    """_src_check in a state (the source's path values there), cached on the source state it comes from (origin: the
    id of the plan's Source, which the plan's actions or the IR keep alive, and the fields a sweep point sets) and on
    the values of what that Source's waveform reads (reads: its _src_reads, which a point's own values only narrow)."""
    scope = ent.path.rpartition(".")[0]
    env: Optional[Mapping[str, float]] = {}
    vals: Tuple = ()
    if reads:
        env = _scope_env(ctx, st, scope)
        if env is None:
            return None
        vals = tuple(st.temp if n == "temp" else st.tnom if n == "tnom" else env.get(n) for n in reads)
    key = (ent.path, origin, wave, consts_checked, vals)
    if key not in ctx.src_cache:
        ctx.src_cache[key] = _src_check(wave, src, env or {}, st.temp, st.tnom, consts_checked)
    return ctx.src_cache[key]


def _src_cause(ctx: _Ctx, ent: _Inst, key: str, wave: str, src: Source, consts_checked: bool, origin: Tuple,
               reads: Tuple[str, ...]) -> str:
    """The alter or set statement since whose state the source has broken the constraint (the states the alters and
    sets made, ctx.history, as _zero_cause reads them), else the source's own last alter when the netlist's source keeps
    it; "" when it breaks it at the netlist's values."""
    cause = ""
    for st, label in reversed(ctx.history):
        viols = _src_violations(ctx, ent, st, wave, src, consts_checked, origin, reads)
        if not viols or not any(v[0] == key for v in viols):
            break
        cause = label
    if not cause and ent.path in ctx.src_alter and ctx.source_state.get(ent.path, (None, None, None))[2] \
            is not _netlist_source(ctx, ent):
        cause = ctx.src_alter[ent.path]
    return cause


_SRC_WAVES = ("pulse", "sin", "exp", "pwl")
# L4: the Spectre fields of each waveform a constraint reads (a sweep of another one leaves the constraints as they are)
_SRC_FIELDS: Dict[str, Tuple[str, ...]] = {"pulse": ("rise", "fall", "width", "period"), "sin": ("freq", "damp"),
                                           "exp": ("td1", "td2", "delay", "tau1", "tau2"), "pwl": ("wave", "stretch")}


def _source_at(ctx: _Ctx, ent: _Inst, st: _State, over: Optional[Mapping[str, float]], kind: str, where: Any,
               sweeps: str, elab: bool = False, states: Optional[Mapping[str, Tuple[Optional[str], bool, Source]]] = None,
               const_only: bool = False, presweep: str = "") -> bool:
    """L4 (phase 2c7): one source's constraints in one state an analysis of this kind runs in (elab: VACASK's first
    elaboration, at an alter instance or model statement), each constraint reported once, naming the state (where: a
    text, or a callable giving it; the sweeps it is a point of; the alter or set that made it) [_SRC_RULES]: on Xyce the
    ones §3.8.1 states, in a tran; on VACASK every one where VACASK sets the waveform up (a tran, or a derived dc).
    over: the fields a sweep point sets.  True when it reported one.  Phase 2c8: the rules of both engines wherever the
    engines use the waveform, on both: in a tran, and in an analysis whose dc is derived, the waveform's value at t = 0
    (VACASK set it up and stopped, or divided by an exp's time constant of 0, NaN, where Xyce printed a value from it:
    `(Xyce runs it)'); a state whose violating fields are constants (no sweep point sets them), in every analysis on
    both engines, as the render refuses the constant whatever runs (an alter of freq to -50k before an op with a given
    dc); on VACASK alone, where it sets the waveform up, the ones only it checks (a sine's freq of 0) and every one at
    its first elaboration.  const_only: the constants' refusals alone (_constant_sources).  Phase 2c9 (N5): presweep,
    the kind of a step whose sweeps VACASK sets the circuit up before (_check_sources): an elaboration in which a tran's
    sources print their waveform type, a given dc's switched on before the sweep."""
    if ctx.engine == "xyce" and (elab or presweep):
        return False
    elab = elab or bool(presweep)
    tran = kind == "tran" and not elab
    wave, given, src = _src_state(ctx, ent, over, states)
    if wave not in _SRC_WAVES:
        return False
    # type="dc": VACASK does not set the waveform up; before a tran's sweeps every waveform prints its type
    vacask_setup = ctx.engine == "vacask" and (tran or presweep == "tran" or not given)
    base = (states or ctx.source_state).get(ent.path, (None, None, ent.inst.source))[2]
    origin = (id(base), tuple(sorted(over.items())) if over else ())
    reads = _src_reads(base, ctx) if base is not None else _src_reads(src)
    reported = False
    for key, detail, const in _src_violations(ctx, ent, st, wave, src, not over, origin, reads) or ():
        both, rule, vtext, setup, render = _SRC_RULES[key]
        if (ent.path, key) in ctx.src_reported or presweep and (ent.path, key) in ctx.src_presweep:
            continue
        if both and tran:
            mode = "tran"                                  # both engines: the waveform acts
        elif both and render and const and not over and not elab:
            mode = "const"                                 # both engines: the constant's refusal, whatever the analysis
        elif const_only:
            continue
        elif both and not given and not elab:
            mode = "dc"                                    # both engines: the derived dc is the waveform's t = 0 value
        elif vacask_setup and setup:
            mode = "setup"                                 # VACASK alone: it stops at its setup
        else:
            continue
        # phase 2c9 (N5): a setup before a sweep, VACASK's alone, leaves a later state's refusal of the constraint (of
        # both engines, say) to be reported too
        (ctx.src_presweep if presweep else ctx.src_reported).add((ent.path, key))
        reported = True
        parts = [sweeps] if sweeps else []
        cause = _src_cause(ctx, ent, key, wave, src, not over, origin, reads)
        if cause:
            parts.append("after " + cause)
        w = "%s%s" % (where() if callable(where) else where, " (%s)" % "; ".join(parts) if parts else "")
        what = "the %s of %s has %s" % (SPECTRE_TYPE.get(wave, wave), ent.path, detail)
        if mode == "tran":
            clause = "VACASK stops at its setup with '%s'" % vtext if setup else vtext
            msg = "%s: %s: %s; %s)" % (w, what, rule, clause)
        elif mode == "const":
            msg = "%s: %s: %s)" % (w, what, rule)
        elif mode == "setup":
            msg = ("%s: on VACASK %s, and VACASK stops at its setup with '%s'%s: not supported on VACASK in v1 (Xyce runs "
                   "it)" % (w, what, vtext, "" if elab or tran else _SRC_NOTE))
        else:
            msg = "%s: %s: %s; its dc is the waveform's value at t = 0)" % (w, what, rule)
        ctx.err(ent.inst.origin, msg)
    return reported


def _sources_at(ctx: _Ctx, st: _State, over: Mapping[str, Mapping[str, float]], kind: str, where: str, sweeps: str,
                elab: bool = False, states: Optional[Mapping[str, Tuple[Optional[str], bool, Source]]] = None) -> None:
    """L4 (phase 2c7): _source_at for every source in one state."""
    for ent in _src_ents(ctx):
        _source_at(ctx, ent, st, over.get(ent.path), kind, where, sweeps, elab, states)


def _constant_sources(ctx: _Ctx) -> None:
    """Phase 2c8 (M1, M3): a netlist that runs no analysis (`nothing to run', no deck rendered) had no state checked, so
    a constant that breaks a rule passed with status 0 on both engines (a negative fall or width, an exp time constant
    of 0, a negative freq, a pwl time point before 0), while spectre.resolve_source refuses a constant period or pwl
    times that do not increase at the parse whatever runs: the constants' refusals on both engines, in the rules'
    words, at the netlist's values, as the parse's."""
    for ent in _src_ents(ctx):
        _source_at(ctx, ent, ctx.base, None, "", "the netlist", "", const_only=True)


def _src_deps(ctx: _Ctx, ent: _Inst, src: Source) -> Optional[Set[str]]:
    """L4: the top-level parameters (and temp, tnom) a top-level source's waveform reads, through the top-level parameters'
    expressions; None for a source in a subckt body (its subckt's parameters may read any of them)."""
    if "." in ent.path:
        return None
    params = {p.name: p for p in ctx.top_params}
    out: Set[str] = set()
    todo = list(_src_reads(src, ctx))
    while todo:
        n = todo.pop()
        if n in out:
            continue
        out.add(n)
        p = params.get(n)
        if p is not None:
            todo.extend(_reads_of(p.expr))
    return out


def _src_level(ctx: _Ctx, ent: _Inst, wave: str, deps: Optional[Set[str]], lvl: SweepLevel, r: _Resolved) -> bool:
    """L4: whether a sweep level can change a source's constraints: a field of it a constraint reads (or one vamos maps
    no other way), what its waveform reads (a variable, temp or tnom; for a source in a body any of them and a subckt
    instance's parameter)."""
    kind, name, param = r.target
    if r.source_field:
        return r.inst is not None and r.inst.path == ent.path and (
            param in _SRC_FIELDS.get(wave, ()) or param not in _SWEPT_ARG.get(wave, {}) and param not in ("dc", "mag",
                                                                                                         "phase"))
    if kind == "instance":                                 # a subckt instance's parameter
        return deps is None
    return deps is None or name in deps                    # a variable; an option's name is temp or tnom


def _check_sources(ctx: _Ctx, step: AnalysisStep, levels: List[Tuple[SweepLevel, _Resolved]], what: str) -> None:
    """L4 (phase 2c7): _source_at in every state the step runs in, per source: its nominal state, and every point of
    the levels that can change its constraints (_src_level: a field of it, what its waveform reads); the state named at
    the levels' first points where none can.  2c6 checked a pulse's period at parse and alter time for constant fields
    only, so R0080 (width=wd altered to 2 s under period=20u) reached VACASK, which stopped with exit 1, while Xyce ran
    it.  A source is reported at its first point that breaks a constraint."""
    ents = _src_ents(ctx)
    if not ents:
        return
    rel = [(lvl, r) for lvl, r in levels if r.target[0] in ("variable", "option")
           or (r.target[0] == "instance" and r.inst is not None and (r.inst.inst.kind == "x" or r.source_field))]
    sweeps = ", ".join("sweep %s" % lvl.name for lvl, _r in rel if lvl.name)
    firsts = [grid_points(lvl.spec)[0] for lvl, _r in rel]

    def where_of(vals: Sequence[float]) -> str:
        if not rel:
            return what
        return "%s at %s" % (what, ", ".join("%s=%s" % (lvl.desc, _fmt(v)) for (lvl, _r), v in zip(rel, vals)))

    # phase 2c9 (N5): outside a tran, VACASK prints type="dc" around this step for a derived dc it sweeps (_step)
    dc_switched = {r.inst.path for lvl, r in rel if step.kind != "tran" and r.source_field and r.inst is not None
                   and lvl.target[2] == "dc"}
    for ent in ents:
        wave, _given, src = _src_state(ctx, ent)
        if wave not in _SRC_WAVES:
            continue
        deps = _src_deps(ctx, ent, src)
        idx = [k for k, (lvl, r) in enumerate(rel) if _src_level(ctx, ent, wave, deps, lvl, r)]
        for combo in itertools.product(*[grid_points(rel[k][0].spec) for k in idx]):
            vals = list(firsts)
            st = ctx.base
            over: Dict[str, float] = {}
            for k, v in zip(idx, combo):
                vals[k] = v
                lvl, r = rel[k]
                if r.source_field:
                    over[lvl.target[2]] = v
                else:
                    st = _apply(st, r, _level_param(lvl), v)
            if _source_at(ctx, ent, st, over, step.kind, lambda: where_of(vals), sweeps):
                break
        if idx and ctx.engine == "vacask" and ent.path not in dc_switched:
            # after the points: a constraint a point breaks too is named at that point (its state is the setup's when
            # the levels are hooked and start there)
            _presweep_source(ctx, ent, step, [rel[k] for k in idx], what)


def _presweep_source(ctx: _Ctx, ent: _Inst, step: AnalysisStep, levels: List[Tuple[SweepLevel, _Resolved]],
                     what: str) -> bool:
    """Phase 2c9 (N5): VACASK sets the circuit up before a sweep's first point, in the values in force then: the
    netlist's own for its first analysis (`Initial circuit setup failed'), else after a change since its last setup
    (`Circuit setup failed'), never at a var statement or at an alter instance after its first setup (measured on
    VACASK 0.3.4-91: phase 2c9's decks V4, V7, V8 stop; V3, V5, V6, V9 run).  The hooked levels whose flag is
    off are at their first point there (vacask.analysis_lines sets the value with the flag, L3), and a tran's sources
    print their waveform type (a given dc's switched on before the sweep).  VACASK's setup rules for one source the
    levels reach, in that state (S-2c8's G20073: freq=qfr, qfr 0 at the netlist's values, every analysis sweeping it
    away from 0: VACASK exit 1 before any analysis, with no refusal; Xyce sets nothing up before its .STEP points).
    True when it reported one."""
    st = ctx.base
    over: Dict[str, float] = {}
    for lvl, r in levels:
        if r.target[0] in ("instance", "model") and _hook_printed(ctx, r.target) and ctx.base.hook(r.target)[0] == 0.0:
            first = grid_points(lvl.spec)[0]
            if r.source_field:
                over[lvl.target[2]] = first
            else:
                st = _point(ctx, st, lvl, r, first, True)
    names = ["sweep %s" % lvl.name if lvl.name else "its sweep of %s" % lvl.desc for lvl, _r in levels]
    where = "%s, VACASK's setup before the first point of %s" % (what, " and ".join(dict.fromkeys(names)))
    return _source_at(ctx, ent, st, over or None, step.kind, where, "", presweep=step.kind)


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


def _innermost(step: AnalysisStep, match: Any) -> Optional[SweepLevel]:
    """The innermost sweep level of a step (its own, then its enclosing ones inner to outer) whose target
    `match` accepts."""
    for lvl in ([step.own] if step.own is not None else []) + list(reversed(step.context)):
        if match(lvl.target):
            return lvl
    return None


def _trise_names(it: Instance) -> Set[str]:
    """The names an instance's trise has in a target: trise, and its rename on the master's rows (dtemp)."""
    out = {"trise"}
    row = T.SPECTRE_MASTERS.get(it.prim) if it.kind not in ("x", "y") else None
    for r in (row.instance if row is not None else ()):
        if r.name == "trise" and r.action == "rename":
            out.add(r.value)
    return out


def _is_trise(ent: _Inst, param: str) -> bool:
    return param in _trise_names(ent.inst)


def _trises(ctx: _Ctx, step: AnalysisStep, path: str, it: Instance) -> List[float]:
    """The trise values an instance has in a step: a dev= sweep level of it, an alter, or its own value (0)."""
    names = _trise_names(it)
    lvl = _innermost(step, lambda t: t[0] == "instance" and t[1] == path and t[2] in names)
    if lvl is not None:
        return grid_points(lvl.spec)
    altered = ctx.step_trise.get(step.id, {})
    if path in altered:
        return [altered[path]]
    v = it.params.get("trise")
    ent = ctx.insts.get(path)
    try:
        return [_ev(_exprs(v)[0], ent.env.env if ent is not None else ctx.nl.values, 27.0, 27.0)] if v is not None else [0.0]
    except (X.EvalError, IndexError):
        return [0.0]


def _off_tnom(ctx: _Ctx, card: Model) -> Optional[str]:
    """R-plan-6 (phase 2c): the first step that runs the card at a device temperature other than its tnom,
    described for the warning: the step's temperature or its temp sweep points, plus the trise of the
    card's instances (given, swept with dev= or altered), against the card's tnom or a tnom sweep's points."""
    users = sorted(ctx.card_users.get(id(card), {}).items()) or [("", None)]
    own_tnom = "tnom" in card.params
    for s in ctx.steps:
        lt = _innermost(s, lambda t: t == ("option", "temp", ""))
        ln = _innermost(s, lambda t: t == ("option", "tnom", ""))
        temps = grid_points(lt.spec) if lt is not None else [float(s.options.get("temp", 27.0))]   # type: ignore[arg-type]
        if own_tnom or ln is None:
            tnoms = [_card_tnom(ctx, card, s)]
        else:
            tnoms = grid_points(ln.spec)
        for path, it in users:
            trises = _trises(ctx, s, path, it) if it is not None else [0.0]
            for t in temps:
                for tr in trises:
                    for tn in tnoms:
                        if t + tr == tn:
                            continue
                        if tr:
                            return "%s runs instance %s at temp=%s (trise=%s) where the card's tnom is %s" % (
                                s.name, path, _fmt(t + tr), _fmt(tr), _fmt(tn))
                        return "%s runs at temp=%s where the card's tnom is %s" % (s.name, _fmt(t), _fmt(tn))
    return None


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
                hit = _off_tnom(ctx, card)
                if hit is not None:
                    ctx.warn(origin, "%s: %s: %s; %s (an approximation, §0)" % (label, shown, _reason(rule), hit))
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


def _global_user_keys(ctx: _Ctx) -> Set[str]:
    """D2: the option keys of the global set the run itself gave: the netlist's options, Settings.options
    (argv over the netlist's; not the output settings job.settings writes there itself unless the netlist
    gives them) and +paramdefault's options lines."""
    nlo = set(ctx.nl.options)
    keys = nlo | {k for k in ctx.settings.options if k not in SETTINGS_WRITTEN or k in nlo}
    keys |= {param for prim, param, _value in ctx.settings.paramdefault if prim == "options"}
    return keys


def build(nl: Netlist, engine: str, settings: Settings, cwd: Optional[str] = None,
          outdir: Optional[str] = None) -> RunPlan:
    """The ordered plan of a Spectre netlist for one engine (§5): per-path and per-state evaluation
    on tables.path_envs, the sweep grids, alters and source states, the options actions, dependents.
    NoteError with every note when a construct is an error.  cwd and outdir (phase 2c, D9) are the
    job's cwd and its resolved -outdir, so state-file names compare as the flow resolves them; the
    defaults are the process cwd and Settings.outdir against it."""
    if engine not in T.ENGINES:
        raise ValueError("engine must be one of %s, not %r" % (T.ENGINES, engine))
    ctx = _Ctx(nl, engine, settings)
    plan = ctx.plan
    if cwd:
        ctx.cwd = cwd
    else:
        try:
            ctx.cwd = os.getcwd()
        except OSError:                                  # a removed cwd: relative names compare as written
            ctx.cwd = ""
    if outdir is not None:
        ctx.outdir = outdir
    elif settings.outdir:
        ctx.outdir = os.path.normpath(os.path.join(ctx.cwd, str(settings.outdir)))
    ctx.scan()
    ctx.user_keys = _global_user_keys(ctx)
    gopts = _global_options(ctx)
    ctx.current = dict(gopts)
    plan.options = dict(gopts)
    plan.actions.append(Action("options", dict(gopts), None, ""))
    ctx.base = _State({}, float(gopts["temp"]), float(gopts["tnom"]))    # type: ignore[arg-type]
    ctx.history = [(ctx.base, "")]
    with substituted(ctx.sites, ctx.base.temp, ctx.base.tnom):
        ctx.elaborate()
    ctx.variables = _variables(ctx)
    ctx.dependents = _dependents(ctx, ctx.variables)
    plan.variables = dict(ctx.variables)
    plan.dependents = set(ctx.dependents)
    if ctx.variables:
        plan.actions.append(Action("var", dict(ctx.variables), None, ""))
    _record_nominal(ctx)                                 # L2 (phase 2c7): §3.7's nominal structure, the netlist's own
    if not nl.analyses:
        ctx.note("", "the netlist has no analyses: nothing to run")
    for a in nl.analyses:
        _analysis(ctx, a, [], in_sweep=False)
    if not ctx.steps and nl.analyses:
        _check_state(ctx, ctx.base, "the netlist")
    if not ctx.steps:
        _constant_sources(ctx)                           # phase 2c8 (M1, M3): the constants' rules with no analysis
    _run_dependent_rules(ctx)
    _ic_values(ctx)
    if ctx.errors:
        raise NoteError(ctx.notes)
    plan.notes = list(ctx.notes)
    return plan
