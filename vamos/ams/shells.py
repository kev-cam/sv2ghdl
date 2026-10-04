"""Shells for the SPICE cells of a vcs-ams compile (docs/VAMOS_AMS_DESIGN.md §5.1).

find_cells() (cell_set() is its §5.8 form) decides which cells are cut:
`iverilog -g2012 -tnull -s <top>` on pp.orig.v with every multi-view
candidate (a Verilog module a use_spice/partition glob names) blanked and
replaced by a placeholder: its header shell plus an instance of a module that
does not exist, `vamos_ams_probe_<k>`.  iverilog reports "Unknown module
type" once per elaborated instance, so vamos_ams_probe_<k> shows exactly the
candidates instantiated under the top (instantiations inside masked bodies and
in never-taken generate branches do not count), and every other unknown
module is a SPICE-only cell: it must name a subckt (use_spice c:s, else its
own name, case-insensitively), else it is an error.  A use_spice cell that is
never instantiated gets a note and no shell.

build() makes one CutCell (vamos/ams/model.py) and one Verilog shell per cut
cell, writes ams/pp.v (pp.orig.v with the instantiated multi-view cells
blanked - user line numbers unchanged - then "`resetall", "`timescale
<p>/<p>", "`default_nettype wire" and the shells) and settles every auto
port's direction with the direction probe:

1. iverilog -tnull -s <top> with every auto port inout.  An auto port whose
   actual is a variable (or a constant, or an expression) is reported as
   "Inout port expression must support continuous assignment." plus
   ": Port N (p) of c is connected to E".
2. Those ports become output in a second probe.  An instance whose variable is
   then reported as "Cannot perform procedural assignment to variable 'x'
   because it is also continuously assigned." (a declaration initializer
   counts), or whose actual is reported as "Output port expression must
   support a continuous assignment." (a constant, an expression, a variable
   with another driver), needs input; the others need output.
2b. iverilog accepts an inout cell port on an `input` port of the enclosing
   module silently, and coerces that input port to inout (port coercion,
   which VCS does by default too) where the module's instance connects it to
   a collapsible net: the nets are one, as the cut (§5.4) joins them.  Where
   the instance connects it to a variable, an expression or a constant, or
   leaves it unconnected, or is an instance array (iverilog coerces none of
   its ports), or the module is the top, the port stays an input: Verilog
   drives the module's net from the actual and the cell could drive only the
   inside.  Such an elaborated instance therefore needs input, for a
   whole-port actual or a bit or part of one; where the module's instance
   connects the port to an input port of its own module, that port is
   checked the same way, up the hierarchy (wrapper_inputs).  The scan is
   structural: it reads the connections as written, counts the instances in
   modules under the top, does not evaluate generate conditions, and knows
   only the variables the declaration scan records (PP.variables: not
   user-typed ones).
   An input cell port has no marker: unless the module drives the port's net
   itself, the port stays a VHDL `in` port and the cell port is on the
   actual's net (for .a(clk), a rule on d2a node=tb.clk reaches it), with the
   reason in ShellResult.directions for the IE report.  A cell port that does
   drive the module's net there (port_dir inout or output, a multi-view
   cell's inout or output port, or an auto port the scan misses) makes the
   iverilog core buffer the port behind a variable, a select of one, an
   expression or a constant: tgt-vhdl draws the buffer in the parent as
   `PB_<label>_<port> <= <actual>` (translator patch T8), and the cut joins
   the module's side to the actual's net one way, the cell port acting as an
   input, or stops with an error asking for port_dir input where the cell
   port is an output or the module also reads the port.
   A module that drives the port's net itself gets the buffer whatever the
   cell port's direction, and its side is a net of its own, as in Verilog
   (cut.py, "port buffers").
3. One shell per cell: an auto port needed as input by one instance and as
   output by another is an error naming both and asking for port_dir.
4. A final probe with the decided directions must be clean.  Any other probe
   error is reported verbatim (at the user's file:line) and stops the build.
   Warnings that name a cut cell (port width mismatches) are passed on.

Markers.  Every non-real port bit whose shell_dir is output or inout gets a
scalar `bufif1 vamos_ams_hiz_<j> (<port>[<i>], 1'b0, 1'b0);` (j counts the
shell's markers); a port whose range is not constant gets a generate loop over
the range text (block vamos_ams_hiz_<port>, genvar vamos_ams_g_<port>).
Without markers tgt-vhdl makes an undriven inout an `in` port and the iverilog
core drops output connections to vector bits, generate loops, instance arrays
and plain bit-selects; with them inout ports become `inout
resolved_logic3d[_vector]` and every output connection is kept (a bit
connection as an LPM_*/tmp_* temporary).  The marker side is pinned by
tests/vamos/test_ams_shells.py (iverilog-sv2ghdl + nvc -e, WSL); the
no-marker losses were observed with the same iverilog (13.0 devel, 6029f3dfb).

Parameter overrides (#(...), #n, defparam) on a SPICE-only cell are errors
(§4.7).  On a multi-view cell deck.py decides (§4.7, over cut.param_overrides):
an override passes only for a parameter the cell's port ranges depend on,
deck.range_params: the whole identifiers (case-sensitive) of the port range
texts, followed through the cell's parameter defaults - the rule of
Header.range_params, which a CutCell does not carry.

Use from ams/flow.py (steps 3-7 of §1; flush() is flow._Printer.flush: print
the notes, stop on an error; ots is job.override_timescale):

    pp = verilog_ports.preprocess(job, layout.pp_orig(daidir), override_timescale=ots, ams=True)
    flush(list(pp.notes) + verilog_ports.api_scan(pp))
    job.precision = pp.precision
    verilog_ports.apply_library_rule(pp)                 # VCS's -v rule (blanks library copies)
    top = verilog_ports.find_top(pp, job, exclude=shells.cell_globs(cfg))    # -top checked too
    flush(verilog_ports.precheck(job, top, ots, pp=pp))  # user file:line syntax/elaboration errors
    res = shells.build(pp, top, cfg, nl, job)         # find_cells() inside (or pass cellset=); writes
    flush(res.notes)                                  # layout.pp(daidir); NoteError on errors
    # translate res.path with -s top (§5.2), then
    # cut.analyse(design, top, res.cells, nl, cfg, hits, pp=pp, directions=res.directions) and
    # cut.assign_roles(analysis, alloc, disabled, removal, directions=res.directions):
    # pp.variables / pp.tri_nets -> Net.variable and the tri0/tri1 rule, res.directions -> the
    # IE report's direction lines.  set_sim_case reaches find_cells() and build() through cfg
    # (case_sensitive()); flow.py leaves pp.case_sensitive False.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from vamos.ams import globs, portmap
from vamos.ams.config import AmsConfig, UseSpice
from vamos.ams.model import AUTO, INOUT, INPUT, LOGIC, OUTPUT, REAL, CutCell, CutPort, PortMap
from vamos.ams.verilog_ports import (KEYWORDS, PP, Diag, Header, Inst, blank, concat_operands, constant_names,
                                     ident_ref, instantiations, module_header, parse_diags, port_directions,
                                     run_iverilog, units)
from vamos.job import Job
from vamos.netlist.ir import Netlist, Subckt
from vamos.notes import Note, NoteError, error, has_errors, note, warning

MARK = "vamos_ams_hiz"            # marker gates and generate blocks (VHDL labels sv_bufif1_vamos_ams_hiz*_inst)
GENVAR = "vamos_ams_g_"
PROBE = "vamos_ams_probe_"        # cell-set placeholder probes: vamos_ams_probe_<k> vamos_ams_probe ();
PRELUDE = "`resetall\n`timescale %s/%s\n`default_nettype wire\n"
DIRS = (INPUT, OUTPUT, INOUT)
INOUT_WHY = "VCS default; port_dir is faster"
_SIMPLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def vid(name: str) -> str:
    """A Verilog identifier for name; escaped (with its terminating space) when not simple."""
    if _SIMPLE.match(name) and name not in KEYWORDS:
        return name
    return "\\" + name + " "


def case_sensitive(pp: PP, cfg: AmsConfig) -> bool:
    """set_sim_case sensitive (XA cfg) or PP.case_sensitive: control-file names match exactly."""
    return pp.case_sensitive or str(cfg.xa.get("case", "")).lower() == "sensitive"


def cell_globs(cfg: AmsConfig) -> List[str]:
    """Every use_spice / partition -cell glob: find_top's exclude set (§1.4)."""
    return [g for u in cfg.use_spice for g, _ in u.cells]


def _lookup(nl: Netlist, name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    subs = nl.subckts()
    if name in subs:
        return name
    low = name.lower()
    return low if low in subs else None


def _stmts_for(cfg: AmsConfig, names: Sequence[str], cs: bool) -> List[Tuple[int, UseSpice, str]]:
    """(index, statement, bound subckt or '') of every use_spice statement naming one of `names`."""
    out = []
    for k, u in enumerate(cfg.use_spice):
        for g, s in u.cells:
            if any(n and globs.match(g, n, cs) for n in names):
                out.append((k, u, s))
                break
    return out


def binding(cfg: AmsConfig, cell: str, cs: bool = False) -> Optional[str]:
    """The subckt a use_spice statement without -inst binds `cell` to (c:s; the last one
    wins), or None when no statement binds it explicitly."""
    sub = None
    for _, u, s in _stmts_for(cfg, [cell], cs):
        if not u.insts and s:
            sub = s
    return sub


def subckt_for(cfg: AmsConfig, nl: Netlist, cell: str, cs: bool = False) -> Optional[str]:
    """The default subckt (IR name) of a Verilog cell: the explicit binding, else a subckt
    of the same name, else the first -inst binding; None if none exists."""
    b = binding(cfg, cell, cs)
    if b:
        return _lookup(nl, b)
    found = _lookup(nl, cell)
    if found:
        return found
    for _, u, s in _stmts_for(cfg, [cell], cs):
        if s and _lookup(nl, s):
            return _lookup(nl, s)
    return None


# =============================================================================
# iverilog -tnull on a text
# =============================================================================

def run_tnull(top: str, text: str) -> List[Diag]:
    """iverilog -g2012 -tnull -s top on `text` (pp.v-shaped: its line numbers are pp's)."""
    d = tempfile.mkdtemp(prefix="vamos-probe-")
    try:
        path = os.path.join(d, "pp.v")
        with open(path, "w") as fh:
            fh.write(text)
        _, out = run_iverilog(["-g2012", "-tnull", "-s", top, path], cwd=d)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    diags, other = parse_diags(out)
    for o in other:
        if o.strip():
            diags.append(Diag("", 0, "error", o.strip()))
    return diags


def unknown_modules(pp: PP, top: str, text: Optional[str] = None) -> Tuple[Dict[str, List[int]], List[Diag]]:
    """Modules `iverilog -tnull -s top` cannot find in `text` (default pp.text): name ->
    pp lines of the elaborated instances, in report order; and every other message."""
    unknown: Dict[str, List[int]] = {}
    other: List[Diag] = []
    for dg in run_tnull(top, pp.text if text is None else text):
        m = re.match(r"Unknown module type: (\S+)$", dg.message)
        if m and dg.kind == "error":
            unknown.setdefault(m.group(1), []).append(dg.line)
        else:
            other.append(dg)
    return unknown, other


@dataclass
class Locator:
    """Message origins for the lines of a probe text (pp.v-shaped): the user's file:line
    for the user part, "pp.v:<line> (shell of <cell>)" for the appended shells."""
    pp: PP
    shell_from: int = 0                                       # first line of the appended part
    spans: Dict[str, Tuple[int, int]] = field(default_factory=dict)   # cell -> lines of its shell

    def where(self, line: int) -> str:
        if self.shell_from and line >= self.shell_from:
            for cell, (a, b) in self.spans.items():
                if a <= line <= b:
                    return "pp.v:%d (shell of %s)" % (line, cell)
            return "pp.v:%d" % line
        return self.pp.origin(line) if line > 0 else ""

    hier: Dict[str, str] = field(default_factory=dict)        # instance name -> cut cell (for hints)

    def verbatim(self, dg: Diag) -> Note:
        """An iverilog message as a note, at its origin; a failing hierarchical name that goes
        through a SPICE cell instance gets a hint (references into SPICE cells are not in v1)."""
        msg = dg.message + "".join("; " + x for x in dg.extra)
        if dg.kind != "warning" and self.hier:
            for path in re.findall(r"[A-Za-z_][\w$]*(?:\[[^\]]*\])?(?:\.[A-Za-z_][\w$]*(?:\[[^\]]*\])?)+", msg):
                parts = [re.sub(r"\[.*$", "", x) for x in path.split(".")]
                hit = [x for x in parts[:-1] if x in self.hier]
                if hit:
                    msg += " (%s is an instance of SPICE cell %s: hierarchical references into SPICE cells are " \
                           "not supported)" % (hit[-1], self.hier[hit[-1]])
                    break
        return warning(self.where(dg.line), msg) if dg.kind == "warning" else error(self.where(dg.line), msg)


# =============================================================================
# The cell set (§5.1, step 6)
# =============================================================================

@dataclass
class CellSet:
    """find_cells() result; build() takes it back."""
    top: str
    spice_only: List[str] = field(default_factory=list)   # Verilog spellings, first-instantiation order
    multi_view: List[str] = field(default_factory=list)   # instantiated multi-view cells, definition order
    candidates: List[str] = field(default_factory=list)   # every Verilog module a use_spice glob names
    subckts: Dict[str, str] = field(default_factory=dict) # cell -> default subckt (IR name)
    lines: Dict[str, List[int]] = field(default_factory=dict)   # SPICE-only cell -> pp lines, per instance
    notes: List[Note] = field(default_factory=list)


def find_cells(pp: PP, top: str, cfg: AmsConfig, nl: Netlist) -> CellSet:
    """The cut cells under `top` (§5.1 "Cell set"); errors are in CellSet.notes."""
    cs = case_sensitive(pp, cfg)
    res = CellSet(top=top)
    notes = res.notes
    pats = cell_globs(cfg)
    cands = sorted((n for n in pp.modules if any(globs.match(g, n, cs) for g in pats)),
                   key=lambda n: pp.modules[n][0].start)
    res.candidates = cands
    if top in cands:
        notes.append(error(pp.modules[top][0].origin, "the top module %s is a use_spice/partition cell; "
                           "SPICE-top designs are not supported" % top))
        return res

    spans: List[Tuple[int, int]] = []
    placeholders: List[Tuple[str, str]] = []
    inserts: List[Tuple[int, str]] = []
    header_errors: Dict[str, List[Note]] = {}
    toks = pp.toks()
    for k, name in enumerate(cands):
        probe = "%s%d vamos_ams_probe ();" % (PROBE, k)
        try:
            h = module_header(pp, name)
        except NoteError as e:
            header_errors[name] = e.notes
            for d in pp.modules[name]:              # keep the definition; probe right after its header
                inserts.append((toks[d.tok_hdr].end, " " + probe))
            continue
        spans += [(d.start, d.end) for d in pp.modules[name]]
        placeholders.append((name, header_shell(name, h, extra=[probe])))
    text = blank(pp.text, spans)
    for off, s in sorted(inserts, reverse=True):
        text = text[:off] + s + text[off:]
    if not text.endswith("\n"):
        text += "\n"
    p = pp.precision or "1ps"
    loc = Locator(pp, text.count("\n") + 1)
    line = loc.shell_from + PRELUDE.count("\n")
    for name, t in placeholders:
        loc.spans[name] = (line, line + t.count("\n") - 1)
        line += t.count("\n")
    unknown, other = unknown_modules(pp, top, text + PRELUDE % (p, p) + "".join(t for _, t in placeholders))
    loc.hier = {x.name: x.module for x in instantiations(pp) if x.module in cands or x.module in unknown}

    for dg in other:
        if dg.kind in ("error", "sorry"):
            notes.append(loc.verbatim(dg))
    instantiated: Set[str] = set()
    for name, lines in unknown.items():
        if name.startswith(PROBE) and name[len(PROBE):].isdigit():
            k = int(name[len(PROBE):])
            if k < len(cands):
                instantiated.add(cands[k])
            continue
        sub = subckt_for(cfg, nl, name, cs)
        if sub is None:
            b = binding(cfg, name, cs)
            for ln in sorted(set(lines)):
                if b:
                    notes.append(error(loc.where(ln), "module %s: use_spice binds it to subckt %s, "
                                       "which is not in the SPICE netlists" % (name, b)))
                else:
                    notes.append(error(loc.where(ln), "module %s not found in Verilog sources or "
                                       "SPICE netlists%s" % (name, " (-y library directories are not searched; "
                                                             "give the file with -v)" if pp.lib_dirs else "")))
            continue
        res.spice_only.append(name)
        res.subckts[name] = sub
        res.lines[name] = lines
    res.spice_only.sort(key=lambda n: min(res.lines[n]))
    for name in cands:
        if name not in instantiated:
            notes.append(note(pp.modules[name][0].origin, "cell %s not instantiated under %s; ignored"
                              % (name, top)))
            continue
        if name in header_errors:
            notes.extend(header_errors[name])
            continue
        res.multi_view.append(name)
        sub = subckt_for(cfg, nl, name, cs)
        if sub is None:
            notes.append(error(pp.modules[name][0].origin, "cell %s: no SPICE subckt %s in the netlists"
                               % (name, binding(cfg, name, cs) or name.lower())))
        else:
            res.subckts[name] = sub
    seen = set(cands) | set(res.spice_only)
    for u in cfg.use_spice:
        for g, _ in u.cells:
            if not any(globs.match(g, n, cs) for n in seen):
                notes.append(note(u.origin, "cell %s not instantiated; ignored" % g))
    return res


def cell_set(pp: PP, top: str, cfg: AmsConfig, nl: Netlist) -> Tuple[List[str], List[str], List[Note]]:
    """§5.8: (SPICE-only cells, instantiated multi-view cells, notes); see find_cells()."""
    r = find_cells(pp, top, cfg, nl)
    return r.spice_only, r.multi_view, r.notes


# =============================================================================
# Ports of a SPICE-only cell
# =============================================================================

def bus_member(name: str, fmts: Sequence[str]) -> Optional[Tuple[str, int]]:
    """(base, index) when name is a bus member under one of the bus formats ("d<3>" -> ("d", 3))."""
    for f in fmts:
        if "%d" not in f:
            continue
        a, _, b = f.partition("%d")
        m = re.match(r"^(.+?)" + re.escape(a) + r"(\d+)" + re.escape(b) + r"$", name)
        if m:
            return m.group(1), int(m.group(2))
    return None


def index_orders(cfg: AmsConfig, names: Sequence[str], cs: bool = False) -> Dict[str, str]:
    """port_index_order of the statements naming a cell: port (lowercase) | '*' -> dec|inc|same."""
    out: Dict[str, str] = {}
    for _, u, _ in _stmts_for(cfg, names, cs):
        for port, order in u.index_order:
            out[port.lower() if port != "*" else "*"] = order.lower()
    return out


@dataclass
class _Group:
    base: str                     # SPICE base name (a scalar's own name), as in the IR
    members: List[Tuple[int, str]] = field(default_factory=list)   # (index, SPICE member name)
    scalar: bool = False


def spice_ports(cell: str, sub: Subckt, cfg: AmsConfig, nl: Netlist, removed: Set[str],
                spellings: Dict[str, str], cs: bool = False) -> Tuple[List[CutPort], List[Note]]:
    """The Verilog ports of a SPICE-only cell (§5.1).

    Subckt.ports minus `removed` (port_connect'ed, lowercase), bus members grouped
    under their base by the bus formats at the first member's position, ranges per
    port_index_order (dec [max:min], inc [min:max], same: the order of appearance,
    which must be monotonic), directions per port_dir (else auto), names spelled per
    `spellings` (lowercase SPICE base -> Verilog spelling), else per Netlist.spelling.
    CutPort.msb/lsb are the left/right bounds as declared.
    """
    notes: List[Note] = []
    fmts = cfg.formats()
    groups: List[_Group] = []
    by_base: Dict[str, _Group] = {}
    for p in sub.ports:
        if p.lower() in removed:
            continue
        bm = bus_member(p, fmts)
        base, idx = (bm[0], bm[1]) if bm else (p, None)
        g = by_base.get(base)
        if g is None:
            g = _Group(base, scalar=idx is None)
            by_base[base] = g
            groups.append(g)
        elif g.scalar or idx is None:
            notes.append(error(sub.origin, "subckt %s: %s is both a scalar port and a bus under the bus "
                               "format" % (sub.name, base)))
            continue
        if idx is not None:
            g.members.append((idx, p))
    orders = index_orders(cfg, [cell, sub.name], cs)
    dirs, dn = _port_dirs(cfg, cell, sub, [g.base for g in groups],
                          {m: g.base for g in groups for _, m in g.members}, cs)
    notes += dn
    ports: List[CutPort] = []
    for g in groups:
        rng_text, msb, lsb = None, None, None
        if not g.scalar:
            idxs = [i for i, _ in g.members]
            if len(set(idxs)) != len(idxs):
                notes.append(error(sub.origin, "subckt %s: bus %s repeats an index (%s)"
                                   % (sub.name, g.base, " ".join(m for _, m in g.members))))
                continue
            order = orders.get(g.base.lower(), orders.get("*", "same"))
            if order == "dec":
                msb, lsb = max(idxs), min(idxs)
            elif order == "inc":
                msb, lsb = min(idxs), max(idxs)
            else:
                inc = all(b > a for a, b in zip(idxs, idxs[1:]))
                dec = all(b < a for a, b in zip(idxs, idxs[1:]))
                if not (inc or dec):
                    notes.append(error(sub.origin, "subckt %s: the indexes of bus %s are not monotonic (%s); "
                                       "give port_index_order (%s=>dec) or (%s=>inc)"
                                       % (sub.name, g.base, " ".join(m for _, m in g.members), g.base, g.base)))
                    continue
                msb, lsb = idxs[0], idxs[-1]
            rng_text = "[%d:%d]" % (msb, lsb)
        spelled = spellings.get(g.base.lower())
        if spelled is None:
            first = g.base if g.scalar else g.members[0][1]
            orig = nl.spelling.get(first, first)
            bm = None if g.scalar else bus_member(orig, fmts)
            spelled = orig if g.scalar else (bm[0] if bm else g.base)
        d = dirs.get(g.base, AUTO)
        ports.append(CutPort(len(ports), spelled, LOGIC, d, INOUT if d == AUTO else d, rng_text, msb, lsb))
    return ports, notes


def _port_dirs(cfg: AmsConfig, cell: str, sub: Subckt, bases: List[str], member_base: Dict[str, str],
               cs: bool) -> Tuple[Dict[str, str], List[Note]]:
    """port_dir directions by SPICE base name (a later statement wins)."""
    out: Dict[str, str] = {}
    notes: List[Note] = []
    lm = {m.lower(): b for m, b in member_base.items()}
    for pd in cfg.port_dirs.values():
        if not (globs.match(pd.cell, cell, cs) or globs.match(pd.cell, sub.name, cs)):
            continue
        for pname, d in pd.dirs.items():
            d = d.lower()
            if d not in DIRS:
                notes.append(error(pd.origin, "port_dir -cell %s: %s is not input, output or inout"
                                   % (pd.cell, d)))
                continue
            hits = [b for b in bases if globs.match(pname, b, cs)]
            if not hits and not globs.has_wildcard(pname) and pname.lower() in lm:
                hits = [lm[pname.lower()]]            # a bus member names its bus
            if not hits and not globs.has_wildcard(pname) and not globs.has_wildcard(pd.cell):
                notes.append(error(pd.origin, "port_dir -cell %s: subckt %s has no port %s (ports: %s)"
                                   % (pd.cell, sub.name, pname, " ".join(bases))))
            for b in hits:
                out[b] = d                            # a later statement wins
    return out, notes


def default_portmap(cfg: AmsConfig, names: Sequence[str], cs: bool = False) -> PortMap:
    """A cell's default PortMap: the port_map items of the use_spice statements without
    -inst naming it (a later item wins) and the bus formats."""
    pm = PortMap(explicit={}, default="snps_by_name", bus_formats=list(cfg.formats()))
    for _, u, _ in _stmts_for(cfg, names, cs):
        if not u.insts:
            _apply_items(pm, u)
    return pm


def statement_portmap(u: UseSpice, cfg: AmsConfig) -> PortMap:
    """The PortMap of one use_spice statement (for its -inst instances)."""
    pm = PortMap(explicit={}, default="snps_by_name", bus_formats=list(cfg.formats()))
    _apply_items(pm, u)
    return pm


def _apply_items(pm: PortMap, u: UseSpice) -> None:
    for v, s in u.port_map:
        if v == "*":
            pm.default = s.lower()
        else:
            pm.explicit[v] = s


def _connects(cfg: AmsConfig, names: Sequence[str], sub: Subckt, cs: bool
              ) -> Tuple[Dict[str, str], Set[str], List[Note]]:
    """port_connect: (SPICE port -> net for statements without -inst, every connected SPICE port, notes)."""
    conns: Dict[str, str] = {}
    removed: Set[str] = set()
    notes: List[Note] = []
    orig = {p.lower() for p in (sub.orig_ports or sub.ports)}
    for pc in cfg.port_connects:
        if not any(globs.match(pc.cell, n, cs) for n in names):
            continue
        for sp, net, _real in pc.conns:
            lo = sp.lower()
            if lo not in orig:
                notes.append(error(pc.origin, "port_connect -cell %s: subckt %s has no port %s"
                                   % (pc.cell, sub.name, sp)))
                continue
            removed.add(lo)
            if pc.inst is None:
                conns[lo] = net
    return conns, removed, notes


def _spellings(insts: Sequence[Inst], cell: str, pp: PP) -> Tuple[Dict[str, str], List[Note]]:
    """lowercase port -> the spelling its instances' named connections use; one port
    spelled two ways is an error naming the instances."""
    seen: Dict[str, Dict[str, List[Inst]]] = {}
    for x in insts:
        if x.module == cell:
            for n in x.named:
                if n != "*":
                    seen.setdefault(n.lower(), {}).setdefault(n, []).append(x)
    out: Dict[str, str] = {}
    notes: List[Note] = []
    for lo, spell in seen.items():
        if len(spell) > 1:
            parts = ["%s by %s" % (s, ", ".join("%s.%s (%s)" % (x.scope, x.name, pp.origin(x.line))
                                                for x in xs[:3])) for s, xs in sorted(spell.items())]
            first = min(x.line for xs in spell.values() for x in xs)
            notes.append(error(pp.origin(first), "cell %s: port %s is spelled differently by its "
                               "instances: %s" % (cell, lo, "; ".join(parts))))
        out[lo] = sorted(spell)[0]
    return out, notes


# =============================================================================
# Shell text
# =============================================================================

def split_range(text: str) -> Tuple[str, str]:
    """"[N-1:0]" -> ("N-1", "0") at the top-level colon."""
    inner = text.strip()[1:-1]
    depth = 0
    for k, ch in enumerate(inner):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == ":" and depth == 0:
            return inner[:k].strip(), inner[k + 1:].strip()
    raise ValueError("not a range: %r" % (text,))


def markers(ports: Sequence[CutPort]) -> List[str]:
    """Marker lines for every non-real port bit whose shell_dir is output or inout (§5.1)."""
    out: List[str] = []
    j = 0
    for p in ports:
        if p.kind == REAL or p.shell_dir not in (OUTPUT, INOUT):
            continue
        ref = vid(p.verilog)
        if p.range_text is None:
            out.append("  bufif1 %s_%d (%s, 1'b0, 1'b0);" % (MARK, j, ref))
            j += 1
        elif p.msb is not None and p.lsb is not None:
            for b in portmap.bits((p.msb, p.lsb)):
                out.append("  bufif1 %s_%d (%s[%d], 1'b0, 1'b0);" % (MARK, j, ref, b))
                j += 1
        else:
            msb, lsb = split_range(p.range_text)
            # block vamos_ams_hiz_<tag> must not collide with the scalar markers vamos_ams_hiz_<j>
            tag = p.verilog if _SIMPLE.match(p.verilog) else "esc%d" % p.index
            g = GENVAR + tag
            lo = "((%s) < (%s) ? (%s) : (%s))" % (msb, lsb, msb, lsb)
            hi = "((%s) > (%s) ? (%s) : (%s))" % (msb, lsb, msb, lsb)
            out.append("  genvar %s;" % g)
            out.append("  for (%s = %s; %s <= %s; %s = %s + 1) begin : %s_%s" % (g, lo, g, hi, g, g, MARK, tag))
            out.append("    bufif1 %s (%s[%s], 1'b0, 1'b0);" % (MARK, ref, g))
            out.append("  end")
    return out


def _decl(p: CutPort, type_text: str = "") -> str:
    if p.kind == REAL:
        return "  %s %s %s;" % (p.shell_dir, "wreal" if "wreal" in type_text.split() else "real", vid(p.verilog))
    return "  %s%s %s;" % (p.shell_dir, " " + p.range_text if p.range_text else "", vid(p.verilog))


def spice_shell(cell: CutCell) -> str:
    """`module <cell> (<ports>); input|output|inout [msb:lsb] <port>; markers; endmodule`."""
    lines = ["module %s (%s);" % (vid(cell.name), ", ".join(vid(p.verilog) for p in cell.ports))]
    lines += [_decl(p) for p in cell.ports]
    lines += markers(cell.ports)
    lines.append("endmodule")
    return "\n".join(lines) + "\n"


def header_shell(name: str, h: Header, ports: Optional[Sequence[CutPort]] = None,
                 extra: Sequence[str] = ()) -> str:
    """A multi-view shell: the user's header imports and parameter port list and the
    body's parameter (and needed function/typedef) items verbatim; every port a net
    (reg/logic/bit dropped; real ports stay real); then the markers of `ports` (by
    shell_dir; none when ports is None, as for the cell-set placeholder) and `extra`
    lines.  Non-ANSI, so port ranges may use the copied body parameters."""
    head = "module %s" % vid(name)
    if h.import_text:
        head += " " + h.import_text
    if h.param_text:
        head += " " + h.param_text
    lines = [head + " (%s);" % ", ".join(vid(p.name) for p in h.ports)]
    lines += ["  " + t for t in h.body_text]
    cps = list(ports) if ports is not None else [
        CutPort(k, p.name, p.kind, p.direction, p.direction, p.range_text, p.msb, p.lsb)
        for k, p in enumerate(h.ports)]
    types = {p.name: p.type_text for p in h.ports}
    lines += [_decl(p, types.get(p.verilog, "")) for p in cps]
    if ports is not None:
        lines += markers(cps)
    lines += ["  " + e for e in extra]
    lines.append("endmodule")
    return "\n".join(lines) + "\n"


# =============================================================================
# build (§5.1, step 7)
# =============================================================================

@dataclass
class InstRef:
    """A static instantiation of a cut cell in pp.v (structural, not elaborated)."""
    scope: str                    # enclosing module
    name: str                     # instance name
    line: int                     # pp line
    origin: str                   # the user's file:line
    params: Optional[str] = None  # "#(...)" text


Chain = Tuple[Tuple[str, str], ...]


class Direction(str):
    """A ShellResult.directions entry: "auto-><mode> (<why>)", the direction of an auto
    port and the reason of the first instance that decided it (a str, as it always was),
    plus by_chain: the step-2b reason of each elaborated instance that has one of its
    own, keyed by its static instance chain ((enclosing module, instance name), ...) from
    the top's instance down to the cell's.  The cut shows a node's own reason
    (cut._probe_reason); one cell port's instances can be made inputs by different
    wrapper instances on different variables."""
    by_chain: Dict[Chain, str]

    def __new__(cls, text: str, by_chain: Optional[Dict[Chain, str]] = None) -> "Direction":
        d = str.__new__(cls, text)
        d.by_chain = dict(by_chain or {})
        return d


@dataclass
class ShellResult:
    path: str                                                 # ams/pp.v
    text: str = ""
    cells: Dict[str, CutCell] = field(default_factory=dict)   # by Verilog spelling: SPICE-only, then multi-view
    spice_only: List[str] = field(default_factory=list)
    multi_view: List[str] = field(default_factory=list)
    headers: Dict[str, Header] = field(default_factory=dict)  # multi-view cells
    shell_lines: Dict[str, Tuple[int, int]] = field(default_factory=dict)   # cell -> first/last pp.v line
    directions: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # cell -> auto port -> "auto->input|output|inout (<why>)", for the IE report; an entry
    # that step 2b made an input is a Direction, with each instance's own reason
    instances: Dict[str, List[InstRef]] = field(default_factory=dict)
    removed: Dict[str, List[str]] = field(default_factory=dict)
    # SPICE-only cell -> SPICE ports port_connect'ed away from its shell (with or without -inst)
    notes: List[Note] = field(default_factory=list)


def build(pp: PP, top: str, cfg: AmsConfig, nl: Netlist, job: Optional[Job] = None,
          cellset: Optional[CellSet] = None, out_path: Optional[str] = None) -> ShellResult:
    """Cut cells, shells and ams/pp.v (§5.1, §5.8).

    cellset: a find_cells() result to reuse (computed when None).  out_path:
    default <dir of pp.path>/pp.v, i.e. layout.pp(daidir) when pp.path is
    layout.pp_orig(daidir).  `job` is accepted for the §5.8 signature.  Raises
    NoteError with every note when any is an error; otherwise the warnings and
    notes (the cell set's included) are in ShellResult.notes.
    """
    cs = case_sensitive(pp, cfg)
    cset = cellset or find_cells(pp, top, cfg, nl)
    notes: List[Note] = list(cset.notes)
    if has_errors(notes):
        raise NoteError(notes)
    out_path = out_path or os.path.join(os.path.dirname(pp.path), "pp.v")
    subs = nl.subckts()
    masked = [(d.start, d.end) for n in cset.multi_view for d in pp.modules[n]]
    insts = [x for x in instantiations(pp) if not any(a <= x.start < b for a, b in masked)]
    res = ShellResult(path=out_path, spice_only=list(cset.spice_only), multi_view=list(cset.multi_view))
    for name in cset.spice_only + cset.multi_view:
        res.instances[name] = [InstRef(x.scope, x.name, x.line, pp.origin(x.line), x.params)
                               for x in insts if x.module == name]

    for name in cset.spice_only:
        notes += _spice_cell(pp, res, name, subs[cset.subckts[name]], cfg, nl, insts, cs)
    for name in cset.multi_view:
        notes += _multi_cell(pp, res, name, subs[cset.subckts[name]], cfg, nl, cs)
    for name, cell in res.cells.items():
        notes += _check_portmaps(cell, subs[cell.subckt], cfg, cs)
    if has_errors(notes):
        raise NoteError(notes)

    base = blank(pp.text, masked)
    if not base.endswith("\n"):
        base += "\n"
    p = pp.precision or "1ps"
    prelude = PRELUDE % (p, p)

    def compose() -> Tuple[str, Dict[str, Tuple[int, int]]]:
        parts = [base, prelude]
        line = base.count("\n") + prelude.count("\n") + 1
        spans: Dict[str, Tuple[int, int]] = {}
        for nm, c in res.cells.items():
            t = spice_shell(c) if c.view == "spice" else header_shell(nm, res.headers[nm], c.ports)
            n = t.count("\n")
            spans[nm] = (line, line + n - 1)
            line += n
            parts.append(t)
        loc.spans = spans
        return "".join(parts), spans

    loc = Locator(pp, base.count("\n") + 1)
    loc.hier = {x.name: x.module for x in insts if x.module in res.cells}
    notes += _direction_probe(pp, top, res, compose, loc, cset.lines)
    if has_errors(notes):
        raise NoteError(notes)
    text, res.shell_lines = compose()

    # exactly one definition of each cut cell is left: its shell
    final = PP(path=out_path, text=text)
    count: Dict[str, int] = {}
    ftoks = final.toks()
    for kw, i, _ in units(final):
        if kw in ("module", "macromodule"):
            j = i + 1
            if ftoks[j].text in ("automatic", "static"):
                j += 1
            count[ftoks[j].name] = count.get(ftoks[j].name, 0) + 1
    for name, cell in res.cells.items():
        if count.get(name, 0) != 1:
            notes.append(error(cell.origin, "cell %s: %d definitions in pp.v after masking (expected only "
                               "its shell)" % (name, count.get(name, 0))))
    if has_errors(notes):
        raise NoteError(notes)
    with open(out_path, "w") as fh:
        fh.write(text)
    res.text = text
    res.notes = notes
    return res


def _use_origin(cfg: AmsConfig, names: Sequence[str], cs: bool) -> str:
    st = _stmts_for(cfg, names, cs)
    return st[-1][1].origin if st else ""


def _spice_cell(pp: PP, res: ShellResult, name: str, sub: Subckt, cfg: AmsConfig, nl: Netlist,
                insts: Sequence[Inst], cs: bool) -> List[Note]:
    names = [name, sub.name]
    conns, removed, notes = _connects(cfg, names, sub, cs)
    res.removed[name] = sorted(removed)
    spell, sn = _spellings(insts, name, pp)
    notes += sn
    pm = default_portmap(cfg, names, cs)
    where = _use_origin(cfg, names, cs)
    renames: Dict[str, str] = {}
    for v, s in list(pm.explicit.items()):
        if s.lower() == portmap.OPEN or "[" in v or "[" in s:
            notes.append(error(where, "cell %s: port_map %s => %s: the Verilog ports of a SPICE-only cell are "
                               "its subckt ports; only whole-port renames v => s are supported" % (name, v, s)))
        else:
            renames[s.lower()] = spell.get(v.lower(), v)
    if pm.default == portmap.OPEN:
        notes.append(error(where, "cell %s: * => snps_open would leave every port of a SPICE-only cell open"
                           % name))
    elif pm.default != "snps_by_name":
        notes.append(note(where, "cell %s: * => %s has no effect on a SPICE-only cell (its ports follow the "
                          "subckt)" % (name, pm.default)))
    pm.default = "snps_by_name"
    for _, u, s in _stmts_for(cfg, names, cs):
        if u.insts and ((s and _lookup(nl, s) != sub.name) or u.port_map):
            notes.append(error(u.origin, "cell %s is SPICE-only: use_spice -inst cannot give it another subckt "
                               "or a port map (one shell per cell)" % name))
        elif u.insts:
            notes.append(note(u.origin, "use_spice -inst on SPICE-only cell %s: every instance is SPICE "
                              "already" % name))
    sp = dict(spell)
    sp.update(renames)
    ports, pn = spice_ports(name, sub, cfg, nl, removed, sp, cs)
    notes += pn
    known = {p.verilog.lower() for p in ports}
    for s_low, v in renames.items():
        if v.lower() not in known:
            notes.append(error(where, "cell %s: port_map %s => %s: subckt %s has no port or bus %s"
                               % (name, v, s_low, sub.name, s_low)))
    refs = res.instances.get(name, [])
    res.cells[name] = CutCell(name=name, view="spice", subckt=sub.name, ports=ports, portmap=pm,
                              connects=conns, origin=sub.origin or (refs[0].origin if refs else ""))
    for x in refs:
        if x.params:
            notes.append(error(x.origin, "parameter override on SPICE instance %s.%s is not passed to subckt %s"
                               % (x.scope, x.name, sub.name)))
    return notes


def _multi_cell(pp: PP, res: ShellResult, name: str, sub: Subckt, cfg: AmsConfig, nl: Netlist,
                cs: bool) -> List[Note]:
    h = module_header(pp, name)
    res.headers[name] = h
    names = [name, sub.name]
    conns, _removed, notes = _connects(cfg, names, sub, cs)
    ports = [CutPort(k, p.name, p.kind, p.direction, p.direction, p.range_text, p.msb, p.lsb)
             for k, p in enumerate(h.ports)]
    res.cells[name] = CutCell(name=name, view="multi", subckt=sub.name, ports=ports,
                              portmap=default_portmap(cfg, names, cs), connects=conns, params=h.defaults(),
                              origin=h.origin)
    for pd in cfg.port_dirs.values():
        if globs.match(pd.cell, name, cs) or globs.match(pd.cell, sub.name, cs):
            notes.append(note(pd.origin, "port_dir -cell %s ignored: the Verilog view of %s declares its "
                              "port directions" % (pd.cell, name)))
    for _, u, s in _stmts_for(cfg, names, cs):
        if s and _lookup(nl, s) is None:
            notes.append(error(u.origin, "use_spice -cell %s:%s: no subckt %s in the SPICE netlists"
                               % (name, s, s)))
    return notes


def _check_portmaps(cell: CutCell, sub: Subckt, cfg: AmsConfig, cs: bool) -> List[Note]:
    """portmap.bind_bits on every port map of a cell whose ranges are all constant
    (parameterised ones are bound per variant by the cut)."""
    if any(p.range_text and p.msb is None for p in cell.ports):
        return []
    rngs = [(p.msb, p.lsb) if p.msb is not None else None for p in cell.ports]
    stmts = _stmts_for(cfg, [cell.name, sub.name], cs)
    maps: List[Optional[PortMap]] = []
    if cell.view == "spice" or not stmts or any(not u.insts for _, u, _ in stmts):
        maps.append(None)
    if cell.view == "multi":
        maps += [statement_portmap(u, cfg) for _, u, _ in stmts if u.insts and u.port_map]
    notes: List[Note] = []
    for pm in maps:
        try:
            portmap.bind_bits(cell, pm, rngs, sub.orig_ports or sub.ports)
        except NoteError as e:
            notes += e.notes
    return notes


# =============================================================================
# The direction probe
# =============================================================================

_PORT_OF = re.compile(r"^Port (\d+) \((.+?)\) of (\S+) is connected to (.*)$")
_COERCED = re.compile(r"^(?:input|output) port \S+ is coerced to inout\.?$")
_PROC = re.compile(r"Cannot perform procedural assignment to variable '([^']+)' because it is also "
                   r"continuously assigned")
_EXTRA_PARAM = re.compile(r"ignoring \d+ extra parameter override\(s\) for instance '([^']+)' of module "
                          r"'([^']+)'")
_PARAM_NF = re.compile(r"parameter `([^`]+)` not found in `([^`]+)`")
_COMPANIONS = ("cannot be driven by a primitive or continuous assignment", "cannot have multiple drivers",
               "expression not valid as argument to inout port", "expression not valid in assign l-value")
_LITERAL = re.compile(r"\d*\s*'[sS]?[bBoOdDhH][0-9a-fA-FxXzZ_?]+")


@dataclass
class PortHit:
    """One "Inout/Output port expression must support continuous assignment" report."""
    cell: str
    port: str
    line: int
    expr: str
    kind: str                     # 'inout' | 'output'


def classify(diags: Sequence[Diag]) -> Tuple[List[PortHit], List[Tuple[str, int]], List[Diag], List[Diag]]:
    """Probe messages -> (port hits, procedurally assigned variables (name, line),
    parameter-override messages, everything else).  The companion messages of a hit
    on the same line ("Variable 'x' cannot be driven by a primitive ...", "... multiple
    drivers", "expression not valid ...") are dropped."""
    hits: List[PortHit] = []
    procs: List[Tuple[str, int]] = []
    params: List[Diag] = []
    other: List[Diag] = []
    for dg in diags:
        if dg.message.startswith(("Inout port expression must support continuous assignment",
                                  "Output port expression must support a continuous assignment")):
            m = None
            for x in dg.extra:
                m = _PORT_OF.match(x)
                if m:
                    break
            if m:
                kind = "inout" if dg.message.startswith("Inout") else "output"
                hits.append(PortHit(m.group(3), m.group(2), dg.line, m.group(4), kind))
                continue
        pm = _PROC.search(dg.message)
        if pm:
            procs.append((pm.group(1), dg.line))
            continue
        if _EXTRA_PARAM.search(dg.message) or _PARAM_NF.search(dg.message):
            params.append(dg)
            continue
        other.append(dg)
    lines = {h.line for h in hits}
    other = [dg for dg in other if not (dg.line in lines and any(c in dg.message for c in _COMPANIONS))]
    return hits, procs, params, other


def vars_in(expr: str) -> Set[str]:
    """Variable names in a probe's connection expression ("{l1, r1}", "tb.rr", "v['sd2:'sd1]")."""
    e = _LITERAL.sub(" ", expr)
    return {m.group(0).split(".")[-1]
            for m in re.finditer(r"[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*)*", e)}


def _insts_at(pp: PP, cell: str, line: int) -> List[Inst]:
    """Static instantiations of `cell` whose text spans pp line `line`."""
    return [x for x in instantiations(pp)
            if x.module == cell and pp.line_of(x.start) <= line <= pp.line_of(max(x.end - 1, x.start))]


def _describe(pp: PP, cell: str, line: int) -> str:
    xs = _insts_at(pp, cell, line)
    if not xs:
        return "an instance of %s at %s" % (cell, pp.origin(line))
    return " / ".join("%s.%s (%s)" % (x.scope, x.name, pp.origin(x.line)) for x in xs)


def _port_hint(pp: PP, res: ShellResult, port: str, inst: str, line: int) -> str:
    """Why a named connection matches no shell port, when the shell explains it."""
    for x in instantiations(pp):
        if x.name != inst or x.module not in res.cells:
            continue
        if not (pp.line_of(x.start) <= line <= pp.line_of(max(x.end - 1, x.start))):
            continue
        cell = res.cells[x.module]
        if port.lower() in res.removed.get(cell.name, []):
            return "SPICE port %s of cell %s is port_connect'ed; drop it from the instantiation" % (port, cell.name)
        names = ", ".join(p.verilog for p in cell.ports)
        return "the ports of SPICE cell %s are: %s" % (cell.name, names or "none")
    return ""


def _line_offset(pp: PP, line: int) -> int:
    pp.line_of(0)
    starts = pp._lines or [0]
    return starts[min(max(line, 1), len(starts)) - 1]


# -- auto ports on input ports that iverilog does not coerce (probe step 2b) ----------

_HIER = re.compile(r"^(?:\\\S+ ?|[A-Za-z_][\w$]*)(?:\[[^\]]*\])*(?:\.(?:\\\S+ ?|[A-Za-z_][\w$]*)(?:\[[^\]]*\])*)+$")


class _InputPorts:
    """The structural scan behind wrapper_inputs, with its per-call caches."""

    def __init__(self, pp: PP, top: str, by_mod: Dict[str, List[Inst]]):
        self.pp, self.top, self.by_mod = pp, top, by_mod
        self.variables = {(d.module, d.name) for d in pp.variables}
        self._ports: Dict[str, Optional[List[Tuple[str, str]]]] = {}
        self._consts: Dict[str, Set[str]] = {}
        self._why: Dict[Tuple[str, str], Optional[str]] = {}
        self._active: Set[Tuple[str, str]] = set()

    def desc(self, x: Inst) -> str:
        return "%s.%s (%s)" % (x.scope, x.name, self.pp.origin(x.line))

    def ports(self, mod: str) -> Optional[List[Tuple[str, str]]]:
        if mod not in self._ports:
            self._ports[mod] = port_directions(self.pp, mod)
        return self._ports[mod]

    def kind(self, scope: str, expr: str) -> Optional[str]:
        """None when `expr`, an actual written in module `scope`, is a net that iverilog
        collapses into an input port (coercing the port to inout when something inside
        drives it); otherwise what it is ("variable clk", "constant P", "the expression ~w")."""
        e = expr.strip()
        ops = concat_operands(e)
        if ops is not None:
            for o in ops:
                k = self.kind(scope, o)
                if k:
                    return k
            return None
        ref = ident_ref(e)
        if ref is None:
            # a hierarchical name: a net elsewhere, as far as the scan can tell
            return None if _HIER.match(e) else "the expression %s" % e
        if (scope, ref[0]) in self.variables:
            return "variable %s" % ref[0]
        if scope not in self._consts:
            self._consts[scope] = constant_names(self.pp, scope)
        if ref[0] in self._consts[scope]:
            return "constant %s" % ref[0]
        return None

    def uncoerced(self, mod: str, port: str) -> Optional[str]:
        """Why input port `port` of module `mod` stays an input although something inside
        drives its net (iverilog coerces it to inout only for a collapsible net actual):
        "input port m.p, which <instance> connects to variable clk" (or leaves it
        unconnected, or is an instance array, or connects it to an input port one level
        up that stays an input).  None when `port` is not an input port of `mod`, or every
        static instance of `mod` coerces it."""
        key = (mod, port)
        if key in self._why:
            return self._why[key]
        if key in self._active:
            return None
        self._active.add(key)
        why = None
        ports = self.ports(mod)
        if ports is not None and dict(ports).get(port) == "input":
            here = "input port %s.%s" % (mod, port)
            index = [n for n, _ in ports].index(port)
            if mod == self.top:
                why = "%s of the top module" % here
            for y in ([] if why else self.by_mod.get(mod, [])):
                if y.array:
                    why = "%s of the instance array %s (iverilog coerces no port of an instance array)" \
                          % (here, self.desc(y))
                    break
                a = y.actual(port, index)
                if not a:
                    why = "%s, which %s leaves unconnected" % (here, self.desc(y))
                    break
                k = self.kind(y.scope, a)
                if k:
                    why = "%s, which %s connects to %s" % (here, self.desc(y), k)
                    break
                ref = ident_ref(a)
                up = self.uncoerced(y.scope, ref[0]) if ref is not None and not ref[1] and y.scope else None
                if up:
                    why = "%s, which %s connects to %s" % (here, self.desc(y), up)
                    break
        self._active.discard(key)
        self._why[key] = why
        return why

    def uncoerced_via(self, chain: Sequence[Inst], mod: str, port: str) -> Optional[str]:
        """uncoerced() for one elaborated instance of `mod`: `chain` holds the static
        instances from the top's down to that instance of `mod` (empty for the top), and
        the reason names that instance and its own actual, not the first instance of
        `mod` that keeps the port an input; None when this instance coerces it."""
        ports = self.ports(mod)
        if ports is None or dict(ports).get(port) != "input":
            return None
        here = "input port %s.%s" % (mod, port)
        if mod == self.top:
            return "%s of the top module" % here
        if not chain:
            return None
        y = chain[-1]
        if y.array:
            return "%s of the instance array %s (iverilog coerces no port of an instance array)" \
                   % (here, self.desc(y))
        a = y.actual(port, [n for n, _ in ports].index(port))
        if not a:
            return "%s, which %s leaves unconnected" % (here, self.desc(y))
        k = self.kind(y.scope, a)
        if k:
            return "%s, which %s connects to %s" % (here, self.desc(y), k)
        ref = ident_ref(a)
        up = self.uncoerced_via(chain[:-1], y.scope, ref[0]) if ref is not None and not ref[1] \
            and y.scope else None
        return "%s, which %s connects to %s" % (here, self.desc(y), up) if up else None


def _scan(pp: PP, res: ShellResult, top: str) -> Tuple[Dict[str, List[Inst]], Dict[str, List[Inst]],
                                                       "_InputPorts"]:
    """(instances by enclosing module, by instantiated module, the scan) of the modules
    instantiated under the top (step 2b's structural scan)."""
    masked = [(d.start, d.end) for n in res.multi_view for d in pp.modules.get(n, [])]
    insts = [x for x in instantiations(pp) if not any(a <= x.start < b for a, b in masked)]
    inside: Dict[str, List[Inst]] = {}
    for x in insts:
        inside.setdefault(x.scope, []).append(x)
    reach, todo = {top}, [top]                       # modules instantiated under the top, statically
    while todo:
        for x in inside.get(todo.pop(), []):
            if x.module not in reach:
                reach.add(x.module)
                todo.append(x.module)
    by_mod: Dict[str, List[Inst]] = {}
    for x in insts:
        if x.scope in reach:
            by_mod.setdefault(x.module, []).append(x)
    return inside, by_mod, _InputPorts(pp, top, by_mod)


def wrapper_input_chains(pp: PP, res: ShellResult, top: str,
                         live: Optional[Dict[str, List[int]]] = None
                         ) -> Dict[Tuple[str, str], Dict[Chain, str]]:
    """wrapper_inputs() per elaborated instance: (cell, port) -> {static instance chain
    ((enclosing module, instance name), ... from the top's instance down to the cell's):
    the reason}, for the instances whose own chain keeps the port an input.  A static
    instance in a module instantiated twice has two chains, each with its own wrapper
    instance and actual in its reason (wrapper_inputs names the first one for both)."""
    out: Dict[Tuple[str, str], Dict[Chain, str]] = {}
    autos = {n: [p for p in res.cells[n].ports if p.declared == AUTO and p.kind != REAL] for n in res.spice_only}
    if not any(autos.values()):
        return out
    inside, by_mod, scan = _scan(pp, res, top)
    cells = set(res.spice_only)

    def walk(mod: str, chain: List[Inst], seen: Set[str]) -> None:
        for x in inside.get(mod, []):
            if x.module in cells:
                if not autos.get(x.module) or (live is not None and x.line not in live.get(x.module, [])):
                    continue
                key = tuple((y.scope, y.name) for y in chain + [x])
                for p in autos[x.module]:
                    a = x.actual(p.verilog, p.index)
                    ref = ident_ref(a)
                    why = scan.uncoerced_via(chain, x.scope, ref[0]) if ref else None
                    if why:
                        via = "" if not ref[1] else "%s, a select of " % a
                        out.setdefault((x.module, p.verilog), {})[key] = \
                            "%s, connected to %s%s" % (scan.desc(x), via, why)
            elif x.module in by_mod and x.module not in seen:
                walk(x.module, chain + [x], seen | {x.module})

    walk(top, [], {top})
    return out


def wrapper_inputs(pp: PP, res: ShellResult, top: str,
                   live: Optional[Dict[str, List[int]]] = None) -> Dict[Tuple[str, str], List[str]]:
    """Auto ports of SPICE-only cells that need `input` (direction probe step 2b, in the
    module docstring): an instance connects the port to an input port of its enclosing
    module, whole or a bit or part of it, that iverilog keeps an input although the cell
    would drive its net, because an instance of that module connects it to a variable,
    an expression or a constant, leaves it unconnected or is an instance array (iverilog
    coerces an input port to inout only for a collapsible net actual), or it is an input
    of the top.  In Verilog the cell could drive only the module's side of such a port;
    as an input it has no marker and, unless the module drives the port's net itself,
    it is on the actual's net.  Left inout, its marker would make the iverilog core
    buffer the port (PB_<label>_<port> in the parent, translator patch T8): the cut
    joins the module's side to the actual's net one way, but stops with an error where
    the module also reads the port (module docstring, step 2b; cut.py, "port buffers").

    Returns (cell, port) -> one reason per such instance.  live: SPICE-only cell -> pp
    lines of its elaborated instances (CellSet.lines); other instances are skipped.
    The scan is structural: only instances in modules instantiated under the top count,
    but generate conditions are not evaluated, and an actual counts as a variable when
    the declaration scan recorded it as one (PP.variables: not user-typed variables);
    a variable it does not know leaves the port inout, behind the port buffer above.
    """
    out: Dict[Tuple[str, str], List[str]] = {}
    autos = {n: [p for p in res.cells[n].ports if p.declared == AUTO and p.kind != REAL] for n in res.spice_only}
    if not any(autos.values()):
        return out
    _inside, by_mod, scan = _scan(pp, res, top)
    for name in res.spice_only:
        for x in (by_mod.get(name, []) if autos[name] else []):
            if not x.scope or (live is not None and x.line not in live.get(name, [])):
                continue
            for p in autos[name]:
                a = x.actual(p.verilog, p.index)
                ref = ident_ref(a)
                why = scan.uncoerced(x.scope, ref[0]) if ref else None
                if why:
                    via = "" if not ref[1] else "%s, a select of " % a
                    out.setdefault((name, p.verilog), []).append(
                        "%s, connected to %s%s" % (scan.desc(x), via, why))
    return out


def _direction_probe(pp: PP, top: str, res: ShellResult,
                     compose: Callable[[], Tuple[str, Dict[str, Tuple[int, int]]]], loc: Locator,
                     live: Optional[Dict[str, List[int]]] = None) -> List[Note]:
    """Settle the auto ports' shell_dir (module docstring, steps 1-4); returns the notes.
    live: CellSet.lines, the elaborated instances of the SPICE-only cells (step 2b)."""
    notes: List[Note] = []
    auto = {(n, p.verilog): p for n, c in res.cells.items() for p in c.ports if p.declared == AUTO}
    for p in auto.values():
        p.shell_dir = INOUT
    spice = set(res.spice_only)

    def probe():
        return classify(run_tnull(top, compose()[0]))

    def param_notes(params: List[Diag]) -> List[Note]:
        out = []
        for dg in params:
            em = _EXTRA_PARAM.search(dg.message)
            nf = _PARAM_NF.search(dg.message)
            if em and em.group(2) in spice:
                out.append(error(loc.where(dg.line), "parameter override on SPICE instance %s "
                                 "is not passed to subckt %s" % (em.group(1), res.cells[em.group(2)].subckt)))
            elif nf and {x.module for x in instantiations(pp) if x.name == nf.group(2).split(".")[-1]} & spice:
                cells = {x.module for x in instantiations(pp) if x.name == nf.group(2).split(".")[-1]} & spice
                out.append(error(loc.where(dg.line), "parameter override (%s) on SPICE instance "
                                 "%s is not passed to subckt %s" % (nf.group(1), nf.group(2),
                                                                     ", ".join(res.cells[c].subckt for c in sorted(cells)))))
            elif dg.kind != "warning":
                out.append(loc.verbatim(dg))
        return out

    def other_notes(other: List[Diag]) -> List[Note]:
        out = []
        for dg in other:
            if dg.kind == "warning" and _COERCED.match(dg.message):
                continue                      # the markers' doing (VCS coerces ports silently)
            m = re.match(r"port ``(.+?)'' is not a port of (\S+?)\.?$", dg.message)
            if m and dg.kind == "error":
                hint = _port_hint(pp, res, m.group(1), m.group(2), dg.line)
                if hint:
                    n = loc.verbatim(dg)
                    out.append(error(n.origin, n.message + " (" + hint + ")"))
                    continue
            if dg.kind in ("error", "sorry"):
                out.append(loc.verbatim(dg))
            elif dg.kind == "warning" and any(re.search(r"(^|\W)%s(\W|$)" % re.escape(c), dg.message)
                                              for c in res.cells):
                out.append(loc.verbatim(dg))
        return out

    def default_dirs() -> None:
        for (n, pv), p in auto.items():
            if p.shell_dir == INOUT:
                res.directions.setdefault(n, {}).setdefault(pv, "auto->inout (%s)" % INOUT_WHY)

    def proc_notes(procs: List[Tuple[str, int]]) -> List[Note]:
        return [error(loc.where(ln), "variable %s is assigned procedurally and also driven through "
                      "a SPICE cell output; give port_dir input, or connect a wire" % v)
                for v, ln in sorted(set(procs), key=lambda x: (x[1], x[0]))]

    hits, procs, params, other = probe()
    cand = [h for h in hits if h.kind == "inout" and (h.cell, h.port) in auto]
    notes += param_notes(params)
    for h in hits:
        if not (h.kind == "inout" and (h.cell, h.port) in auto):
            notes.append(error(loc.where(h.line), "%s port %s of %s cannot be connected to %s "
                               "(a variable, constant or expression); connect a wire"
                               % (h.kind, h.port, h.cell, h.expr)))
    # every auto port is inout here, so a procedural conflict comes from a declared output
    notes += proc_notes(procs)
    if has_errors(notes) or any(dg.kind in ("error", "sorry") for dg in other):
        notes += other_notes(other)
        default_dirs()
        return notes

    need: Dict[Tuple[str, str], Dict[str, List[str]]] = {}
    if cand:
        # probe 2: the reported auto ports as outputs
        keys = {(h.cell, h.port) for h in cand}
        for k in keys:
            auto[k].shell_dir = OUTPUT
        hits2, procs2, _params2, _other2 = probe()
        proc_lines: Dict[str, List[int]] = {}
        for v, ln in procs2:
            proc_lines.setdefault(v, []).append(ln)
        out_fail = {(h.cell, h.port, h.line) for h in hits2 if h.kind == "output"}
        done: Set[Tuple[str, str, int, str]] = set()
        for h in cand:
            if (h.cell, h.port, h.line, h.expr) in done:
                continue
            done.add((h.cell, h.port, h.line, h.expr))
            scope = pp.module_at(_line_offset(pp, h.line))
            why = None
            if (h.cell, h.port, h.line) in out_fail:
                why = "connected to %s, which an output cannot drive" % h.expr
            else:
                for v in sorted(vars_in(h.expr)):
                    lines = proc_lines.get(v, [])
                    same = [ln for ln in lines if scope is None or pp.module_at(_line_offset(pp, ln)) is scope]
                    if same or lines:
                        why = "connected to %s; %s is assigned procedurally at %s" \
                              % (h.expr, v, pp.origin((same or lines)[0]))
                        break
            mode = INPUT if why else OUTPUT
            why = why or "connected to variable %s" % h.expr
            need.setdefault((h.cell, h.port), {}).setdefault(mode, []).append(
                "%s, %s" % (_describe(pp, h.cell, h.line), why))
    # step 2b: auto ports on input ports that iverilog does not coerce to inout
    step2b = wrapper_inputs(pp, res, top, live)
    for key, whys in step2b.items():
        if key in auto:
            need.setdefault(key, {}).setdefault(INPUT, []).extend(whys)
    # each elaborated instance's own step-2b reason, for the IE report (Direction)
    chains = wrapper_input_chains(pp, res, top, live) if step2b else {}
    if not need:
        notes += other_notes(other)           # probe 1 had the final directions
        default_dirs()
        return notes
    for (cell, port), modes in need.items():
        if len(modes) > 1:
            notes.append(error(res.cells[cell].origin, "auto port %s of SPICE cell %s needs different "
                               "directions: input at %s; output at %s; declare it with port_dir -cell %s "
                               "(input %s;) or (output %s;), or connect wires"
                               % (port, cell, "; ".join(modes[INPUT]), "; ".join(modes[OUTPUT]),
                                  res.cells[cell].subckt, port, port)))
            continue
        mode, whys = list(modes.items())[0]
        auto[(cell, port)].shell_dir = mode
        text = "auto->%s (%s)" % (mode, whys[0])
        own = chains.get((cell, port)) if mode == INPUT else None
        res.directions.setdefault(cell, {})[port] = Direction(text, own) if own else text
    default_dirs()
    if has_errors(notes):
        return notes

    # final probe with the decided directions: it must be clean
    hits3, procs3, params3, other3 = probe()
    notes += param_notes(params3)
    for h in hits3:
        d = auto[(h.cell, h.port)].shell_dir if (h.cell, h.port) in auto else "declared"
        notes.append(error(loc.where(h.line), "port %s of %s (%s) cannot be connected to %s; give "
                           "port_dir -cell %s, or connect a wire" % (h.port, h.cell, d, h.expr,
                                                                    res.cells[h.cell].subckt if h.cell in res.cells
                                                                    else h.cell)))
    notes += proc_notes(procs3)
    notes += other_notes(other3)
    return notes
