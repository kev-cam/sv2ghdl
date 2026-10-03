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
    model_params(model, engine, binned=False, options=None) -> (List[(name, Expr)], List[Note])
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
        WD; LEVEL 3 ETA*8.14/8.15; warnings for an ambiguous KP default, a
        LEVEL 3 XJ below 0.05u, a missing VTO the target cannot derive, and
        HSPICE's default CAPOP=2 gate capacitance (simulated as CAPOP=0;
        capop=0 removed, any other CAPOP a warning).  BSIM3 (_bsim3): LEVEL 49
        XPART=1, the CAPMOD default of the card's VERSION (3.0: 1; 3.1: 2, a
        warning at LEVEL 49), a warning for LEVEL 49's ACM=0 junctions.
        D/Q/J (_junctions): HSPICE's default DCAP=2 as FC=0 (FCS=0), DCAP=3 an
        error; diode PB 0.8 (printed vj) and PHP=PB, BJT MJS 0.5, JFET PB 0.8;
        a JFET capop=0 removed, any other CAPOP a warning.  Under .option spice
        SPICE's defaults stay (DCAP=1, MOS CAPOP=0, no LD or NSUB default, MJS=0).
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

Errors are TableError (a ValueError) naming the construct; the emitters turn
them into notes with the element's origin.
"""

from __future__ import annotations

import copy
import math
import os
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple

from vamos.netlist import expr as X
from vamos.netlist.expr_ast import Binary, Call, Expr, Name, Num, Str, Ternary
from vamos.netlist.ir import Instance, Model, Netlist, Source, Subckt
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
                 options: Optional[Mapping[str, Expr]] = None
                 ) -> Tuple[List[Tuple[str, Expr]], List[Note]]:
    """The card's parameters for one engine, and the notes the changes need (see the module docstring).

    options is Netlist.options: .option spice and .option dcap select HSPICE's model defaults
    (hspice_card); None means neither is given.
    """
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
    if engine == "vacask" and row is _MOS3:
        out = _vacask_mos3_kappa(model, out, notes)
    if engine == "vacask":
        if row.keep_level:
            out.insert(0, ("level", Num(float(level_of(model)))))
        if row.polarity and pol is not None:
            out.insert(0, ("type", Num(pol)))
    return out, notes


XYCE_TINY_CJO = 1e-30
VACASK_TINY_KAPPA = 1e-12
MOS_CJ_KEYS = ("cj", "cdb", "csb", "cja", "cbd", "cbs")


def mos_junction_warnings(inst: Instance, model: Model,
                          options: Optional[Mapping[str, Expr]] = None) -> List[Note]:
    """A MOS LEVEL 1, 2 or 3 card that gives no bulk junction capacitance (CJ, or CBD/CBS),
    on an instance that gives a junction area (AD or AS): HSPICE simulates its default CJ
    (579.11 uF/m^2, Star-HSPICE 20-27, with ACM=0 the default), both targets CJ=0.  The default
    is not written - the manual also gives it as sqrt(eps_si*q*NSUB/(2*PB)) for ASPEC=0, which
    differs, and no HSPICE run settles it - so the card gets a warning (an error under
    --vamos-strict) naming the fix.  Not under .option spice (SPICE's default is 0).  Emitters
    call this for every M instance; the message is per card, so it is printed once."""
    row = target(model)
    if not any(row is r for r in _MOS_ROWS) or model_options(options).spice:
        return []
    given = {k.lower() for k in model.params}
    if any(k in given for k in MOS_CJ_KEYS):
        return []
    if not any(_nonzero(inst.params.get(k)) for k in ("ad", "as")):
        return []
    return [warning(model.origin or model.name,
                    "%s: no CJ on a MOS LEVEL %d card whose instances give AD/AS: HSPICE's default bulk "
                    "junction capacitance (CJ=579.11 uF/m^2, Star-HSPICE 20-27) is not simulated, both "
                    "targets use CJ=0; give CJ (F/m^2) on the card"
                    % (card_label(model), level_of(model)))]


def _vacask_mos3_kappa(model: Model, out: List[Tuple[str, Expr]], notes: List[Note]
                       ) -> List[Tuple[str, Expr]]:
    """VACASK's sp_mos3 (mos3.va) gives NaN with KAPPA exactly 0 (the derivative of the
    sqrt(kappa*...) channel-length term at 0), so the deck fails ("NaN found in vector ...
    Homotopy failed") on the Star-HSPICE manual's own LEVEL 3 example card.  KAPPA=0 becomes
    1e-12 on VACASK: the channel-length modulation it leaves is ~1e-6 of KAPPA=0.2's, and the
    manual example's drain current matches HSPICE's published 6.912e-4 A to 5 digits."""
    if not any(k == "kappa" and _is_zero(v) for k, v in out):
        return out
    notes.append(note(model.origin or model.name,
                      "%s: kappa=0 written as kappa=%g for VACASK, whose LEVEL 3 model gives NaN at "
                      "exactly 0; the channel-length modulation this leaves is negligible"
                      % (card_label(model), VACASK_TINY_KAPPA)))
    return [(k, Num(VACASK_TINY_KAPPA) if k == "kappa" and _is_zero(v) else v) for k, v in out]


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
ACM_BERKELEY = 10.0                     # BSIM3 ACM=10: the Berkeley junctions, both targets' own (22-43)
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
      KP and UO (_mos_kp): the LEVEL 1 KP default 2.0718e-5 / 8.632e-6 (21-2), the PMOS UO
        default 250 (21-11, 21-22), UO derived from KP at LEVEL 2/3; KP given with only one of
        UO and TOX, or not given at all at LEVEL 2/3 is a warning: the manual leaves it ambiguous.
      CGSO, CGDO not given but LD or METO and TOX: (LD+METO)*COX; CGBO not given but WD and
        TOX: 2*WD*COX (20-69, 20-73).  METO (no target has it) is removed.
      LEVEL 3: ETA scaled by 8.14/8.15 (HSPICE's constant over SPICE3's, 21-29); 0 < XJ < 0.05u
        is a warning (HSPICE limits fs to 1 there, SPICE3 does not).
      CAPOP: capop=0 is removed (SPICE's Meyer model is what both targets simulate), any other
        CAPOP is a warning and removed; none is a warning that HSPICE's default CAPOP=2 is
        simulated as CAPOP=0 (20-57, Table 20-4), except under .option spice (CAPOP=0) and at
        LEVEL 1 without TOX (no gate capacitance in HSPICE either, 20-56).
    Unless .option spice (9-14: LD=0, NSUB must be given):
      NSUB, GAMMA, PHI, VTO (_mos_body): HSPICE derives them from NSUB (default 1e15, or from
        GAMMA when only GAMMA is given), the targets only when NSUB is given (LEVEL 1: and TOX).
      LD not given but XJ (LEVEL 2, 3; the LEVEL 1 targets reject XJ): LD = 0.75*XJ (20-70, 21-29).
      CJSW (CJP) given but no MJSW (EXP): mjsw=0.33, HSPICE's MOS default (20-28); sp_mos1/2 and
        Xyce's LEVEL 1/2 default it to 0.5.  (No CJ is a warning per instance with AD/AS:
        mos_junction_warnings.)
    """
    tox = c.p.get("tox")
    if isinstance(tox, Num) and tox.value > 1.0:
        c.put("tox", Num(tox.value * 1e-10), "TOX above 1 is in Angstrom")
    elif tox is not None and not isinstance(tox, Num):
        c.put("tox", Ternary(Binary(">", tox, Num(1.0)), Binary("*", tox, Num(1e-10)), tox),
              "a TOX above 1 is in Angstrom")
    tox = c.p.get("tox")
    has_tox = _nonzero(tox)                     # SPICE3 LEVEL 1: no TOX (or 0), no oxide capacitance
    cox = _div(Num(EPS_OX), tox) if has_tox else Num(MOS_COX)
    _mos_kp(c, lv, pol, has_tox, cox)
    if not opts.spice:
        _mos_body(c, lv, has_tox, cox)
        xj = c.p.get("xj")
        if lv != 1 and c.key("ld", "dlat", "latd") is None and _nonzero(xj):
            c.put("ld", _mul(Num(0.75), xj), "HSPICE's default LD=0.75*XJ")
        cjsw = c.key("cjsw", "cjp")
        if cjsw is not None and _nonzero(c.p[cjsw]) and c.key("mjsw", "exp") is None:
            c.put("mjsw", Num(MOS_MJSW), "HSPICE's default MJSW")
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


def _mos_body(c: _Card, lv: int, has_tox: bool, cox: Expr) -> None:
    """NSUB, GAMMA, PHI and VTO (see _mos123); not under .option spice.

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
        return
    if has_tox or lv != 1:
        if nsub_k is None and (lv != 1 or gamma_k is None or phi_k is None or vto_k is None):
            c.put("nsub", nsub, why)
        if phi_k is None:
            c.put("phi", phi_of(nsub), "HSPICE's PHI from NSUB")
        return
    if gamma_k is None:
        c.put("gamma", gamma_of(nsub, Num(MOS_COX)), "HSPICE's GAMMA from NSUB and the default COX; "
              "the LEVEL 1 target without TOX ignores NSUB")
    if phi_k is None:
        c.put("phi", phi_of(nsub), "HSPICE's PHI from NSUB")
    if vto_k is None:
        c.warn("VTO is not given: HSPICE computes it from NSUB, PHI, GAMMA and TPG (20-51), but the "
               "LEVEL 1 target without TOX uses VTO=0; give VTO (or TOX)")


def _bsim3(c: _Card, lv: int) -> None:
    """BSIM3 (LEVEL 49, 53) on Berkeley's code (sp_bsim3v3, Xyce level 9): HSPICE's defaults (22-31,
    22-34, 22-43).

    LEVEL 49 without XPART: xpart=1 (0/100 charge partition; BSIM3 and LEVEL 53: 0, 40/60).
    No CAPMOD: HSPICE's default follows VERSION (default 3.2), 22-31: 3.2 and later 3, the
    targets' own; 3.0: capmod=1; 3.1: capmod=2, exact at LEVEL 53, a warning at LEVEL 49, whose
    default is HSPICE's own CAPMOD=0 (a modified BSIM1 model based on CAPOP=13) that neither
    engine has.  A VERSION that is not a constant is a warning.
    LEVEL 49 without ACM: a warning that HSPICE's default ACM=0 junction model is simulated as
    the Berkeley junctions (HSPICE ACM=10); acm=10 is removed with a note (model_params), and
    then a missing JS is written 0 (HSPICE's LEVEL 49 default; Berkeley's is 1e-4).
    """
    acm = c.model.params.get("acm")
    if lv == 49:
        if "xpart" not in c.p:
            c.put("xpart", Num(1.0), "HSPICE's LEVEL 49 default (0/100 partition); BSIM3's is 0")
        if acm is None:
            c.warn("HSPICE's LEVEL 49 default ACM=0 source/drain junction model (its own diode equations: "
                   "N, PHP and CJGATE; NJ, CJSWG, MJSWG, PBSW and PBSWG are not used) is simulated as "
                   "BSIM3's Berkeley junctions (HSPICE ACM=10); add acm=10 to the card to make HSPICE "
                   "use the same model")
        elif isinstance(acm, Num) and acm.value == ACM_BERKELEY and "js" not in c.p:
            c.put("js", Num(0.0), "HSPICE's LEVEL 49 default; BSIM3's is 1e-4")
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
        # fc=0 for a sidewall-only diode too: both targets' sidewall charge (ngspice's code) adds the
        # area term czeroSW*F1, F1 being 0 only with FC=0, so FC must match FCS there
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
            raise TableError("binned model %s: %s=%s is not constant (%s)"
                             % (model.name, k, X.to_text(e), exc))
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
    """src with every value multiplied by k (dc, ac magnitude, waveform levels, PWL values)."""
    args = dict(src.args)
    for f in _LEVEL_FIELDS.get(src.wave or "", ()):
        if f in args:
            args[f] = times(args[f], k)
    return Source(dc=times(src.dc, k) if src.dc is not None else None,
                  ac=(times(src.ac[0], k), src.ac[1]) if src.ac is not None else None,
                  wave=src.wave, args=args,
                  points=[(t, times(v, k)) for t, v in src.points], code_uri=src.code_uri)


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
