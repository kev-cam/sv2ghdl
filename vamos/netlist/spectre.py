"""Spectre netlists into the IR (docs/VAMOS_SPECTRE_DESIGN.md §3; the API is the §10 block).

statements(text, origin, title=True) -> List[Stmt]
    The lexical and statement layer (§3.2): one Spectre-language text into Stmt trees, without
    file access and without lowering; the CST oracle's unit (§11 T0).  '//' at the start of a
    line or after white space and '*' lines are comments; '\\' at the end of a line and '+' at
    the start of the next continue a statement, inside braces too; fields are white-space
    separated, '( ) [ ] { } = :' are significant, a '\\' escapes the next character into a name.
    Kinds: instance, analysis (an analysis keyword as the master), options set alter altergroup
    info paramtest check checklimit assert shell (the named control statements), parameters,
    model (a model group holds "bin" children: name = the index), subckt (children; ("inline",
    "yes") in params), if (branches: (condition text, body); the else part has condition ""),
    function (name, nodes = the argument names, params = [("return", body text)]), global, ic,
    nodeset, save (nodes = the items, params = the modifiers), include (name = the file, params =
    [("section", s)]), ahdl_include, library/section (children), simulator, statistics/process/
    mismatch (children), vary, correlate, truncate, export, protect, unprotect, and spice: a
    SPICE-mode region after 'simulator lang=spice' (its text is text[span[0]:span[1]]; parse()
    hands it to spice.parse_fragment).  NoteError on a syntax error.  title: line 1 is the title
    and is not read (False for an included file).

parse(path, cwd, opts) -> Netlist
    Two phases over every file in both languages (§3.1, §4.3): the files are read (includes and
    .lib sections spliced in, the language per file and per 'simulator lang=' switch), every
    subckt, model, parameter and Verilog-A module of every file is declared, then the Spectre
    statements are lowered and each SPICE region is read by spice.parse_fragment against the
    complete table.  Then §3.7 (parameters, both languages at once), §3.8-§3.9 (SPECTRE_MASTERS:
    polarity, level, geometry folds, instance defaults and checks), §3.8.1 (resolve_source), §3.4
    (ground), §3.10-§3.12 (options, analyses, saves, ic/nodeset, statistics).  Netlist.dialect is
    "spectre".  NoteError with every note on any error.

resolve_source(params, type, tran_stops, notes) -> Source
    §3.8.1's table over the parameters written on a vsource/isource (Spectre names, Expr or
    lists of Expr or Str) and its type; plan.py reuses it for alters (§5.3).

number = functools.partial(expr.number, dialect="spectre")   # §3.3
"""

from __future__ import annotations

import functools
import heapq
import math
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Set, Tuple, Union

from vamos.netlist import expr
from vamos.netlist import spice
from vamos.netlist import tables as T
from vamos.netlist.expr_ast import Binary, Call, Expr, Name, Num, Str, Ternary, Unary
from vamos.netlist.ir import (Analysis, Cond, Correlate, Instance, Model, Netlist, Param, ParamTest, ParseOpts,
                              SaveSpec, Source, StatBlock, Subckt, SweepSpec, VaModule, Value, Vary,
                              flat_analyses, flat_items)
from vamos.notes import ERROR, NOTE, WARNING, Note, NoteError, error, note, warning


@dataclass
class SpectreParseOpts:
    search: List[str] = field(default_factory=list)          # the -I directories
    percent: Mapping[str, str] = field(default_factory=dict) # %-codes for quoted strings (§2.4)
    pre: List[str] = field(default_factory=list)             # +pre_config fragment files
    post: List[str] = field(default_factory=list)            # +config fragment files
    cpp_markers: bool = False   # the input is cpp output: origins, include directory and language per
    #                             '# <line> "<file>" [flags]' marker, resolved against the cwd (§2.6)
    title: Optional[str] = None # line 1 of the original file when cpp ran
    mts: bool = True            # False under -mts: options inside subckts are global (§3.6)
    va_include: List[str] = field(default_factory=list)      # CDS_VLOGA_INCLUDE (§2.8)
    va_defines: List[str] = field(default_factory=list)      # -va,define, for va_modules' preprocessor (§3.1)
    top_dir: Optional[str] = None   # the directory the top file's relative includes resolve against
    #                                 (None: the file's own; the cwd for a stdin copy, §1)
    top_name: Optional[str] = None  # the name the top file's origins carry ("stdin"; None: its path)


@dataclass
class Stmt:
    """Phase 0: the statement layer, the CST oracle's unit (§3.2, §11 T0)."""
    kind: str                   # instance model parameters subckt if analysis save ic nodeset include ...
    name: str = ""
    nodes: List[str] = field(default_factory=list)
    master: str = ""            # instance master, model master or analysis keyword
    params: List[Tuple[str, str]] = field(default_factory=list)       # (name, value text as written)
    children: List["Stmt"] = field(default_factory=list)              # subckt, sweep, montecarlo, group body
    branches: List[Tuple[str, List["Stmt"]]] = field(default_factory=list)   # if: (condition text, body)
    origin: str = ""
    span: Tuple[int, int] = (0, 0)                                    # character offsets in the text


# =============================================================================================
# Tables
# =============================================================================================

# ref19 pp.495-497 (§3.2): the netlist keywords and the reserved words may not name instances,
# subckts, models or functions; the constants may not name anything.
KEYWORDS = frozenset(("altergroup correlate else end ends export for function global ic if in inline input "
                      "invisible library local march model nodeset out output parameters paramset plot print "
                      "protect pwr real return save sens statistics subckt to truncate unprotect vary "
                      "visible").split())
RESERVED_WORDS = frozenset("freq scalem temp temper time tnom".split())
# every Spectre analysis keyword (ref19); v1 maps dc ac noise xf tran sweep (§3.11), parses montecarlo,
# and refuses the rest with an error naming it (§3.6 "anything else")
ANALYSES = frozenset(("ac dc noise xf tran sweep montecarlo pz sp stb pss pac pnoise pxf psp pstb hb hbac hbnoise "
                      "hbxf hbsp envlp qpss qpac qpnoise qpxf qpsp sens dcmatch acmatch lf tdr pdisto "
                      "dcop").split())
CONTROLS = frozenset("options set alter altergroup info paramtest check checklimit assert shell".split())

# §3.11: the parameters of each v1 analysis under their Spectre names (ir.py's docstring); a '*' ends
# a prefix (emir*, rf*, ...).  The sweep parameters are SweepSpec's.
_SWEEP_KEYS = ("start", "stop", "center", "span", "step", "lin", "dec", "log", "values", "valuesfile",
               "dev", "mod", "sub", "param")
_DC_ARGS = ("readns write writefinal useprevic save nestlvl print oppoint force readforce restart swpuseprevic "
            "homotopy newton maxsteps swp1stpointic maxiters hysteresis check annotate title emir*").split()
_AC_ARGS = _DC_ARGS + ("freq prevoppoint skipdc perturbation out1 out2 contriblist rf* flin_out fim_out "
                       "maxharm_nonlin").split()
_NOISE_ARGS = _AC_ARGS + "oprobe oportv iprobe iportv separatenoise".split()
_XF_ARGS = _AC_ARGS + "probe stimuli".split()
_TRAN_ARGS = ("stop start outputstart step maxstep minstep istep pstep tpoints readtime ic skipdc readic read "
              "readns write writefinal useprevic linearic rampup* oscfreq cmin method errpreset relref lteratio "
              "maxstepratio reltolratio maxiters transres restart skipstart skipstop skipcount strobe* infonames "
              "infotimes infotime_pair acnames actimes actime_pair ckptperiod saveperiod saveclock savetime "
              "savefile recover circuitage noisefmax param paramset param_vec param_file sub annotate* title "
              "progress_* compression comp* complvl flush* fastbreak d2a* fastcross lteminstep ltethstep vref* "
              "iref* emir* autostop dcmaxiters save nestlvl").split()
_SWEEP_ARGS = "paramset faults* distribute numprocesses savedatainseparatedir annotate title".split()
_ANALYSIS_ARGS = {"dc": _DC_ARGS, "ac": _AC_ARGS, "noise": _NOISE_ARGS, "xf": _XF_ARGS, "tran": _TRAN_ARGS,
                  "sweep": _SWEEP_ARGS}
# parameters whose values are enumerations or names, kept as Str (a bare identifier is never a
# parameter reference here, §4.1 item 1)
_ENUM_ARGS = frozenset(("errpreset method relref annotate save oppoint force readforce homotopy ic skipdc useprevic "
                        "prevoppoint print restart swpuseprevic hysteresis check title perturbation separatenoise "
                        "stimuli compression infonames acnames write writefinal readns readic read valuesfile "
                        "savefile recover file what where dev mod sub param probe oprobe iprobe linearic autostop "
                        "fastbreak contriblist out1 out2 variations paramset param_file faults distribute "
                        "savedatainseparatedir readtime strobeoutput progress_output flushpoints saveprocessparams "
                        "donominal firstrun scramble sampling savefamilyplots appendsd").split())
_ENUM_OPTIONS = frozenset(("save rawfmt rawfile tempeffects currents useprobes useterms saveahdlvars subcktiprobes "
                           "savefilter saveports wfdebug homotopy limit newton compatible approx checklimitdest "
                           "psfversion topcheck sensfile audit opptcheck notation dochecklimit pwr "
                           "try_fast_op").split())
_OPTION_NOTE = frozenset(("homotopy newton limit maxdeltav gmin_start gmin_converge try_fast_op dcmaxiters pivrel "
                          "pivabs rforce residualtol digits cols checklimitdest sensfile psfversion topcheck "
                          "compatible approx icversion").split())
# the element-reduction options' silent defaults [M ref19 pp.196-197] (§3.10's table, as plan.py's
# REDUCTION_DEFAULTS): rcut, ccut, cgnd and cmax are infinite; dcut and qcut have none, so any value warns
_OPTION_REDUCTION: Dict[str, Optional[float]] = {
    "rabsshort": 0.0, "rcut": math.inf, "ccut": math.inf, "cgnd": math.inf, "cmax": math.inf, "cclamp": 0.0,
    "lshort": 0.0, "kcut": 0.0, "dcut": None, "qcut": None}
_OPTION_OUTPUT = frozenset(("save nestlvl currents subcktprobelvl pwr useprobes useterms saveahdlvars subcktiprobes "
                            "savefilter saveports wfdebug").split())
_OPTION_SILENT = frozenset("reltol vabstol iabstol chargeabstol temp tnom gmin rawfmt rawfile precision maxwarns "
                           "maxnotes multithread".split())
_SAVE_MODIFIERS = ("depth", "sigtype", "devtype", "subckt", "exclude", "probelvl", "time_window", "ports", "filter",
                   "compression")

# §3.8: master -> IR kind
_MASTER_KIND = {"resistor": "r", "capacitor": "c", "inductor": "l", "vsource": "v", "isource": "i", "iprobe": "v",
                "vcvs": "e", "vccs": "g", "ccvs": "h", "cccs": "f", "mutual_inductor": "k", "diode": "d",
                "bjt": "q", "jfet": "j", "mos1": "m", "mos2": "m", "mos3": "m", "bsim3v3": "m", "bsim4": "m"}
_MODEL_MASTERS = ("diode", "bjt", "jfet", "mos1", "mos2", "mos3", "bsim3v3", "bsim4")
_SOURCE_TYPES = ("dc", "pulse", "sine", "exp", "pwl")

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")
# a hierarchical instance name (X1.V2) where an analysis names an instance: noise iprobe=/oprobe= (§5.6:
# "a given iprobe= inside a subckt (X1.V2)") and xf probe=; dev=/mod=/sub= are read by sweep_spec
_HIER_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")
_INSTANCE_ARGS = frozenset(("iprobe", "oprobe", "probe"))
_PERCENT = re.compile(r"%([A-Za-z%])((?::[reht:])*)")
_LANG = re.compile(r"^\s*simulator\s+lang\s*=\s*(spectre|spice)\b", re.I)
_CPP_MARKER = re.compile(r'^#\s*(\d+)\s+"([^"]*)"((?:\s+\d+)*)\s*$')


# =============================================================================================
# The statement layer (§3.2)
# =============================================================================================

class _Syntax(Exception):
    def __init__(self, msg: str, pos: int = -1):
        super().__init__(msg)
        self.msg, self.pos = msg, pos


class _Line:
    """One logical line: continuations joined, comments dropped."""
    __slots__ = ("text", "start", "end", "lineno")

    def __init__(self, text: str, start: int, end: int, lineno: int):
        self.text, self.start, self.end, self.lineno = text, start, end, lineno


def _strip_comment(text: str) -> str:
    """'//' at the start or after white space, outside quotes, ends the line (UG p.59)."""
    i, n, quote = 0, len(text), False
    while i < n:
        c = text[i]
        if c == '"':
            quote = not quote
        elif c == "\\" and not quote:
            i += 1
        elif not quote and c == "/" and text.startswith("//", i) and (i == 0 or text[i - 1] in " \t"):
            return text[:i]
        i += 1
    return text


def _logical_lines(text: str, origin: str, first: int, start_offset: int, notes: List[Note]) -> List[_Line]:
    """The logical lines of text (which starts at line `first`, offset start_offset of the whole
    file): comments dropped, '\\' and '+' continuations joined (both work inside braces, UG p.59)."""
    out: List[_Line] = []
    phys = text.split("\n")
    pos = start_offset
    k = 0
    while k < len(phys):
        raw = phys[k]
        line_start, lineno = pos, first + k
        pos += len(raw) + 1
        k += 1
        s = raw.rstrip("\r")
        t = s.lstrip()
        if not t or t.startswith("*"):
            continue
        body = _strip_comment(s)
        end = line_start + len(s)
        # a backslash ending the line continues it on the next physical line
        while body.rstrip().endswith("\\") and not body.rstrip().endswith("\\\\") and k < len(phys):
            nxt = phys[k].rstrip("\r")
            end = pos + len(nxt)
            pos += len(phys[k]) + 1
            k += 1
            body = body.rstrip()[:-1] + " " + _strip_comment(nxt)
        if t.startswith("+"):
            cut = body.lstrip()[1:]
            if not out:
                notes.append(error("%s:%d" % (origin, lineno), "continuation line '+' without a statement "
                                                                "before it"))
                continue
            out[-1].text += " " + cut.strip()
            out[-1].end = end
            continue
        out.append(_Line(body.strip(), line_start + (len(s) - len(t)), end, lineno))
    return out


def _split_regions(text: str, lang: str) -> List[Tuple[str, int, int, Optional[str], int]]:
    """The language regions of a file (§3.1): (lang, first line index, end line index, the switch
    statement's text or None, the switch's line index), from 'simulator lang=' lines in either mode."""
    phys = text.split("\n")
    regions: List[Tuple[str, int, int, Optional[str], int]] = []
    cur_lang, cur_start, switch, switch_line = lang, 0, None, -1
    for k, raw in enumerate(phys):
        m = _LANG.match(raw)
        if m is None:
            continue
        regions.append((cur_lang, cur_start, k, switch, switch_line))
        cur_lang, cur_start, switch, switch_line = m.group(1).lower(), k + 1, raw.strip(), k
    regions.append((cur_lang, cur_start, len(phys), switch, switch_line))
    return regions


def _words(text: str) -> List[Tuple[str, int]]:
    """The white-space separated words of a statement, (word, offset); '( ) [ ]' groups are one
    word with their contents, '"..."' strings too, and a '\\' escapes the next character (UG p.60)."""
    out: List[Tuple[str, int]] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t":
            i += 1
            continue
        start = i
        opens: List[int] = []
        quote = False
        while i < n:
            c = text[i]
            if quote:
                if c == "\\" and i + 1 < n:
                    i += 2
                    continue
                if c == '"':
                    quote = False
                i += 1
                continue
            if c == '"':
                quote = True
            elif c == "\\" and i + 1 < n:
                i += 2
                continue
            elif c in "([":
                opens.append(i)
            elif c in ")]":
                if not opens:
                    raise _Syntax("unbalanced %r" % c, i)
                opens.pop()
            elif c in " \t" and not opens:
                break
            i += 1
        if opens:
            raise _Syntax("unclosed %r" % text[opens[-1]], opens[-1])
        if quote:
            raise _Syntax("unterminated string", start)
        out.append((text[start:i], start))
    return out


def _top_level_eq(word: str) -> int:
    """The offset of a '=' that is an assignment (not == <= >= != and not inside a group), else -1."""
    depth = 0
    for k, c in enumerate(word):
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == "=" and depth == 0:
            prev = word[k - 1] if k else ""
            nxt = word[k + 1] if k + 1 < len(word) else ""
            if prev in "<>!=" or nxt == "=":
                continue
            return k
    return -1


def _continues_value(word: str) -> bool:
    """A word that can only continue an expression (an operator, or ? :)."""
    return word[0] in "+-*/?:<>!&|~^," or word in ("<<", ">>", "**")


def _ends_open(word: str) -> bool:
    """A word after which an expression must continue (it ends with an operator)."""
    return word[-1] in "+-*/?:<>=!&|~^(," or word.endswith("**")


def _is_assignment(words: List[Tuple[str, int]], i: int) -> bool:
    w = words[i][0]
    eq = _top_level_eq(w)
    if eq >= 0:
        return True
    return i + 1 < len(words) and words[i + 1][0].startswith("=") and not words[i + 1][0].startswith("==") \
        and _IDENT.match(w) is not None


def _fields(text: str) -> List[Tuple[Optional[str], str, int]]:
    """(key, text, offset): 'key=value' assignments (with or without blanks around '=', the value
    running on while the next word continues the expression, so 'c=0.3374 / (6.2832 * bw * r0)' is
    one value) and bare words (key None)."""
    words = _words(text)
    out: List[Tuple[Optional[str], str, int]] = []
    i = 0
    while i < len(words):
        w, off = words[i]
        eq = _top_level_eq(w)
        if eq > 0:
            key, value = w[:eq], w[eq + 1:]
            i += 1
        elif eq == 0:
            if out and out[-1][0] is None:
                key = out.pop()[1]
                value = w[1:]
                i += 1
            else:
                raise _Syntax("'=' without a name before it", off)
        elif i + 1 < len(words) and words[i + 1][0].startswith("=") and not words[i + 1][0].startswith("=="):
            key, value = w, words[i + 1][0][1:]
            i += 2
        else:
            out.append((None, w, off))
            i += 1
            continue
        parts = [value] if value else []
        while i < len(words):
            nw = words[i][0]
            if _is_assignment(words, i):
                break
            if not parts:
                if nw.startswith("="):
                    break
                parts.append(nw)
                i += 1
                continue
            if _continues_value(nw) or _ends_open(parts[-1]) or (parts[-1][-1] == "?" or parts[-1][-1] == ":"):
                parts.append(nw)
                i += 1
                continue
            break
        out.append((key, " ".join(parts), off))
    return out


def _node_groups(words: List[str]) -> List[str]:
    """Instance node texts: bare words, or one or more '(...)' groups concatenated (§3.2)."""
    out: List[str] = []
    for w in words:
        if w.startswith("("):
            i = 0
            while i < len(w):
                if w[i] != "(":
                    raise _Syntax("unexpected %r in a node list" % w[i:])
                j = w.index(")", i)
                out.extend(w[i + 1:j].split())
                i = j + 1
        else:
            out.append(w)
    return out


class _Chunk:
    __slots__ = ("kind", "text", "start", "end", "lineno")

    def __init__(self, kind: str, text: str, start: int, end: int, lineno: int):
        self.kind, self.text, self.start, self.end, self.lineno = kind, text, start, end, lineno


def _chunks(line: _Line) -> List[_Chunk]:
    """A logical line split at its top-level braces and ';' (blocks, function bodies)."""
    out: List[_Chunk] = []
    text = line.text
    depth = 0
    quote = False
    seg_start = 0
    base = line.start

    def flush(upto: int) -> None:
        seg = text[seg_start:upto]
        if seg.strip():
            lead = len(seg) - len(seg.lstrip())
            out.append(_Chunk("stmt", seg.strip(), base + seg_start + lead, base + seg_start + len(seg.rstrip()),
                              line.lineno))

    i = 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\":
                i += 1
            elif c == '"':
                quote = False
        elif c == '"':
            quote = True
        elif c == "\\":
            i += 1
        elif c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif depth == 0 and c in "{};":
            flush(i)
            out.append(_Chunk({"{": "open", "}": "close", ";": "semi"}[c], c, base + i, base + i + 1, line.lineno))
            seg_start = i + 1
        i += 1
    flush(len(text))
    return out


def _unescape(name: str) -> str:
    """The name a written token denotes: a '\\' escapes the next character (UG p.60)."""
    if "\\" not in name:
        return name
    out: List[str] = []
    i = 0
    while i < len(name):
        if name[i] == "\\" and i + 1 < len(name):
            out.append(name[i + 1])
            i += 2
        else:
            out.append(name[i])
            i += 1
    return "".join(out)


class _Statements:
    """Builds Stmt trees from the chunks of one Spectre-language text."""

    def __init__(self, text: str, origin: str, title: bool, lang: str = "spectre"):
        self.text, self.origin = text, origin
        self.notes: List[Note] = []
        self.chunks: List[_Chunk] = []
        self.lang = lang
        self.k = 0
        self.title = title
        self.switch_follows = False

    def where(self, lineno: int) -> str:
        return "%s:%d" % (self.origin, lineno)

    def err(self, lineno: int, msg: str) -> None:
        self.notes.append(error(self.where(lineno), msg))

    def run(self) -> List[Stmt]:
        regions = _split_regions(self.text, self.lang)
        phys = self.text.split("\n")
        offsets = [0]
        for raw in phys:
            offsets.append(offsets[-1] + len(raw) + 1)
        out: List[Stmt] = []
        for k, (lang, start, end, switch, switch_line) in enumerate(regions):
            self.switch_follows = k + 1 < len(regions)
            if switch is not None and not (self.title and switch_line == 0):
                try:
                    out.append(self.parse_text(switch, switch_line + 1, offsets[switch_line],
                                               offsets[switch_line] + len(switch)))
                except _Syntax as exc:
                    self.err(switch_line + 1, exc.msg)
            if start == 0 and self.title:
                start = 1
            if start >= end:
                continue
            seg = "\n".join(phys[start:end])
            if lang == "spice":
                lead = len(seg) - len(seg.lstrip())
                if seg.strip():
                    out.append(Stmt("spice", origin=self.where(start + 1),
                                    span=(offsets[start] + lead, offsets[start] + len(seg.rstrip()))))
                continue
            lines = _logical_lines(seg, self.origin, start + 1, offsets[start], self.notes)
            self.chunks = []
            for ln in lines:
                try:
                    self.chunks.extend(_chunks(ln))
                except _Syntax as exc:
                    self.err(ln.lineno, exc.msg)
            self.k = 0
            out.extend(self.block(None))
        if any(n.severity == ERROR for n in self.notes):
            raise NoteError(self.notes)
        return out

    # -- the recursive statement builder

    def peek(self) -> Optional[_Chunk]:
        return self.chunks[self.k] if self.k < len(self.chunks) else None

    def block(self, closer: Optional[str]) -> List[Stmt]:
        """Statements up to a closing brace ("}"), an ends/endsection/endlibrary keyword, or the end
        of the text (closer None)."""
        out: List[Stmt] = []
        while True:
            c = self.peek()
            if c is None:
                if closer is not None and closer != "ends":
                    self.err(self.chunks[-1].lineno if self.chunks else 1,
                             "simulator lang= inside a { } block (an if, a sweep or a model group) is an error in v1 "
                             "(§3.1): the language switches before its }" if self.switch_follows
                             else "missing %s" % closer)
                return out
            if c.kind == "close":
                if closer == "}":
                    self.k += 1
                    return out
                self.err(c.lineno, "unexpected '}'")
                self.k += 1
                continue
            if c.kind == "open":
                self.err(c.lineno, "unexpected '{'")
                self.k += 1
                self.block("}")
                continue
            if c.kind == "semi":
                self.err(c.lineno, "unexpected ';' (only a function's return expression ends with one)")
                self.k += 1
                continue
            first = c.text.split(None, 1)[0] if c.text else ""
            if first in ("ends", "endsection", "endlibrary") and closer == first:
                return out
            try:
                st = self.statement()
            except _Syntax as exc:
                self.err(c.lineno, exc.msg)
                continue
            if st is not None:
                out.append(st)

    def statement(self) -> Optional[Stmt]:
        c = self.peek()
        assert c is not None and c.kind == "stmt"
        self.k += 1
        nxt = self.peek()
        opens = nxt is not None and nxt.kind == "open"
        try:
            st = self.parse_text(c.text, c.lineno, c.start, c.end)
        except _Syntax as exc:
            if opens:
                self.k += 1
                self.block("}")
            raise exc
        if st.kind == "subckt":
            st.children = self.block("ends")
            end = self.peek()
            if end is not None and end.text.split(None, 1)[0] == "ends":
                words = end.text.split()
                if len(words) > 1 and words[1] != st.name:
                    self.err(end.lineno, "ends %s closes subckt %s" % (words[1], st.name))
                st.span = (st.span[0], end.end)
                self.k += 1
            elif self.switch_follows:
                self.err(c.lineno, "simulator lang= inside subckt %s is an error in v1 (§3.1): the language "
                         "switches before its ends" % st.name)
            else:
                self.err(c.lineno, "subckt %s has no ends" % st.name)
            return st
        if st.kind in ("library", "section"):
            closer = "end" + st.kind
            st.children = self.block(closer)
            end = self.peek()
            if end is not None and end.text.split(None, 1)[0] == closer:
                words = end.text.split()
                if len(words) > 1 and words[1] != st.name:
                    self.err(end.lineno, "%s %s closes %s %s" % (closer, words[1], st.kind, st.name))
                st.span = (st.span[0], end.end)
                self.k += 1
            else:
                self.err(c.lineno, "%s %s has no %s" % (st.kind, st.name, closer))
            return st
        if st.kind == "if":
            return self.if_chain(st, c, opens)
        if st.kind == "else":
            raise _Syntax("else without if")
        if st.kind == "return":
            if nxt is None or nxt.kind != "semi":
                raise _Syntax("the return expression must end with ';' (UG p.92)")
            self.k += 1
            return st
        if opens:
            self.k += 1
            if st.kind == "function":
                body = self.block("}")
                st.params = [("return", self.function_body(body, c.lineno))]
            elif st.kind == "model":
                st.children = self.group_body(self.block("}"))
            elif st.kind in ("analysis", "altergroup", "statistics", "process", "mismatch"):
                st.children = self.block("}")
            else:
                self.err(c.lineno, "a %s statement does not take a { } block" % st.kind)
                self.block("}")
            last = self.chunks[self.k - 1] if self.k else c
            st.span = (st.span[0], last.end)
        elif st.kind == "function":
            raise _Syntax("a function needs a { return expression; } body")
        elif st.kind in ("statistics", "process", "mismatch"):
            raise _Syntax("%s needs a { } block" % st.kind)
        return st

    def if_chain(self, st: Stmt, c: _Chunk, opens: bool) -> Stmt:
        """if (c) {...} else if (c) {...} else {...}, or the brace-less one-statement forms (UG p.109)."""
        cond, rest = st.branches[0][0], st.master
        st.master = ""
        if opens:
            self.k += 1
            body = self.block("}")
        elif rest:
            inner = self.parse_inline(rest, c)
            body = [inner]
        else:
            raise _Syntax("if (...) needs a statement or a { } block")
        st.branches = [(cond, body)]
        last_end = self.chunks[self.k - 1].end if self.k else c.end
        while True:
            nxt = self.peek()
            if nxt is None or nxt.kind != "stmt" or nxt.text.split(None, 1)[0] != "else":
                break
            self.k += 1
            after = nxt.text[4:].strip()
            follow = self.peek()
            fopens = follow is not None and follow.kind == "open"
            if after.startswith("if") and (len(after) == 2 or not (after[2].isalnum() or after[2] == "_")):
                sub = self.parse_text(after, nxt.lineno, nxt.start + 5, nxt.end)
                cond2, rest2 = sub.branches[0][0], sub.master
                if fopens:
                    self.k += 1
                    body2 = self.block("}")
                elif rest2:
                    body2 = [self.parse_inline(rest2, nxt)]
                else:
                    raise _Syntax("else if (...) needs a statement or a { } block")
                st.branches.append((cond2, body2))
                last_end = self.chunks[self.k - 1].end if self.k else nxt.end
                continue
            if fopens:
                self.k += 1
                body3 = self.block("}")
            elif after:
                body3 = [self.parse_inline(after, nxt)]
            else:
                raise _Syntax("else needs a statement or a { } block")
            st.branches.append(("", body3))
            last_end = self.chunks[self.k - 1].end if self.k else nxt.end
            break
        st.span = (st.span[0], last_end)
        return st

    def parse_inline(self, text: str, c: _Chunk) -> Stmt:
        """The one statement of a brace-less if (itself possibly an if)."""
        at = c.text.find(text)
        st = self.parse_text(text, c.lineno, c.start + max(at, 0), c.end)
        if st.kind == "if":
            cond, rest = st.branches[0][0], st.master
            st.master = ""
            if not rest:
                raise _Syntax("a brace-less if needs a statement")
            st.branches = [(cond, [self.parse_inline(rest, c)])]
        elif st.kind not in ("instance", "paramtest"):
            raise _Syntax("an if holds instance statements, if statements and paramtests only (ref19 p.491), "
                          "not a %s statement" % st.kind)
        return st

    def function_body(self, body: List[Stmt], lineno: int) -> str:
        if len(body) != 1 or body[0].kind != "return":
            self.err(lineno, "a function body must be exactly one 'return expression;' statement (UG p.92)")
            return ""
        return body[0].params[0][1]

    def group_body(self, body: List[Stmt]) -> List[Stmt]:
        """model name master { k: params ... }: the entries are 'bin' statements (§3.9)."""
        out: List[Stmt] = []
        for st in body:
            if st.kind != "bin":
                self.err(int(st.origin.rsplit(":", 1)[-1]), "a model group entry is 'index: parameters', not a %s "
                                                              "statement" % st.kind)
                continue
            out.append(st)
        return out

    # -- one statement text

    def parse_text(self, text: str, lineno: int, start: int, end: int) -> Stmt:
        origin = self.where(lineno)
        span = (start, end)
        m = re.match(r"(\d+)\s*:\s*(.*)$", text, re.S)
        if m:                                                # a model-group entry
            st = Stmt("bin", m.group(1), origin=origin, span=span)
            st.params = self.keyed(_fields(m.group(2)))
            return st
        if text.startswith("return") and (len(text) == 6 or text[6] in " \t"):
            body = text[6:].strip()
            if not body:
                raise _Syntax("return needs an expression")
            return Stmt("return", params=[("return", body)], origin=origin, span=span)
        if text.startswith("if") and (len(text) == 2 or not (text[2].isalnum() or text[2] == "_")):
            return self.if_stmt(text, origin, span)
        if re.match(r"real\s", text):
            return self.function_stmt(text, origin, span)
        fields = _fields(text)
        if not fields:
            raise _Syntax("empty statement")
        key0, w0, _o0 = fields[0]
        if key0 is not None:
            raise _Syntax("a statement cannot start with an assignment (%s=)" % key0)
        if w0.startswith("("):
            raise _Syntax("the component name is missing before the node list")
        first = w0
        if first == "inline":
            if len(fields) < 2 or fields[1][1] != "subckt":
                raise _Syntax("inline must be followed by subckt")
            st = self.subckt_stmt(fields[2:], origin, span)
            st.params.append(("inline", "yes"))
            return st
        if first == "subckt":
            return self.subckt_stmt(fields[1:], origin, span)
        if first == "else":
            return Stmt("else", origin=origin, span=span)
        if first == "parameters":
            return Stmt("parameters", params=self.keyed(fields[1:]), origin=origin, span=span)
        if first == "model":
            if len(fields) < 3 or fields[1][0] is not None or fields[2][0] is not None:
                raise _Syntax("model needs a name and a master: model name master parameters")
            if fields[1][1].startswith("(") or fields[2][1].startswith("("):
                raise _Syntax("model takes no node list")
            return Stmt("model", fields[1][1], master=fields[2][1], params=self.keyed(fields[3:]), origin=origin,
                        span=span)
        if first == "global":
            if any(k is not None for k, _, _ in fields[1:]):
                raise _Syntax("global takes node names only")
            return Stmt("global", nodes=[w for _, w, _ in fields[1:]], origin=origin, span=span)
        if first in ("ic", "nodeset"):
            st = Stmt(first, origin=origin, span=span)
            for k, w, _ in fields[1:]:
                if k is None:
                    raise _Syntax("%s needs node=value assignments, not %r" % (first, w))
                if not w:
                    raise _Syntax("%s %s= has no value" % (first, k))
                st.params.append((k, w))
            if not st.params:
                raise _Syntax("%s needs at least one node=value" % first)
            return st
        if first == "save":
            st = Stmt("save", origin=origin, span=span)
            for k, w, _ in fields[1:]:
                if k is None:
                    if w.endswith(":") or w == ":":
                        raise _Syntax("save item %r: a ':' needs a terminal, index or modifier after it" % w)
                    st.nodes.append(w)
                else:
                    if k not in _SAVE_MODIFIERS:
                        raise _Syntax("save: %s= is not a save modifier (%s)" % (k, " ".join(_SAVE_MODIFIERS)))
                    if not w:
                        raise _Syntax("save %s= has no value" % k)
                    st.params.append((k, w))
            if not st.nodes:
                raise _Syntax("save needs at least one signal")
            return st
        if first in ("include", "ahdl_include"):
            rest = fields[1:]
            if not rest or rest[0][0] is not None or not (rest[0][1].startswith('"') and rest[0][1].endswith('"')
                                                         and len(rest[0][1]) >= 2):
                raise _Syntax("%s needs a quoted file name" % first)
            st = Stmt(first, rest[0][1][1:-1], origin=origin, span=span)
            for k, w, _ in rest[1:]:
                if k is None:
                    raise _Syntax("%s: unexpected %r" % (first, w))
                if first == "include" and k == "section":
                    if not w:
                        raise _Syntax("include: section= needs a name")
                    st.params.append((k, w))
                else:
                    raise _Syntax("%s: %s= is not an %s parameter" % (first, k, first))
            return st
        if first in ("library", "section"):
            if len(fields) < 2 or fields[1][0] is not None:
                raise _Syntax("%s needs a name" % first)
            if len(fields) > 2:
                raise _Syntax("%s: unexpected %r" % (first, fields[2][1]))
            return Stmt(first, fields[1][1], origin=origin, span=span)
        if first in ("endsection", "endlibrary", "ends"):
            raise _Syntax("%s without %s" % (first, {"ends": "subckt", "endsection": "section",
                                                      "endlibrary": "library"}[first]))
        if first == "simulator":
            return Stmt("simulator", params=self.keyed(fields[1:]), origin=origin, span=span)
        if first in ("statistics", "process", "mismatch"):
            if len(fields) > 1:
                raise _Syntax("%s takes no parameters" % first)
            return Stmt(first, origin=origin, span=span)
        if first == "vary":
            if len(fields) < 2 or fields[1][0] is not None:
                raise _Syntax("vary needs a parameter name")
            return Stmt("vary", fields[1][1], params=self.keyed(fields[2:]), origin=origin, span=span)
        if first in ("correlate", "truncate", "export"):
            return Stmt(first, params=self.keyed(fields[1:]), origin=origin, span=span)
        if first.lower() in ("protect", "prot", "unprotect", "unprot"):
            return Stmt("protect" if first.lower().startswith("prot") else "unprotect", origin=origin, span=span)
        if first in ("paramset", "end", "function"):
            raise _Syntax("the %s statement is not supported" % first)
        # name [(]nodes[)]... master params  (instance, analysis or named control statement)
        bare: List[Tuple[str, int]] = []
        i = 0
        while i < len(fields) and fields[i][0] is None:
            bare.append((fields[i][1], fields[i][2]))
            i += 1
        params = self.keyed(fields[i:])
        if len(bare) < 2:
            if len(bare) == 1 and params:
                raise _Syntax("%r needs a master (name [nodes] master parameters)" % bare[0][0])
            raise _Syntax("%r is not a Spectre statement (name [nodes] master parameters)" % text[:40])
        name = bare[0][0]
        master = bare[-1][0]
        if master.startswith("("):
            raise _Syntax("the master name is missing after the node list")
        nodes = _node_groups([w for w, _ in bare[1:-1]])
        if master in ANALYSES:
            kind = "analysis"
        elif master in CONTROLS:
            kind = master
        else:
            kind = "instance"
        return Stmt(kind, name, nodes, master, params, origin=origin, span=span)

    def keyed(self, fields: List[Tuple[Optional[str], str, int]]) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        for k, w, _ in fields:
            if k is None:
                raise _Syntax("unexpected %r where a name=value parameter was expected" % w)
            if not w:
                raise _Syntax("%s= has no value" % k)
            out.append((k, w))
        return out

    def subckt_stmt(self, fields, origin, span) -> Stmt:
        if not fields or fields[0][0] is not None or fields[0][1].startswith("("):
            raise _Syntax("subckt needs a name: subckt name [(]ports[)]")
        name = fields[0][1]
        ports: List[str] = []
        for k, w, _ in fields[1:]:
            if k is not None:
                raise _Syntax("subckt %s: unexpected %s=%s (parameters go on a 'parameters' line)" % (name, k, w))
            ports.extend(_node_groups([w]))
        return Stmt("subckt", name, ports, origin=origin, span=span)

    def if_stmt(self, text: str, origin: str, span: Tuple[int, int]) -> Stmt:
        rest = text[2:].lstrip()
        if not rest.startswith("("):
            raise _Syntax("if needs a parenthesized condition")
        depth = 0
        for i, c in enumerate(rest):
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    cond = rest[1:i].strip()
                    tail = rest[i + 1:].strip()
                    if not cond:
                        raise _Syntax("if ( ) has an empty condition")
                    # the brace-less statement travels in master until if_chain reads it
                    return Stmt("if", branches=[(cond, [])], master=tail, origin=origin, span=span)
            elif c in "{}":
                break
        raise _Syntax("if: the condition's '(' is never closed")

    def function_stmt(self, text: str, origin: str, span: Tuple[int, int]) -> Stmt:
        m = re.match(r"real\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*$", text, re.S)
        if m is None:
            raise _Syntax("a function is declared as real name( real a, real b ) { return expr; }")
        args: List[str] = []
        argtext = m.group(2).strip()
        if argtext:
            for a in argtext.split(","):
                words = a.split()
                if len(words) != 2 or words[0] != "real" or not _IDENT.match(words[1]):
                    raise _Syntax("function %s: argument %r must be 'real name' (UG p.92)" % (m.group(1), a.strip()))
                args.append(words[1])
        return Stmt("function", m.group(1), args, origin=origin, span=span)


def statements(text: str, origin: str, title: bool = True) -> List[Stmt]:
    """The statements of one Spectre-language text: no file access, no lowering; NoteError on a
    syntax error (§3.2).  title: line 1 is the title line (False for an included file)."""
    return _Statements(text, origin, title).run()


def _statements_lang(text: str, origin: str, title: bool, lang: str) -> List[Stmt]:
    return _Statements(text, origin, title, lang).run()


# =============================================================================================
# Sources (§3.8.1)
# =============================================================================================

_SOURCE_PARAMS = frozenset(("dc type fundname delay val0 val1 period rise fall width file wave offset scale stretch "
                            "allbrkpts pwlperiod twidth sinedc ampl freq sinephase ampl2 freq2 sinephase2 fundname2 "
                            "fmmodindex fmmodfreq ammodindex ammodfreq ammodphase damp td1 tau1 td2 tau2 noisefile "
                            "noisevec mag phase xfmag pacmag pacphase tc1 tc2 tnom dbm dbm2").split())
_TRANSRES = 1e-9


def _vec(v: Value) -> List[Expr]:
    return list(v) if isinstance(v, list) else [v]


def _one(v: Value) -> Expr:
    if isinstance(v, list):
        return v[0] if v else Num(0.0)
    return v


def _const(e: Optional[Expr]) -> Optional[float]:
    return e.value if isinstance(e, Num) else None


def _plus(a: Expr, b: Expr) -> Expr:
    if isinstance(a, Num) and a.value == 0:
        return b
    if isinstance(b, Num) and b.value == 0:
        return a
    return expr.fold(Binary("+", a, b))


def resolve_source(params: Mapping[str, Value], type: str, tran_stops: Sequence[float],
                   notes: List[Note]) -> Source:
    """§3.8.1's table: the parameters written on a vsource/isource (Spectre names) and its type into
    a Source with every HSPICE field resolved; tran_stops are the stop times of every tran
    (ir.flat_analyses) for the pulse edge default; plan.py reuses it for alters (§5.3)."""
    src = Source(spectre=dict(params))
    p = params

    def err(msg: str) -> None:
        notes.append(error("", msg))

    for key in p:
        if key not in _SOURCE_PARAMS:
            err("%s is not a vsource/isource parameter (ref5 pp.682-686)" % key)
    for key in ("pwlperiod", "twidth"):
        if key in p:
            err("%s=: a periodic PWL waveform is not supported in v1" % key)
    for key in ("ampl2", "freq2", "sinephase2", "fmmodindex", "fmmodfreq", "ammodindex", "ammodfreq", "ammodphase",
                "dbm", "dbm2"):
        if key in p:
            err("%s=: a second sinusoid, AM/FM modulation or a dBm amplitude is not supported in v1" % key)
    for key in ("tc1", "tc2"):
        if key in p and not (isinstance(p[key], Num) and p[key].value == 0):
            err("%s= on an independent source is not supported (no target); write 0" % key)
    if "file" in p:
        err("file=: the waveform file is read by the parser before resolve_source (internal)")
    if "dc" in p:
        src.dc = _one(p["dc"])
    if "mag" in p or "phase" in p:
        mag = _one(p["mag"]) if "mag" in p else Num(1.0)
        src.ac = (mag, _one(p["phase"]) if "phase" in p else Num(0.0))
    t = (type or "dc").lower()
    if t == "dc":
        return src
    stop = min(tran_stops) if tran_stops else None
    transres = Num(_TRANSRES * stop) if stop is not None else Num(_TRANSRES)
    if t == "pulse":
        src.wave = "pulse"
        args: Dict[str, Expr] = {"v1": _one(p.get("val0", Num(0.0))), "v2": _one(p.get("val1", Num(1.0))),
                                 "td": _one(p.get("delay", Num(0.0)))}
        for key, edge in (("rise", "tr"), ("fall", "tf")):
            e = _one(p[key]) if key in p else None
            if e is None or (isinstance(e, Num) and e.value == 0):
                args[edge] = transres
                if tran_stops:
                    notes.append(warning("", "%s is %s: Spectre's default edge, transres = 1e-9 x the shortest "
                                             "tran stop, %g s, is used (ref19 p.420; the default is undocumented, "
                                             "§14 q.20)" % (key, "absent" if e is None else "0", transres.value)))
            elif isinstance(e, Num):
                args[edge] = e
            else:
                args[edge] = Ternary(Binary("==", e, Num(0.0)), transres, e)
        args["pw"] = _one(p["width"]) if "width" in p else Num(1e30)
        if "period" in p:
            args["per"] = _one(p["period"])
            per, tr, tf, pw = (_const(args[k]) for k in ("per", "tr", "tf", "pw"))
            if None not in (per, tr, tf, pw) and per <= (tr + tf + pw) * (1 + 1e-12):
                err("pulse period %g is not longer than rise+fall+width = %g" % (per, tr + tf + pw))
        src.args = args
        return src
    if t == "sine":
        vo = _one(p["sinedc"]) if "sinedc" in p else (src.dc if src.dc is not None else Num(0.0))
        va = _one(p.get("ampl", Num(1.0)))
        freq = _one(p.get("freq", Num(0.0)))
        damp = _one(p.get("damp", Num(0.0)))
        phase = _one(p.get("sinephase", Num(0.0)))
        if isinstance(freq, Num) and freq.value == 0:
            if not (isinstance(damp, Num) and damp.value == 0):
                err("a sine with freq=0 and damp != 0 is not a constant; give the frequency")
                return src
            const = Binary("+", vo, Binary("*", va, Call("sin", (Binary("*", phase, Num(math.pi / 180.0)),))))
            src.dc = expr.fold(const)                 # a constant: folded into dc (§3.8.1)
            notes.append(note("", "type=sine with freq=0 is a constant: folded into dc (§3.8.1)"))
            return src
        src.wave = "sin"
        src.args = {"vo": vo, "va": va, "freq": freq, "td": _one(p.get("delay", Num(0.0))), "theta": damp,
                    "phase": phase}
        return src
    if t == "exp":
        src.wave = "exp"
        delay = _one(p.get("delay", Num(0.0)))
        for key in ("tau1", "tau2", "td2"):
            if key not in p:
                err("type=exp needs %s (Spectre documents no default, ref5 p.685)" % key)
                return src
        td1 = _one(p.get("td1", Num(0.0)))
        src.args = {"v1": _one(p.get("val0", Num(0.0))), "v2": _one(p.get("val1", Num(1.0))),
                    "td1": _plus(delay, td1), "tau1": _one(p["tau1"]), "td2": _plus(delay, _one(p["td2"])),
                    "tau2": _one(p["tau2"])}
        return src
    if t == "pwl":
        src.wave = "pwl"
        wave = _vec(p["wave"]) if "wave" in p else []
        if not wave or len(wave) % 2:
            err("type=pwl needs wave=[t0 v0 t1 v1 ...] with time-value pairs")
            return src
        stretch = _one(p.get("stretch", Num(1.0)))
        scale = _one(p.get("scale", Num(1.0)))
        offset = _one(p.get("offset", Num(0.0)))
        pts: List[Tuple[Expr, Expr]] = []
        for k in range(0, len(wave), 2):
            tt, vv = wave[k], wave[k + 1]
            if not (isinstance(stretch, Num) and stretch.value == 1):
                tt = expr.fold(Binary("*", tt, stretch))
            if not (isinstance(scale, Num) and scale.value == 1):
                vv = expr.fold(Binary("*", vv, scale))
            if not (isinstance(offset, Num) and offset.value == 0):
                vv = expr.fold(Binary("+", vv, offset))
            pts.append((tt, vv))
        times = [_const(tt) for tt, _ in pts]
        for k in range(1, len(times)):
            if times[k] is not None and times[k - 1] is not None and times[k] <= times[k - 1]:
                err("pwl time points must increase (%s then %s)" % (expr.to_text(pts[k - 1][0], "spectre"),
                                                                     expr.to_text(pts[k][0], "spectre")))
        src.points = pts
        src.args = {"td": _one(p.get("delay", Num(0.0)))}
        return src
    err("type=%s is not supported in v1 (dc pulse sine exp pwl)" % type)
    return src


# =============================================================================================
# parse(): files, declarations, lowering
# =============================================================================================

class _SpiceRegion:
    """A SPICE-mode region: logical statements (text, origin), includes spliced in (§4.3 Files)."""

    def __init__(self, lines: List[Tuple[str, str]], origin: str):
        self.lines = lines
        self.origin = origin


class _Scope:
    """The top level or one subckt definition while it is lowered."""

    def __init__(self, name: Optional[str], parent: Optional["_Scope"]):
        self.name, self.parent = name, parent
        self.items: List[object] = []                 # IR items in order
        self.params: List[Tuple[str, Expr, str, str]] = []   # (name, expr, origin, language)
        self.insts: Dict[str, Instance] = {}
        self.inline = False
        self.ports: List[str] = []
        self.spelling: Dict[str, str] = {}
        self.body: List[object] = self.items          # the Subckt's body list (shared) for subckt scopes


@dataclass
class _Decl:
    kind: str                     # subckt | model | param
    name: str
    insensitive: bool = False
    master: str = ""
    ports: Optional[int] = None
    params: List[str] = field(default_factory=list)
    origin: str = ""
    lang: str = "spectre"


class _Resolver:
    """spectre.py's complete first-phase table (§4.3): exact case first, then a definition marked
    case-insensitive whose lower-cased name matches (§3.1)."""

    def __init__(self) -> None:
        self.table: Dict[str, Dict[str, _Decl]] = {"subckt": {}, "model": {}, "param": {}}
        self.marked: Dict[str, Dict[str, List[_Decl]]] = {"subckt": {}, "model": {}, "param": {}}
        self.va: Dict[str, VaModule] = {}

    def add(self, d: _Decl) -> Optional[str]:
        """Record d; the error text for a second marked definition differing only in case."""
        self.table[d.kind].setdefault(d.name, d)
        if d.insensitive and d.kind != "param":
            lst = self.marked[d.kind].setdefault(d.name.lower(), [])
            if any(o.name != d.name for o in lst):
                return "%s %s and %s differ only in case, and both are case-insensitive definitions (%s)" % (
                    d.kind, lst[0].name, d.name, lst[0].origin)
            lst.append(d)
        return None

    def find(self, kind: str, name: str) -> Optional[_Decl]:
        d = self.table[kind].get(name)
        if d is not None:
            return d
        lst = self.marked[kind].get(name.lower())
        return lst[0] if lst else None

    # the spice.Resolver protocol
    def subckt(self, name: str) -> Optional[spice.Decl]:
        d = self.find("subckt", name)
        return spice.Decl("subckt", d.name, "", d.ports, list(d.params), d.origin) if d else None

    def model(self, name: str) -> Optional[spice.Decl]:
        d = self.find("model", name)
        return spice.Decl("model", d.name, d.master, origin=d.origin) if d else None

    def param(self, name: str) -> Optional[spice.Decl]:
        d = self.find("param", name)
        return spice.Decl("param", d.name, origin=d.origin) if d else None

    def va_module(self, name: str) -> Optional[VaModule]:
        return self.va.get(name.lower())


def _modifiers(val: str, mods: str) -> str:
    """§2.4's colon modifiers, chained left to right: :r root, :e extension, :h head, :t tail, :: colon."""
    out = val
    i = 0
    while i + 1 < len(mods):
        m = mods[i + 1]
        i += 2
        if m == "r":
            out = out[:out.rfind(".")] if "." in os.path.basename(out) else out
        elif m == "e":
            out = out[out.rfind(".") + 1:] if "." in os.path.basename(out) else ""
        elif m == "h":
            out = out[:out.rfind("/")] if "/" in out else "."
        elif m == "t":
            out = out[out.rfind("/") + 1:]
        elif m == ":":
            out = out + ":"
    return out


_MANTISSA = re.compile(r"(\d+\.?\d*|\.\d+)$")


def _exponent_sign(inner: str, start: int, i: int) -> bool:
    """inner[i] is a sign inside a number's exponent (150e-12, 1.5E+3): what precedes it is a mantissa
    and an e, and a digit follows (phase 1b: [150e-12 350e-12] has two elements, not four)."""
    if inner[i] not in "+-" or i < start + 2 or inner[i - 1] not in "eE":
        return False
    body = inner[start:i - 1].lstrip("+-")
    return bool(_MANTISSA.match(body)) and i + 1 < len(inner) and inner[i + 1].isdigit()


def _vector_elements(inner: str) -> List[str]:
    """§3.5: an element is a run of unary signs followed by a number, a name or a parenthesized
    expression, so [0.5 1 +p2 (sqrt(p2*p2))] has four elements and [1+p2] two; a sign inside a
    number's exponent (150e-12) continues the element."""
    out: List[str] = []
    i, n = 0, len(inner)
    while i < n:
        c = inner[i]
        if c in " \t":
            i += 1
            continue
        start = i
        while i < n and inner[i] in "+-":
            i += 1
        if i < n and inner[i] == "(":
            depth = 0
            while i < n:
                if inner[i] == "(":
                    depth += 1
                elif inner[i] == ")":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
        else:
            while i < n and (inner[i].isalnum() or inner[i] in "._%" or _exponent_sign(inner, start, i)):
                i += 1
            if inner[start:i].strip("+-") == "":
                raise ValueError("bad vector element at %r" % inner[start:])
        out.append(inner[start:i])
    return out


def _vector_names(text: str) -> List[str]:
    t = text.strip()
    if t.startswith("[") and t.endswith("]"):
        t = t[1:-1]
    return t.split()


def _vtext(e: Expr) -> str:
    """A value as a message spells it: an enumeration's word bare, an expression in Spectre syntax."""
    return e.text if isinstance(e, Str) else expr.to_text(e, "spectre")


def _allowed(key: str, allowed: Sequence[str]) -> bool:
    for a in allowed:
        if a.endswith("*"):
            if key.startswith(a[:-1]):
                return True
        elif key == a:
            return True
    return False


class _Parser:
    def __init__(self, cwd: str, opts: SpectreParseOpts):
        self.cwd = os.path.abspath(cwd)
        self.opts = opts
        self.notes: List[Note] = []
        self.search = [os.path.normpath(os.path.join(self.cwd, os.path.expanduser(d))) for d in opts.search]
        self.expanded: Dict[Tuple[str, Optional[str], str], str] = {}
        self.resolver = _Resolver()
        self.funcs: Dict[str, Tuple[Sequence[str], Expr]] = {}
        self.hdl: List[str] = []
        self.title = ""
        self.program: List[object] = []                 # Stmt | _SpiceRegion, top level
        self.top = _Scope(None, None)
        self.scopes: List[Tuple[_Scope, Subckt]] = []
        self.values: Dict[str, float] = {}
        self.options: Dict[str, Expr] = {}
        self.option_origin: Dict[str, str] = {}
        self.spelling: Dict[str, str] = {}
        self.globals: List[str] = []
        self.ground = "0"
        self.ground_set = False
        self.analyses: List[Analysis] = []
        self.saves: List[Stmt] = []
        self.save_specs: List[SaveSpec] = []
        self.spice_saves: List[SaveSpec] = []
        self.ics: Dict[str, Union[float, Expr]] = {}
        self.nodesets: Dict[str, Union[float, Expr]] = {}
        self.statistics: List[StatBlock] = []
        self.left_out: Dict[str, Note] = {}
        self.spice_counts: Dict[str, int] = {}
        self.insensitive_now = False
        self.card_folds: Dict[str, Dict[str, Expr]] = {}   # card name -> folded card parameters
        self.xrefs: List[Tuple[Instance, _Decl, str]] = []
        self.va_card_refs: List[Tuple[Instance, str]] = []
        self.probe_refs: List[Tuple[Instance, str, _Scope]] = []
        self.top_sorted: List[Param] = []
        self.tran_stops: List[float] = []
        self.dev_dc_defaults: List[Tuple[SweepSpec, str, str, str]] = []   # dev= sweeps without param= (UG p.168)
        self.top_lang = "spectre"                        # the top-level netlist's own language (§3.1, §4.3 allpub)
        self.top_origin = ""
        self.mode_stack: List[bool] = []                 # insensitive= states saved around includes (§3.1)
        self.popts = ParseOpts(dialect="spectre-spice", case="lower", parhier_local=True, search=list(opts.search))

    # -- notes --------------------------------------------------------------------------------

    def err(self, origin: str, msg: str) -> None:
        self.notes.append(error(origin, msg))

    def warn(self, origin: str, msg: str) -> None:
        self.notes.append(warning(origin, msg))

    def note(self, origin: str, msg: str) -> None:
        self.notes.append(note(origin, msg))

    def warner(self, origin: str) -> Callable[[str, str], None]:
        def w(severity: str, msg: str) -> None:
            self.notes.append(Note(severity, origin, msg))
        return w

    # =========================================================================================
    # Phase 1a: files (§3.1)
    # =========================================================================================

    def language_of(self, name: str, text: str, top: bool) -> str:
        """§3.1: .scs, or a first statement 'simulator lang=spectre' (line 1 of the top file still
        selects the mode, with a note), else SPICE.  Blank and comment lines before it are skipped."""
        if name.lower().endswith(".scs"):
            return "spectre"
        for k, raw in enumerate(text.split("\n")):
            t = raw.strip()
            if not t or t.startswith("//") or t.startswith("*"):
                continue
            m = _LANG.match(raw)
            if m is not None and m.group(1).lower() == "spectre":
                if top and k == 0:
                    self.note("%s:1" % name, "line 1 is the title, but its 'simulator lang=spectre' still selects "
                                             "Spectre mode for the file (§3.1)")
                return "spectre"
            break
        return "spice"

    def read_text(self, path: str, origin: str) -> Optional[str]:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            self.err(origin, "cannot read %s: %s" % (path, exc))
            return None
        if text.startswith("﻿"):
            text = text[1:]
        return text

    def resolve_path(self, text: str, base: Optional[str], origin: str, what: str,
                     cwd_first: bool = False) -> Optional[str]:
        """§3.1: an absolute path as is; a relative one against the including file's directory,
        then each -I directory (the cwd first only for a waveform or values file); ~ $VAR ${VAR}
        and %-codes expanded; the error lists every path tried."""
        p = os.path.expanduser(os.path.expandvars(self.percent(text)))
        if os.path.isabs(p):
            cands = [os.path.normpath(p)]
        else:
            cands = []
            if cwd_first:
                cands.append(os.path.normpath(os.path.join(self.cwd, p)))
            if base:
                cands.append(os.path.normpath(os.path.join(base, p)))
            cands.extend(os.path.normpath(os.path.join(d, p)) for d in self.search)
        seen: List[str] = []
        for c in cands:
            if c not in seen:
                seen.append(c)
        for c in seen:
            if os.path.isfile(c):
                return os.path.abspath(c)
        self.err(origin, "%s %s not found; tried %s" % (what, text, ", ".join(seen) or "(no directory)"))
        return None

    def percent(self, text: str) -> str:
        """%-codes in a quoted string or an include name (§2.4) from opts.percent; %% is %."""
        if "%" not in text:
            return text

        def sub(m) -> str:
            code, mods = m.group(1), m.group(2)
            if code == "%":
                return "%"
            val = self.opts.percent.get(code)
            if val is None:
                return m.group(0)
            return _modifiers(val, mods)
        return _PERCENT.sub(sub, text)

    def read_top(self, path: str) -> None:
        for f in self.opts.pre:
            self.program.extend(self.read_fragment(f))
        name = self.opts.top_name if self.opts.top_name else path
        text = self.read_text(path, name)
        if text is None:
            return
        base = self.opts.top_dir if self.opts.top_dir else os.path.dirname(os.path.abspath(path))
        if self.opts.cpp_markers:
            self.program.extend(self.read_cpp(text, name))
        else:
            lines = text.split("\n")
            self.title = lines[0].rstrip("\r").strip() if lines else ""
            lang = self.language_of(name, text, True)
            self.top_lang, self.top_origin = lang, "%s:1" % name
            self.program.extend(self.read_units(text, name, base, lang, True, None))
        for f in self.opts.post:
            self.program.extend(self.read_fragment(f))

    def read_fragment(self, path: str) -> List[object]:
        """+pre_config / +config: a Spectre-mode fragment without a title (§3.1)."""
        full = os.path.normpath(os.path.join(self.cwd, path))
        text = self.read_text(full, full)
        if text is None:
            return []
        return self.read_units(text, full, os.path.dirname(full), "spectre", False, None)

    def read_cpp(self, text: str, top_name: str) -> List[object]:
        """cpp output (§2.6): '# <line> "<file>" [flags]' markers give each line its origin, each
        file its language and include directory; flag 1 enters a file, 2 returns to the includer.
        The title is opts.title (line 1 of the original file)."""
        self.title = self.opts.title or ""
        done: List[Tuple[str, int, List[str]]] = []
        cur: Optional[Tuple[str, int, List[str]]] = None
        for raw in text.split("\n"):
            m = _CPP_MARKER.match(raw)
            if m is None:
                if cur is not None:
                    cur[2].append(raw)
                continue
            lineno, fname = int(m.group(1)), m.group(2)
            if fname.startswith("<"):                           # <built-in>, <command-line>
                continue
            cur = (fname, lineno, [])
            done.append(cur)
        out: List[object] = []
        top = done[0][0] if done else top_name
        for fname, first, lines in done:
            if not any(l.strip() for l in lines):
                continue
            is_top = fname == top
            body = "\n".join(lines)
            # §1: the markers of a netlist read from stdin name its copy in the run directory; its
            # statements carry opts.top_name (stdin:<line>) and its includes resolve against opts.top_dir
            origin = self.opts.top_name if (is_top and self.opts.top_name) else fname
            if is_top:
                probe = (self.title + "\n" + body) if first > 1 else body
                lang = self.language_of(origin, probe, first <= 1)
                if first <= 1 or not self.top_origin:
                    self.top_lang, self.top_origin = lang, "%s:1" % origin
            else:
                lang = self.language_of(fname, body, False)
            if is_top and self.opts.top_dir:
                base = self.opts.top_dir
            else:
                base = os.path.dirname(os.path.normpath(os.path.join(self.cwd, fname)))
            out.extend(self.read_units(body, origin, base, lang, is_top and first <= 1, None, first_line=first))
        return out

    def read_units(self, text: str, origin: str, base: str, lang: str, title: bool, scope_name: Optional[str],
                   first_line: int = 1, section: Optional[str] = None) -> List[object]:
        """One file's items: its Spectre statements (includes expanded) and SPICE regions."""
        if first_line > 1:
            text = "\n" * (first_line - 1) + text
        try:
            stmts = _statements_lang(text, origin, title, lang)
        except NoteError as exc:
            self.notes.extend(exc.notes)
            return []
        if section is not None:
            stmts = self.section_of(stmts, section, origin)
        elif any(s.kind == "library" for s in stmts):
            libs = [s for s in stmts if s.kind == "library"]
            secs = [c.name for lib in libs for c in lib.children if c.kind == "section"]
            self.err(libs[0].origin, "%s defines a library (sections: %s); include it with section=<name> (§3.1)"
                     % (origin, " ".join(secs) or "none"))
            return []
        return self.expand(stmts, text, origin, base, scope_name)

    def section_of(self, stmts: List[Stmt], section: str, origin: str) -> List[Stmt]:
        for s in stmts:
            if s.kind == "library":
                for c in s.children:
                    if c.kind == "section" and c.name == section:
                        return c.children
        self.err(origin, "the library file %s has no section %s" % (origin, section))
        return []

    def expand(self, stmts: List[Stmt], text: str, origin: str, base: str, scope_name: Optional[str]) -> List[object]:
        """Includes and SPICE regions spliced in, in place, per scope (§3.1, §4.3 Files)."""
        out: List[object] = []
        for st in stmts:
            if st.kind == "include":
                out.extend(self.include(st, base, scope_name))
            elif st.kind == "spice":
                if scope_name is not None:
                    self.err(st.origin, "simulator lang=spice inside subckt %s is an error in v1 (§3.1)" % scope_name)
                    continue
                seg = text[st.span[0]:st.span[1]]
                first_line = int(st.origin.rsplit(":", 1)[1])
                out.extend(self.spice_region(seg, origin, first_line, base))
            elif st.kind == "simulator":
                self.simulator(st, scope_name)
                out.append(st)
            elif st.kind == "ahdl_include":
                self.ahdl_include(st, base)
            elif st.kind == "subckt":
                st.children = [c for c in self.expand(st.children, text, origin, base, _unescape(st.name))
                               if isinstance(c, Stmt)]
                out.append(st)
            elif st.kind == "if":
                self.check_if_contents(st)
                out.append(st)
            elif st.kind in ("library", "section"):
                self.err(st.origin, "a %s statement belongs in a library file read with include section= (§3.1)" % st.kind)
            else:
                out.append(st)
        return out

    def check_if_contents(self, st: Stmt) -> None:
        for _cond, body in st.branches:
            for c in body:
                if c.kind == "simulator":
                    self.err(c.origin, "simulator lang= inside an if statement is an error in v1 (§3.1)")
                elif c.kind == "include":
                    self.err(c.origin, "include inside an if statement is not supported")
                elif c.kind == "if":
                    self.check_if_contents(c)

    def simulator(self, st: Stmt, scope_name: Optional[str]) -> None:
        if scope_name is not None:
            self.err(st.origin, "simulator lang= inside subckt %s is an error in v1 (§3.1)" % scope_name)
        for k, v in st.params:
            if k == "lang":
                if v not in ("spectre", "spice"):
                    self.err(st.origin, "simulator lang=%s: spectre or spice" % v)
            elif k == "insensitive":
                if v not in ("yes", "no"):
                    self.err(st.origin, "simulator insensitive=%s: yes or no" % v)
            else:
                self.err(st.origin, "simulator %s=: only lang and insensitive are simulator parameters (§3.1)" % k)

    def include(self, st: Stmt, base: str, scope_name: Optional[str]) -> List[object]:
        section = dict(st.params).get("section")
        path = self.resolve_path(st.name, base, st.origin, "include file")
        if path is None:
            return []
        key = (os.path.realpath(path), section, scope_name or "")
        if key in self.expanded:
            self.note(st.origin, "%s%s already read at %s in this scope; not read again"
                      % (path, " section %s" % section if section else "", self.expanded[key]))
            return []
        self.expanded[key] = st.origin
        text = self.read_text(path, st.origin)
        if text is None:
            return []
        lang = self.language_of(path, text, False)
        if lang == "spice" and scope_name is not None:
            self.err(st.origin, "include of the SPICE-language file %s inside subckt %s is an error in v1 (a "
                     "language switch inside a subckt body, §3.1)" % (path, scope_name))
            return []
        items = self.read_units(text, path, os.path.dirname(path), lang, False, scope_name, section=section)
        # the includer's mode is restored after an include (§3.1): the language is structural (each file's
        # statements are read in its own language), the insensitive= state is saved and restored around the
        # file by these markers, which declare_stmt and lower read; the included file starts in the
        # includer's state (a mode holds until the next switch)
        items.insert(0, Stmt("simulator", params=[("vamos_mode", "push")], origin=st.origin))
        items.append(Stmt("simulator", params=[("vamos_mode", "pop")], origin=st.origin))
        return items

    def ahdl_include(self, st: Stmt, base: str) -> None:
        path = self.resolve_path(st.name, base, st.origin, "Verilog-A file")
        if path is None:
            return
        self.add_hdl(path, st.origin)

    def add_hdl(self, path: str, origin: str) -> None:
        if path not in self.hdl:
            self.hdl.append(path)
        search = [os.path.normpath(os.path.join(self.cwd, d)) for d in self.opts.va_include] + self.search
        mods, files = spice.spectre_va_modules(path, search, self.opts.va_defines)
        if not mods:
            self.err(origin, "%s declares no Verilog-A module (files read: %s)" % (path, ", ".join(files)))
        for low, vm in mods.items():
            if low in self.resolver.va and self.resolver.va[low].path != vm.path:
                self.err(origin, "Verilog-A module %s is declared in %s and %s" % (vm.name, self.resolver.va[low].path,
                                                                                   vm.path))
            self.resolver.va.setdefault(low, vm)
            if vm.params is None:
                self.note(origin, "Verilog-A module %s: its parameter list is unknown (%s); parameter names pass "
                          "unchecked and the engine reports a wrong one (§3.1)"
                          % (vm.name, vm.attrs.get("vamos_unknown_params", "preprocessor")))
            else:
                seen: Dict[str, str] = {}
                for pname in vm.params:
                    other = seen.setdefault(pname.lower(), pname)
                    if other != pname:
                        self.err(origin, "Verilog-A module %s declares parameters %s and %s, which differ only in "
                                 "case (§3.9)" % (vm.name, other, pname))

    # -- SPICE regions (§4.3 Files)

    def spice_region(self, seg: str, origin: str, first_line: int, base: str) -> List[object]:
        """A SPICE-mode region: logical statements, its file statements spliced in (whole files or
        .lib sections) before the second phase; a Spectre-language file named by a SPICE include
        splits the region (§3.1, §4.3 Files)."""
        phys = [(line, "%s:%d" % (origin, first_line + k)) for k, line in enumerate(seg.split("\n"))]
        stmts, notes = spice._logical(phys, "spectre-spice")
        self.notes.extend(notes)
        # a .lib section defined in the region itself (.lib sec ... .endl) is indexed by spice._File and
        # skipped, as spice.py skips the definitions of a file it reads whole; it is read when a
        # .lib 'file' sec names it (§3.1, §4.3 Files)
        f = spice._File(origin, stmts)
        self.notes.extend(f.notes)
        lines: List[Tuple[str, str]] = []
        i = 0
        while i < len(stmts):
            if i in f.skip:
                i = f.skip[i] + 1
                continue
            lines.append((stmts[i].text, stmts[i].origin))
            i += 1
        return self.splice(lines, base, 0)

    def splice(self, lines: List[Tuple[str, str]], base: str, depth: int) -> List[object]:
        if not lines:
            return []
        if depth > 64:
            self.err(lines[0][1], "SPICE includes nest deeper than 64")
            return [_SpiceRegion(lines, lines[0][1])]
        _decls, refs = spice.declare_fragment(lines, self.popts)
        if not refs:
            return [_SpiceRegion(lines, lines[0][1])]
        ref = refs[0]
        idx = next((i for i, (_t, o) in enumerate(lines) if o == ref.origin), None)
        if idx is None:
            return [_SpiceRegion(lines, lines[0][1])]
        ref_file = spice._file_of(ref.origin)
        ref_base = os.path.dirname(os.path.normpath(os.path.join(self.cwd, ref_file))) if ref_file and \
            os.path.isfile(os.path.normpath(os.path.join(self.cwd, ref_file))) else base
        before, after = lines[:idx], lines[idx + 1:]
        if ref.keyword == ".hdl":
            path = self.resolve_path(ref.path, ref_base, ref.origin, "Verilog-A file")
            if path is not None:
                self.add_hdl(path, ref.origin)
                self.note(ref.origin, ".hdl is read as ahdl_include (§3.13; §14 q.28)")
            return self.splice(before + after, base, depth + 1)
        if ref.keyword == ".lib" and ref.path == "":
            self.err(ref.origin, ".lib %s inside a section (a call to another section of the same file) is not "
                     "supported; write .lib 'file' %s" % (ref.section, ref.section))
            return self.splice(before + after, base, depth + 1)
        path = self.resolve_path(ref.path, ref_base, ref.origin, ".lib file" if ref.section else "include file")
        if path is None:
            return self.splice(before + after, base, depth + 1)
        key = (os.path.realpath(path), ref.section, ref.subckt)
        if key in self.expanded:
            self.note(ref.origin, "%s%s already read at %s in this scope; not read again"
                      % (path, " section %s" % ref.section if ref.section else "", self.expanded[key]))
            return self.splice(before + after, base, depth + 1)
        self.expanded[key] = ref.origin
        text = self.read_text(path, ref.origin)
        if text is None:
            return self.splice(before + after, base, depth + 1)
        if self.language_of(path, text, False) == "spectre":
            if ref.section is not None:
                self.err(ref.origin, ".lib names the Spectre-language file %s; use include \"%s\" section=%s (§3.1)"
                         % (path, ref.path, ref.section))
                return self.splice(before + after, base, depth + 1)
            if ref.subckt:
                self.err(ref.origin, "%s names the Spectre-language file %s inside .subckt %s: a language switch "
                         "inside a subckt body is an error in v1 (§3.1)" % (ref.keyword, path, ref.subckt))
                return self.splice(before + after, base, depth + 1)
            out: List[object] = []
            out.extend(self.splice(before, base, depth + 1))        # the Spectre file ends the region (§3.1)
            out.extend(self.read_units(text, path, os.path.dirname(path), "spectre", False, None))
            out.extend(self.splice(after, base, depth + 1))
            return out
        file_lines = self.spice_file_lines(path, text, ref.section, ref.origin)
        return self.splice(before + file_lines + after, base, depth + 1)

    def spice_file_lines(self, path: str, text: str, section: Optional[str], origin: str) -> List[Tuple[str, str]]:
        """A SPICE file's logical statements (whole: its .lib section definitions skipped; or one
        section), each with its own file:line origin, up to .end (spice._File indexes the sections)."""
        phys = [(line, "%s:%d" % (path, k + 1)) for k, line in enumerate(text.split("\n"))]
        stmts, notes = spice._logical(phys, "spectre-spice")
        for n in notes:
            if n.message.startswith("continuation line '+' without"):
                self.err(n.origin, "continuation line '+' at the start of %s would continue the statement at %s: a "
                         "statement cannot continue across a file boundary (UG p.73)" % (path, origin))
            else:
                self.notes.append(n)
        f = spice._File(path, stmts)
        self.notes.extend(f.notes)
        if section is not None:
            if section not in f.sections:
                self.err(origin, ".lib %s: %s has no section %s%s" % (section, path, section,
                                                                       " (sections: %s)" % " ".join(sorted(f.sections))
                                                                       if f.sections else ""))
                return []
            i, end, _o = f.sections[section]
            skip: Dict[int, int] = {}
        else:
            i, end, skip = 0, len(stmts), f.skip
        out: List[Tuple[str, str]] = []
        while i < end:
            if i in skip:
                i = skip[i] + 1
                continue
            st = stmts[i]
            if st.kw == ".end":
                break
            out.append((st.text, st.origin))
            i += 1
        return out

    # =========================================================================================
    # Phase 1b: declarations (§4.3 Two phases)
    # =========================================================================================

    def declare_all(self) -> None:
        self.insensitive_now = False
        self.mode_stack = []
        for item in self.program:
            if isinstance(item, _SpiceRegion):
                decls, _refs = spice.declare_fragment(item.lines, self.popts)
                for d in decls:
                    self.add_decl(_Decl(d.kind, d.name, True, d.master, d.ports, list(d.params), d.origin, "spice"))
            else:
                self.declare_stmt(item)

    def declare_stmt(self, st: Stmt) -> None:
        if st.kind == "simulator":
            self.simulator_mode(st)
        elif st.kind == "subckt":
            header = [c for c in st.children if c.kind == "parameters"]
            params = [k for c in header for k, _ in c.params]
            self.add_decl(_Decl("subckt", _unescape(st.name), self.insensitive_now, "", len(st.nodes), params,
                                st.origin))
            saved = self.insensitive_now
            for c in st.children:
                self.declare_stmt(c)
            self.insensitive_now = saved
        elif st.kind == "model":
            self.add_decl(_Decl("model", _unescape(st.name), self.insensitive_now, st.master, origin=st.origin))
        elif st.kind == "parameters":
            for k, _ in st.params:
                self.add_decl(_Decl("param", k, False, origin=st.origin))
        elif st.kind == "if":
            for _cond, body in st.branches:
                for c in body:
                    self.declare_stmt(c)

    def add_decl(self, d: _Decl) -> None:
        msg = self.resolver.add(d)
        if msg:
            self.err(d.origin, msg)

    def simulator_mode(self, st: Stmt) -> None:
        """A simulator statement's insensitive= switch, and include()'s push/pop markers, which save the
        includer's state and restore it after the included file (§3.1)."""
        for k, v in st.params:
            if k == "insensitive":
                self.insensitive_now = v == "yes"
            elif k == "vamos_mode":
                if v == "push":
                    self.mode_stack.append(self.insensitive_now)
                else:
                    self.insensitive_now = self.mode_stack.pop() if self.mode_stack else False

    # =========================================================================================
    # Phase 2: lowering
    # =========================================================================================

    def lower_all(self) -> None:
        self.insensitive_now = False
        self.mode_stack = []
        for item in self.program:
            if isinstance(item, _SpiceRegion):
                self.lower_spice(item)
            else:
                self.lower(self.top, item, None)

    # -- expressions

    def expr(self, text: str, origin: str) -> Optional[Expr]:
        try:
            return expr.parse(text, "sensitive", "spectre", funcs=self.funcs, warn=self.warner(origin))
        except expr.ExprError as exc:
            self.err(origin, "cannot read %s: %s" % (text, exc))
            return None

    def value(self, text: str, origin: str, key: str = "") -> Optional[Value]:
        """A parameter value (§4.1 item 11): a number or expression (Expr), a vector (List[Expr]) or
        a quoted string (Str)."""
        t = text.strip()
        if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
            return Str(self.percent(t[1:-1]))
        if t.startswith("["):
            if not t.endswith("]"):
                self.err(origin, "%s: vector %s is not closed" % (key, text))
                return None
            out: List[Expr] = []
            try:
                elements = _vector_elements(t[1:-1])
            except ValueError as exc:
                self.err(origin, "%s: %s" % (key, exc))
                return None
            for el in elements:
                e = self.expr(el, origin)
                if e is None:
                    return None
                out.append(e)
            return out
        return self.expr(t, origin)

    def param_value(self, key: str, text: str, origin: str) -> Optional[Value]:
        """An instance or card parameter value: a bare word written for an enumeration parameter of the
        v1 masters (type=n, isnoisy=no, capmod=meyer, struct=vertical; spice.spectre_enum_params) is
        its Str (§4.1 item 11: enumerations as Str, so that plan.build never evaluates it and the
        emitters read it as text); every other value is an expression, a vector or a quoted string."""
        t = text.strip()
        if key in spice.spectre_enum_params() and _IDENT.match(t) and t not in ("temp", "tnom") and                 t not in expr.SPECTRE_CONSTANTS:
            return Str(t)
        return self.value(text, origin, key)

    def enum_or_value(self, key: str, text: str, origin: str, enums: frozenset) -> Optional[Value]:
        t = text.strip()
        if key in enums and _IDENT.match(t):
            return Str(t)
        if key in _INSTANCE_ARGS and key in enums and _HIER_IDENT.match(t):
            return Str(t)                               # iprobe=I2.vb: an IR instance path (§5.6)
        if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
            return Str(self.percent(t[1:-1]))
        if t.startswith("["):
            return self.value(t, origin, key)
        try:
            e = expr.parse(t, "sensitive", "spectre", funcs=self.funcs, warn=self.warner(origin))
        except expr.ExprError as exc:
            if _IDENT.match(t) and self.resolver.find("param", t) is None:
                return Str(t)                       # an enumeration value outside the known keys
            self.err(origin, "cannot read %s=%s: %s" % (key, text, exc))
            return None
        if isinstance(e, Name) and _IDENT.match(t) and e.name not in ("temper", "$tnom") and \
                self.resolver.find("param", t) is None:
            return Str(t)                           # a bare word that names no parameter: an enumeration
        return e

    # -- names

    def check_name(self, name: str, what: str, origin: str) -> bool:
        if name in KEYWORDS or name in RESERVED_WORDS:
            self.err(origin, "%s is a Spectre keyword or reserved word; it cannot name %s (ref19 p.495)"
                     % (name, what))
            return False
        if name in expr.SPECTRE_CONSTANTS:
            self.err(origin, "%s is a built-in constant; it cannot name anything (ref19 p.495)" % name)
            return False
        return True

    def ir_name(self, written: str) -> str:
        name = _unescape(written)
        if name != written:
            self.spelling.setdefault(name, written)
        return name

    def node(self, written: str, origin: str, terminal: bool = True) -> Optional[str]:
        name = _unescape(written)
        if name in expr.SPECTRE_CONSTANTS:
            self.err(origin, "%s is a built-in constant; it cannot name a node (ref19 p.495)" % name)
            return None
        if terminal and "." in written.replace("\\.", ""):
            self.err(origin, "node %s: a hierarchical node name on an instance terminal is an error in v1 (§3.4)"
                     % written)
            return None
        if name != written:
            self.spelling.setdefault(name, written)
        return name

    def resolve_ref(self, kind: str, written: str) -> Optional[str]:
        """An alter target, sweep mod= or save subckt= to its IR name (§3.1)."""
        d = self.resolver.find(kind, _unescape(written))
        return d.name if d is not None else None

    # -- statements

    def lower(self, scope: _Scope, st: Stmt, branch: Optional[List[object]]) -> None:
        """One Spectre statement into scope (branch: the if branch's item list when inside one)."""
        k = st.kind
        if branch is not None and k not in ("instance", "if", "paramtest"):
            self.err(st.origin, "a %s statement inside an if is an error: an if holds instances, nested ifs "
                     "and paramtests only (ref19 p.491)" % k)
            return
        if scope is not self.top and (k in ("function", "global", "save", "ic", "nodeset", "analysis", "statistics")
                                      or k in CONTROLS):
            if k == "options":
                if not self.opts.mts:
                    self.note(st.origin, "%s options inside subckt %s: global under -mts (§3.6)" % (st.name, scope.name))
                    self.options_stmt(st)
                else:
                    self.err(st.origin, "%s options inside subckt %s: Spectre's multi-technology mode scopes temp, "
                             "tnom, scale and scalem given there to the subckt, which v1 does not model (an error "
                             "in v1; -mts makes them global, §3.6)" % (st.name, scope.name))
            elif k == "paramtest":
                self.paramtest(scope, st, branch)
            elif k == "analysis" or k in CONTROLS:
                self.err(st.origin, "%s %s inside subckt %s: analyses and control statements belong at the top "
                         "level" % (st.name, st.master or k, scope.name))
            else:
                self.err(st.origin, "%s inside subckt %s: only the top level may hold it" % (k, scope.name))
            return
        if k == "instance":
            self.instance(scope, st, branch)
        elif k == "parameters":
            self.parameters(scope, st)
        elif k == "model":
            self.model(scope, st)
        elif k == "subckt":
            self.subckt(scope, st)
        elif k == "if":
            self.cond(scope, st, branch)
        elif k == "function":
            self.function(st)
        elif k == "global":
            self.global_stmt(st)
        elif k in ("ic", "nodeset"):
            self.ic(st)
        elif k == "save":
            self.saves.append(st)
        elif k == "options":
            self.options_stmt(st)
        elif k == "analysis":
            a = self.analysis(st)
            if a is not None:
                self.analyses.append(a)
        elif k == "set":
            self.analyses.append(Analysis("set", self.args_of(st, None), st.origin, name=st.name))
        elif k == "alter":
            a = self.alter(st)
            if a is not None:
                self.analyses.append(a)
        elif k == "altergroup":
            self.err(st.origin, "%s altergroup: altergroup is an error in v1 (§3.6)" % st.name)
        elif k == "info":
            self.analyses.append(Analysis("info", self.args_of(st, None), st.origin, name=st.name))
            self.note(st.origin, "%s info: the info file is not written (§3.6)" % st.name)
        elif k == "paramtest":
            self.paramtest(scope, st, branch)
        elif k in ("check", "checklimit", "assert"):
            self.note(st.origin, "%s %s: not evaluated (§3.6)" % (st.name, k))
        elif k == "shell":
            self.warn(st.origin, "%s shell: the command is not run (§3.6)" % st.name)
        elif k == "statistics":
            self.statistics_block(st)
        elif k == "simulator":
            self.simulator_mode(st)
        elif k in ("protect", "unprotect"):
            self.note(st.origin, "%s: listing control only (§3.1)" % k)
        elif k == "export":
            self.note(st.origin, "export: Monte Carlo output expressions are not evaluated")
        elif k in ("vary", "correlate", "truncate", "process", "mismatch"):
            self.err(st.origin, "%s belongs inside a statistics block" % k)
        elif k == "bin":
            self.err(st.origin, "a model-group entry belongs inside model name master { }")
        elif k == "return":
            self.err(st.origin, "return belongs inside a function body")
        else:
            self.err(st.origin, "statement %s is not supported (§3.6)" % k)

    def function(self, st: Stmt) -> None:
        name = st.name
        if not self.check_name(name, "a function", st.origin):
            return
        if name in expr.SPECTRE_FUNCS or name in expr.EXTRA_FUNCS:
            self.warn(st.origin, "function %s is named like the built-in %s(); the user function is used (UG p.92)"
                      % (name, name))
        args = list(st.nodes)
        body_text = st.params[0][1] if st.params else ""
        if not body_text:
            self.err(st.origin, "function %s has no return expression" % name)
            return
        body = self.expr(body_text, st.origin)
        if body is None:
            return
        free = expr.names(body) - set(args)
        if free:
            self.err(st.origin, "function %s reads %s, which is not among its arguments (UG p.92: a body may read "
                     "only its arguments)" % (name, ", ".join(sorted(free))))
            return
        if name in self.funcs:
            self.note(st.origin, "function %s is defined again; the last definition is used" % name)
        self.funcs[name] = (args, body)

    def parameters(self, scope: _Scope, st: Stmt) -> None:
        for k, v in st.params:
            if not _IDENT.match(k):
                self.err(st.origin, "parameters: %r is not a parameter name" % k)
                continue
            if k in ("temp", "tnom", "temper", "time", "hertz"):
                self.err(st.origin, "a parameter named %s is an error in v1: it is the predefined temperature (temp, "
                         "tnom) or a name the evaluator reserves (§3.5, §14 q.29)" % k)
                continue
            if k in expr.SPECTRE_CONSTANTS:
                self.err(st.origin, "%s is a built-in constant; it cannot name a parameter (ref19 p.495)" % k)
                continue
            val = self.value(v, st.origin, k)
            if val is None:
                continue
            if isinstance(val, list):
                self.err(st.origin, "parameter %s: a vector-valued netlist parameter is not supported in v1" % k)
                continue
            scope.params.append((k, val, st.origin, "spectre"))

    def model(self, scope: _Scope, st: Stmt) -> None:
        name = self.ir_name(st.name)
        if not self.check_name(name, "a model", st.origin):
            return
        master = st.master
        va = self.resolver.va.get(master.lower())
        if va is None and master not in T.SPECTRE_MASTERS:
            self.err(st.origin, "model %s %s: master %s is not a v1 primitive (%s) or a Verilog-A module of an "
                     "ahdl_include" % (name, master, master, " ".join(sorted(T.SPECTRE_MASTERS))))
            return
        if va is None and master in ("vsource", "isource", "iprobe", "vcvs", "vccs", "ccvs", "cccs", "mutual_inductor"):
            self.err(st.origin, "model %s %s: %s takes no model statement" % (name, master, master))
            return
        if st.children:                                      # a model group (§3.9)
            if st.params:
                self.err(st.origin, "model %s: a model group takes its parameters in its entries" % name)
            for entry in st.children:
                params = self.model_params(entry.params, entry.origin, "%s.%s" % (name, entry.name))
                if params is None:
                    continue
                m = Model("%s.%s" % (name, entry.name), "", None, params, entry.origin, base=name,
                          bin_index=int(entry.name), bin_rule="spectre", prim=master)
                self.add_model(scope, m, entry.origin)
            return
        params = self.model_params(st.params, st.origin, name)
        if params is None:
            return
        if va is not None:
            m = Model(name, va.name, None, params, st.origin, prim=va.name)
        else:
            m = Model(name, "", None, params, st.origin, prim=master)
        self.add_model(scope, m, st.origin)

    def model_params(self, pairs: List[Tuple[str, str]], origin: str, label: str) -> Optional[Dict[str, Expr]]:
        params: Dict[str, Expr] = {}
        ok = True
        for k, v in pairs:
            val = self.param_value(k, v, origin)
            if val is None:
                ok = False
                continue
            if isinstance(val, list):
                self.err(origin, "model %s: %s=[...]: a vector model parameter is not supported" % (label, k))
                ok = False
                continue
            if k in params:
                self.note(origin, "model %s: %s is given twice; the last value is used" % (label, k))
            params[k] = val
        return params if ok else None

    def add_model(self, scope: _Scope, m: Model, origin: str) -> None:
        for it in scope.items:
            if isinstance(it, Model) and it.name == m.name:
                self.err(origin, "model %s is defined twice in %s: %s and %s"
                         % (m.name, "subckt %s" % scope.name if scope.name else "the netlist", it.origin, origin))
                return
        scope.items.append(m)

    def subckt(self, scope: _Scope, st: Stmt) -> None:
        name = self.ir_name(st.name)
        if not self.check_name(name, "a subckt", st.origin):
            return
        child = _Scope(name, scope)
        child.inline = ("inline", "yes") in st.params
        ports: List[str] = []
        for p in st.nodes:
            n = self.node(p, st.origin)
            if n is None:
                continue
            if n in ports:
                self.err(st.origin, "subckt %s: port %s is listed twice" % (name, p))
            ports.append(n)
            child.spelling.setdefault(n, p)
        child.ports = ports
        saved = self.insensitive_now
        for c in st.children:
            self.lower(child, c, None)
        self.insensitive_now = saved
        for it in scope.items:
            if isinstance(it, Subckt) and it.name == name:
                self.err(st.origin, "subckt %s is defined twice: %s and %s" % (name, it.origin, st.origin))
                return
        sub = Subckt(name, ports, [], child.items, list(ports), [], st.origin, dict(child.spelling), inline=child.inline)
        scope.items.append(sub)
        self.scopes.append((child, sub))

    def cond(self, scope: _Scope, st: Stmt, branch: Optional[List[object]]) -> None:
        branches: List[Tuple[Expr, List[object]]] = []
        default: List[object] = []
        for cond_text, body in st.branches:
            items: List[object] = []
            for b in body:
                self.lower(scope, b, items)
            if cond_text == "":
                default = items
            else:
                e = self.expr(cond_text, st.origin)
                if e is None:
                    return
                branches.append((e, items))
        if not branches:
            return
        # an else-if chain nests a Cond in default (§4.1 item 4)
        tail: List[object] = default
        for cond_e, items in reversed(branches[1:]):
            tail = [Cond([(cond_e, items)], tail, st.origin)]       # type: ignore[list-item]
        c = Cond([branches[0]], tail, st.origin)                      # type: ignore[arg-type]
        (branch if branch is not None else scope.items).append(c)

    def paramtest(self, scope: _Scope, st: Stmt, branch: Optional[List[object]]) -> None:
        pt = ParamTest(st.name, [], "", "", st.origin)
        for k, v in st.params:
            if k in ("printif", "warnif", "errorif"):
                e = self.expr(v, st.origin)
                if e is not None:
                    pt.tests.append((k, e))
            elif k == "message":
                pt.message = v.strip('"')
            elif k == "severity":
                if v not in ("debug", "status", "warning", "error", "fatal"):
                    self.err(st.origin, "%s paramtest severity=%s: debug status warning error fatal" % (st.name, v))
                pt.severity = v
            else:
                self.err(st.origin, "%s paramtest: %s= is not a paramtest parameter" % (st.name, k))
        (branch if branch is not None else scope.items).append(pt)

    def global_stmt(self, st: Stmt) -> None:
        if not st.nodes:
            self.err(st.origin, "global needs at least one node")
            return
        for k, w in enumerate(st.nodes):
            n = self.node(w, st.origin)
            if n is None:
                continue
            if k == 0 and not self.ground_set:
                self.ground = n                                        # the first name of the first global (§3.4)
                self.ground_set = True
                continue
            if n not in self.globals and n != self.ground:
                self.globals.append(n)

    def ic(self, st: Stmt) -> None:
        target = self.ics if st.kind == "ic" else self.nodesets
        for k, v in st.params:
            if ":" in k:
                self.err(st.origin, "%s %s=: an inst:term initial condition is an error (§3.6)" % (st.kind, k))
                continue
            if "*" in k or "?" in k:
                self.err(st.origin, "%s %s=: wildcards are an error in v1 (§3.10)" % (st.kind, k))
                continue
            e = self.expr(v, st.origin)
            if e is None:
                continue
            name = _unescape(k)
            if name != k:
                self.spelling.setdefault(name, k)
            target[name] = e

    # -- instances (§3.8)

    def instance(self, scope: _Scope, st: Stmt, branch: Optional[List[object]]) -> None:
        name = self.ir_name(st.name)
        if not self.check_name(name, "an instance", st.origin):
            return
        master_written = st.master
        master = _unescape(master_written)
        nodes: List[str] = []
        for w in st.nodes:
            n = self.node(w, st.origin)
            if n is None:
                return
            nodes.append(n)
        params: Dict[str, Value] = {}
        for k, v in st.params:
            val = self.param_value(k, v, st.origin)
            if val is None:
                return
            if k in params:
                self.err(st.origin, "%s: %s is given twice" % (name, k))
                return
            params[k] = val
        inst: Optional[Instance] = None
        if master in _MASTER_KIND:
            if master in _MODEL_MASTERS:
                self.err(st.origin, "%s %s: %s is a model master; instantiate a model of it" % (name, master, master))
                return
            inst = Instance(name, _MASTER_KIND[master], nodes, None, params={}, origin=st.origin, prim=master)
            self.primitive(inst, params, st)
        else:
            d = self.resolver.find("model", master)
            if d is not None:
                inst = self.model_instance(name, nodes, d, params, st)
            else:
                d = self.resolver.find("subckt", master)
                if d is not None:
                    inst = Instance(name, "x", nodes, d.name, params={}, origin=st.origin, prim="subckt")
                    for k, v in params.items():
                        if isinstance(v, list):
                            self.err(st.origin, "%s: %s=[...]: a vector override is not supported" % (name, k))
                            return
                        inst.params[k] = v
                    if d.ports is not None and len(nodes) != d.ports:
                        self.err(st.origin, "%s has %d node%s but subckt %s has %d port%s (%s)"
                                 % (name, len(nodes), "" if len(nodes) == 1 else "s", master_written, d.ports,
                                    "" if d.ports == 1 else "s", d.origin))
                        return
                    self.xrefs.append((inst, d, st.origin))
                else:
                    va = self.resolver.va.get(master.lower())
                    if va is not None:
                        inst = Instance(name, "y", nodes, va.name, params={}, origin=st.origin, prim=va.name)
                        if not self.va_params(inst, va, params, st.origin):
                            return
                    else:
                        self.err(st.origin, "%s: master %s is not a primitive, a model, a subckt or a Verilog-A "
                                 "module (§3.6)" % (name, master_written))
                        return
        if inst is None:
            return
        (branch if branch is not None else scope.items).append(inst)

    def va_params(self, inst: Instance, va: VaModule, params: Dict[str, Value], origin: str) -> bool:
        """§3.9: Verilog-A instance parameters matched case-insensitively against the declarations."""
        declared = {p.lower(): p for p in (va.params or [])}
        for k, v in params.items():
            if isinstance(v, list):
                self.err(origin, "%s: %s=[...]: a vector parameter of a Verilog-A instance is not supported"
                         % (inst.name, k))
                return False
            if va.params is not None and k.lower() not in declared and k != "m":
                self.err(origin, "%s: %s is not a parameter of Verilog-A module %s (declared: %s)"
                         % (inst.name, k, va.name, " ".join(va.params) or "none"))
                return False
            inst.params[k] = v
        return True

    def model_instance(self, name: str, nodes: List[str], d: _Decl, params: Dict[str, Value], st: Stmt
                       ) -> Optional[Instance]:
        master = d.master
        va = self.resolver.va.get(master.lower()) if master else None
        if va is not None:
            # master is the module, as for a direct module instance: the emitters read inst.master as
            # the module (spice.py's rule); the card's parameters are merged into the instance (§3.9)
            inst = Instance(name, "y", nodes, va.name, params={}, origin=st.origin, prim=va.name)
            if not self.va_params(inst, va, params, st.origin):
                return None
            self.va_card_refs.append((inst, d.name))
            return inst
        if master not in _MASTER_KIND:
            self.err(st.origin, "%s: model %s has master %s, which is not a v1 primitive" % (name, d.name, master))
            return None
        inst = Instance(name, _MASTER_KIND[master], nodes, d.name, params={}, origin=st.origin, prim=master)
        self.primitive(inst, params, st)
        return inst

    def primitive(self, inst: Instance, params: Dict[str, Value], st: Stmt) -> None:
        """A primitive's parameters into the IR (§3.8); the master rules run in the masters pass."""
        prim = inst.prim
        if prim in ("vsource", "isource"):
            m = params.pop("m", None)
            if m is not None:
                inst.params["m"] = _one(m)
            inst.source = Source(spectre=dict(params))
            return
        if prim == "iprobe":
            if params:
                self.err(st.origin, "%s iprobe: an iprobe takes no parameters" % inst.name)
            inst.source = Source(dc=Num(0.0))
            return
        for k, v in params.items():
            if isinstance(v, list):
                self.err(st.origin, "%s: %s=[...] is not a %s parameter" % (inst.name, k, prim))
                continue
            inst.params[k] = v

    # -- analyses (§3.11, §3.12)

    def args_of(self, st: Stmt, allowed: Optional[Sequence[str]], skip: Sequence[str] = ()) -> Dict[str, object]:
        out: Dict[str, object] = {}
        for k, v in st.params:
            if k in skip:
                continue
            if allowed is not None and not _allowed(k, allowed):
                self.err(st.origin, "%s %s: %s= is not a parameter of a Spectre %s analysis (§3.11)"
                         % (st.name, st.master, k, st.master))
                continue
            val = self.enum_or_value(k, v, st.origin, _ENUM_ARGS)
            if val is not None:
                out[k] = val
        return out

    def analysis(self, st: Stmt) -> Optional[Analysis]:
        kw = st.master
        if kw not in ("dc", "ac", "noise", "xf", "tran", "sweep", "montecarlo"):
            self.err(st.origin, "%s %s: the %s analysis is not supported in v1 (dc ac noise xf tran sweep; "
                     "montecarlo is parsed and refused at plan time)" % (st.name, kw, kw))
            return None
        if not self.check_name(st.name, "an analysis", st.origin):
            return None
        if kw == "sweep" and not st.children:
            self.err(st.origin, "%s sweep: a sweep block must end its line with { and hold its analyses "
                     "(ref19 p.406)" % st.name)
            return None
        a = Analysis(kw, {}, st.origin, name=st.name)
        if kw in ("noise", "xf"):
            if len(st.nodes) > 2:
                self.err(st.origin, "%s %s: the output is one node or a node pair" % (st.name, kw))
                return None
            a.nodes = [self.node(w, st.origin, terminal=False) or w for w in st.nodes]
        elif st.nodes:
            self.err(st.origin, "%s %s takes no node list" % (st.name, kw))
            return None
        if kw in ("dc", "ac", "noise", "xf", "sweep"):
            a.sweep = self.sweep_spec(st, kw)
            a.args = self.args_of(st, _ANALYSIS_ARGS.get(kw), skip=_SWEEP_KEYS)
        elif kw == "tran":
            a.args = self.args_of(st, _TRAN_ARGS)
        else:
            a.args = self.args_of(st, None)
        if kw == "tran" and "stop" not in a.args:
            self.err(st.origin, "%s tran: stop is required" % st.name)
            return None
        for c in st.children:
            if c.kind == "analysis":
                child = self.analysis(c)
                if child is not None:
                    a.children.append(child)
            elif c.kind == "options":
                self.options_stmt(c)
            elif c.kind == "alter":
                child = self.alter(c)
                if child is not None:
                    a.children.append(child)
            elif c.kind == "set":
                a.children.append(Analysis("set", self.args_of(c, None), c.origin, name=c.name))
            elif c.kind == "info":
                a.children.append(Analysis("info", self.args_of(c, None), c.origin, name=c.name))
                self.note(c.origin, "%s info: the info file is not written (§3.6)" % c.name)
            elif c.kind == "export" and kw == "montecarlo":
                self.note(c.origin, "export: Monte Carlo output expressions are not evaluated")
            else:
                self.err(c.origin, "%s: a %s statement cannot be a child of a %s block (§3.12)"
                         % (c.name or c.kind, c.kind, kw))
        return a

    def sweep_spec(self, st: Stmt, kw: str) -> Optional[SweepSpec]:
        given = dict(st.params)
        if not any(k in given for k in _SWEEP_KEYS):
            return None
        targets = [k for k in ("dev", "mod", "sub") if k in given]
        if len(targets) > 1:
            self.err(st.origin, "%s %s: only one of dev= mod= sub= may be given" % (st.name, kw))
            return None
        if not targets and "param" in given:
            targets = ["param"]
        spec = SweepSpec("", origin=st.origin)
        if targets:
            t = targets[0]
            spec.target = t
            if t == "param":
                spec.param = _unescape(given["param"])
            else:
                if "param" not in given:
                    if t != "dev":
                        self.err(st.origin, "%s %s: %s= needs param=" % (st.name, kw, t))
                        return None
                    # dev= alone sweeps a source's dc: `SwpVccDC dc dev=Vcc values=[0 2 6 7 8 10]` [M UG p.168];
                    # the device must be a vsource/isource, checked once the instances exist (dev_defaults_pass)
                    spec.param = "dc"
                    self.dev_dc_defaults.append((spec, st.origin, st.name, kw))
                else:
                    spec.param = _unescape(given["param"])
                if t == "mod":
                    name = self.resolve_ref("model", given[t])
                    if name is None:
                        self.err(st.origin, "%s %s: mod=%s names no model" % (st.name, kw, given[t]))
                        return None
                    spec.name = name
                else:
                    spec.name = _unescape(given[t])
        elif kw in ("ac", "noise", "xf"):
            spec.target = "freq"
        else:
            self.err(st.origin, "%s %s: a sweep needs dev= mod= sub= or param= (an operating point takes none of "
                     "start stop step)" % (st.name, kw))
            return None
        modes = [k for k in ("lin", "dec", "log", "step", "values", "valuesfile") if k in given]
        if len(modes) > 1:
            self.err(st.origin, "%s %s: only one of lin dec log step values valuesfile may be given"
                     % (st.name, kw))
            return None
        if modes:
            mode = modes[0]
            if mode in ("lin", "dec", "log"):
                e = self.expr(given[mode], st.origin)
                if e is None:
                    return None
                if not isinstance(e, Num) or e.value != int(e.value) or e.value <= 0:
                    self.err(st.origin, "%s %s: %s=%s must be a positive integer" % (st.name, kw, mode, given[mode]))
                    return None
                spec.mode = mode
                spec.count = int(e.value)
            elif mode == "step":
                spec.mode = "step"
                spec.step = self.expr(given["step"], st.origin)
            elif mode == "values":
                v = self.value(given["values"], st.origin, "values")
                if v is None:
                    return None
                spec.mode = "values"
                spec.values = _vec(v)
            else:
                spec.mode = "values"
                spec.values = self.values_file(given["valuesfile"], st.origin)
        for k in ("start", "stop"):
            if k in given:
                setattr(spec, k, self.expr(given[k], st.origin))
        if "center" in given or "span" in given:
            if "start" in given or "stop" in given:
                self.err(st.origin, "%s %s: center/span and start/stop are exclusive" % (st.name, kw))
                return None
            c = self.expr(given.get("center", "0"), st.origin)
            s = self.expr(given.get("span", "0"), st.origin)
            if c is None or s is None:
                return None
            half = Binary("/", s, Num(2.0))
            if spec.mode in ("dec", "log"):
                self.err(st.origin, "%s %s: center/span with a logarithmic sweep is an error (§14 q.11)" % (st.name, kw))
                return None
            if spec.mode == "":
                # no step parameter: the shape center -/+ span/2 stays unfolded, so that plan.build, which
                # applies the ratio rule (§5.2), can refuse the logarithmic grid the rule would select
                spec.start = Binary("-", c, half)
                spec.stop = Binary("+", c, half)
            else:
                spec.start = expr.fold(Binary("-", c, half))
                spec.stop = expr.fold(Binary("+", c, half))
        return spec

    def values_file(self, text: str, origin: str) -> List[Expr]:
        path = self.resolve_path(text.strip('"'), self.cwd, origin, "valuesfile", cwd_first=True)
        if path is None:
            return []
        out: List[Expr] = []
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    for tok in raw.split():
                        try:
                            out.append(Num(expr.number(tok, "spectre")))
                        except ValueError:
                            self.err(origin, "valuesfile %s: %r is not a number" % (path, tok))
                            return out
        except OSError as exc:
            self.err(origin, "cannot read %s: %s" % (path, exc))
        return out

    def alter(self, st: Stmt) -> Optional[Analysis]:
        given = dict(st.params)
        targets = [k for k in ("dev", "mod", "sub") if k in given]
        if not targets and "param" in given:
            targets = ["param"]
        if len(targets) != 1:
            self.err(st.origin, "%s alter: exactly one of dev= mod= sub= (with param=) or param= is needed" % st.name)
            return None
        if "value" not in given:
            self.err(st.origin, "%s alter: value= is needed" % st.name)
            return None
        args: Dict[str, object] = {}
        t = targets[0]
        if t == "mod":
            name = self.resolve_ref("model", given["mod"])
            if name is None:
                self.err(st.origin, "%s alter: mod=%s names no model" % (st.name, given["mod"]))
                return None
            args["mod"] = Str(name)
        else:
            args[t] = Str(_unescape(given[t]))
        if t != "param":
            if "param" not in given:
                self.err(st.origin, "%s alter: %s= needs param=" % (st.name, t))
                return None
            args["param"] = Str(_unescape(given["param"]))
        val = self.enum_or_value("value", given["value"], st.origin, frozenset())
        if val is None:
            return None
        args["value"] = val
        for k, _v in st.params:
            if k in ("dev", "mod", "sub", "param", "value", "annotate"):
                continue                                        # annotate is ignored (§3.10)
            self.err(st.origin, "%s alter: %s= is not an alter parameter" % (st.name, k))
        return Analysis("alter", args, st.origin, name=st.name)

    # -- options (§3.10)

    def options_stmt(self, st: Stmt) -> None:
        for k, v in st.params:
            key = k.lower()
            val = self.enum_or_value(key, v, st.origin, _ENUM_OPTIONS)
            if val is None:
                continue
            if isinstance(val, list):
                self.err(st.origin, "%s options: %s=[...] is not an options value" % (st.name, k))
                continue
            if key in self.options:
                self.note(st.origin, "options %s=%s replaces the earlier value (%s)" % (k, v, self.option_origin[key]))
            self.options[key] = val
            self.option_origin[key] = st.origin
            if k != key:
                self.spelling.setdefault(key, k)
            self.option_disposition(key, val, v, st)

    def option_disposition(self, key: str, val: Expr, text: str, st: Stmt) -> None:
        if key in _OPTION_SILENT or key in _OPTION_OUTPUT:
            return
        if key == "scale":
            if not (isinstance(val, Num) and val.value == 1.0):
                self.warn(st.origin, "options scale=%s: the IR's scale applies HSPICE's rule (§3.10, §14 q.43)" % text)
        elif key == "scalem":
            if not (isinstance(val, Num) and val.value == 1.0):
                self.err(st.origin, "options scalem=%s: model scaling other than 1 is an error (§3.10)" % text)
        elif key == "tempeffects":
            if not (isinstance(val, Str) and val.text == "all"):
                self.err(st.origin, "options tempeffects=%s: only all is supported (§3.10)" % text)
        elif key in _OPTION_REDUCTION:
            if not (isinstance(val, Num) and val.value == _OPTION_REDUCTION[key]):
                self.warn(st.origin, "options %s=%s: element reduction is not applied (§3.10)" % (key, text))
        elif key in _OPTION_NOTE:
            self.note(st.origin, "options %s=%s: not applied (§3.10)" % (key, text))
        else:
            self.warn(st.origin, "options %s=%s: unknown to vamos, not applied (§3.10)" % (key, text))

    # -- statistics (§3.12)

    def statistics_block(self, st: Stmt) -> None:
        block = StatBlock("statistics", origin=st.origin)
        for c in st.children:
            if c.kind in ("process", "mismatch"):
                sub = StatBlock(c.kind, origin=c.origin)
                for cc in c.children:
                    self.stat_item(sub, cc)
                self.statistics.append(sub)
            else:
                self.stat_item(block, c)
        if block.varies or block.correlates or block.truncate is not None:
            self.statistics.append(block)

    def stat_item(self, block: StatBlock, c: Stmt) -> None:
        if c.kind == "vary":
            v = Vary(c.name, origin=c.origin)
            for k, t in c.params:
                if k == "dist":
                    if t not in ("gauss", "lnorm", "unif"):
                        self.err(c.origin, "vary %s dist=%s: gauss lnorm unif" % (c.name, t))
                    v.dist = t
                elif k == "std":
                    v.std = self.expr(t, c.origin)
                elif k == "N":
                    v.n = self.expr(t, c.origin)
                elif k == "percent":
                    v.percent = t == "yes"
                else:
                    self.err(c.origin, "vary %s: %s= is not a vary parameter" % (c.name, k))
            block.varies.append(v)
        elif c.kind == "correlate":
            co = Correlate([], origin=c.origin)
            for k, t in c.params:
                if k == "param":
                    co.params = _vector_names(t)
                elif k == "dev":
                    co.devs = _vector_names(t)
                elif k == "cc":
                    co.cc = self.expr(t, c.origin)
                else:
                    self.err(c.origin, "correlate: %s= is not a correlate parameter" % k)
            block.correlates.append(co)
        elif c.kind == "truncate":
            for k, t in c.params:
                if k == "tr":
                    block.truncate = self.expr(t, c.origin)
                else:
                    self.err(c.origin, "truncate: %s= is not a truncate parameter" % k)
        else:
            self.err(c.origin, "a %s statement inside a statistics block (vary correlate truncate process mismatch)"
                     % c.kind)

    # -- SPICE regions (§4.3)

    def lower_spice(self, region: _SpiceRegion) -> None:
        frag = spice.parse_fragment(region.lines, self.cwd, self.popts, self.resolver)
        self.notes.extend(frag.notes)
        self.left_out.update(frag.left_out)
        for kind, name, origin in frag.pending:
            self.err(origin, "%s %s is defined nowhere (in either language)" % (kind, name))
        for it in frag.items:
            if isinstance(it, Param):
                self.top.params.append((it.name, it.expr, it.origin, "spice"))
            elif isinstance(it, Subckt):
                self.spice_subckt(it, self.top)
                self.top.items.append(it)
            else:
                self.top.items.append(it)
        for keyword, fields, origin in frag.controls:
            self.spice_control(keyword, fields, origin)

    def spice_subckt(self, sub: Subckt, parent: _Scope) -> None:
        child = _Scope(sub.name, parent)
        child.params = [(p.name, p.expr, p.origin, "spice") for p in sub.params]
        child.items = sub.body
        child.body = sub.body
        for it in sub.body:
            if isinstance(it, Subckt):
                self.spice_subckt(it, child)
        self.scopes.append((child, sub))

    def spice_control(self, keyword: str, fields: List[Tuple[Optional[str], str]], origin: str) -> None:
        """§4.3's dot-statement table."""
        bare = [v for k, v in fields if k is None]
        if keyword in (".end", ".hdl"):
            return                                                     # .hdl was read in the first phase
        if keyword == ".title":
            self.note(origin, ".title: line 1 of the top-level netlist is the title (§3.1)")
            return
        if keyword == ".protect":
            self.note(origin, "%s: listing control only (§3.1)" % keyword)
            return
        if keyword == ".global":
            for w in bare:
                if w != "0" and w not in self.globals and w != self.ground:
                    self.globals.append(w)
            return
        if keyword == ".temp":
            if any(k is not None for k, _ in fields):
                self.err(origin, ".temp = t is not SPICE syntax (UG p.55); write .temp t")
                return
            if len(bare) != 1:
                self.err(origin, ".temp with %d temperatures: a temperature sweep is an error in v1 (§4.3)" % len(bare))
                return
            e = self.spice_expr(bare[0], origin)
            if e is not None:
                self.options["temp"] = e
                self.option_origin["temp"] = origin
            return
        if keyword == ".op":
            self.analyses.append(Analysis("dc", {}, origin, name=self.spice_name("opBegin", "dc"), spice=True))
            return
        if keyword == ".dc":
            self.spice_dc(fields, origin)
            return
        if keyword == ".ac":
            self.spice_ac(fields, origin)
            return
        if keyword == ".tran":
            self.spice_tran(fields, origin)
            return
        if keyword in (".ic", ".nodeset"):
            target = self.ics if keyword == ".ic" else self.nodesets
            for k, v in fields:
                if k is None:
                    self.err(origin, "%s: write v(node)=value or node=value, not %r" % (keyword, v))
                    continue
                m = re.match(r"[vV]\((.*)\)$", k)
                node = m.group(1).strip() if m else k
                if "," in node:
                    self.err(origin, "%s v(%s): one node per assignment" % (keyword, node))
                    continue
                e = self.spice_expr(v, origin)
                if e is not None:
                    target[node] = e
            return
        if keyword == ".option":
            self.spice_options(fields, origin)
            return
        if keyword == ".print":
            self.spice_print(fields, origin)
            return
        if keyword == ".measure":
            self.warn(origin, ".measure is not run (§4.3)")
            return
        if keyword == ".noise":
            self.err(origin, ".noise is an error in v1 (v2: a noise analysis over the preceding .ac, §4.3)")
            return
        self.err(origin, "%s is not supported in SPICE mode (§4.3)" % keyword)

    def spice_expr(self, text: str, origin: str) -> Optional[Expr]:
        try:
            return expr.parse(text, "lower", "spectre-spice", warn=self.warner(origin))
        except expr.ExprError as exc:
            self.err(origin, "cannot read %s: %s" % (text, exc))
            return None

    def spice_name(self, base: str, kind: str) -> str:
        """opBegin, srcSweep, frequencySweep, timeSweep; a second one of a type is <type><n> (§4.3)."""
        n = self.spice_counts.get(kind, 0) + 1
        self.spice_counts[kind] = n
        return base if n == 1 else "%s%d" % (kind, n)

    def spice_dc(self, fields, origin) -> None:
        words = [v for k, v in fields if k is None]
        if any(k is not None for k, _ in fields):
            self.err(origin, ".dc: keyed parameters are not SPICE2 syntax")
            return
        if any(w.lower() in ("lin", "dec", "oct", "poi") or w.lower().startswith("data=") for w in words):
            self.err(origin, ".dc LIN/DEC/OCT/POI/DATA= forms are errors in v1 (§4.3)")
            return
        if len(words) not in (4, 8):
            self.err(origin, ".dc needs source start stop step [source2 start stop step]")
            return
        inner: Optional[Analysis] = None
        for k in range(0, len(words), 4):
            src, start, stop, step = words[k:k + 4]
            if src.lower() == "temp":
                spec = SweepSpec("param", "", "temp", "step", origin=origin)
            elif self.resolver.find("param", src) is not None:
                spec = SweepSpec("param", "", src, "step", origin=origin)
            else:
                spec = SweepSpec("dev", src, "dc", "step", origin=origin)
            spec.start, spec.stop, spec.step = (self.spice_expr(t, origin) for t in (start, stop, step))
            if k == 0:
                inner = Analysis("dc", {}, origin, name=self.spice_name("srcSweep", "dc"), sweep=spec, spice=True)
            else:
                inner = Analysis("sweep", {}, origin, name=self.spice_name("srcSweep", "sweep"), sweep=spec,
                                 children=[inner] if inner is not None else [], spice=True)
        if inner is not None:
            self.analyses.append(inner)

    def spice_ac(self, fields, origin) -> None:
        words = [v for k, v in fields if k is None]
        if len(words) != 4 or words[0].lower() not in ("dec", "lin", "oct", "poi"):
            self.err(origin, ".ac needs DEC|LIN n fstart fstop")
            return
        mode = words[0].lower()
        if mode in ("oct", "poi"):
            self.err(origin, ".ac %s is an error in v1 (§4.3)" % mode.upper())
            return
        n = self.spice_expr(words[1], origin)
        if not isinstance(n, Num) or n.value <= 0 or n.value != int(n.value):
            self.err(origin, ".ac: the point count must be a positive integer")
            return
        spec = SweepSpec("freq", mode=mode, count=int(n.value), origin=origin)
        spec.start, spec.stop = self.spice_expr(words[2], origin), self.spice_expr(words[3], origin)
        if mode == "lin":                                   # SPICE's n points are Spectre's n-1 steps
            spec.count = max(int(n.value) - 1, 1)
        self.analyses.append(Analysis("ac", {}, origin, name=self.spice_name("frequencySweep", "ac"), sweep=spec,
                                      spice=True))

    def spice_tran(self, fields, origin) -> None:
        words = [v for k, v in fields if k is None]
        if any(k is not None for k, _ in fields):
            self.err(origin, ".tran: keyed parameters are not SPICE2 syntax (§4.3)")
            return
        uic = False
        if words and words[-1].lower() == "uic":
            uic = True
            words = words[:-1]
        if not 2 <= len(words) <= 4:
            self.err(origin, ".tran needs tstep tstop [tstart [tmax]] [uic] (SPICE2G, §4.3)")
            return
        vals = [self.spice_expr(w, origin) for w in words]
        if any(v is None for v in vals):
            return
        args: Dict[str, object] = {"step": vals[0], "stop": vals[1]}
        if len(vals) >= 3:
            args["outputstart"] = vals[2]
            if not (isinstance(vals[2], Num) and vals[2].value == 0):
                self.warn(origin, ".tran: the third field %s is SPICE2G's TSTART, the time before which nothing is "
                          "output (Spectre's outputstart), not HSPICE's start (§4.3)" % words[2])
        if len(vals) == 4:
            args["maxstep"] = vals[3]
        if uic:
            args["skipdc"] = Str("yes")
        self.analyses.append(Analysis("tran", args, origin, name=self.spice_name("timeSweep", "tran"), spice=True))

    def spice_options(self, fields, origin) -> None:
        renames = {"abstol": "iabstol", "vntol": "vabstol", "chgtol": "chargeabstol"}
        for k, v in fields:
            key = (v if k is None else k).lower()
            val_text = None if k is None else v
            if key in ("post", "ingold", "probe", "list", "node", "opts", "numdgt", "acct", "nomod", "brief",
                       "measout", "csdf", "nopage", "lis_new", "post_version", "captab"):
                self.note(origin, ".option %s ignored (output and listing control)" % key)
            elif key in ("wl", "defl", "defw", "defad", "defas", "defpd", "defps", "defnrd", "defnrs", "parhier",
                         "search", "dcap", "spice", "aspec", "scalm", "geoshrink"):
                self.err(origin, ".option %s changes how the netlist is read; it is an error in SPICE mode (§4.3)" % key)
            elif key in ("delmax", "rmax", "dvdt", "lvltim", "accurate") or re.match(r"itl[1-6]$", key):
                self.warn(origin, ".option %s is not applied (§4.3)" % key)
            elif key in ("reltol", "gmin", "temp", "tnom", "scale") or key in renames:
                if val_text is None:
                    self.err(origin, ".option %s needs a value" % key)
                    continue
                e = self.spice_expr(val_text, origin)
                if e is not None:
                    skey = renames.get(key, key)
                    self.options[skey] = e
                    self.option_origin[skey] = origin
                    if key == "scale" and not (isinstance(e, Num) and e.value == 1.0):
                        self.warn(origin, ".option scale=%s: the IR's scale applies HSPICE's rule (§3.10)" % val_text)
            elif key == "method":
                v2 = (val_text or "").strip("'\"").lower()
                if v2 not in ("trap", "gear"):
                    self.err(origin, ".option method=%s: trap or gear" % val_text)
                    continue
                self.options["method"] = Str("trap" if v2 == "trap" else "gear2")
                self.option_origin["method"] = origin
            else:
                self.warn(origin, ".option %s is not applied (§4.3)" % key)

    def spice_print(self, fields, origin) -> None:
        items = list(fields)
        if items and items[0][0] is None and items[0][1].lower() in ("tran", "dc", "ac", "op", "noise"):
            items = items[1:]
        saves: List[str] = []
        for k, v in items:
            if k is not None:
                self.warn(origin, ".print %s=%s: an output expression is not written (§0)" % (k, v))
                continue
            m = re.match(r"([A-Za-z]+)\s*\((.*)\)$", v)
            if m is None or m.group(1).lower() in ("par", "deriv"):
                self.warn(origin, ".print %s: an output expression is not written (§0)" % v)
                continue
            func = m.group(1).lower()
            args = [a for a in re.split(r"[,\s]+", m.group(2).strip()) if a]
            if func in ("v", "vm", "vp", "vr", "vi", "vdb") and 1 <= len(args) <= 2:
                if args == ["*"]:
                    self.options["save"] = Str("allpub")
                    self.option_origin.setdefault("save", origin)
                    continue
                if func != "v":
                    self.note(origin, ".print %s: the PSF holds the complex value; the %s form is derived" % (v, func))
                saves.extend(args)
            elif func == "i" and len(args) == 1:
                saves.append("%s:1" % args[0])
            else:
                self.warn(origin, ".print %s: an output expression is not written (§0)" % v)
        if saves:
            self.spice_saves.append(SaveSpec(saves, origin=origin))

    # =========================================================================================
    # Phase 3: parameters (§3.7), masters (§3.8-§3.9), sources, ground, checks
    # =========================================================================================

    def parameters_pass(self) -> None:
        self.top_sorted = self.sort_params(self.top, "the netlist")
        names = {p.name for p in self.top_sorted}
        symbolic: Set[str] = set()
        langs = {n: lang for n, _e, _o, lang in self.top.params}
        for p in self.top_sorted:
            used = expr.names(p.expr)
            unknown = sorted(n for n in used if n not in names)
            if unknown:
                self.err(p.origin, "parameter %s uses %s, which %s not defined at top level"
                         % (p.name, ", ".join(unknown), "is" if len(unknown) == 1 else "are"))
                symbolic.add(p.name)
                continue
            cross = sorted(n for n in used if langs.get(n) != langs.get(p.name))
            if cross:
                self.warn(p.origin, "parameter %s (%s mode) reads %s (%s mode): the UG says not to have dependencies "
                          "between the two language sections (UG pp.47-48); vamos reads it naturally (§3.7)"
                          % (p.name, langs.get(p.name, "spectre"), ", ".join(cross),
                             langs.get(cross[0], "spectre")))
            if used & symbolic or isinstance(p.expr, Str) or any(
                    isinstance(x, Name) and x.name in expr.RESERVED for x in expr.walk(p.expr)):
                symbolic.add(p.name)
                continue
            try:
                self.values[p.name] = expr.evaluate(p.expr, self.values, "spectre")
            except expr.EvalError as exc:
                self.err(p.origin, "parameter %s: %s" % (p.name, exc))
                symbolic.add(p.name)
        for scope, sub in self.scopes:
            sub.params = self.sort_params(scope, "subckt %s" % scope.name)

    def sort_params(self, scope: _Scope, where: str) -> List[Param]:
        """The last definition wins (a note), then a topological sort (a cycle is an error) (§3.7)."""
        last: Dict[str, int] = {}
        for k, (name, _e, origin, _l) in enumerate(scope.params):
            if name in last:
                self.note(origin, "parameter %s is defined again (first at %s); the last definition is used "
                          "everywhere in %s" % (name, scope.params[last[name]][2], where))
            last[name] = k
        keep = [scope.params[k] for k in sorted(last.values())]
        index = {name: i for i, (name, _e, _o, _l) in enumerate(keep)}
        users: List[List[int]] = [[] for _ in keep]
        indeg = [0] * len(keep)
        for i, (_name, e, _o, _l) in enumerate(keep):
            deps = sorted({index[n] for n in expr.names(e) if n in index})
            for j in deps:
                users[j].append(i)
            indeg[i] = len(deps)
        ready = [i for i in range(len(keep)) if indeg[i] == 0]
        heapq.heapify(ready)
        order: List[int] = []
        while ready:
            i = heapq.heappop(ready)
            order.append(i)
            for u in users[i]:
                indeg[u] -= 1
                if indeg[u] == 0:
                    heapq.heappush(ready, u)
        if len(order) < len(keep):
            stuck = [keep[i][0] for i in range(len(keep)) if indeg[i] > 0]
            self.err(keep[index[stuck[0]]][2], "parameters depend on each other in a cycle: %s" % " -> ".join(stuck))
            order += [i for i in range(len(keep)) if indeg[i] > 0]
        return [Param(keep[i][0], keep[i][1], keep[i][2]) for i in order]

    def bodies(self) -> List[Tuple[_Scope, List[object]]]:
        return [(self.top, self.top.items)] + [(s, sub.body) for s, sub in self.scopes]

    def masters_pass(self) -> None:
        """§3.8-§3.9 over every card and instance of both languages."""
        self.tran_stops = []
        for a in flat_analyses(self.analyses):
            if a.kind == "tran":
                e = a.args.get("stop")
                try:
                    if isinstance(e, (Num, Name, Binary, Unary, Call, Ternary)):
                        self.tran_stops.append(expr.evaluate(e, self.values, "spectre"))
                except expr.EvalError:
                    pass
        for _scope, items in self.bodies():
            for it in items:
                if isinstance(it, Model):
                    self.card(it)
        for scope, items in self.bodies():
            for it in flat_items(items):
                if isinstance(it, Instance):
                    self.instance_rules(it, scope)
        for inst, d, origin in self.xrefs:
            declared = set(d.params)
            sub = next((sb for _sc, sb in self.scopes if sb.name == d.name), None)
            if sub is not None:
                declared |= {p.name for p in sub.params}
            for k in inst.params:
                if k != "m" and k not in declared:
                    self.err(origin, "%s: %s is not a parameter of subckt %s (%s)" % (inst.name, k, d.name, d.origin))
        for inst, card in self.va_card_refs:                 # a Verilog-A card's parameters merged in (§3.9)
            m = next((it for it in self.all_models() if it.name == card), None)
            if m is not None:
                merged = dict(m.params)
                merged.update(inst.params)
                inst.params = merged

    def all_models(self) -> Iterator[Model]:
        for _s, items in self.bodies():
            for it in items:
                if isinstance(it, Model):
                    yield it

    def card(self, m: Model) -> None:
        if m.prim.lower() in self.resolver.va:
            return                                              # a Verilog-A card: checked per instance
        row = T.SPECTRE_MASTERS.get(m.prim)
        if row is None:
            self.err(m.origin, "model %s: master %s is not a v1 master" % (m.name, m.prim))
            return
        given = set(m.params)
        folds: Dict[str, Expr] = {}
        if row.polarity_key:
            pol = m.params.get(row.polarity_key)
            kinds = dict(row.kinds)
            if pol is None:
                if not m.kind:
                    m.kind = row.kinds[0][1]
            else:
                text = pol.text if isinstance(pol, Str) else pol.name if isinstance(pol, Name) else None
                if text is None or text not in kinds:
                    self.err(m.origin, "model %s %s: %s=%s is not one of %s" % (m.name, m.prim, row.polarity_key,
                                                                              _vtext(pol),
                                                                              " ".join(k for k, _ in row.kinds)))
                    return
                m.kind = kinds[text]
        elif row.kinds and not m.kind:
            m.kind = row.kinds[0][1]
        if "level" in m.params:
            lv = m.params["level"]
            if row.level is None:
                self.err(m.origin, "model %s %s: a %s card takes no level" % (m.name, m.prim, m.prim))
                return
            if not (isinstance(lv, Num) and lv.value == row.level):
                self.err(m.origin, "model %s %s: level=%s is not Spectre's level %d of %s (§3.9)"
                         % (m.name, m.prim, expr.to_text(lv, "spectre"), row.level, m.prim))
                return
            if not any(r.name == "level" for r in row.params):
                m.params.pop("level")                       # a SPICE card's level chose the master (§4.3)
        if row.level is not None:
            m.level = float(row.level)
        for key in list(m.params):
            rule = T.rule_for(row.params, key, m.params[key], given)
            if rule is None:
                self.err(m.origin, "model %s %s: %s is not a parameter of Spectre's %s (ref5)" % (m.name, m.prim, key,
                                                                                                m.prim))
                continue
            if rule.action == "fold":
                folds[key] = m.params.pop(key)
        self.card_folds[m.name] = folds
        if folds and m.prim in ("resistor", "capacitor", "inductor"):
            self.note(m.origin, "model %s: %s folded into its instances (§3.8)" % (m.name, " ".join(sorted(folds))))

    def instance_rules(self, inst: Instance, scope: _Scope) -> None:
        prim = inst.prim
        if inst.kind in ("x", "y") or not prim or prim == "subckt":
            return
        if prim == "iprobe":
            if len(inst.nodes) != 2:
                self.err(inst.origin, "%s iprobe needs 2 terminals (in out)" % inst.name)
            return
        row = T.SPECTRE_MASTERS.get(prim)
        if row is None:
            self.err(inst.origin, "%s: master %s is not a v1 master" % (inst.name, prim))
            return
        terms = len(row.terminals)
        n = len(inst.nodes)
        if prim == "bjt":
            if n not in (3, 4):
                self.err(inst.origin, "%s bjt needs 3 or 4 terminals (c b e [s]), not %d" % (inst.name, n))
        elif prim == "jfet":
            if n == 4:
                self.err(inst.origin, "%s jfet: a fourth terminal is an error in v1 (§3.8)" % inst.name)
            elif n != 3:
                self.err(inst.origin, "%s jfet needs 3 terminals (d g s), not %d" % (inst.name, n))
        elif prim == "mutual_inductor":
            if n:
                self.err(inst.origin, "%s mutual_inductor has no terminals" % inst.name)
        elif n != terms:
            self.err(inst.origin, "%s %s needs %d terminal%s (%s), not %d" % (inst.name, prim, terms,
                                                                            "" if terms == 1 else "s",
                                                                            " ".join(row.terminals), n))
        folds = self.card_folds.get(inst.master or "", {}) if inst.master else {}
        if prim in ("vsource", "isource"):
            self.source_instance(inst, row)
            return
        params = inst.params
        given = set(params)
        for key in list(params):
            rule = T.rule_for(row.instance, key, params[key], given)
            if rule is None:
                self.err(inst.origin, "%s: %s is not an instance parameter of Spectre's %s (ref5)" % (inst.name, key, prim))
                return
            if rule.action == "error":
                self.err(inst.origin, "%s: %s=%s is not supported: %s" % (inst.name, key,
                                                                        _vtext(params[key]),
                                                                        T._rule_reason(rule)))
                return
            if rule.action == "strip":
                del params[key]
                self.notes.append(Note(WARNING if rule.warn == "card" else NOTE, inst.origin,
                                       "%s: %s removed: %s" % (inst.name, key, T._rule_reason(rule))))
        if prim in ("resistor", "capacitor", "inductor"):
            self.rcl_value(inst, prim, folds)
        elif prim in ("mos1", "mos2", "mos3", "bsim3v3", "bsim4"):
            self.mos_defaults(inst, prim, folds, row)
        elif prim in ("diode", "bjt", "jfet"):
            if "area" in params:
                inst.value = params.pop("area")
        elif prim in ("vcvs", "vccs"):
            key = "gain" if prim == "vcvs" else "gm"
            if key in params:
                inst.value = params.pop(key)
            elif inst.value is None:
                # a SPICE-mode E/G line carries its gain positionally (`e1 out 0 in 0 -1`, §4.3 "as
                # spice.py"), which spice.parse_fragment puts in Instance.value
                self.err(inst.origin, "%s %s needs %s=" % (inst.name, prim, key))
                return
            params.pop("type", None)
        elif prim in ("ccvs", "cccs"):
            key = "rm" if prim == "ccvs" else "gain"
            if key in params:
                inst.value = params.pop(key)
            elif inst.value is None:
                self.err(inst.origin, "%s %s needs %s=" % (inst.name, prim, key))
                return
            probe = params.pop("probe", None)
            pn = probe.text if isinstance(probe, Str) else probe.name if isinstance(probe, Name) else None
            if pn is None and inst.ctrl and len(inst.ctrl) == 1:
                pn = inst.ctrl[0]                       # a SPICE-mode F/H line names its controlling source
            if pn is None:
                self.err(inst.origin, "%s %s needs probe=<vsource or iprobe>" % (inst.name, prim))
                return
            inst.ctrl = [pn]
            self.probe_refs.append((inst, pn, scope))
            params.pop("type", None)
        elif prim == "mutual_inductor":
            if "coupling" not in params or "ind1" not in params or "ind2" not in params:
                self.err(inst.origin, "%s mutual_inductor needs coupling= ind1= ind2=" % inst.name)
                return
            inst.value = params.pop("coupling")
            l1, l2 = params.pop("ind1"), params.pop("ind2")
            inst.ctrl = [x.text if isinstance(x, Str) else x.name if isinstance(x, Name) else expr.to_text(x)
                         for x in (l1, l2)]
            for c in inst.ctrl:
                if not any(isinstance(it, Instance) and it.name == c and it.kind == "l"
                           for it in flat_items(self.scope_items(scope))):
                    self.err(inst.origin, "%s: %s is not an inductor of the same scope" % (inst.name, c))

    def scope_items(self, scope: _Scope) -> List[object]:
        return scope.items

    def source_instance(self, inst: Instance, row) -> None:
        src = inst.source if inst.source is not None else Source()
        params = dict(src.spectre)
        given = set(params) | set(inst.params)
        for key, val in list(params.items()):
            if key == "file":
                path = self.resolve_path(val.text if isinstance(val, Str) else expr.to_text(_one(val)), self.cwd,
                                         inst.origin, "waveform file", cwd_first=True)
                if path is None:
                    return
                params["wave"] = self.read_wave(path, inst.origin)
                del params["file"]
                continue
            rule = T.rule_for(row.instance, key, _one(val), given)
            if rule is None:
                self.err(inst.origin, "%s: %s is not a %s parameter (ref5 pp.682-686)" % (inst.name, key, inst.prim))
                return
            if rule.action == "error":
                self.err(inst.origin, "%s: %s= is not supported: %s" % (inst.name, key, T._rule_reason(rule)))
                return
        type_ = params.get("type")
        tname = type_.text if isinstance(type_, Str) else type_.name if isinstance(type_, Name) else "dc"
        if tname not in _SOURCE_TYPES:
            self.err(inst.origin, "%s: type=%s is not supported in v1 (dc pulse sine exp pwl)" % (inst.name, tname))
            return
        notes: List[Note] = []
        src = resolve_source(params, tname, self.tran_stops, notes)
        for n in notes:
            self.notes.append(Note(n.severity, inst.origin, "%s: %s" % (inst.name, n.message)))
        inst.source = src

    def read_wave(self, path: str, origin: str) -> List[Expr]:
        out: List[Expr] = []
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    toks = raw.split()
                    if not toks or toks[0].startswith(("#", ";", "//")):
                        continue
                    if len(toks) != 2:
                        self.err(origin, "waveform file %s: each line holds a time and a value" % path)
                        return out
                    for t in toks:
                        try:
                            out.append(Num(float(t)))              # no scale factors (UG p.74)
                        except ValueError:
                            self.err(origin, "waveform file %s: %r is not a number (no scale factors)" % (path, t))
                            return out
        except OSError as exc:
            self.err(origin, "cannot read %s: %s" % (path, exc))
        return out

    def constant_on(self, e: Optional[Expr], inst: Instance, what: str) -> Optional[float]:
        if e is None:
            return None
        try:
            return expr.evaluate(e, self.values, "spectre")
        except expr.EvalError as exc:
            self.err(inst.origin, "%s: %s=%s is not constant on this instance (an error in v1, §3.8): %s"
                     % (inst.name, what, expr.to_text(e, "spectre"), exc))
            return None

    def rcl_value(self, inst: Instance, prim: str, folds: Dict[str, Expr]) -> None:
        """§3.8: the resistor's r, the capacitor's c and the inductor's l, computed per instance."""
        p = inst.params
        key = {"resistor": "r", "capacitor": "c", "inductor": "l"}[prim]
        scale = p.pop("scale", None)
        s = self.constant_on(scale, inst, "scale") if scale is not None else 1.0
        if s is None:
            return
        if prim == "resistor":
            return self.resistor_value(inst, folds, s)
        geometry = ("w", "l", "area", "perim")
        if key in p:
            inst.value = p.pop(key)
            for g in geometry:
                p.pop(g, None)
            return
        if key in folds:
            inst.value = folds[key]
            for g in geometry:
                p.pop(g, None)
            return
        if inst.value is not None:                  # a SPICE-mode element's positional value (§4.3)
            for g in geometry:
                p.pop(g, None)
            return
        if prim == "inductor":
            inst.value = Num(0.0)
            return
        w_given, l_given = "w" in p, "l" in p
        w_e = p.pop("w", folds.get("w"))
        l_e = p.pop("l", folds.get("l"))
        fed = [g for g, given in (("w", w_given), ("l", l_given)) if given]
        # capacitor: cj*Area_eff + cjsw*Perim_eff from the card's etch and w/l or area/perim (ref5 pp.282-283)
        cj = self.constant_on(folds.get("cj"), inst, "cj") if "cj" in folds else 0.0
        cjsw = self.constant_on(folds.get("cjsw"), inst, "cjsw") if "cjsw" in folds else 0.0
        etch = self.constant_on(folds.get("etch"), inst, "etch") if "etch" in folds else 0.0
        area_e, perim_e = p.pop("area", None), p.pop("perim", None)
        if None in (cj, cjsw, etch):
            return
        if w_e is not None and l_e is not None:
            w, l = self.constant_on(w_e, inst, "w"), self.constant_on(l_e, inst, "l")
            if w is None or l is None:
                return
            w, l = w * s - 2 * etch, l * s - 2 * etch
            area, perim = w * l, 2 * (w + l)
        else:
            area = self.constant_on(area_e, inst, "area") if area_e is not None else 0.0
            perim = self.constant_on(perim_e, inst, "perim") if perim_e is not None else 0.0
            if area is None or perim is None:
                return
            fed = [g for g, e in (("area", area_e), ("perim", perim_e)) if e is not None]
        inst.value = Num(cj * area + cjsw * perim)
        inst.folded = fed

    def resistor_value(self, inst: Instance, folds: Dict[str, Expr], s: float) -> None:
        """§3.8: r is the instance's, else the card's, else rsh*(l-2*etchl)/(w-2*etch) with the instance's
        w/l (else the card's); the instance scale acts on w and l only and is never printed (ref5 p.629).
        The instance's own w and l stay on it, scaled: they are sp_resistor's instance parameters (the
        noise area; the table's pass rows), and Instance.folded records them when they fed r."""
        p = inst.params
        w_given, l_given = "w" in p, "l" in p
        w_e, l_e = p.get("w", folds.get("w")), p.get("l", folds.get("l"))
        if s != 1.0:
            for g in ("w", "l"):
                if g in p:
                    p[g] = expr.fold(Binary("*", p[g], Num(s)))
        p.pop("area", None)
        p.pop("perim", None)
        if "r" in p:
            inst.value = p.pop("r")
            return
        if "r" in folds:
            inst.value = folds["r"]
            return
        if inst.value is not None:                  # a SPICE-mode element's positional value (§4.3)
            return
        fed = [g for g, given in (("w", w_given), ("l", l_given)) if given]
        rsh = self.constant_on(folds.get("rsh"), inst, "rsh") if "rsh" in folds else None
        etch = self.constant_on(folds.get("etch"), inst, "etch") if "etch" in folds else 0.0
        etchl = self.constant_on(folds.get("etchl"), inst, "etchl") if "etchl" in folds else 0.0  # ref5 p.630
        w = self.constant_on(w_e, inst, "w") if w_e is not None else 1e-6
        l = self.constant_on(l_e, inst, "l") if l_e is not None else None
        if None in (etch, etchl, w) or (l_e is not None and l is None):
            return
        if rsh is None or l is None:
            inst.value = Num(math.inf)
            self.note(inst.origin, "%s: no r, rsh or l gives an infinite resistance; the instance is not printed "
                      "but stays in the instance table (§3.8)" % inst.name)
            inst.folded = fed
            return
        w, l = w * s, l * s
        if w - 2 * etch <= 0:
            self.err(inst.origin, "%s: w-2*etch = %g is not positive" % (inst.name, w - 2 * etch))
            return
        inst.value = Num(rsh * (l - 2 * etchl) / (w - 2 * etch))
        inst.folded = fed

    def mos_defaults(self, inst: Instance, prim: str, folds: Dict[str, Expr], row) -> None:
        """§3.9: default MOS geometry (the card's w/l, else the master's default) and nrd/nrs."""
        p = inst.params
        geometry = dict(row.geometry)
        group_cards = [m for m in self.all_models() if m.base is not None and m.base == inst.master]
        for key in ("w", "l"):
            if key in p:
                continue
            if group_cards:
                if any(key in self.card_folds.get(m.name, {}) for m in group_cards):
                    self.err(inst.origin, "%s: a model-group card gives %s for an instance without %s: an error in v1 "
                             "(§3.9)" % (inst.name, key, key))
                    return
                p[key] = Num(geometry[key])
                self.note(inst.origin, "%s: %s not given; the %s master's default %g m is written on the instance "
                          "(§3.9)" % (inst.name, key, prim, geometry[key]))
            elif key in folds:
                p[key] = folds[key]
                self.note(inst.origin, "%s: %s=%s written from the card's %s (§3.9)"
                          % (inst.name, key, expr.to_text(folds[key], "spectre"), key))
            else:
                p[key] = Num(geometry[key])
                self.note(inst.origin, "%s: %s not given; the %s master's default %g m is written on the instance "
                          "(§3.9)" % (inst.name, key, prim, geometry[key]))
        for key in ("nrd", "nrs"):
            if key in p:
                continue
            p[key] = folds[key] if key in folds else Num(0.0)
            self.note(inst.origin, "%s: %s=%s written on the instance (Spectre's card default; the targets' instance "
                      "default is 1, §3.9)" % (inst.name, key, expr.to_text(p[key], "spectre")))

    # -- ground (§3.4) and checks

    def ground_pass(self) -> None:
        g = self.ground
        if g == "0":
            return
        renamed = [False]

        def fold(name: str) -> str:
            if name == g:
                return "0"
            if name == "0":
                renamed[0] = True
                return "vamos_node0"
            return name

        for _scope, items in self.bodies():
            for it in flat_items(items):
                if isinstance(it, Instance):
                    it.nodes = [fold(n) for n in it.nodes]
        for _s, sub in self.scopes:
            sub.ports = [fold(n) for n in sub.ports]
            sub.orig_ports = list(sub.ports)
        for a in flat_analyses(self.analyses):
            a.nodes = [fold(n) for n in a.nodes]
        self.ics = {fold(k): v for k, v in self.ics.items()}
        self.nodesets = {fold(k): v for k, v in self.nodesets.items()}
        if renamed[0]:
            self.spelling.setdefault("vamos_node0", "0")
            self.warn("", "node 0 is not ground (global %s is); it is renamed vamos_node0 (§3.4, §14 q.7)" % g)

    def duplicates_pass(self) -> None:
        """§3.7: duplicate instance names are allowed only as ref19 p.491 states them."""
        for scope, items in self.bodies():
            seen: Dict[str, Instance] = {}
            allowed: Set[int] = set()
            for it in items:
                if isinstance(it, Cond):
                    self.cond_duplicates(it, allowed)
            for it in flat_items(items):
                if not isinstance(it, Instance):
                    continue
                if it.name in seen:
                    prev = seen[it.name]
                    if id(it) in allowed and id(prev) in allowed and prev.nodes == it.nodes and prev.prim == it.prim:
                        continue
                    self.err(it.origin, "instance %s is defined twice (%s and %s); duplicates are allowed only in "
                             "the if and else parts of one if statement, each a single statement, on the same nodes "
                             "and primitive (ref19 p.491)" % (it.name, prev.origin, it.origin))
                    continue
                seen[it.name] = it
                scope.insts[it.name] = it

    def cond_duplicates(self, c: Cond, allowed: Set[int]) -> None:
        """The instances of an if whose every part is a single statement or a nested if."""
        parts = [body for _e, body in c.branches] + ([c.default] if c.default else [])
        if len(parts) < 2 or any(len(b) != 1 for b in parts):
            for b in parts:
                for x in b:
                    if isinstance(x, Cond):
                        self.cond_duplicates(x, allowed)
            return
        for b in parts:
            x = b[0]
            if isinstance(x, Instance):
                allowed.add(id(x))
            elif isinstance(x, Cond):
                self.cond_duplicates(x, allowed)

    def find_instance(self, path: str) -> Optional[Instance]:
        """The instance an IR path (x1.x2.r1) names, through the subckt definitions."""
        scope: Optional[_Scope] = self.top
        parts = path.split(".")
        for k, part in enumerate(parts):
            inst = scope.insts.get(part) if scope is not None else None
            if inst is None:
                return None
            if k == len(parts) - 1:
                return inst
            if inst.kind != "x":
                return None
            scope = next((sc for sc, sub in self.scopes if sub.name == inst.master), None)
        return None

    def dev_defaults_pass(self) -> None:
        """A dc/sweep `dev=` without `param=` sweeps a source's dc (UG p.168); any other device needs param=."""
        for spec, origin, name, kw in self.dev_dc_defaults:
            inst = self.find_instance(spec.name)
            if inst is None:
                self.err(origin, "%s %s: dev=%s names no instance" % (name, kw, spec.name))
            elif inst.prim not in ("vsource", "isource"):
                self.err(origin, "%s %s: dev=%s needs param= (dev= alone sweeps a vsource's or isource's dc, UG "
                         "p.168; %s is a %s)" % (name, kw, spec.name, spec.name, inst.prim or inst.kind))

    def probes_pass(self) -> None:
        for inst, pn, scope in self.probe_refs:
            found = next((it for it in flat_items(scope.items) if isinstance(it, Instance) and it.name == pn), None)
            if found is None or found.kind != "v" or found.prim not in ("vsource", "iprobe"):
                self.err(inst.origin, "%s: probe=%s must name a vsource or iprobe of the same scope (§3.8)"
                         % (inst.name, pn))

    def saves_pass(self) -> None:
        for st in self.saves:
            spec = SaveSpec([_unescape(w) for w in st.nodes], origin=st.origin)
            for k, v in st.params:
                if k == "compression":
                    self.note(st.origin, "save compression=%s: noted (§3.6)" % v)
                elif k in ("depth", "probelvl"):
                    e = self.expr(v, st.origin)
                    val = self.constant(e, st.origin, "save %s" % k) if e is not None else None
                    if val is not None:
                        setattr(spec, k, int(val))
                elif k == "sigtype":
                    if v not in ("node", "dev", "subckt", "all"):
                        self.err(st.origin, "save sigtype=%s: node dev subckt all" % v)
                    spec.sigtype = v
                elif k == "devtype":
                    spec.devtype = v
                elif k == "subckt":
                    spec.subckt = self.resolve_ref("subckt", v) or v
                elif k == "exclude":
                    spec.exclude = _vector_names(v)
                elif k == "time_window":
                    vals = self.value(v, st.origin, k)
                    if vals is None:
                        continue
                    out: List[float] = []
                    for e in _vec(vals):
                        c = self.constant(e, st.origin, "save time_window")
                        if c is not None:
                            out.append(c)
                    if len(out) % 2:
                        self.err(st.origin, "save time_window needs interval pairs")
                    spec.time_window = out
                elif k == "ports":
                    spec.ports = v == "yes"
                elif k == "filter":
                    if v not in ("none", "rc"):
                        self.err(st.origin, "save filter=%s: none or rc" % v)
                    if v == "rc":
                        self.warn(st.origin, "save filter=rc: RC-only nodes are kept (§6.1)")
                    spec.filter = v
            self.save_specs.append(spec)
        self.save_specs.extend(self.spice_saves)

    def constant(self, e: Expr, origin: str, what: str) -> Optional[float]:
        try:
            return expr.evaluate(e, self.values, "spectre")
        except expr.EvalError as exc:
            self.err(origin, "%s must be a constant: %s" % (what, exc))
            return None

    def temps(self, nl: Netlist) -> None:
        nl.temp, nl.tnom = 27.0, 27.0
        for key in ("temp", "tnom"):
            e = self.options.get(key)
            if e is None:
                continue
            if isinstance(e, Str):
                self.err(self.option_origin[key], "options %s=%s is not a number" % (key, e.text))
                continue
            v = self.constant(e, self.option_origin[key], "options %s" % key)
            if v is not None:
                setattr(nl, key, v)
                self.options[key] = Num(v)

    # =========================================================================================
    # Assembly
    # =========================================================================================

    def build(self) -> Netlist:
        nl = Netlist(title=self.title, dialect="spectre")
        nl.parhier = "local"
        nl.body = list(self.top_sorted) + list(self.top.items)
        nl.globals = list(self.globals)
        if self.top_lang == "spice" and "save" not in self.options:
            # a SPICE-mode netlist saves allpub, as the converted decks do: the SPICE Reader inserts
            # `sppSaveOptions options save=allpub` (UG p.47; §4.3)
            self.options["save"] = Str("allpub")
            self.option_origin["save"] = self.top_origin
            self.note(self.top_origin, "a SPICE-mode netlist saves allpub (options save=allpub), as Spectre's SPICE "
                      "Reader writes it (UG p.47; §4.3)")
        nl.options = dict(self.options)
        self.temps(nl)
        nl.analyses = list(self.analyses)
        nl.saves = list(self.save_specs)
        nl.ics = dict(self.ics)
        nl.nodesets = dict(self.nodesets)
        nl.hdl = list(self.hdl)
        nl.values = dict(self.values)
        nl.spelling = dict(self.spelling)
        nl.statistics = list(self.statistics)
        nl.left_out = dict(self.left_out)
        nl.va_modules = dict(self.resolver.va)
        return nl


def parse(path: str, cwd: str, opts: SpectreParseOpts) -> Netlist:
    """A Spectre netlist, with every file it includes in both languages, into one Netlist with
    dialect "spectre" (§3, §4.3); built on statements(); NoteError with every note on error."""
    p = _Parser(cwd, opts)
    full = path if os.path.isabs(path) else os.path.normpath(os.path.join(p.cwd, path))
    if not os.path.isfile(full):
        p.err(opts.top_name or path, "netlist %s not found" % path)
        raise NoteError(p.notes)
    p.read_top(full)
    if any(n.severity == ERROR for n in p.notes):
        raise NoteError(p.notes)
    p.declare_all()
    p.lower_all()
    p.parameters_pass()
    p.masters_pass()
    p.ground_pass()
    p.duplicates_pass()
    p.dev_defaults_pass()
    p.probes_pass()
    p.saves_pass()
    nl = p.build()
    for origin, msg in spice._colon_clashes(nl):
        p.err(origin, msg)
    if any(n.severity == ERROR for n in p.notes):
        raise NoteError(p.notes)
    nl.notes = list(p.notes)
    return nl


number = functools.partial(expr.number, dialect="spectre")   # §3.3 (expr.number's dialect keyword: §4.2)
