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
    isnoisy=no   noisy=0 (resistor and sp_resistor); a parameter (a word other than no/yes, which spectre.py
            keeps as a Str, or an expression) prints as noisy=<it>, which VACASK evaluates per instance
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
    targets a sweep or alter of m is one of $mfactor (refused for a coupled inductor, whose multiplier
            is folded into l, and inside a multiplied subckt instance, whose $mfactor the device's
            carries); a controlled source's gain, gm or rm is VACASK's gain; a Verilog-A instance's
            parameter is on its per-instance card, model="<inst>__va" (hierarchical inside a subckt:
            model="X2:Y1__va"); a mod= target on a Verilog-A card names the cards of the instances
            that read its value (the card's parameters are merged into its instances, §3.9)
    hooks   (phase 2c4, H1) VACASK re-evaluates every parameter its netlist binds to an expression at
            each elaboration after a change of a variable, temp or tnom (inside a subckt instance after a
            change of its parameters or m; before the first analysis when temp or tnom differ from 27 at
            the first elaboration, E151), which lost an alter of such a parameter, and it cannot sweep one
            ('is bound to an expression', E102).  A sweep or alter target bound to an expression that
            reads a name, temp or tnom (an instance's value or parameter, a source field, an m, an X-line
            binding, a card's parameter) prints as (vamos_af<k> ? vamos_av<k> : expr), both declared
            0 by a var statement before the first elaboration; the alter is var vamos_af<k>=1
            vamos_av<k>=<value> (a source's alter statement follows it), a sweep sweeps vamos_av<k> with
            vamos_af<k> set around it (VACASK restores the swept variable after a sweep).  Inside a
            subckt body the hook is per instance path: the body declares vamos_hf<n>=0 vamos_hv<n>=0 for
            the site, and the X lines down the altered path pass them on (vamos_hf<n>=vamos_af<k>, ...).
            A plan variable defined by an expression that reads another one, temp or tnom prints as
            p=(vamos_af<k> ? vamos_v_p : expr), so that it follows its inputs until an alter or a sweep
            sets it.  hook_key names what can be hooked (plan.build keeps G1's rule (b) and E102's
            refusal for the rest); in the control block a source field bound to a parameter is left out
            of an alter or a source switch, which keeps it (VACASK cannot read a netlist parameter there:
            `Variable or constant 'vv' not defined')
    statements (phase 2c6, K2) an alter, a source switch or a sweep of an instance names the instance the run
            takes (run_instance), so that an if and its else that both hold V1 give the taken one, whose netlist
            source is the base of a source statement (printed_items, which plan.build's rule (b) reads too).
            Phase 2c7 (L1): the taken branch is resolved in the state of the statement being printed, as
            plan.build's walk resolves it (run_map: plan.run_values and plan.run_paths with the plan variables'
            values the control block set so far, temp, tnom and the X-line values of the sub= alters), never
            _resolve's first match (a failure to resolve is an error); so are the current saves (current_directive,
            column) and the X lines a multiplier or a coupled inductor's fold reads (run_chain).  L3: the var
            statement that sets a hooked sweep's flag sets its value to the sweep's first point too, so VACASK's
            setup before the first point never sees the declared 0 (an exp's td2, a sine's freq: exit 1 before)
    phase 2c8  a dev= sweep of a rise or a fall prints a point of 0 as Spectre's default edge, transres (edge_grid:
            VACASK read a swept fall of 0 as none and stopped at a rise of 0); a sine of constant freq 0 reaches the
            deck in tables.constant_sine's form (signals.resolve's render copy, plan.build's states and alters: its
            given dc under type="dc", the sine itself at freq=1e-30 for a tran, its value the constant), and
            hook_key reads the IR's in that form, so that plan.build and the deck agree on its places; a sweep of
            its freq prints a point of 0 as that 1e-30 (edge_grid; VACASK stops at a sine of freq 0)
    phase 2c9  a swept point's substitute (a rise or fall of 0, the constant sine's freq of 0) is plan.swept_points',
            read in the source state of the statement being printed (state_source: the last alter's or switch's
            Source, track_source, else the instance line's), as plan.build reads it: 2c8 read the instance line, so a
            sine an alter made the constant sine printed its freq of 0 raw and VACASK stopped (N1); a tran's sweep of
            the dc of a sine whose sinedc defaults to dc sweeps its sinedc, which the tran reads (type="sine"; N3)
"""

from __future__ import annotations

import math
import os
import re
import subprocess
from dataclasses import replace
from typing import TYPE_CHECKING, Collection, Dict, List, Optional, Sequence, Set, Tuple

from vamos.netlist import expr as X
from vamos.netlist import tables as T
from vamos.netlist.expr_ast import Binary, Call, Expr, Name, Num, Str, Ternary, Unary
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
# The Spectre name of a controlled source's gain (ref5 pp.681, 679, 289, 286; the IR holds it in
# Instance.value): VACASK's vcvs, vccs, ccvs and cccs all call it gain (dev-builtin-*.md).
_GAIN_PARAM = {"e": "gain", "g": "gm", "h": "rm", "f": "gain"}
# The global options of a plan's options action, in the order the control block prints them
# (Spectre's keys; tables.SPECTRE_OPTIONS["vacask"] spells VACASK's).
_OPTION_ORDER = ("temp", "tnom", "reltol", "vabstol", "iabstol", "chargeabstol", "gmin")
_HEAD_OPTIONS = ('rawfile="binary"', "strictoutput=0", "strictsave=2", "tran_lteimplicit=0")

# Phase 2c4 (H1): the switchable hooks of a sweep or alter target bound to an expression (the module docstring)
HOOK_FLAG = "vamos_af"          # vamos_af<k>: 1 while an alter or a sweep sets the hooked parameter (a var)
HOOK_VALUE = "vamos_av"         # vamos_av<k>: the value it sets (a var)
BODY_FLAG = "vamos_hf"          # vamos_hf<n>, vamos_hv<n>: a subckt's parameters carrying a hook down an instance path
BODY_VALUE = "vamos_hv"
VALUE_KEY = "@value"            # the site key of an instance's value parameter (Instance.value)
# The Spectre name of the value parameter each kind's Instance.value holds (plan.VALUE_PARAMS by master)
_VALUE_PARAM = {"r": "r", "c": "c", "l": "l", "e": "gain", "g": "gm", "h": "rm", "f": "gain", "k": "coupling",
                "d": "area", "q": "area", "j": "area"}


def reads_something(e: object) -> bool:
    """H1: an expression that reads a parameter, temp or tnom, the kind of binding VACASK re-evaluates at its
    elaborations (phase 2c3, G1 [E148]); a number, a word or a vector is not one."""
    if e is None or isinstance(e, (Num, Str, list, tuple, str, bool, int, float)):
        return False
    try:
        return bool(X.names(e)) or any(isinstance(n, Name) and n.name in ("temper", "$tnom")    # type: ignore[arg-type]
                                       for n in X.walk(e))                                    # type: ignore[arg-type]
    except Exception:  # noqa: BLE001  (not an expression)
        return False


def source_locations(src: Source, field: str) -> List[Tuple]:
    """H1: the places of a Source that hold exactly the expression its Spectre field was written as (src.spectre[field],
    by identity: Source.dc, an ac part, an args field, a pwl point), [] when there is none or when the parser derived
    another place from it (a pulse's rise becomes (rise == 0 ? transres : rise), an exp's td2 delay + td2): such a
    field has no single place a hook could take."""
    obj = src.spectre.get(field)
    if obj is None or isinstance(obj, (list, Str, str)):
        return []
    places: List[Tuple[Tuple, object]] = [(("dc",), src.dc)]
    if src.ac is not None:
        places += [(("ac", 0), src.ac[0]), (("ac", 1), src.ac[1])]
    places += [(("args", k), v) for k, v in src.args.items()]
    places += [(("points", i, j), p[j]) for i, p in enumerate(src.points) for j in (0, 1)]
    found: List[Tuple] = []
    for where, e in places:
        if e is None:
            continue
        if e is obj:
            found.append(where)
            continue
        try:
            if any(n is obj for n in X.walk(e)):          # type: ignore[arg-type]
                return []                                  # derived: the hook would miss that place
        except Exception:  # noqa: BLE001  (not an expression)
            continue
    return found


def hook_key(inst: Instance, param: str) -> Optional[str]:
    """H1: the site key under which the VACASK deck prints the target parameter `param` of `inst` (the spelling a plan
    target carries) through a switchable hook: VALUE_KEY for the value parameter Instance.value holds, "m" for the
    multiplier ($mfactor), a source field's or an X line's name, a Verilog-A parameter in lower case (openvaf-r's
    names), else the parameter.  None where VACASK cannot hook it: isnoisy (the noisy switch, §4.5 item 7), the m of a
    kind whose multiplier is no device parameter (tables.MULT), a behavioral source, a source field without a single
    place (source_locations).  plan.build keeps G1's rule (b) and E102's refusal for those only (§5.3)."""
    k = inst.kind
    if k == "b" or param == "isnoisy":
        return None
    if param == "m":
        return "m" if T.MULT[ENGINE].get(k) == "param" else None
    if k == "x":
        return param
    if k in ("v", "i"):
        src = inst.source
        if src is None or src.code_uri is not None:
            return None
        # phase 2c8 (M2): a sine of constant freq 0 as the render copy prints it (tables.constant_sine), so that plan.build
        # (on the IR) and the deck (on signals.resolve's copy) read the same places
        if not source_locations(T.constant_sine(src) or src, param):
            return None
        return param
    if _VALUE_PARAM.get(k) == param and inst.value is not None:
        return VALUE_KEY
    if k == "y":
        return param.lower()
    return param


def _target_expr(inst: Instance, key: str, param: str) -> object:
    """H1: the IR expression a hooked target is bound to (plan._bound_expr's reading): Instance.value, the m, a source
    field as written, a Verilog-A parameter whatever its case, a parameter under its name or the name a master's rename
    gives it."""
    if key == VALUE_KEY:
        return inst.value
    if key == "m" or inst.kind == "x":
        return inst.params.get(key)
    if inst.kind in ("v", "i"):
        return inst.source.spectre.get(param) if inst.source is not None else None
    if inst.kind == "y":
        return next((v for k, v in inst.params.items() if k.lower() == key), None)
    if param in inst.params:
        return inst.params[param]
    row = T.SPECTRE_MASTERS.get(inst.prim or "")
    for r in (row.instance if row is not None else ()):
        if r.action == "rename" and r.value in (param, "instance_" + param) and r.name in inst.params:
            return inst.params[r.name]
    return None


def path_resolutions(nl: Netlist, path: str, cache: Optional[Dict[str, List]] = None
                     ) -> List[Tuple[List[Tuple[Instance, Subckt]], Instance]]:
    """Every way an IR path resolves through the definitions: (the X instances on it with their subckts, outermost
    first, the instance it names), each body's instances of the name in every Cond branch counted (an if and its else
    may both hold R1; the run takes one of them, which plan.build's walk chose and _resolve's first match need not
    be); [] when it does not resolve.  cache (phase 2c5): path -> resolutions, for the callers of one netlist."""
    if cache is not None and path in cache:
        return cache[path]
    parts = path.split(".")
    out: List[Tuple[List[Tuple[Instance, Subckt]], Instance]] = []
    if cache is not None:
        cache[path] = out

    def walk(items: Sequence[object], scope: T.Scope, k: int, chain: List[Tuple[Instance, Subckt]]) -> None:
        for it in flat_items(items):
            if not isinstance(it, Instance) or it.name != parts[k]:
                continue
            if k == len(parts) - 1:
                out.append((list(chain), it))
                continue
            sub = scope.find_subckt(it.master or "") if it.kind == "x" else None
            if sub is not None:
                walk(sub.body, T.Scope(sub.body, scope, sub), k + 1, chain + [(it, sub)])
    walk(nl.body, T.Scope(nl.body), 0, [])
    return out


def _is_va_card(nl: Netlist, m: Model) -> bool:
    return bool(m.prim) and m.prim.lower() in {k.lower() for k in nl.va_modules}     # type: ignore[union-attr]


def _target_cards(nl: Netlist, name: str) -> List[Model]:
    """The cards a mod= target names: the card, or every entry of a model group (by base name)."""
    return [m for m in _all_models(nl) if m.name == name or m.base == name]


def _card_expr(nl: Netlist, m: Model, param: str) -> object:
    """H1: the card's value of a target parameter (the spelling a plan target carries): under its name or the name a
    master's rename gives it; a Verilog-A card's whatever its case; None when the card leaves it out."""
    if _is_va_card(nl, m):
        return next((v for k, v in m.params.items() if k.lower() == param.lower()), None)
    if param in m.params:
        return m.params[param]
    row = T.SPECTRE_MASTERS.get(m.prim or "")
    return next((m.params[r.name] for r in (row.params if row is not None else ())
                 if r.action == "rename" and r.value == param and r.name in m.params), None)


def hookable(nl: Netlist, target: Tuple[str, str, str], cache: Optional[Dict[str, List]] = None) -> bool:
    """H1: whether the VACASK deck can print a sweep or alter target through a switchable hook, should the netlist bind
    it to an expression: an instance's when hook_key accepts it on every resolution of its path (path_resolutions); a
    card's when every card it names (each entry of a model group) gives the parameter, the deck then printing it through
    the hook on each of them, and a Verilog-A card's (on the per-instance cards of the instances that read its value)
    when its cards all bind it or none does.  plan.build keeps G1's rule (b), E151 and E102's refusal for the others only
    (plan.vacask_hooked).  cache: path_resolutions'."""
    if target[0] == "model":
        cards = _target_cards(nl, target[1])
        exprs = [_card_expr(nl, m, target[2]) for m in cards]
        if not cards or any(e is None for e in exprs):
            return False
        va = {reads_something(e) for m, e in zip(cards, exprs) if _is_va_card(nl, m)}
        return len(va) <= 1
    if target[0] != "instance":
        return False
    res = path_resolutions(nl, target[1], cache)
    return bool(res) and all(hook_key(inst, target[2]) is not None for _chain, inst in res)


def prints_hook(nl: Netlist, target: Tuple[str, str, str], dependents: Collection[str] = (),
                cache: Optional[Dict[str, List]] = None) -> bool:
    """Whether a plan's VACASK deck prints this sweep or alter target through a switchable hook (_Deck._plan_hooks's
    rule), so that its alter is the var statement `var vamos_af<k>=1 vamos_av<k>=<value>' and its sweep sweeps
    vamos_av<k> with the flag set around the nest: an instance's or a card's that hookable accepts when one of its
    resolutions (one of its cards) binds it to an expression that reads a name, temp or tnom, and (phase 2c5, R3) the
    m of an instance inside a subckt body, whose printed $mfactor reads the holder's ($mfactor=$mfactor), unless an X
    line on its path gives m (mfactor_param's refusal stands there); a plan variable's whose expression reads one of
    dependents (plan.dependents), temp or tnom.  plan.build counts those var statements as VACASK's variable changes
    (phase 2c5, R1).  cache: path_resolutions'."""
    kind, name, param = target
    if kind == "instance":
        res = [(chain, inst, hook_key(inst, param)) for chain, inst in path_resolutions(nl, name, cache)]
        if not res or any(key is None for _chain, _inst, key in res):
            return False                                   # hookable's rule
        for chain, inst, key in res:
            if reads_something(_target_expr(inst, key, param)):    # type: ignore[arg-type]
                return True
            if key == "m" and chain and not any("m" in x.params for x, _sub in chain):
                return True
        return False
    if kind == "model":
        return hookable(nl, target) and any(reads_something(_card_expr(nl, m, param)) for m in _target_cards(nl, name))
    if kind == "variable":
        p = next((p for p in nl.body if isinstance(p, Param) and p.name == name), None)
        if p is None:
            return False
        try:
            return bool(X.names(p.expr) & set(dependents)) or any(
                isinstance(n, Name) and n.name in ("temper", "$tnom") for n in X.walk(p.expr))   # type: ignore[arg-type]
        except Exception:  # noqa: BLE001  (not an expression)
            return False
    return False


# Phase 2c5 (R4): the control block's statements about a source, an alter of one of its fields or a switch of its type,
# print every field of its state; VACASK's control block cannot read a netlist parameter (`Variable or constant 'rt'
# not defined', exit 1), so a field that reads a name, temp or tnom is left out of them, the instance's binding keeping
# it, which is right where nothing the alters set reaches the field except through a hook the instance line prints.

def _source_items(src: Source, f: Dict[str, Expr]) -> List[Tuple[str, object, str]]:
    """The fields _Deck.source_fields prints after a waveform's type, in its order: (VACASK key, expression, what its
    messages call it); a pwl's points as ("wave", [(time, value), ...]) and an exp's td2 relative to its delay."""
    w = src.wave
    if w == "pulse":
        items: List[Tuple[str, object, str]] = [
            (key, f[k], what) for key, k, what in (("val0", "v1", "v1"), ("val1", "v2", "v2"), ("delay", "td", "td"),
                                                   ("rise", "tr", "tr"), ("fall", "tf", "tf"), ("width", "pw", "pw"))]
        if "per" in f:
            items.append(("period", f["per"], "per"))
        return items
    if w == "sin":
        return [(key, f[k], what) for key, k, what in (("sinedc", "vo", "vo"), ("ampl", "va", "va"),
                                                       ("freq", "freq", "freq"), ("delay", "td", "td"),
                                                       ("theta", "theta", "theta"), ("tdphase", "phase", "phase"))]
    if w == "exp":
        td1, td2 = f["td1"], f["td2"]
        rel: Expr = (Num(td2.value - td1.value) if isinstance(td1, Num) and isinstance(td2, Num)
                     else Binary("-", td2, td1))       # VACASK td2 is relative to delay
        return [("val0", f["v1"], "v1"), ("val1", f["v2"], "v2"), ("delay", td1, "td1"), ("tau1", f["tau1"], "tau1"),
                ("td2", rel, "td2-td1"), ("tau2", f["tau2"], "tau2")]
    items = [("wave", list(src.points), "")]           # pwl
    if "td" in f:
        items.append(("delay", f["td"], "td"))
    return items


def _item_exprs(key: str, e: object) -> List[object]:
    return [x for pt in e for x in pt] if key == "wave" else [e]          # type: ignore[union-attr]


def _holds(e: object, obj: object) -> bool:
    try:
        return any(n is obj for n in X.walk(e))                          # type: ignore[arg-type]
    except Exception:  # noqa: BLE001  (not an expression)
        return False


def _printed_items(src: Optional[Source]) -> Optional[Dict[str, object]]:
    """The fields the instance line or a control-block statement prints for src after its type, key -> expression (a
    pwl's points as a list of (time, value) pairs); None when its waveform does not resolve (the render reports it)."""
    items: Dict[str, object] = {}
    if src is None or src.code_uri is not None:
        return items
    if src.wave is not None:
        try:
            items.update((k, e) for k, e, _what in _source_items(src, T.wave(src, "")))
        except T.TableError:
            return None
    if src.dc is not None:
        items["dc"] = src.dc
    if src.ac is not None:
        items["mag"], items["phase"] = src.ac[0], src.ac[1]
    return items


def printed_items(src: Optional[Source]) -> Optional[Dict[str, object]]:
    """Phase 2c6 (K1): the fields the instance line or a control-block statement prints for src after its type (VACASK
    key -> expression; a pwl's points as a list of (time, value) pairs), None when its waveform does not resolve:
    plan.build's rule (b) compares a statement's constants with the instance line's bindings field by field."""
    return _printed_items(src)


def holds(e: object, obj: object) -> bool:
    """Phase 2c6: whether the expression e holds the node obj, by identity."""
    return _holds(e, obj)


def swap(e: object, mapping: Dict[int, object]) -> object:
    """Phase 2c6: e with every node that is, by identity, a key of mapping replaced by its value (_swap)."""
    return _swap(e, mapping)


def is_va_card(nl: Netlist, m: Model) -> bool:
    """Phase 2c6: whether a card's master is a Verilog-A module of the netlist (its parameters compare in any case)."""
    return _is_va_card(nl, m)


def _swap(e: object, mapping: Dict[int, object]) -> object:
    """e with every node that is, by identity, a key of mapping replaced by its value (lists and pairs elementwise)."""
    if isinstance(e, list):
        return [_swap(x, mapping) for x in e]
    if isinstance(e, tuple):
        return tuple(_swap(x, mapping) for x in e)
    if id(e) in mapping:
        return mapping[id(e)]
    if isinstance(e, Binary):
        return Binary(e.op, _swap(e.left, mapping), _swap(e.right, mapping))              # type: ignore[arg-type]
    if isinstance(e, Unary):
        return Unary(e.op, _swap(e.arg, mapping))                                         # type: ignore[arg-type]
    if isinstance(e, Ternary):
        return Ternary(_swap(e.cond, mapping), _swap(e.a, mapping), _swap(e.b, mapping))   # type: ignore[arg-type]
    if isinstance(e, Call):
        return Call(e.func, tuple(_swap(a, mapping) for a in e.args))                     # type: ignore[misc]
    return e


def control_source_problems(src: Source, base: Optional[Source], hooked: Collection[str] = ()) -> List[Tuple[str, object]]:
    """R4 (phase 2c5): the fields a control-block statement about src (an alter of one of its fields, a switch of its
    type) prints that read a name, temp or tnom and that it cannot leave out either.  A field left out keeps the
    instance's binding of it, the field as the instance line prints it from base (the netlist's source), so leaving it
    out is right when that binding gives what the statement would print: the same expression, or the same once each
    altered field it derives from (a field src.spectre holds otherwise than base wrote it) is replaced by its altered
    value, which the instance line reads through that field's hook (hooked: the Spectre names of the fields it prints
    through one).  So a pulse's (rt == 0 ? transres : rt) and an exp's delay=td that the alters leave alone, or the
    relative td2 ((vamos_af1 ? vamos_av1 : t2) - td) after an alter of the hooked td2, are left out; the relative td2
    after an alter of an exp's constant td2 beside td1=td (no hook), or a field the instance line does not print (an
    alter of the type of a dc source whose pulse fields read vv; 2c4 left val1=vv out, and VACASK took its default 1),
    would be wrong left out: [(VACASK key, expression)]."""
    now = _printed_items(src)
    if not now:
        return []
    was = _printed_items(base) or {}
    written = {k: (list(v) if isinstance(v, list) else [v]) for k, v in src.spectre.items()}
    altered = {k for k, v in src.spectre.items() if base is None or k not in base.spectre or v != base.spectre[k]}
    out: List[Tuple[str, object]] = []
    for key, e in now.items():
        exprs = _item_exprs(key, e)
        if not any(reads_something(x) for x in exprs):
            continue
        deps = {k for k, objs in written.items() if any(_holds(x, o) for x in exprs for o in objs)}
        moved = deps & altered
        ok = (base is not None and key in was and bool(deps)
              and all(k in hooked and k in base.spectre for k in moved))
        if ok and base is not None:
            swap: Dict[int, object] = {}
            for k in moved:                                # the hooked fields' values, as the instance line reads them
                old, new = base.spectre[k], src.spectre[k]
                olds = old if isinstance(old, list) else [old]
                news = new if isinstance(new, list) else [new]
                swap.update((id(o), n) for o, n in zip(olds, news))
            ok = _swap(was[key], swap) == e
        if not ok:
            out.append((key, e))
    return out


def control_source_message(what: str, statement: str, path: str, key: str, e: object) -> str:
    """The refusal of a control-block statement that can neither print nor leave out a field (control_source_problems):
    what names the alter or analysis, statement the control block's statement ("alter", "switch")."""
    exprs = _item_exprs(key, e)
    text = ("[%s]" % ", ".join(X.to_text(x) for x in exprs)) if key == "wave" else X.to_text(e)   # type: ignore[arg-type]
    reads: Set[str] = set()
    for x in exprs:
        try:
            reads |= set(X.names(x))                                     # type: ignore[arg-type]
            reads |= {"temp" if n.name == "temper" else "tnom" for n in X.walk(x)          # type: ignore[arg-type]
                      if isinstance(n, Name) and n.name in ("temper", "$tnom")}
        except Exception:  # noqa: BLE001  (not an expression)
            continue
    return ("%s: on VACASK the control block's %s of %s would print %s=%s, which reads %s: a VACASK control-block "
            "statement cannot read a netlist parameter, temp or tnom ('Variable or constant ... not defined'), and left "
            "out the field would keep the instance's own value (its binding on the instance line, or VACASK's default "
            "where that line prints none), not this one: not supported on VACASK in v1 (Xyce runs it)"
            % (what, statement, path, key, text, ", ".join(sorted(reads))))


class _Hook:
    """H1: one switchable hook: its flag and value variables (the value of a plan variable's hook is the variable
    vamos_v_<p> itself), the plan target it serves and the sites it is printed at (instance hooks)."""
    __slots__ = ("flag", "value", "target", "var", "sites")

    def __init__(self, flag: str, value: str, target: Tuple[str, str, str], var: bool = False):
        self.flag, self.value, self.target, self.var = flag, value, target, var
        self.sites: List[Tuple] = []


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
        self.va_cards: Dict[int, str] = {}        # id(Verilog-A instance) -> its per-instance card (i_y)
        self.variables: Dict[str, float] = dict(plan.variables) if plan is not None else {}
        # L1 (phase 2c7): the state the control block's statements run in (the plan variables' values, temp, tnom, the
        # X-line values sub= alters set), as plan.build's walk evaluates it, and the instance paths it takes there
        glob = plan.options if plan is not None else {}
        self.run_vars: Dict[str, float] = dict(self.variables)
        self.run_temp = _float_or(glob.get("temp"), 27.0)
        self.run_tnom = _float_or(glob.get("tnom"), 27.0)
        self.run_x: Dict[Tuple[str, str], float] = {}
        self.run_maps: Dict[Tuple, Tuple[Dict[str, Instance], Dict[str, T.PathEnv]]] = {}
        self.run_sites: Optional[object] = None
        self.transres: Optional[float] = None     # phase 2c8: Spectre's default pulse edge (plan.edge_default), once
        # phase 2c9 (N1): source path -> the Source the plan's alter and source actions so far set (plan.build's source
        # state, in the statement being printed); a path no action set is in its instance line's state
        self.src_now: Dict[str, Source] = {}
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
        self._plan_hooks()

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

    # -- the switchable hooks (phase 2c4, H1; the module docstring) ----------------------------------------------------
    def _plan_hooks(self) -> None:
        """The hooks of the plan's sweep and alter targets bound to an expression that reads a name, temp or tnom, in
        the order the plan first names them: an instance's (hook_key; inside a subckt body a site whose hook the X
        lines of the target's path pass down), a card's (on its model line), a plan variable's that reads another one
        (or temp, tnom).  Nothing without a plan or on a HSPICE netlist."""
        self.hooks: Dict[Tuple[str, str, str], _Hook] = {}       # plan target -> its hook
        self.hook_list: List[_Hook] = []
        self.sites: Dict[Tuple[int, str, str], Tuple[str, str, str]] = {}   # (id(body) or 0, instance, key) -> (flag,
        #                                                                     value, kind) as that body reads them
        self.site_params: Dict[int, List[Tuple[str, str]]] = {}              # id(subckt) -> its hook parameter pairs
        self.body_pairs: Dict[Tuple[int, str, str], Tuple[str, str]] = {}    # (id(subckt), path suffix, key) -> pair
        self.xbind: Dict[Tuple[int, str, int], Dict[str, Tuple[str, str, str, str]]] = {}   # (id(body) or 0, X name,
        #                                           id(its subckt)) -> child flag -> (child flag, value, flag, value)
        self.card_hooks: Dict[Tuple[int, str], Tuple[str, str]] = {}         # (id(card), printed key) -> (flag, value)
        self.va_hooks: Dict[int, Tuple[str, str]] = {}                       # id(Verilog-A card's Expr) -> (flag, value)
        self.var_hooks: Dict[str, str] = {}                                  # plan variable -> its flag
        self.hook_flags: Set[str] = set()                                    # every flag name a hook's Ternary tests
        self.flag_on: Dict[str, bool] = {}                                   # the control block's flags so far
        self.printed_sites: Set[Tuple] = set()
        if self.plan is None or not self.spectre:
            return
        self.param_names: Set[str] = {p.name for p in self.nl.body if isinstance(p, Param)}
        for s in T.all_subckts(self.nl):
            self.param_names |= {p.name for p in _plist(s)}
        self.hook_k = self.body_n = 0
        targets: List[Tuple[str, str, str]] = []
        for act in self.plan.actions:
            if act.op == "alter":
                t = act.args.get("target")
                if isinstance(t, tuple) and len(t) == 3:
                    targets.append(t)
            elif act.op == "analysis" and act.step is not None:
                st = act.step
                targets += [lv.target for lv in list(st.context) + ([st.own] if st.own is not None else [])]
        for t in dict.fromkeys(targets):
            if t[0] == "instance":
                self._hook_instance(t)
            elif t[0] == "model":
                self._hook_model(t)
            elif t[0] == "variable":
                self._hook_variable(t)

    def _new_hook(self, target: Tuple[str, str, str], var: bool = False) -> _Hook:
        while True:
            self.hook_k += 1
            flag, value = HOOK_FLAG + str(self.hook_k), HOOK_VALUE + str(self.hook_k)
            if flag not in self.param_names and value not in self.param_names:
                break
        h = _Hook(flag, VAR_PREFIX + target[1] if var else value, target, var)
        self.hooks[target] = h
        self.hook_list.append(h)
        self.hook_flags.add(flag)
        return h

    def _body_pair(self, sub: Subckt, suffix: str, key: str) -> Tuple[str, str]:
        """The hook parameters subckt sub declares for the site at path suffix (relative to its body) and key."""
        mk = (id(sub), suffix, key)
        pair = self.body_pairs.get(mk)
        if pair is None:
            while True:
                self.body_n += 1
                pair = (BODY_FLAG + str(self.body_n), BODY_VALUE + str(self.body_n))
                if pair[0] not in self.param_names and pair[1] not in self.param_names:
                    break
            self.body_pairs[mk] = pair
            self.site_params.setdefault(id(sub), []).append(pair)
            self.hook_flags.add(pair[0])
        return pair

    def _hook_instance(self, t: Tuple[str, str, str]) -> None:
        """An instance target: hooked on every resolution of its path (an alter prints as var statements only, so a
        resolution left unhooked would miss it) when one of them binds it to an expression; none when one of them
        cannot be hooked (hookable)."""
        _kind, path, param = t
        if not prints_hook(self.nl, t):                     # phase 2c5 (R3): also the m of an instance in a body
            return
        res = [(chain, inst, hook_key(inst, param)) for chain, inst in path_resolutions(self.nl, path)]
        h = self._new_hook(t)
        names = path.split(".")
        for chain, inst, key in res:
            assert key is not None
            pair = self._body_pair(chain[-1][1], names[-1], key) if chain else (h.flag, h.value)
            site = (id(chain[-1][1]) if chain else 0, inst.name, key)
            self.sites[site] = (pair[0], pair[1], inst.kind)
            h.sites.append(site)
            for j in range(len(chain) - 1, -1, -1):         # up the path: each X line passes the site's pair on
                x, sub = chain[j]
                holder = chain[j - 1][1] if j > 0 else None
                up = self._body_pair(holder, ".".join(names[j:]), key) if holder is not None else (h.flag, h.value)
                where = (id(holder) if holder is not None else 0, x.name, id(sub))
                self.xbind.setdefault(where, {})[pair[0]] = (pair[0], pair[1], up[0], up[1])
                h.sites.append(where + (pair[0],))
                pair = up

    def _hook_model(self, t: Tuple[str, str, str]) -> None:
        """A mod= target: hooked on every card it names (each entry of a model group: the alter is a var statement,
        which an entry left constant would never see) when one of them binds the parameter to an expression; a
        Verilog-A card's on the per-instance cards of the instances that read its value (the same Expr objects, §3.9);
        none when one of the cards cannot take the hook (hookable)."""
        _kind, name, param = t
        cards = _target_cards(self.nl, name)
        exprs = [_card_expr(self.nl, m, param) for m in cards]
        if not prints_hook(self.nl, t):
            return
        h = self._new_hook(t)
        key = self.card_param(name, param)
        for m, e in zip(cards, exprs):
            if self.is_va_card(m):
                self.va_hooks[id(e)] = (h.flag, h.value)
            elif id(m) in self.used:                         # an unused card is not printed (T.reachable)
                self.card_hooks[(id(m), key)] = (h.flag, h.value)
                h.sites.append(("card", id(m), key))

    def _hook_variable(self, t: Tuple[str, str, str]) -> None:
        name = t[1]
        if name not in self.variables:
            return
        if prints_hook(self.nl, t, self.plan.dependents if self.plan is not None else ()):
            h = self._new_hook(t, var=True)
            self.var_hooks[name] = h.flag

    def hook_expr(self, inst: Instance, scope: T.Scope, key: str, e: Optional[Expr]) -> Optional[Expr]:
        """The expression of inst's parameter `key` as this body prints it: through its site's hook, else e."""
        if not self.sites or e is None:
            return e
        site = (id(scope.subckt) if scope.in_subckt and scope.subckt is not None else 0, inst.name, key)
        got = self.sites.get(site)
        if got is None or got[2] != inst.kind:
            return e
        self.printed_sites.add(site)
        return Ternary(Name(got[0]), Name(got[1]), e)

    def hooked_source(self, inst: Instance, scope: T.Scope, src: Source) -> Source:
        """src with every hooked field's places (source_locations) reading the field's hook (a copy)."""
        out: Optional[Source] = None
        for field in list(src.spectre):
            hooked = self.hook_expr(inst, scope, field, src.spectre[field])   # type: ignore[arg-type]
            if hooked is src.spectre[field]:
                continue
            if out is None:
                out = Source(dc=src.dc, ac=src.ac, wave=src.wave, args=dict(src.args), points=list(src.points),
                             code_uri=src.code_uri, spectre=dict(src.spectre))
            for where in source_locations(src, field):
                if where[0] == "dc":
                    out.dc = hooked                          # type: ignore[assignment]
                elif where[0] == "ac":
                    ac = list(out.ac or (Num(0.0), Num(0.0)))  # ref5 p.685: mag defaults to 0
                    ac[where[1]] = hooked                    # type: ignore[call-overload]
                    out.ac = (ac[0], ac[1])                  # type: ignore[assignment]
                elif where[0] == "args":
                    out.args[where[1]] = hooked              # type: ignore[assignment]
                else:
                    pt = list(out.points[where[1]])
                    pt[where[2]] = hooked                    # type: ignore[call-overload]
                    out.points[where[1]] = (pt[0], pt[1])    # type: ignore[assignment]
        return out if out is not None else src

    def x_bindings(self, inst: Instance, scope: T.Scope, sub: Subckt) -> List[str]:
        """The hook parameters an X line passes into its body (the sites of the altered paths through it)."""
        if not self.xbind:
            return []
        where = (id(scope.subckt) if scope.in_subckt and scope.subckt is not None else 0, inst.name, id(sub))
        out: List[str] = []
        for cf, cv, f, v in self.xbind.get(where, {}).values():
            out += ["%s=%s" % (cf, f), "%s=%s" % (cv, v)]
            self.printed_sites.add(where + (cf,))
        return out

    def hook_params(self, s: Subckt, indent: str) -> None:
        """A body's hook parameters, 0 by default (no alter or sweep on this path)."""
        for f, v in self.site_params.get(id(s), []):
            self.body.append("%sparameters %s=0" % (indent, f))
            self.body.append("%sparameters %s=0" % (indent, v))

    def hook_declarations(self) -> List[str]:
        """The var statement declaring every hook's flag and value 0, before VACASK's first elaboration."""
        parts: List[str] = []
        for h in self.hook_list:
            parts.append("%s=0" % h.flag)
            if not h.var:
                parts.append("%s=0" % h.value)
            self.flag_on[h.flag] = False
        return ["  var " + " ".join(parts)] if parts else []

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
        if isinstance(e, Ternary) and isinstance(e.cond, Name) and e.cond.name in self.hook_flags:
            return t                                # a hook (H1): the printer's own parentheses
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
        for h in self.hook_list:                   # H1: a hooked site the walk did not print would lose the alter
            if any(site not in self.printed_sites for site in h.sites):
                self.err("", "%s %s of %s: the deck does not print the parameter through its switchable hook "
                         "(phase 2c4; internal)" % (h.target[0], h.target[2], h.target[1]))
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
                flag = self.var_hooks.get(p.name)
                if flag is not None:
                    # H1 (phase 2c4): defined by an expression that reads another variable (temp, tnom), it reads it
                    # until an alter or a sweep sets the variable (the hook's flag); rw=vamos_v_rw had stayed at its
                    # nominal under a sweep of rv (0.333, 0.333 where Xyce gives 0.333, 0.2)
                    try:
                        self.body.append("%sparameters %s=%s" % (indent, name, self.pv(
                            Ternary(Name(flag), Name(VAR_PREFIX + p.name), p.expr), p.origin, "parameter " + p.name)))
                    except _Skip:
                        pass
                    continue
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
            self.hook_params(s, inner)
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
            self.hook_params(s, "  ")
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
            h = self.card_hooks.get((id(m), k)) if self.card_hooks else None
            if h is not None and k not in strings:
                v = Ternary(Name(h[0]), Name(h[1]), v)            # H1: a mod= target bound to an expression
                self.printed_sites.add(("card", id(m), k))
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
        m = self.params_of(inst, scope).get("m")
        if m is None and scope.in_subckt and self.sites:
            # phase 2c5 (R3): inside a body the printed $mfactor=$mfactor reads the holder's multiplier, a binding VACASK
            # re-evaluates (an alter of the instance's $mfactor was lost after any change, 0.5 where 0.667, and a sweep
            # of it failed, `is bound to an expression'): a hooked site's own m is 1 where the netlist leaves it out
            site = self.sites.get((id(scope.subckt) if scope.subckt is not None else 0, inst.name, "m"))
            if site is not None and site[2] == inst.kind:
                m = Num(1.0)
        if m is not None:
            m = self.hook_expr(inst, scope, "m", m)         # H1: an m bound to an expression that a target sets
        return T.multiplier(m, scope.in_subckt, ENGINE)

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
            if self.va_hooks and id(v) in self.va_hooks:   # H1: a Verilog-A card's value a mod= target sets
                h = self.va_hooks[id(v)]
                self.printed_sites.add(("va", id(v)))
                v = Ternary(Name(h[0]), Name(h[1]), v)
            if scope is not None:
                v = self.hook_expr(inst, scope, k, v)        # type: ignore[assignment]
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
        value, skip, mf = inst.value, ("m",), None
        if "ic" in params_in:
            if not self.spectre:
                self.err(inst.origin, "%s: IC= on a %s is not supported (use .ic)" % (inst.name, T.ELEMENT_NAME[k]))
                return
            # Spectre netlists: plan.build refuses a device ic= unless every tran uses ic=node or ic=dc,
            # under which Spectre does not apply it (§0, §5.4), so it is not printed
            skip = ("m", "ic")
        tail: List[str] = []
        if self.spectre and k == "r":
            if isinstance(value, Num) and math.isinf(value.value) and value.value > 0:
                return                                  # §3.8: an infinite resistor is not printed (spectre.py notes it)
            if isinstance(value, Num) and value.value == 0.0:
                # r = 0: a 0 V source, a short whose flow is the resistor current (§4.5 item 10) [E22]
                self.add([note(inst.origin or inst.name, "%s: r=0 printed as a 0 V vamos_vsource (VACASK's "
                                                          "resistor masters cannot take r=0)" % inst.name)])
                self.line(indent, inst, inst.nodes, self.use_builtin("vamos_vsource"), ["dc=0.0"])
                return
            noisy = params_in.get("isnoisy")
            if noisy is not None:
                skip = skip + ("isnoisy",)
                switch = _noise_switch(noisy)
                if switch is True:
                    tail.append("noisy=0")              # §4.5 item 7: resistor and sp_resistor
                elif switch is not False:
                    # a parameter (isnoisy=nflag, a subckt's nz): VACASK evaluates noisy= per instance, a real
                    # 0.0 into the integer noisy turning the noise off (raw VACASK, R-emit-6)
                    tail.append("noisy=" + self.pv(switch, inst.origin, "isnoisy of " + inst.name))
        if value is not None:
            value = self.hook_expr(inst, scope, VALUE_KEY, value)   # H1: a value a sweep or alter target sets
        if k == "l" and inst.name in scope.coupled:
            # a coupled inductor's multiplier and TCs are folded into its value (tables.coupled_inductance)
            card = self.card(inst, scope) if inst.master is not None else None
            folded = T.coupled_inductance(inst if value is inst.value else replace(inst, value=value),
                                          self.mult(inst, scope), card, *T.run_temps(self.nl))
            if folded is not None:
                value, skip, mf = folded, T.COUPLED_FOLDED + (("ic",) if "ic" in skip else ()), []
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
        params = ["k=" + self.pv(self.hook_expr(inst, scope, VALUE_KEY, inst.value),   # type: ignore[arg-type]
                                 inst.origin, "coupling of " + inst.name),
                  'ind1="%s"' % inst.ctrl[0], 'ind2="%s"' % inst.ctrl[1]]
        self.line(indent, inst, [], self.use_builtin("vamos_mutual"), params)

    def i_v(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        self.line(indent, inst, inst.nodes, self.use_builtin("vamos_vsource"), self.source(inst, scope))

    def i_i(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        self.line(indent, inst, inst.nodes, self.use_builtin("vamos_isource"),
                  self.source(inst, scope) + self.mf(inst, scope))

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
        params = ["gain=" + self.pv(self.hook_expr(inst, scope, VALUE_KEY, inst.value),     # type: ignore[arg-type]
                                    inst.origin, "gain of " + inst.name)]
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
            area = T.scaled("d", "area", self.hook_expr(inst, scope, VALUE_KEY, inst.value),   # type: ignore[arg-type]
                            self.s, lv)
            params.append("area=" + self.pv(area, inst.origin, "area of " + inst.name))
        self.line(indent, inst, inst.nodes, self.q(self.mname[m.name]),
                  params + self.plist(inst, ("m", "area"), level=lv, scope=scope) + self.mf(inst, scope))

    def i_q(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        m = self.card(inst, scope)
        T.target(m)
        nodes = list(inst.nodes) + (["0"] if len(inst.nodes) == 3 else [])   # SPICE: substrate on ground
        params = []
        if inst.value is not None:
            params.append("area=" + self.pv(self.hook_expr(inst, scope, VALUE_KEY, inst.value),   # type: ignore[arg-type]
                                            inst.origin, "area of " + inst.name))
        self.line(indent, inst, nodes, self.q(self.mname[m.name]),
                  params + self.plist(inst, ("m", "area"), scope=scope) + self.mf(inst, scope))

    def i_j(self, inst: Instance, scope: T.Scope, indent: str) -> None:
        m = self.card(inst, scope)
        T.target(m)
        params = []
        if inst.value is not None:
            params.append("area=" + self.pv(self.hook_expr(inst, scope, VALUE_KEY, inst.value),   # type: ignore[arg-type]
                                            inst.origin, "area of " + inst.name))
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
                                     self.pv(self.hook_expr(inst, scope, k, v), inst.origin,   # type: ignore[arg-type]
                                             "%s parameter %s" % (inst.name, k))))
        lifted = self.lifted.get(id(sub))
        master = sub.name
        if lifted is not None:                     # the lifted definition takes the enclosing parameters
            master, extra = lifted
            for p in extra:
                if p not in inst.params:
                    params.append("%s=%s" % (X.param_ident(p, ENGINE),
                                             self.pv(Name(p), inst.origin, "%s parameter %s" % (inst.name, p))))
        params += self.x_bindings(inst, scope, sub)    # H1: the hooks of the altered paths down this X line
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
        self.va_cards[id(inst)] = card               # a sweep or alter of its parameters targets it (R-plan-4)
        params = self.plist(inst, element="y", scope=scope)
        if self.spectre:
            module = module.lower()                     # openvaf-r's OSDI module names are lower case (phase 2)
        self.body.append("%smodel %s %s%s" % (indent, self.q(card), self.q(module), "".join(" " + p for p in params)))
        self.line(indent, inst, inst.nodes, self.q(card), self.mf(inst, scope))

    # -- sources (§4.3.8; Spectre: VAMOS_SPECTRE_DESIGN.md §4.5 item 8)
    def source(self, inst: Instance, scope: Optional[T.Scope] = None) -> List[str]:
        src = inst.source or Source()
        if scope is not None and self.sites:
            src = self.hooked_source(inst, scope, src)       # H1: a field a sweep or alter target sets, hooked
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

    def source_fields(self, name: str, o: str, src: Source, state: Optional[str], ctl: bool = False,
                      base: Optional[Source] = None, hooked: Collection[str] = (), path: str = "") -> List[str]:
        """The parameters of a vsource/isource: the waveform fields, dc, mag/phase; state: the type
        printed on a Spectre source ("dc" or its waveform), None for the HSPICE path.  ctl (phase 2c4): a
        control-block alter or source switch, which leaves out a field the netlist binds to a parameter
        (a field as written, reading a name, temp or tnom): the instance keeps it, re-evaluated as VACASK
        re-evaluates its bindings, and the control block cannot read a netlist parameter (`Variable or
        constant 'vv' not defined', exit 1, before).  Phase 2c5 (R4): every field that reads a name, temp
        or tnom is left out, a derived one too (a pulse's (rt == 0 ? transres : rt), an exp's relative
        td2, a pwl's wave), which control_source_problems shows keeps the source right, given the
        instance's netlist source (base) and the fields it prints through a hook (hooked); a field it
        cannot leave out is an error (plan.build refuses such a statement first)."""
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
        left = False                                        # R4: a field the control-block statement leaves out

        def put(key: str, e: Expr, what: str) -> None:
            nonlocal left
            if ctl and reads_something(e):
                left = True
                return                                      # it reads a name: the instance keeps its binding
            out.append("%s=%s" % (key, pv(e, what)))
        w = src.wave
        if state is not None and (w is not None or state == "dc"):
            out.append('type="%s"' % ("sine" if state == "sin" else state))
        if w is not None:
            try:
                f = T.wave(src, name)
            except T.TableError as exc:
                self.err(o, str(exc))
                raise _Skip()
            if state is None:
                out.append('type="%s"' % ("sine" if w == "sin" else w))
            for key, e, what in _source_items(src, f):
                if key != "wave":
                    put(key, e, what)                       # type: ignore[arg-type]
                elif ctl and any(reads_something(x) for x in _item_exprs(key, e)):
                    left = True                             # a pwl point reads a name: the instance keeps the wave
                else:
                    out.append("wave=[%s]" % ", ".join("%s, %s" % (pv(t, "time"), pv(v, "value"))
                                                        for t, v in e))   # type: ignore[union-attr]
        if src.dc is not None:
            put("dc", src.dc, "dc")
        elif w is None:
            out.append("dc=0.0")
        if src.ac is not None:
            put("mag", src.ac[0], "ac magnitude")
            put("phase", src.ac[1], "ac phase")
        if left:
            bad = control_source_problems(src, base, hooked)
            if bad:                                         # plan.build refuses these first (R4); a safety net
                raise T.TableError(control_source_message("%s (internal)" % o, "statement", path or name, *bad[0]))
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
    def run_instance(self, ir_path: str) -> Optional[Instance]:
        """K2 (phase 2c6): the Instance an IR path names on the instance paths the run takes, which is what VACASK's
        control block alters: an if and its else may both hold V1, and the base of a source statement is the taken one's
        netlist source, as plan.build's R4 check takes it (2c5 took _resolve's first match, the untaken branch's V1, and
        refused S-2c5's N607 with an `(internal)' message).  L1 (phase 2c7): resolved in the state of the statement being
        printed, as plan.build's walk resolves it (run_map); 2c6 walked the render copy's values, which hold no plan
        variable, so a condition reading one (S-2c6's K2b1, K2b2, K2b4) did not evaluate and the error was swallowed
        for _resolve's first match.  None when the path names no instance there; a TableError when the paths do not
        resolve.  Without a plan (the vcs-ams deck): _resolve's match."""
        if self.plan is None:
            return _resolve(self.nl, ir_path)
        return self.run_map()[0].get(ir_path)

    def run_map(self) -> Tuple[Dict[str, Instance], Dict[str, T.PathEnv]]:
        """L1 (phase 2c7): (IR path -> instance, IR path -> PathEnv) of the instance paths the run takes in the control
        block's current state: plan.run_values and plan.run_paths (strict), with the plan variables' values in force, the
        temperatures and the X-line values the sub= alters so far set; cached per state.  TableError when they do not
        resolve (plan.build walked the same state, so it is an internal error; never _resolve's first match)."""
        key = (tuple(sorted(self.run_vars.items())), self.run_temp, self.run_tnom, tuple(sorted(self.run_x.items())))
        got = self.run_maps.get(key)
        if got is not None:
            return got
        from vamos.netlist import plan as PL
        try:
            if self.run_sites is None:
                self.run_sites = PL.temp_sites(self.nl)
            vals = PL.run_values(self.nl, self.run_vars, self.run_temp, self.run_tnom,
                                 self.plan.dependents if self.plan is not None else ())
            xv = [(x, p, v) for (path, p), v in sorted(self.run_x.items())
                  for _chain, x in path_resolutions(self.nl, path) if x.kind == "x"]
            pes = PL.run_paths(self.nl, vals, self.run_temp, self.run_tnom, xv, self.run_sites)  # type: ignore[arg-type]
        except (X.EvalError, T.TableError) as exc:
            state = ", ".join(["%s=%s" % (k, fmt(v)) for k, v in sorted(self.run_vars.items())]
                              + ["temp=%s" % fmt(self.run_temp), "tnom=%s" % fmt(self.run_tnom)]
                              + ["%s:%s=%s" % (k[0], k[1], fmt(v)) for k, v in sorted(self.run_x.items())])
            raise T.TableError("the instance paths the run takes do not resolve in the state of this statement (%s): "
                               "%s (internal)" % (state, exc))
        insts: Dict[str, Instance] = {}
        for pe in pes:
            for it in pe.items:
                insts.setdefault(it.name if not pe.path else pe.path + "." + it.name, it)
        got = self.run_maps[key] = (insts, {pe.path: pe for pe in pes})
        return got

    def run_chain(self, ir_path: str) -> Tuple[Optional[T.Scope], List[Instance]]:
        """L1 (phase 2c7): _path_chain on the instance paths the run takes (run_map): the scope of the body that holds
        the element and the X instances on its path, the taken ones (X lines of two branches may carry different m, or
        instantiate two subckts); (None, the X instances found) when the path does not resolve.  _path_chain's first
        match without a plan."""
        if self.plan is None:
            return _path_chain(self.nl, ir_path)
        insts, pes = self.run_map()
        parts = ir_path.split(".")
        chain: List[Instance] = []
        for k in range(1, len(parts)):
            x = insts.get(".".join(parts[:k]))
            if x is None or x.kind != "x":
                return None, chain
            chain.append(x)
        pe = pes.get(".".join(parts[:-1]))
        return (pe.scope if pe is not None else None), chain

    def run_update(self, act: Action) -> None:
        """L1 (phase 2c7): the state after an options, var or alter action of the plan (run_map's inputs)."""
        if act.op == "options":
            for key in ("temp", "tnom"):
                v = act.args.get(key)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    setattr(self, "run_" + key, float(v))
            return
        if act.op == "var":
            self.run_vars.update({k: float(v) for k, v in act.args.items()
                                  if isinstance(v, (int, float)) and not isinstance(v, bool)})
            return
        t, v = act.args.get("target"), act.args.get("value")
        if act.op != "alter" or not (isinstance(t, tuple) and len(t) == 3) or isinstance(v, bool) \
                or not isinstance(v, (int, float)):
            return
        kind, name, param = t
        if kind == "variable":
            self.run_vars[name] = float(v)
        elif kind == "option" and name in ("temp", "tnom"):
            setattr(self, "run_" + name, float(v))
        elif kind == "instance" and param != "m":
            inst = self.run_instance(name)
            if inst is not None and inst.kind == "x":
                self.run_x[(name, param)] = float(v)

    def path(self, ir_path: str) -> str:
        """An IR instance or node path (x1.r1) as VACASK spells it (x1:r1), quoted when needed."""
        return self.q(ir_path.replace(".", ":"))

    def spath(self, ir_path: str) -> str:
        """An IR instance or node path inside a string argument, alter instance("x1:r1"), sweep
        instance="x1:r1" and the noise/acxf in= and out=: the string carries the quotes, the name none,
        since VACASK reads the string's text as the name ("Node ''007'' not found" for out="'007'")
        (§5.2, §5.3, §5.6 [E114]; phase 1b, phase 2)."""
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
                        lines += self.hook_declarations()      # H1: before VACASK's first elaboration
                    else:
                        changed = [(k, v) for k, v in vals if self.vstate.get(k) != v]
                        if changed:
                            lines.append("  options " + " ".join("%s=%s" % kv for kv in changed))
                    self.vstate.update(dict(vals))
                    self.run_update(act)                       # L1 (phase 2c7): temp and tnom
                elif act.op == "var":
                    lines.append("  var " + " ".join("%s=%s" % (self.var_name(k), self.setting_text(v, act.origin, k))
                                                      for k, v in act.args.items()))
                    self.run_update(act)                       # L1 (phase 2c7): the variables' values
                elif act.op == "alter":
                    lines += self.alter_lines(act)
                    self.run_update(act)                       # L1 (phase 2c7): the next statements' state
                    self.track_source(act)                     # phase 2c9 (N1): the source state it makes
                elif act.op == "source":
                    lines += self.source_lines(act)
                    self.track_source(act)
                elif act.op == "analysis":
                    if not seen_options:                       # a plan without an options action
                        seen_options = True
                        vals = [("temp", fmt(self.num(27.0 if self.nl.temp is None else self.nl.temp, act.origin, "temp"))),
                                ("tnom", fmt(self.num(27.0 if self.nl.tnom is None else self.nl.tnom, act.origin, "tnom")))]
                        lines.append("  options " + " ".join(list(_HEAD_OPTIONS) + ["%s=%s" % kv for kv in vals]))
                        lines += self.hook_declarations()      # H1: before VACASK's first elaboration
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
        hook = self.hooks.get(target) if self.hooks else None     # type: ignore[arg-type]
        lines: List[str] = []
        if hook is not None:
            # H1 (phase 2c4): the target is printed through its switchable hook, which VACASK's re-evaluation of the
            # bindings keeps; a source's alter statement follows (its type and other fields)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise T.TableError("%s: the alter of %s %s of %s sets %r, not a number, where the deck prints the "
                                   "parameter through a switchable hook; not supported in v1" % (o, kind, param, name, value))
            self.flag_on[hook.flag] = True
            lines.append("  var %s=1 %s=%s" % (hook.flag, hook.value, self.setting_text(value, o, param)))
            if not (kind == "instance" and "source" in act.args):
                return lines
        if kind == "instance":
            inst = self.run_instance(name)          # K2 (phase 2c6): an if's taken branch's instance
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
                return lines + ['  alter instance("%s") %s' % (self.spath(name), " ".join(
                    self.source_fields(inst.name, o, src, state, ctl=True, base=inst.source,
                                       hooked=self.hooked_fields(name, src), path=name)))]
            card = self.va_card_target(inst, name, param)
            if card is not None:                          # a Verilog-A parameter: on the instance's card (R-plan-4)
                return ['  alter model("%s") %s=%s' % (card, param.lower(), self.setting_text(value, o, param))]
            if self.spectre and inst.kind == "r" and param == "isnoisy":
                # phase 2c2 (R-plan-19): VACASK's resistor and sp_resistor call the switch noisy, an integer
                # ("Parameter 'isnoisy' not found" before); plan.build allows the four words and a number
                return ['  alter instance("%s") noisy=%s' % (self.spath(name), _alter_noisy(value, name))]
            pname = self.instance_param(inst, param, name)
            return ['  alter instance("%s") %s=%s' % (self.spath(name), pname, self.setting_text(value, o, param))]
        if kind == "model":
            cards = self.model_names(name, o, param)
            if not cards:
                return []                                 # a Verilog-A card none of whose instances reads param
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
        inst = self.run_instance(path)          # K2 (phase 2c6): an if's taken branch's instance
        if inst is None:
            self.err(act.origin, "source action: no instance %s" % path)
            return []
        state = "dc" if typ == "dc" else typ
        self.src_state[path] = state
        return ['  alter instance("%s") %s' % (self.spath(path), " ".join(
            self.source_fields(inst.name, act.origin or "alter", src, state, ctl=True, base=inst.source,
                               hooked=self.hooked_fields(path, src), path=path)))]

    def hooked_fields(self, path: str, src: Source) -> Set[str]:
        """R4 (phase 2c5): the fields of the source at path that its instance line prints through a hook."""
        return {k for k in src.spectre if self.hooks and ("instance", path, k) in self.hooks}

    def instance_param(self, inst: Instance, param: str, path: str = "") -> str:
        """The VACASK spelling of an instance parameter a sweep or alter targets (the master's instance
        renames; a source field through SOURCE_FIELDS; the multiplier m as $mfactor and a controlled
        source's gain, gm or rm as gain, R-plan-4).  path: the instance's IR path (its name by default)."""
        if self.spectre and param == "m":
            return self.mfactor_param(inst, path or inst.name)
        if inst.kind in ("v", "i"):
            if param in SOURCE_FIELDS:
                return SOURCE_FIELDS[param]
            raise T.TableError("%s: the %s field %s cannot be swept or altered directly on VACASK in v1 (the "
                               "deck prints a derived value for it)" % (inst.name, inst.prim or inst.kind, param))
        if not self.spectre:
            return param
        if param == _GAIN_PARAM.get(inst.kind):
            return "gain"           # VACASK's vcvs/vccs/ccvs/cccs gain (dev-builtin-*.md): `'gm' ... not found' before
        if inst.kind == "k" and param == "coupling":
            return "k"              # VACASK's mutual k (dev-builtin-mutual.md; phase 2c2: 'coupling' was not found)
        if inst.kind == "l" and param in ("l", "tc1", "tc2", "dtemp") and self.coupled_fold(inst, path or inst.name):
            raise T.TableError("%s: a coupled inductor's multiplier and TCs are folded into its l on VACASK "
                               "(tables.coupled_inductance): setting %s would drop the fold; a sweep or alter of it "
                               "is not supported in v1" % (path or inst.name, param))
        scope = T.Scope(self.nl.body)
        card = scope.model(inst.master) if inst.kind in T.MODEL_KINDS_OF and inst.master else None
        master = T.spectre_master_of(inst, card)
        return _renamed(T.SPECTRE_MASTERS[master].instance if master in T.SPECTRE_MASTERS else (), param, ENGINE)

    def mfactor_param(self, inst: Instance, path: str) -> str:
        """The multiplier of an instance as a sweep or alter targets it (R-plan-4): VACASK's $mfactor, which
        `parameter 'm' ... not found' asked for (raw VACASK swept and altered it on a resistor, a subckt
        instance and an isource, X-emit 2c).  Inside a multiplied subckt instance the device's $mfactor also
        carries the enclosing multiplier ($mfactor=$mfactor*m, §4.3.7), and a coupled inductor's is folded
        into its l (tables.coupled_inductance): an error there in v1."""
        if T.MULT[ENGINE].get(inst.kind) != "param":
            raise T.TableError("%s: the multiplier of a %s is not a device parameter on VACASK (folded into the "
                               "element, or without effect); a sweep or alter of m is not supported in v1"
                               % (path, T.ELEMENT_NAME.get(inst.kind, inst.kind)))
        scope, chain = self.run_chain(path)          # L1 (phase 2c7): the X lines the run takes
        if inst.kind == "l" and scope is not None and inst.name in scope.coupled:
            raise T.TableError("%s: the multiplier of a coupled inductor is folded into its l on VACASK "
                               "(tables.coupled_inductance); a sweep or alter of m is not supported in v1" % path)
        for x in chain:
            if "m" in x.params:
                raise T.TableError("%s: VACASK sets the device's $mfactor, which also carries the multiplier of the "
                                   "enclosing instance %s (m=%s): a sweep or alter of its m is not supported on "
                                   "VACASK in v1 (Xyce runs it)" % (path, x.name, X.to_text(x.params["m"])))
        return "$mfactor"

    def coupled_fold(self, inst: Instance, path: str) -> bool:
        """A coupled inductor printed with its multiplier and TCs folded into l (rcl, tables.coupled_inductance:
        a multiplier of its own or of an enclosing subckt instance, or tc1/tc2): a sweep of its l at the nominal
        gave another response than the analysis at the nominal on both engines (X-emit 2c)."""
        if inst.kind != "l":
            return False
        scope, _chain = self.run_chain(path)         # L1 (phase 2c7): the body the run takes
        if scope is None or inst.name not in scope.coupled:
            return False
        return (T.multiplier(self.params_of(inst, scope).get("m"), scope.in_subckt, ENGINE) is not None
                or any(k in inst.params for k in ("tc1", "tc2")))

    def va_card_target(self, inst: Instance, path: str, param: str) -> Optional[str]:
        """The deck name of the per-instance card (i_y, §4.4) that a sweep or alter of a Verilog-A instance's
        parameter targets (R-plan-4): VACASK reported `parameter 'r' of instance 'X1' not found' for the
        instance; raw VACASK swept model="X1__va" and, inside a subckt, the hierarchical model="X2:Y1__va" (the
        local name is `model 'Y1__va' not found', X-emit 2c).  None for anything else, and for m (the
        instance's $mfactor)."""
        if not self.spectre or inst.kind != "y" or param == "m" or (inst.master or "") in T.VA_INSTANCE_MODULES:
            return None
        card = self.va_cards.get(id(inst))
        if card is None:
            raise T.TableError("%s: the Verilog-A instance is not printed in the deck" % path)
        parent = path.rsplit(".", 1)[0] if "." in path else ""
        return "%s:%s" % (parent.replace(".", ":"), card) if parent else card

    def is_va_card(self, m: Model) -> bool:
        return bool(m.prim) and m.prim.lower() in {k.lower() for k in self.nl.va_modules}

    def va_card_cards(self, m: Model, param: str, origin: str) -> List[str]:
        """The per-instance cards a mod= target on Verilog-A card m reaches (R-plan-4; `model 'm_vm' not found'
        before, the card itself is never printed): spectre.parse merged the card's parameters into each of its
        instances (§3.9), the same Expr objects, so an instance of m holds one of them; an instance whose own
        value of param replaced the card's does not follow the card (Spectre's instance value wins).  The
        instance paths are the ones the run takes (the taken branches: a card in an untaken @if branch does not
        exist at run time); L1 (phase 2c7): in the state of the statement (run_map), where 2c6 walked the render copy's
        values, which hold no plan variable."""
        if not m.params:
            raise T.TableError("%s: the instances of Verilog-A card %s cannot be told apart from those of another "
                               "card of its module (it sets no parameter; §3.9 merges a card into its instances): a "
                               "mod= target on it is not supported in v1 (target the instances with dev=)"
                               % (origin, m.name))
        want = param.lower()
        mine = {k.lower(): v for k, v in m.params.items()}
        if self.plan is not None:
            pes = list(self.run_map()[1].values())
        else:
            try:
                pes = list(T.path_envs(self.nl, strict=False, dialect=self.nl.dialect))
            except (X.EvalError, T.TableError) as exc:
                raise T.TableError("%s: the instances of Verilog-A card %s: %s" % (origin, m.name, exc))
        out: List[str] = []
        for pe in pes:
            for it in pe.items:
                if it.kind != "y" or (it.master or "").lower() != m.prim.lower() or id(it) not in self.va_cards:
                    continue
                own = {k.lower(): v for k, v in it.params.items()}       # the last spelling wins, as params_of's
                if not any(own.get(k) is v for k, v in mine.items()):
                    continue                                              # an instance of another card
                if want in own and own[want] is not mine.get(want):
                    continue                                              # its own value of param
                path = it.name if not pe.path else pe.path + "." + it.name
                card = self.va_card_target(it, path, param)
                if card is not None and card not in out:
                    out.append(card)
        return out

    def card_param(self, card: str, param: str) -> str:
        m = self.nl.models().get(card)
        if m is None:
            bins = [b for b in _all_models(self.nl) if b.base == card]
            m = bins[0] if bins else None
        if m is not None and self.spectre and self.is_va_card(m):
            return param.lower()                     # openvaf-r's OSDI names (§4.5 item 12)
        if m is None or not self.spectre or m.prim not in T.SPECTRE_MASTERS:
            return param
        return _renamed(T.SPECTRE_MASTERS[m.prim].params, param, ENGINE)

    def model_names(self, name: str, origin: str, param: Optional[str] = None) -> List[str]:
        """The deck names of a card, or of every entry of a model group (§5.3); for a Verilog-A card the
        per-instance cards of the instances that read its value of param (va_card_cards)."""
        m = self.nl.models().get(name)
        if m is not None and param is not None and self.spectre and self.is_va_card(m):
            return self.va_card_cards(m, param, origin)
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
        inst = self.run_instance(path)               # L1 (phase 2c7): the kind on the taken branch
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
            try:
                inst = self.run_instance(ref[1])     # L1 (phase 2c7): the kind on the taken branch
            except T.TableError as exc:
                self.err("", "save %s: %s" % (ref[1], exc))
                inst = None
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
        pts = step_values(spec)
        if pts is not None:                            # VACASK's step= would run another count (§5.2)
            return "values=[%s]" % ", ".join(fmt(v) for v in pts)
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

    def track_source(self, act: Action) -> None:
        """Phase 2c9 (N1): the source state a plan's alter of a source field (its re-resolved Source) or source switch
        (the state's Source) makes, for the statements after it (state_source)."""
        src = act.args.get("source")
        if not isinstance(src, Source):
            return
        if act.op == "source":
            path = act.args.get("path")
        else:
            t = act.args.get("target")
            path = t[1] if isinstance(t, tuple) and len(t) == 3 and t[0] == "instance" else None
        if isinstance(path, str) and path:
            self.src_now[path] = src

    def state_source(self, ir_path: str, inst: Instance) -> Optional[Source]:
        """Phase 2c9 (N1): the Source of the source at ir_path in the state of the statement being printed, as
        plan.build's source state holds it: the last alter's or switch's (track_source), else the instance line's (the
        render copy's, in tables.constant_sine's form as plan._netlist_source reads the IR)."""
        return self.src_now.get(ir_path, inst.source)

    def engine_points(self, lv: SweepLevel) -> Optional[List[float]]:
        """Phase 2c8/2c9: the points VACASK is given for a dev= sweep of a source's rise, fall or freq, through
        plan.swept_points in the source's state at this statement (state_source): a rise or fall of 0 of a pulse is
        Spectre's default edge, transres (plan.edge_default; VACASK read a fall of 0 as none and stopped at a rise of
        0), a freq of 0 of the constant sine its CONSTANT_SINE_FREQ (VACASK stops at a sine of freq 0).  2c8 read the
        instance line, so a sine an alter made the constant sine printed its freq of 0 raw (S-2c8's P873, Q05: exit
        1).  None for any other level or when no point changes: its grid prints as it is."""
        kind, name, param = lv.target
        if kind != "instance" or param not in ("rise", "fall", "freq"):
            return None
        inst = self.run_instance(name)
        if inst is None or inst.kind not in ("v", "i"):
            return None
        from vamos.netlist import plan as PL
        if param != "freq" and self.transres is None:
            self.transres = PL.edge_default(self.nl, self.variables)         # the render copy holds no plan variable
        return PL.swept_points(param, self.state_source(name, inst), lv.spec, self.transres or 0.0)

    def edge_grid(self, lv: SweepLevel) -> Optional[str]:
        """Phase 2c8: a dev= sweep of a source's rise, fall or freq whose points VACASK is given otherwise than the plan
        writes them (engine_points), as values=[...]; None for any other level."""
        pts = self.engine_points(lv)
        return None if pts is None else "values=[%s]" % ", ".join(fmt(v) for v in pts)

    def sweep_line(self, lv: SweepLevel, step_kind: str = "", nest: Sequence[SweepLevel] = ()) -> str:
        """The sweep statement of one level of a step of kind step_kind (nest: the step's levels)."""
        kind, name, param = lv.target
        hook = self.hooks.get(lv.target) if self.hooks else None      # type: ignore[arg-type]
        grid = self.edge_grid(lv) or self.grid(lv.spec)
        if hook is not None:
            # H1 (phase 2c4): a target bound to an expression sweeps its hook's value (the flag set around the nest,
            # analysis_lines); VACASK cannot sweep the parameter itself ('is bound to an expression' [E102])
            return "sweep %s variable=\"%s\" %s continuation=%d" % (lv.id, hook.value, grid, int(lv.continuation))
        if kind == "instance":
            inst = self.run_instance(name)          # K2 (phase 2c6): an if's taken branch's instance
            if inst is None:
                raise T.TableError("sweep %s: no instance %s" % (lv.id, name))
            card = self.va_card_target(inst, name, param)
            pname = param
            if param == "dc" and step_kind == "tran" and inst.kind in ("v", "i") \
                    and ("instance", name, "sinedc") not in [x.target for x in nest]:
                from vamos.netlist import plan as PL
                if PL.sinedc_follows_dc(self.state_source(name, inst)):
                    # phase 2c9 (N3): in a tran the sine acts (type="sine", dc unused), and its sinedc defaults to the dc
                    # (ref5 p.684): the sweep moves the sinedc, as Spectre's dc sweep does (2c8: the sine stayed centred
                    # on the instance line's dc, S-2c8's P824, P825); a DC-type analysis sweeps dc under type="dc"
                    pname = "sinedc"
            if card is not None:                          # a Verilog-A parameter: on the instance's card (R-plan-4)
                target = 'model="%s" parameter="%s"' % (card, param.lower())
            else:
                target = 'instance="%s" parameter="%s"' % (self.spath(name), self.instance_param(inst, pname, name))
        elif kind == "model":
            cards = self.model_names(name, lv.id, param)
            m = self.nl.models().get(name)
            if len(cards) != 1 and m is not None and self.spectre and self.is_va_card(m):
                raise T.TableError("sweep %s: Verilog-A card %s has %d instances that read its %s on VACASK, one card "
                                   "each (§4.4), and a VACASK sweep names one model: not supported in v1 (sweep an "
                                   "instance with dev=, or alter the card)" % (lv.id, name, len(cards), param))
            if len(cards) != 1:
                raise T.TableError("sweep %s: a model group (%s) cannot be swept on VACASK in v1" % (lv.id, name))
            target = 'model="%s" parameter="%s"' % (cards[0], self.card_param(name, param))
        elif kind == "option":
            target = 'option="%s"' % name
        elif kind == "variable":
            target = 'variable="%s"' % self.var_name(name)
        else:
            raise T.TableError("sweep %s: target kind %r is not a sweep statement" % (lv.id, kind))
        return "sweep %s %s %s continuation=%d" % (lv.id, target, grid, int(lv.continuation))

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
        flags: List[str] = []                        # H1: the hooks this nest sweeps whose flag is off
        sets: List[str] = []
        for lv in levels:
            hook = self.hooks.get(lv.target) if self.hooks else None     # type: ignore[arg-type]
            if hook is not None and not self.flag_on.get(hook.flag) and hook.flag not in flags:
                flags.append(hook.flag)
                sets.append("%s=1" % hook.flag)
                if not hook.var:
                    # L3 (phase 2c7): the value with its flag, the sweep's first point: VACASK sets the swept field up
                    # before the sweep's first point, and at the hook's declared 0 an exp's td2 or a sine's freq stopped
                    # it ('must be greater than 0', exit 1: S-2c6's S04, E06, E08, R0224); the sweep restores it after
                    from vamos.netlist import plan as PL
                    sets.append("%s=%s" % (hook.value, self.setting_text(PL.grid_points(lv.spec)[0], origin, "sweep")))
        if flags:
            out.append("  var " + " ".join(sets))
        indent = "  "
        for lv in levels:
            out.append(indent + self.sweep_line(lv, step.kind, levels))
            indent += "  "
        out.append(indent + self.analysis_line(step, origin))
        if restore:
            out.append("  options " + " ".join("%s=%s" % kv for kv in restore))
            self.vstate.update(dict(restore))
        if flags:                                    # back to the expression after the sweep
            out.append("  var " + " ".join("%s=0" % f for f in flags))
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
                parts.append('in="%s"' % self.spath(step.noise_input))
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
            ics = T.state_file_nodes(a, T.STATE_IC_KEYS)   # readic=/read= replaces the ic statements (§8.6)
            if ics is None:
                ics = self.nl.ics
            if ic_mode in ("all", "node") and ics:
                if step.uses and a.get("useprevic") in ("yes", True):
                    pass                                   # the stored solution is the ic (below)
                else:
                    ic = self.pairs(ics, "ic")
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
        else:
            nodesets = T.state_file_nodes(a, T.STATE_NS_KEYS)   # readns= replaces the nodeset statements (§8.6)
            if nodesets is None:
                nodesets = self.nl.nodesets
            ns = self.pairs(nodesets, "nodeset") if nodesets else ""
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
                return 'out="%s"' % self.spath(p)
            return 'out=["%s", "%s"]' % (self.spath(p), self.spath(n))
        raise T.TableError("%s: %s without an output (AnalysisStep.args['p'], ['n']: the node pair)" % (step.id, step.kind))


class _Skip(Exception):
    """An element whose error is already recorded; the walk moves on."""


def _noise_switch(v: Expr):
    """A resistor's isnoisy (§4.5 item 7, R-emit-6): True when it is off (the words no and false, any case, or
    the number 0), False when it is on (yes, true, another number), else the expression to print as noisy=:
    spectre.py keeps a bare identifier written for isnoisy as a word (a Str, §4.1 item 11), so a word that is
    none of the four names a parameter (Spectre numbers an enumeration's values from 0, UG p.81: isnoisy=no is
    0, ref5 p.629)."""
    if isinstance(v, Num):
        return v.value == 0.0
    word = v.text if isinstance(v, Str) else v.name if isinstance(v, Name) else None
    if word is None:
        return v
    low = word.strip().lower()
    if low in ("no", "false"):
        return True
    if low in ("yes", "true"):
        return False
    try:
        return float(low) == 0.0
    except ValueError:
        return Name(word.strip())


def _alter_noisy(value: object, path: str) -> str:
    """The noisy= of an alter of a resistor's isnoisy (phase 2c2, R-plan-19): 0 when the plan's value turns the
    noise off (the words no and false, any case, or the number 0, as _noise_switch reads the instance's), else
    1; TableError for a value that is neither (plan.build refuses it first)."""
    if isinstance(value, bool):
        return "0" if not value else "1"
    if isinstance(value, (int, float)):
        return "0" if float(value) == 0.0 else "1"
    if isinstance(value, str):
        switch = _noise_switch(Str(value))
        if switch is True:
            return "0"
        if switch is False:
            return "1"
    raise T.TableError("%s: isnoisy=%s is not yes, no, true, false or a number" % (path, value))


def _numv(e: object) -> float:
    if isinstance(e, Num):
        return e.value
    if isinstance(e, (int, float)):
        return float(e)
    raise T.TableError("sweep value %s is not a number after plan.build" % (X.to_text(e) if isinstance(e, Expr) else e))


def _float_or(v: object, default: float) -> float:
    """A plan setting as a float (the global temp, tnom), else default."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def vacask_step_count(start: float, stop: float, step: float) -> Optional[int]:
    """The points VACASK runs for `from=start to=stop step=step` (VACASK lib/answeep.cpp, measured
    2026-10-17 in phase 2): n = ceil((stop-start)/step) intervals, plus the end point when start+n*step
    does not pass stop; None for a step that never reaches stop.  0..0.3 step 0.1 gives 3 points,
    because 0.3/0.1 rounds to 2.9999999999999996 and 3*0.1 to 0.30000000000000004."""
    if step == 0.0 or (stop - start) * step < 0:
        return None
    n = max(int(math.ceil((stop - start) / step)), 1)
    end = start + n * step
    return n + 1 if (end <= stop if step > 0 else end >= stop) else n


def plan_step_points(start: float, stop: float, step: float) -> List[float]:
    """plan.build's step grid (results.grid_values, §5.2): start, start+step, ... up to stop within a
    1e-9 relative step, so 0..0.3 step 0.1 has its 4 points."""
    if step == 0.0 or (stop - start) * step < 0:
        return [start]
    n = int(math.floor((stop - start) / step + 1e-9))
    return [start + k * step for k in range(n + 1)]


def step_values(spec) -> Optional[List[float]]:
    """§5.2: a `step` grid is printed as VACASK's step= when VACASK runs the plan's points; when its own
    count differs (a stop a rounding error short of start+n*step), as values=[...] with the plan's points,
    as a non-integral dec is (phase 2: VACASK ran 3 points for 0..0.3 step 0.1 where the plan, Xyce and
    the results have 4).  None: step= prints."""
    if getattr(spec, "mode", None) != "step":
        return None
    start, stop, step = _numv(spec.start), _numv(spec.stop), _numv(spec.step)
    n = vacask_step_count(start, stop, step)
    pts = plan_step_points(start, stop, step)
    if n is None or n == len(pts):                 # None: a step that never reaches stop (plan.build refuses it)
        return None
    return pts


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


def _path_chain(nl: Netlist, target: str) -> Tuple[Optional[T.Scope], List[Instance]]:
    """The scope of the body that holds the element a hierarchical name (x1.x2.v1) names, and the X
    instances on its path (x1, x1.x2); (None, the instances found) when the path does not resolve."""
    parts = target.split(".")
    items: Sequence[object] = nl.body
    scope = T.Scope(nl.body)
    chain: List[Instance] = []
    for part in parts[:-1]:
        found = next((it for it in flat_items(items) if isinstance(it, Instance) and it.name == part), None)
        sub = scope.find_subckt(found.master or "") if found is not None and found.kind == "x" else None
        if sub is None:
            return None, chain
        chain.append(found)                                   # type: ignore[arg-type]
        scope = T.Scope(sub.body, scope, sub)
        items = sub.body
    return scope, chain


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
