"""HSPICE netlists into the IR (docs/VAMOS_AMS_DESIGN.md §4.3).

parse() reads the choose netlists, then the netlist_commands fragment, and
returns an ir.Netlist in which every dialect question is resolved, so the
emitters only print: includes and .lib sections are flattened, duplicate
parameters resolved, ground aliases folded, HSPICE source and geometry
defaults filled in, every top-level parameter evaluated.

API
---
parse(paths, extra, cwd, opts) -> Netlist
    paths  the choose netlists (AmsConfig.choose.netlists), each the path as
           written or a (path, origin) pair, origin being the "file:line" of
           the choose command (AmsConfig.choose.origin, the file relative to
           cwd): a relative path is then also tried in that file's directory
           and a missing netlist is reported there
    extra  AmsConfig.netlist_lines, (line, origin) pairs: parsed as one more
           fragment after every netlist, never appended to a file, so no .end
           can swallow them
    cwd    the directory vcs runs in
    opts   ir.ParseOpts (case, parhier_local, synth_step/synth_stop, search)
    Every error is collected.  If there is one, NoteError is raised carrying
    every note of the parse in the order found (errors, warnings and notes);
    otherwise the notes are in Netlist.notes.
fold_ground(name) -> str
    '0' for a ground alias (0 gnd gnd! ground in any case, or a number that
    is all zeros such as 00), else name unchanged.  Every net name that enters
    the IR after parse() goes through it (§4.3.4 step 5).
left_out(nl) -> Dict[str, Note]
    The top-level subckts vamos cannot simulate and left out of the IR (see
    Libraries below), by IR name, each with the note saying why.  A cut cell
    that is not in nl.subckts() but is here should be reported with that note.
    Stored in Netlist.left_out (a real field, docs/VAMOS_SPECTRE_DESIGN.md §4.1
    item 13, so that dataclasses.replace keeps it).

The spectre personality's SPICE mode (docs/VAMOS_SPECTRE_DESIGN.md §3.13, §4.3;
phase-0 contracts, S1 implements them)
    Decl, FileRef, Resolver, Fragment      the two-phase types (§4.3, §10)
    declare_fragment(lines, opts) -> (List[Decl], List[FileRef])
        The first phase, SPICE side: the names a region declares and its file
        statements in order (spectre.py splices them in).
    parse_fragment(lines, cwd, opts, resolver) -> Fragment
        The second phase: _Parser.read([], lines) with opts.dialect ==
        "spectre-spice", resolving names against spectre.py's complete table;
        sets Instance.prim on every element (§4.1 item 7).
    va_modules(path, search=(), defines=()) -> (Dict[str, VaModule], List[str])
        The Verilog-A modules of a file (`include followed, the declaration
        preprocessor run, §3.1), by lower-cased name, and every file read; both
        parses store the result in Netlist.va_modules.

Lines and statements (§4.3.2)
-----------------------------
- Line 1 of the first choose netlist is the title (a note if it looks like a
  statement: it is not parsed).  Other netlists, includes and the fragment
  have no title line.  .title sets the title too.
- '*' as the first non-blank character makes a comment line.  Inline
  comments start, outside quotes, at '$' at the line start or after a blank
  (HSPICE's rule: 'net$1' is a name), at ';' anywhere and at '//' at the line
  start or after a blank.
- '+' as the first non-blank character continues the previous statement;
  comment and blank lines in between are skipped (sky130 puts '*' lines inside
  .model cards).  A line ending in '\\' continues on the next line ('\\\\'
  squeezes the white space between them).
- Fields are separated by blanks and commas; '...', "...", {...} and (...)
  group (a blank inside them does not split a field); 'name = value' with
  blanks around '=' is one assignment.
- Names are folded per opts.case (set_sim_case: lower | upper | sensitive);
  keywords, element letters, device and option parameter names and function
  names are always lowercase.  Netlist.spelling maps IR names to the spelling
  first seen (subckt names and ports are taken from their definition).
- Node names: an all-digit name loses its leading zeros (HSPICE: node 007 is
  node 7, 00 is ground); braces become brackets (HSPICE: a{3} is a[3]).  A net
  name with '.' is an error (HSPICE reserves it for <subckt>.<node>, 3-17); so
  is a net named <x>:<node> after an internal node of X instance x of its own
  body (VACASK and Xyce name that node x:<node> too and would merge the two).

Files (§4.3.1)
--------------
- A relative path of a choose netlist, .inc/.include/.incl, .lib or .hdl is
  tried against the cwd, then the directory of the file containing the
  reference, then opts.search and every .option search= directory seen so
  far.  The file containing a choose netlist is the control file its origin
  names (a bare path has none: cwd only); the netlist_commands fragment's
  references (.inc, .lib, .hdl, .option search) use the control file each
  line's origin names.  The first existing file wins; when another
  candidate is a different file, a note names the one used; when none
  exists the error lists every path tried.  $VAR, ${VAR} and ~ are
  expanded.  Resolved paths are absolute.
- .lib 'file' sec reads section sec (.lib sec ... .endl) of file; inside a
  section, '.lib other' calls section other of the same file.  Reading a
  file whole (a netlist or .include) skips its section definitions.
- Each (realpath, section) is expanded once per subckt scope; a repeat is a
  note.  A duplicate .subckt or .model name in one scope is an error naming
  both origins.
- .end ends the file it is in (text after it: one note); a .lib section
  cannot contain .end (error).

Parameters (§4.3.3)
-------------------
- .param name=value (also .parameter/.parameters) and .subckt header
  parameters; a top-level parameter is a Param in Netlist.body, before
  everything else; a subckt's header and body parameters are merged into
  Subckt.params.  User functions (.param f(a,b)='...') are inlined into every
  expression of their scope and below; they are not in the IR.
- Duplicates per scope: the last definition wins for every use, earlier ones
  included; one Param is kept, with a note naming both origins.
- PARHIER: .option parhier (default global).  Under global, a name defined
  at top level and also in a subckt (header or body) or as an X-line
  override is an error per site, unless opts.parhier_local (then a note).
  Netlist.parhier is "local" when the option says so or opts.parhier_local
  is set, else "global".
- Each scope is sorted topologically (a parameter after the ones it uses);
  a cycle is an error naming its parameters.
- Top-level parameters are evaluated in that order into Netlist.values.  One
  that depends on temper (directly or through another) stays symbolic; an
  unknown name or a numeric failure is an error.

Elements (§4.2 conventions; parameters keyed in lowercase)
---------------------------------------------------------
Values are expressions; a constant one is folded to a Num with HSPICE's
semantics ('2*1k' -> 2000.0, agauss(1,0.1,3) -> 1.0).  Number literals are read
correctly rounded (expr.number: '0.22u' is the double nearest 2.2e-7, as
ngspice reads it), with HSPICE's D exponent ('1.0D+3' is 1000, '2.5D' is 2.5
with the unit letter D).  v(), i() and time are allowed only in VOL=/CUR=/VALUE=,
hertz nowhere.  An element name repeated in one scope is an error.
R C L     n1 n2 [model] [value] [tc1 [tc2]] key=value...: value = R/C/L or the
          r=/c=/l= keyword (None for a model-only element); a model is taken
          when the field names a model in scope (HSPICE: before a parameter
          of the same name).  scale= is multiplied into the value.  A value
          that reads nodes, time or hertz, C Q=/POLY, L NT=/POLY: unsupported.
K         l1 l2 [k=]coupling -> ctrl [l1, l2], value.
V I       n+ n- [[dc] v] [dc=v] [ac mag [phase]] [ac=mag[,phase]] [function]
          [m= (I only)]; functions PULSE/PU, SIN, EXP, PWL, PL (value-time
          pairs), with or without parentheses.  Source.args (§4.3.8, every
          field filled; TSTEP/TSTOP from the first .tran, else opts.synth_*):
            pulse v1 v2 td tr tf pw [per]: td omitted -> 0, negative -> 0
                  (HSPICE); tr/tf omitted or 0 -> TSTEP (a non-constant edge
                  becomes '(x==0 ? TSTEP : x)'); pw omitted -> TSTOP; per
                  omitted -> no 'per' key (aperiodic); per <= tr+tf+pw: error
            sin   vo va freq td theta phase: freq omitted -> 1/TSTOP; td,
                  theta, phase -> 0
            exp   v1 v2 td1 tau1 td2 tau2: td1 -> 0; tau1/tau2 -> TSTEP;
                  td2 -> td1+TSTEP; td2 <= td1 or tau <= 0: error
            pwl   td (TD=, default 0) and Source.points: equal or decreasing
                  times are an error; R= is an error (v1); when the first
                  time is after 0, the point (0, DC or 0) is put first
                  (HSPICE uses the DC value at time zero)
          Source.dc: the DC value when one is written, else None.
          Source.ac: (magnitude, phase), phase 0 when omitted.
E G       linear: n+ n- [VCVS|VCCS] in+ in- gain -> nodes [p, n, cp, cn], value.
          VOL= (E), CUR= (G), VALUE= -> kind 'b', expr, expr_kind 'v' | 'i'.
          scale= is multiplied in; m= (G only) is a parameter; MAX/MIN, TC,
          ABS, IC and POLY/PWL/DELAY/LAPLACE/OPAMP/TRANSFORMER/... forms are
          unsupported.
F H       n+ n- [CCCS|CCVS] vname gain -> ctrl [vname], value; vname must be
          a V source of the same scope.
D         a c model [area] [pj]: value = area (also AREA=); params pj w l ...
Q         c b e [s] model [area]: value = area; substrate optional.
J         d g s model [area]: value = area.
M         d g s b model [l w] key=value...: positional L W (W L under .option
          wl); a missing l or w gets DEFL/DEFW (HSPICE default 1e-4, or the
          .option value), with a note.  A bulk node is required.
X         nodes... subckt [params:] p=v... [m=]: kind 'x', params = overrides
          (the subckt name may also follow the parameters, as ngspice libraries
          such as sky130 write it).  The node count must match the subckt's
          ports.  A master that is a .hdl Verilog-A module gives kind 'y' with
          master = the module name as the .va file spells it; a master that is
          a .model card of such a module gives the same, with the card's
          parameters merged under the instance's (the emitters write one card
          per Verilog-A instance).
OFF and IC= on devices are ignored with a warning.  Any other element letter
(HSPICE B S W P T U ...) is unsupported.

Models (§4.3.6, the parse side)
-------------------------------
.model name type [(] key=value... [)]: Model.kind is the type lowercased (one
of ir.MODEL_KINDS; a card whose type is a .hdl Verilog-A module is not a Model
but is folded into the X lines that name it, see Elements); level is the
evaluated 'level' (None if absent or not constant); every parameter stays in
params, level and version included (netlist/tables.py decides).  A card
<base>.<k> carrying all of lmin lmax wmin wmax is a bin: base and bin_index
set; instances name the base.  Any other type (AMP CORE OPT PLOT U W SP ...)
or a card that cannot be read is left out with a note; using it is an error.

Ground pass (§4.3.4)
--------------------
- 0 gnd gnd! ground on a terminal (and inside v(), on the last hierarchical
  component) become '0'.
- Both output terminals '0': a V source with DC 0 and no AC or wave, R C L I
  F G D, a G CUR=, and an E/H/E VOL= of value 0 are dropped with a note;
  another V, E or H is an error; an F, H or K naming a dropped element, an
  i() of one in an expression and a probe of one are errors.
- A subckt port that is an alias is removed from Subckt.ports and its index
  kept in gnd_ports (Subckt.orig_ports keeps every port as written, case
  folded); user X lines drop the actual at that index, which must be ground.
- .global loses the aliases.
- A subckt port named after a .global net that an X line binds to another
  net is an error suggesting port_connect.

Options (§4.3.5)
----------------
Netlist.options keeps: 'scale' (Num), 'tnom' (Num) and 'spice' (Num 1.0) when
given, 'dcap' (Num 1, 2 or 3: the junction capacitance equations of D, Q and J
cards; any other value is an error), and the tolerances 'reltol' 'abstol'
'vntol' 'gmin' (Num) and 'method' (Str, lowercased); the emitters map those
where both engines have an equivalent and note the rest.  spice and dcap select
HSPICE's model defaults (tables.hspice_card: under .option spice DCAP is 1 and
the MOS CAPOP=0 / LD / NSUB and BJT MJS defaults are SPICE's).
Netlist.temp / tnom are always set: .temp (or .option temp) and .option tnom
as evaluated floats, else 25 (27 under .option spice).  A repeated option
takes the last value (HSPICE), with a note when the values differ.  scalm
and geoshrink other than 1, aspec other than 0: error.  wl: positional W L.  defl defw defad
defas defpd defps defnrd defnrs: MOSFET defaults filled into instances.
delmax, rmax: the tran maxstep (Control statements).  dvdt, lvltim and
accurate select HSPICE's timestep algorithm, which is not modelled (note);
they count only for RMAX.  parhier, search: above.  Output, listing and
other accuracy options: note.  gshunt cshunt and unknown keys: warning.

Control statements
------------------
.tran tincr1 tstop1 [tincr2 tstop2 ...] [tstart] [START=t] [UIC] (HSPICE:
further positional pairs are later intervals; the run goes to the last tstop
with the first increment, a note): Analysis('tran', {step, stop, start, uic,
maxstep}), evaluated floats.  maxstep is HSPICE's maximum internal timestep,
which the engines would not apply by themselves (VACASK bounds its step by
(stop-start)/50 only, Xyce by stop/10): .option delmax when given (a
positive number); else min(TSTOP/50, RMAX*TSTEP), with a note naming the
value (Star-HSPICE manual 2001.2: DELMAX 9-43, the bound 11-26 and 11-36,
RMAX 9-46, DVDT 9-44, LVLTIM 9-48, METHOD 9-49, ACCURATE 11-27, DVDT=3
11-35).  TSTEP is the smallest increment of the .tran.  RMAX: the options
apply in order, the last setting winning (rmax, dvdt, lvltim; METHOD=GEAR
sets LVLTIM=2; ACCURATE sets DVDT=2, LVLTIM=3 and RMAX=2; DVDT=3 sets
LVLTIM=1 and RMAX=2); RMAX never set is 5 when DVDT=4 and LVLTIM=1
(HSPICE's defaults), else 2.  Without a .tran (deck.py synthesises the
analysis, maxstep from --vamos-analog-maxstep) .option delmax is a warning
and rmax a note.  .op .dc .ac: Analysis(kind, {}) (deck.py warns that they
are ignored); other analyses (.noise .four ...): warning, not recorded.  .print/.probe [tran|dc|ac|op] v(n) v(a,b) i(e) v(*): probes
(analysis, 'v'|'i', target) with target 'a,b' for a difference and '*' for
every node; other output functions are ignored with a warning.  .ic,
.dcvolt and .nodeset v(n)=value: evaluated floats.  .temp: Netlist.temp
(several values: the first, warning).  .hdl "f.va": Netlist.hdl.  .measure:
warning, ignored.  .alter .data .if .connect .load .vec .stim .malias .alias
.del and unknown dot-commands: error.  .protect/.unprotect: ignored.

Libraries: deferred errors
--------------------------
A model library defines far more than a deck uses (sky130's tt corner has
voltage-dependent resistors and Q= varactors that vamos cannot simulate).  So
inside a subckt every problem with an element line or a model card (an
unsupported construct, an unknown model or subckt, a malformed element) is
deferred: the subckt is left out of the IR with a note, and so is every
subckt instantiating it.  Instantiating a left-out subckt from a kept
element is the error.  At top level such problems are errors at once; a bad
model card is left out (a note) and using it is the error.
"""

from __future__ import annotations

import heapq
import math
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Set, Tuple, Union

from vamos.netlist import expr as E
from vamos.netlist.expr_ast import Binary, Call, Expr, Name, Num, Str, Ternary
from vamos.netlist.ir import (MODEL_KINDS, Analysis, Instance, Item, Model, Netlist, Param, ParseOpts,
                              Source, Subckt, VaModule, flat_items)
from vamos.notes import ERROR, NOTE, WARNING, Note, NoteError, error, note, warning

GROUND_ALIASES = ("0", "gnd", "gnd!", "ground")
_GROUND = frozenset(GROUND_ALIASES)

HSPICE_DEFL = 1e-4          # .option defl / defw defaults (Star-HSPICE manual)
HSPICE_DEFW = 1e-4
TEMP_DEFAULT = 25.0         # HSPICE temp and tnom; 27 under .option spice
TEMP_SPICE = 27.0
# HSPICE's maximum internal timestep without .option delmax: min(TSTOP/TRAN_MIN_POINTS,
# RMAX*TSTEP); RMAX defaults to 5 under DVDT=4 and LVLTIM=1 (HSPICE's defaults), else 2
# (Star-HSPICE manual 2001.2, 9-46, 11-23, 11-36).
TRAN_MIN_POINTS = 50.0
RMAX_DVDT4 = 5.0
RMAX_OTHER = 2.0

# A choose netlist: the path as written, or (path, origin of the choose command).
NetlistRef = Union[str, Tuple[str, str]]


def fold_ground(name: str) -> str:
    """'0' for a ground alias (0 gnd gnd! ground, any case; 00), else name unchanged."""
    low = name.lower()
    if low in _GROUND or (low.isdigit() and not low.strip("0")):
        return "0"
    return name


def _no_period(text: str) -> None:
    """A net name with '.': HSPICE reserves the period as the separator of <subckt>.<node> (Star-HSPICE
    2001.2, 3-17), and vamos reads every v(a.b) as that reference, so such a net cannot be probed or
    given an .ic; refused (_Defer: an error at top level, the subckt left out inside one)."""
    if "." in text and not text.replace(".", "").isdigit():
        raise _Defer("node name %r contains '.', which HSPICE reserves as the hierarchy separator "
                     "(<subckt>.<node>)" % text)


def _colon_clashes(nl: Netlist) -> List[Tuple[str, str]]:
    """(origin, message) for every net whose name is <instance>:<node> of an X instance of its own body
    and an internal node of that subckt (deeper: <x1>:<x2>:<node>): VACASK and Xyce both name a subckt
    instance's internal nodes <instance>:<node>, so the two would silently be one node, where HSPICE
    (hierarchy separator '.') keeps them apart.  Names are compared case-insensitively (Xyce)."""
    out: List[Tuple[str, str]] = []
    glob = {g.lower() for g in nl.globals}

    def defs_of(items: Sequence[object], defs: Dict[str, Subckt]) -> Dict[str, Subckt]:
        d = dict(defs)
        d.update({it.name.lower(): it for it in flat_items(items) if isinstance(it, Subckt)})
        return d

    def xs_of(items: Sequence[object]) -> Dict[str, Instance]:
        return {it.name.lower(): it for it in flat_items(items) if isinstance(it, Instance) and it.kind == "x"}

    def internal(sub: Subckt) -> Set[str]:
        # a net used only inside an if branch counts too (ir.flat_items, §4.5 item 1)
        nodes = {n.lower() for it in flat_items(sub.body) if isinstance(it, Instance) for n in it.nodes}
        return nodes - {p.lower() for p in sub.ports} - glob - {"0"}

    def clash(name: str, items: Sequence[object], defs: Dict[str, Subckt]) -> Optional[str]:
        head, rest = name.split(":", 1)
        x = xs_of(items).get(head)
        sub = defs.get((x.master or "").lower()) if x is not None else None
        if sub is None:
            return None
        if ":" in rest:
            return x.name if clash(rest, sub.body, defs_of(sub.body, defs)) else None
        return x.name if rest in internal(sub) else None

    def visit(items: Sequence[object], defs: Dict[str, Subckt]) -> None:
        defs = defs_of(items, defs)
        seen: Set[str] = set()
        for it in flat_items(items):
            if isinstance(it, Subckt):
                visit(it.body, defs)
            elif isinstance(it, Instance):
                for n in it.nodes:
                    low = n.lower()
                    if ":" not in low or low in seen:
                        continue
                    who = clash(low, items, defs)
                    if who is not None:
                        seen.add(low)
                        out.append((it.origin, "node %s: VACASK and Xyce also call the internal node %s of "
                                               "instance %s '%s', so the two would be one node (HSPICE keeps "
                                               "them apart: its hierarchy separator is '.'); rename the node"
                                    % (n, low.split(":", 1)[1], who, n)))
    visit(nl.body, {})
    return out


def left_out(nl: Netlist) -> Dict[str, Note]:
    """Top-level subckts left out because vamos cannot simulate them, with the reason."""
    return dict(nl.left_out)


# -- the spectre personality's SPICE mode: phase-0 contracts (VAMOS_SPECTRE_DESIGN.md §4.3, §10) ---------

@dataclass
class Decl:
    """A name the first phase declares (§4.3, two phases)."""
    kind: str                                     # subckt | model | param
    name: str                                     # IR name
    master: str = ""                              # model: the Spectre master (Model.prim)
    ports: Optional[int] = None                   # subckt: the port count
    params: List[str] = field(default_factory=list)   # subckt: the header parameter names
    origin: str = ""


@dataclass
class FileRef:
    """A SPICE file statement the first phase found (§3.1, §4.3 Files)."""
    keyword: str                                  # .include | .inc | .incl | .lib | .hdl
    path: str                                     # as written
    section: Optional[str] = None                 # .lib's section
    subckt: str = ""                              # the enclosing .subckt's IR name; "" at the top level
    origin: str = ""


class Resolver(Protocol):
    """What parse_fragment resolves names against: spectre.py's complete first-phase table of every
    subckt, model, parameter and Verilog-A module of every file in both languages (§4.3)."""

    def subckt(self, name: str) -> Optional[Decl]: ...

    def model(self, name: str) -> Optional[Decl]: ...

    def param(self, name: str) -> Optional[Decl]: ...

    def va_module(self, name: str) -> Optional[VaModule]: ...


@dataclass
class Fragment:
    """What parse_fragment returns for one SPICE-mode region (§4.3)."""
    items: List[Item] = field(default_factory=list)   # raw Params, Models with prim, Subckts, Instances; source order
    controls: List[Tuple[str, List[Tuple[Optional[str], str]], str]] = field(default_factory=list)
                                                  # (keyword, spice.py's (key, text) fields, origin)
    notes: List[Note] = field(default_factory=list)
    left_out: Dict[str, Note] = field(default_factory=dict)
    pending: List[Tuple[str, str, str]] = field(default_factory=list)   # (kind, name, origin): defined nowhere


# =============================================================================
# Lexical layer: physical lines -> statements -> fields
# =============================================================================

class _LexError(ValueError):
    pass


class _Stmt:
    """One logical line: comments removed, continuations joined."""
    __slots__ = ("text", "origin", "kw", "_fields")

    def __init__(self, text: str, origin: str):
        self.text = text.strip()
        self.origin = origin
        self.kw = _keyword(self.text)
        self._fields: Optional[List[Tuple[Optional[str], str]]] = None

    def fields(self) -> List[Tuple[Optional[str], str]]:
        if self._fields is None:
            self._fields = _lex(self.text)
        return self._fields


_DOT_KW = re.compile(r"\.[A-Za-z_][A-Za-z0-9_]*")


def _keyword(text: str) -> str:
    """'.param' for a dot-command (lowercased), else the element letter (lowercased)."""
    if text.startswith("."):
        m = _DOT_KW.match(text)
        return m.group(0).lower() if m else "."
    return text[:1].lower()


def _strip_comment(text: str, quote: str) -> Tuple[str, str]:
    """text without its inline comment, and the quote still open at its end."""
    if not quote and "$" not in text and ";" not in text and "/" not in text and \
            "'" not in text and '"' not in text:
        return text, ""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if quote:
            if c == quote:
                quote = ""
        elif c == "'" or c == '"':
            quote = c
        elif c == ";":
            return text[:i], ""
        elif c == "$" and (i == 0 or text[i - 1] in " \t"):
            return text[:i], ""
        elif c == "/" and text.startswith("//", i) and (i == 0 or text[i - 1] in " \t"):
            return text[:i], ""
        i += 1
    return text, quote


def _star_comment(text: str) -> str:
    """spectre-spice (VAMOS_SPECTRE_DESIGN.md §4.3): an element line without its '*' comment.  "The SPICE
    Reader treats the * character as a math function if found inside of an expression and as a comment if
    found outside of an expression" (UG p.52): a '*' that starts a field after the element's bare positional
    fields begins a comment; never inside quotes, braces or parentheses, and never after a key=value field,
    whose value it may continue (the line is then read as it is, and an error stays one)."""
    if "*" not in text:
        return text
    quote, depth = "", 0
    for i, c in enumerate(text):
        if quote:
            if c == quote:
                quote = ""
            continue
        if c in "'\"":
            quote = c
        elif c in "({":
            depth += 1
        elif c in ")}":
            depth -= 1
        elif c == ";" or ((c == "$" or text.startswith("//", i)) and (i == 0 or text[i - 1] in " \t")):
            return text                             # an earlier comment, _strip_comment's
        elif c == "*" and depth == 0 and i > 0 and text[i - 1] in " \t":
            prefix = text[:i].rstrip()
            try:
                fields = _lex(prefix)
            except _LexError:
                return text
            if len(fields) >= 2 and fields[-1][0] is None and not prefix.endswith("="):
                return prefix
            return text
    return text


def _file_of(origin: str) -> str:
    """The file part of a 'file:line' origin ('' when there is none)."""
    name, sep, line = origin.rpartition(":")
    return name if sep and line.strip().isdigit() else origin


def _logical(phys: Sequence[Tuple[str, str]], dialect: str = "hspice") -> Tuple[List[_Stmt], List[Note]]:
    """Statements from (line, origin) pairs: comments dropped, continuations joined.

    dialect "spectre-spice" (VAMOS_SPECTRE_DESIGN.md §3.13, §4.3): a continuation never joins a
    statement that started in another file (an error naming both origins), and a '* spectre:'
    comment, which carries Spectre text hidden from SPICE, is an error (v1 does not read it)."""
    sp = dialect == "spectre-spice"
    stmts: List[_Stmt] = []
    notes: List[Note] = []
    parts: Optional[List[str]] = None
    first = ""
    quote = ""
    k, n = 0, len(phys)
    while k < n:
        line, origin = phys[k]
        k += 1
        line = line.rstrip("\r\n")
        # '\' / '\\' at the end of a line continue it on the next physical line
        while k < n:
            s = line.rstrip()
            if not s.endswith("\\") or s.lstrip().startswith("*"):
                break
            if not _strip_comment(s, quote)[0].rstrip().endswith("\\"):
                break
            if sp and _file_of(phys[k][1]) != _file_of(origin):
                notes.append(error(origin, "the statement continues with '\\' into another file (%s): a "
                                   "statement cannot continue across a file boundary (UG p.73)" % phys[k][1]))
                line = s[:-1]
                break
            nxt = phys[k][0].rstrip("\r\n")
            k += 1
            line = s[:-2].rstrip() + nxt.lstrip() if s.endswith("\\\\") else s[:-1] + nxt
        t = line.lstrip()
        if not t or t[0] == "*":
            if sp and t[1:].lstrip().lower().startswith("spectre:"):
                notes.append(error(origin, "'* spectre:' carries Spectre text hidden from SPICE, which v1 "
                                   "does not read; the element or parameter it carries would be lost (§3.13)"))
            continue
        if t[0] == "+":
            body, quote = _strip_comment(t[1:], quote)
            if parts is None:
                notes.append(error(origin, "continuation line '+' without a statement before it"))
                continue
            if sp and _file_of(origin) != _file_of(first):
                notes.append(error(origin, "continuation line '+' would continue the statement at %s, which "
                                   "is in another file: a statement cannot continue across a file boundary "
                                   "(UG p.73)" % first))
                continue
            parts.append(body)
            continue
        if parts is not None:
            stmts.append(_Stmt(" ".join(parts), first))
        if sp and t[0] != ".":
            t = _star_comment(t)
        body, quote = _strip_comment(t, "")
        parts, first = [body], origin
    if parts is not None:
        stmts.append(_Stmt(" ".join(parts), first))
    return [s for s in stmts if s.text], notes


_PLAIN = re.compile(r"[^\s'\"{}()=,]+")
_SPACE = re.compile(r"\s+")


def _brace_end(text: str, i: int) -> int:
    depth = 0
    for j in range(i, len(text)):
        c = text[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j
    raise _LexError("unclosed '{'")


def _lex(text: str) -> List[Tuple[Optional[str], str]]:
    """Fields of a statement: (key, text) for 'key=text', (None, text) for a bare field."""
    out: List[Tuple[Optional[str], str]] = []
    cur: List[str] = []
    key: Optional[str] = None
    depth = 0
    pos, n = 0, len(text)
    while pos < n:
        c = text[pos]
        if c == "'" or c == '"':
            end = text.find(c, pos + 1)
            if end < 0:
                raise _LexError("unterminated %s quote" % c)
            cur.append(text[pos:end + 1])
            pos = end + 1
        elif c == "{":
            end = _brace_end(text, pos)
            cur.append(text[pos:end + 1])
            pos = end + 1
        elif c == "}":
            raise _LexError("unbalanced '}'")
        elif c == "(":
            depth += 1
            cur.append(c)
            pos += 1
        elif c == ")":
            if not depth:
                raise _LexError("unbalanced ')'")
            depth -= 1
            cur.append(c)
            pos += 1
        elif c in " \t\r\n\f\v":
            m = _SPACE.match(text, pos)
            if depth:
                cur.append(" ")
            elif cur:
                out.append((key, "".join(cur)))
                cur, key = [], None
            pos = m.end()
        elif c == ",":
            if depth:
                cur.append(c)
            elif cur:
                out.append((key, "".join(cur)))
                cur, key = [], None
            pos += 1
        elif c == "=":
            prev = cur[-1][-1:] if cur else ""
            if depth or text.startswith("=", pos + 1) or prev in ("<", ">", "!", "="):
                cur.append(c)
            elif cur:
                if key is not None:
                    raise _LexError("unexpected '=' in %s=%s=" % (key, "".join(cur)))
                key, cur = "".join(cur), []
            elif key is None and out and out[-1][0] is None:
                key = out.pop()[1]
            else:
                raise _LexError("'=' without a name before it")
            pos += 1
        else:
            m = _PLAIN.match(text, pos)
            cur.append(m.group(0))
            pos = m.end()
    if depth:
        raise _LexError("unclosed '('")
    if cur or key is not None:
        out.append((key, "".join(cur)))
    return out


def _path_args(text: str) -> List[str]:
    """The arguments of .inc/.lib/.hdl: quoted or blank-separated words."""
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t,":
            i += 1
        elif c in "'\"":
            end = text.find(c, i + 1)
            if end < 0:
                raise _LexError("unterminated %s quote" % c)
            out.append(text[i + 1:end])
            i = end + 1
        else:
            j = i
            while j < n and text[j] not in " \t,":
                j += 1
            out.append(text[i:j])
            i = j
    return out


class _File:
    """A file read into statements, with its .lib section index."""

    def __init__(self, path: str, stmts: List[_Stmt]):
        self.path = path
        self.stmts = stmts
        self.sections: Dict[str, Tuple[int, int, str]] = {}   # name -> (first, end, origin)
        self.skip: Dict[int, int] = {}                        # .lib index -> .endl index (whole-file reads)
        self.notes: List[Note] = []
        opened: Optional[Tuple[str, int, str]] = None
        for idx, st in enumerate(stmts):
            if st.kw == ".lib":
                try:
                    args = _path_args(st.text[4:])
                except _LexError:
                    continue
                if len(args) == 1 and opened is None:
                    opened = (args[0].lower(), idx, st.origin)
            elif st.kw == ".endl" and opened is not None:
                self._close(opened, idx)
                opened = None
        if opened is not None:
            self.notes.append(error(opened[2], ".lib %s has no .endl" % opened[0]))
            self._close(opened, len(stmts))

    def _close(self, opened: Tuple[str, int, str], end: int) -> None:
        name, start, origin = opened
        self.skip[start] = end
        if name in self.sections:
            self.notes.append(note(origin, "section %s is defined again (first at %s); the first "
                                   "definition is used" % (name, self.sections[name][2])))
        else:
            self.sections[name] = (start + 1, end, origin)


# =============================================================================
# Scopes
# =============================================================================

class _Defer(Exception):
    """A problem with an element or model card: an error at top level, deferred in a subckt."""


class _Scope:
    """The top level or one .subckt definition, with what passes 1 and 2 learn about it."""

    def __init__(self, name: Optional[str], parent: Optional["_Scope"], stmt: Optional[_Stmt]):
        self.name = name                    # IR name; None at top level
        self.parent = parent
        self.stmt = stmt                    # the .subckt statement
        self.items: List[object] = []       # _Stmt and child _Scope, in source order
        self.orig_ports: List[str] = []
        self.ports: List[str] = []
        self.gnd_ports: List[int] = []
        self.spelling: Dict[str, str] = {}  # folded port -> header spelling (Subckt.spelling)
        self.subckts: Dict[str, _Scope] = {}
        self.models: Dict[str, Model] = {}
        self.bins: Dict[str, List[Model]] = {}
        self.bad_models: Dict[str, str] = {}
        self.va_cards: Dict[str, Tuple[str, Dict[str, Expr], str]] = {}   # card -> (module, params, origin)
        self.model_at: Dict[int, Model] = {}                        # id(.model statement) -> Model
        self.funcs_cache: Optional[Dict[str, Tuple[Sequence[str], Expr]]] = None
        self.raw_params: List[Tuple[str, str, str]] = []           # (name, value text, origin)
        self.header_params: Set[str] = set()                        # the .subckt line's parameter names
        self.params: List[Tuple[str, Expr, str]] = []               # parsed, in source order
        self.funcs: Dict[str, Tuple[Tuple[str, ...], Expr, str]] = {}
        self.local: Set[str] = set()        # parameter names of this and enclosing subckts
        self.out: List[object] = []         # IR order: Instance, Model, child _Scope
        self.insts: Dict[str, Instance] = {}
        self.xrefs: List[Tuple[Instance, _Scope]] = []
        self.deferred: List[Note] = []
        self.sources: List[Tuple[Instance, str, List[Expr], Dict[str, Expr]]] = []
        self.sorted_params: List[Param] = []

    def chain(self):
        """This scope, then each enclosing one up to the top level."""
        s: Optional[_Scope] = self
        while s is not None:
            yield s
            s = s.parent


# =============================================================================
# Tables
# =============================================================================

_DOT = {
    ".include": ".inc", ".inc": ".inc", ".incl": ".inc", ".lib": ".lib", ".endl": ".endl",
    ".subckt": ".subckt", ".macro": ".subckt", ".ends": ".ends", ".eom": ".ends",
    ".param": ".param", ".parameter": ".param", ".parameters": ".param", ".model": ".model",
    ".global": ".global", ".option": ".option", ".options": ".option", ".opt": ".option",
    ".temp": ".temp", ".tran": ".tran", ".op": ".op", ".dc": ".dc", ".ac": ".ac",
    ".print": ".print", ".probe": ".print", ".ic": ".ic", ".dcvolt": ".ic",
    ".nodeset": ".nodeset", ".hdl": ".hdl", ".end": ".end", ".title": ".title",
    ".measure": ".measure", ".meas": ".measure", ".alter": ".alter", ".data": ".data",
    ".enddata": ".enddata", ".if": ".if", ".elseif": ".if", ".else": ".if", ".endif": ".if",
    ".protect": ".protect", ".prot": ".protect", ".unprotect": ".protect", ".unprot": ".protect",
}
_DOT_OTHER_ANALYSES = (".noise", ".four", ".fft", ".disto", ".pz", ".sens", ".tf", ".net",
                       ".lstb", ".hb", ".hbac", ".hbnoise", ".hbxf", ".shooting", ".sn", ".snac",
                       ".snnoise", ".snxf", ".acmatch", ".dcmatch", ".loadpull")
_DOT_NOTED = {".width": "output formatting", ".graph": "graphical output", ".plot": "printer plots",
              ".save": "saving the operating point", ".biaschk": "bias checks",
              ".dellib": None}
_DOT_ERRORS = {".connect": "shorting two nodes", ".load": "loading an operating point",
               ".vec": "digital vector files", ".stim": "stimulus output", ".malias": "model aliases",
               ".alias": "aliases", ".del": "library deletion (.del lib)",
               ".func": "functions (write .param f(x)='...')", ".step": "parameter steps",
               ".lin": "linear network extraction", ".sample": "sampling",
               ".dout": "digital output", ".tout": "digital output",
               ".stimulus": "stimulus files", ".sweepblock": "sweep blocks"}
_CONTROLS_TOP_ONLY = (".tran", ".op", ".dc", ".ac", ".print", ".ic", ".nodeset", ".measure")

_SOURCE_FUNCS = {"pulse": "pulse", "pu": "pulse", "sin": "sin", "exp": "exp", "pwl": "pwl",
                 "pl": "pl"}
_SOURCE_FUNCS_BAD = ("sffm", "am", "pe", "pat", "pwlfile", "data", "trnoise", "trrandom",
                     "vmrf", "noise", "lfsr", "perturb")
_SOURCE_ARITY = {"pulse": (2, 7), "sin": (2, 6), "exp": (2, 6)}
_SOURCE_KEYS = {"pulse": ("v1", "v2", "td", "tr", "tf", "pw", "per"),
                "sin": ("vo", "va", "freq", "td", "theta", "phase"),
                "exp": ("v1", "v2", "td1", "tau1", "td2", "tau2")}

_E_FORMS_BAD = ("poly", "pwl", "npwl", "ppwl", "delay", "laplace", "pole", "freq", "opamp",
                "transformer", "and", "nand", "or", "nor", "table", "integ", "deriv", "vcr",
                "vccap", "noise", "foster", "smooth")
_LETTERS_BAD = {
    "b": "an IBIS I/O buffer (HSPICE B element); behavioral sources are E VOL= / G CUR=",
    "s": "an S-parameter element (HSPICE S)", "w": "a W-element transmission line",
    "p": "a port element (HSPICE P)", "t": "a lossless transmission line (T)",
    "u": "a lossy transmission line (U)", "o": "a lossy transmission line (O)",
    "y": "a Y element", "a": "an A element", "n": "an N element", "z": "a Z element",
}

_OPT_OUTPUT = frozenset((
    "post", "probe", "ingold", "measout", "list", "node", "nomod", "acct", "opts", "brief",
    "nopage", "numdgt", "measdgt", "csdf", "post_version", "captab", "statfl", "warnlimit",
    "nowarn", "unwrap", "interp", "putmeas", "listfile", "lennam", "pathnum", "nopiv", "dcon",
    "nxx", "vfloor", "badchr", "noelck", "notop", "nolisub", "autostop", "sda", "zuken",
    "cdsprobe", "artist", "psf", "wdf", "fsdb", "probe_waveform", "co", "limpts", "dccap",
    "measform", "xlinfo", "lis_new", "post_double", "converge", "spmodel"))
_OPT_NUMERIC = frozenset((
    "runlvl", "absv", "relv", "reli", "absi", "itl1", "itl2", "itl3", "itl4", "itl5",
    "ft", "fast", "maxord", "relq", "chgtol", "trtol", "imax", "imin",
    "bypass", "absmos", "relmos", "gmindc", "rmin", "cptime", "sim_accuracy", "absh",
    "relh", "absvar", "relvar", "dvtr", "fs", "dcstep", "gmax", "newtol", "pivot", "pivtol",
    "pivrel", "spice_ver", "symb", "dcfor", "dchold", "dcic", "ic_accuracy", "mbypass",
    "bytol", "absvdc", "relvdc", "kcltest", "di", "mu", "xmu", "sparse", "itlpz", "cvtol",
    "hier_scale", "hier_delim", "modsrh", "modmonte", "seed", "lscal", "macmod",
    "fmax", "wnflag", "tmiflag", "bin_param", "noiseminfreq", "epsmin", "expli", "gramp",
    "cscal", "fscal", "gscal", "pzabs", "pztol", "ritol", "trcon", "rlim", "cmiflag",
    "rcnoise"))
_OPT_WARN = {"gshunt": "a conductance from every node to ground",
             "cshunt": "a capacitance from every node to ground"}
_OPT_MAPPED = ("reltol", "abstol", "vntol", "gmin")
_OPT_STEP_ALGO = ("dvdt", "lvltim", "accurate")     # HSPICE's timestep algorithm; they count for RMAX
_OPT_MOSDEF = {"defl": "l", "defw": "w", "defad": "ad", "defas": "as", "defpd": "pd",
               "defps": "ps", "defnrd": "nrd", "defnrs": "nrs"}

_FAST_NUM = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eEdD][+-]?\d+)?[A-Za-z_]*$")   # D exponent: expr.number
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")
_FUNC_DEF = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(([^()]*)\)$")
_CALLISH = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)$", re.S)
_BIN = re.compile(r"(.+)\.(\d+)$")
_BIN_BOUNDS = ("lmin", "lmax", "wmin", "wmax")
_VA_MODULE = re.compile(r"\b(?:module|macromodule)\s+([A-Za-z_][A-Za-z0-9_$]*)")
_VA_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_BOM = chr(0xFEFF)


def _bare(f: Tuple[Optional[str], str]) -> bool:
    return f[0] is None


def _unparen(text: str) -> Optional[str]:
    """The inside of '( ... )', or None."""
    t = text.strip()
    if t.startswith("(") and t.endswith(")"):
        return t[1:-1]
    return None


def _contains_call(ast: Expr, funcs: Mapping[str, object]) -> bool:
    for e in E.walk(ast):
        if isinstance(e, Call) and e.func in funcs and not E.is_node_call(e):
            return True
    return False


def _mul(a: Expr, b: Expr) -> Expr:
    if isinstance(a, Num) and isinstance(b, Num):
        return Num(a.value * b.value)
    if isinstance(b, Num) and b.value == 1.0:
        return a
    return Binary("*", a, b)


# =============================================================================
# The parser
# =============================================================================

class _Parser:
    def __init__(self, cwd: str, opts: ParseOpts):
        if opts.case not in E.CASES:
            raise ValueError("ParseOpts.case must be one of %s" % (E.CASES,))
        self.cwd = os.path.abspath(cwd)
        self.opts = opts
        self.case = opts.case
        if opts.case == "lower":
            self.fold: Callable[[str], str] = str.lower
        elif opts.case == "upper":
            self.fold = str.upper
        else:
            self.fold = lambda s: s
        self.notes: List[Note] = []
        self.top = _Scope(None, None, None)
        self.scope = self.top
        self.files: Dict[str, _File] = {}
        self.expanded: Dict[Tuple[str, Optional[str], int], str] = {}
        self.search: List[str] = []
        for d in opts.search:
            self.search.append(os.path.normpath(os.path.join(self.cwd, os.path.expanduser(d))))
        self.title = ""
        self.options: List[Tuple[str, Optional[str], str]] = []      # (key, value text, origin)
        self.opt: Dict[str, Tuple[Optional[str], str]] = {}          # key -> last (value, origin)
        self.controls: List[Tuple[str, _Stmt]] = []
        self.globals_raw: List[Tuple[str, str]] = []
        self.hdl: List[str] = []
        self.va_modules: Dict[str, str] = {}                         # lower -> declared name
        self.spelling_def: Dict[str, str] = {}
        self.spelling_use: Dict[str, str] = {}
        self.globals: List[str] = []
        self.global_set: Set[str] = set()
        self.values: Dict[str, float] = {}
        self.tstep = opts.synth_step
        self.tstop = opts.synth_stop
        self.tran_inc: Dict[int, float] = {}                         # id(tran Analysis) -> smallest increment
        self.left: Dict[str, Note] = {}
        self.bad: Dict[int, Note] = {}                               # id(scope) -> why it is left out
        self.dropped: Dict[int, Set[str]] = {}                       # id(scope) -> dropped instance names
        self.wl = False
        self.parhier = "global"
        self.parhier_eff = "global"
        # the spectre personality's SPICE mode (VAMOS_SPECTRE_DESIGN.md §4.3): names resolved against
        # spectre.py's table, Spectre's expression semantics, no file access
        self.sp = opts.dialect == "spectre-spice"
        self.edialect = "spectre-spice" if self.sp else "hspice"
        self.resolver: Optional[Resolver] = None
        self.pending: List[Tuple[str, str, str]] = []
        self.va_mods: Dict[str, VaModule] = {}
        self.cur_origin = ""                                        # for the number reader's notes

    # -- notes ------------------------------------------------------------------

    def ewarn(self, severity: str, msg: str) -> None:
        """expr.number's notes and warnings (the Spectre dialects' scale-factor rules, §3.3)."""
        self.notes.append(Note(severity, self.cur_origin, msg))

    def is_ground(self, text: str) -> bool:
        """'0' and HSPICE's aliases; in spectre-spice only '0' (§3.4, §4.3 Lexical)."""
        return text == "0" if self.sp else fold_ground(text) == "0"

    def err(self, origin: str, msg: str) -> None:
        self.notes.append(error(origin, msg))

    def warn(self, origin: str, msg: str) -> None:
        self.notes.append(warning(origin, msg))

    def note(self, origin: str, msg: str) -> None:
        self.notes.append(note(origin, msg))

    def fail(self, scope: _Scope, origin: str, msg: str) -> None:
        """An element-level problem: an error at top level, deferred inside a subckt."""
        if scope is self.top:
            self.err(origin, msg)
        else:
            scope.deferred.append(error(origin, msg))

    def spell(self, name: str, orig: str, definition: bool = False) -> None:
        if definition:
            self.spelling_def.setdefault(name, orig)
        else:
            self.spelling_use.setdefault(name, orig)

    # =========================================================================
    # Pass 1: files, sections, scopes, control statements
    # =========================================================================

    def read(self, paths: Sequence[NetlistRef], extra: Sequence[Tuple[str, str]]) -> None:
        for k, ref in enumerate(paths):
            if isinstance(ref, str):
                p, origin = ref, ""
            else:
                p, origin = ref
            path = self.resolve(p, self.origin_dir(origin), origin, "netlist")
            if path is None:
                continue
            key = (os.path.realpath(path), None, id(self.top))
            if key in self.expanded:
                self.note(origin or path, "netlist %s already read (%s); not read again"
                          % (p, self.expanded[key]))
                continue
            self.expanded[key] = path
            f = self.load(path, k == 0)
            self.run(f, None)
            self.close_scopes(path)
        if extra:
            phys = [(line, origin) for line, origin in extra]
            stmts, notes = _logical(phys, self.edialect)
            self.notes.extend(notes)
            f = _File("", stmts)
            self.notes.extend(f.notes)
            self.run(f, None)
            self.close_scopes(extra[0][1])

    def close_scopes(self, where: str) -> None:
        while self.scope is not self.top:
            self.err(self.scope.stmt.origin, ".subckt %s has no .ends (end of %s)"
                     % (self.scope.name, where))
            self.scope = self.scope.parent

    def load(self, path: str, title: bool) -> _File:
        key = path + ("\0title" if title else "")
        if key in self.files:
            return self.files[key]
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            self.err(path, "cannot read %s: %s" % (path, exc))
            f = _File(path, [])
            self.files[key] = f
            return f
        if text.startswith(_BOM):
            text = text[1:]
        lines = text.split("\n")
        start = 0
        if title:
            start = 1
            self.title = lines[0].rstrip("\r").strip() if lines else ""
            self.check_title(path)
        phys = [(lines[i], "%s:%d" % (path, i + 1)) for i in range(start, len(lines))]
        stmts, notes = _logical(phys)
        self.notes.extend(notes)
        f = _File(path, stmts)
        self.notes.extend(f.notes)
        self.files[key] = f
        return f

    def check_title(self, path: str) -> None:
        t = self.title
        if not t:
            return
        words = t.split()
        m = _DOT_KW.match(t)
        looks = m is not None and m.group(0).lower() in _DOT
        if not looks and len(words) >= 3 and words[0][:1].lower() in "rclkvieghfdqmjx":
            # an element line has a value: a number or a key=value field
            looks = any("=" in w or _FAST_NUM.match(w) for w in words[2:])
        if looks:
            self.note(path + ":1", "line 1 is the title and is not parsed: %s" % t)

    def origin_dir(self, origin: str) -> Optional[str]:
        """The directory of the file an origin 'file:line' names (relative to the cwd), or None."""
        name, sep, line = origin.rpartition(":")
        if not sep or not name or not line.strip().isdigit():
            return None
        return os.path.dirname(os.path.normpath(os.path.join(self.cwd, name)))

    def resolve(self, text: str, base: Optional[str], origin: str, what: str) -> Optional[str]:
        """The absolute path a reference names (§4.3.1), or None with an error."""
        p = os.path.expanduser(os.path.expandvars(text))
        if os.path.isabs(p):
            cands = [os.path.normpath(p)]
        else:
            cands = [os.path.normpath(os.path.join(self.cwd, p))]
            if base:
                cands.append(os.path.normpath(os.path.join(base, p)))
            cands.extend(os.path.normpath(os.path.join(d, p)) for d in self.search)
        seen: List[str] = []
        for c in cands:
            if c not in seen:
                seen.append(c)
        found = [c for c in seen if os.path.isfile(c)]
        if not found:
            self.err(origin or text, "%s %s not found; tried %s" % (what, text, ", ".join(seen)))
            return None
        use = found[0]
        real = os.path.realpath(use)
        others = [c for c in found[1:] if os.path.realpath(c) != real]
        if others:
            self.note(origin or text, "%s %s: using %s (also found: %s)"
                      % (what, text, use, ", ".join(others)))
        return os.path.abspath(use)

    def run(self, f: _File, section: Optional[str]) -> None:
        """Process f whole (section None) or one of its sections."""
        if section is None:
            i, end, skip = 0, len(f.stmts), f.skip
        else:
            i, end, _ = f.sections[section]
            skip = {}
        base = os.path.dirname(f.path) if f.path else None
        while i < end:
            if i in skip:
                i = skip[i] + 1
                continue
            st = f.stmts[i]
            kw = st.kw
            canon = _DOT.get(kw)
            if canon in (".if", ".data", ".enddata") and kw not in (".if", ".data"):
                self.err(st.origin, "%s without %s" % (kw, ".data" if canon != ".if" else ".if"))
                i += 1
                continue
            if kw == ".end" or kw == ".alter":
                if kw == ".alter":
                    self.err(st.origin, ".alter is not supported (one simulation per run)")
                if section is not None and kw == ".end":
                    self.err(st.origin, "a .lib section cannot contain .end")
                if self.sp and kw == ".end":
                    self.controls.append((".end", st))
                rest = [s for s in f.stmts[i + 1:end]]
                if rest and kw == ".end":
                    self.note(rest[0].origin, "ignored after .end (%d statement%s)"
                              % (len(rest), "" if len(rest) == 1 else "s"))
                return
            if kw == ".data":
                self.err(st.origin, ".data is not supported")
                i = self.skip_to(f, i, end, (".enddata",), ())
                continue
            if kw == ".if":
                self.err(st.origin, "%s is not supported (conditional netlists, .if binning)"
                         % st.text.split()[0])
                i = self.skip_to(f, i, end, (".endif",), (".if",))
                continue
            if kw == ".endl":
                self.err(st.origin, ".endl without a .lib section")
            else:
                # the netlist_commands fragment has no file: its lines name their control file
                self.statement(st, f, base if f.path else self.origin_dir(st.origin), section)
            i += 1

    def skip_to(self, f: _File, i: int, end: int, closers: Tuple[str, ...],
                openers: Tuple[str, ...]) -> int:
        depth = 0
        j = i + 1
        while j < end:
            t = f.stmts[j].text.split()[0].lower()
            if t in closers:
                if depth == 0:
                    return j + 1
                depth -= 1
            elif t in openers:
                depth += 1
            j += 1
        self.err(f.stmts[i].origin, "%s has no %s" % (f.stmts[i].text.split()[0], closers[0]))
        return end

    def statement(self, st: _Stmt, f: _File, base: Optional[str], section: Optional[str]) -> None:
        kw = st.kw
        if not kw.startswith("."):
            self.scope.items.append(st)
            return
        canon = _DOT.get(kw)
        if self.sp and canon not in (".subckt", ".ends", ".param", ".model", ".inc", ".lib", ".endl",
                                     ".data", ".enddata", ".if", ".alter"):
            # spectre.py reads every other dot statement from Fragment.controls (§4.3's table)
            if canon in _CONTROLS_TOP_ONLY and self.scope is not self.top:
                self.err(st.origin, "%s inside .subckt %s is not supported" % (kw, self.scope.name))
                return
            if canon == ".option" and self.scope is not self.top:
                self.err(st.origin, "%s inside .subckt %s: Spectre's multi-technology mode scopes options "
                         "given in a subcircuit to it, which v1 does not model (§3.6)" % (kw, self.scope.name))
                return
            if canon is None and kw not in _DOT_OTHER_ANALYSES and kw not in _DOT_NOTED and kw not in _DOT_ERRORS:
                self.err(st.origin, "unsupported dot-command %s" % kw)
                return
            self.controls.append((canon or kw, st))
            return
        if self.sp and canon in (".inc", ".lib", ".endl"):
            self.err(st.origin, "%s was not spliced in before the SPICE region was read (internal)" % kw)
            return
        if canon is None:
            if kw in _DOT_OTHER_ANALYSES:
                self.warn(st.origin, "%s ignored: only the first .tran is simulated in a "
                          "co-simulation" % kw)
            elif kw in _DOT_NOTED:
                self.note(st.origin, "%s ignored (%s)" % (kw, _DOT_NOTED[kw] or "library control"))
            elif kw in _DOT_ERRORS:
                self.err(st.origin, "%s is not supported (%s)" % (kw, _DOT_ERRORS[kw]))
            else:
                self.err(st.origin, "unsupported dot-command %s" % kw)
            return
        if canon in _CONTROLS_TOP_ONLY and self.scope is not self.top:
            self.err(st.origin, "%s inside .subckt %s is not supported" % (kw, self.scope.name))
            return
        if canon == ".inc":
            self.d_include(st, base)
        elif canon == ".lib":
            self.d_lib(st, f, base, section)
        elif canon == ".subckt":
            self.d_subckt(st)
        elif canon == ".ends":
            self.d_ends(st)
        elif canon in (".param", ".model"):
            self.scope.items.append(st)
        elif canon == ".option":
            self.d_option(st, base)
        elif canon == ".global":
            self.d_global(st)
        elif canon == ".hdl":
            self.d_hdl(st, base)
        elif canon == ".title":
            self.title = st.text[len(kw):].strip()
        elif canon == ".protect":
            pass                                # listing control only
        elif canon == ".measure":
            self.warn(st.origin, ".measure is not supported and is ignored")
        elif canon == ".enddata":
            self.err(st.origin, ".enddata without .data")
        else:
            self.controls.append((canon, st))

    def d_include(self, st: _Stmt, base: Optional[str]) -> None:
        try:
            args = _path_args(st.text[len(st.kw):])
        except _LexError as exc:
            self.err(st.origin, "%s: %s" % (st.kw, exc))
            return
        if len(args) != 1:
            self.err(st.origin, "%s takes one file name" % st.kw)
            return
        self.expand(st, args[0], None, base)

    def d_lib(self, st: _Stmt, f: _File, base: Optional[str], section: Optional[str]) -> None:
        try:
            args = _path_args(st.text[4:])
        except _LexError as exc:
            self.err(st.origin, ".lib: %s" % exc)
            return
        if len(args) == 2:
            self.expand(st, args[0], args[1].lower(), base)
        elif len(args) == 1 and section is not None:
            # inside a section: a call to another section of the same file
            sec = args[0].lower()
            key = (os.path.realpath(f.path), sec, id(self.scope))
            if sec not in f.sections:
                self.err(st.origin, ".lib %s: no section %s in %s" % (args[0], args[0], f.path))
            elif key in self.expanded:
                self.note(st.origin, "section %s of %s already read at %s; not read again"
                          % (sec, f.path, self.expanded[key]))
            else:
                self.expanded[key] = st.origin
                self.run(f, sec)
        else:
            self.err(st.origin, ".lib needs a file name and a section name ('.lib \"file\" sec')")

    def expand(self, st: _Stmt, name: str, section: Optional[str], base: Optional[str]) -> None:
        path = self.resolve(name, base, st.origin, ".lib file" if section else "include file")
        if path is None:
            return
        key = (os.path.realpath(path), section, id(self.scope))
        what = ("section %s of %s" % (section, path)) if section else path
        if key in self.expanded:
            self.note(st.origin, "%s already read at %s; not read again" % (what, self.expanded[key]))
            return
        self.expanded[key] = st.origin
        f = self.load(path, False)
        if section is not None and section not in f.sections:
            self.err(st.origin, ".lib %s %s: the file has no section %s%s"
                     % (name, section, section, " (sections: %s)" % " ".join(sorted(f.sections))
                        if f.sections else ""))
            return
        self.run(f, section)

    def d_subckt(self, st: _Stmt) -> None:
        words = st.text.split()
        if len(words) < 2:
            self.err(st.origin, "%s needs a name" % st.kw)
            name = "?"
        else:
            name = self.fold(words[1])
            self.spell(name, words[1], True)
        child = _Scope(name, self.scope, st)
        self.scope.items.append(child)
        self.scope = child

    def d_ends(self, st: _Stmt) -> None:
        if self.scope is self.top:
            self.err(st.origin, "%s without .subckt" % st.kw)
            return
        words = st.text.split()
        if len(words) > 1 and self.fold(words[1]) != self.scope.name:
            self.err(st.origin, "%s %s closes .subckt %s (%s)" % (st.kw, words[1], self.scope.name,
                                                               self.scope.stmt.origin))
        self.scope = self.scope.parent

    def d_option(self, st: _Stmt, base: Optional[str]) -> None:
        try:
            fields = st.fields()[1:]
        except _LexError as exc:
            self.err(st.origin, "%s: %s" % (st.kw, exc))
            return
        for k, v in fields:
            key = (v if k is None else k).lower()
            val = None if k is None else v
            if key == "search":
                if val is None:
                    self.err(st.origin, ".option search needs a directory")
                    continue
                d = os.path.expanduser(os.path.expandvars(val.strip("'\"")))
                if not os.path.isabs(d):
                    cand = os.path.join(self.cwd, d)
                    if not os.path.isdir(cand) and base:
                        cand = os.path.join(base, d)
                    d = cand
                d = os.path.normpath(d)
                if not os.path.isdir(d):
                    self.warn(st.origin, ".option search=%s: no such directory" % val)
                elif d not in self.search:
                    self.search.append(d)
                continue
            self.options.append((key, val, st.origin))

    def d_global(self, st: _Stmt) -> None:
        try:
            fields = st.fields()[1:]
        except _LexError as exc:
            self.err(st.origin, ".global: %s" % exc)
            return
        for key, w in fields:
            if key is not None:
                self.err(st.origin, ".global: %s=%s is not a node name" % (key, w))
            else:
                self.globals_raw.append((w, st.origin))

    def d_hdl(self, st: _Stmt, base: Optional[str]) -> None:
        try:
            args = _path_args(st.text[4:])
        except _LexError as exc:
            self.err(st.origin, ".hdl: %s" % exc)
            return
        if not args:
            self.err(st.origin, ".hdl needs a Verilog-A file name")
            return
        if len(args) > 1:
            self.note(st.origin, ".hdl: extra arguments ignored: %s" % " ".join(args[1:]))
        path = self.resolve(args[0], base, st.origin, "Verilog-A file")
        if path is None:
            return
        if path not in self.hdl:
            self.hdl.append(path)
        mods, files = (spectre_va_modules if self.sp else va_modules)(path, self.search)   # `include followed (§3.1)
        if not mods:
            self.err(st.origin, "%s declares no Verilog-A module%s"
                     % (path, " (files read: %s)" % ", ".join(files) if len(files) > 1 else ""))
        for low, vm in mods.items():
            self.va_modules.setdefault(low, vm.name)
            self.va_mods.setdefault(low, vm)

    # =========================================================================
    # Options needed before elements are read
    # =========================================================================

    def early_options(self) -> None:
        for key, val, origin in self.options:
            prev = self.opt.get(key)
            if prev is not None and prev[0] != val and key not in ("search",):
                self.note(origin, ".option %s=%s replaces %s (%s); HSPICE uses the last value"
                          % (key, val, prev[0], prev[1]))
            self.opt[key] = (val, origin)
        self.wl = "wl" in self.opt
        pv = self.opt.get("parhier")
        self.parhier = "global"
        if pv is not None:
            v = (pv[0] or "").strip("'\"").lower()
            if v not in ("local", "global"):
                self.err(pv[1], ".option parhier=%s: must be local or global" % pv[0])
            else:
                self.parhier = v
        g: List[str] = []
        for w, origin in self.globals_raw:
            try:
                n = self.node_text(w, terminal=True)
            except _Defer as exc:
                self.err(origin, ".global: %s" % exc)
                continue
            if n == "0" or n in g:
                continue
            g.append(n)
            self.spell(n, w)
        self.globals = g
        self.global_set = set(g)

    def va_module(self, name: str) -> Optional[str]:
        """A Verilog-A module name as declared: this parse's .hdl files, then the resolver's (§4.3)."""
        found = self.va_modules.get(name.lower())
        if found is None and self.resolver is not None:
            vm = self.resolver.va_module(name.lower())
            if vm is not None:
                found = vm.name
        return found

    # =========================================================================
    # Pass 2a: subckt headers, parameters, functions, models
    # =========================================================================

    def declare(self, scope: _Scope) -> None:
        if scope.parent is not None:
            self.header(scope)
            scope.local = set(scope.parent.local)
        param_stmts: List[_Stmt] = []
        model_stmts: List[_Stmt] = []
        for it in scope.items:
            if isinstance(it, _Scope):
                prev = scope.subckts.get(it.name)
                if prev is not None:
                    self.err(it.stmt.origin, ".subckt %s is defined twice: %s and %s"
                             % (it.name, prev.stmt.origin, it.stmt.origin))
                    continue
                scope.subckts[it.name] = it
            elif it.kw in (".param", ".parameter", ".parameters"):
                param_stmts.append(it)
            elif it.kw == ".model":
                model_stmts.append(it)
        for st in param_stmts:
            self.param_stmt(scope, st)
        for name, text, origin in scope.raw_params:
            try:
                e = self.expr(text, scope, "param")
            except _Defer as exc:
                self.err(origin, "parameter %s: %s" % (name, exc))
                continue
            scope.params.append((name, e, origin))
        if scope.parent is not None:
            scope.local |= {n for n, _, _ in scope.params}
        for st in model_stmts:
            self.model_stmt(scope, st)
        self.group_bins(scope)
        for it in scope.items:
            if isinstance(it, _Scope) and scope.subckts.get(it.name) is it:
                self.declare(it)

    def header(self, scope: _Scope) -> None:
        st = scope.stmt
        try:
            fields = st.fields()
        except _LexError as exc:
            self.err(st.origin, "%s: %s" % (st.kw, exc))
            return
        ports: List[str] = []
        k = 2
        while k < len(fields) and _bare(fields[k]) and fields[k][1].lower() != "params:":
            text = fields[k][1]
            try:
                p = self.port_text(text)
            except _Defer as exc:
                self.err(st.origin, ".subckt %s: %s" % (scope.name, exc))
                p = text.lower()
            if p in ports:
                self.err(st.origin, ".subckt %s: port %s is listed twice" % (scope.name, text))
            ports.append(p)
            self.spell(p, text, True)
            scope.spelling.setdefault(p, text)      # this definition's own (Subckt.spelling)
            k += 1
        for key, text in fields[k:]:
            if key is None:
                if text.lower() == "params:":
                    continue
                self.err(st.origin, ".subckt %s: %r after the parameters" % (scope.name, text))
                continue
            if not _IDENT.match(key):
                self.err(st.origin, ".subckt %s: %r is not a parameter name" % (scope.name, key))
                continue
            if text == "":
                self.err(st.origin, ".subckt %s: parameter %s has no value" % (scope.name, key))
                continue
            name = self.fold(key)
            self.spell(name, key, True)
            scope.raw_params.append((name, text, st.origin))
            scope.header_params.add(name)
        scope.orig_ports = ports
        scope.gnd_ports = [i for i, p in enumerate(ports) if self.is_ground(p)]
        scope.ports = [p for i, p in enumerate(ports) if i not in scope.gnd_ports]

    def port_text(self, text: str) -> str:
        """A port name as written, case folded (ground aliases kept: Subckt.orig_ports)."""
        if any(c in text for c in "'\"()"):
            raise _Defer("%r is not a node name" % text)
        _no_period(text)
        t = text
        if t.isdigit() and not self.sp:                 # Spectre: 007 is not 7 (§3.4)
            t = str(int(t))
        if "{" in t and not self.sp:
            t = t.replace("{", "[").replace("}", "]")
        return self.fold(t)

    def node_text(self, text: str, terminal: bool = False) -> str:
        """A node name in the IR: folded, '0' for a ground alias.  terminal: the name of a net an
        element, X line or .global connects (not a hierarchical reference): no '.' (_no_period)."""
        if any(c in text for c in "'\"()"):
            raise _Defer("%r is not a node name" % text)
        if terminal:
            _no_period(text)
        t = text
        if self.sp:                                     # node names are strings; only 0 is ground (§3.4)
            if t == "0":
                return "0"
            n = self.fold(t)
            self.spell(n, text)
            return n
        if t.isdigit():
            t = str(int(t))
        if "{" in t:
            t = t.replace("{", "[").replace("}", "]")
        if t.lower() in _GROUND:
            return "0"
        n = self.fold(t)
        self.spell(n, text)
        return n

    def param_stmt(self, scope: _Scope, st: _Stmt) -> None:
        try:
            fields = st.fields()[1:]
        except _LexError as exc:
            self.err(st.origin, ".param: %s" % exc)
            return
        if not fields:
            self.err(st.origin, ".param defines nothing")
        for key, text in fields:
            if key is None:
                self.err(st.origin, ".param %s has no value" % text)
                continue
            if text == "":
                self.err(st.origin, ".param %s has no value" % key)
                continue
            m = _FUNC_DEF.match(key)
            if m:
                fname = m.group(1).lower()
                args = tuple(self.fold(a.strip()) for a in m.group(2).split(",") if a.strip())
                if self.sp:
                    if fname in E.SPECTRE_FUNCS:         # a user function wins, with a warning (§3.5)
                        self.warn(st.origin, ".param %s(): a user function named like the built-in %s(); "
                                  "the user function is used (UG p.92)" % (fname, fname))
                elif fname in E.FUNCS or E.is_node_call(Call(fname, ())):
                    self.err(st.origin, ".param %s(): redefines a built-in function" % fname)
                    continue
                if not args or any(not _IDENT.match(a) for a in args):
                    self.err(st.origin, ".param %s: bad argument list" % key)
                    continue
                try:
                    if self.sp:
                        self.cur_origin = st.origin
                        body = E.parse(text, self.case, self.edialect, funcs=self.visible_funcs(scope),
                                       warn=self.ewarn)
                        scope.funcs_cache = None
                    else:
                        body = E.parse(text, self.case)
                except E.ExprError as exc:
                    self.err(st.origin, "function %s: %s" % (fname, exc))
                    continue
                if fname in scope.funcs:
                    self.note(st.origin, "function %s() is defined again (first at %s); the last "
                              "definition is used" % (fname, scope.funcs[fname][2]))
                scope.funcs[fname] = (args, body, st.origin)
                continue
            if not _IDENT.match(key):
                self.err(st.origin, ".param: %r is not a parameter name" % key)
                continue
            name = self.fold(key)
            self.spell(name, key, True)
            scope.raw_params.append((name, text, st.origin))

    def visible_funcs(self, scope: _Scope) -> Dict[str, Tuple[Sequence[str], Expr]]:
        """User functions visible in scope (inner definitions shadow outer ones)."""
        if scope.funcs_cache is None:
            out: Dict[str, Tuple[Sequence[str], Expr]] = {}
            for s in reversed(list(scope.chain())):
                for k, (a, b, _) in s.funcs.items():
                    out[k] = (a, b)
            scope.funcs_cache = out
        return scope.funcs_cache

    # -- expressions ------------------------------------------------------------

    def expr(self, text: str, scope: _Scope, ctx: str = "value") -> Expr:
        """One value.  ctx 'param' / 'value': no v(), i(), time; 'behavioral': anything.

        Raises _Defer naming the problem.
        """
        t = text.strip()
        if not t:
            raise _Defer("empty value")
        if self.sp:
            return self.expr_spectre(t, text, scope, ctx)
        if len(t) >= 2 and ((t[0] == "'" and t[-1] == "'") or (t[0] == "{" and t[-1] == "}")):
            inner = t[1:-1].strip()
            if inner and not any(c in inner for c in "'{}\""):
                t = inner
        if _FAST_NUM.match(t):
            try:
                return Num(E.number(t))
            except ValueError:
                pass
        if _IDENT.match(t):
            low = t.lower()
            if low in E.RESERVED:
                ast: Expr = Name(low)
            else:
                return Name(self.fold(t))
        else:
            try:
                ast = E.parse(t, self.case)
            except E.ExprError as exc:
                raise _Defer("cannot read %s: %s" % (text, exc))
        funcs = self.visible_funcs(scope) if any(s.funcs for s in scope.chain()) else {}
        if funcs and _contains_call(ast, funcs):
            try:
                ast = E.inline(ast, funcs)
            except E.ExprError as exc:
                raise _Defer("%s: %s" % (text, exc))
        node_calls = False
        for e in E.walk(ast):
            if isinstance(e, Call):
                if E.is_node_call(e):
                    node_calls = True
                    if ctx != "behavioral":
                        raise _Defer("%s reads %s, which is only allowed in E VOL= / G CUR= "
                                     "sources" % (text, E.to_text(e)))
                elif e.func not in E.FUNCS:
                    raise _Defer("%s: unknown function %s()" % (text, e.func))
                else:
                    E.check_arity(e, _Defer)
            elif isinstance(e, Name):
                if e.name == "hertz":
                    raise _Defer("%s uses hertz, which has no value in a transient co-simulation"
                                 % text)
                if e.name == "time" and ctx != "behavioral":
                    raise _Defer("%s uses time, which is only allowed in E VOL= / G CUR= sources"
                                 % text)
        if node_calls:
            ast = self.fold_vnodes(ast)
        elif ctx == "value" and E.is_constant(ast):
            try:                                    # '2*1k' -> 2000.0 (HSPICE semantics)
                return Num(E.evaluate(ast, {}))
            except E.EvalError as exc:
                raise _Defer("%s: %s" % (text, exc))
        return ast

    def expr_spectre(self, t: str, text: str, scope: _Scope, ctx: str) -> Expr:
        """The spectre-spice dialect's value (§3.5, §4.3's switch points): SPICE mode's numbers, temp
        and tnom as the predefined parameters, the constants, user functions through
        expr.parse(funcs=), the Spectre function table, and the parse-time fold with Spectre's
        evaluator, so an out-of-domain constant is an error here, never Num(-2.0) for sqrt(-4)."""
        if len(t) >= 2 and ((t[0] == "'" and t[-1] == "'") or (t[0] == "{" and t[-1] == "}")):
            inner = t[1:-1].strip()
            if inner and not any(c in inner for c in "'{}\""):
                t = inner
        if _FAST_NUM.match(t):
            try:
                return Num(E.number(t, self.edialect, self.ewarn))
            except ValueError:
                pass
        funcs = self.visible_funcs(scope) if any(s.funcs for s in scope.chain()) else None
        if _IDENT.match(t):
            low = t.lower()
            if low == "temp":
                return Name("temper")
            if low == "tnom":
                return Name("$tnom")
            if low in ("time", "hertz"):
                raise _Defer("%s: behavioral expressions are not supported in v1" % text)
            if low not in E._SPECTRE_CONSTANTS_LOWER and low != "temper":
                return Name(low)
        try:
            ast = E.parse(t, self.case, self.edialect, funcs=funcs, warn=self.ewarn)
        except E.ExprError as exc:
            raise _Defer("cannot read %s: %s" % (text, exc))
        if ctx == "value" and E.is_constant(ast):
            try:
                return Num(E.evaluate(ast, {}, self.edialect))
            except E.EvalError as exc:
                raise _Defer("%s: %s" % (text, exc))
        return ast

    def fold_vnodes(self, ast: Expr) -> Expr:
        """Ground aliases (and leading zeros) inside v(): x1.gnd -> 0."""
        def f(e: Expr) -> Expr:
            if E.is_node_call(e) and e.func.startswith("v"):
                args = []
                for a in e.args:
                    name = a.name
                    last = name.rsplit(".", 1)[-1]
                    if fold_ground(last) == "0":
                        name = "0"
                    elif name.isdigit():
                        name = str(int(name))
                    args.append(Name(name))
                return Call(e.func, tuple(args))
            return e
        return E.transform(ast, f)

    def const(self, e: Expr, scope: _Scope) -> Optional[float]:
        """The value of e if it is constant at parse time (top-level parameters only)."""
        if isinstance(e, Num):
            return e.value
        ns = E.names(e)
        if ns & scope.local:
            return None
        try:
            return E.evaluate(e, self.values, self.edialect)
        except E.EvalError:
            return None

    # -- models -------------------------------------------------------------------

    def model_stmt(self, scope: _Scope, st: _Stmt) -> None:
        try:
            fields = st.fields()
        except _LexError as exc:
            self.err(st.origin, ".model: %s" % exc)
            return
        if len(fields) < 3 or not _bare(fields[1]):
            self.err(st.origin, ".model needs a name and a type")
            return
        name = self.fold(fields[1][1])
        self.spell(name, fields[1][1], True)
        rest = list(fields[3:])
        tkey, ttext = fields[2]
        if tkey is not None:
            self.err(st.origin, ".model %s: %s=%s is not a model type" % (name, tkey, ttext))
            return
        m = _CALLISH.match(ttext)
        if m:
            ttext = m.group(1)
            try:
                rest = _lex(m.group(2)) + rest
            except _LexError as exc:
                self.err(st.origin, ".model %s: %s" % (name, exc))
                return
        mtype = ttext.lower()
        if name in scope.models or name in scope.bad_models or name in scope.va_cards:
            prev = scope.models[name].origin if name in scope.models else \
                scope.va_cards[name][2] if name in scope.va_cards else scope.bad_models[name]
            self.err(st.origin, ".model %s is defined twice: %s and %s" % (name, prev, st.origin))
            return
        va_name = self.va_module(mtype)
        if mtype in MODEL_KINDS:
            kind = mtype
        elif va_name is not None:
            kind = va_name                                 # a card of a .hdl Verilog-A module
        else:
            why = "model type %s is not supported" % ttext.upper()
            if self.sp:
                # §11.3 (c) [E100]: a card whose type binds no module names the modules the .hdl files
                # declare (bsimcmg against bsimcmg_108), spectre-spice only (phase 2's integration, C)
                declared = sorted(set(self.va_modules.values()) |
                                  {vm.name for vm in (getattr(self.resolver, "va", None) or {}).values()})
                if declared:
                    why += "; the .hdl files declare %s" % ", ".join(declared)
            self.leave_model(scope, name, st.origin, why)
            return
        self.cur_origin = st.origin
        params: Dict[str, Expr] = {}
        flat: List[Tuple[Optional[str], str]] = []
        for f in rest:
            inner = _unparen(f[1]) if _bare(f) else None
            if inner is not None:
                try:
                    flat.extend(_lex(inner))
                except _LexError as exc:
                    self.err(st.origin, ".model %s: %s" % (name, exc))
                    return
            else:
                flat.append(f)
        try:
            for key, text in flat:
                if key is None:
                    raise _Defer("%r has no value" % text)
                k = key.lower()
                if text == "":
                    raise _Defer("%s has no value" % key)
                if k in params:
                    self.note(st.origin, ".model %s: %s is given twice; the last value is used"
                              % (name, k))
                params[k] = self.expr(text, scope, "value")
        except _Defer as exc:
            self.leave_model(scope, name, st.origin, str(exc))
            return
        if kind not in MODEL_KINDS:
            # Verilog-A parameters are passed per instance (the emitters write one card per
            # instance), so an X line naming this card becomes a 'y' instance of the module.
            scope.va_cards[name] = (kind, params, st.origin)
            return
        level: Optional[float] = None
        if "level" in params:
            level = self.const(params["level"], scope)
        prim = ""
        if self.sp:                                        # the Spectre master (§4.3 Models, §3.9)
            if "level" in params and level is None:
                self.leave_model(scope, name, st.origin, "level=%s is not a constant; SPICE mode needs a "
                                 "literal level to choose the Spectre master" % E.to_text(params["level"]))
                return
            master, why = _spice_master(mtype, level)
            if master is None:
                self.leave_model(scope, name, st.origin, why or "no Spectre master")
                return
            prim = master
        scope.models[name] = Model(name, kind, level, params, st.origin, prim=prim)
        scope.model_at[id(st)] = scope.models[name]

    def leave_model(self, scope: _Scope, name: str, origin: str, why: str) -> None:
        scope.bad_models[name] = "%s (%s)" % (why, origin)
        self.note(origin, "model %s is left out: %s; using it is an error" % (name, why))

    def group_bins(self, scope: _Scope) -> None:
        for name, m in scope.models.items():
            mm = _BIN.match(name)
            if mm is None or not all(b in m.params for b in _BIN_BOUNDS):
                continue
            m.base, m.bin_index = mm.group(1), int(mm.group(2))
            if self.sp:
                m.bin_rule = "spectre"                      # x.N bins form a Spectre model group (§4.3)
            scope.bins.setdefault(m.base, []).append(m)
        for base, bins in scope.bins.items():
            kinds = {b.kind for b in bins}
            if len(kinds) > 1:
                self.err(bins[0].origin, "binned model %s mixes types %s" % (base, " ".join(sorted(kinds))))
            if base in scope.models:
                self.err(scope.models[base].origin, "model %s is defined both as a card and as "
                         "bins (%s)" % (base, bins[0].origin))
            bins.sort(key=lambda b: b.bin_index)

    def find_model(self, scope: _Scope, name: str) -> Optional[Tuple[str, object]]:
        """('card', Model) | ('bins', [Model]) | ('bad', reason) | ('decl', Decl: a model of another
        region or language, through the resolver), innermost scope first."""
        for s in scope.chain():
            if name in s.models and name not in s.bins:
                return ("card", s.models[name])
            if name in s.bins:
                return ("bins", s.bins[name])
            if name in s.bad_models:
                return ("bad", s.bad_models[name])
        if self.resolver is not None:
            d = self.resolver.model(name)
            if d is not None:
                return ("decl", d)
        return None

    def model_kind(self, scope: _Scope, name: str) -> Optional[str]:
        found = self.find_model(scope, name)
        if found is None:
            return None
        what, obj = found
        if what == "bad":
            return "?"
        if what == "decl":
            letter = _MASTER_LETTER.get(obj.master)             # type: ignore[union-attr]
            return _LETTER_KINDS[letter][0] if letter else "?"
        if what == "bins":
            return obj[0].kind                                  # type: ignore[index]
        return obj.kind                                         # type: ignore[union-attr]

    def need_model(self, scope: _Scope, name_text: str, kinds: Sequence[str], elem: str) -> str:
        name = self.fold(name_text)
        found = self.find_model(scope, name)
        if found is None:
            raise _Defer("%s: model %s not found" % (elem, name_text))
        what, obj = found
        if what == "bad":
            raise _Defer("%s: model %s is left out: %s" % (elem, name_text, obj))
        if what == "decl":
            letter = _MASTER_LETTER.get(obj.master)             # type: ignore[union-attr]
            if letter is None or kinds[0] not in _LETTER_KINDS[letter]:
                raise _Defer("%s: model %s is a %s card, not one of %s" % (elem, name_text,
                                                                           obj.master, " ".join(kinds)))  # type: ignore[union-attr]
            return obj.name                                     # type: ignore[union-attr]
        kind = obj[0].kind if what == "bins" else obj.kind      # type: ignore[index,union-attr]
        if kind not in kinds:
            raise _Defer("%s: model %s is a %s model, not %s" % (elem, name_text, kind,
                                                                 " or ".join(kinds)))
        return name

    def card_prim(self, scope: _Scope, name: str) -> str:
        """The Spectre master of a resolved card (Instance.prim of its devices, §4.1 item 7)."""
        found = self.find_model(scope, name)
        if found is None:
            return ""
        what, obj = found
        if what == "decl":
            return obj.master                                   # type: ignore[union-attr]
        if what == "bins":
            return obj[0].prim                                  # type: ignore[index]
        if what == "card":
            return obj.prim                                     # type: ignore[union-attr]
        return ""

    def is_param_name(self, scope: _Scope, name: str) -> bool:
        """A parameter of this fragment's scopes or of spectre.py's table (§4.3: an R/C/L field)."""
        for s in scope.chain():
            if any(n == name for n, _, _ in s.raw_params) or any(n == name for n, _, _ in s.params):
                return True
        return self.resolver is not None and self.resolver.param(name) is not None

    def spectre_keys(self, scope: _Scope, st: _Stmt, orig: str, letter: str, prim: str,
                     params: Dict[str, Expr]) -> None:
        """§4.3 Elements: a keyed parameter the SPICE element does not define is a Spectre instance
        parameter of the element's master; one the master does not have is an error.  A bare word
        written for an enumeration parameter (isnoisy=no, region=sat) is its Str value."""
        from vamos.netlist import tables as T
        row = T.SPECTRE_MASTERS.get(prim)
        known = set(_SPICE_KEYS.get(letter, ())) | ({r.name for r in row.instance} if row else set())
        enums = spectre_enum_params()
        for key in list(params):
            if key not in known:
                raise _Defer("%s: %s= is neither a SPICE %s parameter nor a Spectre %s instance parameter"
                             % (orig, key, letter.upper(), prim or "?"))
            v = params[key]
            if key in enums and isinstance(v, Name) and v.name not in E.RESERVED:
                params[key] = Str(v.name)

    def find_subckt(self, scope: _Scope, name: str) -> Optional[_Scope]:
        for s in scope.chain():
            if name in s.subckts:
                return s.subckts[name]
        return None

    # =========================================================================
    # Pass 2b: elements
    # =========================================================================

    def elements(self, scope: _Scope) -> None:
        for it in scope.items:
            if isinstance(it, _Scope):
                if scope.subckts.get(it.name) is it:
                    scope.out.append(it)
                    self.elements(it)
                continue
            if it.kw.startswith("."):
                if id(it) in scope.model_at:
                    scope.out.append(scope.model_at[id(it)])
                continue
            try:
                self.cur_origin = it.origin
                inst = self.element(scope, it)
            except _Defer as exc:
                self.fail(scope, it.origin, str(exc))
                continue
            except _LexError as exc:
                self.fail(scope, it.origin, "%s: %s" % (it.text.split()[0], exc))
                continue
            if inst is None:
                continue
            if inst.name in scope.insts:
                self.fail(scope, it.origin, "element %s is defined twice: %s and %s"
                          % (inst.name, scope.insts[inst.name].origin, it.origin))
                continue
            scope.insts[inst.name] = inst
            scope.out.append(inst)

    def element(self, scope: _Scope, st: _Stmt) -> Optional[Instance]:
        fields = st.fields()
        if not fields or not _bare(fields[0]):
            raise _Defer("cannot read the element name")
        orig = fields[0][1]
        name = self.fold(orig)
        self.spell(name, orig)
        letter = orig[0].lower()
        if letter in _LETTERS_BAD:
            raise _Defer("%s: %s is not supported" % (orig, _LETTERS_BAD[letter]))
        handler = {"r": self.e_rcl, "c": self.e_rcl, "l": self.e_rcl, "k": self.e_k,
                   "v": self.e_vi, "i": self.e_vi, "e": self.e_eg, "g": self.e_eg,
                   "f": self.e_fh, "h": self.e_fh, "d": self.e_d, "q": self.e_q, "j": self.e_j,
                   "m": self.e_m, "x": self.e_x}.get(letter)
        if handler is None:
            raise _Defer("%s: element type %s is not supported" % (orig, letter.upper()))
        return handler(scope, st, name, orig, fields[1:])

    # -- helpers

    def nodes(self, fields: Sequence[Tuple[Optional[str], str]], count: int, elem: str) -> List[str]:
        if len(fields) < count or not all(_bare(f) for f in fields[:count]):
            raise _Defer("%s needs %d node%s" % (elem, count, "" if count == 1 else "s"))
        return [self.node_text(f[1], terminal=True) for f in fields[:count]]

    def keyed(self, scope: _Scope, fields: Sequence[Tuple[Optional[str], str]], elem: str,
              ctx: str = "value") -> Dict[str, Expr]:
        out: Dict[str, Expr] = {}
        for key, text in fields:
            if key is None:
                raise _Defer("%s: unexpected %r" % (elem, text))
            if text == "":
                raise _Defer("%s: %s has no value" % (elem, key))
            k = key.lower()
            if k in out:
                raise _Defer("%s: %s is given twice" % (elem, key))
            try:
                out[k] = self.expr(text, scope, ctx)
            except _Defer as exc:
                raise _Defer("%s: %s=%s" % (elem, key, exc))
        return out

    def value(self, scope: _Scope, text: str, elem: str, what: str) -> Expr:
        try:
            return self.expr(text, scope, "value")
        except _Defer as exc:
            raise _Defer("%s: %s %s" % (elem, what, exc))

    def flags(self, rest: List[Tuple[Optional[str], str]], st: _Stmt,
              elem: str) -> List[Tuple[Optional[str], str]]:
        """Remove OFF and device initial conditions (IC=a,b,..., VBE= VCE= VDS= VGS=), warned."""
        out = []
        k = 0
        while k < len(rest):
            f = rest[k]
            k += 1
            if _bare(f) and f[1].lower() == "off":
                self.warn(st.origin, "%s: OFF ignored (an initial-guess hint vamos does not pass "
                          "on)" % elem)
            elif f[0] is not None and f[0].lower() in ("ic", "vbe", "vce", "vds", "vgs"):
                vals = [f[1]]
                if f[0].lower() == "ic":                    # IC=vds,vgs,vbs: one value per field
                    while k < len(rest) and _bare(rest[k]) and _FAST_NUM.match(rest[k][1]):
                        vals.append(rest[k][1])
                        k += 1
                self.warn(st.origin, "%s: %s=%s ignored (device initial conditions are not "
                          "passed on; use .ic)" % (elem, f[0].upper(), ",".join(vals)))
            else:
                out.append(f)
        return out

    # -- R C L

    def e_rcl(self, scope, st, name, orig, fields):
        kind = orig[0].lower()
        nodes = self.nodes(fields, 2, orig)
        rest = list(fields[2:])
        master: Optional[str] = None
        value: Optional[Expr] = None
        params: Dict[str, Expr] = {}
        k = 0
        prim = {"r": "resistor", "c": "capacitor", "l": "inductor"}[kind] if self.sp else ""
        if k < len(rest) and _bare(rest[k]):
            word = rest[k][1]
            if word.lower() == "poly":
                raise _Defer("%s: the POLY form is not supported" % orig)
            # HSPICE: a model of that name wins over a parameter of the same name
            if self.find_model(scope, self.fold(word)) is not None:
                master = self.need_model(scope, word, (kind,), orig)
                k += 1
            elif self.sp and _IDENT.match(word) and not self.is_param_name(scope, self.fold(word)) and \
                    self.fold(word) not in E._SPECTRE_CONSTANTS_LOWER and \
                    self.fold(word) not in ("temp", "tnom", "temper", "time", "hertz"):
                # a field that names nothing is a model name (UG p.45); spectre.py reports pending names
                master = self.fold(word)
                self.pending.append(("model", master, st.origin))
                k += 1
        if k < len(rest) and _bare(rest[k]):
            value = self.value(scope, rest[k][1], orig, "value")
            k += 1
        for tc in ("tc1", "tc2"):
            if k < len(rest) and _bare(rest[k]):
                params[tc] = self.value(scope, rest[k][1], orig, tc)
                k += 1
        bad = {"c": ("q", "ctype", "poly"), "l": ("nt", "ltype", "poly", "r"), "r": ("c",)}[kind]
        for key, _ in rest[k:]:
            b = (key or "").lower()
            if b in bad:
                what = {"q": "a charge-defined capacitor (Q=)", "ctype": "CTYPE=",
                        "nt": "a magnetic-core inductor (NT=)", "ltype": "LTYPE=",
                        "c": "a resistor with a capacitance to bulk (C=)",
                        "r": "an inductor with a series resistance (R=)",
                        "poly": "the POLY form"}[b]
                raise _Defer("%s: %s is not supported" % (orig, what))
        kv = self.keyed(scope, rest[k:], orig)
        vkey = kind
        if vkey in kv:
            if value is not None:
                raise _Defer("%s: the value is given twice" % orig)
            value = kv.pop(vkey)
        if "ac" in kv:
            self.note(st.origin, "%s: AC=%s ignored (AC analysis only)" % (orig, E.to_text(kv["ac"])))
            kv.pop("ac")
        for tc in ("tc1", "tc2"):
            if tc in kv and tc in params:
                raise _Defer("%s: %s is given twice" % (orig, tc))
        params.update(kv)
        if value is None and master is None:
            raise _Defer("%s needs a value or a model" % orig)
        if "scale" in params:
            if self.sp:                                     # HSPICE scales the value; Spectre w and l (§4.3)
                raise _Defer("%s: scale= on a SPICE-mode %s is an error in v1 (HSPICE multiplies the value "
                             "by it, Spectre's resistor scale acts on w and l)" % (orig, kind.upper()))
            if value is None:
                raise _Defer("%s: SCALE= on a model-only element is not supported" % orig)
            value = _mul(value, params.pop("scale"))
        if self.sp:
            self.spectre_keys(scope, st, orig, kind, prim, params)
        return Instance(name, kind, nodes, master=master, value=value, params=params,
                        origin=st.origin, prim=prim)

    # -- K

    def e_k(self, scope, st, name, orig, fields):
        bare = []
        k = 0
        while k < len(fields) and _bare(fields[k]):
            bare.append(fields[k][1])
            k += 1
        kv = self.keyed(scope, fields[k:], orig)
        if len(bare) == 3 and "k" not in kv:
            coupling = self.value(scope, bare[2], orig, "coupling")
        elif len(bare) == 2 and "k" in kv:
            coupling = kv.pop("k")
        else:
            raise _Defer("%s: write K<name> L1 L2 coupling (magnetic cores and more than two "
                         "inductors are not supported)" % orig)
        if kv:
            raise _Defer("%s: %s not supported" % (orig, " ".join(sorted(kv))))
        ctrl = [self.fold(b) for b in bare[:2]]
        return Instance(name, "k", [], value=coupling, ctrl=ctrl, origin=st.origin,
                        prim="mutual_inductor" if self.sp else "")

    # -- V I

    def e_vi(self, scope, st, name, orig, fields):
        kind = orig[0].lower()
        nodes = self.nodes(fields, 2, orig)
        rest = list(fields[2:])
        src = Source()
        params: Dict[str, Expr] = {}
        wave: Optional[str] = None
        wargs: List[Expr] = []
        wkeys: Dict[str, Expr] = {}
        i = 0

        def take_value(what: str) -> Expr:
            nonlocal i
            if i >= len(rest) or not _bare(rest[i]):
                raise _Defer("%s: %s needs a value" % (orig, what))
            v = self.value(scope, rest[i][1], orig, what)
            i += 1
            return v

        def is_kw(f) -> bool:
            if not _bare(f):
                return True
            low = f[1].lower()
            m = _CALLISH.match(f[1])
            head = m.group(1).lower() if m else low
            return low in ("dc", "ac") or head in _SOURCE_FUNCS or head in _SOURCE_FUNCS_BAD

        while i < len(rest):
            key, text = rest[i]
            if key is not None:
                k = key.lower()
                i += 1
                if k == "dc":
                    if src.dc is not None:
                        raise _Defer("%s: DC is given twice" % orig)
                    src.dc = self.value(scope, text, orig, "DC")
                elif k == "ac":
                    mag = self.value(scope, text, orig, "AC")
                    ph: Expr = Num(0.0)
                    if i < len(rest) and not is_kw(rest[i]):
                        ph = take_value("AC phase")
                    src.ac = (mag, ph)
                elif k == "m" and kind == "i":
                    params["m"] = self.value(scope, text, orig, "M")
                elif k in ("td", "r") and wave in ("pwl", "pl"):
                    wkeys[k] = self.value(scope, text, orig, k.upper())
                else:
                    raise _Defer("%s: %s= is not supported on a %s source" % (orig, key,
                                                                          "voltage" if kind == "v"
                                                                          else "current"))
                continue
            low = text.lower()
            m = _CALLISH.match(text)
            head = m.group(1).lower() if m else low
            if low == "dc":
                i += 1
                if src.dc is not None:
                    raise _Defer("%s: DC is given twice" % orig)
                src.dc = take_value("DC")
            elif low == "ac":
                i += 1
                mag = take_value("AC")
                ph = Num(0.0)
                if i < len(rest) and not is_kw(rest[i]):
                    ph = take_value("AC phase")
                src.ac = (mag, ph)
            elif head in _SOURCE_FUNCS_BAD:
                raise _Defer("%s: the %s source function is not supported" % (orig, head.upper()))
            elif head in _SOURCE_FUNCS:
                if wave is not None:
                    raise _Defer("%s has two transient functions" % orig)
                wave = _SOURCE_FUNCS[head]
                i += 1
                if m:
                    args_fields = _lex(m.group(2))
                elif i < len(rest) and _bare(rest[i]) and _unparen(rest[i][1]) is not None:
                    args_fields = _lex(_unparen(rest[i][1]))
                    i += 1
                else:
                    args_fields = []
                    while i < len(rest) and not is_kw(rest[i]) and not \
                            (wave in ("pwl", "pl") and rest[i][1].lower() == "r"):
                        args_fields.append(rest[i])
                        i += 1
                for ak, at in args_fields:
                    if ak is not None:
                        akl = ak.lower()
                        if wave in ("pwl", "pl") and akl in ("td", "r"):
                            wkeys[akl] = self.value(scope, at, orig, ak.upper())
                        else:
                            raise _Defer("%s: %s= inside %s()" % (orig, ak, head.upper()))
                    elif wave in ("pwl", "pl") and at.lower() == "r":
                        wkeys["r"] = Num(0.0)
                    else:
                        wargs.append(self.value(scope, at, orig, head.upper() + " argument"))
                if wave in ("pwl", "pl") and i < len(rest) and _bare(rest[i]) and \
                        rest[i][1].lower() == "r":
                    wkeys["r"] = Num(0.0)
                    i += 1
            elif i == 0 or (src.dc is None and wave is None and src.ac is None):
                if src.dc is not None:
                    raise _Defer("%s: unexpected %r" % (orig, text))
                src.dc = self.value(scope, text, orig, "value")
                i += 1
            else:
                raise _Defer("%s: unexpected %r" % (orig, text))
        if wave is not None:
            src.wave = "pwl" if wave == "pl" else wave
        elif wkeys:
            raise _Defer("%s: TD=/R= without a PWL function" % orig)
        if self.sp:                                         # §4.3 Sources: Spectre names, resolve_source later
            src.spectre = self.spectre_source(orig, src, wave, wargs, wkeys)
            self.spectre_keys(scope, st, orig, kind, "vsource" if kind == "v" else "isource", params)
            return Instance(name, kind, nodes, params=params, source=src, origin=st.origin,
                            prim="vsource" if kind == "v" else "isource")
        inst = Instance(name, kind, nodes, params=params, source=src, origin=st.origin)
        if wave is not None:
            scope.sources.append((inst, wave, wargs, wkeys))   # defaults resolved in pass 3
        return inst

    def spectre_source(self, orig: str, src: Source, wave: Optional[str], args: List[Expr],
                       keys: Dict[str, Expr]) -> Dict[str, object]:
        """A V or I line's fields in Spectre names (§4.3 Sources): DC -> dc; AC -> mag phase; PULSE ->
        type=pulse val0 val1 delay rise fall width period; SIN -> type=sine sinedc ampl freq delay damp
        sinephase; EXP -> type=exp val0 val1 td1 tau1 td2 tau2; PWL -> type=pwl wave=[...] delay.
        spectre.resolve_source then applies §3.8.1."""
        out: Dict[str, object] = {}
        if src.dc is not None:
            out["dc"] = src.dc
        if src.ac is not None:
            out["mag"], out["phase"] = src.ac
        if wave is None:
            return out
        if wave in ("pwl", "pl"):
            if "r" in keys:
                raise _Defer("%s: PWL R= (a repeating PWL) is not supported" % orig)
            if len(args) % 2 or not args:
                raise _Defer("%s: PWL needs time-value pairs" % orig)
            pairs = [(args[k], args[k + 1]) for k in range(0, len(args), 2)]
            if wave == "pl":
                pairs = [(t, v) for v, t in pairs]
            out["type"] = Str("pwl")
            out["wave"] = [x for pair in pairs for x in pair]
            if "td" in keys:
                out["delay"] = keys["td"]
            return out
        lo, hi = _SOURCE_ARITY[wave]
        if not lo <= len(args) <= hi:
            raise _Defer("%s: %s takes %d to %d values, got %d" % (orig, wave.upper(), lo, hi, len(args)))
        out["type"] = Str("sine" if wave == "sin" else wave)
        for sname, value in zip(_SPICE_SOURCE_NAMES[wave], args):
            out[sname] = value
        if wave == "sin" and "freq" not in out:
            raise _Defer("%s: SIN without a frequency: SPICE3 reads 1/TSTOP, a converted Spectre sine freq=0 "
                         "(a constant); give the frequency (§14 q.38)" % orig)
        return out

    # -- E G

    def e_eg(self, scope, st, name, orig, fields):
        kind = orig[0].lower()
        nodes = self.nodes(fields, 2, orig)
        rest = list(fields[2:])
        beh_keys = ("vol", "value") if kind == "e" else ("cur", "value")
        beh = [f for f in rest if f[0] is not None and f[0].lower() in ("vol", "cur", "value")]
        if beh:
            k = beh[0][0].lower()
            if len(beh) > 1:
                raise _Defer("%s: more than one of VOL=, CUR=, VALUE=" % orig)
            if k not in beh_keys:
                raise _Defer("%s: %s= belongs on %s" % (orig, beh[0][0].upper(),
                                                       "a G element" if k == "cur" else "an E element"))
            if not beh[0][1]:
                raise _Defer("%s: %s= has no value" % (orig, beh[0][0].upper()))
            try:
                ex = self.expr(beh[0][1], scope, "behavioral")
            except _Defer as exc:
                raise _Defer("%s: %s" % (orig, exc))
            kv = self.keyed(scope, [f for f in rest if f is not beh[0]], orig)
            params: Dict[str, Expr] = {}
            for bad in ("max", "min", "tc1", "tc2", "abs", "ic", "smooth"):
                if bad in kv:
                    raise _Defer("%s: %s= is not supported" % (orig, bad.upper()))
            if "scale" in kv:
                if self.sp:
                    raise _Defer("%s: scale= on a SPICE-mode %s is an error in v1" % (orig, kind.upper()))
                ex = _mul(ex, kv.pop("scale"))
            if "m" in kv:
                if kind == "e":
                    raise _Defer("%s: M= is not allowed on an E element" % orig)
                params["m"] = kv.pop("m")
            if kv:
                raise _Defer("%s: %s not supported" % (orig, " ".join(sorted(kv))))
            return Instance(name, "b", nodes, params=params, expr=ex,
                            expr_kind="v" if kind == "e" else "i", origin=st.origin,
                            prim=("vcvs" if kind == "e" else "vccs") if self.sp else "")
        if rest and _bare(rest[0]) and rest[0][1].lower() == ("vcvs" if kind == "e" else "vccs"):
            rest = rest[1:]
        if rest and _bare(rest[0]):
            m = _CALLISH.match(rest[0][1])
            head = (m.group(1) if m else rest[0][1]).lower()
            if head in _E_FORMS_BAD:
                raise _Defer("%s: the %s form is not supported (write VOL= / CUR=)"
                             % (orig, head.upper()))
        bare = []
        k = 0
        while k < len(rest) and _bare(rest[k]):
            bare.append(rest[k][1])
            k += 1
        if len(bare) != 3:
            raise _Defer("%s: write %s n+ n- in+ in- %s, or %s='expression'"
                         % (orig, orig[0].upper(), "gain" if kind == "e" else "transconductance",
                            "VOL" if kind == "e" else "CUR"))
        cnodes = [self.node_text(b, terminal=True) for b in bare[:2]]
        gain = self.value(scope, bare[2], orig, "gain")
        kv = self.keyed(scope, rest[k:], orig)
        params = {}
        for bad in ("max", "min", "tc1", "tc2", "abs", "ic"):
            if bad in kv:
                raise _Defer("%s: %s= is not supported" % (orig, bad.upper()))
        if "scale" in kv:
            if self.sp:
                raise _Defer("%s: scale= on a SPICE-mode %s is an error in v1" % (orig, kind.upper()))
            gain = _mul(gain, kv.pop("scale"))
        if "m" in kv:
            if kind == "e":
                raise _Defer("%s: M= is not allowed on an E element" % orig)
            params["m"] = kv.pop("m")
        if kv:
            raise _Defer("%s: %s not supported" % (orig, " ".join(sorted(kv))))
        return Instance(name, kind, nodes + cnodes, value=gain, params=params, origin=st.origin,
                        prim=("vcvs" if kind == "e" else "vccs") if self.sp else "")

    # -- F H

    def e_fh(self, scope, st, name, orig, fields):
        kind = orig[0].lower()
        nodes = self.nodes(fields, 2, orig)
        rest = list(fields[2:])
        if rest and _bare(rest[0]) and rest[0][1].lower() == ("cccs" if kind == "f" else "ccvs"):
            rest = rest[1:]
        if rest and _bare(rest[0]):
            m = _CALLISH.match(rest[0][1])
            head = (m.group(1) if m else rest[0][1]).lower()
            if head in _E_FORMS_BAD:
                raise _Defer("%s: the %s form is not supported" % (orig, head.upper()))
        bare = []
        k = 0
        while k < len(rest) and _bare(rest[k]):
            bare.append(rest[k][1])
            k += 1
        if len(bare) != 2:
            raise _Defer("%s: write %s n+ n- vsource %s" % (orig, orig[0].upper(),
                                                            "gain" if kind == "f" else "transresistance"))
        gain = self.value(scope, bare[1], orig, "gain")
        kv = self.keyed(scope, rest[k:], orig)
        params = {}
        for bad in ("max", "min", "tc1", "tc2", "abs", "ic"):
            if bad in kv:
                raise _Defer("%s: %s= is not supported" % (orig, bad.upper()))
        if "scale" in kv:
            if self.sp:
                raise _Defer("%s: scale= on a SPICE-mode %s is an error in v1" % (orig, kind.upper()))
            gain = _mul(gain, kv.pop("scale"))
        if "m" in kv:
            if kind == "h":
                raise _Defer("%s: M= is not allowed on an H element" % orig)
            params["m"] = kv.pop("m")
        if kv:
            raise _Defer("%s: %s not supported" % (orig, " ".join(sorted(kv))))
        return Instance(name, kind, nodes, value=gain, params=params, ctrl=[self.fold(bare[0])],
                        origin=st.origin, prim=("cccs" if kind == "f" else "ccvs") if self.sp else "")

    # -- D Q J M

    def device(self, scope, st, orig, rest, positional: Sequence[str], value_key: Optional[str]):
        """Positional values after the model, flags and keyed parameters of a device."""
        rest = self.flags(list(rest), st, orig)
        params: Dict[str, Expr] = {}
        k = 0
        for p in positional:
            if k < len(rest) and _bare(rest[k]):
                params[p] = self.value(scope, rest[k][1], orig, p)
                k += 1
        kv = self.keyed(scope, rest[k:], orig)
        for p in list(kv):
            if p in params:
                raise _Defer("%s: %s is given twice" % (orig, p))
        params.update(kv)
        value = params.pop(value_key, None) if value_key else None
        return value, params

    def device_model(self, scope, fields, k, kinds, orig, letter) -> Tuple[str, str]:
        """(IR master, Instance.prim) of the model field k of a device line (§4.3)."""
        if self.sp and self.find_model(scope, self.fold(fields[k][1])) is None and _IDENT.match(fields[k][1]):
            name = self.fold(fields[k][1])                  # defined nowhere: pending (spectre.py reports it)
            self.pending.append(("model", name, self.cur_origin))
            return name, ""
        master = self.need_model(scope, fields[k][1], kinds, orig)
        return master, self.card_prim(scope, master) if self.sp else ""

    def e_d(self, scope, st, name, orig, fields):
        nodes = self.nodes(fields, 2, orig)
        if len(fields) < 3 or not _bare(fields[2]):
            raise _Defer("%s needs a model" % orig)
        master, prim = self.device_model(scope, fields, 2, ("d",), orig, "d")
        value, params = self.device(scope, st, orig, fields[3:], ("area", "pj"), "area")
        if self.sp:
            self.spectre_keys(scope, st, orig, "d", prim, params)
        return Instance(name, "d", nodes, master=master, value=value, params=params, origin=st.origin,
                        prim=prim)

    def three_or_four(self, scope, fields, kinds, orig, four_ok: bool = True) -> int:
        """How many nodes before the model of a Q/J line (3, or 4 with a substrate/bulk).  In
        spectre-spice the fourth field is a terminal unless a model has that name or it is the
        last field (UG p.52); the model then names nothing defined (pending)."""
        def is_model(k):
            return k < len(fields) and _bare(fields[k]) and \
                self.model_kind(scope, self.fold(fields[k][1])) is not None
        if is_model(3):
            return 3
        if four_ok and is_model(4):
            return 4
        if self.sp and len(fields) >= 4 and all(_bare(f) for f in fields[:4]):
            if len(fields) == 4 or not _bare(fields[4]):
                return 3
            if four_ok:
                return 4
        if len(fields) > 3 and _bare(fields[3]):
            self.need_model(scope, fields[3][1], kinds, orig)   # raises: not found
        raise _Defer("%s needs nodes and a model" % orig)

    def e_q(self, scope, st, name, orig, fields):
        n = self.three_or_four(scope, fields, ("npn", "pnp"), orig)
        nodes = self.nodes(fields, n, orig)
        master, prim = self.device_model(scope, fields, n, ("npn", "pnp"), orig, "q")
        value, params = self.device(scope, st, orig, fields[n + 1:], ("area",), "area")
        if self.sp:
            self.spectre_keys(scope, st, orig, "q", prim, params)
        return Instance(name, "q", nodes, master=master, value=value, params=params, origin=st.origin,
                        prim=prim)

    def e_j(self, scope, st, name, orig, fields):
        n = self.three_or_four(scope, fields, ("njf", "pjf"), orig)
        if n == 4:
            raise _Defer("%s: a JFET bulk node is not supported" % orig)
        nodes = self.nodes(fields, 3, orig)
        master, prim = self.device_model(scope, fields, 3, ("njf", "pjf"), orig, "j")
        value, params = self.device(scope, st, orig, fields[4:], ("area",), "area")
        if self.sp:
            self.spectre_keys(scope, st, orig, "j", prim, params)
        return Instance(name, "j", nodes, master=master, value=value, params=params, origin=st.origin,
                        prim=prim)

    def e_m(self, scope, st, name, orig, fields):
        def is_mos(k):
            return k < len(fields) and _bare(fields[k]) and \
                self.model_kind(scope, self.fold(fields[k][1])) is not None
        if not is_mos(4) and is_mos(3):
            raise _Defer("%s: the bulk node is omitted (HSPICE then takes it from the model's "
                         "BULK parameter); give it explicitly" % orig)
        if len(fields) < 5 or not _bare(fields[4]):
            raise _Defer("%s needs 4 nodes and a model" % orig)
        nodes = self.nodes(fields, 4, orig)
        master, prim = self.device_model(scope, fields, 4, ("nmos", "pmos"), orig, "m")
        pos = ("w", "l") if self.wl else ("l", "w")
        _, params = self.device(scope, st, orig, fields[5:], pos, None)
        if self.sp:                                         # no DEFL/DEFW: §3.9 fills the geometry
            self.spectre_keys(scope, st, orig, "m", prim, params)
            return Instance(name, "m", nodes, master=master, params=params, origin=st.origin, prim=prim)
        for opt, key in _OPT_MOSDEF.items():
            if key in params:
                continue
            given = self.opt.get(opt)
            if given is not None and given[0] is not None:
                params[key] = self.value(scope, given[0], orig, opt)
            elif key in ("l", "w"):
                params[key] = Num(HSPICE_DEFL if key == "l" else HSPICE_DEFW)
                self.note(st.origin, "%s: %s not given; the HSPICE default %s=%g m is used"
                          % (orig, key.upper(), opt.upper(), HSPICE_DEFL))
        return Instance(name, "m", nodes, master=master, params=params, origin=st.origin)

    # -- X

    def e_x(self, scope, st, name, orig, fields):
        bare = []
        k = 0
        while k < len(fields) and _bare(fields[k]) and fields[k][1].lower() != "params:":
            bare.append(fields[k][1])
            k += 1
        rest = [f for f in fields[k:] if not (_bare(f) and f[1].lower() == "params:")]
        if rest and _bare(rest[-1]) and all(not _bare(f) for f in rest[:-1]):
            # 'X1 a b p=1 sub': the subckt name after the parameters (ngspice libraries
            # such as sky130 write it; HSPICE syntax cannot be read differently)
            bare.append(rest[-1][1])
            rest = rest[:-1]
        if not bare:
            raise _Defer("%s needs nodes and a subckt name" % orig)
        master_text = bare[-1]
        actual_texts = bare[:-1]
        master = self.fold(master_text)
        params: Dict[str, Expr] = {}
        for key, text in rest:
            if key is None:
                raise _Defer("%s: unexpected %r after the parameters" % (orig, text))
            if text == "":
                raise _Defer("%s: %s has no value" % (orig, key))
            pk = "m" if key.lower() == "m" else self.fold(key)
            if not _IDENT.match(key):
                raise _Defer("%s: %r is not a parameter name" % (orig, key))
            if pk in params:
                raise _Defer("%s: %s is given twice" % (orig, key))
            try:
                params[pk] = self.expr(text, scope, "value")
            except _Defer as exc:
                raise _Defer("%s: %s=%s" % (orig, key, exc))
        target = self.find_subckt(scope, master)
        actuals = [self.node_text(a, terminal=True) for a in actual_texts]
        if target is None and self.resolver is not None:
            decl = self.resolver.subckt(master)
            if decl is not None:                            # a subckt of another region or language (§4.3)
                if decl.ports is not None and len(actuals) != decl.ports:
                    raise _Defer("%s has %d node%s but subckt %s has %d port%s (%s)"
                                 % (orig, len(actuals), "" if len(actuals) == 1 else "s", master_text,
                                    decl.ports, "" if decl.ports == 1 else "s", decl.origin))
                for pk in params:
                    if pk != "m" and pk not in decl.params:
                        raise _Defer("%s: %s is not a parameter of subckt %s's definition line (%s); an "
                                     "override must name one declared there, in lower case (UG pp.55, 97)"
                                     % (orig, pk, master_text, decl.origin))
                return Instance(name, "x", actuals, master=decl.name, params=params, origin=st.origin,
                                prim="subckt" if self.sp else "")
        if target is not None:
            if len(actuals) != len(target.orig_ports):
                raise _Defer("%s has %d node%s but subckt %s has %d port%s (%s)"
                             % (orig, len(actuals), "" if len(actuals) == 1 else "s", master_text,
                                len(target.orig_ports), "" if len(target.orig_ports) == 1 else "s",
                                target.stmt.origin))
            nodes = []
            for i, a in enumerate(actuals):
                if i in target.gnd_ports:
                    if a != "0":
                        raise _Defer("%s connects %s to port %s of subckt %s, which is ground "
                                     "inside the subckt (%s); connect it to ground"
                                     % (orig, actual_texts[i], target.orig_ports[i], master_text,
                                        target.stmt.origin))
                    continue
                nodes.append(a)
            for p, a in zip(target.ports, nodes):
                if p in self.global_set and a != p:
                    raise _Defer("%s binds port %s of subckt %s, a .global net, to %s; the port "
                                 "always joins the global net (port_connect -cell %s (%s => %s) "
                                 "states it)" % (orig, p, master_text, a, target.name, p, p))
            if self.sp:                                 # an override must name a header parameter (UG p.55)
                for pk in params:
                    if pk != "m" and pk not in target.header_params:
                        raise _Defer("%s: %s is not a parameter of .subckt %s's definition line (%s); an "
                                     "override must name one declared there (UG p.55)"
                                     % (orig, pk, master_text, target.stmt.origin))
            inst = Instance(name, "x", nodes, master=target.name, params=params, origin=st.origin,
                            prim="subckt" if self.sp else "")
            scope.xrefs.append((inst, target))
            return inst
        va = self.va_module(master_text)
        if va is not None:
            return Instance(name, "y", actuals, master=va, params=params, origin=st.origin,
                            prim=va if self.sp else "")
        for s in scope.chain():
            if master in s.va_cards:
                module, card, _ = s.va_cards[master]
                merged = dict(card)
                merged.update(params)                      # instance values win over the card's
                return Instance(name, "y", actuals, master=module, params=merged, origin=st.origin,
                                prim=module if self.sp else "")
        found = self.find_model(scope, master)
        if found is not None and found[0] == "bad":
            raise _Defer("%s: model %s is left out: %s" % (orig, master_text, found[1]))
        raise _Defer("%s: subckt %s not found" % (orig, master_text))

    # =========================================================================
    # Pass 2c: leave out subckts that cannot be simulated
    # =========================================================================

    def prune(self) -> None:
        scopes: List[_Scope] = []

        def collect(s: _Scope) -> None:
            for it in s.out:
                if isinstance(it, _Scope):
                    scopes.append(it)
                    collect(it)
        collect(self.top)
        bad: Dict[int, Note] = {}
        changed = True
        while changed:
            changed = False
            for s in scopes:
                if id(s) in bad:
                    continue
                why: Optional[Note] = None
                if s.deferred:
                    d = s.deferred[0]
                    why = Note(ERROR, d.origin, d.message)
                elif s.parent is not None and id(s.parent) in bad:
                    why = bad[id(s.parent)]
                else:
                    for inst, target in s.xrefs:
                        if id(target) in bad:
                            why = Note(ERROR, inst.origin, "%s instantiates subckt %s, which is left "
                                       "out" % (inst.name, target.name))
                            break
                if why is not None:
                    bad[id(s)] = why
                    changed = True
        self.cycles([s for s in scopes if id(s) not in bad], bad)
        for inst, target in self.top.xrefs:
            if id(target) in bad:
                why = bad[id(target)]
                self.err(inst.origin, "%s: subckt %s cannot be simulated by vamos: %s (%s)"
                         % (inst.name, target.name, why.message, why.origin))
        for s in scopes:
            if id(s) in bad and (s.parent is None or id(s.parent) not in bad):
                why = bad[id(s)]
                n = note(s.stmt.origin, "subckt %s is left out: %s (%s); instantiating it is an error"
                         % (s.name, why.message, why.origin))
                self.notes.append(n)
                if s.parent is self.top:
                    self.left[s.name] = n
        self.bad = bad

    def cycles(self, scopes: List[_Scope], bad: Dict[int, Note]) -> None:
        """A subckt that instantiates itself, directly or not, is an error (one per cycle)."""
        state: Dict[int, int] = {}                 # 1 on the DFS stack, 2 done
        stack: List[_Scope] = []

        def visit(x: _Scope) -> None:
            state[id(x)] = 1
            stack.append(x)
            for inst, t in x.xrefs:
                if id(t) in bad:
                    continue
                if state.get(id(t)) == 1:
                    k = next(j for j, s in enumerate(stack) if s is t)
                    names = [s.name for s in stack[k:]] + [t.name]
                    self.err(inst.origin, "subckt %s instantiates itself: %s"
                             % (t.name, " -> ".join(names)))
                elif id(t) not in state:
                    visit(t)
            stack.pop()
            state[id(x)] = 2

        for s in scopes:
            if id(s) not in state:
                visit(s)

    def kept(self, s: _Scope) -> bool:
        return id(s) not in self.bad

    def kept_scopes(self) -> List[_Scope]:
        out: List[_Scope] = []

        def walk(s: _Scope) -> None:
            out.append(s)
            for it in s.out:
                if isinstance(it, _Scope) and self.kept(it):
                    walk(it)
        walk(self.top)
        return out

    # =========================================================================
    # Pass 3: parameters
    # =========================================================================

    def parameters(self) -> None:
        scopes = self.kept_scopes()
        top_names: Dict[str, str] = {}
        for s in scopes:
            s.sorted_params = self.dedup_sort(s)
            if s is self.top:
                top_names = {p.name: p.origin for p in s.sorted_params}
        self.evaluate_top()
        for s in scopes:                       # a level given by a top-level parameter
            for m in s.models.values():
                if m.level is None and "level" in m.params:
                    m.level = self.const(m.params["level"], s)
        local = self.parhier == "local" or self.opts.parhier_local
        self.parhier_eff = "local" if local else "global"
        if self.parhier == "local":
            return
        for s in scopes:
            if s is self.top:
                continue
            for p in s.sorted_params:
                if p.name in top_names:
                    self.collision(p.name, top_names[p.name], p.origin, "subckt %s" % s.name, local)
            for inst in s.out:
                if isinstance(inst, Instance) and inst.kind == "x":
                    self.x_collisions(inst, top_names, local)
        for inst in self.top.out:
            if isinstance(inst, Instance) and inst.kind == "x":
                self.x_collisions(inst, top_names, local)

    def x_collisions(self, inst: Instance, top_names: Dict[str, str], local: bool) -> None:
        for k in inst.params:
            if k != "m" and k in top_names:
                self.collision(k, top_names[k], inst.origin, "the override on %s" % inst.name, local)

    def collision(self, name: str, top_origin: str, origin: str, where: str, local: bool) -> None:
        if local:
            self.note(origin, "parameter %s is defined at top level (%s) and in %s; simulated with "
                      "local scoping (--vamos-parhier=local): the inner definition wins"
                      % (name, top_origin, where))
        else:
            self.err(origin, "parameter %s is defined at top level (%s) and in %s: under "
                     "PARHIER=GLOBAL (the HSPICE default) the top-level value wins, which vamos does "
                     "not emulate; rename one, set .option parhier=local, or give "
                     "--vamos-parhier=local" % (name, top_origin, where))

    def dedup_sort(self, s: _Scope) -> List[Param]:
        last: Dict[str, int] = {}
        for k, (name, _, origin) in enumerate(s.params):
            if name in last:
                self.note(origin, "parameter %s is defined again (first at %s); the last definition "
                          "is used everywhere in %s" % (name, s.params[last[name]][2],
                                                        "subckt " + s.name if s.name else "the netlist"))
            last[name] = k
        keep = [s.params[k] for k in sorted(last.values())]
        index = {name: i for i, (name, _, _) in enumerate(keep)}
        deps: List[List[int]] = []
        users: List[List[int]] = [[] for _ in keep]
        indeg = [0] * len(keep)
        for i, (name, e, _) in enumerate(keep):
            d = sorted({index[n] for n in E.names(e) if n in index})
            deps.append(d)
            for j in d:
                users[j].append(i)
            indeg[i] = len(d)
        heap = [i for i in range(len(keep)) if indeg[i] == 0]
        heapq.heapify(heap)
        order: List[int] = []
        while heap:
            i = heapq.heappop(heap)
            order.append(i)
            for u in users[i]:
                indeg[u] -= 1
                if indeg[u] == 0:
                    heapq.heappush(heap, u)
        if len(order) < len(keep):
            stuck = [keep[i][0] for i in range(len(keep)) if indeg[i] > 0]
            cyc = self.find_cycle(stuck, keep, index)
            self.err(keep[index[cyc[0]]][2], "parameters depend on each other in a cycle: %s"
                     % " -> ".join(cyc + [cyc[0]]))
            order += [i for i in range(len(keep)) if indeg[i] > 0]
        return [Param(keep[i][0], keep[i][1], keep[i][2]) for i in order]

    @staticmethod
    def find_cycle(stuck: List[str], keep, index) -> List[str]:
        stuck_set = set(stuck)
        start = stuck[0]
        path: List[str] = []
        seen: Dict[str, int] = {}
        cur = start
        while cur not in seen:
            seen[cur] = len(path)
            path.append(cur)
            nxt = [n for n in E.names(keep[index[cur]][1]) if n in stuck_set]
            if not nxt:
                return path
            cur = sorted(nxt)[0]
        return path[seen[cur]:]

    def evaluate_top(self) -> None:
        names = {p.name for p in self.top.sorted_params}
        symbolic: Set[str] = set()
        for p in self.top.sorted_params:
            used = E.names(p.expr)
            unknown = sorted(n for n in used if n not in names)
            if unknown:
                self.err(p.origin, "parameter %s uses %s, which %s not defined at top level"
                         % (p.name, ", ".join(unknown), "is" if len(unknown) == 1 else "are"))
                symbolic.add(p.name)
                continue
            if used & symbolic or self.uses_temper(p.expr):
                symbolic.add(p.name)
                continue
            if isinstance(p.expr, Str):
                symbolic.add(p.name)
                continue
            try:
                self.values[p.name] = E.evaluate(p.expr, self.values)
            except E.EvalError as exc:
                self.err(p.origin, "parameter %s: %s" % (p.name, exc))
                symbolic.add(p.name)

    @staticmethod
    def uses_temper(e: Expr) -> bool:
        return any(isinstance(x, Name) and x.name == "temper" for x in E.walk(e))

    # =========================================================================
    # Pass 3: analyses, sources, ground, controls
    # =========================================================================

    def eval_top(self, text: str, origin: str, what: str) -> Optional[float]:
        try:
            e = self.expr(text, self.top, "value")
            return E.evaluate(e, self.values)
        except _Defer as exc:
            self.err(origin, "%s: %s" % (what, exc))
        except E.EvalError as exc:
            self.err(origin, "%s: %s" % (what, exc))
        return None

    def analyses(self) -> List[Analysis]:
        out: List[Analysis] = []
        for canon, st in self.controls:
            if canon == ".tran":
                a = self.tran(st)
                if a is not None:
                    out.append(a)
            elif canon in (".op", ".dc", ".ac"):
                out.append(Analysis(canon[1:], {}, st.origin))
        first = next((a for a in out if a.kind == "tran"), None)
        if first is not None:
            self.tstep = float(first.args["step"])
            self.tstop = float(first.args["stop"])
        self.max_step(first)
        return out

    def max_step(self, first: Optional[Analysis]) -> None:
        """The first .tran's maxstep: HSPICE's maximum internal timestep (module docstring).

        Left to themselves the engines bound their step by the run length only (VACASK
        (stop-start)/50, Xyce stop/10), so one step can straddle a whole transition and the
        crossing that the rawfile and nvc's A2D interpolate from it comes late (§9 e2e 6:
        a 0.34 ns step across a 0.45 ns inverter edge, 20 ps late; HSPICE takes 5 ps steps)."""
        delmax = self.opt.get("delmax")
        rmax = self.opt.get("rmax")
        if delmax is not None:
            if rmax is not None:
                self.note(rmax[1], ".option rmax ignored: .option delmax sets the maximum time step")
            v = self.positive_option("delmax", delmax[0], delmax[1])
            if v is None:
                return
            if first is None:
                # the analysis vamos synthesises (ams/deck.py _analysis) takes it, unless
                # --vamos-analog-maxstep, which is explicit, names another
                self.synth_delmax = v
                self.note(delmax[1], ".option delmax=%s: the maximum time step of the analysis "
                          "vamos synthesises (the netlist has no .tran)" % delmax[0])
            else:
                first.args["maxstep"] = v
            return
        if first is None:
            if rmax is not None:
                self.note(rmax[1], ".option rmax ignored: the netlist has no .tran (the analysis "
                          "vamos synthesises takes its maximum time step from --vamos-analog-maxstep)")
            return
        got = self.rmax()
        if got is None:
            return
        r, why = got
        tstep = self.tran_inc.get(id(first), float(first.args["step"]))
        stop = float(first.args["stop"])
        v = float("%.12g" % min(stop / TRAN_MIN_POINTS, r * tstep))     # 1e-7/50: 2e-09, not ...97e-09
        first.args["maxstep"] = v
        self.note(first.origin, ".tran: maximum time step %g s, HSPICE's bound without .option delmax: "
                  "min(TSTOP/50, RMAX*TSTEP) = min(%g, %g*%g) with %s; .option delmax or "
                  "--vamos-analog-maxstep sets another" % (v, stop / TRAN_MIN_POINTS, r, tstep, why))

    def rmax(self) -> Optional[Tuple[float, str]]:
        """HSPICE's RMAX and where it comes from; None after an error (module docstring).

        The options are applied in order, the last setting winning, as the manual documents
        for METHOD=GEAR (9-49): METHOD=GEAR sets LVLTIM=2; ACCURATE sets DVDT=2, LVLTIM=3 and
        RMAX=2 (11-27); DVDT=3 sets LVLTIM=1 and RMAX=2 (11-35).  RMAX never set: 5 under
        DVDT=4 and LVLTIM=1 (HSPICE's defaults, 9-44 and 9-48), else 2 (9-46)."""
        dvdt, lvltim = 4.0, 1.0
        given: Optional[float] = None
        given_by = ""
        causes: List[str] = []
        for key, val, origin in self.options:
            if key == "rmax":
                given = self.positive_option(key, val, origin)
                if given is None:
                    return None
                given_by = ".option rmax"
            elif key == "accurate":
                on = 1.0 if val is None else self.eval_top(val, origin, ".option accurate")
                if on is None:
                    return None
                if on:
                    dvdt, lvltim, given, given_by = 2.0, 3.0, RMAX_OTHER, ".option accurate"
                    causes.append(given_by)
            elif key == "method":
                if val is not None and val.strip("'\"").lower() == "gear":
                    lvltim = 2.0
                    causes.append(".option method=gear")
            elif key in ("dvdt", "lvltim"):
                if val is None:
                    self.err(origin, ".option %s needs a value" % key)
                    return None
                v = self.eval_top(val, origin, ".option " + key)
                if v is None:
                    return None
                causes.append(".option %s=%s" % (key, val))
                if key == "lvltim":
                    lvltim = v
                else:
                    dvdt = v
                    if v == 3.0:
                        lvltim, given, given_by = 1.0, RMAX_OTHER, causes[-1]
        if given is not None:
            return given, "RMAX=%g (%s)" % (given, given_by)
        r = RMAX_DVDT4 if (dvdt, lvltim) == (4.0, 1.0) else RMAX_OTHER
        why = "RMAX=%g, its default under DVDT=%g and LVLTIM=%g" % (r, dvdt, lvltim)
        if causes:
            why += " (%s)" % ", ".join(dict.fromkeys(causes))
        return r, why

    def positive_option(self, key: str, val: Optional[str], origin: str) -> Optional[float]:
        """A numeric .option that must be a positive number; None after an error."""
        if val is None:
            self.err(origin, ".option %s needs a value" % key)
            return None
        v = self.eval_top(val, origin, ".option " + key)
        if v is not None and not (0.0 < v < math.inf):
            self.err(origin, ".option %s=%s must be a positive number" % (key, val))
            return None
        return v

    def tran(self, st: _Stmt) -> Optional[Analysis]:
        try:
            fields = st.fields()[1:]
        except _LexError as exc:
            self.err(st.origin, ".tran: %s" % exc)
            return None
        pos: List[float] = []
        start: Optional[float] = None
        uic = False
        ok = True
        for key, text in fields:
            if key is None:
                low = text.lower()
                if low == "uic":
                    uic = True
                    continue
                if low in ("sweep", "data", "monte", "optimize"):
                    self.err(st.origin, ".tran %s is not supported" % text.upper())
                    return None
                v = self.eval_top(text, st.origin, ".tran")
                if v is None:
                    ok = False
                else:
                    pos.append(v)
            elif key.lower() == "start":
                start = self.eval_top(text, st.origin, ".tran START")
            else:
                self.err(st.origin, ".tran %s= is not supported" % key)
                return None
        if not ok:
            return None
        if len(pos) < 2:
            self.err(st.origin, ".tran needs a time increment and a stop time")
            return None
        if len(pos) % 2:
            if start is not None:
                self.err(st.origin, ".tran: the start time is given twice")
                return None
            start = pos.pop()
        pairs = [(pos[k], pos[k + 1]) for k in range(0, len(pos), 2)]
        for k, (inc, stop) in enumerate(pairs):
            if k and stop <= pairs[k - 1][1]:
                self.err(st.origin, ".tran: tstop values must increase (HSPICE reads .TRAN tincr1 "
                         "tstop1 tincr2 tstop2 ...; SPICE's tstart is START=)")
                return None
            if inc <= 0 or stop <= 0:
                self.err(st.origin, ".tran: increments and stop times must be positive")
                return None
        if len(pairs) > 1:
            self.note(st.origin, ".tran with %d intervals: simulated to %g s with the first increment"
                      % (len(pairs), pairs[-1][1]))
        start = start or 0.0
        if start >= pairs[-1][1] or start < 0:
            self.err(st.origin, ".tran: START must be in [0, tstop)")
            return None
        a = Analysis("tran", {"step": pairs[0][0], "stop": pairs[-1][1], "start": start, "uic": uic},
                     st.origin)
        self.tran_inc[id(a)] = min(inc for inc, _ in pairs)
        return a

    # -- sources

    def sources(self) -> None:
        for s in self.kept_scopes():
            for inst, wave, args, keys in s.sources:
                if s.insts.get(inst.name) is not inst:
                    continue
                try:
                    self.source(s, inst, wave, args, keys)
                except _Defer as exc:
                    self.err(inst.origin, str(exc))

    def source(self, scope: _Scope, inst: Instance, wave: str, args: List[Expr],
               keys: Dict[str, Expr]) -> None:
        src = inst.source
        name = inst.name
        TSTEP, TSTOP = Num(self.tstep), Num(self.tstop)
        c = lambda e: self.const(e, scope)                          # noqa: E731
        if wave in ("pwl", "pl"):
            if "r" in keys:
                raise _Defer("%s: PWL repeat (R) is not supported" % name)
            if len(args) % 2 or not args:
                raise _Defer("%s: PWL needs time-value pairs" % name)
            pairs = [(args[k], args[k + 1]) for k in range(0, len(args), 2)]
            if wave == "pl":
                pairs = [(t, v) for v, t in pairs]
            times = [c(t) for t, _ in pairs]
            for k in range(1, len(pairs)):
                if times[k] is not None and times[k - 1] is not None and times[k] <= times[k - 1]:
                    raise _Defer("%s: PWL time points must increase (%s then %s)"
                                 % (name, E.to_text(pairs[k - 1][0]), E.to_text(pairs[k][0])))
            if times[0] is None:
                self.warn(inst.origin, "%s: the first PWL time is not constant; vamos assumes it is "
                          "not after 0" % name)
            elif times[0] < 0:
                raise _Defer("%s: PWL time %s is negative" % (name, E.to_text(pairs[0][0])))
            elif times[0] > 0:
                pairs.insert(0, (Num(0.0), src.dc if src.dc is not None else Num(0.0)))
            src.points = pairs
            src.args = {"td": keys.get("td", Num(0.0))}
            return
        lo, hi = _SOURCE_ARITY[wave]
        if not lo <= len(args) <= hi:
            raise _Defer("%s: %s takes %d to %d values, got %d" % (name, wave.upper(), lo, hi, len(args)))
        given = dict(zip(_SOURCE_KEYS[wave], args))
        out: Dict[str, Expr] = {"v1": given.get("v1"), "v2": given.get("v2")} if wave != "sin" else {}
        if wave == "pulse":
            td = given.get("td", Num(0.0))
            v = c(td)
            if v is not None:
                td = Num(max(v, 0.0))
            else:
                td = Call("max", (td, Num(0.0)))
            out["td"] = td
            vals = {}
            for edge in ("tr", "tf"):
                e = given.get(edge)
                if e is None:
                    out[edge] = TSTEP
                    vals[edge] = self.tstep
                    continue
                v = c(e)
                if v is None:
                    out[edge] = Ternary(Binary("==", e, Num(0.0)), TSTEP, e)
                    vals[edge] = None
                elif v < 0:
                    raise _Defer("%s: PULSE %s is negative (%g)" % (name, edge, v))
                else:
                    out[edge] = TSTEP if v == 0 else Num(v)
                    vals[edge] = self.tstep if v == 0 else v
            pw = given.get("pw")
            out["pw"] = TSTOP if pw is None else pw
            vals["pw"] = self.tstop if pw is None else c(pw)
            if vals["pw"] is not None and vals["pw"] < 0:
                raise _Defer("%s: PULSE pw is negative" % name)
            per = given.get("per")
            if per is not None:
                pv = c(per)
                if pv is not None and None not in vals.values() and \
                        pv <= (vals["tr"] + vals["tf"] + vals["pw"]) * (1 + 1e-12):
                    raise _Defer("%s: PULSE period %g is not longer than tr+tf+pw = %g (after "
                                 "zero or omitted edges became TSTEP = %g)"
                                 % (name, pv, vals["tr"] + vals["tf"] + vals["pw"], self.tstep))
                out["per"] = per
        elif wave == "sin":
            out["vo"] = given["vo"]
            out["va"] = given["va"]
            out["freq"] = given.get("freq", Num(1.0 / self.tstop))
            out["td"] = given.get("td", Num(0.0))
            out["theta"] = given.get("theta", Num(0.0))
            out["phase"] = given.get("phase", Num(0.0))
        else:                                                      # exp
            td1 = given.get("td1", Num(0.0))
            out["td1"] = td1
            for tau in ("tau1", "tau2"):
                e = given.get(tau)
                if e is None:
                    out[tau] = TSTEP
                    continue
                v = c(e)
                if v is not None and v <= 0:
                    raise _Defer("%s: EXP %s must be positive" % (name, tau))
                out[tau] = e
            td2 = given.get("td2")
            if td2 is None:
                td2 = Num(td1.value + self.tstep) if isinstance(td1, Num) else Binary("+", td1, TSTEP)
            v1, v2 = c(td1), c(td2)
            if v1 is not None and v2 is not None and v2 - v1 <= 0:
                raise _Defer("%s: EXP td2 (%g) must be after td1 (%g)" % (name, v2, v1))
            out["td2"] = td2
        src.args = out

    # -- ground pass, step 2 (collapsed elements) and references to dropped elements

    def ground(self) -> None:
        for s in self.kept_scopes():
            dropped: Set[str] = set()
            for inst in list(s.insts.values()):
                if len(inst.nodes) < 2 or inst.nodes[0] != "0" or inst.nodes[1] != "0":
                    continue
                if inst.kind not in ("v", "i", "r", "c", "l", "e", "f", "g", "h", "d", "b"):
                    continue
                k = inst.kind
                if k == "b":
                    k = "e" if inst.expr_kind == "v" else "g"
                if k == "v":
                    src = inst.source
                    dc = 0.0 if src.dc is None else self.const(src.dc, s)
                    if dc == 0.0 and src.ac is None and src.wave is None:
                        self.note(inst.origin, "%s: both terminals are ground; the source is dropped"
                                  % inst.name)
                    else:
                        self.err(inst.origin, "%s is a voltage source between two ground nodes"
                                 % inst.name)
                        continue
                elif k in ("e", "h"):
                    v = self.const(inst.expr if inst.kind == "b" else inst.value, s)
                    if v == 0.0:
                        self.note(inst.origin, "%s: both terminals are ground and its value is 0; "
                                  "dropped" % inst.name)
                    else:
                        self.err(inst.origin, "%s drives a voltage between two ground nodes"
                                 % inst.name)
                        continue
                else:
                    self.note(inst.origin, "%s: both terminals are ground; dropped" % inst.name)
                dropped.add(inst.name)
            if not dropped:
                continue
            self.dropped[id(s)] = dropped
            s.out = [it for it in s.out if not (isinstance(it, Instance) and it.name in dropped)]
            for n in dropped:
                del s.insts[n]
            for inst in s.insts.values():
                for c in inst.ctrl:
                    if c in dropped:
                        self.err(inst.origin, "%s refers to %s, which was dropped (both its terminals "
                                 "are ground)" % (inst.name, c))
                if inst.expr is not None:
                    for call in E.node_calls(inst.expr):
                        if call.func.startswith("i") and call.args and call.args[0].name in dropped:
                            self.err(inst.origin, "%s reads %s of %s, which was dropped (both its "
                                     "terminals are ground)" % (inst.name, E.to_text(call),
                                                                call.args[0].name))

    def references(self) -> None:
        """F/H controlling sources and K inductors exist in their scope."""
        for s in self.kept_scopes():
            dropped = self.dropped.get(id(s), set())
            for inst in s.insts.values():
                if inst.kind in ("f", "h"):
                    c = inst.ctrl[0]
                    t = s.insts.get(c)
                    if c in dropped:
                        continue
                    if t is None or t.kind != "v":
                        self.err(inst.origin, "%s: the controlling element %s is not a voltage "
                                 "source of the same %s" % (inst.name, c, "subckt" if s.name else
                                                            "netlist"))
                elif inst.kind == "k":
                    for c in inst.ctrl:
                        t = s.insts.get(c)
                        if c in dropped:
                            continue
                        if t is None or t.kind != "l":
                            self.err(inst.origin, "%s: %s is not an inductor of the same %s"
                                     % (inst.name, c, "subckt" if s.name else "netlist"))

    # -- controls

    def controls_pass(self, nl: Netlist) -> None:
        temps: List[Tuple[float, str]] = []
        for canon, st in self.controls:
            if canon == ".temp":
                vals = []
                try:
                    fields = st.fields()[1:]
                except _LexError as exc:
                    self.err(st.origin, ".temp: %s" % exc)
                    continue
                for key, text in fields:
                    if key is not None:
                        self.err(st.origin, ".temp %s= is not supported" % key)
                        continue
                    v = self.eval_top(text, st.origin, ".temp")
                    if v is not None:
                        vals.append(v)
                if not vals:
                    self.err(st.origin, ".temp needs a temperature")
                    continue
                if len(vals) > 1:
                    self.warn(st.origin, ".temp lists %d temperatures; only the first (%g) is "
                              "simulated" % (len(vals), vals[0]))
                if temps:
                    self.note(st.origin, ".temp replaces the earlier .temp (%s)" % temps[-1][1])
                temps.append((vals[0], st.origin))
            elif canon in (".ic", ".nodeset"):
                target = nl.ics if canon == ".ic" else nl.nodesets
                self.ic(st, target)
            elif canon == ".print":
                self.probes(st, nl)
        spice = "spice" in self.opt
        default = TEMP_SPICE if spice else TEMP_DEFAULT
        if temps:
            nl.temp = temps[-1][0]
        elif "temp" in self.opt and self.opt["temp"][0] is not None:
            nl.temp = self.eval_top(self.opt["temp"][0], self.opt["temp"][1], ".option temp")
        if nl.temp is None:
            nl.temp = default
        if "tnom" in self.opt and self.opt["tnom"][0] is not None:
            nl.tnom = self.eval_top(self.opt["tnom"][0], self.opt["tnom"][1], ".option tnom")
            if nl.tnom is not None:
                nl.options["tnom"] = Num(nl.tnom)
        if nl.tnom is None:
            nl.tnom = default

    def ic(self, st: _Stmt, target: Dict[str, float]) -> None:
        try:
            fields = st.fields()[1:]
        except _LexError as exc:
            self.err(st.origin, "%s: %s" % (st.kw, exc))
            return
        for key, text in fields:
            m = _CALLISH.match(key) if key else None
            if m is None or m.group(1).lower() != "v" or "," in m.group(2):
                self.err(st.origin, "%s: write v(node)=value, not %s" % (st.kw, key or text))
                continue
            raw = m.group(2).strip()
            try:
                node = self.node_text(raw)
            except _Defer as exc:
                self.err(st.origin, "%s: %s" % (st.kw, exc))
                continue
            last = node.rsplit(".", 1)[-1]
            if self.is_ground(last):
                node = "0"
            v = self.eval_top(text, st.origin, "%s v(%s)" % (st.kw, raw))
            if v is None:
                continue
            if node == "0":
                if v != 0:
                    self.err(st.origin, "%s v(%s)=%g: the node is ground" % (st.kw, raw, v))
                continue
            target[node] = v

    def probes(self, st: _Stmt, nl: Netlist) -> None:
        try:
            fields = st.fields()[1:]
        except _LexError as exc:
            self.err(st.origin, "%s: %s" % (st.kw, exc))
            return
        analysis = "tran"
        if fields and _bare(fields[0]) and fields[0][1].lower() in ("tran", "dc", "ac", "op", "noise"):
            analysis = fields[0][1].lower()
            fields = fields[1:]
        for key, text in fields:
            if key is not None:
                self.warn(st.origin, "%s %s=%s ignored (named outputs are not supported)"
                          % (st.kw, key, text))
                continue
            m = _CALLISH.match(text)
            if m is None:
                self.warn(st.origin, "%s %s ignored (not v() or i())" % (st.kw, text))
                continue
            func = m.group(1).lower()
            args = [a for a in re.split(r"[,\s]+", m.group(2).strip())]
            if func == "v" and 1 <= len(args) <= 2 and all(args):
                if args == ["*"]:
                    nl.probes.append((analysis, "v", "*"))
                    continue
                if any("*" in a or "?" in a for a in args):
                    self.warn(st.origin, "%s %s ignored (wildcards other than v(*) are not "
                              "supported)" % (st.kw, text))
                    continue
                try:
                    names = []
                    for a in args:
                        n = self.node_text(a)
                        if fold_ground(n.rsplit(".", 1)[-1]) == "0":
                            n = "0"
                        names.append(n)
                except _Defer as exc:
                    self.err(st.origin, "%s: %s" % (st.kw, exc))
                    continue
                nl.probes.append((analysis, "v", ",".join(names)))
            elif func == "i" and len(args) == 1 and args[0]:
                target = self.fold(args[0])
                if self.dropped_probe(target):
                    self.err(st.origin, "%s i(%s): the element was dropped (both its terminals are "
                             "ground)" % (st.kw, args[0]))
                    continue
                nl.probes.append((analysis, "i", target))
            else:
                self.warn(st.origin, "%s %s ignored (only v(node), v(a,b) and i(element) are "
                          "supported)" % (st.kw, text))

    def dropped_probe(self, target: str) -> bool:
        parts = target.split(".")
        s = self.top
        for p in parts[:-1]:
            inst = s.insts.get(p)
            if inst is None or inst.kind != "x":
                return False
            t = next((t for i, t in s.xrefs if i is inst), None)
            if t is None:
                return False
            s = t
        return parts[-1] in self.dropped.get(id(s), set())

    # -- options

    def options_pass(self, nl: Netlist) -> None:
        for key, (val, origin) in self.opt.items():
            if key in ("wl", "parhier", "temp", "tnom", "delmax", "rmax") or key in _OPT_MOSDEF:
                continue                                # delmax rmax: max_step()
            if key == "scale":
                if val is None:
                    self.err(origin, ".option scale needs a value")
                    continue
                v = self.eval_top(val, origin, ".option scale")
                if v is not None:
                    if v <= 0:
                        self.err(origin, ".option scale=%g must be positive" % v)
                    else:
                        nl.options["scale"] = Num(v)
            elif key in ("scalm", "geoshrink"):
                v = self.eval_top(val, origin, ".option " + key) if val is not None else None
                if v is not None and v != 1.0:
                    self.err(origin, ".option %s=%s is not supported (model or layout scaling)"
                             % (key, val))
            elif key == "aspec":
                v = self.eval_top(val, origin, ".option aspec") if val is not None else 1.0
                if v is not None and v != 0.0:
                    self.err(origin, ".option aspec is not supported: ASPEC compatibility mode sets "
                             "SCALE=SCALM=1e-6, WL, LEVEL=6 and ACM=1 MOS models and the CJ=IS=0 "
                             "defaults (Star-HSPICE 9-13, 21-86)")
            elif key in _OPT_MAPPED:
                if val is None:
                    self.err(origin, ".option %s needs a value" % key)
                    continue
                v = self.eval_top(val, origin, ".option " + key)
                if v is not None:
                    nl.options[key] = Num(v)
            elif key == "method":
                if val is None:
                    self.err(origin, ".option method needs a value")
                    continue
                nl.options["method"] = Str(val.strip("'\"").lower())
            elif key == "dcap":
                # the depletion-capacitance equations of D, Q and J cards (tables.hspice_card)
                if val is None:
                    self.err(origin, ".option dcap needs a value (1, 2 or 3)")
                    continue
                v = self.eval_top(val, origin, ".option dcap")
                if v is not None:
                    if v not in (1.0, 2.0, 3.0):
                        self.err(origin, ".option dcap=%s must be 1, 2 or 3" % val)
                    else:
                        nl.options["dcap"] = Num(v)
            elif key == "spice":
                nl.options["spice"] = Num(1.0)
                self.note(origin, ".option spice: temp and tnom default to 27, DCAP to 1, and the model "
                          "defaults are SPICE's (MOS CAPOP=0, LD=0, no NSUB default; BJT MJS=0); its other "
                          "SPICE-compatibility settings are not modelled")
            elif key in _OPT_STEP_ALGO:
                uses_rmax = any(c == ".tran" for c, _ in self.controls) and "delmax" not in self.opt
                self.note(origin, ".option %s: HSPICE's timestep algorithm is not modelled (the "
                          "engine's own step control applies)%s" % (key, "; it counts only for RMAX, "
                          "the TSTEP multiplier of the maximum time step" if uses_rmax else ""))
            elif key in _OPT_OUTPUT:
                self.note(origin, ".option %s ignored (output and listing control)" % key)
            elif key in _OPT_NUMERIC:
                self.note(origin, ".option %s ignored (simulator control; the engine's default "
                          "accuracy applies)" % key)
            elif key in _OPT_WARN:
                self.warn(origin, ".option %s ignored: vamos does not add %s" % (key, _OPT_WARN[key]))
            else:
                self.warn(origin, ".option %s ignored (unknown to vamos)" % key)

    # =========================================================================
    # The IR
    # =========================================================================

    def build(self) -> Netlist:
        nl = Netlist(title=self.title)
        nl.globals = list(self.globals)
        nl.hdl = list(self.hdl)
        nl.parhier = self.parhier_eff
        self.options_pass(nl)
        nl.analyses = self.analyses()
        if getattr(self, "synth_delmax", None):
            nl.options["delmax"] = Num(self.synth_delmax)   # for ams/deck.py's synthesised .tran
        self.sources()
        self.ground()
        self.references()
        self.controls_pass(nl)
        nl.body = list(self.top.sorted_params) + self.items(self.top)
        nl.values = dict(self.values)
        nl.va_modules = dict(self.va_mods)
        spelling = dict(self.spelling_use)
        spelling.update(self.spelling_def)
        nl.spelling = spelling
        return nl

    def items(self, s: _Scope) -> List[object]:
        out: List[object] = []
        for it in s.out:
            if isinstance(it, _Scope):
                if not self.kept(it):
                    continue
                params = [Param(n, e, o) for n, e, o in it.params] if self.sp else list(it.sorted_params)
                out.append(Subckt(it.name, list(it.ports), params, self.items(it),
                                  list(it.orig_ports), list(it.gnd_ports), it.stmt.origin,
                                  dict(it.spelling)))
            else:
                out.append(it)
        return out

    def fragment(self) -> Fragment:
        """A spectre-spice region's Fragment (§4.3): raw Params in source order, then the body.  The
        controls' fields are folded to lower case outside quotes, as every other SPICE-mode name is
        (UG p.52): .global names, .dc sources, .ic/.nodeset nodes, .print items."""
        frag = Fragment()
        frag.items = [Param(n, e, o) for n, e, o in self.top.params] + self.items(self.top)
        for canon, st in self.controls:
            try:
                fields = st.fields()
            except _LexError as exc:
                self.err(st.origin, "%s: %s" % (st.kw, exc))
                continue
            if fields and fields[0][0] is not None:        # '.temp = 125': the keyword became a key
                fields = [("", fields[0][1])] + fields[1:]
            else:
                fields = fields[1:]
            fields = [(None if k is None else _fold_unquoted(k), _fold_unquoted(v)) for k, v in fields]
            frag.controls.append((canon, fields, st.origin))
        frag.notes = list(self.notes)
        frag.left_out = dict(self.left)
        frag.pending = list(self.pending)
        return frag


def parse(paths: Sequence[NetlistRef], extra: Sequence[Tuple[str, str]], cwd: str,
          opts: Optional[ParseOpts] = None) -> Netlist:
    """HSPICE netlists into one Netlist (see the module docstring); NoteError on any error."""
    opts = opts or ParseOpts()
    p = _Parser(cwd, opts)
    if opts.dialect not in ("hspice", "spice"):
        raise NoteError([error("", "netlist dialect %s is not supported" % opts.dialect)])
    p.read(paths, extra)
    p.early_options()
    p.declare(p.top)
    p.elements(p.top)
    p.prune()
    p.parameters()
    nl = p.build()
    for origin, msg in _colon_clashes(nl):
        p.err(origin, msg)
    if any(n.severity == ERROR for n in p.notes):
        raise NoteError(p.notes)
    nl.notes = list(p.notes)
    nl.left_out = dict(p.left)
    return nl


# -- the spectre personality's SPICE mode: phase-0 signatures (VAMOS_SPECTRE_DESIGN.md §4.3, §10) -------

# SPICE model types -> Spectre masters (§4.3 Models): the level decides the MOS master.
_SPICE_MASTERS = {"d": "diode", "npn": "bjt", "pnp": "bjt", "njf": "jfet", "pjf": "jfet",
                  "r": "resistor", "c": "capacitor", "l": "inductor"}
# level 11 is Spectre's own bsim3v3 selector, which its SPICE reader takes too (UG p.298's `.model N1 NMOS
# +Level=11`; ref5 p.197: "The available level are 11, 49 and 53")
_MOS_MASTERS = {1: "mos1", 2: "mos2", 3: "mos3", 11: "bsim3v3", 49: "bsim3v3", 53: "bsim3v3", 54: "bsim4"}
_MASTER_LETTER = {"resistor": "r", "capacitor": "c", "inductor": "l", "diode": "d", "bjt": "q", "jfet": "j",
                  "mos1": "m", "mos2": "m", "mos3": "m", "bsim3v3": "m", "bsim4": "m"}
_LETTER_KINDS = {"r": ("r",), "c": ("c",), "l": ("l",), "d": ("d",), "q": ("npn", "pnp"), "j": ("njf", "pjf"),
                 "m": ("nmos", "pmos")}
# the keys a SPICE element line defines itself (any other key is a Spectre instance parameter of the
# element's master, checked against SPECTRE_MASTERS, §4.3 Elements)
_SPICE_KEYS = {"r": ("r", "tc1", "tc2", "m", "scale", "ac", "w", "l", "dtemp"),
               "c": ("c", "tc1", "tc2", "m", "scale", "w", "l", "ic", "dtemp"),
               "l": ("l", "tc1", "tc2", "m", "scale", "ic", "dtemp"),
               "d": ("area", "pj", "w", "l", "m", "dtemp"), "q": ("area", "m", "dtemp"), "j": ("area", "m", "dtemp"),
               "m": ("l", "w", "ad", "as", "pd", "ps", "nrd", "nrs", "nf", "m", "dtemp", "geo", "delvto", "mulu0"),
               "v": ("m",), "i": ("m",), "e": ("m",), "g": ("m",), "f": ("m",), "h": ("m",)}
_SPICE_SOURCE_NAMES = {"pulse": ("val0", "val1", "delay", "rise", "fall", "width", "period"),
                       "sin": ("sinedc", "ampl", "freq", "delay", "damp", "sinephase"),
                       "exp": ("val0", "val1", "td1", "tau1", "td2", "tau2")}


_ENUM_PARAMS: Optional[frozenset] = None


def spectre_enum_params() -> frozenset:
    """The instance and card parameters of the v1 masters whose values are enumerations (words): the
    names of SPECTRE_MASTERS rules with a word-valued match token (type, capmod, struct, abs, ...)
    plus isnoisy (yes|no), a source's type, region and file.  A bare identifier written for one of them
    is a Str, as §4.1 item 11 keeps enumerations, never a parameter reference: plan.build evaluates
    every other value, and the emitters read these as text (spectre.py and parse_fragment share it)."""
    global _ENUM_PARAMS
    if _ENUM_PARAMS is None:
        from vamos.netlist import tables as T
        out = {"isnoisy", "type", "region", "file"}
        for row in T.SPECTRE_MASTERS.values():
            for r in tuple(row.params) + tuple(row.instance):
                if any(tok not in ("absent", "nonzero") and not T._SPECTRE_NUM.match(tok) for tok in r.match):
                    out.add(r.name)
        _ENUM_PARAMS = frozenset(out)
    return _ENUM_PARAMS


def _spice_master(mtype: str, level: Optional[float]) -> Tuple[Optional[str], Optional[str]]:
    """(the Spectre master of a SPICE .model type at a level, an error message) (§4.3 Models)."""
    low = mtype.lower()
    if low in ("nmos", "pmos"):
        lv = 1 if level is None else level
        if lv != int(lv) or int(lv) not in _MOS_MASTERS:
            return None, "level %s of a %s card has no Spectre master (mos1 mos2 mos3 are levels 1-3, " \
                         "bsim3v3 11, 49 and 53, bsim4 54)" % (level, mtype)
        return _MOS_MASTERS[int(lv)], None
    if low == "d":
        if level is not None and level != 1:
            return None, "diode level %s is not Spectre's level 1 (junction diode; ref5 p.303)" % level
        return "diode", None
    if low in _SPICE_MASTERS:
        if level is not None and level != 1:
            return None, "level %s of a %s card has no Spectre master" % (level, mtype)
        return _SPICE_MASTERS[low], None
    return None, "model type %s is not supported" % mtype.upper()


def _level_text(fields: Sequence[Tuple[Optional[str], str]]) -> Tuple[Optional[float], Optional[str]]:
    """(the literal level of a card's fields, the text when it is not a literal)."""
    for key, text in fields:
        if key is not None and key.lower() == "level":
            try:
                return E.number(text.strip("'\"{}"), "spectre-spice"), None
            except ValueError:
                return None, text
    return None, None


def _fold_unquoted(text: str) -> str:
    """text lower-cased outside '...' and "..." quotes: the SPICE Reader converts its input to lower case
    except quoted strings (UG p.52; the spectre-spice dialect's controls, Fragment.controls)."""
    if "'" not in text and '"' not in text:
        return text.lower()
    out: List[str] = []
    quote = ""
    for c in text:
        if quote:
            if c == quote:
                quote = ""
            out.append(c)
        elif c in "'\"":
            quote = c
            out.append(c)
        else:
            out.append(c.lower())
    return "".join(out)


def _card_fields(fields: Sequence[Tuple[Optional[str], str]]) -> List[Tuple[Optional[str], str]]:
    """A .model's parameter fields with the '( ... )' grouping flattened."""
    flat: List[Tuple[Optional[str], str]] = []
    for f in fields:
        inner = _unparen(f[1]) if _bare(f) else None
        if inner is not None:
            try:
                flat.extend(_lex(inner))
            except _LexError:
                flat.append(f)
        else:
            flat.append(f)
    return flat


def declare_fragment(lines: List[Tuple[str, str]], opts: ParseOpts) -> Tuple[List[Decl], List[FileRef]]:
    """The first phase, SPICE side (§4.3): the names a region declares (.subckt with its port count
    and header parameter names, .model with its Spectre master, .param), and its file statements
    in order (.include/.inc/.incl, both .lib forms, .hdl: the path as written, the section, the
    enclosing .subckt, the origin), from the same lexing (_logical), so spectre.py never re-lexes
    SPICE text and splices the files in before the second phase.  Names are lower-cased
    (UG p.59).  A one-argument .lib inside a section (a call to another section of the same file)
    has path ''."""
    decls: List[Decl] = []
    refs: List[FileRef] = []
    stmts, _notes = _logical(lines, "spectre-spice")
    stack: List[str] = []
    for st in stmts:
        canon = _DOT.get(st.kw)
        sub = stack[-1] if stack else ""
        if canon == ".subckt":
            try:
                fields = st.fields()
            except _LexError:
                fields = [(None, w) for w in st.text.split()]
            name = fields[1][1].lower() if len(fields) > 1 and _bare(fields[1]) else "?"
            ports = 0
            params: List[str] = []
            k = 2
            while k < len(fields) and _bare(fields[k]) and fields[k][1].lower() != "params:":
                ports += 1
                k += 1
            for key, _text in fields[k:]:
                if key is not None:
                    params.append(key.lower())
            decls.append(Decl("subckt", name, "", ports, params, st.origin))
            stack.append(name)
        elif canon == ".ends":
            if stack:
                stack.pop()
        elif canon == ".model":
            try:
                fields = st.fields()
            except _LexError:
                continue
            if len(fields) < 3 or not _bare(fields[1]) or not _bare(fields[2]):
                continue
            mtype = fields[2][1]
            rest = list(fields[3:])
            m = _CALLISH.match(mtype)
            if m:
                mtype = m.group(1)
                try:
                    rest = _lex(m.group(2)) + rest
                except _LexError:
                    pass
            level, bad = _level_text(_card_fields(rest))
            master, _err = _spice_master(mtype, level) if bad is None else (None, None)
            decls.append(Decl("model", fields[1][1].lower(), master or mtype.lower(), origin=st.origin))
        elif canon == ".param":
            try:
                fields = st.fields()[1:]
            except _LexError:
                continue
            for key, _text in fields:
                if key is not None and _IDENT.match(key):
                    decls.append(Decl("param", key.lower(), origin=st.origin))
        elif canon in (".inc", ".lib", ".hdl"):
            try:
                args = _path_args(st.text[len(st.kw):])
            except _LexError:
                continue
            if canon == ".lib":
                if len(args) == 2:
                    refs.append(FileRef(st.kw.lower(), args[0], args[1].lower(), sub, st.origin))
                elif len(args) == 1:
                    refs.append(FileRef(st.kw.lower(), "", args[0].lower(), sub, st.origin))
            elif args:
                refs.append(FileRef(st.kw.lower(), args[0], None, sub, st.origin))
    return decls, refs


def parse_fragment(lines: List[Tuple[str, str]], cwd: str, opts: ParseOpts, resolver: Resolver) -> Fragment:
    """The second phase (§4.3): _Parser.read([], lines) in the spectre-spice dialect, every name
    resolved against resolver (spectre.py's complete table of both languages).  Returns the raw
    Params (unsorted, unevaluated; a subckt's header and body parameters on its Subckt), the
    Models with Model.prim, the Subckts and the Instances with Instance.prim in source order, the
    dot statements other than definitions as controls (keyword, fields folded to lower case outside
    quotes, origin), the notes (errors included: spectre.py raises), the left-out subckts and the
    names defined nowhere (pending).  Files are never read here: spectre.py splices includes in
    before this phase."""
    if opts.dialect != "spectre-spice":
        raise ValueError("parse_fragment reads the spectre-spice dialect only, not %r" % (opts.dialect,))
    if opts.case != "lower" or not opts.parhier_local:
        raise ValueError("parse_fragment needs ParseOpts(case='lower', parhier_local=True) (§4.3)")
    p = _Parser(cwd, opts)
    p.resolver = resolver
    p.read([], lines)
    p.declare(p.top)
    p.elements(p.top)
    p.prune()
    p.ground()
    p.references()
    return p.fragment()


# -- Verilog-A module discovery (§3.1): a minimal preprocessor and the declaration scan ----------------

_VA_DIRECTIVE = re.compile(r"^\s*`(define|undef|ifdef|ifndef|elsif|else|endif|include)\b(.*)$", re.S)
_VA_MACRO_USE = re.compile(r"`([A-Za-z_][A-Za-z0-9_$]*)")
_VA_MODULE_HEAD = re.compile(r"(?:\(\*(?P<attrs>.*?)\*\)\s*)?\b(?:module|macromodule)\s+(?P<name>[A-Za-z_][A-Za-z0-9_$]*)",
                             re.S)
_VA_ATTR = re.compile(r"([A-Za-z_][A-Za-z0-9_$]*)\s*=\s*(\"[^\"]*\"|[^\s,]+)")
_VA_ATTR_BLOCK = re.compile(r"\(\*.*?\*\)", re.S)
# the Spectre dialects (phase 2): the analog block, not an analog function; the attributes a CMC header
# writes after its port list (`module bsim6(d, g, s, b, t) (* xyceModelGroup="MOSFET" ... *);`, and the
# bsimcmg trees' `attr(...)` macro, which expands to the same)
_VA_ANALOG_BLOCK = re.compile(r"\banalog\b(?!\s+function\b)")
_VA_POST_PORT_ATTRS = re.compile(r"\s*(?:\([^;()]*\))?\s*\(\*(?P<attrs>.*?)\*\)\s*;", re.S)
_VA_PARAM = re.compile(r"\b(parameter|aliasparam|localparam)\b")
# the standard headers are never scanned (§3.1 names *.vams; the legacy discipline.h and constants.h
# spellings, which PSP103 and the ADMS examples use, are the same headers)
_VA_STD_HEADER = (".vams", "discipline.h", "disciplines.h", "constants.h")
_VA_MAX_DEPTH = 64


class _VaPre:
    """A minimal stdlib Verilog-A preprocessor (§3.1): `define with and without arguments, `undef,
    `ifdef/`ifndef/`elsif/`else/`endif, `include on the search list, comments stripped.  Problems
    that leave a module's parameter list unknown are recorded in `trouble` (the caller's note)."""

    def __init__(self, search: Sequence[str], defines: Sequence[str]):
        self.search = [os.path.abspath(d) for d in search]
        self.macros: Dict[str, Tuple[Optional[List[str]], str]] = {}
        for d in defines:
            name, _eq, val = d.partition("=")
            self.macros[name.strip()] = (None, val.strip() if _eq else "1")
        self.files: List[str] = []
        self.trouble: List[str] = []
        self.out: List[str] = []

    def read(self, path: str, depth: int = 0) -> None:
        path = os.path.abspath(path)
        if path in self.files:
            return
        self.files.append(path)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            self.trouble.append("cannot read %s: %s" % (path, exc))
            return
        text = _VA_COMMENT.sub(lambda m: " " if "\n" not in m.group(0) else "\n" * m.group(0).count("\n"), text)
        lines = text.split("\n")
        cond: List[Tuple[bool, bool]] = []          # (this branch active, some branch taken)
        k = 0
        while k < len(lines):
            line = lines[k]
            k += 1
            m = _VA_DIRECTIVE.match(line)
            if m is None:
                if all(c[0] for c in cond):
                    self.out.append(line)               # expanded whole at the end: a call may span lines
                continue
            what, rest = m.group(1), m.group(2)
            active = all(c[0] for c in cond)
            if what in ("ifdef", "ifndef"):
                name = rest.strip().split()[0] if rest.strip() else ""
                taken = (name in self.macros) == (what == "ifdef")
                cond.append((active and taken, taken))
            elif what == "elsif":
                if not cond:
                    self.trouble.append("%s: `elsif without `ifdef" % path)
                    continue
                _a, taken = cond.pop()
                outer = all(c[0] for c in cond)
                name = rest.strip().split()[0] if rest.strip() else ""
                now = not taken and name in self.macros
                cond.append((outer and now, taken or now))
            elif what == "else":
                if not cond:
                    self.trouble.append("%s: `else without `ifdef" % path)
                    continue
                _a, taken = cond.pop()
                outer = all(c[0] for c in cond)
                cond.append((outer and not taken, True))
            elif what == "endif":
                if not cond:
                    self.trouble.append("%s: `endif without `ifdef" % path)
                    continue
                cond.pop()
            elif not active:
                continue
            elif what == "define":
                while rest.rstrip().endswith("\\") and k < len(lines):
                    rest = rest.rstrip()[:-1] + "\n" + lines[k]
                    k += 1
                self.define(rest, path)
            elif what == "undef":
                self.macros.pop(rest.strip().split()[0] if rest.strip() else "", None)
            else:                                   # `include
                self.include(rest, path, depth)
        if cond:
            self.trouble.append("%s: unbalanced `ifdef at the end of the file" % path)

    def define(self, rest: str, path: str) -> None:
        mm = re.match(r"\s*([A-Za-z_][A-Za-z0-9_$]*)(\([^)]*\))?[ \t]*(.*)$", rest, re.S)
        if mm is None:
            self.trouble.append("%s: malformed `define %s" % (path, rest.strip()[:40]))
            return
        args = None
        if mm.group(2):
            args = [a.strip() for a in mm.group(2)[1:-1].split(",") if a.strip()]
        self.macros[mm.group(1)] = (args, mm.group(3).strip())

    def include(self, rest: str, path: str, depth: int) -> None:
        mm = re.search(r'"([^"]+)"', rest)
        if mm is None:
            self.trouble.append("%s: `include without a quoted file name" % path)
            return
        name = mm.group(1)
        if name.endswith(_VA_STD_HEADER):            # disciplines.vams, constants.vams: not scanned
            return
        if depth >= _VA_MAX_DEPTH:
            self.trouble.append("%s: `include nesting deeper than %d" % (path, _VA_MAX_DEPTH))
            return
        cands = [name] if os.path.isabs(name) else \
            [os.path.join(os.path.dirname(path), name)] + [os.path.join(d, name) for d in self.search]
        for c in cands:
            if os.path.isfile(c):
                self.read(c, depth + 1)
                return
        self.trouble.append("%s: `include \"%s\" not found (tried %s)" % (path, name, ", ".join(cands)))

    def expand(self, line: str, depth: int = 0) -> str:
        """Macro uses replaced; an undefined macro stays as written (it only matters at the head
        of a declaration, where the scan reports it)."""
        if "`" not in line or depth > _VA_MAX_DEPTH:
            return line
        out: List[str] = []
        i = 0
        while i < len(line):
            m = _VA_MACRO_USE.search(line, i)
            if m is None:
                out.append(line[i:])
                break
            out.append(line[i:m.start()])
            name = m.group(1)
            i = m.end()
            if name not in self.macros:
                out.append(m.group(0))
                continue
            args, body = self.macros[name]
            if args is None:
                out.append(self.expand(body, depth + 1))
                continue
            j = i
            while j < len(line) and line[j] in " \t":
                j += 1
            if j >= len(line) or line[j] != "(":
                out.append(self.expand(body, depth + 1))
                continue
            actuals, end = _split_args(line, j)
            if end < 0:
                out.append(m.group(0))
                continue
            i = end
            text = body
            for formal, actual in zip(args, actuals + [""] * (len(args) - len(actuals))):
                text = re.sub(r"\b%s\b" % re.escape(formal), lambda _m, a=actual: a, text)
            out.append(self.expand(text, depth + 1))
        return "".join(out)


def _split_args(text: str, lparen: int) -> Tuple[List[str], int]:
    """The comma-separated arguments of the '(' at text[lparen]; (args, index after ')'), -1 unclosed."""
    depth = 0
    args: List[str] = []
    cur: List[str] = []
    quote = ""
    for j in range(lparen, len(text)):
        c = text[j]
        if quote:
            cur.append(c)
            if c == quote:
                quote = ""
            continue
        if c == '"':
            quote = c
            cur.append(c)
        elif c in "([{":
            depth += 1
            if depth > 1:
                cur.append(c)
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                args.append("".join(cur).strip())
                return args, j + 1
            cur.append(c)
        elif c == "," and depth == 1:
            args.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
    return args, -1


def _va_params(body: str, spectre: bool = False) -> Tuple[List[str], Optional[str]]:
    """The names declared by `parameter` and `aliasparam` in an expanded module body (not localparam),
    the (* ... *) attributes before them skipped; (names, the first unexpanded macro at a declaration head).
    spectre (the Spectre dialects, phase 2): the analog block is `analog` not followed by `function`, so
    the analog functions a CMC model declares before its parameters (BSIM6.1.1.va:656) do not end the
    declarations; the HSPICE route keeps its reading."""
    names: List[str] = []
    text = _VA_ATTR_BLOCK.sub(" ", body)
    m_analog = re.search(_VA_ANALOG_BLOCK if spectre else r"\banalog\b", text)
    decl_end = m_analog.start() if m_analog else len(text)   # declarations precede the analog block
    pos = 0
    for stmt in text.split(";"):
        at, pos = pos, pos + len(stmt) + 1
        s = stmt.strip()
        if not s:
            continue
        lead = len(stmt) - len(stmt.lstrip())
        if at + lead >= decl_end:
            break                                           # the analog block holds no declarations
        if s.startswith("`"):
            return names, s.split()[0]
        m = _VA_PARAM.search(s)
        if m is None:
            continue
        if at + lead + m.start() >= decl_end:
            break
        head = s[:m.start()].strip()
        if "`" in head:
            return names, head
        kind = m.group(1)
        if kind == "localparam":
            continue
        decl = s[m.end():].strip()
        decl = re.sub(r"^(real|integer|string)\b", "", decl).strip()
        for piece in _split_decls(decl):
            mm = re.match(r"([A-Za-z_][A-Za-z0-9_$]*)", piece.strip())
            if mm:
                names.append(mm.group(1))
    return names, None


def _split_decls(decl: str) -> List[str]:
    """'a = 1 from (0:inf), b = 2' -> ['a = 1 from (0:inf)', 'b = 2'] (commas inside brackets kept)."""
    out: List[str] = []
    depth = 0
    cur: List[str] = []
    for c in decl:
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    out.append("".join(cur))
    return out


def va_modules(path: str, search: Sequence[str] = (), defines: Sequence[str] = ()
               ) -> Tuple[Dict[str, VaModule], List[str]]:
    """The Verilog-A modules of path (see _va_modules), as the HSPICE route reads them; the Spectre
    dialects read them with spectre_va_modules."""
    return _va_modules(path, search, defines, False)


def spectre_va_modules(path: str, search: Sequence[str] = (), defines: Sequence[str] = ()
                       ) -> Tuple[Dict[str, VaModule], List[str]]:
    """va_modules for spectre.py and the spectre-spice dialect (phase 2's integration): the header's
    attributes may also follow the port list, as every CMC tree writes them (§4.5 item 13 [E64]), and an
    `analog function` does not end the declarations (C's seams); the HSPICE route keeps va_modules."""
    return _va_modules(path, search, defines, True)


def _va_modules(path: str, search: Sequence[str], defines: Sequence[str], spectre: bool
                ) -> Tuple[Dict[str, VaModule], List[str]]:
    """The Verilog-A modules of path by lower-cased name, and every file read (§3.1): `include is
    followed, recursively, relative to the including file and then the search directories
    (CDS_VLOGA_INCLUDE and -I; the *.vams standard headers are skipped), and the declaration
    preprocessor runs with `defines` (-va,define's NAME or NAME=value) defined.  Each VaModule holds
    the names its `parameter` and `aliasparam` declarations give (not localparam; the (* ... *)
    attributes skipped) and the module header's attributes.  When the preprocessor cannot finish
    (an undefined macro at a declaration, an unbalanced `ifdef, an include not found), params is
    None and attrs["vamos_unknown_params"] names the reason: the caller notes it, and the engines
    still fail loudly on a wrong parameter name.  spice.d_hdl uses it too, and both parses store
    the result in Netlist.va_modules (§4.1 item 16).  spectre (spectre_va_modules, phase 2): the
    header's attributes may also follow the port list, and an `analog function` does not end the
    declarations."""
    pre = _VaPre(search, defines)
    pre.read(path)
    text = pre.expand("\n".join(pre.out))             # whole: a macro call may span lines
    out: Dict[str, VaModule] = {}
    pos = 0
    while True:
        m = _VA_MODULE_HEAD.search(text, pos)
        if m is None:
            break
        name = m.group("name")
        end = text.find("endmodule", m.end())
        body = text[m.end():end if end >= 0 else len(text)]
        pos = end + len("endmodule") if end >= 0 else len(text)
        attrs: Dict[str, str] = {}
        if m.group("attrs"):
            for k, v in _VA_ATTR.findall(m.group("attrs")):
                attrs[k] = v.strip('"')
        if spectre:
            post = _VA_POST_PORT_ATTRS.match(body)
            if post is not None:
                for k, v in _VA_ATTR.findall(post.group("attrs")):
                    attrs.setdefault(k, v.strip('"'))
        params, bad = _va_params(body, spectre)
        reason = None
        if bad is not None:
            reason = "an undefined macro at a parameter declaration (%s)" % bad.strip()[:40]
        elif pre.trouble:
            reason = pre.trouble[0]
        if reason is not None:
            attrs["vamos_unknown_params"] = reason
            params = None
        out.setdefault(name.lower(), VaModule(name, os.path.abspath(path), params, attrs))
    return out, list(pre.files)
