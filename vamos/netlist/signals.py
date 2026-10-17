"""Signals and names (docs/VAMOS_SPECTRE_DESIGN.md §6; the API is the §10 block).

Phase 0 (§12) froze Ref, SignalMap and the psf type values; phase 1 (S2) implements resolve().

signals.resolve lists, per step, the requested signals as (Ref, Spectre name, psf type) in PSF
order (§6.4); render records each Ref's engine column in `names` (§4.5 item 6), the only
source of engine columns.  It returns a copy.deepcopy of the netlist with the probes inserted
and Netlist.values reduced per engine from RunPlan.dependents (§4.1), never the input IR.

What resolve does:
  - flattens the IR once, on the copy, with tables.path_envs (nominal values): every instance
    path, every net as an IR path ('.' hierarchy, the original case), the ports of a subckt
    instance bound to its caller's nets, an inline subckt's component named like the subckt
    taking the instance's name (§3.7, §6.4);
  - allpub (§6.1), from the IR before any insertion: every net at every level (down to nestlvl
    for lvlpub) and the currents of the vsource, iprobe and inductor instances, never vamos_*
    items, vcvs/ccvs flows, r = 0 shorts or device internals; sorted case-insensitively;
  - the save statements (§6.2): nodes, X:term (a §3.8 terminal name or its 1-based index),
    X:currents, bare component names, the wildcards with depth/sigtype/devtype/subckt/exclude,
    the node-over-instance rule, an unmatched item warned and dropped;
  - currents (§6.3): native for a vsource, iprobe, inductor, resistor and the output port of a
    vcvs/ccvs (the first terminal), a 0 V probe source vamos_tp<k> in series with every other
    terminal (the terminal moves to vamos_tn<k>), in the copy;
  - the noise STRUCT type names (§8.4), the noise input of §5.6 (vamos_nin<k> across the output
    pair), the xf traces (one per IR independent source), the psf types of §6.4;
  - the ic/nodeset names and the names in readns/readic files (§3.10), warned when unmatched;
  - the output options of §6.1 (notes and warnings).

Everything here speaks IR names; the engines' spellings are render's (§4.5 item 5).
"""

from __future__ import annotations

import copy
import fnmatch
import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from vamos.netlist import plan as PL
from vamos.netlist import tables as T
from vamos.netlist.expr_ast import Num, Str
from vamos.netlist.ir import Analysis, Cond, Instance, Netlist, SaveSpec, Source, Subckt, flat_analyses
from vamos.netlist.plan import AnalysisStep, RunPlan
from vamos.notes import ERROR, NOTE, WARNING, Note

# ('v', node path) | ('i', element path) | ('noise', instance path) | ('onoise',) | ('inoise',)
# | ('gain',) | ('tf', source path); IR names, '.' hierarchy (§6.4)
Ref = Tuple[str, ...]

# The psf type of a signal (§6.4, §8.3, §8.4): the PSF TRACE type name.  Besides these fixed names,
# a per-instance noise Ref carries the instance's STRUCT type name (its model, else its master, a
# collision suffixed, §8.4).
PSF_V = "V"                    # a node voltage
PSF_I = "I"                    # a current
PSF_V_NOISE = "V/sqrt(Hz)"     # noise out; noise in with a vsource input
PSF_A_NOISE = "A/sqrt(Hz)"     # noise in with an isource input
PSF_GAIN_VV = "V/V"            # gain, by the input's kind
PSF_GAIN_VA = "V/A"
PSF_TYPES = (PSF_V, PSF_I, PSF_V_NOISE, PSF_A_NOISE, PSF_GAIN_VV, PSF_GAIN_VA)


@dataclass
class SignalMap:
    per_step: Dict[str, List[Tuple[Ref, str, str]]] = field(default_factory=dict)   # step id -> (Ref, Spectre
    #                                                                                  name, psf type), PSF order
    allpub: Set[str] = field(default_factory=set)       # step ids printed as save default / V(*) (§6.4)


# -- the data resolve reads -------------------------------------------------------------------------

# The fixed PSF TYPE names (§8.3) a noise STRUCT type may not take (§8.4)
PSF_FIXED_TYPES = ("sweep", "LogicV", "V", "I", "MMF", "Wb", "Temp", "Pwr", "U", "V/Sec", "I/Sec", "1/Sec",
                   "HOD", "Q", "V/sqrt(Hz)", "A/sqrt(Hz)", "V^2/Hz", "V/V", "V/A")
# IR kinds whose instances contribute noise (§7.1: every noisy IR instance; §8.4)
NOISY_KINDS = ("r", "d", "q", "j", "m", "y")
# §3.8's terminal names by IR kind, where the instance names no master in SPECTRE_MASTERS
TERMINALS_BY_KIND: Dict[str, Tuple[str, ...]] = {
    "r": ("1", "2"), "c": ("1", "2"), "l": ("1", "2"), "v": ("p", "n"), "i": ("sink", "src"),
    "e": ("p", "n", "ps", "ns"), "g": ("sink", "src", "ps", "ns"), "h": ("p", "n"), "f": ("sink", "src"),
    "d": ("a", "c"), "q": ("c", "b", "e", "s"), "j": ("d", "g", "s", "b"), "m": ("d", "g", "s", "b")}
SAVE_MODES = ("selected", "allpub", "lvlpub", "all", "lvl", "none", "nooutput")
PROBE_PREFIX = "vamos_tp"
PROBE_NODE_PREFIX = "vamos_tn"

_Signal = Tuple[Ref, str, str, str]      # (Ref, Spectre name, psf type, 'v' | 'i' | 'n' | 'x')


@dataclass
class _Path:
    """One instance path of the flattened IR."""
    path: str                                 # "" at the top level, else "x1.x2" (IR names)
    subckt: Optional[Subckt]
    body: List[object]                        # the body list of the copy the probes are appended to
    nodemap: Dict[str, str]                   # a port -> the caller's net (IR path)
    depth: int                                # 0 at the top level: its nets are at level 1
    items: List[Instance] = field(default_factory=list)


@dataclass
class _Inst:
    path: str                                 # the IR path
    inst: Instance
    at: _Path
    alias: Optional[str] = None               # the Spectre name of an inline subckt's component named like it

    @property
    def spectre(self) -> str:
        return self.alias or self.path

    @property
    def level(self) -> int:
        return self.at.depth + 1


def _text(v: object) -> Optional[str]:
    if isinstance(v, Str):
        return v.text
    if isinstance(v, str):
        return v
    return None


def is_no(v: object) -> bool:
    """isnoisy=no as the parser may spell it, read as the emitters read it (vacask._is_no, xyce._is_no):
    Str or Name no|false|0, or the number 0 (phase 2: isnoisy=0 was a G form on Xyce and not here)."""
    if isinstance(v, Num):
        return v.value == 0.0
    if isinstance(v, Str):
        text = v.text
    elif isinstance(v, str):
        text = v
    else:
        text = getattr(v, "name", None)                # a Name
    return isinstance(text, str) and text.lower() in ("no", "false", "0")


def _value(v: object) -> Optional[float]:
    if isinstance(v, Num):
        return float(v.value)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None


def _glob(pattern: str, text: str) -> bool:
    """Spectre's save wildcards: * any string, ? any character (the other glob characters literal)."""
    pat = "".join("[%s]" % ch if ch in "[]" else ch for ch in pattern)
    return fnmatch.fnmatchcase(text, pat)


def _is_vamos(name: str) -> bool:
    return name.rsplit(".", 1)[-1].startswith("vamos_")


def _container_of(items: List[object], target: object) -> Optional[List[object]]:
    """The item list that holds target (by identity): items itself or a branch or default of a Cond
    in it, recursively; None when it is not there."""
    for it in items:
        if it is target:
            return items
    for it in items:
        if isinstance(it, Cond):
            for _cond, body in it.branches:
                found = _container_of(body, target)
                if found is not None:
                    return found
            found = _container_of(it.default, target)
            if found is not None:
                return found
    return None


class _Flat:
    """The flattened IR of the render copy, with the probe insertion (§6.3)."""

    def __init__(self, nl: Netlist, plan: RunPlan, notes: List[Note]):
        self.nl, self.plan, self.notes = nl, plan, notes
        self.paths: List[_Path] = []
        self.by_path: Dict[str, _Path] = {}
        self.insts: Dict[str, _Inst] = {}                 # IR path -> entry, IR instance order
        self.aliases: Dict[str, str] = {}                 # Spectre name of an inline component -> IR path
        self.nodes: Dict[str, int] = {}                   # net (IR path) -> its level (top = 1)
        self.node_at: Dict[str, Tuple[_Path, str]] = {}   # net -> (the path that declares it, local name)
        self.globals = set(nl.globals)
        self.probes: Dict[Tuple[int, int], str] = {}      # (id(instance), terminal) -> probe name
        self.n_probe = 0
        # §6.3: VACASK's ac and acxf save no resistor current (p(r, i) is an op/dc/tran opvar), and Xyce's
        # .PRINT AC prints no I(R) (phase 2), so a resistor terminal saved for a run with such a step gets
        # the generic probe on both engines
        self.small_signal = any(a.op == "analysis" and a.step is not None and a.step.kind in ("ac", "xf")
                                for a in plan.actions)
        self._build()

    # -- flattening
    def _resolve(self, at: _Path, local: str) -> str:
        if local == "0" or local in self.globals or at.path == "":
            return local
        if local in at.nodemap:
            return at.nodemap[local]
        return at.path + "." + local

    def _build(self) -> None:
        temp = _value(self.plan.options.get("temp")) or 27.0
        tnom = _value(self.plan.options.get("tnom")) or 27.0
        try:
            with PL.substituted(PL.temp_sites(self.nl), temp, tnom):     # §3.5: temp and tnom in conditions
                envs = list(T.path_envs(self.nl, dialect=PL.DIALECT))
        except Exception as exc:                          # the plan has reported it; keep what resolves
            self.notes.append(Note(ERROR, "", "the netlist cannot be flattened: %s" % exc))
            envs = []
        for pe in envs:
            if pe.subckt is None:
                p = _Path("", None, self.nl.body, {}, 0, list(pe.items))
            else:
                parent_path, _, xname = pe.path.rpartition(".")
                parent = self.by_path.get(parent_path)
                if parent is None:
                    continue
                xinst = next((it for it in parent.items if it.kind == "x" and it.name == xname), None)
                nodemap: Dict[str, str] = {}
                if xinst is not None:
                    for i, port in enumerate(pe.subckt.ports):
                        if i < len(xinst.nodes):
                            nodemap[port] = self._resolve(parent, xinst.nodes[i])
                p = _Path(pe.path, pe.subckt, pe.subckt.body, nodemap, parent.depth + 1, list(pe.items))
            self.paths.append(p)
            self.by_path[p.path] = p
            for it in p.items:
                full = it.name if not p.path else p.path + "." + it.name
                ent = _Inst(full, it, p)
                if p.subckt is not None and p.subckt.inline and it.name == p.subckt.name:
                    ent.alias = p.path
                    self.aliases[p.path] = full
                self.insts.setdefault(full, ent)
                for n in it.nodes:
                    if n == "0" or (p.path and n in p.nodemap):      # ground; a port: the caller's net
                        continue
                    g = self._resolve(p, n)
                    level = 1 if n in self.globals else p.depth + 1
                    self.nodes[g] = min(self.nodes.get(g, level), level)
                    self.node_at.setdefault(g, (p, n))

    # -- lookups
    def lookup(self, name: str) -> Optional[_Inst]:
        """The instance an item names: an inline subckt's component by the caller's name first
        [M UG p.111], else the IR path."""
        if name in self.aliases:
            return self.insts.get(self.aliases[name])
        return self.insts.get(name)

    def net(self, name: str) -> Optional[str]:
        """The net an IR node name denotes: itself, or the caller's net when it names a subckt port
        (`X1.b` for port b of X1); None when it names no net."""
        if name == "0" or name in self.nodes or name in self.globals:
            return name
        path, sep, local = name.rpartition(".")
        if sep:
            p = self.by_path.get(path)
            if p is not None and local in p.nodemap:
                return p.nodemap[local]
        return None

    def subckt_of(self, ent: _Inst) -> Optional[Subckt]:
        child = self.by_path.get(ent.path)
        return child.subckt if child is not None else None

    def terminals(self, ent: _Inst) -> Tuple[str, ...]:
        """§3.8's terminal names: a subckt's ports, a master's names, none for a Verilog-A module."""
        it = ent.inst
        if it.kind == "x":
            sub = self.subckt_of(ent)
            return tuple(sub.ports) if sub is not None else ()
        if it.kind == "y":
            return ()
        row = T.SPECTRE_MASTERS.get(it.prim) if it.prim else None
        if row is not None and row.terminals:
            return row.terminals
        return TERMINALS_BY_KIND.get(it.kind, ())

    def terminal_index(self, ent: _Inst, term: str) -> Optional[int]:
        """A terminal name of §3.8 or a 1-based index; None when it names no terminal."""
        n = len(ent.inst.nodes)
        if term.isdigit():
            i = int(term) - 1
            return i if 0 <= i < n else None
        for i, t in enumerate(self.terminals(ent)):
            if t == term and i < n:
                return i
        return None

    def terminal_label(self, ent: _Inst, i: int) -> str:
        """How X:currents names terminal i: a device's name, a subckt's or module's 1-based index (§6.2)."""
        terms = self.terminals(ent)
        if ent.inst.kind in ("x", "y") or i >= len(terms):
            return str(i + 1)
        return terms[i]

    def node_global(self, ent: _Inst, idx: int) -> str:
        return self._resolve(ent.at, ent.inst.nodes[idx])

    # -- currents (§6.3)
    def current(self, ent: _Inst, idx: int, origin: str) -> Optional[Ref]:
        it = ent.inst
        if idx >= len(it.nodes):
            self.notes.append(Note(WARNING, origin, "%s has no terminal %d" % (ent.spectre, idx + 1)))
            return None
        kind = it.kind
        if self.plan.engine == "vacask" and kind in ("v", "l", "e", "h", "r") and _multiplied(it):
            # VACASK's native flow(br) and p(r, i) of a $mfactor instance are one unit's current; Spectre's
            # terminal current (and Xyce's I(R1)) is the total: a probe measures it (phase 1b, XDM rlc)
            return self.probe(ent, idx)
        if idx == 0 and kind in ("v", "l", "e", "h"):
            return ("i", ent.path)
        if kind == "r":
            v = it.value
            if isinstance(v, Num) and math.isinf(v.value):
                self.notes.append(Note(WARNING, origin, "%s is an infinite resistor: its terminal current is 0 and "
                                       "is not written" % ent.spectre))
                return None
            if idx == 0:
                noisy_off = is_no(it.params.get("isnoisy"))
                has_tc = any(k in it.params for k in ("tc1", "tc2"))
                short = isinstance(v, Num) and v.value == 0
                g_form = self.plan.engine == "xyce" and noisy_off and not has_tc and not short
                # VACASK has no dv/di of a resistor (its r=0 short is a 0 V source, which has them); Xyce's
                # .PRINT AC cannot print I(R) either ("Function or variable I(R1) is not defined", rc 1,
                # measured on DEVELOPMENT-202610100047 in phase 2), R=0 included
                no_ac_opvar = self.small_signal and (self.plan.engine == "xyce" or not short)
                if not g_form and not no_ac_opvar:
                    return ("i", ent.path)
        return self.probe(ent, idx)

    def probe(self, ent: _Inst, idx: int) -> Ref:
        """A 0 V source vamos_tp<k> in series with the terminal, in the copy (§6.3)."""
        it = ent.inst
        key = (id(it), idx)
        name = self.probes.get(key)
        if name is None:
            self.n_probe += 1
            name = "%s%d" % (PROBE_PREFIX, self.n_probe)
            new_node = "%s%d" % (PROBE_NODE_PREFIX, self.n_probe)
            orig = it.nodes[idx]
            it.nodes[idx] = new_node
            # the probe goes where its instance is: inside the same if/else branch of an inline subckt, so
            # a variant that does not hold the instance prints no dangling probe (phase 2's integration,
            # X's seam: Xyce warned "connected to only 1 device Terminal" for every other branch)
            where = _container_of(ent.at.body, it)
            (where if where is not None else ent.at.body).append(
                Instance(name, "v", [orig, new_node], source=Source(dc=Num(0.0)), origin=it.origin, prim="vsource"))
            self.probes[key] = name
        prefix = ent.at.path + "." if ent.at.path else ""
        return ("i", prefix + name)

    # -- allpub (§6.1)
    def allpub(self, nestlvl: Optional[int]) -> List[_Signal]:
        out: List[_Signal] = []
        for g, level in self.nodes.items():
            if _is_vamos(g):
                continue
            if nestlvl is not None and level > nestlvl:
                continue
            out.append((("v", g), g, PSF_V, "v"))
        for ent in self.insts.values():
            it = ent.inst
            if it.name.startswith("vamos_"):
                continue
            if nestlvl is not None and ent.level > nestlvl:
                continue
            if (it.kind == "v" and it.prim in ("vsource", "iprobe", "")) or it.kind == "l":
                ref = self.current(ent, 0, "") or ("i", ent.path)      # a multiplied instance: a probe on VACASK
                out.append((ref, "%s:%s" % (ent.spectre, self.terminal_label(ent, 0)), PSF_I, "i"))
        out.sort(key=lambda s: (s[1].lower(), s[1]))
        return out


# -- save statements (§6.2) -------------------------------------------------------------------------

def _multiplied(it: Instance) -> bool:
    """An instance whose m is given and not 1 (a non-constant m counts as multiplied)."""
    m = it.params.get("m")
    if m is None:
        return False
    return not (isinstance(m, Num) and m.value == 1.0)


def _terminal_range(ent: _Inst) -> range:
    """The terminals X:currents lists: all of them, a two-terminal component only the first."""
    n = len(ent.inst.nodes)
    return range(1) if n == 2 and ent.inst.kind != "x" else range(n)


def _dedupe(signals: Sequence[_Signal]) -> List[_Signal]:
    seen: Set[Ref] = set()
    out: List[_Signal] = []
    for s in signals:
        if s[0] not in seen:
            seen.add(s[0])
            out.append(s)
    return out


def _instance_currents(flat: _Flat, ent: _Inst, origin: str) -> List[_Signal]:
    """X:currents: every terminal, a two-terminal component only the first [M ref19 p.518]; names for
    device terminals, 1-based indices for subckt terminals (§6.2).  A subckt instance lists every
    terminal (ref19 p.518's `x1.x21:2`)."""
    out: List[_Signal] = []
    for i in _terminal_range(ent):
        ref = flat.current(ent, i, origin)
        if ref is not None:
            out.append((ref, "%s:%s" % (ent.spectre, flat.terminal_label(ent, i)), PSF_I, "i"))
    return out


def _resolve_item(flat: _Flat, spec: SaveSpec, token: str) -> List[_Signal]:
    origin = spec.origin
    if "*" in token or "?" in token:
        return _wildcard(flat, spec, token)
    name, sep, suffix = token.partition(":")
    if not sep:
        net = flat.net(name)
        if net is not None and net != "0":                  # the node wins over an instance [M UG p.61]
            return [(("v", net), name, PSF_V, "v")]
        ent = flat.lookup(name)
        if ent is not None:
            flat.notes.append(Note(WARNING, origin, "save %s: the operating point of %s is not written; its terminal "
                                   "currents are" % (token, name)))
            return _instance_currents(flat, ent, origin)
        flat.notes.append(Note(WARNING, origin, "save %s matches no node or instance; not saved" % token))
        return []
    ent = flat.lookup(name)
    if ent is None:
        flat.notes.append(Note(WARNING, origin, "save %s: no instance %s; not saved" % (token, name)))
        return []
    low = suffix.lower()
    if low == "currents":
        return _instance_currents(flat, ent, origin)
    if low in ("oppoint", "pwr"):
        flat.notes.append(Note(WARNING, origin, "save %s: %s is not written" % (
            token, "the operating point" if low == "oppoint" else "the power")))
        return []
    idx = flat.terminal_index(ent, suffix)
    if idx is None:
        if ent.inst.kind == "y" and not suffix.isdigit():
            flat.notes.append(Note(WARNING, origin, "save %s: the terminal names of Verilog-A module %s are not "
                                   "known; use the 1-based index; not saved" % (token, ent.inst.prim or ent.inst.master)))
        else:
            flat.notes.append(Note(WARNING, origin, "save %s: %s is not a terminal of %s (an operating-point "
                                   "parameter is not written); not saved" % (token, suffix, name)))
        return []
    ref = flat.current(ent, idx, origin)
    return [(ref, token, PSF_I, "i")] if ref is not None else []


def _wildcard(flat: _Flat, spec: SaveSpec, token: str) -> List[_Signal]:
    """The wildcard forms of ref19 pp.517-519: * and ?, depth, sigtype, devtype, subckt, exclude, ports."""
    origin = spec.origin
    sigtype = (spec.sigtype or "node").lower()
    depth = spec.depth
    out: List[_Signal] = []

    def within(path: str) -> Optional[Tuple[str, int]]:
        """(the name relative to the subckt= instance, its depth offset); None outside such an instance."""
        if not spec.subckt:
            return path, 0
        for p in flat.paths:
            if p.subckt is not None and p.subckt.name == spec.subckt and (path == p.path or path.startswith(p.path + ".")):
                return path[len(p.path) + 1:], p.depth
        return None

    def excluded(name: str) -> bool:
        return any(_glob(x, name) for x in spec.exclude)

    def devtype_ok(it: Instance) -> bool:
        return not spec.devtype or spec.devtype in (it.prim, it.master or "")

    ipat, sep, tpat = token.partition(":")
    if sep:                                                 # a component pattern: X:term
        for ent in flat.insts.values():
            it = ent.inst
            if it.name.startswith("vamos_") or (sigtype == "subckt") != (it.kind == "x") and sigtype != "all" \
                    and not (sigtype in ("node", "dev") and it.kind != "x"):
                continue
            rel = within(ent.path)
            if rel is None or not devtype_ok(it):
                continue
            relname, off = rel
            if depth is not None and ent.level - off > depth:
                continue
            if not _glob(ipat, relname) and not (ent.alias and _glob(ipat, ent.alias)):
                continue
            all_terms = tpat.lower() == "currents"
            for i in (_terminal_range(ent) if all_terms else range(len(it.nodes))):
                label = flat.terminal_label(ent, i)
                if not all_terms and not (_glob(tpat, label) or _glob(tpat, str(i + 1))):
                    continue
                name = "%s:%s" % (ent.spectre, label)
                if excluded(name):
                    continue
                ref = flat.current(ent, i, origin)
                if ref is not None:
                    out.append((ref, name, PSF_I, "i"))
    else:
        if sigtype in ("node", "all") and spec.subckt:
            # relative to each instance of the master: its internal nets, and its ports with ports=yes
            saveports = spec.ports or (_text(flat.nl.options.get("saveports")) or "").lower() == "yes"
            for p in flat.paths:
                if p.subckt is None or p.subckt.name != spec.subckt:
                    continue
                for g, level in flat.nodes.items():
                    if _is_vamos(g) or not g.startswith(p.path + "."):
                        continue
                    if depth is not None and level - p.depth > depth:
                        continue
                    if _glob(ipat, g[len(p.path) + 1:]) and not excluded(g):
                        out.append((("v", g), g, PSF_V, "v"))
                if saveports:
                    for port, g in p.nodemap.items():
                        if g != "0" and _glob(ipat, port) and not excluded(g):
                            out.append((("v", g), p.path + "." + port, PSF_V, "v"))
        elif sigtype in ("node", "all"):
            for g, level in flat.nodes.items():
                if _is_vamos(g) or (depth is not None and level > depth):
                    continue
                if _glob(ipat, g) and not excluded(g):
                    out.append((("v", g), g, PSF_V, "v"))
        if sigtype in ("dev", "subckt", "all"):
            for ent in flat.insts.values():
                it = ent.inst
                if it.name.startswith("vamos_"):
                    continue
                if (sigtype == "dev" and it.kind == "x") or (sigtype == "subckt" and it.kind != "x"):
                    continue
                rel = within(ent.path)
                if rel is None or not devtype_ok(it):
                    continue
                relname, off = rel
                if depth is not None and ent.level - off > depth:
                    continue
                if _glob(ipat, relname):
                    out.extend(s for s in _instance_currents(flat, ent, origin) if not excluded(s[1]))
    if not out:
        flat.notes.append(Note(WARNING, origin, "save %s matches nothing; not saved" % token))
    return out


def _explicit(flat: _Flat, saves: Sequence[SaveSpec]) -> List[_Signal]:
    out: List[_Signal] = []
    for spec in saves:
        if (spec.filter or "none").lower() == "rc":
            flat.notes.append(Note(WARNING, spec.origin, "save filter=rc is not applied: RC-only nodes are kept"))
        for token in spec.items:
            out.extend(_resolve_item(flat, spec, token))
    return _dedupe(out)


# -- output options (§6.1) ------------------------------------------------------------------------------

def _output_options(nl: Netlist, notes: List[Note]) -> Dict[str, object]:
    nlopts: Dict[str, object] = {}                       # the netlist's options (not vamos options)
    for key, raw in nl.options.items():
        v: object = _text(raw)
        if v is None:
            v = _value(raw)
        if v is None:
            v = raw
        nlopts[key] = v
    save = str(nlopts.get("save", "selected")).lower()
    if save not in SAVE_MODES:
        notes.append(Note(WARNING, "", "options save=%s is not one of %s; selected is used" % (save, ", ".join(SAVE_MODES))))
        save = "selected"
    nlopts["save"] = save
    if save in ("all", "lvl"):
        notes.append(Note(NOTE, "", "options save=%s: the internal nodes of active devices are not written (v1)" % save))
    cur = str(nlopts.get("currents", "selected")).lower()
    if cur in ("all", "nonlinear"):
        notes.append(Note(WARNING, "", "options currents=%s: only the explicitly saved currents are written" % cur))
    if _value(nlopts.get("subcktprobelvl", 0.0)) not in (None, 0.0):
        notes.append(Note(WARNING, "", "options subcktprobelvl=%s: subckt terminal currents are not saved automatically"
                          % nlopts["subcktprobelvl"]))
    if str(nlopts.get("pwr", "none")).lower() != "none":
        notes.append(Note(WARNING, "", "options pwr=%s: power waveforms are not written" % nlopts["pwr"]))
    if str(nlopts.get("useprobes", "no")).lower() == "yes":
        notes.append(Note(NOTE, "", "options useprobes=yes: vamos measures currents with its own probes either way"))
    if "useterms" in nlopts:
        notes.append(Note(NOTE, "", "options useterms=%s: it names only the automatic currents=all/subcktprobelvl "
                          "saves, which are not written" % nlopts["useterms"]))
    if str(nlopts.get("saveahdlvars", "selected")).lower() != "selected":
        notes.append(Note(WARNING, "", "options saveahdlvars=%s: AHDL variables are not written" % nlopts["saveahdlvars"]))
    if str(nlopts.get("savefilter", "none")).lower() == "rc":
        notes.append(Note(WARNING, "", "options savefilter=rc is not applied: RC-only nodes are kept"))
    return nlopts


def _nestlvl(v: object) -> Optional[int]:
    x = _value(v)
    if x is None or math.isinf(x):
        return None
    return max(1, int(x))


# -- noise and xf (§5.6, §6.4, §8.4) ----------------------------------------------------------------------

def _is_noisy(ent: _Inst) -> bool:
    it = ent.inst
    if it.kind not in NOISY_KINDS or it.name.startswith("vamos_"):
        return False
    if it.kind == "r":
        v = it.value
        if isinstance(v, Num) and (v.value == 0 or math.isinf(v.value)):
            return False
        if is_no(it.params.get("isnoisy")):
            return False
    return True


def _noise_types(flat: _Flat, notes: List[Note]) -> Dict[str, str]:
    """Instance path -> its STRUCT type name: the model, else the master; a collision suffixed (§8.4)."""
    by_key: Dict[str, str] = {}
    taken: Set[str] = set(PSF_FIXED_TYPES)
    out: Dict[str, str] = {}
    for ent in flat.insts.values():
        if not _is_noisy(ent):
            continue
        it = ent.inst
        key = it.master if (it.master and it.kind != "y") else (it.prim or it.master or it.kind)
        if key not in by_key:
            name = key
            k = 1
            while name in taken:
                k += 1
                name = "%s_%d" % (key, k)
            if name != key:
                notes.append(Note(NOTE, it.origin, "noise STRUCT type %s collides with a PSF type name; written as %s"
                                  % (key, name)))
            taken.add(name)
            by_key[key] = name
        out[ent.path] = by_key[key]
    return out


def _noise_signals(flat: _Flat, step: AnalysisStep, types: Dict[str, str]) -> List[_Signal]:
    out: List[_Signal] = []
    for ent in flat.insts.values():
        if ent.path in types:
            out.append((("noise", ent.path), ent.spectre, types[ent.path], "n"))
    out.append((("onoise",), "out", PSF_V_NOISE, "n"))
    inp = step.noise_input
    if inp and not is_vamos_input(inp):
        ent = flat.lookup(inp)
        isrc = ent is not None and ent.inst.kind == "i"
        out.append((("gain",), "gain", PSF_GAIN_VA if isrc else PSF_GAIN_VV, "n"))
        out.append((("inoise",), "in", PSF_A_NOISE if isrc else PSF_V_NOISE, "n"))
    return out


def _xf_signals(flat: _Flat) -> List[_Signal]:
    """One trace per IR independent source: kind v with prim vsource, kind i, at every level (§6.4)."""
    out: List[_Signal] = []
    for ent in flat.insts.values():
        it = ent.inst
        if it.name.startswith("vamos_"):
            continue
        if (it.kind == "v" and it.prim in ("vsource", "")) or it.kind == "i":
            out.append((("tf", ent.path), "%s:%s" % (ent.spectre, flat.terminal_label(ent, 0)),
                        PSF_GAIN_VA if it.kind == "i" else PSF_GAIN_VV, "x"))
    return out


def _analysis_named(nl: Netlist, name: str) -> Optional[Analysis]:
    for a in flat_analyses(nl.analyses):
        if a.name == name and a.kind in PL.STEP_KINDS:
            return a
    return None


def _output_pair(flat: _Flat, a: Analysis, step: AnalysisStep, notes: List[Note]) -> Optional[Tuple[str, str]]:
    """The output node pair of a noise analysis as IR paths (§3.11): plan.build's args["p"]/["n"]
    (p n, or oprobe's terminals), else resolved here from the IR for a hand-built step."""
    p_arg = _text(step.args.get("p"))
    if p_arg:
        return p_arg, _text(step.args.get("n")) or "0"
    oprobe = _text(step.args.get("oprobe"))
    if oprobe:
        ent = flat.lookup(oprobe)
        if ent is None or len(ent.inst.nodes) < 2:
            notes.append(Note(ERROR, a.origin, "noise %s: oprobe=%s names no two-terminal instance" % (a.name, oprobe)))
            return None
        return flat.node_global(ent, 0), flat.node_global(ent, 1)
    if not a.nodes:
        notes.append(Note(ERROR, a.origin, "noise %s: no output node pair" % a.name))
        return None
    p = a.nodes[0]
    n = a.nodes[1] if len(a.nodes) > 1 else "0"
    return p, n


def _insert_noise_input(flat: _Flat, step: AnalysisStep, notes: List[Note]) -> None:
    """§5.6: vamos_nin<k> (p n) isource dc=0 across the output pair, in the body where both nets live."""
    a = _analysis_named(flat.nl, step.name)
    if a is None:
        notes.append(Note(ERROR, "", "noise step %s: no analysis named %s in the netlist" % (step.id, step.name)))
        return
    pair = _output_pair(flat, a, step, notes)
    if pair is None:
        return
    where: Optional[_Path] = None
    local: List[str] = []
    for g in pair:
        if g == "0" or g in flat.globals:
            local.append(g)
            continue
        at = flat.node_at.get(g)
        if at is None:
            notes.append(Note(ERROR, a.origin, "noise %s: output node %s is not a net of the circuit" % (a.name, g)))
            return
        p, name = at
        if where is not None and where is not p:
            notes.append(Note(ERROR, a.origin, "noise %s: the output nodes %s and %s lie in different subckt bodies; "
                              "the noise input of §5.6 cannot be placed (v1)" % (a.name, pair[0], pair[1])))
            return
        where = p
        local.append(name)
    base = (step.noise_input or "vamos_nin").rsplit(".", 1)[-1]
    body = (where or flat.by_path[""]).body
    body.append(Instance(base, "i", local, source=Source(dc=Num(0.0)), origin=a.origin, prim="isource"))
    # placed inside a subckt body, the input is that path's instance: the step names it by its IR path,
    # which render prints (VACASK in="I2:vamos_nin1"; Xyce AC 1 0 in the body, .NOISE naming XI2:VAMOS_NIN1,
    # under §5.3's one-path rule); phase 2's integration (H's seam)
    step.noise_input = ("%s.%s" % (where.path, base)) if where is not None and where.path else base


def is_vamos_input(name: str) -> bool:
    """Whether a step's noise input is §5.6's vamos_nin<k> (at the top level or on a subckt path)."""
    return name.rsplit(".", 1)[-1].startswith("vamos_nin")


# -- ic, nodeset and state files (§3.10, §8.6) ----------------------------------------------------------

def _check_names(flat: _Flat, names: Sequence[str], what: str, origin: str, notes: List[Note]) -> None:
    for n in names:
        if "*" in n or "?" in n:
            notes.append(Note(ERROR, origin, "%s %s: wildcards are not supported (v1)" % (what, n)))
        elif flat.net(n) is None:
            notes.append(Note(WARNING, origin, "%s %s does not name a net of the circuit; the engines would ignore "
                              "it silently" % (what, n)))


def _state_file(flat: _Flat, step: AnalysisStep, key: str, notes: List[Note]) -> None:
    f = _text(step.args.get(key))
    if not f:
        return
    path = f if os.path.isabs(f) else os.path.join(os.getcwd(), f)
    if not os.path.exists(path):
        notes.append(Note(NOTE, "", "%s %s: state file %s not found; nothing is read" % (step.name, key, f)))
        return
    names: List[str] = []
    currents = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                text = line.split("#", 1)[0].strip()
                if not text:
                    continue
                name = text.split()[0]
                if ":" in name:
                    currents += 1
                else:
                    names.append(name)
    except OSError as exc:
        notes.append(Note(WARNING, "", "%s %s: cannot read %s: %s" % (step.name, key, f, exc)))
        return
    _check_names(flat, names, "%s %s" % (step.name, key), "", notes)
    if currents:
        sev = NOTE if key == "readns" else WARNING
        notes.append(Note(sev, "", "%s %s: %d current entries of %s not applied" % (step.name, key, currents, f)))


# -- resolve --------------------------------------------------------------------------------------------

def resolve(nl: Netlist, plan: RunPlan) -> Tuple[Netlist, SignalMap, List[Note]]:
    """The render copy (a copy.deepcopy of nl with the probes, Netlist.values reduced per engine from
    plan.dependents, §4.1), the SignalMap and the notes (§6)."""
    notes: List[Note] = []
    nl2 = copy.deepcopy(nl)
    flat = _Flat(nl2, plan, notes)
    options = _output_options(nl2, notes)
    allpub_all = flat.allpub(None)                       # before any insertion (§6.1)
    allpub_lvl: Dict[Optional[int], List[_Signal]] = {None: allpub_all}
    explicit = _explicit(flat, nl2.saves)
    has_node = any(s[3] == "v" for s in explicit)
    _check_names(flat, list(nl2.ics), "ic", "", notes)
    _check_names(flat, list(nl2.nodesets), "nodeset", "", notes)
    types = _noise_types(flat, notes)
    sigmap = SignalMap()
    for act in plan.actions:
        step = act.step
        if act.op != "analysis" or step is None:
            continue
        for key in ("readns", "readic", "read"):
            _state_file(flat, step, key, notes)
        mode = (_text(step.args.get("save")) or str(options["save"])).lower()
        if mode not in SAVE_MODES:
            notes.append(Note(WARNING, "", "%s: save=%s is not one of %s; selected is used"
                              % (step.name, mode, ", ".join(SAVE_MODES))))
            mode = "selected"
        nest = _nestlvl(step.args.get("nestlvl", options.get("nestlvl")))
        if step.kind == "noise":
            if step.noise_input and is_vamos_input(step.noise_input):
                _insert_noise_input(flat, step, notes)
            sigs = _noise_signals(flat, step, types)
        elif step.kind == "xf":
            sigs = _xf_signals(flat)
        else:
            sigs = []
            if mode == "nooutput":
                notes.append(Note(NOTE, "", "%s: save=nooutput: no data file is written" % step.name))
            elif mode == "none":
                notes.append(Note(NOTE, "", "%s: save=none: one node voltage is written, the first in sorted order"
                                  % step.name))
                nodes = [s for s in allpub_all if s[3] == "v"]
                sigs = nodes[:1] + [s for s in explicit if s[3] == "i"]
            elif mode == "selected" and has_node:
                sigs = list(explicit)
            else:
                if mode in ("lvlpub", "lvl"):
                    if nest not in allpub_lvl:
                        allpub_lvl[nest] = flat.allpub(nest)
                    base = allpub_lvl[nest]
                else:
                    base = allpub_all
                have = {b[0] for b in base}
                sigs = base + [s for s in explicit if s[0] not in have]
                sigmap.allpub.add(step.id)
        sigmap.per_step[step.id] = [(ref, name, typ) for ref, name, typ, _ in _dedupe(sigs)]
    if plan.engine == "vacask":
        for d in plan.dependents:
            nl2.values.pop(d, None)
    return nl2, sigmap, notes
