"""Xyce deck emitter (docs/VAMOS_AMS_DESIGN.md §4.5, with §4.3.5-§4.3.8 and §4.7; the Spectre
extensions of docs/VAMOS_SPECTRE_DESIGN.md §4.5 and §7.2).

Xyce runs under co-simulation with the plain argv {"Xyce", deck}, so the deck is
plain Xyce: floats only (numbers.fmt, no suffixes), no ^ and no bare log (the
expr printer), ':' as the hierarchy separator, {} around every value that is
not a number literal, duplicates already resolved by the IR, parameter names
through expr.param_ident.  Every engine-dependent rule comes from tables.py,
which the VACASK emitter reads too.

API
---
emit(nl, path, osdi=(), notes=None) -> None
    Write the deck for nl to path (the caller picks the file: ams/deck/vamos.cir
    or smoke.cir).  Xyce compiles Verilog-A itself (PyMS): nl.hdl, plus any .va
    path in osdi, become .hdl lines; an .osdi path is an error (deck.py passes
    nothing: the gated conductance is a B source here).  Problems are collected
    over the whole netlist and raised together as one NoteError; without
    errors, warnings and notes are appended to notes when a list is given.
render(nl, osdi=(), notes=None, op=False, step=None, sigmap=None, names=None) -> str
    The deck text; op=True prints .op in place of .tran and .print (the smoke
    deck).  With an AnalysisStep (the spectre personality, VAMOS_SPECTRE_DESIGN.md
    §4.5 items 5-6, §7.2) the deck runs that one step with a .PRINT of exactly its
    SignalMap entries, and `names` receives every printed Ref's column.
names_for(nl, plan, sigmap) -> Dict[str, Dict[Ref, str]]
    The name map render() fills over every step (step id -> Ref -> column), without rendering.
smoke(nl, dir, osdi=(), nvc_libdir=None, timeout=600) -> List[Note]
    The compile-time check (§4.7): writes dir/smoke.cir from
    tables.smoke_netlist(nl) with an operating point, runs `Xyce -norun` (the
    model-parameter check: "No model parameter ... found" and "Unrecognized
    parameter" are errors, because Xyce otherwise runs with the parameter
    silently ignored; the bounds of binned cards are exempt; a Verilog-A module
    bound to a built-in device of its level is an error, device_summary_problems),
    then the operating point itself.  Output in dir/smoke.norun.log and
    dir/smoke.log.  Returns [] when the deck passes, else one error note with
    Xyce's error messages; Xyce's "no valid model card found" / "Unable to find
    model <base>." for a natively binned M line becomes "no bin of <base> for
    l=..., w=...".
output_problems(text, nl) -> List[Note]
    The smoke patterns over one Xyce output: unknown model parameters (the
    bounds of binned cards exempt), unrecognized parameters, netlist errors.
device_summary_problems(text, nl) -> List[Note]
    The Verilog-A binding check (VAMOS_SPECTRE_DESIGN.md §4.5 item 13; the evidence
    is in its docstring).
RAW
    "vamos_tran.raw": the .print file, relative so that Xyce writes it into its
    process cwd, the per-run directory (§6); equal to ams.layout.RAW.

Deck layout: "* title"; .hdl lines; .global; top-level .param; the body in IR
order; .options device temp= tnom= [gmin=] (temp and tnom default to 25, 27
under .option spice); .ic; .nodeset (dropped with a note when .ic exists: Xyce
aborts on both); .tran step stop [start [maxstep]] [UIC]; .print tran
format=raw file=vamos_tran.raw <probes> (v(*) when there are none); .end.
Only the subckts and model cards the deck uses are printed (an elaboration
walk from the top-level instances, see Binning): a library defines far more
than a deck uses, and an unused construct no target honours must not fail it.

Element mapping (IR kind -> Xyce); an element's printed name starts with its
Xyce letter (a behavioral E/G becomes B<name>, the gated conductance g_<n>
becomes bg_<n>; i() references, K, F and H controls and probes follow; the
rawfile reader finds i(<IR name>) in the I(B<name>) column):
    r c l   R/C/L [model] [value] params     k   K l1 l2 k
            (an inductor a K names gets its multiplier and TC1/TC2 folded into
            its value and no m=, tc1, tc2, dtemp: Xyce's K pass keeps only L and
            IC of a coupled inductor's line; tables.coupled_inductance)
    v i     V/I with DC, AC and PULSE/SIN/EXP/PWL (every field printed); a code
            source is PWL FILE "<code_uri>"; a PWL delay (TD=) is added to every
            time point, because Xyce's own TD= gives 0, not the first value,
            before the delay (HSPICE and VACASK hold the first value)
    e g f h linear controlled sources        b   B V={...} / I={...}
    d q m j model cards keep their names and type keyword; level per
            tables.target (MOS 49/53 -> 9; a diode level 3 is refused: Xyce has
            no geometric diode); a 3-terminal Q leaves the substrate to Xyce
            (ground)
    x       X with overrides through expr.param_ident
    y       vamos_gcond: B<name> nd n I={v(ne)*(v(nd)-v(n))/rr}; a module whose
            xyceModelGroup is MOSFET, DIODE, BJT, Resistor or Capacitor with an
            xyceLevelNumber (Netlist.va_modules, VAMOS_SPECTRE_DESIGN.md §4.5 item
            13) prints as that letter device, M/D/Q/R/C<name> <nodes> <card>
            <instance parameters>, on a `.model <module>__vamos[_<kind>] <kind>
            level=<n>` card per module and polarity (the card's xyceTypeVariable
            value against xycePTypeValue selects the p-type kind; that parameter
            is consumed): PyMS registers such a module as that device, and the Y
            form fails for it [E64]; the card itself carries no parameter (PyMS
            lists a card parameter and does not apply it [E63], and Xyce only
            warns about an instance-typed one there), so every parameter goes on
            the device line, where a model-typed one is Xyce's "Unrecognized
            parameter" error and the output scan fails loudly [S3, 2026-10-10].
            Any other module: Y<module> <name> <nodes> <card>, with .model <card>
            <module>.  A Y instance with parameters is refused (PyMS ignores VA
            parameter overrides), and so is a Verilog-A instance that needs a
            multiplier (Y and letter lines alike take no m=)
Names are case-insensitive in Xyce: IR names (set_sim_case sensitive) that
differ only in case are an error, as are names Xyce cannot parse.

The multiplier: Xyce silently drops it for I sources and JFETs under an X with
m= (and ignores m= on I and F lines), so the deck never uses X-line m.  Every
subckt declares vamos_mfactor=1.0 and the emitter carries it down exactly as
VACASK carries $mfactor (tables.MULT): m={vamos_mfactor*m} on R C L D Q M G,
folded into the values of I, the gain of F, the area of J and the expression of
a behavioral i=; nothing on V, E, H, K and behavioral v=.
Binning: native (cards keep lmin/lmax/wmin/wmax; Xyce tries bins in the order
tables.Scope gives the VACASK chain) when nf is absent or 1; otherwise the
instance is bound to the bin card VACASK's rule (per-finger width,
tables.select_bin) selects, which needs l, w and nf as numbers.  They are
evaluated on each instance path: the elaboration walk runs on
tables.path_envs (every subckt's parameters with the X line's overrides;
sky130's wrappers pass l={l} w={w} nf={nf}), and a subckt whose bindings or
branch choices differ between paths is printed once per distinct binding, as
<name>__vb<k> (the X lines name the copy; only the bin cards a copy uses are
printed with it).  Geometry that is not a number even on its path (temper) is
an error when nf is not 1.  A geometry no bin fits is an error at emit time,
naming the evaluated l and w.

Spectre netlists (Netlist.dialect == "spectre"; VAMOS_SPECTRE_DESIGN.md §4.4, §4.5, §7.2):
    Cond    resolved per instance path by tables.path_envs; the printed body holds the taken
            branch's instances, a different resolution is another __vb<k> variant; a condition that
            is not a number on its path is an error
    cards   tables.model_params(dialect="spectre"), MasterRow.instance through
            tables.spectre_instance_params (an instance_<name> rename prints without the prefix)
    bins    bin_rule="spectre": the instance is bound to its group entry per path (group order,
            exact bounds on the total w); no native binning
    sources a given dc prints DC <dc>; a derived one DC {<the t = 0 value of the waveform>}: Xyce's
            operating point for a waveform source without DC is 0 [E70]; a noise step's input source
            prints AC 1 0 (§8.4) [E19]
    isnoisy=no   G<name> a b a b {1/r}, the same current with no noise; with tc1/tc2 the resistor
            stays an R, noisy, with a warning [E32]
    paths   every .PRINT item, .IC/.NODESET node and DNO() is spelled through printed_path
            (V(XI2:MID) for IR I2.Mid) [E84]
    step    one deck per analysis (§7.2): .options device temp tnom [gmin]; .OP (or .DC vamos_op
            LIST 0 under two or more .STEPs [E20]) / .DC / .AC / .NOISE / .TRAN with .OPTIONS TIMEINT
            METHOD; the context sweeps as .STEP lines innermost first [E20]; a sub= or source-field
            target through a generated .param vamos_x_<path>_<param> hook [E20, E71]; .IC only in a
            tran deck whose ic is all or node, .NODESET in every other deck [E18]; the .PRINT with
            exactly the SignalMap's columns (V(*) plus the currents for allpub)
"""

from __future__ import annotations

import os
import re
import subprocess
from collections import abc
from typing import TYPE_CHECKING, Dict, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

from vamos.netlist import expr as X
from vamos.netlist import tables as T
from vamos.netlist.expr_ast import Binary, Call, Expr, Name, Num, Str
from vamos.netlist.ir import (Cond, Instance, Model, Netlist, Param, ParamTest, Source, Subckt, VaModule,
                              flat_items)
from vamos.netlist.numbers import fmt
from vamos.notes import ERROR, Note, NoteError, error, note, warning

if TYPE_CHECKING:                                  # the spectre run plan (VAMOS_SPECTRE_DESIGN.md §5, §6)
    from vamos.netlist.plan import AnalysisStep, RunPlan, SweepLevel
    from vamos.netlist.signals import Ref, SignalMap

ENGINE = "xyce"
RAW = "vamos_tran.raw"
SMOKE_DECK = "smoke.cir"
SMOKE_LOG = "smoke.log"
SMOKE_NORUN_LOG = "smoke.norun.log"
MF = T.MF_PARAM[ENGINE]
HOOK_PREFIX = "vamos_x_"                 # the .param hook of a sub= or source-field sweep target (§7.2)
OP_DUMMY = "vamos_op"                    # the .DC variable standing for .OP under two or more .STEPs [E20]

_LETTER = {"r": "r", "c": "c", "l": "l", "k": "k", "v": "v", "i": "i", "e": "e", "g": "g",
           "f": "f", "h": "h", "b": "b", "d": "d", "q": "q", "m": "m", "j": "j", "x": "x"}
_LITERAL = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_BAD_NAME = re.compile(r"[\s(){},=;'\"]")
# A Spectre source field a sweep hook replaces, as the IR holds it: Source.dc, an args key, or an ac index
# (§3.8.1); exp's td1/td2 and pwl's wave/offset/scale/stretch are transformed by the parser and not hookable.
_SOURCE_FIELD: Dict[str, Tuple[str, object]] = {
    "dc": ("dc", None), "val0": ("args", "v1"), "val1": ("args", "v2"), "delay": ("args", "td"),
    "rise": ("args", "tr"), "fall": ("args", "tf"), "width": ("args", "pw"), "period": ("args", "per"),
    "sinedc": ("args", "vo"), "ampl": ("args", "va"), "freq": ("args", "freq"), "damp": ("args", "theta"),
    "sinephase": ("args", "phase"), "mag": ("ac", 0), "phase": ("ac", 1),
}


def emit(nl: Netlist, path: str, osdi: Sequence[str] = (), notes: Optional[List[Note]] = None) -> None:
    """Write the Xyce deck for nl to path (see the module docstring)."""
    text = render(nl, osdi, notes)
    # UTF-8, whatever the locale: the netlist text spice.py read as UTF-8 (a non-ASCII
    # title line failed the compile under a non-UTF-8 locale)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def render(nl: Netlist, osdi: Sequence[str] = (), notes: Optional[List[Note]] = None,
           op: bool = False, step: Optional[AnalysisStep] = None, sigmap: Optional[SignalMap] = None,
           names: Optional[Dict[str, Dict[Ref, str]]] = None) -> str:
    """The Xyce deck text for nl (see the module docstring).

    step, sigmap, names (VAMOS_SPECTRE_DESIGN.md §4.5 items 5-6, §7.2): with an AnalysisStep
    the deck prints that one step and a .PRINT with exactly its SignalMap entries, in order, and
    fills names (step id -> Ref -> engine column) for every Ref it printed.  With all three None
    the output is byte-identical to the vcs-ams deck.
    """
    return _Deck(nl, osdi, op, step, sigmap, names).render(notes)


def names_for(nl: Netlist, plan: RunPlan, sigmap: SignalMap) -> Dict[str, Dict[Ref, str]]:
    """The name map render() would fill over every step (step id -> Ref -> Xyce column), without
    rendering (VAMOS_SPECTRE_DESIGN.md §4.5 item 6, §6.4)."""
    out: Dict[str, Dict[Ref, str]] = {}
    deck = _Deck(nl, (), False, None, sigmap, out)
    for step_id in sigmap.per_step:
        deck.name_map(step_id)
    return out


class _Skip(Exception):
    """An element whose error is already recorded; the walk moves on."""


class _Env(abc.Mapping):
    """Parameter values inside one subckt instance: its own over the top-level values, with the
    names it declares but cannot evaluate hidden (no copy of the top level per instance).  The
    elaboration walk reads tables.path_envs now (§4.4); kept for callers of child_env."""

    def __init__(self, top: Mapping[str, float]):
        self.top = top
        self.own: Dict[str, float] = {}
        self.hidden: Set[str] = set()

    def __getitem__(self, name: str) -> float:
        if name in self.own:
            return self.own[name]
        if name in self.hidden:
            raise KeyError(name)
        return self.top[name]

    def __contains__(self, name: object) -> bool:
        return name in self.own or (name not in self.hidden and name in self.top)

    def __iter__(self) -> Iterator[str]:
        yield from self.own
        for k in self.top:
            if k not in self.own and k not in self.hidden:
                yield k

    def __len__(self) -> int:
        return sum(1 for _ in self)


class _Variant:
    """One printed body: the top level (sub None) or a subckt under the name it is printed with."""
    __slots__ = ("sub", "name", "bind", "models", "special", "taken")

    def __init__(self, sub: Optional[Subckt], name: str):
        self.sub, self.name = sub, name
        self.bind: Dict[int, Optional[str]] = {}    # id(X or binned M) -> master to print; None: error recorded
        self.models: Set[int] = set()                # ids of the model cards this body prints
        self.special: List[Tuple[str, str]] = []     # (instance, master) differing from the plain subckt
        self.taken: Set[int] = set()                 # ids of the instances on this body's path (Cond resolved)


class _Deck:
    def __init__(self, nl: Netlist, osdi: Sequence[str], op: bool, step: Optional[AnalysisStep] = None,
                 sigmap: Optional[SignalMap] = None, names: Optional[Dict[str, Dict[Ref, str]]] = None):
        self.nl, self.osdi, self.op = nl, list(osdi), op
        self.step, self.sigmap = step, sigmap
        self.names: Dict[str, Dict[Ref, str]] = names if names is not None else {}
        self.spectre = nl.dialect == "spectre"
        self.va: Dict[str, VaModule] = {k.lower(): v for k, v in nl.va_modules.items()}
        self.s = nl.scale()
        self.values = nl.values
        self.notes: List[Note] = []
        self.body: List[str] = []
        self.cards: Set[str] = set()            # Verilog-A model cards already printed
        self.nobin: Dict[int, str] = {}         # body index of a natively binned M line -> message
        self.nobin_lines: Dict[int, str] = {}   # deck line (1-based) -> message
        self.nobin_dev: Dict[Tuple[str, str], str] = {}   # (BASE, INSTANCE) -> message
        self.multiplied = _multiplied(nl)       # subckts some X line with m= reaches
        self.checked: Set[int] = set()          # subckts whose names were checked
        self.iparams: Dict[int, Dict[str, Expr]] = {}     # instance id -> parameters after the Spectre rules
        self.hooks: Dict[Tuple[str, str], Tuple[str, Expr]] = {}   # (IR path, Spectre param) -> (.param, nominal)
        self.noise_input = step.noise_input if step is not None and step.kind == "noise" else None
        self.once_seen: Set[Tuple[str, str]] = set()
        if step is not None:
            self._hooks(step)
        self.elaborate()
        if step is not None:
            self._one_path(step)

    def err(self, origin: str, msg: str) -> None:
        self.notes.append(error(origin, msg))

    def once(self, origin: str, msg: str) -> None:
        if (origin, msg) not in self.once_seen:
            self.once_seen.add((origin, msg))
            self.err(origin, msg)

    def add(self, notes: Sequence[Note]) -> None:
        """Card notes, once per message (bins of one base share theirs, tables.card_label)."""
        seen = {(n.severity, n.message) for n in self.notes}
        for n in notes:
            if (n.severity, n.message) not in seen:
                seen.add((n.severity, n.message))
                self.notes.append(n)

    # -- the step's sweep hooks (§7.2) -----------------------------------------------------
    def _hooks(self, step: AnalysisStep) -> None:
        """A sub= target (an X-line override) or an independent source's field swept by this step
        prints through a generated .param, which .STEP/.DC sweep [E20, E71]."""
        levels = list(step.context) + ([step.own] if step.own is not None else [])
        for lv in levels:
            kind, name, param = lv.target
            if kind != "instance":
                continue
            inst = _resolve(self.nl, name)
            if inst is None:
                continue
            hook = HOOK_PREFIX + "%s_%s" % (name.replace(".", "_"), param)
            if inst.kind == "x":
                sub = T.Scope(self.nl.body).find_subckt(inst.master or "")
                nominal = inst.params.get(param)
                if nominal is None and sub is not None:
                    nominal = next((p.expr for p in _plist(sub) if p.name == param), None)
                if nominal is None:
                    nominal = Num(0.0)
                self.hooks[(name, param)] = (hook, nominal)
            elif inst.kind in ("v", "i"):
                src = inst.source or Source()
                where = _SOURCE_FIELD.get(param)
                if where is None:
                    self.err(inst.origin, "%s: the %s field %s cannot be swept on Xyce in v1 (the deck prints a "
                             "derived value for it)" % (inst.name, inst.prim or inst.kind, param))
                    continue
                nominal: Optional[Expr]
                if where[0] == "dc":
                    nominal = src.dc if src.dc is not None else T.wave_t0(src)
                elif where[0] == "args":
                    nominal = src.args.get(where[1])          # type: ignore[arg-type]
                else:
                    nominal = src.ac[where[1]] if src.ac is not None else None   # type: ignore[index]
                self.hooks[(name, param)] = (hook, nominal if nominal is not None else Num(0.0))

    def _one_path(self, step: AnalysisStep) -> None:
        """§5.3: a sweep hook or a hierarchical noise input inside a subckt that several instance paths
        reach would change the shared definition on every path, where Spectre and VACASK change one path:
        an error in v1.  plan.build refuses such a target (plan._multipath); this guards a step built by
        hand, after the elaboration has listed the paths."""
        names = [name for (name, _p) in self.hooks]
        if self.noise_input is not None:
            names.append(self.noise_input)
        what = "%s %s" % (step.kind, step.name or step.id)
        for name in dict.fromkeys(n for n in names if "." in n):
            parent = _resolve(self.nl, name.rsplit(".", 1)[0])
            sub = parent.master if parent is not None else None
            paths = [p for p, pe in self.by_path.items() if pe.subckt is not None and pe.subckt.name == sub]
            if len(paths) > 1:
                target = _resolve(self.nl, name)
                self.once(target.origin if target is not None else "",
                          "%s: on Xyce %s lies in subckt %s, which %d instance paths reach (%s): changing the "
                          "shared definition would change every path; not supported in v1 (§5.3)"
                          % (what, name, sub, len(paths), ", ".join(paths)))

    def hook_name(self, path: str, param: str) -> Optional[str]:
        h = self.hooks.get((path, param))
        return h[0] if h is not None else None

    # -- elaboration: what each printed body needs ---------------------------------------
    def elaborate(self) -> None:
        """Walk the instance paths from the top level (tables.path_envs, §4.4): decide the printed
        variants of every used subckt, the model cards each printed body needs (unused definitions
        are never printed) and the master of every X and binned M line.

        Xyce bins natively on total W, which is vamos's rule only when nf is absent or 1.  A
        binned device with another nf is bound to the bin card vamos selects (tables.select_bin,
        per-finger W), which needs l, w and nf as numbers: they are evaluated on each instance
        path (the subckt parameters with the X line's overrides; sky130 wrappers pass nf={nf}),
        and a subckt whose bindings or branch choices differ between paths is printed once per
        distinct binding, as <name>__vb<k>.  A Spectre model group is bound per path always.
        """
        self.variants: Dict[int, List[_Variant]] = {}       # id(subckt) -> printed variants
        self.memo: Dict[Tuple[int, Tuple[Tuple[str, str], ...], Tuple[int, ...]], _Variant] = {}
        self.sig_first: Dict[int, Tuple[int, ...]] = {}     # id(subckt) -> the branch signature first seen
        self.vstack: List[_Variant] = []
        self.taken = {s.name.lower() for s in T.all_subckts(self.nl)}
        self.top = _Variant(None, "")
        try:
            pes = list(T.path_envs(self.nl, strict=False, dialect=self.nl.dialect))
        except X.EvalError as exc:                           # a condition that is not a number on its path
            self.err("", str(exc))
            pes = []
        except T.TableError as exc:
            self.err("", str(exc))
            pes = []
        self.by_path: Dict[str, T.PathEnv] = {pe.path: pe for pe in pes}
        if "" in self.by_path:
            self.visit("", self.top)

    def visit(self, path: str, var: _Variant) -> None:
        pe = self.by_path[path]
        var.taken = {id(it) for it in pe.items}
        self.vstack.append(var)
        try:
            for it in pe.items:
                if it.kind == "x":
                    found = pe.scope.where(it.master or "")
                    if found is None:
                        continue                                # i_x reports it
                    sub, _where = found
                    child_path = it.name if not path else path + "." + it.name
                    if child_path not in self.by_path:          # path_envs does not enter a recursion
                        self.once(it.origin, "%s: subckt %s instantiates itself" % (it.name, sub.name))
                        var.bind[id(it)] = None
                        continue
                    try:                                        # the rule path_envs applies (its oracle hook)
                        self.child_env(sub, it, pe.env)
                    except Exception:  # noqa: BLE001  (a Spectre value the HSPICE evaluator refuses)
                        pass
                    child = self.variant(sub, child_path)
                    var.bind[id(it)] = child.name
                    if child.name != sub.name:
                        var.special.append((it.name, child.name))
                elif it.kind in T.MODEL_KINDS_OF:
                    card, bins = pe.scope.card_of(it)
                    if card is not None:
                        self.need(pe.scope, card)
                    elif bins:
                        self.bin_choice(var, it, pe.scope, bins, pe.env)
        finally:
            self.vstack.pop()

    def variant(self, sub: Subckt, path: str) -> _Variant:
        v = _Variant(sub, sub.name)
        self.visit(path, v)
        sig = tuple(sorted(v.taken))
        key = (id(sub), tuple(v.special), sig)
        old = self.memo.get(key)
        if old is not None:
            return old
        first = self.sig_first.setdefault(id(sub), sig)
        if v.special or sig != first:
            k = 1
            while ("%s__vb%d" % (sub.name, k)).lower() in self.taken:
                k += 1
            v.name = "%s__vb%d" % (sub.name, k)
            self.taken.add(v.name.lower())
        self.memo[key] = v
        self.variants.setdefault(id(sub), []).append(v)
        return v

    def child_env(self, sub: Subckt, inst: Instance, env: Mapping[str, float]) -> "_Env":
        """The parameter values inside one instance of sub: the top-level values, then sub's own
        parameters in order, an override evaluated where the X line is; a parameter that is not a
        number on this path is hidden (it shadows the top-level value of its name).  The rule
        tables.path_envs applies (§4.4); the elaboration walk reads path_envs."""
        out = _Env(self.values)
        over = {k: v for k, v in inst.params.items() if k != "m"}
        for p in _plist(sub):
            e = over.get(p.name)
            try:
                out.own[p.name] = X.evaluate(e, env) if e is not None else X.evaluate(p.expr, out)
                out.hidden.discard(p.name)
            except X.EvalError:
                out.own.pop(p.name, None)
                out.hidden.add(p.name)
        return out

    def need(self, scope: T.Scope, card: Model) -> None:
        """Print card in the body that defines it (the variant on the elaboration stack)."""
        s: Optional[T.Scope] = scope
        while s is not None and s.models.get(card.name) is not card:
            s = s.parent
        owner = s.subckt if s is not None else None
        for v in reversed(self.vstack):
            if v.sub is owner:
                v.models.add(id(card))
                return

    def bin_choice(self, var: _Variant, inst: Instance, scope: T.Scope, bins: List[Model],
                   env: Mapping[str, float]) -> None:
        base = inst.master or ""
        where = " in subckt " + scope.path() if scope.in_subckt else ""
        params = self.params_of(inst, scope)
        l, w, nf = params.get("l"), params.get("w"), params.get("nf")
        spectre_group = any(b.bin_rule == "spectre" for b in bins)
        try:
            if l is None or w is None:
                raise T.TableError("%s: binned model %s needs l= and w= on the instance" % (inst.name, base))
            bounds = []
            for b in bins:
                if T.target(b).xyce_level is None:
                    raise T.TableError("%s: model %s has no Xyce equivalent" % (inst.name, b.name))
                bounds.append((b, T.bin_bounds_spectre(b, self.values) if spectre_group
                               else T.bin_bounds(b, self.values)))
        except T.TableError as exc:
            self.once(inst.origin, str(exc))
            var.bind[id(inst)] = None
            return
        if spectre_group:
            # a Spectre model group: bound to its entry on every path (group order, exact bounds,
            # the total w); Xyce never bins it natively (§4.5 item 3)
            geo = T.constant((l, w), env)
            if geo is None:
                self.once(inst.origin, "%s: model group %s needs l and w constant on the instance path on Xyce "
                          "(l=%s, w=%s)%s" % (inst.name, base, X.to_text(l), X.to_text(w), where))
                var.bind[id(inst)] = None
                return
            chosen = T.select_bin_spectre(bounds, geo[0], geo[1], self.s)
            if chosen is None:
                self.once(inst.origin, "no bin of %s for l=%.6g, w=%.6g (instance %s%s)"
                          % (base, geo[0] * self.s, geo[1] * self.s, inst.name, where))
                var.bind[id(inst)] = None
                return
            self.need(scope, chosen)
            var.bind[id(inst)] = chosen.name
            var.special.append((inst.name, chosen.name))
            return
        native = nf is None or (isinstance(nf, Num) and nf.value == 1.0)
        geo = T.constant((l, w, nf if nf is not None else Num(1.0)), env)
        if geo is None:
            if native:                          # Xyce bins natively; smoke() maps its no-bin message
                for b in bins:
                    self.need(scope, b)
                return
            self.once(inst.origin, "%s: binned model %s with nf=%s needs l, w and nf constant at compile "
                      "time on Xyce (it bins on total W, vamos on W/nf)%s" % (inst.name, base, X.to_text(nf), where))
            var.bind[id(inst)] = None
            return
        chosen = T.select_bin(bounds, geo[0], geo[1], geo[2], self.s)
        if chosen is None:
            self.once(inst.origin, "no bin of %s for l=%.6g, w=%.6g%s (instance %s%s)"
                      % (base, geo[0] * self.s, geo[1] * self.s, "" if nf is None else ", nf=%.6g" % geo[2],
                         inst.name, where))
            var.bind[id(inst)] = None
            return
        if native:
            for b in bins:
                self.need(scope, b)
            return
        self.need(scope, chosen)
        var.bind[id(inst)] = chosen.name
        var.special.append((inst.name, chosen.name))

    # -- values
    def xv(self, e: Expr, origin: str, what: str) -> str:
        """A parameter-context value: a number literal as is, anything else in {}."""
        if isinstance(e, Num) and e.value == e.value and abs(e.value) != float("inf"):
            return fmt(e.value)
        try:
            t = X.to_xyce(e, "param")
        except (X.PrintError, X.ExprError) as exc:
            self.err(origin, "%s: %s" % (what, exc))
            raise _Skip()
        return t if _LITERAL.match(t) else "{%s}" % t

    def bx(self, e: Expr, scope: T.Scope, origin: str, what: str) -> str:
        try:
            return X.to_xyce(e, "behavioral", node=lambda kind, name: self.ref(kind, name, scope))
        except (X.PrintError, X.ExprError) as exc:
            self.err(origin, "%s: %s" % (what, exc))
            raise _Skip()

    def num(self, v: object, origin: str, what: str) -> float:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        try:
            return X.evaluate(v, self.values)              # type: ignore[arg-type]
        except (X.EvalError, TypeError, AttributeError) as exc:
            self.err(origin, "%s must be a constant: %s" % (what, exc))
            raise _Skip()

    # -- names
    def ref(self, kind: str, name: str, scope: T.Scope) -> str:
        """A v()/i() argument: '.' hierarchy becomes ':'; i() follows element renames."""
        if kind != "i":
            return name.replace(".", ":")
        return printed_path(self.nl, name, scope, self.va)

    def name_ok(self, name: str, origin: str, what: str) -> bool:
        if not name or _BAD_NAME.search(name):
            self.once(origin, "%s %r cannot be written in a Xyce deck" % (what, name))
            return False
        return True

    def collide(self, names: Sequence[str], origin: str, what: str) -> None:
        """Xyce names are case-insensitive: two IR names that differ only in case would merge."""
        seen: Dict[str, str] = {}
        for n in names:
            k = n.lower()
            if k in seen and seen[k] != n:
                self.once(origin, "%s %s and %s differ only in case; Xyce would merge them"
                          % (what, seen[k], n))
            seen.setdefault(k, n)

    # -- the walk
    def render(self, notes: Optional[List[Note]]) -> str:
        top = T.Scope(self.nl.body)
        subs = [s for s in T.all_subckts(self.nl) if id(s) in self.variants]     # the used ones
        printed_models = set(self.top.models)
        for vs in self.variants.values():
            for v in vs:
                printed_models |= v.models
        self.collide([v.name for s in subs for v in self.variants[id(s)]], "", "subckts")
        self.collide([m.name for it in [self.nl.body] + [s.body for s in subs] for m in it
                      if isinstance(m, Model) and id(m) in printed_models], "", "models")
        self.collide([X.param_ident(p.name, ENGINE) for p in self.nl.body if isinstance(p, Param)], "",
                     "parameters")
        for s in subs:
            self.name_ok(s.name, s.origin, "subckt name")
        self.check_scope(self.nl.body, "", [], self.top.taken)
        self.params([it for it in self.nl.body if isinstance(it, Param)], "")
        self.var = self.top
        self.items(self.nl.body, top)
        tail = self.control()
        head = self.header()
        for idx, msg in self.nobin.items():
            self.nobin_lines[len(head) + idx + 1] = msg
        if any(n.severity == ERROR for n in self.notes):
            raise NoteError(self.notes)
        if notes is not None:
            notes.extend(self.notes)
        return "\n".join(head + self.body + tail) + "\n"

    def header(self) -> List[str]:
        title = (self.nl.title or "").strip().splitlines()
        lines = ["* " + (title[0] if title and title[0].strip() else "vamos deck"), "* generated by vamos"]
        hdl = list(self.nl.hdl)
        for p in self.osdi:
            if p.lower().endswith(".osdi"):
                self.err(p, "Xyce cannot load an OSDI file; give the .va source")
            elif p not in hdl:
                hdl.append(p)
        for p in hdl:
            lines.append('.hdl "%s"' % p)
        glob = [g for g in self.nl.globals if g != "0"]
        if glob:
            lines.append(".global " + " ".join(glob))
        return lines

    def check_scope(self, items: Sequence[object], origin: str, ports: Sequence[str],
                    taken: Optional[Set[int]] = None) -> None:
        """Name collisions in one body: on the resolved branch of every Cond (§3.7, the ref19 p.491
        duplicates in an if and its else are the same instance name on one path only)."""
        nodes = list(ports)
        insts = []
        for it in flat_items(items):
            if isinstance(it, Instance) and (taken is None or id(it) in taken):
                nodes += it.nodes
                insts.append(printed(it, self.va))
                for n in it.nodes:
                    self.name_ok(n, it.origin, "node")
        self.collide(nodes, origin, "nodes")
        self.collide(insts, origin, "elements")

    def params(self, params: Sequence[Param], origin: str) -> None:
        for p in params:
            if X.param_ident(p.name, ENGINE).lower() == MF:
                self.err(p.origin, "parameter %s is reserved by vamos" % MF)
                continue
            try:
                self.body.append(".param %s=%s" % (X.param_ident(p.name, ENGINE),
                                                   self.xv(p.expr, p.origin, "parameter " + p.name)))
            except _Skip:
                pass

    def items(self, items: Sequence[object], scope: T.Scope) -> None:
        for it in items:
            try:
                if isinstance(it, Model):
                    if id(it) in self.var.models:          # unused cards are never printed
                        self.model(it)
                elif isinstance(it, Subckt):
                    for v in self.variants.get(id(it), ()):
                        self.subckt(it, scope, v)
                elif isinstance(it, Instance):
                    self.instance(it, scope)
                elif isinstance(it, Cond):
                    self.cond_items(it, scope)
                elif isinstance(it, (Param, ParamTest)):
                    continue
                else:
                    raise T.TableError("%s: item %r is not an IR item (ir.ITEM_TYPES)"
                                       % (scope.path() or "top level", it))
            except _Skip:
                pass
            except (T.TableError, X.PrintError) as exc:
                self.err(getattr(it, "origin", ""), str(exc))

    def cond_items(self, c: Cond, scope: T.Scope) -> None:
        """The instances of the branch this body's path takes (§4.5 item 2): the elaboration walk
        (tables.path_envs) resolved every Cond per path into _Variant.taken."""
        for it in flat_items([c]):
            if isinstance(it, Instance) and id(it) in self.var.taken:
                try:
                    self.instance(it, scope)
                except _Skip:
                    pass
                except (T.TableError, X.PrintError) as exc:
                    self.err(it.origin, str(exc))

    def subckt(self, s: Subckt, parent: T.Scope, var: _Variant) -> None:
        scope = T.Scope(s.body, parent, s)
        first = id(s) not in self.checked
        self.checked.add(id(s))
        self.check_scope(s.body, s.origin, s.ports, var.taken)
        if first:
            for p in s.ports:
                self.name_ok(p, s.origin, "port")
        plist = _plist(s)
        if first:
            self.collide([X.param_ident(p.name, ENGINE) for p in plist], s.origin, "parameters")
        hdr = []
        for p in plist:
            pid = X.param_ident(p.name, ENGINE)
            if pid.lower() == MF:
                if first:
                    self.err(p.origin, "subckt %s: parameter %s is reserved by vamos" % (s.name, MF))
                continue
            try:
                hdr.append("%s=%s" % (pid, self.xv(p.expr, p.origin, "parameter " + p.name)))
            except _Skip:
                pass
        hdr.append("%s=1.0" % MF)
        self.body.append(".subckt %s %s params: %s" % (var.name, " ".join(s.ports), " ".join(hdr)))
        outer, self.var = self.var, var
        try:
            self.items(s.body, scope)
        finally:
            self.var = outer
        self.body.append(".ends %s" % var.name)

    def model(self, m: Model) -> None:
        row = T.target(m)
        if row.xyce_level is None:
            self.err(m.origin, "model %s: %s" % (m.name, row.describe or "no Xyce equivalent"))
            return
        if not self.name_ok(m.name, m.origin, "model name"):
            return
        params, notes = T.model_params(m, ENGINE, binned=m.base is not None, options=self.nl.options,
                                       dialect=self.nl.dialect)
        self.add(notes)
        if row.strip_version and any(k == "version" for k, _ in params):
            self.add([note(m.origin, "%s: BSIM3 card simulated as Xyce BSIM3v3.2.2 (level 9)" % T.card_label(m))])
        out = ["level=%d" % row.xyce_level] if row.xyce_print_level else []
        for k, v in params:
            out.append("%s=%s" % (k, self.xv(v, m.origin, "model %s parameter %s" % (m.name, k))))
        self.body.append(".model %s %s%s" % (m.name, m.kind, "".join(" " + o for o in out)))

    # -- instances
    def params_of(self, inst: Instance, scope: T.Scope) -> Dict[str, Expr]:
        """The instance's parameters as printed: on a Spectre netlist after MasterRow.instance (the
        renames, instance_<name> without the prefix, the written defaults, the strips with their
        notes; an error row raises); the IR's otherwise."""
        if not self.spectre:
            return inst.params
        cached = self.iparams.get(id(inst))
        if cached is not None:
            return cached
        card = scope.model(inst.master) if inst.kind in T.MODEL_KINDS_OF and inst.master else None
        if card is None and inst.kind == "m" and inst.master:
            bins = scope.bins(inst.master) or []
            card = bins[0] if bins else None
        master = T.spectre_master_of(inst, card)
        params, notes = T.spectre_instance_params(inst, master, ENGINE)
        self.add(notes)
        self.iparams[id(inst)] = params
        return params

    def instance(self, inst: Instance, scope: T.Scope) -> None:
        k = inst.kind
        want = T.TERMINALS.get(k)
        if k == "q":
            want = len(inst.nodes) if len(inst.nodes) in (3, 4) else 4
        if want is not None and len(inst.nodes) != want:
            self.err(inst.origin, "%s element %s has %d terminals, expected %d"
                     % (k, inst.name, len(inst.nodes), want))
            return
        fn = getattr(self, "i_" + k, None)
        if fn is None:
            self.err(inst.origin, "element kind %r of %s is not supported" % (k, inst.name))
            return
        if self.spectre:                           # the Spectre rules first (a stripped stretch=1, tc1=0)
            T.check_params(Instance(inst.name, k, [], params=self.params_of(inst, scope)))
        else:
            T.check_params(inst)
        fn(inst, scope)

    def line(self, inst: Instance, fields: Sequence[str]) -> None:
        self.body.append(" ".join([printed(inst, self.va)] + [f for f in fields if f]))

    def mult(self, inst: Instance, scope: T.Scope) -> Optional[Expr]:
        return T.multiplier(self.params_of(inst, scope).get("m"), scope.in_subckt, ENGINE)

    def mparam(self, inst: Instance, scope: T.Scope) -> List[str]:
        m = self.mult(inst, scope)
        if m is None or T.MULT[ENGINE][inst.kind] != "param":
            return []
        return ["m=" + self.xv(m, inst.origin, "multiplier of " + inst.name)]

    def plist(self, inst: Instance, skip: Sequence[str] = ("m",), element: str = "",
              level: Optional[int] = None, scope: Optional[T.Scope] = None) -> List[str]:
        out = []
        params = self.params_of(inst, scope) if scope is not None else inst.params
        for k, v in params.items():
            if k in skip:
                continue
            v = T.scaled(element or inst.kind, k, v, self.s, level)
            out.append("%s=%s" % (k, self.xv(v, inst.origin, "%s parameter %s" % (inst.name, k))))
        return out

    def card(self, inst: Instance, scope: T.Scope) -> Model:
        if not inst.master:
            self.err(inst.origin, "%s needs a model" % inst.name)
            raise _Skip()
        m = scope.model(inst.master)
        if m is None:
            self.err(inst.origin, "%s: model %s is not defined" % (inst.name, inst.master))
            raise _Skip()
        if m.kind not in T.MODEL_KINDS_OF[inst.kind]:
            self.err(inst.origin, "%s: model %s is a %s card, not a %s model"
                     % (inst.name, m.name, m.kind, T.ELEMENT_NAME[inst.kind]))
            raise _Skip()
        if T.target(m).xyce_level is None:
            self.err(inst.origin, "%s: model %s has no Xyce equivalent" % (inst.name, m.name))
            raise _Skip()
        return m

    def i_r(self, inst: Instance, scope: T.Scope) -> None:
        self.rcl(inst, scope)

    def i_c(self, inst: Instance, scope: T.Scope) -> None:
        self.rcl(inst, scope)

    def i_l(self, inst: Instance, scope: T.Scope) -> None:
        self.rcl(inst, scope)

    def rcl(self, inst: Instance, scope: T.Scope) -> None:
        params_in = self.params_of(inst, scope)
        val, skip, mparam = inst.value, ("m",), None
        if "ic" in params_in:
            if not self.spectre:
                self.err(inst.origin, "%s: IC= on a %s is not supported (use .ic)"
                         % (inst.name, T.ELEMENT_NAME[inst.kind]))
                return
            # Spectre netlists: plan.build refuses a device ic= unless every tran uses ic=node or ic=dc,
            # under which Spectre does not apply it (§0, §5.4), so it is not printed (nor applied by UIC)
            skip = ("m", "ic")
        if self.spectre and inst.kind == "r" and isinstance(val, Num) and val.value == float("inf"):
            return                                      # §3.8: an infinite resistor is not printed (spectre.py notes it)
        card = self.card(inst, scope) if inst.master is not None else None
        master = card.name if card is not None else ""
        if self.spectre and inst.kind == "r" and "isnoisy" in params_in:
            skip = skip + ("isnoisy",)
            if _is_no(params_in["isnoisy"]):
                if any(k in params_in for k in ("tc1", "tc2")):
                    self.add([warning(inst.origin or inst.name, "%s: isnoisy=no with tc1/tc2: Xyce has no resistor "
                                      "noise switch, and the noise-free G form has no temperature coefficient; "
                                      "the resistor stays an R and noisy" % inst.name)])
                elif isinstance(val, Num) and val.value == 0.0:
                    pass                                # r = 0: a short has no noise; R=0 is Xyce's short [E22]
                elif val is not None and card is None:
                    # the same current with no noise: G<name> a b a b {1/r} (§4.5 item 7) [E32]
                    g = Binary("/", Num(1.0), val)
                    m = self.mult(inst, scope)
                    if m is not None:
                        g = Binary("*", g, m)
                    self.body.append(" ".join(["g" + inst.name] + inst.nodes + inst.nodes
                                              + [self.xv(X.fold(g), inst.origin, "conductance of " + inst.name)]))
                    return
                else:
                    self.add([warning(inst.origin or inst.name, "%s: isnoisy=no on a resistor with a model card: "
                                      "Xyce has no resistor noise switch; the resistor stays noisy" % inst.name)])
        if inst.kind == "l" and inst.name in scope.coupled:
            # Xyce's K pass keeps only L and IC of a coupled inductor: its multiplier and TCs are
            # folded into the value (tables.coupled_inductance)
            folded = T.coupled_inductance(inst, self.mult(inst, scope), card, *T.run_temps(self.nl))
            if folded is not None:
                val, skip, mparam = folded, T.COUPLED_FOLDED + (("ic",) if "ic" in skip else ()), []
        if val is None and not master:
            self.err(inst.origin, "%s has no value" % inst.name)
            return
        value = self.xv(val, inst.origin, "value of " + inst.name) if val is not None else ""
        self.line(inst, inst.nodes + [master, value] + self.plist(inst, skip, scope=scope)
                  + (self.mparam(inst, scope) if mparam is None else mparam))

    def i_k(self, inst: Instance, scope: T.Scope) -> None:
        if len(inst.ctrl) != 2 or inst.value is None:
            self.err(inst.origin, "mutual inductance %s needs two inductors and a coupling" % inst.name)
            return
        self.line(inst, [printed_path(self.nl, inst.ctrl[0], scope, self.va),
                         printed_path(self.nl, inst.ctrl[1], scope, self.va),
                         self.xv(inst.value, inst.origin, "coupling of " + inst.name)])

    def i_v(self, inst: Instance, scope: T.Scope) -> None:
        self.line(inst, inst.nodes + self.source(inst, inst.source or Source(), scope))

    def i_i(self, inst: Instance, scope: T.Scope) -> None:
        src = inst.source or Source()
        m = self.mult(inst, scope)
        if m is not None and not src.code_uri:
            src = T.scale_source(src, m)              # Xyce ignores m= on current sources
        self.line(inst, inst.nodes + self.source(inst, src, scope))

    def i_e(self, inst: Instance, scope: T.Scope) -> None:
        self.controlled(inst, scope)

    def i_g(self, inst: Instance, scope: T.Scope) -> None:
        self.controlled(inst, scope)

    def i_f(self, inst: Instance, scope: T.Scope) -> None:
        self.controlled(inst, scope)

    def i_h(self, inst: Instance, scope: T.Scope) -> None:
        self.controlled(inst, scope)

    def controlled(self, inst: Instance, scope: T.Scope) -> None:
        if inst.value is None:
            self.err(inst.origin, "%s has no gain" % inst.name)
            return
        gain = inst.value
        if inst.kind == "f":
            m = self.mult(inst, scope)
            if m is not None:
                gain = T.times(gain, m)                # Xyce ignores m= on F lines
        fields = list(inst.nodes)
        if inst.kind in ("f", "h"):
            if len(inst.ctrl) != 1:
                self.err(inst.origin, "%s needs one controlling voltage source" % inst.name)
                return
            fields.append(printed_path(self.nl, inst.ctrl[0], scope, self.va))
        fields.append(self.xv(gain, inst.origin, "gain of " + inst.name))
        self.line(inst, fields + self.mparam(inst, scope))

    def i_b(self, inst: Instance, scope: T.Scope) -> None:
        if inst.expr is None or inst.expr_kind not in ("v", "i"):
            self.err(inst.origin, "behavioral source %s has no v= or i= expression" % inst.name)
            return
        bad = T.current_ref_errors(inst, scope.subckt.body if scope.subckt else self.nl.body, scope)
        for msg in bad:
            self.err(inst.origin, msg)
        if bad:
            return
        e = inst.expr
        if inst.expr_kind == "i":
            m = self.mult(inst, scope)
            if m is not None:
                e = Binary("*", e, m)
        text = self.bx(e, scope, inst.origin, "expression of " + inst.name)
        self.line(inst, inst.nodes + ["%s={%s}" % (inst.expr_kind.upper(), text)])

    def i_d(self, inst: Instance, scope: T.Scope) -> None:
        m = self.card(inst, scope)
        lv = T.level_of(m)                       # .option scale reaches a LEVEL 3 diode only
        area = (self.xv(T.scaled("d", "area", inst.value, self.s, lv), inst.origin, "area of " + inst.name)
                if inst.value is not None else "")
        if self.spectre and area:
            # a positional area followed by any parameter (the m={vamos_mfactor} of a diode inside a
            # subckt) is "Unrecognized fields for device D1" on Xyce (rc 1); AREA= is accepted (phase 2)
            area = "AREA=" + area
        self.line(inst, inst.nodes + [m.name, area] + self.plist(inst, ("m", "area"), level=lv, scope=scope)
                  + self.mparam(inst, scope))

    def i_q(self, inst: Instance, scope: T.Scope) -> None:
        m = self.card(inst, scope)
        area = self.xv(inst.value, inst.origin, "area of " + inst.name) if inst.value is not None else ""
        self.line(inst, inst.nodes + [m.name, area] + self.plist(inst, ("m", "area"), scope=scope)
                  + self.mparam(inst, scope))

    def i_j(self, inst: Instance, scope: T.Scope) -> None:
        m = self.card(inst, scope)
        area: Optional[Expr] = inst.value
        k = self.mult(inst, scope)
        if k is not None:
            area = T.times(area if area is not None else Num(1.0), k)   # J takes no m=: area is equivalent
        text = self.xv(area, inst.origin, "area of " + inst.name) if area is not None else ""
        self.line(inst, inst.nodes + [m.name, text] + self.plist(inst, ("m", "area"), scope=scope))

    def i_m(self, inst: Instance, scope: T.Scope) -> None:
        if not self.spectre:
            self.add(T.mos_scale_warnings(inst, self.nl.options))
        card, bins = scope.card_of(inst)
        if card is None and bins:
            if id(inst) in self.var.bind:        # bound to the bin card vamos selected
                master = self.var.bind[id(inst)]
                if master is None:
                    raise _Skip()                # the elaboration recorded the error
            else:                                # Xyce bins natively; it names no bin if none fits
                master = inst.master or ""
                params = self.params_of(inst, scope)
                msg = ("no bin of %s for l=%s, w=%s (scale %s; instance %s%s)"
                       % (master, X.to_text(params["l"]), X.to_text(params["w"]), fmt(self.s),
                          inst.name, " in subckt " + scope.path() if scope.in_subckt else ""))
                self.nobin[len(self.body)] = msg
                self.nobin_dev[(master.upper(), printed(inst, self.va).upper())] = msg
        else:
            m = self.card(inst, scope)
            if not self.spectre:
                self.add(T.mos_junction_warnings(inst, m, self.nl.options))
            master = m.name
        self.line(inst, inst.nodes + [master] + self.plist(inst, scope=scope) + self.mparam(inst, scope))

    def i_x(self, inst: Instance, scope: T.Scope) -> None:
        sub = scope.find_subckt(inst.master or "")
        if sub is None:
            self.err(inst.origin, "%s: subckt %s is not defined" % (inst.name, inst.master))
            return
        if len(inst.nodes) != len(sub.ports):
            self.err(inst.origin, "%s: %d nodes for subckt %s with %d ports"
                     % (inst.name, len(inst.nodes), sub.name, len(sub.ports)))
            return
        if self.spectre and not sub.ports:
            # measured (Xyce DEVELOPMENT-202610100047, phase 2): the instances of a subckt without ports
            # share its internal nodes, and V(X1:n) names none of them; §0's one rule makes it an error
            self.err(inst.origin, "%s: subckt %s has no ports: on Xyce the instances of a portless subckt share "
                     "its internal nodes; not supported in v1 on Xyce (VACASK runs it)" % (inst.name, sub.name))
            return
        master = self.var.bind.get(id(inst), sub.name)
        if master is None:
            raise _Skip()                        # the elaboration recorded the error
        fields = list(inst.nodes) + [master]
        path = self.path_of(inst, scope)
        hooked = {p for (ipath, p) in self.hooks if ipath == path} if path is not None else set()
        for k, v in inst.params.items():
            if k == "m":
                continue
            if k in hooked:
                fields.append("%s={%s}" % (X.param_ident(k, ENGINE), self.hooks[(path, k)][0]))   # type: ignore[index]
                continue
            fields.append("%s=%s" % (X.param_ident(k, ENGINE), self.xv(v, inst.origin, "%s parameter %s"
                                                                          % (inst.name, k))))
        for k in sorted(hooked - set(inst.params)):        # a swept default the X line did not override
            fields.append("%s={%s}" % (X.param_ident(k, ENGINE), self.hooks[(path, k)][0]))   # type: ignore[index]
        m = self.mult(inst, scope)
        if m is not None:
            fields.append("%s=%s" % (MF, self.xv(m, inst.origin, "multiplier of " + inst.name)))
        self.line(inst, fields)

    def path_of(self, inst: Instance, scope: T.Scope) -> Optional[str]:
        """The IR path of an instance for the sweep hooks and the noise input: a top-level instance's
        name; inside a subckt the hook path (or the hierarchical noise input, §5.6) whose last name is the
        instance's and whose parent X line instantiates this body (another subckt's instance of the same
        name is not on it); _one_path has refused a body that several paths reach (§5.3)."""
        if not self.hooks and self.noise_input is None:
            return None
        if not scope.in_subckt:
            return inst.name
        cands = [path for (path, _p) in self.hooks]
        if self.noise_input is not None:
            cands.append(self.noise_input)
        for path in cands:
            parts = path.split(".")
            if len(parts) < 2 or parts[-1] != inst.name:
                continue
            parent = _resolve(self.nl, ".".join(parts[:-1]))     # the X line whose body this is
            if parent is not None and scope.subckt is not None and parent.master == scope.subckt.name:
                return path
        return None

    def i_y(self, inst: Instance, scope: T.Scope) -> None:
        module = inst.master or ""
        if module == T.GCOND_MODULE:
            self.gcond(inst, scope)
            return
        if not module:
            self.err(inst.origin, "Verilog-A instance %s has no module" % inst.name)
            return
        va = self.va.get(module.lower())
        letter = T.va_letter(va) if va is not None else None
        multiplied = "m" in inst.params or (scope.in_subckt and scope.subckt.name in self.multiplied)
        if letter is not None and va is not None:
            # the letter-device route (§4.5 item 13): PyMS registers the module as that device [E64]
            if multiplied:
                self.err(inst.origin, "%s: a Verilog-A device takes no multiplier on Xyce (m= on it or on "
                         "an enclosing X instance); VACASK honours it" % inst.name)
                return
            _letter, kinds, level = letter
            params = dict(self.params_of(inst, scope))
            kind, consumed = T.va_polarity(va, params)
            if consumed is not None:
                params.pop(consumed)
            card = "%s__vamos" % module + ("" if kind == kinds[0] else "_" + kind)
            if card not in self.cards:
                self.cards.add(card)
                self.body.append(".model %s %s level=%d" % (card, kind, level))
            fields = list(inst.nodes) + [card]
            for k, v in params.items():
                fields.append("%s=%s" % (k, self.xv(v, inst.origin, "%s parameter %s" % (inst.name, k))))
            self.line(inst, fields)
            return
        extra = [k for k in inst.params if k != "m"]
        if extra:
            self.err(inst.origin, "%s: Verilog-A parameters (%s) on Xyce: PyMS ignores parameter "
                     "overrides, so they are not supported (VACASK honours them)"
                     % (inst.name, ", ".join(extra)))
            return
        if multiplied:
            self.err(inst.origin, "%s: a Verilog-A (Y) device takes no multiplier on Xyce (m= on it or on "
                     "an enclosing X instance); VACASK honours it" % inst.name)
            return
        card = "%s__vamos" % module
        if card not in self.cards:
            self.cards.add(card)
            self.body.append(".model %s %s" % (card, module))
        self.body.append(" ".join(["y%s" % module, inst.name] + list(inst.nodes) + [card]))

    def gcond(self, inst: Instance, scope: T.Scope) -> None:
        """i(nd -> n) = v(ne)*(v(nd)-v(n))/rr as a B source from nd to n (vamos_ie.va's law)."""
        if len(inst.nodes) != 3:
            self.err(inst.origin, "%s: %s has terminals (n, nd, ne)" % (inst.name, T.GCOND_MODULE))
            return
        n, nd, ne = inst.nodes
        rr = inst.params.get("rr", Num(T.GCOND_RR))
        law = Binary("/", Binary("*", Call("v", (Name(ne),)),
                                 Binary("-", Call("v", (Name(nd),)), Call("v", (Name(n),)))), rr)
        text = self.bx(law, scope, inst.origin, "gated conductance " + inst.name)
        self.body.append(" ".join([printed(inst, self.va), nd, n, "I={%s}" % text] + self.mparam(inst, scope)))

    # -- sources (§4.3.8; Spectre: VAMOS_SPECTRE_DESIGN.md §4.5 item 8)
    def source(self, inst: Instance, src: Source, scope: Optional[T.Scope] = None) -> List[str]:
        o = inst.origin
        if src.code_uri is not None:
            if src.dc is not None or src.ac is not None or src.wave is not None:
                self.err(o, "%s: a bridge source has no dc/ac/wave" % inst.name)
                raise _Skip()
            try:
                init = T.parse_code_uri(src.code_uri)[0]
            except T.TableError as exc:
                self.err(o, "%s: %s" % (inst.name, exc))
                raise _Skip()
            if init != T.BRIDGE_INIT[ENGINE]:
                self.err(o, "%s: code URI %s is not for Xyce (%s)" % (inst.name, src.code_uri,
                                                                      T.BRIDGE_INIT[ENGINE]))
                raise _Skip()
            return ['PWL FILE "%s"' % src.code_uri]
        xv = lambda e, what: self.xv(e, o, "%s %s" % (inst.name, what))   # noqa: E731
        path = self.path_of(inst, scope) if scope is not None else None
        hooked = {p: h[0] for (ipath, p), h in self.hooks.items() if ipath == path} if path is not None else {}
        if hooked:                                 # the swept fields read their .param hooks [E71]
            src = _hooked_source(src, hooked)
        out: List[str] = []
        if src.dc is not None:
            out.append("DC " + xv(src.dc, "dc"))
        elif src.wave is None:
            out.append("DC 0.0")
        elif self.spectre:
            t0 = T.wave_t0(src)                    # Xyce's operating point without DC is 0, not t = 0 [E70]
            if t0 is not None:
                out.append("DC " + xv(t0, "dc (the t = 0 value of the waveform)"))
        if self.noise_input is not None and self.noise_input == (path or inst.name):
            out.append("AC 1 0")                   # the noise step's input: INOISE per unit input (§8.4) [E19]
        elif self.spectre and self.noise_input is not None:
            # Spectre's noise gain is from the input probe alone, while Xyce's .NOISE drives every AC source
            # (measured in phase 2: gain 0.8 for 0.6 with a second source of mag=1); the noise deck is the
            # step's own (§7.2), so the other sources print no AC (ONOISE does not depend on them)
            pass
        elif src.ac is not None:
            out.append("AC %s %s" % (xv(src.ac[0], "ac magnitude"), xv(src.ac[1], "ac phase")))
        w = src.wave
        if w is not None:
            try:
                f = T.wave(src, inst.name)
            except T.TableError as exc:
                self.err(o, str(exc))
                raise _Skip()
            if w == "pulse":
                keys = ["v1", "v2", "td", "tr", "tf", "pw"] + (["per"] if "per" in f else [])
                out.append("PULSE(%s)" % " ".join(xv(f[k], k) for k in keys))
            elif w == "sin":
                out.append("SIN(%s)" % " ".join(xv(f[k], k) for k in WAVE["sin"]))
            elif w == "exp":
                out.append("EXP(%s)" % " ".join(xv(f[k], k) for k in WAVE["exp"]))
            else:
                # Xyce's PWL TD= gives 0 before the delay (PWLinData::updateSource), where HSPICE
                # and VACASK hold the first value; the step at TD also stalls Xyce's time
                # stepping.  So the delay is added to every time point instead; Xyce holds the
                # first value from 0 whenever the first time is after 0 (its PWL constructor).
                td = f.get("td")
                if isinstance(td, Num) and td.value == 0.0:
                    td = None
                pts = []
                for t, v in src.points:
                    if td is not None:
                        t = Num(t.value + td.value) if isinstance(t, Num) and isinstance(td, Num) \
                            else Binary("+", t, td)
                    pts.append("%s %s" % (xv(t, "time"), xv(v, "value")))
                out.append(" ".join(["PWL"] + pts))
        return out

    # -- control (§4.5)
    def control(self) -> List[str]:
        if self.step is not None:
            try:
                return self.step_control(self.step)
            except _Skip:
                return []
            except T.TableError as exc:
                self.err(self.step.id, str(exc))
                return []
        nl, o = self.nl, "control"
        spice = "spice" in nl.options or self.spectre              # Spectre's defaults are 27 (§3.10)
        temp = nl.temp if nl.temp is not None else (27.0 if spice else 25.0)
        tnom = nl.tnom if nl.tnom is not None else (27.0 if spice else 25.0)
        lines = []
        try:
            opts = ["temp=" + fmt(self.num(temp, o, "temp")), "tnom=" + fmt(self.num(tnom, o, "tnom"))]
            if self.spectre:                                       # no HSPICE tolerance notes (§3.10, §4.4)
                solver = {"gmin": self.num(nl.options["gmin"], o, "gmin")} if "gmin" in nl.options else {}
            else:
                solver, notes = T.solver_options(nl, o)
                self.notes.extend(notes)
            if "gmin" in solver:
                opts.append("gmin=" + fmt(solver["gmin"]))
            lines.append(".options device " + " ".join(opts))
            if solver.get("method") == "gear":
                lines.append(".options timeint method=gear")
        except _Skip:
            pass
        except T.TableError as exc:
            self.err(o, str(exc))
        ics = self.pairs(nl.ics, ".ic")
        if ics:
            lines.append(".ic " + ics)
        nodesets = self.pairs(nl.nodesets, ".nodeset")
        if nodesets:
            if ics:
                self.notes.append(note(o, ".nodeset dropped: Xyce cannot take .ic and .nodeset together"))
            else:
                lines.append(".nodeset " + nodesets)
        if self.op:
            lines.append(".op")
        else:
            lines.append(self.tran())
            lines.append(".print tran format=raw file=%s %s" % (RAW, " ".join(self.prints() or ["v(*)"])))
        lines.append(".end")
        return lines

    def tran(self) -> str:
        a = self.nl.tran()
        if a is None:
            self.err("", "the netlist has no .tran analysis (ams/deck.py synthesises one)")
            return ".tran"
        args, vals = a.args, []
        try:
            for key in ("step", "stop"):
                if args.get(key) is None:
                    self.err(a.origin, ".tran has no %s" % key)
                    raise _Skip()
                vals.append(fmt(self.num(args[key], a.origin, ".tran " + key)))
            start = args.get("start") or 0.0
            if args.get("maxstep") is not None:
                vals += [fmt(self.num(start, a.origin, ".tran start")),
                         fmt(self.num(args["maxstep"], a.origin, ".tran maxstep"))]
            elif start:
                vals.append(fmt(self.num(start, a.origin, ".tran start")))
        except _Skip:
            pass
        return ".tran " + " ".join(vals + (["UIC"] if args.get("uic") else []))

    def pairs(self, d: Dict[str, float], what: str) -> str:
        out = []
        for node, v in d.items():
            try:
                out.append("v(%s)=%s" % (node.replace(".", ":"), fmt(self.num(v, what, "%s v(%s)" % (what, node)))))
            except _Skip:
                pass
        return " ".join(out)

    def prints(self) -> List[str]:
        out: List[str] = []
        top = T.Scope(self.nl.body)
        for analysis, kind, target in self.nl.probes:
            if analysis.lower() != "tran":
                continue
            if kind == "v":
                p = "v(*)" if target == "*" else "v(%s)" % ",".join(t.strip().replace(".", ":")
                                                                  for t in target.split(","))
            elif kind == "i":
                p = "i(%s)" % printed_path(self.nl, target, top, self.va)
            else:
                self.err("probe", "unknown probe kind %r" % (kind,))
                continue
            if p not in out:
                out.append(p)
        return out

    # -- one plan step (VAMOS_SPECTRE_DESIGN.md §4.5 items 5-6, §7.2) --------------------------
    def node_text(self, path: str) -> str:
        """An IR node path as Xyce's column spells it (§4.5 item 15, §6.4): V(<printed_path(a.b)>:<N>)
        upper-cased, IR I2.Mid being V(XI2:MID) [E84]."""
        parts = path.split(".")
        if len(parts) == 1:
            return path.upper()
        top = T.Scope(self.nl.body)
        return (printed_path(self.nl, ".".join(parts[:-1]), top, self.va) + ":" + parts[-1]).upper()

    def element_text(self, path: str) -> str:
        return printed_path(self.nl, path, T.Scope(self.nl.body), self.va).upper()

    def column(self, ref: Ref, step_kind: str) -> Optional[str]:
        """The .PRINT column of a Ref (§6.4), None for one Xyce has no column for."""
        kind = ref[0]
        if kind == "v":
            return "V(%s)" % self.node_text(ref[1])
        if kind == "i":
            inst = _resolve(self.nl, ref[1])
            if inst is not None and inst.kind == "r" and self.spectre and _is_no(inst.params.get("isnoisy", Num(1.0))) \
                    and not any(k in inst.params for k in ("tc1", "tc2")) \
                    and not (isinstance(inst.value, Num) and inst.value.value == 0.0):   # r=0 stays an R (rcl)
                raise T.TableError("%s: the current of the noise-free G form of %s needs a probe (signals.resolve)"
                                   % (ref[1], ref[1]))
            return "I(%s)" % self.element_text(ref[1])
        if kind == "noise":
            return "DNO(%s)" % self.element_text(ref[1])
        if kind == "onoise":
            return "ONOISE"
        if kind in ("inoise", "gain"):
            return "INOISE"                        # gain = sqrt(ONOISE/INOISE) (§8.4)
        if kind == "tf":
            raise T.TableError("xf (%s) runs on VACASK only (§3.11)" % (ref,))
        return None

    def name_map(self, step_id: str, step_kind: str = "") -> List[str]:
        """Fill names[step_id] from the SignalMap and return the .PRINT items in order."""
        sm = self.sigmap
        if sm is None:
            return []
        out = self.names.setdefault(step_id, {})
        cols: List[str] = []
        for ref, _spectre_name, _psf in sm.per_step.get(step_id, []):
            col = self.column(ref, step_kind)
            if col is None or ref[0] == "gain":
                if col is not None:
                    out[ref] = col
                continue
            out[ref] = col
            if col not in cols:
                cols.append(col)
        return cols

    def setting_text(self, v: object, origin: str, what: str) -> str:
        if isinstance(v, bool):
            return "1" if v else "0"
        if isinstance(v, (int, float)):
            return fmt(float(v))
        if isinstance(v, str):
            return v
        self.err(origin, "%s: value %r is not a plan setting" % (what, v))
        raise _Skip()

    def target_text(self, lv: SweepLevel) -> str:
        """The .STEP/.DC variable of a sweep level: R1:R, X2:R1:R, NCH:VTH0, TEMP, a .param, or a hook."""
        kind, name, param = lv.target
        if kind == "instance":
            hook = self.hook_name(name, param)
            if hook is not None:
                return hook
            inst = _resolve(self.nl, name)
            if inst is None:
                raise T.TableError("sweep %s: no instance %s" % (lv.id, name))
            return "%s:%s" % (self.element_text(name), self.instance_param(inst, param).upper())
        if kind == "model":
            m = self.nl.models().get(name)
            if m is None:
                raise T.TableError("sweep %s: a model group (%s) cannot be swept on Xyce in v1" % (lv.id, name))
            p = param
            if self.spectre and m.prim in T.SPECTRE_MASTERS:
                p = _renamed(T.SPECTRE_MASTERS[m.prim].params, param, ENGINE)
            return "%s:%s" % (name.upper(), p.upper())
        if kind == "option":
            return name.upper()
        if kind == "variable":
            return X.param_ident(name, ENGINE)
        raise T.TableError("sweep %s: target kind %r is not a .STEP/.DC variable" % (lv.id, kind))

    def instance_param(self, inst: Instance, param: str) -> str:
        if not self.spectre:
            return param
        scope = T.Scope(self.nl.body)
        card = scope.model(inst.master) if inst.kind in T.MODEL_KINDS_OF and inst.master else None
        master = T.spectre_master_of(inst, card)
        return _renamed(T.SPECTRE_MASTERS[master].instance if master in T.SPECTRE_MASTERS else (), param, ENGINE)

    def sweep_text(self, lv: SweepLevel, keyword: str) -> str:
        """`.STEP`/`.DC` with the level's grid: `<T> start stop step`, `<T> LIST ...`, `DEC <T> start stop n`."""
        spec = lv.spec
        target = self.target_text(lv)
        if spec.mode in ("values", "log"):             # log: the values plan.build computed (§5.2)
            return "%s %s LIST %s" % (keyword, target, " ".join(fmt(_numv(v)) for v in spec.values))
        start, stop = _numv(spec.start), _numv(spec.stop)
        if spec.mode == "dec":
            if spec.count is None:
                raise T.TableError("sweep %s: dec without a point count" % lv.id)
            return "%s DEC %s %s %s %d" % (keyword, target, fmt(start), fmt(stop), int(spec.count))
        if spec.mode == "step":
            return "%s %s %s %s %s" % (keyword, target, fmt(start), fmt(stop), fmt(_numv(spec.step)))
        if spec.mode == "lin":
            if not spec.count:
                raise T.TableError("sweep %s: lin without a step count" % lv.id)
            return "%s %s %s %s %s" % (keyword, target, fmt(start), fmt(stop), fmt((stop - start) / float(spec.count)))
        raise T.TableError("sweep %s: mode %r is not one render prints (lin, step, values, log, dec)" % (lv.id, spec.mode))

    def freq_text(self, lv: SweepLevel, data_name: str, lines: List[str]) -> str:
        """The frequency grid of .AC/.NOISE: DEC n f1 f2, LIN N+1 f1 f2, or DATA=<table> with a .DATA
        block appended to lines [E21]."""
        spec = lv.spec
        if spec.mode == "dec" and spec.count:
            return "DEC %d %s %s" % (int(spec.count), fmt(_numv(spec.start)), fmt(_numv(spec.stop)))
        if spec.mode == "lin" and spec.count:
            return "LIN %d %s %s" % (int(spec.count) + 1, fmt(_numv(spec.start)), fmt(_numv(spec.stop)))
        if spec.mode in ("values", "log"):             # log: the values plan.build computed (§5.2)
            values = [_numv(v) for v in spec.values]
        elif spec.mode == "step":
            start, stop, step = _numv(spec.start), _numv(spec.stop), _numv(spec.step)
            values, f = [], start
            while f <= stop * (1 + 1e-12) and step > 0:
                values.append(f)
                f += step
        else:
            raise T.TableError("sweep %s: mode %r is not one render prints" % (lv.id, spec.mode))
        lines.append(".data %s" % data_name)
        lines.append("+ hertz")
        lines.extend("+ %s" % fmt(v) for v in values)
        lines.append(".enddata")
        return "DATA=%s" % data_name

    def step_control(self, step: AnalysisStep) -> List[str]:
        o = step.id
        lines: List[str] = []
        so = step.options                               # the global Spectre options in force (§5.1)
        temp = so.get("temp", 27.0 if self.nl.temp is None else self.nl.temp)
        tnom = so.get("tnom", 27.0 if self.nl.tnom is None else self.nl.tnom)
        dev = ["temp=" + fmt(self.num(temp, o, "temp")), "tnom=" + fmt(self.num(tnom, o, "tnom"))]
        if "gmin" in so:
            dev.append("gmin=" + fmt(self.num(so["gmin"], o, "gmin")))
        for key in ("reltol", "vabstol", "iabstol", "chargeabstol"):
            if key in so:
                self.add([note(o, "option %s=%s is not mapped on Xyce: its tolerances do not have SPICE's "
                                  "meaning, so the engine runs with its defaults (§0, §3.10)"
                               % (key, self.setting_text(so[key], o, key)))])
        lines.append(".options device " + " ".join(dev))
        if step.kind == "tran":
            method = step.args.get("method")
            if isinstance(method, str):
                row = T.XYCE_METHODS.get(method.lower())
                if row is None:
                    raise T.TableError("%s: tran method %s has no Xyce equivalent" % (o, method))
                lines.append(".options timeint method=%s%s" % (row[0], "" if row[1] is None else " maxord=%d" % row[1]))
        for (path, param), (hook, nominal) in self.hooks.items():
            lines.append(".param %s=%s" % (hook, self.xv(nominal, o, "hook of %s %s" % (path, param))))
        ics = ""
        if step.kind == "tran" and step.args.get("ic", "all") in ("all", "node"):
            table = T.state_file_nodes(step.args, T.STATE_IC_KEYS)   # readic=/read= replaces the ic statements
            ics = self.step_pairs(self.nl.ics if table is None else table, ".ic")   # Xyce applies .IC to every DC solve [E18]
            if ics:
                lines.append(".IC " + ics)
        table = T.state_file_nodes(step.args, T.STATE_NS_KEYS)       # readns= replaces the nodeset statements
        nodesets = self.step_pairs(self.nl.nodesets if table is None else table, ".nodeset")
        if nodesets:
            if ics:
                self.add([note(o, ".nodeset dropped in the tran deck: Xyce cannot take .ic and .nodeset together")])
            else:
                lines.append(".NODESET " + nodesets)
        own = step.own
        own_stepped = own is not None and own.target[0] != "freq" and step.kind in ("ac", "noise")
        if own_stepped:
            # an ac/noise parameter sweep at a fixed frequency is the innermost level (§7.2 ".STEP lines
            # innermost first"): Xyce's first .STEP is the fastest loop [E20], as results.collect reads it
            lines.append(self.sweep_text(own, ".STEP"))
        for lv in reversed(step.context):              # Xyce's first .STEP is the fastest loop [E20]
            lines.append(self.sweep_text(lv, ".STEP"))
        print_type = "DC"
        if step.kind in ("op", "dc"):
            if own is not None and own.target[0] != "freq":
                lines.append(self.sweep_text(own, ".DC"))
            elif len(step.context) >= 2:
                lines.append(".param %s=0" % OP_DUMMY)
                lines.append(".DC %s LIST 0" % OP_DUMMY)
            else:
                lines.append(".OP")
        elif step.kind in ("ac", "noise"):
            print_type = "AC" if step.kind == "ac" else "NOISE"
            data_name = "vamos_f_%s" % step.id
            if own is not None and own.target[0] == "freq":
                grid = self.freq_text(own, data_name, lines)
            else:
                if "freq" not in step.args:
                    raise T.TableError("%s: an %s parameter sweep needs freq" % (o, step.kind))
                lines.append(".data %s" % data_name)
                lines.append("+ hertz")
                lines.append("+ %s" % fmt(self.num(step.args["freq"], o, "freq")))
                lines.append(".enddata")
                grid = "DATA=%s" % data_name
            if step.kind == "ac":
                lines.append(".AC " + grid)
            else:
                p, n = step.args.get("p"), step.args.get("n", "0")     # §3.11 `p n`: IR node paths, n = "0" for ground
                if not (isinstance(p, str) and p and isinstance(n, str)):
                    raise T.TableError("%s: noise without an output (AnalysisStep.args['p'], ['n']: the node pair)" % o)
                out = [p] if n == "0" else [p, n]
                if len(out) == 2 and self.spectre:
                    # measured (Xyce DEVELOPMENT-202610100047, phase 2, H): Xyce aborts on a .NOISE output
                    # across two nodes, V(a,b) ("OUT OF MEMORY", rc 1; a segfault with a subckt), while
                    # V(a) runs; §2.7: an unsupported construct is status 2, not a failed analysis
                    raise T.TableError("noise %s: the output across two nodes (%s, %s) is not supported on Xyce in "
                                       "v1 (Xyce aborts on .NOISE V(a,b)); VACASK runs it"
                                       % (step.name or o, p, n))
                if not step.noise_input:
                    raise T.TableError("%s: noise without an input source (plan.build names vamos_nin<k>)" % o)
                lines.append(".NOISE V(%s) %s %s" % (",".join(self.node_text(n) for n in out),
                                                      self.element_text(step.noise_input), grid))
        elif step.kind == "xf":
            raise T.TableError("%s: xf runs on VACASK only (§3.11)" % o)
        elif step.kind == "tran":
            print_type = "TRAN"
            a = step.args
            if "step" not in a or "stop" not in a:
                raise T.TableError("%s: tran needs step and stop" % o)
            vals = [fmt(self.num(a["step"], o, "step")), fmt(self.num(a["stop"], o, "stop"))]
            start = self.num(a["outputstart"], o, "outputstart") if "outputstart" in a else 0.0
            if "maxstep" in a:
                vals += [fmt(start), fmt(self.num(a["maxstep"], o, "maxstep"))]
            elif start:
                vals.append(fmt(start))
            lines.append(".TRAN " + " ".join(vals + (["UIC"] if a.get("skipdc") in ("yes", True) else [])))
        else:
            raise T.TableError("%s: analysis kind %r is not one Xyce runs" % (o, step.kind))
        cols = self.name_map(step.id, step.kind)
        if self.sigmap is not None and step.id in self.sigmap.allpub:
            cols = ["V(*)"] + [c for c in cols if not c.startswith("V(")]
        elif step.full_solution and self.spectre and step.kind in ("op", "dc", "tran"):
            cols = ["V(*)"] + [c for c in cols if not c.startswith("V(")]
        if step.full_solution and self.spectre and step.kind in ("op", "dc", "tran"):
            # write=/writefinal= hold the full solution: V(*) plus every V and L current (§5.4); the
            # extra columns feed the state file, and the PSF keeps the SignalMap's selection
            for path in self.source_paths(("v", "l")):
                col = "I(%s)" % self.element_text(path)
                if col not in cols:
                    cols.append(col)
        if step.kind == "noise":
            lines.append(".PRINT NOISE FILE=%s.prn %s" % (step.id, " ".join(cols or ["ONOISE", "INOISE"])))
        else:
            lines.append(".PRINT %s FORMAT=RAW FILE=%s.raw %s" % (print_type, step.id, " ".join(cols or ["V(*)"])))
        lines.append(".end")
        return lines

    def source_paths(self, kinds: Sequence[str]) -> List[str]:
        """The IR paths of every instance of these kinds over the elaborated instance paths, in
        path order (the full-solution currents of §5.4); vamos's own probes are left out."""
        out: List[str] = []
        for path, pe in getattr(self, "by_path", {}).items():
            for it in pe.items:
                if isinstance(it, Instance) and it.kind in kinds and not it.name.startswith("vamos_"):
                    out.append(it.name if not path else path + "." + it.name)
        return out

    def step_pairs(self, d: Dict[str, object], what: str) -> str:
        out = []
        for node, v in d.items():
            try:
                out.append("V(%s)=%s" % (self.node_text(node), fmt(self.num(v, what, "%s v(%s)" % (what, node)))))
            except _Skip:
                pass
        return " ".join(out)


WAVE = T.WAVE_FIELDS


def _is_no(v: Expr) -> bool:
    if isinstance(v, Num):
        return v.value == 0.0
    text = v.text if isinstance(v, Str) else v.name if isinstance(v, Name) else ""
    return text.lower() in ("no", "false", "0")


def _numv(e: object) -> float:
    if isinstance(e, Num):
        return e.value
    if isinstance(e, (int, float)):
        return float(e)
    raise T.TableError("sweep value %s is not a number after plan.build" % (X.to_text(e) if isinstance(e, Expr) else e))


def _renamed(rules: Sequence[T.ParamRule], param: str, engine: str) -> str:
    for r in rules:
        if r.name == param and r.action == "rename":
            name = r.value
            if engine == "xyce" and name.startswith("instance_"):
                name = name[len("instance_"):]
            return name
    return param


def _hooked_source(src: Source, hooked: Dict[str, str]) -> Source:
    """src with every hooked Spectre field replaced by its .param name (a copy; Source.spectre kept)."""
    import copy
    out = copy.deepcopy(src)
    for param, hook in hooked.items():
        where = _SOURCE_FIELD.get(param)
        if where is None:
            continue
        if where[0] == "dc":
            out.dc = Name(hook)
        elif where[0] == "args":
            out.args[where[1]] = Name(hook)                  # type: ignore[index]
        else:
            ac = list(out.ac) if out.ac is not None else [Num(0.0), Num(0.0)]
            ac[where[1]] = Name(hook)                        # type: ignore[index]
            out.ac = (ac[0], ac[1])
    return out


def _plist(s: Subckt) -> List[Param]:
    """A subckt's parameters, header and body (spice.parse merges them; a hand-built IR may not)."""
    names = {p.name for p in s.params}
    return list(s.params) + [p for p in s.body if isinstance(p, Param) and p.name not in names]


def printed(inst: Instance, va: Optional[Mapping[str, VaModule]] = None) -> str:
    """An element's name in the Xyce deck: it must start with the element's letter (a Verilog-A
    module on the letter-device route, §4.5 item 13, takes its group's letter; va: Netlist.va_modules
    by lower-cased name)."""
    if inst.kind == "y":
        if inst.master == T.GCOND_MODULE:
            return inst.name if inst.name[:1].lower() == "b" else "b" + inst.name
        module = va.get((inst.master or "").lower()) if va else None
        letter = T.va_letter(module) if module is not None else None
        if letter is None:
            return inst.name
        return inst.name if inst.name[:1].lower() == letter[0] else letter[0] + inst.name
    letter = _LETTER.get(inst.kind, "")
    return inst.name if inst.name[:1].lower() == letter else letter + inst.name


def printed_path(nl: Netlist, name: str, scope: T.Scope, va: Optional[Mapping[str, VaModule]] = None) -> str:
    """A (hierarchical) element reference with every component as Xyce prints it (instances inside
    Cond branches included)."""
    parts = name.split(".")
    items: Sequence[object] = scope.subckt.body if scope.subckt is not None else nl.body
    sc = scope
    out = []
    for i, part in enumerate(parts):
        inst = next((it for it in flat_items(items) if isinstance(it, Instance) and it.name == part), None)
        if inst is None:
            out += parts[i:]
            break
        out.append(printed(inst, va))
        if i < len(parts) - 1:
            sub = sc.find_subckt(inst.master or "") if inst.kind == "x" else None
            if sub is None:
                out += parts[i + 1:]
                break
            sc = T.Scope(sub.body, sc, sub)
            items = sub.body
    return ":".join(out)


def _multiplied(nl: Netlist) -> Set[str]:
    """Names of the subckts an X line with m= reaches, directly or through further X lines."""
    calls: Dict[str, Set[str]] = {}
    roots: Set[str] = set()
    for scope, items in [("", nl.body)] + [(s.name, s.body) for s in T.all_subckts(nl)]:
        for it in flat_items(items):                        # X lines inside Cond branches too (E56)
            if isinstance(it, Instance) and it.kind == "x" and it.master:
                calls.setdefault(scope, set()).add(it.master)
                if "m" in it.params:
                    roots.add(it.master)
    out: Set[str] = set()
    todo = list(roots)
    while todo:
        n = todo.pop()
        if n not in out:
            out.add(n)
            todo.extend(calls.get(n, ()))
    return out


def _resolve(nl: Netlist, target: str) -> Optional[Instance]:
    """The element a hierarchical name (x1.x2.v1) names, through the subckt definitions."""
    parts = target.split(".")
    items: Sequence[object] = nl.body
    scope = T.Scope(nl.body)
    for i, part in enumerate(parts):
        found = next((it for it in flat_items(items) if isinstance(it, Instance) and it.name == part), None)
        if found is None:
            return None
        if i == len(parts) - 1:
            return found
        if found.kind != "x":
            return None
        sub = scope.find_subckt(found.master or "")
        if sub is None:
            return None
        scope = T.Scope(sub.body, scope, sub)
        items = sub.body
    return None


# -- the smoke check (§4.7) -------------------------------------------------------------

_HEAD = re.compile(r"^(?:Netlist|Simulation|User|Device|Analysis)?\s*(?:warning|error|Error|Warning|fatal)")
_NOPARAM = re.compile(r"No model parameter (\S+) found for model (\S+) of type")
_UNREC = re.compile(r"Unrecognized (?:parameter|fields)")


def smoke(nl: Netlist, dir: str, osdi: Sequence[str] = (), nvc_libdir: Optional[str] = None,
          timeout: int = 600) -> List[Note]:
    """Run the smoke deck standalone (see the module docstring); [] when it passes."""
    from vamos.ams import engines
    origin = os.path.join(dir, SMOKE_DECK)
    snl = T.smoke_netlist(nl)
    deck = _Deck(snl, osdi, True)
    try:
        text = deck.render(None)
    except NoteError as exc:
        return [n for n in exc.notes if n.severity == ERROR]
    os.makedirs(dir, exist_ok=True)
    with open(origin, "w", encoding="utf-8") as fh:
        fh.write(text)
    exe = engines.xyce_bin()
    if not exe:
        return [error(origin, "Xyce not found (set VAMOS_XYCE)")]
    env = engines.env_for(ENGINE, nvc_libdir if nvc_libdir is not None else T.nvc_libdir(), dict(os.environ))
    binned = {m.name.lower() for m in _models(snl) if m.base is not None}
    try:
        p = subprocess.run([exe, "-norun", SMOKE_DECK], cwd=dir, env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, errors="replace",
                           timeout=timeout)
        with open(os.path.join(dir, SMOKE_NORUN_LOG), "w", encoding="utf-8",
                  errors="replace") as fh:
            fh.write(p.stdout)
        msgs = [_nobin(m, deck) for m in _messages(p.stdout)]
        bad = _problem_messages(msgs, binned)
        bad += [n.message for n in device_summary_problems(p.stdout, snl)]
        if p.returncode != 0 or bad:
            return [error(origin, "Xyce model-parameter check failed (exit %d): %s"
                          % (p.returncode, "\n".join(bad or _errors(msgs) or [_tail(p.stdout)])))]
        p = subprocess.run([exe, SMOKE_DECK], cwd=dir, env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, errors="replace",
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return [error(origin, "Xyce smoke check timed out after %d s" % timeout)]
    except OSError as exc:
        return [error(origin, "cannot run Xyce: %s" % exc)]
    with open(os.path.join(dir, SMOKE_LOG), "w", encoding="utf-8", errors="replace") as fh:
        fh.write(p.stdout)
    if p.returncode != 0:
        msgs = _errors([_nobin(m, deck) for m in _messages(p.stdout)])
        return [error(origin, "Xyce smoke check failed (exit %d): %s"
                      % (p.returncode, "\n".join(msgs) or _tail(p.stdout)))]
    return []


def _problem_messages(msgs: Sequence[str], binned: Set[str]) -> List[str]:
    """The smoke patterns: an unknown model parameter (the bounds of binned cards exempt), an
    unrecognized parameter, a netlist error, a mapped no-bin message."""
    return [m for m in msgs if _UNREC.search(m) or _bad_param(m, binned) or m.startswith("no bin of")
            or "Netlist error" in m]


def output_problems(text: str, nl: Netlist) -> List[Note]:
    """The smoke patterns of smoke() over one Xyce output (VAMOS_SPECTRE_DESIGN.md §4.5 item 13,
    §7.2): the errors in Xyce's output text for nl: unknown model parameters (the bounds of binned
    cards exempt), unrecognized parameters, netlist errors.  One error note per message, with
    Xyce's text."""
    binned = {m.name.lower() for m in _models(nl) if m.base is not None}
    return [error("Xyce", m) for m in _problem_messages(_messages(text), binned)]


_SUMMARY_BUILTIN = re.compile(r"^\s+([A-Za-z]) level (\d+) \(([^)]*)\)\s+(\d+)\s*$")
_SUMMARY_MODULE = re.compile(r"^\s+([A-Za-z_][A-Za-z0-9_]*) \(([^)]*)\)\s+(\d+)\s*$")
# Xyce wraps a long path onto the next line (`... registered bsim6 from` + newline + path; phase 2, C)
_REGISTERED = re.compile(r"\.HDL: compiled and registered (\S+) from(?:\s|$)")
_COUNTED = re.compile(r"^Number of (\S+) (models|instances): (\d+)")


def device_summary_problems(text: str, nl: Netlist) -> List[Note]:
    """The Verilog-A binding check of VAMOS_SPECTRE_DESIGN.md §4.5 item 13: an error for every module
    of Netlist.va_modules that the deck instantiates on the letter-device route (tables.va_letter)
    and that Xyce did not bind under its own name.

    Evidence, measured on the launcher vamos runs (/usr/local/src/xyce-build/src/Xyce,
    DEVELOPMENT-202610100047, 7cd78110, under engines.env_for; S3, 2026-10-10, §14 q.50):
    - `Xyce -norun` and a full run both print a Device Count Summary with one line per device
      type: a bound module is listed under its own name, `MYDIO (mydio) 1` [E63] or `RESISTOR
      (resistor) 1` [E64], while a built-in device bound at the module's level is listed as
      `D level 2002 (DIODE_CMC 2.0.0)` (the 2026-09-29 library) or `M level 77 (BSIM6)` [E99];
    - the registration line `Netlist warning: .HDL: compiled and registered <module> from <file>`
      precedes it, and a full run under .op also prints `Number of <module> models: N` and
      `Number of <module> instances: N`.
    The check: a built-in summary line whose letter and level are a used module's is an error (the
    module's level collides with a compiled-in device); when the summary lists device types, a used
    module missing from it under its own name is an error; when the output carries no per-type
    summary line (a launcher that prints only the total), the `Number of <module> ...` lines, else
    the registration line, stand for the listing, and a module with neither is an error.
    """
    out: List[Note] = []
    used: Dict[str, VaModule] = {}
    va = {k.lower(): v for k, v in nl.va_modules.items()}
    for items in [nl.body] + [s.body for s in T.all_subckts(nl)]:
        for it in flat_items(items):
            if isinstance(it, Instance) and it.kind == "y" and it.master:
                m = va.get(it.master.lower())
                if m is not None and T.va_letter(m) is not None:
                    used[it.master.lower()] = m
    if not used:
        return out
    builtins: List[Tuple[str, int, str]] = []
    listed: Set[str] = set()
    for line in text.splitlines():
        m = _SUMMARY_BUILTIN.match(line)
        if m:
            builtins.append((m.group(1).upper(), int(m.group(2)), m.group(3)))
            continue
        m = _SUMMARY_MODULE.match(line)
        if m:
            listed.add(m.group(2).lower())
    registered = {m.lower() for m in _REGISTERED.findall(text)}
    counted = {m.group(1).lower() for m in (_COUNTED.match(l) for l in text.splitlines()) if m}
    per_type = bool(builtins or listed)
    for key, module in sorted(used.items()):
        letter, _kinds, level = T.va_letter(module)       # type: ignore[misc]
        hit = [b for b in builtins if b[0] == letter.upper() and b[1] == level]
        if hit:
            out.append(error(module.path, "Verilog-A module %s: Xyce bound its built-in %s at level %d (the Device "
                                          "Count Summary lists '%s level %d (%s)', not '%s (%s)'); the module's "
                                          "level collides with a compiled-in device, so it cannot run on Xyce"
                             % (module.name, hit[0][2], level, letter.upper(), level, hit[0][2],
                                module.name.upper(), module.name)))
            continue
        if per_type:
            if key not in listed:
                out.append(error(module.path, "Verilog-A module %s is not listed under its own name in Xyce's "
                                              "Device Count Summary ('%s (%s)'): it was not bound on its card"
                                 % (module.name, module.name.upper(), module.name)))
            continue
        if key in counted or key in registered:
            continue
        out.append(error(module.path, "Verilog-A module %s: Xyce's output neither lists it in a Device Count "
                                      "Summary, counts its models or instances, nor reports it compiled and "
                                      "registered; the module was not bound" % module.name))
    return out


_AT_LINE = re.compile(r"at or near line (\d+)\b.*no valid model card found")
_NO_MODEL = re.compile(r"Unable to find model (\S+?)\. for device (\S+)")


def _nobin(msg: str, deck: "_Deck") -> str:
    """Xyce's two ways of saying that no bin fits a natively binned M line become vamos's message."""
    m = _AT_LINE.search(msg)
    if m and int(m.group(1)) in deck.nobin_lines:
        return deck.nobin_lines[int(m.group(1))]
    m = _NO_MODEL.search(msg)
    if m:
        key = (m.group(1).upper(), m.group(2).split(":")[-1].upper())
        if key in deck.nobin_dev:
            return deck.nobin_dev[key]
    return msg


def _errors(msgs: Sequence[str]) -> List[str]:
    """The diagnostics that are errors (Xyce's warnings stay in the log)."""
    return [m for m in msgs if "rror" in m or "ailed" in m or "bort" in m or m.startswith("no bin of")]


def _models(nl: Netlist) -> List[Model]:
    out = [m for m in nl.body if isinstance(m, Model)]
    for s in T.all_subckts(nl):
        out += [m for m in s.body if isinstance(m, Model)]
    return out


def _bad_param(msg: str, binned: Set[str]) -> bool:
    m = _NOPARAM.search(msg)
    if not m:
        return False
    return not (m.group(1).lower() in T.BIN_KEYS and m.group(2).lower() in binned)


def _messages(out: str) -> List[str]:
    """Xyce's diagnostics, one string each: a header line plus its indented continuation lines."""
    msgs: List[str] = []
    cur: Optional[List[str]] = None
    for line in out.splitlines():
        if _HEAD.match(line) or line.startswith("Netlist ") or line.startswith("Simulation aborted"):
            if cur:
                msgs.append(" ".join(cur))
            cur = [line.strip()]
        elif cur is not None and line.startswith(" ") and line.strip():
            cur.append(line.strip())
        else:
            if cur:
                msgs.append(" ".join(cur))
            cur = None
    if cur:
        msgs.append(" ".join(cur))
    return msgs


def _tail(out: str) -> str:
    lines = [l for l in out.splitlines() if l.strip() and not l.startswith("*****")]
    return "\n".join(lines[-15:])
