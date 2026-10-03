"""Interface-element rules: levels, removal and TNF (docs/VAMOS_AMS_DESIGN.md §3).

Every a2d/d2a rule of the control file whose selector matches an analog node
applies to it, in file order (snps_vcsAD.ini first, `include files in place):
a later rule overrides an earlier one key by key, whichever alias of the node
each one names (PAMS p176).  `rf_time` sets both ramps and `delay` both
delays at their place in that order, so `rf_time` followed by `rise_time`
keeps the fall time.  Selectors:

  node=pattern     a pattern with '*' matches the canonical name (names[0])
                   only; a pattern without '*' matches any name of the node
  cell=c port=p    any cut port on the node (Port5) whose cell matches c, or
  inst=i port=p    whose instance path matches i, and whose SPICE name (a_3),
                   Verilog bit (a[3]) or bare Verilog port (a) matches p;
                   a2d except_port= takes ports back out of a port= match
  library=         ignored (a note at parse time)

Patterns are VCS globs (vamos.ams.globs): only '*' is special, so tb.u.d[3]
matches itself.  Names compare case-insensitively unless the XA cfg says
`set_sim_case -case sensitive` (AmsConfig.xa["case"]).

Levels (§3.3, §3.4).  `%` values are fractions of the reference span
vss -> vdd.  The reference is the merged rule's vdd=/vss= (or vdd_port=/
vss_port=, the matched instance's port), looked up with dc_of, else the
caller's vref (steps 2-5: ie_reference_voltage, trace, highest source, 3.3 V).
vref's Note (the 3.3 V warning) is returned only when a default level or a
`%` value actually used vref's vdd.  Defaults: hiv = vdd, lov = vss, ramps
10 ps, delays 0, x2v 0; loth = hith = 50 % of the span.

Hits (RuleHits, model.py).  resolve marks rule#<i> for every rule whose
selector matches the node (within the role's kinds), and vdd#<i> / vss#<i>
when that rule's supply name exists (dc_of not 'missing'); removal marks
remove_d2a#<i>; disabled marks disable_ie#<i>.  The other keys are marked by
whoever finds their target: ref_voltage#<i> (deck.py, when the node exists in
the deck), use_spice_inst#<i>.<j> (an instance matches that -inst path) and
port_connect_inst#<i>.  resolve, removal and disabled also record each node's
canonical name under the reserved key prefix SEEN ("node:"), and resolve each
cut cell under SEEN_CELL ("cell:"): unmatched() takes the nearest names for
its [MSV-IE-OPT-TNF] diagnostics from there.  A rule whose supply name is missing is not applied to the node (VCS:
"the command will be ignored") and its vdd=/vss= is reported by unmatched().

Key dispositions (check_rule, called by initfile at parse time, §3.5):
minv, minv_logic, minv_analog, vdd_filter are errors; hiz_on, hiz_off,
strength= other than rmap, ceff= and queue=nonblocking are warnings and
ignored; midv_logic=0|1|X|Z are all applied (Z releases the net, MIDV_L 3);
an absolute hiv/lov/loth/hith in a rule that also gives vdd=, vss=,
vdd_port= or vss_port= is an error (PAMS p190, p206-207: with a reference
supply the levels are percentages of it); undocumented keys are errors.  The
keys come from PAMS Appendix A, plus queue= (VCS 2019 User Guide, ch. 30).

map_by_node r=<ohms> node=<pattern> (PAMS p243) is kept in AmsConfig.rules as
an IeRule of kind "map_by_node" (key r, selector node= only).  It is a
d2a-direction rule for the role filter: on a D2A or BIDIR node the last match
sets D2A_IE.r_series to r and weak_frac to 1.0 (the resistance replaces the
resistance-map value for every drive strength); on a supply net or a powernet
D2A (ideal sources) it has no effect and warns once; on any other node it is
not marked, so a map_by_node that names only A2D nodes is [MSV-IE-OPT-TNF].

Report lines (§3.6): paste_line() is the one writer of the IE report's
control-file lines and ie_lines() picks them for a node by its role; report.py
calls ie_lines() for every node, so the report's lines pasted back into the
control file (in place of its a2d/d2a/map_by_node rules) reproduce every
node's levels exactly, supply nets included.
"""

from __future__ import annotations

import difflib
import math
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

from vamos.ams import globs
from vamos.ams.config import TNF, AmsConfig, IeRule
from vamos.ams.model import A2D, A2D_IE, BIDIR, D2A, D2A_IE, DISABLED, POWERNET, REMOVED, RuleHits
from vamos.netlist.numbers import fmt, parse_number
from vamos.notes import Note, error, note, warning

Port5 = Tuple[str, str, str, str, str]   # (cell, inst path, spice port, verilog bit "a[3]", verilog port "a")
# ('const', v) | ('dynamic', None) | ('missing', None) | ('unevaluable', "<what holds it>"):
# the last for a net held by a V source whose value cannot be evaluated (deck.py names it,
# "V source vs1 (cells.sp:9), a pwl wave")
DcOf = Callable[[str], Tuple[str, Union[float, str, None]]]
VRef = Tuple[float, float, Optional[Note]]            # (vss, vdd, 3.3 V warning or None)

MIN_RAMP = 1e-15            # rf_time below 1 fs is clamped (nvc P3 clamps there too)
SEEN = "node:"              # RuleHits key prefix: canonical names resolve/removal/disabled saw
SEEN_CELL = "cell:"         # RuleHits key prefix: cut cells resolve saw on a node
SELECTORS = ("node", "cell", "inst", "port", "library")
SUPPLY_KEYS = ("vdd", "vss", "vdd_port", "vss_port")

# Value class of every documented key (PAMS Appendix A, p188 and p205).
_KEYS: Dict[str, Dict[str, str]] = {
    "d2a": {"hiv": "level", "lov": "level",
            "rf_time": "ramp", "rise_time": "ramp", "fall_time": "ramp",
            "delay": "time", "rise_delay": "time", "fall_delay": "time",
            "x2v": "x2v", "powernet": "flag",
            "vdd": "supply", "vss": "supply", "vdd_port": "supply_port", "vss_port": "supply_port",
            "minv": "unsupported", "minv_analog": "unsupported", "vdd_filter": "unsupported"},
    "a2d": {"loth": "level", "hith": "level", "xband": "xband",
            "midv_time": "time", "midv_logic": "logic",
            "vdd": "supply", "vss": "supply", "vdd_port": "supply_port", "vss_port": "supply_port",
            "except_port": "except", "hiz_on": "hiz", "hiz_off": "hiz",
            "strength": "strength", "ceff": "ceff", "queue": "queue",
            "minv": "unsupported", "minv_logic": "unsupported"},
    "map_by_node": {"r": "ohms"},
}
# The level keys a reference supply turns into percentages (PAMS p190, p206-207).
_LEVEL_KEYS = {"d2a": ("hiv", "lov"), "a2d": ("loth", "hith")}
_DEFAULT_D2A = D2A_IE(0.0, 0.0)      # the r_series / weak_frac a D2A has without map_by_node
_STRENGTHS = ("supply", "strong", "pull", "large", "weak", "medium", "small", "hiz", "rmap")
_EXCLUSIVE = (("rf_time", ("rise_time", "fall_time")), ("delay", ("rise_delay", "fall_delay")),
              ("vdd", ("vdd_port",)), ("vss", ("vss_port",)))
# Keys resolve() does not merge into levels: supplies are merged separately,
# the rest only matter at parse time (selector filter or an ignored setting).
_NOT_LEVELS = set(SUPPLY_KEYS) | {"except_port", "hiz_on", "hiz_off", "strength", "ceff", "queue",
                                  "minv", "minv_logic", "minv_analog", "vdd_filter"}
_ROLE_KINDS = {D2A: ("d2a",), POWERNET: ("d2a",), A2D: ("a2d",), BIDIR: ("a2d", "d2a")}
# Roles whose rules found their node but have nothing to apply to: marked, never TNF.
_MARK_ONLY = (DISABLED, REMOVED)


def _direction(rule: IeRule) -> str:
    """The IE kind a rule acts on for the role filter: map_by_node sets the D2A's series
    resistance, so it is a d2a-direction rule (PAMS p243)."""
    return "d2a" if rule.kind == "map_by_node" else rule.kind


# -- values ------------------------------------------------------------------------

def parse_level(raw: str) -> Tuple[float, bool]:
    """'1.2', '1.2V', '500mV' -> (volts, False); '90%' -> (0.9, True).  ValueError otherwise."""
    s = raw.strip()
    if s.endswith("%"):
        v = parse_number(s[:-1]) / 100.0
        pct = True
    else:
        v = parse_number(s)
        pct = False
    if not math.isfinite(v):
        raise ValueError("not a finite number: %r" % (raw,))
    return v, pct


def parse_time(raw: str) -> float:
    """A time in seconds ('1.5n', '10ns', '3e-10').  ValueError for '%' or a non-number."""
    s = raw.strip()
    if s.endswith("%"):
        raise ValueError("a time cannot be a percentage: %r" % (raw,))
    v = parse_number(s)
    if not math.isfinite(v):
        raise ValueError("not a finite number: %r" % (raw,))
    return v


def case_sensitive(cfg: AmsConfig) -> bool:
    """True when the XA cfg says set_sim_case -case sensitive (§2.1)."""
    return str(cfg.xa.get("case") or "").lower() == "sensitive"


def selector_text(rule: IeRule) -> str:
    """The rule's selector as written: node=x | cell=c port=p | inst=i port=p."""
    if rule.node is not None:
        return "node=%s" % rule.node
    parts = []
    if rule.cell is not None:
        parts.append("cell=%s" % rule.cell)
    if rule.inst is not None:
        parts.append("inst=%s" % rule.inst)
    if rule.port is not None:
        parts.append("port=%s" % rule.port)
    return " ".join(parts)


# -- parse-time checks (initfile calls this for every a2d/d2a statement) --------------

def check_rule(rule: IeRule) -> List[Note]:
    """Selector shape, key dispositions and value syntax of one a2d/d2a/map_by_node rule (§3.5).

    initfile appends a rule to AmsConfig.rules only when this returns no
    error, so resolve() can assume every value parses.
    """
    out: List[Note] = []
    o = rule.origin
    kind = rule.kind
    if kind not in _KEYS:
        return [error(o, "unknown interface-element kind %r" % (kind,))]
    keys = _KEYS[kind]
    other = "a2d" if kind == "d2a" else "d2a"

    # selectors: node= | cell= port= | inst= port=  (PAMS p188, p205); map_by_node: node= (p243)
    if kind == "map_by_node":
        if rule.node is None:
            out.append(error(o, "map_by_node needs node= (map_by_node r=<ohms> node=<net>;)"))
        extra = [s + "=" for s in ("cell", "inst", "port", "library") if getattr(rule, s) is not None]
        if extra:
            out.append(error(o, "map_by_node selects with node= only, not %s" % ", ".join(extra)))
        if "r" not in rule.params:
            out.append(error(o, "map_by_node needs r=<ohms>"))
    elif rule.node is not None:
        if rule.cell is not None or rule.inst is not None or rule.port is not None:
            out.append(error(o, "%s: node= cannot be combined with cell=, inst= or port=" % kind))
    elif rule.cell is not None or rule.inst is not None:
        if rule.cell is not None and rule.inst is not None:
            out.append(error(o, "%s: cell= and inst= are exclusive" % kind))
        if rule.port is None:
            out.append(error(o, "%s: %s needs port= (use port=* for every port)"
                             % (kind, "cell=" if rule.cell is not None else "inst=")))
    elif rule.port is not None:
        out.append(error(o, "%s: port= needs cell= or inst=" % kind))
    else:
        out.append(error(o, "%s: no selector: give node=, cell= with port=, or inst= with port= "
                         "(node=* selects every interface element)" % kind))
    for sel in ("node", "cell", "inst", "port", "library"):
        if getattr(rule, sel) == "":
            out.append(error(o, "%s: %s= needs a value" % (kind, sel)))
    if rule.library and kind != "map_by_node":
        out.append(note(o, "%s: library=%s is ignored (one design library in the vcs two-step "
                        "flow): the rule applies wherever its other selectors match"
                        % (kind, rule.library)))

    p = rule.params
    for k, v in p.items():
        cls = keys.get(k)
        if cls is None:
            if k in _KEYS[other]:
                out.append(error(o, "%s: %s= is a %s key, not a %s key" % (kind, k, other, kind)))
            else:
                out.append(error(o, "%s: unknown key %s" % (kind, k if v == "" else k + "=")))
            continue
        if cls == "flag":
            if v != "":
                out.append(error(o, "%s: %s takes no value" % (kind, k)))
            continue
        if cls == "hiz":
            if v != "":
                out.append(error(o, "%s: %s takes no value" % (kind, k)))
            else:
                out.append(warning(o, "%s: %s is ignored: A2D outputs are strong on unidirectional "
                                   "nets and pull strength on bidirectional ones" % (kind, k)))
            continue
        if v == "":
            out.append(error(o, "%s: %s= needs a value" % (kind, k)))
            continue
        try:
            _check_value(kind, k, cls, v, rule, out)
        except ValueError as e:
            out.append(error(o, "%s: bad %s=%s (%s)" % (kind, k, v, e)))

    for k, alts in _EXCLUSIVE:
        if k in p:
            both = [a for a in alts if a in p]
            if both:
                out.append(error(o, "%s: %s= cannot be combined with %s" % (
                    kind, k, ", ".join(a + "=" for a in both))))
    # A rule naming its reference supply gives its levels as percentages of it: VCS
    # rejects an absolute level there (PAMS p190 a2d vdd=, p206-207 d2a hiv=/lov=/vdd=).
    sup = [k + "=" for k in SUPPLY_KEYS if k in p]
    for k in _LEVEL_KEYS.get(kind, ()) if sup else ():
        if k not in p:
            continue
        try:
            pct = parse_level(p[k])[1]
        except ValueError:
            continue                                        # reported above
        if not pct:
            out.append(error(o, "%s: %s=%s cannot be an absolute level in a rule with %s: the "
                             "levels are percentages of that supply (PAMS p190, p207)"
                             % (kind, k, p[k], ", ".join(sup))))
    if kind == "a2d" and "xband" in p and "midv_time" not in p:
        out.append(note(o, "a2d: xband only shapes the midv_time window; it has no effect on a "
                        "node without midv_time"))
    return out


def _check_value(kind: str, k: str, cls: str, v: str, rule: IeRule, out: List[Note]) -> None:
    o = rule.origin
    if cls == "level":
        parse_level(v)
    elif cls in ("time", "ramp"):
        t = parse_time(v)
        if t < 0:
            out.append(error(o, "%s: %s=%s is negative" % (kind, k, v)))
        elif cls == "ramp" and t < MIN_RAMP:
            out.append(note(o, "%s: %s=%s is below 1 fs and is clamped to 1 fs" % (kind, k, v)))
    elif cls == "x2v":
        if v.strip() not in ("0", "1", "2", "3", "4"):
            out.append(error(o, "%s: x2v=%s: expected 0, 1, 2, 3 or 4" % (kind, v)))
    elif cls == "xband":
        x = parse_number(v)
        if not (x > 0 and math.isfinite(x)):
            out.append(error(o, "%s: xband=%s must be a positive number" % (kind, v)))
    elif cls == "logic":
        if v.strip().upper() not in ("0", "1", "X", "Z"):
            out.append(error(o, "%s: midv_logic=%s: expected 0, 1, X or Z" % (kind, v)))
    elif cls == "ohms":
        r = parse_number(v)
        if not (r > 0 and math.isfinite(r)):
            out.append(error(o, "%s: r=%s must be a positive resistance in ohms" % (kind, v)))
    elif cls == "supply":
        if globs.has_wildcard(v):
            out.append(error(o, "%s: %s=%s: a wildcard supply name is not supported" % (kind, k, v)))
    elif cls == "supply_port":
        if rule.port is None:
            out.append(error(o, "%s: %s= needs port= (cell= or inst= selectors)" % (kind, k)))
        # a wildcard is matched against the matched instance's ports (PAMS p194, _supply_name);
        # after ../ it would name a net of the scope above, which vamos does not search
        if globs.has_wildcard(v) and v.startswith("../"):
            out.append(error(o, "%s: %s=%s: a wildcard after ../ is not supported; name the "
                             "net, or use %s= with the supply net" % (kind, k, v, k[:3])))
    elif cls == "except":
        if rule.port is None:
            out.append(error(o, "%s: except_port= needs port= (cell= or inst= selectors)" % kind))
    elif cls == "strength":
        s = v.strip().lower()
        if s not in _STRENGTHS:
            out.append(error(o, "%s: strength=%s: expected one of %s" % (kind, v, " ".join(_STRENGTHS))))
        elif s != "rmap":
            out.append(warning(o, "%s: strength=%s is ignored: A2D outputs are strong on "
                               "unidirectional nets and pull strength on bidirectional ones" % (kind, v)))
    elif cls == "ceff":
        c = parse_number(v)
        if c < 0:
            out.append(error(o, "%s: ceff=%s is negative" % (kind, v)))
        else:
            out.append(warning(o, "%s: ceff=%s is ignored: no load capacitance is added at the "
                               "interface net" % (kind, v)))
    elif cls == "queue":
        q = v.strip().lower()
        if q == "nonblocking":
            out.append(warning(o, "%s: queue=nonblocking is ignored: A2D values reach the digital "
                               "side as ordinary signal updates" % kind))
        elif q != "blocking":
            out.append(error(o, "%s: queue=%s: expected blocking or nonblocking" % (kind, v)))
    elif cls == "unsupported":
        out.append(error(o, "%s: %s= is not supported in v1 (it only acts on dynamic supplies, "
                         "which are not supported)" % (kind, k)))
    else:                                                   # pragma: no cover
        raise AssertionError(cls)


# -- matching ------------------------------------------------------------------------

def node_matches(pattern: str, names: Sequence[str], cs: bool = False) -> bool:
    """§3.2: a pattern with '*' matches names[0] (the canonical name) only."""
    if not names:
        return False
    if globs.has_wildcard(pattern):
        return globs.match(pattern, names[0], cs)
    return any(globs.match(pattern, n, cs) for n in names if n)


def _port_match(pattern: str, p: Port5, cs: bool) -> bool:
    return any(globs.match(pattern, x, cs) for x in (p[2], p[3], p[4]) if x)


def _select(rule: IeRule, names: Sequence[str], ports: Sequence[Port5],
            cs: bool) -> Optional[List[Port5]]:
    """None: no match.  [] for a matching node= rule; else the matching cut ports."""
    if rule.node is not None:
        return [] if node_matches(rule.node, names, cs) else None
    if rule.port is None or (rule.cell is None and rule.inst is None):
        return None
    exc = rule.params.get("except_port")
    out = []
    for p in ports:
        if rule.cell is not None and not globs.match(rule.cell, p[0], cs):
            continue
        if rule.inst is not None and not globs.match(rule.inst, p[1], cs):
            continue
        if not _port_match(rule.port, p, cs):
            continue
        if exc and _port_match(exc, p, cs):
            continue
        out.append(p)
    return out or None


SubcktPorts = Callable[[str], Sequence[str]]     # instance path -> its subckt's SPICE ports


def _supply_name(rule: IeRule, which: str, sel: List[Port5], subckt_ports: Optional[SubcktPorts] = None,
                 cs: bool = False, problems: Optional[List[str]] = None) -> Optional[str]:
    """The net a rule's vdd=/vss= (or vdd_port=/vss_port=) names, '' if it cannot be formed.

    A wildcard vdd_port=/vss_port= (vdd*) is matched, as VCS does (PAMS p194: `a2d cell=*
    port=* vdd_port=vdd* vss_port=vss*'), against the ports of the matched instance's subckt
    (subckt_ports): exactly one match is that port; none is '' (TNF); several are a problem
    (appended to problems: "name one") and ''."""
    if which in rule.params:
        return rule.params[which]
    port = rule.params.get(which + "_port")
    if port is None:
        return None
    if not sel:
        return ""
    inst = sel[0][1]
    if port.startswith("../"):                       # a node one level up (PAMS p191)
        if "." not in inst:
            return ""
        return inst.rsplit(".", 1)[0] + "." + port[3:]
    if globs.has_wildcard(port):
        names = list(subckt_ports(inst)) if subckt_ports is not None else []
        hit = [p for p in names if globs.match(port, p, cs)]
        if len(hit) == 1:
            return inst + "." + hit[0]
        if len(hit) > 1 and problems is not None:
            problems.append("%s_port=%s matches %d ports of %s (%s); name one"
                            % (which, port, len(hit), inst, ", ".join(hit)))
        return ""
    return inst + "." + port


def _record(hits: RuleHits, names: Sequence[str]) -> None:
    if names and names[0]:
        hits.mark(SEEN + names[0])


# -- resolution ----------------------------------------------------------------------

@dataclass
class Resolution:
    """resolve_detail() result: the levels plus what the IE report needs (§3.6)."""
    d2a: D2A_IE
    a2d: A2D_IE
    notes: List[Note] = field(default_factory=list)
    rules: List[int] = field(default_factory=list)          # applied AmsConfig.rules indices, file order
    origins: Dict[str, str] = field(default_factory=dict)   # "d2a.hiv", "a2d.vdd", "map_by_node.r" ... -> origin
    aliases: List[str] = field(default_factory=list)        # node= values of applied rules, other than names[0]
    used_ref: bool = False                                  # a level used vref's vdd (vref's Note was returned)


def resolve(cfg: AmsConfig, names: List[str], ports: List[Port5], vref: VRef, dc_of: DcOf,
            hits: RuleHits, role: Optional[str] = None) -> Tuple[D2A_IE, A2D_IE, List[Note]]:
    """The interface elements of one analog node (§3.4).

    names: the node's canonical name first, then its aliases (§3.1).
    ports: every cut port on the node, (cell, inst path, spice port,
    verilog bit, verilog port).  vref: steps 2-5 of §3.3.  dc_of: a supply
    name -> ('const', v) | ('dynamic', None) | ('missing', None) |
    ('unevaluable', "<the V source that holds it, and why>") (DcOf).
    role (optional, an extension of §3.4): the node's role; only the rule
    kinds the role uses are applied and marked (D2A and POWERNET: d2a, A2D:
    a2d, BIDIR: both; map_by_node counts as d2a), so a d2a rule naming an A2D
    node is reported as TNF.
    DISABLED and REMOVED nodes mark the rules that name them (their target
    exists) and apply none; any other role (THROUGH, NONE, RD2A, RA2D) has no
    IE and marks nothing.  None applies and marks both kinds.
    """
    r = resolve_detail(cfg, names, ports, vref, dc_of, hits, role)
    return r.d2a, r.a2d, r.notes


def resolve_detail(cfg: AmsConfig, names: List[str], ports: List[Port5], vref: VRef, dc_of: DcOf,
                   hits: RuleHits, role: Optional[str] = None,
                   subckt_ports: Optional[SubcktPorts] = None) -> Resolution:
    """resolve() with provenance for the IE report.  subckt_ports: the SPICE ports of an
    instance's subckt, by instance path, for a wildcard vdd_port=/vss_port= (_supply_name;
    without it such a rule finds no port: TNF)."""
    cs = case_sensitive(cfg)
    _record(hits, names)
    for p in ports:
        if p[0] and not hits.hit(SEEN_CELL + p[0]):
            hits.mark(SEEN_CELL + p[0])
    kinds = ("a2d", "d2a") if role is None else _ROLE_KINDS.get(role, ())
    marks = ("a2d", "d2a") if role in _MARK_ONLY else kinds
    canonical = names[0] if names else ""
    notes: List[Note] = []
    applied: List[int] = []
    aliases: List[str] = []
    # kind -> key -> (raw value, rule index); supplies: "vdd"/"vss" -> (volts, rule index)
    merged: Dict[str, Dict[str, Tuple[str, int]]] = {"d2a": {}, "a2d": {}}
    supply: Dict[str, Dict[str, Tuple[float, int]]] = {"d2a": {}, "a2d": {}}

    for i, rule in enumerate(cfg.rules):
        rk = _direction(rule)
        if rk not in marks:
            continue
        sel = _select(rule, names, ports, cs)
        if sel is None:
            continue
        hits.mark("rule#%d" % i)
        apply = rk in kinds
        if (apply and rule.node is not None and not globs.has_wildcard(rule.node)
                and rule.node not in aliases and not globs.match(rule.node, canonical, cs)):
            aliases.append(rule.node)              # "User Specified Aliases" (PAMS p176)
        ok = True
        sup: Dict[str, float] = {}
        for which in ("vdd", "vss"):
            problems: List[str] = []
            name = _supply_name(rule, which, sel, subckt_ports, cs, problems)
            if name is None:
                continue
            if problems:                            # an ambiguous wildcard port: no TNF too
                key = "%s#%d" % (which, i)
                if not hits.hit(key):
                    notes.append(error(rule.origin, "%s: %s" % (rule.kind, problems[0])))
                hits.mark(key)
                ok = False
                continue
            if name == "":
                ok = False                          # unresolvable: left for unmatched() as TNF
                continue
            status, volts = dc_of(name)
            if status == "missing":
                ok = False
                continue
            key = "%s#%d" % (which, i)
            first = not hits.hit(key)
            hits.mark(key)
            if not apply:
                continue
            if status == "unevaluable":
                if first:
                    notes.append(error(rule.origin, "%s: %s=%s is held by %s, which cannot be "
                                       "evaluated, so it gives no level; give the levels as "
                                       "values (hiv=/lov=, loth=/hith=), or name a constant supply"
                                       % (rule.kind, which, name, volts or "a V source")))
                ok = False
                continue
            if status != "const" or not isinstance(volts, (int, float)):
                if first:
                    notes.append(error(rule.origin, "%s: %s=%s is not a constant supply; dynamic "
                                       "supplies are not supported in v1" % (rule.kind, which, name)))
                ok = False
                continue
            sup[which] = float(volts)
        if not ok or not apply:
            continue
        applied.append(i)
        for which, volts in sup.items():
            supply[rk][which] = (volts, i)
        m = merged[rk]                              # a map_by_node's r= merges as d2a key "r"
        for k, v in rule.params.items():
            if k in _NOT_LEVELS:
                continue
            if k == "rf_time":
                m["rise_time"] = m["fall_time"] = (v, i)
            elif k == "delay":
                m["rise_delay"] = m["fall_delay"] = (v, i)
            else:
                m[k] = (v, i)

    res = Resolution(D2A_IE(0.0, 0.0), A2D_IE(0.0, 0.0), notes, applied, {}, aliases)
    used_d = _d2a_levels(cfg, merged["d2a"], supply["d2a"], vref, res)
    used_a = _a2d_levels(cfg, merged["a2d"], supply["a2d"], vref, res, canonical)
    used = (used_d and "d2a" in kinds) or (used_a and "a2d" in kinds)
    res.used_ref = used
    if used and vref[2] is not None:
        notes.append(vref[2])
    rser = merged["d2a"].get("r")
    ideal = role == POWERNET or (res.d2a.powernet and role in (D2A, None))   # BIDIR is always gated
    if rser is not None and "d2a" in kinds and ideal:
        key = "map_by_node_ideal#%d" % rser[1]
        if not hits.hit(key):                      # once per command
            hits.mark(key)
            notes.append(warning(cfg.rules[rser[1]].origin, "map_by_node r=%s has no effect on %s: "
                                 "a %s is an ideal source with no series resistance"
                                 % (rser[0], canonical, "supply net" if role == POWERNET
                                    else "d2a powernet node")))
    return res


def _reference(cfg: AmsConfig, kind: str, sup: Dict[str, Tuple[float, int]], vref: VRef,
               res: Resolution) -> Tuple[float, float, bool]:
    """(vss, vdd, vdd from vref) for one IE kind; records supply origins."""
    vss, vdd = float(vref[0]), float(vref[1])
    from_vref = True
    if "vdd" in sup:
        vdd = sup["vdd"][0]
        from_vref = False
        res.origins[kind + ".vdd"] = cfg.rules[sup["vdd"][1]].origin
    if "vss" in sup:
        vss = sup["vss"][0]
        res.origins[kind + ".vss"] = cfg.rules[sup["vss"][1]].origin
    return vss, vdd, from_vref


def _d2a_levels(cfg: AmsConfig, m: Dict[str, Tuple[str, int]], sup: Dict[str, Tuple[float, int]],
                vref: VRef, res: Resolution) -> bool:
    vss, vdd, from_vref = _reference(cfg, "d2a", sup, vref, res)
    used = [False]

    def origin(key: str) -> None:
        res.origins["d2a." + key] = cfg.rules[m[key][1]].origin

    def level(key: str, default: float, default_uses_vdd: bool) -> float:
        if key not in m:
            if default_uses_vdd and from_vref:
                used[0] = True
            return default
        origin(key)
        v, pct = parse_level(m[key][0])
        if pct:
            if from_vref:
                used[0] = True
            return vss + v * (vdd - vss)
        return v

    def time(key: str, default: float, ramp: bool) -> float:
        if key not in m:
            return default
        origin(key)
        t = parse_time(m[key][0])
        return max(t, MIN_RAMP) if ramp else t

    ie = res.d2a
    ie.hiv = level("hiv", vdd, True)
    ie.lov = level("lov", vss, False)
    ie.rise = time("rise_time", ie.rise, True)
    ie.fall = time("fall_time", ie.fall, True)
    ie.delay_rise = time("rise_delay", 0.0, False)
    ie.delay_fall = time("fall_delay", 0.0, False)
    if "x2v" in m:
        origin("x2v")
        ie.x2v = int(m["x2v"][0].strip())
    if "powernet" in m:
        origin("powernet")
        ie.powernet = True
    if "r" in m:                                    # map_by_node (PAMS p243)
        res.origins["map_by_node.r"] = cfg.rules[m["r"][1]].origin
        ie.r_series = parse_number(m["r"][0])
        ie.weak_frac = 1.0                          # no resistance map: one r for every strength
    return used[0]


def _a2d_levels(cfg: AmsConfig, m: Dict[str, Tuple[str, int]], sup: Dict[str, Tuple[float, int]],
                vref: VRef, res: Resolution, canonical: str) -> bool:
    vss, vdd, from_vref = _reference(cfg, "a2d", sup, vref, res)
    used = [False]
    mid = vss + 0.5 * (vdd - vss)

    def origin(key: str) -> str:
        o = cfg.rules[m[key][1]].origin
        res.origins["a2d." + key] = o
        return o

    def level(key: str) -> float:
        if key not in m:
            if from_vref:
                used[0] = True
            return mid
        origin(key)
        v, pct = parse_level(m[key][0])
        if pct:
            if from_vref:
                used[0] = True
            return vss + v * (vdd - vss)
        return v

    ie = res.a2d
    ie.loth = level("loth")
    ie.hith = level("hith")
    if ie.loth > ie.hith:
        where = [res.origins[k] for k in ("a2d.loth", "a2d.hith") if k in res.origins]
        res.notes.append(error(where[-1] if where else canonical,
                               "a2d on %s: loth=%s V is above hith=%s V"
                               % (canonical, fmt(ie.loth), fmt(ie.hith))))
    if "xband" in m:
        origin("xband")
        ie.xband = parse_number(m["xband"][0])
    if "midv_time" in m:
        origin("midv_time")
        ie.midv_time = parse_time(m["midv_time"][0])
    if "midv_logic" in m:
        origin("midv_logic")
        ie.midv_logic = m["midv_logic"][0].strip().upper()     # 0 1 X Z (cut MIDV_L 0 1 2 3)
    return used[0]


def midv_windows(ie: A2D_IE) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """The midv_time windows (PAMS p189): ((falling lo, hi), (rising lo, hi)).

    hith_hys = hith - (hith - loth)/xband and loth_hys = loth + (hith - loth)/xband;
    a falling voltage is in the window between loth and hith_hys, a rising one
    between loth_hys and hith.  Without xband both windows are [loth, hith].
    0 and 1 are still produced only at loth and hith.
    """
    lo, hi = ie.loth, ie.hith
    if ie.xband:
        d = (hi - lo) / ie.xband
        return (lo, hi - d), (lo + d, hi)
    return (lo, hi), (lo, hi)


# -- remove_d2a, disable_ie, ie_reference_voltage ------------------------------------

def removal(cfg: AmsConfig, names: List[str], hits: RuleHits) -> Tuple[bool, Optional[float]]:
    """remove_d2a for a node: (removed, dc volts or None).  The last match wins (§3.2)."""
    cs = case_sensitive(cfg)
    _record(hits, names)
    out: Tuple[bool, Optional[float]] = (False, None)
    for i, (sel, dc, _origin) in enumerate(cfg.remove_d2a):
        if node_matches(sel, names, cs):
            hits.mark("remove_d2a#%d" % i)
            out = (True, dc)
    return out


def disabled(cfg: AmsConfig, names: List[str], hits: RuleHits) -> bool:
    """disable_ie for a node; every matching command is marked."""
    cs = case_sensitive(cfg)
    _record(hits, names)
    found = False
    for i, (sel, _origin) in enumerate(cfg.disable_ie):
        if node_matches(sel, names, cs):
            hits.mark("disable_ie#%d" % i)
            found = True
    return found


def is_skip(volts: Optional[float]) -> bool:
    """AmsConfig.ref_voltages holds skip_node entries with volts = NaN (initfile.SKIP)."""
    return volts is not None and isinstance(volts, float) and math.isnan(volts)


def ref_nodes(cfg: AmsConfig) -> Tuple[List[Tuple[str, Optional[float], int]], List[str]]:
    """(ie_reference_voltage entries (node, voltage or None, index), skip_nodes) for deck.py.

    Entries keep file order.  deck.py resolves each node in the deck and marks
    ref_voltage#<index> when it exists, whether or not a trace reaches it (so
    only a node the deck lacks is TNF).  When several entries resolve to one
    net the last applies (PAMS p106: "If you use multiple ie_reference_voltage
    commands on the same net, the last one specified is used") and each
    earlier one gets a note.  An applied node is a hit of every supply trace,
    outranking the sources of its stage (PAMS p108: the reference overrides
    the ideal supply traced), so it sets the reference of every interface
    element whose trace reaches it; an entry that no trace reaches is a
    warning.  index is the AmsConfig.ref_voltages index.  voltage None means
    "the node's constant value"; a node that is not a constant supply is an
    error wherever a level uses it (dynamic references are not in v1, §2.2).
    skip_node= entries are never TNF.
    """
    entries: List[Tuple[str, Optional[float], int]] = []
    skips: List[str] = []
    for i, (n, v, _origin) in enumerate(cfg.ref_voltages):
        if is_skip(v):
            skips.append(n)
        else:
            entries.append((n, v, i))
    return entries, skips


# -- [MSV-IE-OPT-TNF] ----------------------------------------------------------------

def _nearest(target: str, cands: Sequence[str], n: int = 3) -> List[str]:
    if not cands:
        return []
    t = target.replace("*", "").lower()
    low: Dict[str, str] = {}
    for c in cands:
        low.setdefault(c.lower(), c)
    near = difflib.get_close_matches(t, list(low), n=n, cutoff=0.5)
    if not near:
        def common(c: str) -> int:
            k = 0
            while k < min(len(c), len(t)) and c[k] == t[k]:
                k += 1
            return k
        near = sorted(low, key=lambda c: (-common(c), c))[:n]
    return [low[c] for c in near]


def unmatched(cfg: AmsConfig, hits: RuleHits, names: Optional[Sequence[str]] = None) -> List[Note]:
    """[MSV-IE-OPT-TNF] for every selector nothing marked (§3.2), after every node.

    An error, or a warning after `downgrade_to_warn MSV-IE-OPT-TNF`.  Checks
    rule#i (and vdd#i / vss#i of a rule that matched), remove_d2a#i,
    disable_ie#i, ref_voltage#i (skip_node entries excepted),
    use_spice_inst#i.j and port_connect_inst#i.  Each message names up to three
    nearest canonical names (node selectors), instance paths (inst= and -inst)
    or cells (cell=).  names: the canonical names to suggest from (default:
    those resolve/removal/disabled recorded in hits under SEEN).
    """
    sev = cfg.severity_overrides.get(TNF, "error")
    mk = warning if sev == "warning" else error
    if names is None:
        cands = sorted(k[len(SEEN):] for k in hits.counts if k.startswith(SEEN))
    else:
        cands = sorted(set(names))
    insts = sorted({c.rsplit(".", 1)[0] for c in cands if "." in c})
    cells = sorted(k[len(SEEN_CELL):] for k in hits.counts if k.startswith(SEEN_CELL))
    pools = {"node": (cands, "interface-element names"), "inst": (insts, "instances"),
             "cell": (cells, "cells")}
    out: List[Note] = []

    def tnf(origin: str, command: str, target: str, probe: str, pool: str) -> None:
        names_, what = pools[pool]
        near = _nearest(probe, names_)
        tail = ("nearest %s: %s" % (what, ", ".join(near))) if near else ("there are no %s" % what)
        out.append(mk(origin, "[%s] Option Target Not Found: \"%s\" command, option target \"%s\" "
                      "was not found; the command is ignored (%s)" % (TNF, command, target, tail)))

    for i, r in enumerate(cfg.rules):
        if not hits.hit("rule#%d" % i):
            if r.node is not None:
                tnf(r.origin, r.kind, selector_text(r), r.node, "node")
            elif r.inst is not None:
                tnf(r.origin, r.kind, selector_text(r), r.inst, "inst")
            else:
                tnf(r.origin, r.kind, selector_text(r), r.cell or "", "cell")
            continue
        for which in ("vdd", "vss"):
            given = r.params.get(which)
            key = which
            if given is None and (which + "_port") in r.params:
                given, key = r.params[which + "_port"], which + "_port"
            if given is not None and not hits.hit("%s#%d" % (which, i)):
                tnf(r.origin, r.kind, "%s=%s" % (key, given), given, "node")
    for i, (sel, _dc, origin) in enumerate(cfg.remove_d2a):
        if not hits.hit("remove_d2a#%d" % i):
            tnf(origin, "remove_d2a", "node=%s" % sel, sel, "node")
    for i, (sel, origin) in enumerate(cfg.disable_ie):
        if not hits.hit("disable_ie#%d" % i):
            tnf(origin, "disable_ie", "node=%s" % sel, sel, "node")
    for i, (n, v, origin) in enumerate(cfg.ref_voltages):
        if not is_skip(v) and not hits.hit("ref_voltage#%d" % i):
            tnf(origin, "ie_reference_voltage", "node=%s" % n, n, "node")
    for i, us in enumerate(cfg.use_spice):
        for j, inst in enumerate(us.insts):
            if not hits.hit("use_spice_inst#%d.%d" % (i, j)):
                tnf(us.origin, "use_spice", "-inst %s" % inst, inst, "inst")
    for i, pc in enumerate(cfg.port_connects):
        if pc.inst and not hits.hit("port_connect_inst#%d" % i):
            tnf(pc.origin, "port_connect", "-inst %s" % pc.inst, pc.inst, "inst")
    return out


# -- report lines ----------------------------------------------------------------------

def paste_line(kind: str, canonical: str, ie: object) -> str:
    """One IE as a control-file line that selects the same node with the same levels.

    The one writer of the IE report's control-file lines (§3.6; report.py calls
    ie_lines, which calls this):
      d2a [powernet] hiv=<v> lov=<v> (rf_time=<s> | rise_time=<s> fall_time=<s>)
          [delay=<s> | rise_delay=<s> fall_delay=<s>] x2v=<n> node=<c>;
      a2d loth=<v> hith=<v> [xband=<x>] [midv_time=<s>] [midv_logic=<l>] node=<c>;
      map_by_node r=<ohms> node=<c>;
    kind 'd2a' and 'map_by_node' take a D2A_IE, 'a2d' an A2D_IE.  Every value that
    depends on the reference supply (hiv, lov, loth, hith) is written, and so are the
    ramps and x2v; only fixed defaults are left out (zero delays, no xband, no
    midv_time, midv_logic=X without midv_time).  A powernet line keeps its ramps,
    delays and x2v: a d2a powernet D2A follows its digital value with them.  Values
    are spelled by numbers.fmt, so each one round-trips exactly through
    parse_control and resolve.
    """
    if kind in ("d2a", "map_by_node"):
        assert isinstance(ie, D2A_IE)
        if kind == "map_by_node":
            return "map_by_node r=%s node=%s;" % (fmt(ie.r_series), canonical)
        keys = ["powernet"] if ie.powernet else []
        keys += ["hiv=" + fmt(ie.hiv), "lov=" + fmt(ie.lov)]
        if ie.rise == ie.fall:
            keys.append("rf_time=" + fmt(ie.rise))
        else:
            keys += ["rise_time=" + fmt(ie.rise), "fall_time=" + fmt(ie.fall)]
        if ie.delay_rise or ie.delay_fall:
            if ie.delay_rise == ie.delay_fall:
                keys.append("delay=" + fmt(ie.delay_rise))
            else:
                keys += ["rise_delay=" + fmt(ie.delay_rise), "fall_delay=" + fmt(ie.delay_fall)]
        keys.append("x2v=%d" % ie.x2v)
    elif kind == "a2d":
        assert isinstance(ie, A2D_IE)
        keys = ["loth=" + fmt(ie.loth), "hith=" + fmt(ie.hith)]
        if ie.xband is not None:
            keys.append("xband=" + fmt(ie.xband))
        if ie.midv_time is not None:
            keys.append("midv_time=" + fmt(ie.midv_time))
        if ie.midv_time is not None or ie.midv_logic != "X":
            keys.append("midv_logic=" + ie.midv_logic)
    else:
        raise ValueError("kind must be 'a2d', 'd2a' or 'map_by_node', not %r" % (kind,))
    return "%s %s node=%s;" % (kind, " ".join(keys), canonical)


def ie_lines(role: str, canonical: str, d2a: Optional[D2A_IE], a2d: Optional[A2D_IE]) -> List[str]:
    """Every control-file line that reproduces one node's interface elements (§3.6).

    D2A and BIDIR: the d2a line, then `map_by_node r=` when the node's D2A is gated
    (not a D2A powernet) and its resistance is not the resistance-map default; A2D
    and BIDIR: the a2d line; POWERNET (a supply1/supply0 net): `d2a powernet` with
    the merged levels, as VCS treats supply nets as d2a powernet IEs (PAMS p25,
    p205), so pasting it back keeps the supply at its level (node.dc is the hiv of
    a supply1 net, the lov of a supply0 net).  Any other role: no line.
    """
    out: List[str] = []
    if role in (D2A, BIDIR) and d2a is not None:
        out.append(paste_line("d2a", canonical, d2a))
        gated = role == BIDIR or not d2a.powernet
        if gated and (d2a.r_series != _DEFAULT_D2A.r_series or d2a.weak_frac != _DEFAULT_D2A.weak_frac):
            out.append(paste_line("map_by_node", canonical, d2a))
    if role in (A2D, BIDIR) and a2d is not None:
        out.append(paste_line("a2d", canonical, a2d))
    if role == POWERNET and d2a is not None:
        out.append(paste_line("d2a", canonical, replace(d2a, powernet=True)))
    return out


def kinds_of(role: str) -> Tuple[str, ...]:
    """The rule kinds a node of this role uses (the role filter of resolve)."""
    return _ROLE_KINDS.get(role, ())
