"""Model dispatch and the per-element rules both emitters share (docs/VAMOS_AMS_DESIGN.md §4.3.5-§4.3.7, §4.7).

vacask.py and xyce.py read every engine-dependent decision from here, so the
two decks are built by one set of rules and differ only in syntax.

API
---
Model dispatch (§4.3.6)
    ELEMENT_OF[model kind] -> element kind ('nmos' -> 'm', 'pnp' -> 'q', 'd' -> 'd', ...)
    POLARITY[model kind]   -> 1.0 | -1.0 (n-type | p-type; m, q and j cards only)
    Target                 one dispatch row: VACASK module and OSDI file, Xyce level,
                           level/version/polarity rules
    DISPATCH[(element, level)] -> Target
    level_of(model) -> int
        The card's HSPICE level (default 1).  TableError for a non-integer level.
    target(model) -> Target
        The row for a card; TableError naming the level when there is none (MOS
        6/9/10/13/50, Q != 1, D 2/6, J != 1, ...: no faithful target on either
        engine, so both emitters refuse the same cards).
    model_params(model, engine, binned=False, options=None, dialect="hspice") -> (List[(name, Expr)], List[Note])
        The card's parameters as an engine gets them: level removed (the emitter
        prints the level its rule asks for), the HSPICE-only geometry keys of
        STRIP_KEYS removed with a warning each (a BSIM3 acm=10, the targets' own
        junction model, with a note), rewritten to HSPICE's values by
        hspice_card (options: Netlist.options, for .option spice and dcap),
        VACASK: version removed with a note where the module rejects it, the
        binning bounds removed from bins, type=+-1.0 added from the card kind
        (every VACASK target defaults to n-type; a PMOS card without it
        silently simulates as NMOS).  Xyce keeps the type keyword, the version
        and the bounds (native binning).  A BSIM4 card whose VERSION is not the
        major.minor an engine simulates (bsim4_version) gets a note.  Notes name
        a bin card by its base (card_label), so the emitters give them once per
        binned model.
    hspice_card(model, params, opts=None, notes=None) -> Dict[str, Expr]
        HSPICE's model defaults the targets lack (SPICE3 M 1/2/3, D, Q, J;
        Berkeley BSIM3), written into the card the same way for both engines,
        with one note per card listing what was written; an HSPICE behaviour
        neither engine has is a warning (an error under --vamos-strict).
        M 1/2/3 (_mos123): TOX>1 in Angstrom; the LEVEL 1 KP default
        2.0718e-5/8.632e-6 and PMOS UO 250; UO from KP (LEVEL 2/3); NSUB
        (default 1e15, or from GAMMA), GAMMA and PHI (0.576036) as HSPICE
        derives them; LD=0.75*XJ; CGSO/CGDO from LD/METO and TOX, CGBO from
        WD; LEVEL 3 ETA*8.14/8.15 and badmos3=1 (HSPICE's channel-length
        modulation, 21-26); the bulk junctions (_mos_junctions): CJ
        sqrt(eps_si*q*NSUB/(2*PB)), FC=0, PHP removed (PB on both targets);
        warnings for an ambiguous KP default, a LEVEL 3 XJ below 0.05u, a
        missing VTO the target cannot derive, a PHP other than PB on a
        sidewall junction, and HSPICE's default CAPOP=2 gate capacitance
        (simulated as CAPOP=0; capop=0 removed, any other CAPOP a warning).
        BSIM3 (_bsim3): LEVEL 49 XPART=1, the CAPMOD default of the card's
        VERSION (3.0: 1; 3.1: 2, a warning at LEVEL 49), HSPICE's junction
        defaults CJ=5.79e-4 and CJSW=0 where its own junction model (ACM 0, 2,
        3) applies, and a warning for that model.
        D/Q/J (_junctions): HSPICE's default DCAP=2 as FC=0 (FCS=0), DCAP=3 an
        error; diode PB 0.8 (printed vj) and PHP=PB, BJT MJS 0.5, JFET PB 0.8;
        a JFET capop=0 removed, any other CAPOP a warning.  Under .option spice
        SPICE's defaults stay (DCAP=1, MOS CAPOP=0, no LD or NSUB default, MJS=0).
    mos_junction_warnings(inst, model, options=None) -> List[Note]
        Per M instance (the message is per card): a MOS 1/2/3 card whose CJ
        default rests on the default NSUB (HSPICE's manual gives two values)
        or whose CBD/CBS HSPICE would not use, when the instance gives AD/AS.
    ModelOptions, model_options(Netlist.options) -> ModelOptions
        The .option settings hspice_card reads (spice, dcap).
    bsim4_version(engine, version) -> str
        The BSIM4 version an engine runs for a card's VERSION: VACASK
        sp_bsim4v8 (ngspice's BSIM4.8 code) 4.8.3; Xyce 4.6.1 below 4.7,
        4.7.0 below 4.8, 4.8.2 from 4.8.  (Level 54 targets sp_bsim4v8, not
        the Berkeley Verilog-A bsim4: that one rejects sky130's lintnoi and
        stops on nigc=0 with igcmod=0.)
    STRING_PARAMS[module] -> names of string-typed module parameters (printed
        "quoted" by the VACASK emitter).
    STRIP_KEYS             HSPICE-only geometry keys stripped from cards (warning).
    Spectre masters (docs/VAMOS_SPECTRE_DESIGN.md §3.9, §4.4, §10; phase 0)
        ParamRule, MasterRow   the row types; SPECTRE_MASTERS[master] -> MasterRow, the
        data (every ref5 card and instance parameter of every master with its disposition);
        model_params(dialect="spectre") applies it (S3).
        bin_bounds_spectre, bin_guard_spectre, select_bin_spectre: bin_rule="spectre"
        (exact bounds, group order, total w); phase-0 signatures, S1 implements them.

Instance rules
    SCALE_POWERS[element] -> {parameter: power of .option scale}   (§4.3.5)
    scaled(element, name, value, s, level=None) -> Expr
        A geometry value multiplied by s**power, exactly once (folded when constant).
        A diode is scaled only at LEVEL 3 (level= the card's level): HSPICE's
        SCALE does not affect a LEVEL 1 diode's unitless AREA and PJ.
    MULT[engine][element] -> how the multiplier (m=, and the one inherited from
        enclosing X instances) reaches an element (§4.3.7):
            "param"  an instance parameter ($mfactor on VACASK, m on Xyce)
            "fold"   multiplied into the expression (behavioral i=)
            "values" multiplied into every value of the waveform (Xyce I sources)
            "gain"   multiplied into the gain (Xyce F)
            "area"   multiplied into the area (Xyce J)
            "none"   potential-defining elements: V, E, H, K, behavioral v=
        Xyce silently ignores m= on I and F lines and drops the multiplier of an
        enclosing X instance for I sources and JFETs (and rejects m= on J), so the
        Xyce emitter never relies on X-line m: it carries the multiplier down the
        hierarchy itself, in the subckt parameter MF_PARAM["xyce"], exactly as
        VACASK carries $mfactor.
    MF_PARAM[engine]       the subckt parameter that carries the multiplier
    coupled_inductance(inst, mult, card, temp, tnom) -> Optional[Expr]
        The value of an inductor a K element names (Scope.coupled), with its
        effective multiplier and TC1/TC2 folded in (L*(1+TC1*dt+TC2*dt^2)/M):
        VACASK's mutual divides by $mfactor and reads the nominal l, Xyce's K
        pass drops m=, TC1 and TC2, so the emitters print the folded value and
        none of COUPLED_FOLDED; None when there is nothing to fold; TableError
        when the inductance is not on the element or the card has TC1/TC2.
    run_temps(nl) -> (temp, tnom)   as both control blocks print them
    CLOSED_PARAMS, check_params(inst)
        V I E G H F K and behavioral elements take no instance parameter but m;
        check_params raises TableError naming any other (never dropped silently).
    multiplier(m, in_subckt, engine) -> Optional[Expr]
        The effective multiplier of an instance: m at top level, MF*m (or MF) in a
        subckt; None when there is none.
    times(a, b) -> Expr    a*b, folded when both are numbers

Binning (§4.3.6)
    BIN_KEYS               ('lmin', 'lmax', 'wmin', 'wmax')
    BIN_TOL                1e-15 (absolute, lower bounds; as ngspice and VACASK)
    bin_bounds(model, values) -> Tuple[float, float, float, float]
    bin_guard(bounds, l, w, nf, s) -> Expr
        (x >= lo || abs(x-lo) < 1e-15) && x < hi for x = l*s and the per-finger
        width w*s/nf, as an expression the printers turn into an @if condition.
    select_bin(bins, l, w, nf, s) -> Optional[Model]
        The same rule evaluated in Python on numbers.
    The lower-bound tolerance lets two bins match a geometry at an edge
    (0.22*1e-6 = 2.1999999999999998e-07 is below 2.2e-7 and within 1e-15 of
    it); the first match wins, in the order Scope.bins gives: Xyce's own
    (card names compared case-insensitively as strings: nch.1, nch.10, nch.2),
    so VACASK's chain and Xyce's native binning pick the same bin.

Sources (§4.3.8)
    WAVE_FIELDS[wave] -> the fields spice.parse resolves (per, td of pwl optional)
    wave(src, name) -> Dict[str, Expr]
        The resolved fields of src's waveform, checked the same way for both
        engines: every field present (unresolved defaults are a parser bug, not a
        default the emitters may pick), constant PULSE edges > 0 (never rise=0 /
        fall=0), a constant PULSE period longer than tr+tf+pw, a constant EXP td2
        after td1, two or more PWL points with constant times >= 0 strictly
        increasing.  TableError otherwise.
    scale_source(src, k) -> Source
        src with every value (dc, ac magnitude, waveform levels, PWL values)
        multiplied by k: the multiplier of a Xyce current source.

Simulator options (§4.3.5)
    solver_options(nl, origin="") -> (Dict[str, object], List[Note])
        The .option keys both engines can honour: {'gmin': float} (VACASK
        options gmin, Xyce .options device gmin) and {'method': 'gear'}
        (VACASK tran_method="gear", Xyce .options timeint method=gear; both
        variable order up to 2, as HSPICE's GEAR).  METHOD=TRAP is both
        engines' default.  reltol, abstol and vntol get a note each: VACASK has
        SPICE's meaning for them, Xyce's RELTOL/ABSTOL control its LTE and
        Newton residual instead, so neither engine is given them and both run
        with their defaults.  Other keys of nl.options are not the emitters'
        (spice.parse gave them their dispositions); scale, tnom and spice are
        read elsewhere.

Deck-level elements the AMS layer adds (§4.7; ordinary IR the emitters print)
    BRIDGE_LIB, BRIDGE_INIT[engine]
    code_uri(engine, direction, name) -> str
        "code:libcosim_bridge.so:<vacask_bridge_init|nvc_bridge_init>:<d2a|a2d>:<name>"
    code_source(name, kind, p, n, uri) -> Instance       ('v' D2A value/enable, 'i' A2D probe)
    dc_source(name, p, n, value) -> Instance
    gcond(name, n, nd, ne, rr) -> Instance
        The gated-conductance D2A element i(nd -> n) = v(ne)*(v(nd)-v(n))/rr:
        kind 'y', master GCOND_MODULE.  VACASK prints it as an instance of the
        OSDI module vamos_gcond (vamos/ams/va/vamos_ie.va, compiled by deck.py);
        Xyce prints it as a B source with the same law.
    shunt(name, node, r=SHUNT_R) -> Instance              (1e12 Ohm to 0)
    VA_SOURCE              absolute path of vamos/ams/va/vamos_ie.va
    VA_INSTANCE_MODULES    Verilog-A modules whose parameters are all instance
                           parameters (one shared model card on VACASK)

Shared walk
    MODEL_KINDS_OF[element] -> the card kinds an element may use; TERMINALS[element]
    constant(exprs, values, shadowed=()) -> Optional[Tuple[float, ...]]
        The values of exprs with the top-level parameters, or None when one is not
        constant or reads a name an enclosing subckt declares (shadowing).
    smoke_netlist(nl) -> Netlist
        A copy of nl for the smoke check (§4.7): every value or probe bridge
        source (Source.code_uri) holds DC 0, every enable source (a bridge name
        ending in __e) DC 1.0.
    nvc_libdir() -> str   the nvc library directory for engines.env_for
    Scope(items, parent=None, subckt=None)
        One body (top level or a subckt) as the emitters see it: model cards,
        binned groups and subckt definitions visible from it (innermost first),
        and coupled (the inductors a K element of the body names).
        Methods model(name), bins(base), find_subckt(name), where(name) (the
        definition and its defining scope), card_of(inst) ((card, []) or
        (None, bins)), path().
    all_subckts(nl) -> List[Subckt]   every definition, nested ones included
    reachable(nl) -> Set[int]
        ids of the Subckt and Model objects reachable from the top-level
        instances.  The emitters print only these: a library defines far more
        than a deck uses (sky130), HSPICE never instantiates the rest, and a
        construct no target honours must not fail a deck that never uses it.
    PathEnv, path_envs(nl, values=None, strict=False, dialect="hspice") -> Iterator[PathEnv]
        Every instance path (docs/VAMOS_SPECTRE_DESIGN.md §4.4), the top level
        first, depth first in body order, with its subckt, Scope, parameter
        values (xyce._Deck.child_env's rule) and instances (each Cond resolved
        on the path).  path_counts(nl) -> {subckt name: paths that reach it}.

Errors are TableError (a ValueError) naming the construct; the emitters turn
them into notes with the element's origin.
"""

from __future__ import annotations

import copy
import math
import os
from collections import abc
from dataclasses import dataclass, field, replace
from typing import Dict, FrozenSet, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

from vamos.netlist import expr as X
from vamos.netlist.expr_ast import Binary, Call, Expr, Name, Num, Str, Ternary
from vamos.netlist.ir import Cond, Instance, Model, Netlist, Param, ParamTest, Source, Subckt
from vamos.notes import Note, note, warning

ENGINES = ("vacask", "xyce")


class TableError(ValueError):
    """A construct no table row covers; the message names it."""


# -- model dispatch (§4.3.6) ------------------------------------------------------

ELEMENT_OF: Dict[str, str] = {"nmos": "m", "pmos": "m", "npn": "q", "pnp": "q", "njf": "j",
                              "pjf": "j", "d": "d", "r": "r", "c": "c", "l": "l"}
POLARITY: Dict[str, float] = {"nmos": 1.0, "pmos": -1.0, "npn": 1.0, "pnp": -1.0, "njf": 1.0,
                              "pjf": -1.0}
ELEMENT_NAME = {"m": "MOS", "q": "BJT", "j": "JFET", "d": "diode", "r": "resistor",
                "c": "capacitor", "l": "inductor"}


@dataclass(frozen=True)
class Target:
    element: str                 # m q j d r c l
    vacask_module: str           # module name in the OSDI file
    vacask_osdi: str             # file in the VACASK module path
    xyce_level: Optional[int]    # None: no faithful Xyce model (the Xyce emitter refuses the card)
    keep_level: bool = False     # VACASK: print level= (sp_diode selects its model with it)
    strip_version: bool = False  # VACASK: the module rejects version=
    polarity: bool = True        # VACASK: print type=+-1.0
    xyce_print_level: bool = True
    describe: str = ""           # what the card is simulated as (notes)


_MOS1 = Target("m", "sp_mos1", "spice/mos1.osdi", 1)
_MOS2 = Target("m", "sp_mos2", "spice/mos2.osdi", 2)
_MOS3 = Target("m", "sp_mos3", "spice/mos3.osdi", 3)
_BSIM3 = Target("m", "sp_bsim3v3", "spice/bsim3v3.osdi", 9, strip_version=True,
                describe="BSIM3v3.3 (VACASK sp_bsim3v3); Xyce simulates it as its BSIM3v3.2.2 (level 9)")
# BSIM4: ngspice's BSIM4.8 code (sp_bsim4v8), as for every other SPICE level.  The Berkeley
# Verilog-A bsim4 (bsim4v8.osdi) rejects sky130's lintnoi and stops on nigc=0 / nigbacc=0
# with igcmod=igbmod=0 (ngspice, HSPICE and Xyce accept them); on N3's BSIM4 bin deck
# sp_bsim4v8 equals Xyce to 7 digits where bsim4 differs in the 6th.
_BSIM4 = Target("m", "sp_bsim4v8", "spice/bsim4v8.osdi", 54)
_DIODE1 = Target("d", "sp_diode", "spice/diode.osdi", 1, keep_level=True, polarity=False)
_DIODE3 = Target("d", "sp_diode", "spice/diode.osdi", None, keep_level=True, polarity=False,
                 describe="HSPICE geometric diode (level 3): Xyce has no such model")
_BJT = Target("q", "sp_bjt", "spice/bjt.osdi", 1)
_JFET = Target("j", "sp_jfet1", "spice/jfet1.osdi", 1)
_RES = Target("r", "sp_resistor", "spice/resistor.osdi", 1, polarity=False, xyce_print_level=False)
_CAP = Target("c", "sp_capacitor", "spice/capacitor.osdi", 1, polarity=False, xyce_print_level=False)
_IND = Target("l", "sp_inductor", "spice/inductor.osdi", 1, polarity=False, xyce_print_level=False)

DISPATCH: Dict[Tuple[str, int], Target] = {
    ("m", 1): _MOS1, ("m", 2): _MOS2, ("m", 3): _MOS3,
    ("m", 49): _BSIM3, ("m", 53): _BSIM3, ("m", 54): _BSIM4,
    ("d", 1): _DIODE1, ("d", 3): _DIODE3,
    ("q", 1): _BJT,
    ("j", 1): _JFET,
    ("r", 1): _RES, ("c", 1): _CAP, ("l", 1): _IND,
}

# HSPICE-only geometry keys the targets do not honour identically (ACM area
# calculation and its diffusion/resistance inputs; VACASK sp_bsim3v3 has
# ngspice's own ACM, Xyce ignores the keys).  Stripped from cards on both
# engines with a warning, so the engines see the same card.
STRIP_KEYS: FrozenSet[str] = frozenset(("acm", "calcacm", "hdif", "ldif", "rdc", "rsc"))

# String-typed module parameters (VACASK prints them as "strings").
STRING_PARAMS: Dict[str, FrozenSet[str]] = {
    "sp_bsim4v8": frozenset(("version",)),
}

BIN_KEYS = ("lmin", "lmax", "wmin", "wmax")
BIN_TOL = 1e-15


def level_of(model: Model) -> int:
    """The card's HSPICE level as an int (HSPICE's default is 1 for every device)."""
    lv = model.level
    if lv is None:
        e = model.params.get("level")
        if e is None:
            return 1
        if not isinstance(e, Num):              # spice.parse leaves level None when it is not constant
            raise TableError("model %s: level=%s is not a constant" % (model.name, X.to_text(e)))
        lv = e.value
    if lv != math.floor(lv):
        raise TableError("level %s of model %s is not an integer" % (X.to_text(Num(lv)), model.name))
    return int(lv)


def target(model: Model) -> Target:
    """The dispatch row for a card; TableError when there is none."""
    elem = ELEMENT_OF.get(model.kind)
    if elem is None:
        raise TableError("model %s has unknown kind %r" % (model.name, model.kind))
    lv = level_of(model)
    row = DISPATCH.get((elem, lv))
    if row is None:
        have = sorted(k[1] for k in DISPATCH if k[0] == elem)
        raise TableError("%s model %s: HSPICE level %d has no faithful VACASK/Xyce target "
                         "(supported levels: %s)"
                         % (ELEMENT_NAME[elem], model.name, lv, ", ".join(str(h) for h in have)))
    return row


def card_label(model: Model) -> str:
    """'model <name>', or 'binned model <base>' for a bin card (notes are given once per base)."""
    return "binned model %s" % model.base if model.base is not None else "model %s" % model.name


def bsim4_version(engine: str, version: float) -> str:
    """The BSIM4 version an engine simulates for a card's VERSION (HSPICE simulates the card's own).

    VACASK sp_bsim4v8 is ngspice's BSIM4.8 code: 4.8.3 whatever the card asks.  Xyce
    (N_DEV_MOSFET_B4.C, checkAndFixVersion_) runs 4.6.1 below 4.7, 4.7.0 below 4.8 and 4.8.2
    from 4.8.
    """
    if engine == "vacask":
        return "4.8.3"
    if version < 4.70:
        return "4.6.1"
    return "4.7.0" if version < 4.80 else "4.8.2"


def _version_note(model: Model, row: Target, engine: str, value: Expr) -> Optional[Note]:
    if row is not _BSIM4 or not isinstance(value, Num):
        return None
    runs = bsim4_version(engine, value.value)
    if math.floor(value.value * 10 + 1e-9) == math.floor(float(runs[:3]) * 10 + 1e-9):
        return None                              # same major.minor: the card's own equations
    return note(model.origin or model.name, "%s: BSIM4 version=%s is simulated as BSIM4 %s on %s (HSPICE "
                "uses %s; the versions differ by bug fixes and new, default-off features)"
                % (card_label(model), X.to_text(value), runs, "VACASK (sp_bsim4v8)" if engine == "vacask"
                   else "Xyce", X.to_text(value)))


def model_params(model: Model, engine: str, binned: bool = False,
                 options: Optional[Mapping[str, Expr]] = None,
                 dialect: str = "hspice") -> Tuple[List[Tuple[str, Expr]], List[Note]]:
    """The card's parameters for one engine, and the notes the changes need (see the module docstring).

    options is Netlist.options: .option spice and .option dcap select HSPICE's model defaults
    (hspice_card); None means neither is given.
    dialect is Netlist.dialect (VAMOS_SPECTRE_DESIGN.md §4.4): "hspice" is this path; "spectre"
    (S3) takes the Spectre path (Model.prim, SPECTRE_MASTERS, no hspice_card, no STRIP_KEYS
    strip) and raises NotImplementedError until then; any other name is a ValueError.
    """
    if dialect not in ("hspice", "spectre"):
        raise ValueError("dialect must be 'hspice' or 'spectre', not %r" % (dialect,))
    if dialect == "spectre":
        raise NotImplementedError("model_params(dialect='spectre') is not implemented yet "
                                  "(VAMOS_SPECTRE_DESIGN.md §3.9, §4.4: phase 1, S3)")
    _engine(engine)
    row = target(model)
    out: List[Tuple[str, Expr]] = []
    notes: List[Note] = []
    origin = model.origin or model.name
    pol = POLARITY.get(model.kind)
    card: Dict[str, Expr] = {}
    for name, value in model.params.items():
        key = name.lower()
        if key == "level":
            continue
        if key == "acm" and row is _BSIM3 and isinstance(value, Num) and value.value == ACM_BERKELEY:
            notes.append(note(origin, "%s: acm=10 removed: HSPICE's ACM=10 (the Berkeley junction model, "
                                      "AS/AD/PS/PD as given) is what both BSIM3 targets simulate"
                              % card_label(model)))
            continue
        if key in STRIP_KEYS:
            notes.append(warning(origin, "%s: HSPICE-only parameter %s=%s removed (the %s "
                                          "target does not honour it as HSPICE does)"
                                 % (card_label(model), key, X.to_text(value), engine)))
            continue
        if key == "type":
            if pol is not None and isinstance(value, Num) and value.value == pol:
                continue                         # the same polarity the card kind gives
            raise TableError("model %s: type=%s contradicts its kind %s"
                             % (model.name, X.to_text(value), model.kind))
        card[key] = value
    for key, value in hspice_card(model, card, model_options(options), notes).items():
        if binned and key in BIN_KEYS and engine == "vacask":
            continue
        if key == "version" and engine == "vacask" and row.strip_version:
            notes.append(note(origin, "%s: version=%s removed; simulated as %s"
                              % (card_label(model), X.to_text(value), row.describe)))
            continue
        if key == "version":
            vn = _version_note(model, row, engine, value)
            if vn is not None:
                notes.append(vn)
        out.append((key, value))
    if engine == "xyce" and row is _DIODE1:
        out = _xyce_diode_charge(model, out, notes)
    if row is _RES or row is _CAP:
        out = _wire_card(model, row.element, engine, out, notes)
    if engine == "vacask":
        if row.keep_level:
            out.insert(0, ("level", Num(float(level_of(model)))))
        if row.polarity and pol is not None:
            out.insert(0, ("type", Num(pol)))
    return out, notes


# -- Spectre masters (VAMOS_SPECTRE_DESIGN.md §3.9, §4.4, §10; phase 0: the row types and the data) ----
#
# SPECTRE_MASTERS lists, per Spectre master, every ref5 card parameter with one disposition
# (ParamRule) and every instance parameter the same way.  model_params(dialect="spectre") (S3)
# applies the card rules; plan.build (S2) decides every rule whose message depends on the run
# (ParamRule.warn) and refuses a sweep or alter of a parameter whose rule folds or strips it (§5.2);
# spectre.py (S1) builds models from it.

@dataclass(frozen=True)
class ParamRule:
    """One disposition of one parameter of a master (§3.9).  Several rules may share a name; the
    first whose `when` and `match` hold applies.  Examples:
        ParamRule("capmod", "strip", match=("absent", "bsim"), warn="analyses=ac,noise,xf,tran")
        ParamRule("capmod", "strip", match=("meyer",)); ParamRule("capmod", "error", match=("none", "yang"))
        ParamRule("hcomp", "error", match=("nonzero",))
        ParamRule("eg", "default", "1.124481", "absent:eg", warn="temp!=tnom")
        ParamRule("nsub", "default", "1.13e16", "absent:nsub"); ParamRule("phi", "default", "0.7", "absent:phi,absent:nsub")
        ParamRule("badmos3", "default", "1", "absent:badmos3", warn="card")
    """
    name: str                                     # the parameter, lower case
    action: str                                   # fold | pass | rename | default | strip | error
    value: str = ""                               # rename target, or the default written ("=fc" copies fc)
    when: str = ""                                # "" | "absent:<p>" | "given:<p>"; several joined by "," (all hold),
                                                  # read on the card as written
    match: Tuple[str, ...] = ()                   # the rule applies only to these values of the parameter: an
                                                  # enumeration value, "absent", "0" or "nonzero", or a Spectre
                                                  # number ("1", "0.5", "1/3", "7.02e-4"), compared numerically;
                                                  # () = any value
    warn: str = ""                                # a warning besides the action: "card" (model_params, per card) |
                                                  # "analyses=ac,noise,xf,tran" | "analyses=noise" | "temp!=tnom"
                                                  # (plan.build, per run)
    cite: str = ""                                # "ref5 p.410"


@dataclass(frozen=True)
class MasterRow:
    """One Spectre master: the IR element kind, the card kind by polarity, the DISPATCH level, the
    terminal names, the default geometry, and the card and instance parameter rules (§3.9, §10)."""
    master: str                                   # resistor capacitor inductor vsource ... diode bjt jfet mos1 ... bsim4
    element: str                                  # IR element kind: r c l v i e g h f k d q j m
    level: Optional[int] = None                   # the tables.DISPATCH level
    polarity_key: str = ""                        # "type" for mos*/bsim*/bjt/jfet
    kinds: Tuple[Tuple[str, str], ...] = ()       # polarity value -> IR card kind, first = default
    terminals: Tuple[str, ...] = ()
    geometry: Tuple[Tuple[str, float], ...] = ()  # default instance w/l; default lmin lmax wmin wmax
    params: Tuple[ParamRule, ...] = ()            # card parameters
    instance: Tuple[ParamRule, ...] = ()          # instance parameters: dev= renames (§5.2), the SPICE-appended
                                                  # check (§4.3), folded inputs (§3.8)


# SPECTRE_MASTERS data (phase 0, S0C).  Built from ref5's component chapters against the targets' parameter
# tables (/usr/local/src/VACASK/devices/spice/*.va, VACASK docs/dev-builtin-*.md; Xyce N_DEV_*.C load*Parameters);
# every ref5 instance and model parameter of every v1 master has a row, every ref5 default was compared with
# both targets' effective defaults (the comparison tables: scratchpad sp0/S0C/comparison.md).  Conventions:
#   - cite "ref5 p.N" is the parameter's page; the text after ";" names the target source lines or the rule's
#     reason.  "fails loudly" = the engine rejects the parameter (VACASK at elaboration, Xyce via the output scan);
#   - the first rule whose `when` and `match` hold applies (§10); a rule reaches an absent parameter only through
#     match=("absent",) or a when= on it (default rows, absence warnings);
#   - match tokens: "absent", "nonzero", an enumeration value, or a Spectre number/expression ("0", "1",
#     "1/3", "7.02e-4") compared numerically;
#   - a default row is written under the name it is printed with (nbv for Spectre's nz; the targets' badmos3);
#     its value is Spectre syntax ("1/3"), "=p" copies parameter p;
#   - strip always carries a note (§3.9), the warning named by warn= where there is one; fold = consumed into the
#     IR by spectre.py (polarity -> Model.kind, level -> Model.level, geometry and instance defaults -> instances);
#   - an instance rule's when= is read on the instance after the card's folded defaults were applied (§3.9 default
#     MOS geometry); the sources' rules transcribe §3.8.1, which spectre.resolve_source implements, so their
#     run-dependent errors (xfmag, noisefile) are pass rows whose cite names the condition.
SPECTRE_MASTERS: Dict[str, MasterRow] = {
    # -- resistor [M ref5 pp.629-632] ------------------------------------------------
    "resistor": MasterRow(
        "resistor", "r", None, "",
        kinds=(('', 'r'),),
        terminals=('1', '2'),
        geometry=(),
        params=(
            ParamRule("r", "fold", cite="ref5 p.630; §3.8: the instance's r, else the card's, else rsh*(l-2*etchl)/(w-2*etch); spectre.py folds it"),
            ParamRule("rsh", "fold", cite="ref5 p.630; §3.8 fold"),
            ParamRule("l", "fold", cite="ref5 p.630; §3.8 fold; card default l=inf"),
            ParamRule("w", "fold", cite="ref5 p.630; §3.8 fold; card default w=1e-6"),
            ParamRule("etch", "fold", cite="ref5 p.630; §3.8 fold"),
            ParamRule("etchl", "fold", cite="ref5 p.630; §3.8 fold"),
            ParamRule("thresh", "strip", cite="ref5 p.630; formulation threshold only; r=0 prints as a short (§3.8, §4.5 item 10)"),
            ParamRule("scaler", "strip", match=("1",), cite="ref5 p.630; resistance scaling factor: no target"),
            ParamRule("scaler", "error", cite="ref5 p.630; resistance scaling factor: no target"),
            ParamRule("tc1", "pass", cite="ref5 p.630; sp_resistor tc1 (instance alias, resistor.va:92; a model statement may give it) / model_tc1; Xyce model TC1 (N_DEV_Resistor.C:241)"),
            ParamRule("tc2", "pass", cite="ref5 p.630; resistor.va:93, 108; N_DEV_Resistor.C:244"),
            ParamRule("tnom", "pass", cite="ref5 p.630; 'set by options' = the targets' 0 (resistor.va:116; N_DEV_Resistor.C:263)"),
            ParamRule("trise", "strip", match=("0",), cite="ref5 p.631; card default temperature rise: no target; §3.8 trise != 0 -> error"),
            ParamRule("trise", "error", match=("nonzero",), cite="ref5 p.631; card default temperature rise: no target; §3.8 trise != 0 -> error"),
            ParamRule("coeffs", "error", cite="ref5 p.631; nonlinear coeffs= on R: error in v1 (§0)"),
            ParamRule("nonlinform", "strip", cite="ref5 p.631; meaningful only with coeffs (an error)"),
            ParamRule("symmetric", "strip", cite="ref5 p.631; meaningful only with coeffs (an error)"),
            ParamRule("kf", "pass", warn="analyses=noise", cite="ref5 p.631; sp_resistor kf (resistor.va:114); Xyce's resistor has no noise parameters (fails loudly); Spectre's W*L normalization of the flicker term is not documented in ref5, VACASK divides by W^wf*L^lf (resistor.va:255)"),
            ParamRule("af", "default", "2", "absent:af,given:kf", cite="ref5 p.631; §3.9 table: ref5 af=2, sp_resistor af=1 (resistor.va:115)"),
            ParamRule("af", "pass", cite="ref5 p.631; resistor.va:115"),
            ParamRule("wdexp", "rename", "wf", cite="ref5 p.631; flicker W exponent on the drawn W: sp_resistor wf acts on W-2*narrow (resistor.va:255); narrow is 0 after the fold"),
            ParamRule("ldexp", "rename", "lf", cite="ref5 p.631; flicker L exponent: sp_resistor lf (resistor.va:255)"),
            ParamRule("weexp", "strip", match=("0",), cite="ref5 p.631; effective-width exponent: no target (sp_resistor has one exponent per dimension)"),
            ParamRule("weexp", "strip", warn="analyses=noise", cite="ref5 p.631; effective-width exponent: no target (sp_resistor has one exponent per dimension)"),
            ParamRule("leexp", "strip", match=("0",), cite="ref5 p.631; effective-length exponent: no target"),
            ParamRule("leexp", "strip", warn="analyses=noise", cite="ref5 p.631; effective-length exponent: no target"),
            ParamRule("fexp", "rename", "ef", cite="ref5 p.631; flicker frequency exponent: sp_resistor ef (resistor.va:122, 322)"),
            *[ParamRule(n, "strip", cite="ref5 p.631; DC-mismatch parameters: only dcmatch reads them (not in v1)")
              for n in "mr mrl mrlp mrw mrwp".split()],
            *[ParamRule(n, "strip", cite="ref5 p.632; DC-mismatch parameters: only dcmatch reads them (not in v1)")
              for n in "mrlw1 mrlw1p mrlw2 mrlw2p".split()],
            ParamRule("c", "strip", match=("0",), cite="ref5 p.632; wire RC model: §3.8 c= -> error"),
            ParamRule("c", "error", match=("nonzero",), cite="ref5 p.632; wire RC model: §3.8 c= -> error"),
            ParamRule("cj", "strip", match=("0",), cite="ref5 p.632; wire RC capacitance: no target"),
            ParamRule("cj", "error", match=("nonzero",), cite="ref5 p.632; wire RC capacitance: no target"),
            ParamRule("cjsw", "strip", match=("0",), cite="ref5 p.632; wire RC"),
            ParamRule("cjsw", "error", match=("nonzero",), cite="ref5 p.632; wire RC"),
            ParamRule("thick", "strip", match=("0",), cite="ref5 p.632; wire RC dielectric"),
            ParamRule("thick", "error", match=("nonzero",), cite="ref5 p.632; wire RC dielectric"),
            ParamRule("di", "strip", match=("0",), cite="ref5 p.632; wire RC dielectric"),
            ParamRule("di", "error", match=("nonzero",), cite="ref5 p.632; wire RC dielectric"),
            ParamRule("cratio", "strip", cite="ref5 p.632; wire RC only (c, cj, cjsw are errors when nonzero)"),
            ParamRule("tc1c", "strip", cite="ref5 p.632; wire RC only"),
            ParamRule("tc2c", "strip", cite="ref5 p.632; wire RC only"),
            ParamRule("shrink", "strip", match=("1",), cite="ref5 p.632; w/l shrink: no target"),
            ParamRule("shrink", "error", cite="ref5 p.632; w/l shrink: no target"),
            ParamRule("scalec", "strip", match=("1",), cite="ref5 p.632; wire RC only"),
            ParamRule("scalec", "error", cite="ref5 p.632; wire RC only"),
        ),
        instance=(
            ParamRule("r", "pass", cite="ref5 p.629; the instance value (Instance.value); sp_resistor r (resistor.va:86), Xyce R (N_DEV_Resistor.C:169)"),
            ParamRule("l", "pass", cite="ref5 p.629; §3.8: l w -> sp_resistor (resistor.va:89-90; noise area); Instance.folded records them when they fed r"),
            ParamRule("w", "pass", cite="ref5 p.629; §3.8; resistor.va:90"),
            ParamRule("m", "pass", cite="ref5 p.629; multiplier (existing MULT rules)"),
            ParamRule("scale", "fold", cite="ref5 p.629; §3.8: applied to w and l by spectre.py, never printed [E23]"),
            ParamRule("resform", "strip", cite="ref5 p.629; formulation choice; r=0 prints as a short (§3.8)"),
            ParamRule("tc1", "pass", cite="ref5 p.629; resistor.va:92; N_DEV_Resistor.C:194"),
            ParamRule("tc2", "pass", cite="ref5 p.629; resistor.va:93; N_DEV_Resistor.C:198"),
            ParamRule("trise", "strip", match=("0",), cite="ref5 p.629; §3.8: trise != 0 -> error"),
            ParamRule("trise", "error", match=("nonzero",), cite="ref5 p.629; §3.8: trise != 0 -> error"),
            ParamRule("isnoisy", "pass", cite="ref5 p.629; §4.5 item 7: VACASK noisy=0, Xyce the G form"),
            ParamRule("c", "error", cite="ref5 p.629; §3.8: c= (wire RC) -> error"),
            ParamRule("tc1c", "strip", cite="ref5 p.629; wire RC only (c is an error)"),
            ParamRule("tc2c", "strip", cite="ref5 p.629; wire RC only"),
        ),
    ),
    # -- capacitor [M ref5 pp.283-285] -----------------------------------------------
    "capacitor": MasterRow(
        "capacitor", "c", None, "",
        kinds=(('', 'c'),),
        terminals=('1', '2'),
        geometry=(),
        params=(
            ParamRule("c", "fold", cite="ref5 p.284; §3.8: the instance's c, else the card's, else cj*Area_eff+cjsw*Perim_eff; spectre.py folds it"),
            ParamRule("tc1", "pass", cite="ref5 p.284; sp_capacitor tc1 (capacitor.va:67; instance alias usable in a model statement); Xyce model TC1 (N_DEV_Capacitor.C:163)"),
            ParamRule("tc2", "pass", cite="ref5 p.284; capacitor.va:68; N_DEV_Capacitor.C:165"),
            ParamRule("trise", "strip", match=("0",), cite="ref5 p.284; card default temperature rise: no target; §3.8 trise != 0 -> error"),
            ParamRule("trise", "error", match=("nonzero",), cite="ref5 p.284; card default temperature rise: no target; §3.8 trise != 0 -> error"),
            ParamRule("tnom", "pass", cite="ref5 p.284; 'set by options' = the targets' 0 (capacitor.va:87; N_DEV_Capacitor.C:167)"),
            ParamRule("w", "fold", cite="ref5 p.284; §3.8 fold"),
            ParamRule("l", "fold", cite="ref5 p.284; §3.8 fold"),
            ParamRule("etch", "fold", cite="ref5 p.284; §3.8 fold"),
            ParamRule("cj", "fold", cite="ref5 p.284; §3.8 fold"),
            ParamRule("cjsw", "fold", cite="ref5 p.285; §3.8 fold"),
            ParamRule("scalec", "strip", match=("1",), cite="ref5 p.285; §3.8: scalec != 1 -> error"),
            ParamRule("scalec", "error", cite="ref5 p.285; §3.8: scalec != 1 -> error"),
            ParamRule("coeffs", "error", cite="ref5 p.285; nonlinear coeffs= on C: error in v1 (§0)"),
            ParamRule("rforce", "strip", cite="ref5 p.285; resistance used when forcing initial conditions: a numerical aid of Spectre's ic mechanism"),
        ),
        instance=(
            ParamRule("c", "pass", cite="ref5 p.283; the instance value; sp_capacitor c (capacitor.va:62), Xyce C (N_DEV_Capacitor.C:77)"),
            ParamRule("w", "fold", cite="ref5 p.283; §3.8: feeds the computed c (Instance.folded)"),
            ParamRule("l", "fold", cite="ref5 p.283; §3.8 fold"),
            ParamRule("m", "pass", cite="ref5 p.283; multiplier"),
            ParamRule("scale", "fold", cite="ref5 p.283; p.283: scales w and l (as the resistor's); never printed"),
            ParamRule("trise", "strip", match=("0",), cite="ref5 p.284; §3.8: trise != 0 -> error"),
            ParamRule("trise", "error", match=("nonzero",), cite="ref5 p.284; §3.8: trise != 0 -> error"),
            ParamRule("tc1", "pass", cite="ref5 p.284; capacitor.va:67; N_DEV_Capacitor.C:113"),
            ParamRule("tc2", "pass", cite="ref5 p.284; capacitor.va:68; N_DEV_Capacitor.C:117"),
            ParamRule("ic", "pass", cite="ref5 p.284; §0: device ic= on C is an error unless every tran uses ic=dc or ic=node; plan.build decides"),
            ParamRule("area", "fold", cite="ref5 p.284; §3.8: feeds the computed c (Instance.folded)"),
            ParamRule("perim", "fold", cite="ref5 p.284; §3.8 fold"),
        ),
    ),
    # -- inductor [M ref5 pp.373-374] ------------------------------------------------
    "inductor": MasterRow(
        "inductor", "l", None, "",
        kinds=(('', 'l'),),
        terminals=('1', '2'),
        geometry=(),
        params=(
            ParamRule("l", "fold", cite="ref5 p.373; §3.8: the instance's l, else the card's l, else 0; spectre.py folds it"),
            ParamRule("r", "strip", match=("0",), cite="ref5 p.373; §0: a series r= on an inductor -> error (v1)"),
            ParamRule("r", "error", match=("nonzero",), cite="ref5 p.373; §0: a series r= on an inductor -> error (v1)"),
            ParamRule("tc1", "pass", cite="ref5 p.373; sp_inductor tc1 (inductor.va:68; instance alias usable in a model statement); Xyce model TC1 (N_DEV_Inductor.C:127)"),
            ParamRule("tc2", "pass", cite="ref5 p.373; inductor.va:69; N_DEV_Inductor.C:132"),
            ParamRule("trise", "strip", match=("0",), cite="ref5 p.373; card default temperature rise: no target (the R/C reading of §3.8)"),
            ParamRule("trise", "error", match=("nonzero",), cite="ref5 p.373; card default temperature rise: no target (the R/C reading of §3.8)"),
            ParamRule("tnom", "pass", cite="ref5 p.373; 'set by options': sp_inductor tnom=0 (inductor.va:77); Xyce's inductor model TNOM default is 27 (N_DEV_Inductor.C:122), which matters only with tc1/tc2 and a tnom option other than 27"),
            ParamRule("rforce", "strip", cite="ref5 p.373; a numerical aid of Spectre's nodeset/ic forcing"),
            ParamRule("coeffs", "error", cite="ref5 p.374; nonlinear coeffs= on L: error in v1 (§0)"),
            ParamRule("scalei", "strip", match=("1",), cite="ref5 p.374; inductance scaling factor: no target"),
            ParamRule("scalei", "error", cite="ref5 p.374; inductance scaling factor: no target"),
            ParamRule("kf", "strip", cite="ref5 p.374; flicker noise of the series resistance, which is an error when nonzero"),
            ParamRule("af", "strip", cite="ref5 p.374; as kf"),
        ),
        instance=(
            ParamRule("l", "pass", cite="ref5 p.373; the instance value; sp_inductor l (inductor.va:65), Xyce L (N_DEV_Inductor.C:66)"),
            ParamRule("r", "strip", match=("0",), cite="ref5 p.373; §3.8: r= -> error (v1)"),
            ParamRule("r", "error", match=("nonzero",), cite="ref5 p.373; §3.8: r= -> error (v1)"),
            ParamRule("m", "pass", cite="ref5 p.373; multiplier"),
            ParamRule("trise", "strip", match=("0",), cite="ref5 p.373; the R/C reading of §3.8: trise != 0 -> error"),
            ParamRule("trise", "error", match=("nonzero",), cite="ref5 p.373; the R/C reading of §3.8: trise != 0 -> error"),
            ParamRule("ic", "pass", cite="ref5 p.373; §0: device ic= on L is an error unless every tran uses ic=dc or ic=node; plan.build decides"),
            ParamRule("isnoisy", "strip", cite="ref5 p.373; noise of the series resistance, which is an error when nonzero"),
        ),
    ),
    # -- vsource [M ref5 pp.683-686] -------------------------------------------------
    "vsource": MasterRow(
        "vsource", "v", None, "",
        kinds=(),
        terminals=('p', 'n'),
        geometry=(),
        params=(),
        instance=(
            ParamRule("dc", "pass", cite="ref5 p.683; §3.8.1 Source.dc"),
            ParamRule("type", "pass", cite="ref5 p.683; §3.8.1: dc pulse pwl sine exp -> Source.wave"),
            ParamRule("fundname", "pass", cite="ref5 p.683; §3.8.1: silent (only pac/pdisto/envlp read it)"),
            ParamRule("delay", "pass", cite="ref5 p.683; §3.8.1 td"),
            ParamRule("val0", "pass", cite="ref5 p.683; §3.8.1 pulse/exp v1"),
            ParamRule("val1", "pass", cite="ref5 p.683; §3.8.1 v2"),
            ParamRule("period", "pass", cite="ref5 p.683; §3.8.1: default inf -> no per"),
            ParamRule("rise", "pass", cite="ref5 p.683; §3.8.1: absent or 0 -> transres, with a warning when a tran runs"),
            ParamRule("fall", "pass", cite="ref5 p.683; §3.8.1: as rise"),
            ParamRule("width", "pass", cite="ref5 p.683; §3.8.1: default inf -> pw=1e30"),
            ParamRule("file", "pass", cite="ref5 p.684; §3.8.1: two-column file, cwd then -I"),
            ParamRule("wave", "pass", cite="ref5 p.684; §3.8.1 pwl points"),
            ParamRule("offset", "pass", cite="ref5 p.684; §3.8.1 pwl v*scale+offset"),
            ParamRule("scale", "pass", cite="ref5 p.684; §3.8.1 pwl"),
            ParamRule("stretch", "pass", cite="ref5 p.684; §3.8.1 pwl t*stretch"),
            ParamRule("allbrkpts", "pass", cite="ref5 p.684; §3.8.1: ignored"),
            ParamRule("pwlperiod", "error", cite="ref5 p.684; §0, §3.8.1: periodic PWL -> error"),
            ParamRule("twidth", "error", cite="ref5 p.684; §0, §3.8.1: periodic PWL -> error"),
            ParamRule("sinedc", "pass", cite="ref5 p.684; §3.8.1: defaults to dc"),
            ParamRule("ampl", "pass", cite="ref5 p.684; §3.8.1 va"),
            ParamRule("freq", "pass", cite="ref5 p.684; §3.8.1: 0 gives a constant"),
            ParamRule("sinephase", "pass", cite="ref5 p.684; §3.8.1 phase"),
            ParamRule("ampl2", "error", cite="ref5 p.684; §0, §3.8.1: second sinusoid -> error"),
            ParamRule("freq2", "error", cite="ref5 p.684; §0, §3.8.1"),
            ParamRule("sinephase2", "error", cite="ref5 p.684; §0, §3.8.1"),
            ParamRule("fundname2", "pass", cite="ref5 p.684; §3.8.1: silent (second fundamental name; freq2 is an error)"),
            ParamRule("fmmodindex", "error", cite="ref5 p.684; §0, §3.8.1: FM modulation -> error"),
            ParamRule("fmmodfreq", "error", cite="ref5 p.685; §0, §3.8.1"),
            ParamRule("ammodindex", "error", cite="ref5 p.685; §0, §3.8.1: AM modulation -> error"),
            ParamRule("ammodfreq", "error", cite="ref5 p.685; §0, §3.8.1"),
            ParamRule("ammodphase", "error", cite="ref5 p.685; §0, §3.8.1"),
            ParamRule("damp", "pass", cite="ref5 p.685; §3.8.1 theta"),
            ParamRule("td1", "pass", cite="ref5 p.685; §3.8.1: td1' = delay+td1"),
            ParamRule("tau1", "pass", cite="ref5 p.685; §3.8.1: missing -> error"),
            ParamRule("td2", "pass", cite="ref5 p.685; §3.8.1"),
            ParamRule("tau2", "pass", cite="ref5 p.685; §3.8.1"),
            ParamRule("noisefile", "pass", cite="ref5 p.685; §3.8.1: error while a noise analysis runs, else silent (run-dependent: resolve_source/plan.build)"),
            ParamRule("noisevec", "pass", cite="ref5 p.685; §3.8.1: as noisefile"),
            ParamRule("mag", "pass", cite="ref5 p.685; §3.8.1 Source.ac"),
            ParamRule("phase", "pass", cite="ref5 p.685; §3.8.1 Source.ac"),
            ParamRule("xfmag", "pass", match=("1",), cite="ref5 p.685; §3.8.1: 1 is the neutral value"),
            ParamRule("xfmag", "pass", cite="ref5 p.685; §3.8.1: != 1 while an xf runs -> error (run-dependent: resolve_source/plan.build)"),
            ParamRule("pacmag", "pass", cite="ref5 p.685; §3.8.1: silent (pac only)"),
            ParamRule("pacphase", "pass", cite="ref5 p.685; §3.8.1: silent"),
            ParamRule("m", "pass", cite="ref5 p.686; multiplier (existing rules)"),
            ParamRule("tc1", "pass", match=("0",), cite="ref5 p.686; §3.8.1: 0 is the neutral value"),
            ParamRule("tc1", "error", match=("nonzero",), cite="ref5 p.686; §3.8.1: tc1 != 0 -> error"),
            ParamRule("tc2", "pass", match=("0",), cite="ref5 p.686; §3.8.1"),
            ParamRule("tc2", "error", match=("nonzero",), cite="ref5 p.686; §3.8.1: tc2 != 0 -> error"),
            ParamRule("tnom", "strip", cite="ref5 p.686; the reference temperature of tc1/tc2, which are errors when nonzero"),
        ),
    ),
    # -- isource [M ref5 pp.380-383] -------------------------------------------------
    "isource": MasterRow(
        "isource", "i", None, "",
        kinds=(),
        terminals=('sink', 'src'),
        geometry=(),
        params=(),
        instance=(
            ParamRule("dc", "pass", cite="ref5 p.380; §3.8.1 Source.dc"),
            ParamRule("type", "pass", cite="ref5 p.380; §3.8.1: dc pulse pwl sine exp -> Source.wave"),
            ParamRule("fundname", "pass", cite="ref5 p.380; §3.8.1: silent (only pac/pdisto/envlp read it)"),
            ParamRule("delay", "pass", cite="ref5 p.381; §3.8.1 td"),
            ParamRule("val0", "pass", cite="ref5 p.381; §3.8.1 pulse/exp v1"),
            ParamRule("val1", "pass", cite="ref5 p.381; §3.8.1 v2"),
            ParamRule("period", "pass", cite="ref5 p.381; §3.8.1: default inf -> no per"),
            ParamRule("rise", "pass", cite="ref5 p.381; §3.8.1: absent or 0 -> transres, with a warning when a tran runs"),
            ParamRule("fall", "pass", cite="ref5 p.381; §3.8.1: as rise"),
            ParamRule("width", "pass", cite="ref5 p.381; §3.8.1: default inf -> pw=1e30"),
            ParamRule("file", "pass", cite="ref5 p.381; §3.8.1: two-column file, cwd then -I"),
            ParamRule("wave", "pass", cite="ref5 p.381; §3.8.1 pwl points"),
            ParamRule("offset", "pass", cite="ref5 p.381; §3.8.1 pwl v*scale+offset"),
            ParamRule("scale", "pass", cite="ref5 p.381; §3.8.1 pwl"),
            ParamRule("stretch", "pass", cite="ref5 p.381; §3.8.1 pwl t*stretch"),
            ParamRule("allbrkpts", "pass", cite="ref5 p.381; §3.8.1: ignored"),
            ParamRule("pwlperiod", "error", cite="ref5 p.381; §0, §3.8.1: periodic PWL -> error"),
            ParamRule("twidth", "error", cite="ref5 p.381; §0, §3.8.1: periodic PWL -> error"),
            ParamRule("sinedc", "pass", cite="ref5 p.381; §3.8.1: defaults to dc"),
            ParamRule("ampl", "pass", cite="ref5 p.382; §3.8.1 va"),
            ParamRule("freq", "pass", cite="ref5 p.382; §3.8.1: 0 gives a constant"),
            ParamRule("sinephase", "pass", cite="ref5 p.382; §3.8.1 phase"),
            ParamRule("ampl2", "error", cite="ref5 p.382; §0, §3.8.1: second sinusoid -> error"),
            ParamRule("freq2", "error", cite="ref5 p.382; §0, §3.8.1"),
            ParamRule("sinephase2", "error", cite="ref5 p.382; §0, §3.8.1"),
            ParamRule("fundname2", "pass", cite="ref5 p.382; §3.8.1: silent (second fundamental name; freq2 is an error)"),
            ParamRule("fmmodindex", "error", cite="ref5 p.382; §0, §3.8.1: FM modulation -> error"),
            ParamRule("fmmodfreq", "error", cite="ref5 p.382; §0, §3.8.1"),
            ParamRule("ammodindex", "error", cite="ref5 p.382; §0, §3.8.1: AM modulation -> error"),
            ParamRule("ammodfreq", "error", cite="ref5 p.382; §0, §3.8.1"),
            ParamRule("ammodphase", "error", cite="ref5 p.382; §0, §3.8.1"),
            ParamRule("damp", "pass", cite="ref5 p.382; §3.8.1 theta"),
            ParamRule("td1", "pass", cite="ref5 p.382; §3.8.1: td1' = delay+td1"),
            ParamRule("tau1", "pass", cite="ref5 p.382; §3.8.1: missing -> error"),
            ParamRule("td2", "pass", cite="ref5 p.382; §3.8.1"),
            ParamRule("tau2", "pass", cite="ref5 p.382; §3.8.1"),
            ParamRule("noisefile", "pass", cite="ref5 p.382; §3.8.1: error while a noise analysis runs, else silent (run-dependent: resolve_source/plan.build)"),
            ParamRule("noisevec", "pass", cite="ref5 p.383; §3.8.1: as noisefile"),
            ParamRule("mag", "pass", cite="ref5 p.383; §3.8.1 Source.ac"),
            ParamRule("phase", "pass", cite="ref5 p.383; §3.8.1 Source.ac"),
            ParamRule("xfmag", "pass", match=("1",), cite="ref5 p.383; §3.8.1: 1 is the neutral value"),
            ParamRule("xfmag", "pass", cite="ref5 p.383; §3.8.1: != 1 while an xf runs -> error (run-dependent: resolve_source/plan.build)"),
            ParamRule("pacmag", "pass", cite="ref5 p.383; §3.8.1: silent (pac only)"),
            ParamRule("pacphase", "pass", cite="ref5 p.383; §3.8.1: silent"),
            ParamRule("m", "pass", cite="ref5 p.383; multiplier (existing rules)"),
            ParamRule("tc1", "pass", match=("0",), cite="ref5 p.383; §3.8.1: 0 is the neutral value"),
            ParamRule("tc1", "error", match=("nonzero",), cite="ref5 p.383; §3.8.1: tc1 != 0 -> error"),
            ParamRule("tc2", "pass", match=("0",), cite="ref5 p.383; §3.8.1"),
            ParamRule("tc2", "error", match=("nonzero",), cite="ref5 p.383; §3.8.1: tc2 != 0 -> error"),
            ParamRule("tnom", "strip", cite="ref5 p.383; the reference temperature of tc1/tc2, which are errors when nonzero"),
        ),
    ),
    # -- iprobe ------------------------------------------------------------------------------
    "iprobe": MasterRow(
        "iprobe", "v", None, "",
        kinds=(),
        terminals=('in', 'out'),
        geometry=(),
        params=(),
        instance=(),
    ),
    # -- vcvs [M ref5 pp.681-681] ----------------------------------------------------
    "vcvs": MasterRow(
        "vcvs", "e", None, "",
        kinds=(),
        terminals=('p', 'n', 'ps', 'ns'),
        geometry=(),
        params=(),
        instance=(
            ParamRule("m", "pass", cite="ref5 p.681; multiplier"),
            ParamRule("type", "fold", match=("vcvs",), cite="ref5 p.681; the linear vcvs is the IR kind"),
            ParamRule("type", "error", cite="ref5 p.681; logic (and/nand/or/nor) and vcr/vccap forms: not in v1"),
            ParamRule("delta", "strip", match=("0",), cite="ref5 p.681; smoothing of the logic forms"),
            ParamRule("delta", "error", match=("nonzero",), cite="ref5 p.681; smoothing of the logic forms"),
            ParamRule("gain", "pass", cite="ref5 p.681; VACASK vcvs gain (dev-builtin-vcvs.md); Xyce E gain (N_DEV_Vcvs.C:63)"),
            ParamRule("min", "error", cite="ref5 p.681; output clamping: no target"),
            ParamRule("max", "error", cite="ref5 p.681; output clamping: no target"),
            ParamRule("abs", "strip", match=("off",), cite="ref5 p.681; off is the linear source"),
            ParamRule("abs", "error", cite="ref5 p.681; absolute output: no target"),
            ParamRule("file", "error", cite="ref5 p.681; PWL transfer function: not in v1"),
            ParamRule("pwl", "error", cite="ref5 p.681; PWL transfer function: not in v1"),
            ParamRule("scale", "strip", match=("1",), cite="ref5 p.681; PWL output scale (pwl is an error)"),
            ParamRule("scale", "error", cite="ref5 p.681; PWL output scale (pwl is an error)"),
            ParamRule("stretch", "strip", match=("1",), cite="ref5 p.681; PWL controlling-value scale (pwl is an error)"),
            ParamRule("stretch", "error", cite="ref5 p.681; PWL controlling-value scale (pwl is an error)"),
            ParamRule("tc1", "strip", match=("0",), cite="ref5 p.681; gain temperature coefficient: no target"),
            ParamRule("tc1", "error", match=("nonzero",), cite="ref5 p.681; gain temperature coefficient: no target"),
        ),
    ),
    # -- vccs [M ref5 pp.679-679] ----------------------------------------------------
    "vccs": MasterRow(
        "vccs", "g", None, "",
        kinds=(),
        terminals=('sink', 'src', 'ps', 'ns'),
        geometry=(),
        params=(),
        instance=(
            ParamRule("m", "pass", cite="ref5 p.679; multiplier"),
            ParamRule("type", "fold", match=("vccs",), cite="ref5 p.679; the linear vccs is the IR kind"),
            ParamRule("type", "error", cite="ref5 p.679; logic (and/nand/or/nor) and vcr/vccap forms: not in v1"),
            ParamRule("delta", "strip", match=("0",), cite="ref5 p.679; smoothing of the logic forms"),
            ParamRule("delta", "error", match=("nonzero",), cite="ref5 p.679; smoothing of the logic forms"),
            ParamRule("gm", "pass", cite="ref5 p.679; VACASK vccs gain (dev-builtin-vccs.md); Xyce G transconductance (N_DEV_VCCS.C:63)"),
            ParamRule("min", "error", cite="ref5 p.679; output clamping: no target"),
            ParamRule("max", "error", cite="ref5 p.679; output clamping: no target"),
            ParamRule("abs", "strip", match=("off",), cite="ref5 p.679; off is the linear source"),
            ParamRule("abs", "error", cite="ref5 p.679; absolute output: no target"),
            ParamRule("file", "error", cite="ref5 p.679; PWL transfer function: not in v1"),
            ParamRule("pwl", "error", cite="ref5 p.679; PWL transfer function: not in v1"),
            ParamRule("scale", "strip", match=("1",), cite="ref5 p.679; PWL output scale (pwl is an error)"),
            ParamRule("scale", "error", cite="ref5 p.679; PWL output scale (pwl is an error)"),
            ParamRule("stretch", "strip", match=("1",), cite="ref5 p.679; PWL controlling-value scale (pwl is an error)"),
            ParamRule("stretch", "error", cite="ref5 p.679; PWL controlling-value scale (pwl is an error)"),
            ParamRule("tc1", "strip", match=("0",), cite="ref5 p.679; gain temperature coefficient: no target"),
            ParamRule("tc1", "error", match=("nonzero",), cite="ref5 p.679; gain temperature coefficient: no target"),
            ParamRule("tc2", "strip", match=("0",), cite="ref5 p.679; gain temperature coefficient: no target"),
            ParamRule("tc2", "error", match=("nonzero",), cite="ref5 p.679; gain temperature coefficient: no target"),
        ),
    ),
    # -- ccvs [M ref5 pp.289-289] ----------------------------------------------------
    "ccvs": MasterRow(
        "ccvs", "h", None, "",
        kinds=(),
        terminals=('p', 'n'),
        geometry=(),
        params=(),
        instance=(
            ParamRule("m", "pass", cite="ref5 p.289; multiplier"),
            ParamRule("probe", "pass", cite="ref5 p.289; §3.8: a vsource or iprobe; VACASK ctlinst (dev-builtin-ccvs.md), Xyce the controlling source"),
            ParamRule("port", "strip", match=("0",), cite="ref5 p.289; the probe's port index: a vsource/iprobe has one port"),
            ParamRule("port", "error", cite="ref5 p.289; the probe's port index: a vsource/iprobe has one port"),
            ParamRule("probes", "error", cite="ref5 p.289; multi-input controlled source: no target"),
            ParamRule("ports", "error", cite="ref5 p.289; multi-input"),
            ParamRule("type", "fold", match=("ccvs",), cite="ref5 p.289; the linear ccvs is the IR kind"),
            ParamRule("type", "error", cite="ref5 p.289; logic (and/nand/or/nor) and vcr/vccap forms: not in v1"),
            ParamRule("delta", "strip", match=("0",), cite="ref5 p.289; smoothing of the logic forms"),
            ParamRule("delta", "error", match=("nonzero",), cite="ref5 p.289; smoothing of the logic forms"),
            ParamRule("rm", "pass", cite="ref5 p.289; VACASK ccvs gain (dev-builtin-ccvs.md); Xyce H"),
            ParamRule("min", "error", cite="ref5 p.289; output clamping: no target"),
            ParamRule("max", "error", cite="ref5 p.289; output clamping: no target"),
            ParamRule("abs", "strip", match=("off",), cite="ref5 p.289; off is the linear source"),
            ParamRule("abs", "error", cite="ref5 p.289; absolute output: no target"),
            ParamRule("file", "error", cite="ref5 p.289; PWL transfer function: not in v1"),
            ParamRule("pwl", "error", cite="ref5 p.289; PWL transfer function: not in v1"),
            ParamRule("scale", "strip", match=("1",), cite="ref5 p.289; PWL output scale (pwl is an error)"),
            ParamRule("scale", "error", cite="ref5 p.289; PWL output scale (pwl is an error)"),
        ),
    ),
    # -- cccs [M ref5 pp.286-287] ----------------------------------------------------
    "cccs": MasterRow(
        "cccs", "f", None, "",
        kinds=(),
        terminals=('sink', 'src'),
        geometry=(),
        params=(),
        instance=(
            ParamRule("m", "pass", cite="ref5 p.286; multiplier"),
            ParamRule("probe", "pass", cite="ref5 p.286; §3.8: a vsource or iprobe; VACASK ctlinst (dev-builtin-cccs.md), Xyce the controlling source"),
            ParamRule("port", "strip", match=("0",), cite="ref5 p.286; the probe's port index: a vsource/iprobe has one port"),
            ParamRule("port", "error", cite="ref5 p.286; the probe's port index: a vsource/iprobe has one port"),
            ParamRule("probes", "error", cite="ref5 p.286; multi-input controlled source: no target"),
            ParamRule("ports", "error", cite="ref5 p.286; multi-input"),
            ParamRule("type", "fold", match=("cccs",), cite="ref5 p.286; the linear cccs is the IR kind"),
            ParamRule("type", "error", cite="ref5 p.286; logic (and/nand/or/nor) and vcr/vccap forms: not in v1"),
            ParamRule("delta", "strip", match=("0",), cite="ref5 p.287; smoothing of the logic forms"),
            ParamRule("delta", "error", match=("nonzero",), cite="ref5 p.287; smoothing of the logic forms"),
            ParamRule("gain", "pass", cite="ref5 p.287; VACASK cccs gain (dev-builtin-cccs.md); Xyce F"),
            ParamRule("min", "error", cite="ref5 p.287; output clamping: no target"),
            ParamRule("max", "error", cite="ref5 p.287; output clamping: no target"),
            ParamRule("abs", "strip", match=("off",), cite="ref5 p.287; off is the linear source"),
            ParamRule("abs", "error", cite="ref5 p.287; absolute output: no target"),
            ParamRule("file", "error", cite="ref5 p.287; PWL transfer function: not in v1"),
            ParamRule("pwl", "error", cite="ref5 p.287; PWL transfer function: not in v1"),
            ParamRule("scale", "strip", match=("1",), cite="ref5 p.287; PWL output scale (pwl is an error)"),
            ParamRule("scale", "error", cite="ref5 p.287; PWL output scale (pwl is an error)"),
            ParamRule("stretch", "strip", match=("1",), cite="ref5 p.287; PWL controlling-value scale (pwl is an error)"),
            ParamRule("stretch", "error", cite="ref5 p.287; PWL controlling-value scale (pwl is an error)"),
            ParamRule("tc1", "strip", match=("0",), cite="ref5 p.287; gain temperature coefficient: no target"),
            ParamRule("tc1", "error", match=("nonzero",), cite="ref5 p.287; gain temperature coefficient: no target"),
            ParamRule("tc2", "strip", match=("0",), cite="ref5 p.287; gain temperature coefficient: no target"),
            ParamRule("tc2", "error", match=("nonzero",), cite="ref5 p.287; gain temperature coefficient: no target"),
        ),
    ),
    # -- mutual_inductor [M ref5 pp.577-577] -----------------------------------------
    "mutual_inductor": MasterRow(
        "mutual_inductor", "k", None, "",
        kinds=(),
        terminals=(),
        geometry=(),
        params=(),
        instance=(
            ParamRule("coupling", "pass", cite="ref5 p.577; VACASK mutual k (dev-builtin-mutual.md); Xyce K coupling (N_DEV_MutIndLin.C:149)"),
            ParamRule("ind1", "pass", cite="ref5 p.577; VACASK ind1; Xyce the coupled inductor names"),
            ParamRule("ind2", "pass", cite="ref5 p.577; as ind1"),
        ),
    ),
    # -- diode [M ref5 pp.302-308] ---------------------------------------------------
    "diode": MasterRow(
        "diode", "d", 1, "",
        kinds=(('', 'd'),),
        terminals=('a', 'c'),
        geometry=(),
        params=(
            ParamRule("level", "fold", match=("1",), cite="ref5 p.303; §3.9: level 1 (junction) is the DISPATCH level"),
            ParamRule("level", "error", cite="ref5 p.303; §3.9: Spectre's diode levels 2 (Fowler-Nordheim) and 3 are errors"),
            ParamRule("hcomp", "strip", match=("0",), cite="ref5 p.303; §3.9: 0 selects Spectre's junction equations"),
            ParamRule("hcomp", "error", match=("nonzero",), cite="ref5 p.303; §3.9: hcomp != 0 -> error in v1"),
            ParamRule("dcap", "strip", cite="ref5 p.303; §3.9: Spectre's dcap acts only with level 3 and hcomp=1 (both errors); never HSPICE's DCAP"),
            ParamRule("etch", "strip", match=("0",), cite="ref5 p.304; ref5 documents no level-1 use of the drawn geometry"),
            ParamRule("etch", "strip", warn="card", cite="ref5 p.304; see 0"),
            ParamRule("etchl", "strip", warn="card", cite="ref5 p.304; as etch (default etchl=etch)"),
            ParamRule("shrink", "strip", match=("1",), cite="ref5 p.304; level 3 only"),
            ParamRule("shrink", "strip", warn="card", cite="ref5 p.304; level 3 only"),
            ParamRule("l", "strip", match=("1e-6",), cite="ref5 p.304; default drawn length: ref5 documents no level-1 use; sp_diode would compute area and pj from w and l (diode.va:657-659)"),
            ParamRule("l", "strip", warn="card", cite="ref5 p.304; see 1e-6"),
            ParamRule("w", "strip", match=("1e-6",), cite="ref5 p.304; as l"),
            ParamRule("w", "strip", warn="card", cite="ref5 p.304; as l"),
            ParamRule("js", "pass", cite="ref5 p.304; sp_diode is (alias js, diode.va:111); Xyce JS (N_DEV_Diode.C:108)"),
            ParamRule("jsw", "pass", cite="ref5 p.304; diode.va:112; N_DEV_Diode.C:115"),
            ParamRule("n", "pass", cite="ref5 p.304; diode.va:121; N_DEV_Diode.C:131"),
            ParamRule("ns", "pass", cite="ref5 p.304; diode.va:122; N_DEV_Diode.C:138"),
            ParamRule("ik", "rename", "ikf", cite="ref5 p.304; sp_diode ikf (alias ik, diode.va:141); Xyce IKF (N_DEV_Diode.C:160); inf = the targets' 0 (off)"),
            ParamRule("ikp", "default", "=ik", "absent:ikp,given:ik,given:jsw", cite="ref5 p.304; ref5 ikp=ik: sp_diode ikp=0 is off (diode.va:143); Xyce has no IKP: the output scan of the deck fails loudly (§7.2)"),
            ParamRule("ikp", "pass", cite="ref5 p.304; diode.va:143; Xyce has no IKP: the output scan of the deck fails loudly (§7.2)"),
            ParamRule("ikr", "pass", cite="ref5 p.304; diode.va:142; Xyce has no IKR: the output scan of the deck fails loudly (§7.2); inf = 0 (off)"),
            ParamRule("area", "strip", match=("1",), cite="ref5 p.304; model default area factor 1 = the instance default of both targets (diode.va:146; N_DEV_Diode.C:67)"),
            ParamRule("area", "rename", "model_area", cite="ref5 p.304; sp_diode model_area (diode.va:146); Xyce has no model-level AREA (fails loudly)"),
            ParamRule("perim", "strip", match=("0",), cite="ref5 p.304; model default perimeter factor 0 = the targets' instance default"),
            ParamRule("perim", "rename", "model_pj", cite="ref5 p.304; sp_diode model_pj (diode.va:147); Xyce has none"),
            ParamRule("allow_scaling", "strip", cite="ref5 p.304; the instance scale is an error when != 1"),
            ParamRule("tt", "pass", cite="ref5 p.304; diode.va:123; N_DEV_Diode.C:167"),
            ParamRule("cd", "strip", match=("0",), cite="ref5 p.304; linear capacitance *area: no target"),
            ParamRule("cd", "error", match=("nonzero",), cite="ref5 p.304; linear capacitance *area: no target"),
            ParamRule("cjo", "pass", cite="ref5 p.305; diode.va:126; N_DEV_Diode.C:174"),
            ParamRule("vj", "pass", cite="ref5 p.305; diode.va:129; N_DEV_Diode.C:199; equal defaults (1) are not written"),
            ParamRule("pb", "rename", "vj", cite="ref5 p.305; alias of vj: sp_diode pb (diode.va:130), Xyce has only VJ"),
            ParamRule("m", "pass", cite="ref5 p.305; grading coefficient: diode.va:131 (alias mj); N_DEV_Diode.C:206"),
            ParamRule("cjsw", "pass", cite="ref5 p.305; diode.va:136 (alias of cjp); N_DEV_Diode.C:212"),
            ParamRule("vjsw", "pass", cite="ref5 p.305; diode.va:138 (alias of php); N_DEV_Diode.C:235"),
            ParamRule("mjsw", "pass", cite="ref5 p.305; 0.33 on both targets (diode.va:139; N_DEV_Diode.C:242): not written (§3.9)"),
            ParamRule("fc", "pass", cite="ref5 p.305; 0.5 on both targets (diode.va:167; N_DEV_Diode.C:299): not written (§3.9)"),
            ParamRule("fcs", "default", "=fc", "absent:fcs,given:fc", cite="ref5 p.305; §3.9 table: ref5 fcs=fc; the targets' fcs=0.5 (diode.va:168; N_DEV_Diode.C:305)"),
            ParamRule("fcs", "pass", cite="ref5 p.305; diode.va:168; N_DEV_Diode.C:305"),
            *[ParamRule(n, "strip", cite="ref5 p.305; level 3 only (metal/poly capacitances); level 3 is an error")
              for n in "lm lp wm wp xm xp xoi xom xw".split()],
            ParamRule("bv", "pass", cite="ref5 p.305; inf = no breakdown = sp_diode bv=0 (diode.va:169), Xyce BV=1e99 (N_DEV_Diode.C:311)"),
            ParamRule("vb", "rename", "bv", cite="ref5 p.306; alias of bv (diode.va:170; N_DEV_Diode.C:320)"),
            ParamRule("ibv", "pass", cite="ref5 p.306; diode.va:173; N_DEV_Diode.C:328"),
            ParamRule("nbv", "default", "1", "absent:nz,given:n", cite="ref5 nz=1 (p.306); the targets default nbv to n (diode.va:602; N_DEV_Diode.C:1631)"),
            ParamRule("nz", "rename", "nbv", cite="ref5 p.306; sp_diode nbv (alias nz, diode.va:145); Xyce NBV (N_DEV_Diode.C:335)"),
            ParamRule("bvj", "strip", cite="ref5 p.306; breakdown warning threshold"),
            ParamRule("rs", "pass", cite="ref5 p.306; diode.va:116; N_DEV_Diode.C:123"),
            ParamRule("rsw", "pass", cite="ref5 p.306; diode.va:117; Xyce has no RSW: the output scan of the deck fails loudly (§7.2)"),
            ParamRule("gleak", "strip", match=("0",), cite="ref5 p.306; junction leakage conductance: no target"),
            ParamRule("gleak", "error", match=("nonzero",), cite="ref5 p.306; junction leakage conductance: no target"),
            ParamRule("gleaksw", "strip", match=("0",), cite="ref5 p.306; no target"),
            ParamRule("gleaksw", "error", match=("nonzero",), cite="ref5 p.306; no target"),
            ParamRule("minr", "strip", cite="ref5 p.306; minimum series resistance: a Spectre numerical floor"),
            ParamRule("tlev", "pass", cite="ref5 p.306; sp_diode tlev (diode.va:148, HSPICE's TLEV); Xyce has no TLEV: the output scan of the deck fails loudly (§7.2)"),
            ParamRule("tlevc", "pass", cite="ref5 p.306; diode.va:149; Xyce has no TLEVC: the output scan of the deck fails loudly (§7.2)"),
            ParamRule("eg", "default", "1.124481", "absent:eg", warn="temp!=tnom", cite="ref5 p.306; §3.9 table: ref5 eg at 27 C; the targets 1.11 (diode.va:605-611; N_DEV_Diode.C:248) [E34]"),
            ParamRule("eg", "pass", cite="ref5 p.306; diode.va:150; N_DEV_Diode.C:248"),
            ParamRule("gap1", "pass", cite="ref5 p.306; diode.va:151; Xyce has no GAP1: the output scan of the deck fails loudly (§7.2)"),
            ParamRule("gap2", "pass", cite="ref5 p.306; diode.va:152; Xyce has no GAP2: the output scan of the deck fails loudly (§7.2)"),
            ParamRule("xti", "pass", cite="ref5 p.306; diode.va:153; N_DEV_Diode.C:255"),
            ParamRule("tbv1", "pass", cite="ref5 p.306; sp_diode tcv (alias tbv1, diode.va:176); Xyce TBV1 (N_DEV_Diode.C:268)"),
            ParamRule("tbv2", "pass", cite="ref5 p.306; Xyce TBV2 (N_DEV_Diode.C:273); sp_diode has no tbv2: VACASK fails loudly at elaboration"),
            ParamRule("tnom", "pass", cite="ref5 p.306; diode.va:114; N_DEV_Diode.C:354"),
            ParamRule("trise", "strip", match=("0",), cite="ref5 p.306; card default temperature rise: no target (the instance trise maps to dtemp)"),
            ParamRule("trise", "error", match=("nonzero",), cite="ref5 p.306; card default temperature rise: no target (the instance trise maps to dtemp)"),
            ParamRule("trs", "pass", cite="ref5 p.307; diode.va:118; N_DEV_Diode.C:285"),
            ParamRule("trs2", "pass", cite="ref5 p.307; diode.va:120; N_DEV_Diode.C:292"),
            ParamRule("tgs", "strip", cite="ref5 p.307; temperature coefficient of gleak, which is an error when nonzero"),
            ParamRule("tgs2", "strip", cite="ref5 p.307; as tgs"),
            ParamRule("cta", "pass", cite="ref5 p.307; diode.va:154; Xyce has no CTA: the output scan of the deck fails loudly (§7.2)"),
            ParamRule("ctp", "pass", cite="ref5 p.307; diode.va:156; Xyce has no CTP: the output scan of the deck fails loudly (§7.2)"),
            ParamRule("pta", "rename", "tpb", cite="ref5 p.307; junction potential temperature coefficient: sp_diode tpb (diode.va:157, HSPICE's TPB); Xyce has none"),
            ParamRule("ptp", "rename", "tphp", cite="ref5 p.307; sidewall potential temperature coefficient: sp_diode tphp (diode.va:159); Xyce has none"),
            *[ParamRule(n, "strip", cite="ref5 p.307; explosion/limit currents and a convergence aid")
              for n in "jmelt jmax dskip".split()],
            *[ParamRule(n, "strip", cite="ref5 p.307; Fowler-Nordheim parameters (level 2, an error); nr must not reach the targets' NR")
              for n in "if ir ecrf ecrr nf nr tox".split()],
            ParamRule("kf", "pass", cite="ref5 p.308; diode.va:165; N_DEV_Diode.C:360"),
            ParamRule("af", "pass", cite="ref5 p.308; diode.va:166; N_DEV_Diode.C:366"),
        ),
        instance=(
            ParamRule("area", "pass", cite="ref5 p.302; diode.va:98; N_DEV_Diode.C:67"),
            ParamRule("perim", "rename", "pj", cite="ref5 p.302; sp_diode pj (alias perim, diode.va:100); Xyce PJ (N_DEV_Diode.C:71)"),
            ParamRule("l", "strip", warn="card", cite="ref5 p.302; ref5 documents no level-1 use of the drawn l/w; sp_diode would compute area and pj from them (diode.va:657-659)"),
            ParamRule("w", "strip", warn="card", cite="ref5 p.302; as l"),
            ParamRule("m", "pass", cite="ref5 p.303; multiplier"),
            ParamRule("scale", "strip", match=("1",), cite="ref5 p.303; geometry scale: no target (allow_scaling)"),
            ParamRule("scale", "error", cite="ref5 p.303; geometry scale: no target (allow_scaling)"),
            ParamRule("region", "strip", cite="ref5 p.303; an initial operating-region hint"),
            ParamRule("trise", "rename", "dtemp", cite="ref5 p.303; temperature rise from ambient: sp_diode dtemp (diode.va:97), Xyce DTEMP (N_DEV_Diode.C:92)"),
            *[ParamRule(n, "strip", cite="ref5 p.303; level 3 only")
              for n in "lm lp wm wp".split()],
        ),
    ),
    # -- bjt [M ref5 pp.50-58] -----------------------------------------------------
    "bjt": MasterRow(
        "bjt", "q", 1, "type",
        kinds=(('npn', 'npn'), ('pnp', 'pnp')),
        terminals=('c', 'b', 'e', 's'),
        geometry=(),
        params=(
            ParamRule("type", "fold", match=("npn", "pnp",), cite="ref5 p.51; §3.9: polarity -> Model.kind npn/pnp"),
            ParamRule("type", "error", cite="ref5 p.51; unknown polarity"),
            ParamRule("struct", "strip", "", "given:cjs", match=("absent",), warn="card", cite="ref5 p.51; ref5 p.51: pnp defaults to lateral; both targets simulate a vertical substrate junction (sp_bjt subs=1, bjt.va:90)"),
            ParamRule("struct", "strip", "", "given:iss", match=("absent",), warn="card", cite="ref5 p.51; as above"),
            ParamRule("struct", "strip", match=("absent", "vertical",), cite="ref5 p.51; vertical is the targets' structure"),
            ParamRule("struct", "error", match=("lateral",), cite="ref5 p.51; lateral: sp_bjt subs=-1 could, Xyce cannot; error in v1"),
            ParamRule("is", "pass", cite="ref5 p.51; bjt.va:93; N_DEV_BJT.C:127"),
            ParamRule("ise", "pass", cite="ref5 p.51; bjt.va:102; N_DEV_BJT.C:215"),
            ParamRule("isc", "pass", cite="ref5 p.51; bjt.va:110; N_DEV_BJT.C:331"),
            ParamRule("iss", "pass", cite="ref5 p.51; bjt.va:148; Xyce has no ISS: the output scan of the deck fails loudly (§7.2)"),
            ParamRule("c2", "pass", cite="ref5 p.51; bjt.va:103 (alias of ise); N_DEV_BJT.C:731"),
            ParamRule("c4", "pass", cite="ref5 p.51; bjt.va:111; N_DEV_BJT.C:739"),
            ParamRule("cbo", "strip", match=("0",), cite="ref5 p.51; B-C leakage model: no target"),
            ParamRule("cbo", "error", match=("nonzero",), cite="ref5 p.51; B-C leakage model: no target"),
            ParamRule("gbo", "strip", match=("0",), cite="ref5 p.51; B-C leakage model: no target"),
            ParamRule("gbo", "error", match=("nonzero",), cite="ref5 p.51; B-C leakage model: no target"),
            ParamRule("vbo", "strip", match=("0",), cite="ref5 p.51; B-C leakage model: no target"),
            ParamRule("vbo", "error", match=("nonzero",), cite="ref5 p.51; B-C leakage model: no target"),
            ParamRule("tcbo", "strip", match=("0",), cite="ref5 p.51; B-C leakage model: no target"),
            ParamRule("tcbo", "error", match=("nonzero",), cite="ref5 p.51; B-C leakage model: no target"),
            ParamRule("tgbo", "strip", match=("0",), cite="ref5 p.51; B-C leakage model: no target"),
            ParamRule("tgbo", "error", match=("nonzero",), cite="ref5 p.51; B-C leakage model: no target"),
            ParamRule("nf", "pass", cite="ref5 p.52; bjt.va:97; N_DEV_BJT.C:154"),
            ParamRule("nr", "pass", cite="ref5 p.52; bjt.va:106; N_DEV_BJT.C:267"),
            ParamRule("ne", "pass", cite="ref5 p.52; bjt.va:104; N_DEV_BJT.C:234"),
            ParamRule("nc", "pass", cite="ref5 p.52; bjt.va:112; N_DEV_BJT.C:348"),
            ParamRule("ns", "pass", cite="ref5 p.52; bjt.va:149; Xyce has no NS: the output scan of the deck fails loudly (§7.2)"),
            ParamRule("bf", "pass", cite="ref5 p.52; bjt.va:96; N_DEV_BJT.C:135"),
            ParamRule("br", "pass", cite="ref5 p.52; bjt.va:105; N_DEV_BJT.C:251"),
            ParamRule("ikf", "pass", cite="ref5 p.52; inf = the targets' 0 (off): bjt.va:100; N_DEV_BJT.C:190"),
            ParamRule("ikr", "pass", cite="ref5 p.52; bjt.va:109; N_DEV_BJT.C:312"),
            ParamRule("vaf", "pass", cite="ref5 p.52; inf = the targets' 0 (off): bjt.va:98; N_DEV_BJT.C:182"),
            ParamRule("var", "pass", cite="ref5 p.52; bjt.va:107; N_DEV_BJT.C:276"),
            ParamRule("ke", "strip", match=("0",), cite="ref5 p.52; space-charge integral multiplier: no target"),
            ParamRule("ke", "error", match=("nonzero",), cite="ref5 p.52; space-charge integral multiplier: no target"),
            ParamRule("kc", "strip", match=("0",), cite="ref5 p.52; no target"),
            ParamRule("kc", "error", match=("nonzero",), cite="ref5 p.52; no target"),
            ParamRule("rb", "pass", cite="ref5 p.52; bjt.va:113; N_DEV_BJT.C:355"),
            ParamRule("rbm", "pass", cite="ref5 p.52; rbm=rb when absent on both targets (bjt.va:807; SPICE3): bjt.va:115; N_DEV_BJT.C:390"),
            ParamRule("irb", "pass", cite="ref5 p.52; inf = 0 (off): bjt.va:114; N_DEV_BJT.C:364"),
            ParamRule("rbmod", "strip", match=("spice",), cite="ref5 p.53; spice is the targets' nonlinear base resistance"),
            ParamRule("rbmod", "error", cite="ref5 p.53; Spectre's own rb model: no target"),
            ParamRule("rc", "pass", cite="ref5 p.53; bjt.va:117; N_DEV_BJT.C:406"),
            ParamRule("rcv", "strip", match=("0",), cite="ref5 p.53; variable collector resistance (quasi-saturation): no target with these parameters"),
            ParamRule("rcv", "error", match=("nonzero",), cite="ref5 p.53; variable collector resistance (quasi-saturation): no target with these parameters"),
            ParamRule("rcm", "strip", match=("0",), cite="ref5 p.53; as rcv"),
            ParamRule("rcm", "error", match=("nonzero",), cite="ref5 p.53; as rcv"),
            *[ParamRule(n, "strip", cite="ref5 p.53; act only with rcv, which is an error when nonzero")
              for n in "dope cex cco".split()],
            ParamRule("re", "pass", cite="ref5 p.53; bjt.va:116; N_DEV_BJT.C:398"),
            ParamRule("minr", "strip", cite="ref5 p.53; minimum parasitic resistance: a Spectre numerical floor"),
            ParamRule("cje", "pass", cite="ref5 p.53; bjt.va:118; N_DEV_BJT.C:414"),
            ParamRule("vje", "pass", cite="ref5 p.53; bjt.va:119; N_DEV_BJT.C:424"),
            ParamRule("mje", "default", "1/3", "absent:mje", cite="ref5 p.53; ref5 mje=1/3 (p.53); the targets 0.33 (bjt.va:121; N_DEV_BJT.C:443)"),
            ParamRule("mje", "pass", cite="ref5 p.53; bjt.va:121; N_DEV_BJT.C:443"),
            ParamRule("cjc", "pass", cite="ref5 p.53; bjt.va:128; N_DEV_BJT.C:508"),
            ParamRule("vjc", "pass", cite="ref5 p.53; bjt.va:129; N_DEV_BJT.C:518"),
            ParamRule("mjc", "default", "1/3", "absent:mjc", cite="ref5 p.53; ref5 mjc=1/3 (p.53); the targets 0.33 (bjt.va:131; N_DEV_BJT.C:537)"),
            ParamRule("mjc", "pass", cite="ref5 p.53; bjt.va:131; N_DEV_BJT.C:537"),
            ParamRule("xcjc", "pass", cite="ref5 p.53; bjt.va:133; N_DEV_BJT.C:555"),
            ParamRule("xcjc2", "strip", match=("1",), cite="ref5 p.53; second B-C partition: no target"),
            ParamRule("xcjc2", "error", cite="ref5 p.53; second B-C partition: no target"),
            ParamRule("cjs", "pass", cite="ref5 p.53; bjt.va:135; N_DEV_BJT.C:580"),
            ParamRule("vjs", "pass", cite="ref5 p.53; bjt.va:138; N_DEV_BJT.C:608"),
            ParamRule("mjs", "pass", cite="ref5 p.54; 0 on both targets (bjt.va:140; N_DEV_BJT.C:635): not written"),
            ParamRule("fc", "default", "0.5", "absent:fc", cite="ref5 p.54; §3.9 table: ref5 fc=0.5 (p.54); sp_bjt fc=0 (bjt.va:145), Xyce 0.5 (N_DEV_BJT.C:723)"),
            ParamRule("fc", "pass", cite="ref5 p.54; bjt.va:145; N_DEV_BJT.C:723"),
            ParamRule("cbcp", "strip", match=("0",), cite="ref5 p.54; parasitic capacitances: no target"),
            ParamRule("cbcp", "error", match=("nonzero",), cite="ref5 p.54; parasitic capacitances: no target"),
            ParamRule("cbep", "strip", match=("0",), cite="ref5 p.54; parasitic capacitances: no target"),
            ParamRule("cbep", "error", match=("nonzero",), cite="ref5 p.54; parasitic capacitances: no target"),
            ParamRule("ccsp", "strip", match=("0",), cite="ref5 p.54; parasitic capacitances: no target"),
            ParamRule("ccsp", "error", match=("nonzero",), cite="ref5 p.54; parasitic capacitances: no target"),
            ParamRule("tf", "pass", cite="ref5 p.54; bjt.va:123; N_DEV_BJT.C:460"),
            ParamRule("td", "strip", match=("0",), cite="ref5 p.54; intrinsic base delay: no target"),
            ParamRule("td", "error", match=("nonzero",), cite="ref5 p.54; intrinsic base delay: no target"),
            ParamRule("xtf", "pass", cite="ref5 p.54; bjt.va:124; N_DEV_BJT.C:467"),
            ParamRule("vtf", "pass", cite="ref5 p.54; inf = 0 (off): bjt.va:125; N_DEV_BJT.C:474"),
            ParamRule("itf", "pass", cite="ref5 p.54; bjt.va:126; N_DEV_BJT.C:484"),
            ParamRule("tr", "pass", cite="ref5 p.54; bjt.va:134; N_DEV_BJT.C:571"),
            ParamRule("ptf", "pass", cite="ref5 p.54; bjt.va:127; N_DEV_BJT.C:501"),
            ParamRule("tnom", "pass", cite="ref5 p.54; bjt.va:91; N_DEV_BJT.C:120"),
            ParamRule("trise", "strip", match=("0",), cite="ref5 p.54; card default temperature rise: no target (the instance trise maps to dtemp)"),
            ParamRule("trise", "error", match=("nonzero",), cite="ref5 p.54; card default temperature rise: no target (the instance trise maps to dtemp)"),
            ParamRule("eg", "pass", cite="ref5 p.54; bjt.va:143; N_DEV_BJT.C:687 (1.11 on all three)"),
            ParamRule("xtb", "pass", cite="ref5 p.54; bjt.va:142; N_DEV_BJT.C:661"),
            ParamRule("xti", "pass", cite="ref5 p.54; bjt.va:144; N_DEV_BJT.C:696"),
            ParamRule("trb1", "pass", cite="ref5 p.54; sp_bjt (bjt.va:174-184); Xyce has none (fails loudly)"),
            ParamRule("trb2", "pass", cite="ref5 p.54; sp_bjt (bjt.va:174-184); Xyce has none (fails loudly)"),
            *[ParamRule(n, "pass", cite="ref5 p.55; sp_bjt (bjt.va:174-184); Xyce has none (fails loudly)")
              for n in "trm1 trm2 trc1 trc2 tre1 tre2".split()],
            ParamRule("tlev", "pass", cite="ref5 p.55; bjt.va:154; Xyce has no TLEV: the output scan of the deck fails loudly (§7.2)"),
            ParamRule("tlevc", "pass", cite="ref5 p.55; bjt.va:155; Xyce has no TLEVC: the output scan of the deck fails loudly (§7.2)"),
            ParamRule("gap1", "strip", match=("7.02e-4",), cite="ref5 p.55; band-gap temperature law: no target parameter"),
            ParamRule("gap1", "error", cite="ref5 p.55; band-gap temperature law: no target parameter"),
            ParamRule("gap2", "strip", match=("1108",), cite="ref5 p.55; as gap1"),
            ParamRule("gap2", "error", cite="ref5 p.55; as gap1"),
            *[ParamRule(n, "pass", cite="ref5 p.55; sp_bjt temperature coefficients (bjt.va:156-218); Xyce has none (fails loudly)")
              for n in "tikf1 tikf2 tikr1 tikr2 tirb1 tirb2 tis1 tis2 tise1 tise2 tisc1 tisc2".split()],
            *[ParamRule(n, "pass", cite="ref5 p.56; sp_bjt temperature coefficients (bjt.va:156-218); Xyce has none (fails loudly)")
              for n in "tiss1 tiss2 tbf1 tbf2 tbr1 tbr2 tvaf1 tvaf2 tvar1 tvar2 titf1 titf2 ttf1 ttf2 ttr1 ttr2 tnf1 tnf2 tnr1 tnr2 tne1 tne2".split()],
            *[ParamRule(n, "pass", cite="ref5 p.57; sp_bjt temperature coefficients (bjt.va:156-218); Xyce has none (fails loudly)")
              for n in "tnc1 tnc2 tns1 tns2 tmje1 tmje2 tmjc1 tmjc2 tmjs1 tmjs2 cte ctc cts tvje tvjc tvjs".split()],
            ParamRule("tvtf1", "strip", match=("0",), cite="ref5 p.57; no target"),
            ParamRule("tvtf1", "error", match=("nonzero",), cite="ref5 p.57; no target"),
            ParamRule("tvtf2", "strip", match=("0",), cite="ref5 p.57; no target"),
            ParamRule("tvtf2", "error", match=("nonzero",), cite="ref5 p.57; no target"),
            ParamRule("txtf1", "strip", match=("0",), cite="ref5 p.57; no target"),
            ParamRule("txtf1", "error", match=("nonzero",), cite="ref5 p.57; no target"),
            ParamRule("txtf2", "strip", match=("0",), cite="ref5 p.57; no target"),
            ParamRule("txtf2", "error", match=("nonzero",), cite="ref5 p.57; no target"),
            *[ParamRule(n, "strip", cite="ref5 p.58; convergence aids and operating-region warnings")
              for n in "dskip imelt bvbe bvbc bvce bvsub vbefwd vbcfwd vsubfwd imax imax1 alarm".split()],
            ParamRule("kf", "pass", cite="ref5 p.58; bjt.va:146; N_DEV_BJT.C:713"),
            ParamRule("af", "pass", cite="ref5 p.58; bjt.va:147; N_DEV_BJT.C:718"),
            ParamRule("kb", "strip", match=("0",), cite="ref5 p.58; burst noise: no target"),
            ParamRule("kb", "strip", warn="analyses=noise", cite="ref5 p.58; burst noise: no target"),
            ParamRule("bnoisefc", "strip", cite="ref5 p.58; burst noise corner (kb is stripped)"),
            ParamRule("rbnoi", "strip", warn="analyses=noise", cite="ref5 p.58; effective base noise resistance: the targets use rb"),
        ),
        instance=(
            ParamRule("area", "pass", cite="ref5 p.50; bjt.va:82; N_DEV_BJT.C:70"),
            ParamRule("areab", "pass", "", "given:area", match=("absent",), warn="card", cite="ref5 p.50; ref5 areab=1 (p.50) while the targets default it to area (bjt.va:83: 0 = area)"),
            ParamRule("areab", "pass", cite="ref5 p.50; bjt.va:83; Xyce has no AREAB: the output scan of the deck fails loudly (§7.2)"),
            ParamRule("areac", "pass", "", "given:area", match=("absent",), warn="card", cite="ref5 p.50; ref5 areac=1 (p.50) while the targets default it to area (bjt.va:84)"),
            ParamRule("areac", "pass", cite="ref5 p.50; bjt.va:84; Xyce has no AREAC: the output scan of the deck fails loudly (§7.2)"),
            ParamRule("m", "pass", cite="ref5 p.50; multiplier"),
            ParamRule("trise", "rename", "dtemp", cite="ref5 p.50; sp_bjt dtemp (bjt.va:86); Xyce DTEMP (N_DEV_BJT.C:97)"),
            ParamRule("region", "strip", cite="ref5 p.50; an initial operating-region hint"),
        ),
    ),
    # -- jfet [M ref5 pp.385-389] ----------------------------------------------------
    "jfet": MasterRow(
        "jfet", "j", 1, "type",
        kinds=(('n', 'njf'), ('p', 'pjf')),
        terminals=('d', 'g', 's', 'b'),
        geometry=(),
        params=(
            ParamRule("type", "fold", match=("n", "p",), cite="ref5 p.386; §3.9: polarity -> Model.kind njf/pjf"),
            ParamRule("type", "error", cite="ref5 p.386; unknown polarity"),
            ParamRule("level", "fold", match=("1",), cite="ref5 p.386; level 1 is the DISPATCH level (sp_jfet1, Xyce JFET)"),
            ParamRule("level", "error", cite="ref5 p.386; other jfet levels: not in v1"),
            ParamRule("vto", "pass", cite="ref5 p.386; jfet1.va:97 (alias of vt0, -2); N_DEV_JFET.C:148"),
            ParamRule("beta", "pass", cite="ref5 p.386; jfet1.va:98; N_DEV_JFET.C:90"),
            ParamRule("lambda", "pass", cite="ref5 p.386; jfet1.va:99; N_DEV_JFET.C:121"),
            ParamRule("lambda1", "strip", match=("0",), cite="ref5 p.386; gate dependence of lambda: no target"),
            ParamRule("lambda1", "error", match=("nonzero",), cite="ref5 p.386; gate dependence of lambda: no target"),
            ParamRule("np", "strip", match=("2",), cite="ref5 p.386; power-law exponent: 2 is the square law of the targets"),
            ParamRule("np", "error", cite="ref5 p.386; power-law exponent: 2 is the square law of the targets"),
            ParamRule("alpha", "strip", match=("2",), cite="ref5 p.386; triode-saturation transition shaping: no target"),
            ParamRule("alpha", "error", cite="ref5 p.386; triode-saturation transition shaping: no target"),
            ParamRule("io", "strip", match=("0",), cite="ref5 p.386; subthreshold current: no target"),
            ParamRule("io", "error", match=("nonzero",), cite="ref5 p.386; subthreshold current: no target"),
            ParamRule("ns", "strip", cite="ref5 p.386; subthreshold swing (io is stripped when 0, an error otherwise)"),
            ParamRule("ai", "strip", match=("0",), cite="ref5 p.386; impact ionization: no target"),
            ParamRule("ai", "error", match=("nonzero",), cite="ref5 p.386; impact ionization: no target"),
            ParamRule("bi", "strip", match=("0",), cite="ref5 p.386; impact ionization"),
            ParamRule("bi", "error", match=("nonzero",), cite="ref5 p.386; impact ionization"),
            *[ParamRule(n, "strip", cite="ref5 p.387; four-terminal threshold model; the fourth terminal is an error in v1 (§3.8)")
              for n in "vtop vtos vtoe vtoc".split()],
            ParamRule("rd", "pass", cite="ref5 p.387; jfet1.va:100; N_DEV_JFET.C:131"),
            ParamRule("rs", "pass", cite="ref5 p.387; jfet1.va:101; N_DEV_JFET.C:137"),
            ParamRule("rg", "strip", match=("0",), cite="ref5 p.387; gate resistance: no target"),
            ParamRule("rg", "error", match=("nonzero",), cite="ref5 p.387; gate resistance: no target"),
            ParamRule("rb", "strip", match=("0",), cite="ref5 p.387; back-gate resistance: no target"),
            ParamRule("rb", "error", match=("nonzero",), cite="ref5 p.387; back-gate resistance: no target"),
            ParamRule("minr", "strip", cite="ref5 p.387; a Spectre numerical floor"),
            ParamRule("is", "pass", cite="ref5 p.387; jfet1.va:105; N_DEV_JFET.C:112"),
            ParamRule("n", "pass", cite="ref5 p.387; jfet1.va:106; Xyce has no N: the output scan of the deck fails loudly (§7.2)"),
            ParamRule("imelt", "strip", cite="ref5 p.387; explosion current and convergence aid"),
            ParamRule("dskip", "strip", cite="ref5 p.387; explosion current and convergence aid"),
            ParamRule("tt", "strip", match=("0",), cite="ref5 p.387; transit time: neither JFET level-1 target has it"),
            ParamRule("tt", "error", match=("nonzero",), cite="ref5 p.387; transit time: neither JFET level-1 target has it"),
            ParamRule("cgs", "pass", cite="ref5 p.387; jfet1.va:102; N_DEV_JFET.C:95"),
            ParamRule("cgd", "pass", cite="ref5 p.387; jfet1.va:103; N_DEV_JFET.C:101"),
            ParamRule("mj", "strip", match=("1/2",), cite="ref5 p.388; grading coefficient: the targets' junctions use 1/2"),
            ParamRule("mj", "error", cite="ref5 p.388; grading coefficient: the targets' junctions use 1/2"),
            ParamRule("pb", "pass", cite="ref5 p.388; jfet1.va:104; N_DEV_JFET.C:126"),
            ParamRule("fc", "pass", cite="ref5 p.388; jfet1.va:107; N_DEV_JFET.C:107"),
            *[ParamRule(n, "strip", cite="ref5 p.388; four-terminal junction; the fourth terminal is an error in v1")
              for n in "isb nb cgbs cgbd mjb pbb".split()],
            ParamRule("tnom", "pass", cite="ref5 p.388; jfet1.va:109; N_DEV_JFET.C:143"),
            ParamRule("trise", "strip", match=("0",), cite="ref5 p.388; card default temperature rise: no target"),
            ParamRule("trise", "error", match=("nonzero",), cite="ref5 p.388; card default temperature rise: no target"),
            ParamRule("xti", "pass", cite="ref5 p.388; jfet1.va:114; Xyce has no XTI: the output scan of the deck fails loudly (§7.2)"),
            ParamRule("tlev", "strip", match=("0",), cite="ref5 p.388; temperature equation selector: no target"),
            ParamRule("tlev", "error", match=("nonzero",), cite="ref5 p.388; temperature equation selector: no target"),
            ParamRule("tlevc", "strip", match=("0",), cite="ref5 p.388; no target"),
            ParamRule("tlevc", "error", match=("nonzero",), cite="ref5 p.388; no target"),
            ParamRule("eg", "strip", match=("absent",), warn="temp!=tnom", cite="ref5 p.388; ref5 eg=1.12452 (p.388); sp_jfet1 eg=1.11 (jfet1.va:115), Xyce's JFET has no EG: an approximation at T != tnom"),
            ParamRule("eg", "pass", cite="ref5 p.388; jfet1.va:115; Xyce has no EG: the output scan of the deck fails loudly (§7.2)"),
            ParamRule("gap1", "strip", match=("7.02e-4",), cite="ref5 p.388; band-gap law: no target"),
            ParamRule("gap1", "error", cite="ref5 p.388; band-gap law: no target"),
            ParamRule("gap2", "strip", match=("1108",), cite="ref5 p.388; as gap1"),
            ParamRule("gap2", "error", cite="ref5 p.388; as gap1"),
            ParamRule("tcv", "pass", cite="ref5 p.388; jfet1.va:110; Xyce has no TCV: the output scan of the deck fails loudly (§7.2)"),
            ParamRule("bto", "strip", match=("0",), cite="ref5 p.388; beta/lambda temperature laws: no target"),
            ParamRule("bto", "error", match=("nonzero",), cite="ref5 p.388; beta/lambda temperature laws: no target"),
            ParamRule("bte", "strip", match=("0",), cite="ref5 p.389; beta/lambda temperature laws: no target"),
            ParamRule("bte", "error", match=("nonzero",), cite="ref5 p.389; beta/lambda temperature laws: no target"),
            ParamRule("lto", "strip", match=("0",), cite="ref5 p.389; beta/lambda temperature laws: no target"),
            ParamRule("lto", "error", match=("nonzero",), cite="ref5 p.389; beta/lambda temperature laws: no target"),
            ParamRule("lte", "strip", match=("0",), cite="ref5 p.389; beta/lambda temperature laws: no target"),
            ParamRule("lte", "error", match=("nonzero",), cite="ref5 p.389; beta/lambda temperature laws: no target"),
            ParamRule("tc1", "strip", match=("0",), cite="ref5 p.389; parasitic resistance tempco: no target"),
            ParamRule("tc1", "error", match=("nonzero",), cite="ref5 p.389; parasitic resistance tempco: no target"),
            ParamRule("tc2", "strip", match=("0",), cite="ref5 p.389; as tc1"),
            ParamRule("tc2", "error", match=("nonzero",), cite="ref5 p.389; as tc1"),
            *[ParamRule(n, "strip", cite="ref5 p.389; operating-region warnings")
              for n in "alarm imax bvj".split()],
            ParamRule("kf", "default", "0", "absent:kf", cite="ref5 p.389; ref5 kf=0 (p.389), sp_jfet1 0 (jfet1.va:116); Xyce's JFET defaults KF to 0.05 (N_DEV_JFET.C:117)"),
            ParamRule("kf", "pass", cite="ref5 p.389; jfet1.va:116; N_DEV_JFET.C:117"),
            ParamRule("af", "pass", cite="ref5 p.389; jfet1.va:117; N_DEV_JFET.C:81"),
            ParamRule("kfd", "strip", match=("0",), cite="ref5 p.389; gate-diode flicker noise: no target"),
            ParamRule("kfd", "strip", warn="analyses=noise", cite="ref5 p.389; gate-diode flicker noise: no target"),
            ParamRule("afg", "strip", cite="ref5 p.389; exponent of kfd"),
        ),
        instance=(
            ParamRule("area", "pass", cite="ref5 p.385; jfet1.va:90; N_DEV_JFET.C:68"),
            ParamRule("m", "pass", cite="ref5 p.385; multiplier"),
            ParamRule("region", "strip", cite="ref5 p.386; an initial operating-region hint"),
        ),
    ),
    # -- mos1 [M ref5 pp.409-419] ----------------------------------------------------
    "mos1": MasterRow(
        "mos1", "m", 1, "type",
        kinds=(('n', 'nmos'), ('p', 'pmos')),
        terminals=('d', 'g', 's', 'b'),
        geometry=(('w', 3e-06), ('l', 3e-06), ('lmin', 0.0), ('lmax', 1.0), ('wmin', 0.0), ('wmax', 1.0)),
        params=(
            ParamRule("type", "fold", match=("n", "p",), cite="ref5 p.410; §3.9: polarity -> Model.kind nmos/pmos"),
            ParamRule("type", "error", cite="ref5 p.410; unknown polarity"),
            ParamRule("vto", "default", "0", "absent:vto,absent:nsub", cite="ref5 p.410; §3.9 nsub row: pins what the targets would derive from the written nsub"),
            ParamRule("vto", "pass", "", "absent:vto,given:nsub", match=("absent",), warn="card", cite="ref5 p.410; §3.9: the targets derive vto from the given nsub (SPICE3)"),
            ParamRule("vto", "pass", cite="ref5 p.410; mos1.va vto; N_DEV_MOSFET1.C VTO"),
            ParamRule("kp", "pass", cite="ref5 p.410; ref5 2.0718e-5 = uo*cox at tox=1e-7, which the targets derive when kp is absent (mos1.va; N_DEV_MOSFET1.C)"),
            ParamRule("lambda", "pass", cite="ref5 p.410; mos1.va lambda; N_DEV_MOSFET1.C LAMBDA"),
            ParamRule("phi", "default", "0.7", "absent:phi,absent:nsub", cite="ref5 p.410; §3.9 nsub row: ref5 phi=0.7; the targets 0.6 (mos1.va; N_DEV_MOSFET1.C)"),
            ParamRule("phi", "pass", "", "absent:phi,given:nsub", match=("absent",), warn="card", cite="ref5 p.410; §3.9: the targets derive phi from the given nsub (SPICE3)"),
            ParamRule("phi", "pass", cite="ref5 p.410; mos1.va phi"),
            ParamRule("gamma", "default", "0", "absent:gamma,absent:nsub", cite="ref5 p.410; §3.9 nsub row: pins what the targets would derive from the written nsub"),
            ParamRule("gamma", "pass", "", "absent:gamma,given:nsub", match=("absent",), warn="card", cite="ref5 p.410; §3.9: the targets derive gamma from the given nsub (SPICE3)"),
            ParamRule("gamma", "pass", cite="ref5 p.410; mos1.va gamma"),
            ParamRule("uo", "pass", cite="ref5 p.410; mos1.va u0 (alias uo); N_DEV_MOSFET1.C UO"),
            ParamRule("vmax", "error", cite="ref5 p.410; velocity saturation in Spectre's mos1: no target (SPICE3 level 1 has none)"),
            ParamRule("theta", "strip", match=("0",), cite="ref5 p.410; mobility modulation in Spectre's mos1: no target"),
            ParamRule("theta", "error", match=("nonzero",), cite="ref5 p.410; mobility modulation in Spectre's mos1: no target"),
            ParamRule("nsub", "default", "1.13e16", "absent:nsub", cite="ref5 p.410; §3.9 table: ref5 nsub=1.13e16; the targets 0 = not given (mos1.va; N_DEV_MOSFET1.C) [E95]"),
            ParamRule("nsub", "pass", cite="ref5 p.410; mos1.va nsub; N_DEV_MOSFET1.C NSUB"),
            ParamRule("nss", "pass", cite="ref5 p.410; mos1.va nss; N_DEV_MOSFET1.C NSS"),
            ParamRule("nfs", "strip", match=("0",), cite="ref5 p.410; fast surface states in Spectre's mos1: no level-1 target"),
            ParamRule("nfs", "error", match=("nonzero",), cite="ref5 p.410; fast surface states in Spectre's mos1: no level-1 target"),
            ParamRule("tpg", "default", "1", "absent:tpg", cite="ref5 p.411; ref5 tpg=+1 (SPICE3's default, mos1.va tpg=1); Xyce's MOSFET1 defaults TPG to 0 (N_DEV_MOSFET1.C)"),
            ParamRule("tpg", "pass", cite="ref5 p.411; mos1.va tpg; N_DEV_MOSFET1.C TPG"),
            ParamRule("ld", "pass", cite="ref5 p.411; mos1.va ld; N_DEV_MOSFET1.C LD"),
            ParamRule("wd", "strip", match=("0",), cite="ref5 p.411; width/length adjustments: no target at this level"),
            ParamRule("wd", "error", match=("nonzero",), cite="ref5 p.411; width/length adjustments: no target at this level"),
            ParamRule("xw", "strip", match=("0",), cite="ref5 p.411; width/length adjustments: no target at this level"),
            ParamRule("xw", "error", match=("nonzero",), cite="ref5 p.411; width/length adjustments: no target at this level"),
            ParamRule("xl", "strip", match=("0",), cite="ref5 p.411; width/length adjustments: no target at this level"),
            ParamRule("xl", "error", match=("nonzero",), cite="ref5 p.411; width/length adjustments: no target at this level"),
            ParamRule("tox", "default", "1e-7", "absent:tox", cite="ref5 p.411; §3.9 table: ref5 tox=1e-7; sp_mos1 has no tox unless given (mos1.va:741-747), Xyce's TOX is 1e-7"),
            ParamRule("tox", "pass", cite="ref5 p.411; mos1.va tox; N_DEV_MOSFET1.C TOX"),
            ParamRule("ai0", "strip", match=("0",), cite="ref5 p.411; impact ionization: no target"),
            ParamRule("ai0", "error", match=("nonzero",), cite="ref5 p.411; impact ionization: no target"),
            ParamRule("lai0", "strip", match=("0",), cite="ref5 p.411; impact ionization: no target"),
            ParamRule("lai0", "error", match=("nonzero",), cite="ref5 p.411; impact ionization: no target"),
            ParamRule("wai0", "strip", match=("0",), cite="ref5 p.411; impact ionization: no target"),
            ParamRule("wai0", "error", match=("nonzero",), cite="ref5 p.411; impact ionization: no target"),
            ParamRule("bi0", "strip", match=("0",), cite="ref5 p.411; impact ionization: no target"),
            ParamRule("bi0", "error", match=("nonzero",), cite="ref5 p.411; impact ionization: no target"),
            ParamRule("lbi0", "strip", match=("0",), cite="ref5 p.411; impact ionization: no target"),
            ParamRule("lbi0", "error", match=("nonzero",), cite="ref5 p.411; impact ionization: no target"),
            ParamRule("wbi0", "strip", match=("0",), cite="ref5 p.411; impact ionization: no target"),
            ParamRule("wbi0", "error", match=("nonzero",), cite="ref5 p.411; impact ionization: no target"),
            *[ParamRule(n, "pass", cite="ref5 p.411; mos1.va; N_DEV_MOSFET1.C")
              for n in "cgso cgdo cgbo".split()],
            ParamRule("meto", "strip", match=("0",), cite="ref5 p.411; metal overlap in the fringing capacitance: no target"),
            ParamRule("meto", "error", match=("nonzero",), cite="ref5 p.411; metal overlap in the fringing capacitance: no target"),
            ParamRule("capmod", "strip", match=("absent", "bsim",), warn="analyses=ac,noise,xf,tran", cite="ref5 p.411; §3.9: Spectre's default charge model is bsim; both targets have only Meyer's charge"),
            ParamRule("capmod", "strip", match=("meyer",), cite="ref5 p.411; §3.9: meyer is the targets' charge model [I, §14 q.45]"),
            ParamRule("capmod", "error", match=("none", "yang",), cite="ref5 p.411; §3.9: none and yang -> error"),
            ParamRule("xpart", "strip", cite="ref5 p.412; charge partition of the bsim charge model (capmod)"),
            ParamRule("xqc", "strip", cite="ref5 p.412; as xpart"),
            ParamRule("rs", "pass", cite="ref5 p.412; mos1.va rs; N_DEV_MOSFET1.C RS"),
            ParamRule("rd", "pass", cite="ref5 p.412; mos1.va rd; N_DEV_MOSFET1.C RD"),
            ParamRule("rss", "strip", match=("0",), cite="ref5 p.412; scalable source resistance: no target"),
            ParamRule("rss", "error", match=("nonzero",), cite="ref5 p.412; scalable source resistance: no target"),
            ParamRule("rdd", "strip", match=("0",), cite="ref5 p.412; scalable drain resistance: no target"),
            ParamRule("rdd", "error", match=("nonzero",), cite="ref5 p.412; scalable drain resistance: no target"),
            ParamRule("rsh", "pass", cite="ref5 p.412; mos1.va rsh; N_DEV_MOSFET1.C RSH"),
            ParamRule("rsc", "strip", match=("0",), cite="ref5 p.412; contact resistance: no target"),
            ParamRule("rsc", "error", match=("nonzero",), cite="ref5 p.412; contact resistance: no target"),
            ParamRule("rdc", "strip", match=("0",), cite="ref5 p.412; contact resistance: no target"),
            ParamRule("rdc", "error", match=("nonzero",), cite="ref5 p.412; contact resistance: no target"),
            ParamRule("minr", "strip", cite="ref5 p.412; a Spectre numerical floor"),
            ParamRule("ldif", "strip", match=("0",), cite="ref5 p.412; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("ldif", "error", match=("nonzero",), cite="ref5 p.412; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("hdif", "strip", match=("0",), cite="ref5 p.412; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("hdif", "error", match=("nonzero",), cite="ref5 p.412; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("lgcs", "strip", match=("0",), cite="ref5 p.412; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("lgcs", "error", match=("nonzero",), cite="ref5 p.412; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("lgcd", "strip", match=("0",), cite="ref5 p.412; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("lgcd", "error", match=("nonzero",), cite="ref5 p.412; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("sc", "error", cite="ref5 p.412; contact spacing: no target"),
            ParamRule("js", "pass", cite="ref5 p.412; mos1.va js (A/m2); N_DEV_MOSFET1.C JS"),
            ParamRule("is", "pass", cite="ref5 p.412; mos1.va is; N_DEV_MOSFET1.C IS"),
            ParamRule("n", "strip", match=("1",), cite="ref5 p.412; junction emission coefficient: the targets' SPICE3 junctions use 1"),
            ParamRule("n", "error", cite="ref5 p.412; junction emission coefficient: the targets' SPICE3 junctions use 1"),
            *[ParamRule(n, "strip", cite="ref5 p.413; convergence aid and explosion currents")
              for n in "dskip imelt jmelt".split()],
            *[ParamRule(n, "pass", cite="ref5 p.413; mos1.va; N_DEV_MOSFET1.C")
              for n in "cbs cbd cj mj pb fc cjsw".split()],
            ParamRule("mjsw", "default", "1/3", "absent:mjsw", cite="ref5 p.413; §3.9 table: ref5 mjsw=1/3; the targets 0.5 (sp_mos1, MOSFET1/2) or 0.33 (sp_mos2/3, MOSFET3) [E95]"),
            ParamRule("mjsw", "pass", cite="ref5 p.413; mos1.va mjsw; N_DEV_MOSFET1.C MJSW"),
            ParamRule("pbsw", "strip", warn="card", cite="ref5 p.413; sidewall potential: the targets use pb for the sidewall"),
            ParamRule("fcsw", "strip", warn="card", cite="ref5 p.413; sidewall forward-bias threshold: the targets use fc for the sidewall"),
            *[ParamRule(n, "strip", cite="ref5 p.413; operating-region warnings")
              for n in "alarm imax jmax".split()],
            ParamRule("bvj", "strip", cite="ref5 p.414; operating-region warnings"),
            ParamRule("vbox", "strip", cite="ref5 p.414; operating-region warnings"),
            ParamRule("tnom", "pass", cite="ref5 p.414; mos1.va tnom; N_DEV_MOSFET1.C TNOM"),
            ParamRule("trise", "strip", match=("0",), cite="ref5 p.414; card default temperature rise: no target (the instance trise maps to dtemp)"),
            ParamRule("trise", "error", match=("nonzero",), cite="ref5 p.414; card default temperature rise: no target (the instance trise maps to dtemp)"),
            ParamRule("uto", "strip", match=("0",), cite="ref5 p.414; mobility temperature offset: no target"),
            ParamRule("uto", "error", match=("nonzero",), cite="ref5 p.414; mobility temperature offset: no target"),
            ParamRule("ute", "strip", match=("-1.5",), cite="ref5 p.414; mobility temperature exponent: SPICE3's fixed -1.5"),
            ParamRule("ute", "error", cite="ref5 p.414; mobility temperature exponent: SPICE3's fixed -1.5"),
            ParamRule("tlev", "strip", match=("0",), cite="ref5 p.414; temperature equation selector: no target"),
            ParamRule("tlev", "error", match=("nonzero",), cite="ref5 p.414; temperature equation selector: no target"),
            ParamRule("tlevc", "strip", match=("0",), cite="ref5 p.414; no target"),
            ParamRule("tlevc", "error", match=("nonzero",), cite="ref5 p.414; no target"),
            ParamRule("eg", "strip", match=("absent", "nonzero",), warn="temp!=tnom", cite="ref5 p.414; ref5 eg=1.12452; SPICE3's mos1-3 compute Eg(T) themselves (1.1151 V at 27 C): an approximation at T != tnom"),
            ParamRule("gap1", "strip", match=("7.02e-4",), cite="ref5 p.414; band-gap law: no target parameter"),
            ParamRule("gap1", "error", cite="ref5 p.414; band-gap law: no target parameter"),
            ParamRule("gap2", "strip", match=("1108",), cite="ref5 p.414; as gap1"),
            ParamRule("gap2", "error", cite="ref5 p.414; as gap1"),
            ParamRule("f1ex", "strip", match=("0",), cite="ref5 p.414; temperature coefficients: no target at this level"),
            ParamRule("f1ex", "error", match=("nonzero",), cite="ref5 p.414; temperature coefficients: no target at this level"),
            ParamRule("lamex", "strip", match=("0",), cite="ref5 p.414; temperature coefficients: no target at this level"),
            ParamRule("lamex", "error", match=("nonzero",), cite="ref5 p.414; temperature coefficients: no target at this level"),
            ParamRule("trs", "strip", match=("0",), cite="ref5 p.414; temperature coefficients: no target at this level"),
            ParamRule("trs", "error", match=("nonzero",), cite="ref5 p.414; temperature coefficients: no target at this level"),
            ParamRule("trd", "strip", match=("0",), cite="ref5 p.414; temperature coefficients: no target at this level"),
            ParamRule("trd", "error", match=("nonzero",), cite="ref5 p.414; temperature coefficients: no target at this level"),
            ParamRule("ptc", "strip", match=("0",), cite="ref5 p.414; temperature coefficients: no target at this level"),
            ParamRule("ptc", "error", match=("nonzero",), cite="ref5 p.414; temperature coefficients: no target at this level"),
            ParamRule("tcv", "strip", match=("0",), cite="ref5 p.414; temperature coefficients: no target at this level"),
            ParamRule("tcv", "error", match=("nonzero",), cite="ref5 p.414; temperature coefficients: no target at this level"),
            ParamRule("pta", "strip", match=("0",), cite="ref5 p.414; temperature coefficients: no target at this level"),
            ParamRule("pta", "error", match=("nonzero",), cite="ref5 p.414; temperature coefficients: no target at this level"),
            ParamRule("ptp", "strip", match=("0",), cite="ref5 p.414; temperature coefficients: no target at this level"),
            ParamRule("ptp", "error", match=("nonzero",), cite="ref5 p.414; temperature coefficients: no target at this level"),
            ParamRule("cta", "strip", match=("0",), cite="ref5 p.414; temperature coefficients: no target at this level"),
            ParamRule("cta", "error", match=("nonzero",), cite="ref5 p.414; temperature coefficients: no target at this level"),
            ParamRule("ctp", "strip", match=("0",), cite="ref5 p.415; temperature coefficients: no target at this level"),
            ParamRule("ctp", "error", match=("nonzero",), cite="ref5 p.415; temperature coefficients: no target at this level"),
            ParamRule("xti", "strip", match=("absent", "nonzero",), warn="temp!=tnom", cite="ref5 p.414; ref5 xti=3; SPICE3's mos1-3 junction saturation current has no xti term: an approximation at T != tnom"),
            ParamRule("w", "fold", cite="ref5 p.415; §3.9 default MOS geometry: folded into instances; a mod= sweep of it is refused"),
            ParamRule("l", "fold", cite="ref5 p.415; §3.9 default MOS geometry"),
            ParamRule("as", "fold", cite="ref5 p.415; ref5 'Default instance parameters': folded into instances as w/l (§3.9)"),
            ParamRule("ad", "fold", cite="ref5 p.415; as as"),
            ParamRule("ps", "fold", cite="ref5 p.415; as as"),
            ParamRule("pd", "fold", cite="ref5 p.415; as as"),
            ParamRule("nrd", "fold", cite="ref5 p.415; ref5 default 0: folded into instances; the targets' instance default is 1 (see the instance rules)"),
            ParamRule("nrs", "fold", cite="ref5 p.415; as nrd"),
            ParamRule("ldd", "strip", match=("0",), cite="ref5 p.415; default drain diffusion length: no target"),
            ParamRule("ldd", "error", match=("nonzero",), cite="ref5 p.415; default drain diffusion length: no target"),
            ParamRule("lds", "strip", match=("0",), cite="ref5 p.415; no target"),
            ParamRule("lds", "error", match=("nonzero",), cite="ref5 p.415; no target"),
            ParamRule("noisemod", "strip", match=("1",), cite="ref5 p.415; noise model selector: the targets' SPICE2 flicker noise (nlev)"),
            ParamRule("noisemod", "error", cite="ref5 p.415; noise model selector: the targets' SPICE2 flicker noise (nlev)"),
            ParamRule("kf", "pass", warn="analyses=noise", cite="ref5 p.415; mos1.va kf; N_DEV_MOSFET1.C KF; Spectre normalizes its flicker noise by wnoi (ref5), the targets do not"),
            ParamRule("af", "pass", cite="ref5 p.415; mos1.va af; N_DEV_MOSFET1.C AF"),
            ParamRule("ef", "strip", match=("1",), cite="ref5 p.415; flicker frequency exponent: no target"),
            ParamRule("ef", "strip", warn="analyses=noise", cite="ref5 p.415; flicker frequency exponent: no target"),
            ParamRule("wnoi", "strip", match=("1e-5",), cite="ref5 p.415; noise reference width: no target"),
            ParamRule("wnoi", "strip", warn="analyses=noise", cite="ref5 p.415; noise reference width: no target"),
            ParamRule("wmax", "pass", cite="ref5 p.415; model-group bounds (bin_bounds_spectre, §4.4)"),
            ParamRule("wmin", "pass", cite="ref5 p.415; model-group bounds (bin_bounds_spectre, §4.4)"),
            ParamRule("lmax", "pass", cite="ref5 p.416; model-group bounds (bin_bounds_spectre, §4.4)"),
            ParamRule("lmin", "pass", cite="ref5 p.416; model-group bounds (bin_bounds_spectre, §4.4)"),
            ParamRule("degramod", "strip", cite="ref5 p.416; degradation model selector (degradation=yes is an error)"),
            ParamRule("degradation", "strip", match=("no",), cite="ref5 p.416; no hot-electron degradation"),
            ParamRule("degradation", "error", match=("yes",), cite="ref5 p.416; hot-electron degradation: not in v1"),
            *[ParamRule(n, "strip", cite="ref5 p.416; degradation coefficients (degradation=yes is an error)")
              for n in "dvthc dvthe duoc duoe crivth criuo crigm criids wnom lnom vbsn vdsni vgsni vdsng vgsng".split()],
            *[ParamRule(n, "strip", cite="ref5 p.417; Spectre stress parameters (degradation=yes is an error)")
              for n in "esat esatg vpg vpb subc1 subc2 sube strc stre".split()],
            *[ParamRule(n, "strip", cite="ref5 p.417; BERT stress parameters (degradation=yes is an error)")
              for n in "h0 hgd m0 mgd ecrit0 lecrit0 wecrit0 ecritg lecritg wecritg ecritb".split()],
            *[ParamRule(n, "strip", cite="ref5 p.418; BERT stress parameters (degradation=yes is an error)")
              for n in "lecritb wecritb lc0 llc0 wlc0 lc1 llc1 wlc1 lc2 llc2 wlc2 lc3 llc3 wlc3 lc4 llc4 wlc4 lc5 llc5 wlc5 lc6 llc6".split()],
            *[ParamRule(n, "strip", cite="ref5 p.419; BERT stress parameters (degradation=yes is an error)")
              for n in "wlc6 lc7 llc7 wlc7".split()],
        ),
        instance=(
            ParamRule("w", "default", "3e-6", "absent:w", cite="ref5 p.409; §3.9 default MOS geometry (ref5 'Default channel width')"),
            ParamRule("w", "pass", cite="ref5 p.409; mos1.va w; N_DEV_MOSFET1.C W"),
            ParamRule("l", "default", "3e-6", "absent:l", cite="ref5 p.409; §3.9 default MOS geometry"),
            ParamRule("l", "pass", cite="ref5 p.409; mos1.va l; N_DEV_MOSFET1.C L"),
            *[ParamRule(n, "pass", cite="ref5 p.409; mos1.va; N_DEV_MOSFET1.C")
              for n in "as ad ps pd".split()],
            ParamRule("nrd", "default", "0", "absent:nrd", cite="ref5 p.409; ref5 card default nrd=0; the targets' instance default is 1 (mos1.va nrd; N_DEV_MOSFET1.C NRD)"),
            ParamRule("nrd", "pass", cite="ref5 p.409; mos1.va nrd; N_DEV_MOSFET1.C NRD"),
            ParamRule("nrs", "default", "0", "absent:nrs", cite="ref5 p.409; as nrd"),
            ParamRule("nrs", "pass", cite="ref5 p.409; mos1.va nrs; N_DEV_MOSFET1.C NRS"),
            ParamRule("ld", "strip", match=("0",), cite="ref5 p.409; drain diffusion length (instance): no target"),
            ParamRule("ld", "error", match=("nonzero",), cite="ref5 p.409; drain diffusion length (instance): no target"),
            ParamRule("ls", "strip", match=("0",), cite="ref5 p.409; no target"),
            ParamRule("ls", "error", match=("nonzero",), cite="ref5 p.409; no target"),
            ParamRule("m", "pass", cite="ref5 p.409; multiplier"),
            ParamRule("region", "strip", cite="ref5 p.409; an initial operating-region hint"),
            ParamRule("trise", "rename", "dtemp", cite="ref5 p.409; sp_mos1 dtemp; Xyce DTEMP"),
            ParamRule("degradation", "strip", match=("no",), cite="ref5 p.410"),
            ParamRule("degradation", "error", match=("yes",), cite="ref5 p.410; hot-electron degradation: not in v1"),
        ),
    ),
    # -- mos2 [M ref5 pp.487-498] ----------------------------------------------------
    "mos2": MasterRow(
        "mos2", "m", 2, "type",
        kinds=(('n', 'nmos'), ('p', 'pmos')),
        terminals=('d', 'g', 's', 'b'),
        geometry=(('w', 3e-06), ('l', 3e-06), ('lmin', 0.0), ('lmax', 1.0), ('wmin', 0.0), ('wmax', 1.0)),
        params=(
            ParamRule("type", "fold", match=("n", "p",), cite="ref5 p.488; §3.9: polarity -> Model.kind nmos/pmos"),
            ParamRule("type", "error", cite="ref5 p.488; unknown polarity"),
            ParamRule("vto", "default", "0", "absent:vto,absent:nsub", cite="ref5 p.488; §3.9 nsub row: pins what the targets would derive from the written nsub"),
            ParamRule("vto", "pass", "", "absent:vto,given:nsub", match=("absent",), warn="card", cite="ref5 p.488; §3.9: the targets derive vto from the given nsub (SPICE3)"),
            ParamRule("vto", "pass", cite="ref5 p.488; mos2.va vto; N_DEV_MOSFET2.C VTO"),
            ParamRule("kp", "pass", cite="ref5 p.488; ref5 2.0718e-5 = uo*cox at tox=1e-7, which the targets derive when kp is absent (mos2.va; N_DEV_MOSFET2.C)"),
            ParamRule("lambda", "pass", cite="ref5 p.488; mos2.va lambda; N_DEV_MOSFET2.C LAMBDA"),
            ParamRule("phi", "default", "0.7", "absent:phi,absent:nsub", cite="ref5 p.488; §3.9 nsub row: ref5 phi=0.7; the targets 0.6 (mos2.va; N_DEV_MOSFET2.C)"),
            ParamRule("phi", "pass", "", "absent:phi,given:nsub", match=("absent",), warn="card", cite="ref5 p.488; §3.9: the targets derive phi from the given nsub (SPICE3)"),
            ParamRule("phi", "pass", cite="ref5 p.488; mos2.va phi"),
            ParamRule("gamma", "default", "0", "absent:gamma,absent:nsub", cite="ref5 p.488; §3.9 nsub row: pins what the targets would derive from the written nsub"),
            ParamRule("gamma", "pass", "", "absent:gamma,given:nsub", match=("absent",), warn="card", cite="ref5 p.488; §3.9: the targets derive gamma from the given nsub (SPICE3)"),
            ParamRule("gamma", "pass", cite="ref5 p.488; mos2.va gamma"),
            ParamRule("uo", "pass", cite="ref5 p.488; mos2.va u0 (alias uo); N_DEV_MOSFET2.C UO"),
            ParamRule("vmax", "pass", cite="ref5 p.489; inf = the targets' 0 (off): mos2.va vmax; N_DEV_MOSFET2.C VMAX"),
            ParamRule("nsub", "default", "1.13e16", "absent:nsub", cite="ref5 p.489; §3.9 table: ref5 nsub=1.13e16; the targets 0 = not given (mos2.va; N_DEV_MOSFET2.C) [E95]"),
            ParamRule("nsub", "pass", cite="ref5 p.489; mos2.va nsub; N_DEV_MOSFET2.C NSUB"),
            ParamRule("nss", "pass", cite="ref5 p.489; mos2.va nss; N_DEV_MOSFET2.C NSS"),
            ParamRule("nfs", "pass", cite="ref5 p.489; mos2.va nfs; N_DEV_MOSFET2.C NFS"),
            ParamRule("tpg", "default", "1", "absent:tpg", cite="ref5 p.489; ref5 tpg=+1 (SPICE3's default, mos2.va tpg=1); Xyce's MOSFET2 defaults TPG to 0 (N_DEV_MOSFET2.C)"),
            ParamRule("tpg", "pass", cite="ref5 p.489; mos2.va tpg; N_DEV_MOSFET2.C TPG"),
            ParamRule("ld", "pass", cite="ref5 p.489; mos2.va ld; N_DEV_MOSFET2.C LD"),
            ParamRule("wd", "strip", match=("0",), cite="ref5 p.489; width/length adjustments: no target at this level"),
            ParamRule("wd", "error", match=("nonzero",), cite="ref5 p.489; width/length adjustments: no target at this level"),
            ParamRule("xw", "strip", match=("0",), cite="ref5 p.489; width/length adjustments: no target at this level"),
            ParamRule("xw", "error", match=("nonzero",), cite="ref5 p.489; width/length adjustments: no target at this level"),
            ParamRule("xl", "strip", match=("0",), cite="ref5 p.489; width/length adjustments: no target at this level"),
            ParamRule("xl", "error", match=("nonzero",), cite="ref5 p.489; width/length adjustments: no target at this level"),
            ParamRule("tox", "pass", cite="ref5 p.489; mos2.va tox; N_DEV_MOSFET2.C TOX"),
            ParamRule("ai0", "strip", match=("0",), cite="ref5 p.490; impact ionization: no target"),
            ParamRule("ai0", "error", match=("nonzero",), cite="ref5 p.490; impact ionization: no target"),
            ParamRule("lai0", "strip", match=("0",), cite="ref5 p.490; impact ionization: no target"),
            ParamRule("lai0", "error", match=("nonzero",), cite="ref5 p.490; impact ionization: no target"),
            ParamRule("wai0", "strip", match=("0",), cite="ref5 p.490; impact ionization: no target"),
            ParamRule("wai0", "error", match=("nonzero",), cite="ref5 p.490; impact ionization: no target"),
            ParamRule("bi0", "strip", match=("0",), cite="ref5 p.490; impact ionization: no target"),
            ParamRule("bi0", "error", match=("nonzero",), cite="ref5 p.490; impact ionization: no target"),
            ParamRule("lbi0", "strip", match=("0",), cite="ref5 p.490; impact ionization: no target"),
            ParamRule("lbi0", "error", match=("nonzero",), cite="ref5 p.490; impact ionization: no target"),
            ParamRule("wbi0", "strip", match=("0",), cite="ref5 p.490; impact ionization: no target"),
            ParamRule("wbi0", "error", match=("nonzero",), cite="ref5 p.490; impact ionization: no target"),
            *[ParamRule(n, "pass", cite="ref5 p.490; mos2.va; N_DEV_MOSFET2.C")
              for n in "cgso cgdo cgbo".split()],
            ParamRule("meto", "strip", match=("0",), cite="ref5 p.490; metal overlap in the fringing capacitance: no target"),
            ParamRule("meto", "error", match=("nonzero",), cite="ref5 p.490; metal overlap in the fringing capacitance: no target"),
            ParamRule("capmod", "strip", match=("absent", "bsim",), warn="analyses=ac,noise,xf,tran", cite="ref5 p.490; §3.9: Spectre's default charge model is bsim; both targets have only Meyer's charge"),
            ParamRule("capmod", "strip", match=("meyer",), cite="ref5 p.490; §3.9: meyer is the targets' charge model [I, §14 q.45]"),
            ParamRule("capmod", "error", match=("none", "yang",), cite="ref5 p.490; §3.9: none and yang -> error"),
            ParamRule("xpart", "strip", cite="ref5 p.490; charge partition of the bsim charge model (capmod)"),
            ParamRule("xqc", "strip", cite="ref5 p.490; as xpart"),
            ParamRule("rs", "pass", cite="ref5 p.490; mos2.va rs; N_DEV_MOSFET2.C RS"),
            ParamRule("rd", "pass", cite="ref5 p.490; mos2.va rd; N_DEV_MOSFET2.C RD"),
            ParamRule("rss", "strip", match=("0",), cite="ref5 p.491; scalable source resistance: no target"),
            ParamRule("rss", "error", match=("nonzero",), cite="ref5 p.491; scalable source resistance: no target"),
            ParamRule("rdd", "strip", match=("0",), cite="ref5 p.491; scalable drain resistance: no target"),
            ParamRule("rdd", "error", match=("nonzero",), cite="ref5 p.491; scalable drain resistance: no target"),
            ParamRule("rsh", "pass", cite="ref5 p.491; mos2.va rsh; N_DEV_MOSFET2.C RSH"),
            ParamRule("rsc", "strip", match=("0",), cite="ref5 p.491; contact resistance: no target"),
            ParamRule("rsc", "error", match=("nonzero",), cite="ref5 p.491; contact resistance: no target"),
            ParamRule("rdc", "strip", match=("0",), cite="ref5 p.491; contact resistance: no target"),
            ParamRule("rdc", "error", match=("nonzero",), cite="ref5 p.491; contact resistance: no target"),
            ParamRule("minr", "strip", cite="ref5 p.491; a Spectre numerical floor"),
            ParamRule("ldif", "strip", match=("0",), cite="ref5 p.491; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("ldif", "error", match=("nonzero",), cite="ref5 p.491; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("hdif", "strip", match=("0",), cite="ref5 p.491; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("hdif", "error", match=("nonzero",), cite="ref5 p.491; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("lgcs", "strip", match=("0",), cite="ref5 p.491; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("lgcs", "error", match=("nonzero",), cite="ref5 p.491; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("lgcd", "strip", match=("0",), cite="ref5 p.491; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("lgcd", "error", match=("nonzero",), cite="ref5 p.491; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("sc", "error", cite="ref5 p.491; contact spacing: no target"),
            ParamRule("js", "pass", cite="ref5 p.491; mos2.va js (A/m2); N_DEV_MOSFET2.C JS"),
            ParamRule("is", "pass", cite="ref5 p.491; mos2.va is; N_DEV_MOSFET2.C IS"),
            ParamRule("n", "strip", match=("1",), cite="ref5 p.491; junction emission coefficient: the targets' SPICE3 junctions use 1"),
            ParamRule("n", "error", cite="ref5 p.491; junction emission coefficient: the targets' SPICE3 junctions use 1"),
            *[ParamRule(n, "strip", cite="ref5 p.491; convergence aid and explosion currents")
              for n in "dskip imelt jmelt".split()],
            *[ParamRule(n, "pass", cite="ref5 p.492; mos2.va; N_DEV_MOSFET2.C")
              for n in "cbs cbd cj mj pb fc cjsw".split()],
            ParamRule("mjsw", "default", "1/3", "absent:mjsw", cite="ref5 p.492; §3.9 table: ref5 mjsw=1/3; the targets 0.5 (sp_mos1, MOSFET1/2) or 0.33 (sp_mos2/3, MOSFET3) [E95]"),
            ParamRule("mjsw", "pass", cite="ref5 p.492; mos2.va mjsw; N_DEV_MOSFET2.C MJSW"),
            ParamRule("pbsw", "strip", warn="card", cite="ref5 p.492; sidewall potential: the targets use pb for the sidewall"),
            ParamRule("fcsw", "strip", warn="card", cite="ref5 p.492; sidewall forward-bias threshold: the targets use fc for the sidewall"),
            *[ParamRule(n, "strip", cite="ref5 p.492; operating-region warnings")
              for n in "alarm imax jmax bvj vbox".split()],
            ParamRule("tnom", "pass", cite="ref5 p.492; mos2.va tnom; N_DEV_MOSFET2.C TNOM"),
            ParamRule("trise", "strip", match=("0",), cite="ref5 p.492; card default temperature rise: no target (the instance trise maps to dtemp)"),
            ParamRule("trise", "error", match=("nonzero",), cite="ref5 p.492; card default temperature rise: no target (the instance trise maps to dtemp)"),
            ParamRule("uto", "strip", match=("0",), cite="ref5 p.493; mobility temperature offset: no target"),
            ParamRule("uto", "error", match=("nonzero",), cite="ref5 p.493; mobility temperature offset: no target"),
            ParamRule("ute", "strip", match=("-1.5",), cite="ref5 p.493; mobility temperature exponent: SPICE3's fixed -1.5"),
            ParamRule("ute", "error", cite="ref5 p.493; mobility temperature exponent: SPICE3's fixed -1.5"),
            ParamRule("tlev", "strip", match=("0",), cite="ref5 p.493; temperature equation selector: no target"),
            ParamRule("tlev", "error", match=("nonzero",), cite="ref5 p.493; temperature equation selector: no target"),
            ParamRule("tlevc", "strip", match=("0",), cite="ref5 p.493; no target"),
            ParamRule("tlevc", "error", match=("nonzero",), cite="ref5 p.493; no target"),
            ParamRule("eg", "strip", match=("absent", "nonzero",), warn="temp!=tnom", cite="ref5 p.493; ref5 eg=1.12452; SPICE3's mos1-3 compute Eg(T) themselves (1.1151 V at 27 C): an approximation at T != tnom"),
            ParamRule("gap1", "strip", match=("7.02e-4",), cite="ref5 p.493; band-gap law: no target parameter"),
            ParamRule("gap1", "error", cite="ref5 p.493; band-gap law: no target parameter"),
            ParamRule("gap2", "strip", match=("1108",), cite="ref5 p.493; as gap1"),
            ParamRule("gap2", "error", cite="ref5 p.493; as gap1"),
            ParamRule("f1ex", "strip", match=("0",), cite="ref5 p.493; temperature coefficients: no target at this level"),
            ParamRule("f1ex", "error", match=("nonzero",), cite="ref5 p.493; temperature coefficients: no target at this level"),
            ParamRule("lamex", "strip", match=("0",), cite="ref5 p.493; temperature coefficients: no target at this level"),
            ParamRule("lamex", "error", match=("nonzero",), cite="ref5 p.493; temperature coefficients: no target at this level"),
            ParamRule("trs", "strip", match=("0",), cite="ref5 p.493; temperature coefficients: no target at this level"),
            ParamRule("trs", "error", match=("nonzero",), cite="ref5 p.493; temperature coefficients: no target at this level"),
            ParamRule("trd", "strip", match=("0",), cite="ref5 p.493; temperature coefficients: no target at this level"),
            ParamRule("trd", "error", match=("nonzero",), cite="ref5 p.493; temperature coefficients: no target at this level"),
            ParamRule("ptc", "strip", match=("0",), cite="ref5 p.493; temperature coefficients: no target at this level"),
            ParamRule("ptc", "error", match=("nonzero",), cite="ref5 p.493; temperature coefficients: no target at this level"),
            ParamRule("tcv", "strip", match=("0",), cite="ref5 p.493; temperature coefficients: no target at this level"),
            ParamRule("tcv", "error", match=("nonzero",), cite="ref5 p.493; temperature coefficients: no target at this level"),
            ParamRule("pta", "strip", match=("0",), cite="ref5 p.493; temperature coefficients: no target at this level"),
            ParamRule("pta", "error", match=("nonzero",), cite="ref5 p.493; temperature coefficients: no target at this level"),
            ParamRule("ptp", "strip", match=("0",), cite="ref5 p.493; temperature coefficients: no target at this level"),
            ParamRule("ptp", "error", match=("nonzero",), cite="ref5 p.493; temperature coefficients: no target at this level"),
            ParamRule("cta", "strip", match=("0",), cite="ref5 p.493; temperature coefficients: no target at this level"),
            ParamRule("cta", "error", match=("nonzero",), cite="ref5 p.493; temperature coefficients: no target at this level"),
            ParamRule("ctp", "strip", match=("0",), cite="ref5 p.493; temperature coefficients: no target at this level"),
            ParamRule("ctp", "error", match=("nonzero",), cite="ref5 p.493; temperature coefficients: no target at this level"),
            ParamRule("xti", "strip", match=("absent", "nonzero",), warn="temp!=tnom", cite="ref5 p.493; ref5 xti=3; SPICE3's mos1-3 junction saturation current has no xti term: an approximation at T != tnom"),
            ParamRule("w", "fold", cite="ref5 p.493; §3.9 default MOS geometry: folded into instances; a mod= sweep of it is refused"),
            ParamRule("l", "fold", cite="ref5 p.493; §3.9 default MOS geometry"),
            ParamRule("as", "fold", cite="ref5 p.494; ref5 'Default instance parameters': folded into instances as w/l (§3.9)"),
            ParamRule("ad", "fold", cite="ref5 p.494; as as"),
            ParamRule("ps", "fold", cite="ref5 p.494; as as"),
            ParamRule("pd", "fold", cite="ref5 p.494; as as"),
            ParamRule("nrd", "fold", cite="ref5 p.494; ref5 default 0: folded into instances; the targets' instance default is 1 (see the instance rules)"),
            ParamRule("nrs", "fold", cite="ref5 p.494; as nrd"),
            ParamRule("ldd", "strip", match=("0",), cite="ref5 p.494; default drain diffusion length: no target"),
            ParamRule("ldd", "error", match=("nonzero",), cite="ref5 p.494; default drain diffusion length: no target"),
            ParamRule("lds", "strip", match=("0",), cite="ref5 p.494; no target"),
            ParamRule("lds", "error", match=("nonzero",), cite="ref5 p.494; no target"),
            ParamRule("noisemod", "strip", match=("1",), cite="ref5 p.494; noise model selector: the targets' SPICE2 flicker noise (nlev)"),
            ParamRule("noisemod", "error", cite="ref5 p.494; noise model selector: the targets' SPICE2 flicker noise (nlev)"),
            ParamRule("kf", "pass", warn="analyses=noise", cite="ref5 p.494; mos2.va kf; N_DEV_MOSFET2.C KF; Spectre normalizes its flicker noise by wnoi (ref5), the targets do not"),
            ParamRule("af", "pass", cite="ref5 p.494; mos2.va af; N_DEV_MOSFET2.C AF"),
            ParamRule("ef", "strip", match=("1",), cite="ref5 p.494; flicker frequency exponent: no target"),
            ParamRule("ef", "strip", warn="analyses=noise", cite="ref5 p.494; flicker frequency exponent: no target"),
            ParamRule("wnoi", "strip", match=("1e-5",), cite="ref5 p.494; noise reference width: no target"),
            ParamRule("wnoi", "strip", warn="analyses=noise", cite="ref5 p.494; noise reference width: no target"),
            *[ParamRule(n, "pass", cite="ref5 p.494; model-group bounds (bin_bounds_spectre, §4.4)")
              for n in "wmax wmin lmax lmin".split()],
            ParamRule("degramod", "strip", cite="ref5 p.495; degradation model selector (degradation=yes is an error)"),
            ParamRule("degradation", "strip", match=("no",), cite="ref5 p.495; no hot-electron degradation"),
            ParamRule("degradation", "error", match=("yes",), cite="ref5 p.495; hot-electron degradation: not in v1"),
            *[ParamRule(n, "strip", cite="ref5 p.495; degradation coefficients (degradation=yes is an error)")
              for n in "dvthc dvthe duoc duoe crivth criuo crigm criids wnom lnom vbsn vdsni vgsni vdsng vgsng".split()],
            ParamRule("esat", "strip", cite="ref5 p.495; Spectre stress parameters (degradation=yes is an error)"),
            *[ParamRule(n, "strip", cite="ref5 p.496; Spectre stress parameters (degradation=yes is an error)")
              for n in "esatg vpg vpb subc1 subc2 sube strc stre".split()],
            *[ParamRule(n, "strip", cite="ref5 p.496; BERT stress parameters (degradation=yes is an error)")
              for n in "h0 hgd m0 mgd ecrit0 lecrit0 wecrit0 ecritg lecritg wecritg ecritb lecritb".split()],
            *[ParamRule(n, "strip", cite="ref5 p.497; BERT stress parameters (degradation=yes is an error)")
              for n in "wecritb lc0 llc0 wlc0 lc1 llc1 wlc1 lc2 llc2 wlc2 lc3 llc3 wlc3 lc4 llc4 wlc4 lc5 llc5 wlc5 lc6 llc6 wlc6".split()],
            *[ParamRule(n, "strip", cite="ref5 p.498; BERT stress parameters (degradation=yes is an error)")
              for n in "lc7 llc7 wlc7".split()],
            ParamRule("ucrit", "pass", cite="ref5 p.489; ref5 0 vs the targets' 1e4 (mos2.va:147; N_DEV_MOSFET2.C:329): acts only with uexp != 0, and Spectre's reading of ucrit=0 is undocumented; not written"),
            ParamRule("uexp", "pass", cite="ref5 p.489; mos2.va:146; N_DEV_MOSFET2.C:324"),
            ParamRule("utra", "strip", match=("0",), cite="ref5 p.489; SPICE2's transverse-field mobility term: no target"),
            ParamRule("utra", "error", match=("nonzero",), cite="ref5 p.489; SPICE2's transverse-field mobility term: no target"),
            ParamRule("neff", "pass", cite="ref5 p.489; mos2.va:150; N_DEV_MOSFET2.C:344"),
            ParamRule("delta", "pass", cite="ref5 p.489; mos2.va:145; N_DEV_MOSFET2.C:319"),
            ParamRule("smooth", "strip", cite="ref5 p.489; Spectre's drain-current smoothing: no target"),
            ParamRule("xj", "pass", cite="ref5 p.489; mos2.va:149; N_DEV_MOSFET2.C:339"),
        ),
        instance=(
            ParamRule("w", "default", "3e-6", "absent:w", cite="ref5 p.487; §3.9 default MOS geometry (ref5 'Default channel width')"),
            ParamRule("w", "pass", cite="ref5 p.487; mos2.va w; N_DEV_MOSFET2.C W"),
            ParamRule("l", "default", "3e-6", "absent:l", cite="ref5 p.487; §3.9 default MOS geometry"),
            ParamRule("l", "pass", cite="ref5 p.487; mos2.va l; N_DEV_MOSFET2.C L"),
            *[ParamRule(n, "pass", cite="ref5 p.487; mos2.va; N_DEV_MOSFET2.C")
              for n in "as ad ps pd".split()],
            ParamRule("nrd", "default", "0", "absent:nrd", cite="ref5 p.487; ref5 card default nrd=0; the targets' instance default is 1 (mos2.va nrd; N_DEV_MOSFET2.C NRD)"),
            ParamRule("nrd", "pass", cite="ref5 p.487; mos2.va nrd; N_DEV_MOSFET2.C NRD"),
            ParamRule("nrs", "default", "0", "absent:nrs", cite="ref5 p.487; as nrd"),
            ParamRule("nrs", "pass", cite="ref5 p.487; mos2.va nrs; N_DEV_MOSFET2.C NRS"),
            ParamRule("ld", "strip", match=("0",), cite="ref5 p.488; drain diffusion length (instance): no target"),
            ParamRule("ld", "error", match=("nonzero",), cite="ref5 p.488; drain diffusion length (instance): no target"),
            ParamRule("ls", "strip", match=("0",), cite="ref5 p.488; no target"),
            ParamRule("ls", "error", match=("nonzero",), cite="ref5 p.488; no target"),
            ParamRule("m", "pass", cite="ref5 p.488; multiplier"),
            ParamRule("region", "strip", cite="ref5 p.488; an initial operating-region hint"),
            ParamRule("trise", "rename", "dtemp", cite="ref5 p.488; sp_mos2 dtemp; Xyce DTEMP"),
            ParamRule("degradation", "strip", match=("no",), cite="ref5 p.488"),
            ParamRule("degradation", "error", match=("yes",), cite="ref5 p.488; hot-electron degradation: not in v1"),
        ),
    ),
    # -- mos3 [M ref5 pp.504-514] ----------------------------------------------------
    "mos3": MasterRow(
        "mos3", "m", 3, "type",
        kinds=(('n', 'nmos'), ('p', 'pmos')),
        terminals=('d', 'g', 's', 'b'),
        geometry=(('w', 3e-06), ('l', 3e-06), ('lmin', 0.0), ('lmax', 1.0), ('wmin', 0.0), ('wmax', 1.0)),
        params=(
            ParamRule("type", "fold", match=("n", "p",), cite="ref5 p.505; §3.9: polarity -> Model.kind nmos/pmos"),
            ParamRule("type", "error", cite="ref5 p.505; unknown polarity"),
            ParamRule("vto", "default", "0", "absent:vto,absent:nsub", cite="ref5 p.505; §3.9 nsub row: pins what the targets would derive from the written nsub"),
            ParamRule("vto", "pass", "", "absent:vto,given:nsub", match=("absent",), warn="card", cite="ref5 p.505; §3.9: the targets derive vto from the given nsub (SPICE3)"),
            ParamRule("vto", "pass", cite="ref5 p.505; mos3.va vto; N_DEV_MOSFET3.C VTO"),
            ParamRule("kp", "pass", cite="ref5 p.505; ref5 2.0718e-5 = uo*cox at tox=1e-7, which the targets derive when kp is absent (mos3.va; N_DEV_MOSFET3.C)"),
            ParamRule("phi", "default", "0.7", "absent:phi,absent:nsub", cite="ref5 p.505; §3.9 nsub row: ref5 phi=0.7; the targets 0.6 (mos3.va; N_DEV_MOSFET3.C)"),
            ParamRule("phi", "pass", "", "absent:phi,given:nsub", match=("absent",), warn="card", cite="ref5 p.505; §3.9: the targets derive phi from the given nsub (SPICE3)"),
            ParamRule("phi", "pass", cite="ref5 p.505; mos3.va phi"),
            ParamRule("gamma", "default", "0", "absent:gamma,absent:nsub", cite="ref5 p.505; §3.9 nsub row: pins what the targets would derive from the written nsub"),
            ParamRule("gamma", "pass", "", "absent:gamma,given:nsub", match=("absent",), warn="card", cite="ref5 p.505; §3.9: the targets derive gamma from the given nsub (SPICE3)"),
            ParamRule("gamma", "pass", cite="ref5 p.505; mos3.va gamma"),
            ParamRule("uo", "pass", cite="ref5 p.505; mos3.va u0 (alias uo); N_DEV_MOSFET3.C UO"),
            ParamRule("vmax", "pass", cite="ref5 p.505; inf = the targets' 0 (off): mos3.va vmax; N_DEV_MOSFET3.C VMAX"),
            ParamRule("theta", "pass", cite="ref5 p.505; mos3.va theta; N_DEV_MOSFET3.C THETA"),
            ParamRule("nsub", "default", "1.13e16", "absent:nsub", cite="ref5 p.505; §3.9 table: ref5 nsub=1.13e16; the targets 0 = not given (mos3.va; N_DEV_MOSFET3.C) [E95]"),
            ParamRule("nsub", "pass", cite="ref5 p.505; mos3.va nsub; N_DEV_MOSFET3.C NSUB"),
            ParamRule("nss", "pass", cite="ref5 p.505; mos3.va nss; N_DEV_MOSFET3.C NSS"),
            ParamRule("nfs", "pass", cite="ref5 p.506; mos3.va nfs; N_DEV_MOSFET3.C NFS"),
            ParamRule("tpg", "pass", cite="ref5 p.506; mos3.va tpg; N_DEV_MOSFET3.C TPG"),
            ParamRule("ld", "pass", cite="ref5 p.506; mos3.va ld; N_DEV_MOSFET3.C LD"),
            *[ParamRule(n, "pass", cite="ref5 p.506; sp_mos3 (mos3.va:141-143); Xyce's MOSFET3 has none (fails loudly)")
              for n in "wd xw xl".split()],
            ParamRule("tox", "pass", cite="ref5 p.506; mos3.va tox; N_DEV_MOSFET3.C TOX"),
            ParamRule("ai0", "strip", match=("0",), cite="ref5 p.506; impact ionization: no target"),
            ParamRule("ai0", "error", match=("nonzero",), cite="ref5 p.506; impact ionization: no target"),
            ParamRule("lai0", "strip", match=("0",), cite="ref5 p.506; impact ionization: no target"),
            ParamRule("lai0", "error", match=("nonzero",), cite="ref5 p.506; impact ionization: no target"),
            ParamRule("wai0", "strip", match=("0",), cite="ref5 p.506; impact ionization: no target"),
            ParamRule("wai0", "error", match=("nonzero",), cite="ref5 p.506; impact ionization: no target"),
            ParamRule("bi0", "strip", match=("0",), cite="ref5 p.506; impact ionization: no target"),
            ParamRule("bi0", "error", match=("nonzero",), cite="ref5 p.506; impact ionization: no target"),
            ParamRule("lbi0", "strip", match=("0",), cite="ref5 p.506; impact ionization: no target"),
            ParamRule("lbi0", "error", match=("nonzero",), cite="ref5 p.506; impact ionization: no target"),
            ParamRule("wbi0", "strip", match=("0",), cite="ref5 p.506; impact ionization: no target"),
            ParamRule("wbi0", "error", match=("nonzero",), cite="ref5 p.506; impact ionization: no target"),
            *[ParamRule(n, "pass", cite="ref5 p.506; mos3.va; N_DEV_MOSFET3.C")
              for n in "cgso cgdo cgbo".split()],
            ParamRule("meto", "strip", match=("0",), cite="ref5 p.506; metal overlap in the fringing capacitance: no target"),
            ParamRule("meto", "error", match=("nonzero",), cite="ref5 p.506; metal overlap in the fringing capacitance: no target"),
            ParamRule("capmod", "strip", match=("absent", "bsim",), warn="analyses=ac,noise,xf,tran", cite="ref5 p.507; §3.9: Spectre's default charge model is bsim; both targets have only Meyer's charge"),
            ParamRule("capmod", "strip", match=("meyer",), cite="ref5 p.507; §3.9: meyer is the targets' charge model [I, §14 q.45]"),
            ParamRule("capmod", "error", match=("none", "yang",), cite="ref5 p.507; §3.9: none and yang -> error"),
            ParamRule("xpart", "strip", cite="ref5 p.507; charge partition of the bsim charge model (capmod)"),
            ParamRule("xqc", "strip", cite="ref5 p.507; as xpart"),
            ParamRule("rs", "pass", cite="ref5 p.507; mos3.va rs; N_DEV_MOSFET3.C RS"),
            ParamRule("rd", "pass", cite="ref5 p.507; mos3.va rd; N_DEV_MOSFET3.C RD"),
            ParamRule("rss", "strip", match=("0",), cite="ref5 p.507; scalable source resistance: no target"),
            ParamRule("rss", "error", match=("nonzero",), cite="ref5 p.507; scalable source resistance: no target"),
            ParamRule("rdd", "strip", match=("0",), cite="ref5 p.507; scalable drain resistance: no target"),
            ParamRule("rdd", "error", match=("nonzero",), cite="ref5 p.507; scalable drain resistance: no target"),
            ParamRule("rsh", "pass", cite="ref5 p.507; mos3.va rsh; N_DEV_MOSFET3.C RSH"),
            ParamRule("rsc", "strip", match=("0",), cite="ref5 p.507; contact resistance: no target"),
            ParamRule("rsc", "error", match=("nonzero",), cite="ref5 p.507; contact resistance: no target"),
            ParamRule("rdc", "strip", match=("0",), cite="ref5 p.507; contact resistance: no target"),
            ParamRule("rdc", "error", match=("nonzero",), cite="ref5 p.507; contact resistance: no target"),
            ParamRule("minr", "strip", cite="ref5 p.507; a Spectre numerical floor"),
            ParamRule("ldif", "strip", match=("0",), cite="ref5 p.507; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("ldif", "error", match=("nonzero",), cite="ref5 p.507; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("hdif", "strip", match=("0",), cite="ref5 p.507; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("hdif", "error", match=("nonzero",), cite="ref5 p.507; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("lgcs", "strip", match=("0",), cite="ref5 p.507; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("lgcs", "error", match=("nonzero",), cite="ref5 p.507; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("lgcd", "strip", match=("0",), cite="ref5 p.507; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("lgcd", "error", match=("nonzero",), cite="ref5 p.507; diffusion geometry for the parasitic resistances: no target"),
            ParamRule("sc", "error", cite="ref5 p.507; contact spacing: no target"),
            ParamRule("js", "pass", cite="ref5 p.508; mos3.va js (A/m2); N_DEV_MOSFET3.C JS"),
            ParamRule("is", "pass", cite="ref5 p.508; mos3.va is; N_DEV_MOSFET3.C IS"),
            ParamRule("n", "strip", match=("1",), cite="ref5 p.508; junction emission coefficient: the targets' SPICE3 junctions use 1"),
            ParamRule("n", "error", cite="ref5 p.508; junction emission coefficient: the targets' SPICE3 junctions use 1"),
            *[ParamRule(n, "strip", cite="ref5 p.508; convergence aid and explosion currents")
              for n in "dskip imelt jmelt".split()],
            *[ParamRule(n, "pass", cite="ref5 p.508; mos3.va; N_DEV_MOSFET3.C")
              for n in "cbs cbd cj mj pb fc cjsw".split()],
            ParamRule("mjsw", "default", "1/3", "absent:mjsw", cite="ref5 p.508; §3.9 table: ref5 mjsw=1/3; the targets 0.5 (sp_mos1, MOSFET1/2) or 0.33 (sp_mos2/3, MOSFET3) [E95]"),
            ParamRule("mjsw", "pass", cite="ref5 p.508; mos3.va mjsw; N_DEV_MOSFET3.C MJSW"),
            ParamRule("pbsw", "strip", warn="card", cite="ref5 p.508; sidewall potential: the targets use pb for the sidewall"),
            ParamRule("fcsw", "strip", warn="card", cite="ref5 p.508; sidewall forward-bias threshold: the targets use fc for the sidewall"),
            *[ParamRule(n, "strip", cite="ref5 p.509; operating-region warnings")
              for n in "alarm imax jmax bvj vbox".split()],
            ParamRule("tnom", "pass", cite="ref5 p.509; mos3.va tnom; N_DEV_MOSFET3.C TNOM"),
            ParamRule("trise", "strip", match=("0",), cite="ref5 p.509; card default temperature rise: no target (the instance trise maps to dtemp)"),
            ParamRule("trise", "error", match=("nonzero",), cite="ref5 p.509; card default temperature rise: no target (the instance trise maps to dtemp)"),
            ParamRule("uto", "strip", match=("0",), cite="ref5 p.509; mobility temperature offset: no target"),
            ParamRule("uto", "error", match=("nonzero",), cite="ref5 p.509; mobility temperature offset: no target"),
            ParamRule("ute", "strip", match=("-1.5",), cite="ref5 p.509; mobility temperature exponent: SPICE3's fixed -1.5"),
            ParamRule("ute", "error", cite="ref5 p.509; mobility temperature exponent: SPICE3's fixed -1.5"),
            ParamRule("tlev", "strip", match=("0",), cite="ref5 p.509; temperature equation selector: no target"),
            ParamRule("tlev", "error", match=("nonzero",), cite="ref5 p.509; temperature equation selector: no target"),
            ParamRule("tlevc", "strip", match=("0",), cite="ref5 p.509; no target"),
            ParamRule("tlevc", "error", match=("nonzero",), cite="ref5 p.509; no target"),
            ParamRule("eg", "strip", match=("absent", "nonzero",), warn="temp!=tnom", cite="ref5 p.509; ref5 eg=1.12452; SPICE3's mos1-3 compute Eg(T) themselves (1.1151 V at 27 C): an approximation at T != tnom"),
            ParamRule("gap1", "strip", match=("7.02e-4",), cite="ref5 p.509; band-gap law: no target parameter"),
            ParamRule("gap1", "error", cite="ref5 p.509; band-gap law: no target parameter"),
            ParamRule("gap2", "strip", match=("1108",), cite="ref5 p.509; as gap1"),
            ParamRule("gap2", "error", cite="ref5 p.509; as gap1"),
            ParamRule("f1ex", "strip", match=("0",), cite="ref5 p.509; temperature coefficients: no target at this level"),
            ParamRule("f1ex", "error", match=("nonzero",), cite="ref5 p.509; temperature coefficients: no target at this level"),
            ParamRule("lamex", "strip", match=("0",), cite="ref5 p.509; temperature coefficients: no target at this level"),
            ParamRule("lamex", "error", match=("nonzero",), cite="ref5 p.509; temperature coefficients: no target at this level"),
            ParamRule("trs", "strip", match=("0",), cite="ref5 p.509; temperature coefficients: no target at this level"),
            ParamRule("trs", "error", match=("nonzero",), cite="ref5 p.509; temperature coefficients: no target at this level"),
            ParamRule("trd", "strip", match=("0",), cite="ref5 p.509; temperature coefficients: no target at this level"),
            ParamRule("trd", "error", match=("nonzero",), cite="ref5 p.509; temperature coefficients: no target at this level"),
            ParamRule("ptc", "strip", match=("0",), cite="ref5 p.510; temperature coefficients: no target at this level"),
            ParamRule("ptc", "error", match=("nonzero",), cite="ref5 p.510; temperature coefficients: no target at this level"),
            ParamRule("tcv", "strip", match=("0",), cite="ref5 p.510; temperature coefficients: no target at this level"),
            ParamRule("tcv", "error", match=("nonzero",), cite="ref5 p.510; temperature coefficients: no target at this level"),
            ParamRule("pta", "strip", match=("0",), cite="ref5 p.510; temperature coefficients: no target at this level"),
            ParamRule("pta", "error", match=("nonzero",), cite="ref5 p.510; temperature coefficients: no target at this level"),
            ParamRule("ptp", "strip", match=("0",), cite="ref5 p.510; temperature coefficients: no target at this level"),
            ParamRule("ptp", "error", match=("nonzero",), cite="ref5 p.510; temperature coefficients: no target at this level"),
            ParamRule("cta", "strip", match=("0",), cite="ref5 p.510; temperature coefficients: no target at this level"),
            ParamRule("cta", "error", match=("nonzero",), cite="ref5 p.510; temperature coefficients: no target at this level"),
            ParamRule("ctp", "strip", match=("0",), cite="ref5 p.510; temperature coefficients: no target at this level"),
            ParamRule("ctp", "error", match=("nonzero",), cite="ref5 p.510; temperature coefficients: no target at this level"),
            ParamRule("xti", "strip", match=("absent", "nonzero",), warn="temp!=tnom", cite="ref5 p.510; ref5 xti=3; SPICE3's mos1-3 junction saturation current has no xti term: an approximation at T != tnom"),
            ParamRule("w", "fold", cite="ref5 p.510; §3.9 default MOS geometry: folded into instances; a mod= sweep of it is refused"),
            ParamRule("l", "fold", cite="ref5 p.510; §3.9 default MOS geometry"),
            ParamRule("as", "fold", cite="ref5 p.510; ref5 'Default instance parameters': folded into instances as w/l (§3.9)"),
            ParamRule("ad", "fold", cite="ref5 p.510; as as"),
            ParamRule("ps", "fold", cite="ref5 p.510; as as"),
            ParamRule("pd", "fold", cite="ref5 p.510; as as"),
            ParamRule("nrd", "fold", cite="ref5 p.510; ref5 default 0: folded into instances; the targets' instance default is 1 (see the instance rules)"),
            ParamRule("nrs", "fold", cite="ref5 p.510; as nrd"),
            ParamRule("ldd", "strip", match=("0",), cite="ref5 p.510; default drain diffusion length: no target"),
            ParamRule("ldd", "error", match=("nonzero",), cite="ref5 p.510; default drain diffusion length: no target"),
            ParamRule("lds", "strip", match=("0",), cite="ref5 p.510; no target"),
            ParamRule("lds", "error", match=("nonzero",), cite="ref5 p.510; no target"),
            ParamRule("noisemod", "strip", match=("1",), cite="ref5 p.510; noise model selector: the targets' SPICE2 flicker noise (nlev)"),
            ParamRule("noisemod", "error", cite="ref5 p.510; noise model selector: the targets' SPICE2 flicker noise (nlev)"),
            ParamRule("kf", "pass", warn="analyses=noise", cite="ref5 p.510; mos3.va kf; N_DEV_MOSFET3.C KF; Spectre normalizes its flicker noise by wnoi (ref5), the targets do not"),
            ParamRule("af", "pass", cite="ref5 p.511; mos3.va af; N_DEV_MOSFET3.C AF"),
            ParamRule("ef", "strip", match=("1",), cite="ref5 p.511; flicker frequency exponent: no target"),
            ParamRule("ef", "strip", warn="analyses=noise", cite="ref5 p.511; flicker frequency exponent: no target"),
            ParamRule("wnoi", "strip", match=("1e-5",), cite="ref5 p.511; noise reference width: no target"),
            ParamRule("wnoi", "strip", warn="analyses=noise", cite="ref5 p.511; noise reference width: no target"),
            *[ParamRule(n, "pass", cite="ref5 p.511; model-group bounds (bin_bounds_spectre, §4.4)")
              for n in "wmax wmin lmax lmin".split()],
            ParamRule("degramod", "strip", cite="ref5 p.511; degradation model selector (degradation=yes is an error)"),
            ParamRule("degradation", "strip", match=("no",), cite="ref5 p.511; no hot-electron degradation"),
            ParamRule("degradation", "error", match=("yes",), cite="ref5 p.511; hot-electron degradation: not in v1"),
            *[ParamRule(n, "strip", cite="ref5 p.511; degradation coefficients (degradation=yes is an error)")
              for n in "dvthc dvthe duoc duoe crivth criuo crigm criids wnom".split()],
            *[ParamRule(n, "strip", cite="ref5 p.512; degradation coefficients (degradation=yes is an error)")
              for n in "lnom vbsn vdsni vgsni vdsng vgsng".split()],
            *[ParamRule(n, "strip", cite="ref5 p.512; Spectre stress parameters (degradation=yes is an error)")
              for n in "esat esatg vpg vpb subc1 subc2 sube strc stre".split()],
            *[ParamRule(n, "strip", cite="ref5 p.512; BERT stress parameters (degradation=yes is an error)")
              for n in "h0 hgd m0 mgd".split()],
            *[ParamRule(n, "strip", cite="ref5 p.513; BERT stress parameters (degradation=yes is an error)")
              for n in "ecrit0 lecrit0 wecrit0 ecritg lecritg wecritg ecritb lecritb wecritb lc0 llc0 wlc0 lc1 llc1 wlc1 lc2 llc2 wlc2 lc3 llc3 wlc3 lc4".split()],
            *[ParamRule(n, "strip", cite="ref5 p.514; BERT stress parameters (degradation=yes is an error)")
              for n in "llc4 wlc4 lc5 llc5 wlc5 lc6 llc6 wlc6 lc7 llc7 wlc7".split()],
            ParamRule("eta", "pass", cite="ref5 p.505; mos3.va:155; N_DEV_MOSFET3.C:319"),
            ParamRule("kappa", "pass", cite="ref5 p.505; mos3.va:158; N_DEV_MOSFET3.C:344"),
            ParamRule("delta", "pass", cite="ref5 p.505; mos3.va:156; N_DEV_MOSFET3.C:324"),
            ParamRule("xj", "pass", cite="ref5 p.506; mos3.va:153; N_DEV_MOSFET3.C:354"),
            ParamRule("badmos3", "default", "1", "absent:badmos3", warn="card", cite="§3.9 table: SPICE2's channel-length modulation, on which the engines agree (mos3.va:159, N_DEV_MOSFET3.C:349) [E69; §14 q.31]"),
        ),
        instance=(
            ParamRule("w", "default", "3e-6", "absent:w", cite="ref5 p.504; §3.9 default MOS geometry (ref5 'Default channel width')"),
            ParamRule("w", "pass", cite="ref5 p.504; mos3.va w; N_DEV_MOSFET3.C W"),
            ParamRule("l", "default", "3e-6", "absent:l", cite="ref5 p.504; §3.9 default MOS geometry"),
            ParamRule("l", "pass", cite="ref5 p.504; mos3.va l; N_DEV_MOSFET3.C L"),
            *[ParamRule(n, "pass", cite="ref5 p.504; mos3.va; N_DEV_MOSFET3.C")
              for n in "as ad ps pd".split()],
            ParamRule("nrd", "default", "0", "absent:nrd", cite="ref5 p.504; ref5 card default nrd=0; the targets' instance default is 1 (mos3.va nrd; N_DEV_MOSFET3.C NRD)"),
            ParamRule("nrd", "pass", cite="ref5 p.504; mos3.va nrd; N_DEV_MOSFET3.C NRD"),
            ParamRule("nrs", "default", "0", "absent:nrs", cite="ref5 p.504; as nrd"),
            ParamRule("nrs", "pass", cite="ref5 p.504; mos3.va nrs; N_DEV_MOSFET3.C NRS"),
            ParamRule("ld", "strip", match=("0",), cite="ref5 p.504; drain diffusion length (instance): no target"),
            ParamRule("ld", "error", match=("nonzero",), cite="ref5 p.504; drain diffusion length (instance): no target"),
            ParamRule("ls", "strip", match=("0",), cite="ref5 p.504; no target"),
            ParamRule("ls", "error", match=("nonzero",), cite="ref5 p.504; no target"),
            ParamRule("m", "pass", cite="ref5 p.504; multiplier"),
            ParamRule("region", "strip", cite="ref5 p.504; an initial operating-region hint"),
            ParamRule("trise", "rename", "dtemp", cite="ref5 p.504; sp_mos3 dtemp; Xyce DTEMP"),
            ParamRule("degradation", "strip", match=("no",), cite="ref5 p.504"),
            ParamRule("degradation", "error", match=("yes",), cite="ref5 p.504; hot-electron degradation: not in v1"),
        ),
    ),
    # -- bsim3v3 [M ref5 pp.187-202] -------------------------------------------------
    "bsim3v3": MasterRow(
        "bsim3v3", "m", 49, "type",
        kinds=(('n', 'nmos'), ('p', 'pmos')),
        terminals=('d', 'g', 's', 'b'),
        geometry=(('w', 5e-06), ('l', 5e-06), ('lmin', 0.0), ('lmax', 1.0), ('wmin', 0.0), ('wmax', 1.0)),
        params=(
            ParamRule("type", "fold", match=("n", "p",), cite="ref5 p.188; §3.9: polarity -> Model.kind"),
            ParamRule("type", "error", cite="ref5 p.188; unknown polarity"),
            ParamRule("vtho", "rename", "vth0", cite="ref5 p.188; Spectre's spelling; sp_bsim3v3 vth0 (alias vtho, bsim3v3.va:190), Xyce VTH0 (N_DEV_MOSFET_B3.C:528)"),
            ParamRule("vfb", "pass", cite="ref5 p.188; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included)"),
            *[ParamRule(n, "pass", cite="ref5 p.189; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included)")
              for n in "k1 k2 k3 k3b w0 nlx gamma1 gamma2 vbx vbm dvt0 dvt1 dvt2 dvt0w dvt1w dvt2w a0 b0 b1 a1 a2 ags".split()],
            ParamRule("keta", "pass", cite="ref5 p.190; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included)"),
            ParamRule("vfbflag", "strip", match=("0",), cite="ref5 p.190; Spectre's vfb selector: no target"),
            ParamRule("vfbflag", "error", match=("nonzero",), cite="ref5 p.190; Spectre's vfb selector: no target"),
            *[ParamRule(n, "pass", cite="ref5 p.190; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included)")
              for n in "nsub nch ngate xj lint wint ll lln lw lwn lwl wl wln ww wwn wwl dwg dwb".split()],
            ParamRule("tox", "pass", cite="ref5 p.191; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included)"),
            ParamRule("dtoxcv", "strip", match=("0",), cite="ref5 p.191; delta oxide thickness for CV: no target (BSIM3v3.2's dtoxcv is not in VACASK's 3.3 or Xyce's 3.2.2 tables)"),
            ParamRule("dtoxcv", "error", match=("nonzero",), cite="ref5 p.191; delta oxide thickness for CV: no target (BSIM3v3.2's dtoxcv is not in VACASK's 3.3 or Xyce's 3.2.2 tables)"),
            *[ParamRule(n, "pass", cite="ref5 p.191; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included)")
              for n in "toxm xt rdsw prwb prwg wr binunit".split()],
            ParamRule("binflag", "strip", match=("0",), cite="ref5 p.191; HSPICE-style binning factor: no target"),
            ParamRule("binflag", "error", match=("nonzero",), cite="ref5 p.191; HSPICE-style binning factor: no target"),
            ParamRule("lref", "strip", match=("1e20",), cite="ref5 p.191; HSPICE-style binning reference: no target"),
            ParamRule("lref", "error", cite="ref5 p.191; HSPICE-style binning reference: no target"),
            ParamRule("wref", "strip", match=("1e20",), cite="ref5 p.191; as lref"),
            ParamRule("wref", "error", cite="ref5 p.191; as lref"),
            *[ParamRule(n, "pass", cite="ref5 p.191; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included); u0 defaults 670 (n) / 250 (p) on the targets, ref5 lists 670")
              for n in "mobmod u0 vsat ua ub uc".split()],
            *[ParamRule(n, "pass", cite="ref5 p.192; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included)")
              for n in "drout pclm pdiblc1 pdiblc2 pdiblcb pscbe1 pscbe2 pvag delta cdsc cdscb cdscd nfactor cit voff dsub eta0 etab".split()],
            *[ParamRule(n, "pass", cite="ref5 p.193; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included)")
              for n in "alpha0 alpha1 beta0 rsh".split()],
            ParamRule("rs", "pass", cite="ref5 p.193; sp_bsim3v3 ACM resistances (bsim3v3.va:287-288); Xyce's BSIM3 has none (fails loudly)"),
            ParamRule("rd", "pass", cite="ref5 p.193; sp_bsim3v3 ACM resistances (bsim3v3.va:287-288); Xyce's BSIM3 has none (fails loudly)"),
            ParamRule("lgcs", "strip", match=("0",), cite="ref5 p.193; contact geometry: no target"),
            ParamRule("lgcs", "error", match=("nonzero",), cite="ref5 p.193; contact geometry: no target"),
            ParamRule("lgcd", "strip", match=("0",), cite="ref5 p.193; no target"),
            ParamRule("lgcd", "error", match=("nonzero",), cite="ref5 p.193; no target"),
            ParamRule("rsc", "pass", cite="ref5 p.193; sp_bsim3v3 ACM contact resistances (bsim3v3.va:289-290); Xyce has none (fails loudly)"),
            ParamRule("rdc", "pass", cite="ref5 p.193; sp_bsim3v3 ACM contact resistances (bsim3v3.va:289-290); Xyce has none (fails loudly)"),
            ParamRule("rss", "strip", match=("0",), cite="ref5 p.193; scalable source resistance: no target"),
            ParamRule("rss", "error", match=("nonzero",), cite="ref5 p.193; scalable source resistance: no target"),
            ParamRule("rdd", "strip", match=("0",), cite="ref5 p.193; no target"),
            ParamRule("rdd", "error", match=("nonzero",), cite="ref5 p.193; no target"),
            ParamRule("sc", "error", cite="ref5 p.193; contact spacing: no target"),
            ParamRule("ldif", "pass", cite="ref5 p.193; sp_bsim3v3 ACM geometry (bsim3v3.va:284-285); Xyce has none (fails loudly)"),
            ParamRule("hdif", "pass", cite="ref5 p.193; sp_bsim3v3 ACM geometry (bsim3v3.va:284-285); Xyce has none (fails loudly)"),
            ParamRule("minr", "strip", cite="ref5 p.193; a Spectre numerical floor"),
            ParamRule("js", "pass", cite="ref5 p.193; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included); ref5 gives no default, the targets 1e-4 A/m2 (bsim3v3.va:221; N_DEV_MOSFET_B3.C:2694)"),
            ParamRule("jsw", "pass", cite="ref5 p.193; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included)"),
            ParamRule("is", "error", cite="ref5 p.194; Spectre's absolute junction current (used when js is absent): no BSIM3 target parameter"),
            ParamRule("n", "rename", "nj", cite="ref5 p.194; junction emission coefficient: BSIM3's nj (bsim3v3.va:224; N_DEV_MOSFET_B3.C:2771)"),
            *[ParamRule(n, "strip", cite="ref5 p.194; convergence aid and explosion currents")
              for n in "dskip imelt jmelt".split()],
            ParamRule("ijth", "pass", cite="ref5 p.194; ref5: alias to imelt; BSIM3v3.2's diode limiting current (bsim3v3.va:295; N_DEV_MOSFET_B3.C:753)"),
            ParamRule("vnds", "strip", match=("-1",), cite="ref5 p.194; Spectre's reverse diode transition: no target"),
            ParamRule("vnds", "error", cite="ref5 p.194; Spectre's reverse diode transition: no target"),
            ParamRule("nds", "strip", match=("1",), cite="ref5 p.194; as vnds"),
            ParamRule("nds", "error", cite="ref5 p.194; as vnds"),
            ParamRule("tt", "strip", match=("0",), cite="ref5 p.194; junction transit time: no BSIM3 target parameter"),
            ParamRule("tt", "error", match=("nonzero",), cite="ref5 p.194; junction transit time: no BSIM3 target parameter"),
            *[ParamRule(n, "pass", cite="ref5 p.194; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included); absent cgso/cgdo/cgbo are derived from dlc/dwc*cox on both (ref5's 'cgbo=2' is that derivation)")
              for n in "cgso cgdo cgbo".split()],
            ParamRule("meto", "strip", match=("0",), cite="ref5 p.194; metal overlap: no target"),
            ParamRule("meto", "error", match=("nonzero",), cite="ref5 p.194; metal overlap: no target"),
            *[ParamRule(n, "pass", cite="ref5 p.194; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included)")
              for n in "cgsl cgdl ckappa".split()],
            ParamRule("cbs", "strip", match=("0",), cite="ref5 p.195; absolute junction capacitance: no BSIM3 target parameter"),
            ParamRule("cbs", "error", match=("nonzero",), cite="ref5 p.195; absolute junction capacitance: no BSIM3 target parameter"),
            ParamRule("cbd", "strip", match=("0",), cite="ref5 p.195; as cbs"),
            ParamRule("cbd", "error", match=("nonzero",), cite="ref5 p.195; as cbs"),
            *[ParamRule(n, "pass", cite="ref5 p.195; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included); cj=5e-4 equal (§3.9: not written)")
              for n in "cj mj pb".split()],
            ParamRule("fc", "strip", match=("0.5",), cite="ref5 p.195; BSIM3's junction capacitance has no forward-bias threshold parameter on either target"),
            ParamRule("fc", "strip", warn="card", cite="ref5 p.195; as above"),
            *[ParamRule(n, "pass", cite="ref5 p.195; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included); cjsw=5e-10 equal (§3.9: not written)")
              for n in "cjsw mjsw pbsw cjswg mjswg pbswg".split()],
            ParamRule("fcsw", "strip", match=("0.5",), cite="ref5 p.195; as fc"),
            ParamRule("fcsw", "strip", warn="card", cite="ref5 p.195; as fc"),
            ParamRule("capmod", "default", "2", "absent:capmod", cite="ref5 p.195; §3.9 table: ref5 capmod=2 (p.195); sp_bsim3v3 capmod=3 (bsim3v3.va:140), Xyce CAPMOD=3 (N_DEV_MOSFET_B3.C:3021)"),
            ParamRule("capmod", "pass", cite="ref5 p.195; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included)"),
            ParamRule("nqsmod", "pass", cite="ref5 p.195; bsim3v3.va:143; Xyce's BSIM3 has NQSMOD on the instance only (N_DEV_MOSFET_B3.C:212): a card nqsmod fails loudly there"),
            *[ParamRule(n, "pass", cite="ref5 p.195; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included); xpart=0 equal (§3.9: not written)")
              for n in "dwc dlc clc cle".split()],
            *[ParamRule(n, "pass", cite="ref5 p.196; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included); xpart=0 equal (§3.9: not written)")
              for n in "cf elm vfbcv acde moin noff voffcv xpart llc lwc lwlc wlc wwc wwlc".split()],
            ParamRule("wmlt", "pass", cite="ref5 p.196; sp_bsim3v3 wmlt (bsim3v3.va:291); Xyce has no WMLT: the output scan of the deck fails loudly (§7.2)"),
            ParamRule("lmlt", "strip", match=("1",), cite="ref5 p.196; length shrink factor: no target"),
            ParamRule("lmlt", "error", cite="ref5 p.196; length shrink factor: no target"),
            ParamRule("w", "fold", cite="ref5 p.196; §3.9 default MOS geometry (5e-6): folded into instances"),
            ParamRule("l", "fold", cite="ref5 p.196; §3.9 default MOS geometry"),
            ParamRule("as", "fold", cite="ref5 p.196; ref5 'Default for instance parameters': folded into instances as w/l (§3.9)"),
            ParamRule("ad", "fold", cite="ref5 p.196; as as"),
            ParamRule("ps", "fold", cite="ref5 p.197; as as"),
            ParamRule("pd", "fold", cite="ref5 p.197; as as"),
            ParamRule("nrd", "fold", cite="ref5 p.197; ref5 default 0: folded into instances (the instance rules write 0 where neither gives it)"),
            ParamRule("nrs", "fold", cite="ref5 p.197; as nrd"),
            ParamRule("version", "pass", cite="ref5 p.197; existing version handling (tables.model_params): VACASK removes it with a note (sp_bsim3v3 is 3.3.0), Xyce keeps it (its default is 3.2.2); ref5's default 3.1 is not written"),
            ParamRule("paramchk", "strip", cite="ref5 p.197; parameter checking messages only"),
            ParamRule("fullreinit", "strip", cite="ref5 p.197; re-initialization selector: no effect on results"),
            ParamRule("level", "fold", match=("11", "49", "53",), cite="ref5 p.197; §3.9: Spectre's bsim3v3 selectors 11, 49 and 53 all dispatch to DISPATCH level 49"),
            ParamRule("level", "error", cite="ref5 p.197; unknown bsim3v3 level"),
            ParamRule("acm", "pass", cite="ref5 p.197; §4.4: Spectre's acm is a bsim3v3 parameter (never STRIP_KEYS); sp_bsim3v3 acm (bsim3v3.va:145), Xyce has none (fails loudly)"),
            ParamRule("geo", "pass", cite="ref5 p.197; sp_bsim3v3 geo (instance parameter, bsim3v3.va:135; a model statement may give it); Xyce has none"),
            ParamRule("calcacm", "pass", cite="ref5 p.197; sp_bsim3v3 calcacm (bsim3v3.va:146); Xyce has none"),
            ParamRule("tnom", "pass", cite="ref5 p.197; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included)"),
            ParamRule("trise", "strip", match=("0",), cite="ref5 p.197; card default temperature rise: no target"),
            ParamRule("trise", "error", match=("nonzero",), cite="ref5 p.197; card default temperature rise: no target"),
            ParamRule("tlev", "strip", match=("0",), cite="ref5 p.197; temperature equation selector: no BSIM3 target parameter"),
            ParamRule("tlev", "error", match=("nonzero",), cite="ref5 p.197; temperature equation selector: no BSIM3 target parameter"),
            ParamRule("tlevc", "strip", match=("0",), cite="ref5 p.197; no target"),
            ParamRule("tlevc", "error", match=("nonzero",), cite="ref5 p.197; no target"),
            ParamRule("eg", "strip", match=("1.12452",), cite="ref5 p.197; band gap: BSIM3 computes Eg(T) itself on both targets"),
            ParamRule("eg", "error", cite="ref5 p.197; band gap: BSIM3 computes Eg(T) itself on both targets"),
            ParamRule("gap1", "strip", match=("7.02e-4",), cite="ref5 p.197; as eg"),
            ParamRule("gap1", "error", cite="ref5 p.197; as eg"),
            ParamRule("gap2", "strip", match=("1108",), cite="ref5 p.197; as eg"),
            ParamRule("gap2", "error", cite="ref5 p.197; as eg"),
            ParamRule("diomod", "strip", match=("1",), cite="ref5 p.197; junction model selector: the targets have BSIM3v3's junction model only"),
            ParamRule("diomod", "error", cite="ref5 p.197; junction model selector: the targets have BSIM3v3's junction model only"),
            *[ParamRule(n, "pass", cite="ref5 p.198; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included)")
              for n in "kt1 kt1l kt2 at ua1 ub1 uc1 prt".split()],
            ParamRule("trs", "strip", match=("0",), cite="ref5 p.198; source resistance tempco: no target"),
            ParamRule("trs", "error", match=("nonzero",), cite="ref5 p.198; source resistance tempco: no target"),
            ParamRule("trd", "strip", match=("0",), cite="ref5 p.198; no target"),
            ParamRule("trd", "error", match=("nonzero",), cite="ref5 p.198; no target"),
            ParamRule("ute", "pass", cite="ref5 p.198; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included)"),
            ParamRule("xti", "pass", cite="ref5 p.198; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included)"),
            ParamRule("pta", "rename", "tpb", cite="ref5 p.198; junction potential tempco: BSIM3's tpb (bsim3v3.va:236; N_DEV_MOSFET_B3.C:887)"),
            ParamRule("tpb", "pass", cite="ref5 p.198; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included)"),
            ParamRule("ptp", "rename", "tpbsw", cite="ref5 p.198; sidewall potential tempco: BSIM3's tpbsw (bsim3v3.va:238)"),
            ParamRule("tpbsw", "pass", cite="ref5 p.198; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included)"),
            ParamRule("tpbswg", "pass", cite="ref5 p.198; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included)"),
            ParamRule("cta", "rename", "tcj", cite="ref5 p.198; junction capacitance tempco: BSIM3's tcj (bsim3v3.va:237; N_DEV_MOSFET_B3.C:866)"),
            ParamRule("tcj", "pass", cite="ref5 p.198; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included)"),
            ParamRule("ctp", "rename", "tcjsw", cite="ref5 p.198; sidewall capacitance tempco: BSIM3's tcjsw (bsim3v3.va:239)"),
            ParamRule("tcjsw", "pass", cite="ref5 p.198; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included)"),
            ParamRule("tcjswg", "pass", cite="ref5 p.199; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included)"),
            *[ParamRule(n, "pass", cite="ref5 p.199; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included); noia/noib/noic default by polarity on both")
              for n in "noimod kf af ef noia noib noic".split()],
            ParamRule("noid", "strip", match=("2e14",), cite="ref5 p.199; Spectre's subthreshold flicker transition coefficient: no target"),
            ParamRule("noid", "strip", warn="analyses=noise", cite="ref5 p.199; Spectre's subthreshold flicker transition coefficient: no target"),
            ParamRule("wnoi", "strip", match=("1e-5",), cite="ref5 p.199; noise reference width: no target"),
            ParamRule("wnoi", "strip", warn="analyses=noise", cite="ref5 p.199; noise reference width: no target"),
            ParamRule("em", "pass", cite="ref5 p.199; bsim3v3.va; N_DEV_MOSFET_B3.C (Berkeley's BSIM3v3 code on both; equal defaults, derived ones included)"),
            ParamRule("flkmod", "strip", match=("0",), cite="ref5 p.199; Spectre's gm-based flicker model: no target"),
            ParamRule("flkmod", "strip", warn="analyses=noise", cite="ref5 p.199; Spectre's gm-based flicker model: no target"),
            ParamRule("gamma", "strip", match=("2/3",), cite="ref5 p.199; thermal noise coefficient: BSIM3's noimod fixes it"),
            ParamRule("gamma", "strip", warn="analyses=noise", cite="ref5 p.199; thermal noise coefficient: BSIM3's noimod fixes it"),
            ParamRule("nlev", "strip", match=("2",), cite="ref5 p.199; SPICE2-style noise selector of Spectre's level-49 compatibility: no target"),
            ParamRule("nlev", "strip", warn="analyses=noise", cite="ref5 p.199; SPICE2-style noise selector of Spectre's level-49 compatibility: no target"),
            ParamRule("bforward", "strip", match=("0",), cite="ref5 p.199; Spectre gate leakage: no target"),
            ParamRule("bforward", "error", match=("nonzero",), cite="ref5 p.199; Spectre gate leakage: no target"),
            ParamRule("breverse", "strip", match=("0",), cite="ref5 p.199; Spectre gate leakage: no target"),
            ParamRule("breverse", "error", match=("nonzero",), cite="ref5 p.199; Spectre gate leakage: no target"),
            ParamRule("cforward", "strip", match=("0",), cite="ref5 p.199; Spectre gate leakage: no target"),
            ParamRule("cforward", "error", match=("nonzero",), cite="ref5 p.199; Spectre gate leakage: no target"),
            ParamRule("creverse", "strip", match=("0",), cite="ref5 p.199; Spectre gate leakage: no target"),
            ParamRule("creverse", "error", match=("nonzero",), cite="ref5 p.199; Spectre gate leakage: no target"),
            ParamRule("tcc", "strip", match=("0",), cite="ref5 p.200; Spectre gate leakage: no target"),
            ParamRule("tcc", "error", match=("nonzero",), cite="ref5 p.200; Spectre gate leakage: no target"),
            *[ParamRule(n, "pass", cite="ref5 p.200; model-group bounds (bin_bounds_spectre, §4.4)")
              for n in "wmax wmin lmax lmin".split()],
            *[ParamRule(n, "strip", cite="ref5 p.200; operating-region warnings")
              for n in "alarm imax jmax bvj vbox warn apwarn".split()],
            ParamRule("xl", "pass", cite="ref5 p.200; sp_bsim3v3 xl/xw (bsim3v3.va:258-259); Xyce's BSIM3 has none (fails loudly)"),
            ParamRule("xw", "pass", cite="ref5 p.200; sp_bsim3v3 xl/xw (bsim3v3.va:258-259); Xyce's BSIM3 has none (fails loudly)"),
            *[ParamRule(n, "strip", cite="ref5 p.201; DC-mismatch parameters (dcmatch is not in v1)")
              for n in "mvtwl mvtwl2 mvt0 mbewl mbe0".split()],
            ParamRule("mos_method", "strip", cite="ref5 p.201; table-model selector"),
            ParamRule("sa0", "strip", cite="ref5 p.201; LOD reference distances (ku0/kvsat/kvth0 are errors when nonzero)"),
            ParamRule("sb0", "strip", cite="ref5 p.201; LOD reference distances (ku0/kvsat/kvth0 are errors when nonzero)"),
            ParamRule("wlod", "strip", match=("0",), cite="ref5 p.201; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("wlod", "error", match=("nonzero",), cite="ref5 p.201; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("ku0", "strip", match=("0",), cite="ref5 p.201; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("ku0", "error", match=("nonzero",), cite="ref5 p.201; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("kvsat", "strip", match=("0",), cite="ref5 p.201; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("kvsat", "error", match=("nonzero",), cite="ref5 p.201; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("kvth0", "strip", match=("0",), cite="ref5 p.201; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("kvth0", "error", match=("nonzero",), cite="ref5 p.201; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("tku0", "strip", match=("0",), cite="ref5 p.201; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("tku0", "error", match=("nonzero",), cite="ref5 p.201; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("llodku0", "strip", match=("0",), cite="ref5 p.201; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("llodku0", "error", match=("nonzero",), cite="ref5 p.201; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("wlodku0", "strip", match=("0",), cite="ref5 p.201; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("wlodku0", "error", match=("nonzero",), cite="ref5 p.201; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("llodvth", "strip", match=("0",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("llodvth", "error", match=("nonzero",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("wlodvth", "strip", match=("0",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("wlodvth", "error", match=("nonzero",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("lku0", "strip", match=("0",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("lku0", "error", match=("nonzero",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("wku0", "strip", match=("0",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("wku0", "error", match=("nonzero",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("pku0", "strip", match=("0",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("pku0", "error", match=("nonzero",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("lkvth0", "strip", match=("0",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("lkvth0", "error", match=("nonzero",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("wkvth0", "strip", match=("0",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("wkvth0", "error", match=("nonzero",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("pkvth0", "strip", match=("0",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("pkvth0", "error", match=("nonzero",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("stk2", "strip", match=("0",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("stk2", "error", match=("nonzero",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("lodk2", "strip", match=("0",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("lodk2", "error", match=("nonzero",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("steta0", "strip", match=("0",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("steta0", "error", match=("nonzero",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("lodeta0", "strip", match=("0",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
            ParamRule("lodeta0", "error", match=("nonzero",), cite="ref5 p.202; Spectre's LOD stress model in bsim3v3: no target (BSIM3 has no LOD)"),
        ),
        instance=(
            ParamRule("w", "default", "5e-6", "absent:w", cite="ref5 p.187; §3.9 default MOS geometry (ref5 p.196)"),
            ParamRule("w", "pass", cite="ref5 p.187; bsim3v3.va:126; N_DEV_MOSFET_B3.C:129"),
            ParamRule("l", "default", "5e-6", "absent:l", cite="ref5 p.187; §3.9 default MOS geometry"),
            ParamRule("l", "pass", cite="ref5 p.187; bsim3v3.va:125; N_DEV_MOSFET_B3.C:120"),
            *[ParamRule(n, "pass", cite="ref5 p.187; bsim3v3.va:127-130; N_DEV_MOSFET_B3.C:138-176")
              for n in "as ad ps pd".split()],
            ParamRule("nrd", "default", "0", "absent:nrd", cite="ref5 p.187; ref5 card default nrd=0; sp_bsim3v3 nrd=0 (bsim3v3.va:131), Xyce NRD=1 (N_DEV_MOSFET_B3.C:154)"),
            ParamRule("nrd", "pass", cite="ref5 p.187; bsim3v3.va:131; N_DEV_MOSFET_B3.C:154"),
            ParamRule("nrs", "default", "0", "absent:nrs", cite="ref5 p.187; as nrd"),
            ParamRule("nrs", "pass", cite="ref5 p.187; bsim3v3.va:132; N_DEV_MOSFET_B3.C:161"),
            ParamRule("m", "pass", cite="ref5 p.187; multiplier"),
            ParamRule("region", "strip", cite="ref5 p.187; an initial operating-region hint"),
            ParamRule("nqsmod", "rename", "instance_nqsmod", cite="ref5 p.187; VACASK's spelling (bsim3v3.va:133); Xyce's is NQSMOD (N_DEV_MOSFET_B3.C:212): S3's Xyce path drops the prefix"),
            ParamRule("trise", "strip", match=("0",), cite="ref5 p.187; sp_bsim3v3 has no instance temperature parameter (bsim3v3.va:125-137); Xyce's DTEMP alone would differ between the engines"),
            ParamRule("trise", "error", match=("nonzero",), cite="ref5 p.187; sp_bsim3v3 has no instance temperature parameter (bsim3v3.va:125-137); Xyce's DTEMP alone would differ between the engines"),
            ParamRule("aforward", "strip", match=("0",), cite="ref5 p.188; Spectre gate leakage: no target"),
            ParamRule("aforward", "error", match=("nonzero",), cite="ref5 p.188; Spectre gate leakage: no target"),
            ParamRule("areverse", "strip", match=("0",), cite="ref5 p.188; no target"),
            ParamRule("areverse", "error", match=("nonzero",), cite="ref5 p.188; no target"),
            ParamRule("delvto", "pass", cite="ref5 p.188; bsim3v3.va:136; Xyce's BSIM3 instance has no DELVTO (fails loudly)"),
            ParamRule("mulmu0", "rename", "mulu0", cite="ref5 p.188; sp_bsim3v3 mulu0 (bsim3v3.va:137); Xyce's BSIM3 has none (fails loudly)"),
            ParamRule("delk1", "strip", match=("0",), cite="ref5 p.188; k1 shift: no target"),
            ParamRule("delk1", "error", match=("nonzero",), cite="ref5 p.188; k1 shift: no target"),
            ParamRule("delnfct", "strip", match=("0",), cite="ref5 p.188; nfactor shift: no target"),
            ParamRule("delnfct", "error", match=("nonzero",), cite="ref5 p.188; nfactor shift: no target"),
            ParamRule("geo", "pass", cite="ref5 p.188; sp_bsim3v3 geo (bsim3v3.va:135); Xyce has none (fails loudly)"),
            ParamRule("rdc", "strip", match=("0",), cite="ref5 p.188; instance contact resistance: sp_bsim3v3 has it on the model only"),
            ParamRule("rdc", "error", match=("nonzero",), cite="ref5 p.188; instance contact resistance: sp_bsim3v3 has it on the model only"),
            ParamRule("rsc", "strip", match=("0",), cite="ref5 p.188; as rdc"),
            ParamRule("rsc", "error", match=("nonzero",), cite="ref5 p.188; as rdc"),
            ParamRule("sa", "strip", match=("0",), cite="ref5 p.188; LOD distance: no target in bsim3v3"),
            ParamRule("sa", "error", match=("nonzero",), cite="ref5 p.188; LOD distance: no target in bsim3v3"),
            ParamRule("sb", "strip", match=("0",), cite="ref5 p.188; as sa"),
            ParamRule("sb", "error", match=("nonzero",), cite="ref5 p.188; as sa"),
        ),
    ),
    # -- bsim4 [M ref5 pp.209-229] ---------------------------------------------------
    "bsim4": MasterRow(
        "bsim4", "m", 54, "type",
        kinds=(('n', 'nmos'), ('p', 'pmos')),
        terminals=('d', 'g', 's', 'b'),
        geometry=(('w', 5e-06), ('l', 5e-06), ('lmin', 0.0), ('lmax', 1.0), ('wmin', 0.0), ('wmax', 1.0)),
        params=(
            ParamRule("type", "fold", match=("n", "p",), cite="ref5 p.211; §3.9: polarity -> Model.kind"),
            ParamRule("type", "error", cite="ref5 p.211; unknown polarity"),
            ParamRule("vtho", "rename", "vth0", cite="ref5 p.211; Spectre's spelling; sp_bsim4v8 vth0 (alias vtho, bsim4v8.va:235), Xyce VTH0 (N_DEV_MOSFET_B4.C:703)"),
            *[ParamRule(n, "pass", cite="ref5 p.211; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)")
              for n in "vfb phin k1".split()],
            *[ParamRule(n, "pass", cite="ref5 p.212; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)")
              for n in "k2 k3 k3b w0 lpe0 lpeb gamma1 gamma2 vbx vbm dvt0 dvt1 dvt2 dvtp0 dvtp1 dvt0w dvt1w dvt2w a0 b0 b1".split()],
            *[ParamRule(n, "pass", cite="ref5 p.213; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)")
              for n in "a1 a2 ags keta epsrox toxe toxp dtox ndep nsd nsub ngate xj lint wint ll lln lw lwn lwl".split()],
            *[ParamRule(n, "pass", cite="ref5 p.214; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)")
              for n in "wl wln ww wwn wwl dwg dwb toxm xt binunit".split()],
            ParamRule("rdsmod", "default", "1", "absent:rdsmod", cite="ref5 p.214; ref5 rdsmod=1 (p.214); BSIM4's own default is 0 on both targets (bsim4v8.va:146; N_DEV_MOSFET_B4.C:4735)"),
            *[ParamRule(n, "pass", cite="ref5 p.214; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)")
              for n in "rdsmod rdsw rdswmin rdw rdwmin rsw rswmin prwb prwg".split()],
            ParamRule("wr", "pass", cite="ref5 p.215; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            *[ParamRule(n, "pass", cite="ref5 p.215; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included); u0 and eu default by polarity on both")
              for n in "mobmod u0 vsat ua ub uc eu".split()],
            *[ParamRule(n, "pass", cite="ref5 p.215; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)")
              for n in "drout fprout pclm pdiblc1 pdiblc2 pdiblcb pscbe1 pscbe2".split()],
            *[ParamRule(n, "pass", cite="ref5 p.216; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)")
              for n in "pvag delta pdits pditsl pditsd cdsc cdscb cdscd nfactor cit voff voffl minv dsub eta0 etab alpha0 alpha1 beta0".split()],
            *[ParamRule(n, "pass", cite="ref5 p.217; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)")
              for n in "rgatemod rsh rshg dmcg dmci dmdg dmcgt dwj xgw xgl ngcon".split()],
            ParamRule("nf", "fold", cite="ref5 p.217; ref5 model default for the instance nf: folded into instances as w/l (§3.9)"),
            ParamRule("min", "fold", cite="ref5 p.217; ref5 model default for the instance min: folded into instances"),
            *[ParamRule(n, "pass", cite="ref5 p.217; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included); rgeomod: Xyce only (sp_bsim4v8 has none)")
              for n in "permod geomod rgeomod xw xl".split()],
            ParamRule("minr", "strip", cite="ref5 p.217; a Spectre numerical floor"),
            *[ParamRule(n, "pass", cite="ref5 p.218; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)")
              for n in "agidl bgidl cgidl egidl igcmod".split()],
            ParamRule("igbmod", "default", "1", "absent:igbmod", cite="ref5 p.218; ref5 igbmod=1 (p.218); BSIM4's own default is 0 on both targets (bsim4v8.va:159; N_DEV_MOSFET_B4.C:4808)"),
            ParamRule("igbmod", "pass", cite="ref5 p.218; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            ParamRule("aigbacc", "pass", warn="card", cite="ref5 p.218; BSIM4 changed the units of the gate-tunneling coefficients in 4.3.0; Spectre 5.0's bsim4 is 4.2.1 (ref5 p.208) and the targets run 4.8.x, so a given 4.2-unit value is read in 4.8 units: a warning per card"),
            ParamRule("bigbacc", "pass", warn="card", cite="ref5 p.218; BSIM4 changed the units of the gate-tunneling coefficients in 4.3.0; Spectre 5.0's bsim4 is 4.2.1 (ref5 p.208) and the targets run 4.8.x, so a given 4.2-unit value is read in 4.8 units: a warning per card"),
            ParamRule("cigbacc", "pass", warn="card", cite="ref5 p.218; BSIM4 changed the units of the gate-tunneling coefficients in 4.3.0; Spectre 5.0's bsim4 is 4.2.1 (ref5 p.208) and the targets run 4.8.x, so a given 4.2-unit value is read in 4.8 units: a warning per card"),
            ParamRule("aigbinv", "pass", warn="card", cite="ref5 p.218; BSIM4 changed the units of the gate-tunneling coefficients in 4.3.0; Spectre 5.0's bsim4 is 4.2.1 (ref5 p.208) and the targets run 4.8.x, so a given 4.2-unit value is read in 4.8 units: a warning per card"),
            ParamRule("bigbinv", "pass", warn="card", cite="ref5 p.218; BSIM4 changed the units of the gate-tunneling coefficients in 4.3.0; Spectre 5.0's bsim4 is 4.2.1 (ref5 p.208) and the targets run 4.8.x, so a given 4.2-unit value is read in 4.8 units: a warning per card"),
            ParamRule("cigbinv", "pass", warn="card", cite="ref5 p.218; BSIM4 changed the units of the gate-tunneling coefficients in 4.3.0; Spectre 5.0's bsim4 is 4.2.1 (ref5 p.208) and the targets run 4.8.x, so a given 4.2-unit value is read in 4.8 units: a warning per card"),
            ParamRule("aigc", "pass", warn="card", cite="ref5 p.218; BSIM4 changed the units of the gate-tunneling coefficients in 4.3.0; Spectre 5.0's bsim4 is 4.2.1 (ref5 p.208) and the targets run 4.8.x, so a given 4.2-unit value is read in 4.8 units: a warning per card"),
            ParamRule("bigc", "pass", warn="card", cite="ref5 p.219; BSIM4 changed the units of the gate-tunneling coefficients in 4.3.0; Spectre 5.0's bsim4 is 4.2.1 (ref5 p.208) and the targets run 4.8.x, so a given 4.2-unit value is read in 4.8 units: a warning per card"),
            ParamRule("cigc", "pass", warn="card", cite="ref5 p.219; BSIM4 changed the units of the gate-tunneling coefficients in 4.3.0; Spectre 5.0's bsim4 is 4.2.1 (ref5 p.208) and the targets run 4.8.x, so a given 4.2-unit value is read in 4.8 units: a warning per card"),
            ParamRule("aigsd", "pass", warn="card", cite="ref5 p.219; BSIM4 changed the units of the gate-tunneling coefficients in 4.3.0; Spectre 5.0's bsim4 is 4.2.1 (ref5 p.208) and the targets run 4.8.x, so a given 4.2-unit value is read in 4.8 units: a warning per card"),
            ParamRule("bigsd", "pass", warn="card", cite="ref5 p.219; BSIM4 changed the units of the gate-tunneling coefficients in 4.3.0; Spectre 5.0's bsim4 is 4.2.1 (ref5 p.208) and the targets run 4.8.x, so a given 4.2-unit value is read in 4.8 units: a warning per card"),
            ParamRule("cigsd", "pass", warn="card", cite="ref5 p.219; BSIM4 changed the units of the gate-tunneling coefficients in 4.3.0; Spectre 5.0's bsim4 is 4.2.1 (ref5 p.208) and the targets run 4.8.x, so a given 4.2-unit value is read in 4.8 units: a warning per card"),
            *[ParamRule(n, "pass", cite="ref5 p.218; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)")
              for n in "nigbacc eigbinv nigbinv".split()],
            *[ParamRule(n, "pass", cite="ref5 p.219; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)")
              for n in "dlcig nigc poxedge pigcd ntox toxref".split()],
            ParamRule("diomod", "strip", match=("1",), cite="ref5 p.219; junction model selector: the targets have BSIM4's junction model only"),
            ParamRule("diomod", "error", cite="ref5 p.219; junction model selector: the targets have BSIM4's junction model only"),
            ParamRule("js", "rename", "jss", cite="ref5 p.219; Spectre's legacy name: BSIM4's jss (jsd follows it) (bsim4v8.va; N_DEV_MOSFET_B4.C)"),
            *[ParamRule(n, "pass", cite="ref5 p.219; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)")
              for n in "jss jsd jsws jswd jswgs".split()],
            ParamRule("jswgd", "pass", cite="ref5 p.220; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            ParamRule("is", "error", cite="ref5 p.220; Spectre's absolute junction current: no BSIM4 target parameter"),
            ParamRule("n", "rename", "njs", cite="ref5 p.220; junction emission coefficient: BSIM4's njs (njd follows it) (bsim4v8.va:289; N_DEV_MOSFET_B4.C:980)"),
            ParamRule("njs", "pass", cite="ref5 p.220; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            ParamRule("njd", "pass", cite="ref5 p.220; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            *[ParamRule(n, "strip", cite="ref5 p.220; convergence aid and explosion currents")
              for n in "dskip imelt jmelt".split()],
            *[ParamRule(n, "pass", cite="ref5 p.220; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)")
              for n in "ijthsrev ijthdrev ijthsfwd ijthdfwd xjbvs xjbvd".split()],
            ParamRule("cgso", "pass", cite="ref5 p.220; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included); absent values are derived from dlc/dwc*cox on both (ref5's 'cgbo=2' is that derivation)"),
            ParamRule("cgdo", "pass", cite="ref5 p.220; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included); absent values are derived from dlc/dwc*cox on both (ref5's 'cgbo=2' is that derivation)"),
            ParamRule("cgbo", "pass", cite="ref5 p.221; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included); absent values are derived from dlc/dwc*cox on both (ref5's 'cgbo=2' is that derivation)"),
            ParamRule("meto", "strip", match=("0",), cite="ref5 p.221; metal overlap: no target"),
            ParamRule("meto", "error", match=("nonzero",), cite="ref5 p.221; metal overlap: no target"),
            *[ParamRule(n, "pass", cite="ref5 p.221; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)")
              for n in "cgsl cgdl ckappas ckappad cj cjs cjd mj mjs mjd pb pbs pbd".split()],
            ParamRule("fc", "strip", match=("0.5",), cite="ref5 p.221; BSIM4's junction capacitance has no forward-bias threshold parameter on either target"),
            ParamRule("fc", "strip", warn="card", cite="ref5 p.221; as above"),
            ParamRule("cjsw", "pass", cite="ref5 p.221; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            *[ParamRule(n, "pass", cite="ref5 p.222; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)")
              for n in "cjsws cjswd mjsw mjsws mjswd pbsw pbsws pbswd".split()],
            ParamRule("cjswg", "rename", "cjswgs", cite="ref5 p.222; BSIM4.0's name: 4.8 has cjswgs (cjswgd follows it) (bsim4v8.va:298; N_DEV_MOSFET_B4.C:1025)"),
            ParamRule("cjswgs", "pass", cite="ref5 p.222; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            ParamRule("cjswgd", "pass", cite="ref5 p.222; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            ParamRule("mjswg", "rename", "mjswgs", cite="ref5 p.222; as cjswg"),
            ParamRule("mjswgs", "pass", cite="ref5 p.222; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            ParamRule("mjswgd", "pass", cite="ref5 p.222; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            ParamRule("pbswg", "rename", "pbswgs", cite="ref5 p.222; as cjswg"),
            ParamRule("pbswgs", "pass", cite="ref5 p.222; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            ParamRule("pbswgd", "pass", cite="ref5 p.222; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            ParamRule("fcsw", "strip", match=("0.5",), cite="ref5 p.222; as fc"),
            ParamRule("fcsw", "strip", warn="card", cite="ref5 p.222; as fc"),
            ParamRule("bvs", "pass", cite="ref5 p.222; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            ParamRule("bvd", "pass", cite="ref5 p.222; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            ParamRule("capmod", "pass", cite="ref5 p.223; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included); ref5 2 = BSIM4's default"),
            *[ParamRule(n, "pass", cite="ref5 p.223; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)")
              for n in "trnqsmod acnqsmod dwc dlc clc cle cf vfbcv acde moin noff voffcv xpart llc lwc lwlc wlc wwc".split()],
            ParamRule("wwlc", "pass", cite="ref5 p.224; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            ParamRule("w", "fold", cite="ref5 p.224; §3.9 default MOS geometry (5e-6): folded into instances"),
            ParamRule("l", "fold", cite="ref5 p.224; §3.9 default MOS geometry"),
            ParamRule("as", "fold", cite="ref5 p.224; ref5 'Default for instance parameters': folded into instances as w/l (§3.9)"),
            ParamRule("ad", "fold", cite="ref5 p.224; as as"),
            ParamRule("ps", "fold", cite="ref5 p.224; as as"),
            ParamRule("pd", "fold", cite="ref5 p.224; as as"),
            ParamRule("nrd", "fold", cite="ref5 p.224; ref5 default 0: folded into instances (the instance rules write 0 where neither gives it)"),
            ParamRule("nrs", "fold", cite="ref5 p.224; as nrd"),
            ParamRule("version", "pass", cite="ref5 p.224; existing version handling (bsim4_version notes); ref5's default 4.21 is not written: Xyce would then run 4.6.1 while VACASK runs 4.8.3 (§14)"),
            ParamRule("level", "fold", match=("14",), cite="ref5 p.224; §3.9: Spectre's bsim4 level 14 dispatches to DISPATCH level 54"),
            ParamRule("level", "error", cite="ref5 p.224; unknown bsim4 level"),
            ParamRule("paramchk", "strip", cite="ref5 p.224; parameter checking messages only"),
            ParamRule("fullreinit", "strip", cite="ref5 p.224; no effect on results"),
            ParamRule("tnom", "pass", cite="ref5 p.224; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            ParamRule("trise", "strip", match=("0",), cite="ref5 p.224; card default temperature rise: no target"),
            ParamRule("trise", "error", match=("nonzero",), cite="ref5 p.224; card default temperature rise: no target"),
            ParamRule("tlev", "strip", match=("0",), cite="ref5 p.224; temperature equation selector: no BSIM4 target parameter"),
            ParamRule("tlev", "error", match=("nonzero",), cite="ref5 p.224; temperature equation selector: no BSIM4 target parameter"),
            ParamRule("tlevc", "strip", match=("0",), cite="ref5 p.224; no target"),
            ParamRule("tlevc", "error", match=("nonzero",), cite="ref5 p.224; no target"),
            ParamRule("eg", "strip", match=("1.12452",), cite="ref5 p.224; band gap: BSIM4 computes Eg(T) itself on both targets"),
            ParamRule("eg", "error", cite="ref5 p.224; band gap: BSIM4 computes Eg(T) itself on both targets"),
            ParamRule("gap1", "strip", match=("7.02e-4",), cite="ref5 p.224; as eg"),
            ParamRule("gap1", "error", cite="ref5 p.224; as eg"),
            ParamRule("gap2", "strip", match=("1108",), cite="ref5 p.225; as eg"),
            ParamRule("gap2", "error", cite="ref5 p.225; as eg"),
            *[ParamRule(n, "pass", cite="ref5 p.225; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)")
              for n in "kt1 kt1l kt2 at ua1 ub1 uc1 prt ute".split()],
            ParamRule("xti", "rename", "xtis", cite="ref5 p.225; BSIM4's xtis (xtid follows it) (bsim4v8.va:290; N_DEV_MOSFET_B4.C:985)"),
            ParamRule("xtis", "pass", cite="ref5 p.225; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            ParamRule("xtid", "pass", cite="ref5 p.225; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            ParamRule("pta", "rename", "tpb", cite="ref5 p.225; BSIM4's tpb (bsim4v8.va:315; N_DEV_MOSFET_B4.C:1111)"),
            ParamRule("tpb", "pass", cite="ref5 p.225; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            ParamRule("ptp", "rename", "tpbsw", cite="ref5 p.225; BSIM4's tpbsw"),
            ParamRule("tpbsw", "pass", cite="ref5 p.225; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            ParamRule("tpbswg", "pass", cite="ref5 p.225; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            ParamRule("cta", "rename", "tcj", cite="ref5 p.225; BSIM4's tcj (bsim4v8.va:316; N_DEV_MOSFET_B4.C:1116)"),
            ParamRule("tcj", "pass", cite="ref5 p.225; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            ParamRule("ctp", "rename", "tcjsw", cite="ref5 p.225; BSIM4's tcjsw"),
            *[ParamRule(n, "pass", cite="ref5 p.226; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)")
              for n in "tcjsw tcjswg saref sbref wlod ku0 kvsat kvth0 tku0 llodku0 wlodku0 llodvth wlodvth lku0 wku0 pku0 lkvth0 wkvth0".split()],
            *[ParamRule(n, "pass", cite="ref5 p.227; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)")
              for n in "pkvth0 stk2 steta0".split()],
            ParamRule("lodk2", "pass", "", "absent:lodk2,given:ku0", match=("absent",), warn="card", cite="ref5 p.227; ref5 lodk2=0 (p.227) while BSIM4 defaults it to 1 on both targets (bsim4v8.va:992-994; N_DEV_MOSFET_B4.C:4536-4546): a warning when the LOD model is active and lodk2 is absent"),
            ParamRule("lodk2", "pass", "", "absent:lodk2,given:kvth0", match=("absent",), warn="card", cite="ref5 p.227; as above"),
            ParamRule("lodk2", "pass", cite="ref5 p.227; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            ParamRule("lodeta0", "pass", "", "absent:lodeta0,given:ku0", match=("absent",), warn="card", cite="ref5 p.227; ref5 lodeta0=0 (p.227) while BSIM4 defaults it to 1 on both targets (bsim4v8.va:992-994; N_DEV_MOSFET_B4.C:4536-4546): a warning when the LOD model is active and lodeta0 is absent"),
            ParamRule("lodeta0", "pass", "", "absent:lodeta0,given:kvth0", match=("absent",), warn="card", cite="ref5 p.227; as above"),
            ParamRule("lodeta0", "pass", cite="ref5 p.227; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            *[ParamRule(n, "pass", cite="ref5 p.227; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included); noia/noib/noic default by polarity on both")
              for n in "fnoimod tnoimod kf af ef noia noib noic".split()],
            ParamRule("wnoi", "strip", match=("1e-5",), cite="ref5 p.227; noise reference width: no target"),
            ParamRule("wnoi", "strip", warn="analyses=noise", cite="ref5 p.227; noise reference width: no target"),
            ParamRule("em", "pass", cite="ref5 p.227; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)"),
            ParamRule("flkmod", "strip", warn="analyses=noise", cite="ref5 p.227; Spectre's flicker model selector: no target"),
            *[ParamRule(n, "pass", cite="ref5 p.227; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)")
              for n in "ntnoi tnoia tnoib".split()],
            *[ParamRule(n, "pass", cite="ref5 p.228; bsim4v8.va; N_DEV_MOSFET_B4.C (Berkeley's BSIM4 code on both; equal defaults, derived ones included)")
              for n in "rbodymod xrcrg1 xrcrg2 rbpb rbpd rbps rbdb rbsb gbmin".split()],
            *[ParamRule(n, "pass", cite="ref5 p.228; model-group bounds (bin_bounds_spectre, §4.4)")
              for n in "wmax wmin lmax lmin".split()],
            ParamRule("alarm", "strip", cite="ref5 p.228; operating-region warnings"),
            *[ParamRule(n, "strip", cite="ref5 p.229; operating-region warnings")
              for n in "imax jmax bvj vbox warn".split()],
            *[ParamRule(n, "strip", cite="ref5 p.229; DC-mismatch parameters (dcmatch is not in v1)")
              for n in "mvtwl mvtwl2 mvt0 mbewl mbe0".split()],
            ParamRule("mos_method", "strip", cite="ref5 p.229; table-model selector"),
        ),
        instance=(
            ParamRule("w", "default", "5e-6", "absent:w", cite="ref5 p.209; §3.9 default MOS geometry (ref5 p.224)"),
            ParamRule("w", "pass", cite="ref5 p.209; bsim4v8.va:104; N_DEV_MOSFET_B4.C:114"),
            ParamRule("l", "default", "5e-6", "absent:l", cite="ref5 p.209; §3.9 default MOS geometry"),
            ParamRule("l", "pass", cite="ref5 p.209; bsim4v8.va:103; N_DEV_MOSFET_B4.C:108"),
            ParamRule("as", "pass", cite="ref5 p.209; bsim4v8.va:114-117; N_DEV_MOSFET_B4.C:166-187"),
            *[ParamRule(n, "pass", cite="ref5 p.210; bsim4v8.va:114-117; N_DEV_MOSFET_B4.C:166-187")
              for n in "ad ps pd".split()],
            ParamRule("nrd", "default", "0", "absent:nrd", cite="ref5 p.210; ref5 card default nrd=0; the targets' instance default is 1 (bsim4v8.va:118; N_DEV_MOSFET_B4.C:194)"),
            ParamRule("nrd", "pass", cite="ref5 p.210; bsim4v8.va:118; N_DEV_MOSFET_B4.C:194"),
            ParamRule("nrs", "default", "0", "absent:nrs", cite="ref5 p.210; as nrd"),
            ParamRule("nrs", "pass", cite="ref5 p.210; bsim4v8.va:119; N_DEV_MOSFET_B4.C:200"),
            ParamRule("m", "pass", cite="ref5 p.210; multiplier"),
            ParamRule("region", "strip", cite="ref5 p.210; an initial operating-region hint"),
            ParamRule("trnqsmod", "rename", "instance_trnqsmod", cite="ref5 p.210; VACASK's spelling (bsim4v8.va:113-137); Xyce's is TRNQSMOD: S3's Xyce path drops the prefix"),
            ParamRule("acnqsmod", "rename", "instance_acnqsmod", cite="ref5 p.210; VACASK's spelling (bsim4v8.va:113-137); Xyce's is ACNQSMOD: S3's Xyce path drops the prefix"),
            ParamRule("rgatemod", "rename", "instance_rgatemod", cite="ref5 p.210; VACASK's spelling (bsim4v8.va:113-137); Xyce's is RGATEMOD: S3's Xyce path drops the prefix"),
            ParamRule("rbodymod", "rename", "instance_rbodymod", cite="ref5 p.210; VACASK's spelling (bsim4v8.va:113-137); Xyce's is RBODYMOD: S3's Xyce path drops the prefix"),
            ParamRule("geomod", "rename", "instance_geomod", cite="ref5 p.210; VACASK's spelling (bsim4v8.va:113-137); Xyce's is GEOMOD: S3's Xyce path drops the prefix"),
            ParamRule("rbpb", "rename", "instance_rbpb", cite="ref5 p.210; VACASK's spelling (bsim4v8.va:113-137); Xyce's is RBPB: S3's Xyce path drops the prefix"),
            ParamRule("rbpd", "rename", "instance_rbpd", cite="ref5 p.210; VACASK's spelling (bsim4v8.va:113-137); Xyce's is RBPD: S3's Xyce path drops the prefix"),
            ParamRule("rbps", "rename", "instance_rbps", cite="ref5 p.210; VACASK's spelling (bsim4v8.va:113-137); Xyce's is RBPS: S3's Xyce path drops the prefix"),
            ParamRule("rbdb", "rename", "instance_rbdb", cite="ref5 p.210; VACASK's spelling (bsim4v8.va:113-137); Xyce's is RBDB: S3's Xyce path drops the prefix"),
            ParamRule("rbsb", "rename", "instance_rbsb", cite="ref5 p.210; VACASK's spelling (bsim4v8.va:113-137); Xyce's is RBSB: S3's Xyce path drops the prefix"),
            ParamRule("min", "rename", "instance_min", cite="ref5 p.210; VACASK's spelling (bsim4v8.va:113-137); Xyce's is MIN: S3's Xyce path drops the prefix"),
            ParamRule("trise", "rename", "dtemp", cite="ref5 p.210; sp_bsim4v8 dtemp (bsim4v8.va:131); Xyce DTEMP (N_DEV_MOSFET_B4.C:327)"),
            ParamRule("rgeomod", "pass", cite="ref5 p.210; Xyce RGEOMOD (N_DEV_MOSFET_B4.C:311); sp_bsim4v8 has no rgeomod (fails loudly)"),
            ParamRule("nf", "pass", cite="ref5 p.210; bsim4v8.va:105; N_DEV_MOSFET_B4.C:120"),
            ParamRule("delvto", "pass", cite="ref5 p.211; bsim4v8.va:125; N_DEV_MOSFET_B4.C:237"),
            ParamRule("mulmu0", "rename", "mulu0", cite="ref5 p.211; sp_bsim4v8 mulu0 (bsim4v8.va:127); Xyce's BSIM4 instance has none (fails loudly)"),
            ParamRule("delk1", "strip", match=("0",), cite="ref5 p.211; k1 shift: no target"),
            ParamRule("delk1", "error", match=("nonzero",), cite="ref5 p.211; k1 shift: no target"),
            ParamRule("delnfct", "strip", match=("0",), cite="ref5 p.211; nfactor shift: no target"),
            ParamRule("delnfct", "error", match=("nonzero",), cite="ref5 p.211; nfactor shift: no target"),
            *[ParamRule(n, "pass", cite="ref5 p.211; bsim4v8.va:106-108; N_DEV_MOSFET_B4.C:125-135")
              for n in "sa sb sd".split()],
        ),
    ),
}


# HSPICE's R (wire) and C model parameters (Star-HSPICE 2001.2, 14-3..14-10) on the targets: the
# names each engine gives the same meaning.  VACASK's sp_resistor and sp_capacitor are ngspice's
# models, which alias HSPICE's names (dw dlr tc1r tc2r res; cox capsw del di thick) except for
# the capacitor's renamed model_cap/model_tc1/model_tc2; Xyce knows RSH, TC1, TC2, TNOM and NARROW
# (R) and CJ, CJSW, NARROW, TC1, TC2, TNOM (C), and ignores any other model parameter silently.
WIRE_RENAME: Dict[Tuple[str, str], Dict[str, str]] = {
    ("r", "vacask"): {"tref": "tnom", "w": "defw", "l": "model_l"},
    ("r", "xyce"): {"tc1r": "tc1", "tc2r": "tc2", "tref": "tnom", "w": "defw"},
    ("c", "vacask"): {"tref": "tnom", "cap": "model_cap", "tc1": "model_tc1", "tc2": "model_tc2", "w": "defw",
                      "l": "defl"},
    ("c", "xyce"): {"tref": "tnom", "cox": "cj", "capsw": "cjsw", "w": "defw"},
}
# The R card's wire capacitance (the CRC pi model with BULK and CRATIO): neither target has it.
WIRE_CAP_KEYS = ("cap", "capsw", "cox", "di", "thick", "bulk", "cratio", "tc1c", "tc2c")
EPS0_HSPICE = 8.8542149e-12             # F/m (14-7, 14-11)
EPSOX_HSPICE = 3.453148e-11


def _wire_card(model: Model, elem: str, engine: str, out: List[Tuple[str, Expr]], notes: List[Note]
               ) -> List[Tuple[str, Expr]]:
    """An R or C model card's HSPICE parameters for one engine (WIRE_RENAME), the geometry HSPICE
    computes kept exact: R = RSH*(L-2*DLR)/(W-2*DW), C = COX*(L-2*DEL)*(W-2*DEL)+2*CAPSW*(L+W-4*DEL),
    COX from THICK and DI when not given (14-5, 14-10).  Xyce subtracts its NARROW once from L and
    from W, so DEL becomes narrow=2*DEL and DW, DLR become narrow=2*DW where DW equals DLR.
    The model W and L defaults (14-4, 14-10) are ngspice's defw/defl (model_l for R) and Xyce's
    DEFW.  TableError for what an engine cannot compute as HSPICE does: the R card's wire
    capacitance, SHRINK other than 1; on Xyce a model L, DW other than DLR and a CAP/RES default
    (Xyce's C and R model parameters are multipliers)."""
    p = dict(out)
    label = card_label(model)

    def refuse(what: str) -> None:
        raise TableError("%s: %s; not supported on %s" % (label, what, "VACASK" if engine == "vacask" else "Xyce"))

    shrink = p.pop("shrink", None)
    if shrink is not None and not (isinstance(shrink, Num) and shrink.value == 1.0):
        refuse("HSPICE's SHRINK=%s scales the element's W and L, which neither target does" % _txt(shrink))
    if engine == "xyce" and "l" in p:
        if _nonzero(p["l"]):
            refuse("HSPICE's model default length l=%s: Xyce's %s model has none" % (_txt(p["l"]), ELEMENT_NAME[elem]))
        p.pop("l")
    if elem == "r":
        caps = [k for k in WIRE_CAP_KEYS if k in p and _nonzero(p[k])]
        if caps:
            refuse("HSPICE's wire capacitance (%s: the CRC model of a wire resistor) has no target "
                   "equivalent" % ", ".join(k.upper() for k in caps))
        for k in WIRE_CAP_KEYS:
            p.pop(k, None)
    else:
        if "cox" not in p and _nonzero(p.get("thick")):
            di = p.get("di")
            eps: Expr = _mul(di, Num(EPS0_HSPICE)) if _nonzero(di) else Num(EPSOX_HSPICE)
            p["cox"] = _div(eps, p["thick"])
            notes.append(note(model.origin or model.name, "%s: cox=%s written (HSPICE's COX from THICK%s, 14-11)"
                              % (label, _txt(p["cox"]), " and DI" if _nonzero(di) else "")))
        p.pop("thick", None)
        p.pop("di", None)
    if engine == "xyce":
        if elem == "r":
            dw, dlr = p.pop("dw", None), p.pop("dlr", None)
            if _nonzero(dw) or _nonzero(dlr):
                if X.to_text(X.fold(dw or Num(0.0))) != X.to_text(X.fold(dlr or Num(0.0))):
                    refuse("HSPICE's DW=%s and DLR=%s: Xyce's resistor subtracts one NARROW from both L and W"
                           % (_txt(dw or Num(0.0)), _txt(dlr or Num(0.0))))
                p["narrow"] = _mul(Num(2.0), dw)
            res = p.pop("res", None)
            if _nonzero(res):
                refuse("HSPICE's RES=%s default resistance: Xyce's R model parameter is a multiplier" % _txt(res))
        else:
            dl = p.pop("del", None)
            if _nonzero(dl):
                p["narrow"] = _mul(Num(2.0), dl)
            cap = p.pop("cap", None)
            if _nonzero(cap):
                refuse("HSPICE's CAP=%s default capacitance: Xyce's C model parameter is a multiplier" % _txt(cap))
    ren = WIRE_RENAME[(elem, engine)]
    return [(ren.get(k, k), v) for k, v in p.items()]


XYCE_TINY_CJO = 1e-30
MOS_CJ_KEYS = ("cj", "cdb", "csb", "cja")          # CJ and its HSPICE aliases (20-28)
MOS_CBX_KEYS = ("cbd", "cbs")
MOS_PB_KEYS = ("pb", "pha", "phs", "phd")          # PB and its HSPICE aliases (20-28)
MOS_CJ_TABLE = 579.11e-6                           # F/m2: the CJ default column of 20-28


def mos_junction_warnings(inst: Instance, model: Model,
                          options: Optional[Mapping[str, Expr]] = None) -> List[Note]:
    """Warnings for a MOS LEVEL 1, 2 or 3 card's bulk junctions where an instance gives a junction
    area (AD or AS), the only case where they matter.  Emitters call this for every M instance; the
    message is per card, so it is printed once.  Both are warnings (errors under --vamos-strict):

    - No CJ (nor an alias) and no NSUB, outside .option spice: hspice_card writes HSPICE's ASPEC=0
      default sqrt(eps_si*q*NSUB/(2*PB)) (20-28) with the default (or GAMMA-derived) NSUB, but the
      manual's default column gives 579.11 uF/m^2 for the same parameter, and nothing settles which
      one HSPICE uses without NSUB; with NSUB on the card the formula is the documented default.
    - CBD or CBS on the card: HSPICE uses them only when CJ*AD + CJSW*PD (CJ*AS + CJSW*PS) is 0
      (20-27, 20-48), and CJ has a nonzero default, so an instance with AD/AS gets CJ*AD where both
      targets use CBD (SPICE's precedence).
    ACM=1 cards (per-width CJ) are left to the ACM warning of model_params.
    """
    row = target(model)
    if not any(row is r for r in _MOS_ROWS):
        return []
    if not any(_nonzero(inst.params.get(k)) for k in ("ad", "as")):
        return []
    given = {k.lower(): v for k, v in model.params.items()}
    acm = given.get("acm")
    if isinstance(acm, Num) and acm.value == 1.0:
        return []
    spice = model_options(options).spice
    out: List[Note] = []
    origin = model.origin or model.name
    cj_k = next((k for k in MOS_CJ_KEYS if k in given), None)
    has_nsub = any(k in given for k in ("nsub", "dnb", "nb"))
    if cj_k is None and not spice and not has_nsub:
        out.append(warning(origin, "%s: no CJ and no NSUB on a MOS LEVEL %d card whose instances give AD/AS: "
                                   "HSPICE's default CJ is ambiguous (Star-HSPICE 20-28 gives "
                                   "sqrt(eps_si*q*NSUB/(2*PB)) for the default option ASPEC=0, which vamos "
                                   "writes with the NSUB HSPICE assumes (none: CJ=0), and %g uF/m^2 in its "
                                   "default column); give CJ (F/m^2) or NSUB on the card"
                           % (card_label(model), level_of(model), MOS_CJ_TABLE * 1e6)))
    cbx = [k for k in MOS_CBX_KEYS if _nonzero(given.get(k))]
    # HSPICE's CJ*AD+CJSW*PD is nonzero: CJ given nonzero, or defaulted (the formula, which needs
    # NSUB under .option spice), or a sidewall capacitance
    cj_on = _nonzero(given[cj_k]) if cj_k is not None else (not spice or has_nsub)
    if cbx and (cj_on or any(_nonzero(given.get(k)) for k in ("cjsw", "cjp"))):
        out.append(warning(origin, "%s: %s on a MOS LEVEL %d card whose instances give AD/AS: HSPICE uses "
                                   "CBD/CBS only when CJ*AD+CJSW*PD is 0 (20-27, 20-48) and simulates CJ*AD "
                                   "here, both targets use %s; give AD=AS=0 or remove %s"
                           % (card_label(model), "/".join(k.upper() for k in cbx), level_of(model),
                              "/".join(k.upper() for k in cbx), "/".join(k.upper() for k in cbx))))
    return out


def _xyce_diode_charge(model: Model, out: List[Tuple[str, Expr]], notes: List[Note]
                       ) -> List[Tuple[str, Expr]]:
    """Xyce's diode (N_DEV_Diode.C, `if (tJctCap != 0.0)') computes no junction charge at all
    when CJO is 0: a sidewall capacitance (CJSW with PJ) and the transit-time charge TT*Id are
    lost, silently (a cjsw-only diode drew 4e-13 A of displacement current where VACASK and
    HSPICE's formula give 1.3e-6 A).  Such a card gets cjo=1e-30 on Xyce, so that block runs:
    the area junction then adds 1e-30 F, which no circuit sees."""
    area = [k for k, v in out if k in _CAPS_AREA["d"]]
    if any(_nonzero(v) for k, v in out if k in area):
        return out
    side = [k for k, v in out if k in ("cjp", "cjsw") and _nonzero(v)]
    tt = [k for k, v in out if k == "tt" and _nonzero(v)]
    if not side and not tt:
        return out
    lost = " and ".join(([] if not side else ["its sidewall capacitance (%s)" % side[0].upper()])
                        + ([] if not tt else ["its transit-time charge (TT)"]))
    notes.append(note(model.origin or model.name,
                      "%s: cjo=%g written for Xyce, which computes no junction charge at all when CJO is "
                      "0 and would lose %s; the %g F area junction this adds is negligible"
                      % (card_label(model), XYCE_TINY_CJO, lost, XYCE_TINY_CJO)))
    return [(k, v) for k, v in out if k not in area] + [("cjo", Num(XYCE_TINY_CJO))]


# -- HSPICE's model defaults (§4.3.6) ----------------------------------------------------------
#
# The M 1/2/3, D, Q and J targets are SPICE3 models (ngspice's code on VACASK, Xyce's ports of
# it) and the BSIM3 targets are Berkeley's code: their defaults are not HSPICE's.  hspice_card
# rewrites a card to the values HSPICE simulates, the same way for both engines, with one note
# per card naming what it wrote; an HSPICE behaviour neither engine has is a warning (an error
# under --vamos-strict), or a TableError where no approximation is meaningful.  The page
# numbers are the Star-HSPICE Manual 2001.2's.

EPS_OX = 3.45314379969e-11              # F/m (21-29); the targets' 3.9*8.854214871e-12
EPS_SI = 1.035943139907e-10             # F/m (21-29); the targets' 11.7*8.854214871e-12
Q_E = 1.6021918e-19                     # C (21-29)
VT_300 = 1.3806226e-23 * 300.0 / Q_E    # V: 2*VT_300*ln(1e15/1.45e10) = 0.576036, HSPICE's PHI default
NI_300 = 1.45e10                        # cm^-3
MOS_COX = EPS_OX / 1e-7                 # F/m2: the TOX default 1e-7 m gives COX 3.453e-4 (20-69)
MOS_NSUB = 1e15                         # cm^-3 (20-50)
PHI_DEFAULT = 2.0 * VT_300 * math.log(MOS_NSUB / NI_300)    # 0.576036 (20-50)
MOS_KP1 = {1.0: 2.0718e-5, -1.0: 8.632e-6}     # A/V2: the LEVEL 1 KP default, NMOS / PMOS (21-2)
MOS_UO = {1.0: 600.0, -1.0: 250.0}      # cm2/V/s: the UO default, NMOS / PMOS (21-11, 21-22)
ETA_RATIO = 8.14 / 8.15                 # LEVEL 3 ETA constant, HSPICE's over SPICE3's (21-29)
XJ_SMALL = 0.05e-6                      # m: below it HSPICE limits LEVEL 3's fs to 1, SPICE3 does not
DIODE_PB = 0.8                          # V (15-12); both targets 1.0
JFET_PB = 0.8                           # V (17-18); both targets 1.0
BJT_MJS = 0.5                           # (16-11); both targets (and .option spice) 0
MOS_MJSW = 0.33                         # MOS sidewall grading (20-28); sp_mos1/2, Xyce LEVEL 1/2: 0.5
MOS_PB = 0.8                            # V: MOS bulk junction potential (20-28); the targets' too
ACM_BERKELEY = 10.0                     # BSIM3 ACM=10: the Berkeley junctions, both targets' own (22-43)
BSIM3_HSPICE_CJ = 5.79e-4               # F/m2: LEVEL 49/53 CJ with HSPICE's junction model (22-43); BSIM3 5e-4
BSIM3_HSPICE_ACM = (0.0, 2.0, 3.0)      # ACMs of HSPICE's own (area) junction model at LEVEL 49/53
DCAPS = (1.0, 2.0, 3.0)
_MOS_ROWS = (_MOS1, _MOS2, _MOS3)


@dataclass(frozen=True)
class ModelOptions:
    """The .option settings that select HSPICE's model defaults."""
    spice: bool = False                 # .option spice (9-14): DCAP=1, CAPOP=0, LD=0, no NSUB default, MJS=0
    dcap: Optional[float] = None        # .option dcap: 1, 2 or 3 (HSPICE's default is 2)


def model_options(options: Optional[Mapping[str, Expr]]) -> ModelOptions:
    """ModelOptions from Netlist.options (None: neither option is given)."""
    if not options:
        return ModelOptions()
    d = options.get("dcap")
    return ModelOptions("spice" in options, d.value if isinstance(d, Num) else None)


def _nonzero(e: Optional[Expr]) -> bool:
    """e is given and is not the constant 0 (a capacitance that exists, a term that acts)."""
    return e is not None and not (isinstance(e, Num) and e.value == 0.0)


def _is_zero(e: Optional[Expr]) -> bool:
    return isinstance(e, Num) and e.value == 0.0


def _txt(e: Expr) -> str:
    return "%.6g" % e.value if isinstance(e, Num) else X.to_text(e)


def _mul(*fs: Expr) -> Expr:
    out = fs[0]
    for f in fs[1:]:
        out = Binary("*", out, f)
    return X.fold(out)


def _div(a: Expr, b: Expr) -> Expr:
    return X.fold(Binary("/", a, b))


def gamma_of(nsub: Expr, cox: Expr) -> Expr:
    """GAMMA from NSUB (cm^-3) and COX (F/m2) as HSPICE computes it (20-51): 0.527625 at the defaults."""
    return _div(Call("sqrt", (_mul(Num(2.0 * Q_E * EPS_SI * 1e6), nsub),)), cox)


def nsub_of(gamma: Expr, cox: Expr) -> Expr:
    """NSUB (cm^-3) from GAMMA and COX, as HSPICE derives it when NSUB is not given (21-4, 21-29)."""
    g = _mul(gamma, cox)
    return _div(_mul(g, g), Num(2.0 * Q_E * EPS_SI * 1e6))


def phi_of(nsub: Expr) -> Expr:
    """PHI = 2*vt*ln(NSUB/ni) (20-51) with vt and ni at 300 K: 0.576036 at NSUB=1e15, HSPICE's
    documented default.  (SPICE3 levels 1 and 2 take vt at TNOM and ni = 1.45e10, ngspice's
    level 3 ni at TNOM: 0.5725 and 0.5799 at 25 C, so the engines would not even agree.)"""
    return _mul(Num(2.0 * VT_300), Call("log", (_div(nsub, Num(NI_300)),)))


class _Card:
    """One card while hspice_card rewrites it: its parameters (lowercase keys, card order), what
    was written (one note per card) and the warnings."""

    def __init__(self, model: Model, params: Dict[str, Expr], notes: List[Note]):
        self.model, self.p, self.notes = model, dict(params), notes
        self.label, self.origin = card_label(model), model.origin or model.name
        self.wrote: List[str] = []

    def key(self, *names: str) -> Optional[str]:
        """The first of names (a parameter and its HSPICE aliases) the card gives, else None."""
        return next((k for k in names if k in self.p), None)

    def put(self, key: str, value: Expr, why: str) -> Expr:
        v = self.p[key] = X.fold(value)
        self.wrote.append("%s=%s (%s)" % (key, _txt(v), why))
        return v

    def drop(self, key: str, why: str) -> Expr:
        v = self.p.pop(key)
        self.wrote.append("%s=%s removed (%s)" % (key, _txt(v), why))
        return v

    def warn(self, message: str) -> None:
        self.notes.append(warning(self.origin, "%s: %s" % (self.label, message)))

    def done(self) -> None:
        if self.wrote:
            self.notes.append(note(self.origin, "%s: written as HSPICE simulates it: %s"
                                   % (self.label, "; ".join(self.wrote))))


def hspice_card(model: Model, params: Mapping[str, Expr], opts: Optional[ModelOptions] = None,
                notes: Optional[List[Note]] = None) -> Dict[str, Expr]:
    """params (a card's parameters, lowercase keys, card order, level and STRIP_KEYS already
    removed) rewritten to what HSPICE simulates; the note and warnings are appended to notes.

    Rules: _mos123 (M LEVEL 1, 2, 3), _bsim3 (M LEVEL 49, 53), _junctions (D, Q, J).  Replaced
    keys keep their place, new ones are appended.  TableError for a card no target can simulate
    as HSPICE does (DCAP=3 with a depletion capacitance, a DCAP other than 1, 2, 3).
    """
    opts = opts or ModelOptions()
    row = target(model)
    c = _Card(model, dict(params), notes if notes is not None else [])
    lv = level_of(model)
    if any(row is r for r in _MOS_ROWS):
        _mos123(c, lv, POLARITY[model.kind], opts)
    elif row is _BSIM3:
        _bsim3(c, lv)
    elif row.element in ("d", "q", "j"):
        _junctions(c, row.element, opts)
    c.done()
    return c.p


def _mos123(c: _Card, lv: int, pol: float, opts: ModelOptions) -> None:
    """MOS LEVEL 1, 2, 3 on the SPICE3 targets (sp_mos1/2/3, Xyce levels 1/2/3).

    Always:
      TOX > 1 is in Angstrom (20-69): a constant TOX becomes TOX*1e-10, an expression
        (t > 1 ? t*1e-10 : t).
      COX (CO), which neither target has (_mos_cox): tox=eps_ox/COX, HSPICE's "TOX calculated
        from COX when COX is input" (20-69, 21-2, 21-7, 21-19), replacing a given TOX; at LEVEL 1
        a warning: "the model parameter TOX must be specified to invoke the Meyer model" (20-57)
        and KP=UO*COX needs "UO and TOX entered" (21-2), and whether a COX-derived TOX counts is not
        documented, while the target with TOX has the Meyer capacitance (and KP=UO*COX).
      KP and UO (_mos_kp): the LEVEL 1 KP default 2.0718e-5 / 8.632e-6 (21-2), the PMOS UO
        default 250 (21-11, 21-22), UO derived from KP at LEVEL 2/3; KP given with only one of
        UO and TOX, or not given at all at LEVEL 2/3 is a warning: the manual leaves it ambiguous.
      CGSO, CGDO not given but LD or METO and TOX: (LD+METO)*COX; CGBO not given but WD and
        TOX: 2*WD*COX (20-69, 20-73).  METO (no target has it) is removed.
      LEVEL 3: ETA scaled by 8.14/8.15 (HSPICE's constant over SPICE3's, 21-29); 0 < XJ < 0.05u
        is a warning (HSPICE limits fs to 1 there, SPICE3 does not).  badmos3=1: HSPICE's channel-
        length modulation (21-26: dL = Xd*sqrt(KAPPA*(vds-vdsat)) above vdsat with VMAX=0, the
        pinch-off field Ep without KAPPA with VMAX>0) is SPICE2's, SPICE3's .option badmos3;
        VACASK's sp_mos3 defaults to ngspice's modified CLM (dL from vds-vdsat+vdsat/8, and a
        vds^4 onset below vdsat: +1.2% on a 1u device), Xyce's to SPICE3f's KAPPA*Ep.
      Bulk junctions (_mos_junctions, 20-26..20-48; not for ACM=1, whose CJ is per width):
        no CJ (CDB CSB CJA): HSPICE's default for option ASPEC=0, sqrt(eps_si*q*NSUB/(2*PB)), with
        NSUB as _mos_body has it (under .option spice: only a given NSUB); both targets use 0
        (ngspice's and Xyce's level 2 compute it and never use it).  HSPICE's MOS junction
        capacitance is linear in forward bias (FC "not used"): fc=0 wherever there is one.  PHP
        is removed (neither target has it; their sidewall uses PB): PHP other than PB on a card
        with a sidewall capacitance is a warning.  mos_junction_warnings adds the per-instance
        warnings (the ambiguous default without NSUB, CBD/CBS HSPICE would not use).
      CAPOP: capop=0 is removed (SPICE's Meyer model is what both targets simulate), any other
        CAPOP is a warning and removed; none is a warning that HSPICE's default CAPOP=2 is
        simulated as CAPOP=0 (20-57, Table 20-4), except under .option spice (CAPOP=0) and at
        LEVEL 1 without TOX (no gate capacitance in HSPICE either, 20-57).
    Unless .option spice (9-14: LD=0, NSUB must be given):
      NSUB, GAMMA, PHI, VTO (_mos_body): HSPICE derives them from NSUB (default 1e15, or from
        GAMMA when only GAMMA is given), the targets only when NSUB is given (LEVEL 1: and TOX).
      LD not given but XJ (LEVEL 2, 3; the LEVEL 1 targets reject XJ): LD = 0.75*XJ (20-70, 21-29).
      CJSW (CJP) given but no MJSW (EXP): mjsw=0.33, HSPICE's MOS default (20-28); sp_mos1/2 and
        Xyce's LEVEL 1/2 default it to 0.5.
    """
    tox = c.p.get("tox")
    if isinstance(tox, Num) and tox.value > 1.0:
        c.put("tox", Num(tox.value * 1e-10), "TOX above 1 is in Angstrom")
    elif tox is not None and not isinstance(tox, Num):
        c.put("tox", Ternary(Binary(">", tox, Num(1.0)), Binary("*", tox, Num(1e-10)), tox),
              "a TOX above 1 is in Angstrom")
    _mos_cox(c, lv)
    tox = c.p.get("tox")
    has_tox = _nonzero(tox)                     # SPICE3 LEVEL 1: no TOX (or 0), no oxide capacitance
    cox = _div(Num(EPS_OX), tox) if has_tox else Num(MOS_COX)
    _mos_kp(c, lv, pol, has_tox, cox)
    nsub_k = c.key("nsub", "dnb", "nb")
    nsub: Optional[Expr] = c.p[nsub_k] if nsub_k is not None else None     # SPICE's: only a given one
    if not opts.spice:
        nsub = _mos_body(c, lv, has_tox, cox)
        xj = c.p.get("xj")
        if lv != 1 and c.key("ld", "dlat", "latd") is None and _nonzero(xj):
            c.put("ld", _mul(Num(0.75), xj), "HSPICE's default LD=0.75*XJ")
        cjsw = c.key("cjsw", "cjp")
        if cjsw is not None and _nonzero(c.p[cjsw]) and c.key("mjsw", "exp") is None:
            c.put("mjsw", Num(MOS_MJSW), "HSPICE's default MJSW")
    _mos_junctions(c, nsub)
    meto = c.drop("meto", "no target has it; HSPICE uses it only for CGSO and CGDO") if "meto" in c.p else None
    if has_tox:
        over = [e for e in (c.p.get("ld"), meto) if _nonzero(e)]
        if over:
            lov = over[0] if len(over) == 1 else X.fold(Binary("+", over[0], over[1]))
            for k, aliases in (("cgso", ("cgso", "cgs", "c1")), ("cgdo", ("cgdo", "cgd", "c2"))):
                if c.key(*aliases) is None:
                    c.put(k, _mul(lov, cox), "HSPICE computes it from %s and TOX"
                          % ("LD and METO" if len(over) == 2 else "METO" if meto is over[0] else "LD"))
        if _nonzero(c.p.get("wd")) and c.key("cgbo", "cgb") is None:
            c.put("cgbo", _mul(Num(2.0), c.p["wd"], cox), "HSPICE computes it from WD and TOX")
    if lv == 3:
        if _nonzero(c.p.get("eta")):
            c.put("eta", _mul(c.p["eta"], Num(ETA_RATIO)), "HSPICE's ETA term uses 8.14, SPICE3's 8.15")
        xj = c.p.get("xj")
        if isinstance(xj, Num) and 0.0 < xj.value < XJ_SMALL:
            c.warn("xj=%s is below 0.05u: HSPICE limits the short-channel factor fs to 1 there, both "
                   "targets (SPICE3) do not" % _txt(xj))
        if "badmos3" not in c.p:
            c.put("badmos3", Num(1.0), "HSPICE's channel-length modulation (21-26) is SPICE2's: the targets' "
                  "badmos3=1, not ngspice's modified one (VACASK) or SPICE3f's KAPPA*Ep (Xyce)")
    capop = c.p.get("capop")
    if capop is not None:
        if _is_zero(capop):
            c.drop("capop", "SPICE's Meyer gate capacitance, which both targets simulate")
        else:
            c.p.pop("capop")
            c.warn("HSPICE's CAPOP=%s gate capacitance is not available on either engine; simulated "
                   "with SPICE's Meyer model (CAPOP=0)" % _txt(capop))
    elif not opts.spice and (lv != 1 or has_tox):
        c.warn("HSPICE's default CAPOP=2 gate capacitance (parameterized modified Meyer) is simulated as "
               "SPICE's Meyer model (CAPOP=0); add capop=0 to the card (or .option spice) to make HSPICE "
               "use the same model")


def _mos_cox(c: _Card, lv: int) -> None:
    """COX (alias CO) on a MOS 1/2/3 card becomes tox=EPS_OX/COX (see _mos123)."""
    k = c.key("cox", "co")
    if k is None:
        return
    cox = c.drop(k, "neither target has it; HSPICE calculates TOX from COX when COX is input (20-69)")
    if not _nonzero(cox):
        raise TableError("model %s: %s=%s: the oxide capacitance must be positive" % (c.model.name, k, _txt(cox)))
    given = c.p.get("tox")
    c.put("tox", _div(Num(EPS_OX), cox), "eps_ox/%s%s" % (k.upper(), "" if given is None else
                                                           "; the given tox=%s is replaced" % _txt(given)))
    if lv == 1:
        c.warn("%s=%s with LEVEL 1: HSPICE invokes the Meyer gate capacitance only when TOX is specified (20-57) "
               "and computes KP=UO*COX when UO and TOX are entered (21-2); whether a TOX calculated from COX "
               "counts is not documented; simulated with tox=eps_ox/COX, so with the Meyer capacitance (and KP=UO*COX "
               "when KP is not given); give TOX instead of COX to remove the ambiguity" % (k, _txt(cox)))


def _mos_kp(c: _Card, lv: int, pol: float, has_tox: bool, cox: Expr) -> None:
    """KP and UO (see _mos123).  HSPICE: KP = UO*COX "if KP is not specified and UO and TOX are
    entered"; else the LEVEL 1 default 2.0718e-5 (NMOS) / 8.632e-6 (PMOS) and the LEVEL 2/3
    column 2.0e-5; UO defaults to 600 (NMOS) / 250 (PMOS) and at LEVEL 2/3 "is calculated from
    KP if KP is input" (21-2, 21-7, 21-11, 21-19, 21-22).  The targets: KP = UO*COX whenever KP
    is not given and there is an oxide capacitance (UO 600 for both types), else 2e-5."""
    kp_k, uo_k = c.key("kp", "bet", "beta"), c.key("uo", "u0", "ub", "ubo")
    kind = "NMOS" if pol > 0 else "PMOS"
    if lv == 1:
        if kp_k is not None or (uo_k is not None and has_tox):
            return                                  # the card's KP, or UO*COX on both
        if uo_k is None and not has_tox:
            c.put("kp", Num(MOS_KP1[pol]), "HSPICE's LEVEL 1 default KP for %s" % kind)
            return
        uo = c.p[uo_k] if uo_k is not None else Num(MOS_UO[pol])
        kp = _mul(uo, Num(1e-4), cox)
        if not has_tox:
            c.put("kp", kp, "UO*COX with the default COX; the target ignores UO without TOX")
        elif pol < 0:
            c.put("uo", Num(MOS_UO[pol]), "HSPICE's default UO for PMOS")
        c.warn("KP is not given and only %s is: HSPICE documents KP=UO*COX when both UO and TOX are given "
               "and the default 2.0718e-5 (NMOS) / 8.632e-6 (PMOS) otherwise; simulated with KP=UO*COX=%s "
               "(UO %s); give KP to remove the ambiguity" % ("UO" if uo_k else "TOX", _txt(kp), _txt(uo)))
        return
    if kp_k is not None:
        if uo_k is None:
            c.put("uo", _div(c.p[kp_k], _mul(cox, Num(1e-4))), "HSPICE computes UO from KP")
        return
    if uo_k is None and pol < 0:
        c.put("uo", Num(MOS_UO[pol]), "HSPICE's default UO for PMOS")
    if uo_k is None or not has_tox:
        uo = c.p[uo_k] if uo_k is not None else Num(MOS_UO[pol])
        c.warn("KP is not given: HSPICE documents KP=UO*COX when UO and TOX are given and a LEVEL %d "
               "default of 2.0e-5 otherwise; simulated with KP=UO*COX=%s (UO %s, TOX %s); give KP to "
               "remove the ambiguity" % (lv, _txt(_mul(uo, Num(1e-4), cox)), _txt(uo),
                                         _txt(c.p["tox"]) if has_tox else "1e-07"))


def _mos_body(c: _Card, lv: int, has_tox: bool, cox: Expr) -> Optional[Expr]:
    """NSUB, GAMMA, PHI and VTO (see _mos123); not under .option spice.  Returns the NSUB HSPICE
    simulates (given, derived from GAMMA or the default 1e15), None when there is none.

    HSPICE: NSUB defaults to 1e15 or, when only GAMMA is given, is derived from it; GAMMA, PHI
    and VTO not given are computed from NSUB (20-50, 20-51), and NSUB drives the LEVEL 2/3
    depletion terms (level 3's KAPPA is inactive without it, 21-29).  The targets read NSUB only
    when it is given (LEVEL 1: and only with TOX) and default GAMMA to 0, PHI to 0.6, VTO to 0.
    So NSUB is written where the target needs it, PHI always (phi_of: HSPICE's default at
    NSUB=1e15, where the targets' own formulas differ), and at LEVEL 1 without TOX GAMMA and PHI
    are written instead: there the target ignores NSUB, and a missing VTO is a warning.
    """
    nsub_k, gamma_k = c.key("nsub", "dnb", "nb"), c.key("gamma")
    phi_k, vto_k = c.key("phi"), c.key("vto", "vt0", "vt")
    nsub: Optional[Expr]
    if nsub_k is not None:
        nsub, why = c.p[nsub_k], ""
    elif gamma_k is not None:                    # GAMMA=0: no body effect and no NSUB to derive
        g = c.p[gamma_k]
        nsub, why = (None, "") if _is_zero(g) else (nsub_of(g, cox), "HSPICE derives NSUB from GAMMA")
    else:
        nsub, why = Num(MOS_NSUB), "HSPICE's default NSUB"
    if isinstance(nsub, Num) and nsub.value <= NI_300:
        c.warn("NSUB=%.4g%s is at or below the intrinsic density 1.45e10: the NSUB-derived terms HSPICE "
               "computes (PHI, VTO, the LEVEL 2/3 depletion terms) are not simulated"
               % (nsub.value, "" if nsub_k else " (from gamma=%s)" % _txt(c.p[gamma_k])))
        nsub = None
    if nsub is None:
        if phi_k is None:
            c.put("phi", Num(PHI_DEFAULT), "HSPICE's default PHI")
        if vto_k is None:
            c.warn("VTO is not given: HSPICE computes it from NSUB, PHI, GAMMA and TPG (20-51), but the "
                   "target uses VTO=0 here; give VTO")
        return None
    if has_tox or lv != 1:
        if nsub_k is None and (lv != 1 or gamma_k is None or phi_k is None or vto_k is None):
            c.put("nsub", nsub, why)
        if phi_k is None:
            c.put("phi", phi_of(nsub), "HSPICE's PHI from NSUB")
        return nsub
    if gamma_k is None:
        c.put("gamma", gamma_of(nsub, Num(MOS_COX)), "HSPICE's GAMMA from NSUB and the default COX; "
              "the LEVEL 1 target without TOX ignores NSUB")
    if phi_k is None:
        c.put("phi", phi_of(nsub), "HSPICE's PHI from NSUB")
    if vto_k is None:
        c.warn("VTO is not given: HSPICE computes it from NSUB, PHI, GAMMA and TPG (20-51), but the "
               "LEVEL 1 target without TOX uses VTO=0; give VTO (or TOX)")
    return nsub


def _mos_junctions(c: _Card, nsub: Optional[Expr]) -> None:
    """The bulk junctions of a MOS LEVEL 1, 2 or 3 card (see _mos123).

    HSPICE's MOS diode (20-26..20-48) with ACM 0, 2 or 3 (vamos removes ACM with a warning and the
    targets compute AD*CJ+PD*CJSW as ACM=0 does; ACM=1's CJ is per width and is left alone):
    CJ defaults to sqrt(eps_si*q*NSUB/(2*PB)) for ASPEC=0 (20-28; ASPEC=1, which vamos refuses,
    sets 0, 21-86), PB to 0.8 (both targets' too), PHP to PB; FC is not used: below 0 V the
    capacitance is CJ*(1-v/PB)^-MJ, above it CJ*(1+MJ*v/PB) (20-47), SPICE's formula with FC=0.
    The targets have no PHP (SPICE3's MOS sidewall uses PB) and default CJ to 0 and FC to 0.5.
    """
    acm = next((v for k, v in c.model.params.items() if k.lower() == "acm"), None)
    if isinstance(acm, Num) and acm.value == 1.0:
        return
    pb_k = c.key(*MOS_PB_KEYS)
    pb = c.p[pb_k] if pb_k is not None else Num(MOS_PB)
    if c.key(*MOS_CJ_KEYS) is None and nsub is not None and _nonzero(nsub):
        c.put("cj", Call("sqrt", (_div(_mul(Num(EPS_SI * Q_E * 1e6), nsub), _mul(Num(2.0), pb)),)),
              "HSPICE's default for option ASPEC=0, sqrt(eps_si*q*NSUB/(2*PB)) (20-28)")
    caps = MOS_CJ_KEYS + ("cjsw", "cjp") + MOS_CBX_KEYS
    if any(_nonzero(c.p.get(k)) for k in caps) and not _is_zero(c.p.get("fc")):
        was = c.p.get("fc")
        c.put("fc", Num(0.0), "HSPICE does not use FC for MOS junctions (20-28): its forward-bias depletion "
              "capacitance is SPICE's with FC=0%s" % ("" if was is None else "; fc=%s ignored" % _txt(was)))
    php = c.p.get("php")
    if php is not None:
        same = X.to_text(X.fold(php)) == X.to_text(X.fold(pb))
        side = c.key("cjsw", "cjp")
        c.drop("php", "neither target has it: their sidewall junction uses PB")
        if not same and side is not None and _nonzero(c.p[side]):
            c.warn("php=%s differs from PB=%s: HSPICE's sidewall junction uses PHP (20-47), both targets PB; "
                   "simulated with PB" % (_txt(php), _txt(pb)))


def _bsim3(c: _Card, lv: int) -> None:
    """BSIM3 (LEVEL 49, 53) on Berkeley's code (sp_bsim3v3, Xyce level 9): HSPICE's defaults (22-31,
    22-34, 22-43).

    LEVEL 49 without XPART: xpart=1 (0/100 charge partition; BSIM3 and LEVEL 53: 0, 40/60).
    No CAPMOD: HSPICE's default follows VERSION (default 3.2), 22-31: 3.2 and later 3, the
    targets' own; 3.0: capmod=1; 3.1: capmod=2, exact at LEVEL 53, a warning at LEVEL 49, whose
    default is HSPICE's own CAPMOD=0 (a modified BSIM1 model based on CAPOP=13) that neither
    engine has.  A VERSION that is not a constant is a warning.
    LEVEL 49 without ACM: a warning that HSPICE's default ACM=0 junction model is simulated as
    the Berkeley junctions (HSPICE ACM=10).  Where HSPICE's own junction model applies (LEVEL 49
    without ACM, or ACM 0, 2 or 3 at either level) its defaults CJ=5.79e-4 and CJSW=0 (22-43,
    "Default deviates from BSIM3v3": 5e-4 and 5e-10) are written: the targets' bottom junction
    then has HSPICE's capacitance.  With ACM=10 (removed with a note, model_params) the defaults
    are Berkeley's, which both targets have: LEVEL 53 keeps "identical parameter default values"
    and ACM=10 is how LEVEL 49 achieves "compliance with Berkeley BSIM3v3" (22-19), so
    the table's JS=0, CJ=5.79e-4 and CJSW=0 belong to the ACM=0 default.
    """
    acm = next((v for k, v in c.model.params.items() if k.lower() == "acm"), None)
    if lv == 49:
        if "xpart" not in c.p:
            c.put("xpart", Num(1.0), "HSPICE's LEVEL 49 default (0/100 partition); BSIM3's is 0")
        if acm is None:
            c.warn("HSPICE's LEVEL 49 default ACM=0 source/drain junction model (its own diode equations: "
                   "N, PHP and CJGATE; NJ, CJSWG, MJSWG, PBSW and PBSWG are not used) is simulated as "
                   "BSIM3's Berkeley junctions (HSPICE ACM=10); add acm=10 to the card to make HSPICE "
                   "use the same model")
    if (lv == 49 and acm is None) or (isinstance(acm, Num) and acm.value in BSIM3_HSPICE_ACM):
        for k, v, berkeley in (("cj", BSIM3_HSPICE_CJ, "5e-4"), ("cjsw", 0.0, "5e-10")):
            if k not in c.p:
                c.put(k, Num(v), "HSPICE's default with its own junction model (ACM 0-3); BSIM3's is %s"
                      % berkeley)
    if "capmod" in c.p:
        return
    v = c.p.get("version")
    if v is not None and not isinstance(v, Num):
        c.warn("version=%s is not a constant: HSPICE's CAPMOD default depends on it (1 for 3.0, 2 or "
               "HSPICE's own 0 for 3.1, 3 from 3.2); simulated with CAPMOD=3" % _txt(v))
        return
    tenth = int(math.floor((3.2 if v is None else v.value) * 10.0 + 1e-6))
    if tenth == 30:
        c.put("capmod", Num(1.0), "HSPICE's (and BSIM3's) CAPMOD default for VERSION 3.0")
    elif tenth == 31:
        if lv == 53:
            c.put("capmod", Num(2.0), "HSPICE's (and BSIM3's) CAPMOD default for VERSION 3.1")
        else:
            c.put("capmod", Num(2.0), "BSIM3's CAPMOD default for VERSION 3.1")
            c.warn("HSPICE's LEVEL 49 VERSION 3.1 default CAPMOD=0 is its own charge model (a modified "
                   "BSIM1 model based on CAPOP=13), which neither engine has; simulated with BSIM3's "
                   "CAPMOD=2; give capmod to choose")


_CAPS_AREA = {"d": ("cjo", "cj", "cj0", "cja"), "q": ("cje", "cjc"), "j": ("cgs", "cgd")}
_PB_KEYS = ("vj", "pb", "phi", "pha")            # the diode's PB and its HSPICE aliases (15-12)


def _junctions(c: _Card, elem: str, opts: ModelOptions) -> None:
    """D (LEVEL 1, 3), Q and J: depletion capacitance and junction defaults.

    DCAP: the card's own dcap= (removed: no target has it), else .option dcap, else 1 under
      .option spice, else HSPICE's default 2 (15-4, 16-2, 17-6).  DCAP=2 (15-27, 16-35..37,
      17-26) is C = CJ*(1-v/PB)^-M below 0 and CJ*(1+M*v/PB) above: SPICE's formula with FC=0.
      So a card with a depletion capacitance gets fc=0 (fcs=0 for a diode's sidewall) and any
      FC it gives is replaced (HSPICE ignores it under DCAP=2); DCAP=1 is the targets' own
      formula; DCAP=3 (peak-limited) is a TableError.  The BJT substrate capacitance is linear
      in forward bias on HSPICE and on both targets.
    Diode: PB (aliases VJ, PHI, PHA) is printed vj, the name both targets know; with a depletion
      capacitance and no PB, vj=0.8; with a sidewall capacitance and no PHP, php=PB (HSPICE's
      defaults, 15-12; both targets default to 1.0).
    BJT (not under .option spice, MJS=0 there): a substrate capacitance without MJS gets mjs=0.5.
    JFET: a gate capacitance without PB gets pb=0.8; capop=0 (HSPICE's default, SPICE's model)
      is removed, another CAPOP is a warning and removed.
    """
    dk = c.key("dcap")
    if dk is not None:
        d = c.p[dk]
        if not isinstance(d, Num) or d.value not in DCAPS:
            raise TableError("model %s: dcap=%s must be 1, 2 or 3" % (c.model.name, _txt(d)))
        dcap = d.value
        c.drop(dk, "no target has it; it selects the depletion-capacitance equations")
    else:
        dcap = opts.dcap if opts.dcap is not None else 1.0 if opts.spice else 2.0
    area = [k for k in _CAPS_AREA[elem] if _nonzero(c.p.get(k))]
    side = [k for k in ("cjp", "cjsw") if elem == "d" and _nonzero(c.p.get(k))]
    if (area or side) and dcap == 3.0:
        raise TableError("model %s: DCAP=3 (peak-limited depletion capacitance) has no VACASK or Xyce "
                         "equivalent; use DCAP=1 or 2" % c.model.name)
    if dcap == 2.0:
        # fc=0 for a sidewall-only diode too, as HSPICE ignores FC under DCAP=2 (the engines'
        # sidewall charge takes its own F1 from FCS since round 6, P14: VACASK diode.va
        # DIOtF1SW, Xyce tF1SW; before, both used the area's F1, which this also matched)
        for k, used in (("fc", area + side), ("fcs", side)):
            if used and not _is_zero(c.p.get(k)):
                was = c.p.get(k)
                c.put(k, Num(0.0), "HSPICE's default DCAP=2 forward-bias capacitance is SPICE's with %s=0%s"
                      % (k.upper(), "" if was is None else "; HSPICE ignores %s=%s under DCAP=2" % (k, _txt(was))))
    if elem == "d":
        given = [k for k in c.p if k in _PB_KEYS]
        if given and given != ["vj"]:
            pb = c.p[given[-1]]                     # the last one written, as for a repeated key
            for k in given:
                c.p.pop(k)
            c.put("vj", pb, "%s: VACASK and Xyce know PB as VJ" % ", ".join(given))
        if (area or side) and "vj" not in c.p:
            c.put("vj", Num(DIODE_PB), "HSPICE's default PB")
        if side and c.key("php", "vjsw") is None:
            c.put("php", c.p.get("vj", Num(DIODE_PB)), "HSPICE's default PHP=PB")
    elif elem == "q":
        cjs = c.key("cjs", "ccs", "csub")
        if not opts.spice and cjs is not None and _nonzero(c.p[cjs]) and c.key("mjs", "ms", "esub") is None:
            c.put("mjs", Num(BJT_MJS), "HSPICE's default MJS")
    else:
        if area and "pb" not in c.p:
            c.put("pb", Num(JFET_PB), "HSPICE's default PB")
        capop = c.p.get("capop")
        if capop is not None:
            if _is_zero(capop):
                c.drop("capop", "HSPICE's default, SPICE's JFET capacitance, which both targets simulate")
            else:
                c.p.pop("capop")
                c.warn("HSPICE's JFET CAPOP=%s gate capacitance is not available on either engine; "
                       "simulated with SPICE's (CAPOP=0)" % _txt(capop))


# -- instance rules (§4.3.5, §4.3.7) ----------------------------------------------

SCALE_POWERS: Dict[str, Dict[str, int]] = {
    "m": {"w": 1, "l": 1, "ad": 2, "as": 2, "pd": 1, "ps": 1},
    # a geometric (LEVEL 3) diode only: a LEVEL 1 diode's AREA and PJ are unitless factors
    # that SCALE does not touch (Star-HSPICE manual, "Diode Element", "LEVEL=1 Scaling")
    "d": {"area": 2, "pj": 1, "w": 1, "l": 1, "wp": 1, "lp": 1, "wm": 1, "lm": 1},
    "r": {"w": 1, "l": 1},
    "c": {"w": 1, "l": 1},
}


def times(a: Expr, b: Expr) -> Expr:
    """a*b, folded when both are numbers; a factor of 1.0 disappears."""
    if isinstance(a, Num) and isinstance(b, Num):
        return Num(a.value * b.value)
    if isinstance(b, Num) and b.value == 1.0:
        return a
    if isinstance(a, Num) and a.value == 1.0:
        return b
    return Binary("*", a, b)


def scaled(element: str, name: str, value: Expr, s: float, level: Optional[int] = None) -> Expr:
    """value times s**power for a geometry parameter of element (§4.3.5); unchanged otherwise.

    level is the card's level; it matters for a diode: only LEVEL 3 is geometric, a LEVEL 1
    diode's AREA and PJ are not affected by .option scale.
    """
    if element == "d" and level != 3:
        return value
    p = SCALE_POWERS.get(element, {}).get(name)
    if not p or s == 1.0:
        return value
    return times(value, Num(s ** p))


MULT: Dict[str, Dict[str, str]] = {
    "vacask": {"x": "param", "r": "param", "c": "param", "l": "param", "d": "param",
               "q": "param", "m": "param", "j": "param", "y": "param", "i": "param",
               "g": "param", "f": "param", "b": "fold", "v": "none", "e": "none",
               "h": "none", "k": "none"},
    "xyce": {"x": "param", "r": "param", "c": "param", "l": "param", "d": "param",
             "q": "param", "m": "param", "j": "area", "y": "param", "i": "values",
             "g": "param", "f": "gain", "b": "fold", "v": "none", "e": "none",
             "h": "none", "k": "none"},
}
MF_PARAM = {"vacask": "$mfactor", "xyce": "vamos_mfactor"}

# Elements whose instance parameters are not passed through to a device: the
# only parameter they may carry is m (applied per MULT); anything else is an
# error rather than a silently dropped setting.
CLOSED_PARAMS: Dict[str, FrozenSet[str]] = {k: frozenset(("m",)) for k in "vieghfkb"}


def check_params(inst: Instance) -> None:
    """TableError for a parameter an element of a CLOSED_PARAMS kind cannot take."""
    allowed = CLOSED_PARAMS.get(inst.kind)
    if allowed is None:
        return
    bad = sorted(k for k in inst.params if k not in allowed)
    if bad:
        raise TableError("%s: parameter%s %s not supported on a %s element"
                         % (inst.name, "s" if len(bad) > 1 else "", ", ".join(bad), inst.kind.upper()))


def multiplier(m: Optional[Expr], in_subckt: bool, engine: str) -> Optional[Expr]:
    """The effective multiplier of an instance whose own m= is m (None if absent)."""
    _engine(engine)
    if in_subckt:
        mf = Name(MF_PARAM[engine])
        return mf if m is None else times(mf, m)
    return m


# Elements whose branch current both engines can read inside a behavioral expression: VACASK
# reads <inst>:flow(br), Xyce I(<dev>); R, C, D, F, G, M, ... have no such branch on either
# ("Controlling unknown 'r1:flow(br)' not found"; Xyce aborts), though HSPICE reads i() of them.
BRANCH_KINDS = ("v", "l", "e", "h")


def has_branch(inst: Instance) -> bool:
    """inst has a branch current both engines can read in an expression (and probe as i())."""
    return inst.kind in BRANCH_KINDS or (inst.kind == "b" and inst.expr_kind == "v")


def element_at(items: Sequence[object], scope: "Scope", target: str) -> Optional[Instance]:
    """The element a (hierarchical, x1.x2.r1) name of a v()/i() reference names, relative to the
    body items of scope; None when there is none."""
    parts = target.split(".")
    for k, part in enumerate(parts):
        found = next((it for it in items if isinstance(it, Instance) and it.name == part), None)
        if found is None or k == len(parts) - 1:
            return found
        sub = scope.where(found.master or "") if found.kind == "x" else None
        if sub is None:
            return None
        scope = Scope(sub[0].body, sub[1], sub[0])
        items = sub[0].body
    return None


def current_ref_errors(inst: Instance, items: Sequence[object], scope: "Scope") -> List[str]:
    """Messages for the i() references of a behavioral element's expression that name an element
    without a branch current (has_branch): both engines fail on them, inside the engine."""
    out: List[str] = []
    if inst.expr is None:
        return out
    for e in X.walk(inst.expr):
        if not (isinstance(e, Call) and e.func == "i" and len(e.args) == 1 and isinstance(e.args[0], Name)):
            continue
        target = e.args[0].name
        el = element_at(items, scope, target)
        if el is None or has_branch(el):
            continue
        msg = ("i(%s) in the expression of %s: the %s element has no branch current VACASK or Xyce can read "
               "in an expression (V, L, E, H and E VOL= elements have one); put a 0 V source in series with "
               "%s and use i() of it" % (target, inst.name, el.kind.upper(), target))
        if msg not in out:
            out.append(msg)
    return out


def item_exprs(item: object) -> List[Expr]:
    """Every expression of one body item (not of the definitions nested in a Subckt): a Param's,
    an Instance's value, parameters, behavioral expression and source fields, a Model's parameters,
    a Subckt's header parameters."""
    out: List[Expr] = []
    if isinstance(item, Param):
        out.append(item.expr)
    elif isinstance(item, Model):
        out.extend(item.params.values())
    elif isinstance(item, Subckt):
        out.extend(p.expr for p in item.params)
    elif isinstance(item, Instance):
        out.extend(e for e in (item.value, item.expr) if e is not None)
        out.extend(item.params.values())
        s = item.source
        if s is not None:
            out.extend(e for e in (s.dc, s.ac[0] if s.ac else None, s.ac[1] if s.ac else None) if e is not None)
            out.extend(s.args.values())
            for t, v in s.points:
                out.extend((t, v))
    return out


def enclosing_reads(s: Subckt, enclosing: Sequence[str]) -> List[str]:
    """The parameters of enclosing subckts (enclosing: their names) that the body of the nested
    definition s reads and does not declare itself, sorted."""
    own = {p.name for p in s.params} | {p.name for p in s.body if isinstance(p, Param)}
    want = set(enclosing) - own
    found: Set[str] = set()
    for it in s.body:
        if isinstance(it, Subckt):
            continue
        for e in item_exprs(it):
            found |= X.names(e) & want
    return sorted(found)


MOS_STRESS_KEYS = ("sa", "sb", "sd", "sc")


def mos_scale_warnings(inst: Instance, options: Optional[Mapping[str, Expr]] = None) -> List[Note]:
    """An M instance giving the BSIM4 layout distances SA, SB, SD or SC under .option scale other than
    1: vamos scales W, L, AD, AS, PD and PS (SCALE_POWERS), as ngspice's BSIM4 code (VACASK's
    sp_bsim4v8) does with its own scale, and passes the distances as written; whether HSPICE's SCALE
    ("scales element statement parameters", MOSFET Models X-2005.09 p. 11) reaches them is not
    documented, and the two readings differ by the scale factor: a warning."""
    s = (options or {}).get("scale")
    if not isinstance(s, Num) or s.value == 1.0:
        return []
    given = [k for k in MOS_STRESS_KEYS if _nonzero(inst.params.get(k))]
    if not given:
        return []
    return [warning(inst.origin or inst.name,
                    "%s: %s under .option scale=%g: both targets read %s as written (in meters, unscaled); "
                    "whether HSPICE's SCALE applies to %s is not documented"
                    % (inst.name, "/".join(given), s.value, "it" if len(given) == 1 else "them",
                       "it" if len(given) == 1 else "them"))]


# Parameters coupled_inductance folds into a coupled inductor's value (the emitters print none of them).
COUPLED_FOLDED = ("m", "tc1", "tc2", "dtemp")


def coupled_inductance(inst: Instance, mult: Optional[Expr], card: Optional[Model], temp: float,
                       tnom: float) -> Optional[Expr]:
    """The value a coupled inductor (one a K element of its scope names, Scope.coupled) is printed
    with: None when it is printed as written (no multiplier, no temperature coefficient).

    Neither engine couples such an inductor as HSPICE does: VACASK's mutual divides each
    inductance by its inductor's $mfactor and couples the per-copy branch currents, so the
    coupling is divided by m twice, and it reads the nominal inductance (no TC1/TC2); Xyce's K
    pass keeps only L and IC of a coupled inductor's line and drops m=, TC1 and TC2.  So the
    inductance HSPICE simulates, M parallel inductors of L*(1+TC1*dt+TC2*dt^2) with
    dt = temp+DTEMP-tnom (Star-HSPICE 2001.2, 4-8 and 14-17), is folded into the value, mult
    being the effective multiplier (tables.multiplier), and the emitters print the inductor
    without COUPLED_FOLDED; K is unchanged.  i() of such an inductor is its total current.
    TableError when the inductance is not on the element (a model card's value cannot be folded)
    or the model card has TC1/TC2.
    """
    tc1, tc2, dtemp = (inst.params.get(k) for k in ("tc1", "tc2", "dtemp"))
    card_tc = card is not None and any(k in card.params for k in ("tc1", "tc2"))
    if mult is None and tc1 is None and tc2 is None and not card_tc:
        return None
    if card_tc:
        raise TableError("%s: coupled inductor with model %s, whose TC1/TC2 neither engine applies to a "
                         "coupled inductor; give tc1/tc2 on the element" % (inst.name, card.name if card else ""))
    if inst.value is None:
        raise TableError("%s: a coupled inductor needs its inductance on the element: neither engine couples "
                         "an inductor with %s as HSPICE does, so vamos folds %s into the element's value, and "
                         "a model card's inductance cannot be folded"
                         % (inst.name, "a multiplier (its m=, or the one of an enclosing subckt instance)"
                            if mult is not None else "TC1/TC2", "it" if mult is not None else "them"))
    value = inst.value
    if tc1 is not None or tc2 is not None:
        dt: Expr = Num(float(temp) - float(tnom))
        if dtemp is not None:
            dt = Binary("+", dt, dtemp)
        f: Expr = Num(1.0)
        if tc1 is not None:
            f = Binary("+", f, Binary("*", tc1, dt))
        if tc2 is not None:
            f = Binary("+", f, Binary("*", tc2, Binary("*", dt, dt)))
        value = Binary("*", value, f)
    if mult is not None:
        value = Binary("/", value, mult)
    return X.fold(value)


def run_temps(nl: Netlist) -> Tuple[float, float]:
    """(temp, tnom) of a run in Celsius, as both emitters' control blocks print them: Netlist.temp
    and .tnom, 25 each when absent (27 under .option spice)."""
    default = 27.0 if "spice" in nl.options else 25.0
    out: List[float] = []
    for v in (nl.temp, nl.tnom):
        if v is None:
            out.append(default)
        elif isinstance(v, (int, float)):
            out.append(float(v))
        else:
            try:
                out.append(X.evaluate(v, nl.values))       # type: ignore[arg-type]
            except X.EvalError as exc:
                raise TableError("temp and tnom must be constants: %s" % exc)
    return out[0], out[1]


# -- binning (§4.3.6) ---------------------------------------------------------------

def bin_bounds(model: Model, values: Mapping[str, float]) -> Tuple[float, float, float, float]:
    """(lmin, lmax, wmin, wmax) of a bin card, evaluated with the top-level values."""
    out = []
    for k in BIN_KEYS:
        e = model.params.get(k)
        if e is None:
            raise TableError("binned model %s has no %s" % (model.name, k))
        try:
            out.append(X.evaluate(e, values))
        except X.EvalError as exc:
            raise TableError("binned model %s: %s=%s is not constant (%s): vamos evaluates bin bounds with "
                             "the top-level parameters only, so a bin card in a subckt cannot read the "
                             "subckt's parameters; give constant bounds" % (model.name, k, X.to_text(e), exc))
    return out[0], out[1], out[2], out[3]


def _in_range(x: Expr, lo: float, hi: float) -> Expr:
    lo_e, hi_e = Num(lo), Num(hi)
    lower = Binary("||", Binary(">=", x, lo_e),
                   Binary("<", Call("abs", (Binary("-", x, lo_e),)), Num(BIN_TOL)))
    return Binary("&&", lower, Binary("<", x, hi_e))


def bin_guard(bounds: Sequence[float], l: Expr, w: Expr, nf: Optional[Expr], s: float) -> Expr:
    """The selection condition of one bin for an instance (l, w unscaled; s applied once)."""
    lmin, lmax, wmin, wmax = bounds
    lx = times(l, Num(s))
    wx = times(w, Num(s))
    if nf is not None:
        wx = Binary("/", wx, nf)
    return Binary("&&", _in_range(lx, lmin, lmax), _in_range(wx, wmin, wmax))


def _in_range_f(x: float, lo: float, hi: float) -> bool:
    return (x >= lo or abs(x - lo) < BIN_TOL) and x < hi


def select_bin(bins: Sequence[Tuple[Model, Tuple[float, float, float, float]]], l: float,
               w: float, nf: float, s: float) -> Optional[Model]:
    """The first bin whose bounds hold l*s and w*s/nf (the rule bin_guard prints); None if none."""
    lx, wx = l * s, w * s / nf
    for m, (lmin, lmax, wmin, wmax) in bins:
        if _in_range_f(lx, lmin, lmax) and _in_range_f(wx, wmin, wmax):
            return m
    return None


# Spectre model groups, bin_rule="spectre" (VAMOS_SPECTRE_DESIGN.md §3.9, §4.4): exact bounds (no
# BIN_TOL), group order, the instance's total w (also with nf > 1), and a bound the card omits taken
# from the master's default (lmin=wmin=0, lmax=wmax=1 m).  Phase-0 signatures; S1 implements them.

def bin_bounds_spectre(model: Model, values: Mapping[str, float]) -> Tuple[float, float, float, float]:
    """(lmin, lmax, wmin, wmax) of a Spectre model-group entry, a bound the card omits filled from
    SPECTRE_MASTERS (§4.4)."""
    raise NotImplementedError("bin_bounds_spectre is not implemented yet (VAMOS_SPECTRE_DESIGN.md §4.4: "
                              "phase 1, S1)")


def bin_guard_spectre(bounds: Sequence[float], l: Expr, w: Expr, s: float) -> Expr:
    """The selection condition of one group entry: lmin <= l*s < lmax and wmin <= w*s < wmax, exact."""
    raise NotImplementedError("bin_guard_spectre is not implemented yet (VAMOS_SPECTRE_DESIGN.md §4.4: "
                              "phase 1, S1)")


def select_bin_spectre(bins: Sequence[Tuple[Model, Tuple[float, float, float, float]]], l: float,
                       w: float, s: float) -> Optional[Model]:
    """The first entry in group order whose exact bounds hold l*s and the total w*s; None if none."""
    raise NotImplementedError("select_bin_spectre is not implemented yet (VAMOS_SPECTRE_DESIGN.md §4.4: "
                              "phase 1, S1)")


# -- sources (§4.3.8) -----------------------------------------------------------------

WAVE_FIELDS: Dict[str, Tuple[str, ...]] = {
    "pulse": ("v1", "v2", "td", "tr", "tf", "pw"),
    "sin": ("vo", "va", "freq", "td", "theta", "phase"),
    "exp": ("v1", "v2", "td1", "tau1", "td2", "tau2"),
    "pwl": (),
}
_OPTIONAL_FIELDS = {"pulse": ("per",), "pwl": ("td",)}
_LEVEL_FIELDS = {"pulse": ("v1", "v2"), "sin": ("vo", "va"), "exp": ("v1", "v2")}


def _numv(e: Optional[Expr]) -> Optional[float]:
    return e.value if isinstance(e, Num) else None


def wave(src: Source, name: str) -> Dict[str, Expr]:
    """The resolved, checked fields of src's waveform (see the module docstring)."""
    w = src.wave
    if w not in WAVE_FIELDS:
        raise TableError("%s: unknown source waveform %r" % (name, w))
    out: Dict[str, Expr] = {}
    for k in WAVE_FIELDS[w]:
        e = src.args.get(k)
        if e is None:
            raise TableError("%s: %s field %s is unresolved (spice.parse resolves every source "
                             "field, §4.3.8)" % (name, w.upper(), k))
        out[k] = e
    for k in _OPTIONAL_FIELDS.get(w, ()):
        if k in src.args:
            out[k] = src.args[k]
    if w == "pulse":
        for k in ("tr", "tf"):
            v = _numv(out[k])
            if v is not None and v <= 0:
                raise TableError("%s: PULSE %s=%s (an explicit or omitted zero edge becomes TSTEP in "
                                 "spice.parse; never rise=0/fall=0)" % (name, k, repr(v)))
        if "per" in out:
            parts = [_numv(out[k]) for k in ("per", "tr", "tf", "pw")]
            if None not in parts and parts[0] <= parts[1] + parts[2] + parts[3]:   # type: ignore[operator]
                raise TableError("%s: PULSE period %r is not longer than tr+tf+pw" % (name, parts[0]))
    elif w == "exp":
        a, b = _numv(out["td1"]), _numv(out["td2"])
        if a is not None and b is not None and b <= a:
            raise TableError("%s: EXP td2=%r must be after td1=%r" % (name, b, a))
    elif w == "sin":
        f = _numv(out["freq"])
        if f is not None and f <= 0:
            raise TableError("%s: SIN frequency %r must be > 0" % (name, f))
    elif w == "pwl":
        pts = src.points
        if len(pts) < 2:
            raise TableError("%s: a PWL source needs at least two points" % name)
        prev: Optional[float] = None
        for t, _ in pts:
            v = _numv(t)
            if v is not None and (v < 0 or (prev is not None and v <= prev)):
                raise TableError("%s: PWL times must be >= 0 and strictly increasing (at %r)" % (name, v))
            prev = v
    return out


def scale_source(src: Source, k: Expr) -> Source:
    """src with every value multiplied by k (dc, ac magnitude, waveform levels, PWL values).

    Built with dataclasses.replace, so every other field of Source (Source.spectre among them,
    VAMOS_SPECTRE_DESIGN.md §4.1) survives unchanged.
    """
    args = dict(src.args)
    for f in _LEVEL_FIELDS.get(src.wave or "", ()):
        if f in args:
            args[f] = times(args[f], k)
    return replace(src, dc=times(src.dc, k) if src.dc is not None else None,
                   ac=(times(src.ac[0], k), src.ac[1]) if src.ac is not None else None,
                   args=args, points=[(t, times(v, k)) for t, v in src.points])


# -- simulator options (§4.3.5) -------------------------------------------------------

TOLERANCE_KEYS = ("reltol", "abstol", "vntol")


def solver_options(nl: Netlist, origin: str = "") -> Tuple[Dict[str, object], List[Note]]:
    """The .option settings both engines honour, and a note for each one they cannot."""
    out: Dict[str, object] = {}
    notes: List[Note] = []
    opts = nl.options
    if "gmin" in opts:
        try:
            out["gmin"] = X.evaluate(opts["gmin"], nl.values)
        except X.EvalError as exc:
            raise TableError(".option gmin must be a constant: %s" % exc)
    if "method" in opts:
        m = opts["method"]
        text = (m.text if isinstance(m, Str) else m.name if isinstance(m, Name) else X.to_text(m)).lower()
        if text == "gear":
            out["method"] = "gear"
        elif text != "trap":                       # trap is both engines' default
            notes.append(note(origin, ".option method=%s is not mapped (VACASK and Xyce offer trap and gear); "
                                      "the engine default (trapezoidal) is used" % text))
    for k in TOLERANCE_KEYS:
        if k in opts:
            notes.append(note(origin, ".option %s=%s is not mapped: Xyce's tolerances do not have SPICE's "
                                      "meaning, so both engines run with their defaults" % (k, X.to_text(opts[k]))))
    return out, notes


# -- deck-level elements (§4.7) -------------------------------------------------------

BRIDGE_LIB = "libcosim_bridge.so"
BRIDGE_INIT = {"vacask": "vacask_bridge_init", "xyce": "nvc_bridge_init"}
BRIDGE_SUFFIX_EN = "__e"          # names.BRIDGE_SUFFIX["en"]: the enable of a gated D2A
GCOND_MODULE = "vamos_gcond"
GCOND_RR = 500.7                  # D2A r_series default (§3.4)
SHUNT_R = 1e12
VA_INSTANCE_MODULES: FrozenSet[str] = frozenset((GCOND_MODULE,))
VA_SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ams", "va",
                         "vamos_ie.va")


def code_uri(engine: str, direction: str, name: str) -> str:
    """The complete engine URI of a bridge source (only ams/deck.py builds Source.code_uri)."""
    _engine(engine)
    if direction not in ("d2a", "a2d"):
        raise TableError("bridge direction must be d2a or a2d, not %r" % (direction,))
    if not name or any(c.isspace() for c in name) or '"' in name:
        raise TableError("bad bridge name %r" % (name,))
    return "code:%s:%s:%s:%s" % (BRIDGE_LIB, BRIDGE_INIT[engine], direction, name)


def parse_code_uri(uri: str) -> Tuple[str, str, str]:
    """(init, direction, name) of a code URI; TableError if it is not one."""
    prefix = "code:%s:" % BRIDGE_LIB
    if not uri.startswith(prefix):
        raise TableError("code URI %r does not start with %r" % (uri, prefix))
    parts = uri[len(prefix):].split(":", 2)
    if len(parts) != 3 or parts[1] not in ("d2a", "a2d") or not parts[2]:
        raise TableError("malformed code URI %r" % (uri,))
    return parts[0], parts[1], parts[2]


def code_source(name: str, kind: str, p: str, n: str, uri: str, origin: str = "") -> Instance:
    """A bridge source: 'v' for a D2A value or enable, 'i' (0 A) for an A2D probe."""
    if kind not in ("v", "i"):
        raise TableError("a bridge source is a v or i element, not %r" % (kind,))
    parse_code_uri(uri)
    return Instance(name, kind, [p, n], source=Source(code_uri=uri), origin=origin)


def dc_source(name: str, p: str, n: str, value: float, origin: str = "") -> Instance:
    """An ideal DC voltage source (POWERNET, REMOVED with dc=)."""
    return Instance(name, "v", [p, n], source=Source(dc=Num(float(value))), origin=origin)


def gcond(name: str, n: str, nd: str, ne: str, rr: float = GCOND_RR, origin: str = "") -> Instance:
    """The gated-conductance D2A element i(nd -> n) = v(ne)*(v(nd)-v(n))/rr."""
    if not rr > 0:
        raise TableError("gated conductance %s: rr must be > 0, not %r" % (name, rr))
    return Instance(name, "y", [n, nd, ne], master=GCOND_MODULE, params={"rr": Num(float(rr))},
                    origin=origin)


def shunt(name: str, node: str, r: float = SHUNT_R, origin: str = "") -> Instance:
    """The DC path of a node the AMS layer creates or bridges (§4.7)."""
    return Instance(name, "r", [node, "0"], value=Num(float(r)), origin=origin)


def _engine(engine: str) -> None:
    if engine not in ENGINES:
        raise ValueError("unknown analog engine %r" % (engine,))


# -- shared walk ----------------------------------------------------------------------

class Scope:
    """One body (the top level or a subckt) and what is visible from it."""

    def __init__(self, items: Sequence[object], parent: Optional["Scope"] = None,
                 subckt: Optional[Subckt] = None):
        self.parent, self.subckt = parent, subckt
        self.models: Dict[str, Model] = {}
        self.binned: Dict[str, List[Model]] = {}
        self.subckts: Dict[str, Subckt] = {}
        self.coupled: Set[str] = set()          # inductors a K element of this body names
        for it in items:
            if isinstance(it, Model):
                self.models[it.name] = it
                if it.base is not None:
                    self.binned.setdefault(it.base, []).append(it)
            elif isinstance(it, Subckt):
                self.subckts[it.name] = it
            elif isinstance(it, Instance) and it.kind == "k":
                self.coupled.update(it.ctrl)
        # Bins are tried in Xyce's order (its model map sorts names case-insensitively
        # as strings: nch.1, nch.10, nch.2): at an edge the lower-bound tolerance lets
        # two bins match, and the first match must be the same on both engines.
        for group in self.binned.values():
            group.sort(key=lambda m: m.name.upper())

    @property
    def in_subckt(self) -> bool:
        return self.subckt is not None

    def model(self, name: str) -> Optional[Model]:
        s: Optional[Scope] = self
        while s is not None:
            if name in s.models:
                return s.models[name]
            s = s.parent
        return None

    def bins(self, base: str) -> Optional[List[Model]]:
        s: Optional[Scope] = self
        while s is not None:
            if base in s.binned:
                return s.binned[base]
            s = s.parent
        return None

    def find_subckt(self, name: str) -> Optional[Subckt]:
        found = self.where(name)
        return found[0] if found is not None else None

    def where(self, name: str) -> Optional[Tuple[Subckt, "Scope"]]:
        """The subckt definition a name resolves to and the scope that defines it."""
        s: Optional[Scope] = self
        while s is not None:
            if name in s.subckts:
                return s.subckts[name], s
            s = s.parent
        return None

    def card_of(self, inst: Instance) -> Tuple[Optional[Model], List[Model]]:
        """(the card, []) for a device naming a model card, (None, bins) for one naming a binned
        base (M only), (None, []) when neither is visible (the emitters report that)."""
        if not inst.master:
            return None, []
        m = self.model(inst.master)
        if m is not None:
            return m, []
        if inst.kind == "m":
            return None, list(self.bins(inst.master) or [])
        return None, []

    def path(self) -> str:
        names = []
        s: Optional[Scope] = self
        while s is not None and s.subckt is not None:
            names.append(s.subckt.name)
            s = s.parent
        return ".".join(reversed(names))


MODEL_KINDS_OF: Dict[str, Tuple[str, ...]] = {
    "m": ("nmos", "pmos"), "q": ("npn", "pnp"), "j": ("njf", "pjf"), "d": ("d",), "r": ("r",),
    "c": ("c",), "l": ("l",)}
TERMINALS: Dict[str, int] = {"r": 2, "c": 2, "l": 2, "v": 2, "i": 2, "e": 4, "g": 4, "f": 2, "h": 2,
                             "b": 2, "d": 2, "j": 3, "m": 4}          # q: 3 or 4; x, y: the definition's


def constant(exprs: Sequence[Expr], values: Mapping[str, float], shadowed: Sequence[str] = ()
             ) -> Optional[Tuple[float, ...]]:
    """The values of exprs with the top-level parameters; None if one is not constant."""
    out = []
    for e in exprs:
        if shadowed and X.names(e) & set(shadowed):
            return None
        try:
            out.append(X.evaluate(e, values))
        except X.EvalError:
            return None
    return tuple(out)


def smoke_netlist(nl: Netlist) -> Netlist:
    """A copy of nl whose bridge sources hold DC 0 (values, probes) or DC 1.0 (enables)."""
    out = copy.deepcopy(nl)

    def fix(items: Sequence[object]) -> None:
        for it in items:
            if isinstance(it, Subckt):
                fix(it.body)
            elif isinstance(it, Instance) and it.source is not None and it.source.code_uri:
                try:
                    name = parse_code_uri(it.source.code_uri)[2]
                except TableError:
                    name = ""
                level = 1.0 if (it.kind == "v" and name.endswith(BRIDGE_SUFFIX_EN)) else 0.0
                it.source = Source(dc=Num(level))
    fix(out.body)
    return out


def nvc_libdir() -> str:
    """nvc's library directory (engines.env_for wants it); a dummy when nvc is absent:
    the standalone smoke runs load no bridge library."""
    try:
        from vamos import tools
        nvc = tools.find_real("nvc")
        if nvc:
            return tools.nvc_libdir(nvc)
    except Exception:          # pragma: no cover
        pass
    return "/nonexistent"


def all_subckts(nl: Netlist) -> List[Subckt]:
    """Every subckt definition, nested ones included, outer first."""
    out: List[Subckt] = []
    stack = [it for it in nl.body if isinstance(it, Subckt)]
    while stack:
        s = stack.pop(0)
        out.append(s)
        stack.extend(it for it in s.body if isinstance(it, Subckt))
    return out


def reachable(nl: Netlist) -> Set[int]:
    """ids of the Subckt and Model objects the deck uses: the definitions reachable from the
    top-level instances, through X lines and device model names (bins of a binned base included).

    A model library defines far more than a deck uses (sky130's corner files hold hundreds of
    cards and subckts, some with constructs no target honours); HSPICE never instantiates an
    unused definition, so the emitters print only these and judge nothing else.
    """
    used: Set[int] = set()

    def visit(items: Sequence[object], scope: Scope) -> None:
        for it in items:
            if not isinstance(it, Instance):
                continue
            if it.kind == "x":
                found = scope.where(it.master or "")
                if found is None or id(found[0]) in used:
                    continue
                sub, where = found
                used.add(id(sub))
                visit(sub.body, Scope(sub.body, where, sub))
            elif it.kind in MODEL_KINDS_OF:
                card, bins = scope.card_of(it)
                if card is not None:
                    used.add(id(card))
                used.update(id(b) for b in bins)
    visit(nl.body, Scope(nl.body))
    return used


# -- instance paths (VAMOS_SPECTRE_DESIGN.md §3.5, §4.4; phase 0, implemented) -------------------
#
# One walker computes the parameter values of every instance path, by the rule xyce._Deck.child_env
# applies today: the top-level values, then the subckt's own parameters in order, an X-line override
# evaluated where the X line is, a parameter that does not evaluate hidden (it shadows the top-level
# value of its name).  plan.build walks it strict with dialect="spectre"; the Xyce emitter is rebuilt
# on it (non-strict, nominal values), so the branches and bins the plan checked are the ones the deck
# prints.

class _PathValues(abc.Mapping):
    """The parameter values on one instance path: the subckt's own over the top-level values, with
    the names it declares but cannot evaluate hidden (no copy of the top level per path)."""

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

    def __repr__(self) -> str:
        return "_PathValues(%r, hidden=%r)" % (dict(self), sorted(self.hidden))


@dataclass
class PathEnv:
    """One instance path (§3.5, §4.4)."""
    path: str                                     # "" at the top level, else the X-instance path "x1.x2" (IR names)
    subckt: Optional[Subckt]                      # None at the top level
    scope: Scope
    env: Mapping[str, float]                      # the parameter values on this path (child_env's rule)
    items: List[Instance] = field(default_factory=list)   # the body's instances on this path, Cond resolved
    hidden: Set[str] = field(default_factory=set) # parameters that did not evaluate (non-strict walks only)


def _subckt_params(s: Subckt) -> List[Param]:
    """A subckt's parameters, header and body (spice.parse merges them; a hand-built IR may not)."""
    names = {p.name for p in s.params}
    return list(s.params) + [p for p in s.body if isinstance(p, Param) and p.name not in names]


def _path_items(items: Sequence[object], path: str, env: Mapping[str, float], dialect: str,
                out: List[Instance]) -> None:
    """The instances of one body on one path, each Cond resolved with the path's values (only the
    taken branch's items are walked); a condition that is not a number on its path raises."""
    for it in items:
        if isinstance(it, Instance):
            out.append(it)
        elif isinstance(it, (Param, Model, Subckt)):
            continue
        elif isinstance(it, Cond):
            taken: Optional[Sequence[object]] = None
            for cond, branch in it.branches:
                try:
                    value = X.evaluate(cond, env, dialect=dialect)
                except X.EvalError as exc:
                    raise X.EvalError("%s: condition %s is not a number on this path: %s"
                                      % (path or "top level", X.to_text(cond), exc))
                if value != 0:
                    taken = branch
                    break
            _path_items(it.default if taken is None else taken, path, env, dialect, out)
        elif isinstance(it, ParamTest):
            continue                                        # the emitters print nothing for it
        else:
            raise TableError("%s: item %r is not an IR item (ir.ITEM_TYPES)" % (path or "top level", it))


def path_envs(nl: Netlist, values: Optional[Mapping[str, float]] = None, strict: bool = False,
              dialect: str = "hspice") -> Iterator[PathEnv]:
    """Every instance path, the top level first, depth first in body order (§4.4).

    values: the top-level parameter values (Netlist.values by default; a render copy's reduced ones).
    strict: a parameter that does not evaluate raises EvalError naming the path and the expression;
    non-strict, it is hidden on that path, as xyce._Deck.child_env hides it today.  A subckt that
    instantiates itself is not entered again (the emitters report it).
    """
    top_values: Mapping[str, float] = nl.values if values is None else values
    top_scope = Scope(nl.body)
    top_items: List[Instance] = []
    _path_items(nl.body, "", top_values, dialect, top_items)
    yield PathEnv("", None, top_scope, top_values, top_items, set())
    yield from _child_paths(nl, top_items, top_scope, top_values, "", [], top_values, strict, dialect)


def _child_paths(nl: Netlist, items: Sequence[Instance], scope: Scope, env: Mapping[str, float],
                 path: str, stack: List[int], top_values: Mapping[str, float], strict: bool,
                 dialect: str) -> Iterator[PathEnv]:
    for it in items:
        if it.kind != "x":
            continue
        found = scope.where(it.master or "")
        if found is None or id(found[0]) in stack:          # undefined (the emitters report it) / recursion
            continue
        sub, where = found
        child_path = it.name if not path else path + "." + it.name
        child_env = _PathValues(top_values)
        over = {k: v for k, v in it.params.items() if k != "m"}
        for p in _subckt_params(sub):
            e = over.get(p.name)
            try:
                child_env.own[p.name] = (X.evaluate(e, env, dialect=dialect) if e is not None
                                         else X.evaluate(p.expr, child_env, dialect=dialect))
                child_env.hidden.discard(p.name)
            except X.EvalError as exc:
                if strict:
                    raise X.EvalError("%s: parameter %s=%s of subckt %s does not evaluate: %s"
                                      % (child_path, p.name, X.to_text(e if e is not None else p.expr),
                                         sub.name, exc))
                child_env.own.pop(p.name, None)
                child_env.hidden.add(p.name)
        child_scope = Scope(sub.body, where, sub)
        child_items: List[Instance] = []
        _path_items(sub.body, child_path, child_env, dialect, child_items)
        yield PathEnv(child_path, sub, child_scope, child_env, child_items, set(child_env.hidden))
        stack.append(id(sub))
        try:
            yield from _child_paths(nl, child_items, child_scope, child_env, child_path, stack, top_values,
                                    strict, dialect)
        finally:
            stack.pop()


def path_counts(nl: Netlist) -> Dict[str, int]:
    """Subckt name -> the number of instance paths that reach it (§5.3's per-path rule on Xyce);
    every definition is listed, an unreached one with 0.  Non-strict, nominal values."""
    out: Dict[str, int] = {s.name: 0 for s in all_subckts(nl)}
    for pe in path_envs(nl):
        if pe.subckt is not None:
            out[pe.subckt.name] = out.get(pe.subckt.name, 0) + 1
    return out
