"""The co-simulation deck (docs/VAMOS_AMS_DESIGN.md §4.7, §3.3).

build() takes the user's netlist IR and the cut plan and adds, as ordinary
IR: one X instance per cut instance, the bridge sources and gated
conductances, POWERNET/REMOVED sources, shunts, the analysis and the saves.
It resolves every interface element's levels through rules.py, with the
reference supply from the staged trace in supply.py, compiles the Verilog-A
the deck loads, emits the deck for the chosen engine and runs the smoke check.

port_connect (§2.2).  The connection of a SPICE port of a cut instance is,
in order: the last `port_connect -cell c -inst p` statement whose -cell
pattern matches the Verilog cell or its subckt and whose -inst pattern
matches the instance's Verilog path, whatever its place in the file (an
-inst statement beats a cell-level one); else the cell-level connection
(CutCell.connects, where shells.py applied the last statement); else the
cut port bit's analog node.  A later statement that gives one port another
net is a note.  Applying an -inst statement marks port_connect_inst#<i>.
A port that has none of these is an error, which names the -inst statements
that connect it for other instances when there are any (an -inst statement
must cover every instance of a SPICE-only cell).  The net is resolved as:
snps_open; an analog node's name or alias (so <cut instance>.<port> of a
port a Verilog net connects); ground (the alias fold, with or without the
<top>. prefix); a .global net or a net of a top-level element of the
netlist (with or without <top>.).  Anything else is an error naming the
statement: a node inside a SPICE instance (<instance>.<internal node>, PAMS
p258) and a Verilog-only net are not in v1, and a misspelt net never becomes
a new floating node.  `real p => net` is an error (real-number interface
elements are not in v1).  A ground-alias port (§4.3.4) is ground inside the
subckt: connecting it to another net is an error, snps_open a warning.  After
the levels, a port_connect net that no supply trace reaches and that no
source element touches is a warning (the port's supply floats).

ie_reference_voltage (§3.3 step 2).  Each entry whose node resolves in the
deck is marked ref_voltage#<i>; when several name one net the last applies
(PAMS p106) and the earlier ones get a note.  The applied nodes are hits of
every supply trace that outrank the sources of their stage (supply.py), so
an entry sets the levels of every interface element whose trace reaches its
node, whether or not the trace would have found a source.  An entry that no
trace reaches is a warning.  A V source that cannot be evaluated is an error
naming it wherever a level would depend on it (§3.3).
"""

from __future__ import annotations

import copy
import difflib
import os
import re
import shutil
import subprocess
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from vamos.ams import cut, engines, globs, layout, names, rules
from vamos.ams.config import AmsConfig
from vamos.ams.model import (A2D, BIDIR, BRIDGED, D2A, DISABLED, POWERNET, PULL_DOWN, PULL_UP, RA2D,
                             RD2A, REMOVED, STRONG, SUPPLY0, SUPPLY1, WEAK, AmsPlan, AnalogNode,
                             CutCell, CutInstance, D2A_IE, Driver, PortRef, RuleHits)
from vamos.ams.names import NameAllocator
from vamos.ams.supply import TOP, Level, SupplyGraph
from vamos.netlist import ir, spice, tables
from vamos.netlist.expr_ast import Num
from vamos.netlist.numbers import fmt
from vamos.notes import Note, NoteError, error, note, warning

SYNTH_STEP = 1e-11
DEFAULT_STOP = 3600.0
MAX_STOP = 9000.0             # nvc time saturates near 9223 s
DEFAULT_MAXSTEP = 1e-8


@dataclass
class OpenPort:
    """A SPICE port tied off by `port_connect -cell c (p => snps_open)` (§4.7): the
    private node it gets and that node's shunt, both listed in the IE report (§3.6)."""
    vpath: str                                 # the cut instance's Verilog path
    port: str                                  # the SPICE port, in the netlist's spelling
    node: str                                  # the private deck node (nc_...)
    shunt: str                                 # its rsh_ shunt


@dataclass
class DeckResult:
    path: str                                  # the emitted deck
    engine: str
    analysis: str
    stop: float
    stop_synthesized: bool
    osdi: List[str] = field(default_factory=list)
    notes: List[Note] = field(default_factory=list)
    opens: List[OpenPort] = field(default_factory=list)
    start: float = 0.0                         # the .tran output start (TSTART), 0 when none


def seed_names(nl: ir.Netlist) -> List[str]:
    """Every user name at the deck's top level, for the NameAllocator."""
    out = set(nl.globals)
    for it in nl.body:
        if isinstance(it, ir.Instance):
            out.add(it.name)
            out.update(it.nodes)
        elif isinstance(it, (ir.Model, ir.Subckt)):
            out.add(it.name)
    return sorted(out)


def _time(opts: dict, key: str, default: float, origin: str, notes: List[Note]) -> float:
    raw = opts.get(key)
    if raw in (None, True, ""):
        return default
    from vamos.netlist.numbers import parse_number
    try:
        return parse_number(str(raw))
    except ValueError:
        notes.append(error(origin, "--vamos-%s=%s is not a time" % (key.replace("_", "-"), raw)))
        return default


def analog_stop(opts: dict, notes: List[Note]) -> float:
    stop = _time(opts, "analog_stop", DEFAULT_STOP, "--vamos-analog-stop", notes)
    if stop > MAX_STOP:
        notes.append(warning("--vamos-analog-stop", "%g s clamped to %g s (nvc time saturates "
                             "near 9223 s)" % (stop, MAX_STOP)))
        stop = MAX_STOP
    return stop


class _Ctx:
    """Shared state while one deck is composed."""

    def __init__(self, nl: ir.Netlist, plan: AmsPlan, cfg: AmsConfig, engine: str,
                 alloc: NameAllocator, hits: RuleHits, design=None):
        self.nl, self.plan, self.cfg, self.engine, self.alloc, self.hits = nl, plan, cfg, engine, alloc, hits
        self.design = design                   # vhdl.VhdlDesign (design.vhd), for driver values
        self.notes: List[Note] = []
        self.opens: List[OpenPort] = []
        self.ana = plan.analysis
        self.node_of: Dict[PortRef, AnalogNode] = {}
        for n in plan.nodes:
            for r in n.ports:
                self.node_of[r] = n
        self.by_alias: Dict[str, AnalogNode] = {}
        for n in plan.nodes:
            for a in [n.canonical] + list(n.aliases):
                self.by_alias.setdefault(a.lower(), n)
        self.vpath_x: Dict[str, str] = {}      # cut instance vpath (lower) -> xv_ name
        self.top_nodes: Set[str] = set()
        self.case = rules.case_sensitive(cfg)
        self.graph: Optional[SupplyGraph] = None    # the (c) graph of _levels, for later checks
        # port_connect'ed X-instance ports wired to an existing net: (node, statement, inst path, port)
        self.connected: List[Tuple[str, "_Conn", str, str]] = []
        self._once: Set[Tuple[str, str]] = set()

    # -- names --------------------------------------------------------------------

    def resolve_net(self, graph: SupplyGraph, name: str):
        """A control-file net name -> supply-graph node, or None (§3.3 step 1 order)."""
        key = name.strip().lower()
        n = self.by_alias.get(key)
        if n is not None:
            return (TOP, n.name)
        top = self.ana.top.lower()
        local = key[len(top) + 1:] if key.startswith(top + ".") else key
        if "." not in local:
            folded = spice.fold_ground(local)
            if folded in self.top_nodes or folded in self.nl.globals or folded == "0":
                return (TOP, folded)
        for vpath, xname in sorted(self.vpath_x.items(), key=lambda kv: -len(kv[0])):
            if key.startswith(vpath + "."):
                parts = key[len(vpath) + 1:].split(".")
                scope = (xname,) + tuple(parts[:-1])
                gn = graph.node(parts[-1], scope)
                if graph.constant_value(gn)[0] != "missing":
                    return gn
        return None

    def once(self, kind: str, key: str) -> bool:
        """True the first time (kind, key) is asked: for notes that would repeat."""
        if (kind, key) in self._once:
            return False
        self._once.add((kind, key))
        return True


# -- multi-view parameter overrides (§4.7) ----------------------------------------------

_IDENT = re.compile(r"(?<![\w$])[A-Za-z_][A-Za-z0-9_$]*")
_STRING = re.compile(r'"(?:\\.|[^"\\])*"')


def _idents(text: Optional[str]) -> Set[str]:
    """The identifiers of a Verilog expression (whole words; system functions and the
    contents of string literals excluded)."""
    return set(_IDENT.findall(_STRING.sub(" ", text or "")))


def range_params(cell: CutCell) -> Set[str]:
    """The parameters a multi-view cell's port ranges depend on: every parameter named,
    as a whole identifier (case-sensitive, as Verilog compares names), in a port's range
    text, and every parameter the default expression of one of those names (localparam
    W = N * 2 under input [W-1:0] a gives W and N).  An override of one of them only
    reshapes the ports, which every variant binds bit by bit, so it may differ from the
    default; an override of any other parameter would be lost (the subckt takes none)."""
    params = set(cell.params)
    todo: Set[str] = set()
    for cp in cell.ports:
        todo |= _idents(cp.range_text) & params
    seen: Set[str] = set()
    while todo:
        p = todo.pop()
        if p not in seen:
            seen.add(p)
            todo |= (_idents(cell.params.get(p)) & params) - seen
    return seen


# -- port_connect (§2.2; module docstring) ---------------------------------------------------

@dataclass
class _Conn:
    """One port_connect connection as it applies to a cut instance."""
    port: str                      # the SPICE port as written in the statement
    net: str                       # the net as written ('snps_open' for an open port)
    real: bool = False             # `real p => net`
    origin: str = ""               # the statement's control-file origin ('' when unknown)
    cell: str = ""                 # its -cell pattern
    inst: Optional[str] = None     # its -inst pattern; None for a cell-level statement

    def text(self) -> str:
        return "port_connect -cell %s%s (%s%s => %s)" % (
            self.cell, " -inst %s" % self.inst if self.inst else "", "real " if self.real else "",
            self.port, self.net)

    def where(self, inst_vpath: str) -> str:
        return self.origin or inst_vpath


def _cell_names(cell: CutCell) -> List[str]:
    """The names a port_connect -cell pattern is matched against (as shells.py does)."""
    return [n for n in (cell.name, cell.subckt) if n]


def _matches_cell(ctx: _Ctx, pattern: str, cell: CutCell) -> bool:
    return any(globs.match(pattern, n, ctx.case) for n in _cell_names(cell))


def _same_net(a: str, b: str) -> bool:
    return a.lower() == b.lower() or spice.fold_ground(a) == spice.fold_ground(b) == "0"


def _cell_connects(ctx: _Ctx, cell: CutCell) -> Dict[str, _Conn]:
    """The cell-level connections (CutCell.connects, as shells.py applied them) with the
    statement each comes from; a later cell-level statement that gives a port another net
    is noted once per cell and port."""
    stmts: Dict[str, List[_Conn]] = {}
    for pc in ctx.cfg.port_connects:
        if pc.inst is None and _matches_cell(ctx, pc.cell, cell):
            for p, net, real in pc.conns:
                stmts.setdefault(p.lower(), []).append(_Conn(p, net, real, pc.origin, pc.cell, None))
    out: Dict[str, _Conn] = {}
    for k, net in cell.connects.items():
        lo = k.lower()
        seen = stmts.get(lo, [])
        c = seen[-1] if seen and _same_net(seen[-1].net, net) else _Conn(k, net, False, "", cell.name)
        out[lo] = c
        prev = [s for s in seen[:-1] if not _same_net(s.net, c.net)]
        if prev and ctx.once("cell-replace", "%s.%s" % (cell.name, lo)):
            ctx.notes.append(note(c.origin or cell.name, "%s replaces %s for every instance of %s (the "
                                  "later command wins)" % (c.text(), ", ".join(
                                      "%s (%s)" % (s.text(), s.origin) for s in prev), cell.name)))
    return out


def _inst_connects(ctx: _Ctx, cell: CutCell, inst: CutInstance) -> Dict[str, _Conn]:
    """The -inst connections that apply to one instance, the last statement winning per
    port; marks port_connect_inst#<i> for every statement that applies."""
    out: Dict[str, _Conn] = {}
    for i, pc in enumerate(ctx.cfg.port_connects):
        if pc.inst is None or not _matches_cell(ctx, pc.cell, cell) or \
                not globs.match(pc.inst, inst.vpath, ctx.case):
            continue
        ctx.hits.mark("port_connect_inst#%d" % i)
        for p, net, real in pc.conns:
            c = _Conn(p, net, real, pc.origin, pc.cell, pc.inst)
            prev = out.get(p.lower())
            if prev is not None and not _same_net(prev.net, net):
                ctx.notes.append(note(c.origin or inst.vpath, "%s replaces %s for %s (the later "
                                      "command wins)" % (c.text(), "%s (%s)" % (prev.text(), prev.origin),
                                                         inst.vpath)))
            out[p.lower()] = c
    return out


def _other_inst_statements(ctx: _Ctx, cell: CutCell, port: str) -> List[_Conn]:
    """-inst statements for this cell that connect `port` (for the coverage error)."""
    out = []
    for pc in ctx.cfg.port_connects:
        if pc.inst is not None and _matches_cell(ctx, pc.cell, cell):
            for p, net, real in pc.conns:
                if p.lower() == port:
                    out.append(_Conn(p, net, real, pc.origin, pc.cell, pc.inst))
    return out


def _deck_nets(ctx: _Ctx) -> Dict[str, str]:
    """name (lowercase unless set_sim_case sensitive) -> net, for the .global nets and the
    nets of the netlist's top-level elements (no generated node exists yet)."""
    names_ = set(ctx.nl.globals)
    for it in ctx.nl.body:
        if isinstance(it, ir.Instance):
            names_.update(it.nodes)
    names_.discard("0")
    return {(n if ctx.case else n.lower()): n for n in sorted(names_)}


def _connect_net(ctx: _Ctx, c: _Conn, inst: CutInstance, nets: Dict[str, str],
                 cut_paths: Sequence[str]) -> Tuple[Optional[str], str]:
    """(deck node or None for snps_open, how): how is 'open', 'alias', 'ground', 'deck' or
    'error' (the node is then '0'; the error is reported once per statement)."""
    where = c.where(inst.vpath)
    if c.net.lower() == "snps_open":
        return None, "open"

    def fail(message: str) -> Tuple[Optional[str], str]:
        if ctx.once("connect", "%s|%s" % (where, c.text())):
            ctx.notes.append(error(where, message))
        return "0", "error"

    if c.real:
        return fail("%s: real-number interface elements are not supported in v1" % c.text())
    key = c.net.lower()
    an = ctx.by_alias.get(key)
    if an is not None:
        return an.name, "alias"
    top = ctx.ana.top.lower()
    local = c.net[len(top) + 1:] if key.startswith(top + ".") else c.net
    if spice.fold_ground(local) == "0":
        return "0", "ground"
    hit = nets.get(local if ctx.case else local.lower())
    if hit is not None:
        return hit, "deck"
    for vpath in cut_paths:
        if key.startswith(vpath.lower() + "."):
            inner = c.net[len(vpath) + 1:]
            return fail("%s: %s is %s inside SPICE instance %s; in v1 a port_connect net is a .global "
                        "or top-level net of the netlist, ground, or <SPICE instance>.<port> of a port a "
                        "Verilog net connects: make %s a port of its subckt and connect it from Verilog, "
                        "or declare it .global" % (c.text(), c.net, "node " + inner if "." not in inner
                                                   else "a node", vpath, inner))
    cands = sorted(set(nets.values()) | {n.canonical for n in ctx.plan.nodes})
    near = difflib.get_close_matches(local, cands, n=3, cutoff=0.6)
    return fail("%s: no net %s in the deck; a port_connect net is a .global or top-level net of the "
                "netlist, ground, or <SPICE instance>.<port> of a port a Verilog net connects (a "
                "Verilog-only net is not in v1)%s" % (c.text(), c.net, "; nearest: " + ", ".join(near)
                                                      if near else ""))


def _x_instances(ctx: _Ctx) -> List[ir.Instance]:
    """One X instance per cut instance, nodes in the subckt's post-ground-pass port order
    (the port_connect rules: module docstring)."""
    out: List[ir.Instance] = []
    subs = ctx.nl.subckts()
    globals_ = {g.lower() for g in ctx.nl.globals}
    nets = _deck_nets(ctx)
    cut_paths = sorted({i.vpath for i in ctx.ana.instances}, key=len, reverse=True)
    cell_conns: Dict[str, Dict[str, _Conn]] = {}
    nc_count = 0
    for ii, inst in enumerate(ctx.ana.instances):
        cell = ctx.ana.cells[inst.cell]
        sub = subs.get(inst.subckt)
        if sub is None:
            ctx.notes.append(error(inst.vpath, "subckt %s of SPICE instance %s is not in the netlist"
                                   % (inst.subckt, inst.vpath)))
            continue
        exempt = range_params(cell) if cell.view != "spice" else set()
        for p, (dflt, actual) in sorted(cut.param_overrides(cell, inst).items()):
            if p not in exempt:
                ctx.notes.append(error(inst.vpath, "parameter override %s=%s on SPICE instance %s is "
                                       "not passed to subckt %s" % (p, actual, inst.vpath, sub.name)))
        by_spice: Dict[str, Tuple[int, int]] = {}
        for key, sp in inst.spice.items():
            if sp:
                by_spice[sp.lower()] = key
        if cell.name not in cell_conns:
            cell_conns[cell.name] = _cell_connects(ctx, cell)
        connects = dict(cell_conns[cell.name])
        connects.update(_inst_connects(ctx, cell, inst))
        # ports the statements name that this instance's subckt does not have as a port
        orig = {p.lower(): p for p in (sub.orig_ports or sub.ports)}
        gnd = {orig_p.lower() for k, orig_p in enumerate(sub.orig_ports or []) if k in sub.gnd_ports}
        live = {p.lower() for p in sub.ports}
        for spl, c in sorted(connects.items()):
            if spl in gnd:
                if not ctx.once("gnd", "%s|%s|%s" % (c.where(inst.vpath), c.text(), sub.name)):
                    continue
                if c.net.lower() == "snps_open":
                    ctx.notes.append(warning(c.where(inst.vpath), "%s: port %s of subckt %s is a ground "
                                             "alias, which is ground inside the subckt: snps_open cannot "
                                             "leave it open" % (c.text(), c.port, sub.name)))
                elif _connect_net(ctx, c, inst, nets, cut_paths)[1] not in ("ground", "error"):
                    ctx.notes.append(error(c.where(inst.vpath), "%s: port %s of subckt %s is a ground alias, "
                                           "which is ground inside the subckt, so it cannot be connected "
                                           "to %s; connect it to ground or rename the port"
                                           % (c.text(), c.port, sub.name, c.net)))
            elif spl not in live and spl not in orig:
                ctx.notes.append(error(c.where(inst.vpath), "%s: subckt %s of SPICE instance %s has no "
                                       "port %s" % (c.text(), sub.name, inst.vpath, c.port)))
        xname = names.xname(ctx.alloc, inst.vpath)
        inst.xname = xname
        ctx.vpath_x[inst.vpath.lower()] = xname
        nodes: List[str] = []
        for sp in sub.ports:
            spl = sp.lower()
            node: Optional[str] = None
            c = connects.get(spl)
            if c is not None:
                if spl in by_spice and ctx.node_of.get(PortRef(ii, *by_spice[spl])) is not None:
                    # a backstop: the cut (cut._Analyser._passive) treats every port_connect'ed
                    # bit as passive, matching -cell as here, so it never bridges such a bit
                    ctx.notes.append(error(c.where(inst.vpath), "%s: port %s of SPICE instance %s is also "
                                           "connected from Verilog, and the digital cut bridged that bit "
                                           "(an internal disagreement between the cut and the deck; "
                                           "please report it)" % (c.text(), sp, inst.vpath)))
                node, how = _connect_net(ctx, c, inst, nets, cut_paths)
                if how in ("deck", "alias"):
                    ctx.connected.append((node, c, inst.vpath, sp))
            elif spl in by_spice:
                port, off = by_spice[spl]
                an = ctx.node_of.get(PortRef(ii, port, off))
                if an is None:
                    ctx.notes.append(error(inst.vpath, "cut port bit %s of %s has no analog node"
                                           % (sp, inst.vpath)))
                    node = "0"
                else:
                    node = an.name
            else:
                others = _other_inst_statements(ctx, cell, spl)
                if others:
                    ctx.notes.append(error(inst.vpath, "port %s of subckt %s is port_connect'ed only by "
                                           "-inst statements that do not match %s (%s); add a cell-level "
                                           "port_connect -cell %s or an -inst for %s"
                                           % (sp, sub.name, inst.vpath, ", ".join(
                                               "%s at %s" % (o.text(), o.origin) for o in others),
                                              cell.name, inst.vpath)))
                else:
                    ctx.notes.append(error(inst.vpath, "port %s of subckt %s is not connected for SPICE "
                                           "instance %s (no Verilog port maps to it, no port_connect, no "
                                           "snps_open)" % (sp, sub.name, inst.vpath)))
                node = "0"
            if node is None:
                nc_count += 1
                node = names.inst(ctx.alloc, "nc_", "%s_%s" % (xname, spl.replace("<", "_").replace(">", "")))
                shunt = names.inst(ctx.alloc, "rsh_", node)
                out.append(tables.shunt(shunt, node, origin="snps_open " + inst.vpath))
                ctx.opens.append(OpenPort(inst.vpath, ctx.nl.spelling.get(spl, sp), node, shunt))
            if spl in globals_ and node != spl:
                ctx.notes.append(error(inst.vpath, "port %s of subckt %s is the .global net %s but is "
                                       "bound to %s; use port_connect -cell %s (%s => %s)"
                                       % (sp, sub.name, sp, node, inst.cell, sp, sp)))
            inst.port_nodes[spl] = node
            nodes.append(node)
        out.append(ir.Instance(xname, "x", nodes, master=sub.name, origin="cut instance " + inst.vpath))
    return out


_DRIVEN_BY_AMS = (D2A, BIDIR, RD2A, POWERNET, REMOVED)


def _floating_connects(ctx: _Ctx) -> None:
    """The revision-3 §4.7 warning: a port_connect net that no supply trace reaches (no
    ideal source through channels, resistors or inductors) and that no source element
    touches (VCS's trace sees ideal V sources only; an E/G/B or Verilog-A driver still
    holds the net) leaves the port's supply floating.  Nets the AMS layer drives (D2A,
    BIDIR, RD2A, POWERNET and REMOVED nodes) are never reported."""
    g = ctx.graph
    if g is None:
        return
    roles = {n.name: n.role for n in ctx.plan.nodes}
    for node, c, vpath, port in ctx.connected:
        if roles.get(node) in _DRIVEN_BY_AMS or not ctx.once("float", node):
            continue
        gn = g.node(node)
        if g.trace(gn) is None and not g.touches_source(gn):
            ctx.notes.append(warning(c.where(vpath), "%s: no voltage source reaches %s (port %s of %s): "
                                     "the port floats" % (c.text(), node, port, vpath)))


def _supply_polarity(ctx: _Ctx, node: AnalogNode) -> str:
    net = next((n for n in ctx.ana.nets if n.key == node.net), None)
    if net is not None:
        for d in net.drivers:
            if d.strength in (SUPPLY1, SUPPLY0):
                return d.strength
    return SUPPLY1


# -- constant digital drivers (a `d2a powernet` is a constant supply only with one) ------

# tgt-vhdl's logic3d literals that hold a logic level; a powernet source follows the
# level, never the strength (no series R, no gating).
_LEVEL_OF = {"l3d_0": "0", "l3d_l": "0", "l3d_1": "1", "l3d_h": "1"}
_LITERAL = re.compile(r"^\s*(?:(l3d_\w+)|\(\s*others\s*=>\s*(l3d_\w+)\s*\))\s*$", re.I)
# cut.py's origin of a constant port-association driver:
#   "<arch>:<label> (<formal> => <actual>) (<scope vpath>)"
_TIE_ORIGINS = (re.compile(r"\((\S+) => (.*)\) \([^()]*\)\s*$"), re.compile(r"\((\S+) => (.*)\)\s*$"))
_DRIVER_RANK = {SUPPLY1: 3, SUPPLY0: 3, STRONG: 2, WEAK: 1, PULL_UP: 1, PULL_DOWN: 1}


def literal_level(text: str) -> Optional[str]:
    """'0' / '1' for a constant logic3d value (L3D_1, L3D_L, (others => L3D_0)); None
    for X, Z, U, W or anything that is not a lone literal."""
    m = _LITERAL.match(text or "")
    if m is None:
        return None
    return _LEVEL_OF.get((m.group(1) or m.group(2)).lower())


def driver_level(design, drv: Driver) -> Optional[str]:
    """The logic level ('0'/'1') a digital driver holds from t=0 for the whole run, or
    None when vamos cannot prove one.

    Proved from the design.vhd statement behind Driver.stmt (vhdl.Stmt.sid, or
    "<arch>#init:<signal>" for a declaration initial value): a statement whose one
    assignment is a lone constant literal with no delay (`vdd <= L3D_1;`), a constant
    port association (`p => L3D_1`), or an initial value with no other source.  A
    static pull holds its pull level."""
    if drv.strength == PULL_UP:
        return "1"
    if drv.strength == PULL_DOWN:
        return "0"
    if design is None:
        return None
    arch, sep, rest = (drv.stmt or "").partition("#")
    ar = design.archs.get(arch.lower()) if sep else None
    if ar is None:
        return None
    if rest.startswith("init:"):
        sd = ar.signals.get(rest[len("init:"):].lower())
        return literal_level(sd.init) if sd is not None and sd.init else None
    if not rest.isdigit() or int(rest) >= len(ar.stmts):
        return None
    st = ar.stmts[int(rest)]
    if st.kind == "instance":
        for rx in _TIE_ORIGINS:
            m = rx.search(drv.origin or "")
            if m is None:
                continue
            for a in st.assocs:
                if a.const and not a.z_only and a.formal.name == m.group(1) and a.text == m.group(2):
                    return literal_level(a.text)
        return None
    if len(st.assigns) != 1 or not st.simple:
        return None
    asg = st.assigns[0]
    if not asg.const or asg.z_only or asg.op not in ("<=", ":="):
        return None
    text = design.text[st.span[0]:st.span[1]]
    m = re.search(r"\b%s\b[^;]*?(?:<=|:=)([^;]*);" % re.escape(asg.target.name), text, re.I)
    return literal_level(m.group(1)) if m else None


def held_level(design, drivers: Sequence[Driver]) -> Optional[str]:
    """The level a net's digital drivers hold it at for the whole run ('0'/'1'), or None:
    the strongest class decides (supply > strong > weak/pull, §5.4), and every driver
    must be a proved constant (driver_level) and that class must agree."""
    levels: List[Tuple[int, str]] = []
    for d in drivers:
        rank, lv = _DRIVER_RANK.get(d.strength), driver_level(design, d)
        if rank is None or lv is None:
            return None
        levels.append((rank, lv))
    if not levels:
        return None
    top = max(r for r, _ in levels)
    vals = {lv for r, lv in levels if r == top}
    return vals.pop() if len(vals) == 1 else None


def _level_uses_vdd(cfg: AmsConfig, res: "rules.Resolution", key: str) -> bool:
    """Whether the resolved D2A level `key` ('hiv' or 'lov') took the reference supply's
    vdd, so that a 3.3 V fallback warning applies to it (§3.3 step 5).  The defaults are
    hiv = vdd and lov = vss, and the reference vss is always 0 V: a default lov never
    uses the fallback; a rule's value does only as a '%' of a span whose vdd is the
    reference's.  The applied rules merge key by key in file order (rules.py)."""
    if "d2a.vdd" in res.origins:             # a rule's vdd= replaced the reference
        return False
    raw: Optional[str] = None
    for i in res.rules:
        r = cfg.rules[i]
        if r.kind == "d2a" and key in r.params:
            raw = r.params[key]
    if raw is None:
        return key == "hiv"
    try:
        return rules.parse_level(raw)[1]
    except ValueError:
        return False


def _levels(ctx: _Ctx, deck_nl: ir.Netlist, extra: List[ir.Instance]) -> None:
    """Resolve every IE's levels in the §3.3 evaluation order: (b) the supply nodes
    first - supply1/supply0 nets (POWERNET), then `d2a powernet` D2A nodes (PAMS
    Method #2a), which may see the supply nets - and then (c) every other node,
    whose traces see both as hits.  A d2a powernet whose digital driver holds a
    constant level is a constant supply at that level; any other one is a varying
    supply counted at its highest level (with a warning when it decides a trace)."""
    cfg, hits = ctx.cfg, ctx.hits
    refs, skips = rules.ref_nodes(cfg)
    ctx.top_nodes = {nd for it in deck_nl.body if isinstance(it, ir.Instance) for nd in it.nodes}
    ctx.top_nodes |= {nd for it in extra for nd in it.nodes}
    labels: Dict[str, str] = {}
    skip_nodes: List[Set] = []                  # resolved once (errors reported once)
    ref_entries: Dict[object, Tuple[str, Optional[float], int]] = OrderedDict()   # graph node -> last entry
    ref_used: Set[int] = set()
    graphs: List[SupplyGraph] = []
    # levels that depend on what vamos cannot know: (kind, key) -> IE names, one error each
    problems: Dict[Tuple[str, object], List[str]] = OrderedDict()
    uneval_of: Dict[object, List[Level]] = {}

    def ref_origin(idx: int) -> str:
        return cfg.ref_voltages[idx][2] if 0 <= idx < len(cfg.ref_voltages) else "ie_reference_voltage"

    def resolve_once(g: SupplyGraph) -> None:
        found = set()
        for s in skips:
            gn = ctx.resolve_net(g, s)
            if gn is None:
                ctx.notes.append(error("ie_reference_voltage", "skip_node %s is not a deck node" % s))
            else:
                found.add(gn)
        skip_nodes.append(found)
        # every entry whose node exists is found (never TNF); the last one per net applies
        for rnode, volts, idx in refs:
            gn = ctx.resolve_net(g, rnode)
            if gn is None:
                continue                       # TNF at step 13 (rules.unmatched)
            hits.mark("ref_voltage#%d" % idx)
            prev = ref_entries.pop(gn, None)
            if prev is not None:
                ctx.notes.append(note(ref_origin(prev[2]), "ie_reference_voltage node=%s is replaced by "
                                      "node=%s at %s (the same net; the last command is used)"
                                      % (prev[0], rnode, ref_origin(idx))))
            ref_entries[gn] = (rnode, volts, idx)

    def make_graph(powernets: Dict[str, float],
                   dynamic: Optional[Dict[str, float]] = None) -> SupplyGraph:
        g = SupplyGraph(deck_nl, extra, powernets=powernets, dynamic_powernets=dynamic,
                        labels=labels)
        if not skip_nodes:
            resolve_once(g)
        g.skip = set(skip_nodes[0])
        for gn, (rnode, volts, idx) in ref_entries.items():
            v = volts
            if v is None:
                kind, cv = g.constant_value(gn)
                v = cv if kind == "const" else None
            g.refs[gn] = (None if v is None else float(v), rnode, idx)
        graphs.append(g)
        return g

    def vref_for(g: SupplyGraph, node: AnalogNode
                 ) -> Tuple[Tuple[float, float, Optional[Note]], str, Optional[Tuple[str, object]]]:
        """(vref, how the IE report names it, the problem a level using it has, if any)."""
        tr = g.trace((TOP, node.name))
        if tr is not None and tr.ref_name is not None:
            ref_used.add(tr.ref_index)
            src = "ie_reference_voltage %s" % tr.ref_name
            if tr.ref_dynamic:
                return (0.0, 3.3, None), src, ("dynref", tr.ref_index)
            return (0.0, tr.vdd, None), src, None
        if tr is not None:
            src = "trace %s via %s" % (tr.source, " ".join(tr.path))
            if tr.unevaluable:
                key = tuple(sorted({lv.source for lv in tr.unevaluable}))
                uneval_of[key] = tr.unevaluable
                return (0.0, tr.vdd, None), src, ("uneval", key)
            return (0.0, tr.vdd, None), src, None
        hv = g.highest_constant()
        unev = g.unevaluable_sources()
        if unev:
            key = tuple(sorted({lv.source for lv in unev}))
            uneval_of[key] = unev
            return ((0.0, hv[0] if hv else 3.3, None), "highest deck source %s" % (hv[1] if hv else "?"),
                    ("uneval-any", key))
        if hv is not None:
            return (0.0, hv[0], None), "highest deck source %s" % hv[1], None
        w = warning(node.canonical, "no supply reached from %s: using the VCS 3.3 V fallback"
                    % node.canonical)
        return (0.0, 3.3, w), "3.3 V fallback", None

    def report_problems() -> None:
        for (kind, key), who in problems.items():
            ies = ", ".join(who[:6]) + (" and %d more" % (len(who) - 6) if len(who) > 6 else "")
            if kind == "dynref":
                rnode, _volts, _origin = cfg.ref_voltages[key]  # type: ignore[index]
                ctx.notes.append(error(ref_origin(key), "ie_reference_voltage node=%s has no voltage= and "  # type: ignore[arg-type]
                                       "%s is not a constant supply, so the levels of %s would follow "
                                       "it (dynamic references are not in v1); give voltage="
                                       % (rnode, rnode, ies)))
                continue
            srcs = uneval_of.get(key, [])
            what = "; ".join("V source %s%s cannot be evaluated (%s)"
                             % (lv.source, " (%s)" % lv.origin if lv.origin and k else "",
                                lv.why or "unknown") for k, lv in enumerate(srcs))
            if kind == "uneval":
                msg = ("the supply trace of %s reaches it, so their levels cannot be set" % ies)
            else:
                msg = ("no supply trace of %s finds a source, and the highest deck source (the next "
                       "step, §3.3) cannot be chosen" % ies)
            ctx.notes.append(error(srcs[0].origin or srcs[0].source if srcs else "supply",
                                   "%s: %s; give those interface elements their levels (a2d/d2a rules "
                                   "with hiv=, loth=/hith= or vdd=), or name their supply with "
                                   "ie_reference_voltage node=<node> voltage=<v>" % (what, msg)))

    def dc_of_for(g: SupplyGraph):
        def dc_of(name: str):
            gn = ctx.resolve_net(g, name)
            if gn is None:
                return ("missing", None)
            st = g.constant_value(gn)
            if st[0] != "const":
                # a net held by a V source that cannot be evaluated: say so, not "dynamic"
                lv = g.unevaluable_at(gn)
                if lv is not None:
                    return ("unevaluable", "V source %s%s, %s" % (
                        lv.source, " (%s)" % lv.origin if lv.origin else "", lv.why or "unknown"))
            return st
        return dc_of

    def names_of(node: AnalogNode) -> List[str]:
        return [node.canonical] + [a for a in node.aliases if a != node.canonical]

    def ports_of(node: AnalogNode) -> List[Tuple[str, str, str, str, str]]:
        return [cut.port5(ctx.ana, r) for r in node.ports]

    # the SPICE ports of each SPICE instance's subckt, by instance path: a wildcard
    # vdd_port=/vss_port= is matched against them (rules._supply_name, PAMS p194)
    subs = ctx.nl.subckts()
    inst_ports: Dict[str, List[str]] = {}
    for ci in ctx.ana.instances:
        sub = subs.get(ci.subckt)
        if sub is not None:
            inst_ports[ci.vpath] = list(sub.ports)

    def subckt_ports(vpath: str) -> List[str]:
        return inst_ports.get(vpath, [])

    def resolve(g: SupplyGraph, node: AnalogNode, uses: Optional[Tuple[str, ...]] = None) -> None:
        """uses: the D2A levels that reach the deck when that is fewer than the role's
        (a supply1 net uses hiv, a supply0 net lov); the 3.3 V fallback warning and the
        "levels: reference" line then appear only if one of them used the reference."""
        vref, src, problem = vref_for(g, node)
        res = rules.resolve_detail(cfg, names_of(node), ports_of(node), (vref[0], vref[1], None),
                                   dc_of_for(g), hits, role=node.role, subckt_ports=subckt_ports)
        used = res.used_ref if uses is None else any(_level_uses_vdd(cfg, res, k) for k in uses)
        ctx.notes.extend(res.notes)
        if used and vref[2] is not None:
            ctx.notes.append(vref[2])
        if used and problem is not None:
            problems.setdefault(problem, []).append(node.canonical)
        node.d2a, node.a2d = res.d2a, res.a2d
        if res.origins:
            node.report.append("levels: rule %s" % ", ".join(
                "%s %s" % (k, v) for k, v in sorted(res.origins.items())))
        if used or (uses is None and not res.origins):
            node.report.append("levels: reference %s" % src)
        elif not res.origins:
            node.report.append("levels: default %s (the reference supply is not used)"
                               % " ".join("%s=%s" % (k, fmt(getattr(res.d2a, k))) for k in uses))
        if res.aliases:
            node.report.append("User Specified Aliases %s" % " ".join(res.aliases))

    def is_powernet(node: AnalogNode) -> bool:
        """Whether the node's merged d2a rules carry `powernet`.  A dry run: the flag does
        not depend on any supply, so every supply name counts as constant; its notes and
        rule marks are discarded (the real resolution below makes them)."""
        res = rules.resolve_detail(cfg, names_of(node), ports_of(node), (0.0, 0.0, None),
                                   lambda _name: ("const", 0.0), RuleHits(), role=node.role,
                                   subckt_ports=subckt_ports)
        return res.d2a.powernet

    done: Set[int] = set()
    power: Dict[str, float] = {}
    # (b1) supply nets: a supply1 net sits at its hiv, a supply0 net at its lov
    g0 = make_graph({})
    for node in ctx.plan.nodes:
        if node.role == POWERNET:
            pol = _supply_polarity(ctx, node)
            resolve(g0, node, uses=("hiv",) if pol == SUPPLY1 else ("lov",))
            ie = node.d2a or D2A_IE(3.3, 0.0)
            node.dc = ie.hiv if pol == SUPPLY1 else ie.lov
            power[node.name] = node.dc
            done.add(id(node))
    # (b2) `d2a powernet` nodes: ideal sources following their digital value
    pnodes = [n for n in ctx.plan.nodes if n.role == D2A and is_powernet(n)]
    dynamic: Dict[str, float] = {}
    if pnodes:
        g_sup = make_graph(dict(power)) if power else g0
        for node in pnodes:
            level = _held_level(ctx, node)
            resolve(g_sup, node, uses={"1": ("hiv",), "0": ("lov",)}.get(level, ("hiv", "lov")))
            done.add(id(node))
            ie = node.d2a
            if ie is None or not ie.powernet:      # cannot happen when the compile succeeds
                continue
            node.shunt = False
            labels[node.name] = "d2a powernet " + node.name
            if level is not None:
                power[node.name] = ie.hiv if level == "1" else ie.lov
                node.report.append("supply: constant %s V (the digital side holds %s)"
                                   % (fmt(power[node.name]), level))
            else:
                dynamic[node.name] = max(ie.hiv, ie.lov)
                node.report.append("supply: follows a digital value that is not a proved "
                                   "constant; traces count its highest level %s V"
                                   % fmt(dynamic[node.name]))
    # (c) every other node
    g1 = make_graph(power, dynamic)
    for node in ctx.plan.nodes:
        if id(node) in done:
            continue
        if node.role in BRIDGED or node.role in (DISABLED, REMOVED):
            resolve(g1, node)
            if node.role == D2A and node.d2a is not None and node.d2a.powernet:
                node.shunt = False
    ctx.graph = g1
    report_problems()
    for gn, (rnode, _volts, idx) in ref_entries.items():
        if idx not in ref_used:
            ctx.notes.append(warning(ref_origin(idx), "ie_reference_voltage node=%s: no interface "
                                     "element's supply trace reaches %s, so the command sets no levels "
                                     "(a trace stops at the first stage that holds a supply; the IE "
                                     "report's \"levels:\" lines show where each one stopped)"
                                     % (rnode, rnode)))
    seen: Set[int] = set()
    for g in graphs:
        if id(g) not in seen:
            seen.add(id(g))
            ctx.notes.extend(g.notes)


def _held_level(ctx: _Ctx, node: AnalogNode) -> Optional[str]:
    """The level the digital side holds a D2A node at for the whole run, or None."""
    net = next((n for n in ctx.ana.nets if n.key == node.net), None)
    if net is None or not net.drivers:
        return None
    return held_level(ctx.design, net.drivers)


def _bridge_elements(ctx: _Ctx) -> List[ir.Instance]:
    out: List[ir.Instance] = []
    by_host = {n.host: n for n in ctx.plan.nodes if n.host is not None and n.role in BRIDGED}
    for b in ctx.plan.bridges:
        n = by_host[b.host]
        direction = "a2d" if b.kind == "a2d" else "d2a"
        uri = tables.code_uri(ctx.engine, direction, b.name)
        prefix = {"d2a": "vd_", "en": "ve_", "a2d": "ia_"}[b.kind]
        kind = "i" if b.kind == "a2d" else "v"
        out.append(tables.code_source(names.inst(ctx.alloc, prefix, n.name), kind, b.node, "0", uri,
                                      origin="bridge " + b.name))
    for n in ctx.plan.nodes:
        gated = (n.role == BIDIR) or (n.role == D2A and not (n.d2a and n.d2a.powernet))
        if gated:
            rr = n.d2a.r_series if n.d2a else tables.GCOND_RR
            out.append(tables.gcond(names.inst(ctx.alloc, "g_", n.name), n.name, n.name + "_d",
                                    n.name + "_e", rr, origin="d2a " + n.canonical))
        if n.role in (POWERNET, REMOVED) and n.dc is not None:
            out.append(tables.dc_source(names.inst(ctx.alloc, "vs_", n.name), n.name, "0", n.dc,
                                        origin=n.role.lower() + " " + n.canonical))
        if n.shunt:
            out.append(tables.shunt(names.inst(ctx.alloc, "rsh_", n.name), n.name,
                                    origin="shunt " + n.canonical))
    return out


def _analysis(ctx: _Ctx, dk: ir.Netlist, opts: dict) -> Tuple[float, bool]:
    """The deck's one analysis (§4.7).  --vamos-analog-stop's own notes (its clamp
    warning) are printed by flow.py at step 5, which calls analog_stop() first; here only
    an error of it is kept, for a caller that skipped that step."""
    trans = [a for a in dk.analyses if a.kind == "tran"]
    for a in dk.analyses:
        if a.kind != "tran" or (trans and a is not trans[0]):
            ctx.notes.append(warning(a.origin, ".%s ignored in co-simulation (only the first .tran runs)"
                                     % a.kind))
    uic = bool(ctx.cfg.choose and ctx.cfg.choose.uic)
    if trans:
        tran = trans[0]
        if uic:
            tran.args["uic"] = True
        if opts.get("analog_maxstep"):
            old = tran.args.get("maxstep")
            tran.args["maxstep"] = _time(opts, "analog_maxstep", DEFAULT_MAXSTEP,
                                         "--vamos-analog-maxstep", ctx.notes)
            ctx.notes.append(note(tran.origin, ".tran: maximum time step %g s, from "
                                  "--vamos-analog-maxstep%s" % (tran.args["maxstep"], " (in place of %g s)"
                                                                % float(old) if old else "")))
        dk.analyses = [tran]
        return float(tran.args["stop"]), False
    stop_notes: List[Note] = []
    stop = analog_stop(opts, stop_notes)
    ctx.notes.extend(n for n in stop_notes if n.severity == "error")
    # the maximum time step: --vamos-analog-maxstep (explicit), else the netlist's .option
    # delmax (spice.py keeps it for this analysis: it was ignored, with a warning), else
    # DEFAULT_MAXSTEP
    delmax = dk.options.get("delmax")
    if opts.get("analog_maxstep") or delmax is None:
        maxstep = _time(opts, "analog_maxstep", DEFAULT_MAXSTEP, "--vamos-analog-maxstep",
                        ctx.notes)
    else:
        maxstep = float(delmax.value)
        ctx.notes.append(note("", "no .tran: maximum time step %g s, from .option delmax" % maxstep))
    dk.analyses = [ir.Analysis("tran", {"step": SYNTH_STEP, "stop": stop, "start": 0.0,
                                        "maxstep": maxstep, "uic": uic}, origin="vamos")]
    ctx.notes.append(note("", "no .tran: the run ends at $finish/$stop or at %g s" % stop))
    return stop, True


def _find_subckt(name: str, bodies: Sequence[Sequence[ir.Item]], top: Dict[str, ir.Subckt]
                 ) -> Optional[ir.Subckt]:
    """A subckt by name: nested definitions in the enclosing bodies (innermost first), then
    the top-level ones."""
    for body in reversed(bodies):
        for it in body:
            if isinstance(it, ir.Subckt) and it.name == name:
                return it
    return top.get(name)


def _walk_nodes(dk: ir.Netlist):
    """Yield (path, node, scope) for every node name of the deck: path is the name as a
    netlist writes it (a top-level net 'n', or 'x1.x2.n' inside X instances, a subckt's
    ports included), node the name the engines know that node by (ground '0', a .global
    net, a top-level net, or the path of the scope that owns it: a port of a subckt
    instance names its parent's node, which VACASK insists on), scope the X-instance path
    (() at the top level)."""
    top = dk.subckts()
    globals_ = set(dk.globals)
    seen: Set[str] = set()

    def canon(n: str, binding: Dict[str, str], prefix: Tuple[str, ...]) -> str:
        if spice.fold_ground(n) == "0":
            return "0"
        if n in binding:
            return binding[n]
        if n in globals_ or not prefix:
            return n
        return ".".join(prefix + (n,))

    def walk(items: Sequence[ir.Item], bodies: List[Sequence[ir.Item]], prefix: Tuple[str, ...],
             binding: Dict[str, str], depth: int):
        for it in items:
            if not isinstance(it, ir.Instance):
                continue
            if not prefix:
                for n in it.nodes:
                    if n not in seen:
                        seen.add(n)
                        yield n, canon(n, binding, prefix), prefix
            if it.kind != "x" or not it.master or depth > 64:
                continue
            sub = _find_subckt(it.master, bodies, top)
            if sub is None:
                continue
            here = prefix + (it.name,)
            inner = {p: canon(a, binding, prefix) for p, a in zip(sub.ports, it.nodes)}
            for k in sub.gnd_ports:
                if k < len(sub.orig_ports):
                    inner[sub.orig_ports[k]] = "0"
            names_ = list(sub.orig_ports or sub.ports)
            for x in sub.body:
                if isinstance(x, ir.Instance):
                    names_.extend(x.nodes)
            done: Set[str] = set()
            for n in names_:
                if n not in done:
                    done.add(n)
                    yield ".".join(here + (n,)), canon(n, inner, here), here
            yield from walk(sub.body, bodies + [sub.body], here, inner, depth + 1)

    for g in dk.globals:
        if g not in seen:
            seen.add(g)
            yield g, g, ()
    yield from walk(dk.body, [dk.body], (), {}, 0)


def _vcs_names(ctx: _Ctx, dk: ir.Netlist) -> List[Tuple[str, str]]:
    """(name as VCS/XA would print it, deck node) for every node of the deck: a top-level
    net under its own name, an analog node under its canonical name and aliases, and a
    node inside a cut instance under <Verilog path>.<subckt path>.<node> (a port names the
    node it is bound to)."""
    out: List[Tuple[str, str]] = []
    by_x = {i.xname: i for i in ctx.ana.instances if i.xname}
    for n in ctx.plan.nodes:
        for a in [n.canonical] + list(n.aliases):
            out.append((a, n.name))
    for path, node, scope in _walk_nodes(dk):
        if node == "0":
            continue
        ci = by_x.get(scope[0]) if scope else None
        out.append((ci.vpath + path[len(scope[0]):] if ci is not None else path, node))
    return out


def _saves(ctx: _Ctx, dk: ir.Netlist) -> None:
    """The deck's saves (§4.7): the netlist's .print/.probe tran probes, every v() target
    checked against the deck (an error names a node that is not there); the XA cfg's
    probe_waveform_voltage/_current patterns, matched as VCS globs against the deck's
    nodes (elements) under their VCS names (§3.1: <Verilog path>.<subckt path>.<node> inside
    a cut instance), a pattern that matches nothing being a warning; with no probe at all,
    every node voltage; and every bridged node."""
    deck_nodes: Dict[str, str] = {}
    user: List[Tuple[str, str, str]] = []
    for analysis, kind, target in dk.probes:
        if analysis != "tran":
            continue
        if kind != "v" or target == "*":
            user.append((analysis, kind, target))
            continue
        if not deck_nodes:                           # walked once, and only when there is a v()
            deck_nodes = {p: n for p, n, _ in _walk_nodes(dk)}
            deck_nodes["0"] = "0"
        parts = []
        for t in (x.strip() for x in target.split(",")):
            if spice.fold_ground(t.rsplit(".", 1)[-1]) == "0":
                parts.append("0")
            elif t in deck_nodes:
                parts.append(deck_nodes[t])          # a port alias: the node its scope owns
            else:
                ctx.notes.append(error(".print/.probe", "v(%s): the deck has no node %s (a top-level or "
                                       ".global net, or <X instance>.<node>)" % (target, t)))
                parts.append(t)
        user.append((analysis, kind, ",".join(parts)))
    xa_v = list(ctx.cfg.xa.get("probe_v") or [])          # type: ignore[arg-type]
    xa_i = list(ctx.cfg.xa.get("probe_i") or [])          # type: ignore[arg-type]
    if xa_v or xa_i:
        user += _xa_saves(ctx, dk, xa_v, xa_i)
    if not user:
        user.append(("tran", "v", "*"))
    have = {(k, t) for _, k, t in user}
    for n in ctx.plan.nodes:
        if n.role in BRIDGED and ("v", n.name) not in have and ("v", "*") not in have:
            user.append(("tran", "v", n.name))
    dk.probes = user


_SAVABLE_CURRENT = ("v", "l", "e", "h")


def _xa_saves(ctx: _Ctx, dk: ir.Netlist, xa_v: List[Tuple[str, str]],
              xa_i: List[Tuple[str, str]]) -> List[Tuple[str, str, str]]:
    out: List[Tuple[str, str, str]] = []
    if xa_v:
        names_ = _vcs_names(ctx, dk)
        every = {d for _, d in names_}
        for pat, origin in xa_v:
            got: List[str] = []
            for vname, d in names_:
                if d not in got and globs.match(pat, vname, ctx.case):
                    got.append(d)
            if not got:
                ctx.notes.append(warning(origin, "probe_waveform_voltage %s matches no node of the deck, "
                                         "so it saves nothing (names are VCS paths: <Verilog instance "
                                         "path>.<node> inside a SPICE instance, a netlist net by its own "
                                         "name)" % pat))
            elif set(got) >= every:
                out.append(("tran", "v", "*"))
            else:
                out += [("tran", "v", d) for d in got]
    if xa_i:
        elems = _vcs_elements(ctx, dk)
        for pat, origin in xa_i:
            got = [(v, d, k) for v, d, k in elems if globs.match(pat, v, ctx.case)]
            if not got:
                ctx.notes.append(warning(origin, "probe_waveform_current %s matches no element of the "
                                         "deck, so it saves nothing" % pat))
                continue
            bad = [v for v, _d, k in got if k not in _SAVABLE_CURRENT]
            if bad:
                ctx.notes.append(warning(origin, "probe_waveform_current %s: the current of %s is not "
                                         "saved (only V, L, E and H elements have a branch current both "
                                         "engines save)" % (pat, ", ".join(bad[:6]) + (" and %d more" % (
                                             len(bad) - 6) if len(bad) > 6 else ""))))
            out += [("tran", "i", d) for _v, d, k in got if k in _SAVABLE_CURRENT]
    seen: Set[Tuple[str, str, str]] = set()
    return [p for p in out if not (p in seen or seen.add(p))]


def _vcs_elements(ctx: _Ctx, dk: ir.Netlist) -> List[Tuple[str, str, str]]:
    """(name as VCS/XA would print it, deck path, kind) of every user element of the deck
    (not the ones the AMS layer adds)."""
    by_x = {i.xname: i for i in ctx.ana.instances if i.xname}
    user = {it.name for it in ctx.nl.body if isinstance(it, ir.Instance)}    # the generated ones
    top = dk.subckts()                                                       # never share a name
    out: List[Tuple[str, str, str]] = []

    def walk(items, bodies, prefix: Tuple[str, ...], vprefix: str, depth: int) -> None:
        for it in items:
            if not isinstance(it, ir.Instance) or (not prefix and it.name not in user
                                                   and it.name not in by_x):
                continue
            path = ".".join(prefix + (it.name,))
            if not prefix and it.kind == "x" and it.name in by_x:
                vname = by_x[it.name].vpath
            else:
                vname = (vprefix + "." if vprefix else "") + it.name
            if it.kind != "x":
                out.append((vname, path, it.kind))
                continue
            sub = _find_subckt(it.master or "", bodies, top) if depth < 64 else None
            if sub is not None:
                walk(sub.body, bodies + [sub.body], prefix + (it.name,), vname, depth + 1)

    walk(dk.body, [dk.body], (), "", 0)
    return out


def _compile_va(ctx: _Ctx, dk: ir.Netlist, daidir: str) -> List[str]:
    """Copy and compile the Verilog-A the VACASK deck loads (vamos_ie + user .hdl)."""
    if ctx.engine != "vacask":
        return []
    ov = engines.openvaf()
    if not ov:
        ctx.notes.append(error("openvaf-r", "cannot find openvaf-r (set VAMOS_OPENVAF)"))
        return []
    vdir = layout.va_dir(daidir)
    os.makedirs(vdir, exist_ok=True)
    srcs = [tables.VA_SOURCE] + list(dk.hdl)
    out = []
    for k, src in enumerate(srcs):
        stem = os.path.splitext(os.path.basename(src))[0]
        va = os.path.join(vdir, "%d_%s.va" % (k, stem))
        osdi = os.path.join(vdir, "%d_%s.osdi" % (k, stem))
        shutil.copyfile(src, va)
        try:
            r = subprocess.run([ov, va, "-o", osdi], cwd=os.path.dirname(src), stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, universal_newlines=True, errors="replace")
        except OSError as exc:
            via = " (VAMOS_OPENVAF)" if os.environ.get("VAMOS_OPENVAF") == ov else ""
            ctx.notes.append(error("openvaf-r", "cannot run openvaf-r %s%s: %s"
                                   % (ov, via, exc.strerror or exc)))
            return []
        if r.returncode != 0 or not os.path.isfile(osdi):
            ctx.notes.append(error(src, "openvaf-r failed:\n" + r.stdout.strip()))
            continue
        out.append(osdi)
    return out


def build(nl: ir.Netlist, plan: AmsPlan, cfg: AmsConfig, engine: str, alloc: NameAllocator,
          hits: RuleHits, daidir: str, opts: dict, design=None) -> DeckResult:
    """Compose, emit and smoke-check the deck; raises NoteError on any error.

    design: the parsed design.vhd (vhdl.VhdlDesign) the cut analysis read; it proves
    the constant level a `d2a powernet` node is held at (§3.3).  Without it every
    d2a powernet counts as a varying supply."""
    ctx = _Ctx(nl, plan, cfg, engine, alloc, hits, design)
    dk = copy.deepcopy(nl)
    extra = _x_instances(ctx)
    if any(n.severity == "error" for n in ctx.notes):
        raise NoteError(ctx.notes)
    _levels(ctx, dk, [i for i in extra if i.kind == "x"])
    _floating_connects(ctx)
    if any(n.severity == "error" for n in ctx.notes):
        raise NoteError(ctx.notes)
    plan.bridges = names.build_bridges(plan)
    dk.body = list(dk.body) + extra + _bridge_elements(ctx)
    stop, synth = _analysis(ctx, dk, opts)
    _saves(ctx, dk)
    osdi = _compile_va(ctx, dk, daidir)
    if any(n.severity == "error" for n in ctx.notes):
        raise NoteError(ctx.notes)
    path = layout.deck(daidir, engine)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # The decks load the compiled Verilog-A relative to their own directory (VACASK resolves
    # a relative `load` against the directory of the file holding it, standalone and through
    # its C interface alike): a copied or moved daidir then loads its own .osdi files, never
    # the original's.  Both decks (vamos.sim and smoke.sim) are in layout.deck_dir.
    osdi_rel = [os.path.relpath(p, os.path.dirname(path)) for p in osdi]
    emit_notes: List[Note] = []
    if engine == "vacask":
        from vamos.netlist import vacask
        vacask.emit(dk, path, layout.ANALYSIS, osdi=osdi_rel, notes=emit_notes)
    else:
        from vamos.netlist import xyce
        xyce.emit(dk, path, notes=emit_notes)
    ctx.notes.extend(emit_notes)
    if not opts.get("no_deck_check"):
        nvc_lib = tables.nvc_libdir()
        if engine == "vacask":
            from vamos.netlist import vacask
            ctx.notes.extend(vacask.smoke(dk, layout.deck_dir(daidir), osdi=osdi_rel,
                                          nvc_libdir=nvc_lib))
        else:
            from vamos.netlist import xyce
            ctx.notes.extend(xyce.smoke(dk, layout.deck_dir(daidir), nvc_libdir=nvc_lib))
    tran = dk.tran()
    start = float(tran.args.get("start") or 0.0) if tran is not None else 0.0
    res = DeckResult(path, engine, layout.ANALYSIS, stop, synth, osdi, ctx.notes, list(ctx.opens),
                     start)
    if any(n.severity == "error" for n in ctx.notes):
        raise NoteError(ctx.notes)
    return res


def code_sources(path: str) -> List[Tuple[str, str, str]]:
    """(direction, bridge name, positive node) of every code: URI source in an emitted
    deck, either engine: `<inst> (<p> <n>) ... file="code:..."` (VACASK) or
    `<inst> <p> <n> PWL FILE "code:..."` (Xyce)."""
    out = []
    marker = ":%s:" % tables.BRIDGE_LIB
    with open(path, errors="replace") as fh:
        for line in fh:
            i = line.find("code:")
            if i < 0:
                continue
            uri = line[i:].split('"')[0].strip()
            j = uri.find(marker)
            if j < 0:
                continue
            rest = uri[j + len(marker):]                 # <init>:<dir>:<name>
            parts = rest.split(":", 2)
            if len(parts) != 3:
                continue
            head = line[:i].replace("(", " ").replace(")", " ").split()
            node = head[1].strip("'\"") if len(head) > 1 else ""
            out.append(("A2D" if parts[1] == "a2d" else "D2A", parts[2], node))
    return out


def deck_uris(path: str) -> List[Tuple[str, str]]:
    """(direction, name) of every code: URI in an emitted deck (for the agreement check)."""
    return [(d, n) for d, n, _ in code_sources(path)]


def enable_nodes(path: str) -> List[str]:
    """The deck nodes of the gated D2A enables (`<n>_e`): only their `ve_` bridge source
    has a terminal there (the gated element reads the node in an expression), which Xyce
    reports as "connected to only 1 device Terminal" (backends/cosim.py drops that)."""
    return [node for d, name, node in code_sources(path)
            if d == "D2A" and name.endswith(tables.BRIDGE_SUFFIX_EN) and node]
