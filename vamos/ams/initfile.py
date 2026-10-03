"""The VCS AMS control file, vcsAD.init (docs/VAMOS_AMS_DESIGN.md §2).

parse_control() reads snps_vcsAD.ini (when find_ini() finds one) and then the
-ad file, and fills the frozen AmsConfig (vamos/ams/config.py).  Every command
gets exactly one disposition (§0): applied, note, warning or error.  Problems
never raise: they are Notes in cfg.notes (file:line origins), printed with the
option report, and the caller stops on notes.has_errors(cfg.notes).

Lexical rules (§2.1):
  - // and /* */ comments, outside "double quotes".
  - A statement ends at ';' at bracket depth 0 ("...", () and {} are tracked,
    so port_dir (input a; output y) is one statement).  A line whose first
    word is a command keyword (_RECOVERY) also ends the previous statement,
    because Synopsys' own examples omit the final ';', and end of file ends the
    last one.  Such a keyword at depth > 0, or end of file at depth > 0, is the
    error "unterminated '(' in <cmd> at <file:line>": the broken statement is
    dropped and parsing resumes at the keyword.  A keyword that is a name in a
    list is no command start (a port called set: "set => SET," inside
    port_map; see _starts_command).
  - netlist_commands_begin ... netlist_commands_end and xa_commands_begin ...
    xa_commands_end are raw blocks.  The begin statement ends at its optional
    ';' or at the end of its line; what follows the ';' on that line is the
    first raw line.  The block ends at the first line whose first word is the
    *_end keyword (its ';' optional; the rest of that line is ordinary control
    text).  Lines in between are kept verbatim, comments included, each with
    its file:line origin.  A missing *_end is an error.
  - `include "f" (quotes and ';' optional; the statement ends at the end of
    its line): $VAR and ${VAR} expanded from env (an unset one is an error);
    a relative path is taken against cwd, the directory vcs runs in, for
    nested includes too (PAMS p123).  Included statements take effect in place.
  - Command keywords are case-insensitive.  Numbers are parsed by rules.py
    (netlist.numbers.parse_number; '%' = fraction of the reference span).
  - An unknown command is an error naming file:line.

Values in AmsConfig keep their spelling unless the frozen docstring says
otherwise (PortDir ports are lowercased).  AmsConfig.ref_voltages holds both
ie_reference_voltage forms: (node, volts or None, origin) for node= and
(node, SKIP, origin) for skip_node=, SKIP being NaN; rules.ref_nodes()
separates them.  AmsConfig.xa is {'probe_v': [(pattern, origin)], 'probe_i':
[(pattern, origin)], 'case': 'lower' | 'upper' | 'sensitive' | None}, mined
from every choose -c/-C cfg and then the xa_commands blocks.  A -c/-C path
keeps its spelling in Choose.cfgs; it is read with $VAR, ${VAR} (an unset
one is an error, as for `include) and a leading ~ expanded, from the cwd,
else beside the control file holding the choose.

map_by_node r=<ohms> node=<net> (PAMS p243) is an IeRule of kind
"map_by_node" in AmsConfig.rules, checked by rules.check_rule like the
a2d/d2a rules it sits among (rules.py applies it: the D2A series resistance).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

from vamos.ams import rules
from vamos.ams.config import TNF, AmsConfig, Choose, IeRule, PortConnect, PortDir, UseSpice
from vamos.netlist.numbers import is_number
from vamos.notes import Note, error, has_errors, note, warning

INI_NAME = "snps_vcsAD.ini"
DEFAULT_CONTROL = "vcsAD.init"
SKIP = float("nan")                  # AmsConfig.ref_voltages volts of a skip_node= entry
MAX_INCLUDE_DEPTH = 32

DEFAULT_ENGINES = ("xa", "finesim", "primesim", "hsim", "nanosim")   # -> the default analog engine
VAMOS_ENGINES = ("vacask", "xyce")
_NETLIST_OPTS = ("-n", "-hspice", "-nspice")
_VALUE_OPTS = _NETLIST_OPTS + ("-c", "-C", "-o", "-wavefmt", "-afile")
_PORT_MODES = ("snps_by_name", "snps_by_position", "snps_open")

# -- command dispositions (§2.2) ------------------------------------------------------

_RAW = {"netlist_commands_begin": "netlist_commands_end", "xa_commands_begin": "xa_commands_end"}
_RAW_ENDS = set(_RAW.values())
_EOL_STATEMENTS = {"`include"} | set(_RAW)       # end at the end of their line even without ';'

_HANDLED = {"choose", "use_spice", "partition", "bus_format", "set", "port_dir", "port_connect",
            "a2d", "d2a", "map_by_node", "remove_d2a", "disable_ie", "transient_analysis",
            "ie_reference_voltage", "downgrade_to_warn", "upgrade_to_error",
            "`include"} | set(_RAW) | _RAW_ENDS

_V1_ONLY = "Verilog-top designs with SPICE cells only in v1"
_ERRORS = {
    "use_verilog": "a Verilog view under a SPICE parent is not supported (%s)" % _V1_ONLY,
    "use_vcs": "a Verilog view under a SPICE parent is not supported (%s)" % _V1_ONLY,
    "use_vhdl": "VHDL views are not supported in v1",
    "use_veriloga": "Verilog-A views chosen by use_veriloga are not supported in v1 (instantiate "
                    "the Verilog-A model from the SPICE netlist with .hdl)",
    "spice_top": "SPICE-top designs are not supported (%s)" % _V1_ONLY,
    "dynamic_supply_filter": "dynamic supplies are not supported in v1",
}
_REAL_IE = "real-number and nettype interface elements are not supported in v1"
_RT_IE = "runtime interface elements are never created (no $hdl_xmr or force into SPICE in v1)"
_RMAP = ("the resistance map is not read: a D2A's series resistance is 500.7 ohm (rmap strength 6) "
         "unless map_by_node sets it")
_WARNINGS = {
    "e2r": _REAL_IE, "r2e": _REAL_IE, "e2n": _REAL_IE, "n2e": _REAL_IE, "udn_e2n": _REAL_IE,
    "udn_n2e": _REAL_IE, "nettype_map": _REAL_IE,
    "e2u": "UPF supply interface elements are not supported", "u2e": "UPF supply interface "
    "elements are not supported",
    "insert_cell": "no cell is inserted at the interface net",
    "rmap_file": _RMAP,
    "rt_a2d": _RT_IE, "rt_d2a": _RT_IE, "rt_e2n": _RT_IE, "rt_e2r": _RT_IE, "rt_n2e": _RT_IE,
    "rt_r2e": _RT_IE,
    "ams_cm": "Verilog-AMS flow command", "ams_set_discipline": "Verilog-AMS flow command",
    "ams_supply": "Verilog-AMS flow command", "ams_cdef_net": "Verilog-AMS flow command",
    "ams_cdef_inst": "Verilog-AMS flow command",
}
_NO_XMR = "there are no hierarchical references into SPICE in v1"
_NOTES = {
    "report_option": "interface_element.rpt is always written with its verbose comments",
    "ie_activity_rpt": "only interface_element.rpt is written",
    "ie_connect_rpt": "only interface_element.rpt is written",
    "ie_tracing_rpt": "only interface_element.rpt is written (its '// levels:' lines say where "
                      "every level came from)",
    "param_pass": "Verilog parameters never reach SPICE (an override on a SPICE instance is an "
                  "error)",
    "resolve_x_inst_prefix": _NO_XMR, "form_spice_bus": _NO_XMR, "skip_xmr_name_check": _NO_XMR,
    "optimize_shadowfile": "vamos builds its own shell modules",
    "shadow_file": "vamos builds its own shell modules",
    "shadow_file_dir": "vamos builds its own shell modules; edited shadow modules are not read",
    "duplicate_net_inst_name": "SPICE net and instance names never become Verilog names",
    "print_ie_res": "no analog drive-strength calculation is made",
    "gen_spice_wrapper": "vamos reads the Verilog headers itself",
}
# Words that end a statement missing its ';' when they start a line (§2.1).
_RECOVERY = _HANDLED | set(_ERRORS) | set(_WARNINGS) | set(_NOTES)


def _family(word: str) -> Tuple[str, str]:
    """(disposition, reason) of a command not in the tables: the ams_*, ie_*_rpt and
    shadow_file* families of §2.2, else ('unknown', '')."""
    if word.startswith("ams_"):
        return "warning", "Verilog-AMS flow command"
    if word.startswith("ie_") and word.endswith("_rpt"):
        return "note", "only interface_element.rpt is written"
    if word.startswith("shadow_file"):
        return "note", "vamos builds its own shell modules"
    return "unknown", ""


# -- lexer --------------------------------------------------------------------------------

_WORD = re.compile(r"`?[A-Za-z_][A-Za-z0-9_]*")
_LIST_ITEM = re.compile(r"(?:\[[^\]]*\]|<[^>]*>)?\s*[=,)}.:]")


def _starts_command(line: str, m: "re.Match[str]") -> bool:
    """A line-initial keyword that begins a command, not a name in a list.

    A port may be called set, choose or d2a: "set => SET,", "set[0] => s" or
    "set, rst;" inside port_map (...) or port_dir (...) is a list item, so a
    keyword followed (after an optional [i] or <i>) by = , ) } . or : never
    ends a statement; and 'set' must be 'set bus_format'.
    """
    word = m.group(0).lower()
    if word not in _RECOVERY:
        return False
    rest = line[m.end():]
    if _LIST_ITEM.match(rest.lstrip()):
        return False
    if word == "set":
        return re.match(r"\s+bus_format\b", rest, re.I) is not None
    return True


@dataclass
class Statement:
    keyword: str                     # first word, lowercased ('`include' included)
    text: str                        # without its ';', comments removed, line breaks kept
    origin: str                      # "<file>:<line>" of the first word
    raw: List[Tuple[str, str]] = field(default_factory=list)   # raw blocks: (line, origin)


def lex(text: str, label: str, notes: List[Note]) -> List[Statement]:
    """Split a control file into statements (§2.1); problems are appended to notes."""
    out: List[Statement] = []
    cur: List[str] = []
    started = False                  # cur holds a statement's first word
    kw = ""
    line0 = 0
    depth = 0
    in_comment = False
    comment_origin = ""
    raw: Optional[Statement] = None
    raw_end = ""

    def origin(ln: int) -> str:
        return "%s:%d" % (label, ln)

    def finish() -> Optional[Statement]:
        nonlocal cur, started, kw, line0
        st = None
        txt = "".join(cur).strip()
        if started and txt:
            st = Statement(kw, txt, origin(line0))
            out.append(st)
        cur, started, kw, line0 = [], False, "", 0
        return st

    for ln, line in enumerate(text.split("\n"), 1):
        if line.endswith("\r"):
            line = line[:-1]
        i, n = 0, len(line)
        at_start = True
        if raw is not None:
            stripped = line.strip()
            first = re.split(r"[\s;]+", stripped, maxsplit=1)[0].lower() if stripped else ""
            if first != raw_end:
                raw.raw.append((line, origin(ln)))
                continue
            raw = None
            i = line.lower().find(raw_end) + len(raw_end)
            j = i
            while j < n and line[j] in " \t":
                j += 1
            if j < n and line[j] == ";":
                i = j + 1
            at_start = False
        quote = False
        while i < n:
            c = line[i]
            if in_comment:
                j = line.find("*/", i)
                if j < 0:
                    i = n
                    break
                in_comment = False
                cur.append(" ")
                i = j + 2
                continue
            if quote:
                cur.append(c)
                quote = c != '"'
                i += 1
                continue
            if c.isspace():
                cur.append(c)
                i += 1
                continue
            if line.startswith("//", i):
                break
            if line.startswith("/*", i):
                in_comment, comment_origin = True, origin(ln)
                i += 2
                continue
            if at_start:
                at_start = False
                m = _WORD.match(line, i)
                if m and _starts_command(line, m):
                    if depth > 0:
                        o = origin(line0)
                        notes.append(error(o, "unterminated '(' in %s at %s" % (kw or "statement", o)))
                        cur, started, kw, line0, depth = [], False, "", 0, 0
                    elif started:
                        finish()
            if c in ")}" and depth == 0:
                notes.append(error(origin(ln), "unbalanced '%s'%s" % (c, " in " + kw if kw else "")))
                i += 1
                continue
            if not started:
                m = _WORD.match(line, i)
                started, kw, line0 = True, (m.group(0).lower() if m else c), ln
            if c == '"':
                quote = True
            elif c in "({":
                depth += 1
            elif c in ")}":
                depth -= 1
            elif c == ";" and depth == 0:
                st = finish()
                i += 1
                if st is not None and st.keyword in _RAW:
                    raw, raw_end = st, _RAW[st.keyword]
                    if line[i:].strip():
                        raw.raw.append((line[i:], origin(ln)))
                    i = n
                continue
            cur.append(c)
            i += 1
        if quote:
            notes.append(error(origin(ln), "unterminated string in %s" % (kw or "statement")))
            cur.append('"')
        if raw is None and started and kw in _EOL_STATEMENTS and depth == 0:
            st = finish()
            if st is not None and st.keyword in _RAW:
                raw, raw_end = st, _RAW[st.keyword]
        else:
            cur.append("\n")

    if raw is not None:
        notes.append(error(raw.origin, "%s at %s has no %s" % (raw.keyword, raw.origin, raw_end)))
        out = [s for s in out if s is not raw]
    if in_comment:
        notes.append(error(comment_origin, "unterminated /* comment"))
    if depth > 0:
        o = origin(line0)
        notes.append(error(o, "unterminated '(' in %s at %s" % (kw or "statement", o)))
    elif started:
        finish()
    return out


# -- tokens -------------------------------------------------------------------------------

def _close(text: str, i: int) -> int:
    """Index after the bracket matching text[i] ('(' or '{'), or len(text)."""
    depth, quote, n = 0, False, len(text)
    while i < n:
        c = text[i]
        if quote:
            quote = c != '"'
        elif c == '"':
            quote = True
        elif c in "({":
            depth += 1
        elif c in ")}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def tokens(text: str) -> List[str]:
    """Words, "quoted" runs (kept inside their word) and (...) / {...} groups.

    Commas between words separate like blanks.  A Verilog escaped name runs
    to the next blank and loses its backslash; a following '.' or '[' goes on
    with the same word: \\i1<1> .i2 -> i1<1>.i2 (PAMS p298).
    """
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace() or c == ",":
            i += 1
            continue
        if c in "({":
            j = _close(text, i)
            out.append(text[i:j])
            i = j
            continue
        if c in ")}":
            out.append(c)
            i += 1
            continue
        buf: List[str] = []
        j = i
        while j < n:
            c = text[j]
            if c == "$" and text.startswith("{", j + 1):     # ${VAR} stays in its word
                k = text.find("}", j)
                k = n if k < 0 else k + 1
                buf.append(text[j:k])
                j = k
                continue
            if c == '"':
                k = text.find('"', j + 1)
                k = n if k < 0 else k + 1
                buf.append(text[j:k])
                j = k
                continue
            if c == "\\":
                k = j + 1
                while k < n and not text[k].isspace():
                    k += 1
                buf.append(text[j + 1:k])
                m = k
                while m < n and text[m] in " \t":
                    m += 1
                if m < n and text[m] in ".[":
                    j = m
                    continue
                j = k
                break
            if c.isspace() or c in ",(){}":
                break
            buf.append(c)
            j += 1
        out.append("".join(buf))
        i = j
    return out


def _kv(toks: Sequence[str]) -> List[str]:
    """Join 'k = v', 'k= v' and 'k =v' into 'k=v' (PAMS writes "lov= 0.2")."""
    out: List[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if t == "=" and out and nxt is not None and "=" not in nxt:
            out[-1] += "=" + nxt
            i += 2
            continue
        if t.startswith("=") and not t.startswith("=>") and out:
            out[-1] += t
            i += 1
            continue
        if (t.endswith("=") and not t.endswith("=>") and nxt is not None and "=" not in nxt
                and nxt[:1] not in "({)}"):
            out.append(t + nxt)
            i += 2
            continue
        out.append(t)
        i += 1
    return out


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        return s[1:-1]
    return s


def _split_top(body: str, sep: str) -> List[str]:
    """Split on sep outside quotes and nested brackets."""
    parts: List[str] = []
    depth, quote, start = 0, False, 0
    for i, c in enumerate(body):
        if quote:
            quote = c != '"'
        elif c == '"':
            quote = True
        elif c in "({[":
            depth += 1
        elif c in ")}]":
            depth -= 1
        elif c == sep and depth == 0:
            parts.append(body[start:i])
            start = i + 1
    parts.append(body[start:])
    return parts


def _group_body(group: str) -> Optional[str]:
    """The text inside '( ... )', None if the group is not closed."""
    if len(group) >= 2 and group[0] == "(" and group[-1] == ")":
        return group[1:-1]
    return None


_ENV = re.compile(r"\$\{(\w+)\}|\$(\w+)")


def _expand(s: str, env: Mapping[str, str], missing: List[str]) -> str:
    def sub(m: "re.Match[str]") -> str:
        name = m.group(1) or m.group(2)
        if name not in env:
            missing.append(name)
            return ""
        return env[name]
    return _ENV.sub(sub, s)


# -- the parser ---------------------------------------------------------------------------

_PM_SRC = re.compile(r"^(?:\*|[A-Za-z_][A-Za-z0-9_$]*(?:\[-?\d+\])?)$")
_BUS_FORMAT = re.compile(r"^[<\[_]?%d[>\]]?$")


class _Parser:
    def __init__(self, cfg: AmsConfig, cwd: str, env: Mapping[str, str]):
        self.cfg = cfg
        self.cwd = cwd
        self.env = env
        self.notes = cfg.notes
        self.stack: List[str] = []              # realpaths being read (include cycles)
        self.xa_lines: List[Tuple[str, str]] = []
        self.choose_dir = cwd                   # directory of the file holding the last choose
        self.bus_origin = ""

    # -- files ------------------------------------------------------------------

    def label(self, path: str) -> str:
        """A file as origins name it: relative to the directory vcs runs in."""
        try:
            return os.path.relpath(path, self.cwd)
        except ValueError:                      # another drive
            return path

    def read(self, path: str, depth: int, origin: str = "") -> None:
        real = os.path.realpath(path)
        if real in self.stack:
            self.notes.append(error(origin or path, "`include cycle: %s includes itself"
                                    % self.label(path)))
            return
        if depth > MAX_INCLUDE_DEPTH:
            self.notes.append(error(origin or path, "`include nested deeper than %d"
                                    % MAX_INCLUDE_DEPTH))
            return
        try:
            with open(path, errors="replace") as fh:
                text = fh.read()
        except OSError as e:
            self.notes.append(error(origin or path, "cannot read control file %s: %s"
                                    % (path, e.strerror or e)))
            return
        self.cfg.files.append(path)
        self.stack.append(real)
        here = os.path.dirname(path)
        for st in lex(text, self.label(path), self.notes):
            self.statement(st, depth, here)
        self.stack.pop()

    def expand_path(self, name: str, missing: List[str]) -> str:
        """A path as written in the control file with $VAR and ${VAR} expanded from the
        environment given to parse_control (each unset name is appended to missing),
        and a leading ~ or ~user expanded to that home directory."""
        p = _expand(name, self.env, missing)
        if p.startswith("~"):
            home = self.env.get("HOME")
            if home and (p == "~" or p.startswith("~/")):
                p = home + p[1:]
            else:
                p = os.path.expanduser(p)
        return p

    def find(self, name: str, other_dir: str, origin: str) -> Tuple[Optional[str], List[str]]:
        """A path as §4.3.1 resolves it: cwd, then the referring file's directory."""
        if os.path.isabs(name):
            return (name if os.path.isfile(name) else None), [name]
        tried: List[str] = []
        found: List[str] = []
        for d in (self.cwd, other_dir):
            p = os.path.normpath(os.path.join(d, name))
            if p in tried:
                continue
            tried.append(p)
            if os.path.isfile(p):
                found.append(p)
        if len(found) > 1 and os.path.realpath(found[0]) != os.path.realpath(found[1]):
            self.notes.append(note(origin, "%s: %s is used; %s also exists"
                                   % (name, self.label(found[0]), self.label(found[1]))))
        return (found[0] if found else None), tried

    # -- dispatch ---------------------------------------------------------------

    def statement(self, st: Statement, depth: int, here: str) -> None:
        kw = st.keyword
        if kw == "`include":
            self.include(st, depth)
            return
        if kw in _RAW:
            extra = st.text[len(kw):].strip()
            if extra:
                self.notes.append(error(st.origin, "%s takes no arguments (%r)" % (kw, extra)))
                return
            if kw == "netlist_commands_begin":
                self.cfg.netlist_lines.extend(st.raw)
            else:
                self.xa_lines.extend(st.raw)
            return
        if kw in _RAW_ENDS:
            self.notes.append(error(st.origin, "%s without %s" % (kw, kw.replace("_end", "_begin"))))
            return
        toks = tokens(st.text)
        handler = getattr(self, "cmd_" + kw, None) if kw in _HANDLED else None
        if handler is not None:
            if kw == "choose":
                self.choose_dir = here
            handler(st, toks)
            return
        if kw in _ERRORS:
            self.notes.append(error(st.origin, "%s: %s" % (kw, _ERRORS[kw])))
            return
        if kw in _WARNINGS:
            self.notes.append(warning(st.origin, "%s is ignored: %s" % (kw, _WARNINGS[kw])))
            return
        if kw in _NOTES:
            self.notes.append(note(st.origin, "%s has no effect: %s" % (kw, _NOTES[kw])))
            return
        disp, why = _family(kw)
        if disp == "warning":
            self.notes.append(warning(st.origin, "%s is ignored: %s" % (kw, why)))
        elif disp == "note":
            self.notes.append(note(st.origin, "%s has no effect: %s" % (kw, why)))
        elif _WORD.fullmatch(kw):
            self.notes.append(error(st.origin, "unknown command '%s'" % kw))
        else:
            self.notes.append(error(st.origin, "unrecognised text %r" % st.text[:40]))

    def include(self, st: Statement, depth: int) -> None:
        arg = st.text[len("`include"):].strip()
        toks = tokens(arg)
        if len(toks) != 1:
            self.notes.append(error(st.origin, "`include takes one file name"))
            return
        missing: List[str] = []
        name = _expand(_unquote(toks[0]), self.env, missing)
        if missing:
            self.notes.append(error(st.origin, "`include %s: environment variable %s is not set"
                                    % (toks[0], ", ".join(sorted(set(missing))))))
            return
        path = name if os.path.isabs(name) else os.path.normpath(os.path.join(self.cwd, name))
        if not os.path.isfile(path):
            self.notes.append(error(st.origin, "`include file %s not found (relative names are "
                                    "taken against the directory vcs runs in)" % path))
            return
        self.read(path, depth + 1, st.origin)

    # -- commands -----------------------------------------------------------------

    def cmd_choose(self, st: Statement, toks: List[str]) -> None:
        o = st.origin
        args = toks[1:]
        if not args or args[0].startswith("-"):
            self.notes.append(error(o, "choose needs an analog engine (xa, finesim, primesim, "
                                    "hsim, nanosim, vacask or xyce)"))
            return
        engine = args[0].lower()
        if engine not in DEFAULT_ENGINES + VAMOS_ENGINES:
            self.notes.append(error(o, "choose: unknown analog engine '%s' (expected xa, finesim, "
                                    "primesim, hsim, nanosim, vacask or xyce)" % args[0]))
            return
        ch = Choose(engine, origin=o)
        nspice = False
        i = 1
        while i < len(args):
            a = args[i]
            if a[:1] in "({)}":
                self.notes.append(error(o, "choose: unexpected '%s'" % a))
                i += 1
                continue
            if a.startswith("-") and len(a) > 1:
                if a in _VALUE_OPTS:
                    if i + 1 >= len(args):
                        self.notes.append(error(o, "choose: %s needs a value" % a))
                        break
                    val = _unquote(args[i + 1])
                    i += 2
                    if a in _NETLIST_OPTS:
                        ch.netlists.append(val)
                        nspice = nspice or a == "-nspice"
                    elif a in ("-c", "-C"):
                        ch.cfgs.append(val)
                    elif a == "-o":
                        if ch.out_prefix is not None:
                            self.notes.append(note(o, "choose: -o %s replaces -o %s" % (val, ch.out_prefix)))
                        ch.out_prefix = val
                    elif a == "-wavefmt":
                        ch.options.append("%s %s" % (a, val))
                        self.notes.append(note(o, "choose: -wavefmt %s is ignored: the analog "
                                               "output is a SPICE rawfile" % val))
                    else:                                       # -afile
                        ch.options.append("%s %s" % (a, val))
                        self.notes.append(warning(o, "choose: -afile %s is ignored: the file is "
                                                  "not read" % val))
                    continue
                if a == "-spice":
                    ch.options.append(a)
                    self.notes.append(note(o, "choose: -spice (FineSim/PrimeSim SPICE mode) has no "
                                           "effect: the analog engine always runs full SPICE"))
                elif a == "-skipdc":
                    ch.uic = True
                else:
                    ch.options.append(a)
                    self.notes.append(warning(o, "choose: option %s is ignored (it takes no "
                                              "value here)" % a))
                i += 1
                continue
            ch.netlists.append(_unquote(a))
            i += 1
        if nspice:
            ch.dialect = "spice"
            self.notes.append(warning(o, "choose: -nspice netlists are parsed as HSPICE in v1"))
        if self.cfg.choose is not None:
            self.notes.append(note(o, "choose replaces the choose at %s (the command read later "
                                   "wins)" % self.cfg.choose.origin))
        self.cfg.choose = ch

    def cmd_use_spice(self, st: Statement, toks: List[str]) -> None:
        o = st.origin
        kw = toks[0].lower()
        us = UseSpice(origin=o)
        bad = False
        mode = ""
        groups: Set[str] = set()
        i = 1
        while i < len(toks):
            t = toks[i]
            low = t.lower()
            if low in ("-cell", "-inst"):
                mode = low[1:]
                i += 1
                continue
            if low in ("port_map", "port_index_order"):
                mode = ""
                grp = toks[i + 1] if i + 1 < len(toks) else ""
                body = _group_body(grp) if grp.startswith("(") else None
                if body is None:
                    self.notes.append(error(o, "%s: %s needs a ( ... ) list" % (kw, low)))
                    bad = True
                    i += 1
                    continue
                if low in groups:
                    self.notes.append(error(o, "%s: %s given twice" % (kw, low)))
                    bad = True
                groups.add(low)
                if low == "port_map":
                    bad = not self.port_map(kw, body, o, us) or bad
                else:
                    bad = not self.index_order(kw, body, o, us) or bad
                i += 2
                continue
            if t[:1] in "({)}" or t.startswith("-"):
                self.notes.append(error(o, "%s: unexpected '%s'" % (kw, t)))
                bad = True
                mode = ""
            elif mode == "cell":
                cell, colon, sub = t.partition(":")
                if not cell or (colon and not sub):
                    self.notes.append(error(o, "%s: bad -cell name '%s' (cell or cell:subckt)" % (kw, t)))
                    bad = True
                else:
                    us.cells.append((cell, sub))
            elif mode == "inst":
                us.insts.append(t)
            else:
                self.notes.append(error(o, "%s: unexpected '%s'" % (kw, t)))
                bad = True
            i += 1
        if not us.cells:
            self.notes.append(error(o, "%s needs -cell <cell>..." % kw))
            bad = True
        if not bad:
            self.cfg.use_spice.append(us)

    cmd_partition = cmd_use_spice

    def port_map(self, kw: str, body: str, o: str, us: UseSpice) -> bool:
        ok = True
        seen: Set[str] = set()
        for item in _split_top(body, ","):
            it = item.strip()
            if not it:
                continue
            if "=>" not in it:
                self.notes.append(error(o, "%s port_map: '%s' is not 'verilog => spice'" % (kw, it)))
                ok = False
                continue
            src, tgt = (x.strip() for x in it.split("=>", 1))
            src = re.sub(r"\s+", "", src)
            low = tgt.lower()
            why = ""
            if src == "*":
                if low not in _PORT_MODES:
                    why = "'* =>' takes snps_by_name, snps_by_position or snps_open"
            elif ":" in src:
                why = "a Verilog part-select is not supported; map each bit with v[i] => s"
            elif not _PM_SRC.match(src):
                why = "expected a Verilog port or one bit of it (v or v[i])"
            elif low in ("snps_by_name", "snps_by_position"):
                why = "%s is only valid as '* => %s'" % (low, low)
            elif tgt.startswith("{"):
                why = "a concatenation is not supported; map each bit with v[i] => s"
            elif re.search(r"\[[^\]]*:[^\]]*\]\s*$", tgt):
                why = "a SPICE bus range is not supported; map each bit with v[i] => s"
            elif not tgt or re.search(r"[\s(){},]", tgt):
                why = "expected one SPICE port name"
            if why:
                self.notes.append(error(o, "%s port_map: '%s': %s" % (kw, it, why)))
                ok = False
                continue
            if src.lower() in seen:
                self.notes.append(error(o, "%s port_map: %s is mapped twice" % (kw, src)))
                ok = False
                continue
            seen.add(src.lower())
            us.port_map.append((src, tgt))
        return ok

    def index_order(self, kw: str, body: str, o: str, us: UseSpice) -> bool:
        ok = True
        for item in _split_top(body, ","):
            it = item.strip()
            if not it:
                continue
            m = re.match(r"^(\*|[A-Za-z_][A-Za-z0-9_$]*)\s*=>\s*(\w+)$", it)
            if not m or m.group(2).lower() not in ("same", "dec", "inc"):
                self.notes.append(error(o, "%s port_index_order: '%s' is not 'port => same|dec|inc'"
                                        % (kw, it)))
                ok = False
                continue
            us.index_order.append((m.group(1), m.group(2).lower()))
        return ok

    def cmd_bus_format(self, st: Statement, toks: List[str]) -> None:
        self.bus_format(st, toks[1:])

    def cmd_set(self, st: Statement, toks: List[str]) -> None:
        if len(toks) > 1 and toks[1].lower() == "bus_format":
            self.bus_format(st, toks[2:])
        else:
            self.notes.append(error(st.origin, "unknown command 'set %s' (only 'set bus_format' "
                                    "is known)" % (toks[1] if len(toks) > 1 else "")))

    def bus_format(self, st: Statement, fmts: List[str]) -> None:
        o = st.origin
        if not fmts:
            self.notes.append(error(o, "bus_format needs a format such as <%d>"))
            return
        bad = [f for f in fmts if not _BUS_FORMAT.match(f)]
        if bad:
            self.notes.append(error(o, "bus_format %s: a format is [open]%%d[close] with < > [ ] _ "
                                    "only" % " ".join(bad)))
            return
        if self.cfg.bus_formats and self.cfg.bus_formats != fmts:
            self.notes.append(note(o, "bus_format replaces the bus_format at %s" % self.bus_origin))
        self.cfg.bus_formats = list(fmts)
        self.bus_origin = o

    def cmd_port_dir(self, st: Statement, toks: List[str]) -> None:
        o = st.origin
        cell = None
        body = None
        i = 1
        bad = False
        while i < len(toks):
            t = toks[i]
            if t.lower() == "-cell" and i + 1 < len(toks) and toks[i + 1][:1] not in "({":
                if cell is not None:
                    self.notes.append(error(o, "port_dir: -cell given twice"))
                    bad = True
                cell = toks[i + 1]
                i += 2
                continue
            if t.startswith("(") and body is None:
                body = _group_body(t)
                if body is None:
                    self.notes.append(error(o, "port_dir: unbalanced '('"))
                    bad = True
                i += 1
                continue
            self.notes.append(error(o, "port_dir: unexpected '%s'" % t))
            bad = True
            i += 1
        if cell is None or (body is None and not bad):
            self.notes.append(error(o, "port_dir needs -cell <cell> (input|output|inout ports; ...)"))
            return
        if bad or body is None:
            return
        dirs: Dict[str, str] = {}
        for grp in _split_top(body, ";"):
            words = [w for w in re.split(r"[\s,]+", grp.strip()) if w]
            if not words:
                continue
            d = words[0].lower()
            if d not in ("input", "output", "inout"):
                self.notes.append(error(o, "port_dir: '%s' should start with input, output or inout"
                                        % grp.strip()))
                bad = True
                continue
            if len(words) == 1:
                self.notes.append(error(o, "port_dir: '%s' lists no ports" % d))
                bad = True
                continue
            for p in words[1:]:
                pl = p.lower()
                if dirs.get(pl, d) != d:
                    self.notes.append(error(o, "port_dir: port %s is both %s and %s" % (p, dirs[pl], d)))
                    bad = True
                dirs[pl] = d
        if bad:
            return
        key = cell.lower()
        old = self.cfg.port_dirs.get(key)
        if old is None:
            self.cfg.port_dirs[key] = PortDir(cell, dirs, o)
        else:                                       # later commands win, port by port
            old.dirs.update(dirs)
            old.origin = "%s, %s" % (old.origin, o)

    def cmd_port_connect(self, st: Statement, toks: List[str]) -> None:
        o = st.origin
        cell = None
        insts: List[str] = []
        body = None
        bad = False
        i = 1
        while i < len(toks):
            t = toks[i]
            low = t.lower()
            if low == "-cell" and i + 1 < len(toks) and toks[i + 1][:1] not in "({-":
                cell = toks[i + 1]
                i += 2
                continue
            if low == "-inst":
                i += 1
                while i < len(toks) and toks[i][:1] not in "({)}-":
                    insts.append(toks[i])
                    i += 1
                continue
            if t.startswith("(") and body is None:
                body = _group_body(t)
                if body is None:
                    self.notes.append(error(o, "port_connect: unbalanced '('"))
                    bad = True
                i += 1
                continue
            self.notes.append(error(o, "port_connect: unexpected '%s'" % t))
            bad = True
            i += 1
        if cell is None or body is None:
            if not bad:
                self.notes.append(error(o, "port_connect needs -cell <cell> (port => net, ...)"))
            return
        conns: List[Tuple[str, str, bool]] = []
        seen: Set[str] = set()
        for item in _split_top(body, ","):
            it = item.strip()
            if not it:
                continue
            m = re.match(r"^-inst\s+(\S+)\s*(.*)$", it, re.S)
            if m:                                   # PAMS p256: ([-inst i] [real] p => net, ...)
                insts.append(m.group(1))
                it = m.group(2).strip()
                if not it:
                    continue
            real = False
            m = re.match(r"^real\s+(?!=>)(.*)$", it, re.S | re.I)
            if m:
                real, it = True, m.group(1).strip()
            if "=>" not in it:
                self.notes.append(error(o, "port_connect: '%s' is not 'spice_port => net'" % item.strip()))
                bad = True
                continue
            p, net = (x.strip() for x in it.split("=>", 1))
            if not p or not net or re.search(r"\s", p + net):
                self.notes.append(error(o, "port_connect: '%s' is not 'spice_port => net'" % item.strip()))
                bad = True
                continue
            if p.lower() in seen:
                self.notes.append(error(o, "port_connect: port %s is connected twice" % p))
                bad = True
                continue
            seen.add(p.lower())
            net = _unquote(net)
            conns.append((p, "snps_open" if net.lower() == "snps_open" else net, real))
        if bad:
            return
        if not conns:
            self.notes.append(error(o, "port_connect lists no connection"))
            return
        for inst in insts or [None]:
            self.cfg.port_connects.append(PortConnect(cell, inst, list(conns), o))

    def _keyvals(self, st: Statement, toks: List[str]) -> Optional[List[Tuple[str, str]]]:
        """[(key lowercased, value or '' for a flag)] of a key=value statement; None on error."""
        out: List[Tuple[str, str]] = []
        ok = True
        for t in _kv(toks[1:]):
            if t[:1] in "({)}" or t.startswith("="):
                self.notes.append(error(st.origin, "%s: unexpected '%s'" % (st.keyword, t)))
                ok = False
                continue
            if "=" in t:
                k, v = t.split("=", 1)
                v = _unquote(v)
                if not v:
                    self.notes.append(error(st.origin, "%s: %s= needs a value" % (st.keyword, k)))
                    ok = False
                    continue
                out.append((k.lower(), v))
            else:
                out.append((t.lower(), ""))
        return out if ok else None

    def cmd_a2d(self, st: Statement, toks: List[str]) -> None:
        kvs = self._keyvals(st, toks)
        if kvs is None:
            return
        rule = IeRule(st.keyword, origin=st.origin)
        for k, v in kvs:
            if k in rules.SELECTORS:
                if not v:
                    self.notes.append(error(st.origin, "%s: %s needs a value" % (st.keyword, k)))
                    return
                if getattr(rule, k) is not None:
                    self.notes.append(note(st.origin, "%s: %s= given twice; the last value is used"
                                           % (st.keyword, k)))
                setattr(rule, k, v)
            else:
                if k in rule.params:
                    self.notes.append(note(st.origin, "%s: %s given twice; the last value is used"
                                           % (st.keyword, k)))
                rule.params[k] = v
        found = rules.check_rule(rule)
        self.notes.extend(found)
        if not has_errors(found):
            self.cfg.rules.append(rule)

    cmd_d2a = cmd_a2d
    # map_by_node r=<ohms> node=<net> (PAMS p243): an IeRule of kind "map_by_node" in
    # cfg.rules, in file order with the a2d/d2a rules (rules.py applies and checks it)
    cmd_map_by_node = cmd_a2d

    def _node_only(self, st: Statement, toks: List[str], allowed: Sequence[str]) -> Optional[Dict[str, str]]:
        kvs = self._keyvals(st, toks)
        if kvs is None:
            return None
        got: Dict[str, str] = {}
        ok = True
        for k, v in kvs:
            if k not in allowed:
                self.notes.append(error(st.origin, "%s: unknown key %s" % (st.keyword, k)))
                ok = False
            elif not v:
                self.notes.append(error(st.origin, "%s: %s needs a value" % (st.keyword, k)))
                ok = False
            else:
                got[k] = v
        return got if ok else None

    def _node_pattern(self, st: Statement, node: Optional[str]) -> bool:
        if node is None:
            self.notes.append(error(st.origin, "%s needs node=" % st.keyword))
            return False
        if node.strip("*") == "":
            self.notes.append(error(st.origin, "%s: node=%s is not allowed; a partial pattern such "
                                    "as top.din* is (PAMS p210, p264)" % (st.keyword, node)))
            return False
        return True

    def cmd_remove_d2a(self, st: Statement, toks: List[str]) -> None:
        got = self._node_only(st, toks, ("dc", "node"))
        if got is None or not self._node_pattern(st, got.get("node")):
            return
        dc: Optional[float] = None
        if "dc" in got:
            try:
                v, pct = rules.parse_level(got["dc"])
            except ValueError:
                v, pct = 0.0, True
            if pct:
                self.notes.append(error(st.origin, "remove_d2a: dc=%s is not a voltage" % got["dc"]))
                return
            dc = v
        self.cfg.remove_d2a.append((got["node"], dc, st.origin))

    def cmd_disable_ie(self, st: Statement, toks: List[str]) -> None:
        got = self._node_only(st, toks, ("node",))
        if got is None or not self._node_pattern(st, got.get("node")):
            return
        self.cfg.disable_ie.append((got["node"], st.origin))

    def cmd_ie_reference_voltage(self, st: Statement, toks: List[str]) -> None:
        o = st.origin
        got = self._node_only(st, toks, ("node", "voltage", "skip_node"))
        if got is None:
            return
        for k in ("node", "skip_node"):
            if "*" in got.get(k, ""):
                self.notes.append(error(o, "ie_reference_voltage: %s=%s: wildcards are not "
                                        "supported" % (k, got[k])))
                return
        if "skip_node" in got:
            if len(got) > 1:
                self.notes.append(error(o, "ie_reference_voltage: skip_node= cannot be combined "
                                        "with node= or voltage= (PAMS p239)"))
                return
            self.cfg.ref_voltages.append((got["skip_node"], SKIP, o))
            return
        if "node" not in got:
            self.notes.append(error(o, "ie_reference_voltage needs node= or skip_node="))
            return
        volts: Optional[float] = None
        if "voltage" in got:
            try:
                volts, pct = rules.parse_level(got["voltage"])
            except ValueError:
                pct = True
            if pct:
                self.notes.append(error(o, "ie_reference_voltage: voltage=%s is not a voltage"
                                        % got["voltage"]))
                return
        self.cfg.ref_voltages.append((got["node"], volts, o))  # type: ignore[arg-type]

    def _severity(self, st: Statement, toks: List[str], sev: str) -> None:
        ids: List[str] = []
        for t in toks[1:]:
            ids += [p.strip().strip("[]").strip() for p in t.split("|")]
        ids = [i for i in ids if i]
        if not ids:
            self.notes.append(error(st.origin, "%s needs a message id such as %s" % (st.keyword, TNF)))
            return
        for mid in ids:
            if mid.upper() == TNF:
                self.cfg.severity_overrides[TNF] = sev
            else:
                self.notes.append(note(st.origin, "%s %s has no effect: only %s is reported by "
                                       "vamos" % (st.keyword, mid, TNF)))

    def cmd_downgrade_to_warn(self, st: Statement, toks: List[str]) -> None:
        self._severity(st, toks, "warning")

    def cmd_upgrade_to_error(self, st: Statement, toks: List[str]) -> None:
        self._severity(st, toks, "error")

    def cmd_transient_analysis(self, st: Statement, toks: List[str]) -> None:
        args = [t.lower() for t in toks[1:]]
        if args in (["$finish"], ["$stop"]):
            self.notes.append(note(st.origin, "transient_analysis %s: the run ends at the analog "
                                   "stop time, and also at $finish or $stop" % args[0]))
        else:
            self.notes.append(error(st.origin, "transient_analysis takes $finish or $stop"))

    # -- after every file -----------------------------------------------------------

    def mine(self) -> None:
        xa: Dict[str, object] = {"probe_v": [], "probe_i": [], "case": None}
        found: List[Dict[str, object]] = []
        ch = self.cfg.choose
        if ch is not None:
            for name in ch.cfgs:
                missing: List[str] = []
                full = self.expand_path(name, missing)
                if missing:
                    self.notes.append(error(ch.origin, "choose: XA cfg %s: environment variable %s "
                                            "is not set" % (name, ", ".join(sorted(set(missing))))))
                    continue
                path, tried = self.find(full, self.choose_dir, ch.origin)
                if path is None:
                    self.notes.append(error(ch.origin, "choose: XA cfg %s not found (tried %s)"
                                            % (name, ", ".join(tried))))
                    continue
                found.append(mine_xa_cfg(path, self.label(path)))
        if self.xa_lines:
            found.append(_mine_lines(self.xa_lines))
        for d in found:
            xa["probe_v"] += d["probe_v"]                               # type: ignore[operator]
            xa["probe_i"] += d["probe_i"]                               # type: ignore[operator]
            if d["case"] is not None:
                xa["case"] = d["case"]
            self.notes.extend(d["notes"])                               # type: ignore[arg-type]
        self.cfg.xa = xa

    def check_libraries(self) -> None:
        libs = sorted({r.library for r in self.cfg.rules if r.library})
        if len(libs) > 1:
            first = next(r for r in self.cfg.rules if r.library)
            self.notes.append(warning(first.origin, "rules name several libraries (%s); library= "
                                      "is ignored, so their cell=/port= selectors overlap and the "
                                      "last matching rule wins" % ", ".join(libs)))


def find_ini(cwd: str, env: Mapping[str, str]) -> Optional[str]:
    """snps_vcsAD.ini in cwd, else in $HOME (PAMS p124); None if neither exists."""
    dirs = [cwd]
    home = env.get("HOME")
    if home:
        dirs.append(home)
    for d in dirs:
        p = os.path.join(d, INI_NAME)
        if os.path.isfile(p):
            return p
    return None


def parse_control(paths: List[str], cwd: str, env: Mapping[str, str]) -> AmsConfig:
    """Read the control files in order ([ini?, adfile]) into one AmsConfig (§2).

    Relative paths are taken against cwd.  A command read later wins.  Every
    problem is a Note in cfg.notes; nothing raises for bad input.
    """
    cfg = AmsConfig()
    p = _Parser(cfg, cwd, env)
    if not paths:
        cfg.notes.append(error("", "no mixed-signal control file given"))
        return cfg
    seen: Set[str] = set()
    read: List[str] = []
    for name in paths:
        path = name if os.path.isabs(name) else os.path.normpath(os.path.join(cwd, name))
        real = os.path.realpath(path)
        if real in seen:
            cfg.notes.append(note(p.label(path), "control file %s is read once" % name))
            continue
        seen.add(real)
        if not os.path.isfile(path):
            cfg.notes.append(error(name, "mixed-signal control file %s not found" % path))
            continue
        before = len(cfg.files)
        p.read(path, 0)
        if len(cfg.files) > before:
            read.append(p.label(path))
    if len(read) > 1:
        cfg.notes.append(note(read[-1], "[MSV-MC-FRO] mixed-signal commands were read in this "
                              "order: %s (a command read later wins)" % ", ".join(read)))
    if read and cfg.choose is None:
        cfg.notes.append(error(read[-1], "the mixed-signal control file must contain at least "
                               "choose"))
    p.mine()
    p.check_libraries()
    return cfg


# -- XA cfg files --------------------------------------------------------------------------

def _xa_words(line: str) -> List[str]:
    out: List[str] = []
    for m in re.finditer(r'"[^"]*"|\S+', line):
        out.append(_unquote(m.group(0)))
    return out


def _mine_lines(lines: Sequence[Tuple[str, str]]) -> Dict[str, object]:
    """probe_waveform_voltage/_current patterns and set_sim_case from XA command lines."""
    probe_v: List[Tuple[str, str]] = []
    probe_i: List[Tuple[str, str]] = []
    case: Optional[str] = None
    notes: List[Note] = []
    ignored: Dict[str, str] = {}
    joined: List[Tuple[str, str]] = []
    pend, pend_origin = "", ""
    for line, origin in lines:                   # Tcl-style '\' continuation
        if pend:
            line, origin = pend + " " + line, pend_origin
        if line.rstrip().endswith("\\"):
            pend, pend_origin = line.rstrip()[:-1], origin
            continue
        pend = ""
        joined.append((line, origin))
    if pend:
        joined.append((pend, pend_origin))
    for line, origin in joined:
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("//"):
            continue
        words = _xa_words(s)
        cmd = words[0].lower()
        if cmd in ("probe_waveform_voltage", "probe_waveform_current"):
            pats: List[str] = []
            opts: List[str] = []
            j = 1
            while j < len(words):
                w = words[j]
                if w.startswith("-") and len(w) > 1 and not is_number(w):
                    if j + 1 < len(words) and is_number(words[j + 1]):
                        opts.append("%s %s" % (w, words[j + 1]))
                        j += 2
                    else:
                        opts.append(w)
                        j += 1
                    continue
                pats.append(w)
                j += 1
            if not pats:
                notes.append(note(origin, "%s names no node and is ignored" % cmd))
            (probe_v if cmd.endswith("voltage") else probe_i).extend((p, origin) for p in pats)
            if opts:
                notes.append(note(origin, "%s: %s not applied: every node matching the pattern is "
                                  "saved" % (cmd, " ".join(opts))))
        elif cmd == "set_sim_case":
            rest = [w for w in words[1:] if w.lower() != "-case"]
            val = rest[0].lower() if rest else ""
            if val in ("lower", "upper", "sensitive") and len(rest) == 1:
                case = val
            else:
                notes.append(warning(origin, "set_sim_case %s is not understood and is ignored "
                                     "(expected -case lower|upper|sensitive)" % " ".join(words[1:])))
        elif cmd not in ignored:                 # one note per command, at its first use
            ignored[cmd] = origin
            notes.append(note(origin, "XA command %s has no counterpart in vamos and is ignored"
                              % cmd))
    return {"probe_v": probe_v, "probe_i": probe_i, "case": case, "notes": notes}


def mine_xa_cfg(path: str, label: Optional[str] = None) -> Dict[str, object]:
    """{'probe_v': [(pattern, origin)], 'probe_i': [...], 'case': str|None, 'notes': [Note]}.

    Only probe_waveform_voltage/_current and set_sim_case are used; every
    other XA command gets a note.  An unreadable file is an error note.
    label names the file in origins (default: path).
    """
    label = label or path
    try:
        with open(path, errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        return {"probe_v": [], "probe_i": [], "case": None,
                "notes": [error(label, "cannot read XA cfg %s: %s" % (path, e.strerror or e))]}
    lines = [(ln.rstrip("\r"), "%s:%d" % (label, k)) for k, ln in enumerate(text.split("\n"), 1)]
    return _mine_lines(lines)
