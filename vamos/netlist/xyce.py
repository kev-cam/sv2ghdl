"""Xyce deck emitter (docs/VAMOS_AMS_DESIGN.md §4.5, with §4.3.5-§4.3.8 and §4.7).

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
render(nl, osdi=(), notes=None, op=False) -> str
    The deck text; op=True prints .op in place of .tran and .print (the smoke
    deck).
smoke(nl, dir, osdi=(), nvc_libdir=None, timeout=600) -> List[Note]
    The compile-time check (§4.7): writes dir/smoke.cir from
    tables.smoke_netlist(nl) with an operating point, runs `Xyce -norun` (the
    model-parameter check: "No model parameter ... found" and "Unrecognized
    parameter" are errors, because Xyce otherwise runs with the parameter
    silently ignored; the bounds of binned cards are exempt), then the operating
    point itself.  Output in dir/smoke.norun.log and dir/smoke.log.  Returns []
    when the deck passes, else one error note with Xyce's error messages; Xyce's
    "no valid model card found" / "Unable to find model <base>." for a natively
    binned M line becomes "no bin of <base> for l=..., w=...".
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
    y       vamos_gcond: B<name> nd n I={v(ne)*(v(nd)-v(n))/rr}; any other module:
            Y<module> <name> <nodes> <card>, with .model <card> <module>.  A
            Verilog-A instance with parameters is refused (PyMS ignores VA
            parameter overrides), and so is one that needs a multiplier (Y lines
            take no m=)
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
evaluated on each instance path: an elaboration walk from the top level
evaluates every subckt's parameters with the X line's overrides (sky130's
wrappers pass l={l} w={w} nf={nf}), and a subckt whose bindings differ between
paths is printed once per distinct binding, as <name>__vb<k> (the X lines name
the copy; only the bin cards a copy uses are printed with it).  Geometry that is
not a number even on its path (temper) is an error when nf is not 1.  A
geometry no bin fits is an error at emit time, naming the evaluated l and w.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections import abc
from typing import Dict, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

from vamos.netlist import expr as X
from vamos.netlist import tables as T
from vamos.netlist.expr_ast import Binary, Call, Expr, Name, Num
from vamos.netlist.ir import Instance, Model, Netlist, Param, Source, Subckt
from vamos.netlist.numbers import fmt
from vamos.notes import ERROR, Note, NoteError, error, note

ENGINE = "xyce"
RAW = "vamos_tran.raw"
SMOKE_DECK = "smoke.cir"
SMOKE_LOG = "smoke.log"
SMOKE_NORUN_LOG = "smoke.norun.log"
MF = T.MF_PARAM[ENGINE]

_LETTER = {"r": "r", "c": "c", "l": "l", "k": "k", "v": "v", "i": "i", "e": "e", "g": "g",
           "f": "f", "h": "h", "b": "b", "d": "d", "q": "q", "m": "m", "j": "j", "x": "x"}
_LITERAL = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_BAD_NAME = re.compile(r"[\s(){},=;'\"]")


def emit(nl: Netlist, path: str, osdi: Sequence[str] = (), notes: Optional[List[Note]] = None) -> None:
    """Write the Xyce deck for nl to path (see the module docstring)."""
    text = render(nl, osdi, notes)
    # UTF-8, whatever the locale: the netlist text spice.py read as UTF-8 (a non-ASCII
    # title line failed the compile under a non-UTF-8 locale)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def render(nl: Netlist, osdi: Sequence[str] = (), notes: Optional[List[Note]] = None,
           op: bool = False) -> str:
    """The Xyce deck text for nl (see the module docstring)."""
    return _Deck(nl, osdi, op).render(notes)


class _Skip(Exception):
    """An element whose error is already recorded; the walk moves on."""


class _Env(abc.Mapping):
    """Parameter values inside one subckt instance: its own over the top-level values, with the
    names it declares but cannot evaluate hidden (no copy of the top level per instance)."""

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
    __slots__ = ("sub", "name", "bind", "models", "special")

    def __init__(self, sub: Optional[Subckt], name: str):
        self.sub, self.name = sub, name
        self.bind: Dict[int, Optional[str]] = {}    # id(X or binned M) -> master to print; None: error recorded
        self.models: Set[int] = set()                # ids of the model cards this body prints
        self.special: List[Tuple[str, str]] = []     # (instance, master) differing from the plain subckt


class _Deck:
    def __init__(self, nl: Netlist, osdi: Sequence[str], op: bool):
        self.nl, self.osdi, self.op = nl, list(osdi), op
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
        self.elaborate()

    def err(self, origin: str, msg: str) -> None:
        self.notes.append(error(origin, msg))

    def add(self, notes: Sequence[Note]) -> None:
        """Card notes, once per message (bins of one base share theirs, tables.card_label)."""
        seen = {(n.severity, n.message) for n in self.notes}
        for n in notes:
            if (n.severity, n.message) not in seen:
                seen.add((n.severity, n.message))
                self.notes.append(n)

    # -- elaboration: what each printed body needs ---------------------------------------
    def elaborate(self) -> None:
        """Walk the instance paths from the top level: decide the printed variants of every used
        subckt, the model cards each printed body needs (unused definitions are never printed)
        and the master of every X and binned M line.

        Xyce bins natively on total W, which is vamos's rule only when nf is absent or 1.  A
        binned device with another nf is bound to the bin card vamos selects (tables.select_bin,
        per-finger W), which needs l, w and nf as numbers: they are evaluated on each instance
        path (the subckt parameters with the X line's overrides; sky130 wrappers pass nf={nf}),
        and a subckt whose bindings differ between paths is printed once per distinct binding,
        as <name>__vb<k>.
        """
        self.variants: Dict[int, List[_Variant]] = {}       # id(subckt) -> printed variants
        self.memo: Dict[Tuple[int, Tuple[Tuple[str, str], ...]], _Variant] = {}
        self.vstack: List[_Variant] = []
        self.once_seen: Set[Tuple[str, str]] = set()
        self.taken = {s.name.lower() for s in T.all_subckts(self.nl)}
        self.top = _Variant(None, "")
        self.vstack.append(self.top)
        self.body_of(self.top, self.nl.body, T.Scope(self.nl.body), self.values)
        self.vstack.pop()

    def once(self, origin: str, msg: str) -> None:
        if (origin, msg) not in self.once_seen:
            self.once_seen.add((origin, msg))
            self.err(origin, msg)

    def body_of(self, var: _Variant, items: Sequence[object], scope: T.Scope, env: Mapping[str, float]) -> None:
        for it in items:
            if not isinstance(it, Instance):
                continue
            if it.kind == "x":
                found = scope.where(it.master or "")
                if found is None:
                    continue                                    # i_x reports it
                sub, where = found
                if any(v.sub is sub for v in self.vstack):
                    self.once(it.origin, "%s: subckt %s instantiates itself" % (it.name, sub.name))
                    var.bind[id(it)] = None
                    continue
                child = self.variant(sub, where, self.child_env(sub, it, env))
                var.bind[id(it)] = child.name
                if child.name != sub.name:
                    var.special.append((it.name, child.name))
            elif it.kind in T.MODEL_KINDS_OF:
                card, bins = scope.card_of(it)
                if card is not None:
                    self.need(scope, card)
                elif bins:
                    self.bin_choice(var, it, scope, bins, env)

    def variant(self, sub: Subckt, where: T.Scope, env: Mapping[str, float]) -> _Variant:
        v = _Variant(sub, sub.name)
        self.vstack.append(v)
        try:
            self.body_of(v, sub.body, T.Scope(sub.body, where, sub), env)
        finally:
            self.vstack.pop()
        key = (id(sub), tuple(v.special))
        old = self.memo.get(key)
        if old is not None:
            return old
        if v.special:
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
        number on this path is hidden (it shadows the top-level value of its name)."""
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
        l, w, nf = inst.params.get("l"), inst.params.get("w"), inst.params.get("nf")
        try:
            if l is None or w is None:
                raise T.TableError("%s: binned model %s needs l= and w= on the instance" % (inst.name, base))
            bounds = []
            for b in bins:
                if T.target(b).xyce_level is None:
                    raise T.TableError("%s: model %s has no Xyce equivalent" % (inst.name, b.name))
                bounds.append((b, T.bin_bounds(b, self.values)))
        except T.TableError as exc:
            self.once(inst.origin, str(exc))
            var.bind[id(inst)] = None
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
        return printed_path(self.nl, name, scope)

    def name_ok(self, name: str, origin: str, what: str) -> bool:
        if not name or _BAD_NAME.search(name):
            self.err(origin, "%s %r cannot be written in a Xyce deck" % (what, name))
            return False
        return True

    def collide(self, names: Sequence[str], origin: str, what: str) -> None:
        """Xyce names are case-insensitive: two IR names that differ only in case would merge."""
        seen: Dict[str, str] = {}
        for n in names:
            k = n.lower()
            if k in seen and seen[k] != n:
                self.err(origin, "%s %s and %s differ only in case; Xyce would merge them"
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
        self.check_scope(self.nl.body, "", [])
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

    def check_scope(self, items: Sequence[object], origin: str, ports: Sequence[str]) -> None:
        nodes = list(ports)
        insts = []
        for it in items:
            if isinstance(it, Instance):
                nodes += it.nodes
                insts.append(printed(it))
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
            except _Skip:
                pass
            except (T.TableError, X.PrintError) as exc:
                self.err(getattr(it, "origin", ""), str(exc))

    def subckt(self, s: Subckt, parent: T.Scope, var: _Variant) -> None:
        scope = T.Scope(s.body, parent, s)
        first = id(s) not in self.checked
        self.checked.add(id(s))
        if first:
            self.check_scope(s.body, s.origin, s.ports)
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
        params, notes = T.model_params(m, ENGINE, binned=m.base is not None, options=self.nl.options)
        self.add(notes)
        if row.strip_version and any(k == "version" for k, _ in params):
            self.add([note(m.origin, "%s: BSIM3 card simulated as Xyce BSIM3v3.2.2 (level 9)" % T.card_label(m))])
        out = ["level=%d" % row.xyce_level] if row.xyce_print_level else []
        for k, v in params:
            out.append("%s=%s" % (k, self.xv(v, m.origin, "model %s parameter %s" % (m.name, k))))
        self.body.append(".model %s %s%s" % (m.name, m.kind, "".join(" " + o for o in out)))

    # -- instances
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
        T.check_params(inst)
        fn(inst, scope)

    def line(self, inst: Instance, fields: Sequence[str]) -> None:
        self.body.append(" ".join([printed(inst)] + [f for f in fields if f]))

    def mult(self, inst: Instance, scope: T.Scope) -> Optional[Expr]:
        return T.multiplier(inst.params.get("m"), scope.in_subckt, ENGINE)

    def mparam(self, inst: Instance, scope: T.Scope) -> List[str]:
        m = self.mult(inst, scope)
        if m is None or T.MULT[ENGINE][inst.kind] != "param":
            return []
        return ["m=" + self.xv(m, inst.origin, "multiplier of " + inst.name)]

    def plist(self, inst: Instance, skip: Sequence[str] = ("m",), element: str = "",
              level: Optional[int] = None) -> List[str]:
        out = []
        for k, v in inst.params.items():
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
        if "ic" in inst.params:
            self.err(inst.origin, "%s: IC= on a %s is not supported (use .ic)"
                     % (inst.name, T.ELEMENT_NAME[inst.kind]))
            return
        card = self.card(inst, scope) if inst.master is not None else None
        master = card.name if card is not None else ""
        val, skip, mparam = inst.value, ("m",), None
        if inst.kind == "l" and inst.name in scope.coupled:
            # Xyce's K pass keeps only L and IC of a coupled inductor: its multiplier and TCs are
            # folded into the value (tables.coupled_inductance)
            folded = T.coupled_inductance(inst, self.mult(inst, scope), card, *T.run_temps(self.nl))
            if folded is not None:
                val, skip, mparam = folded, T.COUPLED_FOLDED, []
        if val is None and not master:
            self.err(inst.origin, "%s has no value" % inst.name)
            return
        value = self.xv(val, inst.origin, "value of " + inst.name) if val is not None else ""
        self.line(inst, inst.nodes + [master, value] + self.plist(inst, skip)
                  + (self.mparam(inst, scope) if mparam is None else mparam))

    def i_k(self, inst: Instance, scope: T.Scope) -> None:
        if len(inst.ctrl) != 2 or inst.value is None:
            self.err(inst.origin, "mutual inductance %s needs two inductors and a coupling" % inst.name)
            return
        self.line(inst, [printed_path(self.nl, inst.ctrl[0], scope), printed_path(self.nl, inst.ctrl[1], scope),
                         self.xv(inst.value, inst.origin, "coupling of " + inst.name)])

    def i_v(self, inst: Instance, scope: T.Scope) -> None:
        self.line(inst, inst.nodes + self.source(inst, inst.source or Source()))

    def i_i(self, inst: Instance, scope: T.Scope) -> None:
        src = inst.source or Source()
        m = self.mult(inst, scope)
        if m is not None and not src.code_uri:
            src = T.scale_source(src, m)              # Xyce ignores m= on current sources
        self.line(inst, inst.nodes + self.source(inst, src))

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
            fields.append(printed_path(self.nl, inst.ctrl[0], scope))
        fields.append(self.xv(gain, inst.origin, "gain of " + inst.name))
        self.line(inst, fields + self.mparam(inst, scope))

    def i_b(self, inst: Instance, scope: T.Scope) -> None:
        if inst.expr is None or inst.expr_kind not in ("v", "i"):
            self.err(inst.origin, "behavioral source %s has no v= or i= expression" % inst.name)
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
        self.line(inst, inst.nodes + [m.name, area] + self.plist(inst, ("m", "area"), level=lv)
                  + self.mparam(inst, scope))

    def i_q(self, inst: Instance, scope: T.Scope) -> None:
        m = self.card(inst, scope)
        area = self.xv(inst.value, inst.origin, "area of " + inst.name) if inst.value is not None else ""
        self.line(inst, inst.nodes + [m.name, area] + self.plist(inst, ("m", "area")) + self.mparam(inst, scope))

    def i_j(self, inst: Instance, scope: T.Scope) -> None:
        m = self.card(inst, scope)
        area: Optional[Expr] = inst.value
        k = self.mult(inst, scope)
        if k is not None:
            area = T.times(area if area is not None else Num(1.0), k)   # J takes no m=: area is equivalent
        text = self.xv(area, inst.origin, "area of " + inst.name) if area is not None else ""
        self.line(inst, inst.nodes + [m.name, text] + self.plist(inst, ("m", "area")))

    def i_m(self, inst: Instance, scope: T.Scope) -> None:
        card, bins = scope.card_of(inst)
        if card is None and bins:
            if id(inst) in self.var.bind:        # bound to the bin card vamos selected
                master = self.var.bind[id(inst)]
                if master is None:
                    raise _Skip()                # the elaboration recorded the error
            else:                                # Xyce bins natively; it names no bin if none fits
                master = inst.master or ""
                msg = ("no bin of %s for l=%s, w=%s (scale %s; instance %s%s)"
                       % (master, X.to_text(inst.params["l"]), X.to_text(inst.params["w"]), fmt(self.s),
                          inst.name, " in subckt " + scope.path() if scope.in_subckt else ""))
                self.nobin[len(self.body)] = msg
                self.nobin_dev[(master.upper(), printed(inst).upper())] = msg
        else:
            m = self.card(inst, scope)
            self.add(T.mos_junction_warnings(inst, m, self.nl.options))
            master = m.name
        self.line(inst, inst.nodes + [master] + self.plist(inst) + self.mparam(inst, scope))

    def i_x(self, inst: Instance, scope: T.Scope) -> None:
        sub = scope.find_subckt(inst.master or "")
        if sub is None:
            self.err(inst.origin, "%s: subckt %s is not defined" % (inst.name, inst.master))
            return
        if len(inst.nodes) != len(sub.ports):
            self.err(inst.origin, "%s: %d nodes for subckt %s with %d ports"
                     % (inst.name, len(inst.nodes), sub.name, len(sub.ports)))
            return
        master = self.var.bind.get(id(inst), sub.name)
        if master is None:
            raise _Skip()                        # the elaboration recorded the error
        fields = list(inst.nodes) + [master]
        for k, v in inst.params.items():
            if k == "m":
                continue
            fields.append("%s=%s" % (X.param_ident(k, ENGINE), self.xv(v, inst.origin, "%s parameter %s"
                                                                          % (inst.name, k))))
        m = self.mult(inst, scope)
        if m is not None:
            fields.append("%s=%s" % (MF, self.xv(m, inst.origin, "multiplier of " + inst.name)))
        self.line(inst, fields)

    def i_y(self, inst: Instance, scope: T.Scope) -> None:
        module = inst.master or ""
        if module == T.GCOND_MODULE:
            self.gcond(inst, scope)
            return
        if not module:
            self.err(inst.origin, "Verilog-A instance %s has no module" % inst.name)
            return
        extra = [k for k in inst.params if k != "m"]
        if extra:
            self.err(inst.origin, "%s: Verilog-A parameters (%s) on Xyce: PyMS ignores parameter "
                     "overrides, so they are not supported (VACASK honours them)"
                     % (inst.name, ", ".join(extra)))
            return
        if "m" in inst.params or (scope.in_subckt and scope.subckt.name in self.multiplied):
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
        self.body.append(" ".join([printed(inst), nd, n, "I={%s}" % text] + self.mparam(inst, scope)))

    # -- sources (§4.3.8)
    def source(self, inst: Instance, src: Source) -> List[str]:
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
        out: List[str] = []
        if src.dc is not None:
            out.append("DC " + xv(src.dc, "dc"))
        elif src.wave is None:
            out.append("DC 0.0")
        if src.ac is not None:
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
        nl, o = self.nl, "control"
        spice = "spice" in nl.options
        temp = nl.temp if nl.temp is not None else (27.0 if spice else 25.0)
        tnom = nl.tnom if nl.tnom is not None else (27.0 if spice else 25.0)
        lines = []
        try:
            opts = ["temp=" + fmt(self.num(temp, o, "temp")), "tnom=" + fmt(self.num(tnom, o, "tnom"))]
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
                p = "i(%s)" % printed_path(self.nl, target, top)
            else:
                self.err("probe", "unknown probe kind %r" % (kind,))
                continue
            if p not in out:
                out.append(p)
        return out


WAVE = T.WAVE_FIELDS


def _plist(s: Subckt) -> List[Param]:
    """A subckt's parameters, header and body (spice.parse merges them; a hand-built IR may not)."""
    names = {p.name for p in s.params}
    return list(s.params) + [p for p in s.body if isinstance(p, Param) and p.name not in names]


def printed(inst: Instance) -> str:
    """An element's name in the Xyce deck: it must start with the element's letter."""
    if inst.kind == "y":
        return inst.name if inst.master != T.GCOND_MODULE or inst.name[:1].lower() == "b" else "b" + inst.name
    letter = _LETTER.get(inst.kind, "")
    return inst.name if inst.name[:1].lower() == letter else letter + inst.name


def printed_path(nl: Netlist, name: str, scope: T.Scope) -> str:
    """A (hierarchical) element reference with every component as Xyce prints it."""
    parts = name.split(".")
    items: Sequence[object] = scope.subckt.body if scope.subckt is not None else nl.body
    sc = scope
    out = []
    for i, part in enumerate(parts):
        inst = next((it for it in items if isinstance(it, Instance) and it.name == part), None)
        if inst is None:
            out += parts[i:]
            break
        out.append(printed(inst))
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
        for it in items:
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
        bad = [m for m in msgs if _UNREC.search(m) or _bad_param(m, binned) or m.startswith("no bin of")]
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
