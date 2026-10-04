"""Read the design.vhd that iverilog-sv2ghdl writes (docs/VAMOS_AMS_DESIGN.md §5.3).

tgt-vhdl's output is regular, so this is not a VHDL front end: it reads
exactly what tgt-vhdl (plus sv-rename-variants / sv-dedup-vhdl) emits and
raises NoteError on anything else, naming file:line.  What it keeps:

  Entity        name, ports (name, mode, type, range), the provenance comment
                "-- Generated from Verilog module <m> (<file>:<line>)" above
                "entity X is" (Entity.module), the "--   P = v" lines and the
                attribute nvc_verilog_params (Entity.params), the verbatim
                port clause (for clones), the sv2vhdl:deferred stub marker.
  Architecture  signals with their initial value and trailing comment ("--
                Declared at f:l", "-- Needed to connect outputs" = a _Readable
                shadow, "-- Temporary created at f:l", "-- Port buffer of input
                <port> of instance <label> ..." = a port buffer), aliases ("alias x is
                y(3)", "alias x is y(4 downto 2)", T2), constants, the signals
                each impure function reads, and the concurrent statements in
                textual order (Stmt).
  Stmt          kind 'instance' (label, library, entity, port map with
                formals and actuals, the comments above it: "-- Verilog
                instance: <path>" from T3 (Stmt.vpath), "-- sv_strength: ..."
                (Stmt.strength), "-- Generated from instantiation at f:l"
                (Stmt.origin)), kind 'process' (a process, a concurrent signal
                assignment, a selected assignment) with its signal assignments
                (Assign: '<=', ':=', force, release; target bits; the right-hand
                side as a plain copy, element by element (Assign.parts:
                references and logic3d literals, so pa <= t5 & tz & t3 and
                v <= logic3d_vector'(L3D_Z, L3D_1) can be read bit by bit), Z
                only, weak literals only, delayed, its reads), whether it has
                control flow (Stmt.control, Stmt.straight) and what it reads:
                sens_reads (sensitivity list), cond_reads (conditions and
                waits), ctrl_reads (all reads outside assignments), var_reads
                (the reads of assignments to process variables, e.g. the
                translator's `v_nba_q := l3d_strengthen(x)` for `q <= x` in a
                clocked always block), reads (everything); kind 'call'
                (concurrent procedure call or assert).
                Constant port actuals and signal initial values are kept
                element by element too (Assoc.elems, SignalDecl.init_elems).

parse(path) / parse_text(text, path) -> VhdlDesign; Entity.context is the
entity's library/use clauses (re-used for clones and re-emitted parents).

translation_runs(text) / read_translation_runs(nvc_dir) -> the per-module
tgt-vhdl runs that sv2vhdl-modules concatenates into <nvc>/_mods.vhd before
sv-dedup-vhdl makes design.vhd of it: per run, its entities in order (name,
Verilog module, "--   P = v" values), the run's root (the module it
translated, elaborated on its own with every parameter at its default) last.
Two runs can make a same-named variant whose "--   P = v" values differ (a
real or string parameter does not enter the variant's name), and design.vhd
keeps only the first; the cut reads the elaborated values from the top's run.

Bits.  Ref.indices are VHDL indices in left-to-right order (None = the whole
signal); Range.offset() turns an index into the offset from the right bound,
which is the bit number every other module uses (tgt-vhdl declares every
vector (w-1 downto 0), so for vectors offset == index).

Positions.  Every entity, architecture and statement keeps its character span
in VhdlDesign.text so cut.emit can re-emit an architecture with a few
statements changed (Stmt.span, Stmt.ent_span: the entity-name token).

The sv2vhdl library entities (sv2vhdl.sv_*) are not in design.vhd; their port
modes come from vamos/ams/sv2vhdl_modes.py, which is generated from the nvc
sources by render_modes_module() (a test regenerates and compares it):

    python3 -c "from vamos.ams import vhdl; \\
        open('vamos/ams/sv2vhdl_modes.py', 'w').write( \\
        vhdl.render_modes_module('/usr/local/src/nvc/lib/sv2vhdl'))"
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from vamos.notes import Note, NoteError, error

# -- tgt-vhdl naming (iverilog/tgt-vhdl/scope.cc) ------------------------------

# is_vhdl_reserved_word(): the exact list tgt-vhdl renames, matched
# case-insensitively: VHDL-93, then the words later revisions, PSL and nvc reserve
# (iverilog df8369d00: protected, context, force, release, ..., pipe).
VHDL_RESERVED = frozenset((
    "abs", "access", "after", "alias", "all", "and", "architecture", "array", "assert",
    "attribute", "begin", "block", "body", "buffer", "bus", "case", "component",
    "configuration", "constant", "disconnect", "downto", "else", "elsif", "end", "entity",
    "exit", "file", "for", "function", "generate", "generic", "group", "guarded", "if",
    "impure", "in", "inertial", "inout", "is", "label", "library", "linkage", "literal",
    "loop", "map", "mod", "nand", "new", "next", "nor", "not", "null", "of", "on", "open",
    "or", "others", "out", "package", "port", "postponed", "procedure", "process", "pure",
    "range", "record", "register", "reject", "rem", "report", "return", "rol", "ror",
    "select", "severity", "signal", "shared", "sla", "sll", "sra", "srl", "subtype", "then",
    "to", "transport", "type", "unaffected", "units", "until", "use", "variable", "wait",
    "when", "while", "with", "xnor", "xor",
    # IEEE 1076-2000/2002
    "protected",
    # IEEE 1076-2008: keywords and PSL reserved words
    "assume", "assume_guarantee", "context", "cover", "default", "fairness", "force",
    "parameter", "property", "release", "restrict", "restrict_guarantee", "sequence",
    "strong", "vmode", "vprop", "vunit",
    # IEEE 1076-2019
    "private", "view", "vpkg",
    # nvc: reverse_range (every standard), pipe (the kev-cam fork's --std=2040 construct)
    "reverse_range", "pipe"))


def _collapse_underscores(s: str) -> str:
    while "__" in s:
        s = s.replace("__", "_")
    return s


def make_safe_name(verilog: str, entity_collision: bool = False) -> str:
    """tgt-vhdl's make_safe_name() for a non-temporary Verilog signal or port.

    Leading '_' -> 'sig' prefix, trailing '_' -> 'sig' suffix, '__' collapsed,
    '_sig' when the name equals an entity name (entity_collision: tgt-vhdl
    only knows the entities created so far, so this differs between variants),
    then '_sig' for a reserved word.
    """
    base = verilog
    if base.startswith("_"):
        base = "sig" + base
    if base.endswith("_"):
        base += "sig"
    base = _collapse_underscores(base)
    if entity_collision:
        base += "_sig"
    if base.lower() in VHDL_RESERVED:
        base += "_sig"
    return base


_COLLISION_SUFFIX = re.compile(r"_\d+\Z")


def safe_name_matches(vhdl: str, verilog: str) -> bool:
    """Whether VHDL name `vhdl` is what tgt-vhdl makes of Verilog name `verilog`.

    Case-insensitive; allows the entity-collision '_sig' and the '_<n>' that
    avoid_name_collision() appends after a case-only clash (out / OUT).
    """
    v = vhdl.lower()
    cands = {make_safe_name(verilog).lower(), make_safe_name(verilog, True).lower()}
    if v in cands:
        return True
    m = _COLLISION_SUFFIX.search(v)
    return bool(m) and v[:m.start()] in cands


def valid_entity_name(module: str) -> str:
    """tgt-vhdl's valid_entity_name() without the '<n>' de-duplication suffix."""
    name = _collapse_underscores(module)
    if name.startswith("_"):
        name = "module" + name
    if name.endswith("_"):
        name += "module"
    if name.lower() in VHDL_RESERVED:
        name += "_module"
    return name


# -- data model ------------------------------------------------------------------

@dataclass(frozen=True)
class Range:
    """A VHDL discrete range: (left downto right) or (left to right)."""
    left: int
    right: int
    downto: bool = True

    @property
    def width(self) -> int:
        return abs(self.left - self.right) + 1

    def indices(self) -> List[int]:
        """VHDL indices left to right."""
        step = -1 if self.downto else 1
        return list(range(self.left, self.right + step, step))

    def offset(self, index: int) -> int:
        """Offset of an index from the right bound (the bit number)."""
        return index - self.right if self.downto else self.right - index

    def contains(self, index: int) -> bool:
        lo, hi = min(self.left, self.right), max(self.left, self.right)
        return lo <= index <= hi


LOGIC_TYPES = ("logic3d", "resolved_logic3d", "logic3d_vector", "resolved_logic3d_vector")


@dataclass
class TypeSpec:
    """A subtype indication as tgt-vhdl writes it: a type mark and an optional range."""
    mark: str                        # lowercased type mark
    rng: Optional[Range] = None
    text: str = ""

    @property
    def kind(self) -> str:
        """'logic' (the logic3d family), 'real', or 'other'."""
        if self.mark in LOGIC_TYPES:
            return "logic"
        if self.mark == "real":
            return "real"
        return "other"

    @property
    def vector(self) -> bool:
        return self.rng is not None and self.mark.endswith("_vector")

    @property
    def width(self) -> int:
        return self.rng.width if self.vector and self.rng is not None else 1

    def offsets(self) -> List[int]:
        """Bit offsets left to right (one bit for a scalar or a non-logic type)."""
        if self.vector and self.rng is not None:
            return [self.rng.offset(i) for i in self.rng.indices()]
        return [0]


@dataclass
class Ref:
    """A (possibly indexed or sliced) reference to a named object.

    indices: VHDL indices left to right, or None for the whole object.
    exact: False when an index or slice bound is not a constant (the
    reference then stands for the whole object).
    """
    name: str
    indices: Optional[Tuple[int, ...]] = None
    exact: bool = True

    @property
    def lname(self) -> str:
        return self.name.lower()


@dataclass
class PortDecl:
    name: str
    mode: str                        # in | out | inout | buffer | linkage
    type: TypeSpec
    line: int = 0

    @property
    def lname(self) -> str:
        return self.name.lower()


@dataclass
class Entity:
    name: str
    ports: List[PortDecl] = field(default_factory=list)
    module: Optional[str] = None     # Verilog module from the provenance comment (exact spelling)
    module_file: str = ""
    module_line: int = 0
    params: Dict[str, str] = field(default_factory=dict)        # attribute nvc_verilog_params
    param_comments: Dict[str, str] = field(default_factory=dict)  # "--   N = 8" lines (all params)
    verilog_src: str = ""
    deferred: bool = False
    port_clause: str = ""            # "port (\n ... \n  );" verbatim, "" without ports
    context: str = ""                # the context clause in front of the entity
    span: Tuple[int, int] = (0, 0)
    line: int = 0

    @property
    def lname(self) -> str:
        return self.name.lower()

    def port(self, name: str) -> Optional[PortDecl]:
        n = name.lower()
        for p in self.ports:
            if p.lname == n:
                return p
        return None


@dataclass
class SignalDecl:
    name: str
    type: TypeSpec
    init: str = ""
    comment: str = ""                # trailing comment text (without "--")
    line: int = 0

    @property
    def lname(self) -> str:
        return self.name.lower()

    @property
    def declared_at(self) -> Optional[Tuple[str, int]]:
        """(file, line) from "-- Declared at <file>:<line>", else None."""
        m = re.match(r"\s*Declared at (.*):(\d+)\s*$", self.comment)
        return (m.group(1), int(m.group(2))) if m else None

    @property
    def created_at(self) -> Optional[str]:
        """"file:line" from tgt-vhdl's "-- Temporary created at <file>:<line>", else None."""
        m = re.match(r"\s*Temporary created at (\S+:\d+)\s*$", self.comment)
        return m.group(1) if m else None

    @property
    def readable_shadow(self) -> bool:
        """tgt-vhdl's port shadow (map_signal: "Needed to connect outputs")."""
        return "Needed to connect outputs" in self.comment

    @property
    def port_buffer(self) -> Optional[Tuple[str, str]]:
        """(port, instance) of tgt-vhdl's port buffer, else None.

        The iverilog core buffers an input port whose net the module also drives
        inside (a cut shell's marker on an inout or output port does); where the
        instance connects that port to a variable, an expression or a constant,
        tgt-vhdl draws the buffer in the parent as a resolved signal
        "PB_<label>_<port> ... -- Port buffer of input <port> of instance <label>
        (its net is also driven inside)", assigns it the actual (`PB_.. <= <actual>`)
        and associates it with the port.  The copy is one-way, as in Verilog: the
        module's drivers reach the signal, not the actual.  A temporary.
        """
        m = re.match(r"\s*Port buffer of input (\S+) of instance (\S+)", self.comment)
        return (m.group(1), m.group(2)) if m else None

    @property
    def temporary(self) -> bool:
        """A translator-made signal: no "-- Declared at" comment."""
        return self.declared_at is None

    @property
    def init_elems(self) -> Optional[List[str]]:
        """The initial value bit by bit, left to right, as logic3d literals.

        Lowercased, with the logic3d_types_pkg aliases folded (L3D_0ZX is
        l3d_z); one element per bit of the declared type, so element k is the
        bit at offset width-1-k of a (w-1 downto 0) vector.  None when there
        is no initial value or it is not a constant literal, aggregate or
        concatenation (see literal_elems).
        """
        if not self.init:
            return None
        try:
            toks, _ = lex(self.init)
        except NoteError:
            return None
        width = self.type.width if self.type.vector else 1
        elems = literal_elems(toks, width)
        if elems is None or len(elems) != width:
            return None
        return elems


@dataclass
class AliasDecl:
    name: str
    target: Ref
    line: int = 0

    @property
    def lname(self) -> str:
        return self.name.lower()


@dataclass
class Assign:
    """One signal assignment inside a process (or a concurrent assignment).

    op: '<=' (signal assignment), ':=' (tgt-vhdl's STD_MX signal deposit),
    'force' or 'release'.  copy: the right-hand side as plain references
    joined by '&' (names, indexed names, slices), or None when it is any other
    expression.  parts: the right-hand side element by element, left to
    right, when it is a concatenation of plain references (a Ref each) and
    logic3d constants (one lowercased literal per element: L3D_Z, a
    positional aggregate such as logic3d_vector'(L3D_Z, L3D_1)), else None;
    a copy has parts too.  z_only: it can only assign L3D_Z.  weak_only: only
    weak literals (L3D_L, L3D_H, L3D_W).  const: no signal is read on the
    right.  delayed: an 'after' clause (the value lands later; until then the
    driver holds its previous value).
    """
    target: Ref
    op: str
    copy: Optional[List[Ref]] = None
    reads: List[Ref] = field(default_factory=list)
    z_only: bool = False
    weak_only: bool = False
    const: bool = False
    line: int = 0
    parts: Optional[List[object]] = None     # Ref | str (a literal), left to right
    delayed: bool = False


@dataclass
class Assoc:
    """One port (or generic) association of an instance statement.

    actual: the actual as plain references joined by '&' (one Ref for a
    name / indexed name / slice), or None for 'open' or any other expression;
    reads: the signals an expression actual reads; z_only / const describe a
    constant actual (L3D_Z, (others => L3D_Z), L3D_1, ...); elems: a
    constant actual element by element, left to right (lowercased literals:
    logic3d_vector'(L3D_Z, L3D_1) gives ['l3d_z', 'l3d_1']), else None.
    """
    formal: Ref
    text: str
    actual: Optional[List[Ref]] = None
    reads: List[Ref] = field(default_factory=list)
    open: bool = False
    const: bool = False
    z_only: bool = False
    weak_only: bool = False
    elems: Optional[List[str]] = None


@dataclass
class Stmt:
    """A concurrent statement of an architecture, in textual order."""
    kind: str                        # 'instance' | 'process' | 'call'
    index: int                       # position in Architecture.stmts
    arch: str                        # owning architecture's entity (lowercased)
    label: Optional[str] = None
    line: int = 0
    span: Tuple[int, int] = (0, 0)   # from the label (or first token) to the ';' after 'end ...'
    comments: List[str] = field(default_factory=list)   # comment lines above the statement
    # instance
    lib: str = ""
    entity: str = ""
    arch_name: str = ""
    ent_span: Tuple[int, int] = (0, 0)
    generics: Dict[str, str] = field(default_factory=dict)
    assocs: List[Assoc] = field(default_factory=list)
    # process / concurrent assignment / call
    assigns: List[Assign] = field(default_factory=list)
    reads: List[Ref] = field(default_factory=list)       # everything read (all of the below)
    ctrl_reads: List[Ref] = field(default_factory=list)  # read outside assignments: conditions, waits, calls
    cond_reads: List[Ref] = field(default_factory=list)  # read by if/elsif/case/loop conditions and waits
    var_reads: List[Ref] = field(default_factory=list)   # read by assignments to process variables
    sens_reads: List[Ref] = field(default_factory=list)  # named in the sensitivity list
    variables: Set[str] = field(default_factory=set)
    sens: Optional[List[str]] = None  # sensitivity list (['all'] for process (all))
    simple: bool = False             # exactly one assignment, no other statement
    form: str = ""                   # 'process' | 'cassign' | 'select' | 'call' | 'assert'
    control: int = 0                 # if/case/loop/wait/call statements; conditional waveforms

    @property
    def sid(self) -> str:
        return "%s#%d" % (self.arch, self.index)

    @property
    def straight(self) -> bool:
        """A process or concurrent assignment with no control flow: every one of
        its assignments runs, unconditionally, each time it runs (once at
        initialisation at least)."""
        return self.kind == "process" and self.control == 0

    @property
    def fused(self) -> bool:
        return bool(self.label) and self.label.lower().startswith("comb_fused")

    @property
    def vpath(self) -> Optional[str]:
        """The T3 "-- Verilog instance: <relative path>" comment, if present."""
        for c in self.comments:
            m = re.match(r"\s*Verilog instance:\s*(\S.*?)\s*$", c)
            if m:
                return m.group(1)
        return None

    @property
    def strength(self) -> Optional[Tuple[str, str]]:
        """("supply1", "supply0") from "-- sv_strength: supply1 supply0", else None."""
        for c in self.comments:
            m = re.match(r"\s*sv_strength:\s*(\w+?)1\s+(\w+?)0\s*$", c)
            if m:
                return (m.group(1).lower(), m.group(2).lower())
        return None

    @property
    def origin(self) -> str:
        """file:line of "-- Generated from instantiation at ..." or "... process in m (f:l)"."""
        for c in self.comments:
            m = re.match(r"\s*Generated from instantiation at (\S+)\s*$", c)
            if m:
                return m.group(1)
            # a merged same-edge process (tgt-vhdl merge_cluster) appends
            # " [+ merged same-edge always block(s): f:l, ...]": the first f:l is its own
            m = re.match(r"\s*Generated from .* \((\S+:\d+)\)"
                         r"(?:\s*\[\+ merged same-edge always block\(s\):[^\]]*\])?\s*$", c)
            if m:
                return m.group(1)
        return ""

    def assoc(self, formal: str) -> Optional[Assoc]:
        f = formal.lower()
        for a in self.assocs:
            if a.formal.lname == f:
                return a
        return None


@dataclass
class Architecture:
    name: str
    entity: str                      # entity name as written
    signals: Dict[str, SignalDecl] = field(default_factory=dict)   # lowercased name ->
    aliases: Dict[str, AliasDecl] = field(default_factory=dict)
    constants: Set[str] = field(default_factory=set)
    functions: Dict[str, List[Ref]] = field(default_factory=dict)  # impure function -> signals it reads
    stmts: List[Stmt] = field(default_factory=list)
    ports: Set[str] = field(default_factory=set)   # lowercased port names of the entity
    comment: str = ""
    span: Tuple[int, int] = (0, 0)
    line: int = 0

    @property
    def lentity(self) -> str:
        return self.entity.lower()


@dataclass
class VhdlDesign:
    path: str
    text: str
    entities: Dict[str, Entity] = field(default_factory=dict)        # lowercased name ->
    archs: Dict[str, Architecture] = field(default_factory=dict)     # lowercased entity ->
    order: List[Tuple[str, str]] = field(default_factory=list)       # ('entity'|'architecture', lname)
    notes: List[Note] = field(default_factory=list)

    def entity(self, name: str) -> Optional[Entity]:
        return self.entities.get(name.lower())

    def arch(self, entity: str) -> Optional[Architecture]:
        return self.archs.get(entity.lower())

    def variants(self, module: str) -> List[Entity]:
        """Entities generated from Verilog module `module` (exact spelling), in file order."""
        return [self.entities[n] for k, n in self.order
                if k == "entity" and self.entities[n].module == module]

    def where(self, line: int) -> str:
        return "%s:%d" % (self.path, line)


# -- lexer -------------------------------------------------------------------------

ID, NUM, STR, CHR, OP, BITSTR = "id", "num", "str", "chr", "op", "bitstr"

_RX = re.compile(r"""
   (?P<nl>\n)
  |(?P<ws>[ \t\r\f\v]+)
  |(?P<comment>--[^\n]*)
  |(?P<bitstr>(?:[0-9]+)?(?:[uUsS]?[bBoOxX]|[dD])"[0-9A-Za-z_]*")
  |(?P<id>[A-Za-z][A-Za-z0-9_]*)
  |(?P<num>[0-9][0-9_]*(?:\#[0-9A-Fa-f_]+(?:\.[0-9A-Fa-f_]+)?\#|\.[0-9][0-9_]*)?(?:[eE][+-]?[0-9]+)?)
  |(?P<str>"(?:[^"\n]|"")*")
  |(?P<ext>\\(?:[^\\\n]|\\\\)*\\)
  |(?P<op>\?/=|\?<=|\?>=|\?\?|\?=|<<|>>|<=|:=|=>|/=|>=|\*\*|<>|[-+*/&|()<>=,;:.\[\]@^?])
""", re.X)


class Tok:
    __slots__ = ("kind", "text", "low", "start", "end", "line")

    def __init__(self, kind: str, text: str, start: int, end: int, line: int):
        self.kind = kind
        self.text = text
        self.low = text.lower() if kind == ID else text
        self.start = start
        self.end = end
        self.line = line

    def __repr__(self) -> str:
        return "Tok(%s %r @%d)" % (self.kind, self.text, self.line)


class _Comment:
    __slots__ = ("text", "start", "line")

    def __init__(self, text: str, start: int, line: int):
        self.text = text
        self.start = start
        self.line = line


def lex(text: str, path: str = "") -> Tuple[List[Tok], List[_Comment]]:
    toks: List[Tok] = []
    comments: List[_Comment] = []
    pos, line, n = 0, 1, len(text)
    while pos < n:
        c = text[pos]
        if c == "'":
            prev = toks[-1] if toks else None
            tick = prev is not None and (prev.kind == ID or prev.text in (")", "]")
                                         or prev.kind == STR)
            if not tick and pos + 2 < n and text[pos + 2] == "'" and text[pos + 1] != "\n":
                toks.append(Tok(CHR, text[pos:pos + 3], pos, pos + 3, line))
                pos += 3
                continue
            toks.append(Tok(OP, "'", pos, pos + 1, line))
            pos += 1
            continue
        m = _RX.match(text, pos)
        if not m:
            raise NoteError([error("%s:%d" % (path, line), "cannot read VHDL text near %r"
                                   % text[pos:pos + 20])])
        kind = m.lastgroup
        s = m.group(0)
        if kind == "nl":
            line += 1
        elif kind == "ws":
            pass
        elif kind == "comment":
            comments.append(_Comment(s[2:], pos, line))
        elif kind == "ext":
            raise NoteError([error("%s:%d" % (path, line), "extended identifier %s is not "
                                   "expected in tgt-vhdl output" % s)])
        else:
            toks.append(Tok({"id": ID, "num": NUM, "str": STR, "op": OP, "bitstr": BITSTR}[kind],
                            s, pos, m.end(), line))
        pos = m.end()
    return toks, comments


# -- small integer expressions (slice bounds such as "4 + 1") -------------------

def int_eval(toks: Sequence[Tok], names: Optional[Dict[str, int]] = None) -> Optional[int]:
    """Evaluate + - * / ( ) over integer literals (and `names`), else None."""
    names = names or {}
    pos = [0]

    def peek() -> Optional[Tok]:
        return toks[pos[0]] if pos[0] < len(toks) else None

    def primary() -> Optional[int]:
        t = peek()
        if t is None:
            return None
        if t.text == "(":
            pos[0] += 1
            v = expr()
            t2 = peek()
            if v is None or t2 is None or t2.text != ")":
                return None
            pos[0] += 1
            return v
        if t.text in ("-", "+"):
            pos[0] += 1
            v = primary()
            return None if v is None else (-v if t.text == "-" else v)
        if t.kind == NUM and re.match(r"^[0-9_]+$", t.text):
            pos[0] += 1
            return int(t.text.replace("_", ""))
        if t.kind == ID and t.low in names:
            pos[0] += 1
            return names[t.low]
        return None

    def term() -> Optional[int]:
        v = primary()
        while v is not None:
            t = peek()
            if t is None or t.text not in ("*", "/"):
                break
            pos[0] += 1
            w = primary()
            if w is None or (t.text == "/" and w == 0):
                return None
            v = v * w if t.text == "*" else int(v / w)
        return v

    def expr() -> Optional[int]:
        v = term()
        while v is not None:
            t = peek()
            if t is None or t.text not in ("+", "-"):
                break
            pos[0] += 1
            w = term()
            if w is None:
                return None
            v = v + w if t.text == "+" else v - w
        return v

    v = expr()
    return v if v is not None and pos[0] == len(toks) else None


# -- parser --------------------------------------------------------------------------

_SEQ_END = ("end", "elsif", "else", "when")
_Z_LIT = "l3d_z"
_WEAK_LITS = ("l3d_l", "l3d_h", "l3d_w")
_L3D_LITS = ("l3d_0", "l3d_1", "l3d_l", "l3d_h", "l3d_z", "l3d_w", "l3d_x", "l3d_u",
             "l3d_0z", "l3d_1z", "l3d_0x", "l3d_1x", "l3d_0zx", "l3d_1zx")
# logic3d_types_pkg aliases: the same codes as the names tgt-vhdl writes
_LIT_ALIAS = {"l3d_0zx": "l3d_z", "l3d_0z": "l3d_l", "l3d_1z": "l3d_h", "l3d_1zx": "l3d_w",
              "l3d_0x": "l3d_x", "l3d_1x": "l3d_u"}


def literal_elems(toks: Sequence[Tok], width: Optional[int] = None) -> Optional[List[str]]:
    """The elements, left to right, of a constant logic3d value; None for anything else.

    Accepted (as tgt-vhdl writes them): a literal (L3D_1); a positional
    aggregate with or without its type mark (logic3d_vector'(L3D_Z, L3D_1));
    the one-element form (0 => L3D_1); one literal for a whole range
    (1 downto 0 => L3D_X); (others => L3D_Z) and positional elements followed
    by 'others', when `width` (the number of elements the context needs) is
    given; and a concatenation of these with '&' ('others' then needs no
    width of its own and is refused).  Literals are lowercased with the
    logic3d_types_pkg aliases folded (L3D_0ZX -> l3d_z).
    """
    parts: List[List[Tok]] = [[]]
    depth = 0
    for t in toks:
        if t.text == "(":
            depth += 1
        elif t.text == ")":
            depth -= 1
        if t.text == "&" and depth == 0:
            parts.append([])
        else:
            parts[-1].append(t)
    if len(parts) > 1:
        width = None
    out: List[str] = []
    for p in parts:
        e = _const_elems(p, width)
        if e is None:
            return None
        out.extend(e)
    return out


def _lit(t: Tok) -> Optional[str]:
    if t.kind == ID and t.low in _L3D_LITS:
        return _LIT_ALIAS.get(t.low, t.low)
    return None


def _const_elems(toks: Sequence[Tok], width: Optional[int]) -> Optional[List[str]]:
    """One operand of literal_elems: a literal or a (qualified) aggregate of literals."""
    toks = list(toks)
    if len(toks) == 1:
        lit = _lit(toks[0])
        return [lit] if lit is not None else None
    # type mark qualification: <mark>'( ... )
    if len(toks) >= 3 and toks[0].kind == ID and toks[1].text == "'" and toks[2].text == "(":
        toks = toks[2:]
    if len(toks) < 2 or toks[0].text != "(" or toks[-1].text != ")":
        return None
    depth = 0
    for k, t in enumerate(toks):
        if t.text == "(":
            depth += 1
        elif t.text == ")":
            depth -= 1
            if depth == 0 and k != len(toks) - 1:
                return None                   # (a) & ... was split above; this is (x)(y)
    items: List[List[Tok]] = [[]]
    depth = 0
    for t in toks[1:-1]:
        if t.text == "(":
            depth += 1
        elif t.text == ")":
            depth -= 1
        if t.text == "," and depth == 0:
            items.append([])
        else:
            items[-1].append(t)
    out: List[str] = []
    named = False
    for k, it in enumerate(items):
        arrow = next((j for j, t in enumerate(it) if t.text == "=>"), None)
        if arrow is None:
            if named or len(it) != 1:
                return None
            lit = _lit(it[0])
            if lit is None:
                return None
            out.append(lit)
            continue
        named = True
        if len(it) != arrow + 2:
            return None
        lit = _lit(it[arrow + 1])
        if lit is None:
            return None
        choice = it[:arrow]
        if len(choice) == 1 and choice[0].low == "others":
            if k != len(items) - 1 or width is None or width < len(out):
                return None
            out.extend([lit] * (width - len(out)))
            continue
        if out or len(items) != 1:
            return None          # named choices besides 'others': only alone (the order rules differ)
        count = None
        for j, t in enumerate(choice):
            if t.low in ("downto", "to"):
                left, right = int_eval(choice[:j]), int_eval(choice[j + 1:])
                if left is None or right is None:
                    return None
                step = 1 if t.low == "to" else -1
                count = max(0, (right - left) * step + 1)
                break
        if count is None:
            if int_eval(choice) is None:
                return None
            count = 1
        out.extend([lit] * count)
    return out


class _Parser:
    def __init__(self, text: str, path: str):
        self.text = text
        self.path = path
        self.toks, self.comments = lex(text, path)
        self.i = 0
        self.ci = 0                  # next unattached comment
        self.design = VhdlDesign(path=path, text=text)

    # -- token helpers ----------------------------------------------------------

    def where(self, tok: Optional[Tok] = None) -> str:
        t = tok if tok is not None else self.peek()
        line = t.line if t is not None else (self.toks[-1].line if self.toks else 0)
        return "%s:%d" % (self.path, line)

    def fail(self, msg: str, tok: Optional[Tok] = None) -> NoteError:
        return NoteError([error(self.where(tok), "design.vhd: %s (vamos reads tgt-vhdl output "
                                "only)" % msg)])

    def peek(self, k: int = 0) -> Optional[Tok]:
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else None

    def at(self, *words: str) -> bool:
        t = self.peek()
        return t is not None and t.low in words

    def next(self) -> Tok:
        t = self.peek()
        if t is None:
            raise self.fail("unexpected end of file")
        self.i += 1
        return t

    def expect(self, *words: str) -> Tok:
        t = self.next()
        if t.low not in words:
            raise self.fail("expected %s, found %r" % (" or ".join(repr(w) for w in words), t.text), t)
        return t

    def ident(self) -> Tok:
        t = self.next()
        if t.kind != ID:
            raise self.fail("expected an identifier, found %r" % t.text, t)
        return t

    def skip_to_semicolon(self) -> int:
        """Advance past the next ';' at bracket depth 0; return its token index."""
        depth = 0
        while True:
            t = self.next()
            if t.text in ("(", "["):
                depth += 1
            elif t.text in (")", "]"):
                depth -= 1
            elif t.text == ";" and depth <= 0:
                return self.i - 1

    def balanced(self) -> Tuple[int, int]:
        """At '(': return (first, last) token indices inside the parentheses; consume them.

        For "()" the result is (k, k - 1), an empty slice.
        """
        if not self.at("("):
            raise self.fail("expected '('")
        start = self.i + 1
        depth = 0
        while True:
            t = self.next()
            if t.text == "(":
                depth += 1
            elif t.text == ")":
                depth -= 1
                if depth == 0:
                    return start, self.i - 2

    def comments_before(self, tok: Tok) -> List[str]:
        """Unattached comments that start before `tok` (all are consumed)."""
        out = []
        while self.ci < len(self.comments) and self.comments[self.ci].start < tok.start:
            out.append(self.comments[self.ci].text)
            self.ci += 1
        return out

    def drop_comments_before(self, pos: int) -> None:
        while self.ci < len(self.comments) and self.comments[self.ci].start < pos:
            self.ci += 1

    def trailing_comment(self, semi: Tok) -> str:
        """The comment on the same line right after `semi` (consumed), else ''."""
        if self.ci < len(self.comments):
            c = self.comments[self.ci]
            if c.line == semi.line and c.start >= semi.end:
                between = self.text[semi.end:c.start]
                if between.strip() == "":
                    self.ci += 1
                    return c.text
        return ""

    # -- design file ---------------------------------------------------------------

    def parse(self) -> VhdlDesign:
        d = self.design
        while self.peek() is not None:
            clauses = []
            while self.at("library", "use"):
                a = self.i
                semi_i = self.skip_to_semicolon()
                clauses.append(self.text[self.toks[a].start:self.toks[semi_i].end])
            t = self.peek()
            if t is None:
                break
            context = "\n".join(clauses)
            if t.low == "entity":
                ent = self.entity(context)
                key = ent.lname
                if key in d.entities:
                    raise self.fail("entity %s is defined twice" % ent.name, t)
                d.entities[key] = ent
                d.order.append(("entity", key))
            elif t.low == "architecture":
                arch = self.architecture()
                key = arch.lentity
                if key in d.archs:
                    raise self.fail("entity %s has two architectures" % arch.entity, t)
                d.archs[key] = arch
                d.order.append(("architecture", key))
            else:
                raise self.fail("unexpected design unit starting with %r" % t.text, t)
        for key, arch in d.archs.items():
            if key not in d.entities:
                raise NoteError([error(d.where(arch.line), "architecture %s of %s: no such entity"
                                       % (arch.name, arch.entity))])
        return d

    # -- entity --------------------------------------------------------------------

    def entity(self, context: str) -> Entity:
        start_tok = self.expect("entity")
        comments = self.comments_before(start_tok)
        name = self.ident()
        self.expect("is")
        ent = Entity(name=name.text, context=context, line=start_tok.line)
        prov = None
        pcomments: Dict[str, str] = {}
        for c in comments:
            m = re.match(r"\s*Generated from Verilog module (\S+) \((.*):(\d+)\)\s*$", c)
            if m:
                prov = m
                pcomments = {}
                continue
            m = _PARAM_LINE.match(c)
            if m and prov is not None:
                pcomments[m.group(1)] = m.group(2)
        if prov is not None:
            ent.module, ent.module_file, ent.module_line = (prov.group(1), prov.group(2),
                                                           int(prov.group(3)))
            ent.param_comments = pcomments
        while True:
            t = self.peek()
            if t is None:
                raise self.fail("unterminated entity %s" % ent.name, start_tok)
            if t.low == "end":
                break
            if t.low == "generic":
                self.next()
                self.balanced()
                self.expect(";")
            elif t.low == "port":
                pstart = t.start
                self.next()
                a, b = self.balanced()
                semi = self.expect(";")
                ent.port_clause = self.text[pstart:semi.end]
                ent.ports = self.port_list(a, b)
            elif t.low == "attribute":
                self.attribute_spec(ent)
            else:
                raise self.fail("unexpected %r in entity %s" % (t.text, ent.name), t)
        self.expect("end")
        if self.at("entity"):
            self.next()
        if self.peek() is not None and self.peek().kind == ID and self.peek().low == ent.lname:
            self.next()
        semi = self.expect(";")
        ent.span = (start_tok.start, semi.end)
        # iverilog-sv2ghdl's stub for a module it could not translate
        if "sv2vhdl:deferred" in self.text[start_tok.start:semi.end]:
            ent.deferred = True
        self.drop_comments_before(semi.end)
        return ent

    def attribute_spec(self, ent: Entity) -> None:
        self.expect("attribute")
        name = self.ident()
        t = self.next()
        if t.text == ":":
            self.skip_to_semicolon()
            return
        if t.low != "of":
            raise self.fail("unexpected attribute form", t)
        self.ident()
        self.expect(":")
        self.ident()
        self.expect("is")
        val = self.next()
        self.expect(";")
        if val.kind == STR:
            s = val.text[1:-1].replace('""', '"')
            if name.low == "nvc_verilog_params":
                for item in s.split():
                    k, _, v = item.partition("=")
                    if k:
                        ent.params[k] = v
            elif name.low == "nvc_verilog_src":
                ent.verilog_src = s

    def port_list(self, a: int, b: int) -> List[PortDecl]:
        """Ports between token indices a..b (inclusive): 'n1, n2 : mode type [:= x]; ...'."""
        out: List[PortDecl] = []
        groups: List[List[Tok]] = [[]]
        depth = 0
        for t in self.toks[a:b + 1]:
            if t.text == "(":
                depth += 1
            elif t.text == ")":
                depth -= 1
            if t.text == ";" and depth == 0:
                groups.append([])
            else:
                groups[-1].append(t)
        for g in groups:
            if not g:
                continue
            colon = next((k for k, t in enumerate(g) if t.text == ":"), None)
            if colon is None:
                raise self.fail("cannot read port declaration", g[0])
            names = [t for t in g[:colon] if t.text != ","]
            rest = g[colon + 1:]
            if rest and rest[0].low == "signal":
                rest = rest[1:]
            mode = "in"
            if rest and rest[0].low in ("in", "out", "inout", "buffer", "linkage"):
                mode = rest[0].low
                rest = rest[1:]
            k = next((k for k, t in enumerate(rest) if t.text == ":="), len(rest))
            ts = self.typespec(rest[:k])
            for nt in names:
                if nt.kind != ID:
                    raise self.fail("cannot read port name", nt)
                out.append(PortDecl(nt.text, mode, ts, nt.line))
        return out

    def typespec(self, toks: Sequence[Tok]) -> TypeSpec:
        if not toks:
            raise self.fail("missing type")
        text = self.text[toks[0].start:toks[-1].end]
        mark = toks[0].low
        k = 1
        while k + 1 < len(toks) and toks[k].text == "." and toks[k + 1].kind == ID:
            mark = toks[k + 1].low       # library.package.type -> type
            k += 2
        rng = None
        rest = list(toks[k:])
        if rest and rest[0].text == "(" and rest[-1].text == ")":
            rng = self.range_of(rest[1:-1])
        elif rest and rest[0].low == "range":
            rng = self.range_of(rest[1:])
        return TypeSpec(mark, rng, text)

    def range_of(self, toks: Sequence[Tok]) -> Optional[Range]:
        depth = 0
        for k, t in enumerate(toks):
            if t.text == "(":
                depth += 1
            elif t.text == ")":
                depth -= 1
            elif depth == 0 and t.low in ("downto", "to"):
                left = int_eval(toks[:k])
                right = int_eval(toks[k + 1:])
                if left is None or right is None:
                    return None
                return Range(left, right, t.low == "downto")
        return None

    # -- architecture ----------------------------------------------------------------

    def architecture(self) -> Architecture:
        start_tok = self.expect("architecture")
        comments = self.comments_before(start_tok)
        name = self.ident()
        self.expect("of")
        ent = self.ident()
        self.expect("is")
        arch = Architecture(name=name.text, entity=ent.text, line=start_tok.line,
                            comment="\n".join(comments))
        entity = self.design.entities.get(ent.low)
        if entity is None:
            raise self.fail("architecture %s of %s precedes its entity" % (name.text, ent.text),
                            start_tok)
        arch.ports = {q.lname for q in entity.ports}
        self.declarations(arch)
        self.expect("begin")
        while not self.at("end"):
            self.concurrent(arch)
        self.expect("end")
        if self.at("architecture"):
            self.next()
        if self.peek() is not None and self.peek().kind == ID and self.peek().low == name.low:
            self.next()
        semi = self.expect(";")
        arch.span = (start_tok.start, semi.end)
        self.drop_comments_before(semi.end)
        return arch

    def declarations(self, arch: Architecture) -> None:
        while not self.at("begin"):
            t = self.peek()
            if t is None:
                raise self.fail("unterminated architecture %s" % arch.name)
            if t.low == "signal":
                self.next()
                names = [self.ident()]
                while self.at(","):
                    self.next()
                    names.append(self.ident())
                self.expect(":")
                a = self.i
                depth = 0
                while True:
                    u = self.peek()
                    if u is None:
                        raise self.fail("unterminated signal declaration", t)
                    if u.text == "(":
                        depth += 1
                    elif u.text == ")":
                        depth -= 1
                    if depth == 0 and u.text in (":=", ";"):
                        break
                    self.i += 1
                ts = self.typespec(self.toks[a:self.i])
                init = ""
                if self.at(":="):
                    self.next()
                    b = self.i
                    semi_i = self.skip_to_semicolon()
                    init = self.text[self.toks[b].start:self.toks[semi_i - 1].end] if semi_i > b else ""
                    semi = self.toks[semi_i]
                else:
                    semi = self.expect(";")
                self.drop_comments_before(semi.start)
                comment = self.trailing_comment(semi)
                for nt in names:
                    arch.signals[nt.low] = SignalDecl(nt.text, ts, init, comment, nt.line)
            elif t.low == "alias":
                self.next()
                nt = self.ident()
                if self.at(":"):
                    self.next()
                    while not self.at("is"):
                        self.next()
                self.expect("is")
                a = self.i
                semi_i = self.skip_to_semicolon()
                ref = self.ref_of(self.toks[a:semi_i])
                if ref is None:
                    raise self.fail("cannot read alias %s" % nt.text, nt)
                arch.aliases[nt.low] = AliasDecl(nt.text, ref, nt.line)
                self.drop_comments_before(self.toks[semi_i].end)
            elif t.low == "constant":
                self.next()
                arch.constants.add(self.ident().low)
                self.skip_to_semicolon()
            elif t.low == "component":
                self.next()
                self.ident()
                while not self.at("end"):
                    if self.peek() is None:
                        raise self.fail("unterminated component", t)
                    self.next()
                self.expect("end")
                self.expect("component")
                if self.peek() is not None and self.peek().kind == ID:
                    self.next()
                self.expect(";")
            elif t.low in ("impure", "pure", "function", "procedure"):
                self.subprogram(arch)
            elif t.low in ("type", "subtype", "attribute", "shared", "file", "use", "variable"):
                self.skip_to_semicolon()
            else:
                raise self.fail("unexpected %r in the declarations of architecture %s"
                                % (t.text, arch.name), t)
        self.drop_comments_before(self.peek().start)

    def subprogram(self, arch: Architecture) -> None:
        """Skip a function/procedure declaration or body; record the signals a body reads."""
        if self.at("impure", "pure"):
            self.next()
        kw = self.expect("function", "procedure")
        name = self.ident()
        # parameters, return type: up to 'is' (a body) or ';' (a declaration)
        depth = 0
        params: Set[str] = set()
        while True:
            t = self.next()
            if t.text == "(":
                depth += 1
                # parameter names: identifiers before ':' at depth 1
            elif t.text == ")":
                depth -= 1
            elif depth == 1 and t.kind == ID:
                nxt = self.peek()
                if nxt is not None and nxt.text in (":", ","):
                    params.add(t.low)
            elif depth == 0 and t.text == ";":
                return
            elif depth == 0 and t.low == "is":
                break
        local = set(params)
        while not self.at("begin"):
            t = self.peek()
            if t is None:
                raise self.fail("unterminated %s %s" % (kw.text, name.text), kw)
            if t.low in ("variable", "constant"):
                self.next()
                local.add(self.ident().low)
                while self.at(","):
                    self.next()
                    local.add(self.ident().low)
                self.skip_to_semicolon()
            elif t.low in ("impure", "pure", "function", "procedure"):
                self.subprogram(arch)
            else:
                self.skip_to_semicolon()
        self.expect("begin")
        body = _Body(self, arch, local)
        body.statements(stop=("end",))
        self.expect("end")
        if self.at("function", "procedure"):
            self.next()
        if self.peek() is not None and self.peek().kind == ID:
            self.next()
        semi = self.expect(";")
        self.drop_comments_before(semi.end)
        arch.functions[name.low] = body.reads

    # -- concurrent statements ---------------------------------------------------------

    def concurrent(self, arch: Architecture) -> None:
        first = self.peek()
        comments = self.comments_before(first)
        st = Stmt(kind="process", index=len(arch.stmts), arch=arch.lentity, line=first.line,
                  comments=[c for c in comments if c.strip()])
        label = None
        if first.kind == ID and self.peek(1) is not None and self.peek(1).text == ":":
            label = first.text
            self.i += 2
        st.label = label
        t = self.peek()
        if t is None:
            raise self.fail("unterminated architecture body")
        if t.low == "entity":
            self.instance(st, arch)
        elif t.low in ("process", "postponed"):
            self.process(st, arch)
        elif t.low == "with":
            self.selected(st, arch)
        elif t.low in ("block", "for", "if", "case") or (t.low == "assert"):
            if t.low == "assert":
                st.kind, st.form = "call", "assert"
                a = self.i
                semi_i = self.skip_to_semicolon()
                st.reads = _reads(self.toks[a:semi_i], arch, set())
                st.ctrl_reads = list(st.reads)
            else:
                raise self.fail("unsupported concurrent statement %r" % t.text, t)
        elif label is not None and t.kind == ID and self.peek(1) is not None and \
                self.peek(1).low in ("port", "generic"):
            # component instantiation "label: comp port map (...)": tgt-vhdl binds
            # work.<comp>, so read it as an entity instantiation of work.<comp>.
            self.instance(st, arch, component=True)
        else:
            self.cassign(st, arch)
        st.span = (first.start, self.toks[self.i - 1].end)
        arch.stmts.append(st)

    def instance(self, st: Stmt, arch: Architecture, component: bool = False) -> None:
        st.kind = "instance"
        st.form = "instance"
        if component:
            nt = self.ident()
            st.lib, st.entity, st.ent_span = "work", nt.text, (nt.start, nt.end)
        else:
            self.expect("entity")
            lib = self.ident()
            self.expect(".")
            nt = self.ident()
            st.lib, st.entity, st.ent_span = lib.low, nt.text, (nt.start, nt.end)
            if self.at("("):
                a, b = self.balanced()
                st.arch_name = " ".join(t.text for t in self.toks[a:b + 1])
        while not self.at(";"):
            t = self.next()
            if t.low == "generic":
                self.expect("map")
                a, b = self.balanced()
                for assoc in self.assoc_list(a, b, arch):
                    st.generics[assoc.formal.lname] = assoc.text
            elif t.low == "port":
                self.expect("map")
                a, b = self.balanced()
                st.assocs = self.assoc_list(a, b, arch)
            else:
                raise self.fail("unexpected %r in instance %s" % (t.text, st.label), t)
        self.expect(";")

    def assoc_list(self, a: int, b: int, arch: Architecture) -> List[Assoc]:
        out: List[Assoc] = []
        items: List[List[Tok]] = [[]]
        depth = 0
        for t in self.toks[a:b + 1]:
            if t.text == "(":
                depth += 1
            elif t.text == ")":
                depth -= 1
            if t.text == "," and depth == 0:
                items.append([])
            else:
                items[-1].append(t)
        for it in items:
            if not it:
                continue
            arrow = next((k for k, t in enumerate(it) if t.text == "=>"), None)
            if arrow is None:
                raise self.fail("positional association in a port map", it[0])
            formal = self.ref_of(it[:arrow])
            if formal is None:
                raise self.fail("cannot read formal", it[0])
            act = it[arrow + 1:]
            text = self.text[act[0].start:act[-1].end] if act else ""
            asc = Assoc(formal=formal, text=text)
            if len(act) == 1 and act[0].low == "open":
                asc.open = True
            else:
                refs = self.concat_refs(act, arch)
                if refs is not None:
                    asc.actual = refs
                else:
                    asc.reads = _reads(act, arch, set())
                    lits = _literals(act)
                    asc.const = not asc.reads
                    asc.z_only = asc.const and lits == {_Z_LIT}
                    asc.weak_only = asc.const and bool(lits) and lits <= set(_WEAK_LITS)
                    if asc.const:
                        asc.elems = literal_elems(act)
            out.append(asc)
        return out

    def ref_of(self, toks: Sequence[Tok]) -> Optional[Ref]:
        return _ref_of(toks)

    def concat_refs(self, toks: Sequence[Tok], arch: Architecture) -> Optional[List[Ref]]:
        return _concat_refs(toks, arch)

    def process(self, st: Stmt, arch: Architecture) -> None:
        st.kind, st.form = "process", "process"
        if self.at("postponed"):
            self.next()
        self.expect("process")
        if self.at("("):
            a, b = self.balanced()
            st.sens = [t.text for t in self.toks[a:b + 1] if t.kind == ID]
        if self.at("is"):
            self.next()
        local: Set[str] = set()
        while not self.at("begin"):
            t = self.peek()
            if t is None:
                raise self.fail("unterminated process", t)
            if t.low in ("variable", "constant", "file"):
                self.next()
                local.add(self.ident().low)
                while self.at(","):
                    self.next()
                    local.add(self.ident().low)
                self.skip_to_semicolon()
            elif t.low in ("impure", "pure", "function", "procedure"):
                self.subprogram(arch)
            elif t.low in ("type", "subtype", "attribute", "alias", "use"):
                self.skip_to_semicolon()
            else:
                raise self.fail("unexpected %r in process declarations" % t.text, t)
        self.expect("begin")
        body = _Body(self, arch, local)
        n = body.statements(stop=("end",))
        self.expect("end")
        self.expect("process")
        if self.peek() is not None and self.peek().kind == ID:
            self.next()
        semi = self.expect(";")
        self.drop_comments_before(semi.end)
        st.variables = local
        st.assigns = body.assigns
        st.reads = body.reads
        st.ctrl_reads = body.ctrl_reads
        st.cond_reads = body.cond_reads
        st.var_reads = body.var_reads
        st.control = body.control
        if st.sens and [x.lower() for x in st.sens] != ["all"]:
            for x in st.sens:
                if _is_object(x.lower(), arch) and x.lower() not in local:
                    st.sens_reads.append(Ref(x))
            st.reads = st.reads + st.sens_reads
        st.simple = n == 1 and len(body.assigns) == 1 and body.control == 0

    def cassign(self, st: Stmt, arch: Architecture) -> None:
        """target <= expr [when c else expr ...] ;  or a concurrent procedure call."""
        a = self.i
        semi_i = self.skip_to_semicolon()
        toks = self.toks[a:semi_i]
        op = next((k for k, t in enumerate(toks) if t.text == "<=" and _depth0(toks, k)), None)
        if op is None:
            st.kind, st.form = "call", "call"
            st.reads = _reads(toks, arch, set())
            st.ctrl_reads = list(st.reads)
            st.control = 1
            return
        st.kind, st.form = "process", "cassign"
        asg = _assign(self, toks[:op], toks[op], toks[op + 1:], arch, set())
        st.assigns = [asg]
        st.reads = list(asg.reads)
        st.simple = True
        rhs = toks[op + 1:]
        if any(t.low == "when" and _depth0(rhs, k) for k, t in enumerate(rhs)):
            st.control = 1           # a conditional waveform: x <= a when c else b

    def selected(self, st: Stmt, arch: Architecture) -> None:
        """with sel select[?] target <= v when c, ... ;"""
        st.kind, st.form = "process", "select"
        self.expect("with")
        a = self.i
        while not self.at("select"):
            self.next()
        sel = self.toks[a:self.i]
        self.next()
        if self.at("?"):
            self.next()
        b = self.i
        semi_i = self.skip_to_semicolon()
        toks = self.toks[b:semi_i]
        op = next((k for k, t in enumerate(toks) if t.text == "<=" and _depth0(toks, k)), None)
        if op is None:
            raise self.fail("cannot read selected assignment", self.toks[b])
        asg = _assign(self, toks[:op], toks[op], toks[op + 1:], arch, set())
        asg.copy = None
        asg.parts = None
        sel_reads = _reads(sel, arch, set())
        asg.reads = sel_reads + asg.reads
        asg.const = not asg.reads
        st.assigns = [asg]
        st.reads = list(asg.reads)
        st.control = 1


def _ref_of(toks: Sequence[Tok]) -> Optional[Ref]:
    """name | name(int) | name(int downto|to int); None otherwise."""
    if not toks or toks[0].kind != ID:
        return None
    name = toks[0].text
    if len(toks) == 1:
        return Ref(name)
    if toks[1].text == ".":
        return Ref(name, None, False)            # a record field: the whole object
    if toks[1].text != "(" or toks[-1].text != ")":
        return None
    depth = 0
    end = len(toks) - 1
    for k in range(1, len(toks)):
        if toks[k].text == "(":
            depth += 1
        elif toks[k].text == ")":
            depth -= 1
            if depth == 0:
                end = k
                break
    if end != len(toks) - 1:
        if toks[end + 1].text in ("(", "."):
            return Ref(name, None, False)        # mem(i)(j downto k): an element of an array
        return None
    inner = toks[2:-1]
    depth = 0
    for k, t in enumerate(inner):
        if t.text == "(":
            depth += 1
        elif t.text == ")":
            depth -= 1
            if depth < 0:
                return None
        elif depth == 0 and t.low in ("downto", "to"):
            left, right = int_eval(inner[:k]), int_eval(inner[k + 1:])
            if left is None or right is None:
                return Ref(name, None, False)
            step = -1 if t.low == "downto" else 1
            if (right - left) * step < 0:
                return Ref(name, (), True)       # null slice
            return Ref(name, tuple(range(left, right + step, step)), True)
        elif depth == 0 and t.text in (",",):
            return Ref(name, None, False)
    v = int_eval(inner)
    if v is None:
        return Ref(name, None, False)
    return Ref(name, (v,), True)


def _concat_refs(toks: Sequence[Tok], arch: Architecture) -> Optional[List[Ref]]:
    """Plain references of signals/ports/aliases joined by '&', else None."""
    if not toks:
        return None
    parts: List[List[Tok]] = [[]]
    depth = 0
    for t in toks:
        if t.text == "(":
            depth += 1
        elif t.text == ")":
            depth -= 1
        if t.text == "&" and depth == 0:
            parts.append([])
        else:
            parts[-1].append(t)
    out = []
    for p in parts:
        r = _ref_of(p)
        if r is None or not r.exact or not _is_object(r.lname, arch):
            return None                          # a computed index is not a plain copy
        out.append(r)
    return out


def _depth0(toks: Sequence[Tok], k: int) -> bool:
    depth = 0
    for t in toks[:k]:
        if t.text in ("(", "["):
            depth += 1
        elif t.text in (")", "]"):
            depth -= 1
    return depth == 0


def _is_object(lname: str, arch: Architecture) -> bool:
    """A signal, alias or port of the architecture's entity."""
    return lname in arch.signals or lname in arch.aliases or lname in arch.ports


def _literals(toks: Sequence[Tok]) -> Set[str]:
    return {_LIT_ALIAS.get(t.low, t.low) for t in toks if t.kind == ID and t.low in _L3D_LITS}


def _value_parts(toks: Sequence[Tok], arch: Architecture,
                 local: Set[str]) -> Optional[List[object]]:
    """Assign.parts: plain references (Ref) and logic3d constants (one literal per
    element) joined by '&', left to right; None for any other expression."""
    if not toks:
        return None
    pieces: List[List[Tok]] = [[]]
    depth = 0
    for t in toks:
        if t.text == "(":
            depth += 1
        elif t.text == ")":
            depth -= 1
        if t.text == "&" and depth == 0:
            pieces.append([])
        else:
            pieces[-1].append(t)
    out: List[object] = []
    for p in pieces:
        r = _ref_of(p)
        if r is not None and r.exact and _is_object(r.lname, arch) and r.lname not in local:
            out.append(r)
            continue
        e = _const_elems(p, None)
        if e is None:
            return None
        out.extend(e)
    return out


def _reads(toks: Sequence[Tok], arch: Architecture, local: Set[str]) -> List[Ref]:
    """Every signal/port/alias referenced in `toks` (indexed names give their bits)."""
    out: List[Ref] = []
    k = 0
    n = len(toks)
    while k < n:
        t = toks[k]
        if t.text == "<<":            # VHDL-2008 external name: opaque
            while k < n and toks[k].text != ">>":
                k += 1
            k += 1
            continue
        if t.kind == ID and t.low not in local and t.low in arch.functions and \
                not _is_object(t.low, arch):
            out.extend(arch.functions[t.low])     # an impure function reads these signals
        if t.kind == ID and t.low not in local and _is_object(t.low, arch):
            prev = toks[k - 1] if k > 0 else None
            if prev is not None and prev.text in (".", "'"):
                k += 1
                continue
            if k + 1 < n and toks[k + 1].text == "(":
                depth = 0
                j = k + 1
                while j < n:
                    if toks[j].text == "(":
                        depth += 1
                    elif toks[j].text == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1
                r = _ref_of(toks[k:j + 1])
                out.append(r if r is not None else Ref(t.text, None, False))
                # the index expression may itself read signals
                out.extend(_reads(toks[k + 2:j], arch, local))
                k = j + 1
                continue
            out.append(Ref(t.text))
        k += 1
    return out


def _assign(p: "_Parser", lhs: Sequence[Tok], optok: Tok, rhs: Sequence[Tok],
            arch: Architecture, local: Set[str]) -> Assign:
    target = p.ref_of(lhs)
    if target is None:
        raise p.fail("cannot read assignment target", lhs[0] if lhs else optok)
    op = optok.text
    rest = list(rhs)
    force = None
    if rest and rest[0].low in ("force", "release"):
        force = rest[0].low
        rest = rest[1:]
        if rest and rest[0].low in ("in", "out"):
            rest = rest[1:]
    if rest and rest[0].low in ("transport", "inertial"):
        rest = rest[1:]
    if rest and rest[0].low == "reject":
        k = next((k for k, t in enumerate(rest) if t.low == "inertial"), None)
        rest = rest[k + 1:] if k is not None else rest
    # value part: up to 'after' / 'when' / ',' at depth 0
    value = rest
    for k, t in enumerate(rest):
        if t.low in ("after", "when") or t.text == ",":
            if _depth0(rest, k):
                value = rest[:k]
                break
    reads = _reads(rest, arch, local)
    # index expressions of the target are reads too
    if len(lhs) > 1:
        reads.extend(_reads(lhs[2:-1], arch, local))
    lits = _literals(rest)
    asg = Assign(target=target, op=force or op, reads=reads, line=optok.line)
    asg.const = not reads
    asg.z_only = asg.const and (lits == {_Z_LIT} or force == "release")
    asg.weak_only = asg.const and bool(lits) and lits <= set(_WEAK_LITS)
    asg.delayed = any(t.low == "after" and _depth0(rest, k) for k, t in enumerate(rest))
    if force is None and value is rest:
        cp = p.concat_refs(value, arch)
        if cp is not None and not any(r.lname in local for r in cp):
            asg.copy = cp
        asg.parts = _value_parts(value, arch, local)
    return asg


class _Body:
    """Sequential statements of a process or subprogram: assignments and reads."""

    def __init__(self, p: _Parser, arch: Architecture, local: Set[str]):
        self.p = p
        self.arch = arch
        self.local = local
        self.assigns: List[Assign] = []
        self.reads: List[Ref] = []
        self.control = 0              # if / case / loop / wait / call statements seen
        self.ctrl_reads: List[Ref] = []  # reads outside assignments
        self.cond_reads: List[Ref] = []  # reads by conditions and waits (control dependences)
        self.var_reads: List[Ref] = []   # reads by assignments to process variables

    def statements(self, stop: Tuple[str, ...]) -> int:
        """Read statements until one of the `stop` words; return how many."""
        p = self.p
        count = 0
        while True:
            t = p.peek()
            if t is None:
                raise p.fail("unterminated statement list")
            if t.low in stop:
                return count
            self.statement()
            count += 1

    def cond_until(self, *words: str) -> None:
        """Read an expression up to one of `words` at depth 0 (consumed): reads only."""
        p = self.p
        a = p.i
        depth = 0
        while True:
            t = p.next()
            if t.text == "(":
                depth += 1
            elif t.text == ")":
                depth -= 1
            elif depth == 0 and t.low in words:
                r = _reads(p.toks[a:p.i - 1], self.arch, self.local)
                self.reads.extend(r)
                self.ctrl_reads.extend(r)
                self.cond_reads.extend(r)
                return

    def statement(self) -> None:
        p = self.p
        t = p.peek()
        p.drop_comments_before(t.start)
        if t.kind == ID and p.peek(1) is not None and p.peek(1).text == ":" and \
                p.peek(2) is not None and p.peek(2).low in ("loop", "for", "while", "if", "case"):
            p.i += 2
            t = p.peek()
        w = t.low
        if w == "if":
            self.control += 1
            p.next()
            self.cond_until("then")
            while True:
                self.statements(stop=("elsif", "else", "end"))
                if p.at("elsif"):
                    p.next()
                    self.cond_until("then")
                    continue
                if p.at("else"):
                    p.next()
                    self.statements(stop=("end",))
                p.expect("end")
                p.expect("if")
                if p.peek() is not None and p.peek().kind == ID:
                    p.next()
                p.expect(";")
                return
        if w == "case":
            self.control += 1
            p.next()
            if p.at("?"):
                p.next()
            self.cond_until("is")
            while p.at("when"):
                p.next()
                self.cond_until("=>")
                self.statements(stop=("when", "end"))
            p.expect("end")
            p.expect("case")
            if p.peek() is not None and p.peek().kind == ID:
                p.next()
            p.expect(";")
            return
        if w in ("for", "while", "loop"):
            self.control += 1
            if w == "for":
                p.next()
                var = p.ident()
                self.local = set(self.local) | {var.low}
                p.expect("in")
                self.cond_until("loop")
            elif w == "while":
                p.next()
                self.cond_until("loop")
            else:
                p.next()
            self.statements(stop=("end",))
            p.expect("end")
            p.expect("loop")
            if p.peek() is not None and p.peek().kind == ID:
                p.next()
            p.expect(";")
            return
        if w in ("wait", "null", "return", "exit", "next", "report", "assert"):
            if w != "null":
                self.control += 1
            a = p.i
            semi_i = p.skip_to_semicolon()
            r = _reads(p.toks[a + 1:semi_i], self.arch, self.local)
            self.reads.extend(r)
            self.ctrl_reads.extend(r)
            if w in ("wait", "exit", "next"):
                self.cond_reads.extend(r)
            return
        # assignment or procedure call
        a = p.i
        semi_i = p.skip_to_semicolon()
        toks = p.toks[a:semi_i]
        op = next((k for k, u in enumerate(toks) if u.text in ("<=", ":=") and _depth0(toks, k)),
                  None)
        if op is None:
            self.control += 1
            r = _reads(toks, self.arch, self.local)
            self.reads.extend(r)
            self.ctrl_reads.extend(r)
            return
        asg = _assign(p, toks[:op], toks[op], toks[op + 1:], self.arch, self.local)
        self.reads.extend(asg.reads)
        if asg.target.lname in self.local or not _is_object(asg.target.lname, self.arch):
            self.var_reads.extend(asg.reads)
            return                    # a variable assignment
        self.assigns.append(asg)


def parse(path: str) -> VhdlDesign:
    """Read a design.vhd written by iverilog-sv2ghdl (NoteError on anything unexpected)."""
    try:
        with open(path, errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        raise NoteError([error(path, "cannot read design.vhd: %s" % e)])
    return parse_text(text, path)


def parse_text(text: str, path: str = "design.vhd") -> VhdlDesign:
    return _Parser(text, path).parse()


# -- sv2vhdl-modules' per-module runs (<nvc>/_mods.vhd) -----------------------------

# a "--   P = v" line of tgt-vhdl's entity comment, "--" stripped (scope.cc: every
# parameter of the scope, integer, real or string, localparams included)
_PARAM_LINE = re.compile(r"\s{3}([A-Za-z_][A-Za-z0-9_$]*) = (.*?)\s*$")
_RUN_HEAD = "-- This VHDL was converted from Verilog"      # tgt-vhdl's first line, once per run
_PROVENANCE = re.compile(r"--\s*Generated from Verilog module (\S+) \(")
_ENTITY_LINE = re.compile(r"\s*entity\s+(\w+)\s+is\b", re.I)


@dataclass
class RunEntity:
    """An entity of one tgt-vhdl run: name, Verilog module, "--   P = v" values."""
    name: str
    module: Optional[str]
    params: Dict[str, str] = field(default_factory=dict)


def translation_runs(text: str) -> List[List[RunEntity]]:
    """The tgt-vhdl runs in sv2vhdl-modules' output, each a list of its entities in order.

    A run starts at tgt-vhdl's "-- This VHDL was converted from Verilog" line
    (anything before the first, the deferred stubs, is a run of its own); an
    entity's module and values are the comment lines right above "entity X
    is".  tgt-vhdl writes the run's root, the module it was asked to
    translate, last.
    """
    runs: List[List[RunEntity]] = [[]]
    module: Optional[str] = None
    params: Dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith(_RUN_HEAD):
            if runs[-1]:
                runs.append([])
            module, params = None, {}
            continue
        s = line.lstrip()
        if s.startswith("--"):
            m = _PROVENANCE.match(s)
            if m:
                module, params = m.group(1), {}
                continue
            m = _PARAM_LINE.match(s[2:])
            if m and module is not None:
                params[m.group(1)] = m.group(2)
            continue
        m = _ENTITY_LINE.match(line)
        if m:
            runs[-1].append(RunEntity(m.group(1), module, params))
        if s:
            module, params = None, {}
    return [r for r in runs if r]


def read_translation_runs(nvc_dir: str) -> Optional[List[List[RunEntity]]]:
    """translation_runs() of <nvc_dir>/_mods.vhd, or None without one.

    Also None when iverilog-sv2ghdl recorded IVERILOG_BACKEND=1 (in _metadata.tmp,
    where it also records SV2VHDL_MODULES=1 for its stage 1a): design.vhd then
    came from its whole-design run, and _mods.vhd is what a failed stage 1a
    left behind.
    """
    meta = ""
    for name in ("_metadata", "_metadata.tmp"):
        try:
            with open(os.path.join(nvc_dir, name), errors="replace") as fh:
                meta += fh.read() + "\n"
        except OSError:
            pass
    if re.search(r"(?m)^IVERILOG_BACKEND=1\s*$", meta):
        return None
    try:
        with open(os.path.join(nvc_dir, "_mods.vhd"), errors="replace") as fh:
            return translation_runs(fh.read())
    except OSError:
        return None


# -- sv2vhdl library port modes (sv2vhdl_modes.py) ---------------------------------

LIBRARY_SOURCES = ("sv_gates.vhd", "sv_mos.vhd", "sv_pull.vhd", "sv_tran.vhd", "sv_tristate.vhd")


def scan_library_modes(src_dir: str) -> Dict[str, Dict[str, str]]:
    """Port modes of every entity in nvc's lib/sv2vhdl/{LIBRARY_SOURCES}.

    Returns {entity (lowercased): {port (lowercased): mode}}.  Only entity
    port clauses are read; the architectures (which use 'driver/'other) are
    skipped.
    """
    out: Dict[str, Dict[str, str]] = {}
    for fn in LIBRARY_SOURCES:
        path = os.path.join(src_dir, fn)
        with open(path, errors="replace") as fh:
            text = fh.read()
        p = _Parser(text, path)
        toks = p.toks
        k = 0
        while k < len(toks):
            t = toks[k]
            if t.low == "entity" and k + 2 < len(toks) and toks[k + 1].kind == ID and \
                    toks[k + 2].low == "is":
                name = toks[k + 1].low
                p.i = k + 3
                ports: Dict[str, str] = {}
                while not p.at("end"):
                    if p.at("port"):
                        p.next()
                        a, b = p.balanced()
                        for q in p.port_list(a, b):
                            ports[q.lname] = q.mode
                    elif p.at("generic"):
                        p.next()
                        p.balanced()
                    else:
                        p.next()
                out[name] = ports
                k = p.i
            k += 1
    return out


def render_modes_module(src_dir: str) -> str:
    """The text of vamos/ams/sv2vhdl_modes.py, generated from the nvc sources."""
    modes = scan_library_modes(src_dir)
    lines = [
        '"""Port modes of the sv2vhdl library entities (generated: do not edit).',
        "",
        "tgt-vhdl instantiates these as sv2vhdl.sv_* (gates, MOS switches, pulls,",
        "tran switches, three-state buffers); their declarations are not in",
        "design.vhd.  Generated by vamos.ams.vhdl.render_modes_module() from nvc's",
        "lib/sv2vhdl/{%s};" % ",".join(LIBRARY_SOURCES),
        "tests/vamos/test_ams_vhdl.py regenerates it and compares.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Dict",
        "",
        "SOURCES = (%s)" % ", ".join('"%s"' % s for s in LIBRARY_SOURCES),
        "",
        "# entity -> port -> mode (in | out | inout)",
        "MODES: Dict[str, Dict[str, str]] = {",
    ]
    for ent in sorted(modes):
        items = ", ".join('"%s": "%s"' % (p, m) for p, m in modes[ent].items())
        lines.append('    "%s": {%s},' % (ent, items))
    lines.append("}")
    return "\n".join(lines) + "\n"
