"""Reference-supply tracing over the merged deck graph (docs/VAMOS_AMS_DESIGN.md §3.3).

The graph is the user IR plus the generated top-level instances (the xv_ cut
instances), with X instances expanded through their port maps.  A graph node
is (scope, name): scope is the tuple of X-instance names from the top, and
.global nets and ground live in the top scope.

trace() follows VCS's staging (PAMS p108).  Stage 1 is the node's
channel-connected region: MOS and JFET drain-source channels, resistors and
inductors, across subckt boundaries.  If that region holds no hit, the next
stage starts from the source and drain of every MOS whose gate is in the
region.  The walk never enters ground, a skip node, or a hit node.  A hit is
a node held by an ideal V source to ground, or a POWERNET node; the first
stage with hits decides, and its highest hit is vdd.

Reference nodes (SupplyGraph.refs, the ie_reference_voltage nodes deck.py
resolves) are hits too, and they outrank every source of their stage (PAMS
p108: the reference node overrides the ideal supply traced): a stage that
reaches one is decided by its highest reference.  A reference whose value is
not known (no voltage= on a node that is not a constant supply) decides as
"dynamic" (TraceResult.ref_dynamic; deck.py refuses it where a level uses
it), except at the trace's own start node, where it is not a hit (a supply
node's own level never follows itself).

A V source to ground whose value cannot be evaluated (one that depends on
temper, say) is a supply all the same: the walk stops there as at a hit, and
a stage that holds one reports it (TraceResult.unevaluable) whatever else it
holds, because vamos cannot tell which of its hits is the highest; deck.py
turns that into an error naming the source where a level uses the reference.
unevaluable_sources() lists them all for step 4 (the highest deck source).

POWERNET nodes are top-level deck nodes held by an ideal source the AMS layer
adds: supply1/supply0 nets (constant), and D2A nodes with `d2a powernet`
(PAMS Method #2a), which are constant when their digital driver is, else
dynamic at their highest level (§3.3 treats a varying V source the same way).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from vamos.netlist import ir
from vamos.netlist.expr_ast import Expr, Num
from vamos.notes import Note, warning

GROUND = "0"
Node = Tuple[Tuple[str, ...], str]          # (scope, local name)
TOP: Tuple[str, ...] = ()


@dataclass
class Level:
    """The value of a V source: constant, or dynamic with its highest level."""
    kind: str                # 'const' | 'dynamic' | 'unevaluable'
    value: Optional[float]   # the constant, or the highest level of a dynamic wave
    source: str              # instance path, for messages
    origin: str = ""         # the source's netlist origin (file:line), when known
    why: str = ""            # 'unevaluable': what the evaluator could not do


@dataclass
class TraceResult:
    vdd: float
    source: str                                   # instance path of the deciding source (or POWERNET node)
    path: List[str]                               # node names from the start to the hit
    dynamic: bool = False                         # the deciding source has a varying wave
    visited: Set[Node] = field(default_factory=set)
    ref_name: Optional[str] = None                # an ie_reference_voltage node decided (its name as written)
    ref_index: int = -1                           # ... its AmsConfig.ref_voltages index
    ref_dynamic: bool = False                     # ... and its value is not known (vdd is then 0.0)
    unevaluable: List[Level] = field(default_factory=list)  # V sources of the deciding stage with no value


def _fmt_node(n: Node) -> str:
    scope, name = n
    return ".".join(list(scope) + [name])


class SupplyGraph:
    """The merged deck graph for §3.3 traces.

    powernets: top-level node -> constant value of a POWERNET node.
    dynamic_powernets: top-level node -> highest level of a POWERNET node whose
    value varies (a `d2a powernet` with a non-constant digital driver): a hit
    like a varying V source (counted at that level, with a warning when it
    decides a trace), never a constant (dc_of, step 4).
    labels: node -> how messages and the IE report name that POWERNET source
    (default "powernet <node>").
    refs: reference node -> (value or None, name as written, entry index): the
    ie_reference_voltage nodes (module docstring); deck.py sets it.
    """

    def __init__(self, nl: ir.Netlist, extra: Iterable[ir.Instance] = (),
                 powernets: Optional[Dict[str, float]] = None,
                 skip: Iterable[Node] = (),
                 evaluate: Optional[Callable[[Expr, Dict[str, float]], float]] = None,
                 dynamic_powernets: Optional[Dict[str, float]] = None,
                 labels: Optional[Dict[str, str]] = None,
                 refs: Optional[Dict[Node, Tuple[Optional[float], str, int]]] = None):
        if evaluate is None:
            from vamos.netlist.expr import evaluate as _ev   # N1's evaluator
            evaluate = _ev
        self.evaluate = evaluate
        self.nl = nl
        self.globals = {g.lower() for g in nl.globals} | {GROUND}
        self.top_instances = list(nl.instances()) + list(extra)
        self.subckts = nl.subckts()
        self.powernets = dict(powernets or {})
        self.dynamic_powernets = {k: v for k, v in (dynamic_powernets or {}).items()
                                  if k not in self.powernets}
        self.labels = dict(labels or {})
        self.skip = set(skip)
        self.refs: Dict[Node, Tuple[Optional[float], str, int]] = dict(refs or {})
        self.notes: List[Note] = []
        self._scope_cache: Dict[Tuple[str, ...], Tuple[List[ir.Instance], Dict[str, float], Dict[str, Node]]] = {}
        self._adj_cache: Dict[Tuple[str, ...], Dict[str, List[Tuple[str, Node]]]] = {}
        self._hits_cache: Dict[Tuple[str, ...], Dict[str, Level]] = {}
        self._uneval_cache: Dict[Tuple[str, ...], Dict[Node, Level]] = {}
        self._touch: Optional[Dict[Node, Set[Tuple[str, ...]]]] = None
        self._adj_all: Dict[Node, List[Tuple[str, Node]]] = {}
        self._gates_all: Dict[Node, List[Tuple[Node, Node]]] = {}

    # -- scopes ----------------------------------------------------------------

    def _canon(self, scope: Tuple[str, ...], name: str) -> Node:
        n = name.lower()
        if n in self.globals:
            return (TOP, n)
        return (scope, n)

    def _subckt_def(self, scope: Tuple[str, ...], master: str) -> Optional[ir.Subckt]:
        master = master.lower()
        # nested definitions in enclosing subckt bodies win over top-level ones
        for k in range(len(scope), 0, -1):
            parent = self._scope(scope[:k])
            for it in parent[3]:
                if isinstance(it, ir.Subckt) and it.name == master:
                    return it
        return self.subckts.get(master)

    def _scope(self, scope: Tuple[str, ...]):
        """(instances, parameter values, port binding formal -> parent node, body) of a scope."""
        if scope in self._scope_cache:
            return self._scope_cache[scope]
        if not scope:
            res = (self.top_instances, dict(self.nl.values), {}, list(self.nl.body))
            self._scope_cache[scope] = res
            return res
        parent_scope = scope[:-1]
        p_insts, p_vals, _, _ = self._scope(parent_scope)
        xi = next((i for i in p_insts if i.kind == "x" and i.name == scope[-1]), None)
        if xi is None or xi.master is None:
            res = ([], {}, {}, [])
            self._scope_cache[scope] = res
            return res
        sub = self._subckt_def(parent_scope, xi.master)
        if sub is None:
            res = ([], {}, {}, [])
            self._scope_cache[scope] = res
            return res
        vals = dict(p_vals)
        overrides = {k.lower(): v for k, v in xi.params.items()}
        for p in sub.params:
            e = overrides.get(p.name, p.expr)
            ctx = p_vals if p.name in overrides else vals
            try:
                vals[p.name] = self.evaluate(e, ctx)
            except Exception:                 # not constant here: sources using it are unevaluable
                vals.pop(p.name, None)
        binding = {}
        for formal, actual in zip(sub.ports, xi.nodes):
            binding[formal.lower()] = self._canon(parent_scope, actual)
        insts = [i for i in sub.body if isinstance(i, ir.Instance)]
        res = (insts, vals, binding, list(sub.body))
        self._scope_cache[scope] = res
        return res

    def _node(self, scope: Tuple[str, ...], name: str) -> Node:
        """Resolve a local node name: a subckt port is its parent's node."""
        n = self._canon(scope, name)
        while n[0] and n[0] == scope and scope:
            binding = self._scope(scope)[2]
            if n[1] in binding:
                n = binding[n[1]]
                scope = n[0]
            else:
                break
        return n

    # -- elements ----------------------------------------------------------------

    def _adjacency(self, scope: Tuple[str, ...]) -> Dict[Node, List[Tuple[str, Node]]]:
        if scope in self._adj_cache:
            return self._adj_cache[scope]
        adj: Dict[Node, List[Tuple[str, Node]]] = {}

        def link(a: Node, b: Node, how: str) -> None:
            adj.setdefault(a, []).append((how, b))
            adj.setdefault(b, []).append((how, a))

        for inst in self._scope(scope)[0]:
            k = inst.kind
            if k in ("r", "l") and len(inst.nodes) >= 2:
                link(self._node(scope, inst.nodes[0]), self._node(scope, inst.nodes[1]),
                     _fmt_node((scope, inst.name)))
            elif k in ("m", "j") and len(inst.nodes) >= 3:
                link(self._node(scope, inst.nodes[0]), self._node(scope, inst.nodes[2]),
                     _fmt_node((scope, inst.name)))
        self._adj_cache[scope] = adj
        return adj

    def _gates(self, scope: Tuple[str, ...]) -> List[Tuple[Node, Node, Node]]:
        out = []
        for inst in self._scope(scope)[0]:
            if inst.kind == "m" and len(inst.nodes) >= 3:
                out.append((self._node(scope, inst.nodes[1]), self._node(scope, inst.nodes[0]),
                            self._node(scope, inst.nodes[2])))
        return out

    def _children(self, scope: Tuple[str, ...]) -> List[Tuple[str, ...]]:
        return [scope + (i.name,) for i in self._scope(scope)[0]
                if i.kind == "x" and i.master is not None
                and self._subckt_def(scope, i.master) is not None]

    def level(self, inst: ir.Instance, scope: Tuple[str, ...]) -> Level:
        """Value of a V source: constant, dynamic (highest level) or unevaluable."""
        vals = self._scope(scope)[1]
        where = _fmt_node((scope, inst.name))
        src = inst.source or ir.Source(dc=inst.value)

        def ev(e: Optional[Expr]) -> float:
            return 0.0 if e is None else float(self.evaluate(e, vals))
        try:
            if src.code_uri:
                return Level("unevaluable", None, where, inst.origin, "a co-simulation source")
            if not src.wave:
                return Level("const", ev(src.dc), where, inst.origin)
            a = src.args
            if src.wave == "pulse":
                lv = [ev(a.get("v1")), ev(a.get("v2"))]
            elif src.wave == "sin":
                vo, va = ev(a.get("vo")), ev(a.get("va"))
                lv = [vo - abs(va), vo + abs(va)]
            elif src.wave == "exp":
                lv = [ev(a.get("v1")), ev(a.get("v2"))]
            elif src.wave == "pwl":
                lv = [ev(v) for _, v in src.points] or [ev(src.dc)]
            else:
                return Level("unevaluable", None, where, inst.origin, "a %s wave" % src.wave)
        except Exception as exc:
            return Level("unevaluable", None, where, inst.origin, str(exc) or type(exc).__name__)
        if max(lv) == min(lv):
            return Level("const", lv[0], where, inst.origin)
        return Level("dynamic", max(lv), where, inst.origin)

    def _hits(self, scope: Tuple[str, ...]) -> Dict[Node, Level]:
        if scope in self._hits_cache:
            return self._hits_cache[scope]
        hits: Dict[Node, Level] = {}
        uneval: Dict[Node, Level] = {}
        for inst in self._scope(scope)[0]:
            if inst.kind != "v" or len(inst.nodes) < 2:
                continue
            p, n = self._node(scope, inst.nodes[0]), self._node(scope, inst.nodes[1])
            lv = self.level(inst, scope)
            to = hits if lv.kind != "unevaluable" else uneval
            if n == (TOP, GROUND) and p != (TOP, GROUND):
                to[p] = lv
            elif p == (TOP, GROUND) and n != (TOP, GROUND):
                to[n] = Level(lv.kind, -lv.value if lv.value is not None else None, lv.source,
                              lv.origin, lv.why)
        self._hits_cache[scope] = hits
        self._uneval_cache[scope] = uneval
        return hits

    def _uneval(self, scope: Tuple[str, ...]) -> Dict[Node, Level]:
        if scope not in self._uneval_cache:
            self._hits(scope)
        return self._uneval_cache[scope]

    def _all_scopes(self) -> List[Tuple[str, ...]]:
        out, todo = [], [TOP]
        while todo:
            s = todo.pop()
            out.append(s)
            todo.extend(self._children(s))
        return out

    # -- queries -----------------------------------------------------------------

    def node(self, name: str, scope: Tuple[str, ...] = TOP) -> Node:
        return self._node(scope, name)

    def _is_powernet(self, n: Node) -> bool:
        return n[0] == TOP and (n[1] in self.powernets or n[1] in self.dynamic_powernets)

    def hit_at(self, n: Node) -> Optional[Level]:
        if n[0] == TOP and n[1] in self.powernets:
            return Level("const", self.powernets[n[1]], self.labels.get(n[1], "powernet " + n[1]))
        if n[0] == TOP and n[1] in self.dynamic_powernets:
            return Level("dynamic", self.dynamic_powernets[n[1]],
                         self.labels.get(n[1], "powernet " + n[1]))
        for s in self._scopes_touching(n):
            lv = self._hits(s).get(n)
            if lv is not None:
                return lv
        return None

    def unevaluable_at(self, n: Node) -> Optional[Level]:
        """The V source to ground holding n whose value cannot be evaluated, if any."""
        for s in self._scopes_touching(n):
            lv = self._uneval(s).get(n)
            if lv is not None:
                return lv
        return None

    def unevaluable_sources(self) -> List[Level]:
        """Every V source to ground in the deck whose value cannot be evaluated."""
        out: List[Level] = []
        for s in self._all_scopes():
            out.extend(self._uneval(s).values())
        return out

    def touches_source(self, n: Node) -> bool:
        """Whether a source element (V, I, E, F, G, H, B) or a Verilog-A instance has a
        terminal on n, in any scope: a net such an element can hold, which the trace
        (ideal V sources only, as VCS's) may still not reach."""
        self._index()
        for s in self._touch.get(n, ()):
            for inst in self._scope(s)[0]:
                if inst.kind in ("v", "i", "e", "f", "g", "h", "b", "y") and \
                        any(self._node(s, nm) == n for nm in inst.nodes):
                    return True
        return False

    def _index(self) -> None:
        """Build the merged indexes once: which scopes touch a node, channel
        adjacency, and MOS gate -> (drain, source)."""
        if self._touch is not None:
            return
        self._touch = {}
        self._adj_all = {}
        self._gates_all = {}
        for s in self._all_scopes():
            for inst in self._scope(s)[0]:
                for nm in inst.nodes:
                    self._touch.setdefault(self._node(s, nm), set()).add(s)
            for a, lst in self._adjacency(s).items():
                self._adj_all.setdefault(a, []).extend(lst)
            for g, d, src in self._gates(s):
                self._gates_all.setdefault(g, []).append((d, src))

    def _scopes_touching(self, n: Node) -> List[Tuple[str, ...]]:
        self._index()
        return sorted(self._touch.get(n, ()), key=len)

    def constant_value(self, n: Node) -> Tuple[str, Optional[float]]:
        """('const', v) | ('dynamic', None) | ('missing', None) for a net (rules dc_of).
        Ground (a ground alias, or a subckt port bound to it) is the constant 0 V: `vss=0',
        `vss=gnd' and a vss_port= on a grounded port were "not a constant supply"."""
        self._index()
        if n == (TOP, GROUND):
            return ("const", 0.0)
        if n not in self._touch and not self._is_powernet(n):
            return ("missing", None)
        lv = self.hit_at(n)
        if lv is None:
            return ("dynamic", None)
        return ("const", lv.value) if lv.kind == "const" else ("dynamic", None)

    def highest_constant(self) -> Optional[Tuple[float, str]]:
        """Step 4: the highest constant V source anywhere in the deck."""
        best: Optional[Tuple[float, str]] = None
        for s in self._all_scopes():
            for lv in self._hits(s).values():
                if lv.kind == "const" and lv.value is not None and (best is None or lv.value > best[0]):
                    best = (lv.value, lv.source)
        for name, v in self.powernets.items():
            if best is None or v > best[0]:
                best = (v, self.labels.get(name, "powernet " + name))
        return best

    def trace(self, start: Node) -> Optional[TraceResult]:
        """Step 3: the staged channel trace from an IE node (module docstring: reference
        nodes and unevaluable sources stop it too)."""
        self._index()
        adj, gates = self._adj_all, self._gates_all

        visited: Set[Node] = set()
        parent: Dict[Node, Optional[Node]] = {start: None}

        def path_to(n: Node) -> List[str]:
            path, cur = [], n
            while cur is not None:
                path.append(_fmt_node(cur))
                cur = parent.get(cur)
            return list(reversed(path))

        frontier = [start]
        while frontier:
            region: List[Node] = []
            hits: List[Tuple[float, Level, Node]] = []
            refs: List[Tuple[Node, Tuple[Optional[float], str, int]]] = []
            uneval: List[Level] = []
            q = deque()
            for f in frontier:
                if f not in visited:
                    visited.add(f)
                    q.append(f)
            while q:
                n = q.popleft()
                if n == (TOP, GROUND) or n in self.skip:
                    continue
                ref = self.refs.get(n)
                if ref is not None and not (n == start and ref[0] is None):
                    refs.append((n, ref))
                    continue                       # the reference is where the trace ends
                lv = self.hit_at(n)
                if lv is not None and lv.value is not None:
                    hits.append((lv.value, lv, n))
                    continue                       # never walk past a hit
                un = self.unevaluable_at(n)
                if un is not None:
                    uneval.append(un)
                    continue                       # a supply whose value vamos does not know
                region.append(n)
                for _, m in adj.get(n, ()):
                    if m not in visited:
                        visited.add(m)
                        parent.setdefault(m, n)
                        q.append(m)
            if refs:
                unknown = [r for r in refs if r[1][0] is None]
                n, (rv, name, idx) = unknown[0] if unknown else max(refs, key=lambda r: r[1][0])
                return TraceResult(0.0 if rv is None else float(rv), "ie_reference_voltage " + name,
                                   path_to(n), False, visited, ref_name=name, ref_index=idx,
                                   ref_dynamic=rv is None)
            if hits or uneval:
                if not hits:
                    return TraceResult(0.0, uneval[0].source, [], False, visited, unevaluable=uneval)
                v, lv, n = max(hits, key=lambda h: h[0])
                if lv.kind == "dynamic" and not uneval:
                    self.notes.append(warning(lv.source, "supply trace from %s reached a varying "
                                              "source; its highest level %g V is used"
                                              % (_fmt_node(start), v)))
                return TraceResult(v, lv.source, path_to(n), lv.kind == "dynamic", visited,
                                   unevaluable=uneval)
            nxt = []
            for g in region:
                for d, s in gates.get(g, ()):
                    for m in (d, s):
                        if m not in visited:
                            parent.setdefault(m, g)
                            nxt.append(m)
            frontier = nxt
        return None
