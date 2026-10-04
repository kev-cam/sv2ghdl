"""VACASK deck emitter (docs/VAMOS_AMS_DESIGN.md §4.4, with §4.3.5-§4.3.8 and §4.7).

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
render(nl, analysis_name="vamos_tran", osdi=(), notes=None, op=False) -> str
    The deck text emit() writes; op=True prints an operating point analysis in
    place of the transient (the smoke deck).
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
    digits, never a reserved word; anything else in single quotes).

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
"""

from __future__ import annotations

import math
import os
import re
import subprocess
from typing import Dict, List, Optional, Sequence, Tuple

from vamos.netlist import expr as X
from vamos.netlist import tables as T
from vamos.netlist.expr_ast import Binary, Expr, Name, Num, Str, Ternary
from vamos.netlist.ir import Instance, Model, Netlist, Param, Source, Subckt
from vamos.netlist.numbers import fmt
from vamos.notes import ERROR, Note, NoteError, error

ENGINE = "vacask"
SMOKE_DECK = "smoke.sim"
SMOKE_LOG = "smoke.log"
SMOKE_ANALYSIS = "vamos_smoke"
NOT_GIVEN = -1.2345678901234568e-300    # default of an overridable dependent subckt parameter

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


def emit(nl: Netlist, path: str, analysis_name: str = "vamos_tran", osdi: Sequence[str] = (),
         notes: Optional[List[Note]] = None) -> None:
    """Write the VACASK deck for nl to path (see the module docstring)."""
    text = render(nl, analysis_name, osdi, notes)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def render(nl: Netlist, analysis_name: str = "vamos_tran", osdi: Sequence[str] = (),
           notes: Optional[List[Note]] = None, op: bool = False) -> str:
    """The VACASK deck text for nl (see the module docstring)."""
    return _Deck(nl, analysis_name, osdi, op).render(notes)


# -- the emitter ----------------------------------------------------------------------

class _Deck:
    def __init__(self, nl: Netlist, analysis: str, osdi: Sequence[str], op: bool):
        if not re.match(r"[A-Za-z_][A-Za-z0-9_]*$", analysis):
            raise ValueError("bad analysis name %r" % (analysis,))
        self.nl, self.analysis, self.osdi, self.op = nl, analysis, list(osdi), op
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
        for items in [nl.body] + [s.body for s in T.all_subckts(nl)]:
            for it in items:
                if isinstance(it, Instance) and it.kind == "x" and it.master:
                    self.overridden.setdefault(it.master, set()).update(k for k in it.params if k != "m")
        self._names()

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
        control = self.control()
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
        glob = [quote(g) for g in self.nl.globals if g != "0"]
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
            except _Skip:
                pass
            except (T.TableError, X.PrintError) as exc:
                self.err(getattr(it, "origin", ""), str(exc))

    def subckt(self, s: Subckt, parent: T.Scope, indent: str) -> None:
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
        self.body.append("%ssubckt %s (%s)" % (indent, quote(s.name), " ".join(quote(p) for p in s.ports)))
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

    def subckt_params(self, s: Subckt, plist: Sequence[Param], indent: str) -> None:
        """A subckt's parameters, overridable as in HSPICE.

        VACASK lets an instance override only a primary parameter, one whose
        default is a constant when the deck is parsed; a default that refers to a
        top-level parameter or to another parameter makes it dependent, and an
        override of it is an error.  Top-level values are constants here, so they
        are folded into defaults (wp=2.5*wn becomes primary); a default that still
        depends on another subckt parameter and that some X line overrides gets
        the constant NOT_GIVEN as its default plus an effective-value parameter
        p__v=(p==NOT_GIVEN) ? (default) : p, and every expression in the body
        reads p__v.
        """
        consts = {k: v for k, v in self.values.items() if k not in self.shadow}
        local = {p.name for p in plist}
        over = self.overridden.get(s.name, set())
        taken = set(local) | {"$mfactor"}
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

    def model(self, m: Model, indent: str) -> None:
        row = T.target(m)
        params, notes = T.model_params(m, ENGINE, binned=m.base is not None, options=self.nl.options)
        self.add(notes)
        self.use_load(row.vacask_osdi)
        strings = T.STRING_PARAMS.get(row.vacask_module, frozenset())
        out = []
        for k, v in params:
            out.append("%s=%s" % (k, self.string(v, m.origin, k) if k in strings
                                  else self.pv(v, m.origin, "model %s parameter %s" % (m.name, k))))
        self.body.append("%smodel %s %s%s" % (indent, quote(self.mname[m.name]), row.vacask_module,
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
        T.check_params(inst)
        fn(inst, scope, indent)

    def line(self, indent: str, inst: Instance, nodes: Sequence[str], master: str,
             params: Sequence[str]) -> None:
        self.body.append("%s%s (%s) %s%s" % (indent, quote(inst.name), " ".join(quote(n) for n in nodes),
                                            master, "".join(" " + p for p in params)))

    def mult(self, inst: Instance, scope: T.Scope) -> Optional[Expr]:
        return T.multiplier(inst.params.get("m"), scope.in_subckt, ENGINE)

    def mf(self, inst: Instance, scope: T.Scope) -> List[str]:
        """$mfactor=... for an element that takes the multiplier as a parameter."""
        m = self.mult(inst, scope)
        if m is None or T.MULT[ENGINE][inst.kind] != "param":
            return []
        return ["$mfactor=" + self.pv(m, inst.origin, "multiplier of " + inst.name)]

    def plist(self, inst: Instance, skip: Sequence[str] = ("m",), element: str = "",
              level: Optional[int] = None) -> List[str]:
        out = []
        for k, v in inst.params.items():
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
        if "ic" in inst.params:
            self.err(inst.origin, "%s: IC= on a %s is not supported (use .ic)" % (inst.name, T.ELEMENT_NAME[k]))
            return
        value, skip, mf = inst.value, ("m",), None
        if k == "l" and inst.name in scope.coupled:
            # a coupled inductor's multiplier and TCs are folded into its value (tables.coupled_inductance)
            card = self.card(inst, scope) if inst.master is not None else None
            folded = T.coupled_inductance(inst, self.mult(inst, scope), card, *T.run_temps(self.nl))
            if folded is not None:
                value, skip, mf = folded, T.COUPLED_FOLDED, []
        if mf is None:
            mf = self.mf(inst, scope)
        extra = [p for p in inst.params if p not in skip]
        simple, key = _SIMPLE[k]
        if inst.master is None and not extra:
            if value is None:
                self.err(inst.origin, "%s has no value" % inst.name)
                return
            params = ["%s=%s" % (key, self.pv(value, inst.origin, "value of " + inst.name))]
            self.line(indent, inst, inst.nodes, self.use_builtin(simple), params + mf)
            return
        if inst.master is not None:
            master = quote(self.mname[self.card(inst, scope).name])
        else:
            master = self.use_builtin(_SPICE_RCL[k])
        params = []
        if value is not None:
            params.append("%s=%s" % (key, self.pv(value, inst.origin, "value of " + inst.name)))
        self.line(indent, inst, inst.nodes, master, params + self.plist(inst, skip) + mf)

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
        self.body.append("%s%s (%s) %s=%s" % (indent, quote(inst.name),
                                              " ".join(quote(n) for n in inst.nodes), inst.expr_kind, text))

    def i_d(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        m = self.card(inst, scope)
        T.target(m)
        lv = T.level_of(m)                       # .option scale reaches a LEVEL 3 diode only
        params = []
        if inst.value is not None:
            area = T.scaled("d", "area", inst.value, self.s, lv)
            params.append("area=" + self.pv(area, inst.origin, "area of " + inst.name))
        self.line(indent, inst, inst.nodes, quote(self.mname[m.name]),
                  params + self.plist(inst, ("m", "area"), level=lv) + self.mf(inst, scope))

    def i_q(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        m = self.card(inst, scope)
        T.target(m)
        nodes = list(inst.nodes) + (["0"] if len(inst.nodes) == 3 else [])   # SPICE: substrate on ground
        params = []
        if inst.value is not None:
            params.append("area=" + self.pv(inst.value, inst.origin, "area of " + inst.name))
        self.line(indent, inst, nodes, quote(self.mname[m.name]),
                  params + self.plist(inst, ("m", "area")) + self.mf(inst, scope))

    def i_j(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        m = self.card(inst, scope)
        T.target(m)
        params = []
        if inst.value is not None:
            params.append("area=" + self.pv(inst.value, inst.origin, "area of " + inst.name))
        self.line(indent, inst, inst.nodes, quote(self.mname[m.name]),
                  params + self.plist(inst, ("m", "area")) + self.mf(inst, scope))

    def i_m(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        self.add(T.mos_scale_warnings(inst, self.nl.options))
        if inst.master and scope.model(inst.master) is None and scope.bins(inst.master):
            self.binned(inst, scope, indent, scope.bins(inst.master) or [])
            return
        m = self.card(inst, scope)
        T.target(m)
        self.add(T.mos_junction_warnings(inst, m, self.nl.options))
        self.line(indent, inst, inst.nodes, quote(self.mname[m.name]), self.plist(inst) + self.mf(inst, scope))

    def binned(self, inst: Instance, scope: T.Scope, indent: str, bins: List[Model]) -> None:
        """The @if/@elseif chain of copies, one per bin; @else binds a master that does not exist."""
        base = inst.master or ""
        l, w, nf = inst.params.get("l"), inst.params.get("w"), inst.params.get("nf")
        if l is None or w is None:
            self.err(inst.origin, "%s: binned model %s needs l= and w= on the instance" % (inst.name, base))
            return
        bounds = []
        for b in bins:
            T.target(b)
            bounds.append((b, T.bin_bounds(b, self.values)))
        geo = T.constant((l, w, nf if nf is not None else Num(1.0)), self.values, self.shadow)
        if geo is not None and T.select_bin(bounds, geo[0], geo[1], geo[2], self.s) is None:
            self.err(inst.origin, "no bin of %s for l=%.6g, w=%.6g%s (instance %s%s)"
                     % (base, geo[0] * self.s, geo[1] * self.s,
                        "" if nf is None else ", nf=%.6g" % geo[2], inst.name,
                        " in subckt " + scope.path() if scope.in_subckt else ""))
            return
        params = self.plist(inst) + self.mf(inst, scope)
        nodes = " ".join(quote(n) for n in inst.nodes)
        for i, (b, bd) in enumerate(bounds):
            guard = self.pv(T.bin_guard(bd, l, w, nf, self.s), inst.origin, "bin guard of " + inst.name)
            self.body.append("%s%s %s" % (indent, "@if" if i == 0 else "@elseif", guard))
            self.body.append("%s  %s (%s) %s%s" % (indent, quote(inst.name), nodes, quote(self.mname[b.name]),
                                                  "".join(" " + p for p in params)))
        self.body.append("%s@else" % indent)
        self.nobin[len(self.body)] = (
            "no bin of %s for l=%s, w=%s%s (scale %s; instance %s%s)"
            % (base, X.to_text(l), X.to_text(w), "" if nf is None else ", nf=" + X.to_text(nf),
               fmt(self.s), inst.name, " in subckt " + scope.path() if scope.in_subckt else ""))
        self.body.append("%s  %s (%s) %s%s" % (indent, quote(inst.name), nodes, quote(self.missing[base]),
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
        self.line(indent, inst, inst.nodes, quote(sub.name), params + self.mf(inst, scope))

    def i_y(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        module = inst.master or ""
        if not module:
            self.err(inst.origin, "Verilog-A instance %s has no module" % inst.name)
            return
        if module in T.VA_INSTANCE_MODULES:
            self.line(indent, inst, inst.nodes, self.use_builtin(module),
                      self.plist(inst, element="y") + self.mf(inst, scope))
            return
        # VA parameters are model parameters unless the module says otherwise:
        # one model card per instance, in the instance's scope (§4.4).
        card = _fresh("%s__va" % inst.name, self.taken)
        params = self.plist(inst, element="y")
        self.body.append("%smodel %s %s%s" % (indent, quote(card), quote(module), "".join(" " + p for p in params)))
        self.line(indent, inst, inst.nodes, quote(card), self.mf(inst, scope))

    # -- sources (§4.3.8)
    def source(self, inst: Instance) -> List[str]:
        src = inst.source or Source()
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
                self.err(o, "%s: code URI %s is not for VACASK (%s)" % (inst.name, src.code_uri,
                                                                        T.BRIDGE_INIT[ENGINE]))
                raise _Skip()
            return ['type="pwl"', 'file="%s"' % src.code_uri]
        out: List[str] = []
        pv = lambda e, what: self.pv(e, o, "%s %s" % (inst.name, what))   # noqa: E731
        w = src.wave
        if w is not None:
            try:
                f = T.wave(src, inst.name)
            except T.TableError as exc:
                self.err(o, str(exc))
                raise _Skip()
            if w == "pulse":
                out += ['type="pulse"', "val0=" + pv(f["v1"], "v1"), "val1=" + pv(f["v2"], "v2"),
                        "delay=" + pv(f["td"], "td"), "rise=" + pv(f["tr"], "tr"),
                        "fall=" + pv(f["tf"], "tf"), "width=" + pv(f["pw"], "pw")]
                if "per" in f:
                    out.append("period=" + pv(f["per"], "per"))
            elif w == "sin":
                out += ['type="sine"', "sinedc=" + pv(f["vo"], "vo"), "ampl=" + pv(f["va"], "va"),
                        "freq=" + pv(f["freq"], "freq"), "delay=" + pv(f["td"], "td"),
                        "theta=" + pv(f["theta"], "theta"), "tdphase=" + pv(f["phase"], "phase")]
            elif w == "exp":
                td1, td2 = f["td1"], f["td2"]
                rel: Expr = (Num(td2.value - td1.value) if isinstance(td1, Num) and isinstance(td2, Num)
                             else Binary("-", td2, td1))       # VACASK td2 is relative to delay
                out += ['type="exp"', "val0=" + pv(f["v1"], "v1"), "val1=" + pv(f["v2"], "v2"),
                        "delay=" + pv(td1, "td1"), "tau1=" + pv(f["tau1"], "tau1"),
                        "td2=" + pv(rel, "td2-td1"), "tau2=" + pv(f["tau2"], "tau2")]
            else:                                              # pwl
                out += ['type="pwl"', "wave=[%s]" % ", ".join(
                    "%s, %s" % (pv(t, "time"), pv(v, "value")) for t, v in src.points)]
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
        spice = "spice" in nl.options
        temp = nl.temp if nl.temp is not None else (27.0 if spice else 25.0)
        tnom = nl.tnom if nl.tnom is not None else (27.0 if spice else 25.0)
        opts = ["tran_lteimplicit=0"]
        try:
            opts += ["temp=" + fmt(self.num(temp, o, "temp")), "tnom=" + fmt(self.num(tnom, o, "tnom"))]
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


class _Skip(Exception):
    """An element whose error is already recorded; the walk moves on."""


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
    """The element a hierarchical name (x1.x2.v1) names, through the subckt definitions."""
    parts = target.split(".")
    items: Sequence[object] = nl.body
    scope = T.Scope(nl.body)
    for i, part in enumerate(parts):
        found = None
        for it in items:
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
