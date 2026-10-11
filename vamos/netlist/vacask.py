"""VACASK deck emitter (docs/VAMOS_AMS_DESIGN.md §4.4, with §4.3.5-§4.3.8 and §4.7; the Spectre
extensions of docs/VAMOS_SPECTRE_DESIGN.md §4.5 and §7.1).

The IR has every dialect question resolved; this module only prints it in
VACASK's Spectre-like netlist language.  Every engine-dependent rule (model
dispatch, polarity, levels, versions, the multiplier, .option scale, binning,
source fields) comes from tables.py, which the Xyce emitter reads too.

API
---
emit(nl, path, analysis_name="vamos_tran", osdi=(), notes=None) -> None
    Write the deck for nl to path (the caller picks the file: ams/deck/vamos.sim
    or smoke.sim), as UTF-8 (the netlist text spice.py read as UTF-8, whatever
    the locale).  osdi lists the .osdi files to load (vamos_ie.osdi and the
    compiled user .hdl files: deck.py compiles them), each absolute or relative
    to the deck's directory, where VACASK resolves a relative load (deck.py
    passes them relative, so a copied daidir loads its own); with no osdi, the
    user .hdl files (nl.hdl) are loaded as .va and VACASK compiles them itself.
    Problems are collected over the whole netlist and raised together as one
    NoteError whose notes name each construct and its origin (warnings
    included).  Without errors, the warnings and notes (stripped HSPICE-only
    keys, a removed BSIM3 version) are appended to notes when a list is given.
render(nl, analysis_name="vamos_tran", osdi=(), notes=None, op=False, plan=None, sigmap=None, names=None) -> str
    The deck text emit() writes; op=True prints an operating point analysis in
    place of the transient (the smoke deck).  With a RunPlan (the spectre
    personality, VAMOS_SPECTRE_DESIGN.md §4.5 items 4-6, §7.1) the control block
    is the plan's (below) and `names` receives every printed Ref's column.
names_for(nl, plan, sigmap) -> Dict[str, Dict[Ref, str]]
    The name map render() fills (step id -> Ref -> rawfile column), without rendering.
smoke(nl, dir, osdi=(), nvc_libdir=None, timeout=600) -> List[Note]
    The compile-time check (§4.7): writes dir/smoke.sim from smoke_netlist(nl)
    with an operating point analysis named vamos_smoke, runs the standalone
    VACASK (engines.vacask_bin(), environment from engines.env_for) in dir,
    keeps its output in dir/smoke.log and returns [] when it exits 0 (abort
    always: a failed analysis exits 1), else one error note with the engine's
    message.  VACASK's "Master 'm_<base>' not found" at a binning @else line
    becomes "no bin of <base> for l=..., w=... (scale ...; instance ...)".
smoke_netlist(nl) -> Netlist
    tables.smoke_netlist: every value or probe bridge source (Source.code_uri)
    holds DC 0, every enable source (bridge name ending in __e) DC 1.0.
quote(name) -> str
    The one quoting predicate for every identifier class (nodes, instances,
    models, subckts): expr.vacask_quote (bare [A-Za-z_$][A-Za-z0-9_$]* or all
    digits, never a reserved word; anything else in single quotes).  A Spectre
    netlist also quotes an all-digit name with a leading zero ('007', '00'):
    bare, VACASK rejects it, and quoted it is a node distinct from 7 and from
    ground [E35] (VAMOS_SPECTRE_DESIGN.md §4.5 item 9).

Deck layout: the title; ground 0; global; load lines (the osdi list, then the
module files the dispatch uses, resolved through VACASK's module path);
builtin model lines (vamos_vsource, vamos_isource, vamos_vcvs, vamos_vccs,
vamos_ccvs, vamos_cccs, vamos_mutual, vamos_resistor, vamos_capacitor,
vamos_inductor, vamos_sp_resistor/_capacitor/_inductor, vamos_gcond), each
declared once and only when used; top-level parameters; the body in IR order;
the control block: abort always, options tran_lteimplicit=0 temp= tnom=
[gmin=] (temp and tnom default to 25, 27 under .option spice), save, one
analysis with evaluated floats (step stop [start] [maxstep] [icmode="uic"]
[ic={...}] [nodeset={...}]).  Only the subckts and model cards reachable from
the top-level instances are printed (tables.reachable): a library defines far
more than a deck uses, and an unused construct no target honours must not
fail it (HSPICE never instantiates it either).

Element mapping (IR kind -> VACASK):
    r c l   value only (and m): resistor/capacitor/inductor.osdi (r=, c=, l=);
            with a model card or other parameters: sp_resistor/sp_capacitor/
            sp_inductor; IC= on C/L is an error (use .ic)
    k       mutual k= ind1="l1" ind2="l2" (no $mfactor); an inductor a K names gets
            its multiplier and TC1/TC2 folded into l= and no $mfactor, tc1, tc2,
            dtemp (tables.coupled_inductance: the mutual divides each inductance
            by its $mfactor and reads the nominal l)
    v i     vsource/isource; a code source: type="pwl" file="<code_uri>"
    e g     vcvs/vccs gain=        f h   cccs/ccvs gain= ctlinst="<vsrc>"
    b       behavioral v=/i= (no $mfactor; a multiplier is folded into i=)
    d q m j model card m_<name> (module per tables.target, BSIM4 sp_bsim4v8 with
            version="<v>" a string; a name colliding with a subckt gets a
            suffix: models and subckts share one namespace); a 3-terminal Q
            gets its substrate on 0, as in SPICE; .option scale reaches a
            diode only at LEVEL 3 (tables.scaled)
    x       the subckt, overrides through expr.param_ident
    y       Verilog-A: vamos_gcond instances share one model card (rr is an
            instance parameter); any other module gets a model card per
            instance, in the instance's scope (VA parameters are model
            parameters unless the module marks them instance parameters)
Probes: v(n) (hierarchical '.' becomes ':'), v(a,b) saves both nodes, '*' is
save default; i(x) of V, L, E, H and behavioral v= elements is i(x), of a
resistor p(x, i); any other i() is an error.

Subckt parameters: VACASK lets an instance override only a primary parameter
(a default that is constant when the deck is parsed).  Top-level values are
folded into defaults, so wp=2.5*wn stays overridable; a default that depends
on another subckt parameter and that some X line overrides is declared with
the default NOT_GIVEN and read through p__v=(p==NOT_GIVEN) ? (default) : p.
The multiplier (§4.3.7): every subckt declares $mfactor=1.0; children take
$mfactor=$mfactor*m per tables.MULT; at top level m= becomes $mfactor=m.
Binning (§4.3.6): each bin is model 'm_<base>.<k>' without its bounds; a
binned M instance becomes an @if/@elseif chain of copies, one per bin in
Xyce's order (tables.Scope), guards from tables.bin_guard with .option scale
applied once; @else binds the absent master m_<base>.  A constant geometry no
bin fits is an error at emit time.

Spectre netlists (Netlist.dialect == "spectre"; VAMOS_SPECTRE_DESIGN.md §4.4, §4.5, §7.1):
    Cond    @if c / @elseif c / @else / @end around the branch items, nested blocks included
            (VACASK re-evaluates them at every sweep point) [E9]; every walk sees a Cond
    cards   tables.model_params(dialect="spectre"): SPECTRE_MASTERS, no HSPICE defaults and no
            HSPICE M-instance warnings; instances take MasterRow.instance (renames, the written
            defaults, strips, errors) through tables.spectre_instance_params
    bins    bin_rule="spectre": the @if chain in group order with exact guards on the total w
    sources a waveform source whose dc is given prints type="dc" dc=<dc> with its waveform fields
            (VACASK uses dc only under type="dc" [E17]); the plan switches the type around each tran
    r = 0   a 0 V vamos_vsource, a short whose flow is the resistor current [E22]
    isnoisy=no   noisy=0 (resistor and sp_resistor)
    nested subckts   a definition nested in another that reads the enclosing parameters P is
            lifted to top level as <outer>__<name> with P as its own parameters; its X lines pass
            p=p for every p in P they do not override (VACASK resolves names in a nested definition
            in that definition and at top level only [E65]); deeper nesting is lifted recursively
    Verilog-A   parameter names lower-cased (openvaf-r's OSDI names) [E64]
    plan    the control block: abort except analysis; options rawfile="binary" strictoutput=0
            strictsave=2 tran_lteimplicit=0 <the global options>; var vamos_v_<p> for every plan
            variable (hooked as parameters p=vamos_v_p at top level); per-step saves from the
            SignalMap with the directives each analysis type reads (v/i/p for op, dc and tran;
            dv/di for ac; nc for noise; save default for allpub), re-issued after clear saves when a
            step's set differs; each tran's settings as options lines before its sweep statements
            and restored after the analysis; sweep statements (context outermost first, then the
            step's own) around the analysis line vamos_a<k>; alter instance/model, options, var;
            the source-state alters with every field explicit [E37]
"""

from __future__ import annotations

import math
import os
import re
import subprocess
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Set, Tuple

from vamos.netlist import expr as X
from vamos.netlist import tables as T
from vamos.netlist.expr_ast import Binary, Expr, Name, Num, Str, Ternary
from vamos.netlist.ir import Cond, Instance, Model, Netlist, Param, ParamTest, Source, Subckt, flat_items
from vamos.netlist.numbers import fmt
from vamos.notes import ERROR, Note, NoteError, error, note, warning

if TYPE_CHECKING:                                  # the spectre run plan (VAMOS_SPECTRE_DESIGN.md §5, §6)
    from vamos.netlist.plan import Action, AnalysisStep, RunPlan, SweepLevel
    from vamos.netlist.signals import Ref, SignalMap

ENGINE = "vacask"
SMOKE_DECK = "smoke.sim"
SMOKE_LOG = "smoke.log"
SMOKE_ANALYSIS = "vamos_smoke"
NOT_GIVEN = -1.2345678901234568e-300    # default of an overridable dependent subckt parameter
VAR_PREFIX = "vamos_v_"                 # the var hook of a plan variable (VAMOS_SPECTRE_DESIGN.md §4.5 item 4)

quote = X.vacask_quote
_MIN_NORMAL = 2.2250738585072014e-308

# Builtin and generic module models, declared once each, only when used:
# deck name -> (module, file to load or "" for VACASK builtins).
BUILTIN_MODELS: Dict[str, Tuple[str, str]] = {
    "vamos_vsource": ("vsource", ""), "vamos_isource": ("isource", ""),
    "vamos_vcvs": ("vcvs", ""), "vamos_vccs": ("vccs", ""), "vamos_ccvs": ("ccvs", ""),
    "vamos_cccs": ("cccs", ""), "vamos_mutual": ("mutual", ""),
    "vamos_resistor": ("resistor", "resistor.osdi"),
    "vamos_capacitor": ("capacitor", "capacitor.osdi"),
    "vamos_inductor": ("inductor", "inductor.osdi"),
    "vamos_sp_resistor": ("sp_resistor", "spice/resistor.osdi"),
    "vamos_sp_capacitor": ("sp_capacitor", "spice/capacitor.osdi"),
    "vamos_sp_inductor": ("sp_inductor", "spice/inductor.osdi"),
    T.GCOND_MODULE: (T.GCOND_MODULE, ""),         # loaded from the osdi list
}
_SIMPLE = {"r": ("vamos_resistor", "r"), "c": ("vamos_capacitor", "c"), "l": ("vamos_inductor", "l")}
_SPICE_RCL = {"r": "vamos_sp_resistor", "c": "vamos_sp_capacitor", "l": "vamos_sp_inductor"}

# Spectre source fields a sweep may target directly on VACASK, with VACASK's spelling of the vsource/
# isource parameter the deck prints (§4.5 item 8, §5.2); a field the IR transforms (exp's td1/td2,
# pwl's wave/offset/scale/stretch, file) is not sweepable in v1.
SOURCE_FIELDS: Dict[str, str] = {
    "dc": "dc", "val0": "val0", "val1": "val1", "delay": "delay", "rise": "rise", "fall": "fall",
    "width": "width", "period": "period", "sinedc": "sinedc", "ampl": "ampl", "freq": "freq",
    "damp": "theta", "sinephase": "tdphase", "mag": "mag", "phase": "phase", "tau1": "tau1", "tau2": "tau2",
}
# The global options of a plan's options action, in the order the control block prints them
# (Spectre's keys; tables.SPECTRE_OPTIONS["vacask"] spells VACASK's).
_OPTION_ORDER = ("temp", "tnom", "reltol", "vabstol", "iabstol", "chargeabstol", "gmin")
_HEAD_OPTIONS = ('rawfile="binary"', "strictoutput=0", "strictsave=2", "tran_lteimplicit=0")


def emit(nl: Netlist, path: str, analysis_name: str = "vamos_tran", osdi: Sequence[str] = (),
         notes: Optional[List[Note]] = None) -> None:
    """Write the VACASK deck for nl to path (see the module docstring)."""
    text = render(nl, analysis_name, osdi, notes)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def render(nl: Netlist, analysis_name: str = "vamos_tran", osdi: Sequence[str] = (),
           notes: Optional[List[Note]] = None, op: bool = False,
           plan: Optional[RunPlan] = None, sigmap: Optional[SignalMap] = None,
           names: Optional[Dict[str, Dict[Ref, str]]] = None) -> str:
    """The VACASK deck text for nl (see the module docstring).

    plan, sigmap, names (VAMOS_SPECTRE_DESIGN.md §4.5 items 5-6, §7.1): with a RunPlan the deck
    prints the plan's control block in place of the single analysis, each step's saves from
    sigmap, and fills names (step id -> Ref -> engine column) for every Ref it printed.  With all
    three None the output is byte-identical to the vcs-ams deck.
    """
    return _Deck(nl, analysis_name, osdi, op, plan, sigmap, names).render(notes)


def names_for(nl: Netlist, plan: RunPlan, sigmap: SignalMap) -> Dict[str, Dict[Ref, str]]:
    """The name map render() would fill (step id -> Ref -> VACASK column), without rendering
    (VAMOS_SPECTRE_DESIGN.md §4.5 item 6, §6.4)."""
    deck = _Deck(nl, "vamos_tran", (), False, plan, sigmap, None)
    return deck.name_map()


# -- the emitter ----------------------------------------------------------------------

class _Deck:
    def __init__(self, nl: Netlist, analysis: str, osdi: Sequence[str], op: bool,
                 plan: Optional[RunPlan] = None, sigmap: Optional[SignalMap] = None,
                 names: Optional[Dict[str, Dict[Ref, str]]] = None):
        if not re.match(r"[A-Za-z_][A-Za-z0-9_]*$", analysis):
            raise ValueError("bad analysis name %r" % (analysis,))
        self.nl, self.analysis, self.osdi, self.op = nl, analysis, list(osdi), op
        self.plan, self.sigmap = plan, sigmap
        self.names: Dict[str, Dict[Ref, str]] = names if names is not None else {}
        self.spectre = nl.dialect == "spectre"
        self.s = nl.scale()
        self.values = nl.values
        self.notes: List[Note] = []
        self.body: List[str] = []
        self.loads: List[str] = []
        self.builtins: List[str] = []
        self.nobin: Dict[int, str] = {}           # body index -> message
        self.nobin_lines: Dict[int, str] = {}     # deck line (1-based) -> message
        self.missing: Dict[str, str] = {}         # binned base -> the master that must not exist
        self.rename: Dict[str, Expr] = {}         # subckt parameter -> its effective-value parameter
        self.shadow: Tuple[str, ...] = ()         # parameters the enclosing subckts declare
        self.used = T.reachable(nl)               # ids of the subckts and cards the deck uses
        self.overridden: Dict[str, set] = {}      # subckt name -> parameters some X line overrides
        self.iparams: Dict[int, Dict[str, Expr]] = {}   # instance id -> parameters after the Spectre rules
        self.lifted: Dict[int, Tuple[str, List[str]]] = {}   # id(nested subckt) -> (lifted name, enclosing params)
        self.lift_queue: List[Tuple[Subckt, T.Scope]] = []
        self.variables: Dict[str, float] = dict(plan.variables) if plan is not None else {}
        for items in [nl.body] + [s.body for s in T.all_subckts(nl)]:
            for it in flat_items(items):          # X lines inside Cond branches too (§4.5 item 1)
                if isinstance(it, Instance) and it.kind == "x" and it.master:
                    self.overridden.setdefault(it.master, set()).update(k for k in it.params if k != "m")
        if plan is not None:                      # sub= sweep and alter targets are overrides too (item 4)
            for sub, params in plan.overridden.items():
                self.overridden.setdefault(sub, set()).update(params)
        self._names()
        if self.spectre:
            self._lift_plan()

    # -- diagnostics
    def err(self, origin: str, msg: str) -> None:
        self.notes.append(error(origin, msg))

    def add(self, notes: Sequence[Note]) -> None:
        """Card notes, once per message (bins of one base share theirs, tables.card_label)."""
        seen = {(n.severity, n.message) for n in self.notes}
        for n in notes:
            if (n.severity, n.message) not in seen:
                seen.add((n.severity, n.message))
                self.notes.append(n)

    # -- names
    def _names(self) -> None:
        """Model deck names m_<name>, never equal to a subckt or builtin name (one namespace)."""
        subs = T.all_subckts(self.nl)
        taken = set(BUILTIN_MODELS) | {s.name for s in subs}
        for s in subs:
            if s.name in BUILTIN_MODELS and id(s) in self.used:
                self.err(s.origin, "subckt name %s is reserved for a vamos builtin model" % s.name)
        models: List[Model] = []
        for items in [self.nl.body] + [s.body for s in subs]:
            models += [m for m in items if isinstance(m, Model)]
        self.mname: Dict[str, str] = {}
        for name in sorted({m.name for m in models}):
            self.mname[name] = _fresh("m_" + name, taken)
        for base in sorted({m.base for m in models if m.base is not None}):
            self.missing[base] = _fresh("m_" + base, taken)
        self.taken = taken                         # per-instance Verilog-A cards are allocated later

    def q(self, name: str) -> str:
        """quote(), plus the Spectre rule for an all-digit name with a leading zero (§4.5 item 9)."""
        if self.spectre and len(name) > 1 and name.isdigit() and name[0] == "0":
            return "'%s'" % name
        return quote(name)

    def use_builtin(self, name: str) -> str:
        if name not in self.builtins:
            self.builtins.append(name)
            f = BUILTIN_MODELS[name][1]
            if f:
                self.use_load(f)
        return name

    def use_load(self, f: str) -> None:
        if f not in self.loads:
            self.loads.append(f)

    # -- values
    def pv(self, e: Expr, origin: str, what: str) -> str:
        """A parameter-context value; raises _Skip after recording the error."""
        if self.rename:
            e = X.substitute(e, self.rename)
        if isinstance(e, Num) and math.isfinite(e.value) and (e.value == 0 or abs(e.value) >= _MIN_NORMAL):
            return fmt(e.value)
        try:
            t = X.to_vacask(e, "param")
        except (X.PrintError, X.ExprError) as exc:
            self.err(origin, "%s: %s" % (what, exc))
            raise _Skip()
        return "(%s)" % t if " " in t else t

    def bv(self, e: Expr, origin: str, what: str) -> str:
        if self.rename:
            e = X.substitute(e, self.rename)
        try:
            t = X.to_vacask(e, "behavioral")
        except (X.PrintError, X.ExprError) as exc:
            self.err(origin, "%s: %s" % (what, exc))
            raise _Skip()
        return "(%s)" % t if " " in t else t

    def num(self, v: object, origin: str, what: str) -> float:
        """A control-block value as a float (the IR keeps them evaluated)."""
        return _number(self, v, origin, what)

    # -- the walk
    def render(self, notes: Optional[List[Note]]) -> str:
        top = T.Scope(self.nl.body)
        try:
            self.params([it for it in self.nl.body if isinstance(it, Param)], "")
        except _Skip:
            pass
        self.items(self.nl.body, top, "")
        while self.lift_queue:                     # the lifted nested definitions, at top level (§4.5 item 11)
            s, where = self.lift_queue.pop(0)
            try:
                self.lifted_subckt(s, where)
            except _Skip:
                pass
            except (T.TableError, X.PrintError) as exc:
                self.err(s.origin, str(exc))
        if self.plan is not None:
            self.name_map()                        # every printed Ref's column (§4.5 item 6)
        control = self.plan_control() if self.plan is not None else self.control()
        head = self.header()
        for idx, msg in self.nobin.items():
            self.nobin_lines[len(head) + idx + 1] = msg
        if any(n.severity == ERROR for n in self.notes):
            raise NoteError(self.notes)
        if notes is not None:
            notes.extend(self.notes)
        return "\n".join(head + self.body + control) + "\n"

    def header(self) -> List[str]:
        title = (self.nl.title or "").strip().splitlines()
        lines = [title[0] if title and title[0].strip() else "vamos deck", "// generated by vamos",
                 "ground 0"]
        glob = [self.q(g) for g in self.nl.globals if g != "0"]
        if glob:
            lines.append("global " + " ".join(glob))
        loads = list(self.osdi) if self.osdi else list(self.nl.hdl)
        for p in loads:
            lines.append('load "%s"' % p)
        for f in self.loads:
            lines.append('load "%s"' % f)
        for b in self.builtins:
            lines.append("model %s %s" % (b, BUILTIN_MODELS[b][0]))
        return lines

    def params(self, params: Sequence[Param], indent: str) -> None:
        for p in params:
            name = X.param_ident(p.name, ENGINE)
            if not re.match(r"[A-Za-z_$][A-Za-z0-9_$]*$", name):
                self.err(p.origin, "parameter name %r is not a VACASK identifier" % p.name)
                continue
            if not indent and p.name in self.variables:
                # a plan variable: hooked to the var the control block sets (§4.5 item 4) [E2]
                self.body.append("%sparameters %s=%s" % (indent, name, VAR_PREFIX + p.name))
                continue
            try:
                self.body.append("%sparameters %s=%s" % (indent, name,
                                                         self.pv(p.expr, p.origin, "parameter " + p.name)))
            except _Skip:
                pass

    def items(self, items: Sequence[object], scope: T.Scope, indent: str) -> None:
        for it in items:
            if isinstance(it, (Model, Subckt)) and id(it) not in self.used:
                continue                            # never instantiated: HSPICE ignores it too
            try:
                if isinstance(it, Model):
                    self.model(it, indent)
                elif isinstance(it, Subckt):
                    self.subckt(it, scope, indent)
                elif isinstance(it, Instance):
                    self.instance(it, scope, indent)
                elif isinstance(it, Cond):
                    self.cond(it, scope, indent)
                elif isinstance(it, (Param, ParamTest)):
                    continue                        # parameters are printed by params(); paramtests print nothing
                else:
                    raise T.TableError("%s: item %r is not an IR item (ir.ITEM_TYPES)"
                                       % (scope.path() or "top level", it))
            except _Skip:
                pass
            except (T.TableError, X.PrintError) as exc:
                self.err(getattr(it, "origin", ""), str(exc))

    def cond(self, c: Cond, scope: T.Scope, indent: str) -> None:
        """A structural if as VACASK's @if / @elseif / @else / @end block (§4.5 item 2) [E9]."""
        inner = indent + "  " if c.branches else indent
        for i, (test, body) in enumerate(c.branches):
            text = self.pv(test, c.origin, "condition")
            self.body.append("%s%s %s" % (indent, "@if" if i == 0 else "@elseif", text))
            self.items(body, scope, inner)
        if c.default:
            if c.branches:
                self.body.append("%s@else" % indent)
            self.items(c.default, scope, inner)
        if c.branches:
            self.body.append("%s@end" % indent)

    def subckt(self, s: Subckt, parent: T.Scope, indent: str) -> None:
        if id(s) in self.lifted:                  # printed at top level after the body (§4.5 item 11)
            self.lift_queue.append((s, parent))
            return
        scope = T.Scope(s.body, parent, s)
        up = T.enclosing_reads(s, self.shadow) if parent.in_subckt else []
        if up:
            # VACASK resolves a name in a nested subckt in that subckt and at top level only
            # ("Variable or constant 'p' not defined"; a top-level p of that name would be read
            # silently); HSPICE and Xyce read the enclosing subckt's parameter
            self.err(s.origin, "subckt %s, defined inside subckt %s, reads %s of the enclosing subckt: "
                     "VACASK does not pass an enclosing subckt's parameters into a nested definition; "
                     "define %s at top level and pass %s on its X lines"
                     % (s.name, parent.path(), ", ".join(up), s.name, ", ".join(up)))
            return
        self.body.append("%ssubckt %s (%s)" % (indent, self.q(s.name), " ".join(self.q(p) for p in s.ports)))
        inner = indent + "  "
        names = {p.name for p in s.params}
        plist = list(s.params) + [p for p in s.body if isinstance(p, Param) and p.name not in names]
        outer, shadow = self.rename, self.shadow
        self.rename = {k: v for k, v in outer.items() if k not in {p.name for p in plist}}
        self.shadow = shadow + tuple(p.name for p in plist)
        try:
            self.subckt_params(s, plist, inner)
            self.body.append("%sparameters $mfactor=1.0" % inner)
            self.items(s.body, scope, inner)
        finally:
            self.rename, self.shadow = outer, shadow
        self.body.append("%sends" % indent)

    def subckt_params(self, s: Subckt, plist: Sequence[Param], indent: str,
                      extra: Sequence[str] = ()) -> None:
        """A subckt's parameters, overridable as in HSPICE.

        VACASK lets an instance override only a primary parameter, one whose
        default is a constant when the deck is parsed; a default that refers to a
        top-level parameter or to another parameter makes it dependent, and an
        override of it is an error.  Top-level values are constants here, so they
        are folded into defaults (wp=2.5*wn becomes primary); a default that still
        depends on another subckt parameter and that some X line overrides gets
        the constant NOT_GIVEN as its default plus an effective-value parameter
        p__v=(p==NOT_GIVEN) ? (default) : p, and every expression in the body
        reads p__v.  extra: the enclosing parameters a lifted nested definition
        declares as its own (every X line passes them, so the default never acts).
        """
        consts = {k: v for k, v in self.values.items() if k not in self.shadow}
        local = {p.name for p in plist} | set(extra)
        over = self.overridden.get(s.name, set())
        taken = set(local) | {"$mfactor"}
        for p in extra:
            self.body.append("%sparameters %s=0.0" % (indent, X.param_ident(p, ENGINE)))
        for p in plist:
            name = X.param_ident(p.name, ENGINE)
            if name == "$mfactor" or not re.match(r"[A-Za-z_$][A-Za-z0-9_$]*$", name):
                self.err(p.origin, "subckt %s: parameter name %r cannot be used on VACASK" % (s.name, p.name))
                continue
            e = X.fold(X.substitute(p.expr, self.rename) if self.rename else p.expr, consts)
            try:
                if isinstance(e, (Num, Str)) or p.name not in over:
                    self.body.append("%sparameters %s=%s" % (indent, name, self.pv(e, p.origin, "parameter " + p.name)))
                    continue
                eff = _fresh(p.name + "__v", taken)
                test = Binary("==", Name(p.name), Num(NOT_GIVEN))
                self.body.append("%sparameters %s=%s" % (indent, name, fmt(NOT_GIVEN)))
                self.body.append("%sparameters %s=%s" % (indent, X.param_ident(eff, ENGINE),
                                                         self.pv(Ternary(test, e, Name(p.name)), p.origin,
                                                                 "parameter " + p.name)))
                self.rename[p.name] = Name(eff)
            except _Skip:
                pass

    # -- nested definitions on a Spectre netlist (§3.7, §4.5 item 11) [E65]
    def _lift_plan(self) -> None:
        """Which nested definitions are lifted, and the enclosing parameters each declares: its own
        reads of enclosing parameters (tables.enclosing_reads, which walks Cond), plus, to a fixpoint,
        every parameter a nested definition it instantiates needs and it neither declares nor
        overrides on the X line (the lift passes p=p there, so the parameter must be visible)."""
        nested: List[Tuple[Subckt, T.Scope, Tuple[str, ...]]] = []

        def walk(items: Sequence[object], scope: T.Scope, shadow: Tuple[str, ...]) -> None:
            for it in items:
                if isinstance(it, Subckt):
                    if scope.in_subckt:
                        nested.append((it, scope, shadow))
                    walk(it.body, T.Scope(it.body, scope, it), shadow + tuple(p.name for p in _plist(it)))
        walk(self.nl.body, T.Scope(self.nl.body), ())
        needs: Dict[int, Set[str]] = {id(s): set(T.enclosing_reads(s, shadow)) for s, _sc, shadow in nested}
        changed = True
        while changed:
            changed = False
            for s, scope, shadow in nested:
                own = {p.name for p in _plist(s)}
                inner = T.Scope(s.body, scope, s)
                for it in flat_items(s.body):
                    if not (isinstance(it, Instance) and it.kind == "x"):
                        continue
                    t = inner.find_subckt(it.master or "")
                    if t is None or id(t) not in needs or t is s:
                        continue
                    for p in needs[id(t)]:
                        if p in it.params or p in own or p not in shadow or p in needs[id(s)]:
                            continue
                        needs[id(s)].add(p)
                        changed = True
        for s, scope, _shadow in nested:
            ps = sorted(needs[id(s)])
            if ps and scope.subckt is not None:
                self.lifted[id(s)] = (_fresh("%s__%s" % (scope.subckt.name, s.name), self.taken), ps)

    def lifted_subckt(self, s: Subckt, where: T.Scope) -> None:
        """A lifted nested definition printed at top level under its fresh name, with the enclosing
        parameters it needs declared as its own (§4.5 item 11)."""
        name, extra = self.lifted[id(s)]
        scope = T.Scope(s.body, where, s)
        self.body.append("subckt %s (%s)" % (self.q(name), " ".join(self.q(p) for p in s.ports)))
        plist = _plist(s)
        outer, shadow = self.rename, self.shadow
        self.rename = {}
        self.shadow = tuple(extra) + tuple(p.name for p in plist)
        try:
            self.subckt_params(s, plist, "  ", extra)
            self.body.append("  parameters $mfactor=1.0")
            self.items(s.body, scope, "  ")
        finally:
            self.rename, self.shadow = outer, shadow
        self.body.append("ends")

    def model(self, m: Model, indent: str) -> None:
        row = T.target(m)
        params, notes = T.model_params(m, ENGINE, binned=m.base is not None, options=self.nl.options,
                                       dialect=self.nl.dialect)
        self.add(notes)
        self.use_load(row.vacask_osdi)
        strings = T.STRING_PARAMS.get(row.vacask_module, frozenset())
        out = []
        for k, v in params:
            out.append("%s=%s" % (k, self.string(v, m.origin, k) if k in strings
                                  else self.pv(v, m.origin, "model %s parameter %s" % (m.name, k))))
        self.body.append("%smodel %s %s%s" % (indent, self.q(self.mname[m.name]), row.vacask_module,
                                              "".join(" " + o for o in out)))

    def string(self, v: Expr, origin: str, what: str) -> str:
        if isinstance(v, Str):
            return '"%s"' % v.text
        if isinstance(v, Num):
            return '"%s"' % (("%d" % v.value) if v.value == int(v.value) and abs(v.value) < 1e15
                             else repr(v.value))
        self.err(origin, "string parameter %s must be a literal, not %s" % (what, X.to_text(v)))
        raise _Skip()

    # -- instances
    def params_of(self, inst: Instance, scope: T.Scope) -> Dict[str, Expr]:
        """The instance's parameters as printed: on a Spectre netlist after MasterRow.instance (the
        renames, the written defaults, the strips with their notes; an error row raises), with a
        Verilog-A instance's names lower-cased (§4.5 item 12) [E64]; the IR's otherwise."""
        if not self.spectre:
            return inst.params
        cached = self.iparams.get(id(inst))
        if cached is not None:
            return cached
        card = scope.model(inst.master) if inst.kind in T.MODEL_KINDS_OF and inst.master else None
        master = T.spectre_master_of(inst, card)
        params, notes = T.spectre_instance_params(inst, master, ENGINE)
        self.add(notes)
        if inst.kind == "y":
            params = {k.lower(): v for k, v in params.items()}
        self.iparams[id(inst)] = params
        return params

    def instance(self, inst: Instance, scope: T.Scope, indent: str) -> None:
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
        fn(inst, scope, indent)

    def line(self, indent: str, inst: Instance, nodes: Sequence[str], master: str,
             params: Sequence[str]) -> None:
        self.body.append("%s%s (%s) %s%s" % (indent, self.q(inst.name), " ".join(self.q(n) for n in nodes),
                                            master, "".join(" " + p for p in params)))

    def mult(self, inst: Instance, scope: T.Scope) -> Optional[Expr]:
        return T.multiplier(self.params_of(inst, scope).get("m"), scope.in_subckt, ENGINE)

    def mf(self, inst: Instance, scope: T.Scope) -> List[str]:
        """$mfactor=... for an element that takes the multiplier as a parameter."""
        m = self.mult(inst, scope)
        if m is None or T.MULT[ENGINE][inst.kind] != "param":
            return []
        return ["$mfactor=" + self.pv(m, inst.origin, "multiplier of " + inst.name)]

    def plist(self, inst: Instance, skip: Sequence[str] = ("m",), element: str = "",
              level: Optional[int] = None, scope: Optional[T.Scope] = None) -> List[str]:
        out = []
        params = self.params_of(inst, scope) if scope is not None else inst.params
        for k, v in params.items():
            if k in skip:
                continue
            v = T.scaled(element or inst.kind, k, v, self.s, level)
            out.append("%s=%s" % (k, self.pv(v, inst.origin, "%s parameter %s" % (inst.name, k))))
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
        return m

    def i_r(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        self.rcl(inst, scope, indent)

    def i_c(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        self.rcl(inst, scope, indent)

    def i_l(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        self.rcl(inst, scope, indent)

    def rcl(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        k = inst.kind
        params_in = self.params_of(inst, scope)
        if "ic" in params_in:
            self.err(inst.origin, "%s: IC= on a %s is not supported (use .ic)" % (inst.name, T.ELEMENT_NAME[k]))
            return
        value, skip, mf = inst.value, ("m",), None
        tail: List[str] = []
        if self.spectre and k == "r":
            if isinstance(value, Num) and value.value == 0.0:
                # r = 0: a 0 V source, a short whose flow is the resistor current (§4.5 item 10) [E22]
                self.add([note(inst.origin or inst.name, "%s: r=0 printed as a 0 V vamos_vsource (VACASK's "
                                                          "resistor masters cannot take r=0)" % inst.name)])
                self.line(indent, inst, inst.nodes, self.use_builtin("vamos_vsource"), ["dc=0.0"])
                return
            noisy = params_in.get("isnoisy")
            if noisy is not None:
                skip = ("m", "isnoisy")
                if _is_no(noisy):
                    tail.append("noisy=0")              # §4.5 item 7: resistor and sp_resistor
        if k == "l" and inst.name in scope.coupled:
            # a coupled inductor's multiplier and TCs are folded into its value (tables.coupled_inductance)
            card = self.card(inst, scope) if inst.master is not None else None
            folded = T.coupled_inductance(inst, self.mult(inst, scope), card, *T.run_temps(self.nl))
            if folded is not None:
                value, skip, mf = folded, T.COUPLED_FOLDED, []
        if mf is None:
            mf = self.mf(inst, scope)
        extra = [p for p in params_in if p not in skip]
        simple, key = _SIMPLE[k]
        if inst.master is None and not extra:
            if value is None:
                self.err(inst.origin, "%s has no value" % inst.name)
                return
            params = ["%s=%s" % (key, self.pv(value, inst.origin, "value of " + inst.name))]
            self.line(indent, inst, inst.nodes, self.use_builtin(simple), params + tail + mf)
            return
        if inst.master is not None:
            master = self.q(self.mname[self.card(inst, scope).name])
        else:
            master = self.use_builtin(_SPICE_RCL[k])
        params = []
        if value is not None:
            params.append("%s=%s" % (key, self.pv(value, inst.origin, "value of " + inst.name)))
        self.line(indent, inst, inst.nodes, master, params + self.plist(inst, skip, scope=scope) + tail + mf)

    def i_k(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        if len(inst.ctrl) != 2 or inst.value is None:
            self.err(inst.origin, "mutual inductance %s needs two inductors and a coupling" % inst.name)
            return
        params = ["k=" + self.pv(inst.value, inst.origin, "coupling of " + inst.name),
                  'ind1="%s"' % inst.ctrl[0], 'ind2="%s"' % inst.ctrl[1]]
        self.line(indent, inst, [], self.use_builtin("vamos_mutual"), params)

    def i_v(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        self.line(indent, inst, inst.nodes, self.use_builtin("vamos_vsource"), self.source(inst))

    def i_i(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        self.line(indent, inst, inst.nodes, self.use_builtin("vamos_isource"),
                  self.source(inst) + self.mf(inst, scope))

    def i_e(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        self.controlled(inst, scope, indent, "vamos_vcvs")

    def i_g(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        self.controlled(inst, scope, indent, "vamos_vccs")

    def i_f(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        self.controlled(inst, scope, indent, "vamos_cccs")

    def i_h(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        self.controlled(inst, scope, indent, "vamos_ccvs")

    def controlled(self, inst: Instance, scope: T.Scope, indent: str, model: str) -> None:
        if inst.value is None:
            self.err(inst.origin, "%s has no gain" % inst.name)
            return
        params = ["gain=" + self.pv(inst.value, inst.origin, "gain of " + inst.name)]
        if inst.kind in ("f", "h"):
            if len(inst.ctrl) != 1:
                self.err(inst.origin, "%s needs one controlling voltage source" % inst.name)
                return
            params.append('ctlinst="%s"' % inst.ctrl[0])
        self.line(indent, inst, inst.nodes, self.use_builtin(model), params + self.mf(inst, scope))

    def i_b(self, inst: Instance, scope: T.Scope, indent: str) -> None:
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
                e = Binary("*", e, m)       # behavioral sources take no $mfactor (§4.3.7)
        text = self.bv(e, inst.origin, "expression of " + inst.name)
        self.body.append("%s%s (%s) %s=%s" % (indent, self.q(inst.name),
                                              " ".join(self.q(n) for n in inst.nodes), inst.expr_kind, text))

    def i_d(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        m = self.card(inst, scope)
        T.target(m)
        lv = T.level_of(m)                       # .option scale reaches a LEVEL 3 diode only
        params = []
        if inst.value is not None:
            area = T.scaled("d", "area", inst.value, self.s, lv)
            params.append("area=" + self.pv(area, inst.origin, "area of " + inst.name))
        self.line(indent, inst, inst.nodes, self.q(self.mname[m.name]),
                  params + self.plist(inst, ("m", "area"), level=lv, scope=scope) + self.mf(inst, scope))

    def i_q(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        m = self.card(inst, scope)
        T.target(m)
        nodes = list(inst.nodes) + (["0"] if len(inst.nodes) == 3 else [])   # SPICE: substrate on ground
        params = []
        if inst.value is not None:
            params.append("area=" + self.pv(inst.value, inst.origin, "area of " + inst.name))
        self.line(indent, inst, nodes, self.q(self.mname[m.name]),
                  params + self.plist(inst, ("m", "area"), scope=scope) + self.mf(inst, scope))

    def i_j(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        m = self.card(inst, scope)
        T.target(m)
        params = []
        if inst.value is not None:
            params.append("area=" + self.pv(inst.value, inst.origin, "area of " + inst.name))
        self.line(indent, inst, inst.nodes, self.q(self.mname[m.name]),
                  params + self.plist(inst, ("m", "area"), scope=scope) + self.mf(inst, scope))

    def i_m(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        if not self.spectre:
            self.add(T.mos_scale_warnings(inst, self.nl.options))
        if inst.master and scope.model(inst.master) is None and scope.bins(inst.master):
            self.binned(inst, scope, indent, scope.bins(inst.master) or [])
            return
        m = self.card(inst, scope)
        T.target(m)
        if not self.spectre:                       # the HSPICE junction warnings (§4.4, §4.5 item 14)
            self.add(T.mos_junction_warnings(inst, m, self.nl.options))
        self.line(indent, inst, inst.nodes, self.q(self.mname[m.name]),
                  self.plist(inst, scope=scope) + self.mf(inst, scope))

    def binned(self, inst: Instance, scope: T.Scope, indent: str, bins: List[Model]) -> None:
        """The @if/@elseif chain of copies, one per bin; @else binds a master that does not exist.
        A Spectre model group (bin_rule="spectre") uses the group order, exact bounds (the master's
        defaults for an omitted one) and the total w (§3.9, §4.5 item 3)."""
        base = inst.master or ""
        params_in = self.params_of(inst, scope)
        spectre_group = any(b.bin_rule == "spectre" for b in bins)
        l, w, nf = params_in.get("l"), params_in.get("w"), params_in.get("nf")
        if l is None or w is None:
            self.err(inst.origin, "%s: binned model %s needs l= and w= on the instance" % (inst.name, base))
            return
        bounds = []
        for b in bins:
            T.target(b)
            bounds.append((b, T.bin_bounds_spectre(b, self.values) if spectre_group else T.bin_bounds(b, self.values)))
        if spectre_group:
            geo = T.constant((l, w), self.values, self.shadow)
            chosen = None if geo is None else T.select_bin_spectre(bounds, geo[0], geo[1], self.s)
        else:
            geo = T.constant((l, w, nf if nf is not None else Num(1.0)), self.values, self.shadow)
            chosen = None if geo is None else T.select_bin(bounds, geo[0], geo[1], geo[2], self.s)
        if geo is not None and chosen is None:
            self.err(inst.origin, "no bin of %s for l=%.6g, w=%.6g%s (instance %s%s)"
                     % (base, geo[0] * self.s, geo[1] * self.s,
                        "" if nf is None or spectre_group else ", nf=%.6g" % geo[2], inst.name,
                        " in subckt " + scope.path() if scope.in_subckt else ""))
            return
        params = self.plist(inst, scope=scope) + self.mf(inst, scope)
        nodes = " ".join(self.q(n) for n in inst.nodes)
        for i, (b, bd) in enumerate(bounds):
            cond = T.bin_guard_spectre(bd, l, w, self.s) if spectre_group else T.bin_guard(bd, l, w, nf, self.s)
            guard = self.pv(cond, inst.origin, "bin guard of " + inst.name)
            self.body.append("%s%s %s" % (indent, "@if" if i == 0 else "@elseif", guard))
            self.body.append("%s  %s (%s) %s%s" % (indent, self.q(inst.name), nodes, self.q(self.mname[b.name]),
                                                  "".join(" " + p for p in params)))
        self.body.append("%s@else" % indent)
        self.nobin[len(self.body)] = (
            "no bin of %s for l=%s, w=%s%s (scale %s; instance %s%s)"
            % (base, X.to_text(l), X.to_text(w), "" if nf is None or spectre_group else ", nf=" + X.to_text(nf),
               fmt(self.s), inst.name, " in subckt " + scope.path() if scope.in_subckt else ""))
        self.body.append("%s  %s (%s) %s%s" % (indent, self.q(inst.name), nodes, self.q(self.missing[base]),
                                              "".join(" " + p for p in params)))
        self.body.append("%s@end" % indent)

    def i_x(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        sub = scope.find_subckt(inst.master or "")
        if sub is None:
            self.err(inst.origin, "%s: subckt %s is not defined" % (inst.name, inst.master))
            return
        if len(inst.nodes) != len(sub.ports):
            self.err(inst.origin, "%s: %d nodes for subckt %s with %d ports"
                     % (inst.name, len(inst.nodes), sub.name, len(sub.ports)))
            return
        params = []
        for k, v in inst.params.items():
            if k == "m":
                continue
            params.append("%s=%s" % (X.param_ident(k, ENGINE),
                                     self.pv(v, inst.origin, "%s parameter %s" % (inst.name, k))))
        lifted = self.lifted.get(id(sub))
        master = sub.name
        if lifted is not None:                     # the lifted definition takes the enclosing parameters
            master, extra = lifted
            for p in extra:
                if p not in inst.params:
                    params.append("%s=%s" % (X.param_ident(p, ENGINE),
                                             self.pv(Name(p), inst.origin, "%s parameter %s" % (inst.name, p))))
        self.line(indent, inst, inst.nodes, self.q(master), params + self.mf(inst, scope))

    def i_y(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        module = inst.master or ""
        if not module:
            self.err(inst.origin, "Verilog-A instance %s has no module" % inst.name)
            return
        if module in T.VA_INSTANCE_MODULES:
            self.line(indent, inst, inst.nodes, self.use_builtin(module),
                      self.plist(inst, element="y", scope=scope) + self.mf(inst, scope))
            return
        # VA parameters are model parameters unless the module says otherwise:
        # one model card per instance, in the instance's scope (§4.4).
        card = _fresh("%s__va" % inst.name, self.taken)
        params = self.plist(inst, element="y", scope=scope)
        self.body.append("%smodel %s %s%s" % (indent, self.q(card), self.q(module), "".join(" " + p for p in params)))
        self.line(indent, inst, inst.nodes, self.q(card), self.mf(inst, scope))

    # -- sources (§4.3.8; Spectre: VAMOS_SPECTRE_DESIGN.md §4.5 item 8)
    def source(self, inst: Instance) -> List[str]:
        src = inst.source or Source()
        return self.source_fields(inst.name, inst.origin, src, self.source_type(src))

    def source_type(self, src: Source) -> Optional[str]:
        """The type a Spectre source prints: "dc" when its dc is given (VACASK uses dc only under
        type="dc" [E17]) or it has no waveform; its waveform type otherwise (a derived dc follows the
        fields [E38]).  None on the HSPICE path (the waveform's own type, as today)."""
        if not self.spectre:
            return None
        if src.wave is None or src.dc is not None:
            return "dc"
        return src.wave

    def source_fields(self, name: str, o: str, src: Source, state: Optional[str]) -> List[str]:
        """The parameters of a vsource/isource: the waveform fields, dc, mag/phase; state: the type
        printed on a Spectre source ("dc" or its waveform), None for the HSPICE path."""
        if src.code_uri is not None:
            if src.dc is not None or src.ac is not None or src.wave is not None:
                self.err(o, "%s: a bridge source has no dc/ac/wave" % name)
                raise _Skip()
            try:
                init = T.parse_code_uri(src.code_uri)[0]
            except T.TableError as exc:
                self.err(o, "%s: %s" % (name, exc))
                raise _Skip()
            if init != T.BRIDGE_INIT[ENGINE]:
                self.err(o, "%s: code URI %s is not for VACASK (%s)" % (name, src.code_uri,
                                                                        T.BRIDGE_INIT[ENGINE]))
                raise _Skip()
            return ['type="pwl"', 'file="%s"' % src.code_uri]
        out: List[str] = []
        pv = lambda e, what: self.pv(e, o, "%s %s" % (name, what))   # noqa: E731
        w = src.wave
        if state is not None and (w is not None or state == "dc"):
            out.append('type="%s"' % ("sine" if state == "sin" else state))
        if w is not None:
            try:
                f = T.wave(src, name)
            except T.TableError as exc:
                self.err(o, str(exc))
                raise _Skip()
            if w == "pulse":
                out += ([] if state is not None else ['type="pulse"']) + [
                    "val0=" + pv(f["v1"], "v1"), "val1=" + pv(f["v2"], "v2"),
                    "delay=" + pv(f["td"], "td"), "rise=" + pv(f["tr"], "tr"),
                    "fall=" + pv(f["tf"], "tf"), "width=" + pv(f["pw"], "pw")]
                if "per" in f:
                    out.append("period=" + pv(f["per"], "per"))
            elif w == "sin":
                out += ([] if state is not None else ['type="sine"']) + [
                    "sinedc=" + pv(f["vo"], "vo"), "ampl=" + pv(f["va"], "va"),
                    "freq=" + pv(f["freq"], "freq"), "delay=" + pv(f["td"], "td"),
                    "theta=" + pv(f["theta"], "theta"), "tdphase=" + pv(f["phase"], "phase")]
            elif w == "exp":
                td1, td2 = f["td1"], f["td2"]
                rel: Expr = (Num(td2.value - td1.value) if isinstance(td1, Num) and isinstance(td2, Num)
                             else Binary("-", td2, td1))       # VACASK td2 is relative to delay
                out += ([] if state is not None else ['type="exp"']) + [
                    "val0=" + pv(f["v1"], "v1"), "val1=" + pv(f["v2"], "v2"),
                    "delay=" + pv(td1, "td1"), "tau1=" + pv(f["tau1"], "tau1"),
                    "td2=" + pv(rel, "td2-td1"), "tau2=" + pv(f["tau2"], "tau2")]
            else:                                              # pwl
                out += ([] if state is not None else ['type="pwl"']) + [
                    "wave=[%s]" % ", ".join("%s, %s" % (pv(t, "time"), pv(v, "value")) for t, v in src.points)]
                if "td" in f:
                    out.append("delay=" + pv(f["td"], "td"))
        if src.dc is not None:
            out.append("dc=" + pv(src.dc, "dc"))
        elif w is None:
            out.append("dc=0.0")
        if src.ac is not None:
            out += ["mag=" + pv(src.ac[0], "ac magnitude"), "phase=" + pv(src.ac[1], "ac phase")]
        return out

    # -- control block (§4.4)
    def control(self) -> List[str]:
        nl, o = self.nl, "control"
        spice = "spice" in nl.options or self.spectre              # Spectre's defaults are 27 (§3.10)
        temp = nl.temp if nl.temp is not None else (27.0 if spice else 25.0)
        tnom = nl.tnom if nl.tnom is not None else (27.0 if spice else 25.0)
        opts = ["tran_lteimplicit=0"]
        try:
            opts += ["temp=" + fmt(self.num(temp, o, "temp")), "tnom=" + fmt(self.num(tnom, o, "tnom"))]
            if self.spectre:                                       # no HSPICE tolerance notes (§3.10, §4.4)
                solver = {"gmin": self.num(nl.options["gmin"], o, "gmin")} if "gmin" in nl.options else {}
            else:
                solver, notes = T.solver_options(nl, o)
                self.notes.extend(notes)
            if "gmin" in solver:
                opts.append("gmin=" + fmt(solver["gmin"]))
            if solver.get("method") == "gear":
                opts.append('tran_method="gear"')
        except _Skip:
            pass
        except T.TableError as exc:
            self.err(o, str(exc))
        lines = ["control", "  abort always", "  options " + " ".join(opts)]
        saves = self.saves()
        if saves:
            lines.append("  save " + " ".join(saves))
        nodeset = self.pairs(nl.nodesets, "nodeset")
        if self.op:
            lines.append("  analysis %s op%s" % (self.analysis, " nodeset=" + nodeset if nodeset else ""))
        else:
            lines.append("  analysis %s %s" % (self.analysis, self.tran(nodeset)))
        lines.append("endc")
        return lines

    def tran(self, nodeset: str) -> str:
        a = self.nl.tran()
        if a is None:
            self.err("", "the netlist has no .tran analysis (ams/deck.py synthesises one)")
            return "tran"
        args = a.args
        out = []
        try:
            for key in ("step", "stop"):
                if args.get(key) is None:
                    self.err(a.origin, ".tran has no %s" % key)
                    raise _Skip()
                out.append("%s=%s" % (key, fmt(self.num(args[key], a.origin, ".tran " + key))))
            if args.get("start"):
                out.append("start=" + fmt(self.num(args["start"], a.origin, ".tran start")))
            if args.get("maxstep") is not None:
                out.append("maxstep=" + fmt(self.num(args["maxstep"], a.origin, ".tran maxstep")))
        except _Skip:
            pass
        if args.get("uic"):
            out.append('icmode="uic"')
        ic = self.pairs(self.nl.ics, "ic")
        if ic:
            out.append("ic=" + ic)
        if nodeset:
            out.append("nodeset=" + nodeset)
        return "tran " + " ".join(out)

    def pairs(self, d: Dict[str, float], what: str) -> str:
        if not d:
            return ""
        items = []
        for node, v in d.items():
            try:
                items.append('"%s", %s' % (node.replace(".", ":"), fmt(self.num(v, what, "%s v(%s)" % (what, node)))))
            except _Skip:
                pass
        return "{%s}" % ", ".join(items)

    def saves(self) -> List[str]:
        out: List[str] = []
        for analysis, kind, target in self.nl.probes:
            if analysis.lower() != "tran":
                continue
            if kind == "v":
                if target == "*":
                    s = ["default"]
                else:
                    s = ["v(%s)" % quote(t.strip().replace(".", ":")) for t in target.split(",")]
            elif kind == "i":
                s = [self.isave(target)]
            else:
                self.err("probe", "unknown probe kind %r" % (kind,))
                continue
            for x in s:
                if x and x not in out:
                    out.append(x)
        return out

    def isave(self, target: str) -> str:
        inst = _resolve(self.nl, target)
        name = quote(target.replace(".", ":"))
        if inst is None:
            self.err("probe", "i(%s): no such element" % target)
            return ""
        if inst.kind in ("v", "l", "e", "h") or (inst.kind == "b" and inst.expr_kind == "v"):
            return "i(%s)" % name
        if inst.kind == "r":                                  # resistor and sp_resistor output i
            return "p(%s, i)" % name
        self.err("probe", "i(%s): the %s element has no branch current VACASK can save" % (target, inst.kind))
        return ""

    # -- the plan's control block (VAMOS_SPECTRE_DESIGN.md §4.5 items 4-6, §5, §7.1) ---------------
    def path(self, ir_path: str) -> str:
        """An IR instance or node path (x1.r1) as VACASK spells it (x1:r1), quoted when needed."""
        return self.q(ir_path.replace(".", ":"))

    def spath(self, ir_path: str) -> str:
        """An IR instance path inside a string argument, alter instance("x1:r1") and sweep
        instance="x1:r1": the string carries the quotes, the name none (§5.2, §5.3 [E]; phase 1b)."""
        return ir_path.replace(".", ":")

    def var_name(self, p: str) -> str:
        return VAR_PREFIX + p

    def setting_text(self, v: object, origin: str, what: str) -> str:
        """A plan Setting (float, str, list of floats) as VACASK text."""
        if isinstance(v, bool):
            return "1" if v else "0"
        if isinstance(v, (int, float)):
            return fmt(float(v))
        if isinstance(v, str):
            return '"%s"' % v
        if isinstance(v, (list, tuple)):
            return "[%s]" % ", ".join(self.setting_text(x, origin, what) for x in v)
        self.err(origin, "%s: value %r is not a plan setting" % (what, v))
        raise _Skip()

    def option_values(self, args: Dict[str, object], origin: str) -> List[Tuple[str, str]]:
        """The VACASK options an options action sets, in _OPTION_ORDER (tables.SPECTRE_OPTIONS)."""
        out: List[Tuple[str, str]] = []
        omap = T.SPECTRE_OPTIONS[ENGINE]
        for key in _OPTION_ORDER:
            if key in args:
                out.append((omap[key], self.setting_text(args[key], origin, "option " + key)))
        return out

    def plan_control(self) -> List[str]:
        plan = self.plan
        assert plan is not None
        lines = ["control", "  abort except analysis"]
        self.vstate: Dict[str, str] = {}             # VACASK option -> the value text in force
        self.src_state: Dict[str, str] = {}          # source path -> the type printed (§5.3)
        self.active_saves: Optional[List[str]] = None
        seen_options = False
        for act in plan.actions:
            try:
                if act.op == "options":
                    vals = self.option_values(act.args, act.origin)
                    if not seen_options:
                        seen_options = True
                        have = {k for k, _v in vals}
                        for key, default in (("temp", self.nl.temp), ("tnom", self.nl.tnom)):
                            if key not in have:                # always set (§3.10)
                                vals.append((key, fmt(self.num(27.0 if default is None else default, act.origin, key))))
                        vals.sort(key=lambda kv: _OPTION_ORDER.index(_spectre_key(kv[0])))
                        lines.append("  options " + " ".join(list(_HEAD_OPTIONS) + ["%s=%s" % kv for kv in vals]))
                    else:
                        changed = [(k, v) for k, v in vals if self.vstate.get(k) != v]
                        if changed:
                            lines.append("  options " + " ".join("%s=%s" % kv for kv in changed))
                    self.vstate.update(dict(vals))
                elif act.op == "var":
                    lines.append("  var " + " ".join("%s=%s" % (self.var_name(k), self.setting_text(v, act.origin, k))
                                                      for k, v in act.args.items()))
                elif act.op == "alter":
                    lines += self.alter_lines(act)
                elif act.op == "source":
                    lines += self.source_lines(act)
                elif act.op == "analysis":
                    if not seen_options:                       # a plan without an options action
                        seen_options = True
                        vals = [("temp", fmt(self.num(27.0 if self.nl.temp is None else self.nl.temp, act.origin, "temp"))),
                                ("tnom", fmt(self.num(27.0 if self.nl.tnom is None else self.nl.tnom, act.origin, "tnom")))]
                        lines.append("  options " + " ".join(list(_HEAD_OPTIONS) + ["%s=%s" % kv for kv in vals]))
                        self.vstate.update(dict(vals))
                    if act.step is None:
                        self.err(act.origin, "analysis action without a step")
                        continue
                    lines += self.analysis_lines(act.step, act.origin)
                elif act.op == "note":
                    continue
                else:
                    self.err(act.origin, "unknown plan action %r" % (act.op,))
            except _Skip:
                pass
            except T.TableError as exc:
                self.err(act.origin, str(exc))
        lines.append("endc")
        return lines

    def alter_lines(self, act: Action) -> List[str]:
        target = act.args.get("target")
        if not (isinstance(target, tuple) and len(target) == 3):
            self.err(act.origin, "alter without a 3-tuple target")
            return []
        kind, name, param = target
        value = act.args.get("value")
        o = act.origin or "alter"
        if kind == "instance":
            inst = _resolve(self.nl, name)
            if inst is None:
                self.err(o, "alter: no instance %s" % name)
                return []
            if "source" in act.args:                      # a source field: one complete alter (§5.3) [E37]
                src = act.args["source"]
                state = self.src_state.get(name) or self.source_type(src) or "dc"
                if src.dc is None and src.wave is not None:
                    state = src.wave                      # a derived dc stays in its waveform type
                elif param == "dc" or src.wave is None:
                    state = "dc"                          # an alter of dc makes it given from then on
                self.src_state[name] = state
                return ['  alter instance("%s") %s' % (self.spath(name), " ".join(self.source_fields(inst.name, o, src, state)))]
            pname = self.instance_param(inst, param)
            return ['  alter instance("%s") %s=%s' % (self.spath(name), pname, self.setting_text(value, o, param))]
        if kind == "model":
            cards = self.model_names(name, o)
            pname = self.card_param(name, param)
            return ['  alter model(%s) %s=%s' % (", ".join('"%s"' % c for c in cards), pname,
                                               self.setting_text(value, o, param))]
        if kind == "option":
            key = T.SPECTRE_OPTIONS[ENGINE].get(name)
            if key is None:
                self.err(o, "alter of option %s: no VACASK option" % name)
                return []
            text = self.setting_text(value, o, name)
            self.vstate[key] = text
            return ["  options %s=%s" % (key, text)]
        if kind == "variable":
            return ["  var %s=%s" % (self.var_name(name), self.setting_text(value, o, name))]
        self.err(o, "alter target kind %r is not supported" % (kind,))
        return []

    def source_lines(self, act: Action) -> List[str]:
        path, typ, src = act.args.get("path"), act.args.get("type"), act.args.get("source")
        if not isinstance(path, str) or not isinstance(src, Source) or not isinstance(typ, str):
            self.err(act.origin, "source action needs path, type and source")
            return []
        inst = _resolve(self.nl, path)
        if inst is None:
            self.err(act.origin, "source action: no instance %s" % path)
            return []
        state = "dc" if typ == "dc" else typ
        self.src_state[path] = state
        return ['  alter instance("%s") %s' % (self.spath(path), " ".join(self.source_fields(inst.name, act.origin or "alter", src, state)))]

    def instance_param(self, inst: Instance, param: str) -> str:
        """The VACASK spelling of an instance parameter a sweep or alter targets (the master's instance
        renames; a source field through SOURCE_FIELDS)."""
        if inst.kind in ("v", "i"):
            if param in SOURCE_FIELDS:
                return SOURCE_FIELDS[param]
            raise T.TableError("%s: the %s field %s cannot be swept or altered directly on VACASK in v1 (the "
                               "deck prints a derived value for it)" % (inst.name, inst.prim or inst.kind, param))
        if not self.spectre:
            return param
        scope = T.Scope(self.nl.body)
        card = scope.model(inst.master) if inst.kind in T.MODEL_KINDS_OF and inst.master else None
        master = T.spectre_master_of(inst, card)
        return _renamed(T.SPECTRE_MASTERS[master].instance if master in T.SPECTRE_MASTERS else (), param, ENGINE)

    def card_param(self, card: str, param: str) -> str:
        m = self.nl.models().get(card)
        if m is None:
            bins = [b for b in _all_models(self.nl) if b.base == card]
            m = bins[0] if bins else None
        if m is None or not self.spectre or m.prim not in T.SPECTRE_MASTERS:
            return param
        return _renamed(T.SPECTRE_MASTERS[m.prim].params, param, ENGINE)

    def model_names(self, name: str, origin: str) -> List[str]:
        """The deck names of a card, or of every entry of a model group (§5.3)."""
        if name in self.mname:
            return [self.mname[name]]
        bins = [b for b in _all_models(self.nl) if b.base == name]
        if not bins:
            self.err(origin, "alter: no model %s" % name)
            raise _Skip()
        return [self.mname[b.name] for b in bins]

    def tran_settings(self, step: AnalysisStep) -> List[Tuple[str, str]]:
        """A tran's effective settings as VACASK options (§5.4): reltol, relref, tran_method
        (tran_maxord), tran_lteratio."""
        out: List[Tuple[str, str]] = []
        a = step.args
        if "reltol" in a:
            out.append(("reltol", self.setting_text(a["reltol"], step.id, "reltol")))
        relref = a.get("relref")
        if isinstance(relref, str):
            if relref == "pointlocal":
                out += [(k, '"pointlocal"') for k in ("relrefsol", "relrefres", "relreflte")]
            else:
                out.append(("relref", '"%s"' % relref))
        method = a.get("method")
        if isinstance(method, str):
            row = T.VACASK_METHODS.get(method.lower())
            if row is None:
                raise T.TableError("%s: tran method %s has no VACASK equivalent" % (step.name or step.id, method))
            out.append(("tran_method", '"%s"' % row[0]))
            if row[1] is not None:
                out.append(("tran_maxord", str(row[1])))
            if row[2]:
                self.add([note(step.id, "%s: %s" % (step.name or step.id, row[2]))])
        if "lteratio" in a:
            out.append(("tran_lteratio", self.setting_text(a["lteratio"], step.id, "lteratio")))
        return out

    def save_directives(self, step: AnalysisStep) -> List[str]:
        """The save directives a step needs (§5.5, §7.1): default for allpub and a full solution, plus
        the per-type directives of its SignalMap entries that default does not write (p(r, i), nc())."""
        sm = self.sigmap
        refs = list(sm.per_step.get(step.id, [])) if sm is not None else []
        allpub = (sm is not None and step.id in sm.allpub) or step.full_solution
        out: List[str] = ["default"] if allpub else []
        for ref, _spectre_name, _psf in refs:
            d = self.directive(ref, step)
            if d is None:
                continue
            if allpub and not (d.startswith("p(") or d.startswith("nc(")):
                continue                                   # default writes it
            if d not in out:
                out.append(d)
        return out

    def directive(self, ref: Ref, step: AnalysisStep) -> Optional[str]:
        kind = ref[0]
        if step.kind in ("op", "dc", "tran"):
            if kind == "v":
                return "v(%s)" % self.path(ref[1])
            if kind == "i":
                return self.current_directive(ref[1], "i", "p")
            return None
        if step.kind == "ac":
            if kind == "v":
                return "dv(%s)" % self.path(ref[1])
            if kind == "i":
                return self.current_directive(ref[1], "di", None)
            return None
        if step.kind == "noise":
            if kind == "noise":
                return "nc(%s)" % self.path(ref[1])
            return None
        return None                                        # acxf writes every tf()/zin(); onoise/gain/in always

    def current_directive(self, path: str, flow: str, opvar: Optional[str]) -> Optional[str]:
        inst = _resolve(self.nl, path)
        if inst is None:
            raise T.TableError("save: no element %s" % path)
        if self.is_short(inst) or inst.kind in ("v", "l", "e", "h") or (inst.kind == "b" and inst.expr_kind == "v"):
            return "%s(%s)" % (flow, self.path(path))
        if inst.kind == "r" and opvar is not None:
            return "%s(%s, i)" % (opvar, self.path(path))
        raise T.TableError("save: the %s element %s has no branch current VACASK can save in this analysis "
                           "(signals.resolve inserts a probe for it)" % (inst.kind.upper(), path))

    def is_short(self, inst: Instance) -> bool:
        """An r = 0 resistor printed as a 0 V source (§4.5 item 10)."""
        return self.spectre and inst.kind == "r" and isinstance(inst.value, Num) and inst.value.value == 0.0

    def column(self, ref: Ref) -> Optional[str]:
        """The rawfile column of a Ref (§6.4), None for a Ref VACASK does not write under its own name."""
        kind = ref[0]
        if kind == "v":
            return ref[1].replace(".", ":")
        if kind == "i":
            inst = _resolve(self.nl, ref[1])
            path = ref[1].replace(".", ":")
            if inst is not None and inst.kind == "r" and not self.is_short(inst):
                return path + ".i"
            return path + ":flow(br)"
        if kind == "noise":
            return "n(%s)" % ref[1].replace(".", ":")
        if kind == "onoise":
            return "onoise"
        if kind in ("gain", "inoise"):
            return "gain"                                  # in = out / gain (§6.4, §8.4)
        if kind == "tf":
            return "tf(%s)" % ref[1].replace(".", ":")
        return None

    def name_map(self) -> Dict[str, Dict[Ref, str]]:
        sm = self.sigmap
        if sm is None:
            return self.names
        for step_id, refs in sm.per_step.items():
            out = self.names.setdefault(step_id, {})
            for ref, _spectre_name, _psf in refs:
                col = self.column(ref)
                if col is not None:
                    out[ref] = col
        return self.names

    def grid(self, spec) -> str:
        """A SweepSpec after plan.build (every field a Num) as VACASK's sweep/analysis grid (§5.2)."""
        if spec.mode in ("values", "log"):             # log: the values plan.build computed (§5.2)
            return "values=[%s]" % ", ".join(fmt(_numv(v)) for v in spec.values)
        parts = ["from=%s" % fmt(_numv(spec.start)), "to=%s" % fmt(_numv(spec.stop))]
        if spec.mode == "step":
            parts.append("step=%s" % fmt(_numv(spec.step)))
        elif spec.mode in ("lin", "dec"):
            if spec.count is None:
                raise T.TableError("sweep mode %s without a point count after plan.build" % spec.mode)
            parts += ['mode="%s"' % spec.mode, "points=%d" % int(spec.count)]
        else:
            raise T.TableError("sweep mode %r is not one render prints (lin, step, values, log, dec)" % (spec.mode,))
        return " ".join(parts)

    def sweep_line(self, lv: SweepLevel) -> str:
        kind, name, param = lv.target
        if kind == "instance":
            inst = _resolve(self.nl, name)
            if inst is None:
                raise T.TableError("sweep %s: no instance %s" % (lv.id, name))
            target = 'instance="%s" parameter="%s"' % (self.spath(name), self.instance_param(inst, param))
        elif kind == "model":
            cards = self.model_names(name, lv.id)
            if len(cards) != 1:
                raise T.TableError("sweep %s: a model group (%s) cannot be swept on VACASK in v1" % (lv.id, name))
            target = 'model="%s" parameter="%s"' % (cards[0], self.card_param(name, param))
        elif kind == "option":
            target = 'option="%s"' % name
        elif kind == "variable":
            target = 'variable="%s"' % self.var_name(name)
        else:
            raise T.TableError("sweep %s: target kind %r is not a sweep statement" % (lv.id, kind))
        return "sweep %s %s %s continuation=%d" % (lv.id, target, self.grid(lv.spec), int(lv.continuation))

    def analysis_lines(self, step: AnalysisStep, origin: str) -> List[str]:
        out: List[str] = []
        want = self.save_directives(step)
        if want != self.active_saves:
            if self.active_saves is not None:
                out.append("  clear saves")
            if want:
                out.append("  save " + " ".join(want))
            self.active_saves = want
        restore: List[Tuple[str, str]] = []
        if step.kind == "tran":
            settings = self.tran_settings(step)
            changed = [(k, v) for k, v in settings if self.vstate.get(k) != v]
            if changed:
                out.append("  options " + " ".join("%s=%s" % kv for kv in changed))
                for k, v in changed:
                    prev = self.vstate.get(k)
                    if prev is None:
                        d = T.VACASK_OPTION_DEFAULTS.get(k)
                        prev = (None if d is None else str(d) if isinstance(d, int) and not isinstance(d, bool)
                                else self.setting_text(d, origin, k))
                    if prev is not None and prev != v:
                        restore.append((k, prev))
                    self.vstate[k] = v
        levels = list(step.context)
        if step.own is not None and step.own.target[0] != "freq":
            levels.append(step.own)
        indent = "  "
        for lv in levels:
            out.append(indent + self.sweep_line(lv))
            indent += "  "
        out.append(indent + self.analysis_line(step, origin))
        if restore:
            out.append("  options " + " ".join("%s=%s" % kv for kv in restore))
            self.vstate.update(dict(restore))
        return out

    def analysis_line(self, step: AnalysisStep, origin: str) -> str:
        a = step.args
        parts = ["analysis", step.id]
        freq_grid = self.grid(step.own.spec) if step.own is not None and step.own.target[0] == "freq" else None
        if step.kind in ("op", "dc"):
            parts.append("op")
        elif step.kind in ("ac", "noise", "xf"):
            parts.append({"ac": "ac", "noise": "noise", "xf": "acxf"}[step.kind])
            if step.kind in ("noise", "xf"):
                parts.append(self.out_nodes(step, origin))
            if step.kind == "noise":
                if not step.noise_input:
                    raise T.TableError("%s: noise without an input source (plan.build names vamos_nin<k>)" % step.id)
                parts.append('in="%s"' % self.path(step.noise_input))
            if freq_grid is not None:
                parts.append(freq_grid)
            else:
                if "freq" not in a:
                    raise T.TableError("%s: an %s parameter sweep needs freq" % (step.id, step.kind))
                parts.append("values=[%s]" % self.setting_text(a["freq"], origin, "freq").strip('"'))
        elif step.kind == "tran":
            parts.append("tran")
            for key, out_key in (("step", "step"), ("stop", "stop"), ("outputstart", "start"), ("maxstep", "maxstep")):
                if key in a:
                    parts.append("%s=%s" % (out_key, self.setting_text(a[key], origin, key)))
            if a.get("skipdc") in ("yes", True):
                parts.append('icmode="uic"')
            ic_mode = a.get("ic", "all")
            if ic_mode in ("all", "node") and self.nl.ics:
                if step.uses and a.get("useprevic") in ("yes", True):
                    pass                                   # the stored solution is the ic (below)
                else:
                    ic = self.pairs(self.nl.ics, "ic")
                    if ic:
                        parts.append("ic=" + ic)
        else:
            raise T.TableError("%s: analysis kind %r is not one VACASK runs" % (step.id, step.kind))
        if step.uses:
            if step.kind == "tran" and a.get("useprevic") in ("yes", True):
                parts.append('ic="%s"' % step.uses)
            elif a.get("prevoppoint") in ("yes", True):
                parts.append('nodeset="%s" opsolve=0' % step.uses)
            else:
                parts.append('nodeset="%s"' % step.uses)
        elif self.nl.nodesets:
            ns = self.pairs(self.nl.nodesets, "nodeset")
            if ns:
                parts.append("nodeset=" + ns)
        if step.stores:
            parts.append('store="%s"' % step.stores)
        return " ".join(parts)

    def out_nodes(self, step: AnalysisStep, origin: str) -> str:
        """The noise/xf output pair, AnalysisStep.args["p"] and ["n"] (§3.11 `p n`, IR node paths; plan.build
        writes n = "0" for a single node): out="p" against ground, else out=["p", "n"]."""
        p, n = step.args.get("p"), step.args.get("n", "0")
        if isinstance(p, str) and p and isinstance(n, str):
            if n == "0":
                return 'out="%s"' % self.path(p)
            return 'out=["%s", "%s"]' % (self.path(p), self.path(n))
        raise T.TableError("%s: %s without an output (AnalysisStep.args['p'], ['n']: the node pair)" % (step.id, step.kind))


class _Skip(Exception):
    """An element whose error is already recorded; the walk moves on."""


def _is_no(v: Expr) -> bool:
    """isnoisy=no, as the parser may spell it: Str/Name no|false|0, or the number 0."""
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


def _spectre_key(vacask_option: str) -> str:
    """The Spectre key of a VACASK option (the inverse of tables.SPECTRE_OPTIONS["vacask"])."""
    for k, v in T.SPECTRE_OPTIONS[ENGINE].items():
        if v == vacask_option:
            return k
    return vacask_option


def _renamed(rules: Sequence[T.ParamRule], param: str, engine: str) -> str:
    """The engine spelling of a parameter under a master's rules: a rename's target (without the
    instance_ prefix on Xyce), else the name itself."""
    for r in rules:
        if r.name == param and r.action == "rename":
            name = r.value
            if engine == "xyce" and name.startswith("instance_"):
                name = name[len("instance_"):]
            return name
    return param


def _all_models(nl: Netlist) -> List[Model]:
    out = [m for m in nl.body if isinstance(m, Model)]
    for s in T.all_subckts(nl):
        out += [m for m in s.body if isinstance(m, Model)]
    return out


def _plist(s: Subckt) -> List[Param]:
    names = {p.name for p in s.params}
    return list(s.params) + [p for p in s.body if isinstance(p, Param) and p.name not in names]


def _number(d, v: object, origin: str, what: str) -> float:
    """A control-block value as a float: the IR keeps them evaluated; an Expr is
    evaluated with the top-level values (shared with xyce.py)."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    try:
        return X.evaluate(v, d.values)                    # type: ignore[arg-type]
    except (X.EvalError, TypeError, AttributeError) as exc:
        d.err(origin, "%s must be a constant: %s" % (what, exc))
        raise _Skip()


def _fresh(want: str, taken: set) -> str:
    k, cand = 0, want
    while cand in taken:
        k += 1
        cand = "%s_%d" % (want, k)
    taken.add(cand)
    return cand


def _resolve(nl: Netlist, target: str) -> Optional[Instance]:
    """The element a hierarchical name (x1.x2.v1) names, through the subckt definitions (instances
    inside Cond branches included)."""
    parts = target.split(".")
    items: Sequence[object] = nl.body
    scope = T.Scope(nl.body)
    for i, part in enumerate(parts):
        found = None
        for it in flat_items(items):
            if isinstance(it, Instance) and it.name == part:
                found = it
                break
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

smoke_netlist = T.smoke_netlist


def smoke(nl: Netlist, dir: str, osdi: Sequence[str] = (), nvc_libdir: Optional[str] = None,
          timeout: int = 600) -> List[Note]:
    """Run the smoke deck standalone (see the module docstring); [] when it passes."""
    from vamos.ams import engines
    origin = os.path.join(dir, SMOKE_DECK)
    deck = _Deck(smoke_netlist(nl), SMOKE_ANALYSIS, osdi, True)
    try:
        text = deck.render(None)
    except NoteError as exc:
        return [n for n in exc.notes if n.severity == ERROR]
    os.makedirs(dir, exist_ok=True)
    with open(origin, "w", encoding="utf-8") as fh:
        fh.write(text)
    exe = engines.vacask_bin()
    if not os.access(exe, os.X_OK):
        return [error(origin, "VACASK not found at %s (set VAMOS_VACASK or VAMOS_VACASK_HOME)" % exe)]
    env = engines.env_for(ENGINE, nvc_libdir if nvc_libdir is not None else T.nvc_libdir(), dict(os.environ))
    try:
        p = subprocess.run([exe, SMOKE_DECK], cwd=dir, env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, errors="replace",
                           timeout=timeout)
        out, rc = p.stdout, p.returncode
    except subprocess.TimeoutExpired:
        return [error(origin, "VACASK smoke check timed out after %d s" % timeout)]
    except OSError as exc:
        return [error(origin, "cannot run VACASK: %s" % exc)]
    with open(os.path.join(dir, SMOKE_LOG), "w", encoding="utf-8", errors="replace") as fh:
        fh.write(out)
    if rc == 0:                     # abort always: a failed analysis exits 1
        return []
    return [error(origin, "VACASK smoke check failed (exit %d): %s" % (rc, _explain(out, deck)))]


_BANNER = re.compile(r"^(?:This is vacask|\(c\)|https?://|\s*OpenMP|Available CPUs|CPUs used|OpenBLAS"
                     r"|Simulating:|Elaboration|\s*$)")
_WHERE = re.compile(r"^(\d+):(\d+) \(0x[0-9a-fA-F]+\) in (.*)$")


def _explain(out: str, deck: "_Deck") -> str:
    lines = [l.rstrip() for l in out.splitlines() if not _BANNER.match(l)]
    for i, l in enumerate(lines):
        m = re.match(r"Master '([^']+)' not found", l.strip())
        if not m:
            continue
        for l2 in lines[i + 1:i + 5]:
            w = _WHERE.match(l2.strip())
            if w and int(w.group(1)) in deck.nobin_lines:
                return deck.nobin_lines[int(w.group(1))]
    seen: List[str] = []
    for l in lines:
        if l.strip() and l not in seen:
            seen.append(l)
    return "\n".join(seen[:20])
