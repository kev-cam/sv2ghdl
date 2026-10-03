"""The digital cut (docs/VAMOS_AMS_DESIGN.md §5.4, §5.5).

    design = vhdl.parse(<daidir>/nvc/design.vhd)
    analysis = cut.analyse(design, top, cells, nl, cfg, hits[, pp=pp][, directions=sh.directions])
    nodes = cut.assign_roles(analysis, alloc, disabled, removal)       # List[AnalogNode]
    # deck.py fills node.d2a / a2d / dc (rules.py); plan.bridges = names.build_bridges(plan)
    result = cut.emit(plan, layout.ams_dir(daidir))                    # CutEmitResult
    # helpers: cut.port5(analysis, portref) -> rules' Port5;
    #          cut.param_overrides(cell, inst) -> parameters differing from the defaults
    #          (any type; cut.vvalue evaluates Verilog constants, cut.veval integer ones)

analyse() maps the VHDL that iverilog-sv2ghdl made of the design back onto
the cut cells:

  * variants: entities whose provenance comment names a cut cell (exact
    Verilog spelling), whatever the entity is called;
  * cut instances: the instances bound to a variant, found by walking the
    elaborated tree from the top entity, depth first in textual order (the
    walk order).  Verilog paths come from T3's "-- Verilog instance:"
    comments, else the VHDL labels stand in (warning);
  * port binding per variant: by position, with hard checks (make_safe_name,
    kind, width against the header range evaluated with the variant's
    nvc_verilog_params, mode against shell_dir, a marker on every output bit);
  * nets: a union-find over (scope, signal, bit) segments, searched from every
    cut-port bit through user-module port maps, aliases, _Readable shadows,
    port temporaries and port buffers (below); then the drivers (with strength
    classes) and readers.
    Drivers are decided bit by bit: a Z element of a constant, a bit copied
    from a translator temporary that is only ever assigned Z (tgt-vhdl's
    `pa <= t5 & tmp_z & t3`), and the Z bit of a constant port actual drive
    nothing; a declared signal's initial value (reg [5:3] r = 3'b001) drives
    each bit that has no other source.  Verilog names, ranges and
    variable/tri0/tri1 kinds of the signals come from the module declarations
    (pp, or nvc/_norm.sv).
  * port buffers: an input port of a user module whose net a cut port
    (declared inout or output, or a multi-view cell's) also drives inside,
    connected to a variable, a select of one, an expression or a constant,
    reaches the parent as tgt-vhdl's `PB_<label>_<port> <= <actual>`
    (vhdl.SignalDecl.port_buffer).  As in Verilog the buffer is one-way: the
    cut port can drive only the wrapper's side of the port.  A plain copy is
    a one-way join, decided per elaborated copy and bit by what else is on
    the wrapper's side W (the port's net without the copy): when the inside
    drives W too, W is a net of its own (the copy drives it; its node says
    so); otherwise a cut output on W, or an inout one on a W that the inside
    also reads, is an error asking for port_dir input (what the cell drives
    there would reach only W); otherwise W joins the actual's net and its
    cut ports act as inputs on it (their drive reaches nothing that reads),
    exactly as with port_dir input, and the IE report says so.  A constant
    (not a copy) drives W.

Everything the later steps need but CutAnalysis does not hold (the parsed
design, the elaborated scopes, driver occurrences) is kept on the analysis
object as the private attribute `_cut` (not a dataclass field, so
dataclasses.asdict() of the analysis stays small).  CutInstance.params holds
every parameter value of the instance's variant, integer, real or string, as
tgt-vhdl printed it: the integers from nvc_verilog_params, the rest from the
"--   P = v" lines of the top's sv2vhdl-modules run (vhdl.read_translation_runs;
design.vhd may keep another run's lines for a variant), else of design.vhd.
What param_overrides() compares them with rides on the instance as private
attributes (_param_values, _param_defaults, _param_locals).

assign_roles() applies the ordered role table of §5.4 (step 5), picks each
node's host bit, applies remove_d2a and emits the warnings.  The `names`
list it passes to disabled()/removal() has the canonical name first, then the
aliases.  AnalogNode.shunt is set by role (deck.py clears it for a powernet
D2A); POWERNET nodes leave dc to deck.py (the supply class is on the net's
drivers).  Notes are appended to analysis.notes; errors raise NoteError.
The IE report's direction lines (AnalogNode.report) take the direction
probe's reason for an auto port that probe step 2b made an input from
shells.ShellResult.directions, when the caller passes it (analyse() or
assign_roles() `directions=`); without it every auto input or output says
"(variable actual)".

emit() writes <out_dir>/cut.vhd (vams_cut_pkg, one clone per variant, the
re-pointed parent architectures, in that order) and <out_dir>/vamos.boundary
(exactly one names.boundary_line() per bridge).  Every host node must have
its levels (d2a/a2d) filled.

Python 3.9, standard library only.
"""

from __future__ import annotations

import collections
import math
import os
import re
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

from vamos.ams import globs, names, portmap
from vamos.ams import sv2vhdl_modes
from vamos.ams import verilog_ports
from vamos.ams import vhdl
from vamos.ams.config import AmsConfig
from vamos.ams.model import (A2D, AUTO, BIDIR, D2A, DISABLED, INOUT, INPUT, LOGIC, NONE, OUTPUT,
                             POWERNET, PULL_DOWN, PULL_UP, RA2D, RD2A, REAL, REMOVED, ROLE_CODE,
                             ROLE_PASSIVE, STRONG, SUPPLY0, SUPPLY1, THROUGH, UNKNOWN, WEAK,
                             AmsPlan, AnalogNode, CutAnalysis, CutCell, CutEmitResult, CutInstance,
                             CutPort, Driver, Net, PortMap, PortRef, RuleHits, VariantBind)
from vamos.netlist.ir import Netlist
from vamos.notes import Note, NoteError, error, note, warning

MODE_OF_DIR = {INPUT: "in", OUTPUT: "out", INOUT: "inout"}

# tgt-vhdl's translator-made signals (no "-- Declared at" comment)
_TEMP_RX = re.compile(r"(lpm_|tmp_ivl_|lo_)", re.I)          # port temporaries (wires, §5.4)
_GUARD_RX = re.compile(r"(sw\w*_b$|lpm_|lo_|tmp_)", re.I)    # temporaries the guard rejects
_MARKER_RX = re.compile(r"^sv_bufif1_vamos_ams_hiz\w*_inst$", re.I)

# l3ds drive strength codes (tgt-vhdl logic.cc) for the "-- sv_strength:" names
_WEAK_INIT = ("l3d_l", "l3d_h", "l3d_w")     # weak logic3d literals (vhdl.py folds aliases)
_Z_LIT = "l3d_z"
_STRENGTH_CODE = {"highz": 0, "small": 2, "medium": 2, "weak": 2, "large": 4, "pull": 4,
                  "strong": 8, "supply": 16}

TOP, USER, CUT = "top", "user", "cut"


# -- Verilog constant expressions (port ranges, parameter defaults) ----------------

_VTOK = re.compile(r"""
   (?P<ws>\s+)
  |(?P<sized>[0-9]*\s*'[sS]?[bBoOdDhH]\s*[0-9a-fA-F_xXzZ?]+)
  |(?P<real>[0-9][0-9_]*(?:\.[0-9][0-9_]*(?:[eE][-+]?[0-9][0-9_]*)?|[eE][-+]?[0-9][0-9_]*))
  |(?P<num>[0-9][0-9_]*)
  |(?P<str>"(?:[^"\\\n]|\\.)*")
  |(?P<id>[A-Za-z_$][A-Za-z0-9_$]*)
  |(?P<op>\*\*|<<<|>>>|<<|>>|<=|>=|===|!==|==|!=|&&|\|\||~\^|\^~|[-+*/%&|^~!?:()<>\[\],{}])
""", re.X)

# A Verilog constant: an integer (int), a real (float) or a string literal (str).
VValue = Union[int, float, str]


def _vtokens(text: str) -> Optional[List[Tuple[str, str]]]:
    out = []
    pos = 0
    while pos < len(text):
        m = _VTOK.match(text, pos)
        if not m:
            return None
        if m.lastgroup != "ws":
            out.append((m.lastgroup, m.group(0)))
        pos = m.end()
    return out


def _sized(lit: str) -> Optional[int]:
    m = re.match(r"\s*[0-9]*\s*'[sS]?([bBoOdDhH])\s*([0-9a-fA-F_]+)\s*$", lit)
    if not m:
        return None
    base = {"b": 2, "o": 8, "d": 10, "h": 16}[m.group(1).lower()]
    try:
        return int(m.group(2).replace("_", ""), base)
    except ValueError:
        return None


def veval(text: str, params: Optional[Dict[str, int]] = None) -> Optional[int]:
    """Evaluate a Verilog constant integer expression; None if it is not one.

    Integers, sized literals, parameters from `params`, $clog2, and the
    unary, binary and ternary operators.
    """
    v = _veval(text, params or {}, True)
    return v if isinstance(v, int) else None


def vvalue(text: str, params: Optional[Dict[str, VValue]] = None) -> Optional[VValue]:
    """Evaluate a Verilog constant expression of any parameter type; None if it is not one.

    As veval(), plus real literals and arithmetic (an operation with a real
    operand is real), string literals (compared with == and !=), parameters
    of those types from `params`, and $rtoi, $itor, $ln, $log10, $exp,
    $sqrt, $pow, $floor and $ceil.  The declared type of a parameter is not
    known here: `parameter real R = 1` evaluates to the integer 1.
    """
    return _veval(text, params or {}, False)


_PREC = {"||": 1, "&&": 2, "|": 3, "^": 4, "~^": 4, "^~": 4, "&": 5, "==": 6, "!=": 6,
         "===": 6, "!==": 6, "<": 7, "<=": 7, ">": 7, ">=": 7, "<<": 8, ">>": 8, "<<<": 8,
         ">>>": 8, "+": 9, "-": 9, "*": 10, "/": 10, "%": 10, "**": 11}

_REAL_FUNCS: Dict[str, Tuple[int, Callable[..., VValue]]] = {
    "$rtoi": (1, lambda x: int(x)),                    # truncation toward zero
    "$itor": (1, lambda x: float(x)),
    "$ln": (1, lambda x: math.log(x)),
    "$log10": (1, lambda x: math.log10(x)),
    "$exp": (1, lambda x: math.exp(x)),
    "$sqrt": (1, lambda x: math.sqrt(x)),
    "$floor": (1, lambda x: float(math.floor(x))),
    "$ceil": (1, lambda x: float(math.ceil(x))),
    "$pow": (2, lambda x, y: math.pow(x, y)),
}

_REAL_OPS: Dict[str, Callable[[float, float], VValue]] = {
    "+": lambda x, y: x + y, "-": lambda x, y: x - y, "*": lambda x, y: x * y,
    "/": lambda x, y: x / y, "**": lambda x, y: math.pow(x, y),
    "<": lambda x, y: int(x < y), "<=": lambda x, y: int(x <= y), ">": lambda x, y: int(x > y),
    ">=": lambda x, y: int(x >= y), "==": lambda x, y: int(x == y), "!=": lambda x, y: int(x != y),
    "&&": lambda x, y: int(bool(x) and bool(y)), "||": lambda x, y: int(bool(x) or bool(y)),
}

_ESCAPES = {"n": "\n", "t": "\t", "v": "\v", "f": "\f", "a": "\a"}


def _veval(text: str, params: Dict[str, VValue], int_only: bool) -> Optional[VValue]:
    toks = _vtokens(text)
    if toks is None:
        return None
    pos = [0]

    def peek() -> Optional[Tuple[str, str]]:
        return toks[pos[0]] if pos[0] < len(toks) else None

    def take() -> Tuple[str, str]:
        t = toks[pos[0]]
        pos[0] += 1
        return t

    def call(name: str) -> Optional[VValue]:
        if peek() is None or peek()[1] != "(":
            return None
        take()
        args: List[VValue] = []
        while True:
            a = ternary()
            t = peek()
            if a is None or t is None or t[1] not in (",", ")"):
                return None
            take()
            args.append(a)
            if t[1] == ")":
                break
        if name == "$clog2":
            if len(args) != 1 or not isinstance(args[0], int):
                return None
            v = args[0]
            return max(0, (v - 1).bit_length()) if v > 0 else 0
        if int_only or name not in _REAL_FUNCS:
            return None
        n, fn = _REAL_FUNCS[name]
        if len(args) != n or any(isinstance(a, str) for a in args):
            return None
        try:
            return fn(*args)
        except (ValueError, OverflowError, ZeroDivisionError):
            return None

    def unary() -> Optional[VValue]:
        t = peek()
        if t is None:
            return None
        if t[1] in ("+", "-", "!", "~"):
            take()
            v = unary()
            if v is None or isinstance(v, str):
                return None
            if t[1] == "~":
                return ~v if isinstance(v, int) else None
            return {"+": v, "-": -v, "!": int(not v)}[t[1]]
        if t[1] == "(":
            take()
            v = ternary()
            if peek() is None or peek()[1] != ")":
                return None
            take()
            return v
        if t[0] == "num":
            take()
            return int(t[1].replace("_", ""))
        if t[0] == "sized":
            take()
            return _sized(t[1])
        if t[0] == "real":
            take()
            return None if int_only else float(t[1].replace("_", ""))
        if t[0] == "str":
            take()
            return None if int_only else _vstring(t[1])
        if t[0] == "id":
            take()
            if t[1].startswith("$"):
                return call(t[1])
            v = params.get(t[1])
            return None if int_only and not isinstance(v, int) else v
        return None

    def binary(minp: int) -> Optional[VValue]:
        left = unary()
        while left is not None:
            t = peek()
            if t is None or t[1] not in _PREC or _PREC[t[1]] < minp:
                break
            op = take()[1]
            right = binary(_PREC[op] + (0 if op == "**" else 1))
            if right is None:
                return None
            try:
                left = _vbinop(op, left, right)
            except (ZeroDivisionError, ValueError, OverflowError):
                return None
        return left

    def ternary() -> Optional[VValue]:
        c = binary(1)
        if c is None:
            return None
        t = peek()
        if t is not None and t[1] == "?":
            take()
            a = ternary()
            if peek() is None or peek()[1] != ":":
                return None
            take()
            b = ternary()
            if a is None or b is None or isinstance(c, str):
                return None
            return a if c else b
        return c

    v = ternary()
    return v if v is not None and pos[0] == len(toks) else None


def _vbinop(op: str, a: VValue, b: VValue) -> Optional[VValue]:
    """A binary operator on constants; None where Verilog has no such operation."""
    if isinstance(a, str) or isinstance(b, str):
        if isinstance(a, str) and isinstance(b, str) and op in ("==", "!="):
            return int((a == b) == (op == "=="))
        return None
    if isinstance(a, float) or isinstance(b, float):
        fn = _REAL_OPS.get(op)              # no %, bitwise, shift or case equality on reals
        return fn(float(a), float(b)) if fn is not None else None
    return _vbin(op, a, b)


def _vstring(tok: str) -> str:
    """The characters of a Verilog string literal token, escapes resolved."""
    body = tok[1:-1]
    out: List[str] = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            m = re.match(r"[0-7]{1,3}", body[i + 1:])
            if m:
                out.append(chr(int(m.group(0), 8) & 0xFF))
                i += 1 + len(m.group(0))
                continue
            out.append(_ESCAPES.get(body[i + 1], body[i + 1]))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _vbin(op: str, a: int, b: int) -> int:
    if op == "/":
        return int(a / b)
    if op == "%":
        return a - int(a / b) * b
    if op == "**":
        if b < 0:
            raise ValueError("negative exponent")
        return a ** b
    return {"+": a + b, "-": a - b, "*": a * b, "<<": a << b, ">>": a >> b, "<<<": a << b,
            ">>>": a >> b, "<": int(a < b), "<=": int(a <= b), ">": int(a > b),
            ">=": int(a >= b), "==": int(a == b), "!=": int(a != b), "===": int(a == b),
            "!==": int(a != b), "&": a & b, "|": a | b, "^": a ^ b, "~^": ~(a ^ b),
            "^~": ~(a ^ b), "&&": int(bool(a and b)), "||": int(bool(a or b))}[op]


def range_of_text(text: str, params: Dict[str, int]) -> Optional[Tuple[int, int]]:
    """'[msb:lsb]' (or 'msb:lsb') evaluated with `params`, else None."""
    t = text.strip()
    if t.startswith("[") and t.endswith("]"):
        t = t[1:-1]
    depth = 0
    for k, c in enumerate(t):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == ":" and depth == 0:
            # skip the ':' of a ternary in the left bound
            if "?" in t[:k] and t[:k].count("?") > t[:k].count(":"):
                continue
            left, right = veval(t[:k], params), veval(t[k + 1:], params)
            if left is None or right is None:
                return None
            return (left, right)
    return None


def eval_params(defaults: Dict[str, str], actual: Dict[str, str]) -> Dict[str, int]:
    """Integer parameter values: the variant's actual values (nvc_verilog_params),
    then the evaluable defaults for the rest (in dependency order)."""
    vals: Dict[str, int] = {}
    for k, v in actual.items():
        try:
            vals[k] = int(v)
        except ValueError:
            pass
    pending = {k: v for k, v in defaults.items() if k not in vals}
    progress = True
    while pending and progress:
        progress = False
        for k in list(pending):
            v = veval(pending[k], vals)
            if v is not None:
                vals[k] = v
                del pending[k]
                progress = True
    return vals


# -- Verilog declarations (from nvc/_norm.sv: names, ranges, variables) ------------

_DECL_KW = {"input", "output", "inout", "ref", "wire", "reg", "logic", "bit", "byte", "int",
            "integer", "shortint", "longint", "time", "real", "realtime", "shortreal", "wreal",
            "tri", "tri0", "tri1", "triand", "trior", "trireg", "supply0", "supply1", "wand",
            "wor", "uwire", "var", "interconnect"}
_DIRS = {"input", "output", "inout", "ref"}
_VARIABLE_KINDS = {"reg", "logic", "bit", "byte", "int", "integer", "shortint", "longint",
                   "time", "real", "realtime", "shortreal", "var"}
_STMT_BREAK = {";", "begin", "end", "generate", "endgenerate", "else", "endcase", "endfunction",
               "endtask", "endmodule"}

_VLEX = re.compile(r"""
   (?P<ws>\s+)
  |(?P<lcomment>//[^\n]*)
  |(?P<bcomment>/\*.*?\*/)
  |(?P<attr>\(\*.*?\*\))
  |(?P<str>"(?:[^"\\\n]|\\.)*")
  |(?P<esc>\\\S+)
  |(?P<id>[A-Za-z_$][A-Za-z0-9_$]*)
  |(?P<num>[0-9][0-9_]*(?:\.[0-9_]+)?(?:[eE][-+]?[0-9]+)?|[0-9]*\s*'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+)
  |(?P<op>.)
""", re.X | re.S)


class VDecl:
    """A Verilog declaration found by the scan: direction, kind and packed range."""
    __slots__ = ("name", "direction", "kind", "rng_text", "line")

    def __init__(self, name: str, line: int):
        self.name = name
        self.direction: Optional[str] = None
        self.kind: Optional[str] = None
        self.rng_text: Optional[str] = None
        self.line = line

    @property
    def variable(self) -> bool:
        """reg/logic/bit/... as VCS sees it (an input or inout port is a net)."""
        if self.kind in ("var",):
            return True
        if self.direction in ("input", "inout") and self.kind in (None, "logic"):
            return False
        return self.kind in _VARIABLE_KINDS and self.kind not in ("real", "realtime", "shortreal")

    @property
    def tri(self) -> Optional[str]:
        return self.kind if self.kind in ("tri0", "tri1") else None


class VModule:
    """The declarations of one Verilog module, read from the translator's _norm.sv."""

    def __init__(self, name: str):
        self.name = name
        self.decls: Dict[str, VDecl] = {}
        self._exact: Dict[str, str] = {}
        self._safe: Dict[str, str] = {}

    def add(self, d: VDecl) -> None:
        old = self.decls.get(d.name)
        if old is None:
            self.decls[d.name] = d
            for c in (vhdl.make_safe_name(d.name), vhdl.make_safe_name(d.name, True)):
                self._exact.setdefault(c, d.name)
                self._safe.setdefault(c.lower(), d.name)
            return
        old.direction = old.direction or d.direction
        if d.kind and (old.kind is None or old.kind == "wire"):
            old.kind = d.kind
        old.rng_text = old.rng_text or d.rng_text

    def lookup(self, vhdl_name: str) -> Optional[VDecl]:
        """The declaration a VHDL signal/port name came from (tgt-vhdl renaming undone).

        tgt-vhdl keeps the Verilog case, so an exact match wins (w_OUT vs w_out);
        '_<n>' is avoid_name_collision()'s suffix after a case-only clash.
        """
        m = re.search(r"_\d+$", vhdl_name)
        base = vhdl_name[:m.start()] if m else None
        n = self._exact.get(vhdl_name)
        if n is None and base is not None:
            n = self._exact.get(base)
        if n is None:
            n = self._safe.get(vhdl_name.lower())
        if n is None and base is not None:
            n = self._safe.get(base.lower())
        return self.decls.get(n) if n is not None else None


def scan_module(text: str, module: str, line: int) -> Optional[VModule]:
    """Declarations of `module` defined at (or after) `line` of `text`."""
    toks: List[Tuple[str, str, int]] = []
    pos, ln = 0, 1
    for m in _VLEX.finditer(text):
        kind = m.lastgroup
        s = m.group(0)
        if kind not in ("ws", "lcomment", "bcomment", "attr"):
            toks.append((kind, s, ln))
        ln += s.count("\n")
    start = None
    for k, (kind, s, l) in enumerate(toks):
        if l >= line and s in ("module", "macromodule"):
            j = k + 1
            if j < len(toks) and toks[j][1] in ("automatic", "static"):
                j += 1
            if j < len(toks) and toks[j][1] == module:
                start = j + 1
                break
    if start is None:
        return None
    vm = VModule(module)
    k = start
    # parameter port list
    if k < len(toks) and toks[k][1] == "#":
        k += 1
        k = _skip_parens(toks, k)
    # port list
    if k < len(toks) and toks[k][1] == "(":
        end = _skip_parens(toks, k)
        _ansi_ports(toks[k + 1:end - 1], vm)
        k = end
    # body
    prev = ";"
    while k < len(toks) and toks[k][1] not in ("endmodule",):
        s = toks[k][1]
        if s in _DECL_KW and (prev in _STMT_BREAK or prev == ")" or prev == ":"):
            e = k
            depth = 0
            while e < len(toks):
                if toks[e][1] in ("(", "[", "{"):
                    depth += 1
                elif toks[e][1] in (")", "]", "}"):
                    depth -= 1
                elif toks[e][1] == ";" and depth <= 0:
                    break
                e += 1
            _declaration(toks[k:e], vm, {})
            k = e
            prev = ";"
            continue
        if toks[k][0] in ("id", "op"):
            prev = s
        k += 1
    return vm


def _skip_parens(toks: Sequence[Tuple[str, str, int]], k: int) -> int:
    """At '(' (index k): return the index after the matching ')'."""
    depth = 0
    while k < len(toks):
        if toks[k][1] == "(":
            depth += 1
        elif toks[k][1] == ")":
            depth -= 1
            if depth == 0:
                return k + 1
        k += 1
    return k


def _split_top(toks: Sequence[Tuple[str, str, int]], sep: str) -> List[List[Tuple[str, str, int]]]:
    out: List[List[Tuple[str, str, int]]] = [[]]
    depth = 0
    for t in toks:
        if t[1] in ("(", "[", "{"):
            depth += 1
        elif t[1] in (")", "]", "}"):
            depth -= 1
        if t[1] == sep and depth == 0:
            out.append([])
        else:
            out[-1].append(t)
    return out


def _ansi_ports(toks: Sequence[Tuple[str, str, int]], vm: VModule) -> None:
    inherit: Dict[str, Optional[str]] = {}
    for item in _split_top(toks, ","):
        if not item:
            continue
        if item[0][1] == ".":
            continue                  # port expression .a(x): not a declaration
        if item[0][1] in _DECL_KW or item[0][1] == "[":
            inherit = {}
            _declaration(item, vm, inherit)
        elif len(item) == 1 and item[0][0] == "id":
            if inherit:
                _declaration(item, vm, inherit)
            else:
                vm.add(VDecl(item[0][1], item[0][2]))


def _declaration(toks: Sequence[Tuple[str, str, int]], vm: VModule,
                 inherit: Dict[str, Optional[str]]) -> None:
    """direction? kind* signed? strength? delay? [range]* name dims? (= expr)?, ..."""
    k = 0
    direction = inherit.get("direction")
    kind = inherit.get("kind")
    rng = inherit.get("rng")
    explicit = False
    while k < len(toks):
        s = toks[k][1]
        if s in _DIRS:
            direction, kind, rng, explicit = s, None, None, True
        elif s in _DECL_KW:
            if s == "var" or kind is None or kind == "var":
                kind = s if kind != "var" or s == "var" else "var"
            else:
                kind = s
            explicit = True
        elif s in ("signed", "unsigned", "scalared", "vectored", "const", "static", "automatic"):
            pass
        elif s == "(":
            k = _skip_parens(toks, k)            # drive strength
            continue
        elif s == "#":
            k += 1
            if k < len(toks) and toks[k][1] == "(":
                k = _skip_parens(toks, k)
            else:
                k += 1
            continue
        elif s == "[":
            depth = 0
            e = k
            while e < len(toks):
                if toks[e][1] == "[":
                    depth += 1
                elif toks[e][1] == "]":
                    depth -= 1
                    if depth == 0:
                        break
                e += 1
            if rng is None or explicit:
                rng = " ".join(t[1] for t in toks[k:e + 1])
            explicit = False
            k = e + 1
            continue
        else:
            break
        k += 1
    if inherit is not None:
        inherit.update({"direction": direction, "kind": kind, "rng": rng})
    for item in _split_top(toks[k:], ","):
        if not item or item[0][0] not in ("id", "esc"):
            continue
        d = VDecl(item[0][1], item[0][2])
        d.direction = direction
        d.kind = kind
        d.rng_text = rng
        vm.add(d)


# -- the analysis state --------------------------------------------------------------

class _Scope:
    """One elaborated instance of an architecture (the top, a user module, a cut cell)."""
    __slots__ = ("id", "labels", "entity", "arch", "parent", "stmt", "kind", "vpath", "inst",
                 "children", "index")

    def __init__(self, sid: int, labels: Tuple[str, ...], entity: vhdl.Entity,
                 arch: Optional[vhdl.Architecture], parent: Optional[int],
                 stmt: Optional[vhdl.Stmt], kind: str, vpath: str):
        self.id = sid
        self.labels = labels
        self.entity = entity
        self.arch = arch
        self.parent = parent
        self.stmt = stmt
        self.kind = kind
        self.vpath = vpath
        self.inst = -1
        self.children: Dict[int, int] = {}       # statement index -> scope id
        self.index: Optional[_Index] = None


class _Touch:
    """A statement's contact with some bits of a signal: drive, read, force, Z-only drive."""
    __slots__ = ("stmt", "assign", "what", "offs", "strength", "removable", "origin", "sid")

    def __init__(self, stmt: int, assign: int, what: str, offs: Optional[Set[int]],
                 strength: str = STRONG, removable: bool = False, origin: str = "", sid: str = ""):
        self.stmt = stmt
        self.assign = assign
        self.what = what                 # 'drv' | 'rd' | 'zdrv' | 'force' | 'release'
        self.offs = offs                 # None = every bit
        self.strength = strength
        self.removable = removable
        self.origin = origin
        self.sid = sid


Seg = Tuple[int, str, int]                   # (scope id, signal (lowercased), bit offset)


def _stmt_origin(arch: str, st: vhdl.Stmt) -> str:
    """'<arch>:<label or line N>', plus the Verilog file:line tgt-vhdl noted for it."""
    o = "%s:%s" % (arch, st.label or "line %d" % st.line)
    v = st.origin
    if v:
        f, _, ln = v.rpartition(":")
        o += " (%s:%s)" % (os.path.basename(f), ln)
    return o


class _Index:
    """What one architecture (or the foreign statements of a cut variant) does with each bit."""

    def __init__(self, an: "_Analyser", arch: vhdl.Architecture, entity: vhdl.Entity,
                 subset: Optional[Set[int]] = None):
        self.arch = arch
        self.entity = entity
        self.types: Dict[str, vhdl.TypeSpec] = {p.lname: p.type for p in entity.ports}
        for n, sd in arch.signals.items():
            self.types.setdefault(n, sd.type)
        self.ports = {p.lname for p in entity.ports}
        # actual bit -> [(stmt, formal (lower), formal offset)] for user/cut children
        self.assoc: Dict[str, Dict[int, List[Tuple[int, str, int]]]] = {}
        # stmt -> (formal, formal offset) -> (actual, offset) | ('#expr', assoc index)
        self.formal: Dict[int, Dict[Tuple[str, int], Tuple[str, int]]] = {}
        self.touch: Dict[str, List[_Touch]] = {}
        self.joins: Dict[Tuple[str, int], List[Tuple[str, int]]] = {}
        self.consumed: Set[Tuple[int, int]] = set()
        self.drivers_of: Dict[str, int] = collections.Counter()   # assignments + out formals per signal
        # bits with a source other than their initial value: a value-driving or forced
        # assignment, a copy (port temporary) into them, an out/inout formal
        self.sourced: Dict[str, Set[int]] = {}
        self.zbits: Set[Tuple[str, int]] = set()      # bits of Z-only temporaries
        # (stmt, formal, formal offset) -> the literal of that bit of a constant actual
        self.tie_lits: Dict[Tuple[int, str, int], str] = {}
        self.stmt_reads: Dict[int, List[Tuple[str, Optional[Set[int]]]]] = {}  # stmt -> bits read
        # (stmt, assignment or -1 for an instance) -> bits its driven value depends on
        self.assign_reads: Dict[Tuple[int, int], List[Tuple[str, Optional[Set[int]]]]] = {}
        # port buffers (PB_<label>_<port> <= <actual>, a plain copy): bit of either side ->
        # [(stmt, the bit on the other side, whether that other bit is the buffer's)]
        self.pb_edges: Dict[Tuple[str, int], List[Tuple[int, Tuple[str, int], bool]]] = {}
        self.an = an
        self._build(subset)

    def _initializers(self) -> None:
        """A declared logic signal's initial value (reg [5:3] r = 3'b001) drives every
        bit that has no other source (§5.4 step 4), each bit with its own literal:
        no assignment that can drive a value, no copy into it, no out/inout formal.
        An X or Z bit drives nothing.  A bit whose only assignments are Z-only still
        starts at its initial value (a process driver starts at the signal's initial
        value: reg r = 1; initial #10 r = 1'bz), so the initial value drives it too."""
        for n, sd in self.arch.signals.items():
            if sd.declared_at is None or sd.type.kind != "logic" or not sd.init:
                continue
            origin = "%s:%s initial value %s" % (self.arch.entity, sd.name, sd.init)
            sid = "%s#init:%s" % (self.arch.lentity, n)
            offs = sd.type.offsets()
            elems = sd.init_elems
            if elems is None or len(elems) != len(offs):
                # not a constant we can read bit by bit: the whole signal, if nothing else
                # drives any of it
                if self.drivers_of.get(n, 0):
                    continue
                init = re.sub(r"\s+", "", sd.init).lower()
                if init in ("l3d_x", "(others=>l3d_x)", "l3d_z", "(others=>l3d_z)"):
                    continue
                lits = re.findall(r"l3d_\w+", init)
                weak = bool(lits) and all(w in _WEAK_INIT for w in lits)
                self._add(n, _Touch(-1, -1, "drv", None, WEAK if weak else STRONG, False,
                                    origin, sid))
                continue
            busy = self.sourced.get(n, set())
            groups: Dict[str, Set[int]] = {}
            for o, lit in zip(offs, elems):
                if o in busy or lit in ("l3d_x", "l3d_u", _Z_LIT):
                    continue
                groups.setdefault(WEAK if lit in _WEAK_INIT else STRONG, set()).add(o)
            for strength in sorted(groups):
                self._add(n, _Touch(-1, -1, "drv", groups[strength], strength, False, origin, sid))

    # -- references --------------------------------------------------------------

    def resolve(self, ref: vhdl.Ref) -> vhdl.Ref:
        """Follow aliases down to the aliased object."""
        for _ in range(16):
            al = self.arch.aliases.get(ref.lname)
            if al is None:
                return ref
            t = al.target
            if t.indices is not None and len(t.indices) == 1:
                ref = vhdl.Ref(t.name, t.indices, t.exact)          # an element alias
            elif t.indices is not None:
                ref = vhdl.Ref(t.name, ref.indices if ref.indices is not None else t.indices,
                               ref.exact and t.exact)
            else:
                ref = vhdl.Ref(t.name, ref.indices, ref.exact)
        return ref

    def offs(self, ref: vhdl.Ref) -> List[int]:
        ts = self.types.get(ref.lname)
        if ts is None:
            return []
        if ref.indices is None or not ref.exact:
            return ts.offsets()
        if ts.vector and ts.rng is not None:
            return [ts.rng.offset(i) for i in ref.indices if ts.rng.contains(i)]
        return [0]

    def bits(self, refs: Sequence[vhdl.Ref]) -> List[Tuple[str, int]]:
        out = []
        for r in refs:
            r = self.resolve(r)
            out.extend((r.lname, o) for o in self.offs(r))
        return out

    def _add(self, name: str, t: _Touch) -> None:
        self.touch.setdefault(name, []).append(t)

    def _touch_refs(self, si: int, ai: int, what: str, refs: Iterable[vhdl.Ref], **kw) -> None:
        per: Dict[str, Set[int]] = {}
        for r in refs:
            r = self.resolve(r)
            if r.lname not in self.types:
                continue
            per.setdefault(r.lname, set()).update(self.offs(r))
        for n, o in per.items():
            self._add(n, _Touch(si, ai, what, o, **kw))
            if what == "rd":
                self.stmt_reads.setdefault(si, []).append((n, o))

    def _bits_read(self, refs: Iterable[vhdl.Ref]) -> List[Tuple[str, Optional[Set[int]]]]:
        per: Dict[str, Set[int]] = {}
        for r in refs:
            r = self.resolve(r)
            if r.lname in self.types:
                per.setdefault(r.lname, set()).update(self.offs(r))
        return list(per.items())

    # -- build ---------------------------------------------------------------------

    def _build(self, subset: Optional[Set[int]]) -> None:
        arch = self.arch
        stmts = [(si, st) for si, st in enumerate(arch.stmts) if subset is None or si in subset]
        aname = arch.entity
        qual = self._wire_temps(stmts)
        self.zbits = self._z_bits(stmts)
        for si, st in stmts:
            origin = _stmt_origin(aname, st)
            if st.kind == "instance":
                self._instance(si, st, origin)
                continue
            copy_ok = st.simple or st.fused
            used_in_copies: Set[str] = set()
            other_reads: List[vhdl.Ref] = list(st.ctrl_reads)
            for ai, asg in enumerate(st.assigns):
                tgt = self.resolve(asg.target)
                if asg.op in ("<=", ":="):
                    self.drivers_of[tgt.lname] += 1
                # what this assignment's value depends on (the quantised round trip)
                self.assign_reads[(si, ai)] = self._bits_read(list(asg.reads) +
                                                              list(st.cond_reads))
                pairs = None
                if self.port_buffer(tgt.lname):
                    # never a wire join: a one-way join candidate that the cut decides per
                    # elaborated copy (_Analyser._port_buffers); meanwhile a driver of the
                    # buffer and a reader of the actual, as any other assignment
                    if copy_ok:
                        self._pb_copy(si, asg)
                elif copy_ok and asg.copy is not None and asg.op in ("<=", ":="):
                    names_in = {tgt.lname} | {self.resolve(r).lname for r in asg.copy}
                    if names_in & qual or self._shadow_copy(tgt, asg.copy):
                        tb = self.bits([asg.target])
                        rb = self.bits(asg.copy)
                        if len(tb) == len(rb) and tb:
                            pairs = list(zip(tb, rb))
                if pairs is not None:
                    self.consumed.add((si, ai))
                    for a, b in pairs:
                        self.joins.setdefault(a, []).append(b)
                        self.joins.setdefault(b, []).append(a)
                        used_in_copies.add(a[0])
                        used_in_copies.add(b[0])
                        self.sourced.setdefault(a[0], set()).add(a[1])    # a: the target bit
                    continue
                self._drive(si, st, ai, asg, origin)
                other_reads.extend(asg.reads)
            sens = [r for r in st.sens_reads
                    if self.resolve(r).lname not in used_in_copies
                    or any(self.resolve(o).lname == self.resolve(r).lname for o in other_reads)]
            self._touch_refs(si, -1, "rd", other_reads + sens, origin=origin, sid=st.sid)
        if subset is None:
            self._initializers()

    def _drive(self, si: int, st: vhdl.Stmt, ai: int, asg: vhdl.Assign, origin: str) -> None:
        """The touches of an assignment that is not a wire join (§5.4 step 4).

        A driver that can only produce Z is not a driver, bit by bit: a value
        read element by element (Assign.parts) gives a Z literal or a bit of a
        Z-only temporary a 'zdrv' touch, a weak literal a weak driver, anything
        else a strong one.  So `pa <= t5 & tmp_z & t3` (tmp_z <= L3D_Z) drives
        only pa's outer bits, and `wv <= logic3d_vector'(L3D_Z, L3D_1)` only
        wv(0).  Any other value drives (or forces, or Z-drives) the whole target.
        """
        kw = {"origin": origin, "sid": st.sid}
        if asg.op in ("force", "release"):
            self._touch_refs(si, ai, asg.op, [asg.target], **kw)
            self._mark_sourced(self.bits([asg.target]))
            return
        if asg.z_only:
            self._touch_refs(si, ai, "zdrv", [asg.target], **kw)
            return
        vals = self._value_bits(asg)
        tb = self.bits([asg.target])
        if vals is None or not tb or len(vals) != len(tb):
            self._touch_refs(si, ai, "drv", [asg.target],
                             strength=WEAK if asg.weak_only else STRONG, **kw)
            self._mark_sourced(tb)
            return
        groups: Dict[Tuple[str, str, str], Set[int]] = collections.OrderedDict()
        for (n, o), v in zip(tb, vals):
            if v == _Z_LIT or v in self.zbits:
                key = ("zdrv", STRONG)
            elif v in _WEAK_INIT:
                key = ("drv", WEAK)
            else:
                key = ("drv", STRONG)
            groups.setdefault((n,) + key, set()).add(o)
        for (n, what, strength), offs in groups.items():
            self._add(n, _Touch(si, ai, what, offs, strength, False, origin, st.sid))
            if what == "drv":
                self._mark_sourced([(n, o) for o in offs])

    def _mark_sourced(self, bits: Iterable[Tuple[str, int]]) -> None:
        for n, o in bits:
            self.sourced.setdefault(n, set()).add(o)

    def port_buffer(self, lname: str) -> bool:
        """Whether signal `lname` is one of tgt-vhdl's logic port buffers (PB_<label>_<port>)."""
        sd = self.arch.signals.get(lname)
        return sd is not None and sd.port_buffer is not None and sd.type.kind == "logic"

    def _pb_copy(self, si: int, asg: vhdl.Assign) -> None:
        """Record the bit pairs of a port buffer's plain copy (PB_<label>_<port> <= <actual>
        with references only, bit for bit, undelayed) as one-way join candidates."""
        if asg.copy is None or asg.op not in ("<=", ":=") or asg.delayed:
            return
        tb = self.bits([asg.target])
        rb = self.bits(asg.copy)
        if not tb or len(tb) != len(rb):
            return
        for a, b in zip(tb, rb):
            self.pb_edges.setdefault(a, []).append((si, b, False))
            self.pb_edges.setdefault(b, []).append((si, a, True))

    def _value_bits(self, asg: vhdl.Assign) -> Optional[List[object]]:
        """The assigned value bit by bit, left to right: a literal (str) or the
        (signal, offset) bit it copies; None when the value is not a
        concatenation of plain references and logic3d constants."""
        if asg.parts is None:
            return None
        out: List[object] = []
        for p in asg.parts:
            if isinstance(p, str):
                out.append(p)
                continue
            r = self.resolve(p)
            if r.lname not in self.types or (r.indices is not None and not r.exact):
                return None
            out.extend((r.lname, o) for o in self.offs(r))
        return out

    def _z_bits(self, stmts: Sequence[Tuple[int, vhdl.Stmt]]) -> Set[Tuple[str, int]]:
        """Bits of translator temporaries that can only ever hold Z (§5.4 step 4).

        tgt-vhdl writes the undriven part of a partly assigned vector as a
        temporary assigned L3D_Z and concatenates it into the vector's driver
        (`pa <= t5 & tmp_z & t3` with `tmp_z <= L3D_Z`, also as ':=' in a
        comb_fused process).  A temporary bit is Z-only when it is assigned,
        and every assignment to it is unconditional (Stmt.straight, no 'after')
        and gives it Z: a Z literal, or a copy of a Z-only bit (the least fixed
        point, so a copy cycle is not Z-only).  A temporary that an instance
        port or a call touches, or that a condition reads, never is.
        """
        temps = {n for n, sd in self.arch.signals.items()
                 if sd.temporary and not sd.readable_shadow and sd.type.kind == "logic"}
        if not temps:
            return set()
        for _, st in stmts:
            if st.kind == "instance":
                for a in st.assocs:
                    for r in list(a.actual or ()) + list(a.reads):
                        temps.discard(self.resolve(r).lname)
            else:
                for r in st.ctrl_reads:
                    temps.discard(self.resolve(r).lname)
        if not temps:
            return set()
        srcs: Dict[Tuple[str, int], List[object]] = {}
        bad: Set[Tuple[str, int]] = set()
        for _, st in stmts:
            if st.kind != "process":
                continue
            for asg in st.assigns:
                tl = self.resolve(asg.target).lname
                if tl not in temps:
                    continue
                tb = self.bits([asg.target])
                vals: Optional[List[object]] = None
                if st.straight and asg.op in ("<=", ":=") and not asg.delayed:
                    vals = [_Z_LIT] * len(tb) if asg.z_only else self._value_bits(asg)
                if vals is None or len(vals) != len(tb):
                    bad.update(tb)
                    continue
                for b, v in zip(tb, vals):
                    srcs.setdefault(b, []).append(v)
        zb: Set[Tuple[str, int]] = set()
        changed = True
        while changed:
            changed = False
            for b, vs in srcs.items():
                if b not in zb and b not in bad and all(v == _Z_LIT or v in zb for v in vs):
                    zb.add(b)
                    changed = True
        return zb

    def _shadow_copy(self, tgt: vhdl.Ref, copy: Sequence[vhdl.Ref]) -> bool:
        """P <= S or S <= P between a _Readable shadow S and an out port P of the entity."""
        if len(copy) != 1:
            return False
        src = self.resolve(copy[0])
        a, b = tgt.lname, src.lname
        for s, p in ((a, b), (b, a)):
            sd = self.arch.signals.get(s)
            port = self.entity.port(p)
            if sd is not None and sd.readable_shadow and port is not None and port.mode == "out":
                return True
        return False

    def _wire_temps(self, stmts: Sequence[Tuple[int, vhdl.Stmt]]) -> Set[str]:
        """Port temporaries that are wires (§5.4): port actuals used only in plain copies.

        Extension: a temporary that shares a copy with a wire temporary, is
        used only in copies, and was created at the instantiation line of the
        port it feeds (tgt-vhdl's "Temporary created at f:l" = "Generated from
        instantiation at f:l") is part of the same port expression, so it is a
        wire too (.d({code[0], code[1]}) goes through tmp_ivl_* bit copies).
        The actual of a port buffer's copy (PB_wb_a <= tmp_ivl_1 for .a(rv[1]))
        counts as the actual of the instance the buffer is associated with.
        """
        temps = {n for n, sd in self.arch.signals.items()
                 if sd.temporary and _TEMP_RX.match(n) and not sd.readable_shadow}
        if not temps:
            return set()
        actual_of: Dict[str, Set[str]] = {}      # temp -> instantiation origins it is an actual of
        bad: Set[str] = set()
        targeted: Dict[str, int] = collections.Counter()
        partners: Dict[str, Set[str]] = {}
        pb_origin: Dict[str, str] = {}           # port buffer -> origin of its instance
        for _, st in stmts:
            if st.kind == "instance":
                for a in st.assocs:
                    if a.actual is not None:
                        for r in a.actual:
                            actual_of.setdefault(self.resolve(r).lname, set()).add(st.origin)
                            if self.port_buffer(self.resolve(r).lname):
                                pb_origin[self.resolve(r).lname] = st.origin
                    else:
                        bad.update(self.resolve(r).lname for r in a.reads)
        for _, st in stmts:
            if st.kind == "instance":
                continue
            copy_ok = st.simple or st.fused
            for asg in st.assigns:
                tl = self.resolve(asg.target).lname
                targeted[tl] += 1
                names_in = {tl} | {self.resolve(r).lname for r in asg.reads}
                if not copy_ok or asg.copy is None or asg.op not in ("<=", ":="):
                    bad.update(names_in)
                else:
                    for n in names_in & temps:
                        partners.setdefault(n, set()).update(names_in - {n})
                    if tl in pb_origin:
                        for r in asg.copy:
                            actual_of.setdefault(self.resolve(r).lname, set()).add(pb_origin[tl])
            bad.update(self.resolve(r).lname for r in st.ctrl_reads)
        bad.update(n for n, c in targeted.items() if c > 1)     # two copies into one temp: not a wire
        ok = temps - bad
        qual = {t for t in ok if t in actual_of}
        origins = {t: actual_of[t] for t in qual}
        frontier = list(qual)
        while frontier:
            q = frontier.pop()
            for t in partners.get(q, ()):
                if t in ok and t not in qual and \
                        self.arch.signals[t].created_at in origins[q] - {""}:
                    qual.add(t)
                    origins[t] = origins[q]
                    frontier.append(t)
        return qual

    def _instance(self, si: int, st: vhdl.Stmt, origin: str) -> None:
        an = self.an
        if st.lib == "work":
            child = an.design.entity(st.entity)
            fmap = self.formal.setdefault(si, {})
            for ai, a in enumerate(st.assocs):
                if a.open or child is None:
                    continue
                port = child.port(a.formal.name)
                if port is None:
                    an.err(an.design.where(st.line), "instance %s: entity %s has no port %s"
                           % (st.label, st.entity, a.formal.name))
                    continue
                fts = port.type
                if a.formal.indices is None or not a.formal.exact:
                    foffs = fts.offsets()
                elif fts.vector and fts.rng is not None:
                    foffs = [fts.rng.offset(i) for i in a.formal.indices]
                else:
                    foffs = [0]
                if a.actual is not None:
                    acts = self.bits(a.actual)
                    if len(acts) != len(foffs):
                        an.err(an.design.where(st.line), "instance %s: port %s has %d bits but "
                               "its actual %s has %d" % (st.label, a.formal.name, len(foffs),
                                                         a.text, len(acts)))
                        continue
                    for (al, ao), fo in zip(acts, foffs):
                        self.assoc.setdefault(al, {}).setdefault(ao, []).append(
                            (si, a.formal.lname, fo))
                        fmap[(a.formal.lname, fo)] = (al, ao)
                    if port.mode in ("out", "inout", "buffer"):
                        for al in {n for n, _ in acts}:
                            self.drivers_of[al] += 1
                        self._mark_sourced(acts)
                else:
                    for fo in foffs:
                        fmap[(a.formal.lname, fo)] = ("#expr", ai)
                    # a constant actual bit by bit (cin u (.d(2'bz1)): only d[0] is driven)
                    if a.elems is not None and len(a.elems) == len(foffs):
                        for fo, lit in zip(foffs, a.elems):
                            self.tie_lits[(si, a.formal.lname, fo)] = lit
                    self._touch_refs(si, -1, "rd", a.reads, origin=origin, sid=st.sid)
            return
        if st.lib != "sv2vhdl":
            return
        modes = sv2vhdl_modes.MODES.get(st.entity.lower())
        if modes is None:
            return                        # reported by the walk
        strength, removable = self._lib_strength(st)
        ent = st.entity.lower()
        inputs: List[vhdl.Ref] = []
        for a in st.assocs:
            mode = modes.get(a.formal.lname, "in")
            if not a.open and mode in ("in", "inout"):
                inputs.extend(a.actual if a.actual is not None else a.reads)
        for ai, a in enumerate(st.assocs):
            if a.open:
                continue
            mode = modes.get(a.formal.lname, "in")
            if ent == "sv_strength_buf" and a.formal.lname == "y":
                mode = "out"              # inout only for 'driver mechanics; it drives y
            refs = a.actual if a.actual is not None else a.reads
            if a.actual is None and mode != "in":
                continue
            if mode in ("out", "inout"):
                what = "zdrv" if strength is None else "drv"
                self._touch_refs(si, ai, what, refs, strength=strength or STRONG,
                                 removable=removable, origin=origin, sid=st.sid)
                for n in {self.resolve(r).lname for r in refs}:
                    self.drivers_of[n] += 1
                self._mark_sourced(self.bits(refs))
                self.assign_reads[(si, ai)] = self._bits_read(inputs)
            if mode in ("in", "inout"):
                self._touch_refs(si, -1, "rd", refs, origin=origin, sid=st.sid)

    @staticmethod
    def _lib_strength(st: vhdl.Stmt) -> Tuple[Optional[str], bool]:
        """(strength class or None for 'drives nothing', removable) of a library primitive."""
        ent = st.entity.lower()
        sv = st.strength
        if ent in ("sv_pullup", "sv_pulldown"):
            if sv == ("supply", "supply"):
                return (SUPPLY1 if ent == "sv_pullup" else SUPPLY0), False
            return (PULL_UP if ent == "sv_pullup" else PULL_DOWN), True
        if ent == "sv_strength_buf":
            try:
                mx = max(int(st.generics.get("str1", "8")), int(st.generics.get("str0", "8")))
            except ValueError:
                mx = 8
            return (None if mx == 0 else STRONG if mx >= 8 else WEAK), False
        if sv is not None:
            mx = max(_STRENGTH_CODE.get(sv[0], 8), _STRENGTH_CODE.get(sv[1], 8))
            return (None if mx == 0 else STRONG if mx >= 8 else WEAK), False
        return STRONG, False


# -- analyse ---------------------------------------------------------------------------

class _State:
    """Private analysis state handed from analyse() to assign_roles() and emit()."""

    def __init__(self) -> None:
        self.design: Optional[vhdl.VhdlDesign] = None
        self.scopes: List[_Scope] = []
        self.top_entity: Optional[vhdl.Entity] = None
        self.cut_scope: List[int] = []                  # CutInstance index -> scope id
        self.variant_cells: Dict[str, str] = {}         # variant entity (lower) -> cell name
        self.foreign: Dict[str, List[int]] = {}         # variant (lower) -> foreign statement indices
        self.footprint: Dict[str, Set[int]] = {}        # variant (lower) -> dropped statement indices
        self.net_touches: Dict[str, List[Tuple[int, _Touch]]] = {}   # net key -> [(scope, touch)]
        self.net_reads_nets: Dict[str, Set[str]] = {}   # net key -> nets read by its drivers
        self.net_variables: Dict[str, List[str]] = {}   # net key -> Verilog variables on it
        self.seg_net: Dict[Seg, str] = {}
        self.pull_occ: Dict[str, List[Tuple[int, Optional[str]]]] = {}  # pull stmt -> [(scope, net)]
        self.t3 = True
        self.nodes_by_net: Dict[str, AnalogNode] = {}
        self.moved_pulls: Set[str] = set()
        self.nl: Optional[Netlist] = None
        # port buffers: cut-port bits on the wrapper's side of a joined buffer (they act as
        # inputs) -> why; net key -> IE-report lines about a buffer that was not joined
        self.one_way: Dict[PortRef, str] = {}
        self.pb_notes: Dict[str, List[str]] = {}
        # shells.ShellResult.directions (cell -> auto port -> "auto->mode (why)"), if given
        self.directions: Dict[str, Dict[str, str]] = {}


class _Analyser:
    def __init__(self, design: vhdl.VhdlDesign, top: str, cells: Union[Dict[str, CutCell],
                 Iterable[CutCell]], nl: Netlist, cfg: AmsConfig, hits: RuleHits,
                 pp: Optional[object] = None,
                 directions: Optional[Dict[str, Dict[str, str]]] = None):
        self.design = design
        self.top = top
        self.pp = pp
        if isinstance(cells, dict):
            self.cells: Dict[str, CutCell] = dict(cells)
        else:
            self.cells = {c.name: c for c in cells}
        self.nl = nl
        self.cfg = cfg
        self.hits = hits
        self.notes: List[Note] = []
        self.st = _State()
        self.st.design = design
        self.st.nl = nl
        self.st.directions = dict(directions or {})
        self.ana = CutAnalysis(top=top, cells=dict(self.cells))
        self.vmods: Dict[Tuple[str, str], Optional[VModule]] = {}
        self.files: Dict[str, Optional[str]] = {}
        self.arch_index: Dict[str, _Index] = {}
        self._pb_split: List[Tuple[Seg, str]] = []     # (buffer bit, IE-report line) of unjoined buffers

    def err(self, origin: str, msg: str) -> None:
        self.notes.append(error(origin, msg))

    # -- driver ---------------------------------------------------------------------

    def run(self) -> CutAnalysis:
        top_ent = self._top_entity()
        self.st.top_entity = top_ent
        self._walk(top_ent)
        self._check_cells()
        self._read_runs()
        for k, sc_id in enumerate(self.st.cut_scope):
            self._instance(k, self.st.scopes[sc_id])
        if not any(n.severity == "error" for n in self.notes):
            self._nets()
            self._undriven_warnings()
        errs = [n for n in self.notes if n.severity == "error"]
        self.ana.notes.extend(n for n in self.notes if n.severity != "error")
        if errs:
            raise NoteError(errs)
        self.ana._cut = self.st              # type: ignore[attr-defined]
        return self.ana

    def _top_entity(self) -> vhdl.Entity:
        d = self.design
        ent = d.entity(self.top)
        if ent is None:
            cands = [e for k, e in d.entities.items() if e.module == self.top]
            ent = cands[0] if len(cands) == 1 else None
        if ent is None or d.arch(ent.name) is None:
            raise NoteError([error(d.path, "design.vhd has no entity for the top module %s"
                                   % self.top)])
        if ent.module is not None and ent.module != self.top:
            raise NoteError([error(d.where(ent.line), "entity %s was generated from Verilog module "
                                   "%s, not from the top %s" % (ent.name, ent.module, self.top))])
        if ent.deferred:
            raise NoteError([error(d.where(ent.line), "the top module %s was not translated "
                                   "(sv2vhdl:deferred stub)" % self.top)])
        return ent

    # -- 1. the walk -------------------------------------------------------------------

    def _walk(self, top_ent: vhdl.Entity) -> None:
        d = self.design
        top_scope = _Scope(0, (), top_ent, d.arch(top_ent.name), None, None, TOP, self.top)
        self.st.scopes.append(top_scope)
        stack = [0]
        missing_t3 = 0
        while stack:
            sc = self.st.scopes[stack.pop()]
            arch = sc.arch
            assert arch is not None
            kids: List[int] = []
            for si, stmt in enumerate(arch.stmts):
                if stmt.kind != "instance":
                    continue
                where = d.where(stmt.line)
                if stmt.lib == "sv2vhdl":
                    if stmt.entity.lower() not in sv2vhdl_modes.MODES:
                        self.err(where, "unknown sv2vhdl library entity %s (instance %s)"
                                 % (stmt.entity, stmt.label))
                    continue
                if stmt.lib != "work":
                    self.err(where, "instance %s of %s.%s: unknown library" %
                             (stmt.label, stmt.lib, stmt.entity))
                    continue
                ent = d.entity(stmt.entity)
                if ent is None:
                    self.err(where, "instance %s: entity %s is not in design.vhd"
                             % (stmt.label, stmt.entity))
                    continue
                if ent.deferred:
                    self.err(where, "module %s (instance %s) was not translated (sv2vhdl:deferred)"
                             % (ent.module or ent.name, stmt.label))
                    continue
                rel = stmt.vpath
                if rel is None:
                    missing_t3 += 1
                    rel = stmt.label or ""
                labels = sc.labels + (stmt.label or "",)
                vpath = sc.vpath + "." + rel
                is_cut = ent.module in self.cells
                child = _Scope(len(self.st.scopes), labels, ent, d.arch(ent.name), sc.id, stmt,
                               CUT if is_cut else USER, vpath)
                if child.arch is None:
                    self.err(where, "entity %s has no architecture" % ent.name)
                    continue
                self.st.scopes.append(child)
                sc.children[si] = child.id
                if is_cut:
                    child.inst = len(self.st.cut_scope)
                    self.st.cut_scope.append(child.id)
                else:
                    kids.append(child.id)
            stack.extend(reversed(kids))
        # depth-first textual order: the stack above visits scopes in that order,
        # but cut instances are numbered as they are met, which is the same order
        # only if every cut instance of a scope precedes its sub-scopes' ones.
        self._renumber_walk_order()
        if missing_t3:
            self.st.t3 = False
            self.notes.append(warning(
                d.path, "design.vhd has no '-- Verilog instance:' comments (translator patch "
                "T3): Verilog paths of cut instances use VHDL labels (tb.u_i0 for tb.g[0].u), so "
                "rules written with generate or array names will not match"))

    def _renumber_walk_order(self) -> None:
        """Order cut instances depth first, textual order within each architecture."""
        order: List[int] = []

        def visit(sid: int) -> None:
            sc = self.st.scopes[sid]
            for si in sorted(sc.children):
                c = self.st.scopes[sc.children[si]]
                if c.kind == CUT:
                    order.append(c.id)
                else:
                    visit(c.id)

        visit(0)
        self.st.cut_scope = order
        for k, sid in enumerate(order):
            self.st.scopes[sid].inst = k

    def _check_cells(self) -> None:
        count = collections.Counter(self.st.scopes[s].entity.module for s in self.st.cut_scope)
        for name, cell in self.cells.items():
            if count[name]:
                continue
            msg = "cell %s has no instance under top %s" % (name, self.top)
            if cell.view == "spice":
                self.err(cell.origin or self.design.path, msg)
            else:
                self.notes.append(warning(cell.origin or self.design.path, msg))
        if not self.st.cut_scope:
            self.err(self.design.path, "no SPICE instance found under top %s" % self.top)

    # -- 2. cut instances and variants ----------------------------------------------------

    def _instance(self, k: int, sc: _Scope) -> None:
        cell = self.cells[sc.entity.module or ""]
        ent = sc.entity
        vb = self.ana.variants.get(ent.name)
        if vb is None:
            vb = self._bind_variant(ent, cell)
            self.ana.variants[ent.name] = vb
            self.st.variant_cells[ent.lname] = cell.name
        top_name = self.st.top_entity.name if self.st.top_entity else self.top
        ci = CutInstance(labels=list(sc.labels), vpath=sc.vpath,
                         path_name=names.path_name(top_name, sc.labels), cell=cell.name,
                         variant=ent.name, subckt=cell.subckt, params=self._param_texts(ent))
        self._param_info(ci, ent, cell)
        sub, pm = self._covering(cell, ci.vpath)
        if sub is not None:
            ci.subckt = sub
        ci.portmap = pm
        subckt = self._subckt(ci.subckt)
        if subckt is None:
            self.err(cell.origin or ci.vpath, "cut instance %s: subckt %s is not in the netlists"
                     % (ci.vpath, ci.subckt))
        elif vb.ranges is not None and len(vb.ranges) == len(cell.ports):
            try:
                ci.spice = portmap.bind_bits(cell, pm, vb.ranges, subckt.orig_ports)
            except NoteError as e:
                for n in e.notes:
                    self.notes.append(Note(n.severity, n.origin or ci.vpath,
                                           "%s (instance %s)" % (n.message, ci.vpath)))
        self.ana.instances.append(ci)

    def _subckt(self, name: str):
        subs = self.nl.subckts() if self.nl is not None else {}
        s = subs.get(name)
        if s is None:
            for k, v in subs.items():
                if k.lower() == name.lower():
                    return v
        return s

    # -- parameter values (§4.7: cut.param_overrides) ---------------------------------------

    def _read_runs(self) -> None:
        """The elaborated parameter values in sv2vhdl-modules' per-module runs (_mods.vhd).

        A real or string parameter does not enter a variant's name, so two runs
        (the top's and a wrapper's, or the cell's own at its defaults) can make
        same-named variants with different "--   P = v" lines, of which
        design.vhd keeps the first.  The top's run made every variant the
        design elaborates, so its lines are the instances' values; each run's
        root is its module at the defaults.
        """
        self.run_values: Optional[Dict[str, Dict[str, str]]] = None
        self.run_roots: Dict[str, Dict[str, str]] = {}
        self.param_cache: Dict[str, Tuple[Optional[Dict[str, str]], Set[str]]] = {}
        self.bound = {self.st.scopes[s].entity.lname for s in self.st.cut_scope}
        if not any(c.view == "multi" and c.params for c in self.cells.values()):
            return
        runs = vhdl.read_translation_runs(os.path.dirname(self.design.path))
        for r in runs or []:
            if r[-1].module is not None:
                self.run_roots.setdefault(r[-1].module, r[-1].params)
        tops = [r for r in runs or [] if r[-1].module == self.top]
        if len(tops) == 1:
            self.run_values = {e.name.lower(): e.params for e in tops[0]}

    def _param_texts(self, ent: vhdl.Entity) -> Dict[str, str]:
        """Every parameter value of a variant as tgt-vhdl printed it (integers from
        nvc_verilog_params, the rest from the top's run, else design.vhd's lines)."""
        texts = dict(ent.param_comments)
        if self.run_values is not None and ent.lname in self.run_values:
            texts.update(self.run_values[ent.lname])
        texts.update(ent.params)
        return texts

    def _param_info(self, ci: CutInstance, ent: vhdl.Entity, cell: CutCell) -> None:
        """What param_overrides() needs besides CutInstance.params (private attributes)."""
        typed: Dict[str, Optional[VValue]] = {}
        for p, t in ci.params.items():
            typed[p] = printed_value(t, real=p not in ent.params)
        if cell.name not in self.param_cache:
            self.param_cache[cell.name] = (self._printed_defaults(cell), self._local_params(cell))
        printed, local = self.param_cache[cell.name]
        ci._param_values = typed                  # type: ignore[attr-defined]
        ci._param_defaults = printed              # type: ignore[attr-defined]
        ci._param_locals = local                  # type: ignore[attr-defined]

    def _printed_defaults(self, cell: CutCell) -> Optional[Dict[str, str]]:
        """What tgt-vhdl printed for the cell module elaborated on its own, every parameter
        at its default: the root of its sv2vhdl-modules run, else the entity design.vhd
        keeps under the module's own name when no cut instance binds it (sv2vhdl-modules
        renames the root only when the module name is no valid entity name); None
        without either."""
        if cell.name in self.run_roots:
            return dict(self.run_roots[cell.name])
        ent = self.design.entity(cell.name)
        if ent is None or ent.module != cell.name or ent.deferred or ent.lname in self.bound:
            return None
        texts = dict(ent.param_comments)
        texts.update(ent.params)
        return texts

    def _local_params(self, cell: CutCell) -> Set[str]:
        """The cell's parameters no override can reach (localparams, body parameters beside
        a parameter port list), from the module header when the caller passed the PP."""
        if self.pp is None or cell.view != "multi" or not cell.params:
            return set()
        try:
            h = verilog_ports.module_header(self.pp, cell.name)   # type: ignore[arg-type]
        except (NoteError, AttributeError, KeyError, TypeError):
            return set()
        return {p.name for p in h.params if p.local}

    def _covering(self, cell: CutCell, vpath: str) -> Tuple[Optional[str], Optional[PortMap]]:
        """Per-instance subckt and port map from the use_spice statement covering vpath."""
        case = bool(self.cfg and self.cfg.xa.get("case") == "sensitive")
        wide: Optional[Tuple[int, str, list]] = None
        inst_hits: List[Tuple[int, str, list]] = []
        inst_stmts = 0
        for i, us in enumerate(self.cfg.use_spice if self.cfg else []):
            sub = None
            for glob, s in us.cells:
                if globs.match(glob, cell.name, case):
                    sub = s
            if sub is None:
                continue
            if not us.insts:
                wide = (i, sub, us.port_map)
                continue
            inst_stmts += 1
            for j, pat in enumerate(us.insts):
                if globs.match(pat, vpath, case):
                    self.hits.mark("use_spice_inst#%d.%d" % (i, j))
                    if not inst_hits or inst_hits[-1][0] != i:
                        inst_hits.append((i, sub, us.port_map))
        origin = cell.origin or vpath
        if len(inst_hits) > 1:
            self.err(origin, "cut instance %s is covered by %d use_spice -inst statements (%s)"
                     % (vpath, len(inst_hits), ", ".join(self.cfg.use_spice[i].origin
                                                         for i, _, _ in inst_hits)))
            return None, None
        chosen = inst_hits[0] if inst_hits else wide
        if chosen is None:
            if inst_stmts and cell.view == "multi":
                self.err(origin, "use_spice -inst statements for cell %s do not cover instance "
                         "%s (every instance of a multi-view cell must be covered)"
                         % (cell.name, vpath))
            return None, None
        i, sub, items = chosen
        pm = self._portmap(items) if items else None
        subname = (sub or cell.name).lower()
        if cell.view == "spice" and inst_hits:
            if subname != cell.subckt.lower() or pm is not None:
                self.err(self.cfg.use_spice[i].origin or origin,
                         "use_spice -inst on SPICE-only cell %s gives instance %s its own subckt "
                         "or port_map (one shell per cell; not supported)" % (cell.name, vpath))
            return None, None
        if chosen is wide and pm is None and subname == cell.subckt.lower():
            return None, None
        return subname, pm

    def _portmap(self, items: Sequence[Tuple[str, str]]) -> PortMap:
        pm = PortMap(bus_formats=self.cfg.formats() if self.cfg else ["[%d]"])
        for k, v in items:
            if k == "*":
                pm.default = v
            else:
                pm.explicit[k] = v
        return pm

    def _bind_variant(self, ent: vhdl.Entity, cell: CutCell) -> VariantBind:
        """Positional port binding of one variant with the hard checks of §5.4 step 2."""
        d = self.design
        where = d.where(ent.line)
        vb = VariantBind(entity=ent.name, clone=names.clone_entity(ent.name), params=dict(ent.params))
        if len(ent.ports) != len(cell.ports):
            self.err(where, "entity %s (Verilog module %s) has %d ports, the cut cell has %d"
                     % (ent.name, cell.name, len(ent.ports), len(cell.ports)))
            vb.ranges = []           # type: ignore[assignment]
            return vb
        params = eval_params(cell.params, ent.params)
        arch = d.arch(ent.name)
        covered, dropped, foreign = self._footprint(ent, arch)
        self.st.footprint[ent.lname] = dropped
        self.st.foreign[ent.lname] = foreign
        for si in foreign:
            st = arch.stmts[si]
            self.notes.append(note(d.where(st.line), "statement %s in shell variant %s is kept as "
                                   "a digital driver/reader (a connection the translator folded "
                                   "into the cell)" % (st.label or "at line %d" % st.line,
                                                       ent.name)))
        for cp, vp in zip(sorted(cell.ports, key=lambda p: p.index), ent.ports):
            vb.vhdl_ports.append(vp.name)
            if not vhdl.safe_name_matches(vp.name, cp.verilog):
                self.err(where, "entity %s port %d is %s, which tgt-vhdl does not make of Verilog "
                         "port %s (port order changed?)" % (ent.name, cp.index, vp.name,
                                                            cp.verilog))
            kind = vp.type.kind
            if kind == "other" or (kind == "real") != (cp.kind == REAL):
                self.err(where, "entity %s port %s has type %s; the cut cell port %s is %s"
                         % (ent.name, vp.name, vp.type.text, cp.verilog, cp.kind))
            rng: Optional[Tuple[int, int]] = None
            if cp.msb is not None and cp.lsb is not None:
                rng = (cp.msb, cp.lsb)
            elif cp.range_text:
                rng = range_of_text(cp.range_text, params)
                if rng is None:
                    self.err(where, "cannot evaluate the range %s of port %s of %s with %s"
                             % (cp.range_text, cp.verilog, cell.name,
                                " ".join("%s=%s" % kv for kv in sorted(ent.params.items()))
                                or "no parameter values"))
            vb.ranges.append(rng)
            vwidth = 1 if rng is None else abs(rng[0] - rng[1]) + 1
            vb.vhdl_vector.append(vp.type.vector)
            if kind == "logic":
                if vp.type.vector and vp.type.rng is None:
                    self.err(where, "entity %s port %s: cannot read its range %s"
                             % (ent.name, vp.name, vp.type.text))
                elif vp.type.width != vwidth:
                    self.err(where, "entity %s port %s is %d bits wide; Verilog port %s%s is %d"
                             % (ent.name, vp.name, vp.type.width, cp.verilog,
                                cp.range_text or "", vwidth))
                elif vp.type.vector and rng is None:
                    self.err(where, "entity %s port %s is a vector; Verilog port %s is a scalar"
                             % (ent.name, vp.name, cp.verilog))
            want = MODE_OF_DIR.get(cp.shell_dir, "inout")
            if vp.mode != want:
                self.err(where, "entity %s port %s has mode %s; the shell declares it %s"
                         % (ent.name, vp.name, vp.mode, cp.shell_dir))
            if kind == "logic" and vp.mode in ("out", "inout"):
                have = covered.get(vp.lname, set())
                miss = [o for o in range(vp.type.width) if o not in have]
                if miss:
                    self.err(where, "entity %s port %s: no marker on bit(s) %s (the shell "
                             "markers are missing; outputs may be dropped)"
                             % (ent.name, vp.name, ", ".join(str(o) for o in miss)))
        return vb

    def _footprint(self, ent: vhdl.Entity, arch: vhdl.Architecture
                   ) -> Tuple[Dict[str, Set[int]], Set[int], List[int]]:
        """(marker-covered port bits, dropped statements, foreign statements) of a variant."""
        ports = {p.lname: p for p in ent.ports}
        idx = _Index(self, arch, ent, subset=set())        # only for reference helpers
        dropped: Set[int] = set()
        covered: Dict[str, Set[int]] = {}
        ytemps: Dict[str, Tuple[str, int]] = {}             # temp -> marker
        feeders: Set[str] = set()
        for si, st in enumerate(arch.stmts):
            if st.kind != "instance" or st.lib != "sv2vhdl" or st.entity.lower() != "sv_bufif1":
                continue
            if not st.label or not _MARKER_RX.match(st.label):
                continue
            dropped.add(si)
            for a in st.assocs:
                if a.actual is None:
                    continue
                if a.formal.lname == "y":
                    for (n, o) in idx.bits(a.actual):
                        if n in ports:
                            covered.setdefault(n, set()).add(o)
                        else:
                            ytemps[n] = (n, o)
                else:
                    feeders.update(idx.resolve(r).lname for r in a.actual)
        for si, st in enumerate(arch.stmts):
            if st.kind != "process" or si in dropped:
                continue
            if st.assigns and all(idx.resolve(a.target).lname in feeders and a.const
                                  for a in st.assigns):
                dropped.add(si)               # constant feeders of the markers
                continue
            if len(st.assigns) == 1 and (st.simple or st.fused):
                asg = st.assigns[0]
                tgt = idx.resolve(asg.target)
                if asg.copy and tgt.lname in ports and \
                        all(idx.resolve(r).lname in ytemps for r in asg.copy):
                    tb = idx.bits([asg.target])
                    rb = idx.bits(asg.copy)
                    if len(tb) == len(rb):
                        for (n, o), _ in zip(tb, rb):
                            covered.setdefault(n, set()).add(o)
                        dropped.add(si)
                        continue
                if asg.copy and len(asg.copy) == 1 and tgt.lname in ports and \
                        idx.resolve(asg.copy[0]).lname == tgt.lname + "_reg":
                    dropped.add(si)           # <port>_Reg shadow of a real/reg output
                    continue
        foreign = [si for si in range(len(arch.stmts)) if si not in dropped]
        for si in foreign:
            st = arch.stmts[si]
            if st.kind == "instance" and st.lib == "work":
                self.err(self.design.where(st.line), "shell variant %s instantiates %s (%s): a "
                         "cut cell's shell must not contain module instances"
                         % (ent.name, st.entity, st.label))
        return covered, dropped, foreign

    # -- 3/4. nets, drivers, readers ---------------------------------------------------

    def _index(self, sc: _Scope) -> _Index:
        if sc.index is not None:
            return sc.index
        arch = sc.arch
        assert arch is not None
        if sc.kind == CUT:
            key = "cut:" + sc.entity.lname
            subset = set(self.st.foreign.get(sc.entity.lname, []))
        else:
            key = sc.entity.lname
            subset = None
        idx = self.arch_index.get(key)
        if idx is None:
            idx = _Index(self, arch, sc.entity, subset)
            self.arch_index[key] = idx
        sc.index = idx
        return idx

    def _nets(self) -> None:
        st = self.st
        ana = self.ana
        parent: Dict[Seg, Seg] = {}

        def find(x: Seg) -> Seg:
            root = x
            while parent[root] != root:
                root = parent[root]
            while parent[x] != root:
                parent[x], x = root, parent[x]
            return root

        def union(a: Seg, b: Seg) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        queue: collections.deque = collections.deque()
        order: List[Seg] = []
        touches: Dict[Seg, List[Tuple[int, _Touch]]] = {}
        # seg -> [(parent scope, stmt, assoc, the literal of this bit of a constant actual)]
        ties: Dict[Seg, List[Tuple[int, int, int, Optional[str]]]] = {}
        # port buffer copies met: (scope, stmt) -> [(buffer bit, actual bit)]
        pbs: Dict[Tuple[int, int], List[Tuple[Seg, Seg]]] = collections.OrderedDict()

        def add(s: Seg) -> None:
            if s not in parent:
                parent[s] = s
                order.append(s)
                queue.append(s)

        portrefs: Dict[Seg, PortRef] = {}
        for i, sid in enumerate(st.cut_scope):
            sc = st.scopes[sid]
            cell = self.cells[sc.entity.module or ""]
            vb = ana.variants[sc.entity.name]
            for cp in sorted(cell.ports, key=lambda p: p.index):
                vname = vb.vhdl_ports[cp.index].lower()
                rng = vb.ranges[cp.index] if cp.index < len(vb.ranges) else None
                width = 1 if rng is None or cp.kind == REAL else abs(rng[0] - rng[1]) + 1
                for off in range(width):
                    s = (sid, vname, off)
                    portrefs[s] = PortRef(i, cp.index, off)
                    add(s)
        while queue:
            seg = queue.popleft()
            sid, name, off = seg
            sc = st.scopes[sid]
            idx = self._index(sc)
            # (a) up through the parent's port map
            if sc.parent is not None and name in idx.ports:
                psc = st.scopes[sc.parent]
                pidx = self._index(psc)
                fm = pidx.formal.get(sc.stmt.index if sc.stmt else -1, {}).get((name, off))
                if fm is not None:
                    if fm[0] == "#expr":
                        ties.setdefault(seg, []).append(
                            (psc.id, sc.stmt.index, fm[1],
                             pidx.tie_lits.get((sc.stmt.index, name, off))))
                    else:
                        other = (psc.id, fm[0], fm[1])
                        add(other)
                        union(seg, other)
            # (b) aliases, shadows, port temporaries
            for (n2, o2) in idx.joins.get((name, off), ()):
                other = (sid, n2, o2)
                add(other)
                union(seg, other)
            # (c) down into user-module and cut children
            for (si, formal, fo) in idx.assoc.get(name, {}).get(off, ()):
                cid = sc.children.get(si)
                if cid is None:
                    continue
                other = (cid, formal, fo)
                add(other)
                union(seg, other)
            # (d) statements
            for t in idx.touch.get(name, ()):
                if t.offs is None or off in t.offs:
                    touches.setdefault(seg, []).append((sid, t))
            # (e) port buffers: searched both ways, joined (one way) or not below
            for (si, o, other_is_pb) in idx.pb_edges.get((name, off), ()):
                other = (sid, o[0], o[1])
                add(other)
                pair = (other, seg) if other_is_pb else (seg, other)
                lst = pbs.setdefault((sid, si), [])
                if pair not in lst:
                    lst.append(pair)

        skip: Set[Tuple[int, int, Seg]] = set()
        if pbs:
            skip = self._port_buffers(pbs, find, union, order, touches, ties, portrefs)
            if skip:
                touches = {s: [(tsid, t) for (tsid, t) in ts if (tsid, t.stmt, s) not in skip]
                           for s, ts in touches.items()}

        # group segments into nets, numbered by their first cut port in walk order
        groups: Dict[Seg, List[Seg]] = collections.OrderedDict()
        for s in order:
            groups.setdefault(find(s), []).append(s)
        root_ports: Dict[Seg, List[Tuple[PortRef, Seg]]] = {}
        for s, pr in portrefs.items():
            root_ports.setdefault(find(s), []).append((pr, s))
        roots = sorted(root_ports, key=lambda r: min((p.inst, p.port, p.bit)
                                                     for p, _ in root_ports[r]))
        seg_net: Dict[Seg, str] = {}
        for k, r in enumerate(roots):
            key = "net%d" % k
            for s in groups[r]:
                seg_net[s] = key
        st.seg_net = seg_net
        for s, text in self._pb_split:
            k = seg_net.get(s)
            if k is not None and text not in st.pb_notes.get(k, []):
                st.pb_notes.setdefault(k, []).append(text)
        for k, r in enumerate(roots):
            key = "net%d" % k
            net = Net(key=key)
            prs = sorted(root_ports[r], key=lambda x: (x[0].inst, x[0].port, x[0].bit))
            for pr, s in prs:
                ci = ana.instances[pr.inst]
                if self._passive(ci, pr):
                    net.passive.append(pr)
                else:
                    net.ports.append(pr)
            self._net_contents(net, groups[r], touches, ties, seg_net)
            ana.nets.append(net)
        self._guards(portrefs, seg_net)

    # -- port buffers (one-way joins) -------------------------------------------------

    def _port_buffers(self, pbs: Dict[Tuple[int, int], List[Tuple[Seg, Seg]]],
                      find: Callable[[Seg], Seg], union: Callable[[Seg, Seg], None],
                      order: List[Seg], touches: Dict[Seg, List[Tuple[int, _Touch]]],
                      ties: Dict[Seg, List[Tuple[int, int, int, Optional[str]]]],
                      portrefs: Dict[Seg, PortRef]) -> Set[Tuple[int, int, Seg]]:
        """Decide every port buffer copy met by the search, bit by bit (module docstring).

        W is the buffer bit's net without the copy: the wrapper's side of its input
        port.  Returns the (scope, stmt, seg) touches of the joined copies, which are
        neither drivers nor readers any more (the copy is a wire on the joined net).
        """
        classes: Dict[Seg, List[Seg]] = {}
        for s in order:
            classes.setdefault(find(s), []).append(s)
        ports_of: Dict[Seg, List[PortRef]] = {}
        for s, pr in portrefs.items():
            ports_of.setdefault(find(s), []).append(pr)
        reported: Set[Tuple[int, int]] = set()
        joins: List[Tuple[int, int, Seg, Seg]] = []
        kept: Set[Tuple[int, int, Seg]] = set()        # actual bits a copy still reads
        for (sid, si), pairs in pbs.items():
            for pb_seg, src_seg in pairs:
                if self._pb_join(sid, si, pb_seg, src_seg, find, classes, ports_of, touches, ties,
                                 reported):
                    joins.append((sid, si, pb_seg, src_seg))
                else:
                    kept.add((sid, si, src_seg))
        skip: Set[Tuple[int, int, Seg]] = set()
        for sid, si, pb_seg, src_seg in joins:
            union(pb_seg, src_seg)
            skip.add((sid, si, pb_seg))
            if (sid, si, src_seg) not in kept:
                skip.add((sid, si, src_seg))
        return skip

    def _pb_join(self, sid: int, si: int, pb_seg: Seg, src_seg: Seg, find: Callable[[Seg], Seg],
                 classes: Dict[Seg, List[Seg]], ports_of: Dict[Seg, List[PortRef]],
                 touches: Dict[Seg, List[Tuple[int, _Touch]]],
                 ties: Dict[Seg, List[Tuple[int, int, int, Optional[str]]]],
                 reported: Set[Tuple[int, int]]) -> bool:
        """Whether one bit of a port buffer copy joins (marking W's cut ports one-way); a
        bit that does not is noted (W driven inside) or an error (a cut output on W, or W
        read inside while a cut port could drive it)."""
        w = find(pb_seg)
        if w == find(src_seg):
            return False                          # never drawn so; leave the copy as it is
        inside, readers = self._pb_side(sid, si, classes[w], touches, ties)
        port, src = self._pb_names(sid, pb_seg, src_seg, find, classes)
        if inside:
            # the wrapper drives its port's net too: a net of its own, as in Verilog
            self._pb_split.append((pb_seg, "port buffer: input port %s, fed one way from %s, is "
                                   "also driven inside %s (a net of its own)"
                                   % (port, src, port.rsplit(".", 1)[0])))
            return False
        active = [pr for pr in ports_of.get(w, ())
                  if not self._passive(self.ana.instances[pr.inst], pr)]
        drivable = [pr for pr in active if self._cut_port(pr).kind != REAL
                    and self._cut_port(pr).shell_dir in (OUTPUT, INOUT)]
        outs = [pr for pr in drivable if self._cut_port(pr).shell_dir == OUTPUT]
        bad = outs or (drivable if readers else [])
        for pr in bad:
            if (pr.inst, pr.port) not in reported:
                reported.add((pr.inst, pr.port))
                self._pb_error(pr, port, src, readers)
        if bad:
            return False
        for pr in drivable:
            self.st.one_way[pr] = "one-way port buffer: input port %s fed from %s" % (port, src)
        return True

    def _pb_side(self, sid: int, si: int, segs: List[Seg],
                 touches: Dict[Seg, List[Tuple[int, _Touch]]],
                 ties: Dict[Seg, List[Tuple[int, int, int, Optional[str]]]]
                 ) -> Tuple[bool, List[int]]:
        """(whether anything but the copy drives these segments, the scopes that read them)."""
        driven = False
        readers: List[int] = []
        for s in segs:
            for (tsid, t) in touches.get(s, ()):
                if tsid == sid and t.stmt == si:
                    continue                      # the copy itself
                if t.what in ("drv", "force", "release"):
                    driven = True
                elif t.what == "rd" and tsid not in readers:
                    readers.append(tsid)
            for (psid, psi, ai, lit) in ties.get(s, ()):
                a = self.st.scopes[psid].arch.stmts[psi].assocs[ai]
                if not (a.z_only or lit == _Z_LIT):
                    driven = True
        return driven, readers

    def _cut_port(self, pr: PortRef) -> CutPort:
        return self.cells[self.ana.instances[pr.inst].cell].ports[pr.port]

    def _pb_names(self, sid: int, pb_seg: Seg, src_seg: Seg, find: Callable[[Seg], Seg],
                  classes: Dict[Seg, List[Seg]]) -> Tuple[str, str]:
        """("tb.we.a", "variable tb.clk"): the input port bit a buffer feeds, and its actual
        ("an expression" when the actual is a translator temporary of no declared signal)."""
        st = self.st
        sc = st.scopes[sid]
        idx = self._index(sc)
        port = None
        for (ist, formal, fo) in idx.assoc.get(pb_seg[1], {}).get(pb_seg[2], ()):
            cid = sc.children.get(ist)
            if cid is not None:
                got = self._bit_alias(st.scopes[cid], formal, fo)
                if got is not None:
                    port = got[0]
                    break
        if port is None:
            sd = sc.arch.signals.get(pb_seg[1]) if sc.arch is not None else None
            pb = sd.port_buffer if sd is not None else None
            port = "%s.%s.%s" % (sc.vpath, pb[1], pb[0]) if pb else "%s.%s" % (sc.vpath, pb_seg[1])
        src = self._bit_alias(sc, src_seg[1], src_seg[2])
        if src is None:
            # a temporary: the declared signal it is a wire of, in the same scope
            for s in classes.get(find(src_seg), ()):
                if s[0] == sid and s != src_seg:
                    src = self._bit_alias(sc, s[1], s[2])
                    if src is not None:
                        break
        if src is None:
            return port, "an expression"
        return port, ("variable " if src[1] else "") + src[0]

    def _bit_alias(self, sc: _Scope, lname: str, off: int) -> Optional[Tuple[str, bool]]:
        """("tb.rv[1]", is a variable) for a bit of a declared signal or port of a user
        scope, None for a translator temporary."""
        if sc.kind == CUT:
            return None
        vn = self._verilog_signal(sc, lname)
        if vn is None:
            return None
        vname, decl = vn
        module = sc.entity.module or sc.entity.name
        if self.pp is not None:
            is_var = bool(self.pp.is_variable(module, vname))
        else:
            is_var = decl is not None and decl.variable
        bare = "%s.%s" % (sc.vpath, vname)
        ts = self._index(sc).types.get(lname)
        if ts is not None and ts.vector:
            rng = self._decl_range(sc, decl)
            if rng is not None:
                return "%s[%d]" % (bare, portmap.bit_of(off, rng)), is_var
        return bare, is_var

    def _pb_error(self, pr: PortRef, port: str, src: str, readers: List[int]) -> None:
        ci = self.ana.instances[pr.inst]
        cell = self.cells[ci.cell]
        cp = cell.ports[pr.port]
        fix = ("port_dir -cell %s (input %s;)" % (cell.subckt, cp.verilog) if cell.view == "spice"
               else "in the Verilog view of %s" % cell.name)
        if cp.shell_dir == OUTPUT:
            what = ("is an output behind input port %s, fed one way from %s (a port buffer): the "
                    "cell could drive only the wrapper's side of that port, against the buffer"
                    % (port, src))
        else:
            where = ", ".join(self.st.scopes[r].vpath for r in readers[:3])
            what = ("is inout behind input port %s, fed one way from %s (a port buffer), and %s "
                    "reads that port: what the cell drives there would reach only the wrapper's "
                    "side, which vamos does not model" % (port, src, where))
        self.err(ci.vpath, "port %s of %s %s; declare it input (%s), or connect a net to %s"
                 % (cp.verilog, ci.vpath, what, fix, port))

    def _passive(self, ci: CutInstance, pr: PortRef) -> bool:
        """A bit mapped to no SPICE port, a ground port or a port_connect'ed port."""
        sp = ci.spice.get((pr.port, pr.bit))
        if sp is None:
            return True
        sub = self._subckt(ci.subckt)
        if sub is not None:
            gnd = {sub.orig_ports[i].lower() for i in sub.gnd_ports if i < len(sub.orig_ports)}
            if sp.lower() in gnd:
                return True
        cell = self.cells[ci.cell]
        if sp.lower() in {k.lower() for k in cell.connects}:
            return True
        # -cell names the Verilog cell or its subckt, as in shells._connects and deck.py
        # (deck._cell_names); set_sim_case read as rules.case_sensitive reads it
        case = bool(self.cfg and str(self.cfg.xa.get("case") or "").lower() == "sensitive")
        cell_names = [n for n in (ci.cell, cell.subckt) if n]
        for i, pc in enumerate(self.cfg.port_connects if self.cfg else []):
            if not any(globs.match(pc.cell, n, case) for n in cell_names):
                continue
            if pc.inst is not None:
                if not globs.match(pc.inst, ci.vpath, case):
                    continue
                self.hits.mark("port_connect_inst#%d" % i)
            if any(p.lower() == sp.lower() for p, _, _ in pc.conns):
                return True
        return False

    def _net_contents(self, net: Net, segs: List[Seg], touches: Dict[Seg, List[Tuple[int, _Touch]]],
                      ties: Dict[Seg, List[Tuple[int, int, int, Optional[str]]]],
                      seg_net: Dict[Seg, str]) -> None:
        st = self.st
        drivers: Dict[Tuple[int, str], Driver] = collections.OrderedDict()
        readers: Set[Tuple[int, str]] = set()
        occ: List[Tuple[int, _Touch]] = []
        forced: Set[Tuple[int, str]] = set()
        aliases: List[str] = []
        rank = {SUPPLY1: 5, SUPPLY0: 5, STRONG: 4, UNKNOWN: 3, WEAK: 2, PULL_UP: 1, PULL_DOWN: 1}
        for s in segs:
            for (sid, t) in touches.get(s, ()):
                key = (sid, t.sid)                 # one driver per elaborated statement
                if t.what == "rd":
                    readers.add(key)
                elif t.what == "drv":
                    old = drivers.get(key)
                    if old is None or rank.get(t.strength, 0) > rank.get(old.strength, 0):
                        drivers[key] = Driver(t.strength, self._origin(sid, t.origin),
                                              t.removable, t.sid)
                    if old is None:
                        occ.append((sid, t))
                        if t.removable:
                            st.pull_occ.setdefault(t.sid, []).append((sid, net.key))
                elif t.what in ("force", "release") and key not in forced:
                    forced.add(key)
                    self.err(self._origin(sid, t.origin),
                             "force/release on mixed-signal net %s is not supported"
                             % self._net_name(s))
            for (psid, si, ai, lit) in ties.get(s, ()):
                psc = st.scopes[psid]
                pst = psc.arch.stmts[si]
                a = pst.assocs[ai]
                # a Z actual, or the Z bit of a constant actual, drives nothing
                if a.z_only or lit == _Z_LIT:
                    continue
                weak = (lit in _WEAK_INIT) if lit is not None else a.weak_only
                strength = WEAK if weak else STRONG
                key = (psid, "%s.%d" % (pst.sid, ai))
                old = drivers.get(key)
                if old is None or rank.get(strength, 0) > rank.get(old.strength, 0):
                    drivers[key] = Driver(strength,
                                          self._origin(psid, "%s:%s (%s => %s)"
                                                       % (psc.arch.entity, pst.label,
                                                          a.formal.name, a.text)),
                                          False, pst.sid)
        net.drivers = list(drivers.values())
        net.readers = len(readers)
        st.net_touches[net.key] = occ
        # nets the driven values depend on (for the quantised round-trip warning)
        rd_nets: Set[str] = set()
        for (sid, t) in occ:
            sc = st.scopes[sid]
            idx = sc.index
            if idx is None:
                continue
            for n, offs in idx.assign_reads.get((t.stmt, t.assign), ()):
                for o in (offs if offs is not None else {0}):
                    k2 = seg_net.get((sid, n, o))
                    if k2 is not None and k2 != net.key:
                        rd_nets.add(k2)
        st.net_reads_nets[net.key] = rd_nets
        # aliases (§3.1), variable and tri nets
        tri_kinds: Set[str] = set()
        for s in segs:
            sid, n, off = s
            sc = st.scopes[sid]
            if sc.kind == CUT:
                continue
            name = self._verilog_signal(sc, n)
            if name is None:
                continue
            vname, decl = name
            sd = sc.arch.signals.get(n) if sc.arch is not None else None
            if sd is not None and sd.declared_at is not None:
                o = "%s:%d" % sd.declared_at
                if o not in net.origins:
                    net.origins.append(o)
            module = sc.entity.module or sc.entity.name
            if self.pp is not None:
                # verilog_ports' declaration scan of pp.orig.v (§5.4 step 4)
                is_var = bool(self.pp.is_variable(module, vname))
                tri = self.pp.tri_kind(module, vname)
            else:
                is_var = decl is not None and decl.variable
                tri = decl.tri if decl is not None else None
            if is_var:
                net.variable = True
                vs = st.net_variables.setdefault(net.key, [])
                if '%s.%s' % (sc.vpath, vname) not in vs:
                    vs.append('%s.%s' % (sc.vpath, vname))
            if tri in ("tri0", "tri1"):
                tri_kinds.add(tri)
            ts = sc.index.types.get(n) if sc.index else None
            bare = "%s.%s" % (sc.vpath, vname)
            if bare not in aliases:
                aliases.append(bare)
            if ts is not None and ts.vector:
                rng = self._decl_range(sc, decl)
                if rng is None:
                    self.notes.append(note(bare, "the Verilog range of %s is unknown: only the "
                                           "bare-signal alias is used" % bare))
                    continue
                al = "%s[%d]" % (bare, portmap.bit_of(off, rng))
                if al not in aliases:
                    aliases.append(al)
        cut_aliases: List[str] = []
        for pr in net.ports + net.passive:
            ci = self.ana.instances[pr.inst]
            cell = self.cells[ci.cell]
            cp = cell.ports[pr.port]
            vb = self.ana.variants[ci.variant]
            rng = vb.ranges[pr.port] if pr.port < len(vb.ranges) else None
            sp = ci.spice.get((pr.port, pr.bit))
            if sp is not None:
                cut_aliases.append("%s.%s" % (ci.vpath, self._spell(sp)))
            if rng is None:
                cut_aliases.append("%s.%s" % (ci.vpath, cp.verilog))
            else:
                cut_aliases.append("%s.%s[%d]" % (ci.vpath, cp.verilog, portmap.bit_of(pr.bit, rng)))
                cut_aliases.append("%s.%s" % (ci.vpath, cp.verilog))
        seen: Set[str] = set()
        net.aliases = []
        for a in cut_aliases + aliases:
            if a not in seen:
                seen.add(a)
                net.aliases.append(a)
        for tri in sorted(tri_kinds):
            want = PULL_UP if tri == "tri1" else PULL_DOWN
            if not any(dv.strength == want for dv in net.drivers):
                self.err(net.aliases[0] if net.aliases else net.key,
                         "%s net %s reaches a SPICE port; its pull needs translator patch T5"
                         % (tri, self._first_parent_alias(net) or net.key))

    def _first_parent_alias(self, net: Net) -> Optional[str]:
        return min(net.aliases, key=lambda a: a.count(".")) if net.aliases else None

    def _origin(self, sid: int, origin: str) -> str:
        sc = self.st.scopes[sid]
        return "%s (%s)" % (origin, sc.vpath)

    def _net_name(self, seg: Seg) -> str:
        sc = self.st.scopes[seg[0]]
        vn = self._verilog_signal(sc, seg[1]) if sc.kind != CUT else None
        return "%s.%s" % (sc.vpath, vn[0] if vn else seg[1])

    def _spell(self, sp: str) -> str:
        if self.nl is not None:
            return self.nl.spelling.get(sp.lower(), sp)
        return sp

    # -- Verilog names of VHDL signals -------------------------------------------------

    def _vmodule(self, ent: vhdl.Entity) -> Optional[VModule]:
        if not ent.module or not ent.module_file:
            return None
        key = (ent.module, ent.module_file + ":%d" % ent.module_line)
        if key in self.vmods:
            return self.vmods[key]
        text = self._read(ent.module_file)
        vm = scan_module(text, ent.module, ent.module_line) if text is not None else None
        self.vmods[key] = vm
        return vm

    def _read(self, path: str) -> Optional[str]:
        if path in self.files:
            return self.files[path]
        # the translator writes _norm.sv next to design.vhd; the comment holds its absolute path
        cands = [os.path.join(os.path.dirname(self.design.path), os.path.basename(path)), path]
        text = None
        for c in cands:
            try:
                with open(c, errors="replace") as fh:
                    text = fh.read()
                break
            except OSError:
                continue
        self.files[path] = text
        return text

    def _verilog_signal(self, sc: _Scope, lname: str) -> Optional[Tuple[str, Optional[VDecl]]]:
        """(Verilog name, declaration) of a declared signal or port, None for temporaries."""
        arch = sc.arch
        sd = arch.signals.get(lname) if arch is not None else None
        port = sc.entity.port(lname)
        if sd is not None and (sd.temporary or sd.readable_shadow):
            return None
        if sd is None and port is None:
            return None
        vm = self._vmodule(sc.entity)
        decl = vm.lookup(sd.name if sd is not None else port.name) if vm is not None else None
        if decl is not None:
            return decl.name, decl
        return (sd.name if sd is not None else port.name), None

    def _decl_range(self, sc: _Scope, decl: Optional[VDecl]) -> Optional[Tuple[int, int]]:
        if decl is None or not decl.rng_text:
            return None
        params = {}
        for k, v in sc.entity.params.items():
            try:
                params[k] = int(v)
            except ValueError:
                pass
        for k, v in sc.entity.param_comments.items():
            try:
                params.setdefault(k, int(v))
            except ValueError:
                pass
        return range_of_text(decl.rng_text.replace(" ", ""), params)

    # -- guards ------------------------------------------------------------------------

    def _guards(self, portrefs: Dict[Seg, PortRef], seg_net: Dict[Seg, str]) -> None:
        """The temporaries guard of §5.4 step 3."""
        st = self.st
        done: Set[Tuple[int, int]] = set()
        for seg, pr in portrefs.items():
            if (pr.inst, pr.port) in done:
                continue
            sc = st.scopes[seg[0]]
            psc = st.scopes[sc.parent] if sc.parent is not None else None
            if psc is None or sc.stmt is None:
                continue
            pidx = self._index(psc)
            fm = pidx.formal.get(sc.stmt.index, {}).get((seg[1], seg[2]))
            if fm is None or fm[0] == "#expr":
                continue
            sd = psc.arch.signals.get(fm[0])
            if sd is None or not sd.temporary or sd.readable_shadow or not _GUARD_RX.match(fm[0]):
                continue
            cell = self.cells[sc.entity.module or ""]
            cp = cell.ports[pr.port]
            ci = self.ana.instances[pr.inst]
            done.add((pr.inst, pr.port))
            if cp.shell_dir == INOUT and cp.kind == LOGIC:
                # T2 aliases an inout port's bit- or part-select straight to the vector; what is
                # left is a one-way copy the translator warns about (a tran primitive on a
                # select, a select of an input or output port of the enclosing module)
                self.err(ci.vpath, "inout port %s of %s is connected through translator temporary "
                         "%s, a one-way copy (the translator joins this bit- or part-select one way "
                         "only, e.g. a tran primitive on a select); use port_dir or connect a plain "
                         "net" % (cp.verilog, ci.vpath, sd.name))
            elif pidx.drivers_of.get(fm[0], 0) > 1:
                self.err(ci.vpath, "port %s of %s is connected through translator temporary %s, "
                         "which has %d drivers; use port_dir or connect a plain net"
                         % (cp.verilog, ci.vpath, sd.name, pidx.drivers_of[fm[0]]))

    def _undriven_warnings(self) -> None:
        """Digitally read nets driven only by tgt-vhdl's undriven-net constant (§5.4)."""
        st = self.st
        seen_arch: Set[str] = set()
        for sc in st.scopes:
            if sc.kind == CUT or sc.arch is None or sc.entity.lname in seen_arch:
                continue
            if not any(st.scopes[c].kind == CUT for c in sc.children.values()):
                continue
            seen_arch.add(sc.entity.lname)
            idx = self._index(sc)
            for n, sd in sc.arch.signals.items():
                if sd.temporary or sd.readable_shadow:
                    continue
                ts = idx.touch.get(n, [])
                kinds = {t.what for t in ts}
                if "zdrv" not in kinds or "drv" in kinds or "rd" not in kinds:
                    continue
                if idx.drivers_of.get(n, 0) > sum(1 for t in ts if t.what == "zdrv"):
                    continue
                offs = sd.type.offsets()
                if any((sc.id, n, o) in st.seg_net for o in offs):
                    continue
                vn = self._verilog_signal(sc, n)
                self.notes.append(warning(
                    "%s.%s" % (sc.vpath, vn[0] if vn else sd.name),
                    "net is read but its only driver is the translator's undriven-net constant "
                    "(Z); a cut output connection dropped by the iverilog core looks like this"))


def analyse(design: vhdl.VhdlDesign, top: str,
            cells: Union[Dict[str, CutCell], Iterable[CutCell]], nl: Netlist, cfg: AmsConfig,
            hits: RuleHits, pp: Optional[object] = None,
            directions: Optional[Dict[str, Dict[str, str]]] = None) -> CutAnalysis:
    """Cut instances, variants and nets of the translated design (§5.4 steps 1-4).

    design: vhdl.parse(<daidir>/nvc/design.vhd).  top: the Verilog top module.
    cells: the CutCells (shell_dir set), as a list or a dict by name.  nl: the
    parsed netlist (subckts: orig_ports, gnd_ports; spelling).  cfg: the
    control file (use_spice -inst statements, port_connect, bus_format).
    hits: marks use_spice_inst#i.j and port_connect_inst#i selectors.
    pp (optional): the verilog_ports.PP; when given, Net.variable and the
    tri0/tri1 rule use pp.is_variable(module, name) / pp.tri_kind(module,
    name); without it the module declarations are read from the _norm.sv the
    "-- Generated from Verilog module" comments point to (next to design.vhd).
    directions (optional): shells.ShellResult.directions (cell -> auto port ->
    "auto-><mode> (<why>)"), kept for assign_roles' IE-report direction lines.

    Raises NoteError with every error found; notes and warnings go to
    CutAnalysis.notes.
    """
    return _Analyser(design, top, cells, nl, cfg, hits, pp, directions).run()


def port5(analysis: CutAnalysis, pr: PortRef) -> Tuple[str, str, str, str, str]:
    """rules.Port5 of a cut-port bit: (cell, inst path, spice port, verilog bit, verilog port).

    The SPICE port is in the netlist's original spelling ('' for a bit with no
    SPICE port); the Verilog bit is "a[3]" with the Verilog index ("a" for a
    scalar).
    """
    ci = analysis.instances[pr.inst]
    cp = analysis.cells[ci.cell].ports[pr.port]
    sp = ci.spice.get((pr.port, pr.bit))
    vb = analysis.variants.get(ci.variant)
    rng = vb.ranges[pr.port] if vb is not None and pr.port < len(vb.ranges) else None
    bit = cp.verilog if rng is None else "%s[%d]" % (cp.verilog, portmap.bit_of(pr.bit, rng))
    return (ci.cell, ci.vpath, _spelled(analysis, sp) if sp else "", bit, cp.verilog)


def printed_value(text: str, real: Optional[bool] = None) -> Optional[VValue]:
    """A parameter value as tgt-vhdl prints it ("--   P = v" lines, nvc_verilog_params).

    A string in double quotes (str); an integer, printed as the unsigned value
    of its bits (int); a real, printed in the C++ stream's default format with
    6 significant digits (float).  tgt-vhdl puts the integers, and only them,
    in nvc_verilog_params, so a value printed without point or exponent is an
    integer unless `real` says it is not; None for anything else.
    """
    t = text.strip()
    if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
        return t[1:-1]
    if not real and re.match(r"^[0-9]+$", t):
        return int(t)
    try:
        return float(t)
    except ValueError:
        return None


def param_defaults(defaults: Dict[str, str]) -> Dict[str, VValue]:
    """The header defaults that evaluate (vvalue), every parameter at its default."""
    vals: Dict[str, VValue] = {}
    pending = dict(defaults)
    progress = True
    while pending and progress:
        progress = False
        for k in list(pending):
            v = vvalue(pending[k], vals)
            if v is not None:
                vals[k] = v
                del pending[k]
                progress = True
    return vals


def _str_bits(s: str) -> int:
    """A string literal as a packed integer, 8 bits per character."""
    return int.from_bytes(s.encode("latin-1", "replace"), "big") if s else 0


def _same_printed(a: str, b: str) -> bool:
    """Whether two values tgt-vhdl printed are the same value."""
    a, b = a.strip(), b.strip()
    if a == b:
        return True
    x, y = printed_value(a, True), printed_value(b, True)
    return isinstance(x, float) and isinstance(y, float) and x == y


def _matches(got: Optional[VValue], value: Optional[VValue], loose: bool = False) -> bool:
    """Whether a printed value (printed_value) is what the constant `value` prints as.

    A negative integer also matches its 32- or 64-bit unsigned value (tgt-vhdl
    prints an integer parameter's bits unsigned); a string literal given to a
    packed parameter prints as its bits; a real matches when equal or when it
    is `value` rounded to the 6 significant digits tgt-vhdl prints (`loose`:
    or within 2e-5 relative, for a value computed from printed, rounded ones).
    """
    if got is None or value is None:
        return False
    if isinstance(got, str) or isinstance(value, str):
        if isinstance(got, str) and isinstance(value, str):
            return got == value
        return isinstance(got, int) and isinstance(value, str) and _str_bits(value) == got
    if isinstance(got, int) and isinstance(value, int):
        return got == value or (value < 0 and got in (value + (1 << 32), value + (1 << 64)))
    g, v = float(got), float(value)
    if g == v:
        return True
    if math.isnan(g) or math.isnan(v) or math.isinf(g) or math.isinf(v):
        return False
    if float("%g" % v) == g:
        return True
    return loose and abs(g - v) <= 2e-5 * max(abs(g), abs(v))


def _param_refs(text: str) -> Set[str]:
    """The identifiers (not system functions) a default expression uses."""
    return {v for kind, v in (_vtokens(text) or []) if kind == "id" and not v.startswith("$")}


def param_overrides(cell: CutCell, inst: CutInstance) -> Dict[str, Tuple[str, str]]:
    """Parameters of a cut instance whose value differs from the cell default.

    {name: (default text, actual value as printed)} over the cell's parameters
    (CutCell.params: the header defaults, body parameters and localparams
    included) in CutInstance.params, every elaborated value of the instance,
    integer, real or string, as tgt-vhdl printed it.  deck.py uses this for
    its §4.7 check.

    The default is what tgt-vhdl printed for the cell module elaborated on its
    own (analyse() keeps it on the instance as the private attribute
    _param_defaults), compared as printed; without it the header default is
    evaluated (vvalue), and one that does not evaluate counts as differing.  A
    parameter an override cannot reach (a localparam, or a body parameter
    beside a parameter port list: _param_locals, known when analyse() had
    the PP) is not one; nor is a value that differs only because its default
    expression uses parameters the instance changed (localparam real LSB =
    VREF / (1 << N) under #(.N(4))), when that expression, evaluated with the
    instance's values, gives it.  tgt-vhdl prints a real exactly (T12: the
    fewest of 6 to 17 significant digits that read back as the same double), so
    an override that changes only a late digit is seen too.
    """
    texts = inst.params
    dflt = param_defaults(cell.params)
    typed: Optional[Dict[str, Optional[VValue]]] = getattr(inst, "_param_values", None)
    if typed is None:
        # no analyse() typing: a value printed without point is real when its default is
        typed = {p: printed_value(t, real=isinstance(dflt.get(p), float)) for p, t in texts.items()}
    printed: Optional[Dict[str, str]] = getattr(inst, "_param_defaults", None)
    local: Set[str] = getattr(inst, "_param_locals", None) or set()

    def at_default(p: str) -> bool:
        if printed is not None and p in printed:
            return _same_printed(texts[p], printed[p])
        return _matches(typed.get(p), dflt.get(p))

    def derived(p: str) -> bool:
        refs = {q for q in _param_refs(cell.params[p]) if q != p and q in cell.params}
        if not any(q in texts and not at_default(q) for q in refs):
            return False
        ctx: Dict[str, VValue] = {}
        for q in cell.params:
            if q == p:
                continue
            if q in texts and not at_default(q):
                tv = typed.get(q)
                if tv is not None:
                    ctx[q] = tv
            elif q in dflt:
                ctx[q] = dflt[q]                      # full precision, not the printed digits
            elif typed.get(q) is not None:
                ctx[q] = typed[q]                     # type: ignore[assignment]
        return _matches(typed.get(p), vvalue(cell.params[p], ctx), loose=True)

    out: Dict[str, Tuple[str, str]] = {}
    for p, t in texts.items():
        if p not in cell.params or p in local:
            continue
        if not at_default(p) and not derived(p):
            out[p] = (cell.params[p], t)
    return out


# -- roles (§5.4 step 5) ------------------------------------------------------------------

def assign_roles(analysis: CutAnalysis, alloc: names.NameAllocator,
                 disabled: Callable[[List[str]], bool],
                 removal: Callable[[List[str]], Tuple[bool, Optional[float]]],
                 directions: Optional[Dict[str, Dict[str, str]]] = None) -> List[AnalogNode]:
    """One AnalogNode per net with a non-passive cut port, by the ordered role table.

    disabled(names) / removal(names): flow.py binds rules.disabled/removal to
    the config and RuleHits; names[0] is the canonical name, the rest are the
    aliases.  directions (optional): shells.ShellResult.directions, for the
    direction lines of the IE report (default: what analyse() was given).
    Notes go to analysis.notes; errors raise NoteError.
    """
    st: Optional[_State] = getattr(analysis, "_cut", None)
    if directions is None and st is not None:
        directions = st.directions
    out: List[AnalogNode] = []
    notes: List[Note] = []
    nodes_by_net: Dict[str, AnalogNode] = {}
    for net in analysis.nets:
        if not net.ports:
            if net.passive and net.drivers:
                notes.append(note(net.aliases[0] if net.aliases else net.key,
                                  "digital driver(s) on bits mapped to ground or port_connect'ed "
                                  "SPICE ports only: %s" % ", ".join(d.origin for d in net.drivers)))
            continue
        node = _role(analysis, net, alloc, disabled, notes, directions)
        if node is None:
            continue
        if st is not None:
            node.report.extend(st.pb_notes.get(net.key, []))
        nodes_by_net[net.key] = node
        out.append(node)
    # remove_d2a after the table
    for node in out:
        net = _net(analysis, node.net)
        rm, dc = removal([node.canonical] + [a for a in node.aliases if a != node.canonical])
        if not rm:
            continue
        if node.role == D2A:
            node.role = REMOVED
            node.host = None
            node.dc = dc
            node.shunt = dc is None
            node.report.append("remove_d2a%s" % (" dc=%r" % dc if dc is not None else ""))
        elif node.role == BIDIR:
            notes.append(error(node.canonical, "remove_d2a on bidirectional node %s is not "
                               "supported in v1" % node.canonical))
        else:
            notes.append(note(node.canonical, "remove_d2a: no D2A on %s (%s)"
                              % (node.canonical, node.role)))
    _pull_moves(analysis, st, out, notes)
    _warnings(analysis, st, out, nodes_by_net, notes)
    if st is not None:
        st.nodes_by_net = nodes_by_net
    errs = [n for n in notes if n.severity == "error"]
    analysis.notes.extend(n for n in notes if n.severity != "error")
    if errs:
        raise NoteError(errs)
    return out


def _net(analysis: CutAnalysis, key: Optional[str]) -> Optional[Net]:
    for n in analysis.nets:
        if n.key == key:
            return n
    return None


def _port_of(analysis: CutAnalysis, pr: PortRef):
    ci = analysis.instances[pr.inst]
    return ci, analysis.cells[ci.cell].ports[pr.port]


def _bit_name(analysis: CutAnalysis, pr: PortRef) -> str:
    ci, cp = _port_of(analysis, pr)
    vb = analysis.variants.get(ci.variant)
    rng = vb.ranges[pr.port] if vb is not None and pr.port < len(vb.ranges) else None
    if rng is None:
        return "%s.%s" % (ci.vpath, cp.verilog)
    return "%s.%s[%d]" % (ci.vpath, cp.verilog, portmap.bit_of(pr.bit, rng))


def _vhdl_mode(analysis: CutAnalysis, pr: PortRef) -> str:
    ci, cp = _port_of(analysis, pr)
    return MODE_OF_DIR.get(cp.shell_dir, "inout")


def _role(analysis: CutAnalysis, net: Net, alloc: names.NameAllocator,
          disabled: Callable[[List[str]], bool], notes: List[Note],
          directions: Optional[Dict[str, Dict[str, str]]] = None) -> Optional[AnalogNode]:
    ports = net.ports
    # canonical: the shortest instance path, ties to walk order
    best = min(ports, key=lambda p: (len(analysis.instances[p.inst].labels), p.inst, p.port, p.bit))
    ci, cp = _port_of(analysis, best)
    sp = ci.spice.get((best.port, best.bit)) or cp.verilog
    st: Optional[_State] = getattr(analysis, "_cut", None)
    # cut ports behind a joined port buffer drive nothing digital: inputs here
    one_way = st.one_way if st is not None else {}
    canonical = "%s.%s" % (ci.vpath, _spelled(analysis, sp))
    aliases = [a for a in net.aliases if a != canonical]
    names_list = [canonical] + aliases
    node = AnalogNode(name=names.node(alloc, canonical), canonical=canonical, aliases=aliases,
                      net=net.key, ports=list(ports), role=NONE)
    reals = [p for p in ports if _port_of(analysis, p)[1].kind == REAL]
    logics = [p for p in ports if _port_of(analysis, p)[1].kind != REAL]
    strengths = [d.strength for d in net.drivers]
    strong_weak = [d for d in net.drivers if d.strength in (STRONG, WEAK, UNKNOWN)]
    pulls = [d for d in net.drivers if d.strength in (PULL_UP, PULL_DOWN)]
    supplies = [d for d in net.drivers if d.strength in (SUPPLY1, SUPPLY0)]
    driven = bool(strong_weak or pulls)
    read = net.readers > 0
    drivable = [p for p in ports if _port_of(analysis, p)[1].shell_dir in (OUTPUT, INOUT)
                and p not in one_way]
    outputs = [p for p in ports if _port_of(analysis, p)[1].shell_dir == OUTPUT and p not in one_way]

    def why(drvs: Sequence[Driver]) -> str:
        return ", ".join(d.origin for d in drvs[:4]) + (" ..." if len(drvs) > 4 else "")

    if disabled(names_list):
        node.role = DISABLED
        node.shunt = True
        notes.append(warning(canonical, "disable_ie: no interface element on %s; the digital side "
                             "of its cut ports is not driven" % canonical))
        node.report.append("disable_ie")
        return node
    if reals:
        if logics:
            notes.append(error(canonical, "net %s joins real and logic SPICE ports (%s / %s)"
                               % (canonical, _bit_name(analysis, reals[0]),
                                  _bit_name(analysis, logics[0]))))
            return None
        real_outs = [p for p in reals if _port_of(analysis, p)[1].shell_dir == OUTPUT]
        if driven:
            if real_outs:
                notes.append(error(canonical, "real output %s and a digital driver (%s) on one net"
                                   % (_bit_name(analysis, real_outs[0]), why(net.drivers))))
                return None
            node.role, node.host = RD2A, reals[0]
        elif real_outs and read:
            node.role, node.host = RA2D, real_outs[0]
        else:
            node.role = THROUGH
        node.shunt = node.role != RD2A
        _report_role(analysis, node, net, directions)
        return node
    if supplies:
        kinds = {d.strength for d in supplies}
        if len(kinds) > 1:
            notes.append(error(canonical, "supply1 and supply0 drive one net (%s)" % why(supplies)))
            return None
        node.role = POWERNET
        others = [d for d in net.drivers if d not in supplies]
        if others:
            notes.append(note(canonical, "supply net %s also has other drivers (%s); the supply "
                              "wins" % (canonical, why(others))))
        node.report.append("supply net (%s)" % ("supply1" if SUPPLY1 in kinds else "supply0"))
        return node
    if strong_weak and outputs:
        notes.append(error(canonical, "SPICE output %s and a digital driver (%s) on one net; "
                           "declare the port inout with port_dir"
                           % (_bit_name(analysis, outputs[0]), why(strong_weak))))
        return None
    bidir_ports = [p for p in ports if p not in one_way and (
                   _port_of(analysis, p)[1].declared == INOUT
                   or (_port_of(analysis, p)[1].declared == AUTO
                       and _port_of(analysis, p)[1].shell_dir == INOUT and read))]
    if driven and bidir_ports:
        weak = [d for d in strong_weak if d.strength == WEAK]
        if weak:
            notes.append(error(canonical, "weak driver(s) %s on bidirectional net %s: only static "
                               "pulls are supported on a BIDIR net in v1" % (why(weak), canonical)))
            return None
        dirs = {d.strength for d in pulls}
        if len(dirs) > 1:
            notes.append(error(canonical, "pull-up and pull-down on bidirectional net %s (%s)"
                               % (canonical, why(pulls))))
            return None
        node.role = BIDIR
        node.host = bidir_ports[0]
        if pulls:
            node.pull = "up" if PULL_UP in dirs else "down"
            node.report.append("pull-%s moved into the analog deck (%s)" % (node.pull, why(pulls)))
        node.shunt = True
        _report_role(analysis, node, net, directions)
        return node
    if driven:
        hosts = [p for p in ports if _vhdl_mode(analysis, p) in ("in", "inout")]
        if not hosts:
            notes.append(error(canonical, "net %s is digitally driven (%s) but every SPICE port on "
                               "it is an output; declare one inout with port_dir"
                               % (canonical, why(net.drivers))))
            return None
        node.role, node.host = D2A, hosts[0]
        node.shunt = True
        _report_role(analysis, node, net, directions)
        return node
    if drivable and read:
        node.role, node.host = A2D, drivable[0]
        node.shunt = True
        _report_role(analysis, node, net, directions)
        return node
    if drivable:
        node.role = THROUGH
        node.shunt = True
        _report_role(analysis, node, net, directions)
        return node
    node.role = NONE
    node.shunt = True
    if len(ports) == 1 and not read and not net.drivers:
        node.report.append("unconnected bit %s (private node)" % _bit_name(analysis, ports[0]))
    else:
        notes.append(warning(canonical, "input %s is not driven" % canonical))
        node.report.append("not driven")
    _report_role(analysis, node, net, directions)
    return node


def _spelled(analysis: CutAnalysis, sp: str) -> str:
    """A SPICE port name in the netlist's original spelling."""
    st: Optional[_State] = getattr(analysis, "_cut", None)
    nl = st.nl if st is not None else None
    if nl is not None:
        return nl.spelling.get(sp.lower(), sp)
    return sp


_STEP2B = re.compile(r", connected to (?:.+?, a select of )?input port ")


def _probe_reason(directions: Optional[Dict[str, Dict[str, str]]], cell: str, cp: CutPort
                  ) -> Optional[str]:
    """The direction probe's reason for an auto port that its step 2b made an input
    (shells.ShellResult.directions: "auto->input (wrap_e.u (tb.sv:13), connected to
    input port wrap_e.a, which tb.we (tb.sv:7) connects to variable clk)"), else None."""
    text = ((directions or {}).get(cell) or {}).get(cp.verilog)
    m = re.match(r"auto->(\w+) \((.*)\)\s*$", text or "", re.S)
    if m is None or m.group(1) != cp.shell_dir or not _STEP2B.search(m.group(2)):
        return None
    return m.group(2)


def _report_role(analysis: CutAnalysis, node: AnalogNode, net: Net,
                 directions: Optional[Dict[str, Dict[str, str]]] = None) -> None:
    st: Optional[_State] = getattr(analysis, "_cut", None)
    one_way = st.one_way if st is not None else {}
    if node.host is not None:
        node.report.append("host %s" % _bit_name(analysis, node.host))
    for p in node.ports:
        ci, cp = _port_of(analysis, p)
        if p in one_way:
            node.report.append("direction: %s→input (%s) %s"
                               % ("auto→inout" if cp.declared == AUTO else cp.declared,
                                  one_way[p], _bit_name(analysis, p)))
        elif cp.declared == AUTO:
            if cp.shell_dir == INOUT:
                node.report.append("direction: auto→inout (VCS default; port_dir is faster) %s"
                                   % _bit_name(analysis, p))
            else:
                node.report.append("direction: auto→%s (%s) %s"
                                   % (cp.shell_dir,
                                      _probe_reason(directions, ci.cell, cp) or "variable actual",
                                      _bit_name(analysis, p)))
    if net.passive:
        node.report.append("passive bits: %s" % " ".join(_bit_name(analysis, p)
                                                          for p in net.passive))


def _pull_moves(analysis: CutAnalysis, st: Optional[_State], nodes: List[AnalogNode],
                notes: List[Note]) -> None:
    """A pull moved into the deck must be moved on every elaborated path (§5.4)."""
    if st is None:
        return
    bidir_nets = {n.net for n in nodes if n.role == BIDIR and n.pull is not None}
    moved: Set[str] = set()
    for n in nodes:
        if n.role != BIDIR or n.pull is None:
            continue
        net = _net(analysis, n.net)
        for d in net.drivers if net else []:
            if d.removable and d.strength in (PULL_UP, PULL_DOWN):
                moved.add(d.stmt)
    for sid in sorted(moved):
        arch_l, _, si = sid.rpartition("#")
        scopes = [sc for sc in st.scopes if sc.kind != CUT and sc.entity.lname == arch_l]
        nets_of = {s: k for s, k in st.pull_occ.get(sid, [])}
        bad = [sc for sc in scopes if nets_of.get(sc.id) not in bidir_nets]
        if bad:
            notes.append(error(sid, "the pull %s is needed digitally on %s but moved into the deck "
                               "on another path (v1 cannot split it)"
                               % (sid, ", ".join(sc.vpath for sc in bad[:4]))))
    st.moved_pulls = moved


def _warnings(analysis: CutAnalysis, st: Optional[_State], nodes: List[AnalogNode],
              nodes_by_net: Dict[str, AnalogNode], notes: List[Note]) -> None:
    for n in nodes:
        net = _net(analysis, n.net)
        if net is None:
            continue
        # A variable between two or more SPICE ports: vamos joins them in one analog node,
        # where VCS puts an interface element on each port and passes the value digitally.
        # One SPICE port on a variable joins nothing (its value reaches the digital side
        # through the node's IE, as in VCS, or nothing reads it).
        if net.variable and len(net.ports) > 1 and n.role in (THROUGH, D2A, A2D, BIDIR, REMOVED):
            vs = st.net_variables.get(net.key, []) if st is not None else []
            var = ", ".join(vs) if vs else n.canonical
            ports = [_bit_name(analysis, p) for p in net.ports]
            shown = ", ".join(ports[:4]) + (" and %d more" % (len(ports) - 4) if len(ports) > 4 else "")
            notes.append(warning(n.canonical, "variable %s joins SPICE ports in analog; VCS "
                                 "digitises it: %s share one analog node here, where VCS gives each "
                                 "SPICE port an interface element of its own; declare %s a wire if "
                                 "the analog connection is intended, or connect each SPICE port to a "
                                 "net of its own (wire w = %s;) to get VCS's digital connection"
                                 % (var, shown, var, var.split(",")[0].strip().rsplit(".", 1)[-1])))
        if n.role in (D2A, BIDIR) and st is not None:
            for k in st.net_reads_nets.get(n.net or "", ()):
                src = nodes_by_net.get(k)
                if src is not None and src.role in (A2D, BIDIR):
                    notes.append(warning(n.canonical, "analog connection quantised: %s -> digital "
                                         "-> %s" % (src.canonical, n.canonical)))


# -- emit (§5.5) ----------------------------------------------------------------------------

K_NAMES = ("HIV", "LOV", "X2V", "HITH", "LOTH", "XBAND", "MIDV_T", "MIDV_L", "DR", "DF", "WF",
           "PULL_V", "PULL_E")
_MIDV_CODE = {"0": 0, "1": 1, "X": 2, "Z": 3}


def _vreal(x: float) -> str:
    """A VHDL real literal."""
    x = float(x)
    if x != x or x in (float("inf"), float("-inf")):
        raise NoteError([error("", "level %r cannot be written to VHDL" % x)])
    s = repr(x)
    if "e" in s or "E" in s:
        mant, _, exp = s.lower().partition("e")
        if "." not in mant:
            mant += ".0"
        s = "%se%s" % (mant, exp)
    elif "." not in s:
        s += ".0"
    return s


def _aggregate(items: Sequence[str], indent: str = "", per_line: int = 0) -> str:
    """A positional VHDL aggregate ('(0 => x)' for one element); wrapped when per_line > 0."""
    if len(items) == 1:
        return "(0 => %s)" % items[0]
    if per_line <= 0 or len(items) <= per_line:
        return "(" + ", ".join(items) + ")"
    rows = [", ".join(items[i:i + per_line]) for i in range(0, len(items), per_line)]
    return "(\n" + indent + (",\n" + indent).join(rows) + ")"


class _Variant:
    def __init__(self, k: int, vb: VariantBind, cell: CutCell, ent: vhdl.Entity):
        self.k = k
        self.vb = vb
        self.cell = cell
        self.ent = ent
        self.insts: List[int] = []                       # CutInstance indices
        self.slots: List[Tuple[int, int]] = []           # (port index, bit offset)
        for cp in sorted(cell.ports, key=lambda p: p.index):
            rng = vb.ranges[cp.index] if cp.index < len(vb.ranges) else None
            width = 1 if rng is None or cp.kind == REAL else abs(rng[0] - rng[1]) + 1
            for b in range(width):
                self.slots.append((cp.index, b))
        self.slot_of = {s: i for i, s in enumerate(self.slots)}


def _levels(node: AnalogNode) -> List[float]:
    v = [0.0] * len(K_NAMES)
    d, a = node.d2a, node.a2d
    if node.role in (D2A, BIDIR):
        if d is None:
            raise NoteError([error(node.canonical, "node %s has no d2a levels (deck.py fills "
                                   "them before cut.emit)" % node.canonical)])
        v[0], v[1], v[2] = d.hiv, d.lov, float(d.x2v)
        v[8], v[9], v[10] = d.delay_rise, d.delay_fall, d.weak_frac
        if node.role == BIDIR and node.pull is not None:
            v[11] = d.hiv if node.pull == "up" else d.lov
            v[12] = d.weak_frac
    if node.role in (A2D, BIDIR):
        if a is None:
            raise NoteError([error(node.canonical, "node %s has no a2d thresholds (deck.py fills "
                                   "them before cut.emit)" % node.canonical)])
        v[3], v[4] = a.hith, a.loth
        v[5] = float(a.xband) if a.xband else 0.0
        v[6] = float(a.midv_time) if a.midv_time is not None else -1.0
        v[7] = float(_MIDV_CODE.get(str(a.midv_logic).upper(), 2))
    return v


def emit(plan: AmsPlan, out_dir: str) -> CutEmitResult:
    """Write <out_dir>/cut.vhd and <out_dir>/vamos.boundary (§5.5)."""
    ana = plan.analysis
    st: Optional[_State] = getattr(ana, "_cut", None)
    if st is None or st.design is None:
        raise NoteError([error("", "cut.emit needs the CutAnalysis made by cut.analyse")])
    d = st.design
    # hosts: (inst, port, bit) -> node
    hosts: Dict[Tuple[int, int, int], AnalogNode] = {}
    for n in plan.nodes:
        if n.host is not None and n.role in ROLE_CODE:
            hosts[(n.host.inst, n.host.port, n.host.bit)] = n
    # variants in walk order of their first instance
    variants: Dict[str, _Variant] = collections.OrderedDict()
    for i, ci in enumerate(ana.instances):
        key = ci.variant.lower()
        if key not in variants:
            ent = d.entity(ci.variant)
            if ent is None:
                raise NoteError([error(ci.vpath, "variant %s is not in design.vhd" % ci.variant)])
            variants[key] = _Variant(len(variants), ana.variants[ci.variant],
                                     ana.cells[ci.cell], ent)
        variants[key].insts.append(i)
    # consistency: every bridge sits on a host bit with a matching role
    for b in plan.bridges:
        n = hosts.get((b.host.inst, b.host.port, b.host.bit))
        ok = n is not None and (
            (b.kind in ("d2a", "en") and n.role in (D2A, BIDIR, RD2A)) or
            (b.kind == "a2d" and n.role in (A2D, BIDIR, RA2D)))
        if not ok:
            raise NoteError([error(b.vhdl_path, "bridge %s is not on a host bit of its role"
                                   % b.name)])
    pkg = _package(ana, variants, hosts)
    clones: Dict[str, str] = {}
    parts = [pkg]
    for key, v in variants.items():
        parts.append(_clone(ana, st, v))
        clones[v.ent.name] = v.vb.clone
    reparts, repointed = _repoint(ana, st, variants)
    parts.extend(reparts)
    os.makedirs(out_dir, exist_ok=True)
    vhd = os.path.join(out_dir, "cut.vhd")
    bnd = os.path.join(out_dir, "vamos.boundary")
    _write(vhd, "\n".join(parts))
    # exactly one names.boundary_line() per bridge, in plan.bridges order
    lines = [names.boundary_line(b) for b in plan.bridges]
    _write(bnd, "".join(line + "\n" for line in lines))
    return CutEmitResult(vhdl_path=vhd, boundary_path=bnd, clones=clones, repointed=repointed)


def _write(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)


_CONTEXT = ("library ieee;\nuse ieee.std_logic_1164.all;\nuse ieee.numeric_std.all;\n"
            "library sv2vhdl;\nuse sv2vhdl.sv_display_pkg.all;\nuse sv2vhdl.sv_strength_pkg.all;\n"
            "use sv2vhdl.logic3d_types_pkg.all;\nuse sv2vhdl.sv_analog_pkg.all;\n"
            "use sv2vhdl.sv_math_pkg.all;\n")


def _package(ana: CutAnalysis, variants: Dict[str, _Variant],
             hosts: Dict[Tuple[int, int, int], AnalogNode]) -> str:
    head = ["-- vams_cut_pkg: per-path roles and levels of the cut clones (vamos, generated)",
            "library sv2vhdl;", "use sv2vhdl.logic3d_types_pkg.all;", "",
            "package vams_cut_pkg is",
            "  constant ROLE_PASSIVE : natural := %d;" % ROLE_PASSIVE]
    for r in (D2A, A2D, BIDIR, RD2A, RA2D):
        head.append("  constant ROLE_%s : natural := %d;" % (r, ROLE_CODE[r]))
    for k, nm in enumerate(K_NAMES):
        head.append("  constant K_%s : natural := %d;" % (nm, k))
    body = ["package body vams_cut_pkg is"]
    for key, v in variants.items():
        k = v.k
        paths = sorted((ana.instances[i].path_name, i) for i in v.insts)
        n = len(paths)
        width = max(len(p) for p, _ in paths)
        s = max(1, len(v.slots))           # a cell with no Verilog port still gets a column
        roles: List[List[int]] = []
        lsets: List[Tuple[float, ...]] = [tuple([0.0] * len(K_NAMES))]
        lset_of: Dict[Tuple[float, ...], int] = {lsets[0]: 0}
        lidx: List[List[int]] = []
        for _, i in paths:
            rrow, lrow = [], []
            for (port, bit) in v.slots:
                node = hosts.get((i, port, bit))
                code = ROLE_CODE[node.role] if node is not None else ROLE_PASSIVE
                rrow.append(code)
                if node is not None:
                    lv = tuple(_levels(node))
                    if lv not in lset_of:
                        lset_of[lv] = len(lsets)
                        lsets.append(lv)
                    lrow.append(lset_of[lv])
                else:
                    lrow.append(0)
            if not v.slots:
                rrow, lrow = [ROLE_PASSIVE], [0]
            roles.append(rrow)
            lidx.append(lrow)
        ind = " " * 6
        head += [
            "",
            "  -- variant %d: %s (Verilog module %s), %d instance(s), %d slot(s)"
            % (k, v.ent.name, v.cell.name, n, len(v.slots)),
            "  constant VAMS_N_%d : natural := %d;" % (k, n),
            "  constant VAMS_LEN_%d : natural := %d;" % (k, width),
            "  type vams_paths_%d_t is array (0 to %d) of string(1 to %d);" % (k, n - 1, width),
            "  constant VAMS_PATHS_%d : vams_paths_%d_t := %s;" % (k, k, _aggregate(
                ['"%s"' % p.ljust(width) for p, _ in paths], ind, 4)),
            "  type vams_roles_%d_t is array (0 to %d, 0 to %d) of natural;" % (k, n - 1, s - 1),
            "  constant VAMS_ROLES_%d : vams_roles_%d_t := %s;" % (k, k, _aggregate(
                [_aggregate([str(c) for c in row]) for row in roles], ind, 8)),
            "  type vams_lidx_%d_t is array (0 to %d, 0 to %d) of natural;" % (k, n - 1, s - 1),
            "  constant VAMS_LIDX_%d : vams_lidx_%d_t := %s;" % (k, k, _aggregate(
                [_aggregate([str(c) for c in row]) for row in lidx], ind, 8)),
            "  type vams_lvls_%d_t is array (0 to %d, 0 to %d) of real;"
            % (k, len(lsets) - 1, len(K_NAMES) - 1),
            "  constant VAMS_LVLS_%d : vams_lvls_%d_t := %s;" % (k, k, _aggregate(
                [_aggregate([_vreal(x) for x in row]) for row in lsets], ind, 1)),
            "  function vams_index_%d(p : string) return natural;" % k,
            "  function vams_role_%d(i, slot : natural) return natural;" % k,
            "  function vams_lvl_%d(i, slot, key : natural) return real;" % k,
        ]
        body += [
            "",
            "  function vams_index_%d(p : string) return natural is" % k,
            "    variable key : string(1 to VAMS_LEN_%d) := (others => ' ');" % k,
            "    variable lo, hi, mid : integer;",
            "  begin",
            "    if p'length <= VAMS_LEN_%d then" % k,
            "      key(1 to p'length) := p;",
            "      lo := 0;",
            "      hi := VAMS_N_%d - 1;" % k,
            "      while lo <= hi loop",
            "        mid := (lo + hi) / 2;",
            "        if VAMS_PATHS_%d(mid) = key then" % k,
            "          return mid;",
            "        elsif VAMS_PATHS_%d(mid) < key then" % k,
            "          lo := mid + 1;",
            "        else",
            "          hi := mid - 1;",
            "        end if;",
            "      end loop;",
            "    end if;",
            "    report \"vamos: no cut table entry for instance path \" & p severity failure;",
            "    return 0;",
            "  end function;",
            "",
            "  function vams_role_%d(i, slot : natural) return natural is" % k,
            "  begin",
            "    return VAMS_ROLES_%d(i, slot);" % k,
            "  end function;",
            "",
            "  function vams_lvl_%d(i, slot, key : natural) return real is" % k,
            "  begin",
            "    return VAMS_LVLS_%d(VAMS_LIDX_%d(i, slot), key);" % (k, k),
            "  end function;",
        ]
    head.append("end package;")
    body.append("end package body;")
    return "\n".join(head + [""] + body) + "\n"


def _clone(ana: CutAnalysis, st: _State, v: _Variant) -> str:
    d = st.design
    ent = v.ent
    arch = d.arch(ent.name)
    clone = v.vb.clone
    ctx = ent.context.strip() or _CONTEXT.strip()
    lines = ["", "-- vamos cut clone of %s (Verilog module %s): %d instance(s)"
             % (ent.name, v.cell.name, len(v.insts)), ctx, "use work.vams_cut_pkg.all;", "",
             "entity %s is" % clone]
    if ent.port_clause:
        lines.append("  " + ent.port_clause)
    lines += ["end entity;", "", ctx, "use work.vams_cut_pkg.all;", "",
              "architecture vams_cut of %s is" % clone,
              "  constant vams_path : string := %s'path_name;" % clone,
              "  constant vams_i : natural := vams_index_%d(vams_path);" % v.k]
    taken = {p.lname for p in ent.ports}
    gen: List[str] = []

    def own(name: str) -> str:
        if name.lower() in taken:
            raise NoteError([error(d.where(ent.line), "port or signal %s of %s collides with a "
                                   "generated cut name" % (name, ent.name))])
        taken.add(name.lower())
        return name

    own("vams_path")
    own("vams_i")
    for name in SLOT_LOCALS:
        own(name)
    for slot, (k, b) in enumerate(v.slots):
        cp = v.cell.ports[k]
        vp = ent.ports[k]
        for kind, init in (("d", "0.0"), ("e", "0.0"), ("a", "real'low")):
            lines.append("  signal %s : real := %s;" % (own(names.vb_signal(k, b, kind)), init))
        for label in ("vams_d%d_%d", "vams_a%d_%d", "vams_z%d_%d"):
            own(label % (k, b))
        if b == 0:
            own("vams_rd%d" % k)
            own("vams_ra%d" % k)
        r = own("vams_r%d_%d" % (k, b))
        lines.append("  constant %s : natural := vams_role_%d(vams_i, %d);" % (r, v.k, slot))
        if cp.kind == REAL:
            gen += _real_slot(v, k, vp, r)
        else:
            pb = vp.name
            if v.vb.vhdl_vector[k] if k < len(v.vb.vhdl_vector) else vp.type.vector:
                rng = vp.type.rng
                idx = (rng.right + b) if rng is None or rng.downto else (rng.right - b)
                pb = "%s(%d)" % (vp.name, idx)
            gen += _logic_slot(v, k, b, slot, vp, pb, r)
    # foreign statements (parent ties the translator folded into the shell) and
    # the declarations of the signals they use
    foreign = st.foreign.get(ent.lname, [])
    used: Set[str] = set()
    for si in foreign:
        s = arch.stmts[si]
        for asg in s.assigns:
            used.add(asg.target.lname)
            used.update(r.lname for r in asg.reads)
        used.update(r.lname for r in s.reads)
        for a in s.assocs:
            used.update(r.lname for r in (a.actual or []))
            used.update(r.lname for r in a.reads)
    for n in sorted(used):
        sd = arch.signals.get(n)
        if sd is not None:
            own(sd.name)
            lines.append("  signal %s : %s%s;" % (sd.name, sd.type.text,
                                                  " := %s" % sd.init if sd.init else ""))
    lines.append("begin")
    lines += gen
    for si in foreign:
        s = arch.stmts[si]
        if s.sid in st.moved_pulls:
            lines.append("  -- vamos: pull %s moved into the analog deck (BIDIR)" % s.sid)
            continue
        lines.append("  -- kept from %s (line %d)" % (ent.name, s.line))
        lines.append("  " + d.text[s.span[0]:s.span[1]])
    lines.append("end architecture;")
    return "\n".join(lines) + "\n"


def _real_slot(v: _Variant, k: int, vp: vhdl.PortDecl, r: str) -> List[str]:
    out = ["", "  -- port %s (real)" % vp.name]
    if vp.mode in ("in", "inout"):
        out += ["  vams_rd%d: if %s = ROLE_RD2A generate" % (k, r),
                "    %s <= %s;" % (names.vb_signal(k, 0, "d"), vp.name),
                "  end generate;"]
    if vp.mode in ("out", "inout"):
        a = names.vb_signal(k, 0, "a")
        out += ["  vams_ra%d: if %s = ROLE_RA2D generate" % (k, r),
                "    %s <= 0.0 when %s < -1.0e300 else %s;" % (vp.name, a, a),
                "  end generate;"]
    return out


# Every name a logic slot's generate blocks declare (constants, functions, process
# variables).  Inside a block such a name hides a port or signal of the clone spelled the
# same, so _clone() reserves them all (a port of that name is an error): unprefixed, a cut
# port named v was read as the D2A process's own variable, and its node silently stayed
# at lov.
SLOT_LOCALS = ("vams_hiv", "vams_lov", "vams_x2v", "vams_dr", "vams_df", "vams_wf", "vams_pull_v",
               "vams_pull_e", "vams_bidir", "vams_v", "vams_nv", "vams_ne", "vams_last",
               "vams_prev", "vams_dt", "vams_hith", "vams_loth", "vams_xband", "vams_midv_t",
               "vams_midv_l", "vams_hys", "vams_hith_hys", "vams_loth_hys", "vams_drive",
               "vams_va", "vams_st", "vams_inwin", "vams_t_in", "vams_w", "vams_mv")


def _logic_slot(v: _Variant, k: int, b: int, slot: int, vp: vhdl.PortDecl, pb: str,
                r: str) -> List[str]:
    vd, ve, va = (names.vb_signal(k, b, x) for x in ("d", "e", "a"))
    lv = "vams_lvl_%d(vams_i, %d, K_%%s)" % (v.k, slot)
    out = ["", "  -- port %s bit %d (slot %d)" % (vp.name, b, slot)]
    if vp.mode in ("in", "inout"):
        out += [
            "  vams_d%d_%d: if %s = ROLE_D2A or %s = ROLE_BIDIR generate" % (k, b, r, r),
            "    constant VAMS_HIV : real := %s;" % (lv % "HIV"),
            "    constant VAMS_LOV : real := %s;" % (lv % "LOV"),
            "    constant VAMS_X2V : natural := natural(%s);" % (lv % "X2V"),
            "    constant VAMS_DR : time := %s * 1 sec;" % (lv % "DR"),
            "    constant VAMS_DF : time := %s * 1 sec;" % (lv % "DF"),
            "    constant VAMS_WF : real := %s;" % (lv % "WF"),
            "    constant VAMS_PULL_V : real := %s;" % (lv % "PULL_V"),
            "    constant VAMS_PULL_E : real := %s;" % (lv % "PULL_E"),
            "    constant VAMS_BIDIR : boolean := %s = ROLE_BIDIR;" % r,
            "  begin",
            "    process (%s)" % pb,
            "      variable vams_v : logic3d;",
            "      variable vams_nv, vams_ne : real;",
            "      variable vams_last : real := VAMS_LOV;",
            "      -- the previous digital input, for x2v=4: 0, 1, or 2 (Z, X, start-up)",
            "      variable vams_prev : natural := 2;",
            "      variable vams_dt : time;",
            "    begin",
            "      vams_v := %s;" % pb,
            "      if VAMS_BIDIR and not is_strong(vams_v) then",
            "        -- no external strong driver: only the moved pull (if any) drives; the",
            "        -- weak H/L there is the A2D's own echo, so the input counts as Z",
            "        if VAMS_PULL_E > 0.0 then",
            "          vams_nv := VAMS_PULL_V;",
            "        else",
            "          vams_nv := vams_last;",
            "        end if;",
            "        vams_ne := VAMS_PULL_E;",
            "        vams_prev := 2;",
            "      else",
            "        if vams_v = L3D_1 or vams_v = L3D_H then",
            "          vams_nv := VAMS_HIV;",
            "        elsif vams_v = L3D_0 or vams_v = L3D_L then",
            "          vams_nv := VAMS_LOV;",
            "        elsif vams_v = L3D_Z then",
            "          vams_nv := vams_last;",
            "        elsif VAMS_X2V = 1 then",
            "          vams_nv := VAMS_HIV;",
            "        elsif VAMS_X2V = 2 then",
            "          vams_nv := (VAMS_HIV + VAMS_LOV) / 2.0;",
            "        elsif VAMS_X2V = 3 then",
            "          vams_nv := vams_last;",
            "        elsif VAMS_X2V = 4 then",
            "          -- PAMS p206: the logic 1 voltage after a 0, the logic 0 voltage after",
            "          -- a 1, otherwise (after Z or X, or at start-up) the previous voltage",
            "          if vams_prev = 0 then",
            "            vams_nv := VAMS_HIV;",
            "          elsif vams_prev = 1 then",
            "            vams_nv := VAMS_LOV;",
            "          else",
            "            vams_nv := vams_last;",
            "          end if;",
            "        else",
            "          vams_nv := VAMS_LOV;",
            "        end if;",
            "        if is_strong(vams_v) then",
            "          vams_ne := 1.0;",
            "        elsif vams_v = L3D_Z then",
            "          vams_ne := 0.0;",
            "        else",
            "          vams_ne := VAMS_WF;",
            "        end if;",
            "        if vams_v = L3D_0 or vams_v = L3D_L then",
            "          vams_prev := 0;",
            "        elsif vams_v = L3D_1 or vams_v = L3D_H then",
            "          vams_prev := 1;",
            "        else",
            "          vams_prev := 2;",
            "        end if;",
            "      end if;",
            "      if vams_nv > vams_last then",
            "        vams_dt := VAMS_DR;",
            "      elsif vams_nv < vams_last then",
            "        vams_dt := VAMS_DF;",
            "      else",
            "        vams_dt := 0 fs;",
            "      end if;",
            "      %s <= vams_nv after vams_dt;" % vd,
            "      %s <= vams_ne after vams_dt;" % ve,
            "      vams_last := vams_nv;",
            "    end process;",
            "  end generate;",
        ]
    if vp.mode in ("out", "inout"):
        out += [
            "  vams_a%d_%d: if %s = ROLE_A2D or %s = ROLE_BIDIR generate" % (k, b, r, r),
            "    constant VAMS_HITH : real := %s;" % (lv % "HITH"),
            "    constant VAMS_LOTH : real := %s;" % (lv % "LOTH"),
            "    constant VAMS_XBAND : real := %s;" % (lv % "XBAND"),
            "    constant VAMS_MIDV_T : real := %s;" % (lv % "MIDV_T"),
            "    constant VAMS_MIDV_L : natural := natural(%s);" % (lv % "MIDV_L"),
            "    constant VAMS_BIDIR : boolean := %s = ROLE_BIDIR;" % r,
            "    -- PAMS p189 window: falling (LOTH, HITH_HYS), rising (LOTH_HYS, HITH)",
            "    function vams_hys(hi, lo, xb : real; up : boolean) return real is",
            "    begin",
            "      if xb <= 0.0 then",
            "        if up then return hi; else return lo; end if;",
            "      elsif up then",
            "        return hi - (hi - lo) / xb;",
            "      else",
            "        return lo + (hi - lo) / xb;",
            "      end if;",
            "    end function;",
            "    constant VAMS_HITH_HYS : real := vams_hys(VAMS_HITH, VAMS_LOTH, VAMS_XBAND, true);",
            "    constant VAMS_LOTH_HYS : real := vams_hys(VAMS_HITH, VAMS_LOTH, VAMS_XBAND, false);",
            "    function vams_drive(x : logic3d) return logic3d is",
            "    begin",
            "      if VAMS_BIDIR then return l3d_weaken(x); else return x; end if;",
            "    end function;",
            "  begin",
            "    process",
            "      variable vams_va : real;",
            "      variable vams_st : logic3d := L3D_X;",
            "      variable vams_inwin : boolean := false;",
            "      variable vams_t_in : time := 0 fs;",
            "      variable vams_w : boolean;",
            "      variable vams_mv : logic3d;",
            "    begin",
            "      loop",
            "        vams_va := %s;" % va,
            "        if vams_va < -1.0e300 then",
            "          vams_st := L3D_X;",       # real'low: no analog value yet
            "          vams_inwin := false;",
            "        elsif vams_va >= VAMS_HITH then",
            "          vams_st := L3D_1;",
            "          vams_inwin := false;",
            "        elsif vams_va <= VAMS_LOTH then",
            "          vams_st := L3D_0;",
            "          vams_inwin := false;",
            "        elsif VAMS_MIDV_T >= 0.0 then",
            "          if vams_st = L3D_1 then",
            "            vams_w := vams_va < VAMS_HITH_HYS;",
            "          elsif vams_st = L3D_0 then",
            "            vams_w := vams_va > VAMS_LOTH_HYS;",
            "          else",
            "            vams_w := true;",
            "          end if;",
            "          if vams_w and not vams_inwin then",
            "            vams_inwin := true;",
            "            vams_t_in := now;",
            "          elsif not vams_w then",
            "            vams_inwin := false;",
            "          end if;",
            "        end if;",
            "        if vams_inwin and now - vams_t_in >= VAMS_MIDV_T * 1 sec then",
            "          if VAMS_MIDV_L = 0 then vams_mv := L3D_0;",
            "          elsif VAMS_MIDV_L = 1 then vams_mv := L3D_1;",
            "          elsif VAMS_MIDV_L = 3 then vams_mv := L3D_Z;",
            "          else vams_mv := L3D_X;",
            "          end if;",
            "          %s <= vams_drive(vams_mv);" % pb,
            "          wait on %s;" % va,
            "        elsif vams_inwin then",
            "          %s <= vams_drive(vams_st);" % pb,
            "          wait on %s for vams_t_in + VAMS_MIDV_T * 1 sec - now;" % va,
            "        else",
            "          %s <= vams_drive(vams_st);" % pb,
            "          wait on %s;" % va,
            "        end if;",
            "      end loop;",
            "    end process;",
            "  end generate;",
            "  vams_z%d_%d: if %s /= ROLE_A2D and %s /= ROLE_BIDIR generate" % (k, b, r, r),
            "    %s <= L3D_Z;" % pb,
            "  end generate;",
        ]
    return out


def _repoint(ana: CutAnalysis, st: _State, variants: Dict[str, _Variant]
             ) -> Tuple[List[str], List[str]]:
    """Re-emit every elaborated architecture that instantiates a cut variant or loses a pull."""
    d = st.design
    changes: Dict[str, Dict[int, str]] = collections.OrderedDict()    # arch -> stmt -> how
    for sc in st.scopes:
        if sc.kind == CUT or sc.arch is None:
            continue
        for si, cid in sorted(sc.children.items()):
            c = st.scopes[cid]
            if c.kind != CUT:
                continue
            key = c.entity.lname
            if key not in variants:
                raise NoteError([error(c.vpath, "instance %s still binds the un-cloned cut variant "
                                       "%s" % (c.vpath, c.entity.name))])
            changes.setdefault(sc.entity.lname, {})[si] = "repoint"
    for sid in sorted(st.moved_pulls):
        arch_l, _, si = sid.rpartition("#")
        changes.setdefault(arch_l, {})[int(si)] = "remove"
    parts: List[str] = []
    repointed: List[str] = []
    for arch_l, stmts in changes.items():
        arch = d.archs[arch_l]
        ent = d.entities[arch_l]
        text = d.text
        a0, a1 = arch.span
        edits: List[Tuple[int, int, str]] = []
        for si, how in sorted(stmts.items()):
            s = arch.stmts[si]
            if how == "repoint":
                key = s.entity.lower()
                v = variants[key]
                edits.append((s.ent_span[0], s.ent_span[1], v.vb.clone))
                tag = "%s:%s" % (arch.entity, s.label)
                if tag in repointed:
                    raise NoteError([error(tag, "statement %s re-pointed twice" % tag)])
                repointed.append(tag)
            else:
                src = text[s.span[0]:s.span[1]].replace("\n", "\n  -- ")
                edits.append((s.span[0], s.span[1],
                              "-- vamos: pull moved into the analog deck (BIDIR):\n  -- " + src))
        out = []
        pos = a0
        for e0, e1, rep in sorted(edits):
            out.append(text[pos:e0])
            out.append(rep)
            pos = e1
        out.append(text[pos:a1])
        ctx = ent.context.strip() or _CONTEXT.strip()
        parts.append("\n-- vamos: %s re-emitted with its cut instances re-pointed\n%s\n%s\n"
                     % (arch.entity, ctx, "".join(out)))
    # every re-pointed statement now binds a clone
    for arch_l, stmts in changes.items():
        for si, how in stmts.items():
            if how == "repoint" and d.archs[arch_l].stmts[si].entity.lower() not in variants:
                raise NoteError([error(arch_l, "statement %d of %s binds no clone" % (si, arch_l))])
    return parts, repointed
