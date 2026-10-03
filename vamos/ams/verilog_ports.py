"""The Verilog side of a vcs-ams compile (docs/VAMOS_AMS_DESIGN.md §1.2-§1.4, §5.1).

preprocess() runs `iverilog -E -g2012` once over the whole job (the timescale
prelude, the sources, the -v files, with the job's -D/-I, in the job's cwd)
and writes the stream out (ams/pp.orig.v; pp/pp.v in plain vcs mode).  The
time-unit rules, the API scan, the root scan, the header parser, the
declaration scan and (in shells.py) masking all work on that one stream, so a
macro defined inside a masked module, a cell reached through `include and a
cell in a -v file behave as in the user's own compile.

Origins.  iverilog -E emits no `line directives, and its output does not keep
each file's line numbers (an `include inserts the included lines, a
multi-line macro body adds lines).  The driver starts its preprocessor as
<dir>/ivlpp and -BP<dir> names that directory, so preprocess() points -BP at
a one-line wrapper that adds ivlpp's own -L flag.  The stream then carries
`line directives: they are stripped from the text written out (which differs
from plain -E output only in blank lines) and kept as a per-line origin map,
so every diagnostic names the user's file:line (PP.origin).  When the wrapper
cannot be set up (no ivlpp path in `iverilog -v -E`), origins degrade to
pp.orig.v lines, with a note.  pp.orig.v itself never holds `line
directives: tgt-vhdl's "-- Declared at <file>:<line>" comments must keep
pointing into nvc/_pp.v, whose lines are pp.v's (and pp.orig.v's).

Time units (§1.2).  -override_timescale=u/p rewrites every `timescale
directive and every timeunit/timeprecision declaration to u/p (line count
kept; a `resetall on a line of its own gets "`timescale u/p" after it) and
the prelude carries `timescale u/p; -timescale=u/p is the prelude only.  Each
module's unit and precision are then computed as IEEE 1800 §3.14.2.3 says:
its own timeunit/timeprecision, else the last `timescale (a `resetall clears
it), else a compilation-unit timeunit/timeprecision.  In AMS mode a module
with no unit or precision, or with a precision coarser than 1 ms, is an error
naming it (tgt-vhdl would compress that time base, and sv2vhdl-modules
translates each module on its own); a module only in a -v file counts only if
something instantiates it.  PP.precision is the finest precision in effect
(Job.precision).

The rest is text processing on the preprocessed stream: a comment-, string-
and attribute-aware lexer; a design-unit scanner (module/macromodule,
lifetimes, leading attributes, `endmodule : label`, other units skipped); an
instantiation scanner; an ANSI/non-ANSI header parser; a declaration scan
(variables and tri0/tri1-style nets, for the cut).  Checks return notes;
anything that cannot produce its result raises NoteError.  PP.notes can hold
errors (the time rules): the caller stops on vamos.notes.has_errors(pp.notes).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from vamos import tools
from vamos.ams import globs
from vamos.ams.model import LOGIC, REAL
from vamos.job import Job
from vamos.notes import ERROR, WARNING, Note, NoteError, error, note, warning

# =============================================================================
# Lexer
# =============================================================================

_LEX = re.compile(
    r"(?P<ws>\s+)"
    r"|(?P<cmt>//[^\n]*|/\*.*?\*/)"
    r"|(?P<attr>\(\*(?!\s*\)).*?\*\))"
    r'|(?P<str>"(?:\\.|[^"\\\n])*")'
    r"|(?P<dir>`[A-Za-z_][A-Za-z0-9_$]*)"
    r"|(?P<sys>\$[A-Za-z0-9_$]+)"
    r"|(?P<esc>\\\S+)"
    r"|(?P<num>(?:[0-9][0-9_]*[ \t]*)?'[sS]?[bBoOdDhH][ \t]*[0-9a-fA-FxXzZ?_]+"
    r"|[0-9][0-9_]*(?:\.[0-9][0-9_]*)?(?:[eE][+-]?[0-9][0-9_]*)?"
    r"|'[01xXzZ](?![A-Za-z0-9_$]))"
    r"|(?P<id>[A-Za-z_][A-Za-z0-9_$]*)"
    r"|(?P<op>::|\*\*|<<<|>>>|<<|>>|<=|>=|===|!==|==|!=|&&|\|\||->|\+:|-:|\.\*|##|.)",
    re.S)

# Directives whose arguments run to the end of the line; the others (`resetall,
# `celldefine, ...) are single words.  After -E no `define/`ifdef/`include remain.
_LINE_DIRECTIVES = {"timescale", "default_nettype", "pragma", "line", "begin_keywords",
                    "unconnected_drive", "default_decay_time", "default_trireg_strength"}


class Tok:
    """One token: kind id | esc | sys | num | str | dir | op | attr, its text and offsets."""
    __slots__ = ("kind", "text", "start", "end")

    def __init__(self, kind: str, text: str, start: int, end: int):
        self.kind, self.text, self.start, self.end = kind, text, start, end

    def __repr__(self) -> str:
        return "Tok(%s %r @%d)" % (self.kind, self.text, self.start)

    @property
    def name(self) -> str:
        """Identifier text; an escaped identifier without its backslash."""
        return self.text[1:] if self.kind == "esc" else self.text


def tokenize(text: str) -> List[Tok]:
    """Tokens of preprocessed Verilog; comments and white space are dropped,
    attributes (* ... *) are kept as one 'attr' token ("@(*)" is not one)."""
    out: List[Tok] = []
    pos, n = 0, len(text)
    match = _LEX.match
    while pos < n:
        m = match(text, pos)
        kind = m.lastgroup
        end = m.end()
        if kind == "ws" or kind == "cmt":
            pos = end
            continue
        if kind == "dir" and m.group(kind)[1:] in _LINE_DIRECTIVES:
            nl = text.find("\n", end)
            end = n if nl < 0 else nl
        out.append(Tok(kind, text[pos:end], pos, end))
        pos = end
    return out


KEYWORDS = frozenset("""
accept_on alias always always_comb always_ff always_latch and assert assign assume automatic before
begin bind bins binsof bit break buf bufif0 bufif1 byte case casex casez cell chandle checker class
clocking cmos config const constraint context continue cover covergroup coverpoint cross deassign
default defparam design disable dist do edge else end endcase endchecker endclass endclocking
endconfig endfunction endgenerate endgroup endinterface endmodule endpackage endprimitive endprogram
endproperty endspecify endsequence endtable endtask enum event eventually expect export extends
extern final first_match for force foreach forever fork forkjoin function generate genvar global
highz0 highz1 if iff ifnone ignore_bins illegal_bins implements implies import incdir include
initial inout input inside instance int integer interconnect interface intersect join join_any
join_none large let liblist library local localparam logic longint macromodule matches medium modport
module nand negedge nettype new nexttime nmos nor noshowcancelled not notif0 notif1 null or output
package packed parameter pmos posedge primitive priority program property protected pull0 pull1
pulldown pullup pulsestyle_ondetect pulsestyle_onevent pure rand randc randcase randsequence rcmos
real realtime ref reg reject_on release repeat restrict return rnmos rpmos rtran rtranif0 rtranif1
s_always s_eventually s_nexttime s_until s_until_with scalared sequence shortint shortreal
showcancelled signed small soft solve specify specparam static string strong strong0 strong1 struct
super supply0 supply1 sync_accept_on sync_reject_on table tagged task this throughout time
timeprecision timeunit tran tranif0 tranif1 tri tri0 tri1 triand trior trireg type typedef union
unique unique0 unsigned until until_with untyped use uwire var vectored virtual void wait wait_order
wand weak weak0 weak1 while wildcard wire with within wor xnor xor wreal
""".split())

DIRECTIONS = ("input", "output", "inout", "ref")
NET_TYPES = frozenset(("wire", "tri", "tri0", "tri1", "wand", "wor", "triand", "trior", "trireg",
                       "supply0", "supply1", "uwire", "interconnect"))
VAR_TYPES = frozenset(("reg", "logic", "bit", "byte", "shortint", "int", "longint", "integer", "time"))
REAL_TYPES = frozenset(("real", "realtime", "shortreal", "wreal"))
DATA_TYPES = VAR_TYPES | REAL_TYPES | frozenset(("string", "chandle", "event"))
TRI_KINDS = ("tri0", "tri1", "trireg", "wand", "wor", "triand", "trior")
_OPEN = "([{"
_CLOSE = ")]}"


def _is_ident(t: Tok) -> bool:
    return t.kind == "esc" or (t.kind == "id" and t.text not in KEYWORDS)


def _match(toks: Sequence[Tok], i: int) -> int:
    """Index of the bracket closing toks[i] (any of ( [ {), or len(toks) - 1."""
    depth = 0
    for j in range(i, len(toks)):
        t = toks[j]
        if t.kind != "op":
            continue
        if t.text in _OPEN:
            depth += 1
        elif t.text in _CLOSE:
            depth -= 1
            if depth == 0:
                return j
    return len(toks) - 1


def _split(toks: Sequence[Tok], a: int, b: int, sep: str = ",") -> List[Tuple[int, int]]:
    """[a, b) split at depth-0 separators."""
    out, depth, s = [], 0, a
    for j in range(a, b):
        t = toks[j]
        if t.kind != "op":
            continue
        if t.text in _OPEN:
            depth += 1
        elif t.text in _CLOSE:
            depth -= 1
        elif t.text == sep and depth == 0:
            out.append((s, j))
            s = j + 1
    out.append((s, b))
    return out


def _find(toks: Sequence[Tok], a: int, b: int, text: str) -> int:
    """First depth-0 op token `text` in [a, b), or -1."""
    depth = 0
    for j in range(a, b):
        t = toks[j]
        if t.kind != "op":
            continue
        if t.text == text and depth == 0:
            return j
        if t.text in _OPEN:
            depth += 1
        elif t.text in _CLOSE:
            depth -= 1
    return -1


def line_starts(text: str) -> List[int]:
    out = [0]
    i = text.find("\n")
    while i >= 0:
        out.append(i + 1)
        i = text.find("\n", i + 1)
    return out


def blank(text: str, spans: Iterable[Tuple[int, int]]) -> str:
    """text with every [a, b) span replaced by spaces, newlines kept (line numbers unchanged)."""
    parts, last = [], 0
    for a, b in sorted(spans):
        a = max(a, last)
        if b <= a:
            continue
        parts.append(text[last:a])
        parts.append(re.sub(r"[^\n]", " ", text[a:b]))
        last = b
    parts.append(text[last:])
    return "".join(parts)


# =============================================================================
# Time units
# =============================================================================

_UNITS = {"s": 0, "ms": -3, "us": -6, "ns": -9, "ps": -12, "fs": -15}
_TIME = re.compile(r"^\s*(1|10|100)(?:\.0*)?\s*(s|ms|us|ns|ps|fs)\s*$")
_TS_ARGS = re.compile(r"^\s*(\S.*?)\s*/\s*(\S.*?)\s*$")
MAX_PRECISION = -3          # 1 ms: tgt-vhdl compresses a coarser time base (§1.2)


def time_exp(text: str) -> Optional[int]:
    """Power of ten (in seconds) of a time literal "1ns", "100 ps"; None if it is not one."""
    m = _TIME.match(text or "")
    if not m:
        return None
    return _UNITS[m.group(2)] + len(m.group(1)) - 1


def time_text(exp: int) -> str:
    """"1ps", "10ns", "100us", ... for a power of ten."""
    u = max(-15, min(0, 3 * (exp // 3)))
    name = [k for k, v in _UNITS.items() if v == u][0]
    return "%d%s" % (10 ** (exp - u), name)


def time_seconds(text: str) -> float:
    """Seconds of a time literal ("1ps" -> 1e-12); ValueError if it is not one."""
    e = time_exp(text)
    if e is None:
        raise ValueError("not a time literal: %r" % (text,))
    return float("1e%d" % e)


def parse_timescale(text: str) -> Tuple[int, int]:
    """"1ns/1ps" -> (-9, -12); ValueError naming the problem."""
    m = _TS_ARGS.match(text or "")
    if not m:
        raise ValueError("expected <unit>/<precision>, got %r" % (text,))
    u, p = time_exp(m.group(1)), time_exp(m.group(2))
    if u is None or p is None:
        raise ValueError("bad time value in %r (use 1, 10 or 100 with s, ms, us, ns, ps or fs)" % (text,))
    if p > u:
        raise ValueError("precision %s is coarser than the unit %s in %r" % (time_text(p), time_text(u), text))
    return u, p


# =============================================================================
# The preprocessed stream
# =============================================================================

@dataclass
class ModuleDef:
    """One module definition in the preprocessed stream."""
    name: str
    keyword: str                 # module | macromodule
    start: int                   # offset of its first leading attribute, else of the keyword
    end: int                     # offset just past endmodule (and an ": label")
    line: int                    # pp line of the keyword
    end_line: int
    file: str                    # command-line file the text came from (absolute; "" if unknown)
    lib: bool                    # that file is a -v library file
    origin: str                  # "file:line" of the keyword in the user's sources
    unit: Optional[str] = None   # effective time unit ("1ns"), None if none
    precision: Optional[str] = None
    tok_kw: int = 0              # token indices: the keyword, the name, the header ';', endmodule
    tok_name: int = 0
    tok_hdr: int = 0
    tok_end: int = 0


@dataclass
class Decl:
    """A variable (reg/logic/bit/...) or special-net (tri0/tri1/...) declaration."""
    module: str                  # enclosing module definition
    name: str
    kind: str                    # reg logic bit integer int ... | tri0 tri1 trireg wand wor triand trior
    line: int                    # pp line of the name: pp.orig.v == pp.v == nvc/_pp.v
    origin: str                  # the user's file:line
    range_text: Optional[str] = None
    port: bool = False           # a port declaration (output reg y / input tri1 a)
    block: str = ""              # "" at module level, else the enclosing begin-block labels ("g", "g.b"; "-" unnamed)


@dataclass
class Conn:
    """One connection of an instantiation, as written."""
    port: Optional[str]          # the named port (".*" -> "*"); None for a positional connection
    expr: Optional[str]          # the actual's text; "" for .p() or an empty positional slot; None for .p and .*


@dataclass
class Inst:
    """A module instantiation found by the structural scan (not elaborated)."""
    module: str                  # instantiated module name, as spelled
    name: str                    # instance name
    scope: str                   # enclosing module definition ("" outside any)
    start: int                   # offset of the module-name token
    line: int                    # pp line of the instance name
    params: Optional[str] = None # "#(...)" or "#5" text; None without parameter overrides
    named: List[str] = field(default_factory=list)   # named connections in order (".*" -> "*")
    positional: int = 0          # number of positional connections
    array: Optional[str] = None  # instance-array range text
    end: int = 0                 # offset just past the connection list's ')'
    conns: List[Conn] = field(default_factory=list)  # every connection in order

    def actual(self, port: str, index: int) -> str:
        """The actual connected to `port`, the index-th port of the module: the expression
        text of `.port(e)` or of the index-th positional connection; the port's own name for
        `.port` and `.*`; "" when the port is left unconnected."""
        star = False
        for c in self.conns:
            if c.port == port:
                return port if c.expr is None else c.expr.strip()
            star = star or c.port == "*"
        if star:
            return port
        pos = [c for c in self.conns if c.port is None]
        return pos[index].expr.strip() if 0 <= index < len(pos) and pos[index].expr else ""


@dataclass
class PP:
    """The preprocessed sources (§5.8): text, module index, time base, declarations, notes."""
    path: str
    text: str
    modules: Dict[str, List[ModuleDef]] = field(default_factory=dict)
    precision: Optional[str] = None
    tri_nets: List[Decl] = field(default_factory=list)
    variables: List[Decl] = field(default_factory=list)
    notes: List[Note] = field(default_factory=list)
    cwd: str = ""
    files: List[str] = field(default_factory=list)       # command-line files in stream order (absolute)
    lib_files: List[str] = field(default_factory=list)   # the -v ones among them
    lib_dirs: List[str] = field(default_factory=list)    # -y directories (not searched; for messages)
    origins: List[Tuple[int, int]] = field(default_factory=list)   # per pp line: (origin_files index, line)
    origin_files: List[str] = field(default_factory=list)          # display names
    cmdfile: List[int] = field(default_factory=list)     # per pp line: index into files, -1 unknown
    case_sensitive: bool = False                         # set_sim_case sensitive (control-file globs)
    library_masked: bool = False                         # apply_library_rule blanked a -v copy
    _toks: Optional[List[Tok]] = field(default=None, repr=False)
    _insts: Optional[List[Inst]] = field(default=None, repr=False)
    _lines: Optional[List[int]] = field(default=None, repr=False)
    _units: Optional[List[Tuple[str, int, int]]] = field(default=None, repr=False)
    _headers: Dict[str, "Header"] = field(default_factory=dict, repr=False)

    def toks(self) -> List[Tok]:
        if self._toks is None:
            self._toks = tokenize(self.text)
        return self._toks

    def reset(self, text: str) -> None:
        """Replace the text (same line structure) and drop every cache."""
        self.text = text
        self._toks = self._insts = self._lines = self._units = None
        self._headers = {}

    def line_of(self, offset: int) -> int:
        if self._lines is None:
            self._lines = line_starts(self.text)
        return bisect_right(self._lines, offset)

    def origin(self, line: int) -> str:
        """The user's "file:line" for a line of pp.orig.v (or of the masked part of pp.v)."""
        if 1 <= line <= len(self.origins):
            fi, ln = self.origins[line - 1]
            if fi >= 0:
                return "%s:%d" % (self.origin_files[fi], ln)
        return "%s:%d" % (os.path.basename(self.path) or "pp.v", line)

    def origin_at(self, offset: int) -> str:
        return self.origin(self.line_of(offset))

    def file_of_line(self, line: int) -> str:
        if 1 <= line <= len(self.cmdfile) and self.cmdfile[line - 1] >= 0:
            return self.files[self.cmdfile[line - 1]]
        return ""

    def definitions(self) -> List[ModuleDef]:
        """Every module definition, in stream order."""
        return sorted((d for ds in self.modules.values() for d in ds), key=lambda d: d.start)

    def module_at(self, offset: int) -> Optional[ModuleDef]:
        for d in self.definitions():
            if d.start <= offset < d.end:
                return d
        return None

    def decls(self, module: str, name: str) -> List[Decl]:
        return [d for d in self.variables + self.tri_nets if d.module == module and d.name == name]

    def is_variable(self, module: str, name: str) -> bool:
        """name is declared reg/logic/bit/... at the top level of module (not in a begin block)."""
        return any(d.module == module and d.name == name and not d.block for d in self.variables)

    def tri_kind(self, module: str, name: str) -> Optional[str]:
        for d in self.tri_nets:
            if d.module == module and d.name == name and not d.block:
                return d.kind
        return None

    def decl_at(self, line: int, name: str) -> Optional[Decl]:
        """The declaration of `name` on pp line `line`: what a tgt-vhdl "-- Declared at
        nvc/_pp.v:<line>" (or _norm.sv, when sv-normalize kept the lines) comment names."""
        for d in self.variables + self.tri_nets:
            if d.line == line and d.name == name:
                return d
        return None


# =============================================================================
# iverilog runs and diagnostics
# =============================================================================

@dataclass
class Diag:
    """One located iverilog message with its continuation lines."""
    file: str
    line: int
    kind: str                    # error | warning | sorry | "" (e.g. "Include file x not found")
    message: str
    extra: List[str] = field(default_factory=list)   # e.g. "Port 1 (a) of inv_sp is connected to r1"


_DIAG = re.compile(r"^(?P<file>[^:\s][^:]*?):(?P<line>\d+):\s*(?P<rest>.*)$")
_SUMMARY = re.compile(r"^(\d+ error\(s\) during elaboration\.|\*\*\*.*|\s+\S+ referenced \d+ times\.|"
                      r"Elaboration failed|errors preprocessing Verilog program\.|I give up\.|"
                      r"\d+ error\(s\) in post-elaboration processing\.|)$")


def parse_diags(output: str) -> Tuple[List[Diag], List[str]]:
    """iverilog output -> (located messages, other lines); elaboration summaries are dropped."""
    diags: List[Diag] = []
    other: List[str] = []
    for raw in output.splitlines():
        line = raw.rstrip()
        m = _DIAG.match(line)
        if m:
            rest = m.group("rest")
            if rest.startswith(":") and diags:
                diags[-1].extra.append(rest[1:].strip())
                continue
            kind = ""
            for k in ("error", "warning", "sorry"):
                if rest.startswith(k + ":"):
                    kind, rest = k, rest[len(k) + 1:].strip()
                    break
            if not kind and rest.startswith("syntax error"):
                kind = "error"
            diags.append(Diag(m.group("file"), int(m.group("line")), kind, rest))
            continue
        if _SUMMARY.match(line):
            continue
        other.append(line)
    return diags, other


def iverilog_path() -> str:
    iv = tools.find_real("iverilog")
    if not iv:
        raise NoteError([error("", "cannot find iverilog (set VAMOS_IVERILOG, or put iverilog on PATH)")])
    return iv


def run_iverilog(args: Sequence[str], cwd: str, iv: Optional[str] = None,
                 timeout: Optional[float] = None) -> Tuple[int, str]:
    """Run iverilog; (exit status, merged output).  NoteError if it cannot run."""
    cmd = [iv or iverilog_path()] + list(args)
    if os.environ.get("VAMOS_VERBOSE"):
        sys.stderr.write("vamos: + %s\n" % " ".join(cmd))
    if timeout is None:
        timeout = float(os.environ.get("VAMOS_IVERILOG_TIMEOUT", "3600"))
    try:
        r = subprocess.run(cmd, cwd=cwd or None, env=tools.child_env(), stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, errors="replace",
                           timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        raise NoteError([error("", "cannot run %s: %s" % (cmd[0], e))])
    return r.returncode, r.stdout or ""


_ivlpp_cache: Dict[str, Optional[str]] = {}


def _real_ivlpp(iv: str) -> Optional[str]:
    """The ivlpp the driver runs: `iverilog -v -E` prints "preprocess: <ivlpp> -v -F...". Cached."""
    if iv in _ivlpp_cache:
        return _ivlpp_cache[iv]
    found = None
    d = tempfile.mkdtemp(prefix="vamos-ivlpp-")
    try:
        src = os.path.join(d, "empty.v")
        with open(src, "w") as fh:
            fh.write("\n")
        _, out = run_iverilog(["-v", "-E", "-o", os.devnull, src], cwd=d, iv=iv, timeout=120)
        for line in out.splitlines():
            if line.startswith("preprocess: "):
                words = line[len("preprocess: "):].split()
                if words and os.path.isfile(words[0]) and os.access(words[0], os.X_OK):
                    found = words[0]
                break
    except NoteError:
        found = None
    finally:
        shutil.rmtree(d, ignore_errors=True)
    _ivlpp_cache[iv] = found
    return found


def iverilog_defines(job: Job) -> List[str]:
    """-I and -D arguments for the job (as NvcBackend passes them to iverilog-sv2ghdl)."""
    out = ["-I" + d for d in job.incdirs]
    for k, v in job.defines.items():
        out.append("-D%s=%s" % (k, v) if v is not None else "-D" + k)
    return out


def job_path(job: Job, path: str) -> str:
    """An absolute, normalised path; a relative one is taken against the job's cwd."""
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(job.cwd or os.getcwd(), path))


def display_path(path: str, cwd: str) -> str:
    """A path for messages: relative to cwd when it lies inside it."""
    if cwd and os.path.isabs(path):
        try:
            rel = os.path.relpath(path, cwd)
        except ValueError:
            return path
        if not rel.startswith(".."):
            return rel
    return path


# =============================================================================
# preprocess (§1.2, §5.8)
# =============================================================================

_LINE_DIR = re.compile(r'^`line\s+(\d+)\s+"((?:\\.|[^"\\])*)"\s+(\d+)\s*$')


def strip_line_directives(raw: str, files: Sequence[str], cwd: str = ""
                          ) -> Tuple[str, List[Tuple[int, int]], List[str], List[int]]:
    """ivlpp -L output -> (text, origins, origin_files, cmdfile).

    origins[k] = (index into origin_files, line) of pp line k+1.  cmdfile[k] =
    index into `files` (the command-line files, in order) of the file the line
    came from, an `include resolved to its includer (-1 before the first).
    ivlpp marks a new command-line file with level 0, entering and leaving an
    include with 1 and 2.
    """
    norm = [os.path.normpath(f) for f in files]
    lines: List[str] = []
    origins: List[Tuple[int, int]] = []
    cmdfile: List[int] = []
    names: List[str] = []
    index: Dict[str, int] = {}
    cur, ln, cmd = -1, 1, -1
    for line in raw.split("\n"):
        m = _LINE_DIR.match(line) if line.startswith("`line") else None
        if m:
            fname = m.group(2).replace('\\"', '"')
            full = os.path.normpath(fname if os.path.isabs(fname) else os.path.join(cwd, fname))
            if full not in index:
                index[full] = len(names)
                names.append(display_path(full, cwd))
            cur, ln = index[full], int(m.group(1))
            if m.group(3) == "0" and cmd + 1 < len(norm) and norm[cmd + 1] == full:
                cmd += 1
            continue
        lines.append(line)
        origins.append((cur, ln))
        cmdfile.append(cmd)
        ln += 1
    return "\n".join(lines), origins, names, cmdfile


def preprocess(job: Job, out_path: str, override_timescale: Optional[str] = None,
               ams: bool = True) -> PP:
    """Preprocess the job's Verilog into out_path and index it (§1.2, §5.8).

    out_path: layout.pp_orig(daidir) in AMS mode; layout.plain_pp(daidir) for a
    plain vcs compile with -override_timescale.  The timescale prelude, when
    there is one, is written next to it (vamos_prelude.v).  ams: apply the AMS
    time rules (errors in PP.notes) and make an undefined macro an error (a
    warning otherwise).  Raises NoteError when iverilog -E fails or an option
    value is malformed.
    """
    notes: List[Note] = []
    over: Optional[Tuple[int, int]] = None
    ts_text: Optional[str] = None
    if override_timescale is not None:
        try:
            over = parse_timescale(override_timescale)
        except ValueError as e:
            raise NoteError([error("-override_timescale", str(e))])
        ts_text = "%s/%s" % (time_text(over[0]), time_text(over[1]))
        if job.timescale and job.timescale.replace(" ", "") != override_timescale.replace(" ", ""):
            notes.append(note("-timescale", "-timescale=%s is superseded by -override_timescale=%s"
                              % (job.timescale, override_timescale)))
    elif job.timescale:
        try:
            parse_timescale(job.timescale)
        except ValueError as e:
            raise NoteError([error("-timescale", str(e))])
        ts_text = job.timescale.strip()

    bad = [s for s in job.sources if s.lang not in ("verilog", "sv")]
    if bad:
        raise NoteError([error(s.path, "a %s source cannot be compiled in this flow" % s.lang) for s in bad])
    if not job.sources:
        raise NoteError([error("", "no Verilog source files given")])

    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)
    cwd = job.cwd or os.getcwd()
    files: List[str] = []
    if ts_text:
        prelude = os.path.join(out_dir, "vamos_prelude.v")
        with open(prelude, "w") as fh:
            fh.write("`timescale %s\n" % ts_text)
        files.append(prelude)
    files += [job_path(job, s.path) for s in job.sources]
    libs = [job_path(job, p) for p in job.lib_files]
    files += libs

    rc, output, plain, mapped = _run_E(iverilog_path(), job, files, cwd)
    diags, other = parse_diags(output)
    for d in diags:
        path = d.file if os.path.isabs(d.file) else os.path.join(cwd, d.file)
        where = "%s:%d" % (display_path(os.path.normpath(path), cwd), d.line)
        msg = d.message + "".join("; " + x for x in d.extra)
        if "undefined (and assumed null)" in d.message:
            notes.append(Note(ERROR if ams else WARNING, where, msg))
        elif d.kind == "warning":
            notes.append(warning(where, msg))
        else:
            notes.append(error(where, msg))
    if rc != 0:
        errs = [n for n in notes if n.severity == ERROR]
        raise NoteError(errs or [error("", "iverilog -E failed (exit %d)%s"
                                       % (rc, (": " + " | ".join(other[-5:])) if other else ""))])

    if mapped is None:
        text, origins, names, cmdfile = plain, [], [], []
        notes.append(note("", "iverilog gave no `line directives: messages name %s lines"
                          % os.path.basename(out_path)))
    else:
        text, origins, names, cmdfile = mapped
    if text and not text.endswith("\n"):
        text += "\n"

    pp = PP(path=os.path.abspath(out_path), text=text, cwd=cwd, files=files, lib_files=libs,
            lib_dirs=list(job.lib_dirs), origins=origins, origin_files=names, cmdfile=cmdfile)
    pp.notes = notes
    if over is not None:
        new, more = _override(pp, over)
        pp.reset(new)
        pp.notes += more
    _index(pp)
    pp.notes += _time_rules(pp, ams, override_timescale)
    with open(out_path, "w") as fh:
        fh.write(pp.text)
    return pp


def library_rule(pp: PP) -> Tuple[str, List[str]]:
    """VCS's -v rule on the preprocessed stream: a module defined in a -v library file is
    used only when no source file defines it, and among -v files the first that defines it
    wins.  Returns pp's text with every other library copy blanked (line numbers kept) and
    the names of the modules that had one.  Two definitions in source files are left for
    iverilog to report."""
    spans: List[Tuple[int, int]] = []
    names: List[str] = []
    for name, defs in pp.modules.items():
        libs = [d for d in defs if d.lib]
        if not libs or len(defs) < 2:
            continue
        keep = [d for d in defs if not d.lib] or sorted(libs, key=lambda d: d.start)[:1]
        drop = [d for d in libs if d not in keep]
        if drop:
            names.append(name)
            spans += [(d.start, d.end) for d in drop]
    return (blank(pp.text, spans) if spans else pp.text), sorted(names)


def apply_library_rule(pp: PP) -> List[str]:
    """library_rule applied to pp in place (AMS mode, §1.2): the text blanked, the module
    index rebuilt without the dropped copies and pp.path rewritten, so the top scan, the
    shells, the translation and precheck(pp=) all see the copy VCS would use.  Returns
    the names of the modules that had a library copy dropped ([]: pp is unchanged)."""
    text, names = library_rule(pp)
    if names:
        notes = list(pp.notes)          # _index notes them again for a malformed module
        pp.reset(text)
        _index(pp)
        pp.notes = notes
        pp.library_masked = True
        with open(pp.path, "w") as fh:
            fh.write(pp.text)
    return names


def from_text(text: str, path: str = "pp.orig.v", ams: bool = False,
              lib_lines: Sequence[Tuple[int, int]] = ()) -> PP:
    """A PP over already preprocessed text, without running iverilog: indexed, with the
    time rules applied (their notes in PP.notes).  lib_lines: (first, last) pp line
    ranges that came from a -v file.  Origins are pp lines."""
    if text and not text.endswith("\n"):
        text += "\n"
    pp = PP(path=path, text=text)
    if lib_lines:
        nlines = text.count("\n") + 1
        pp.files = ["<sources>", "<-v>"]
        pp.lib_files = ["<-v>"]
        pp.cmdfile = [0] * nlines
        for a, b in lib_lines:
            for k in range(max(a, 1), min(b, nlines) + 1):
                pp.cmdfile[k - 1] = 1
    _index(pp)
    pp.notes += _time_rules(pp, ams, None)
    return pp


def _run_E(iv: str, job: Job, files: List[str], cwd: str):
    """(rc, output, plain text, mapped): mapped is strip_line_directives() of an -L
    run, or None when the -L wrapper is unavailable (plain text is then -E's)."""
    base = ["-E", "-g2012"] + iverilog_defines(job)
    ivlpp = _real_ivlpp(iv)
    wrap = tempfile.mkdtemp(prefix="vamos-ivlpp-")
    try:
        out = os.path.join(wrap, "pp.v")
        if ivlpp and not re.search(r"[\s'\"\\$`]", wrap):
            script = os.path.join(wrap, "ivlpp")
            with open(script, "w") as fh:
                fh.write("#!/bin/sh\nexec '%s' -L \"$@\"\n" % ivlpp.replace("'", "'\\''"))
            os.chmod(script, 0o755)
            rc, output = run_iverilog(base + ["-BP" + wrap, "-o", out] + files, cwd=cwd, iv=iv)
            if rc != 0:
                return rc, output, "", None
            with open(out, errors="replace") as fh:
                raw = fh.read()
            if raw.startswith("`line "):
                return rc, output, "", strip_line_directives(raw, files, cwd)
        rc, output = run_iverilog(base + ["-o", out] + files, cwd=cwd, iv=iv)
        text = ""
        if rc == 0:
            with open(out, errors="replace") as fh:
                text = fh.read()
        return rc, output, text, None
    finally:
        shutil.rmtree(wrap, ignore_errors=True)


def _override(pp: PP, over: Tuple[int, int]) -> Tuple[str, List[Note]]:
    """-override_timescale: rewrite every `timescale, timeunit and timeprecision (lines kept)."""
    u, p = time_text(over[0]), time_text(over[1])
    toks = pp.toks()
    text = pp.text
    edits: List[Tuple[int, int, str]] = []
    notes: List[Note] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.kind == "dir" and t.text.startswith("`timescale"):
            edits.append((t.start, t.end, "`timescale %s/%s" % (u, p)))
        elif t.kind == "dir" and t.text == "`resetall":
            eol = text.find("\n", t.end)
            rest = text[t.end:eol if eol >= 0 else len(text)]
            if re.match(r"^\s*(//.*)?$", rest):
                edits.append((t.start, t.end, "`resetall `timescale %s/%s" % (u, p)))
            else:
                notes.append(warning(pp.origin_at(t.start), "code follows `resetall on its line: "
                                     "-override_timescale cannot be applied after it"))
        elif t.kind == "id" and t.text in ("timeunit", "timeprecision"):
            j = _find(toks, i, len(toks), ";")
            if j > i:
                old = text[t.start:toks[j].end]
                if t.text == "timeprecision":
                    new = "timeprecision %s;" % p
                elif _find(toks, i, j, "/") > 0:
                    new = "timeunit %s/%s;" % (u, p)
                else:
                    new = "timeunit %s;" % u
                edits.append((t.start, toks[j].end, new + "\n" * old.count("\n")))
                i = j
        i += 1
    parts, last = [], 0
    for a, b, s in edits:
        parts.append(text[last:a])
        parts.append(s)
        last = b
    parts.append(text[last:])
    return "".join(parts), notes


# =============================================================================
# Design units, module definitions, declarations
# =============================================================================

_UNIT_END = {"module": "endmodule", "macromodule": "endmodule", "interface": "endinterface",
             "program": "endprogram", "package": "endpackage", "class": "endclass",
             "primitive": "endprimitive", "config": "endconfig", "checker": "endchecker"}


def _label_end(toks: Sequence[Tok], e: int) -> int:
    """Index of the last token of `endX [: label]` at e."""
    if e + 2 < len(toks) and toks[e + 1].kind == "op" and toks[e + 1].text == ":" and \
            toks[e + 2].kind in ("id", "esc"):
        return e + 2
    return e


def units(pp: PP) -> List[Tuple[str, int, int]]:
    """Top-level design units: (keyword, keyword token, last token incl. end label). Cached."""
    if pp._units is not None:
        return pp._units
    toks = pp.toks()
    out: List[Tuple[str, int, int]] = []
    n = len(toks)
    i = 0
    while i < n:
        t = toks[i]
        if t.kind != "id" or t.text not in _UNIT_END:
            i += 1
            continue
        w = t.text
        prev = toks[i - 1].text if i > 0 else ""
        if prev == "extern":
            j = _find(toks, i, n, ";")
            i = n if j < 0 else j + 1
            continue
        if prev == "typedef" or (w == "class" and prev == "interface"):
            i += 1
            continue
        endkw = _UNIT_END[w]
        if w == "interface" and i + 1 < n and toks[i + 1].text == "class":
            endkw = "endclass"
        openers = ("module", "macromodule") if endkw == "endmodule" else (w,)
        depth = 0
        j = i
        while j < n:
            x = toks[j]
            if x.kind == "id":
                if x.text in openers and (j == i or toks[j - 1].text not in ("extern", "typedef")):
                    depth += 1
                elif x.text == endkw:
                    depth -= 1
                    if depth == 0:
                        break
            j += 1
        if j >= n:
            out.append((w, i, n - 1))
            break
        j = _label_end(toks, j)
        out.append((w, i, j))
        i = j + 1
    pp._units = out
    return out


def _index(pp: PP) -> None:
    toks = pp.toks()
    pp.modules = {}
    for kw, i, last in units(pp):
        if kw not in ("module", "macromodule"):
            continue
        d = _read_module(pp, toks, kw, i, last)
        if d is not None:
            pp.modules.setdefault(d.name, []).append(d)
    pp.variables, pp.tri_nets = [], []
    for d in pp.definitions():
        _scan_decls(pp, toks, d)


def _read_module(pp: PP, toks: List[Tok], kw: str, i: int, last: int) -> Optional[ModuleDef]:
    a = i
    while a > 0 and toks[a - 1].kind == "attr":
        a -= 1
    k = i + 1
    if k <= last and toks[k].text in ("automatic", "static"):
        k += 1
    if k > last or toks[k].kind not in ("id", "esc"):
        pp.notes.append(error(pp.origin_at(toks[i].start), "%s without a name" % kw))
        return None
    p = k + 1
    while p <= last and toks[p].text == "import":
        j = _find(toks, p, last + 1, ";")
        p = last + 1 if j < 0 else j + 1
    hdr = _find(toks, p, last + 1, ";")
    end = last if toks[last].text == "endmodule" else last - 2
    if toks[end].text != "endmodule":
        pp.notes.append(error(pp.origin_at(toks[i].start), "module %s has no endmodule" % toks[k].name))
        end = last
    if hdr < 0:
        hdr = end
    line = pp.line_of(toks[i].start)
    f = pp.file_of_line(line)
    return ModuleDef(name=toks[k].name, keyword=kw, start=toks[a].start, end=toks[last].end, line=line,
                     end_line=pp.line_of(max(toks[last].end - 1, 0)), file=f,
                     lib=bool(f) and f in pp.lib_files, origin=pp.origin(line),
                     tok_kw=i, tok_name=k, tok_hdr=hdr, tok_end=end)


# Blocks for the item-level scans: opener -> closers.
_BLOCKS = {"begin": ("end",), "fork": ("join", "join_any", "join_none"),
           "case": ("endcase",), "casex": ("endcase",), "casez": ("endcase",), "randcase": ("endcase",),
           "function": ("endfunction",), "task": ("endtask",), "generate": ("endgenerate",),
           "specify": ("endspecify",), "class": ("endclass",), "covergroup": ("endgroup",),
           "property": ("endproperty",), "sequence": ("endsequence",), "clocking": ("endclocking",),
           "interface": ("endinterface",), "module": ("endmodule",), "macromodule": ("endmodule",),
           "checker": ("endchecker",), "randsequence": ("endsequence",), "table": ("endtable",),
           "primitive": ("endprimitive",), "program": ("endprogram",)}
_CLOSERS = {c for cs in _BLOCKS.values() for c in cs}
# Blocks whose declarations are not module-level nets or variables.
_CODE_BLOCKS = frozenset(("function", "task", "class", "covergroup", "property", "sequence", "clocking",
                          "specify", "randsequence", "table", "interface", "module", "macromodule",
                          "checker", "primitive", "program"))
_STMT_PREV = frozenset(("begin", "end", "fork", "join", "join_any", "join_none", "generate",
                        "endgenerate", "endfunction", "endtask", "endcase", "endclass", "endgroup",
                        "endproperty", "endsequence", "endclocking", "endspecify", "else"))


def _opens_block(toks: Sequence[Tok], i: int) -> bool:
    """toks[i] (a _BLOCKS keyword) really opens a block here."""
    w = toks[i].text
    prev = toks[i - 1].text if i > 0 else ""
    if w == "fork" and prev in ("wait", "disable"):
        return False
    if w in ("function", "task"):
        j = i - 1
        while j >= 0 and toks[j].kind == "id" and toks[j].text in ("context", "pure", "virtual", "static",
                                                                    "automatic", "protected", "local"):
            j -= 1
        if j >= 0 and toks[j].kind == "str" and j > 0 and toks[j - 1].text in ("import", "export"):
            return False                              # DPI import/export: no body
        if prev == "extern" or (prev == "virtual" and i > 1 and toks[i - 2].text == "pure"):
            return False
    if w in ("property", "sequence") and prev in ("assert", "assume", "cover", "restrict", "expect"):
        return False
    if w == "class" and prev in ("typedef", "interface"):
        return False
    if w == "clocking" and prev in ("default", "global"):
        j = i + 1
        if j < len(toks) and _is_ident(toks[j]):
            j += 1
        return j < len(toks) and toks[j].text == "@"
    if w in ("interface", "module", "macromodule") and prev == "extern":
        return False
    if w == "interface" and i + 1 < len(toks) and toks[i + 1].text == "class":
        return False
    return True


def _stmt_start(toks: Sequence[Tok], i: int, first: int) -> bool:
    """toks[i] begins a statement (follows ';', a block keyword or a block label)."""
    j = i - 1
    while j >= first and toks[j].kind == "attr":
        j -= 1
    if j < first:
        return True
    p = toks[j]
    if p.kind == "op":
        return p.text == ";"
    if p.kind == "id" and p.text in _STMT_PREV:
        return True
    if j - 2 >= first and toks[j - 1].text == ":" and toks[j - 2].kind == "id" and \
            toks[j - 2].text in ("begin", "fork", "end", "join", "join_any", "join_none"):
        return True                                   # begin : label <stmt>
    return False


@dataclass
class _Item:
    """One parsed declaration item: a port-list item or one declared name."""
    direction: str = ""
    net: str = ""
    var: bool = False
    dtype: str = ""
    signed: str = ""
    packed: Optional[str] = None
    ndims: int = 0
    name: str = ""
    name_tok: int = -1
    unpacked: Optional[str] = None
    user: str = ""
    default: Optional[str] = None
    explicit: bool = False       # carries a direction, a type, a signing or a packed range
    expr: bool = False           # a port expression (.a(x), {a, b}, a[3:0]) or not understood


def _parse_item(text: str, toks: Sequence[Tok], a: int, b: int) -> _Item:
    """[dir] [net] [var] [type] [signed] {[packed]} [user type {[packed]}] name {[unpacked]} [= v]."""
    it = _Item()
    i = a
    while i < b and toks[i].kind == "attr":
        i += 1
    if i < b and toks[i].kind == "op" and toks[i].text in (".", "{"):
        it.expr = True
        return it
    if i < b and toks[i].kind == "id" and toks[i].text in DIRECTIONS:
        it.direction, it.explicit = toks[i].text, True
        i += 1
    while i < b and toks[i].kind == "id":
        w = toks[i].text
        if w in NET_TYPES:
            it.net = w
        elif w == "var":
            it.var = True
        elif w in DATA_TYPES:
            it.dtype = w
        elif w in ("signed", "unsigned"):
            it.signed = w
        else:
            break
        it.explicit = True
        i += 1
    dims = []
    while i < b and toks[i].kind == "op" and toks[i].text == "[":
        j = _match(toks, i)
        dims.append(text[toks[i].start:toks[j].end])
        i = j + 1
    if dims:
        it.packed, it.ndims, it.explicit = "".join(dims), len(dims), True
    if i < b and _is_ident(toks[i]):
        k = i + 1
        if k + 1 < b and toks[k].text == "." and _is_ident(toks[k + 1]):
            k += 2                                    # interface.modport
        elif k + 1 < b and toks[k].text == "::" and _is_ident(toks[k + 1]):
            k += 2                                    # package::type
        while k < b and toks[k].kind == "op" and toks[k].text == "[":
            k = _match(toks, k) + 1
        if k < b and _is_ident(toks[k]):
            it.user = text[toks[i].start:toks[k - 1].end]
            i = k
        it.name, it.name_tok = toks[i].name, i
        i += 1
    else:
        it.expr = True
        return it
    udims = []
    while i < b and toks[i].kind == "op" and toks[i].text == "[":
        j = _match(toks, i)
        udims.append(text[toks[i].start:toks[j].end])
        i = j + 1
    if udims:
        it.unpacked = "".join(udims)
    if i < b and toks[i].text == "=":
        it.default = text[toks[i + 1].start:toks[b - 1].end].strip() if i + 1 < b else ""
        i = b
    if i < b:
        it.expr = True
    return it


def _inherit(items: List[_Item], first_default: str = "inout") -> None:
    """ANSI rules (LRM 23.2.2.3): an item with no direction, type, signing or packed
    range inherits the previous item's; a missing direction alone is inherited (the
    first port's defaults to inout)."""
    prev: Optional[_Item] = None
    for it in items:
        if it.expr:
            prev = it
            continue
        if prev is not None and not it.explicit and not it.user:
            it.direction, it.net, it.var, it.dtype = prev.direction, prev.net, prev.var, prev.dtype
            it.signed, it.packed, it.ndims = prev.signed, prev.packed, prev.ndims
        elif not it.direction:
            it.direction = prev.direction if prev is not None else first_default
        prev = it


def _is_variable(it: _Item) -> bool:
    """A declared item that is a variable: `output reg y`, `logic x`, `var x`, but not
    `input logic a` (a net), `wire logic w` or an inout."""
    if it.net or it.direction == "inout":
        return False
    if it.var:
        return True
    if it.dtype in VAR_TYPES:
        return not (it.direction == "input" and it.dtype == "logic")
    return False


def _scan_decls(pp: PP, toks: List[Tok], d: ModuleDef) -> None:
    """Record the variable and special-net declarations of one module, ports included."""
    text = pp.text
    lp = d.tok_name + 1
    while lp < d.tok_hdr and toks[lp].text == "import":
        j = _find(toks, lp, d.tok_hdr, ";")
        lp = d.tok_hdr if j < 0 else j + 1
    if lp < d.tok_hdr and toks[lp].text == "#" and lp + 1 < d.tok_hdr:
        lp = _match(toks, lp + 1) + 1
    if lp < d.tok_hdr and toks[lp].text == "(":
        close = _match(toks, lp)
        items = [_parse_item(text, toks, a, b) for a, b in _split(toks, lp + 1, close) if a < b]
        if items and (items[0].explicit or items[0].user):
            _inherit(items)
            for it in items:
                _record(pp, toks, d, it, True, "")
    stack: List[Tuple[str, str]] = []
    i = d.tok_hdr + 1
    while i < d.tok_end:
        t = toks[i]
        if t.kind != "id":
            # struct/union members and assignment patterns sit inside braces: never module items
            i = _match(toks, i) + 1 if (t.kind == "op" and t.text == "{") else i + 1
            continue
        w = t.text
        if w in _BLOCKS and _opens_block(toks, i):
            label = ""
            if w == "begin":
                label = "-"
                if i + 2 < d.tok_end and toks[i + 1].text == ":":
                    label = toks[i + 2].name
            stack.append((w, label))
            i += 1
            continue
        if w in _CLOSERS:
            for k in range(len(stack) - 1, -1, -1):
                if w in _BLOCKS[stack[k][0]]:
                    del stack[k:]
                    break
            i += 1
            continue
        if (w in DIRECTIONS or w in NET_TYPES or w in VAR_TYPES or w == "var") and \
                not any(s[0] in _CODE_BLOCKS for s in stack) and _stmt_start(toks, i, d.tok_hdr + 1):
            j = _find(toks, i, d.tok_end, ";")
            if j < 0:
                break
            items = [_parse_item(text, toks, a, b) for a, b in _split(toks, i, j) if a < b]
            _inherit(items, first_default="")
            block = ".".join(s[1] for s in stack if s[0] == "begin")
            for it in items:
                _record(pp, toks, d, it, bool(it.direction), block)
            i = j + 1
            continue
        i += 1


def _record(pp: PP, toks: Sequence[Tok], d: ModuleDef, it: _Item, port: bool, block: str) -> None:
    if it.expr or not it.name:
        return
    line = pp.line_of(toks[it.name_tok].start)
    if it.net in TRI_KINDS:
        pp.tri_nets.append(Decl(d.name, it.name, it.net, line, pp.origin(line), it.packed, port, block))
    elif _is_variable(it):
        kind = it.dtype or "logic"
        pp.variables.append(Decl(d.name, it.name, kind, line, pp.origin(line), it.packed, port, block))


# =============================================================================
# The time base (§1.2)
# =============================================================================

def _time_decl(toks: Sequence[Tok], i: int) -> Tuple[Optional[int], Optional[int]]:
    """timeunit X [/ Y]; or timeprecision X; at toks[i] -> exponents (X, Y)."""
    j = _find(toks, i, len(toks), ";")
    if j < 0:
        return None, None
    body = " ".join(t.text for t in toks[i + 1:j])
    if "/" in body:
        a, _, b = body.partition("/")
        return time_exp(a), time_exp(b)
    return time_exp(body), None


def _local_time(toks: Sequence[Tok], d: ModuleDef) -> Tuple[Optional[int], Optional[int]]:
    unit = prec = None
    depth = 0
    for j in range(d.tok_hdr + 1, d.tok_end):
        t = toks[j]
        if t.kind != "id":
            continue
        if t.text in ("module", "macromodule", "interface", "class", "program", "function", "task") and \
                _opens_block(toks, j):
            depth += 1
        elif t.text in ("endmodule", "endinterface", "endclass", "endprogram", "endfunction", "endtask"):
            depth -= 1
        elif depth == 0 and t.text == "timeunit":
            u, p = _time_decl(toks, j)
            unit = u if u is not None else unit
            prec = p if p is not None else prec
        elif depth == 0 and t.text == "timeprecision":
            u, _ = _time_decl(toks, j)
            prec = u if u is not None else prec
    return unit, prec


def _time_rules(pp: PP, ams: bool, override: Optional[str]) -> List[Note]:
    """Each module's unit and precision; the AMS rules; PP.precision."""
    toks = pp.toks()
    defs = {d.tok_kw: d for d in pp.definitions()}
    unit_of: Dict[int, Tuple[str, int]] = {}          # token index -> (unit keyword, last token)
    for kw, i, last in units(pp):
        unit_of[i] = (kw, last)
    cur: Optional[Tuple[int, int]] = None
    cu_unit: Optional[int] = None
    cu_prec: Optional[int] = None
    eff: Dict[int, Tuple[Optional[int], Optional[int]]] = {}

    def directive(t: Tok) -> None:
        nonlocal cur
        if t.text.startswith("`timescale"):
            try:
                cur = parse_timescale(t.text[len("`timescale"):])
            except ValueError:
                pass
        elif t.text == "`resetall":
            cur = None

    i, n = 0, len(toks)
    while i < n:
        t = toks[i]
        if t.kind == "dir":
            directive(t)
            i += 1
            continue
        if i in unit_of:
            kw, last = unit_of[i]
            if i in defs:
                d = defs[i]
                lu, lp = _local_time(toks, d)
                eff[d.tok_kw] = (lu if lu is not None else (cur[0] if cur else cu_unit),
                                 lp if lp is not None else (cur[1] if cur else cu_prec))
            for j in range(i, last + 1):               # directives inside a unit still count
                if toks[j].kind == "dir":
                    directive(toks[j])
            i = last + 1
            continue
        if t.kind == "id" and t.text in ("timeunit", "timeprecision"):
            u, p = _time_decl(toks, i)
            if t.text == "timeunit":
                cu_unit = u if u is not None else cu_unit
                cu_prec = p if p is not None else cu_prec
            elif u is not None:
                cu_prec = u
        i += 1

    used = {x.module for x in instantiations(pp)}
    notes: List[Note] = []
    finest: Optional[int] = None
    hint = "give -timescale or -override_timescale with a precision <= 1ms"
    for d in pp.definitions():
        unit, prec = eff.get(d.tok_kw, (None, None))
        d.unit = time_text(unit) if unit is not None else None
        d.precision = time_text(prec) if prec is not None else None
        if d.lib and d.name not in used:
            continue
        if prec is not None and (finest is None or prec < finest):
            finest = prec
        if not ams:
            continue
        if unit is None or prec is None:
            what = "time unit" if unit is None else "time precision"
            notes.append(error(d.origin, "module %s has no %s (no `timescale, %s); %s"
                               % (d.name, what, "timeunit" if unit is None else "timeprecision", hint)))
        elif prec > MAX_PRECISION:
            if override is not None:
                notes.append(error("-override_timescale", "digital precision %s is coarser than 1 ms; "
                                   "give a precision <= 1ms" % d.precision))
                break
            notes.append(error(d.origin, "module %s: digital precision %s is coarser than 1 ms; %s"
                               % (d.name, d.precision, hint)))
    pp.precision = time_text(finest) if finest is not None else None
    return notes


# =============================================================================
# Instantiations, the API scan, the root scan (§1.3, §1.4)
# =============================================================================

def instantiations(pp: PP) -> List[Inst]:
    """Every `<id> [#(...)|#n] <id> [range] (...) [, <id> (...)]` instantiation (cached).

    A structural scan: generate conditions are not evaluated, so an
    instantiation in a branch that is never taken is listed too.
    """
    if pp._insts is None:
        pp._insts = _scan_insts(pp, pp.toks())
    return pp._insts


def _scan_insts(pp: PP, toks: List[Tok]) -> List[Inst]:
    out: List[Inst] = []
    defs = pp.definitions()
    starts = [d.start for d in defs]
    text = pp.text
    n = len(toks)
    i = 0
    while i < n - 2:
        t = toks[i]
        if not _is_ident(t) or (i > 0 and toks[i - 1].kind == "op" and toks[i - 1].text in (".", "::", "#", "'")):
            i += 1
            continue
        j = i + 1
        params = None
        if toks[j].kind == "op" and toks[j].text == "#":
            if j + 1 < n and toks[j + 1].text == "(":
                c = _match(toks, j + 1)
                params = text[toks[j].start:toks[c].end]
                j = c + 1
            elif j + 1 < n and toks[j + 1].kind in ("num", "id"):
                params = text[toks[j].start:toks[j + 1].end]
                j += 2
            else:
                i += 1
                continue
        found = False
        while j < n and _is_ident(toks[j]):
            k = j + 1
            arr = None
            while k < n and toks[k].kind == "op" and toks[k].text == "[":
                c = _match(toks, k)
                arr = (arr or "") + text[toks[k].start:toks[c].end]
                k = c + 1
            if not (k < n and toks[k].kind == "op" and toks[k].text == "("):
                break
            c = _match(toks, k)
            named: List[str] = []
            positional = 0
            conns: List[Conn] = []
            segs = _split(toks, k + 1, c)
            for a, b in segs:
                while a < b and toks[a].kind == "attr":
                    a += 1
                if a >= b:
                    if len(segs) > 1:
                        positional += 1                   # an empty positional connection
                        conns.append(Conn(None, ""))
                    continue
                if toks[a].text == ".*":
                    named.append("*")
                    conns.append(Conn("*", None))
                elif toks[a].text == "." and a + 1 < b and _is_ident(toks[a + 1]):
                    named.append(toks[a + 1].name)
                    expr = None
                    if a + 2 < b and toks[a + 2].text == "(":
                        close = _match(toks, a + 2)
                        expr = text[toks[a + 3].start:toks[close - 1].end] if close > a + 3 else ""
                    conns.append(Conn(toks[a + 1].name, expr))
                else:
                    positional += 1
                    conns.append(Conn(None, text[toks[a].start:toks[b - 1].end]))
            x = bisect_right(starts, t.start) - 1
            scope = defs[x].name if x >= 0 and defs[x].start <= t.start < defs[x].end else ""
            out.append(Inst(t.name, toks[j].name, scope, t.start, pp.line_of(toks[j].start), params,
                            named, positional, arr, toks[c].end, conns))
            found = True
            j = c + 1
            if j + 1 < n and toks[j].text == "," and _is_ident(toks[j + 1]):
                j += 1
                continue
            break
        i = j if found else i + 1
    return out


def ident_ref(expr: Optional[str]) -> Optional[Tuple[str, bool]]:
    """(name, selected) when `expr` is one identifier (escaped allowed) with optional bit or
    part selects: "a" -> ("a", False), "b[3]", "c[W-1:0]", "d[i][1]" -> (.., True); None for
    anything else (an expression, a literal, a concatenation, a hierarchical name)."""
    toks = tokenize(expr or "")
    if not toks or not _is_ident(toks[0]):
        return None
    i = 1
    while i < len(toks) and toks[i].kind == "op" and toks[i].text == "[":
        j = _match(toks, i)
        if toks[j].text != "]":
            return None
        i = j + 1
    if i != len(toks):
        return None
    return toks[0].name, len(toks) > 1


def concat_operands(expr: Optional[str]) -> Optional[List[str]]:
    """The operand texts of a concatenation "{a, b[1], {c, d}}" (one level); None when
    `expr` is not one (a replication "{2{a}}" is not), or an operand is empty."""
    e = (expr or "").strip()
    toks = tokenize(e)
    if len(toks) < 2 or toks[0].text != "{" or _match(toks, 0) != len(toks) - 1:
        return None
    out = []
    for a, b in _split(toks, 1, len(toks) - 1):
        if a >= b or (b - a > 1 and toks[a + 1].text == "{" and toks[a].kind != "op"):
            return None                                    # empty, or a replication count
        out.append(e[toks[a].start:toks[b - 1].end])
    return out


_API_SYS = ("$snps_", "$hdl_xmr")
_API_IDS = frozenset(("snps_above", "snps_cross", "snps_absdelta"))


def api_scan(pp: PP) -> List[Note]:
    """The VCS analog-access API (§1.3): every $snps_* / $hdl_xmr* call and every
    snps_above / snps_cross / snps_absdelta outside comments, strings and
    attributes is an error at its line."""
    out: List[Note] = []
    for t in pp.toks():
        if (t.kind == "sys" and t.text.startswith(_API_SYS)) or (t.kind == "id" and t.text in _API_IDS):
            out.append(error(pp.origin_at(t.start), "%s: the VCS analog-access API is not supported "
                             "(vamos AMS v1)" % t.text))
    return out


# tgt-vhdl's located comments for what it cannot translate: a dropped system task, and a
# system function replaced by a constant.
_UNSUPPORTED = re.compile(r"Unsupported system (task|function) (\S+) (?:omitted|replaced by (.+?)) "
                          r"here \((.*):(\d+)\)")


def unsupported_tasks(vhd_text: str, pp: Optional[PP] = None, ams: bool = True) -> List[Note]:
    """§1.3, after translation: every tgt-vhdl "Unsupported system task $x omitted here
    (<file>:<line>)" comment in design.vhd, and every "Unsupported system function $f
    replaced by <value> here (<file>:<line>)" one, is an error in AMS mode and a warning in
    plain vcs mode, once per task or function and source line.  With pp, a line of nvc/_pp.v
    (or _norm.sv, whose lines are pp.v's when sv-normalize kept them) is reported at the
    user's file:line."""
    out: List[Note] = []
    seen: Set[Tuple[str, str, str, int]] = set()
    for m in _UNSUPPORTED.finditer(vhd_text):
        kind, name, value, f, ln = m.group(1), m.group(2), m.group(3), m.group(4), int(m.group(5))
        if (kind, name, f, ln) in seen:
            continue
        seen.add((kind, name, f, ln))
        base = os.path.basename(f)
        where = pp.origin(ln) if pp is not None and base in ("_pp.v", "_norm.sv", "pp.v", "pp.orig.v") \
            else "%s:%d" % (f, ln)
        if kind == "task":
            msg = ("system task %s is not translated (it would be dropped from the simulation)" if ams
                   else "system task %s is not translated: the simulation drops it") % name
        else:
            msg = ("system function %s is not translated (it would return %s in the simulation)" if ams
                   else "system function %s is not translated: every call returns %s in the "
                        "simulation") % (name, value)
        out.append(error(where, msg) if ams else warning(where, msg))
    return out


# iverilog-sv2ghdl's surfaced tgt-vhdl warnings (T4: under vamos it repeats the top run's
# "connected one way only" / "not translated" warnings on stderr, prefixed so)
TRANSLATOR_WARNING = re.compile(r"^iverilog-sv2ghdl: Warning: (.*)$")
_XLAT_AT = re.compile(r" at (.+?):(\d+)(?= is | |$)")


def translator_warnings(lines: Iterable[str], pp: Optional[PP] = None) -> List[Note]:
    """The translator's warnings (TRANSLATOR_WARNING lines of the translation's output) as
    vamos warnings, once each: an approximation of the design (a connection made one way
    only, a pull with nothing to sit on), so --vamos-strict makes each one an error (§0).
    A location in nvc/_pp.v, _norm.sv or pp.v is reported at the user's file:line (pp),
    and taken out of the message ("bus_sig(2) at _norm.sv:7 is connected one way only: ..."
    -> "tb.v:7: bus_sig(2) is connected one way only: ...")."""
    out: List[Note] = []
    seen: Set[Tuple[str, str]] = set()
    for line in lines:
        m = TRANSLATOR_WARNING.match(line.rstrip("\r\n"))
        if not m:
            continue
        text, where = m.group(1).strip(), ""
        a = _XLAT_AT.search(text)
        if a:
            f, ln = a.group(1), int(a.group(2))
            base = os.path.basename(f)
            where = pp.origin(ln) if pp is not None and base in ("_pp.v", "_norm.sv", "pp.v",
                                                                 "pp.orig.v") else "%s:%d" % (f, ln)
            text = text[:a.start()] + text[a.end():]
        if (where, text) in seen:
            continue
        seen.add((where, text))
        out.append(warning(where, text))
    return out


def find_roots(pp: PP, exclude: Iterable[str] = (), case_sensitive: Optional[bool] = None) -> List[str]:
    """Structural roots (§1.4), in definition order: the defined modules, minus every
    module named in an instantiation, minus every module matching an `exclude` glob
    (the use_spice/partition cells), minus modules defined only in -v files."""
    cs = pp.case_sensitive if case_sensitive is None else case_sensitive
    used = {x.module for x in instantiations(pp)}
    pats = list(exclude)
    roots = []
    for name, defs in pp.modules.items():
        if name in used or all(d.lib for d in defs):
            continue
        if any(globs.match(p, name, cs) for p in pats):
            continue
        roots.append((min(d.start for d in defs), name))
    return [r for _, r in sorted(roots)]


def find_top(pp: PP, job: Job, exclude: Iterable[str] = ()) -> str:
    """The top module (§1.4): -top (job.tops[0]) if given, else the one structural root.

    exclude: the cut-cell globs (use_spice / partition -cell patterns).  Raises
    NoteError when -top names no module, or when there is not exactly one root.
    """
    if len(job.tops) > 1:
        raise NoteError([error("-top", "an AMS design has one top module; -top was given %d times (%s)"
                               % (len(job.tops), ", ".join(job.tops)))])
    if job.tops:
        top = job.tops[0]
        if top not in pp.modules:
            raise NoteError([error("-top", "-top %s: no module %s in the Verilog sources" % (top, top))])
        return top
    roots = find_roots(pp, exclude)
    if len(roots) == 1:
        return roots[0]
    if not roots:
        raise NoteError([error("", "no top-level module: every module is instantiated, a SPICE cell "
                               "or only in a -v library; give -top <module>")])
    where = ", ".join("%s (%s)" % (r, pp.modules[r][0].origin) for r in roots)
    raise NoteError([error("", "several top-level modules: %s; give -top <module>" % where)])


# =============================================================================
# Module headers (§5.1)
# =============================================================================

@dataclass
class HeaderPort:
    name: str
    direction: str               # input | output | inout
    kind: str                    # model.LOGIC | model.REAL (real, realtime, shortreal, wreal)
    range_text: Optional[str]    # packed range as written ("[N-1:0]"), None for a scalar
    msb: Optional[int] = None    # left bound when the range is constant
    lsb: Optional[int] = None    # right bound
    variable: bool = False       # declared reg/logic/bit/var: the shell drops that (every port is a net)
    type_text: str = ""          # net/data-type words as written ("wire", "reg signed", "wreal")
    origin: str = ""


@dataclass
class HeaderParam:
    name: str
    default: str                 # value text as written
    local: bool = False          # localparam, or a body parameter beside a parameter port list
    in_header: bool = False      # declared in #( ... )
    origin: str = ""


@dataclass
class Header:
    """A module header, parsed for the shell (ANSI or non-ANSI)."""
    name: str
    keyword: str
    ansi: bool
    ports: List[HeaderPort] = field(default_factory=list)
    params: List[HeaderParam] = field(default_factory=list)
    param_text: str = ""         # "#( ... )" verbatim, "" if none
    import_text: str = ""        # package imports between the name and #( / ( (verbatim)
    body_text: List[str] = field(default_factory=list)
    # Body items the shell copies, verbatim and in order: imports, parameter and localparam
    # declarations, and the functions/typedefs those or the port ranges use.
    range_params: Set[str] = field(default_factory=set)
    # Parameters the port ranges depend on, directly or through body parameters.
    origin: str = ""

    def defaults(self) -> Dict[str, str]:
        return {p.name: p.default for p in self.params}


def const_range(text: Optional[str]) -> Optional[Tuple[int, int]]:
    """"[3:0]" -> (3, 0) when both bounds are integer constants (+ - * ** and parentheses)."""
    if not text:
        return None
    m = re.match(r"^\s*\[(.*)\]\s*$", text, re.S)
    if not m or re.search(r"\]\s*\[", text):
        return None
    inner = m.group(1)
    depth, cut = 0, -1
    for k, ch in enumerate(inner):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == ":" and depth == 0:
            cut = k
            break
    if cut < 0:
        return None
    a, b = _const_int(inner[:cut]), _const_int(inner[cut + 1:])
    if a is None or b is None:
        return None
    return a, b


def _two_bounds(text: str) -> bool:
    """"[a:b]" with exactly one top-level colon (not "[N]", "[a+:w]", "[a::b]")."""
    m = re.match(r"^\s*\[(.*)\]\s*$", text, re.S)
    if not m:
        return False
    depth, colons = 0, 0
    inner = m.group(1)
    for k, ch in enumerate(inner):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == ":" and depth == 0:
            if (k > 0 and inner[k - 1] in "+-:") or (k + 1 < len(inner) and inner[k + 1] == ":"):
                return False
            colons += 1
    return colons == 1


def _const_int(expr: str) -> Optional[int]:
    expr = expr.replace("_", "").strip()
    if not expr or not re.match(r"^[0-9+\-*() \t]+$", expr):
        return None
    try:
        v = eval(compile(expr, "<range>", "eval"), {"__builtins__": {}}, {})
    except Exception:
        return None
    return v if isinstance(v, int) else None


def _idents(toks: Sequence[Tok], a: int, b: int) -> Set[str]:
    return {toks[j].name for j in range(a, b) if _is_ident(toks[j])}


def module_header(pp: PP, name: str) -> Header:
    """Parse the header of module `name` (its one definition) for a multi-view shell (§5.1).

    Ports in order with direction, kind, range text and constant bounds;
    parameters with defaults; the body items the shell copies.  Raises
    NoteError for: no definition or several; port expressions or
    concatenations in the port list (v1); interface, user-typed, ref,
    unpacked, multi-dimensional or integer-typed ports; a port without a
    direction declaration; type parameters.  Cached on the PP.
    """
    if name in pp._headers:
        return pp._headers[name]
    defs = pp.modules.get(name, [])
    if len(defs) != 1:
        where = ", ".join(d.origin for d in defs) or "nowhere"
        raise NoteError([error(defs[0].origin if defs else "", "module %s is defined %d times (%s); a "
                               "SPICE cell needs exactly one Verilog view" % (name, len(defs), where))])
    h, problems, _ = _parse_header(pp, defs[0])
    if problems:
        raise NoteError(problems)
    pp._headers[name] = h
    return h


def port_directions(pp: PP, name: str) -> Optional[List[Tuple[str, str]]]:
    """[(port, direction)] of module `name` in port-list order, for any module (a wrapper,
    not only a SPICE cell): never raises.  None when the module is not defined exactly once
    or its port list cannot be read in order (port expressions, concatenations, a port
    listed twice or without a direction declaration)."""
    defs = pp.modules.get(name, [])
    if len(defs) != 1:
        return None
    h, _, ordered = _parse_header(pp, defs[0])
    if not ordered:
        return None
    return [(p.name, p.direction) for p in h.ports]


def constant_names(pp: PP, name: str) -> Set[str]:
    """The parameters, localparams, specparams and genvars declared anywhere in module
    `name` (an identifier naming one is a constant, not a net); empty if not defined once."""
    defs = pp.modules.get(name, [])
    if len(defs) != 1:
        return set()
    d = defs[0]
    toks = pp.toks()
    out: Set[str] = set()

    def assigned(a: int, b: int) -> None:          # "[type] [range] N = v, M = w": N, M
        for x, y in _split(toks, a, b):
            eq = _find(toks, x, y, "=")
            ids = [toks[k].name for k in range(x, eq) if _is_ident(toks[k])] if eq > 0 else []
            if ids:
                out.add(ids[-1])

    i = d.tok_name + 1
    while i < d.tok_end:
        t = toks[i]
        if i < d.tok_hdr and t.text == "#" and i + 1 < d.tok_hdr and toks[i + 1].text == "(":
            close = _match(toks, i + 1)              # the #( ... ) parameter port list
            assigned(i + 2, close)
            i = close + 1
            continue
        if t.kind == "id" and t.text in ("parameter", "localparam", "specparam") and i > d.tok_hdr:
            j = _find(toks, i, d.tok_end, ";")
            if j < 0:
                break
            assigned(i + 1, j)
            i = j + 1
            continue
        if t.kind == "id" and t.text == "genvar":
            if toks[i - 1].text == "(":                 # for (genvar g = 0; ...)
                if i + 1 < d.tok_end and _is_ident(toks[i + 1]):
                    out.add(toks[i + 1].name)
                i += 1
                continue
            j = _find(toks, i, d.tok_end, ";")
            if j < 0:
                break
            out |= {toks[k].name for k in range(i + 1, j) if _is_ident(toks[k])}
            i = j + 1
            continue
        i += 1
    return out


def _parse_header(pp: PP, d: ModuleDef) -> Tuple[Header, List[Note], bool]:
    """The header of definition d: (Header, the SPICE-cell problems module_header raises,
    whether the port list was read completely and in order)."""
    name = d.name
    toks, text = pp.toks(), pp.text
    h = Header(name=d.name, keyword=d.keyword, ansi=False, origin=d.origin)
    problems: List[Note] = []

    def bad(j: int, msg: str) -> None:
        problems.append(error(pp.origin_at(toks[j].start), "cell %s: %s" % (name, msg)))

    p = d.tok_name + 1
    imp = p
    while p < d.tok_hdr and toks[p].text == "import":
        j = _find(toks, p, d.tok_hdr, ";")
        p = d.tok_hdr if j < 0 else j + 1
    if p > imp:
        h.import_text = text[toks[imp].start:toks[p - 1].end]
    has_plist = False
    deps: Dict[str, Set[str]] = {}                   # parameter -> identifiers of its default
    if p < d.tok_hdr and toks[p].text == "#" and p + 1 < d.tok_hdr and toks[p + 1].text == "(":
        close = _match(toks, p + 1)
        h.param_text = text[toks[p].start:toks[close].end]
        has_plist = True
        local = False
        for a, b in _split(toks, p + 2, close):
            if a >= b:
                continue
            if toks[a].text in ("parameter", "localparam"):
                local = toks[a].text == "localparam"
            if any(toks[j].text == "type" for j in range(a, b)):
                bad(a, "type parameters are not supported on a SPICE cell")
                continue
            eq = _find(toks, a, b, "=")
            nm = [toks[j].name for j in range(a, eq if eq > 0 else b) if _is_ident(toks[j])]
            if nm:
                dflt = text[toks[eq + 1].start:toks[b - 1].end].strip() if 0 < eq < b - 1 else ""
                h.params.append(HeaderParam(nm[-1], dflt, local, True, pp.origin_at(toks[a].start)))
                if eq > 0:
                    deps[nm[-1]] = _idents(toks, eq + 1, b)
        p = close + 1
    order: List[str] = []
    ordered = True                                   # every port read, in port-list order
    ansi_items: List[_Item] = []
    if p < d.tok_hdr and toks[p].text == "(":
        close = _match(toks, p)
        segs = [(a, b) for a, b in _split(toks, p + 1, close) if a < b]
        items = [_parse_item(text, toks, a, b) for a, b in segs]
        if items:
            first = items[0]
            h.ansi = bool(first.explicit or first.user) and not first.expr
            for (a, b), it in zip(segs, items):
                simple = b - a == 1 and _is_ident(toks[a])
                if it.expr or (not h.ansi and not simple):
                    ordered = False
                    bad(a, "port %s: port expressions and concatenations in the port list are not "
                        "supported (v1)" % text[toks[a].start:toks[b - 1].end])
            if h.ansi:
                _inherit(items)
                ansi_items = items
            else:
                order = [it.name for it in items]

    # body: non-ANSI port declarations, parameters, imports, typedefs, functions
    body_decl: Dict[str, _Item] = {}
    types: Dict[str, _Item] = {}
    copies: List[Tuple[int, int, str, Set[str], str]] = []    # (first, last tok, kind, refs, name)
    stack: List[str] = []
    i = d.tok_hdr + 1
    while i < d.tok_end:
        t = toks[i]
        if t.kind != "id":
            i = _match(toks, i) + 1 if (t.kind == "op" and t.text == "{") else i + 1
            continue
        w = t.text
        if w == "function" and not stack and _opens_block(toks, i):
            e = i
            while e < d.tok_end and toks[e].text != "endfunction":
                e += 1
            e = _label_end(toks, e)
            hdr = _find(toks, i, e, ";")
            fname = ""
            for j in range(i + 1, hdr if hdr > 0 else e):
                if _is_ident(toks[j]) and toks[j + 1].text in ("(", ";"):
                    fname = toks[j].name
                    break
            copies.append((i, e, "function", _idents(toks, i, e), fname))
            i = e + 1
            continue
        if w in _BLOCKS and _opens_block(toks, i):
            stack.append(w)
            i += 1
            continue
        if w in _CLOSERS:
            for k in range(len(stack) - 1, -1, -1):
                if w in _BLOCKS[stack[k]]:
                    del stack[k:]
                    break
            i += 1
            continue
        decl_kw = w in ("parameter", "localparam", "specparam", "import", "typedef") or w in DIRECTIONS or \
            w in NET_TYPES or w in DATA_TYPES or w == "var"
        if stack or not decl_kw or not _stmt_start(toks, i, d.tok_hdr + 1):
            i += 1
            continue
        j = _find(toks, i, d.tok_end, ";")
        if j < 0:
            break
        if w in ("parameter", "localparam", "specparam"):
            if any(toks[x].text == "type" for x in range(i, j)):
                bad(i, "type parameters are not supported on a SPICE cell")
            local = w != "parameter" or has_plist
            for a, b in _split(toks, i + 1, j):
                eq = _find(toks, a, b, "=")
                if eq < 0:
                    continue
                nm = [toks[x].name for x in range(a, eq) if _is_ident(toks[x])]
                if nm:
                    h.params.append(HeaderParam(nm[-1], text[toks[eq + 1].start:toks[b - 1].end].strip()
                                                if eq + 1 < b else "", local, False,
                                                pp.origin_at(toks[a].start)))
            copies.append((i, j, "param", _idents(toks, i + 1, j), ""))
        elif w == "import":
            copies.append((i, j, "import", set(), ""))
        elif w == "typedef":
            nm = toks[j - 1].name if _is_ident(toks[j - 1]) else ""
            copies.append((i, j, "typedef", _idents(toks, i + 1, j), nm))
        elif not h.ansi:
            items = [_parse_item(text, toks, a, b) for a, b in _split(toks, i, j) if a < b]
            _inherit(items, first_default="")
            for it in items:
                if it.name:
                    if w in DIRECTIONS:
                        body_decl[it.name] = it
                    else:
                        types[it.name] = it
        i = j + 1

    if h.ansi:
        decl = {it.name: it for it in ansi_items}
        order = [it.name for it in ansi_items]
    else:
        decl = body_decl
    twice = sorted({x for x in order if order.count(x) > 1})
    if twice:
        problems.append(error(d.origin, "cell %s: port %s is listed more than once in the port list; not "
                              "supported on a SPICE cell" % (name, ", ".join(twice))))
        order = []
        ordered = False
    for pname in order:
        it = decl.get(pname)
        if it is None or not it.direction:
            problems.append(error(d.origin, "cell %s: port %s has no direction declaration" % (name, pname)))
            ordered = False
            continue
        ty = types.get(pname)
        dtype = it.dtype or (ty.dtype if ty else "")
        net = it.net or (ty.net if ty else "")
        packed = it.packed if it.packed is not None else (ty.packed if ty else None)
        ndims = it.ndims or (ty.ndims if ty else 0)
        where = pp.origin_at(toks[it.name_tok].start) if it.name_tok >= 0 else d.origin
        if it.direction == "ref":
            problems.append(error(where, "cell %s: ref port %s is not supported" % (name, pname)))
        if it.user or (ty is not None and ty.user):
            problems.append(error(where, "cell %s: port %s has the user-defined or interface type %s; a "
                                  "SPICE cell needs logic or real ports" % (name, pname, it.user or ty.user)))
        if it.unpacked or (ty is not None and ty.unpacked):
            problems.append(error(where, "cell %s: port %s has an unpacked dimension" % (name, pname)))
        if ndims > 1:
            problems.append(error(where, "cell %s: port %s has more than one packed dimension" % (name, pname)))
        if dtype in ("string", "chandle", "event", "byte", "shortint", "int", "longint", "integer", "time"):
            problems.append(error(where, "cell %s: port %s has type %s; declare it with an explicit range"
                                  % (name, pname, dtype)))
        kind = REAL if dtype in REAL_TYPES else LOGIC
        if kind == REAL and packed:
            problems.append(error(where, "cell %s: real port %s has a range" % (name, pname)))
        if packed and ndims == 1 and not _two_bounds(packed):
            problems.append(error(where, "cell %s: port %s: range %s is not [msb:lsb]" % (name, pname, packed)))
        rng = const_range(packed)
        isvar = bool(it.var or dtype in VAR_TYPES or (ty is not None and ty.var))
        words = " ".join(x for x in (net, "var" if it.var else "", dtype, it.signed) if x)
        h.ports.append(HeaderPort(pname, it.direction, kind, packed, rng[0] if rng else None,
                                  rng[1] if rng else None, isvar and kind == LOGIC, words, where))

    # body items to copy: imports and parameters always; functions/typedefs when used -- by a
    # port range, a copied body item, or the header's own parameter port list (`#(... localparam
    # real LSB = scale(VREF, N))' with scale a function of the body: without it the shell failed,
    # "No function named `scale' found")
    needed: Set[str] = set()
    for hp in h.ports:
        if hp.range_text:
            needed |= set(re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", hp.range_text))
    if h.param_text:
        needed |= set(re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", h.param_text))
    chosen = {k for k, c in enumerate(copies) if c[2] in ("param", "import")}
    for k in chosen:
        needed |= copies[k][3]
    changed = True
    while changed:
        changed = False
        for k, c in enumerate(copies):
            if k not in chosen and c[4] and c[4] in needed:
                chosen.add(k)
                needed |= c[3]
                changed = True
    h.body_text = [text[toks[c[0]].start:toks[c[1]].end] for k, c in enumerate(copies) if k in chosen]

    # the parameters behind the port ranges, through other parameters' defaults
    pnames = {x.name for x in h.params}
    for c in copies:
        if c[2] == "param":
            for a, b in _split(toks, c[0] + 1, c[1]):
                eq = _find(toks, a, b, "=")
                if eq > 0:
                    nm = [toks[x].name for x in range(a, eq) if _is_ident(toks[x])]
                    if nm:
                        deps[nm[-1]] = _idents(toks, eq + 1, b)
    todo: Set[str] = set()
    for hp in h.ports:
        if hp.range_text:
            todo |= set(re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", hp.range_text)) & pnames
    seen: Set[str] = set()
    while todo:
        x = todo.pop()
        if x not in seen:
            seen.add(x)
            todo |= (deps.get(x, set()) & pnames) - seen
    h.range_params = seen
    return h, problems, ordered


# =============================================================================
# precheck (§1 step 3)
# =============================================================================

_TIMESCALE_WARN = ("time unit", "timescale", "time precision", "explicit time")


def precheck(job: Job, top: Optional[str] = None, override_timescale: Optional[str] = None,
             pp: Optional[PP] = None) -> List[Note]:
    """iverilog -g2012 -tnull on the ORIGINAL sources, so syntax and elaboration
    errors carry the user's file:line.  The -v files follow the sources as plain
    files, as in the translation (iverilog's own -l preprocesses each library file
    on its own, so the sources' macros would be undefined there); pass the top
    (-s top) so unused library modules are not elaborated.  The timescale prelude
    is the one preprocess() writes.  "Unknown module type" (the SPICE cells, which
    cell_set handles) and time-unit warnings are dropped; any other error is an
    error note, a warning a note.

    pp: the preprocessed stream; when apply_library_rule blanked a -v copy of a module
    (pp.library_masked), the original files would define it twice ("already declared"),
    so the check runs on pp.path instead, its lines reported at the user's file:line
    through pp.origin."""
    args = ["-g2012", "-tnull"]
    if top:
        args += ["-s", top]
    masked = pp is not None and pp.library_masked
    if not masked:
        args += iverilog_defines(job)
    cwd = job.cwd or os.getcwd()
    tmp = None
    ts = override_timescale or job.timescale
    try:
        if masked:
            args.append(pp.path)           # preprocessed: prelude and macros already in it
        else:
            if ts:
                tmp = tempfile.mkdtemp(prefix="vamos-pre-")
                pre = os.path.join(tmp, "vamos_prelude.v")
                with open(pre, "w") as fh:
                    fh.write("`timescale %s\n" % ts)
                args.append(pre)
            args += [job_path(job, s.path) for s in job.sources]
            args += [job_path(job, lib) for lib in job.lib_files]
        rc, out = run_iverilog(args, cwd=cwd)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    diags, other = parse_diags(out)
    notes: List[Note] = []
    unknown = False
    for dg in diags:
        if dg.message.startswith("Unknown module type"):
            unknown = True
            continue
        path = dg.file if os.path.isabs(dg.file) else os.path.join(cwd, dg.file)
        if masked and os.path.normpath(path) == os.path.normpath(pp.path):
            where = pp.origin(dg.line)
        else:
            where = "%s:%d" % (display_path(os.path.normpath(path), cwd), dg.line)
        msg = dg.message + "".join("; " + x for x in dg.extra)
        if dg.kind == "warning":
            if not any(k in dg.message for k in _TIMESCALE_WARN):
                notes.append(note(where, "iverilog: " + msg))
        else:
            notes.append(error(where, msg))
    if rc != 0 and not unknown and not any(n.severity == ERROR for n in notes):
        notes.append(error("", "iverilog -tnull failed (exit %d)%s"
                           % (rc, (": " + " | ".join(other[-5:])) if other else "")))
    return notes
